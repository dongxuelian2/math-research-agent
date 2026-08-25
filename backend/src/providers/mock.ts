import type { ModelProvider, ModelStreamEvent, ProviderRequest } from "./types.js";

export type MockResponse = {
	readonly events: readonly ModelStreamEvent[];
	readonly delayMs?: number;
};

export class MockProvider implements ModelProvider {
	readonly id = "mock" as const;
	readonly requests: ProviderRequest[] = [];
	private readonly responses: MockResponse[];

	constructor(responses: readonly MockResponse[] = []) {
		this.responses = [...responses];
	}

	 enqueue(response: MockResponse): void {
		this.responses.push(response);
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		this.requests.push(request);
		const response = this.responses.shift() ?? {
			events: [{ type: "complete", stopReason: "end_turn" } satisfies ModelStreamEvent],
		};
		for (const event of response.events) {
			if (request.signal?.aborted) {
				throw new DOMException("The operation was aborted", "AbortError");
			}
			if (response.delayMs !== undefined) {
				await new Promise<void>((resolve) => setTimeout(resolve, response.delayMs));
			}
			yield event;
		}
	}
}
