import { randomUUID } from "node:crypto";
import type {
	AgentEndEvent,
	AgentErrorEvent,
	AgentEvent,
	AgentRunResult,
	AgentStartEvent,
	MessageUpdateEvent,
	ToolExecutionEvent,
	TurnEndEvent,
	TurnStartEvent,
} from "../models/events.js";
import { isJsonObject, stringifyJson, type JsonObject } from "../models/json.js";
import {
	createAssistantMessage,
	createToolResult,
	createUserMessage,
	type AgentMessage,
	type AgentStopReason,
	type AssistantContent,
	type AssistantMessage,
	type AssistantToolCallContent,
	type ErrorRecord,
	type ToolResultMessage,
	type UserMessage,
} from "../models/messages.js";
import { toolDefinition, ToolValidationError, type RuntimeTool } from "../models/tools.js";
import type { ModelStreamEvent, ProviderRequest } from "../providers/types.js";
import { Session } from "../session/session.js";
import type { Agent, AgentEventListener, AgentHooks, AgentOptions, AgentState, ToolExecutionMode } from "./types.js";

type PendingToolCall = {
	readonly id: string;
	readonly name: string;
	readonly rawArguments: string;
};

type AssistantCollection = {
	readonly message: AssistantMessage;
	readonly calls: readonly PendingToolCall[];
	readonly argumentErrors: ReadonlyMap<string, string>;
	readonly failure?: ErrorRecord;
	readonly aborted: boolean;
};

type ActiveRun = {
	readonly runId: string;
	readonly controller: AbortController;
	readonly promise: Promise<AgentRunResult>;
};

export class AgentCore implements Agent {
	private readonly session: Session;
	private readonly model: AgentOptions["model"];
	private readonly provider: AgentOptions["provider"];
	private readonly tools: readonly RuntimeTool[];
	private readonly toolExecutionMode: ToolExecutionMode;
	private readonly maxTurns: number;
	private readonly hooks: AgentHooks;
	private readonly listeners = new Set<AgentEventListener>();
	private contextMessages: AgentMessage[];
	private steeringQueue: UserMessage[] = [];
	private followUpQueue: UserMessage[] = [];
	private activeRun: ActiveRun | undefined;
	private stateValue: AgentState = { status: "idle", messages: [] };

	constructor(options: AgentOptions) {
		this.session = options.session;
		this.model = options.model;
		this.provider = options.provider;
		this.tools = options.tools ?? [];
		this.toolExecutionMode = options.toolExecutionMode ?? "sequential";
		this.maxTurns = options.maxTurns ?? 32;
		this.hooks = options.hooks ?? {};
		this.contextMessages = this.session.contextProjection();
		this.stateValue = { status: "idle", messages: [...this.contextMessages] };
	}

	get state(): AgentState {
		return {
			...this.stateValue,
			messages: [...this.stateValue.messages],
		};
	}

	subscribe(listener: AgentEventListener): () => void {
		this.listeners.add(listener);
		return () => this.listeners.delete(listener);
	}

	async prompt(input: UserMessage | string): Promise<AgentRunResult> {
		if (this.activeRun !== undefined) {
			throw new Error("Agent is already running; use steer or followUp for another message");
		}

		const userMessage = typeof input === "string" ? createUserMessage(input) : input;
		const runId = randomUUID();
		const controller = new AbortController();
		let resolveExecution: (result: AgentRunResult) => void = () => undefined;
		let rejectExecution: (error: unknown) => void = () => undefined;
		const execution = new Promise<AgentRunResult>((resolve, reject) => {
			resolveExecution = resolve;
			rejectExecution = reject;
		});
		const active: ActiveRun = { runId, controller, promise: execution };
		this.activeRun = active;
		this.stateValue = { status: "running", runId, messages: [...this.contextMessages] };

		void this.execute(runId, userMessage, controller.signal).then(resolveExecution, rejectExecution);
		try {
			return await execution;
		} finally {
			if (this.activeRun === active) {
				this.activeRun = undefined;
				this.stateValue = { status: "idle", messages: [...this.contextMessages] };
			}
		}
	}

	steer(message: UserMessage | string): void {
		this.steeringQueue.push(typeof message === "string" ? createUserMessage(message) : message);
	}

	followUp(message: UserMessage | string): void {
		this.followUpQueue.push(typeof message === "string" ? createUserMessage(message) : message);
	}

	async abort(): Promise<void> {
		const active = this.activeRun;
		if (active === undefined) {
			return;
		}
		this.stateValue = { ...this.stateValue, status: "aborting" };
		active.controller.abort();
		await active.promise;
	}

