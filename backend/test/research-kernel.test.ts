import { strict as assert } from "node:assert";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, type TestContext } from "node:test";
import {
	ArtifactAuthorityError, CorpusService, ResearchRuntime, ResearchStateReducer, ResearchStore,
	createResearchTools, effectIdentity, rootSynthesisReadiness, stableId, type ResearchOutcome, type TrustReceipt,
} from "../src/index.js";

async function fixture(t: TestContext) {
	const directory = await mkdtemp(join(tmpdir(), "mrr-kernel-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const store = new ResearchStore(join(directory, "data")); await store.initialize(); await store.create("p", "project");
	return { directory, store };
}

test("artifact authority resolves exact immutable bodies and rejects missing/hash-mismatched bodies", async (t) => {
	const { store } = await fixture(t); const artifact = await store.putArtifact("p", { artifactType: "CANDIDATE_PROOF", body: "exact proof body", provenance: "test" });
	assert.equal((await store.resolveArtifact("p", artifact)).body, "exact proof body");
	await assert.rejects(store.resolveArtifact("p", { ...artifact, contentHash: "0".repeat(64) }), ArtifactAuthorityError);
	await writeFile(artifact.bodyPath, "tampered", "utf8"); await assert.rejects(store.resolveArtifact("p", artifact), /hash mismatch/);
	await rm(artifact.bodyPath); await assert.rejects(store.resolveArtifact("p", artifact), /body missing/);
});

test("stable orchestration effect identity promotes exactly once across retry and restart", async (t) => {
	const { store } = await fixture(t); const runtime = new ResearchRuntime({ store, proofRunner: unresolved });
	await runtime.setRootObjective("p", "Root theorem"); const candidate = await store.putArtifact("p", { artifactType: "CANDIDATE_PROOF", body: "root proof", provenance: "worker" });
	const receipt = (slot: string): TrustReceipt => ({ receiptId: stableId("receipt", slot), claimId: (runtime as unknown as { x: string }).x ?? (""), candidate, verifierProfile: slot, evidenceInspected: [], verdict: "CORRECT", independentContext: true, stale: false, createdAt: new Date().toISOString() });
	const state = await store.read("p"); const claimId = state.rootClaimId as string; const receipts = [receipt("primary"), receipt("secondary")].map((item) => ({ ...item, claimId }));
	const outcome: ResearchOutcome = { type: "PROVED_CLAIM", claimId, statement: "Root theorem", candidate, receipts, dependencies: [] }; const reducer = new ResearchStateReducer(store);
	const first = await reducer.apply({ projectId: "p", cycleId: "cycle-1", logicalJobId: "job-1", effectSlot: "promotion", outcome }); assert.equal(first.applied, true);
	const restarted = new ResearchStateReducer(new ResearchStore(store.rootDirectory)); const retry = await restarted.apply({ projectId: "p", cycleId: "cycle-1", logicalJobId: "job-1", effectSlot: "promotion", outcome });
	assert.equal(retry.applied, false); assert.equal(Object.keys(retry.state.acceptedEffects).length, 1); assert.equal(retry.state.events.filter((event) => event.type === "research/claim_promoted").length, 1); assert.equal(retry.state.claims[claimId]?.length, 2); assert.equal(first.effect.effectId, effectIdentity("p", "cycle-1", "job-1", "promotion"));
});

test("corpus ingestion/search/bootstrap is durable, stable, and hash changes invalidate trust", async (t) => {
	const { directory, store } = await fixture(t); const corpusRoot = join(directory, "corpus"); await mkdir(corpusRoot);
	await writeFile(join(corpusRoot, "lemma-a.md"), "# Lemma A\nLemma A: every square is nonnegative. PROVED\n\n## Open problem\nOpen problem: close the odd case.");
	await writeFile(join(corpusRoot, "lemma-b.tex"), "\\begin{lemma} Lemma B follows from Lemma A. \\end{lemma}"); await writeFile(join(corpusRoot, "notes.txt"), "Failed route: parity alone does not close the case."); await writeFile(join(corpusRoot, "formal.lean"), "theorem demo : 1 = 1 := rfl");
	const runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Close every case");
	const corpus = new CorpusService(store); await corpus.attach("p", [corpusRoot]); const first = await corpus.ingest("p"); assert.equal(first.imported, 4); const ids = Object.keys(first.state.corpus); assert.equal((await corpus.search("p", "odd case")).length, 1);
	const report = await corpus.bootstrap("p"); assert.ok(report.createdClaimIds.length > 0); assert.ok(report.createdObligationIds.length > 0); assert.ok(report.proposals.every((proposal) => proposal.authority !== "VERIFIED_CURRENT"));
	const restarted = new ResearchStore(join(directory, "data")); assert.ok(Object.keys((await restarted.read("p")).claims).length > 1);
	await writeFile(join(corpusRoot, "lemma-a.md"), "# Lemma A\nLemma A changed materially.\n"); const second = await new CorpusService(restarted).ingest("p"); assert.equal(second.changed.length, 1); assert.deepEqual(Object.keys(second.state.corpus).sort(), ids.sort());
});

test("isolated production tools read corpus, write only attempt scratch, reject traversal and destructive computation", async (t) => {
	const { directory, store } = await fixture(t); const corpusRoot = join(directory, "corpus"); await mkdir(corpusRoot); await writeFile(join(corpusRoot, "source.md"), "Lemma source body"); const corpus = new CorpusService(store); await corpus.attach("p", [corpusRoot]); const ingested = await corpus.ingest("p");
	const scratch = await store.createScratch("p", "attempt-one"); const tools = Object.fromEntries(createResearchTools({ projectId: "p", corpus, scratchDirectory: scratch }).map((tool) => [tool.name, tool]));
	const read = tools.corpus_read; const write = tools.scratch_write; const computation = tools.controlled_computation; assert.ok(read && write && computation);
	const record = Object.values(ingested.state.corpus)[0]; assert.ok(record); const exact = await read.execute(read.validate({ artifactId: record.artifactId })); assert.match(JSON.stringify(exact), /Lemma source body/);
	await write.execute(write.validate({ path: "result.txt", content: "candidate" })); assert.equal(await readFile(join(scratch, "result.txt"), "utf8"), "candidate");
	await assert.rejects(write.execute(write.validate({ path: "../escape.txt", content: "x" })), /escapes/);
	assert.throws(() => computation.validate({ executable: "powershell", args: ["-Command", "Remove-Item", "state.json"] }), /not allowlisted/);
});

test("reduction, explicit case coverage, route reopening, and reverse invalidation update frontier deterministically", async (t) => {
	const { store } = await fixture(t); const runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Root"); const state = await store.read("p"); const root = state.rootClaimId as string; const proof = await store.putArtifact("p", { artifactType: "CANDIDATE_PROOF", body: "valid reduction", provenance: "test" }); const reducer = new ResearchStateReducer(store);
	const structuralReceipt: TrustReceipt = { receiptId: stableId("receipt", "structure"), claimId: root, candidate: proof, verifierProfile: "structure-verifier", evidenceInspected: [], verdict: "CORRECT", independentContext: true, stale: false, createdAt: new Date().toISOString() };
	await reducer.apply({ projectId: "p", cycleId: "c1", logicalJobId: "j1", effectSlot: "reduction", outcome: { type: "REDUCTION", claimId: root, childClaims: [{ claimId: "even", statement: "even case" }, { claimId: "odd", statement: "odd case" }], proof, receipts: [structuralReceipt], assumptions: [], dependencies: [], scope: "all integers" } });
	let current = (await reducer.apply({ projectId: "p", cycleId: "c2", logicalJobId: "j2", effectSlot: "cases", outcome: { type: "CASE_SPLIT", claimId: root, scope: "all integers", coverageAssertion: "Every integer is uniquely even or odd", cases: [{ claimId: "even", statement: "even case" }, { claimId: "odd", statement: "odd case" }], proof, receipts: [{ ...structuralReceipt, receiptId: stableId("receipt", "cases") }], assumptions: [], dependencies: [] } })).state; assert.equal(rootSynthesisReadiness(current).ready, false);
	await reducer.apply({ projectId: "p", cycleId: "c3", logicalJobId: "j3", effectSlot: "route", outcome: { type: "FAILED_ROUTE", obligationId: Object.values(current.obligations).find((item) => item.claimId === "odd")!.obligationId, family: "descent", mechanism: "dependency-descent", strategy: "use missing even", failureMechanism: "dependency missing", evidence: [proof], reopenPredicate: { type: "CLAIM_PROVED", claimId: "even" }, failureKind: "MATHEMATICAL_FAILURE" } });
	for (const child of ["even", "odd"]) { const candidate = await store.putArtifact("p", { artifactType: "CANDIDATE_PROOF", body: `proof ${child}`, provenance: "test" }); const receipt: TrustReceipt = { receiptId: stableId("receipt", child), claimId: child, candidate, verifierProfile: "v", evidenceInspected: [], verdict: "CORRECT", independentContext: true, stale: false, createdAt: new Date().toISOString() }; current = (await reducer.apply({ projectId: "p", cycleId: `c-${child}`, logicalJobId: `j-${child}`, effectSlot: "promote", outcome: { type: "PROVED_CLAIM", claimId: child, statement: `${child} case`, candidate, receipts: [receipt], dependencies: [] } })).state; }
	assert.equal(Object.values(current.coverage)[0]?.disposition, "CLOSED"); assert.ok(Object.values(current.routes).some((route) => route.status === "ACTIVE")); current = await reducer.invalidate("p", "even", "audit found a gap"); assert.equal(current.claims[root]?.at(-1)?.status, "NEEDS_REVALIDATION"); assert.ok(Object.values(current.obligations).some((item) => item.claimId === "even" && item.status === "OPEN"));
});

async function unresolved(request: import("../src/index.js").TacticalProofRequest): Promise<import("../src/index.js").TacticalResearchResult> { const now = new Date().toISOString(); return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: "TARGET_UNRESOLVED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: `receipt-${request.attemptId}`, logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: [], evidenceReceiptIds: [], startedAt: now, completedAt: now }, feedback: "unused" }; }
