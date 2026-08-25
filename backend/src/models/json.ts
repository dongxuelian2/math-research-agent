export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];

export interface JsonObject {
	readonly [key: string]: JsonValue;
}

export function isJsonObject(value: unknown): value is JsonObject {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asJsonObject(value: unknown, label: string): JsonObject {
	if (!isJsonObject(value)) {
		throw new TypeError(`${label} must be a JSON object`);
	}
	return value;
}

export function stringifyJson(value: unknown): string {
	if (typeof value === "string") {
		return value;
	}

	try {
		const serialized = JSON.stringify(value);
		return serialized === undefined ? String(value) : serialized;
	} catch {
		return String(value);
	}
}
