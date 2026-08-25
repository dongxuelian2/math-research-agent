import { randomUUID } from "node:crypto";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import type { JsonObject, JsonValue } from "../models/json.js";
import { isRecord } from "../models/json.js";
import type { AgentMessage, AssistantMessage, ToolResultMessage, UserMessage } from "../models/messages.js";
import type {
	SessionAppendEntry,
	SessionEntry,
	SessionHeaderEntry,
	SessionMetadata,
	SessionMessageEntry,
	SessionCustomEntry,
	SessionToolResultEntry,
} from "./types.js";
import type { SessionBranchEntry } from "./types.js";

export interface CreateSessionOptions {
	readonly projectId: string;
	readonly cwd: string;
	readonly directory: string;
	readonly sessionId?: string;
	readonly metadata?: SessionMetadata;
}

export interface ForkSessionOptions {
	readonly projectId?: string;
	readonly sessionId?: string;
	readonly directory?: string;
	readonly metadata?: SessionMetadata;
}

export class Session {
	private readonly entryList: SessionEntry[];
	private metadataValue: SessionMetadata;
	private appendTail: Promise<void> = Promise.resolve();

	private constructor(
		readonly filePath: string,
		private readonly header: SessionHeaderEntry,
		entries: SessionEntry[],
	) {
		this.entryList = entries;
		this.metadataValue = { ...header.metadata };
		for (const entry of entries) {
			if (entry.kind === "metadata") {
				this.metadataValue = { ...this.metadataValue, ...entry.patch };
			}
		}
	}

	static async create(options: CreateSessionOptions): Promise<Session> {
		const sessionId = options.sessionId ?? options.projectId;
		if (sessionId !== options.projectId) {
			throw new Error("Session ID must equal Project ID in phase one");
		}
		const filePath = resolve(options.directory, `${sessionId}.jsonl`);
		await mkdir(dirname(filePath), { recursive: true });
		const header: SessionHeaderEntry = {
			kind: "session",
			sequence: 0,
			sessionId,
			projectId: options.projectId,
			cwd: resolve(options.cwd),
			createdAt: new Date().toISOString(),
			metadata: { ...(options.metadata ?? {}) },
		};
		await writeFile(filePath, `${JSON.stringify(header)}\n`, { encoding: "utf8", flag: "wx" });
		return new Session(filePath, header, [header]);
	}

	static async resume(filePath: string): Promise<Session> {
		const resolvedPath = resolve(filePath);
		const raw = await readFile(resolvedPath, "utf8");
		const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
		if (lines.length === 0) {
			throw new Error(`Session file is empty: ${resolvedPath}`);
		}

		const parsed = lines.map((line, index) => parseEntry(line, index));
		const first = parsed[0];
		if (first === undefined || first.kind !== "session") {
			throw new Error(`Session file has no header: ${resolvedPath}`);
		}
		return new Session(resolvedPath, first, parsed);
	}

	static open(filePath: string): Promise<Session> {
		return Session.resume(filePath);
	}

	get sessionId(): string {
		return this.header.sessionId;
	}

	get projectId(): string {
		return this.header.projectId;
	}

	get cwd(): string {
		return this.header.cwd;
	}

	get metadata(): SessionMetadata {
		return { ...this.metadataValue };
	}

	get entries(): readonly SessionEntry[] {
		return [...this.entryList];
	}

	contextProjection(): AgentMessage[] {
		const messages: AgentMessage[] = [];
		for (const entry of this.entryList) {
			if (entry.kind === "message" || entry.kind === "tool_result") {
				messages.push(entry.message);
			}
		}
		return messages;
	}

	async appendMessage(message: UserMessage | AssistantMessage): Promise<void> {
		await this.append({ kind: "message", timestamp: message.timestamp, message });
	}

	async appendToolResult(message: ToolResultMessage<unknown>): Promise<void> {
		await this.append({ kind: "tool_result", timestamp: message.timestamp, message });
	}

	async updateMetadata(patch: JsonObject): Promise<void> {
		await this.append({ kind: "metadata", timestamp: Date.now(), patch });
		this.metadataValue = { ...this.metadataValue, ...patch };
	}

	async appendCustom<TPayload extends JsonObject>(request: {
		readonly namespace: string;
		readonly type: string;
		readonly payload: TPayload;
		readonly timestamp?: number;
	}): Promise<void> {
		await this.append({
			kind: "custom",
			timestamp: request.timestamp ?? Date.now(),
			namespace: request.namespace,
			type: request.type,
			payload: request.payload,
		});
	}

	customEntries(namespace?: string): readonly SessionCustomEntry[] {
		return this.entryList.filter(
			(entry): entry is SessionCustomEntry =>
				entry.kind === "custom" && (namespace === undefined || entry.namespace === namespace),
		);
	}

