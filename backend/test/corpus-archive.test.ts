import { strict as assert } from "node:assert";
import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, type TestContext } from "node:test";
import {
	CorpusArchiveCoordinator, CorpusArchivePolicy, CorpusArchiveStore, CorpusNodeResolver, CorpusProjectionCrash, DEFAULT_CORPUS_PUBLISHING_CONFIG, ResearchCorpusProjector, ResearchRuntime, ResearchStateReducer, ResearchStore, RootClosureService, stableId,
	type CorpusArchiveEffectSource, type CorpusArchiveIntent, type CorpusPublishingConfig, type FinalProofAuthority, type ResearchOutcome, type TrustReceipt,
} from "../src/index.js";

async function fixture(t: TestContext) {
	const directory = await mkdtemp(join(tmpdir(), "mrr-corpus-archive-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const store = new ResearchStore(join(directory, "data"));
	await store.initialize(); await store.create("p", "project");
	return { directory, store, config: { ...DEFAULT_CORPUS_PUBLISHING_CONFIG, enabled: true, localCheckout: join(directory, "corpus"), nodePath: "research/foundations" } };
}

function receipt(claimId: string, candidate: { readonly artifactId: string; readonly contentHash: string }, slot: string): TrustReceipt {
	return { receiptId: stableId("receipt", claimId, slot), claimId, candidate, verifierProfile: slot, evidenceInspected: [], verdict: "CORRECT", independentContext: true, stale: false, createdAt: new Date().toISOString() };
}

function source(outcome: ResearchOutcome, slot = "effect"): CorpusArchiveEffectSource { return { projectId: "p", cycleId: "cycle", logicalJobId: "job", effectSlot: slot, outcome }; }

function pendingIntent(sourceId = "source"): CorpusArchiveIntent {
	const now = new Date().toISOString();
	return { schemaVersion: 1, intentId: stableId("intent", sourceId), projectId: "p", sourceId, researchMapId: "p", researchMapVersion: 1, classificationHint: "ATTEMPT", evidenceRefs: [], createdFromAuthoritativeState: true, canonicalKey: stableId("canonical", "claim"), artifactSlug: "local-reduction", semantic: { title: "Local reduction", statement: "Local reduction", scope: "scope", sourceOutcomeType: "VERIFIED_OBSERVATION", strictResult: false }, status: "PENDING", createdAt: now, updatedAt: now };
}

test("A/N: operational and ordinary failed runs classify NO_ARCHIVE and create no intent", async (t) => {
	const { store, config } = await fixture(t), policy = new CorpusArchivePolicy(), reducer = new ResearchStateReducer(store);
	const outcome: ResearchOutcome = { type: "BLOCKED", reason: "provider retry failed", failureKind: "PROVIDER_ERROR" }, applied = await reducer.apply({ ...source(outcome), outcome });
	const disposition = policy.classifyAcceptedEffect(source(outcome), applied.effect, applied.state, config);
	assert.equal(disposition.classification, "NO_ARCHIVE"); assert.equal(disposition.intent, undefined);
	const evidence = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "unverified attempt", provenance: "test" });
	const route: ResearchOutcome = { type: "FAILED_ROUTE", obligationId: "obligation", family: "descent", mechanism: "try-descent", strategy: "try it", failureMechanism: "did not finish", evidence: [evidence], failureKind: "MATHEMATICAL_FAILURE" };
	const failed = await reducer.apply({ ...source(route, "route"), outcome: route }); assert.equal(policy.classifyAcceptedEffect(source(route, "route"), failed.effect, failed.state, config).classification, "NO_ARCHIVE");
});

