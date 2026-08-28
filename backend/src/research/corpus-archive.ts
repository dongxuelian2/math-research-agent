import { spawn } from "node:child_process";
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { archiveReceiptId, CorpusArchiveStore } from "./corpus-archive-store.js";
import { CorpusArchivePolicy, CorpusNodeResolver, strictPublicationAuthority } from "./corpus-archive-policy.js";
import { sha256 } from "./ids.js";
import { ResearchStore } from "./store.js";
import type { ArchiveReceipt, CorpusArchiveIntent, CorpusArchiveReconcileResult, CorpusArchiveSink, CorpusPublishingConfig, CorpusPushResult, CorpusValidationResult } from "./corpus-archive-types.js";
import type { AcceptedEffect, ArtifactRef, FinalProofAuthority, ResearchOutcome, ResearchProjectState } from "./types.js";

export type CorpusProjectionFaultPoint = "BEFORE_COMMIT" | "AFTER_LOCAL_COMMIT" | "AFTER_PUSH";
export interface ResearchCorpusProjectorOptions {
	readonly researchStore: ResearchStore;
	readonly archiveStore: CorpusArchiveStore;
	readonly config: CorpusPublishingConfig;
	readonly nodeResolver?: CorpusNodeResolver;
	readonly faultPoint?: CorpusProjectionFaultPoint;
	readonly writerLockPollMs?: number;
	readonly writerLockStaleMs?: number;
}

export class CorpusManualReviewError extends Error {
	constructor(readonly code: string, message: string) { super(message); this.name = "CorpusManualReviewError"; }
}

export class CorpusProjectionCrash extends Error {
	constructor(readonly point: CorpusProjectionFaultPoint) { super(`Injected corpus projection crash: ${point}`); this.name = "CorpusProjectionCrash"; }
}

export class ResearchCorpusProjector {
	private readonly resolver: CorpusNodeResolver;
	constructor(private readonly options: ResearchCorpusProjectorOptions) { this.resolver = options.nodeResolver ?? new CorpusNodeResolver(); }

	async project(input: CorpusArchiveIntent): Promise<ArchiveReceipt> {
		const state = await this.options.archiveStore.read(input.projectId), receipt = state.receipts[input.intentId];
		if (receipt !== undefined) return receipt;
		const storedIntent = state.intents[input.intentId];
		if (storedIntent === undefined) throw new Error(`Corpus archive intent not found: ${input.intentId}`);
		if (!this.options.config.enabled) throw new CorpusManualReviewError("PUBLISHING_DISABLED", "Corpus publishing is disabled");
		return this.withPublisherLock(() => this.projectLocked(storedIntent));
	}