	async fork(options: ForkSessionOptions = {}): Promise<Session> {
		const suffix = randomUUID().slice(0, 8);
		const projectId = options.projectId ?? `${this.projectId}/branch-${suffix}`;
		const child = await Session.create({
			projectId,
			cwd: this.cwd,
			directory: options.directory ?? dirname(this.filePath),
			sessionId: options.sessionId ?? projectId,
			metadata: { ...this.metadataValue, ...(options.metadata ?? {}) },
		});
		await child.append({
			kind: "branch",
			timestamp: Date.now(),
			parentSessionId: this.sessionId,
			parentEntryCount: this.entryList.length,
		});

		for (const entry of this.entryList) {
			if (entry.kind === "message") {
				await child.append({ kind: "message", timestamp: entry.timestamp, message: entry.message });
			}
			if (entry.kind === "tool_result") {
				await child.append({ kind: "tool_result", timestamp: entry.timestamp, message: entry.message });
			}
			if (entry.kind === "custom") {
				await child.append({
					kind: "custom",
					timestamp: entry.timestamp,
					namespace: entry.namespace,
					type: entry.type,
					payload: entry.payload,
				});
			}
		}
		return child;
	}

	branch(options: ForkSessionOptions = {}): Promise<Session> {
		return this.fork(options);
	}

	private async append(entry: SessionAppendEntry): Promise<void> {
		const write = this.appendTail.then(async () => {
			const materialized = {
				...entry,
				sequence: this.entryList.length,
			} as SessionEntry;
			await appendFile(this.filePath, `${JSON.stringify(materialized)}\n`, "utf8");
			this.entryList.push(materialized);
		});
		this.appendTail = write.then(
			() => undefined,
			() => undefined,
		);
		await write;
	}
}

function parseEntry(line: string, index: number): SessionEntry {
		let value: unknown;
		try {
			value = JSON.parse(line) as unknown;
		} catch (error) {
			throw new Error(`Invalid JSONL session entry ${index}: ${String(error)}`);
		}
		if (!isRecord(value) || typeof value.kind !== "string") {
			throw new Error(`Invalid session entry ${index}`);
		}
		if (value.kind === "session") {
			if (
				typeof value.sessionId !== "string" ||
				typeof value.projectId !== "string" ||
				typeof value.cwd !== "string" ||
				typeof value.createdAt !== "string" ||
				!isRecord(value.metadata)
			) {
				throw new Error(`Invalid session header ${index}`);
			}
			return {
				kind: "session",
				sequence: 0,
				sessionId: value.sessionId,
				projectId: value.projectId,
				cwd: value.cwd,
				createdAt: value.createdAt,
				metadata: value.metadata as SessionMetadata,
			};
		}
		if (typeof value.sequence !== "number" || typeof value.timestamp !== "number") {
			throw new Error(`Invalid session sequence or timestamp ${index}`);
		}
		if (value.kind === "message" && isAgentMessage(value.message) && value.message.role !== "tool_result") {
			return {
				kind: "message",
				sequence: value.sequence,
				timestamp: value.timestamp,
				message: value.message,
			} satisfies SessionMessageEntry;
		}
		if (value.kind === "tool_result" && isAgentMessage(value.message) && value.message.role === "tool_result") {
			return {
				kind: "tool_result",
				sequence: value.sequence,
				timestamp: value.timestamp,
				message: value.message,
			} satisfies SessionToolResultEntry;
		}
		if (
			value.kind === "branch" &&
			typeof value.parentSessionId === "string" &&
			typeof value.parentEntryCount === "number"
		) {
			return {
				kind: "branch",
				sequence: value.sequence,
				timestamp: value.timestamp,
				parentSessionId: value.parentSessionId,
				parentEntryCount: value.parentEntryCount,
			} satisfies SessionBranchEntry;
		}
		if (value.kind === "metadata" && isJsonObjectValue(value.patch)) {
			return {
				kind: "metadata",
				sequence: value.sequence,
				timestamp: value.timestamp,
				patch: value.patch,
			};
		}
		if (
			value.kind === "custom" &&
			typeof value.namespace === "string" &&
			typeof value.type === "string" &&
			isJsonObjectValue(value.payload)
		) {
			return {
				kind: "custom",
				sequence: value.sequence,
				timestamp: value.timestamp,
				namespace: value.namespace,
				type: value.type,
				payload: value.payload,
			} satisfies SessionCustomEntry;
		}
		throw new Error(`Invalid session entry ${index} of kind ${value.kind}`);
}

function isAgentMessage(value: unknown): value is AgentMessage {
	return isRecord(value) && (value.role === "user" || value.role === "assistant" || value.role === "tool_result");
}

function isJsonObjectValue(value: unknown): value is JsonObject {
	if (!isRecord(value)) {
		return false;
	}
	return Object.values(value).every((item) => isJsonValue(item));
}

function isJsonValue(value: unknown): value is JsonValue {
	if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
		return true;
	}
	if (Array.isArray(value)) {
		return value.every((item) => isJsonValue(item));
	}
	if (isRecord(value)) {
		return Object.values(value).every((item) => isJsonValue(item));
	}
	return false;
}
