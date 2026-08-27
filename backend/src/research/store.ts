import { access, mkdir, open, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";
import { stableId, sha256 } from "./ids.js";
import { inspectResearchState } from "./invariants.js";
import type { ArtifactRef, ArtifactType, AuthorityValidationState, ClaimSnapshot, FinalProofAuthority, ResearchArtifact, ResearchProjectState, RootObjectiveContract, StateMigrationReport } from "./types.js";

export class ArtifactAuthorityError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "ArtifactAuthorityError";
	}
}

export type StateWriteFaultPhase = "AFTER_TMP_WRITE" | "BEFORE_REPLACE" | "AFTER_REPLACE";
export interface ResearchStoreOptions { readonly stateWriteFaultInjector?: (phase: StateWriteFaultPhase, attempt: number, canonicalPath: string, temporaryPath: string) => void | Promise<void>; }

export function emptyProject(projectId: string, name: string, now = new Date().toISOString(), effectiveConfig: Readonly<Record<string, unknown>> = {}): ResearchProjectState {
	return {
		schemaVersion: 4, projectId, name, createdAt: now, updatedAt: now, status: "CREATED",
		corpusRoots: [], corpus: {}, artifacts: {}, claims: {}, obligations: {}, coverage: {}, routes: {}, supportEdges: [],
		jobs: {}, attempts: {}, executionTasks: {}, executionPlans: {}, acceptedEffects: {}, authorityReceipts: {}, authorityValidation: {}, trustReceipts: {}, toolEvidenceReceipts: {},
		contextManifests: {}, decisions: [], events: [], checkpoints: [], bootstrapReports: [], bootstrapRuns: {}, cycle: 0,
		cyclesSinceStructuralProgress: 0,
		budget: { cycles: 0, plannerCalls: 0, workerCalls: 0, verifierCalls: 0, secondaryAuditorCalls: 0, literatureCalls: 0, toolCalls: 0, startedAt: Date.now() },
		effectiveConfig: structuredClone(effectiveConfig), configRevision: stableId("effective-config", projectId, JSON.stringify(effectiveConfig)), migrationReports: [], finalProofHistory: [],
	};
}

/**
 * Durable single-writer authority.  A transaction publishes one complete state
 * snapshot using write+rename; model and tool code never receives this object.
 */
export class ResearchStore {
	private static readonly mutationTails = new Map<string, Promise<void>>();

	constructor(readonly rootDirectory: string, private readonly options: ResearchStoreOptions = {}) {}

	projectDirectory(projectId: string): string {
		assertSafeId(projectId);
		return join(resolve(this.rootDirectory), "projects", projectId);
	}

	async initialize(): Promise<void> {
		await mkdir(join(resolve(this.rootDirectory), "projects"), { recursive: true });
	}

	async listProjects(): Promise<ResearchProjectState[]> {
		await this.initialize();
		const entries = await readdir(join(resolve(this.rootDirectory), "projects"), { withFileTypes: true });
		const values: ResearchProjectState[] = [];
		for (const entry of entries) {
			if (!entry.isDirectory()) continue;
			try { values.push(await this.read(entry.name)); } catch (error) { if (!isMissing(error)) throw error; }
		}
		return values;
	}

