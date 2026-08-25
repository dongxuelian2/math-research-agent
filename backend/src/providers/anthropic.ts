import { FetchTransport } from "./http.js";
import { asArray, asNumber, asRecord, asString, jsonString, parseJson } from "./parse.js";
import { parseSse } from "./sse.js";
import {
	resolveCredential,
	type ModelProvider,
	type ModelStreamEvent,
	type ProviderRequest,
	type ProviderTransport,
} from "./types.js";

export interface AnthropicProviderOptions {
	readonly transport?: ProviderTransport;
	readonly defaultBaseUrl?: string;
}

export class AnthropicProvider implements ModelProvider {
	readonly id = "anthropic" as const;
	private readonly transport: ProviderTransport;
	private readonly defaultBaseUrl: string;

	constructor(options: AnthropicProviderOptions = {}) {
		this.transport = options.transport ?? new FetchTransport();
		this.defaultBaseUrl = options.defaultBaseUrl ?? "https://api.anthropic.com";
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			const credential = await resolveCredential(request.model);
			const headers: Record<string, string> = {
				"content-type": "application/json",
				"anthropic-version": "2023-06-01",
				...request.model.requestHeaders,
			};
			if (credential !== undefined) {
				headers["x-api-key"] = credential;
			}

			const body: Record<string, unknown> = {
				model: request.model.model,
				max_tokens: request.model.maxTokens ?? 4096,
				stream: true,
				messages: toAnthropicMessages(request),
				...request.model.requestParameters,
			};
			if (request.tools.length > 0) {
				body.tools = request.tools.map((tool) => ({
					name: tool.name,
					description: tool.description,
					input_schema: tool.parameters,
				}));
			}

			const transport = this.transport.stream({
				url: `${(request.model.baseUrl ?? this.defaultBaseUrl).replace(/\/$/, "")}/v1/messages`,
				method: "POST",
				headers,
				body: JSON.stringify(body),
				signal: request.signal,
			});
			const toolIds = new Map<number, string>();
			let completed = false;

			for await (const event of parseSse(transport)) {
				const payload = parseJson(event.data);
				if (payload === undefined) {
					continue;
				}

				if (event.event === "content_block_start") {
					const index = asNumber(payload.index);
					const block = asRecord(payload.content_block);
					if (index !== undefined && block !== undefined && asString(block.type) === "tool_use") {
						const id = asString(block.id) ?? `anthropic-call-${index}`;
						toolIds.set(index, id);
						yield {
							type: "tool_call_delta",
							callId: id,
							...(asString(block.name) === undefined ? {} : { name: asString(block.name) }),
						};
					}
				}

				if (event.event === "content_block_delta") {
					const index = asNumber(payload.index);
					const delta = asRecord(payload.delta);
					if (delta === undefined) {
						continue;
					}
					if (asString(delta.type) === "text_delta") {
						const text = asString(delta.text);
						if (text !== undefined && text.length > 0) {
							yield { type: "text_delta", text };
						}
					}
					if (asString(delta.type) === "input_json_delta" && index !== undefined) {
						const callId = toolIds.get(index) ?? `anthropic-call-${index}`;
						const partialJson = asString(delta.partial_json);
						yield {
							type: "tool_call_delta",
							callId,
							...(partialJson === undefined ? {} : { argumentsDelta: partialJson }),
						};
					}
				}

				if (event.event === "message_delta") {
					const delta = asRecord(payload.delta);
					const stopReason = delta === undefined ? undefined : asString(delta.stop_reason);
					if (stopReason !== undefined && !completed) {
						completed = true;
						yield {
							type: "complete",
							stopReason:
								stopReason === "tool_use"
									? "tool_calls"
									: stopReason === "max_tokens"
										? "length"
										: "end_turn",
						};
					}
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

function toAnthropicMessages(request: ProviderRequest): readonly Record<string, unknown>[] {
		const messages: Record<string, unknown>[] = [];
		for (const message of request.messages) {
			if (message.role === "user") {
				messages.push({ role: "user", content: message.content });
				continue;
			}
			if (message.role === "assistant") {
				messages.push({
					role: "assistant",
					content: message.content.map((part) =>
						part.kind === "text"
							? { type: "text", text: part.text }
							: { type: "tool_use", id: part.id, name: part.name, input: part.arguments },
					),
				});
				continue;
			}
			messages.push({
				role: "user",
				content: [
					{
						type: "tool_result",
						tool_use_id: message.toolCallId,
						content: message.content,
					},
				],
			});
		}
		return messages;
}

export function anthropicToolArguments(value: unknown): string {
	return jsonString(value);
}
