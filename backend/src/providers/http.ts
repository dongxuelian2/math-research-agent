import type { ProviderTransport, TransportRequest } from "./types.js";
import { configureProxyFromEnvironment } from "./network.js";

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
		const response = await fetch(request.url, {
			method: request.method,
			headers: request.headers,
			body: request.body,
			signal: request.signal,
		});

		if (!response.ok) {
			throw new ProviderHttpError(response.status, await response.text());
		}

		if (response.body === null) {
			yield await response.text();
			return;
		}

		const reader = response.body.getReader();
		const decoder = new TextDecoder();
		try {
			while (true) {
				const chunk = await reader.read();
				if (chunk.done) {
					break;
				}
				yield decoder.decode(chunk.value, { stream: true });
			}

			const finalChunk = decoder.decode();
			if (finalChunk.length > 0) {
				yield finalChunk;
			}
		} finally {
			reader.releaseLock();
		}
	}
}