	private async projectLocked(storedIntent: CorpusArchiveIntent): Promise<ArchiveReceipt> {
		const latest = await this.options.archiveStore.read(storedIntent.projectId), completed = latest.receipts[storedIntent.intentId];
		if (completed !== undefined) return completed;
		let intent: CorpusArchiveIntent = latest.intents[storedIntent.intentId] ?? storedIntent;
		if (intent.status === "PENDING" || intent.status === "RETRYABLE_FAILURE") intent = await this.options.archiveStore.updateIntent(intent.projectId, intent.intentId, { status: "CLAIMED", claimedAt: new Date().toISOString(), statusCode: "CLAIMED", statusDetail: "Projection worker claimed intent" });
		if (intent.status === "CLAIMED") intent = await this.options.archiveStore.updateIntent(intent.projectId, intent.intentId, { status: "PROJECTING", statusCode: "PROJECTING", statusDetail: "Git projection started" });
		if (intent.status === "MANUAL_REVIEW" || intent.status === "PERMANENT_FAILURE") throw new CorpusManualReviewError(intent.statusCode ?? intent.status, intent.statusDetail ?? `Intent is ${intent.status}`);

		const checkout = await this.ensureCheckout(), markers = await findCorpusMarkers(checkout), sameIntent = markers.filter((item) => item.intentId === intent.intentId);
		if (sameIntent.length > 1) throw new CorpusManualReviewError("DUPLICATE_ARCHIVE_IDENTITY", `Archive intent appears in multiple corpus files: ${sameIntent.map((item) => item.relativePath).join(", ")}`);
		const recoveredCommit = await this.findIntentCommit(checkout, intent.intentId);
		if (recoveredCommit !== undefined) return this.finishCommitted(intent, checkout, recoveredCommit, sameIntent[0]?.relativePath);

		const dirtyBefore = await changedPaths(checkout);
		if (dirtyBefore.length > 0 && sameIntent.length === 0) throw new CorpusManualReviewError("DIRTY_CHECKOUT", `Corpus checkout has unrelated changes: ${dirtyBefore.join(", ")}`);
		if (dirtyBefore.length === 0) await this.syncRemote(checkout);

		const resolution = await this.resolver.resolve({ checkout, projectId: intent.projectId, ...(intent.theoremId === undefined ? {} : { theoremId: intent.theoremId }), ...(intent.obligationId === undefined ? {} : { obligationId: intent.obligationId }), researchMapId: intent.researchMapId, ...(intent.requestedNodePath === undefined ? {} : { requestedNodePath: intent.requestedNodePath }) });
		if (resolution.status !== "RESOLVED") throw new CorpusManualReviewError("BLOCKED_PLACEMENT", resolution.reason);
		const classificationDirectory = directoryFor(intent.classificationHint), canonicalMatches = markers.filter((item) => item.canonicalKey === intent.canonicalKey), distinctCanonical = [...new Set(canonicalMatches.map((item) => item.relativePath))];
		if (distinctCanonical.length > 1) throw new CorpusManualReviewError("DUPLICATE_CANONICAL_ARTIFACT", `Canonical key appears in multiple files: ${distinctCanonical.join(", ")}`);
		const existingPath = sameIntent[0]?.relativePath ?? distinctCanonical[0], filename = existingPath === undefined ? `${intent.artifactSlug}.md` : basename(existingPath), targetPath = `${resolution.nodePath}/${classificationDirectory}/${filename}`.replaceAll("\\", "/");
		assertCanonicalArtifactPath(targetPath, intent.artifactSlug);
		const absoluteTarget = resolve(checkout, ...targetPath.split("/"));
		if (existingPath !== undefined && existingPath !== targetPath) {
			const collision = markers.find((item) => item.relativePath === targetPath && item.canonicalKey !== intent.canonicalKey);
			if (collision !== undefined || await fileExists(absoluteTarget)) throw new CorpusManualReviewError("TARGET_COLLISION", `Canonical target already exists: ${targetPath}`);
			await mkdir(dirname(absoluteTarget), { recursive: true }); await rename(resolve(checkout, ...existingPath.split("/")), absoluteTarget);
		} else await mkdir(dirname(absoluteTarget), { recursive: true });

		const content = await this.render(intent); await writeFile(absoluteTarget, content, "utf8");
		const baseCommit = await git(checkout, ["rev-parse", "HEAD"]), beforeIndex = await gitDiff(checkout);
		await this.runIndex(checkout); const afterFirstIndex = await gitDiff(checkout); await this.runIndex(checkout); const afterSecondIndex = await gitDiff(checkout);
		if (afterFirstIndex !== afterSecondIndex) throw new CorpusManualReviewError("INDEX_NOT_IDEMPOTENT", "Running the research index generator twice produced a second diff");
		const touched = await changedPaths(checkout), allowed = new Set([targetPath, ...(existingPath === undefined ? [] : [existingPath]), "TREE.md", "INDEX.md"]);
		const unexpected = touched.filter((path) => !allowed.has(path)); if (unexpected.length > 0) throw new CorpusManualReviewError("UNEXPECTED_CORPUS_DIFF", `Projection changed unowned files: ${unexpected.join(", ")}`);
		if (dirtyBefore.length > 0 && dirtyBefore.some((path) => !allowed.has(path))) throw new CorpusManualReviewError("DIRTY_CHECKOUT", `Recovery found unrelated changes: ${dirtyBefore.join(", ")}`);
		const validation = await this.validate(intent, checkout, targetPath, touched, afterFirstIndex === beforeIndex ? false : true);
		if (!validation.ok) throw new CorpusManualReviewError("CORPUS_VALIDATION_FAILED", validation.errors.join("; "));
		this.inject("BEFORE_COMMIT");
		await git(checkout, ["add", "--all", "--", ...touched]);
		if (await gitQuiet(checkout, ["diff", "--cached", "--quiet"])) throw new CorpusManualReviewError("EMPTY_PROJECTION", "Archive projection produced no Git change and no recoverable receipt");
		const message = `${commitVerb(intent.classificationHint)} ${intent.artifactSlug}\n\nArchive-Intent: ${intent.intentId}`; await git(checkout, ["commit", "-m", message]);
		const resultCommit = await git(checkout, ["rev-parse", "HEAD"]); this.inject("AFTER_LOCAL_COMMIT");
		intent = await this.options.archiveStore.updateIntent(intent.projectId, intent.intentId, { status: "COMMITTED_LOCAL", localCommit: resultCommit, statusCode: "COMMITTED_LOCAL", statusDetail: "Corpus projection committed locally" });
		return this.finishCommitted(intent, checkout, resultCommit, targetPath, baseCommit, validation, existingPath !== undefined && existingPath !== targetPath ? { from: existingPath, to: targetPath } : undefined);
	}

