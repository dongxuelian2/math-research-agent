import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";
import type { JsonObject } from "../models/json.js";
import { defineTool, ToolValidationError, type AgentTool } from "../models/tools.js";

export type ReadArguments = JsonObject & {
	readonly path: string;
	readonly offset?: number;
	readonly limit?: number;
};

export type ReadDetails = {
	readonly path: string;
	readonly content: string;
	readonly lineStart: number;
	readonly lineEnd: number;
};

export type WriteArguments = JsonObject & {
	readonly path: string;
	readonly content: string;
};

export type WriteDetails = {
	readonly path: string;
	readonly bytes: number;
};

export type EditArguments = JsonObject & {
	readonly path: string;
	readonly oldText: string;
	readonly newText: string;
	readonly replaceAll?: boolean;
};

export type EditDetails = {
	readonly path: string;
	readonly replacements: number;
};

export type BashArguments = JsonObject & {
	readonly command: string;
	readonly cwd?: string;
	readonly timeoutMs?: number;
};

export type BashDetails = {
	readonly command: string;
	readonly cwd: string;
	readonly exitCode: number | null;
	readonly signal?: string;
	readonly stdout: string;
	readonly stderr: string;
	readonly timedOut: boolean;
	readonly aborted: boolean;
};

export interface BuiltinToolOptions {
	readonly cwd?: string;
}

export function createBuiltinTools(options: BuiltinToolOptions = {}): readonly AgentTool<JsonObject, unknown>[] {
	return [
		createReadTool(options),
		createWriteTool(options),
		createEditTool(options),
		createBashTool(options),
	];
}

export function createReadTool(options: BuiltinToolOptions = {}): AgentTool<ReadArguments, ReadDetails> {
	return defineTool<ReadArguments, ReadDetails>({
		name: "read",
		description: "Read a UTF-8 text file, optionally selecting a range of lines.",
		parameters: {
			type: "object",
			properties: {
				path: { type: "string", description: "Path relative to the agent working directory." },
				offset: { type: "integer", description: "Zero-based starting line." },
				limit: { type: "integer", description: "Maximum number of lines to return." },
			},
			required: ["path"],
			additionalProperties: false,
		},
		validate(input: unknown): ReadArguments {
			const object = asObject(input, "read arguments");
			const path = requiredString(object, "path");
			const offset = optionalInteger(object, "offset");
			const limit = optionalInteger(object, "limit");
			return { path, ...(offset === undefined ? {} : { offset }), ...(limit === undefined ? {} : { limit }) };
		},
		async execute(args: ReadArguments): Promise<ReadDetails> {
			const target = resolvePath(options.cwd, args.path);
			const source = await readFile(target, "utf8");
			const lines = source.split(/\r?\n/);
			const lineStart = args.offset ?? 0;
			const lineEnd = Math.min(lines.length, lineStart + (args.limit ?? lines.length));
			return {
				path: target,
				content: lines.slice(lineStart, lineEnd).join("\n"),
				lineStart,
				lineEnd,
			};
		},
	});
}

export function createWriteTool(options: BuiltinToolOptions = {}): AgentTool<WriteArguments, WriteDetails> {
	return defineTool<WriteArguments, WriteDetails>({
		name: "write",
		description: "Write UTF-8 text to a file, creating parent directories when needed.",
		parameters: {
			type: "object",
			properties: {
				path: { type: "string" },
				content: { type: "string" },
			},
			required: ["path", "content"],
			additionalProperties: false,
		},
		validate(input: unknown): WriteArguments {
			const object = asObject(input, "write arguments");
			return { path: requiredString(object, "path"), content: requiredString(object, "content") };
		},
		async execute(args: WriteArguments): Promise<WriteDetails> {
			const target = resolvePath(options.cwd, args.path);
			await mkdir(dirname(target), { recursive: true });
			await writeFile(target, args.content, "utf8");
			return { path: target, bytes: Buffer.byteLength(args.content, "utf8") };
		},
	});
}

export function createEditTool(options: BuiltinToolOptions = {}): AgentTool<EditArguments, EditDetails> {
	return defineTool<EditArguments, EditDetails>({
		name: "edit",
		description: "Replace an exact text fragment in a UTF-8 file.",
		parameters: {
			type: "object",
			properties: {
				path: { type: "string" },
				oldText: { type: "string" },
				newText: { type: "string" },
				replaceAll: { type: "boolean" },
			},
			required: ["path", "oldText", "newText"],
			additionalProperties: false,
		},
		validate(input: unknown): EditArguments {
			const object = asObject(input, "edit arguments");
			const replaceAll = optionalBoolean(object, "replaceAll");
			return {
				path: requiredString(object, "path"),
				oldText: requiredString(object, "oldText"),
				newText: requiredString(object, "newText"),
				...(replaceAll === undefined ? {} : { replaceAll }),
			};
		},
		async execute(args: EditArguments): Promise<EditDetails> {
			const target = resolvePath(options.cwd, args.path);
			const source = await readFile(target, "utf8");
			const occurrences = countOccurrences(source, args.oldText);
			if (occurrences === 0) {
				throw new Error(`Text to replace was not found in ${target}`);
			}
			if (occurrences > 1 && args.replaceAll !== true) {
				throw new Error(`Text to replace occurs ${occurrences} times; set replaceAll=true to edit all matches`);
			}
			const updated = args.replaceAll === true ? source.split(args.oldText).join(args.newText) : source.replace(args.oldText, args.newText);
			await writeFile(target, updated, "utf8");
			return { path: target, replacements: args.replaceAll === true ? occurrences : 1 };
		},
	});
}