test("B/D: verified unresolved reductions become ATTEMPT and scoped lemmas become RESULT from committed authority", async (t) => {
	const { store, config } = await fixture(t), runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Root theorem");
	const root = (await store.read("p")).rootClaimId as string, candidate = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "verified reduction", provenance: "worker" }), reducer = new ResearchStateReducer(store), policy = new CorpusArchivePolicy();
	const reduction: ResearchOutcome = { type: "REDUCTION", claimId: root, childClaims: [{ claimId: "local-lemma", statement: "Local lemma" }], proof: candidate, receipts: [receipt(root, candidate, "reduction")], assumptions: [], dependencies: [], scope: "bounded odd inputs" }, reduced = await reducer.apply({ ...source(reduction, "reduction"), outcome: reduction });
	const attempt = policy.classifyAcceptedEffect(source(reduction, "reduction"), reduced.effect, reduced.state, config); assert.equal(attempt.classification, "ATTEMPT"); assert.equal(attempt.intent?.semantic.authoritativeArtifact?.artifactId.startsWith("artifact-"), true);
	const lemmaCandidate = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "complete scoped proof", provenance: "worker" }), lemma: ResearchOutcome = { type: "NEW_LEMMA", claimId: "local-lemma", statement: "Local lemma", candidate: lemmaCandidate, receipts: [receipt("local-lemma", lemmaCandidate, "lemma")], dependencies: [], assumptions: [], scope: "bounded odd inputs" }, proved = await reducer.apply({ ...source(lemma, "lemma"), outcome: lemma });
	const result = policy.classifyAcceptedEffect(source(lemma, "lemma"), proved.effect, proved.state, config); assert.equal(result.classification, "RESULT"); assert.equal(result.intent?.theoremId, "local-lemma"); assert.equal(result.intent?.semantic.strictResult, false);
});

test("C: reusable RouteFailureRecord semantics require scope, evidence, and reopen conditions", async (t) => {
	const { store, config } = await fixture(t), evidence = await store.putArtifact("p", { artifactType: "COMPUTATION_RESULT", body: "exhaustive certificate", provenance: "deterministic-test" }), reducer = new ResearchStateReducer(store), policy = new CorpusArchivePolicy();
	const outcome: ResearchOutcome = { type: "ROUTE_EXHAUSTED", obligationId: "odd-case", family: "common-u", mechanism: "progression-bound", strategy: "bound every common-u progression", failureMechanism: "the accepted bound is insufficient at residue 7", failureDomain: "common-u progressions with modulus at most 100", evidence: [evidence], reopenPredicate: { type: "PARAMETER_DOMAIN_REDUCED", domainId: "common-u-domain" }, failureKind: "MATHEMATICAL_FAILURE" }, applied = await reducer.apply({ ...source(outcome, "failure"), outcome });
	const disposition = policy.classifyAcceptedEffect(source(outcome, "failure"), applied.effect, applied.state, config); assert.equal(disposition.classification, "FAILURE"); assert.match(disposition.intent?.semantic.failure?.whatIsNotRuledOut ?? "", /not ruled out/iu); assert.equal(disposition.intent?.routeFailureId?.startsWith("route-"), true);
});

test("E/F: candidate/final proof alone is NO_ARCHIVE and only active final authority opens strict RESULT", async (t) => {
	const { store, config } = await fixture(t), runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Strict root theorem");
	const root = (await store.read("p")).rootClaimId as string, candidate = await store.putArtifact("p", { artifactType: "FINAL_PROOF", body: "fresh audited final proof", provenance: "synthesizer" }), policy = new CorpusArchivePolicy();
	assert.equal(policy.classifyArtifact("CANDIDATE_PROOF"), "NO_ARCHIVE"); assert.equal(policy.classifyArtifact("FINAL_PROOF"), "NO_ARCHIVE"); assert.equal(policy.classifyArtifact("AUDIT_RECEIPT"), "NO_ARCHIVE");
	const outcome: ResearchOutcome = { type: "PROVED_CLAIM", claimId: root, statement: "Strict root theorem", candidate, receipts: [receipt(root, candidate, "primary"), receipt(root, candidate, "final")], dependencies: [], assumptions: [], scope: "ROOT_SYNTHESIS" }, applied = await new ResearchStateReducer(store).apply({ ...source(outcome, "root"), outcome });
	assert.equal(policy.classifyAcceptedEffect(source(outcome, "root"), applied.effect, applied.state, config).classification, "NO_ARCHIVE");
	const rootSnapshot = applied.state.claims[root]?.at(-1), rootAuthority = Object.values(applied.state.authorityReceipts).find((item) => item.effectId === applied.effect.effectId); assert.ok(rootSnapshot && rootAuthority);
	const authority: FinalProofAuthority = { finalProofAuthorityId: stableId("final-proof-authority", "p", candidate.artifactId, rootAuthority.authorityReceiptId), artifact: candidate, rootClaimId: root, rootClaimRevision: rootSnapshot.revision, rootAuthorityReceiptId: rootAuthority.authorityReceiptId, status: "ACTIVE", createdAt: new Date().toISOString(), changedAt: new Date().toISOString() };
	const closed = (await store.transaction("p", (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.currentFinalProofAuthority = authority; mutable.finalProofHistory = [authority]; })).state;
	const strict = policy.classifyPromotionClosure(closed, authority, config); assert.equal(strict.classification, "RESULT"); assert.equal(strict.intent?.semantic.strictResult, true); assert.equal(strict.intent?.finalProofAuthorityId, authority.finalProofAuthorityId);
});

