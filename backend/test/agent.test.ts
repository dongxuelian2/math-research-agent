import { strict as assert } from "node:assert";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	AgentCore,
	asRuntimeTool,
	defineTool,
	MockProvider,
	Session,
	type AgentEvent,
	type JsonObject,
	type RuntimeTool,
} from "../src/index.js";

type EchoArguments = JsonObject & { readonly value: string };

async function makeSession(): Promise<{ directory: string; session: Session }> {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-test-"));
	const session = await Session.create({ projectId: "project", cwd: directory, directory });
	return { directory, session };
}

function model() {
	return { provider: "openai" as const, model: "mock-model" };
}

function echoTool(log: string[]): RuntimeTool {
	return asRuntimeTool(
		defineTool<EchoArguments, { readonly echoed: string }>({
			name: "echo",
			description: "Echo a value.",
			parameters: {
				type: "object",
				properties: { value: { type: "string" } },
				required: ["value"],
			},
			validate(input: unknown): EchoArguments {
				if (typeof input !== "object" || input === null || Array.isArray(input)) {
					throw new Error("arguments must be an object");
				}
				const value = (input as Record<string, unknown>).value;
				if (typeof value !== "string") {
					throw new Error("value must be a string");
				}
				return { value };
			},
			async execute(args: EchoArguments): Promise<{ readonly echoed: string }> {
				log.push(args.value);
				return { echoed: args.value };
			},
		}),
	);
}

test("runs a streaming single turn and persists the projected context", async (t) => {
	const { directory, session } = await makeSession();
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const provider = new MockProvider([
		{
			events: [
				{ type: "text_delta", text: "hello" },
				{ type: "text_delta", text: " world" },
				{ type: "complete", stopReason: "end_turn" },
			],
		},
	]);
	const events: AgentEvent[] = [];
	const agent = new AgentCore({ session, model: model(), provider });
	agent.subscribe((event) => {
		events.push(event);
	});

	const result = await agent.prompt("greet me");

	assert.equal(result.stopReason, "completed");
	assert.equal(provider.requests.length, 1);
	assert.deepEqual(agent.state.messages.map((message) => message.role), ["user", "assistant"]);
	const assistant = result.messages.find((message) => message.role === "assistant");
	assert.equal(assistant?.role, "assistant");
	assert.equal(assistant?.content[0]?.kind, "text");
	if (assistant?.role === "assistant" && assistant.content[0]?.kind === "text") {
		assert.equal(assistant.content[0].text, "hello world");
	}
	assert.deepEqual(
		events.map((event) => event.type),
		["agent_start", "turn_start", "message_update", "message_update", "turn_end", "agent_end"],
	);

	const resumed = await Session.resume(session.filePath);
	assert.deepEqual(resumed.contextProjection().map((message) => message.role), ["user", "assistant"]);
});

test("executes a sequential tool loop and distinguishes tool errors", async (t) => {
	const { directory, session } = await makeSession();
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const calls: string[] = [];
	const hookTrace: string[] = [];
	const provider = new MockProvider([
		{
			events: [
				{ type: "tool_call_delta", callId: "call-1", name: "echo", argumentsDelta: '{"value":"ok"}' },
				{ type: "complete", stopReason: "tool_calls" },
			],
		},
		{
			events: [{ type: "text_delta", text: "tool completed" }, { type: "complete", stopReason: "end_turn" }],
		},
	]);
	const turnEnds: AgentEvent[] = [];
	const agent = new AgentCore({
		session,
		model: model(),
		provider,
		tools: [echoTool(calls)],
		hooks: {
			beforeToolCall: (context) => {
				hookTrace.push(`before:${context.name}`);
			},
			afterToolCall: (context) => {
				hookTrace.push(`after:${context.name}`);
			},
		},
	});
	agent.subscribe((event) => {
		if (event.type === "turn_end") {
			turnEnds.push(event);
		}
	});

	const result = await agent.prompt("use echo");

	assert.equal(result.stopReason, "completed");
	assert.deepEqual(calls, ["ok"]);
	assert.deepEqual(hookTrace, ["before:echo", "after:echo"]);
	assert.equal(provider.requests.length, 2);
	assert.equal(turnEnds[0]?.type, "turn_end");
	if (turnEnds[0]?.type === "turn_end") {
		assert.equal(turnEnds[0].stopReason, "tool_calls");
		assert.equal(turnEnds[0].toolResults[0]?.isError, false);
	}
	assert.equal(session.contextProjection().filter((message) => message.role === "tool_result").length, 1);
});

