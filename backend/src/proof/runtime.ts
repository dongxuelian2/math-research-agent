import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { JsonObject } from "../models/json.js";
import type { Session, SessionCustomEntry } from "../session/index.js";
import { ProofProviderError, ProofProtocolError } from "./agent-role.js";
import { ProofRepository } from "./repository.js";
import type {
	FormalVerificationResult,
	ProofAction,
	ProofArtifactStatus,
	ProofBudgetOptions,
	ProofBudgetState,
	ProofCandidate,
	ProofEvent,
	ProofFormalVerifier,
	ProofItemInput,
	ProofLiteratureSearcher,
	ProofMode,
	ProofObligation,
	ProofOutput,
	ProofPlan,
	ProofPlanner,
	ProofPlannerContext,
	ProofPlannerWithTrace,
	ProofRejectedCandidate,
	ProofResearchContext,
	ProofResearcher,
	ProofRouteFailure,
	ProofRunResult,
	ProofState,
	ProofStatus,
	ProofStepRecord,
	ProofTask,
	ProofTaskInput,
	ProofTool,
	ProofVerifier,
	ProofVerifierContext,
	ResearchResult,
	VerificationResult,
} from "./types.js";

export interface ProofRuntimeOptions {
	readonly session: Session;
	readonly obligation: ProofObligation;
	readonly planner: ProofPlanner;
	readonly researcher: ProofResearcher;
	readonly verifier: ProofVerifier;
	readonly runId?: string;
	readonly mode?: ProofMode;
	readonly autoVerify?: boolean;
	readonly maxWorkers?: number;
	readonly maxSteps?: number;
	readonly historyLimit?: number;
	readonly workspaceDirectory?: string;
	readonly repository?: ProofRepository;
	readonly literatureSearcher?: ProofLiteratureSearcher;
	readonly tools?: readonly ProofTool[];
	readonly leanTheorem?: string;
	readonly formalVerifier?: ProofFormalVerifier;
	readonly verifyLeanItems?: boolean;
	readonly budget?: ProofBudgetOptions;
	/** Secret-free launch configuration captured for durable resume. */
	readonly runConfig?: JsonObject;
}

type ResearchWork = {
	readonly task: ProofTask;
	readonly result: ResearchResult;
	readonly providerBlocked: boolean;
};

type CandidateWork = {
	readonly task: ProofTask;
	readonly candidate: ProofCandidate;
};

type VerificationWork = {
	readonly task: ProofTask;
	readonly candidate: ProofCandidate;
	readonly result?: VerificationResult;
	readonly providerBlocked: boolean;
	readonly error?: string;
};

/**
 * OpenProver-shaped proof workflow for the TypeScript Agent Core.
 *
 * The runtime owns durable state and actions. Planner/worker/verifier agents
 * are deliberately injected so this layer remains provider-neutral and can be
 * exercised through the repository's real AgentCore API.
 */
export class ProofRuntime {
	private readonly session: Session;
	private readonly planner: ProofPlanner;
	private readonly researcher: ProofResearcher;
	private readonly verifier: ProofVerifier;
	private readonly autoVerify: boolean;
	private readonly maxWorkers: number;
	private readonly maxSteps: number;
	private readonly historyLimit: number;
	private readonly obligation: ProofObligation;
	private readonly runIdValue: string;
	private readonly runDirectory: string;
	private readonly repositoryValue: ProofRepository;
	private readonly literatureSearcher?: ProofLiteratureSearcher;
	private readonly tools: readonly ProofTool[];
	private readonly leanTheorem?: string;
	private readonly formalVerifier?: ProofFormalVerifier;
	private readonly verifyLeanItems: boolean;
	private readonly runConfigValue: JsonObject;
	private readonly requestedMode?: ProofMode;
	private stateValue: ProofState;
	private eventList: ProofEvent[] = [];
	private readonly eventListeners = new Set<(event: ProofEvent) => void | Promise<void>>();
	private started = false;
	private proofPathValue: string | undefined;

	constructor(options: ProofRuntimeOptions) {
		this.session = options.session;
		this.planner = options.planner;
		this.researcher = options.researcher;
		this.verifier = options.verifier;
		this.autoVerify = options.autoVerify ?? true;
		this.maxWorkers = Math.max(1, options.maxWorkers ?? 3);
		this.maxSteps = Math.max(1, options.maxSteps ?? 32);
		this.historyLimit = Math.max(1, options.historyLimit ?? 3);
		this.obligation = options.obligation;
		this.requestedMode = options.mode;
		this.runIdValue = selectRunId(options.session, options.obligation, options.runId);
		this.runDirectory = options.workspaceDirectory ?? join(options.session.cwd, ".math-agent", "proof-runs", this.runIdValue);
		this.repositoryValue = options.repository ?? new ProofRepository(join(this.runDirectory, "repo"));
		this.literatureSearcher = options.literatureSearcher;
		this.tools = options.tools ?? [];
		this.leanTheorem = options.leanTheorem;
		this.formalVerifier = options.formalVerifier;
		this.verifyLeanItems = options.verifyLeanItems ?? false;
		this.runConfigValue = options.runConfig ?? {};
		this.stateValue = {
			runId: this.runIdValue,
			mode: options.mode ?? "prove",
			status: "OPEN",
			step: 0,
			obligation: this.obligation,
			whiteboard: "",
			tasks: [],
			candidates: [],
			verifications: {},
			failedRoutes: [],
			rejectedCandidates: [],
			recentOutputs: [],
			stepHistory: [],
			budget: createBudget(options.budget),
		};
		this.restoreFromSession();
	}

	get runId(): string {
		return this.runIdValue;
	}

	get runDirectoryPath(): string {
		return this.runDirectory;
	}

	get repository(): ProofRepository {
		return this.repositoryValue;
	}

	get state(): ProofState {
		return snapshotState(this.stateValue);
	}

	get events(): readonly ProofEvent[] {
		return [...this.eventList];
	}

	/** Subscribe to durable workflow events without exposing workflow internals. */
	subscribe(listener: (event: ProofEvent) => void | Promise<void>): () => void {
		this.eventListeners.add(listener);
		return () => this.eventListeners.delete(listener);
	}

