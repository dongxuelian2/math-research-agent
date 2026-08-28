import dns, { promises as dnsPromises } from "node:dns";
import type { LookupAddress, LookupOptions } from "node:dns";
import { isIP } from "node:net";
import type { ConnectionOptions } from "node:tls";
import { Agent, ProxyAgent, setGlobalDispatcher } from "undici";

let configuredDispatcher: string | undefined;
let configuredDnsResolver = false;
const systemLookup = dns.lookup.bind(dns);

type LookupCallback = (error: NodeJS.ErrnoException | null, address: string | LookupAddress[], family?: number) => void;

const lookupWithDnsResolve = ((hostname: string, optionsOrCallback: LookupOptions | number | LookupCallback, maybeCallback?: LookupCallback): void => {
	const options = typeof optionsOrCallback === "function" ? {} : optionsOrCallback;
	const callback = typeof optionsOrCallback === "function" ? optionsOrCallback : maybeCallback;
	if (callback === undefined) {
		systemLookup(hostname, options as never, undefined as never);
		return;
	}
	const requestedFamily = typeof options === "number" ? options : options.family;
	const family = requestedFamily === "IPv4" ? 4 : requestedFamily === "IPv6" ? 6 : requestedFamily ?? 0;
	if (isIP(hostname) !== 0) {
		callback(null, hostname, isIP(hostname));
		return;
	}
	void (async () => {
		const addresses: LookupAddress[] = [];
		if (family !== 6) {
			try {
				for (const address of await dnsPromises.resolve4(hostname)) addresses.push({ address, family: 4 });
			} catch {
				// A host may legitimately have no A record; try AAAA and then the OS resolver.
			}
		}
		if (family !== 4) {
			try {
				for (const address of await dnsPromises.resolve6(hostname)) addresses.push({ address, family: 6 });
			} catch {
				// A host may legitimately have no AAAA record; the A result is sufficient.
			}
		}
		if (addresses.length === 0) throw new Error(`DNS resolver returned no address for ${hostname}`);
		if (typeof options !== "number" && options.all === true) callback(null, addresses);
		else {
			const first = addresses[0] as LookupAddress;
			callback(null, first.address, first.family);
		}
	})().catch(() => {
		// Keep local names, /etc/hosts, and Unix-specific names working through
		// the system resolver while bypassing its broken public-name path.
		systemLookup(hostname, options as never, callback as never);
	});
}) as NonNullable<ConnectionOptions["lookup"]>;

/**
 * Node's native fetch does not automatically consume HTTP(S)_PROXY.  The
 * desktop runtime uses a local forward proxy for outbound Google traffic, so
 * configure Undici once before any provider request is made.
 */
export function configureProxyFromEnvironment(): void {
	if (!configuredDnsResolver) {
		// The host currently resolves public names through the asynchronous DNS
		// API, while Node's getaddrinfo path returns ENOTFOUND. Native fetch and
		// socket clients both consult dns.lookup, so install the working resolver
		// once for the process. Local/hosts-only names still fall back to the
		// original implementation below.
		dns.lookup = lookupWithDnsResolve as typeof dns.lookup;
		configuredDnsResolver = true;
	}
	const proxy = firstNonEmpty(
		process.env.HTTPS_PROXY,
		process.env.https_proxy,
		process.env.HTTP_PROXY,
		process.env.http_proxy,
		process.env.ALL_PROXY,
		process.env.all_proxy,
	);
	const dispatcherKey = proxy ?? "direct:dns-resolve";
	if (dispatcherKey === configuredDispatcher) return;
	if (proxy !== undefined) setGlobalDispatcher(new ProxyAgent(proxy));
	else setGlobalDispatcher(new Agent({ connect: { lookup: lookupWithDnsResolve } }));
	configuredDispatcher = dispatcherKey;
}

function firstNonEmpty(...values: readonly (string | undefined)[]): string | undefined {
	return values.find((value) => value !== undefined && value.trim().length > 0)?.trim();
}
