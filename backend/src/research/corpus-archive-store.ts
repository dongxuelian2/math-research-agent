import { mkdir, open, readFile, rename } from "node:fs/promises";
import { dirname, join } from "node:path";
import { stableId } from "./ids.js";
import { ResearchStore } from "./store.js";
import { CORPUS_ARCHIVE_CLASSES, CORPUS_ARCHIVE_STATUSES, type ArchiveReceipt, type CorpusArchiveIntent, type CorpusArchiveOutboxState, type CorpusArchiveStatus } from "./corpus-archive-types.js";

export type CorpusArchiveStoreFaultPhase = "AFTER_TMP_WRITE" | "BEFORE_REPLACE" | "AFTER_REPLACE";
export interface CorpusArchiveStoreOptions {
	readonly faultInjector?: (phase: CorpusArchiveStoreFaultPhase, canonicalPath: string, temporaryPath: string) => void | Promise<void>;
}

export class CorpusArchiveStore {
	private static readonly mutationTails = new Map<string, Promise<void>>();

	constructor(private readonly researchStore: ResearchStore, private readonly options: CorpusArchiveStoreOptions = {}) {}

	statePath(projectId: string): string {
		return join(this.researchStore.projectDirectory(projectId), "corpus-archive", "state.json");
	}

	async activate(projectId: string, activatedAt = new Date().toISOString()): Promise<CorpusArchiveOutboxState> {
		return this.serialized(projectId, async () => {
			const existing = await this.readOptional(projectId);
			if (existing !== undefined) return existing;
			const state: CorpusArchiveOutboxState = { schemaVersion: 1, projectId, activatedAt, intents: {}, receipts: {} };
			await atomicJson(this.statePath(projectId), state, this.options.faultInjector);
			return state;
		});
	}

	async read(projectId: string): Promise<CorpusArchiveOutboxState> {
		const state = await this.readOptional(projectId);
		if (state === undefined) throw new Error(`Corpus archive is not activated for project: ${projectId}`);
		return state;
	}

	async readOptional(projectId: string): Promise<CorpusArchiveOutboxState | undefined> {
		let raw: string;
		try { raw = await readFile(this.statePath(projectId), "utf8"); } catch (error) {
			if (isMissing(error)) return undefined;
			throw error;
		}
		const value: unknown = JSON.parse(raw);
		assertOutboxState(value, projectId);
		return value;
	}

	async enqueue(intent: CorpusArchiveIntent): Promise<{ readonly created: boolean; readonly intent: CorpusArchiveIntent }> {
		return this.transaction<{ readonly created: boolean; readonly intent: CorpusArchiveIntent }>(intent.projectId, (state) => {
			const existing = state.intents[intent.intentId];
			if (existing !== undefined) {
				if (existing.sourceId !== intent.sourceId || existing.canonicalKey !== intent.canonicalKey || existing.classificationHint !== intent.classificationHint) throw new Error(`Corpus archive intent identity collision: ${intent.intentId}`);
				return { state, result: { created: false as boolean, intent: existing } };
			}
			const next: CorpusArchiveOutboxState = { ...state, intents: { ...state.intents, [intent.intentId]: intent } };
			return { state: next, result: { created: true as boolean, intent } };
		});
	}