	/** Load durable state before a status/result/API read after a process restart. */
	async hydrate(): Promise<void> {
		await this.ensureWorkspace();
	}

	async run(signal?: AbortSignal): Promise<ProofRunResult> {
		if (this.started) return this.result();
		this.started = true;
		try {
			await this.ensureWorkspace();
			if (this.stateValue.status === "PROVED") return this.result();
			if (this.eventList.length === 0) {
				await this.emit({
					type: "proof/obligation_created",
					eventId: randomUUID(),
					runId: this.runIdValue,
					timestamp: Date.now(),
					obligation: this.obligation,
				});
				await this.changeStatus("RUNNING");
			} else if (this.stateValue.status !== "RUNNING") {
				await this.changeStatus("RUNNING", "Resuming a durable proof run");
			}

			const firstStep = this.stateValue.step + 1;
			for (let step = firstStep; step <= this.maxSteps; step += 1) {
				this.stateValue = { ...this.stateValue, step };
				const stepDirectory = await this.startStep(step);
				if (signal?.aborted) {
					await this.changeStatus("CANCELLED", "Proof run was cancelled before planning");
					await this.finishStep(step, stepDirectory, "interrupted", "Proof run was cancelled");
					return this.result("Proof run was cancelled");
				}

				const context = await this.plannerContext();
				await writeJson(join(stepDirectory, "planner_context.json"), context);
				let plan: ProofPlan;
				try {
					plan = await this.planner.plan(context, signal);
				} catch (error) {
					const message = errorMessage(error);
					await this.writePlannerFailure(stepDirectory, error);
					if (isProviderFailure(error)) {
						await this.changeStatus("BLOCKED_PROVIDER", message);
					} else {
						this.addOutput("planner", "Planner protocol failure", message);
					}
					await this.finishStep(step, stepDirectory, "failed", message);
					if (isProviderFailure(error)) return this.result(message);
					continue;
				}

				await this.writePlannerArtifacts(stepDirectory, plan);
				await this.executePlan(plan, signal, stepDirectory);
				await this.finishStep(step, stepDirectory, "completed", plan.summary ?? `Proof workflow step ${step} completed`);

				if (["PROVED", "CANCELLED", "BLOCKED_PROVIDER"].includes(this.stateValue.status)) return this.result();
				if (plan.actions.some((action) => action.action === "stop")) return this.result(this.stateValue.lastError);
			}

			if (this.stateValue.status === "RUNNING") {
				const hasVerifiedCandidate = this.hasVerifiedCandidate();
				const hasFailedWork = this.stateValue.failedRoutes.length > 0 || this.stateValue.stepHistory.some((record) => record.status === "failed");
				await this.changeStatus(
					hasVerifiedCandidate ? "CANDIDATE_READY" : hasFailedWork ? "FAILED" : "PARTIAL",
					hasVerifiedCandidate ? "A verified candidate is ready for submission" : hasFailedWork ? "No proof route passed independent verification" : "No verified candidate was produced",
				);
			}
			return this.result(
				this.stateValue.status === "CANDIDATE_READY" ? "A verified candidate is ready for submission" : undefined,
			);
		} catch (error) {
			const message = errorMessage(error);
			this.stateValue = { ...this.stateValue, lastError: message };
			await this.changeStatus(isProviderFailure(error) ? "BLOCKED_PROVIDER" : "FAILED", message);
			return this.result(message);
		}
	}

	private async ensureWorkspace(): Promise<void> {
		await mkdir(join(this.runDirectory, "steps"), { recursive: true });
		await this.repositoryValue.ensure();
		await this.loadPersistedState();
		await ensureTextFile(join(this.runDirectory, "THEOREM.md"), formatTheorem(this.obligation));
		if (this.leanTheorem !== undefined) {
			await ensureTextFile(join(this.runDirectory, "THEOREM.lean"), this.leanTheorem);
		}
		if (this.stateValue.whiteboard.length === 0) {
			try {
				this.stateValue = { ...this.stateValue, whiteboard: await readFile(join(this.runDirectory, "WHITEBOARD.md"), "utf8") };
			} catch (error) {
				if (!isMissingFile(error)) throw error;
				this.stateValue = { ...this.stateValue, whiteboard: initialWhiteboard(this.obligation, this.stateValue.mode) };
			}
		}
		await writeFile(join(this.runDirectory, "WHITEBOARD.md"), this.stateValue.whiteboard, "utf8");
		await writeJson(join(this.runDirectory, "run_config.json"), {
			...this.runConfigValue,
			runId: this.runIdValue,
			mode: this.stateValue.mode,
			maxWorkers: this.maxWorkers,
			maxSteps: this.maxSteps,
			autoVerify: this.autoVerify,
			formalVerification: this.formalVerifier !== undefined,
		});
		await this.persistState();
	}

	private async loadPersistedState(): Promise<void> {
		try {
			const raw = await readFile(join(this.runDirectory, "state.json"), "utf8");
			const saved = JSON.parse(raw) as Partial<ProofState>;
			if (saved.runId !== this.runIdValue) return;
			this.stateValue = {
				...this.stateValue,
				...saved,
				obligation: this.obligation,
				mode: this.requestedMode ?? saved.mode ?? this.stateValue.mode,
				budget: saved.budget ?? this.stateValue.budget,
			};
			if (saved.submittedCandidateId !== undefined) {
				this.proofPathValue = join(this.runDirectory, "PROOF.md");
			}
		} catch (error) {
			if (!isMissingFile(error)) throw error;
		}
	}

	private async startStep(step: number): Promise<string> {
		const directory = join(this.runDirectory, "steps", `step_${String(step).padStart(3, "0")}`);
		await mkdir(directory, { recursive: true });
		this.stateValue = {
			...this.stateValue,
			stepHistory: [...this.stateValue.stepHistory.filter((record) => record.step !== step), { step, status: "started" }],
		};
		await this.persistState();
		await this.emit({
			type: "proof/step_started",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step,
		});
		return directory;
	}

