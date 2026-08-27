import { asArray, asRecord, asString, jsonString, parseJson } from "./parse.js";
import { parseSse } from "./sse.js";
import type { ModelStreamEvent, ProviderRequest, ReasoningEffort } from "./types.js";

export function googleRequestBody(request: ProviderRequest): Record<string, unknown> {
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

	const configuredGeneration = asRecord(request.model.requestParameters?.generationConfig);
	if (request.model.maxTokens !== undefined || request.model.reasoningEffort !== undefined || request.responseSchema !== undefined) {
		body.generationConfig = {
			...configuredGeneration,
			...(request.responseSchema === undefined ? {} : { responseMimeType: "application/json", responseJsonSchema: request.responseSchema }),
			...(request.model.maxTokens === undefined ? {} : { maxOutputTokens: request.model.maxTokens }),
			...(request.model.reasoningEffort === undefined
				? {}
				: {
						thinkingConfig: {
							...asRecord(configuredGeneration?.thinkingConfig),
							...(isGemini3Model(request.model.model)
								? { thinkingLevel: request.model.reasoningEffort }
								: { thinkingBudget: thinkingBudget(request.model.reasoningEffort) }),
						},
					}),
		};
	}
	return body;
}

export async function* parseGoogleStream(chunks: AsyncIterable<string>): AsyncIterable<ModelStreamEvent> {
	let completed = false;
	let callNumber = 0;
	for await (const event of parseSse(chunks)) {
		const payload = parseJson(event.data);
		if (payload === undefined) continue;
		const candidates = asArray(payload.candidates);
		const candidate = candidates === undefined ? undefined : asRecord(candidates[0]);
		const content = candidate === undefined ? undefined : asRecord(candidate.content);
		const parts = content === undefined ? undefined : asArray(content.parts);
		if (parts !== undefined) {
			for (const rawPart of parts) {
				const part = asRecord(rawPart);
				if (part === undefined) continue;
				const text = asString(part.text);
				if (text !== undefined && text.length > 0) yield { type: "text_delta", text };
				const functionCall = asRecord(part.functionCall);
				if (functionCall !== undefined) {
					const explicitId = asString(functionCall.id);
					const callId = explicitId ?? `google-call-${callNumber}`;
					if (explicitId === undefined) callNumber += 1;
					const thoughtSignature = asString(part.thoughtSignature) ?? asString(part.thought_signature);
					yield {
						type: "tool_call_delta",
						callId,
						...(asString(functionCall.name) === undefined ? {} : { name: asString(functionCall.name) }),
						argumentsDelta: jsonString(functionCall.args ?? {}),
						...(thoughtSignature === undefined ? {} : { providerMetadata: { google: { thought_signature: thoughtSignature } } }),
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
	if (!completed) yield { type: "complete", stopReason: "end_turn" };
}

function toGoogleContents(request: ProviderRequest): readonly Record<string, unknown>[] {
	const contents: Record<string, unknown>[] = [];
	for (const message of request.messages) {
		if (message.role === "user") {
			contents.push({ role: "user", parts: [{ text: message.content }] });
			continue;
		}
		if (message.role === "assistant") {
			const parts = message.content.map((part) =>
				part.kind === "text"
					? { text: part.text }
					: {
							functionCall: {
								name: part.name,
								args: part.arguments,
								...(isGemini3Model(request.model.model) ? { id: part.id } : {}),
							},
							...(googleThoughtSignature(part.providerMetadata) === undefined ? {} : { thoughtSignature: googleThoughtSignature(part.providerMetadata) }),
						},
			);
			// An interrupted/empty model turn can be retained in the Agent
			// history. Vertex rejects a content item whose parts array is empty;
			// omitting that no-op turn preserves the valid conversation state.
			if (parts.length === 0) continue;
			contents.push({
				role: "model",
				parts,
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
						...(isGemini3Model(request.model.model) ? { id: message.toolCallId } : {}),
					},
				},
			],
		});
	}
	if (contents.length === 0) {
		contents.push({ role: "user", parts: [{ text: "Continue." }] });
	}
	return contents;
}

function isGemini3Model(model: string): boolean {
	return /^gemini-3(?:\.|-)/u.test(model);
}

function thinkingBudget(effort: ReasoningEffort): number {
	return effort === "low" ? 1024 : effort === "medium" ? 4096 : 8192;
}

function googleThoughtSignature(metadata: import("../models/json.js").JsonObject | undefined): string | undefined {
	const google = asRecord(metadata?.google);
	return asString(google?.thought_signature) ?? asString(google?.thoughtSignature);
}