	async updateIntent(projectId: string, intentId: string, update: Partial<Pick<CorpusArchiveIntent, "status" | "statusCode" | "statusDetail" | "localCommit" | "claimedAt">>): Promise<CorpusArchiveIntent> {
		return this.transaction(projectId, (state) => {
			const current = state.intents[intentId];
			if (current === undefined) throw new Error(`Corpus archive intent not found: ${intentId}`);
			if (update.status !== undefined && !allowedTransition(current.status, update.status)) throw new Error(`Invalid corpus archive status transition: ${current.status} -> ${update.status}`);
			const statusCode = update.statusCode === undefined ? current.statusCode : update.statusCode;
			const statusDetail = update.statusDetail === undefined ? current.statusDetail : update.statusDetail;
			const localCommit = update.localCommit === undefined ? current.localCommit : update.localCommit;
			const claimedAt = update.claimedAt === undefined ? current.claimedAt : update.claimedAt;
			const next: CorpusArchiveIntent = {
				...current,
				...(update.status === undefined ? {} : { status: update.status }),
				...(statusCode === undefined ? {} : { statusCode }),
				...(statusDetail === undefined ? {} : { statusDetail }),
				...(localCommit === undefined ? {} : { localCommit }),
				...(claimedAt === undefined ? {} : { claimedAt }),
				updatedAt: new Date().toISOString(),
			};
			return { state: { ...state, intents: { ...state.intents, [intentId]: next } }, result: next };
		});
	}

	async resetForRetry(projectId: string, intentId: string): Promise<CorpusArchiveIntent> {
		const current = (await this.read(projectId)).intents[intentId];
		if (current === undefined) throw new Error(`Corpus archive intent not found: ${intentId}`);
		if (current.status !== "RETRYABLE_FAILURE" && current.status !== "MANUAL_REVIEW" && current.status !== "CLAIMED" && current.status !== "PROJECTING") throw new Error(`Intent is not retryable: ${current.status}`);
		return this.updateIntent(projectId, intentId, { status: "PENDING", statusCode: "RETRY_REQUESTED", statusDetail: "Explicit retry requested" });
	}

	async complete(projectId: string, receipt: ArchiveReceipt): Promise<ArchiveReceipt> {
		return this.transaction(projectId, (state) => {
			const existing = state.receipts[receipt.intentId];
			if (existing !== undefined) {
				if (existing.corpusResultCommit !== receipt.corpusResultCommit) throw new Error(`Archive receipt collision: ${receipt.intentId}`);
				return { state, result: existing };
			}
			const intent = state.intents[receipt.intentId];
			if (intent === undefined) throw new Error(`Archive receipt has no intent: ${receipt.intentId}`);
			const completed: CorpusArchiveIntent = { ...intent, status: "COMPLETE", statusCode: "PUBLISHED", statusDetail: "Archive receipt committed", localCommit: receipt.corpusResultCommit, updatedAt: receipt.completedAt };
			return { state: { ...state, intents: { ...state.intents, [receipt.intentId]: completed }, receipts: { ...state.receipts, [receipt.intentId]: receipt } }, result: receipt };
		});
	}

	async pending(projectId: string): Promise<CorpusArchiveIntent[]> {
		const state = await this.read(projectId);
		return Object.values(state.intents).filter((intent) => intent.status !== "COMPLETE" && intent.status !== "PERMANENT_FAILURE" && intent.status !== "MANUAL_REVIEW").sort((left, right) => left.createdAt.localeCompare(right.createdAt));
	}

	private async transaction<T>(projectId: string, mutate: (state: CorpusArchiveOutboxState) => { readonly state: CorpusArchiveOutboxState; readonly result: T }): Promise<T> {
		return this.serialized(projectId, async () => {
			const before = await this.read(projectId), mutation = mutate(structuredClone(before));
			assertOutboxState(mutation.state, projectId);
			await atomicJson(this.statePath(projectId), mutation.state, this.options.faultInjector);
			return mutation.result;
		});
	}

	private async serialized<T>(projectId: string, operation: () => Promise<T>): Promise<T> {
		const key = this.statePath(projectId).toLocaleLowerCase(), previous = CorpusArchiveStore.mutationTails.get(key) ?? Promise.resolve();
		let release!: () => void;
		const current = new Promise<void>((resolvePromise) => { release = resolvePromise; });
		CorpusArchiveStore.mutationTails.set(key, current);
		await previous;
		try { return await operation(); } finally { release(); if (CorpusArchiveStore.mutationTails.get(key) === current) CorpusArchiveStore.mutationTails.delete(key); }
	}
}