	async create(projectId: string, name: string, effectiveConfig: Readonly<Record<string, unknown>> = {}): Promise<ResearchProjectState> {
		assertSafeId(projectId);
		return this.serialized(projectId, async () => {
			const directory = this.projectDirectory(projectId);
			await mkdir(join(directory, "artifacts"), { recursive: true });
			await mkdir(join(directory, "scratch"), { recursive: true });
			const state = emptyProject(projectId, name, new Date().toISOString(), effectiveConfig);
			await writeFile(join(directory, "state.json"), `${JSON.stringify(state, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
			return state;
		});
	}

	async read(projectId: string): Promise<ResearchProjectState> {
		const raw = await readFile(join(this.projectDirectory(projectId), "state.json"), "utf8");
		const value = migrateProject(JSON.parse(raw) as Record<string, unknown>);
		if (value.schemaVersion !== 4 || value.projectId !== projectId) throw new Error(`Invalid research project state: ${projectId}`);
		const hardViolations = inspectResearchState(value).violations.filter((item) => item.code !== "ARTIFACT_RESOLUTION");
		if (hardViolations.length > 0) throw new Error(`Invalid research project invariants: ${hardViolations.map((item) => `${item.code}${item.entityId === undefined ? "" : `(${item.entityId})`}`).join(", ")}`);
		return value;
	}

	async transaction<T>(projectId: string, mutate: (draft: ResearchProjectState) => T | Promise<T>): Promise<{ readonly state: ResearchProjectState; readonly result: T }> {
		return this.serialized(projectId, async () => {
			const before = await this.read(projectId);
			const draft = structuredClone(before);
			const result = await mutate(draft);
			const state = { ...draft, updatedAt: new Date().toISOString() };
			await atomicJson(join(this.projectDirectory(projectId), "state.json"), state, this.options.stateWriteFaultInjector);
			return { state, result };
		});
	}

	async putArtifact(projectId: string, request: {
		readonly artifactType: ArtifactType;
		readonly body: string;
		readonly provenance: string;
		readonly creationAttemptId?: string;
		readonly references?: readonly ArtifactRef[];
		readonly authority?: ResearchArtifact["authority"];
		readonly metadata?: ResearchArtifact["metadata"];
	}): Promise<ResearchArtifact> {
		const artifact = await this.prepareArtifact(projectId, request);
		await this.transaction(projectId, (draft) => {
			(draft as MutableState).artifacts = { ...draft.artifacts, [artifact.artifactId]: draft.artifacts[artifact.artifactId] ?? artifact };
		});
		return artifact;
	}

	/** Write an immutable body without granting visibility or authority in project state. */
	async prepareArtifact(projectId: string, request: {
		readonly artifactType: ArtifactType;
		readonly body: string;
		readonly provenance: string;
		readonly creationAttemptId?: string;
		readonly references?: readonly ArtifactRef[];
		readonly authority?: ResearchArtifact["authority"];
		readonly metadata?: ResearchArtifact["metadata"];
	}): Promise<ResearchArtifact> {
		const contentHash = sha256(request.body);
		const artifactId = stableId("artifact", projectId, request.artifactType, contentHash);
		const bodyPath = join(this.projectDirectory(projectId), "artifacts", `${artifactId}.body`);
		await mkdir(dirname(bodyPath), { recursive: true });
		try {
			await writeFile(bodyPath, request.body, { encoding: "utf8", flag: "wx" });
		} catch (error) {
			if (!isExists(error)) throw error;
			const existing = await readFile(bodyPath, "utf8");
			if (sha256(existing) !== contentHash) throw new ArtifactAuthorityError(`Immutable artifact collision: ${artifactId}`);
		}
		const artifact: ResearchArtifact = {
			artifactId, contentHash, artifactType: request.artifactType, bodyPath, provenance: request.provenance,
			...(request.creationAttemptId === undefined ? {} : { creationAttemptId: request.creationAttemptId }),
			...(request.authority === undefined ? {} : { authority: request.authority }),
			...(request.metadata === undefined ? {} : { metadata: request.metadata }),
			references: request.references ?? [], createdAt: new Date().toISOString(),
		};
		return artifact;
	}

	async resolveArtifact(projectId: string, ref: ArtifactRef): Promise<{ readonly artifact: ResearchArtifact; readonly body: string }> {
		const state = await this.read(projectId);
		const artifact = state.artifacts[ref.artifactId];
		if (artifact === undefined) throw new ArtifactAuthorityError(`Artifact metadata missing: ${ref.artifactId}`);
		if (artifact.contentHash !== ref.contentHash) throw new ArtifactAuthorityError(`Artifact reference hash mismatch: ${ref.artifactId}`);
		let body: string;
		try { body = await readFile(artifact.bodyPath, "utf8"); } catch (error) {
			if (isMissing(error)) throw new ArtifactAuthorityError(`Artifact body missing: ${ref.artifactId}`);
			throw error;
		}
		const actual = sha256(body);
		if (actual !== artifact.contentHash) throw new ArtifactAuthorityError(`Artifact body hash mismatch: ${ref.artifactId}`);
		return { artifact, body };
	}

	async createScratch(projectId: string, attemptId: string): Promise<string> {
		assertSafeId(attemptId);
		const path = join(this.projectDirectory(projectId), "scratch", `attempt-${attemptId}`);
		await mkdir(path, { recursive: true });
		return path;
	}

	assertInsideProject(projectId: string, candidate: string): string {
		const root = this.projectDirectory(projectId);
		const target = resolve(candidate);
		if (target !== root && !target.startsWith(`${root}${sep}`)) throw new Error("Path escapes the research project");
		return target;
	}

	private async serialized<T>(projectId: string, operation: () => Promise<T>): Promise<T> {
		const key = join(this.projectDirectory(projectId), "state.json").toLocaleLowerCase(), previous = ResearchStore.mutationTails.get(key) ?? Promise.resolve();
		let release!: () => void;
		const current = new Promise<void>((resolvePromise) => { release = resolvePromise; }); ResearchStore.mutationTails.set(key, current);
		await previous;
		try { return await operation(); } finally { release(); if (ResearchStore.mutationTails.get(key) === current) ResearchStore.mutationTails.delete(key); }
	}
}

function migrateProject(value: Record<string, unknown>): ResearchProjectState {
	if ((value.schemaVersion !== 1 && value.schemaVersion !== 2 && value.schemaVersion !== 3 && value.schemaVersion !== 4) || typeof value.projectId !== "string" || typeof value.name !== "string") throw new Error("Invalid research project state");
	const source = value as unknown as Partial<ResearchProjectState> & { readonly schemaVersion: 1 | 2 | 3 | 4 }, now = Date.now();
	const claims = normalizeClaims(source.claims ?? {}), migrationReports = [...(source.migrationReports ?? [])];
	let status = source.status ?? "CREATED"; const downgradedClaimIds: string[] = [];
	if (value.schemaVersion === 1) {
		for (const [claimId, revisions] of Object.entries(claims)) {
			const latest = revisions.at(-1); if (latest === undefined || (latest.status !== "PROVED" && latest.status !== "REFUTED" && latest.status !== "REDUCED")) continue;
			claims[claimId] = [...revisions, { ...latest, revision: latest.revision + 1, status: "NEEDS_REVALIDATION", invalidationReason: "MRR v1 authority lacked a canonical atomic authority receipt", createdAt: new Date().toISOString() }];
			downgradedClaimIds.push(claimId);
		}
		if (downgradedClaimIds.length > 0 && status === "PROVED") status = "PARTIAL";
		const report: StateMigrationReport = { fromVersion: 1, toVersion: 2, migratedAt: new Date().toISOString(), downgradedClaimIds, preservedArtifactCount: Object.keys(source.artifacts ?? {}).length, preservedEventCount: source.events?.length ?? 0, warnings: downgradedClaimIds.length === 0 ? [] : ["Unsafe v1 truth authority was downgraded to NEEDS_REVALIDATION; historical artifacts and events were preserved."] };
		migrationReports.push(report);
	}
	const effectiveConfig = source.effectiveConfig ?? {};
	const obligations = { ...(source.obligations ?? {}) }; if (value.schemaVersion === 1) for (const claimId of downgradedClaimIds) { let found = false; for (const [obligationId, obligation] of Object.entries(obligations)) if (obligation.claimId === claimId) { obligations[obligationId] = { ...obligation, status: "OPEN", updatedAt: new Date().toISOString(), causalReason: "MRR v1 authority requires canonical revalidation" }; found = true; } if (!found) { const claim = claims[claimId]?.at(-1), obligationId = stableId("obligation", source.projectId as string, claimId, "v2-migration-revalidation"), timestamp = new Date().toISOString(); obligations[obligationId] = { obligationId, claimId, kind: "AUDIT", statement: claim?.statement ?? claimId, status: "OPEN", priority: 100, createdAt: timestamp, updatedAt: timestamp, causalReason: "MRR v1 authority requires canonical revalidation" }; } }
	const coverage = Object.fromEntries(Object.entries(source.coverage ?? {}).map(([id, item]) => [id, item.coverageAssertion?.trim() ? item : { ...item, coverageAssertion: "MRR v1 coverage assertion unavailable; revalidation required", disposition: "INVALIDATED" as const }]));
	let rootObjectiveContract = source.rootObjectiveContract;
	if (value.schemaVersion < 3 && source.rootClaimId !== undefined && source.rootObjective !== undefined) {
		const firstRoot = claims[source.rootClaimId]?.[0], deterministicUserObjective = firstRoot?.role === "ROOT" && firstRoot.provenance === "user-objective" && firstRoot.statement === source.rootObjective && (firstRoot.assumptions?.length ?? 0) === 0;
		const recoveredContract = migratedRootContract(source.projectId as string, source.rootClaimId, source.rootObjective, firstRoot?.createdAt ?? source.createdAt ?? new Date().toISOString(), deterministicUserObjective); rootObjectiveContract = recoveredContract;
		const latestRoot = claims[source.rootClaimId]?.at(-1), unsafeRootAuthority = latestRoot !== undefined && (latestRoot.status === "PROVED" || latestRoot.status === "REFUTED" || latestRoot.status === "REDUCED") && (recoveredContract.status !== "VALID" || latestRoot.assumptions.some((item) => !recoveredContract.allowedAssumptions.some((allowed) => normalizeMath(allowed) === normalizeMath(item))));
		if (unsafeRootAuthority && latestRoot !== undefined) {
			claims[source.rootClaimId] = [...(claims[source.rootClaimId] ?? []), { ...latestRoot, revision: latestRoot.revision + 1, status: "NEEDS_REVALIDATION", invalidationReason: "MRR v3 root objective contract does not authorize the research-required assumptions", createdAt: new Date().toISOString() }];
			if (!downgradedClaimIds.includes(source.rootClaimId)) downgradedClaimIds.push(source.rootClaimId);
			status = status === "PROVED" ? "PARTIAL" : status;
			const existing = Object.values(obligations).find((item) => item.claimId === source.rootClaimId && item.status === "OPEN");
			if (existing === undefined) { const obligationId = stableId("obligation", source.projectId as string, source.rootClaimId, "v3-root-contract-revalidation"), timestamp = new Date().toISOString(); obligations[obligationId] = { obligationId, claimId: source.rootClaimId, kind: "AUDIT", statement: source.rootObjective, status: "OPEN", priority: 100, createdAt: timestamp, updatedAt: timestamp, causalReason: "Root contract authority requires revalidation" }; }
		}
		migrationReports.push({ fromVersion: 2, toVersion: 3, migratedAt: new Date().toISOString(), downgradedClaimIds: unsafeRootAuthority ? [source.rootClaimId] : [], preservedArtifactCount: Object.keys(source.artifacts ?? {}).length, preservedEventCount: source.events?.length ?? 0, warnings: rootObjectiveContract.status === "VALID" ? (unsafeRootAuthority ? ["Root authority requiring assumptions outside the recovered unconditional user objective was reopened."] : []) : ["ROOT_CONTRACT_NEEDS_REVALIDATION: original root authorization could not be recovered deterministically; current root assumptions were not treated as permission."] });
	}
	const executionPlans = Object.fromEntries(Object.entries(source.executionPlans ?? {}).map(([id, plan]) => [id, { ...plan, actionExecutions: plan.actionExecutions ?? [] }]));
	const authorityReceipts = Object.fromEntries(Object.entries(source.authorityReceipts ?? {}).map(([id, receipt]) => { const claim = claims[receipt.claimId]?.find((item) => item.revision === receipt.claimRevision); return [id, { ...receipt, statement: receipt.statement ?? claim?.statement ?? receipt.claimId, dependencyRevisions: receipt.dependencyRevisions ?? claim?.dependencyRevisions ?? {}, assumptionDischarges: receipt.assumptionDischarges ?? [] }]; }));
	const authorityValidation: Record<string, AuthorityValidationState> = { ...(source.authorityValidation ?? {}) }, migrationFourDowngrades: string[] = [];
	if (value.schemaVersion < 4) {
		for (const receipt of Object.values(authorityReceipts)) {
			const claim = claims[receipt.claimId]?.find((item) => item.revision === receipt.claimRevision), latest = claims[receipt.claimId]?.at(-1), allowed = receipt.claimId === source.rootClaimId ? new Set((rootObjectiveContract?.allowedAssumptions ?? []).map(normalizeMath)) : new Set<string>();
			const missingDischargeLineage = receipt.assumptions.some((item) => !allowed.has(normalizeMath(item))) && receipt.assumptionDischarges.length === 0;
			const missingDependencyLineage = receipt.dependencies.some((item) => receipt.dependencyRevisions[item] === undefined);
			const current = latest?.revision === receipt.claimRevision, unsafe = current && (missingDischargeLineage || missingDependencyLineage);
			authorityValidation[receipt.authorityReceiptId] = { authorityReceiptId: receipt.authorityReceiptId, status: unsafe ? "STALE" : current ? "ACTIVE" : "SUPERSEDED", ...(unsafe ? { reason: "MRR v4 authority dependency lineage could not be reconstructed without inventing provenance" } : {}), causalAuthorityReceiptIds: [], changedAt: new Date().toISOString() };
			if (unsafe && latest !== undefined && claim !== undefined && (latest.status === "PROVED" || latest.status === "REFUTED" || latest.status === "REDUCED")) {
				claims[receipt.claimId] = [...(claims[receipt.claimId] ?? []), { ...latest, revision: latest.revision + 1, status: "NEEDS_REVALIDATION", invalidationReason: "MRR v4 requires exact authority/discharge revision lineage", createdAt: new Date().toISOString() }];
				migrationFourDowngrades.push(receipt.claimId);
				const obligationId = stableId("obligation", source.projectId as string, receipt.claimId, "v4-authority-lineage-revalidation"), timestamp = new Date().toISOString();
				if (!Object.values(obligations).some((item) => item.claimId === receipt.claimId && item.status === "OPEN")) obligations[obligationId] = { obligationId, claimId: receipt.claimId, kind: "AUDIT", statement: latest.statement, status: "OPEN", priority: 100, createdAt: timestamp, updatedAt: timestamp, causalReason: "Exact authority dependency lineage requires revalidation" };
			}
		}
		if (migrationFourDowngrades.length > 0 && status === "PROVED") status = "PARTIAL";
		migrationReports.push({ fromVersion: 3, toVersion: 4, migratedAt: new Date().toISOString(), downgradedClaimIds: [...new Set(migrationFourDowngrades)], preservedArtifactCount: Object.keys(source.artifacts ?? {}).length, preservedEventCount: source.events?.length ?? 0, warnings: migrationFourDowngrades.length === 0 ? [] : ["Authorities whose exact dependency or assumption-discharge lineage could not be reconstructed were reopened; no witness authority was invented."] });
	}
	for (const receipt of Object.values(authorityReceipts)) authorityValidation[receipt.authorityReceiptId] ??= { authorityReceiptId: receipt.authorityReceiptId, status: claims[receipt.claimId]?.at(-1)?.revision === receipt.claimRevision ? "ACTIVE" : "SUPERSEDED", causalAuthorityReceiptIds: [], changedAt: receipt.createdAt };
	let finalProofHistory: FinalProofAuthority[] = [...(source.finalProofHistory ?? [])], currentFinalProofAuthority = source.currentFinalProofAuthority;
	if (value.schemaVersion < 4 && source.finalProofArtifact !== undefined && source.rootClaimId !== undefined) {
		const root = claims[source.rootClaimId]?.at(-1), rootReceipt = root === undefined ? undefined : Object.values(authorityReceipts).find((item) => item.claimId === source.rootClaimId && item.claimRevision === root.revision), active = rootReceipt !== undefined && authorityValidation[rootReceipt.authorityReceiptId]?.status === "ACTIVE";
		const historical: FinalProofAuthority = { finalProofAuthorityId: stableId("final-proof-authority", source.projectId as string, source.finalProofArtifact.artifactId, rootReceipt?.authorityReceiptId ?? "legacy"), artifact: source.finalProofArtifact, rootClaimId: source.rootClaimId, rootClaimRevision: root?.revision ?? 0, rootAuthorityReceiptId: rootReceipt?.authorityReceiptId ?? "legacy-unresolved", status: active ? "ACTIVE" : "STALE", ...(active ? {} : { reason: "Legacy final proof lacks current dependency-complete root authority" }), createdAt: source.updatedAt ?? source.createdAt ?? new Date().toISOString(), changedAt: new Date().toISOString() };
		finalProofHistory = [historical]; currentFinalProofAuthority = historical;
	}
	return {
		...source,
		schemaVersion: 4,
		status,
		claims,
		obligations,
		coverage,
		...(rootObjectiveContract === undefined ? {} : { rootObjectiveContract }),
		executionTasks: source.executionTasks ?? {}, executionPlans, authorityReceipts, authorityValidation,
		acceptedEffects: source.acceptedEffects ?? {}, trustReceipts: source.trustReceipts ?? {}, toolEvidenceReceipts: source.toolEvidenceReceipts ?? {}, supportEdges: source.supportEdges ?? [],
		bootstrapReports: source.bootstrapReports ?? [], bootstrapRuns: source.bootstrapRuns ?? {}, migrationReports, effectiveConfig, configRevision: source.configRevision ?? stableId("effective-config", source.projectId as string, JSON.stringify(effectiveConfig)), finalProofHistory,
		...(currentFinalProofAuthority === undefined ? {} : { currentFinalProofAuthority }),
		budget: { cycles: source.cycle ?? 0, plannerCalls: 0, workerCalls: 0, verifierCalls: 0, secondaryAuditorCalls: 0, literatureCalls: 0, toolCalls: 0, startedAt: now, ...(source.budget ?? {}) },
	} as ResearchProjectState;
}

function migratedRootContract(projectId: string, rootClaimId: string, statement: string, createdAt: string, deterministicUserObjective: boolean): RootObjectiveContract {
	const normalizedStatement = normalizeMath(statement);
	return {
		contractId: stableId("root-contract", projectId, rootClaimId, normalizedStatement, "1"), version: 1, rootClaimId, statement, normalizedStatement,
		allowedAssumptions: [], status: deterministicUserObjective ? "VALID" : "NEEDS_REVALIDATION",
		provenance: deterministicUserObjective ? { source: "MIGRATED_USER_OBJECTIVE", detail: "Recovered from the immutable first user-objective root revision; the v2 API admitted no root assumptions." } : { source: "LEGACY_AMBIGUOUS", detail: "Original root assumption authorization was not deterministically recoverable." },
		createdAt,
	};
}

function normalizeMath(value: string): string { return value.trim().replace(/\s+/gu, " "); }

function normalizeClaims(input: ResearchProjectState["claims"]): Record<string, readonly ClaimSnapshot[]> {
	const claims: Record<string, readonly ClaimSnapshot[]> = {};
	for (const [claimId, revisions] of Object.entries(input)) claims[claimId] = revisions.map((claim) => ({ ...claim, assumptions: claim.assumptions ?? [] }));
	return claims;
}

type MutableState = { -readonly [K in keyof ResearchProjectState]: ResearchProjectState[K] };

async function atomicJson(path: string, value: unknown, faultInjector?: ResearchStoreOptions["stateWriteFaultInjector"]): Promise<void> {
	await mkdir(dirname(path), { recursive: true });
	const temporary = `${path}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
	const handle = await open(temporary, "wx");
	try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
	await faultInjector?.("AFTER_TMP_WRITE", 0, path, temporary);
	for (let attempt = 1; ; attempt += 1) {
		try { await faultInjector?.("BEFORE_REPLACE", attempt, path, temporary); await rename(temporary, path); await faultInjector?.("AFTER_REPLACE", attempt, path, temporary); return; } catch (error) {
			const code = (error as NodeJS.ErrnoException).code;
			if ((code !== "EPERM" && code !== "EBUSY" && code !== "EACCES") || attempt >= 8) throw error;
			await new Promise<void>((resolvePromise) => setTimeout(resolvePromise, Math.min(250, attempt * attempt * 10)));
		}
	}
}

function assertSafeId(value: string): void {
	if (!/^[A-Za-z0-9_-]{1,100}$/u.test(value)) throw new Error("Identifier must contain only letters, numbers, '_' or '-'");
}

function isMissing(error: unknown): boolean { return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT"; }
function isExists(error: unknown): boolean { return (error as NodeJS.ErrnoException | undefined)?.code === "EEXIST"; }

export async function pathExists(path: string): Promise<boolean> {
	try { await access(path); return true; } catch (error) { if (isMissing(error)) return false; throw error; }
}