	private async finishStep(step: number, directory: string, status: ProofStepRecord["status"], summary: string): Promise<void> {
		const previous = this.stateValue.stepHistory.find((record) => record.step === step);
		const record: ProofStepRecord = {
			step,
			status,
			action: previous?.action,
			summary,
			plannerResponse: previous?.plannerResponse,
			error: status === "failed" ? summary : undefined,
			outputCount: this.stateValue.recentOutputs.filter((output) => output.step === step).length,
		};
		this.stateValue = {
			...this.stateValue,
			stepHistory: [...this.stateValue.stepHistory.filter((entry) => entry.step !== step), record].sort((a, b) => a.step - b.step),
		};
		await writeJson(join(directory, "step_status.json"), record);
		await this.emit({
			type: "proof/step_finished",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step,
			summary,
		});
	}

	private async writePlannerArtifacts(directory: string, plan: ProofPlan): Promise<void> {
		const trace = this.planner as ProofPlannerWithTrace;
		if (trace.lastTrace?.prompt !== undefined) await writeFile(join(directory, "planner_prompt.md"), trace.lastTrace.prompt, "utf8");
		if (trace.lastTrace?.response !== undefined) await writeFile(join(directory, "planner_response.txt"), trace.lastTrace.response, "utf8");
		await writeJson(join(directory, "planner_plan.json"), plan);
		this.stateValue = {
			...this.stateValue,
			stepHistory: this.stateValue.stepHistory.map((record) => record.step === this.stateValue.step ? {
				...record,
				status: "started",
				action: plan.actions.at(-1)?.action,
				plannerResponse: trace.lastTrace?.response,
			} : record),
		};
		await this.persistState();
	}

	private async writePlannerFailure(directory: string, error: unknown): Promise<void> {
		const trace = this.planner as ProofPlannerWithTrace;
		if (trace.lastTrace?.prompt !== undefined) await writeFile(join(directory, "planner_prompt.md"), trace.lastTrace.prompt, "utf8");
		if (trace.lastTrace?.response !== undefined) await writeFile(join(directory, "planner_response.txt"), trace.lastTrace.response, "utf8");
		await writeJson(join(directory, "planner_error.json"), { error: errorMessage(error), trace: trace.lastTrace ?? null });
	}

	private async executePlan(plan: ProofPlan, signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		await writeJson(join(stepDirectory, "actions.json"), plan.actions);
		for (const action of plan.actions) {
			if (signal?.aborted) {
				await this.changeStatus("CANCELLED", "Proof run was cancelled during action execution");
				return;
			}
			if (["PROVED", "CANCELLED", "BLOCKED_PROVIDER"].includes(this.stateValue.status)) return;
			this.setCurrentAction(action);
			switch (action.action) {
				case "read_theorem":
					this.addOutput("read_theorem", action.summary ?? "Read theorem", await this.readTheorem());
					break;
				case "read_items":
					this.addOutput("read_items", action.summary ?? "Read repository materials", await this.repositoryValue.readItems(action.slugs));
					break;
				case "write_items":
					await this.writeRepositoryItems(action.items, signal, stepDirectory, action.summary);
					break;
				case "write_whiteboard":
					await this.writeWhiteboard(action.content);
					break;
				case "spawn":
					await this.dispatchTasks(action.tasks, signal, stepDirectory);
					break;
				case "literature_search":
					await this.searchLiterature(action.query, action.context, signal, stepDirectory);
					break;
				case "use_tool":
					await this.useTool(action.toolName, action.input, signal);
					break;
				case "submit_proof":
					await this.submitProof(action, stepDirectory);
					break;
				case "submit_lean_proof":
					await this.submitLeanProof(action, signal, stepDirectory);
					break;
				case "stop":
					await this.changeStatus("PARTIAL", action.reason ?? "Planner stopped the run");
					return;
			}
		}
	}

	private setCurrentAction(action: ProofAction): void {
		this.stateValue = {
			...this.stateValue,
			stepHistory: this.stateValue.stepHistory.map((record) => record.step === this.stateValue.step ? { ...record, action: action.action } : record),
		};
	}

	private async writeRepositoryItems(items: readonly ProofItemInput[], signal: AbortSignal | undefined, stepDirectory: string, summary?: string): Promise<void> {
		const written: ProofItemInput[] = [];
		const feedback: string[] = [];
		for (const item of items) {
			if (signal?.aborted) return;
			if (item.format === "lean" && this.verifyLeanItems) {
				if (this.formalVerifier === undefined) {
					feedback.push(`[[${item.slug}]] rejected: no formal verifier is configured.`);
					continue;
				}
				if (item.content === undefined) {
					feedback.push(`[[${item.slug}]] rejected: Lean items need content.`);
					continue;
				}
				const result = await this.formalVerifier.verify(item.content, {
					runId: this.runIdValue,
					step: this.stateValue.step,
					obligation: this.obligation,
					theoremText: this.leanTheorem,
					workDirectory: join(stepDirectory, "lean"),
				}, signal);
				await writeJson(join(stepDirectory, `lean_${slugify(item.slug)}.json`), result);
				if (!result.ok) {
					feedback.push(`[[${item.slug}]] Lean verification failed: ${result.feedback}`);
					continue;
				}
			}
			await this.repositoryValue.writeItem(item);
			written.push(item);
		}
		if (written.length > 0) {
			await this.emit({
				type: "proof/repository_updated",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				items: written,
			});
		}
		const content = [
			written.length > 0 ? `Wrote repository items: ${written.map((item) => `[[${item.slug}]]`).join(", ")}` : "No repository item was written.",
			...feedback,
		].join("\n");
		this.addOutput("write_items", summary ?? "Repository updated", content);
	}

	private async writeWhiteboard(content: string): Promise<void> {
		this.stateValue = { ...this.stateValue, whiteboard: content };
		await writeFile(join(this.runDirectory, "WHITEBOARD.md"), content, "utf8");
		await this.emit({
			type: "proof/whiteboard_updated",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step: this.stateValue.step,
			content,
		});
	}

