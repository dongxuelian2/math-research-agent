import type { AgentEvent, AgentRunResult } from "../models/events.js";
import type { JsonObject } from "../models/json.js";
import type { AgentMessage, ToolResultMessage, UserMessage } from "../models/messages.js";
import type { Session } from "../session/session.js";
import type { ModelConfig, ModelProvider } from "../providers/types.js";
import type { RuntimeTool } from "../models/tools.js";

export type ToolExecutionMode = "sequential" | "parallel";

export type AgentStatus = "idle" | "running" | "aborting";

export type AgentState = {
	readonly status: AgentStatus;
	readonly runId?: string;
	readonly messages: readonly AgentMessage[];
};

export type BeforeToolCallContext = {
	readonly runId: string;
	readonly turn: number;
	readonly callId: string;
	readonly name: string;
	readonly arguments: JsonObject;
	readonly tool?: RuntimeTool;
};

export type BeforeToolCallResult =
	| void
	| {
			readonly block: true;
			readonly reason?: string;
	  };

export type AfterToolCallContext = {
	readonly runId: string;
	readonly turn: number;
	readonly callId: string;
	readonly name: string;
	readonly arguments: JsonObject;
	readonly tool?: RuntimeTool;
	readonly result: ToolResultMessage<unknown>;
};

export type AfterToolCallResult =
	| void
	| {
			readonly content?: string;
			readonly details?: unknown;
			readonly isError?: boolean;
	  };

export interface AgentHooks {
	readonly beforeToolCall?: (context: BeforeToolCallContext) => BeforeToolCallResult | Promise<BeforeToolCallResult>;
	readonly afterToolCall?: (context: AfterToolCallContext) => AfterToolCallResult | Promise<AfterToolCallResult>;
}

export interface AgentOptions {
	readonly session: Session;
	readonly model: ModelConfig;
	readonly provider: ModelProvider;
	readonly tools?: readonly RuntimeTool[];
	readonly toolExecutionMode?: ToolExecutionMode;
	readonly maxTurns?: number;
	readonly hooks?: AgentHooks;
}

export interface Agent {
	readonly state: AgentState;

	prompt(input: UserMessage | string): Promise<AgentRunResult>;
	steer(message: UserMessage | string): void;
	followUp(message: UserMessage | string): void;
	abort(): Promise<void>;

	subscribe(listener: AgentEventListener): () => void;
}

export type AgentEventListener = (event: AgentEvent) => void | Promise<void>;
