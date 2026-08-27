import type { JsonObject } from "../models/json.js";
import type { BootstrapDependencyProposal, BootstrapProposal } from "./types.js";

export const BOOTSTRAP_SCHEMA_VERSION = "mrr-bootstrap-v2";

const stringArray = { type: "array", items: { type: "string" } } as const;

/** Canonical contract shared by prompts, provider structured output, and validation. */
export const BOOTSTRAP_ANALYSIS_SCHEMA: JsonObject = {
	$schema: "https://json-schema.org/draft/2020-12/schema",
	type: "object",
	additionalProperties: false,
	required: ["proposals", "dependencies", "warnings"],
	properties: {
		proposals: {
			type: "array",
			items: {
				type: "object",
				additionalProperties: false,
				required: ["entityKey", "kind", "statement", "dependencyHints", "targetHint", "routeFamily", "mechanism", "failureMechanism", "cases"],
				properties: {
					entityKey: { type: "string", minLength: 1 },
					kind: { type: "string", enum: ["DEFINITION", "CLAIM", "OPEN_PROBLEM", "FAILED_ROUTE", "REDUCTION", "CASE_SPLIT", "COMPUTATIONAL_EVIDENCE"] },
					statement: { type: "string", minLength: 1 },
					dependencyHints: { ...stringArray, description: "Required for every proposal. Use [] when no dependency is known." },
					targetHint: { type: "string", description: "Nonempty for REDUCTION and CASE_SPLIT; otherwise the required value is an empty string." },
					routeFamily: { type: "string", description: "Nonempty for FAILED_ROUTE; otherwise the required value is an empty string." },
					mechanism: { type: "string", description: "Nonempty for FAILED_ROUTE; otherwise the required value is an empty string." },
					failureMechanism: { type: "string", description: "Nonempty for FAILED_ROUTE; otherwise the required value is an empty string." },
					cases: { ...stringArray, description: "For CASE_SPLIT use at least two case-statement strings, never objects; otherwise the required value is []." },
				},
			},
		},
		dependencies: {
			type: "array",
			items: {
				type: "object",
				additionalProperties: false,
				required: ["fromEntity", "toEntity", "confidence", "confidenceScore"],
				properties: {
					fromEntity: { type: "string", minLength: 1 },
					toEntity: { type: "string", minLength: 1 },
					confidence: { type: "string", enum: ["EXPLICIT", "INFERRED"] },
					confidenceScore: { type: ["number", "null"], minimum: 0, maximum: 1, description: "Optional numeric score represented as null when unused; it never replaces the confidence enum." },
				},
			},
		},
		warnings: stringArray,
	},
};

export const BOOTSTRAP_REVIEW_SCHEMA: JsonObject = {
	$schema: "https://json-schema.org/draft/2020-12/schema",
	type: "object",
	additionalProperties: false,
	required: ["frontierEntityKeys", "warnings"],
	properties: { frontierEntityKeys: stringArray, warnings: stringArray },
};

export class BootstrapSchemaValidationError extends Error {
	readonly failureType = "SCHEMA_VALIDATION_FAILURE" as const;
	modelSessionId?: string; provider?: string; model?: string;
	constructor(readonly issues: readonly string[], readonly rawResponse?: string) { super(`Bootstrap schema validation failed: ${issues.join("; ")}`); this.name = "BootstrapSchemaValidationError"; }
	attachModelMetadata(metadata: { readonly sessionId: string; readonly provider?: string; readonly model?: string }): this { this.modelSessionId = metadata.sessionId; this.provider = metadata.provider; this.model = metadata.model; return this; }
}

export class BootstrapStructuredOutputParseError extends Error {
	readonly failureType = "STRUCTURED_OUTPUT_PARSE_FAILURE" as const;
	modelSessionId?: string; provider?: string; model?: string;
	constructor(message: string, readonly rawResponse?: string) { super(message); this.name = "BootstrapStructuredOutputParseError"; }
	attachModelMetadata(metadata: { readonly sessionId: string; readonly provider?: string; readonly model?: string }): this { this.modelSessionId = metadata.sessionId; this.provider = metadata.provider; this.model = metadata.model; return this; }
}

export interface CanonicalBootstrapAnalysis {
	readonly proposals: readonly Omit<BootstrapProposal, "source" | "authority">[];
	readonly dependencies: readonly BootstrapDependencyProposal[];
	readonly warnings: readonly string[];
}

export function parseBootstrapAnalysisText(text: string): CanonicalBootstrapAnalysis {
	if (text.trim().length === 0) throw new BootstrapStructuredOutputParseError("Bootstrap provider returned an empty response", text);
	const trimmed = text.trim().replace(/^```(?:json)?\s*/iu, "").replace(/\s*```$/u, "");
	let value: unknown;
	try { value = JSON.parse(trimmed); } catch (error) { throw new BootstrapStructuredOutputParseError(`Malformed bootstrap JSON: ${String(error)}`, text); }
	try { return parseBootstrapAnalysis(value); } catch (error) { if (error instanceof BootstrapSchemaValidationError) throw new BootstrapSchemaValidationError(error.issues, text); throw error; }
}

export function parseBootstrapAnalysis(value: unknown): CanonicalBootstrapAnalysis {
	const issues: string[] = [];
	if (!record(value)) throw new BootstrapSchemaValidationError(["root must be an object"]);
	for (const key of Object.keys(value)) if (!["proposals", "dependencies", "warnings"].includes(key)) issues.push(`root.${key} is not allowed`);
	if (!Array.isArray(value.proposals)) issues.push("proposals must be an array");
	if (!Array.isArray(value.dependencies)) issues.push("dependencies must be an array");
	if (!Array.isArray(value.warnings) || !value.warnings.every(isString)) issues.push("warnings is required and must be string[]");
	const proposals = Array.isArray(value.proposals) ? value.proposals.map((item, index) => parseProposal(item, index, issues)).filter(defined) : [];
	const dependencies = Array.isArray(value.dependencies) ? value.dependencies.map((item, index) => parseDependency(item, index, issues)).filter(defined) : [];
	if (issues.length > 0) throw new BootstrapSchemaValidationError(issues);
	return { proposals, dependencies, warnings: value.warnings as string[] };
}

