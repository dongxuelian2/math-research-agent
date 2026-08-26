import { spawn } from "node:child_process";
import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { currentProofToolScope } from "../proof/tool-scope.js";
import { defineTool, ToolValidationError, type RuntimeTool } from "../models/tools.js";
import type { JsonObject } from "../models/json.js";
import { CorpusService } from "./corpus.js";
import { ResearchEvidenceRecorder } from "./evidence.js";
import { stableId } from "./ids.js";
import { ResearchRetrievalService } from "./retrieval.js";
import type { ResearchStore } from "./store.js";
import type { ArtifactRef, ArtifactType, EvidenceRole } from "./types.js";

export interface ResearchToolOptions {
	readonly projectId: string; readonly corpus: CorpusService; readonly retrieval?: ResearchRetrievalService;
	readonly store?: ResearchStore;
	readonly evidenceRecorder?: ResearchEvidenceRecorder; readonly defaultRole?: EvidenceRole; readonly scratchDirectory: string;
	readonly timeoutMs?: number; readonly outputLimit?: number; readonly allowedExecutables?: Readonly<Record<string, string>>;
	readonly allowedCapabilities?: readonly string[]; readonly executionBoundary?: string;
}

/** Read-only mathematical memory plus attempt-scoped writes and an explicitly controlled non-shell runner. */
export function createResearchTools(options: ResearchToolOptions): readonly RuntimeTool[] {
	if (options.executionBoundary !== undefined && options.executionBoundary !== "CONTROLLED_COMMAND_RUNNER") throw new Error(`Unsupported tool execution boundary: ${options.executionBoundary}`);
	const timeoutMs = Math.max(100, options.timeoutMs ?? 10_000); const outputLimit = Math.max(1_024, options.outputLimit ?? 64 * 1_024);
	const retrieval = options.retrieval; const allowedExecutables = options.allowedExecutables ?? { node: process.execPath, python: "python", lean: "lean", lake: "lake" };
	const record = async (operation: "SEARCH" | "READ" | "METADATA" | "COMPUTE", artifact: ArtifactRef, ranges: readonly string[] = []): Promise<void> => {
		if (options.evidenceRecorder === undefined) return; const scope = currentProofToolScope(); const role = scope?.role ?? options.defaultRole ?? "worker";
		await options.evidenceRecorder.record({ role, ...(scope?.logicalTaskId === undefined ? {} : { logicalTaskId: scope.logicalTaskId }), operation, artifact, ranges });
	};
	const tools: RuntimeTool[] = [
		defineTool<JsonObject & { query: string; exact?: boolean }, unknown>({
			name: "corpus_search", description: "Search the project's immutable initial corpus. Search access is receipted.",
			parameters: { type: "object", properties: { query: { type: "string" }, exact: { type: "boolean" } }, required: ["query"], additionalProperties: false },
			validate(input) { const value = object(input); if (typeof value.query !== "string") throw new ToolValidationError("query must be a string"); return { query: value.query, ...(typeof value.exact === "boolean" ? { exact: value.exact } : {}) }; },
			async execute(args) { const matches = await options.corpus.search(options.projectId, args.query, args.exact); const state = await options.corpus.state(options.projectId); for (const match of matches) { const ref = state.corpus[match.artifactId]; if (ref !== undefined) await record("SEARCH", ref, [`line:${match.line}`]); } return matches; },
		}),
		defineTool<JsonObject & { artifactId: string; offset?: number; limit?: number }, unknown>({
			name: "corpus_read", description: "Read exact lines from an initial corpus artifact; the access is automatically receipted.",
			parameters: rangeSchema(), validate: validateRange,
			async execute(args) { const result = await options.corpus.read(options.projectId, args.artifactId, args.offset, args.limit); await record("READ", result.record, [`lines:${result.lineStart}-${result.lineEnd}`]); return result; },
		}),
		defineTool<JsonObject & { path: string; content: string }, unknown>({
			name: "scratch_write", description: "Write a new attempt-scoped scratch file.", parameters: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"], additionalProperties: false },
			validate(input) { const value = object(input); if (typeof value.path !== "string" || typeof value.content !== "string") throw new ToolValidationError("path and content must be strings"); return { path: value.path, content: value.content }; },
			async execute(args) { const target = await safeScratchPath(options.scratchDirectory, args.path, false); await mkdir(dirname(target), { recursive: true }); await writeFile(target, args.content, { encoding: "utf8", flag: "wx" }); return { path: target, bytes: Buffer.byteLength(args.content) }; },
		}),
		defineTool<JsonObject & { path: string }, unknown>({
			name: "scratch_read", description: "Read an attempt-scoped scratch file.", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"], additionalProperties: false },
			validate(input) { const value = object(input); if (typeof value.path !== "string") throw new ToolValidationError("path must be a string"); return { path: value.path }; },
			async execute(args) { const target = await safeScratchPath(options.scratchDirectory, args.path, true); return { path: target, content: await readFile(target, "utf8") }; },
		}),
		defineTool<JsonObject & { executable: string; args?: string[] }, unknown>({
			name: "controlled_computation", description: "Run an allowlisted executable without a shell, inside attempt scratch, with filtered environment, timeout, and output caps.",
			parameters: { type: "object", properties: { executable: { type: "string", enum: Object.keys(allowedExecutables) }, args: { type: "array" } }, required: ["executable"], additionalProperties: false },
			validate(input) { const value = object(input); if (typeof value.executable !== "string" || allowedExecutables[value.executable] === undefined) throw new ToolValidationError("executable is not allowlisted"); if (value.args !== undefined && (!Array.isArray(value.args) || !value.args.every((item) => typeof item === "string"))) throw new ToolValidationError("args must be an array of strings"); const args = (value.args ?? []) as string[]; if (args.some((arg) => arg.includes("\0"))) throw new ToolValidationError("invalid argument"); return { executable: value.executable, args }; },
			async execute(args, signal) { const cwd = resolve(options.scratchDirectory), result = await controlledCommand(allowedExecutables[args.executable] as string, args.args ?? [], cwd, timeoutMs, outputLimit, signal); if (options.store === undefined) return result; const payload = { boundary: "CONTROLLED_COMMAND_RUNNER", executable: args.executable, executablePath: result.executable, arguments: args.args ?? [], environment: { keys: result.environmentKeys, locale: "C.UTF-8", inheritedCredentials: false }, workingDirectoryIdentity: stableId("scratch-cwd", options.projectId, options.evidenceRecorder?.attemptIdentity ?? cwd), stdin: null, stdout: result.stdout, stderr: result.stderr, exitCode: result.exitCode, timeoutStatus: result.timedOut ? "TIMED_OUT" : "COMPLETED", truncated: result.truncated, aborted: result.aborted }; const artifact = await options.store.putArtifact(options.projectId, { artifactType: "COMPUTATION_RESULT", body: `${JSON.stringify(payload, null, 2)}\n`, provenance: "CONTROLLED_COMMAND_RUNNER", ...(options.evidenceRecorder === undefined ? {} : { creationAttemptId: options.evidenceRecorder.attemptIdentity }), metadata: { boundary: "CONTROLLED_COMMAND_RUNNER", executable: args.executable, exitCode: result.exitCode, timedOut: result.timedOut } }); await record("COMPUTE", artifact, ["complete-result"]); return { artifactId: artifact.artifactId, contentHash: artifact.contentHash, result: payload }; },
		}),
	];
	if (retrieval !== undefined) tools.unshift(
		defineTool<JsonObject & { query: string; artifactTypes?: string[]; limit?: number }, unknown>({
			name: "artifact_search", description: "Search unified mathematical memory: corpus, verified proofs, counterexamples, literature, computations, and route evidence.",
			parameters: { type: "object", properties: { query: { type: "string" }, artifactTypes: { type: "array" }, limit: { type: "integer" } }, required: ["query"], additionalProperties: false },
			validate(input) { const value = object(input); if (typeof value.query !== "string") throw new ToolValidationError("query must be a string"); if (value.artifactTypes !== undefined && (!Array.isArray(value.artifactTypes) || !value.artifactTypes.every((item) => typeof item === "string"))) throw new ToolValidationError("artifactTypes must be strings"); return { query: value.query, ...(Array.isArray(value.artifactTypes) ? { artifactTypes: value.artifactTypes as string[] } : {}), ...(typeof value.limit === "number" && Number.isInteger(value.limit) ? { limit: value.limit } : {}) }; },
			async execute(args) { const hits = await retrieval.search(options.projectId, { text: args.query, ...(args.artifactTypes === undefined ? {} : { artifactTypes: args.artifactTypes as ArtifactType[] }), ...(args.limit === undefined ? {} : { limit: args.limit }) }); for (const hit of hits) await record("SEARCH", hit.artifact); return hits.map((hit) => ({ artifactId: hit.artifact.artifactId, contentHash: hit.artifact.contentHash, artifactType: hit.artifact.artifactType, authority: hit.artifact.authority, score: hit.score, excerpt: hit.excerpt })); },
		}),
		defineTool<JsonObject & { artifactId: string; offset?: number; limit?: number }, unknown>({ name: "artifact_read", description: "Read exact lines from any unified mathematical artifact; access is automatically receipted.", parameters: rangeSchema(), validate: validateRange, async execute(args) { const result = await retrieval.read(options.projectId, args.artifactId, args.offset, args.limit); await record("READ", result.artifact, [`lines:${result.lineStart}-${result.lineEnd}`]); return result; } }),
		defineTool<JsonObject & { artifactId: string }, unknown>({ name: "artifact_metadata", description: "Read authority and provenance metadata for a unified artifact.", parameters: { type: "object", properties: { artifactId: { type: "string" } }, required: ["artifactId"], additionalProperties: false }, validate(input) { const value = object(input); if (typeof value.artifactId !== "string") throw new ToolValidationError("artifactId must be a string"); return { artifactId: value.artifactId }; }, async execute(args) { const artifact = await retrieval.metadata(options.projectId, args.artifactId); await record("METADATA", artifact); return artifact; } }),
	);
	const allowed = options.allowedCapabilities === undefined ? undefined : new Set(options.allowedCapabilities), selected = allowed === undefined ? tools : tools.filter((tool) => allowed.has(tool.name));
	if (options.evidenceRecorder === undefined) return selected;
	return selected.map((tool) => ({ ...tool, async execute(args: JsonObject, signal?: AbortSignal) { await options.evidenceRecorder?.countToolCall(); return tool.execute(args, signal); } }));
}