export function archiveReceiptId(intentId: string, commit: string): string {
	return stableId("archive-receipt", intentId, commit);
}

function allowedTransition(from: CorpusArchiveStatus, to: CorpusArchiveStatus): boolean {
	if (from === to) return true;
	const allowed: Readonly<Record<CorpusArchiveStatus, readonly CorpusArchiveStatus[]>> = {
		PENDING: ["CLAIMED", "MANUAL_REVIEW", "PERMANENT_FAILURE"],
		CLAIMED: ["PROJECTING", "PENDING", "RETRYABLE_FAILURE", "MANUAL_REVIEW", "PERMANENT_FAILURE"],
		PROJECTING: ["COMMITTED_LOCAL", "PENDING", "RETRYABLE_FAILURE", "MANUAL_REVIEW", "PERMANENT_FAILURE"],
		COMMITTED_LOCAL: ["PUSHED", "COMPLETE", "RETRYABLE_FAILURE", "MANUAL_REVIEW"],
		PUSHED: ["COMPLETE", "RETRYABLE_FAILURE"],
		COMPLETE: [],
		RETRYABLE_FAILURE: ["PENDING", "CLAIMED", "MANUAL_REVIEW", "PERMANENT_FAILURE"],
		MANUAL_REVIEW: ["PENDING", "PERMANENT_FAILURE"],
		PERMANENT_FAILURE: [],
	};
	return allowed[from].includes(to);
}

function assertOutboxState(value: unknown, projectId: string): asserts value is CorpusArchiveOutboxState {
	if (!isRecord(value) || value.schemaVersion !== 1 || value.projectId !== projectId || typeof value.activatedAt !== "string" || !isRecord(value.intents) || !isRecord(value.receipts)) throw new Error(`Invalid corpus archive outbox state: ${projectId}`);
	for (const [intentId, raw] of Object.entries(value.intents)) {
		if (!isRecord(raw) || raw.schemaVersion !== 1 || raw.intentId !== intentId || raw.projectId !== projectId || typeof raw.sourceId !== "string" || !CORPUS_ARCHIVE_CLASSES.includes(raw.classificationHint as never) || raw.classificationHint === "NO_ARCHIVE" || !CORPUS_ARCHIVE_STATUSES.includes(raw.status as never) || raw.createdFromAuthoritativeState !== true || typeof raw.canonicalKey !== "string" || typeof raw.artifactSlug !== "string" || !isRecord(raw.semantic) || !Array.isArray(raw.evidenceRefs)) throw new Error(`Invalid corpus archive intent: ${intentId}`);
	}
	for (const [intentId, raw] of Object.entries(value.receipts)) if (!isRecord(raw) || raw.schemaVersion !== 1 || raw.intentId !== intentId || typeof raw.corpusResultCommit !== "string" || !isRecord(raw.validationResult) || !isRecord(raw.pushResult)) throw new Error(`Invalid corpus archive receipt: ${intentId}`);
}

async function atomicJson(path: string, value: unknown, faultInjector?: CorpusArchiveStoreOptions["faultInjector"]): Promise<void> {
	await mkdir(dirname(path), { recursive: true });
	const temporary = `${path}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`, handle = await open(temporary, "wx");
	try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
	await faultInjector?.("AFTER_TMP_WRITE", path, temporary);
	for (let attempt = 1; ; attempt += 1) {
		try { await faultInjector?.("BEFORE_REPLACE", path, temporary); await rename(temporary, path); await faultInjector?.("AFTER_REPLACE", path, temporary); return; } catch (error) {
			const code = (error as NodeJS.ErrnoException | undefined)?.code; if ((code !== "EPERM" && code !== "EBUSY" && code !== "EACCES") || attempt >= 8) throw error;
			await new Promise<void>((resolvePromise) => setTimeout(resolvePromise, Math.min(250, attempt * attempt * 10)));
		}
	}
}

function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function isMissing(error: unknown): boolean { return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT"; }