test("J/Q: durable outbox activation and stable enqueue are idempotent and do not backfill implicitly", async (t) => {
	const { store } = await fixture(t), outbox = new CorpusArchiveStore(store), activated = await outbox.activate("p", "2026-08-27T00:00:00.000Z"); assert.deepEqual(activated.intents, {});
	const intent = pendingIntent();
	assert.equal((await outbox.enqueue(intent)).created, true); assert.equal((await outbox.enqueue(intent)).created, false); assert.equal(Object.keys((await outbox.read("p")).intents).length, 1);
});

test("archive outbox replacement is atomic across pre-replace and acknowledgement crashes", async (t) => {
	const { store } = await fixture(t), durable = new CorpusArchiveStore(store); await durable.activate("p"); const intent = pendingIntent("atomic-source");
	const beforeReplace = new CorpusArchiveStore(store, { faultInjector: (phase) => { if (phase === "AFTER_TMP_WRITE") throw new Error("crash-before-replace"); } });
	await assert.rejects(beforeReplace.enqueue(intent), /crash-before-replace/u); assert.equal((await durable.read("p")).intents[intent.intentId], undefined);
	const afterReplace = new CorpusArchiveStore(store, { faultInjector: (phase) => { if (phase === "AFTER_REPLACE") throw new Error("crash-after-replace"); } });
	await assert.rejects(afterReplace.enqueue(intent), /crash-after-replace/u); assert.equal((await durable.read("p")).intents[intent.intentId]?.sourceId, intent.sourceId); assert.equal((await durable.enqueue(intent)).created, false);
});

test("P/Q: archive hook failure cannot fail truth and activation-bounded reconciliation recovers a missing intent", async (t) => {
	const { store } = await fixture(t), throwingSink = { recordAcceptedEffect: async () => { throw new Error("outbox unavailable"); }, recordPromotionClosure: async () => { throw new Error("outbox unavailable"); }, reconcile: async () => { throw new Error("Git unavailable"); } }, runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Recovery root"); const root = (await store.read("p")).rootClaimId as string, outbox = new CorpusArchiveStore(store); await outbox.activate("p");
	const candidate = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "durable reduction", provenance: "test" }), outcome: ResearchOutcome = { type: "REDUCTION", claimId: root, childClaims: [{ claimId: "recovery-child", statement: "Recovery child" }], proof: candidate, receipts: [receipt(root, candidate, "recovery")], assumptions: [], dependencies: [], scope: "recovery scope" }, reducer = new ResearchStateReducer(store, { archiveSink: throwingSink }), applied = await reducer.apply({ ...source(outcome, "recovery"), outcome });
	assert.equal(applied.applied, true); assert.ok((await store.read("p")).acceptedEffects[applied.effect.effectId]); assert.equal(Object.keys((await outbox.read("p")).intents).length, 0);
	const config = { ...DEFAULT_CORPUS_PUBLISHING_CONFIG, enabled: true, localCheckout: "" }, coordinator = new CorpusArchiveCoordinator({ researchStore: store, archiveStore: outbox, configForState: () => config }), reconciled = await coordinator.reconcile("p");
	assert.equal(reconciled.recoveredIntentIds.length, 1); const recovered = Object.values((await outbox.read("p")).intents)[0]; assert.equal(recovered?.sourceEffectId, applied.effect.effectId); assert.equal(recovered?.status, "MANUAL_REVIEW"); assert.equal((await store.read("p")).claims[root]?.at(-1)?.status, "REDUCED");
});