	private async execute(runId: string, initialMessage: UserMessage, signal: AbortSignal): Promise<AgentRunResult> {
		const newMessages: AgentMessage[] = [];
		try {
			await this.session.appendMessage(initialMessage);
			this.contextMessages.push(initialMessage);
			this.syncStateMessages();
			newMessages.push(initialMessage);
			await this.emit(agentStart(runId));

			let turn = 0;
			while (true) {
				if (signal.aborted) {
					return this.finish(runId, newMessages, "aborted", signalError("Agent run was aborted"));
				}
				const injected = this.drainSteering();
				for (const message of injected) {
					await this.appendUserMessage(message, newMessages);
				}

				turn += 1;
				if (turn > this.maxTurns) {
					return this.finish(runId, newMessages, "max_turns");
				}
				await this.emit(turnStart(runId, turn));

				const collection = await this.collectAssistant(runId, turn, signal);
				await this.session.appendMessage(collection.message);
				this.contextMessages.push(collection.message);
				this.syncStateMessages();
				newMessages.push(collection.message);

				if (collection.failure !== undefined) {
					await this.emit(agentError(runId, "model", collection.failure));
					await this.emit(turnEnd(runId, turn, collection.message, [], "model_error"));
					return await this.finish(runId, newMessages, "model_error", collection.failure);
				}
				if (collection.aborted || signal.aborted) {
					const error = signalError("Agent run was aborted");
					await this.emit(agentError(runId, "cancelled", error));
					await this.emit(turnEnd(runId, turn, collection.message, [], "aborted"));
					return await this.finish(runId, newMessages, "aborted", error);
				}

				if (collection.calls.length > 0) {
					const toolResults = await this.executeToolCalls(runId, turn, collection, signal);
					for (const result of toolResults) {
						await this.session.appendToolResult(result);
						this.contextMessages.push(result);
						this.syncStateMessages();
						newMessages.push(result);
					}
					const hasToolError = toolResults.some((result) => result.isError);
					if (hasToolError) {
						const firstError = toolResults.find((result) => result.isError);
						await this.emit(
							agentError(runId, "tool", {
								name: "ToolError",
								message: firstError?.content ?? "A tool call failed",
							}),
						);
					}
					await this.emit(turnEnd(runId, turn, collection.message, toolResults, hasToolError ? "tool_error" : "tool_calls"));
					if (signal.aborted) {
						const error = signalError("Agent run was aborted");
						await this.emit(agentError(runId, "cancelled", error));
						return await this.finish(runId, newMessages, "aborted", error);
					}
					continue;
				}

				await this.emit(turnEnd(runId, turn, collection.message, [], "completed"));
				const steering = this.drainSteering();
				if (steering.length > 0) {
					for (const message of steering) {
						await this.appendUserMessage(message, newMessages);
					}
					continue;
				}
				const followUps = this.drainFollowUps();
				if (followUps.length > 0) {
					for (const message of followUps) {
						await this.appendUserMessage(message, newMessages);
					}
					continue;
				}
				return await this.finish(runId, newMessages, "completed");
			}
		} catch (error) {
			const record = toErrorRecord(error);
			await this.emit(agentError(runId, "session", record));
			return await this.finish(runId, newMessages, "session_error", record);
		}
	}