function parseProposal(value: unknown, index: number, issues: string[]): Omit<BootstrapProposal, "source" | "authority"> | undefined {
	const at = `proposals[${index}]`;
	if (!record(value)) { issues.push(`${at} must be an object`); return undefined; }
	for (const key of Object.keys(value)) if (!["entityKey", "kind", "statement", "dependencyHints", "targetHint", "routeFamily", "mechanism", "failureMechanism", "cases"].includes(key)) issues.push(`${at}.${key} is not allowed`);
	if (typeof value.entityKey !== "string" || value.entityKey.length === 0) issues.push(`${at}.entityKey must be a nonempty string`);
	if (!bootstrapKind(value.kind)) issues.push(`${at}.kind must be a canonical enum value`);
	if (typeof value.statement !== "string" || value.statement.length === 0) issues.push(`${at}.statement must be a nonempty string`);
	if (!Array.isArray(value.dependencyHints) || !value.dependencyHints.every(isString)) issues.push(`${at}.dependencyHints is required and must be string[]`);
	for (const key of ["targetHint", "routeFamily", "mechanism", "failureMechanism"] as const) if (typeof value[key] !== "string") issues.push(`${at}.${key} is required and must be a string (empty when not applicable)`);
	if (!Array.isArray(value.cases) || !value.cases.every(isString)) issues.push(`${at}.cases is required and must be string[]`);
	if (value.kind === "CASE_SPLIT" && (!Array.isArray(value.cases) || value.cases.length < 2 || !value.cases.every(isString))) issues.push(`${at}.cases requires at least two strings for CASE_SPLIT`);
	if ((value.kind === "REDUCTION" || value.kind === "CASE_SPLIT") && (typeof value.targetHint !== "string" || value.targetHint.length === 0)) issues.push(`${at}.targetHint must be nonempty for ${String(value.kind)}`);
	if (value.kind === "FAILED_ROUTE" && ([value.routeFamily, value.mechanism, value.failureMechanism].some((item) => typeof item !== "string" || item.length === 0))) issues.push(`${at} FAILED_ROUTE requires nonempty routeFamily, mechanism, and failureMechanism strings`);
	if (issues.some((issue) => issue.startsWith(at))) return undefined;
	return { entityKey: value.entityKey as string, kind: value.kind as BootstrapProposal["kind"], statement: value.statement as string, dependencyHints: value.dependencyHints as string[], ...nonemptyString(value, "targetHint"), ...nonemptyString(value, "routeFamily"), ...nonemptyString(value, "mechanism"), ...nonemptyString(value, "failureMechanism"), ...((value.cases as string[]).length === 0 ? {} : { cases: value.cases as string[] }) };
}

function parseDependency(value: unknown, index: number, issues: string[]): BootstrapDependencyProposal | undefined {
	const at = `dependencies[${index}]`;
	if (!record(value)) { issues.push(`${at} must be an object`); return undefined; }
	for (const key of Object.keys(value)) if (!["fromEntity", "toEntity", "confidence", "confidenceScore"].includes(key)) issues.push(`${at}.${key} is not allowed`);
	if (typeof value.fromEntity !== "string" || value.fromEntity.length === 0) issues.push(`${at}.fromEntity must be a nonempty string`);
	if (typeof value.toEntity !== "string" || value.toEntity.length === 0) issues.push(`${at}.toEntity must be a nonempty string`);
	if (value.confidence !== "EXPLICIT" && value.confidence !== "INFERRED") issues.push(`${at}.confidence must be EXPLICIT or INFERRED`);
	if (value.confidenceScore !== null && (typeof value.confidenceScore !== "number" || value.confidenceScore < 0 || value.confidenceScore > 1)) issues.push(`${at}.confidenceScore is required and must be null or a number between 0 and 1`);
	if (issues.some((issue) => issue.startsWith(at))) return undefined;
	return { fromEntity: value.fromEntity as string, toEntity: value.toEntity as string, confidence: value.confidence as BootstrapDependencyProposal["confidence"], ...(value.confidenceScore === null ? {} : { confidenceScore: value.confidenceScore as number }) };
}

function nonemptyString<K extends string>(value: Record<string, unknown>, key: K): Partial<Record<K, string>> { return typeof value[key] === "string" && value[key].length > 0 ? { [key]: value[key] } as Partial<Record<K, string>> : {}; }
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function isString(value: unknown): value is string { return typeof value === "string"; }
function defined<T>(value: T | undefined): value is T { return value !== undefined; }
function bootstrapKind(value: unknown): value is BootstrapProposal["kind"] { return value === "DEFINITION" || value === "CLAIM" || value === "OPEN_PROBLEM" || value === "FAILED_ROUTE" || value === "REDUCTION" || value === "CASE_SPLIT" || value === "COMPUTATIONAL_EVIDENCE"; }

export function bootstrapSchemaInstructions(schema: JsonObject = BOOTSTRAP_ANALYSIS_SCHEMA): string {
	return `Return exactly one JSON object conforming to this machine schema. Do not omit required arrays and do not substitute numeric confidence for the confidence enum.\n${JSON.stringify(schema, null, 2)}`;
}