test("K: node resolution uses existing canonical nodes and fails closed instead of inventing paths", async (t) => {
	const { directory } = await fixture(t), checkout = join(directory, "checkout"); await mkdir(join(checkout, "research", "foundations"), { recursive: true }); await mkdir(join(checkout, "provenance"), { recursive: true }); await writeFile(join(checkout, "provenance", "corpus-node-aliases.json"), `${JSON.stringify({ aliases: { "project:p": "research/foundations" } })}\n`);
	const resolver = new CorpusNodeResolver(), resolved = await resolver.resolve({ checkout, projectId: "p" }); assert.equal(resolved.status, "RESOLVED"); if (resolved.status === "RESOLVED") assert.equal(resolved.nodePath, "research/foundations");
	const blocked = await resolver.resolve({ checkout, projectId: "unknown" }); assert.equal(blocked.status, "BLOCKED_PLACEMENT"); assert.equal(await pathMissing(join(checkout, "research", "unknown")), true);
	const obsolete = await resolver.resolve({ checkout, projectId: "p", requestedNodePath: "research/round-17" }); assert.equal(obsolete.status, "BLOCKED_PLACEMENT");
});

test("strict post-closure hook observes active FinalProofAuthority and its failure cannot roll back closure", async (t) => {
	const { store } = await fixture(t), runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Closed root"); const root = (await store.read("p")).rootClaimId as string, initial = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "initial root proof", provenance: "test" }), initialOutcome: ResearchOutcome = { type: "PROVED_CLAIM", claimId: root, statement: "Closed root", candidate: initial, receipts: [receipt(root, initial, "initial-primary"), receipt(root, initial, "initial-secondary")], dependencies: [], assumptions: [] }; await new ResearchStateReducer(store).apply({ ...source(initialOutcome, "initial-root"), outcome: initialOutcome });
	const observed: FinalProofAuthority[] = [], sink = { recordAcceptedEffect: async () => {}, recordPromotionClosure: async (committed: import("../src/index.js").ResearchProjectState, authority: FinalProofAuthority) => { assert.equal(committed.currentFinalProofAuthority?.status, "ACTIVE"); observed.push(authority); throw new Error("Git offline after closure"); }, reconcile: async () => ({ projectId: "p", recoveredIntentIds: [], completedIntentIds: [], failedIntentIds: [] }) }, synthesizer = { synthesize: async (manifest: import("../src/index.js").SynthesisManifest) => ({ proof: "fresh final root proof", usedArtifactIds: manifest.proofArtifacts.map((item) => item.artifactId), theoremStatement: "Closed root", assumptions: [] }) }, auditor = { audit: async () => ({ verdict: "CORRECT" as const, feedback: "correct", profile: "deterministic-auditor", evidenceInspected: [] }) };
	const closed = await new RootClosureService(store, synthesizer, auditor, auditor, sink).synthesizeAndAudit("p"); assert.equal(closed.currentFinalProofAuthority?.status, "ACTIVE"); assert.equal(observed.length, 1); assert.equal(observed[0]?.finalProofAuthorityId, closed.currentFinalProofAuthority?.finalProofAuthorityId);
});

test("malformed archive state fails closed without changing legacy ResearchProjectState", async (t) => {
	const { store } = await fixture(t), outbox = new CorpusArchiveStore(store); await outbox.activate("p"); const researchBefore = await store.read("p"); await writeFile(outbox.statePath("p"), `${JSON.stringify({ schemaVersion: 1, projectId: "p", activatedAt: new Date().toISOString(), intents: { bad: { schemaVersion: 1, intentId: "bad", projectId: "p", classificationHint: "RESULT" } }, receipts: {} })}\n`); await assert.rejects(outbox.read("p"), /Invalid corpus archive intent/u); assert.deepEqual(await store.read("p"), researchBefore);
});