	private async finishCommitted(intent: CorpusArchiveIntent, checkout: string, commit: string, knownPath?: string, knownBase?: string, knownValidation?: CorpusValidationResult, knownMove?: { readonly from: string; readonly to: string }): Promise<ArchiveReceipt> {
		let current = (await this.options.archiveStore.read(intent.projectId)).intents[intent.intentId] ?? intent;
		if (current.status === "PROJECTING" || current.status === "CLAIMED" || current.status === "RETRYABLE_FAILURE") current = await this.options.archiveStore.updateIntent(current.projectId, current.intentId, { status: "COMMITTED_LOCAL", localCommit: commit, statusCode: "RECOVERED_LOCAL_COMMIT", statusDetail: "Recovered existing archive commit" });
		const aligned = await this.alignWithRemote(checkout, commit); commit = aligned.commit;
		if (aligned.rebased) { knownValidation = undefined; knownBase = undefined; current = await this.options.archiveStore.updateIntent(current.projectId, current.intentId, { status: "COMMITTED_LOCAL", localCommit: commit, statusCode: "REBASED_LOCAL_COMMIT", statusDetail: "Rebased archive commit onto the advanced remote without force push" }); }
		const markers = await findCorpusMarkers(checkout), marker = knownPath === undefined ? markers.find((item) => item.intentId === current.intentId) : markers.find((item) => item.relativePath === knownPath && item.intentId === current.intentId);
		if (marker === undefined) throw new CorpusManualReviewError("COMMIT_MARKER_MISSING", `Archive commit lacks intent marker: ${current.intentId}`);
		const rawChanged = await commitChanges(checkout, commit), moved = knownMove === undefined || rawChanged.moved.some((item) => item.from === knownMove.from && item.to === knownMove.to) ? rawChanged.moved : [...rawChanged.moved, knownMove], movedTargets = new Set(moved.map((item) => item.to)), changed = { created: rawChanged.created.filter((path) => !movedTargets.has(path)), updated: rawChanged.updated, moved }, touched = uniqueStrings([...changed.created, ...changed.updated, ...changed.moved.flatMap((move) => [move.from, move.to])]);
		let validation = knownValidation;
		if (validation === undefined) { const beforeIndex = await gitDiff(checkout); await this.runIndex(checkout); const afterFirst = await gitDiff(checkout); await this.runIndex(checkout); const afterSecond = await gitDiff(checkout); if (afterFirst !== afterSecond || afterFirst !== beforeIndex) throw new CorpusManualReviewError("RECOVERED_INDEX_STALE", "Recovered archive commit does not contain a current idempotent generated index"); validation = await this.validate(current, checkout, marker.relativePath, touched, true); }
		if (!validation.ok) throw new CorpusManualReviewError("RECOVERED_COMMIT_INVALID", validation.errors.join("; "));
		const pushResult = await this.pushAligned(checkout, commit, aligned.alreadyPresent);
		if (current.status !== "PUSHED" && pushResult.status !== "SKIPPED") current = await this.options.archiveStore.updateIntent(current.projectId, current.intentId, { status: "PUSHED", localCommit: commit, statusCode: pushResult.status, statusDetail: `Commit is present on ${pushResult.remote ?? "configured remote"}` });
		const contentHashes: Record<string, string> = {};
		for (const path of uniqueStrings([...changed.created, ...changed.updated, ...changed.moved.map((move) => move.to)])) contentHashes[path] = sha256(await committedBlob(checkout, commit, path));
		const baseCommit = knownBase ?? await git(checkout, ["rev-parse", `${commit}^`]), receipt: ArchiveReceipt = { schemaVersion: 1, receiptId: archiveReceiptId(current.intentId, commit), intentId: current.intentId, ...(current.sourceEffectId === undefined ? {} : { sourceEffectId: current.sourceEffectId }), ...(current.finalProofAuthorityId === undefined ? {} : { finalProofAuthorityId: current.finalProofAuthorityId }), corpusRepository: this.options.config.repositoryUrl || checkout, corpusBaseCommit: baseCommit, corpusResultCommit: commit, classification: current.classificationHint, nodePath: nodePathFromArtifact(marker.relativePath), filesCreated: changed.created, filesUpdated: changed.updated, filesMoved: changed.moved, indexRegenerated: this.options.config.indexCommand.length > 0, validationResult: validation, pushResult, contentHashes, completedAt: new Date().toISOString() };
		return this.options.archiveStore.complete(current.projectId, receipt);
	}

	private async render(intent: CorpusArchiveIntent): Promise<string> {
		await this.assertStrictAuthority(intent);
		let mathematicalContent = "";
		const ref = intent.semantic.authoritativeArtifact;
		if (ref !== undefined && intent.classificationHint !== "FAILURE" && intent.classificationHint !== "LITERATURE") {
			const resolved = await this.options.researchStore.resolveArtifact(intent.projectId, ref), allowedBodies = new Set(["PROMOTED_PROOF", "FORMAL_PROOF", "COMPUTATION_RESULT", "LITERATURE_SOURCE", ...(intent.semantic.strictResult ? ["FINAL_PROOF"] : [])]);
			if (allowedBodies.has(resolved.artifact.artifactType)) mathematicalContent = resolved.body.trim();
		}
		const lines = [`<!-- corpus-archive-intent: ${intent.intentId} -->`, `<!-- corpus-canonical-key: ${intent.canonicalKey} -->`, "", `# ${intent.semantic.title.trim()}`, "", `Classification: ${intent.classificationHint}`, `Scope: ${intent.semantic.scope}`, `Source: ${intent.sourceEffectId ?? intent.finalProofAuthorityId ?? intent.sourceId}`, "", "## Semantic statement", "", intent.semantic.statement.trim()];
		if (intent.semantic.failure !== undefined) {
			const failure = intent.semantic.failure; lines.push("", "## Attempted route", "", `${failure.routeFamily}: ${failure.strategy}`, "", "## Mathematical scope", "", failure.mathematicalScope, "", "## Obtained progress", "", failure.obtainedProgress, "", "## Failure point / obstruction", "", failure.failurePoint, "", "## What is ruled out", "", failure.whatIsRuledOut, "", "## What is not ruled out", "", failure.whatIsNotRuledOut, "", "## Recovery / reopen condition", "", failure.reopenPredicate === undefined ? "The verified counterexample closes the stated scope; a materially revised claim requires new authority." : `\`${JSON.stringify(failure.reopenPredicate)}\``);
		}
		if (mathematicalContent.length > 0) lines.push("", "## Mathematical content", "", mathematicalContent);
		lines.push("", "## Evidence identities", "", ...(intent.evidenceRefs.length === 0 ? ["No external evidence references."] : intent.evidenceRefs.map((evidence) => `- \`${evidence.artifactId}\` — \`${evidence.contentHash}\``)), "");
		return lines.join("\n");
	}