async function safeScratchPath(root: string, requested: string, mustExist: boolean): Promise<string> { if (requested.includes("\0")) throw new ToolValidationError("Invalid path"); const base = resolve(root), target = resolve(base, requested); if (target !== base && !target.startsWith(`${base}${sep}`)) throw new ToolValidationError("Path escapes attempt scratch"); if (mustExist) { const actual = await realpath(target); if (actual !== base && !actual.startsWith(`${base}${sep}`)) throw new ToolValidationError("Symlink escapes attempt scratch"); } else { try { const actualParent = await realpath(dirname(target)); if (actualParent !== base && !actualParent.startsWith(`${base}${sep}`)) throw new ToolValidationError("Symlink escapes attempt scratch"); } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; } } return target; }

interface ControlledComputationResult { readonly boundary: "CONTROLLED_COMMAND_RUNNER"; readonly executable: string; readonly args: readonly string[]; readonly cwd: string; readonly environmentKeys: readonly string[]; readonly exitCode: number | null; readonly stdout: string; readonly stderr: string; readonly truncated: boolean; readonly timedOut: boolean; readonly aborted: boolean; }
function controlledCommand(executable: string, args: readonly string[], cwd: string, timeoutMs: number, limit: number, signal?: AbortSignal): Promise<ControlledComputationResult> { return new Promise((resolvePromise, reject) => { const env: NodeJS.ProcessEnv = { PATH: process.env.PATH, Path: process.env.Path, SystemRoot: process.env.SystemRoot, WINDIR: process.env.WINDIR, TEMP: cwd, TMP: cwd, LANG: "C.UTF-8", PYTHONIOENCODING: "utf-8" }; const child = spawn(executable, [...args], { cwd, shell: false, stdio: ["ignore", "pipe", "pipe"], env, windowsHide: true }); let stdout = "", stderr = "", truncated = false, timedOut = false, settled = false; const append = (current: string, chunk: Buffer): string => { const next = current + chunk.toString(); if (Buffer.byteLength(next) > limit) { truncated = true; child.kill(); return next.slice(0, limit); } return next; }; child.stdout.on("data", (chunk: Buffer) => { stdout = append(stdout, chunk); }); child.stderr.on("data", (chunk: Buffer) => { stderr = append(stderr, chunk); }); const abort = () => child.kill(); signal?.addEventListener("abort", abort, { once: true }); const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeoutMs); child.once("error", (error) => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } }); child.once("close", (exitCode) => { if (settled) return; settled = true; clearTimeout(timer); signal?.removeEventListener("abort", abort); resolvePromise({ boundary: "CONTROLLED_COMMAND_RUNNER", executable, args, cwd, environmentKeys: Object.keys(env).filter((key) => env[key] !== undefined).sort(), exitCode, stdout, stderr, truncated, timedOut, aborted: signal?.aborted ?? false }); }); }); }
function rangeSchema() { return { type: "object", properties: { artifactId: { type: "string" }, offset: { type: "integer" }, limit: { type: "integer" } }, required: ["artifactId"], additionalProperties: false } as const; }
function validateRange(input: unknown): JsonObject & { artifactId: string; offset?: number; limit?: number } { const value = object(input); if (typeof value.artifactId !== "string") throw new ToolValidationError("artifactId must be a string"); return { artifactId: value.artifactId, ...integerFields(value) }; }
function object(input: unknown): JsonObject { if (typeof input !== "object" || input === null || Array.isArray(input)) throw new ToolValidationError("arguments must be an object"); return input as JsonObject; }
function integerFields(value: JsonObject): { offset?: number; limit?: number } { const result: { offset?: number; limit?: number } = {}; if (typeof value.offset === "number" && Number.isInteger(value.offset) && value.offset >= 0) result.offset = value.offset; if (typeof value.limit === "number" && Number.isInteger(value.limit) && value.limit >= 0) result.limit = value.limit; return result; }