test("G/H/J/L: projection resumes before/after commit, generates the index idempotently, and duplicates return one receipt", async (t) => {
	for (const faultPoint of ["BEFORE_COMMIT", "AFTER_LOCAL_COMMIT"] as const) {
		const setup = await projectionFixture(t, `resume-${faultPoint.toLocaleLowerCase()}`), crashing = new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config, faultPoint });
		await assert.rejects(crashing.project(setup.intent), CorpusProjectionCrash); const headAfterCrash = await run("git", ["rev-parse", "HEAD"], setup.checkout);
		const restarted = new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config }), receipt = await restarted.project(setup.intent), duplicate = await restarted.project(setup.intent), finalHead = await run("git", ["rev-parse", "HEAD"], setup.checkout);
		assert.equal(receipt.corpusResultCommit, duplicate.corpusResultCommit); assert.equal(receipt.corpusResultCommit, finalHead.trim()); assert.equal(Number(await run("git", ["rev-list", "--count", `${setup.baseCommit}..HEAD`], setup.checkout)), 1); assert.equal(receipt.indexRegenerated, true); assert.equal(receipt.validationResult.ok, true);
		if (process.env.CORPUS_ARCHIVE_SHOW_RECEIPT === "1" && faultPoint === "BEFORE_COMMIT") t.diagnostic(JSON.stringify(receipt));
		if (faultPoint === "AFTER_LOCAL_COMMIT") assert.notEqual(headAfterCrash.trim(), setup.baseCommit); else assert.equal(headAfterCrash.trim(), setup.baseCommit);
	}
});

test("I: push success followed by receipt crash is recovered from remote containment without a second corpus commit", async (t) => {
	const setup = await projectionFixture(t, "push-recovery", true), crashing = new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config, faultPoint: "AFTER_PUSH" });
	await assert.rejects(crashing.project(setup.intent), CorpusProjectionCrash); assert.equal((await setup.outbox.read("p")).receipts[setup.intent.intentId], undefined);
	const remoteAfterCrash = (await run("git", ["--git-dir", setup.remote as string, "rev-parse", "refs/heads/main"], setup.directory)).trim(), restarted = new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config }), receipt = await restarted.project(setup.intent);
	assert.equal(receipt.corpusResultCommit, remoteAfterCrash); assert.equal(receipt.pushResult.status, "ALREADY_PRESENT"); assert.equal(Number(await run("git", ["rev-list", "--count", `${setup.baseCommit}..refs/heads/main`], setup.remote as string)), 1);
});

test("M/P: unsafe semantic content blocks commit while committed Research truth remains accepted", async (t) => {
	const setup = await projectionFixture(t, "unsafe", false, "A durable proof containing sk-abcdefghijklmnopqrstuvwxyz0123456789 must be rejected from Git."), projector = new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config });
	await assert.rejects(projector.project(setup.intent), /secret/iu); assert.equal((await run("git", ["rev-parse", "HEAD"], setup.checkout)).trim(), setup.baseCommit);
	const research = await setup.store.read("p"); assert.ok(research.acceptedEffects[setup.intent.sourceEffectId as string]); assert.equal(research.claims[research.rootClaimId as string]?.at(-1)?.status, "REDUCED");
});