	private async dispatchTasks(taskInputs: readonly ProofTaskInput[], signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		const materialized = taskInputs.slice(0, this.maxWorkers).map((input) => this.materializeTask(input));
		this.stateValue = { ...this.stateValue, tasks: [...this.stateValue.tasks, ...materialized] };
		const accepted: ProofTask[] = [];
		const seenRoutes = new Set<string>();
		for (const task of materialized) {
			if (seenRoutes.has(task.routeFingerprint) || this.stateValue.failedRoutes.some((failure) => failure.routeFingerprint === task.routeFingerprint)) {
				await this.recordFailure({
					routeFingerprint: task.routeFingerprint,
					taskId: task.taskId,
					reason: "This proof route was already rejected and is blocked from retry.",
					step: this.stateValue.step,
				});
				this.addOutput("spawn", task.summary, "Rejected duplicate failed route.");
				continue;
			}
			if (!this.consumeBudget("workerCalls")) {
				this.addOutput("spawn", task.summary, "Worker budget exhausted; task was not dispatched.");
				continue;
			}
			seenRoutes.add(task.routeFingerprint);
			accepted.push(task);
			await this.emit({
				type: "proof/task_dispatched",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				task,
			});
		}

		const researchWorks = await mapConcurrent(accepted, this.maxWorkers, async (task): Promise<ResearchWork> => {
			await writeFile(join(stepDirectory, `worker_${slugify(task.taskId)}_task.md`), task.description, "utf8");
			if (signal?.aborted) return { task, result: { kind: "blocked", reason: "Proof run was aborted" }, providerBlocked: false };
			try {
				const referencedMaterials = await this.repositoryValue.resolveWikilinks(task.description);
				const context: ProofResearchContext = {
					runId: this.runIdValue,
					step: this.stateValue.step,
					obligation: this.obligation,
					whiteboard: this.stateValue.whiteboard,
					task,
					referencedMaterials,
				};
				return { task, result: await this.researcher.research(context, signal), providerBlocked: false };
			} catch (error) {
				return { task, result: { kind: "blocked", reason: errorMessage(error) }, providerBlocked: isProviderFailure(error) };
			}
		});

		const candidates: CandidateWork[] = [];
		let providerBlockedResearch = 0;
		for (const work of researchWorks) {
			await writeJson(join(stepDirectory, `worker_${slugify(work.task.taskId)}_result.json`), work.result);
			if (work.result.kind === "candidate") {
				await writeFile(join(stepDirectory, `worker_${slugify(work.task.taskId)}_output.md`), work.result.candidate.content, "utf8");
			} else {
				await writeFile(join(stepDirectory, `worker_${slugify(work.task.taskId)}_output.md`), work.result.kind === "observation" ? work.result.content : work.result.reason, "utf8");
			}
			await this.emit({
				type: "proof/research_result",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				taskId: work.task.taskId,
				result: work.result,
			});
			if (work.providerBlocked) providerBlockedResearch += 1;
			if (work.result.kind === "candidate") {
				const candidate = this.materializeCandidate(work.task, work.result);
				const duplicate = this.duplicateCandidateReason(candidate);
				if (duplicate !== undefined) {
					await this.rejectCandidate(candidate, work.task, duplicate);
					continue;
				}
				this.stateValue = { ...this.stateValue, candidates: [...this.stateValue.candidates, candidate] };
				await this.repositoryValue.writeItem({
					slug: `candidates/${candidate.candidateId}`,
					content: candidate.content,
					summary: candidate.strategy,
				});
				candidates.push({ task: work.task, candidate });
				continue;
			}
			this.addOutput("spawn", work.task.summary, work.result.kind === "observation" ? `${work.result.content}${work.result.suggestedNext === undefined ? "" : `\nNext: ${work.result.suggestedNext}`}` : work.result.reason);
		}

		if (!this.autoVerify || candidates.length === 0) {
			this.addOutput("spawn", "Merged Worker results", mergeWorkerVerifierOutput(researchWorks, []));
			if (providerBlockedResearch === accepted.length && accepted.length > 0) await this.changeStatus("BLOCKED_PROVIDER", "All proof workers were blocked by their providers.");
			return;
		}

		const verificationWorks = await mapConcurrent(candidates, this.maxWorkers, async (work): Promise<VerificationWork> => {
			if (!this.consumeBudget("verifierCalls")) {
				return { ...work, providerBlocked: false, result: { verdict: "UNFINISHED", feedback: "Verifier budget exhausted; candidate was not verified." } };
			}
			if (signal?.aborted) return { ...work, providerBlocked: false, error: "Proof run was aborted" };
			try {
				const context: ProofVerifierContext = {
					runId: this.runIdValue,
					step: this.stateValue.step,
					obligation: this.obligation,
					task: work.task,
				};
				return { ...work, providerBlocked: false, result: await this.verifier.verify(work.candidate, context, signal) };
			} catch (error) {
				return { ...work, providerBlocked: isProviderFailure(error), error: errorMessage(error) };
			}
		});

		let providerBlockedVerifiers = 0;
		for (const work of verificationWorks) {
			await writeJson(join(stepDirectory, `verifier_${slugify(work.candidate.candidateId)}.json`), work.result ?? { error: work.error });
			if (work.providerBlocked) providerBlockedVerifiers += 1;
			if (work.result === undefined) {
				this.addOutput("spawn", work.task.summary, `Verifier failed: ${work.error ?? "unknown verifier error"}`);
				continue;
			}
			await this.recordVerification(work.task, work.candidate, work.result);
		}
		this.addOutput("spawn", "Merged Worker + Verifier feedback", mergeWorkerVerifierOutput(researchWorks, verificationWorks));
		if (providerBlockedVerifiers === verificationWorks.length && verificationWorks.length > 0) {
			await this.changeStatus("BLOCKED_PROVIDER", "All proof verifiers were blocked by their providers.");
		}
	}