	private async collectAssistant(runId: string, turn: number, signal: AbortSignal): Promise<AssistantCollection> {
		const assistantId = randomUUID();
		let text = "";
		let providerStopReason: AssistantMessage["stopReason"] = "end_turn";
		let failure: ErrorRecord | undefined;
		let aborted = false;
		const calls = new Map<string, { name: string; rawArguments: string }>();
		const argumentErrors = new Map<string, string>();
		const request: ProviderRequest = {
			model: this.model,
			messages: [...this.contextMessages],
			tools: this.tools.map(toolDefinition),
			signal,
			...([...this.contextMessages].reverse().find((message): message is UserMessage => message.role === "user")?.responseSchema === undefined ? {} : { responseSchema: [...this.contextMessages].reverse().find((message): message is UserMessage => message.role === "user")?.responseSchema }),
		};

		try {
			for await (const event of this.provider.stream(request)) {
				if (signal.aborted) {
					aborted = true;
					break;
				}
				if (event.type === "text_delta") {
					text += event.text;
					await this.emit(messageUpdate(runId, turn, assistantId, { kind: "text_delta", text: event.text }));
					continue;
				}
				if (event.type === "tool_call_delta") {
					const previous = calls.get(event.callId) ?? { name: "", rawArguments: "" };
					calls.set(event.callId, {
						name: event.name ?? previous.name,
						rawArguments: previous.rawArguments + (event.argumentsDelta ?? ""),
					});
					await this.emit(
						messageUpdate(runId, turn, assistantId, {
							kind: "tool_call_delta",
							callId: event.callId,
							...(event.name === undefined ? {} : { name: event.name }),
							...(event.argumentsDelta === undefined ? {} : { argumentsDelta: event.argumentsDelta }),
						}),
					);
					continue;
				}
				if (event.type === "complete") {
					providerStopReason = event.stopReason;
					continue;
				}
				if (event.type === "failure") {
					failure = toErrorRecord(event.error);
					break;
				}
			}
		} catch (error) {
			if (signal.aborted || isAbortError(error)) {
				aborted = true;
			} else {
				failure = toErrorRecord(error);
			}
		}

		const contents: AssistantContent[] = [];
		if (text.length > 0) {
			contents.push({ kind: "text", text });
		}
		const pendingCalls: PendingToolCall[] = [];
		for (const [id, call] of calls) {
			let parsedArguments: unknown = {};
			try {
				parsedArguments = JSON.parse(call.rawArguments.length === 0 ? "{}" : call.rawArguments) as unknown;
			} catch (error) {
				argumentErrors.set(id, `Invalid JSON tool arguments: ${toErrorRecord(error).message}`);
			}
			let argumentsObject: JsonObject;
			if (!isJsonObject(parsedArguments)) {
				argumentErrors.set(id, "Tool arguments must be a JSON object");
				argumentsObject = {};
			} else {
				argumentsObject = parsedArguments;
			}
			contents.push({
				kind: "tool_call",
				id,
				name: call.name,
				arguments: argumentsObject,
			} satisfies AssistantToolCallContent);
			pendingCalls.push({ id, name: call.name, rawArguments: call.rawArguments });
		}

		const stopReason = aborted ? "aborted" : failure === undefined ? (pendingCalls.length > 0 ? "tool_calls" : providerStopReason) : "error";
		return {
			message: createAssistantMessage(contents, {
				provider: this.provider.id,
				model: this.model.model,
				stopReason,
				...(failure === undefined ? {} : { error: failure }),
				id: assistantId,
			}),
			calls: pendingCalls,
			argumentErrors,
			failure,
			aborted,
		};
	}

	private async executeToolCalls(
		runId: string,
		turn: number,
		collection: AssistantCollection,
		signal: AbortSignal,
	): Promise<ToolResultMessage<unknown>[]> {
		if (this.toolExecutionMode === "sequential") {
			const results: ToolResultMessage<unknown>[] = [];
			for (const call of collection.calls) {
				results.push(await this.executeToolCall(runId, turn, call, collection.argumentErrors.get(call.id), signal));
			}
			return results;
		}

		return Promise.all(
			collection.calls.map((call) => this.executeToolCall(runId, turn, call, collection.argumentErrors.get(call.id), signal)),
		);
	}

	private async executeToolCall(
		runId: string,
		turn: number,
		call: PendingToolCall,
		argumentError: string | undefined,
		signal: AbortSignal,
	): Promise<ToolResultMessage<unknown>> {
		const tool = this.tools.find((candidate) => candidate.name === call.name);
		const rawArguments = this.toolArguments(call);
		await this.emit(toolStart(runId, turn, call, rawArguments));
		let result: ToolResultMessage<unknown>;
		let validatedArguments = rawArguments;
		try {
			if (argumentError !== undefined) {
				throw new ToolValidationError(argumentError);
			}
			if (tool === undefined) {
				throw new Error(`Unknown tool: ${call.name}`);
			}
			const argumentsObject = this.getAssistantArguments(call);
			const validated = tool.validate(argumentsObject);
			validatedArguments = validated;
			const before = await this.hooks.beforeToolCall?.({
				runId,
				turn,
				callId: call.id,
				name: call.name,
				arguments: validated,
				tool,
			});
			if (before !== undefined && before.block) {
				throw new Error(before.reason ?? `Tool call ${call.name} was blocked`);
			}
			const details = await tool.execute(validated, signal);
			result = createToolResult({
				toolCallId: call.id,
				toolName: call.name,
				content: stringifyJson(details),
				details,
				isError: false,
			});
		} catch (error) {
			const errorRecord = toErrorRecord(error);
			result = createToolResult({
				toolCallId: call.id,
				toolName: call.name,
				content: errorRecord.message,
				details: { code: errorRecord.name, message: errorRecord.message },
				isError: true,
			});
		}
		try {
			result = await this.applyAfterToolCall(runId, turn, call, validatedArguments, tool, result);
		} catch (error) {
			const errorRecord = toErrorRecord(error);
			result = createToolResult({
				toolCallId: call.id,
				toolName: call.name,
				content: errorRecord.message,
				details: { code: errorRecord.name, message: errorRecord.message },
				isError: true,
			});
		}
		await this.emit(toolEnd(runId, turn, call, result));
		return result;
	}