test("artifact lifecycle moves one canonical claim from attempts to results instead of duplicating versioned files", async (t) => {
	const { directory, store } = await fixture(t), checkout = await initializeCorpus(directory, "lifecycle"), config = publishingConfig(checkout), runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Root");
	const root = (await store.read("p")).rootClaimId as string, seed = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "reduce root to L", provenance: "test" }), reducer = new ResearchStateReducer(store), rootReduction: ResearchOutcome = { type: "REDUCTION", claimId: root, childClaims: [{ claimId: "lemma-l", statement: "Lemma L" }], proof: seed, receipts: [receipt(root, seed, "seed")], assumptions: [], dependencies: [], scope: "root" }; await reducer.apply({ ...source(rootReduction, "seed"), outcome: rootReduction });
	const attemptBody = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "Lemma L reduces to sublemma", provenance: "test" }), attemptOutcome: ResearchOutcome = { type: "REDUCTION", claimId: "lemma-l", childClaims: [{ claimId: "sublemma", statement: "Sublemma" }], proof: attemptBody, receipts: [receipt("lemma-l", attemptBody, "attempt")], assumptions: [], dependencies: [], scope: "lemma scope" }, attempted = await reducer.apply({ ...source(attemptOutcome, "attempt"), outcome: attemptOutcome }), policy = new CorpusArchivePolicy(), attemptDisposition = policy.classifyAcceptedEffect(source(attemptOutcome, "attempt"), attempted.effect, attempted.state, config); assert.ok(attemptDisposition.intent);
	const outbox = new CorpusArchiveStore(store); await outbox.activate("p"); await outbox.enqueue(attemptDisposition.intent); await new ResearchCorpusProjector({ researchStore: store, archiveStore: outbox, config }).project(attemptDisposition.intent);
	const resultBody = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body: "complete proof of Lemma L", provenance: "test" }), resultOutcome: ResearchOutcome = { type: "NEW_LEMMA", claimId: "lemma-l", statement: "Lemma L", candidate: resultBody, receipts: [receipt("lemma-l", resultBody, "result")], dependencies: [], assumptions: [], scope: "lemma scope" }, proved = await reducer.apply({ ...source(resultOutcome, "result"), outcome: resultOutcome }), resultDisposition = policy.classifyAcceptedEffect(source(resultOutcome, "result"), proved.effect, proved.state, config); assert.ok(resultDisposition.intent); await outbox.enqueue(resultDisposition.intent);
	const resultReceipt = await new ResearchCorpusProjector({ researchStore: store, archiveStore: outbox, config }).project(resultDisposition.intent); assert.equal(resultReceipt.filesMoved.length, 1); assert.match(resultReceipt.filesMoved[0]?.from ?? "", /\/attempts\//u); assert.match(resultReceipt.filesMoved[0]?.to ?? "", /\/results\//u); assert.equal(await pathMissing(join(checkout, "research", "foundations", "attempts", "lemma-l.md")), true); assert.match(await readFile(join(checkout, "research", "foundations", "results", "lemma-l.md"), "utf8"), /complete proof of Lemma L/u);
});

test("O: projection leaves runtime, audit, scratch, tests, and immutable artifact ownership unchanged", async (t) => {
	const setup = await projectionFixture(t, "placement"), before = await setup.store.read("p"), artifactPaths = Object.values(before.artifacts).map((artifact) => artifact.bodyPath).sort(), scratch = await setup.store.createScratch("p", "placement-attempt"); await writeFile(join(scratch, "note.txt"), "runtime-owned");
	await new ResearchCorpusProjector({ researchStore: setup.store, archiveStore: setup.outbox, config: setup.config }).project(setup.intent); const after = await setup.store.read("p"); assert.deepEqual(Object.values(after.artifacts).map((artifact) => artifact.bodyPath).sort(), artifactPaths); assert.equal(await readFile(join(scratch, "note.txt"), "utf8"), "runtime-owned"); assert.equal(await pathMissing(join(setup.checkout, "runtime")), true); assert.equal(await pathMissing(join(setup.checkout, "scratch")), true);
});

interface ProjectionFixture { readonly directory: string; readonly store: ResearchStore; readonly outbox: CorpusArchiveStore; readonly config: CorpusPublishingConfig; readonly checkout: string; readonly remote?: string; readonly intent: CorpusArchiveIntent; readonly baseCommit: string; }