	private async recordVerification(task: ProofTask, candidate: ProofCandidate, verification: VerificationResult): Promise<void> {
		this.stateValue = {
			...this.stateValue,
			verifications: { ...this.stateValue.verifications, [candidate.candidateId]: verification },
		};
		await this.emit({
			type: "proof/verification_result",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step: this.stateValue.step,
			candidateId: candidate.candidateId,
			result: verification,
		});
		const feedback = verification.feedback || `Verifier verdict: ${verification.verdict}`;
		this.addOutput("spawn", task.summary, `VERDICT: ${verification.verdict}\n${feedback}`);
		if (verification.verdict === "CORRECT") {
			await this.emit({
				type: "proof/candidate_ready",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				candidate,
			});
			await this.changeStatus("CANDIDATE_READY", "A worker candidate passed independent verification");
			return;
		}
		await this.recordFailure({
			routeFingerprint: candidate.routeFingerprint,
			taskId: task.taskId,
			reason: feedback,
			candidateFingerprint: candidate.candidateFingerprint,
			claimFingerprint: candidate.claimFingerprint,
			step: this.stateValue.step,
		});
	}

	private duplicateCandidateReason(candidate: ProofCandidate): string | undefined {
		if (this.stateValue.candidates.some((item) => item.candidateFingerprint === candidate.candidateFingerprint)) return "The exact candidate text duplicates an earlier candidate.";
		if (this.stateValue.rejectedCandidates.some((item) => item.candidateFingerprint === candidate.candidateFingerprint)) return "The exact candidate text was previously rejected.";
		if (this.stateValue.rejectedCandidates.some((item) => item.routeFingerprint === candidate.routeFingerprint && item.claimFingerprint === candidate.claimFingerprint)) return "The same claim on the same failed route is blocked.";
		return undefined;
	}

	private async rejectCandidate(candidate: ProofCandidate, task: ProofTask, reason: string): Promise<void> {
		const rejected: ProofRejectedCandidate = {
			candidateFingerprint: candidate.candidateFingerprint,
			claimFingerprint: candidate.claimFingerprint,
			routeFingerprint: candidate.routeFingerprint,
			reason,
			step: this.stateValue.step,
		};
		this.stateValue = { ...this.stateValue, rejectedCandidates: [...this.stateValue.rejectedCandidates, rejected] };
		await this.recordFailure({
			routeFingerprint: candidate.routeFingerprint,
			taskId: task.taskId,
			reason,
			candidateFingerprint: candidate.candidateFingerprint,
			claimFingerprint: candidate.claimFingerprint,
			step: this.stateValue.step,
		});
		this.addOutput("spawn", task.summary, `Candidate rejected by novelty gate: ${reason}`);
	}

	private async searchLiterature(query: string, context: string | undefined, signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		if (this.literatureSearcher === undefined) {
			await this.changeStatus("BLOCKED_PROVIDER", "Literature search was requested but no search provider is configured");
			return;
		}
		if (!this.consumeBudget("literatureSearches")) {
			this.addOutput("literature_search", query, "Literature-search budget exhausted.");
			return;
		}
		try {
			const result = await this.literatureSearcher.search(query, await this.plannerContext(), signal);
			await writeFile(join(stepDirectory, "literature.md"), result.content, "utf8");
			this.addOutput("literature_search", query, result.content);
			await this.repositoryValue.writeItem({
				slug: `literature/${slugify(query)}`,
				content: result.content,
				summary: result.sources?.join(", ") ?? context ?? query,
			});
		} catch (error) {
			await this.changeStatus("BLOCKED_PROVIDER", `Literature search failed: ${errorMessage(error)}`);
		}
	}

	private async useTool(toolName: string, input: JsonObject, signal: AbortSignal | undefined): Promise<void> {
		const tool = this.tools.find((item) => item.name === toolName);
		if (tool === undefined) {
			await this.changeStatus("BLOCKED_PROVIDER", `Proof tool is not configured: ${toolName}`);
			return;
		}
		if (!this.consumeBudget("toolCalls")) {
			this.addOutput("use_tool", toolName, "Tool budget exhausted.");
			return;
		}
		try {
			const result = stringify(await tool.execute(input, await this.plannerContext(), signal));
			this.addOutput("use_tool", toolName, result);
			await this.emit({
				type: "proof/tool_result",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				toolName,
				ok: true,
				content: result,
			});
		} catch (error) {
			const content = errorMessage(error);
			this.addOutput("use_tool", toolName, content);
			await this.emit({
				type: "proof/tool_result",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				toolName,
				ok: false,
				content,
			});
		}
	}

	private async submitProof(action: Extract<ProofAction, { action: "submit_proof" }>, stepDirectory: string): Promise<void> {
		if (this.stateValue.mode === "formalize_only") {
			this.addOutput("submit_proof", "Submission rejected", "formalize_only requires submit_lean_proof.");
			return;
		}
		let candidate = action.candidateId === undefined ? undefined : this.stateValue.candidates.find((item) => item.candidateId === action.candidateId);
		let proofContent: string | undefined;
		if (action.proofSlug !== undefined) {
			const item = await this.repositoryValue.readItem(action.proofSlug);
			if (item === undefined) {
				this.addOutput("submit_proof", "Submission rejected", `Repository item [[${action.proofSlug}]] was not found.`);
				return;
			}
			proofContent = item.content;
			candidate ??= this.stateValue.candidates.find((itemCandidate) => itemCandidate.content.trim() === item.content.trim());
		}
		if (candidate === undefined) {
			this.addOutput("submit_proof", "Submission rejected", "No candidate matches the submitted proof item.");
			return;
		}
		const verification = this.stateValue.verifications[candidate.candidateId];
		if (verification?.verdict !== "CORRECT") {
			this.addOutput("submit_proof", "Submission rejected", "The candidate has no independent CORRECT verification.");
			return;
		}
		if (!this.noveltyGate(candidate)) {
			this.addOutput("submit_proof", "Submission rejected", "Candidate novelty gate rejected the candidate's text, claim, or route.");
			return;
		}
		const proofPath = join(this.runDirectory, "PROOF.md");
		const proof = [
			`# Proof: ${this.obligation.theorem}`,
			"",
			proofContent ?? candidate.content,
			"",
			`<!-- candidate_id: ${candidate.candidateId} -->`,
			`<!-- verifier: ${verification.verdict} -->`,
			"",
		].join("\n");
		await writeFile(proofPath, proof, "utf8");
		await writeFile(join(stepDirectory, "submitted_proof.md"), proof, "utf8");
		this.proofPathValue = proofPath;
		this.stateValue = { ...this.stateValue, submittedCandidateId: candidate.candidateId, submittedProofSlug: action.proofSlug };
		await this.emit({
			type: "proof/submitted",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step: this.stateValue.step,
			candidateId: candidate.candidateId,
			proofPath,
		});
		await this.checkCompletion(`PROOF.md written${action.proofSlug === undefined ? "" : ` from [[${action.proofSlug}]]`}.`);
	}

