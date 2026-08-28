import {
	GoogleGenAI,
	type Content,
	type GenerateContentConfig,
	type GenerateContentParameters,
	type GenerateContentResponse,
	type GoogleGenAIOptions,
	type HttpOptions,
	type Part,
} from "@google/genai";
import { asRecord, asString } from "./parse.js";
import { configureProxyFromEnvironment } from "./network.js";
import { resolveCredential, type ModelConfig, type ModelProvider, type ModelStreamEvent, type ProviderRequest } from "./types.js";
import type { AgentMessage, AssistantContent, Usage } from "../models/messages.js";

const DEFAULT_LOCATION = "global";
const DEFAULT_API_VERSION = "v1";

/**
 * The small part of GoogleGenAI used by the provider. Keeping this seam
 * injectable makes the adapter testable without making a network request or
 * coupling the rest of the agent runtime to the SDK's client class.
 */
export interface GoogleGenAIClient {
	readonly models: {
		generateContentStream(request: GenerateContentParameters): Promise<AsyncIterable<GenerateContentResponse>>;
	};
}

export interface GoogleVertexProviderOptions {
	readonly clientFactory?: (options: GoogleGenAIOptions) => GoogleGenAIClient;
	readonly project?: string;
	readonly location?: string;
	readonly apiVersion?: string;
}

/**
 * Google Cloud Gemini adapter backed by Google's official @google/genai SDK.
 *
 * The SDK owns authentication. In a local shell it uses Application Default
 * Credentials; in Cloud Run it uses the service identity exposed by the
 * metadata server. This adapter deliberately never reads, scans, parses, or
 * signs a service-account JSON file.
 */
export class GoogleVertexProvider implements ModelProvider {
	readonly id = "google-vertex" as const;
	private readonly clientFactory: (options: GoogleGenAIOptions) => GoogleGenAIClient;
	private readonly project?: string;
	private readonly location?: string;
	private readonly apiVersion?: string;
	private clientPromise: Promise<GoogleGenAIClient> | undefined;

	constructor(options: GoogleVertexProviderOptions = {}) {
		configureProxyFromEnvironment();
		this.clientFactory = options.clientFactory ?? ((clientOptions) => new GoogleGenAI(clientOptions));
		this.project = options.project;
		this.location = options.location;
		this.apiVersion = options.apiVersion;
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			const client = await this.clientFor(request.model);
			const response = await client.models.generateContentStream(this.toSdkRequest(request));
			let completed = false;
			let callNumber = 0;
			let usage: Usage | undefined;

			for await (const chunk of response) {
				const chunkUsage = sdkUsage(chunk);
				if (chunkUsage !== undefined) usage = chunkUsage;
				const candidate = chunk.candidates?.[0];
				for (const part of candidate?.content?.parts ?? []) {
					if (part.text !== undefined && part.text.length > 0) {
						yield { type: "text_delta", text: part.text };
					}
					const functionCall = part.functionCall;
					if (functionCall !== undefined) {
						const callId = functionCall.id ?? `google-call-${callNumber}`;
						if (functionCall.id === undefined) callNumber += 1;
						const thoughtSignature = part.thoughtSignature;
						yield {
							type: "tool_call_delta",
							callId,
							...(functionCall.name === undefined ? {} : { name: functionCall.name }),
							argumentsDelta: JSON.stringify(functionCall.args ?? {}),
							...(thoughtSignature === undefined ? {} : { providerMetadata: { google: { thought_signature: thoughtSignature } } }),
						};
					}
				}

				const finishReason = candidate?.finishReason;
				if (finishReason !== undefined && !completed) {
					completed = true;
					yield {
						type: "complete",
						stopReason: sdkStopReason(finishReason, candidate?.content?.parts),
						...(usage === undefined ? {} : { usage }),
					};
				}
			}
			if (!completed) yield { type: "complete", stopReason: "end_turn", ...(usage === undefined ? {} : { usage }) };
		} catch (error) {
			yield { type: "failure", error, retryable: isRetryableProviderError(error) };
		}
	}

	private async clientFor(model: ModelConfig): Promise<GoogleGenAIClient> {
		if (this.clientPromise !== undefined) return this.clientPromise;
		this.clientPromise = this.createClient(model);
		return this.clientPromise;
	}

	private async createClient(model: ModelConfig): Promise<GoogleGenAIClient> {
		const apiKey = await resolveCredential(model);
		const project = nonEmpty(this.project) ?? nonEmpty(process.env.GOOGLE_CLOUD_PROJECT);
		const location = nonEmpty(this.location) ?? nonEmpty(process.env.GOOGLE_CLOUD_LOCATION) ?? DEFAULT_LOCATION;
		const apiVersion = nonEmpty(this.apiVersion) ?? DEFAULT_API_VERSION;
		const httpOptions = sdkHttpOptions(model, apiVersion);
		const options: GoogleGenAIOptions = {
			// `enterprise` is the current official SDK name for the Vertex AI
			// backend. It is equivalent to the SDK's legacy `vertexai` flag.
			enterprise: true,
			...(project === undefined ? {} : { project }),
			...(location === undefined ? {} : { location }),
			...(nonEmpty(apiKey) === undefined ? {} : { apiKey: nonEmpty(apiKey) }),
			...(httpOptions === undefined ? {} : { httpOptions }),
			apiVersion,
		};
		return this.clientFactory(options);
	}

	private toSdkRequest(request: ProviderRequest): GenerateContentParameters {
		const config = sdkConfig(request);
		return {
			model: request.model.model,
			contents: sdkContents(request.messages, request.model.model),
			...(Object.keys(config).length === 0 ? {} : { config }),
		};
	}
}

