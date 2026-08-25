import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
	createProvider,
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
