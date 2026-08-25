export function asRecord(value: unknown): Record<string, unknown> | undefined {
	return typeof value === "object" && value !== null && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: undefined;
}

export function asString(value: unknown): string | undefined {
	return typeof value === "string" ? value : undefined;
}

export function asNumber(value: unknown): number | undefined {
	return typeof value === "number" ? value : undefined;
}

export function asArray(value: unknown): readonly unknown[] | undefined {
	return Array.isArray(value) ? value : undefined;
}

export function parseJson(data: string): Record<string, unknown> | undefined {
	try {
		return asRecord(JSON.parse(data) as unknown);
	} catch {
		return undefined;
	}
}

export function jsonString(value: unknown): string {
	const serialized = JSON.stringify(value);
	return serialized === undefined ? "{}" : serialized;
}
