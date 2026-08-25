import type { JsonObject, JsonValue } from "./json.js";

export interface JsonSchema {
	readonly type?: string;
	readonly description?: string;
	readonly properties?: Readonly<Record<string, JsonSchema>>;
	readonly required?: readonly string[];
	readonly additionalProperties?: boolean;
	readonly enum?: readonly JsonValue[];
}

export interface AgentTool<TParameters extends JsonObject, TDetails> {
	readonly name: string;
	readonly description: string;
	readonly parameters: JsonSchema;

	validate(input: unknown): TParameters;
	execute(args: TParameters, signal?: AbortSignal): Promise<TDetails>;
}

export type RuntimeTool = AgentTool<JsonObject, unknown>;

export type ToolDefinition = {
	readonly name: string;
	readonly description: string;
	readonly parameters: JsonSchema;
};

export class ToolValidationError extends Error {
	readonly code = "invalid_tool_arguments";

	constructor(message: string) {
		super(message);
		this.name = "ToolValidationError";
	}
}

export function defineTool<TParameters extends JsonObject, TDetails>(
	tool: AgentTool<TParameters, TDetails>,
): AgentTool<TParameters, TDetails> {
	return tool;
}

export function asRuntimeTool<TParameters extends JsonObject, TDetails>(
	tool: AgentTool<TParameters, TDetails>,
): RuntimeTool {
	return {
		name: tool.name,
		description: tool.description,
		parameters: tool.parameters,
		validate(input: unknown): JsonObject {
			return tool.validate(input);
		},
		async execute(args: JsonObject, signal?: AbortSignal): Promise<unknown> {
			return tool.execute(tool.validate(args), signal);
		},
	};
}

export function toolDefinition(tool: RuntimeTool): ToolDefinition {
	return {
		name: tool.name,
		description: tool.description,
		parameters: tool.parameters,
	};
}