	private async submitLeanProof(action: Extract<ProofAction, { action: "submit_lean_proof" }>, signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		if (this.stateValue.mode === "prove") {
			this.addOutput("submit_lean_proof", "Submission rejected", "The current mode only requests an informal proof.");
			return;
		}
		const proofSlug = action.proofSlug ?? action.leanProofSlug;
		if (proofSlug === undefined) {
			this.addOutput("submit_lean_proof", "Submission rejected", "Provide leanProofSlug/proofSlug.");
			return;
		}
		const item = await this.repositoryValue.readItem(proofSlug);
		if (item === undefined || item.format !== "lean") {
			this.addOutput("submit_lean_proof", "Submission rejected", `[[${proofSlug}]] is missing or is not a Lean repository item.`);
			return;
		}
		if (this.formalVerifier === undefined) {
			await this.changeStatus("BLOCKED_PROVIDER", "submit_lean_proof requires a configured formal verifier.");
			return;
		}
		let result: FormalVerificationResult;
		try {
			result = await this.formalVerifier.verify(item.content, {
				runId: this.runIdValue,
				step: this.stateValue.step,
				obligation: this.obligation,
				theoremText: this.leanTheorem,
				workDirectory: join(stepDirectory, "lean"),
			}, signal);
		} catch (error) {
			await this.changeStatus("BLOCKED_PROVIDER", `Formal verifier failed: ${errorMessage(error)}`);
			return;
		}
		await mkdir(join(stepDirectory, "lean"), { recursive: true });
		await writeFile(join(stepDirectory, "lean", "proof_attempt.lean"), item.content, "utf8");
		await writeJson(join(stepDirectory, "lean", "proof_result.json"), result);
		await this.emit({
			type: "proof/formal_verification_result",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			step: this.stateValue.step,
			proofSlug,
			result,
		});
		if (!result.ok) {
			this.addOutput("submit_lean_proof", `Formal verification failed for [[${proofSlug}]]`, result.feedback);
			return;
		}
		const proofLeanPath = join(this.runDirectory, "PROOF.lean");
		await writeFile(proofLeanPath, item.content, "utf8");
		this.stateValue = { ...this.stateValue, proofLeanPath };
		await this.checkCompletion(`PROOF.lean written from [[${proofSlug}]] (formal verification passed).`);
	}

	private noveltyGate(candidate: ProofCandidate): boolean {
		if (this.stateValue.rejectedCandidates.some((item) => item.candidateFingerprint === candidate.candidateFingerprint)) return false;
		if (this.stateValue.failedRoutes.some((item) => item.routeFingerprint === candidate.routeFingerprint && item.candidateFingerprint === candidate.candidateFingerprint)) return false;
		return true;
	}

	private async checkCompletion(feedback: string): Promise<void> {
		const hasMd = this.proofPathValue !== undefined;
		const hasLean = this.stateValue.proofLeanPath !== undefined;
		const complete = this.stateValue.mode === "prove" ? hasMd : this.stateValue.mode === "formalize_only" ? hasLean : hasMd && hasLean;
		if (complete) {
			this.addOutput("submit", "Proof workflow complete", feedback);
			await this.changeStatus("PROVED", feedback);
			return;
		}
		this.addOutput("submit", "Proof artifact accepted; workflow continues", feedback);
		await this.changeStatus("CANDIDATE_READY", feedback);
	}

	private async readTheorem(): Promise<string> {
		const parts = [`## THEOREM.md\n\n${formatTheorem(this.obligation)}`];
		if (this.leanTheorem !== undefined) parts.push(`## THEOREM.lean\n\n\`\`\`lean\n${this.leanTheorem}\n\`\`\``);
		if (this.proofPathValue !== undefined) parts.push(`## PROOF.md\n\n${await readFile(this.proofPathValue, "utf8")}`);
		if (this.stateValue.proofLeanPath !== undefined) parts.push(`## PROOF.lean\n\n\`\`\`lean\n${await readFile(this.stateValue.proofLeanPath, "utf8")}\n\`\`\``);
		return parts.join("\n\n");
	}

	private async plannerContext(): Promise<ProofPlannerContext> {
		const items = await this.repositoryValue.listSummaries();
		return {
			runId: this.runIdValue,
			step: this.stateValue.step,
			obligation: this.obligation,
			mode: this.stateValue.mode,
			status: this.stateValue.status,
			whiteboard: this.stateValue.whiteboard,
			repository: items.map((item) => ({ ...item, content: "" })),
			repositoryIndex: await this.repositoryValue.formatIndex(),
			candidates: [...this.stateValue.candidates],
			failedRoutes: [...this.stateValue.failedRoutes],
			recentOutputs: [...this.stateValue.recentOutputs],
			stepHistory: [...this.stateValue.stepHistory],
			budget: this.stateValue.budget,
			artifacts: this.artifactStatus(),
		};
	}

	private artifactStatus(): ProofArtifactStatus {
		return {
			theoremPath: join(this.runDirectory, "THEOREM.md"),
			whiteboardPath: join(this.runDirectory, "WHITEBOARD.md"),
			statePath: join(this.runDirectory, "state.json"),
			...(this.leanTheorem === undefined ? {} : { leanTheoremPath: join(this.runDirectory, "THEOREM.lean") }),
			...(this.proofPathValue === undefined ? {} : { proofPath: this.proofPathValue }),
			...(this.stateValue.proofLeanPath === undefined ? {} : { proofLeanPath: this.stateValue.proofLeanPath }),
		};
	}

	private materializeTask(input: ProofTaskInput): ProofTask {
		const taskId = input.taskId ?? `${this.runIdValue}-task-${this.stateValue.tasks.length + 1}`;
		const routeKey = input.routeKey ?? input.description;
		return {
			taskId,
			summary: input.summary,
			description: input.description,
			routeFingerprint: fingerprint(`${this.obligation.theorem}\n${routeKey}`),
			...(input.routeKey === undefined ? {} : { routeKey: input.routeKey }),
		};
	}

