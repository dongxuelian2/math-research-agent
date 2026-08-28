import type { ProviderTransport, TransportRequest } from "./types.js";
import { configureProxyFromEnvironment } from "./network.js";
import { request as undiciRequest } from "undici";

export class ProviderHttpError extends Error {
	readonly status: number;
	readonly body: string;

	constructor(status: number, body: string) {
		super(`Provider request failed with HTTP ${status}: ${body}`);
		this.name = "ProviderHttpError";
		this.status = status;
		this.body = body;
	}
}

export class FetchTransport implements ProviderTransport {
	constructor() {
		configureProxyFromEnvironment();
	}

	async *stream(request: TransportRequest): AsyncIterable<string> {
		const response = await undiciRequest(request.url, {
			method: request.method,
			headers: request.headers,
			body: request.body,
			signal: request.signal,
		});

		if (response.statusCode < 200 || response.statusCode >= 300) {
			throw new ProviderHttpError(response.statusCode, await response.body.text());
		}

		const decoder = new TextDecoder();
		for await (const chunk of response.body) yield decoder.decode(chunk as Uint8Array, { stream: true });
		const finalChunk = decoder.decode();
		if (finalChunk.length > 0) yield finalChunk;
	}
}
