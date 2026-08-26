import { ProxyAgent, setGlobalDispatcher } from "undici";

let configuredProxy: string | undefined;

/**
 * Node's native fetch does not automatically consume HTTP(S)_PROXY.  The
 * desktop runtime uses a local forward proxy for outbound Google traffic, so
 * configure Undici once before any provider request is made.
 */
export function configureProxyFromEnvironment(): void {
	const proxy = firstNonEmpty(
		process.env.HTTPS_PROXY,
		process.env.https_proxy,
		process.env.HTTP_PROXY,
		process.env.http_proxy,
		process.env.ALL_PROXY,
		process.env.all_proxy,
	);
	if (proxy === undefined || proxy === configuredProxy) return;
	setGlobalDispatcher(new ProxyAgent(proxy));
	configuredProxy = proxy;
}

function firstNonEmpty(...values: readonly (string | undefined)[]): string | undefined {
	return values.find((value) => value !== undefined && value.trim().length > 0)?.trim();
}
