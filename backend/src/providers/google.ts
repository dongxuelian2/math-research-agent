import { FetchTransport } from "./http.js";
import { asArray, asRecord, asString, jsonString, parseJson } from "./parse.js";
import { parseSse } from "./sse.js";
import {
	resolveCredential,
	type ModelProvider,
	type ModelStreamEvent,
	type ProviderRequest,
	type ProviderTransport,
} from "./types.js";

export interface GoogleProviderOptions {
	readonly transport?: ProviderTransport;
	readonly defaultBaseUrl?: string;
}

export class GoogleProvider implements ModelProvider {
	readonly id = "google" as const;
	private readonly transport: ProviderTransport;
	private readonly defaultBaseUrl: string;

	constructor(options: GoogleProviderOptions = {}) {
		this.transport = options.transport ?? new FetchTransport();
		this.defaultBaseUrl = options.defaultBaseUrl ?? "https://generativelanguage.googleapis.com/v1beta";
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			const credential = await resolveCredential(request.model);
			const baseUrl = (request.model.baseUrl ?? this.defaultBaseUrl).replace(/\/$/, "");
			const query = credential === undefined ? "" : `?alt=sse&key=${encodeURIComponent(credential)}`;
			const body: Record<string, unknown> = {
				contents: toGoogleContents(request),
				...request.model.requestParameters,
			};
			if (request.tools.length > 0) {
				body.tools = [
					{
						functionDeclarations: request.tools.map((tool) => ({
							name: tool.name,
							description: tool.description,
							parameters: tool.parameters,
						})),
					},
				];
			}
			if (request.model.maxTokens !== undefined || request.model.reasoningEffort !== undefined) {
				body.generationConfig = {
					...(request.model.maxTokens === undefined ? {} : { maxOutputTokens: request.model.maxTokens }),
					...(request.model.reasoningEffort === undefined
						? {}
						: { thinkingConfig: { thinkingBudget: thinkingBudget(request.model.reasoningEffort) } }),
				};
			}

			const events = parseSse(
				this.transport.stream({
					url: `${baseUrl}/models/${encodeURIComponent(request.model.model)}:streamGenerateContent${query}`,
					method: "POST",
					headers: { "content-type": "application/json", ...request.model.requestHeaders },
					body: JSON.stringify(body),
					signal: request.signal,
				}),
			);

			let completed = false;
			let callNumber = 0;
			for await (const event of events) {
				const payload = parseJson(event.data);
				if (payload === undefined) {
					continue;
				}
				const candidates = asArray(payload.candidates);
				const candidate = candidates === undefined ? undefined : asRecord(candidates[0]);
				const content = candidate === undefined ? undefined : asRecord(candidate.content);
				const parts = content === undefined ? undefined : asArray(content.parts);
				if (parts !== undefined) {
					for (const rawPart of parts) {
						const part = asRecord(rawPart);
						if (part === undefined) {
							continue;
						}
						const text = asString(part.text);
						if (text !== undefined && text.length > 0) {
							yield { type: "text_delta", text };
						}
						const functionCall = asRecord(part.functionCall);
						if (functionCall !== undefined) {
							const callId = `google-call-${callNumber}`;
							callNumber += 1;
							yield {
								type: "tool_call_delta",
								callId,
								...(asString(functionCall.name) === undefined
									? {}
									: { name: asString(functionCall.name) }),
								argumentsDelta: jsonString(functionCall.args ?? {}),
							};
						}
					}
				}

				const finishReason = candidate === undefined ? undefined : asString(candidate.finishReason);
				if (finishReason !== undefined && !completed) {
					completed = true;
					yield {
						type: "complete",
						stopReason:
							finishReason === "MAX_TOKENS"
								? "length"
								: finishReason === "STOP"
									? "end_turn"
									: "tool_calls",
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

function toGoogleContents(request: ProviderRequest): readonly Record<string, unknown>[] {
		const contents: Record<string, unknown>[] = [];
		for (const message of request.messages) {
			if (message.role === "user") {
				contents.push({ role: "user", parts: [{ text: message.content }] });
				continue;
			}
			if (message.role === "assistant") {
				contents.push({
					role: "model",
					parts: message.content.map((part) =>
						part.kind === "text"
							? { text: part.text }
							: { functionCall: { name: part.name, args: part.arguments } },
					),
				});
				continue;
			}
			contents.push({
				role: "user",
				parts: [
					{
						functionResponse: {
							name: message.toolName,
							response: { result: message.content },
						},
					},
				],
			});
		}
		return contents;
}

function thinkingBudget(effort: "low" | "medium" | "high"): number {
	return effort === "low" ? 1024 : effort === "medium" ? 4096 : 8192;
}