	private materializeCandidate(task: ProofTask, result: Extract<ResearchResult, { kind: "candidate" }>): ProofCandidate {
		const content = result.candidate.content.trim();
		const claim = result.candidate.claim;
		return {
			candidateId: result.candidate.candidateId ?? `${task.taskId}-candidate`,
			taskId: task.taskId,
			content,
			strategy: result.candidate.strategy,
			routeFingerprint: task.routeFingerprint,
			claimFingerprint: result.candidate.claimFingerprint ?? fingerprint(claim ?? this.obligation.theorem),
			candidateFingerprint: fingerprint(`${this.obligation.theorem}\n${content}`),
			...(claim === undefined ? {} : { claim }),
		};
	}

	private addOutput(action: string, summary: string, content: string): void {
		const output: ProofOutput = { step: this.stateValue.step, action, summary, content };
		this.stateValue = { ...this.stateValue, recentOutputs: [...this.stateValue.recentOutputs, output].slice(-this.historyLimit) };
	}

	private async recordFailure(failure: ProofRouteFailure): Promise<void> {
		if (!this.stateValue.failedRoutes.some((item) => item.routeFingerprint === failure.routeFingerprint && item.reason === failure.reason)) {
			this.stateValue = { ...this.stateValue, failedRoutes: [...this.stateValue.failedRoutes, failure] };
		}
		await this.emit({
			type: "proof/route_failed",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			failure,
		});
	}

	private consumeBudget(counter: "workerCalls" | "verifierCalls" | "literatureSearches" | "toolCalls"): boolean {
		const budget = this.stateValue.budget;
		const limitKey = {
			workerCalls: "maxWorkerCalls",
			verifierCalls: "maxVerifierCalls",
			literatureSearches: "maxLiteratureSearches",
			toolCalls: "maxToolCalls",
		}[counter] as keyof ProofBudgetState;
		const limit = budget[limitKey] as number | undefined;
		if (limit !== undefined && budget[counter] >= limit) return false;
		if (budget.maxWallTimeMs !== undefined && Date.now() - budget.startedAt >= budget.maxWallTimeMs) return false;
		this.stateValue = { ...this.stateValue, budget: { ...budget, [counter]: budget[counter] + 1 } };
		return true;
	}

	private hasVerifiedCandidate(): boolean {
		return this.stateValue.candidates.some((candidate) => this.stateValue.verifications[candidate.candidateId]?.verdict === "CORRECT");
	}

	private async changeStatus(status: ProofStatus, reason?: string): Promise<void> {
		this.stateValue = {
			...this.stateValue,
			status,
			...(reason === undefined ? {} : { lastError: reason }),
		};
		await this.emit({
			type: "proof/status_changed",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: Date.now(),
			status,
			...(reason === undefined ? {} : { reason }),
		});
	}

	private async emit(event: ProofEvent): Promise<void> {
		this.eventList.push(event);
		await this.session.appendCustom({
			namespace: "proof",
			type: event.type,
			payload: event as unknown as JsonObject,
			timestamp: event.timestamp,
		});
		await this.persistState();
		await Promise.allSettled([...this.eventListeners].map((listener) => Promise.resolve().then(() => listener(event))));
	}

	private async persistState(): Promise<void> {
		await mkdir(this.runDirectory, { recursive: true });
		await writeJson(join(this.runDirectory, "state.json"), this.stateValue);
	}

