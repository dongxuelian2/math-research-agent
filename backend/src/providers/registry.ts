import { AnthropicProvider } from "./anthropic.js";
import { CodexCliProvider, type CodexCliProviderOptions } from "./codex.js";
import { GoogleProvider } from "./google.js";
import { GoogleVertexProvider, type GoogleVertexProviderOptions } from "./google-vertex.js";
import { MockProvider, type MockResponse } from "./mock.js";
import { OpenAICompatibleProvider } from "./openai-compatible.js";
import type { ProviderTransport, ModelConfig, ModelProvider, ProviderId } from "./types.js";

export type ProviderFactoryOptions = {
	readonly transport?: ProviderTransport;
	readonly codex?: CodexCliProviderOptions;
	readonly mockResponses?: readonly MockResponse[];
	readonly googleVertex?: GoogleVertexProviderOptions;
};

export function createProvider(model: ModelConfig, options: ProviderFactoryOptions = {}): ModelProvider {
	switch (model.provider) {
		case "mock":
			return new MockProvider(options.mockResponses);
		case "openai":
			return new OpenAICompatibleProvider({
				id: "openai",
				defaultBaseUrl: "https://api.openai.com/v1",
				transport: options.transport,
			});
		case "openrouter":
			return new OpenAICompatibleProvider({
				id: "openrouter",
				defaultBaseUrl: "https://openrouter.ai/api/v1",
				transport: options.transport,
			});
		case "deepseek":
			return new OpenAICompatibleProvider({
				id: "deepseek",
				defaultBaseUrl: "https://api.deepseek.com/v1",
				transport: options.transport,
			});
		case "anthropic":
			return new AnthropicProvider({ transport: options.transport });
		case "google":
			return new GoogleProvider({ transport: options.transport });
		case "google-vertex":
			return new GoogleVertexProvider({ ...options.googleVertex, transport: options.transport ?? options.googleVertex?.transport });
		case "openai-codex":
			return new CodexCliProvider(options.codex);
		default:
			return assertNeverProvider(model.provider);
	}
}

export class ProviderRegistry {
	private readonly providers = new Map<ProviderId, ModelProvider>();

	register(provider: ModelProvider): void {
		this.providers.set(provider.id, provider);
	}

	get(id: ProviderId): ModelProvider {
		const provider = this.providers.get(id);
		if (provider === undefined) {
			throw new Error(`No provider registered for ${id}`);
		}
		return provider;
	}
}

function assertNeverProvider(value: never): never {
	throw new Error(`Unsupported provider: ${String(value)}`);
}