function sdkContents(messages: readonly AgentMessage[], model: string): Content[] {
	const contents: Content[] = [];
	for (const message of messages) {
		if (message.role === "user") {
			contents.push({ role: "user", parts: [{ text: message.content }] });
			continue;
		}
		if (message.role === "assistant") {
			const parts = message.content.map((part) => sdkAssistantPart(part, model)).filter((part): part is Part => part !== undefined);
			// An interrupted/empty model turn can be retained in the Agent
			// history. The SDK/API rejects a content item without parts.
			if (parts.length > 0) contents.push({ role: "model", parts });
			continue;
		}
		contents.push({
			role: "user",
			parts: [{
				functionResponse: {
					name: message.toolName,
					response: { result: message.content },
					...(isGemini3Model(model) ? { id: message.toolCallId } : {}),
				},
			}],
		});
	}
	if (contents.length === 0) contents.push({ role: "user", parts: [{ text: "Continue." }] });
	return contents;
}

function sdkAssistantPart(part: AssistantContent, model: string): Part | undefined {
	if (part.kind === "text") return { text: part.text };
	const thoughtSignature = googleThoughtSignature(part.providerMetadata);
	return {
		functionCall: {
			name: part.name,
			args: part.arguments,
			...(isGemini3Model(model) ? { id: part.id } : {}),
		},
		...(thoughtSignature === undefined ? {} : { thoughtSignature }),
	};
}

function sdkConfig(request: ProviderRequest): GenerateContentConfig {
	const parameters = asRecord(request.model.requestParameters) ?? {};
	const config: Record<string, unknown> = {};
	const configured = asRecord(parameters.config);
	if (configured !== undefined) Object.assign(config, configured);
	for (const [key, value] of Object.entries(parameters)) {
		if (key !== "config" && key !== "generationConfig" && key !== "generation_config" && key !== "contents" && key !== "model" && key !== "tools") config[key] = value;
	}
	const generationConfig = asRecord(parameters.generationConfig ?? parameters.generation_config);
	if (generationConfig !== undefined) Object.assign(config, generationConfig);

	if (request.tools.length > 0) {
		config.tools = [{
			functionDeclarations: request.tools.map((tool) => ({
				name: tool.name,
				description: tool.description,
				parametersJsonSchema: tool.parameters,
			})),
		}];
	}
	if (request.model.maxTokens !== undefined) config.maxOutputTokens = request.model.maxTokens;
	if (request.model.reasoningEffort !== undefined) {
		const thinkingConfig = asRecord(config.thinkingConfig);
		config.thinkingConfig = {
			...thinkingConfig,
			...(isGemini3Model(request.model.model)
				? { thinkingLevel: request.model.reasoningEffort }
				: { thinkingBudget: thinkingBudget(request.model.reasoningEffort) }),
		};
	}
	if (request.responseSchema !== undefined) {
		config.responseMimeType = "application/json";
		config.responseJsonSchema = request.responseSchema;
	}
	if (request.signal !== undefined) config.abortSignal = request.signal;
	return config as GenerateContentConfig;
}

