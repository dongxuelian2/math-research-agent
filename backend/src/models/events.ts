import type {
	AgentMessage,
	AgentStopReason,
	AssistantMessage,
	ErrorRecord,
	ToolResultMessage,
} from "./messages.js";

export type AgentStartEvent = {
	readonly type: "agent_start";
	readonly runId: string;
	readonly timestamp: number;
};

export type TurnStartEvent = {
	readonly type: "turn_start";
	readonly runId: string;
	readonly turn: number;
	readonly timestamp: number;
};

export type MessageUpdateEvent = {
	readonly type: "message_update";
	readonly runId: string;
	readonly turn: number;
	readonly messageId: string;
	readonly update:
		| { readonly kind: "text_delta"; readonly text: string }
		| {
					readonly kind: "tool_call_delta";
					readonly callId: string;
					readonly name?: string;
					readonly argumentsDelta?: string;
				};
	readonly timestamp: number;
};

export type ToolExecutionEvent = {
	readonly type: "tool_execution";
	readonly runId: string;
	readonly turn: number;
	readonly phase: "start" | "end";
	readonly callId: string;
	readonly name: string;
	readonly arguments?: unknown;
	readonly result?: unknown;
	readonly isError?: boolean;
	readonly timestamp: number;
};

export type TurnEndEvent = {
	readonly type: "turn_end";
	readonly runId: string;
	readonly turn: number;
	readonly message: AssistantMessage;
	readonly toolResults: readonly ToolResultMessage<unknown>[];
	readonly stopReason: AgentStopReason;
	readonly timestamp: number;
};

export type AgentEndEvent = {
	readonly type: "agent_end";
	readonly runId: string;
	readonly result: AgentRunResult;
	readonly timestamp: number;
};

export type AgentErrorEvent = {
	readonly type: "agent_error";
	readonly runId: string;
	readonly phase: "model" | "tool" | "session" | "cancelled";
	readonly error: ErrorRecord;
	readonly timestamp: number;
};

export type AgentEvent =
	| AgentStartEvent
	| TurnStartEvent
	| MessageUpdateEvent
	| ToolExecutionEvent
	| TurnEndEvent
	| AgentEndEvent
	| AgentErrorEvent;

export type AgentRunResult = {
	readonly runId: string;
	readonly messages: readonly AgentMessage[];
	readonly stopReason: AgentStopReason;
	readonly error?: ErrorRecord;
};