	private async applyAfterToolCall(
		runId: string,
		turn: number,
		call: PendingToolCall,
		argumentsObject: JsonObject,
		tool: RuntimeTool | undefined,
		result: ToolResultMessage<unknown>,
	): Promise<ToolResultMessage<unknown>> {
		const after = await this.hooks.afterToolCall?.({
			runId,
			turn,
			callId: call.id,
			name: call.name,
			arguments: argumentsObject,
			tool,
			result,
		});
		if (after === undefined) {
			return result;
		}
		return {
			...result,
			...(after.content === undefined ? {} : { content: after.content }),
			...(after.details === undefined ? {} : { details: after.details }),
			...(after.isError === undefined ? {} : { isError: after.isError }),
		};
	}

	private getAssistantArguments(call: PendingToolCall): JsonObject {
		let assistant: AssistantMessage | undefined;
		for (let index = this.contextMessages.length - 1; index >= 0; index -= 1) {
			const message = this.contextMessages[index];
			if (
				message !== undefined &&
				message.role === "assistant" &&
				message.content.some((part) => part.kind === "tool_call" && part.id === call.id)
			) {
				assistant = message;
				break;
			}
		}
		const part = assistant?.content.find(
			(candidate): candidate is AssistantToolCallContent => candidate.kind === "tool_call" && candidate.id === call.id,
		);
		return part?.arguments ?? {};
	}

	private toolArguments(call: PendingToolCall): JsonObject {
		return this.getAssistantArguments(call);
	}

	private async appendUserMessage(message: UserMessage, newMessages: AgentMessage[]): Promise<void> {
		await this.session.appendMessage(message);
		this.contextMessages.push(message);
		this.syncStateMessages();
		newMessages.push(message);
	}

	private syncStateMessages(): void {
		this.stateValue = { ...this.stateValue, messages: [...this.contextMessages] };
	}

	private drainSteering(): UserMessage[] {
		const messages = this.steeringQueue;
		this.steeringQueue = [];
		return messages;
	}

	private drainFollowUps(): UserMessage[] {
		const messages = this.followUpQueue;
		this.followUpQueue = [];
		return messages;
	}

	private async finish(
		runId: string,
		messages: readonly AgentMessage[],
		stopReason: AgentStopReason,
		error?: ErrorRecord,
	): Promise<AgentRunResult> {
		const result: AgentRunResult = {
			runId,
			messages: [...messages],
			stopReason,
			...(error === undefined ? {} : { error }),
		};
		await this.emit(agentEnd(runId, result));
		return result;
	}

	private async emit(event: AgentEvent): Promise<void> {
		for (const listener of this.listeners) {
			try {
				await listener(event);
			} catch {
				// Observers are not allowed to break the agent loop.
			}
		}
	}
}

function agentStart(runId: string): AgentStartEvent {
	return { type: "agent_start", runId, timestamp: Date.now() };
}

function turnStart(runId: string, turn: number): TurnStartEvent {
	return { type: "turn_start", runId, turn, timestamp: Date.now() };
}

function messageUpdate(
	runId: string,
	turn: number,
	messageId: string,
	update: MessageUpdateEvent["update"],
): MessageUpdateEvent {
	return { type: "message_update", runId, turn, messageId, update, timestamp: Date.now() };
}

function toolStart(runId: string, turn: number, call: PendingToolCall, argumentsObject: JsonObject): ToolExecutionEvent {
	return {
		type: "tool_execution",
		runId,
		turn,
		phase: "start",
		callId: call.id,
		name: call.name,
		arguments: argumentsObject,
		timestamp: Date.now(),
	};
}

function toolEnd(runId: string, turn: number, call: PendingToolCall, result: ToolResultMessage<unknown>): ToolExecutionEvent {
	return {
		type: "tool_execution",
		runId,
		turn,
		phase: "end",
		callId: call.id,
		name: call.name,
		result: result.details,
		isError: result.isError,
		timestamp: Date.now(),
	};
}

function turnEnd(
	runId: string,
	turn: number,
	message: AssistantMessage,
	toolResults: readonly ToolResultMessage<unknown>[],
	stopReason: AgentStopReason,
): TurnEndEvent {
	return { type: "turn_end", runId, turn, message, toolResults, stopReason, timestamp: Date.now() };
}

function agentEnd(runId: string, result: AgentRunResult): AgentEndEvent {
	return { type: "agent_end", runId, result, timestamp: Date.now() };
}

function agentError(runId: string, phase: AgentErrorEvent["phase"], error: ErrorRecord): AgentErrorEvent {
	return { type: "agent_error", runId, phase, error, timestamp: Date.now() };
}

function toErrorRecord(error: unknown): ErrorRecord {
	if (error instanceof Error) {
		return {
			name: error.name,
			message: error.message,
			...(error.stack === undefined ? {} : { stack: error.stack }),
		};
	}
	return { name: "UnknownError", message: String(error) };
}

function signalError(message: string): ErrorRecord {
	return { name: "AbortError", message };
}

function isAbortError(error: unknown): boolean {
	return error instanceof Error && (error.name === "AbortError" || error.name === "CanceledError");
}
