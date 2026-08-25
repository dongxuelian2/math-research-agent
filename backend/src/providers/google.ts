import { FetchTransport } from "./http.js";
import { googleRequestBody, parseGoogleStream } from "./google-common.js";
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
			const baseUrl = (request.model.baseUrl ?? this.defaultBaseUrl).replace(/\/$/u, "");
			const query = credential === undefined ? "?alt=sse" : `?alt=sse&key=${encodeURIComponent(credential)}`;
			const events = parseGoogleStream(
				this.transport.stream({
					url: `${baseUrl}/models/${encodeURIComponent(request.model.model)}:streamGenerateContent${query}`,
					method: "POST",
					headers: { "content-type": "application/json", ...request.model.requestHeaders },
					body: JSON.stringify(googleRequestBody(request)),
					signal: request.signal,
				}),
			);
			for await (const event of events) yield event;
		} catch (error) {
			yield { type: "failure", error, retryable: false };
		}
	}
}
