import type { AgentMessage, ModelStopReason, Usage } from "../models/messages.js";
import type { JsonValue } from "../models/json.js";
import type { ToolDefinition } from "../models/tools.js";

export type ProviderId =
	| "mock"
	| "openai"
	| "openai-codex"
	| "anthropic"
	| "google"
	| "openrouter"
	| "deepseek";

export type ReasoningEffort = "low" | "medium" | "high";

export type CredentialResolver = () => string | undefined | Promise<string | undefined>;

export interface ModelConfig {
	readonly provider: ProviderId;
	readonly model: string;
	readonly baseUrl?: string;
	readonly credentialResolver?: CredentialResolver;
	readonly reasoningEffort?: ReasoningEffort;
	readonly contextWindow?: number;
	readonly maxTokens?: number;
	readonly requestHeaders?: Readonly<Record<string, string>>;
	readonly requestParameters?: Readonly<Record<string, JsonValue>>;
}

export type ProviderRequest = {
	readonly model: ModelConfig;
	readonly messages: readonly AgentMessage[];
	readonly tools: readonly ToolDefinition[];
	readonly signal?: AbortSignal;
};

export type ModelTextDelta = {
	readonly type: "text_delta";
	readonly text: string;
};

export type ModelToolCallDelta = {
	readonly type: "tool_call_delta";
	readonly callId: string;
	readonly name?: string;
	readonly argumentsDelta?: string;
};

export type ModelComplete = {
	readonly type: "complete";
	readonly stopReason: Exclude<ModelStopReason, "error" | "aborted">;
	readonly usage?: Usage;
};

export type ModelFailure = {
	readonly type: "failure";
	readonly error: unknown;
	readonly retryable?: boolean;
};

export type ModelStreamEvent = ModelTextDelta | ModelToolCallDelta | ModelComplete | ModelFailure;

export interface ModelProvider {
	readonly id: ProviderId;
	stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent>;
}

export type TransportRequest = {
	readonly url: string;
	readonly method: "POST";
	readonly headers: Readonly<Record<string, string>>;
	readonly body: string;
	readonly signal?: AbortSignal;
};

export interface ProviderTransport {
	stream(request: TransportRequest): AsyncIterable<string>;
}

export function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

export async function resolveCredential(model: ModelConfig): Promise<string | undefined> {
	return model.credentialResolver === undefined ? undefined : model.credentialResolver();
}