	private async validate(intent: CorpusArchiveIntent, checkout: string, targetPath: string, touched: readonly string[], indexRegenerated: boolean): Promise<CorpusValidationResult> {
		const checks: string[] = [], errors: string[] = [];
		try { assertCanonicalArtifactPath(targetPath, intent.artifactSlug); checks.push("canonical filename and placement"); } catch (error) { errors.push(String(error)); }
		const markers = await findCorpusMarkers(checkout), intentMatches = markers.filter((item) => item.intentId === intent.intentId), keyMatches = markers.filter((item) => item.canonicalKey === intent.canonicalKey);
		if (intentMatches.length !== 1) errors.push(`Archive intent marker count is ${intentMatches.length}, expected 1`); else checks.push("unique archive intent identity");
		if (keyMatches.length !== 1) errors.push(`Canonical key marker count is ${keyMatches.length}, expected 1`); else checks.push("unique canonical artifact identity");
		for (const path of touched) {
			if (/(?:^|\/)(?:runs?|scratch|audits?|proof-runs?|role-sessions?|runtime)(?:\/|$)/iu.test(path)) errors.push(`Runtime-owned directory changed: ${path}`);
			if (/(?:^|\/)(?:campaigns?|rounds?|cycles?|five-round-plan|strict-layer|critical-layer)(?:\/|$)/iu.test(path)) errors.push(`Obsolete workflow naming introduced: ${path}`);
			const absolute = resolve(checkout, ...path.split("/")); if (!await fileExists(absolute)) continue;
			const body = await readFile(absolute, "utf8"); if (containsSecret(body)) errors.push(`Potential secret or credential in ${path}`); if (containsPrivatePath(body)) errors.push(`Absolute private/local path in ${path}`);
			if (path.endsWith(".md")) for (const linkError of await brokenLinks(checkout, path, body)) errors.push(linkError);
		}
		if (!errors.some((item) => item.includes("secret") || item.includes("path"))) checks.push("no secrets or private paths");
		if (!errors.some((item) => item.includes("link"))) checks.push("internal links resolve");
		if (this.options.config.indexCommand.length === 0) errors.push("Index command is empty"); else if (indexRegenerated || touched.every((path) => path !== "TREE.md" && path !== "INDEX.md")) checks.push("index generator is idempotent");
		try { await this.assertStrictAuthority(intent); checks.push(intent.semantic.strictResult ? "strict promotion closure is current" : "non-strict semantic authority"); } catch (error) { errors.push(String(error)); }
		return { ok: errors.length === 0, checks, errors };
	}

	private async assertStrictAuthority(intent: CorpusArchiveIntent): Promise<void> {
		if (!intent.semantic.strictResult) return;
		const state = await this.options.researchStore.read(intent.projectId), authority = state.currentFinalProofAuthority;
		if (intent.finalProofAuthorityId === undefined || authority?.finalProofAuthorityId !== intent.finalProofAuthorityId || authority.artifact.artifactId !== intent.semantic.authoritativeArtifact?.artifactId || authority.artifact.contentHash !== intent.semantic.authoritativeArtifact?.contentHash || strictPublicationAuthority(state, authority) === undefined) throw new CorpusManualReviewError("STRICT_AUTHORITY_STALE", "Strict corpus result is not backed by the current final-proof/root/audit authority chain");
		await this.options.researchStore.resolveArtifact(intent.projectId, authority.artifact);
	}