function sdkHttpOptions(model: ModelConfig, apiVersion: string | undefined): HttpOptions | undefined {
	const headers = model.requestHeaders === undefined ? undefined : { ...model.requestHeaders };
	const baseUrl = nonEmpty(model.baseUrl);
	if (baseUrl === undefined && headers === undefined) return undefined;
	const options: HttpOptions = {
		...(headers === undefined ? {} : { headers }),
		...(nonEmpty(apiVersion) === undefined ? {} : { apiVersion }),
	};
	if (baseUrl !== undefined) {
		const versioned = baseUrl.match(/^(.*)\/(v1(?:beta\d*)?)\/?$/u);
		if (versioned?.[1] !== undefined && versioned[2] !== undefined) {
			options.baseUrl = versioned[1];
			if (nonEmpty(apiVersion) === undefined) options.apiVersion = versioned[2];
		} else {
			options.baseUrl = baseUrl;
			if (nonEmpty(apiVersion) === undefined) options.apiVersion = "";
		}
	}
	return options;
}

function sdkUsage(response: GenerateContentResponse): Usage | undefined {
	const metadata = response.usageMetadata;
	if (metadata === undefined) return undefined;
	const usage: Usage = {
		...(metadata.promptTokenCount === undefined ? {} : { inputTokens: metadata.promptTokenCount }),
		...(metadata.candidatesTokenCount === undefined ? {} : { outputTokens: metadata.candidatesTokenCount }),
		...(metadata.totalTokenCount === undefined ? {} : { totalTokens: metadata.totalTokenCount }),
	};
	return Object.keys(usage).length === 0 ? undefined : usage;
}

function sdkStopReason(finishReason: string, parts: readonly Part[] | undefined): "end_turn" | "tool_calls" | "length" {
	if (finishReason === "MAX_TOKENS") return "length";
	if (finishReason === "STOP") return "end_turn";
	return parts?.some((part) => part.functionCall !== undefined) === true ? "tool_calls" : "end_turn";
}

function isGemini3Model(model: string): boolean {
	return /^gemini-3(?:\.|-)/u.test(model);
}

function thinkingBudget(effort: NonNullable<ModelConfig["reasoningEffort"]>): number {
	return effort === "low" ? 1024 : effort === "medium" ? 4096 : 8192;
}

function googleThoughtSignature(metadata: import("../models/json.js").JsonObject | undefined): string | undefined {
	const google = asRecord(metadata?.google);
	return asString(google?.thought_signature) ?? asString(google?.thoughtSignature);
}

function nonEmpty(value: string | undefined): string | undefined {
	return value !== undefined && value.trim().length > 0 ? value.trim() : undefined;
}

function isRetryableProviderError(error: unknown): boolean {
	if (!(error instanceof Error)) return false;
	const status = (error as Error & { readonly status?: unknown }).status;
	return (typeof status === "number" && [408, 425, 429, 500, 502, 503, 504].includes(status))
		|| /fetch failed|eai_again|enotfound|econnreset|econnrefused|etimedout|timeout|socket|temporarily unavailable|http (?:408|425|429|5\d\d)\b/iu.test(`${error.message} ${String((error as { readonly cause?: unknown }).cause ?? "")}`);
}