async function projectionFixture(t: TestContext, name: string, withRemote = false, body = "A verified reusable reduction."): Promise<ProjectionFixture> {
	const { directory, store } = await fixture(t), checkout = await initializeCorpus(directory, name), remote = withRemote ? join(directory, `${name}-remote.git`) : undefined;
	if (remote !== undefined) { await run("git", ["init", "--bare", remote], directory); await run("git", ["remote", "add", "origin", remote], checkout); await run("git", ["push", "-u", "origin", "main"], checkout); }
	const config = { ...publishingConfig(checkout), ...(remote === undefined ? {} : { repositoryUrl: remote, autoPush: true }) }, runtime = new ResearchRuntime({ store, proofRunner: unresolved }); await runtime.setRootObjective("p", "Projection root"); const root = (await store.read("p")).rootClaimId as string, artifact = await store.putArtifact("p", { artifactType: "WORKER_CANDIDATE", body, provenance: "verified-test" }), outcome: ResearchOutcome = { type: "REDUCTION", claimId: root, childClaims: [{ claimId: `${name}-child`, statement: "Projection child" }], proof: artifact, receipts: [receipt(root, artifact, name)], assumptions: [], dependencies: [], scope: "projection fixture" }, applied = await new ResearchStateReducer(store).apply({ ...source(outcome, name), outcome }), disposition = new CorpusArchivePolicy().classifyAcceptedEffect(source(outcome, name), applied.effect, applied.state, config); assert.ok(disposition.intent); const outbox = new CorpusArchiveStore(store); await outbox.activate("p"); await outbox.enqueue(disposition.intent); return { directory, store, outbox, config, checkout, ...(remote === undefined ? {} : { remote }), intent: disposition.intent, baseCommit: (await run("git", ["rev-parse", "HEAD"], checkout)).trim() };
}

function publishingConfig(checkout: string): CorpusPublishingConfig { return { ...DEFAULT_CORPUS_PUBLISHING_CONFIG, enabled: true, repositoryUrl: "", localCheckout: checkout, branch: "main", autoPush: false, indexCommand: [process.execPath, "tools/update-index.mjs"], nodePath: "research/foundations" }; }

async function initializeCorpus(directory: string, name: string): Promise<string> {
	const checkout = join(directory, `${name}-corpus`); await mkdir(join(checkout, "research", "foundations"), { recursive: true }); await mkdir(join(checkout, "tools"), { recursive: true }); await writeFile(join(checkout, "README.md"), "# Corpus\n"); await writeFile(join(checkout, "research", "foundations", "README.md"), "# Foundations\n");
	const script = `import { readdir, writeFile } from "node:fs/promises";\nimport { join } from "node:path";\nasync function walk(d,p=""){const out=[];for(const e of await readdir(d,{withFileTypes:true})){const r=p?\`${"${p}"}/\${e.name}\`:e.name;if(e.isDirectory())out.push(...await walk(join(d,e.name),r));else if(e.isFile()&&e.name.endsWith(".md"))out.push(r)}return out}\nconst files=(await walk("research")).sort();const text=\`# Research Index\\n\\n\${files.map(f=>\`- \${f}\`).join("\\n")}\\n\`;await writeFile("INDEX.md",text);await writeFile("TREE.md",text);\n`;
	await writeFile(join(checkout, "tools", "update-index.mjs"), script); await run("git", ["init", "-b", "main"], checkout); await run("git", ["config", "user.name", "Corpus Test"], checkout); await run("git", ["config", "user.email", "corpus-test@example.invalid"], checkout); await run(process.execPath, ["tools/update-index.mjs"], checkout); await run("git", ["add", "."], checkout); await run("git", ["commit", "-m", "initialize canonical corpus"], checkout); return checkout;
}

async function run(executable: string, args: readonly string[], cwd: string): Promise<string> { return new Promise((resolvePromise, reject) => { const child = spawn(executable, [...args], { cwd, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }); let stdout = "", stderr = ""; child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); }); child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); }); child.once("error", reject); child.once("close", (code) => code === 0 ? resolvePromise(stdout) : reject(new Error(`${executable} ${args.join(" ")} failed: ${stderr}`))); }); }

async function unresolved(request: import("../src/index.js").TacticalProofRequest): Promise<import("../src/index.js").TacticalResearchResult> { const now = new Date().toISOString(); return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: "TARGET_UNRESOLVED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: `receipt-${request.attemptId}`, logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: [], evidenceReceiptIds: [], startedAt: now, completedAt: now }, feedback: "unused" }; }
async function pathMissing(path: string): Promise<boolean> { try { await access(path); return false; } catch { return true; } }
