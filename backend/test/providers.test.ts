import { strict as assert } from "node:assert";
import { generateKeyPairSync } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	createAssistantMessage,
	createProvider,
	createToolResult,
	createUserMessage,
	type ModelStreamEvent,
	type ProviderRequest,
	type ProviderTransport,
} from "../src/index.js";

class StaticTransport implements ProviderTransport {
	readonly requests: string[] = [];
	private readonly chunks: readonly string[];

	constructor(chunks: readonly string[]) {
		this.chunks = chunks;
	}

	async *stream(request: { readonly body: string }): AsyncIterable<string> {
		this.requests.push(request.body);
		for (const chunk of this.chunks) {
			yield chunk;
		}
	}
}

async function collect(provider: ReturnType<typeof createProvider>, request: ProviderRequest): Promise<ModelStreamEvent[]> {
	const events: ModelStreamEvent[] = [];
	for await (const event of provider.stream(request)) {
		events.push(event);
	}
	return events;
}

function request(provider: ProviderRequest["model"]): ProviderRequest {
	return { model: provider, messages: [createUserMessage("hello")], tools: [] };
}

test("normalizes all six provider adapters through the same offline contract", async () => {
	const openAiSse = [
		'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n',
		'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
		"data: [DONE]\n\n",
	];
	for (const providerId of ["openai", "openrouter", "deepseek"] as const) {
		const transport = new StaticTransport(openAiSse);
		const provider = createProvider(
			{ provider: providerId, model: "model", credentialResolver: () => "secret" },
			{ transport },
		);
		const events = await collect(provider, request({ provider: providerId, model: "model" }));
		assert.equal(events.some((event) => event.type === "text_delta" && event.text === "ok"), true);
		assert.equal(events.at(-1)?.type, "complete");
		assert.match(transport.requests[0] ?? "", /"stream":true/);
	}
	const fragmentedToolTransport = new StaticTransport([
		'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"read"}}]},"finish_reason":null}]}\n\n',
		'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\n',
	]);
	const fragmentedToolProvider = createProvider(
		{ provider: "openai", model: "model", credentialResolver: () => "secret" },
		{ transport: fragmentedToolTransport },
	);
	const fragmentedToolEvents = await collect(fragmentedToolProvider, request({ provider: "openai", model: "model" }));
	const fragmentedCalls = fragmentedToolEvents.filter((event) => event.type === "tool_call_delta");
	assert.equal(fragmentedCalls.length, 2);
	if (fragmentedCalls[0]?.type === "tool_call_delta" && fragmentedCalls[1]?.type === "tool_call_delta") {
		assert.equal(fragmentedCalls[0].callId, "call-1");
		assert.equal(fragmentedCalls[1].callId, "call-1");
	}

	const thoughtSignature = { google: { thought_signature: "opaque-signature" } };
	const metadataTransport = new StaticTransport([
		`data: ${JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call-meta", extra_content: thoughtSignature, function: { name: "read", arguments: "{}" } }] }, finish_reason: "tool_calls" }] })}\n\n`,
	]);
	const metadataProvider = createProvider(
		{ provider: "openai", model: "model", credentialResolver: () => "secret" },
		{ transport: metadataTransport },
	);
	const metadataEvents = await collect(metadataProvider, request({ provider: "openai", model: "model" }));
	const metadataCall = metadataEvents.find((event): event is Extract<ModelStreamEvent, { type: "tool_call_delta" }> => event.type === "tool_call_delta");
	assert.deepEqual(metadataCall?.providerMetadata, thoughtSignature);

	const replayTransport = new StaticTransport(["data: [DONE]\n\n"]);
	const replayProvider = createProvider(
		{ provider: "openai", model: "model", credentialResolver: () => "secret" },
		{ transport: replayTransport },
	);
	await collect(replayProvider, {
		model: { provider: "openai", model: "model", credentialResolver: () => "secret" },
		tools: [],
		messages: [
			createUserMessage("read the theorem"),
			createAssistantMessage(
				[{ kind: "tool_call", id: "call-meta", name: "read", arguments: {}, providerMetadata: thoughtSignature }],
				{ provider: "openai", model: "model", stopReason: "tool_calls" },
			),
			createToolResult({ toolCallId: "call-meta", toolName: "read", content: "done", details: {}, isError: false }),
		],
	});
	const replayBody = JSON.parse(replayTransport.requests[0] ?? "{}") as { messages?: Array<{ tool_calls?: Array<{ extra_content?: unknown }> }> };
	assert.deepEqual(replayBody.messages?.[1]?.tool_calls?.[0]?.extra_content, thoughtSignature);

	const anthropicTransport = new StaticTransport([
		'event: content_block_delta\ndata: {"index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n',
		'event: message_delta\ndata: {"delta":{"stop_reason":"end_turn"}}\n\n',
	]);
	const anthropic = createProvider({ provider: "anthropic", model: "claude", credentialResolver: () => "secret" }, { transport: anthropicTransport });
	const anthropicEvents = await collect(anthropic, request({ provider: "anthropic", model: "claude" }));
	assert.equal(anthropicEvents.some((event) => event.type === "text_delta" && event.text === "ok"), true);

	const googleTransport = new StaticTransport([
		'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}\n\n',
	]);
	const google = createProvider({ provider: "google", model: "gemini", credentialResolver: () => "secret" }, { transport: googleTransport });
	const googleEvents = await collect(google, request({ provider: "google", model: "gemini" }));
	assert.equal(googleEvents.some((event) => event.type === "text_delta" && event.text === "ok"), true);
	const googleThoughtSignature = "native-opaque-signature";
	const googleToolTransport = new StaticTransport([
		`data: ${JSON.stringify({ candidates: [{ content: { parts: [{ functionCall: { name: "read", args: {} }, thoughtSignature: googleThoughtSignature }] }, finishReason: "STOP" }] })}\n\n`,
	]);
	const googleToolProvider = createProvider({ provider: "google", model: "gemini-3.7-flash", credentialResolver: () => "secret" }, { transport: googleToolTransport });
	const googleToolEvents = await collect(googleToolProvider, request({ provider: "google", model: "gemini-3.7-flash" }));
	const googleToolCall = googleToolEvents.find((event): event is Extract<ModelStreamEvent, { type: "tool_call_delta" }> => event.type === "tool_call_delta");
	assert.deepEqual(googleToolCall?.providerMetadata, { google: { thought_signature: googleThoughtSignature } });
	const googleReplayTransport = new StaticTransport(["data: [DONE]\n\n"]);
	const googleReplayProvider = createProvider({ provider: "google", model: "gemini-3.7-flash", credentialResolver: () => "secret" }, { transport: googleReplayTransport });
	await collect(googleReplayProvider, {
		model: { provider: "google", model: "gemini-3.7-flash", credentialResolver: () => "secret" },
		tools: [],
		messages: [
			createUserMessage("read the theorem"),
			createAssistantMessage(
				[{ kind: "tool_call", id: "google-call-0", name: "read", arguments: {}, providerMetadata: { google: { thought_signature: googleThoughtSignature } } }],
				{ provider: "google", model: "gemini-3.7-flash", stopReason: "tool_calls" },
			),
			createToolResult({ toolCallId: "google-call-0", toolName: "read", content: "done", details: {}, isError: false }),
		],
	});
	const googleReplayBody = JSON.parse(googleReplayTransport.requests[0] ?? "{}") as { contents?: Array<{ parts?: Array<{ thoughtSignature?: unknown }> }> };
	assert.equal(googleReplayBody.contents?.[1]?.parts?.[0]?.thoughtSignature, googleThoughtSignature);

	const codex = createProvider(
		{ provider: "openai-codex", model: "codex-model" },
		{
			codex: {
				runner: {
					async *run(): AsyncIterable<string> {
						yield JSON.stringify({ type: "response.output_text.delta", delta: "ok" });
						yield JSON.stringify({ type: "response.completed" });
					},
				},
			},
		},
	);
	const codexEvents = await collect(codex, request({ provider: "openai-codex", model: "codex-model" }));
	assert.equal(codexEvents.some((event) => event.type === "text_delta" && event.text === "ok"), true);
	assert.equal(codexEvents.at(-1)?.type, "complete");
});

