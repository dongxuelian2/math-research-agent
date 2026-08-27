import { randomUUID } from "node:crypto";
import type { JsonObject } from "./json.js";

export type UserMessage = {
	readonly role: "user";
	readonly id: string;
	readonly content: string;
	readonly timestamp: number;
	/** Optional machine contract for the next model response. */
	readonly responseSchema?: JsonObject;
};

export type AssistantTextContent = {
	readonly kind: "text";
	readonly text: string;
};

export type AssistantToolCallContent = {
	readonly kind: "tool_call";
	readonly id: string;
	readonly name: string;
	readonly arguments: JsonObject;
};

export type AssistantContent = AssistantTextContent | AssistantToolCallContent;

export type ModelStopReason = "end_turn" | "tool_calls" | "length" | "error" | "aborted";

export type AssistantMessage = {
	readonly role: "assistant";
	readonly id: string;
	readonly content: readonly AssistantContent[];
	readonly stopReason: ModelStopReason;
	readonly provider: string;
	readonly model: string;
	readonly timestamp: number;
	readonly usage?: Usage;
	readonly error?: ErrorRecord;
};

export type ToolResultMessage<TDetails = unknown> = {
	readonly role: "tool_result";
	readonly id: string;
	readonly toolCallId: string;
	readonly toolName: string;
	readonly content: string;
	readonly details: TDetails;
	readonly isError: boolean;
	readonly timestamp: number;
};

export type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage<unknown>;

export type ContextMessage = UserMessage | AssistantMessage | ToolResultMessage<unknown>;

export type Usage = {
	readonly inputTokens?: number;
	readonly outputTokens?: number;
	readonly totalTokens?: number;
};

export type ErrorRecord = {
	readonly name: string;
	readonly message: string;
	readonly stack?: string;
};

export type AgentStopReason =
	| "completed"
	| "tool_calls"
	| "model_error"
	| "tool_error"
	| "aborted"
	| "max_turns"
	| "session_error";

export function createUserMessage(content: string, responseSchema?: JsonObject): UserMessage {
	return {
		role: "user",
		id: randomUUID(),
		content,
		timestamp: Date.now(),
		...(responseSchema === undefined ? {} : { responseSchema }),
	};
}

export function createAssistantMessage(
	content: readonly AssistantContent[],
	request: { provider: string; model: string; stopReason: ModelStopReason; error?: ErrorRecord; id?: string },
): AssistantMessage {
	return {
		role: "assistant",
		id: request.id ?? randomUUID(),
		content,
		provider: request.provider,
		model: request.model,
		stopReason: request.stopReason,
		timestamp: Date.now(),
		...(request.error === undefined ? {} : { error: request.error }),
	};
}

export function createToolResult<TDetails>(
	request: {
		toolCallId: string;
		toolName: string;
		content: string;
		details: TDetails;
		isError: boolean;
	},
): ToolResultMessage<TDetails> {
	return {
		role: "tool_result",
		id: randomUUID(),
		toolCallId: request.toolCallId,
		toolName: request.toolName,
		content: request.content,
		details: request.details,
		isError: request.isError,
		timestamp: Date.now(),
	};
}