	private async withPublisherLock<T>(operation: () => Promise<T>): Promise<T> {
		const configured = this.options.config.localCheckout.trim();
		if (configured.length === 0) return operation();
		const checkout = resolve(configured), lockPath = publisherLockPath(checkout), token = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`, pollMs = this.options.writerLockPollMs ?? 25, staleMs = this.options.writerLockStaleMs ?? 30_000;
		if (!await fileExists(dirname(checkout))) await mkdir(dirname(checkout), { recursive: true });
		for (;;) {
			try {
				await mkdir(lockPath); await writeFile(join(lockPath, "owner.json"), `${JSON.stringify({ pid: process.pid, token, acquiredAt: new Date().toISOString() })}\n`, { encoding: "utf8", flag: "wx" });
				break;
			} catch (error) {
				if ((error as NodeJS.ErrnoException | undefined)?.code !== "EEXIST") throw error;
				if (await publisherLockIsOrphaned(lockPath, staleMs)) {
					const stalePath = `${lockPath}.stale-${token}`;
					try { await rename(lockPath, stalePath); await rm(stalePath, { recursive: true, force: true }); } catch (renameError) { if ((renameError as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") await delay(pollMs); }
					continue;
				}
				await delay(pollMs);
			}
		}
		try { return await operation(); } finally {
			try { const owner = JSON.parse(await readFile(join(lockPath, "owner.json"), "utf8")) as { token?: string }; if (owner.token === token) await rm(lockPath, { recursive: true, force: true }); } catch (error) { if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") throw error; }
		}
	}

	private async ensureCheckout(): Promise<string> {
		const configured = this.options.config.localCheckout.trim(); if (configured.length === 0) throw new CorpusManualReviewError("CHECKOUT_NOT_CONFIGURED", "Corpus local checkout is not configured");
		const checkout = resolve(configured), gitDirectory = resolve(checkout, ".git");
		if (!await fileExists(gitDirectory)) {
			if (this.options.config.repositoryUrl.trim().length === 0) throw new CorpusManualReviewError("REPOSITORY_NOT_CONFIGURED", "Corpus repository URL is not configured");
			await mkdir(dirname(checkout), { recursive: true }); await command("git", ["clone", "--branch", this.options.config.branch, "--single-branch", this.options.config.repositoryUrl, checkout], dirname(checkout));
		}
		const root = resolve(await git(checkout, ["rev-parse", "--show-toplevel"])); if (root !== checkout) throw new CorpusManualReviewError("CHECKOUT_ROOT_MISMATCH", `Configured corpus checkout is not its Git root: ${checkout}`);
		const branch = await git(checkout, ["branch", "--show-current"]); if (branch !== this.options.config.branch) throw new CorpusManualReviewError("BRANCH_MISMATCH", `Corpus checkout branch is ${branch}, expected ${this.options.config.branch}`);
		const origin = await gitOptional(checkout, ["remote", "get-url", "origin"]); if (origin !== undefined && this.options.config.repositoryUrl.trim().length > 0 && normalizeRepository(origin) !== normalizeRepository(this.options.config.repositoryUrl)) throw new CorpusManualReviewError("REMOTE_MISMATCH", `Corpus checkout origin does not match configured repository`);
		return checkout;
	}

	private async syncRemote(checkout: string): Promise<void> {
		const origin = await gitOptional(checkout, ["remote", "get-url", "origin"]); if (origin === undefined) { if (this.options.config.autoPush) throw new CorpusManualReviewError("REMOTE_MISSING", "autoPush requires an origin remote"); return; }
		await git(checkout, ["fetch", "origin", this.options.config.branch]);
		await git(checkout, ["merge", "--ff-only", `origin/${this.options.config.branch}`]);
	}

	private async runIndex(checkout: string): Promise<void> { const [executable, ...args] = this.options.config.indexCommand; if (executable === undefined || executable.trim().length === 0) throw new CorpusManualReviewError("INDEX_COMMAND_MISSING", "Corpus index command is empty"); await command(executable, args, checkout); }
	private async findIntentCommit(checkout: string, intentId: string): Promise<string | undefined> { return gitOptional(checkout, ["log", "--all", "-1", "--format=%H", "--fixed-strings", `--grep=Archive-Intent: ${intentId}`]); }

	private async alignWithRemote(checkout: string, commit: string): Promise<{ readonly commit: string; readonly alreadyPresent: boolean; readonly rebased: boolean }> {
		if (!this.options.config.autoPush) return { commit, alreadyPresent: false, rebased: false };
		await git(checkout, ["fetch", "origin", this.options.config.branch]); const remote = await gitOptional(checkout, ["rev-parse", `origin/${this.options.config.branch}`]);
		if (remote === undefined) throw new CorpusManualReviewError("REMOTE_MISSING", "autoPush requires the configured remote branch");
		if (await gitQuiet(checkout, ["merge-base", "--is-ancestor", commit, remote])) return { commit, alreadyPresent: true, rebased: false };
		if (await gitQuiet(checkout, ["merge-base", "--is-ancestor", remote, commit])) return { commit, alreadyPresent: false, rebased: false };
		if (await git(checkout, ["rev-parse", "HEAD"]) !== commit || (await changedPaths(checkout)).length > 0) throw new Error("REMOTE_ADVANCED_RETRY: local archive commit is not a clean branch tip");
		try { await git(checkout, ["rebase", `origin/${this.options.config.branch}`]); } catch (error) { await gitOptional(checkout, ["rebase", "--abort"]); throw new Error(`REMOTE_ADVANCED_RETRY: safe rebase requires reconciliation: ${errorMessage(error)}`); }
		let rebasedCommit = await git(checkout, ["rev-parse", "HEAD"]), beforeIndex = await gitDiff(checkout); await this.runIndex(checkout); const afterFirst = await gitDiff(checkout); await this.runIndex(checkout); const afterSecond = await gitDiff(checkout);
		if (afterFirst !== afterSecond) throw new CorpusManualReviewError("INDEX_NOT_IDEMPOTENT", "Remote reconciliation made the index generator non-idempotent");
		if (afterFirst !== beforeIndex) {
			const changed = await changedPaths(checkout), unexpected = changed.filter((path) => path !== "TREE.md" && path !== "INDEX.md");
			if (unexpected.length > 0) throw new CorpusManualReviewError("REMOTE_REBASE_UNEXPECTED_DIFF", `Remote reconciliation changed unowned files: ${unexpected.join(", ")}`);
			await git(checkout, ["add", "--", ...changed]); await git(checkout, ["commit", "--amend", "--no-edit"]); rebasedCommit = await git(checkout, ["rev-parse", "HEAD"]);
		}
		return { commit: rebasedCommit, alreadyPresent: false, rebased: true };
	}

	private async pushAligned(checkout: string, commit: string, alreadyPresent: boolean): Promise<CorpusPushResult> {
		if (!this.options.config.autoPush) return { status: "SKIPPED", branch: this.options.config.branch };
		if (alreadyPresent) return { status: "ALREADY_PRESENT", remote: "origin", branch: this.options.config.branch };
		await git(checkout, ["fetch", "origin", this.options.config.branch]); const remote = await gitOptional(checkout, ["rev-parse", `origin/${this.options.config.branch}`]);
		if (remote !== undefined && await gitQuiet(checkout, ["merge-base", "--is-ancestor", commit, remote])) return { status: "ALREADY_PRESENT", remote: "origin", branch: this.options.config.branch };
		if (remote !== undefined && !await gitQuiet(checkout, ["merge-base", "--is-ancestor", remote, commit])) throw new Error("REMOTE_ADVANCED_RETRY: remote advanced after validation; retry will rebase without force push");
		await git(checkout, ["push", "--porcelain", "origin", `HEAD:${this.options.config.branch}`]); this.inject("AFTER_PUSH");
		return { status: "PUSHED", remote: "origin", branch: this.options.config.branch };
	}

	private inject(point: CorpusProjectionFaultPoint): void { if (this.options.faultPoint === point) throw new CorpusProjectionCrash(point); }
}

export interface CorpusArchiveCoordinatorOptions {
	readonly researchStore: ResearchStore;
	readonly configForState: (state: ResearchProjectState) => CorpusPublishingConfig;
	readonly archiveStore?: CorpusArchiveStore;
	readonly projectorFactory?: (config: CorpusPublishingConfig) => ResearchCorpusProjector;
}

export class CorpusArchiveCoordinator implements CorpusArchiveSink {
	readonly archiveStore: CorpusArchiveStore;
	readonly reconciler: CorpusArchiveReconciler;
	private readonly policy = new CorpusArchivePolicy();
	constructor(private readonly options: CorpusArchiveCoordinatorOptions) { this.archiveStore = options.archiveStore ?? new CorpusArchiveStore(options.researchStore); this.reconciler = new CorpusArchiveReconciler({ ...options, archiveStore: this.archiveStore }); }

	async recordAcceptedEffect(source: import("./corpus-archive-types.js").CorpusArchiveEffectSource, effect: AcceptedEffect, committed: ResearchProjectState): Promise<void> {
		const config = this.options.configForState(committed); if (!config.enabled) return; await this.archiveStore.activate(committed.projectId, committed.createdAt);
		const disposition = this.policy.classifyAcceptedEffect(source, effect, committed, config); if (disposition.intent !== undefined) await this.archiveStore.enqueue(disposition.intent);
	}

	async recordPromotionClosure(committed: ResearchProjectState, authority: FinalProofAuthority): Promise<void> {
		const config = this.options.configForState(committed); if (!config.enabled) return; await this.archiveStore.activate(committed.projectId, committed.createdAt);
		const disposition = this.policy.classifyPromotionClosure(committed, authority, config); if (disposition.intent !== undefined) await this.archiveStore.enqueue(disposition.intent);
	}

	async reconcile(projectId: string, intentId?: string): Promise<CorpusArchiveReconcileResult> {
		return this.reconciler.reconcile(projectId, intentId);
	}
}

/** Recovers post-commit enqueue gaps and drives the external Git delivery saga. */
export class CorpusArchiveReconciler {
	private readonly policy = new CorpusArchivePolicy();
	private readonly archiveStore: CorpusArchiveStore;
	constructor(private readonly options: CorpusArchiveCoordinatorOptions & { readonly archiveStore: CorpusArchiveStore }) { this.archiveStore = options.archiveStore; }

	async reconcile(projectId: string, intentId?: string): Promise<CorpusArchiveReconcileResult> {
		const research = await this.options.researchStore.read(projectId), config = this.options.configForState(research); if (!config.enabled) return { projectId, recoveredIntentIds: [], completedIntentIds: [], failedIntentIds: [] };
		const outbox = await this.archiveStore.activate(projectId, research.createdAt), recoveredIntentIds = await this.recoverMissingIntents(research, outbox.activatedAt), state = await this.archiveStore.read(projectId), selected = Object.values(state.intents).filter((intent) => intentId === undefined ? intent.status !== "COMPLETE" && intent.status !== "MANUAL_REVIEW" && intent.status !== "PERMANENT_FAILURE" : intent.intentId === intentId), completedIntentIds: string[] = [], failedIntentIds: string[] = [];
		for (let intent of selected) {
			try {
				if (intent.status === "RETRYABLE_FAILURE") intent = await this.archiveStore.resetForRetry(projectId, intent.intentId);
				const projector = this.options.projectorFactory?.(config) ?? new ResearchCorpusProjector({ researchStore: this.options.researchStore, archiveStore: this.archiveStore, config }); await projector.project(intent); completedIntentIds.push(intent.intentId);
			} catch (error) {
				if (error instanceof CorpusProjectionCrash) throw error;
				const manual = error instanceof CorpusManualReviewError, current = (await this.archiveStore.read(projectId)).intents[intent.intentId]; if (current !== undefined && current.status !== "COMPLETE") await this.archiveStore.updateIntent(projectId, intent.intentId, { status: manual ? "MANUAL_REVIEW" : "RETRYABLE_FAILURE", statusCode: manual ? error.code : "PROJECTION_ERROR", statusDetail: errorMessage(error) }); failedIntentIds.push(intent.intentId);
			}
		}
		return { projectId, recoveredIntentIds, completedIntentIds, failedIntentIds };
	}

	private async recoverMissingIntents(research: ResearchProjectState, activatedAt: string): Promise<string[]> {
		const outbox = await this.archiveStore.read(research.projectId), known = new Set(Object.values(outbox.intents).map((intent) => intent.sourceId)), recovered: string[] = [];
		for (const effect of Object.values(research.acceptedEffects).filter((item) => item.appliedAt >= activatedAt && !known.has(item.effectId))) {
			const event = research.events.find((item) => item.eventId === effect.eventId); if (event === undefined || !isResearchOutcome(event.detail)) continue;
			const source = { projectId: research.projectId, cycleId: `recovered-${effect.effectId}`, logicalJobId: effect.logicalJobId, effectSlot: effect.effectSlot, outcome: event.detail as unknown as ResearchOutcome }, disposition = this.policy.classifyAcceptedEffect(source, effect, research, this.options.configForState(research)); if (disposition.intent !== undefined) { await this.archiveStore.enqueue(disposition.intent); recovered.push(disposition.intent.intentId); known.add(effect.effectId); }
		}
		const final = research.currentFinalProofAuthority; if (final !== undefined && final.status === "ACTIVE" && final.createdAt >= activatedAt && !known.has(final.finalProofAuthorityId)) { const disposition = this.policy.classifyPromotionClosure(research, final, this.options.configForState(research)); if (disposition.intent !== undefined) { await this.archiveStore.enqueue(disposition.intent); recovered.push(disposition.intent.intentId); } }
		return recovered;
	}
}

interface CorpusMarker { readonly relativePath: string; readonly intentId: string; readonly canonicalKey: string; }

async function findCorpusMarkers(checkout: string): Promise<CorpusMarker[]> {
	const root = resolve(checkout, "research"); if (!await fileExists(root)) return [];
	const result: CorpusMarker[] = [];
	for (const path of await collectMarkdown(root)) { const body = await readFile(path, "utf8"), intent = body.match(/<!--\s*corpus-archive-intent:\s*([^\s]+)\s*-->/u)?.[1], key = body.match(/<!--\s*corpus-canonical-key:\s*([^\s]+)\s*-->/u)?.[1]; if (intent !== undefined && key !== undefined) result.push({ relativePath: relative(checkout, path).split(sep).join("/"), intentId: intent, canonicalKey: key }); }
	return result;
}

async function collectMarkdown(directory: string): Promise<string[]> { const result: string[] = []; for (const entry of await readdir(directory, { withFileTypes: true })) { const path = join(directory, entry.name); if (entry.isDirectory()) result.push(...await collectMarkdown(path)); else if (entry.isFile() && entry.name.endsWith(".md")) result.push(path); } return result; }

function directoryFor(classification: CorpusArchiveIntent["classificationHint"]): string { switch (classification) { case "ATTEMPT": return "attempts"; case "RESULT": return "results"; case "FAILURE": return "failures"; case "COMPUTATION": return "computations"; case "LITERATURE": return "literature"; case "STATE_UPDATE": throw new CorpusManualReviewError("STATE_UPDATE_REQUIRES_EXPLICIT_TARGET", "STATE_UPDATE must target a reviewed canonical README/status document"); } }
function commitVerb(classification: CorpusArchiveIntent["classificationHint"]): string { return classification === "FAILURE" ? "record" : classification === "ATTEMPT" ? "develop" : classification === "COMPUTATION" ? "add" : classification === "LITERATURE" ? "audit" : "record"; }
function nodePathFromArtifact(path: string): string { const parts = path.split("/"); return parts.slice(0, Math.max(1, parts.length - 2)).join("/"); }

function assertCanonicalArtifactPath(path: string, slug: string): void {
	const normalized = path.replaceAll("\\", "/"), name = basename(normalized);
	if (!normalized.startsWith("research/") || normalized.split("/").some((part) => part === ".." || part === "." || part.length === 0)) throw new CorpusManualReviewError("INVALID_ARTIFACT_PATH", `Artifact path is outside research/: ${path}`);
	if (!/^[a-z0-9]+(?:-[a-z0-9]+)*\.md$/u.test(name)) throw new CorpusManualReviewError("INVALID_FILENAME", `Corpus filename is not lowercase kebab case: ${name}`);
	if (name !== `${slug}.md` && !/^[a-z0-9]+(?:-[a-z0-9]+)*\.md$/u.test(name)) throw new CorpusManualReviewError("INVALID_FILENAME", `Existing canonical filename is invalid: ${name}`);
	if (/(?:^|-)(?:v\d+|final|final-new|final-final)(?:-|\.|$)/iu.test(name)) throw new CorpusManualReviewError("VERSIONED_FILENAME", `Corpus filename contains a version/final suffix: ${name}`);
}

async function changedPaths(checkout: string): Promise<string[]> { const tracked = splitNull(await gitRaw(checkout, ["diff", "--name-only", "-z"])), staged = splitNull(await gitRaw(checkout, ["diff", "--cached", "--name-only", "-z"])), untracked = splitNull(await gitRaw(checkout, ["ls-files", "--others", "--exclude-standard", "-z"])); return uniqueStrings([...tracked, ...staged, ...untracked]); }
async function gitDiff(checkout: string): Promise<string> { return gitRaw(checkout, ["diff", "--binary", "--no-ext-diff"]); }
async function committedBlob(checkout: string, commit: string, path: string): Promise<Buffer> { return commandBuffer("git", ["show", `${commit}:${path}`], checkout); }

async function commitChanges(checkout: string, commit: string): Promise<{ readonly created: string[]; readonly updated: string[]; readonly moved: { readonly from: string; readonly to: string }[] }> {
	const raw = await gitRaw(checkout, ["diff-tree", "--no-commit-id", "--name-status", "-r", "-M1%", commit]), created: string[] = [], updated: string[] = [], moved: { readonly from: string; readonly to: string }[] = [];
	for (const line of raw.split(/\r?\n/u).filter(Boolean)) { const [status, first, second] = line.split("\t"); if (status?.startsWith("R") && first !== undefined && second !== undefined) moved.push({ from: first, to: second }); else if (status === "A" && first !== undefined) created.push(first); else if (status === "M" && first !== undefined) updated.push(first); }
	return { created, updated, moved };
}

async function brokenLinks(checkout: string, markdownPath: string, body: string): Promise<string[]> {
	const errors: string[] = [], directory = dirname(resolve(checkout, ...markdownPath.split("/")));
	for (const match of body.matchAll(/!?\[[^\]]*\]\(([^)]+)\)/gu)) { let target = match[1]?.trim() ?? ""; if (target.startsWith("<") && target.endsWith(">")) target = target.slice(1, -1); target = target.split(/\s+["']/u)[0] ?? target; const pathPart = target.split("#")[0] ?? ""; if (pathPart.length === 0 || /^(?:https?:|mailto:|tel:)/iu.test(pathPart)) continue; let decoded: string; try { decoded = decodeURIComponent(pathPart); } catch { errors.push(`Invalid encoded internal link in ${markdownPath}: ${target}`); continue; } const absolute = resolve(directory, decoded); if (!inside(resolve(checkout), absolute) || !await fileExists(absolute)) errors.push(`Broken internal link in ${markdownPath}: ${target}`); }
	return errors;
}

function containsSecret(body: string): boolean { return /-----BEGIN [A-Z ]*PRIVATE KEY-----/u.test(body) || /\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,})\b/u.test(body) || /\b(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*["']?[^\s"']{8,}/iu.test(body); }
function containsPrivatePath(body: string): boolean { return /\b[A-Za-z]:\\(?:Users|Documents|AppData)\\/u.test(body) || /\/(?:Users|home)\/[^\s)]+/u.test(body); }
function inside(root: string, candidate: string): boolean { const value = relative(root, candidate); return value === "" || (!value.startsWith(`..${sep}`) && value !== ".." && !isAbsolute(value)); }

async function git(checkout: string, args: readonly string[]): Promise<string> { return (await command("git", args, checkout)).trim(); }
async function gitRaw(checkout: string, args: readonly string[]): Promise<string> { return command("git", args, checkout); }
async function gitOptional(checkout: string, args: readonly string[]): Promise<string | undefined> { try { const value = await git(checkout, args); return value.length === 0 ? undefined : value; } catch { return undefined; } }
async function gitQuiet(checkout: string, args: readonly string[]): Promise<boolean> { try { await command("git", args, checkout); return true; } catch { return false; } }
async function command(executable: string, args: readonly string[], cwd: string): Promise<string> { return new Promise((resolvePromise, reject) => { const child = spawn(executable, [...args], { cwd, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }); let stdout = "", stderr = ""; child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); }); child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); }); child.once("error", reject); child.once("close", (code) => { if (code === 0) resolvePromise(stdout); else reject(new Error(`${executable} ${args.join(" ")} failed (${String(code)}): ${stderr.trim() || stdout.trim()}`)); }); }); }
async function commandBuffer(executable: string, args: readonly string[], cwd: string): Promise<Buffer> { return new Promise((resolvePromise, reject) => { const child = spawn(executable, [...args], { cwd, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }), stdout: Buffer[] = []; let stderr = ""; child.stdout.on("data", (chunk: Buffer) => { stdout.push(chunk); }); child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); }); child.once("error", reject); child.once("close", (code) => { if (code === 0) resolvePromise(Buffer.concat(stdout)); else reject(new Error(`${executable} ${args.join(" ")} failed (${String(code)}): ${stderr.trim()}`)); }); }); }

function normalizeRepository(value: string): string { return value.trim().replaceAll("\\", "/").replace(/\.git$/u, "").replace(/\/$/u, "").toLocaleLowerCase(); }
function uniqueStrings(values: readonly string[]): string[] { return [...new Set(values.filter((value) => value.length > 0))].sort(); }
function splitNull(value: string): string[] { return value.split("\0").filter(Boolean).map((path) => path.replaceAll("\\", "/")); }
function publisherLockPath(checkout: string): string { return resolve(dirname(checkout), `.${basename(checkout)}.corpus-archive-publisher.lock`); }
async function publisherLockIsOrphaned(lockPath: string, staleMs: number): Promise<boolean> {
	try {
		const owner = JSON.parse(await readFile(join(lockPath, "owner.json"), "utf8")) as { pid?: unknown };
		if (typeof owner.pid === "number" && Number.isInteger(owner.pid) && owner.pid > 0) return !processIsAlive(owner.pid);
	} catch (error) { if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error; }
	try { return Date.now() - (await stat(lockPath)).mtimeMs >= staleMs; } catch (error) { if ((error as NodeJS.ErrnoException | undefined)?.code === "ENOENT") return false; throw error; }
}
function processIsAlive(pid: number): boolean { try { process.kill(pid, 0); return true; } catch (error) { return (error as NodeJS.ErrnoException | undefined)?.code === "EPERM"; } }
async function delay(ms: number): Promise<void> { await new Promise<void>((resolvePromise) => setTimeout(resolvePromise, ms)); }
async function fileExists(path: string): Promise<boolean> { try { return (await stat(path)).isFile() || (await stat(path)).isDirectory(); } catch (error) { if ((error as NodeJS.ErrnoException | undefined)?.code === "ENOENT") return false; throw error; } }
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function isResearchOutcome(value: unknown): boolean { if (!isRecord(value) || typeof value.type !== "string") return false; return ["PROVED_CLAIM", "NEW_LEMMA", "REFUTED_CLAIM", "REDUCTION", "CASE_SPLIT", "CASE_CLOSURE", "FAILED_ROUTE", "ROUTE_EXHAUSTED", "PARTIAL_PROGRESS", "STRUCTURAL_DISCOVERY", "VERIFIED_OBSERVATION", "NO_PROGRESS", "BLOCKED"].includes(value.type); }