test("runs multiple tools in parallel and survives invalid arguments", async (t) => {
	const { directory, session } = await makeSession();
	t.after(async () => rm(directory, { recursive: true, force: true }));
	let running = 0;
	let peak = 0;
	const makeSlowTool = (name: string): RuntimeTool =>
		asRuntimeTool(
			defineTool<JsonObject, { readonly name: string }>({
				name,
				description: name,
				parameters: { type: "object" },
				validate(input: unknown): JsonObject {
					if (typeof input !== "object" || input === null || Array.isArray(input)) {
						throw new Error("object required");
					}
					return input as JsonObject;
				},
				async execute(): Promise<{ readonly name: string }> {
					running += 1;
					peak = Math.max(peak, running);
					await new Promise((resolve) => setTimeout(resolve, 20));
					running -= 1;
					return { name };
				},
			}),
		);
	const provider = new MockProvider([
		{
			events: [
				{ type: "tool_call_delta", callId: "a", name: "slow-a", argumentsDelta: "{}" },
				{ type: "tool_call_delta", callId: "b", name: "slow-b", argumentsDelta: "{}" },
				{ type: "complete", stopReason: "tool_calls" },
			],
		},
		{ events: [{ type: "complete", stopReason: "end_turn" }] },
	]);
	const agent = new AgentCore({
		session,
		model: model(),
		provider,
		toolExecutionMode: "parallel",
		tools: [makeSlowTool("slow-a"), makeSlowTool("slow-b")],
	});
	const result = await agent.prompt("parallel");

	assert.equal(result.stopReason, "completed");
	assert.equal(peak, 2);

	const invalidSession = await Session.create({ projectId: "invalid", cwd: directory, directory });
	const invalidProvider = new MockProvider([
		{
			events: [
				{ type: "tool_call_delta", callId: "bad", name: "echo", argumentsDelta: "{}" },
				{ type: "complete", stopReason: "tool_calls" },
			],
		},
		{ events: [{ type: "complete", stopReason: "end_turn" }] },
	]);
	const invalidAgent = new AgentCore({ session: invalidSession, model: model(), provider: invalidProvider, tools: [echoTool([])] });
	const invalidResult = await invalidAgent.prompt("invalid");
	assert.equal(invalidResult.stopReason, "completed");
	const invalidToolResult = invalidSession.contextProjection().find((message) => message.role === "tool_result");
	assert.equal(invalidToolResult?.role, "tool_result");
	assert.equal(invalidToolResult?.isError, true);
});

test("records a tool execution exception as a tool error event", async (t) => {
	const { directory, session } = await makeSession();
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const failingTool = asRuntimeTool(
		defineTool<JsonObject, never>({
			name: "explode",
			description: "Always fails.",
			parameters: { type: "object" },
			validate(input: unknown): JsonObject {
				if (typeof input !== "object" || input === null || Array.isArray(input)) {
					throw new Error("object required");
				}
				return input as JsonObject;
			},
			async execute(): Promise<never> {
				throw new Error("intentional tool failure");
			},
		}),
	);
	const provider = new MockProvider([
		{
			events: [
				{ type: "tool_call_delta", callId: "explode-1", name: "explode", argumentsDelta: "{}" },
				{ type: "complete", stopReason: "tool_calls" },
			],
		},
		{ events: [{ type: "complete", stopReason: "end_turn" }] },
	]);
	const events: AgentEvent[] = [];
	const agent = new AgentCore({ session, model: model(), provider, tools: [failingTool] });
	agent.subscribe((event) => {
		events.push(event);
	});

	const result = await agent.prompt("run failure");

	assert.equal(result.stopReason, "completed");
	assert.equal(events.some((event) => event.type === "agent_error" && event.phase === "tool"), true);
	assert.equal(
		events.some((event) => event.type === "turn_end" && event.stopReason === "tool_error"),
		true,
	);
});

test("injects steering during a run and follow-up after a turn", async (t) => {
	const { directory, session } = await makeSession();
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const provider = new MockProvider([
		{ events: [{ type: "text_delta", text: "first" }, { type: "complete", stopReason: "end_turn" }] },
		{ events: [{ type: "text_delta", text: "second" }, { type: "complete", stopReason: "end_turn" }] },
		{ events: [{ type: "text_delta", text: "third" }, { type: "complete", stopReason: "end_turn" }] },
	]);
	const agent = new AgentCore({ session, model: model(), provider });
	let queuedSteer = false;
	agent.subscribe((event) => {
		if (event.type === "message_update" && event.update.kind === "text_delta" && !queuedSteer) {
			queuedSteer = true;
			agent.steer("steer now");
			agent.followUp("follow later");
		}
	});

	const result = await agent.prompt("start");

	assert.equal(result.stopReason, "completed");
	assert.equal(provider.requests.length, 3);
	assert.deepEqual(
		session.contextProjection().filter((message) => message.role === "user").map((message) => message.content),
		["start", "steer now", "follow later"],
	);
});

test("separates model failure and user cancellation stop reasons", async (t) => {
	const failureRoot = await makeSession();
	t.after(async () => rm(failureRoot.directory, { recursive: true, force: true }));
	const failureEvents: AgentEvent[] = [];
	const failingProvider = new MockProvider();
	failingProvider.enqueue({ events: [{ type: "failure", error: new Error("provider offline") }] });
	const actualFailureAgent = new AgentCore({ session: failureRoot.session, model: model(), provider: failingProvider });
	actualFailureAgent.subscribe((event) => {
		failureEvents.push(event);
	});
	const failureResult = await actualFailureAgent.prompt("fail");
	assert.equal(failureResult.stopReason, "model_error");
	assert.equal(failureEvents.some((event) => event.type === "agent_error" && event.phase === "model"), true);

	const abortRoot = await makeSession();
	t.after(async () => rm(abortRoot.directory, { recursive: true, force: true }));
	let markStarted: () => void = () => undefined;
	const started = new Promise<void>((resolve) => {
		markStarted = resolve;
	});
	const blockingProvider = {
		id: "openai" as const,
		async *stream(request: { readonly signal?: AbortSignal }): AsyncIterable<never> {
			markStarted();
			await new Promise<void>((resolve) => {
				if (request.signal?.aborted) {
					resolve();
					return;
				}
				request.signal?.addEventListener("abort", () => resolve(), { once: true });
			});
			throw new DOMException("aborted", "AbortError");
		},
	};
	const abortAgent = new AgentCore({ session: abortRoot.session, model: model(), provider: blockingProvider });
	const pending = abortAgent.prompt("cancel me");
	await started;
	await abortAgent.abort();
	const abortResult = await pending;
	assert.equal(abortResult.stopReason, "aborted");
});