	private result(reason?: string): ProofRunResult {
		const candidate = this.stateValue.candidates.find((item) => this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT");
		return {
			runId: this.runIdValue,
			status: this.stateValue.status,
			mode: this.stateValue.mode,
			steps: this.stateValue.step,
			...(candidate === undefined ? {} : { candidateId: candidate.candidateId }),
			...(this.proofPathValue === undefined ? {} : { proofPath: this.proofPathValue }),
			...(this.stateValue.proofLeanPath === undefined ? {} : { proofLeanPath: this.stateValue.proofLeanPath }),
			...(reason === undefined ? {} : { reason }),
		};
	}

	private restoreFromSession(): void {
		const entries = this.session.customEntries("proof");
		const selected = entries.map(parseStoredEvent).filter((event): event is ProofEvent => event !== undefined && event.runId === this.runIdValue);
		this.eventList = selected;
		for (const event of selected) {
			switch (event.type) {
				case "proof/status_changed":
					this.stateValue = { ...this.stateValue, status: event.status, ...(event.reason === undefined ? {} : { lastError: event.reason }) };
					break;
				case "proof/step_started":
				case "proof/step_finished":
					this.stateValue = { ...this.stateValue, step: Math.max(this.stateValue.step, event.step) };
					break;
				case "proof/whiteboard_updated":
					this.stateValue = { ...this.stateValue, whiteboard: event.content };
					break;
				case "proof/task_dispatched":
					if (!this.stateValue.tasks.some((task) => task.taskId === event.task.taskId)) this.stateValue = { ...this.stateValue, tasks: [...this.stateValue.tasks, event.task] };
					break;
				case "proof/research_result":
					this.restoreCandidateFromResearch(event.taskId, event.result);
					break;
				case "proof/verification_result":
					this.stateValue = { ...this.stateValue, verifications: { ...this.stateValue.verifications, [event.candidateId]: event.result } };
					break;
				case "proof/candidate_ready":
					if (!this.stateValue.candidates.some((candidate) => candidate.candidateId === event.candidate.candidateId)) this.stateValue = { ...this.stateValue, candidates: [...this.stateValue.candidates, event.candidate] };
					break;
				case "proof/route_failed":
					if (!this.stateValue.failedRoutes.some((failure) => failure.routeFingerprint === event.failure.routeFingerprint && failure.reason === event.failure.reason)) this.stateValue = { ...this.stateValue, failedRoutes: [...this.stateValue.failedRoutes, event.failure] };
					break;
				case "proof/submitted":
					this.stateValue = { ...this.stateValue, submittedCandidateId: event.candidateId, status: this.stateValue.mode === "prove" ? "PROVED" : this.stateValue.status };
					this.proofPathValue = event.proofPath;
					break;
				case "proof/formal_verification_result":
					if (event.result.ok) this.stateValue = { ...this.stateValue, proofLeanPath: join(this.runDirectory, "PROOF.lean") };
					break;
				case "proof/obligation_created":
				case "proof/repository_updated":
				case "proof/tool_result":
				case "proof/planner_output":
					break;
			}
		}
	}

	private restoreCandidateFromResearch(taskId: string, result: ResearchResult): void {
		if (result.kind !== "candidate") return;
		const task = this.stateValue.tasks.find((item) => item.taskId === taskId);
		if (task === undefined) return;
		const candidate = this.materializeCandidate(task, result);
		if (!this.stateValue.candidates.some((item) => item.candidateId === candidate.candidateId)) this.stateValue = { ...this.stateValue, candidates: [...this.stateValue.candidates, candidate] };
	}
}

/** Named entry point for callers that want the orchestration concept explicitly. */
export class ProofWorkflow extends ProofRuntime {}

async function mapConcurrent<T, R>(items: readonly T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
	const results: R[] = [];
	let cursor = 0;
	const worker = async (): Promise<void> => {
		while (true) {
			const index = cursor;
			cursor += 1;
			if (index >= items.length) return;
			results[index] = await fn(items[index] as T);
		}
	};
	await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
	return results;
}

function createBudget(options: ProofBudgetOptions | undefined): ProofBudgetState {
	return {
		workerCalls: 0,
		verifierCalls: 0,
		literatureSearches: 0,
		toolCalls: 0,
		startedAt: Date.now(),
		...(options?.maxWorkerCalls === undefined ? {} : { maxWorkerCalls: options.maxWorkerCalls }),
		...(options?.maxVerifierCalls === undefined ? {} : { maxVerifierCalls: options.maxVerifierCalls }),
		...(options?.maxLiteratureSearches === undefined ? {} : { maxLiteratureSearches: options.maxLiteratureSearches }),
		...(options?.maxToolCalls === undefined ? {} : { maxToolCalls: options.maxToolCalls }),
		...(options?.maxWallTimeMs === undefined ? {} : { maxWallTimeMs: options.maxWallTimeMs }),
	};
}

function selectRunId(session: Session, obligation: ProofObligation, requested: string | undefined): string {
	if (requested !== undefined) return requested;
	const last = [...session.customEntries("proof")].reverse().find((entry) => {
		const payload = entry.payload;
		return typeof payload.runId === "string" && payload.type === "proof/obligation_created" && isRecord(payload.obligation) && payload.obligation.theorem === obligation.theorem;
	});
	return typeof last?.payload.runId === "string" ? last.payload.runId : randomUUID();
}

function parseStoredEvent(entry: SessionCustomEntry): ProofEvent | undefined {
	if (entry.namespace !== "proof" || typeof entry.payload.type !== "string" || typeof entry.payload.runId !== "string") return undefined;
	return entry.payload as unknown as ProofEvent;
}

function snapshotState(state: ProofState): ProofState {
	return {
		...state,
		obligation: { ...state.obligation },
		tasks: [...state.tasks],
		candidates: [...state.candidates],
		verifications: { ...state.verifications },
		failedRoutes: [...state.failedRoutes],
		rejectedCandidates: [...state.rejectedCandidates],
		recentOutputs: [...state.recentOutputs],
		stepHistory: [...state.stepHistory],
		budget: { ...state.budget },
	};
}

function fingerprint(value: string): string {
	return createHash("sha256").update(normalize(value)).digest("hex");
}

function normalize(value: string): string {
	return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function formatTheorem(obligation: ProofObligation): string {
	return [obligation.theorem, obligation.context ?? ""].filter((part) => part.length > 0).join("\n\n");
}

function initialWhiteboard(obligation: ProofObligation, mode: ProofMode): string {
	return [
		"# Goal",
	obligation.theorem,
	"",
	"# Plan",
	"- [ ] Delegate focused proof attempts to workers.",
	"- [ ] Independently verify a complete candidate.",
	mode === "prove" ? "- [ ] Submit PROOF.md." : mode === "formalize_only" ? "- [ ] Submit a Lean item and pass the formal gate." : "- [ ] Submit PROOF.md and PROOF.lean.",
	"",
	"# Failed",
	"(none)",
	"",
	"# Backlog",
	"(none)",
	"",
	"# Status",
	"OPEN",
	].join("\n");
}

function slugify(value: string): string {
	return normalize(value).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "item";
}

function stringify(value: unknown): string {
	if (typeof value === "string") return value;
	try {
		return JSON.stringify(value) ?? String(value);
	} catch {
		return String(value);
	}
}

function mergeWorkerVerifierOutput(research: readonly ResearchWork[], verifications: readonly VerificationWork[]): string {
	const byTask = new Map(verifications.map((work) => [work.task.taskId, work]));
	return research.map((work, index) => {
		const workerText = work.result.kind === "candidate"
			? work.result.candidate.content
			: work.result.kind === "observation"
				? work.result.content
				: `BLOCKED: ${work.result.reason}`;
			const verification = byTask.get(work.task.taskId);
		const verifierText = verification === undefined
			? "Verifier: not run"
			: verification.result === undefined
				? `Verifier: ERROR — ${verification.error ?? "unknown error"}`
				: `Verifier: ${verification.result.verdict}\n${verification.result.feedback}`;
		return [`## Worker ${index + 1}: ${work.task.summary}`, workerText, verifierText].join("\n\n");
	}).join("\n\n---\n\n");
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function isProviderFailure(error: unknown): boolean {
	return error instanceof ProofProviderError || (error instanceof Error && error.name === "ProofProviderError");
}

function isMissingFile(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

async function ensureTextFile(path: string, content: string): Promise<void> {
	try {
		await readFile(path, "utf8");
	} catch (error) {
		if (!isMissingFile(error)) throw error;
		await writeFile(path, content, "utf8");
	}
}

async function writeJson(path: string, value: unknown): Promise<void> {
	await mkdir(join(path, ".."), { recursive: true });
	await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