test("authenticates the Vertex adapter with a service-account file and Gemini 3 thinking level", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-google-vertex-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
	const credentialPath = join(directory, "service-account.json");
	await writeFile(credentialPath, JSON.stringify({
		type: "service_account",
		project_id: "test-project",
		client_email: "test@test-project.iam.gserviceaccount.com",
		private_key: privateKey.export({ type: "pkcs8", format: "pem" }),
		private_key_id: "test-key",
		token_uri: "https://oauth2.example.test/token",
	}));
	const transport = new StaticTransport([
		' data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}\n\n'.trimStart(),
	]);
	const provider = createProvider(
		{
			provider: "google-vertex",
			model: "gemini-3.7-flash",
			credentialFileResolver: () => credentialPath,
			reasoningEffort: "high",
			maxTokens: 123,
		},
		{
			transport,
			googleVertex: { tokenRequester: async () => ({ accessToken: "access-token", expiresIn: 3600 }) },
		},
	);
	const events = await collect(provider, request({ provider: "google-vertex", model: "gemini-3.7-flash", credentialFileResolver: () => credentialPath, reasoningEffort: "high", maxTokens: 123 }));
	assert.equal(events.some((event) => event.type === "text_delta" && event.text === "ok"), true);
	assert.equal(transport.requests.length, 1);
	assert.match(transport.requests[0] ?? "", /"thinkingLevel":"high"/u);
	assert.doesNotMatch(transport.requests[0] ?? "", /thinkingBudget/u);
});
