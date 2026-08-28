import { isJsonObject } from "../models/json.js";
import { FetchTransport } from "./http.js";
import { asArray, asRecord, asString, jsonString, parseJson } from "./parse.js";
import { parseSse } from "./sse.js";
import {
	errorMessage,
	resolveCredential,
	type ModelProvider,
	type ModelStreamEvent,
	type ProviderId,
	type ProviderRequest,
	type ProviderTransport,
	type TransportRequest,
} from "./types.js";

export interface OpenAICompatibleOptions {
	readonly id: Extract<ProviderId, "openai" | "openrouter" | "deepseek">;
	readonly defaultBaseUrl: string;
	readonly transport?: ProviderTransport;
}

export class OpenAICompatibleProvider implements ModelProvider {
	readonly id: OpenAICompatibleOptions["id"];
	private readonly defaultBaseUrl: string;
	private readonly transport: ProviderTransport;

	constructor(options: OpenAICompatibleOptions) {
		this.id = options.id;
		this.defaultBaseUrl = options.defaultBaseUrl;
		this.transport = options.transport ?? new FetchTransport();
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			const credential = await resolveCredential(request.model);
			const headers: Record<string, string> = {
				"content-type": "application/json",
				...request.model.requestHeaders,
			};
			if (credential !== undefined) {
				headers.authorization = `Bearer ${credential}`;
			}

			const body: Record<string, unknown> = {
				model: request.model.model,
				messages: toOpenAIMessages(request),
				stream: true,
				...request.model.requestParameters,
			};
			if (request.tools.length > 0) {
				body.tools = request.tools.map((tool) => ({
					type: "function",
					function: {
						name: tool.name,
						description: tool.description,
						parameters: tool.parameters,
					},
				}));
			}
			if (request.model.maxTokens !== undefined) {
				body.max_tokens = request.model.maxTokens;
			}
			if (request.model.reasoningEffort !== undefined) {
				body.reasoning_effort = request.model.reasoningEffort;
			}
			if (request.responseSchema !== undefined) {
				body.response_format = { type: "json_schema", json_schema: { name: "mrr_structured_response", strict: true, schema: request.responseSchema } };
			}

			const transportRequest: TransportRequest = {
				url: `${(request.model.baseUrl ?? this.defaultBaseUrl).replace(/\/$/, "")}/chat/completions`,
				method: "POST",
				headers,
				body: JSON.stringify(body),
				signal: request.signal,
			};

			let completed = false;
			const toolIds = new Map<number, string>();
			for await (const event of parseSse(this.transport.stream(transportRequest))) {
				if (event.data === "[DONE]") {
					if (!completed) {
						completed = true;
						yield { type: "complete", stopReason: "end_turn" };
					}
					continue;
				}

				const payload = parseJson(event.data);
				if (payload === undefined) {
					continue;
				}

				const choices = asArray(payload.choices);
				const firstChoice = choices === undefined ? undefined : asRecord(choices[0]);
				const delta = firstChoice === undefined ? undefined : asRecord(firstChoice.delta);
				const text = delta === undefined ? undefined : asString(delta.content);
				if (text !== undefined && text.length > 0) {
					yield { type: "text_delta", text };
				}

				const toolCalls = delta === undefined ? undefined : asArray(delta.tool_calls);
				if (toolCalls !== undefined) {
					for (let toolIndex = 0; toolIndex < toolCalls.length; toolIndex += 1) {
						const rawToolCall = toolCalls[toolIndex];
						const toolCall = asRecord(rawToolCall);
						if (toolCall === undefined) {
							continue;
						}
						const functionCall = asRecord(toolCall.function);
						const providerMetadata = isJsonObject(toolCall.extra_content)
							? toolCall.extra_content
							: functionCall !== undefined && isJsonObject(functionCall.extra_content)
								? functionCall.extra_content
								: undefined;
						const announcedId = asString(toolCall.id);
						if (announcedId !== undefined) {
							toolIds.set(toolIndex, announcedId);
						}
						const callId = announcedId ?? toolIds.get(toolIndex) ?? `call-${toolIndex}`;
						yield {
							type: "tool_call_delta",
							callId,
							...(functionCall === undefined || asString(functionCall.name) === undefined
								? {}
								: { name: asString(functionCall.name) }),
							...(functionCall === undefined || asString(functionCall.arguments) === undefined
								? {}
								: { argumentsDelta: asString(functionCall.arguments) }),
							...(providerMetadata === undefined ? {} : { providerMetadata }),
						};
					}
				}

				const finishReason = firstChoice === undefined ? undefined : asString(firstChoice.finish_reason);
				if (finishReason !== undefined && !completed) {
					completed = true;
					yield {
						type: "complete",
						stopReason:
							finishReason === "tool_calls"
								? "tool_calls"
								: finishReason === "length"
									? "length"
									: "end_turn",
					};
				}
			}

			if (!completed) {
				yield { type: "complete", stopReason: "end_turn" };
			}
		} catch (error) {
			yield { type: "failure", error, retryable: false };
		}
	}
}

function toOpenAIMessages(request: ProviderRequest): readonly Record<string, unknown>[] {
		return request.messages.map((message) => {
			if (message.role === "user") {
				return { role: "user", content: message.content };
			}
			if (message.role === "tool_result") {
				return {
					role: "tool",
					tool_call_id: message.toolCallId,
					content: message.content,
				};
			}

			const text = message.content
				.filter((part): part is Extract<typeof part, { kind: "text" }> => part.kind === "text")
				.map((part) => part.text)
				.join("");
			const toolCalls = message.content
				.filter((part): part is Extract<typeof part, { kind: "tool_call" }> => part.kind === "tool_call")
				.map((part) => ({
					id: part.id,
					type: "function",
					function: { name: part.name, arguments: jsonString(part.arguments) },
					...(part.providerMetadata === undefined ? {} : { extra_content: part.providerMetadata }),
				}));
			return {
				role: "assistant",
				content: text.length === 0 ? null : text,
				...(toolCalls.length === 0 ? {} : { tool_calls: toolCalls }),
			};
		});
}

export function providerEndpointError(error: unknown): string {
	return errorMessage(error);
}