export function createBashTool(options: BuiltinToolOptions = {}): AgentTool<BashArguments, BashDetails> {
	return defineTool<BashArguments, BashDetails>({
		name: "bash",
		description: "Run a shell command in the agent working directory.",
		parameters: {
			type: "object",
			properties: {
				command: { type: "string" },
				cwd: { type: "string" },
				timeoutMs: { type: "integer" },
			},
			required: ["command"],
			additionalProperties: false,
		},
		validate(input: unknown): BashArguments {
			const object = asObject(input, "bash arguments");
			const cwd = optionalString(object, "cwd");
			const timeoutMs = optionalInteger(object, "timeoutMs");
			return {
				command: requiredString(object, "command"),
				...(cwd === undefined ? {} : { cwd }),
				...(timeoutMs === undefined ? {} : { timeoutMs }),
			};
		},
		async execute(args: BashArguments, signal?: AbortSignal): Promise<BashDetails> {
			const cwd = resolvePath(options.cwd, args.cwd ?? ".");
			return runShellCommand(args.command, cwd, args.timeoutMs, signal);
		},
	});
}

function resolvePath(baseCwd: string | undefined, path: string): string {
	return baseCwd === undefined ? resolve(path) : resolve(baseCwd, path);
}

function asObject(input: unknown, label: string): JsonObject {
	if (typeof input !== "object" || input === null || Array.isArray(input)) {
		throw new ToolValidationError(`${label} must be an object`);
	}
	return input as JsonObject;
}

function requiredString(input: JsonObject, key: string): string {
	const value = input[key];
	if (typeof value !== "string") {
		throw new ToolValidationError(`${key} must be a string`);
	}
	return value;
}

function optionalString(input: JsonObject, key: string): string | undefined {
	const value = input[key];
	if (value === undefined) {
		return undefined;
	}
	if (typeof value !== "string") {
		throw new ToolValidationError(`${key} must be a string when provided`);
	}
	return value;
}

function optionalInteger(input: JsonObject, key: string): number | undefined {
	const value = input[key];
	if (value === undefined) {
		return undefined;
	}
	if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
		throw new ToolValidationError(`${key} must be a non-negative integer when provided`);
	}
	return value;
}

function optionalBoolean(input: JsonObject, key: string): boolean | undefined {
	const value = input[key];
	if (value === undefined) {
		return undefined;
	}
	if (typeof value !== "boolean") {
		throw new ToolValidationError(`${key} must be a boolean when provided`);
	}
	return value;
}

function countOccurrences(source: string, fragment: string): number {
	if (fragment.length === 0) {
		throw new ToolValidationError("oldText must not be empty");
	}
	let count = 0;
	let offset = 0;
	while (true) {
		const index = source.indexOf(fragment, offset);
		if (index < 0) {
			return count;
		}
		count += 1;
		offset = index + fragment.length;
	}
}

function runShellCommand(command: string, cwd: string, timeoutMs: number | undefined, signal: AbortSignal | undefined): Promise<BashDetails> {
	return new Promise<BashDetails>((resolvePromise, reject) => {
		const child = spawn(command, { cwd, shell: true, stdio: ["ignore", "pipe", "pipe"] });
		let stdout = "";
		let stderr = "";
		let timedOut = false;
		let aborted = false;
		let settled = false;
		let timer: NodeJS.Timeout | undefined;

		const finish = (exitCode: number | null, signalName: NodeJS.Signals | null): void => {
			if (settled) {
				return;
			}
			settled = true;
			if (timer !== undefined) {
				clearTimeout(timer);
			}
			signal?.removeEventListener("abort", abort);
			resolvePromise({
				command,
				cwd,
				exitCode,
				...(signalName === null ? {} : { signal: signalName }),
				stdout,
				stderr,
				timedOut,
				aborted,
			});
		};
		const abort = (): void => {
			aborted = true;
			child.kill("SIGTERM");
		};

		child.stdout?.on("data", (chunk: Buffer) => {
			stdout += chunk.toString();
		});
		child.stderr?.on("data", (chunk: Buffer) => {
			stderr += chunk.toString();
		});
		child.once("error", reject);
		child.once("close", finish);
		if (timeoutMs !== undefined) {
			timer = setTimeout(() => {
				timedOut = true;
				child.kill("SIGTERM");
			}, timeoutMs);
		}
		if (signal?.aborted) {
			abort();
		} else {
			signal?.addEventListener("abort", abort, { once: true });
		}
	});
}
