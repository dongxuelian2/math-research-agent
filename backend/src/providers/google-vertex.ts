import { createSign } from "node:crypto";
import { readFile } from "node:fs/promises";
import { FetchTransport } from "./http.js";
import { asNumber, asRecord, asString } from "./parse.js";
import { googleRequestBody, parseGoogleStream } from "./google-common.js";
import {
	resolveCredentialFile,
	type ModelConfig,
	type ModelProvider,
	type ModelStreamEvent,
	type ProviderRequest,
	type ProviderTransport,
} from "./types.js";

const CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform";
const DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token";
const DEFAULT_LOCATION = "global";

type ServiceAccount = {
	readonly clientEmail: string;
	readonly privateKey: string;
	readonly privateKeyId?: string;
	readonly projectId: string;
	readonly tokenUri: string;
};

type AccessToken = {
	readonly value: string;
	readonly expiresAt: number;
};

type TokenRequest = {
	readonly credentials: ServiceAccount;
	readonly signal?: AbortSignal;
};

type TokenResponse = {
	readonly accessToken: string;
	readonly expiresIn: number;
};

export interface GoogleVertexProviderOptions {
	readonly transport?: ProviderTransport;
	readonly tokenRequester?: (request: TokenRequest) => Promise<TokenResponse>;
	readonly defaultBaseUrl?: string;
}

export class GoogleVertexProvider implements ModelProvider {
	readonly id = "google-vertex" as const;
	private readonly transport: ProviderTransport;
	private readonly tokenRequester: (request: TokenRequest) => Promise<TokenResponse>;
	private readonly defaultBaseUrl?: string;
	private cachedToken: AccessToken | undefined;

	constructor(options: GoogleVertexProviderOptions = {}) {
		this.transport = options.transport ?? new FetchTransport();
		this.tokenRequester = options.tokenRequester ?? requestAccessToken;
		this.defaultBaseUrl = options.defaultBaseUrl;
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			const credentials = await readServiceAccount(request.model);
			const accessToken = await this.getAccessToken(credentials, request.signal);
			const projectId = process.env.GOOGLE_CLOUD_PROJECT || credentials.projectId;
			const location = process.env.GOOGLE_CLOUD_LOCATION || DEFAULT_LOCATION;
			const baseUrl = (request.model.baseUrl ?? this.defaultBaseUrl ?? defaultBaseUrl(location)).replace(/\/$/u, "");
			const modelPath = [
				"projects",
				encodeURIComponent(projectId),
				"locations",
				encodeURIComponent(location),
				"publishers",
				"google",
				"models",
				encodeURIComponent(request.model.model),
			].join("/");
			const events = parseGoogleStream(
				this.transport.stream({
					url: `${baseUrl}/${modelPath}:streamGenerateContent?alt=sse`,
					method: "POST",
					headers: {
						"content-type": "application/json",
						authorization: `Bearer ${accessToken}`,
						...request.model.requestHeaders,
					},
					body: JSON.stringify(googleRequestBody(request)),
					signal: request.signal,
				}),
			);
			for await (const event of events) yield event;
		} catch (error) {
			yield { type: "failure", error, retryable: false };
		}
	}

	private async getAccessToken(credentials: ServiceAccount, signal?: AbortSignal): Promise<string> {
		const reusable = this.cachedToken;
		if (reusable !== undefined && reusable.expiresAt > Date.now() + 60_000) return reusable.value;
		const response = await this.tokenRequester({ credentials, signal });
		const expiresIn = Math.max(60, response.expiresIn);
		this.cachedToken = {
			value: response.accessToken,
			expiresAt: Date.now() + (expiresIn - 60) * 1000,
		};
		return response.accessToken;
	}
}

async function readServiceAccount(model: ModelConfig): Promise<ServiceAccount> {
	const filename = await resolveCredentialFile(model) ?? process.env.GOOGLE_APPLICATION_CREDENTIALS;
	if (filename === undefined || filename.length === 0) {
		throw new Error("Google Vertex requires GOOGLE_APPLICATION_CREDENTIALS to point to a Service Account JSON file");
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(await readFile(filename, "utf8")) as unknown;
	} catch {
		throw new Error(`Unable to read Google Service Account JSON from ${filename}`);
	}
	const object = asRecord(parsed);
	const clientEmail = asString(object?.client_email);
	const privateKey = asString(object?.private_key);
	const projectId = asString(object?.project_id);
	if (object?.type !== "service_account" || clientEmail === undefined || privateKey === undefined || projectId === undefined) {
		throw new Error("Google credential file must be a service_account JSON with client_email, private_key, and project_id");
	}
	return {
		clientEmail,
		privateKey,
		projectId,
		...(asString(object.private_key_id) === undefined ? {} : { privateKeyId: asString(object.private_key_id) }),
		tokenUri: asString(object.token_uri) ?? DEFAULT_TOKEN_URI,
	};
}

async function requestAccessToken(request: TokenRequest): Promise<TokenResponse> {
	const now = Math.floor(Date.now() / 1000);
	const header = {
		alg: "RS256",
		typ: "JWT",
		...(request.credentials.privateKeyId === undefined ? {} : { kid: request.credentials.privateKeyId }),
	};
	const claims = {
		iss: request.credentials.clientEmail,
		scope: CLOUD_PLATFORM_SCOPE,
		aud: request.credentials.tokenUri,
		iat: now,
		exp: now + 3600,
	};
	const unsigned = `${base64UrlJson(header)}.${base64UrlJson(claims)}`;
	const signer = createSign("RSA-SHA256");
	signer.update(unsigned);
	const assertion = `${unsigned}.${signer.sign(request.credentials.privateKey).toString("base64url")}`;
	const response = await fetch(request.credentials.tokenUri, {
		method: "POST",
		headers: { "content-type": "application/x-www-form-urlencoded" },
		body: new URLSearchParams({
			grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
			assertion,
		}).toString(),
		signal: request.signal,
	});
	if (!response.ok) {
		throw new Error(`Google Service Account token request failed with HTTP ${response.status}`);
	}
	const payload = asRecord(await response.json() as unknown);
	const accessToken = asString(payload?.access_token);
	if (accessToken === undefined) throw new Error("Google Service Account token response did not contain access_token");
	return { accessToken, expiresIn: asNumber(payload?.expires_in) ?? 3600 };
}

function defaultBaseUrl(location: string): string {
	const host = location === "global" ? "aiplatform.googleapis.com" : `${location}-aiplatform.googleapis.com`;
	return `https://${host}/v1`;
}

function base64UrlJson(value: Record<string, unknown>): string {
	return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}
