import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { JsonObject } from "../models/json.js";
import type { Session, SessionCustomEntry } from "../session/index.js";
import { ProofProviderError, ProofProtocolError } from "./agent-role.js";
import { ProofRepository } from "./repository.js";
import { withProofToolScope } from "./tool-scope.js";
import type {
	FormalVerificationAttempt,
	FormalVerificationResult,
	ProofAction,
	ProofAgentFactory,
	ProofArtifactStatus,
	ProofBudgetOptions,
	ProofBudgetState,
	ProofCandidate,
	ProofDecomposition,
	ProofDecompositionUnit,
	ProofEvent,
	ProofExecutionPlan,
	ProofPlanActionExecution,
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
	ProofTaskStatus,
	ProofTool,
	ProofVerifier,
	ProofVerifierContext,
	ProofWorkflowMode,
	ResearchResult,
	VerificationResult,
} from "./types.js";

export interface ProofRuntimeOptions {
	readonly session: Session;
	readonly obligation: ProofObligation;
	readonly planner: ProofPlanner;
	readonly researcher: ProofResearcher;
	readonly verifier: ProofVerifier;
	/** Optional factory for logical agents selected by a dynamic plan. */
	readonly agentFactory?: ProofAgentFactory;
	readonly workflowMode?: ProofWorkflowMode;
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
	/** Enables the explicit research-target submission gate. Ordinary proof API runs omit this. */
	readonly targetGate?: { readonly targetObligationId: string; readonly targetClaimId: string };
	/** Returns automatically instrumented evidence for one logical tactical task. */
	readonly evidenceProvider?: (role: "worker" | "verifier", taskId: string, classification?: "BODY_READ" | "DISCOVERED") => Promise<readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[]>;
	readonly evidenceResolver?: (refs: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[]) => Promise<string>;
	readonly tacticalDirective?: Readonly<Record<string, unknown>>;
	readonly planDependencies?: readonly { readonly artifactId: string; readonly contentHash: string }[];
	readonly planDependencyValidator?: (plan: ProofExecutionPlan) => Promise<string | undefined>;
	/** Deterministic fault injection used to verify task-granular resume. */
	readonly faultAfterWorkerResults?: number;
	/** Deterministic crash window after a durable per-action COMPLETED receipt. */
	readonly faultAfterCompletedActionReceipts?: number;
}

type ResearchWork = {
	readonly task: ProofTask;
	readonly result: ResearchResult;
	readonly providerBlocked: boolean;
	readonly persisted: boolean;
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
	private readonly agentFactory?: ProofAgentFactory;
	private readonly workflowMode: ProofWorkflowMode;
	private readonly autoVerify: boolean;
	private readonly maxWorkers: number;
	private readonly maxSteps: number;
	private readonly historyLimit: number;
	private readonly obligation: ProofObligation;
	private readonly decomposition: ProofDecomposition;
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
	private readonly requestedWorkflowMode?: ProofWorkflowMode;
	private readonly targetGate?: { readonly targetObligationId: string; readonly targetClaimId: string };
	private readonly evidenceProvider?: ProofRuntimeOptions["evidenceProvider"];
	private readonly evidenceResolver?: ProofRuntimeOptions["evidenceResolver"];
	private readonly tacticalDirective?: Readonly<Record<string, unknown>>;
	private readonly planDependencies: readonly { readonly artifactId: string; readonly contentHash: string }[];
	private readonly planDependencyValidator?: ProofRuntimeOptions["planDependencyValidator"];
	private readonly faultAfterWorkerResults?: number;
	private readonly faultAfterCompletedActionReceipts?: number;
	private persistedWorkerResults = 0;
	private persistedActionReceipts = 0;
	private taskStatusQueue: Promise<void> = Promise.resolve();
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
		this.agentFactory = options.agentFactory;
		this.workflowMode = options.workflowMode ?? "dynamic";
		this.autoVerify = options.autoVerify ?? true;
		this.maxWorkers = Math.max(1, options.maxWorkers ?? 3);
		this.maxSteps = Math.max(1, options.maxSteps ?? 32);
		this.historyLimit = Math.max(1, options.historyLimit ?? 3);
		this.obligation = options.obligation;
		this.decomposition = analyzeProofDecomposition(this.obligation);
		this.requestedMode = options.mode;
		this.requestedWorkflowMode = options.workflowMode;
		this.targetGate = options.targetGate;
		this.evidenceProvider = options.evidenceProvider;
		this.evidenceResolver = options.evidenceResolver;
		this.tacticalDirective = options.tacticalDirective;
		this.planDependencies = options.planDependencies ?? [];
		this.planDependencyValidator = options.planDependencyValidator;
		this.faultAfterWorkerResults = options.faultAfterWorkerResults;
		this.faultAfterCompletedActionReceipts = options.faultAfterCompletedActionReceipts;
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
			workflowMode: this.workflowMode,
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
			executionPlans: [],
			formalAttempts: [],
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
				if (this.stateValue.mode !== "prove" && this.formalVerifier === undefined) {
					await this.changeStatus("BLOCKED_FORMAL", "Formal verification is required by this run but no Lean process verifier is configured.");
					return this.result(this.stateValue.lastError);
				}

			const unfinishedPlan = [...this.stateValue.executionPlans].reverse().find((item) => item.status === "RUNNING");
			const firstStep = unfinishedPlan?.step ?? this.stateValue.step + 1;
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
				let persisted = [...this.stateValue.executionPlans].reverse().find((item) => item.step === step && item.status === "RUNNING");
				if (persisted !== undefined) {
					const invalidation = await this.planDependencyValidator?.(persisted);
					if (invalidation === undefined) plan = persisted.plan;
					else { await this.markPlanStale(persisted, invalidation); persisted = undefined; plan = { actions: [] }; }
				} else plan = { actions: [] };
				if (persisted === undefined) try {
						if (!this.consumeBudget("plannerCalls")) { await this.changeStatus("PARTIAL", "Planner or wall-time budget exhausted"); await this.finishStep(step, stepDirectory, "interrupted", "Planner or wall-time budget exhausted"); return this.result("Planner or wall-time budget exhausted"); }
						plan = await this.planner.plan(context, signal);
						plan = this.ensureDynamicDecomposition(plan, context.decomposition);
						plan = this.ensureDynamicContinuations(plan);
					validatePlanTaskIdentities(plan);
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

				if (persisted === undefined) persisted = await this.writePlannerArtifacts(stepDirectory, plan);
				else await writeJson(join(stepDirectory, "planner_plan.json"), plan);
				await this.executePlan(persisted, signal, stepDirectory);
				await this.finishStep(step, stepDirectory, "completed", plan.summary ?? `Proof workflow step ${step} completed`);

					if (["PROVED", "CANCELLED", "BLOCKED_PROVIDER", "BLOCKED_FORMAL"].includes(this.stateValue.status)) return this.result();
				if (plan.actions.some((action) => action.action === "stop") && !this.formalWorkRequired()) return this.result(this.stateValue.lastError);
			}

			if (this.stateValue.status === "RUNNING") {
					const hasVerifiedCandidate = this.hasVerifiedCandidate();
					const formalPending = this.stateValue.mode !== "prove" && this.stateValue.proofLeanPath === undefined;
					const hasFailedWork = this.stateValue.failedRoutes.length > 0 || this.stateValue.stepHistory.some((record) => record.status === "failed");
					await this.changeStatus(
						formalPending ? "PARTIAL" : hasVerifiedCandidate ? "CANDIDATE_READY" : hasFailedWork ? "FAILED" : "PARTIAL",
						formalPending ? "The configured runtime limit was reached before Lean verification passed" : hasVerifiedCandidate ? "A verified candidate is ready for submission" : hasFailedWork ? "No proof route passed independent verification" : "No verified candidate was produced",
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
			workflowMode: this.stateValue.workflowMode,
			autoVerify: this.autoVerify,
				formalVerification: this.formalVerifier !== undefined,
				formalTargetConfigured: this.leanTheorem !== undefined,
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
					workflowMode: this.requestedWorkflowMode ?? (isWorkflowMode(saved.workflowMode) ? saved.workflowMode : undefined) ?? this.stateValue.workflowMode,
				tasks: (saved.tasks ?? []).map(normalizePersistedTask),
				budget: { ...this.stateValue.budget, ...(saved.budget ?? {}), plannerCalls: saved.budget?.plannerCalls ?? 0 },
					executionPlans: (saved.executionPlans ?? []).map(normalizeExecutionPlan),
					formalAttempts: saved.formalAttempts ?? [],
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
			executionPlans: this.stateValue.executionPlans.map((plan) => plan.step === step && plan.status === "RUNNING" && status === "completed" ? { ...plan, status: "COMPLETED", completedAt: new Date().toISOString() } : plan),
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

	private async writePlannerArtifacts(directory: string, plan: ProofPlan): Promise<ProofExecutionPlan> {
		const trace = this.planner as ProofPlannerWithTrace;
		if (trace.lastTrace?.prompt !== undefined) await writeFile(join(directory, "planner_prompt.md"), trace.lastTrace.prompt, "utf8");
		if (trace.lastTrace?.response !== undefined) await writeFile(join(directory, "planner_response.txt"), trace.lastTrace.response, "utf8");
		await writeJson(join(directory, "planner_plan.json"), plan);
		const taskIds = plan.actions.flatMap((action) => action.action === "spawn" ? action.tasks.map((task) => task.taskId).filter((item): item is string => item !== undefined) : []);
		const planId = fingerprint(`${this.runIdValue}\n${this.stateValue.step}\n${JSON.stringify(plan)}\n${this.stateValue.executionPlans.length}`), executionPlan: ProofExecutionPlan = { planId, step: this.stateValue.step, inputHash: fingerprint(JSON.stringify({ obligation: this.obligation, directive: this.tacticalDirective, dependencies: this.planDependencies })), plan, taskIds, dependencyRefs: this.planDependencies, actionExecutions: plan.actions.map((action, ordinal) => actionExecution(planId, action, ordinal)), status: "RUNNING", createdAt: new Date().toISOString() };
		this.stateValue = {
			...this.stateValue,
			executionPlans: [...this.stateValue.executionPlans, executionPlan],
			stepHistory: this.stateValue.stepHistory.map((record) => record.step === this.stateValue.step ? {
				...record,
				status: "started",
				action: plan.actions.at(-1)?.action,
				plannerResponse: trace.lastTrace?.response,
			} : record),
		};
		await this.persistState();
		return executionPlan;
	}

	/**
	 * Do not let a composite first obligation be admitted as one broad worker
	 * task. The controller still chooses the route and receives the full
	 * problem-derived unit list; this guard only supplies the missing fan-out
	 * when the model collapses that list into a single task.
	 */
	private ensureDynamicDecomposition(plan: ProofPlan, decomposition: ProofDecomposition | undefined): ProofPlan {
		if (this.workflowMode !== "dynamic" || decomposition?.complexity !== "COMPOSITE" || this.stateValue.tasks.length > 0) return plan;

		const spawnIndex = plan.actions.findIndex((action) => action.action === "spawn");
		const spawnedTasks = plan.actions.flatMap((action) => action.action === "spawn" ? [...action.tasks] : []);
		const seed = spawnedTasks.length === 1 ? spawnedTasks[0] : undefined;
		if (spawnedTasks.length > 1 || seed?.kind === "FORMALIZATION" || seed?.continuationOf !== undefined) return plan;

		const baseId = seed?.taskId ?? this.runIdValue + ":decomposition";
		const occupiedIds = new Set(plan.actions.flatMap((action) => action.action === "spawn" ? action.tasks.map((task) => task.taskId).filter((taskId): taskId is string => taskId !== undefined) : []));
		const baseDependencies = [...new Set(seed?.dependsOn ?? [])];
		const baseRoute = seed?.routeKey ?? seed?.description ?? decomposition.units.map((unit) => unit.unitId).join(",");
		const baseAgentId = seed?.agent?.agentId ?? baseId + ":controller";
		const supportTasks = decomposition.units.map((unit, index): ProofTaskInput => {
			const taskId = uniquePlanTaskId(baseId + ":unit-" + (index + 1), occupiedIds);
			return {
				taskId,
				summary: "Focused proof work: " + unit.label,
				description: [
					"Solve only decomposition unit " + unit.unitId + ": " + unit.label + ".",
					unit.description,
					"Return a rigorous, independently checkable result for this unit. Do not claim that a local result closes the full obligation.",
				].join("\n\n"),
				routeKey: baseRoute + "\nunit=" + unit.unitId,
				scope: "CONTRIBUTION",
				dependsOn: baseDependencies,
				agent: {
					agentId: baseAgentId + ":unit-" + (index + 1),
					purpose: "Independently solve " + unit.label,
					capabilities: ["focused-proof", "local-gap-detection"],
					role: "worker",
				},
				successCriteria: "The " + unit.label + " sub-obligation is fully discharged, with every non-trivial inference stated and no unresolved dependency hidden behind a slogan.",
				kind: "MATHEMATICAL",
			};
		});
		const supportIds = supportTasks.map((task) => task.taskId as string);
		const finalTaskId = seed?.taskId ?? uniquePlanTaskId(baseId + ":synthesis", occupiedIds);
		const targetClaimId = this.targetGate?.targetClaimId ?? seed?.targetClaimId;
		const finalTask: ProofTaskInput = {
			taskId: finalTaskId,
			summary: seed === undefined ? "Synthesize the complete proof from all decomposition units" : "Synthesize complete proof: " + seed.summary,
			description: [
				"Synthesize and close the complete original proof obligation from the independently solved decomposition units.",
				"Required units: " + decomposition.units.map((unit) => unit.unitId + " (" + unit.label + ")").join(", ") + ".",
				seed === undefined ? "The original controller did not provide a focused synthesis task; construct the final proof from the theorem and the verified dependency outputs." : "Preserved controller route intent:\n" + seed.description,
				"Use the exact runtime dependency outputs, repair any gaps they expose, and return one complete self-contained proof for the original theorem.",
			].join("\n\n"),
			routeKey: baseRoute + "\nsynthesis=" + decomposition.units.map((unit) => unit.unitId).join(","),
			scope: "TARGET",
			...(targetClaimId === undefined ? {} : { targetClaimId }),
			dependsOn: [...new Set([...baseDependencies, ...supportIds])],
			agent: seed?.agent ?? {
				agentId: baseAgentId + ":synthesis",
				purpose: "Integrate independently solved units into the complete target proof",
				capabilities: ["proof-synthesis", "dependency-integration"],
				role: "worker",
			},
			successCriteria: "A complete, self-contained proof of the original obligation covers all " + decomposition.unitCount + " identified units and can be independently verified.",
			kind: "MATHEMATICAL",
		};
		const decompositionActions: ProofAction[] = [
			{ action: "spawn", tasks: supportTasks, summary: "Parallel focused work across " + decomposition.unitCount + " problem-derived units" },
			{ action: "spawn", tasks: [finalTask], summary: "Synthesize and close the complete original obligation" },
		];
		const actions = [...plan.actions];
		if (spawnIndex >= 0) actions.splice(spawnIndex, 1, ...decompositionActions);
		else actions.unshift(...decompositionActions);
		const hadStop = actions.some((action) => action.action === "stop");
		const workflow: ProofPlan["workflow"] = {
			strategy: plan.workflow?.strategy ?? "Problem-derived parallel decomposition followed by verified synthesis",
			rationale: [
				plan.workflow?.rationale,
				"The runtime identified " + decomposition.unitCount + " independently addressable units and expanded the single-task plan into focused workers plus a synthesis task." + (hadStop ? " The initial stop was deferred until this required work completes." : ""),
			].filter((part): part is string => part !== undefined && part.length > 0).join("\n\n"),
			successCriteria: plan.workflow?.successCriteria ?? [finalTask.successCriteria as string],
		};
		this.addOutput(
			"planner",
			"Controller plan expanded into parallel decomposition",
			decomposition.unitCount + " problem-derived units were assigned to independent logical agents, followed by " + finalTask.taskId + " as the synthesis task." + (hadStop ? " The planner stop was deferred." : ""),
		);
		return {
			...plan,
			actions: hadStop ? actions.filter((action) => action.action !== "stop") : actions,
			summary: plan.summary ?? "Dynamic controller plan expanded into focused parallel work and final synthesis",
			workflow,
		};
	}

	/**
	 * Keep the controller model-driven while making an incomplete worker result
	 * impossible to lose between rounds. The planner still owns task boundaries,
	 * descriptions, dependencies, and agent selection. This is only a durable
	 * safety net for a planner response that forgets to continue an unresolved
	 * task after a provider truncation or retryable worker failure.
	 */
	private ensureDynamicContinuations(plan: ProofPlan): ProofPlan {
		if (this.workflowMode !== "dynamic") return plan;

		const plannedTaskIds = new Set(plan.actions.flatMap((action) => action.action === "spawn"
			? action.tasks.map((task) => task.taskId).filter((taskId): taskId is string => taskId !== undefined)
			: []));
		const plannedContinuations = new Set(plan.actions.flatMap((action) => action.action === "spawn"
			? action.tasks.map((task) => task.continuationOf).filter((taskId): taskId is string => taskId !== undefined)
			: []));
		const incomplete = this.stateValue.tasks.filter((task) =>
			(task.status === "PARTIAL" || task.status === "FAILED_RETRYABLE")
			&& !this.stateValue.tasks.some((child) => child.continuationOf === task.taskId),
		);
		const continuations = incomplete
			.filter((task) => !plannedContinuations.has(task.taskId))
			.map((task) => createContinuationTask(task, this.eventList, this.stateValue.tasks, plannedTaskIds));
		const formalReady = this.formalWorkRequired();
		const planHandlesFormalization = plan.actions.some((action) => action.action === "submit_lean_proof"
			|| (action.action === "spawn" && action.tasks.some((task) => task.kind === "FORMALIZATION")));
		const formalizationAlreadyOpen = this.stateValue.tasks.some((task) => task.kind === "FORMALIZATION"
			&& (task.status === "PENDING" || task.status === "RUNNING"));
		const formalContinuationScheduled = continuations.some((task) => task.kind === "FORMALIZATION");
		const formalization = formalReady && !planHandlesFormalization && !formalizationAlreadyOpen && !formalContinuationScheduled
			? [createFormalizationTask(this.stateValue, plannedTaskIds, this.leanTheorem !== undefined)]
			: [];
		const requiredTasks = [...continuations, ...formalization];
		if (requiredTasks.length === 0) return plan;

		const continuationAction: ProofAction = {
			action: "spawn",
			tasks: requiredTasks,
			summary: formalization.length > 0 ? "Continue required formal verification" : "Continue incomplete worker tasks",
		};
		const actions = [...plan.actions];
		const spawnIndex = actions.findIndex((action) => action.action === "spawn");
		if (spawnIndex >= 0) {
			const spawn = actions[spawnIndex];
			if (spawn?.action === "spawn") actions[spawnIndex] = { ...spawn, tasks: [...requiredTasks, ...spawn.tasks] };
		} else {
			actions.unshift(continuationAction);
		}

		// A model-issued stop is a valid terminal choice only after unresolved
		// worker output has either been continued or deliberately represented by a
		// different route. Do not let a stale stop strand a provider partial.
		const hadStop = actions.some((action) => action.action === "stop");
		const normalizedActions = hadStop ? actions.filter((action) => action.action !== "stop") : actions;
		this.addOutput(
			"planner",
			"Runtime scheduled required work",
			`${requiredTasks.map((task) => task.kind === "FORMALIZATION" ? `${task.taskId} [Lean process gate]` : `${task.taskId} ← ${task.continuationOf}`).join(", ")} must complete before the run may stop.${hadStop ? " The planner stop was deferred." : ""}`,
		);
		return { ...plan, actions: normalizedActions };
	}

	/** A planner STOP cannot terminate a run while its mandatory Lean gate is open. */
	private formalWorkRequired(): boolean {
		const formalizableCandidate = this.decomposition.complexity === "COMPOSITE"
			? this.hasVerifiedTargetCandidate()
			: this.hasVerifiedCandidate();
		return this.stateValue.mode !== "prove"
			&& this.stateValue.proofLeanPath === undefined
			&& (this.stateValue.mode === "formalize_only" || this.proofPathValue !== undefined || formalizableCandidate);
	}

	private async acceptedInformalProofMaterial(): Promise<string> {
		if (this.proofPathValue !== undefined) return `# ACCEPTED INFORMAL PROOF\n\n${await readFile(this.proofPathValue, "utf8")}`;
		const candidate = this.stateValue.candidates.find((item) => item.scope === "TARGET" && this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT")
			?? this.stateValue.candidates.find((item) => this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT");
		return candidate === undefined ? "" : `# VERIFIED INFORMAL CANDIDATE\n\n${candidate.content}`;
	}

	private async markPlanStale(plan: ProofExecutionPlan, reason: string): Promise<void> {
		this.stateValue = { ...this.stateValue, executionPlans: this.stateValue.executionPlans.map((item) => item.planId === plan.planId ? { ...item, status: "STALE", staleReason: reason, actionExecutions: item.actionExecutions.map((action) => action.status === "COMPLETED" ? action : { ...action, status: "STALE", error: reason }) } : item) };
		await this.persistState();
		this.addOutput("planner", "Persisted plan invalidated", reason);
	}

	private async writePlannerFailure(directory: string, error: unknown): Promise<void> {
		const trace = this.planner as ProofPlannerWithTrace;
		if (trace.lastTrace?.prompt !== undefined) await writeFile(join(directory, "planner_prompt.md"), trace.lastTrace.prompt, "utf8");
		if (trace.lastTrace?.response !== undefined) await writeFile(join(directory, "planner_response.txt"), trace.lastTrace.response, "utf8");
		await writeJson(join(directory, "planner_error.json"), { error: errorMessage(error), trace: trace.lastTrace ?? null });
	}

	private async executePlan(executionPlan: ProofExecutionPlan, signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		const plan = executionPlan.plan; await writeJson(join(stepDirectory, "actions.json"), plan.actions);
		let stopDeferred = false;
		for (let ordinal = 0; ordinal < plan.actions.length; ordinal += 1) {
			const action = plan.actions[ordinal] as ProofAction, current = this.actionExecution(executionPlan.planId, ordinal);
			if (current?.status === "COMPLETED") {
				if (action.action === "stop" && !this.formalWorkRequired()) {
					await this.rehydrateTerminalAction(action, current);
					return;
				}
				continue;
			}
			if (signal?.aborted) {
				await this.changeStatus("CANCELLED", "Proof run was cancelled during action execution");
				return;
			}
			if (["PROVED", "CANCELLED", "BLOCKED_PROVIDER", "BLOCKED_FORMAL"].includes(this.stateValue.status)) return;
			this.setCurrentAction(action); const startedAt = new Date().toISOString(), before = actionResultCursor(this.stateValue, this.eventList);
			await this.updateActionExecution(executionPlan.planId, ordinal, (item) => ({ ...item, status: "RUNNING", startedAt: item.startedAt ?? startedAt, error: undefined }));
			try { switch (action.action) {
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
				case "submit_target_proof":
					await this.submitTargetProof(action, stepDirectory);
					break;
				case "submit_lean_proof":
					await this.submitLeanProof(action, signal, stepDirectory);
					break;
				case "stop":
					if (this.formalWorkRequired()) {
						stopDeferred = true;
						this.addOutput("stop", "Stop deferred", "Formal verification is still required; the runtime will schedule the Lean formalizer continuation.");
					} else {
						await this.changeStatus("PARTIAL", action.reason ?? "Planner stopped the run");
					}
					break;
			} } catch (error) { await this.updateActionExecution(executionPlan.planId, ordinal, (item) => ({ ...item, status: "INTERRUPTED", error: errorMessage(error) })); throw error; }
			if (signal?.aborted || this.stateValue.status === "CANCELLED") { await this.updateActionExecution(executionPlan.planId, ordinal, (item) => ({ ...item, status: "INTERRUPTED", error: "Action interrupted by cancellation" })); return; }
			const completedAt = new Date().toISOString(), result = collectActionResult(action, before, this.stateValue, this.eventList);
			await this.updateActionExecution(executionPlan.planId, ordinal, (item) => ({ ...item, status: "COMPLETED", completedAt, resultArtifactIds: result.resultArtifactIds, effectIds: result.effectIds, result: result.result }));
			this.persistedActionReceipts += 1; if (this.faultAfterCompletedActionReceipts === this.persistedActionReceipts) throw new InjectedProofTaskFault(`Injected fault after completed action receipt ${this.persistedActionReceipts}`);
			if (action.action === "stop" && !stopDeferred) return;
		}
	}

	private actionExecution(planId: string, ordinal: number): ProofPlanActionExecution | undefined { return this.stateValue.executionPlans.find((plan) => plan.planId === planId)?.actionExecutions.find((action) => action.ordinal === ordinal); }
	private async updateActionExecution(planId: string, ordinal: number, update: (action: ProofPlanActionExecution) => ProofPlanActionExecution): Promise<void> { this.stateValue = { ...this.stateValue, executionPlans: this.stateValue.executionPlans.map((plan) => plan.planId === planId ? { ...plan, actionExecutions: plan.actionExecutions.map((action) => action.ordinal === ordinal ? update(action) : action) } : plan) }; await this.persistState(); }
	private async rehydrateTerminalAction(action: Extract<ProofAction, { readonly action: "stop" }>, execution: ProofPlanActionExecution): Promise<void> { const status = execution.result?.terminalStatus, terminalStatus: ProofStatus = status === "PARTIAL" || status === "FAILED" || status === "CANCELLED" || status === "BLOCKED_PROVIDER" || status === "BLOCKED_FORMAL" ? status : "PARTIAL", reason = typeof execution.result?.terminalReason === "string" ? execution.result.terminalReason : action.reason ?? "Planner stopped the run"; this.stateValue = { ...this.stateValue, status: terminalStatus, lastError: reason }; await this.persistState(); }

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
		const materialized = taskInputs.map((input, index) => this.materializeTask(input, index));
		if (new Set(materialized.map((task) => task.taskId)).size !== materialized.length) throw new ProofProtocolError("Planner task ids must be unique inside a persisted plan");
		validateTaskDependencies(materialized, this.stateValue.tasks);
		const tasks = materialized.map((task) => {
			const existing = this.stateValue.tasks.find((item) => item.taskId === task.taskId);
			if (existing === undefined) return task;
			if (existing.routeFingerprint !== task.routeFingerprint || JSON.stringify(existing.dependsOn) !== JSON.stringify(task.dependsOn)) throw new ProofProtocolError(`Task ${task.taskId} is immutable once admitted; create a new task id for a changed route or dependency graph`);
			return existing;
		});
		this.stateValue = { ...this.stateValue, tasks: [...this.stateValue.tasks, ...tasks.filter((task) => !this.stateValue.tasks.some((existing) => existing.taskId === task.taskId))] };
		await this.persistState();
		const accepted: ProofTask[] = [];
		const seenRoutes = new Set<string>();
		for (const task of tasks) {
			if (seenRoutes.has(task.routeFingerprint) || this.stateValue.failedRoutes.some((failure) => failure.routeFingerprint === task.routeFingerprint)) {
				await this.recordFailure({
					routeFingerprint: task.routeFingerprint,
					taskId: task.taskId,
						reason: "This proof route was already rejected and is blocked from retry.",
						step: this.stateValue.step,
					});
				await this.updateTaskStatus(task.taskId, "FAILED_TERMINAL", "This proof route was already rejected and is blocked from retry.");
				this.addOutput("spawn", task.summary, "Rejected duplicate failed route.");
				continue;
			}
			const completed = this.persistedResearchResult(task.taskId);
			if (completed !== undefined) {
				if (task.status === "RUNNING" || task.status === "PENDING") await this.updateTaskStatus(task.taskId, taskStatusForResearchResult(completed, false));
				// A durable worker result is not only a signal to skip the provider
				// call. It must re-enter the merge/verification pipeline as well;
				// otherwise a crash after the result receipt but before candidate
				// materialization would permanently strand that candidate.
				seenRoutes.add(task.routeFingerprint);
				accepted.push(this.stateValue.tasks.find((item) => item.taskId === task.taskId) ?? task);
				continue;
			}
			const dependencyReason = taskDependencyReason(task, this.stateValue.tasks);
			if (dependencyReason !== undefined) {
				if (dependencyReason.terminal) await this.updateTaskStatus(task.taskId, "BLOCKED", dependencyReason.reason);
				this.addOutput("spawn", task.summary, `Task deferred: ${dependencyReason.reason}`);
				continue;
			}
			if (!this.consumeBudget("workerCalls")) {
				this.addOutput("spawn", task.summary, "Worker budget exhausted; task was not dispatched.");
				continue;
			}
			seenRoutes.add(task.routeFingerprint);
			await this.emit({
				type: "proof/task_dispatched",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				task,
			});
			await this.updateTaskStatus(task.taskId, "RUNNING");
			accepted.push(this.stateValue.tasks.find((item) => item.taskId === task.taskId) ?? task);
		}
		if (accepted.length === 0) return;

		const researchWorks = await mapConcurrent(accepted, this.maxWorkers, async (task): Promise<ResearchWork> => {
			const persisted = this.persistedResearchResult(task.taskId);
			if (persisted !== undefined) {
				if (task.status === "RUNNING" || task.status === "PENDING") await this.updateTaskStatus(task.taskId, taskStatusForResearchResult(persisted, false));
				return { task, result: persisted, providerBlocked: false, persisted: true };
			}
			await writeFile(join(stepDirectory, `worker_${slugify(task.taskId)}_task.md`), task.description, "utf8");
			if (signal?.aborted) {
				const result: ResearchResult = { kind: "blocked", reason: "Proof run was aborted" };
				await this.emit({ type: "proof/research_result", eventId: stableEventId(this.runIdValue, "research", task.taskId), runId: this.runIdValue, timestamp: Date.now(), step: this.stateValue.step, taskId: task.taskId, result });
				await this.updateTaskStatus(task.taskId, "BLOCKED", result.reason);
				return { task, result, providerBlocked: false, persisted: false };
			}
			try {
				const referencedMaterials = await this.repositoryValue.resolveWikilinks(task.description);
				const continuation = task.continuationOf === undefined ? "" : formatContinuationMaterials(task.continuationOf, this.persistedResearchResult(task.continuationOf));
				const formalizationMaterials = task.kind === "FORMALIZATION"
					? [
						this.leanTheorem === undefined
							? "# NO PRECONFIGURED LEAN TARGET\n\nFormalizer must translate the original mathematical theorem into Lean."
							: "# EXACT CONFIGURED LEAN TARGET\n\n" + this.leanTheorem,
						await this.acceptedInformalProofMaterial(),
					].filter((item) => item.length > 0).join("\n\n")
					: "";
				const context: ProofResearchContext = {
					runId: this.runIdValue,
					step: this.stateValue.step,
					obligation: this.obligation,
					whiteboard: this.stateValue.whiteboard,
					task,
					referencedMaterials: [referencedMaterials, continuation, formalizationMaterials].filter((item) => item.length > 0).join("\n\n"),
				};
				// A dynamic plan may omit agent metadata for a simple task. It still
				// needs an isolated logical worker: the shared fallback AgentCore is
				// not safe to run concurrently and would make the model-generated
				// workflow less reliable than the old serial path.
				const selectedAgent = task.agent ?? { agentId: task.taskId, purpose: task.summary };
				const researcher = this.workflowMode === "dynamic" && this.agentFactory !== undefined
					? await this.agentFactory(selectedAgent, context)
					: task.agent === undefined || this.agentFactory === undefined
						? this.researcher
						: await this.agentFactory(task.agent, context);
				const result = await withProofToolScope({ role: "worker", logicalTaskId: task.taskId }, () => researcher.research(context, signal));
				await writeJson(join(stepDirectory, `worker_${slugify(task.taskId)}_result.json`), result);
				await this.emit({ type: "proof/research_result", eventId: stableEventId(this.runIdValue, "research", task.taskId), runId: this.runIdValue, timestamp: Date.now(), step: this.stateValue.step, taskId: task.taskId, result });
				await this.updateTaskStatus(task.taskId, taskStatusForResearchResult(result, false), result.kind === "partial" ? result.reason : undefined);
				this.persistedWorkerResults += 1; if (this.faultAfterWorkerResults === this.persistedWorkerResults) throw new InjectedProofTaskFault(`Injected fault after worker result ${this.persistedWorkerResults}`);
				return { task, result, providerBlocked: false, persisted: false };
			} catch (error) {
				if (error instanceof InjectedProofTaskFault) throw error;
				const result: ResearchResult = { kind: "blocked", reason: errorMessage(error) };
				await this.emit({ type: "proof/research_result", eventId: stableEventId(this.runIdValue, "research", task.taskId), runId: this.runIdValue, timestamp: Date.now(), step: this.stateValue.step, taskId: task.taskId, result });
				await this.updateTaskStatus(task.taskId, isProviderFailure(error) ? "BLOCKED" : "FAILED_RETRYABLE", result.reason);
				return { task, result, providerBlocked: isProviderFailure(error), persisted: false };
			}
		});

		const candidates: CandidateWork[] = [];
		let providerBlockedResearch = 0;
		for (const work of researchWorks) {
			if (work.persisted) await writeJson(join(stepDirectory, `worker_${slugify(work.task.taskId)}_result.json`), work.result);
			if (work.result.kind === "candidate") {
				await writeFile(join(stepDirectory, `worker_${slugify(work.task.taskId)}_output.md`), work.result.candidate.content, "utf8");
			} else {
				const output = work.result.kind === "observation"
					? work.result.content
					: work.result.kind === "partial"
						? `PARTIAL: ${work.result.reason}\n\n${work.result.content}`
						: work.result.reason;
				await writeFile(join(stepDirectory, `worker_${slugify(work.task.taskId)}_output.md`), output, "utf8");
			}
			if (work.providerBlocked) providerBlockedResearch += 1;
			if (work.result.kind === "candidate") {
				const materializedCandidate = this.materializeCandidate(work.task, work.result);
				const bodyReadEvidence = uniqueEvidence(await this.evidenceProvider?.("worker", work.task.taskId, "BODY_READ") ?? []);
				const discoveredEvidence = uniqueEvidence(await this.evidenceProvider?.("worker", work.task.taskId, "DISCOVERED") ?? []);
				const declaredIds = materializedCandidate.reliedOnArtifactIds.length > 0 ? materializedCandidate.reliedOnArtifactIds : materializedCandidate.declaredEvidence.map((item) => item.artifactId);
				const reliedOnIds = declaredIds.length > 0 ? declaredIds : bodyReadEvidence.map((item) => item.artifactId);
				const bodyReadIds = new Set(bodyReadEvidence.map((item) => item.artifactId));
				const inaccessible = reliedOnIds.filter((artifactId) => !bodyReadIds.has(artifactId));
				if (inaccessible.length > 0) throw new ProofProtocolError(`Worker declared reliance on artifacts it did not read: ${inaccessible.join(", ")}`);
				const reliedOnEvidence = bodyReadEvidence.filter((item) => reliedOnIds.includes(item.artifactId));
				const restored = this.stateValue.candidates.find((item) => item.candidateId === materializedCandidate.candidateId);
				const candidate: ProofCandidate = restored ?? { ...materializedCandidate, evidence: reliedOnEvidence, bodyReadEvidence, discoveredEvidence, reliedOnArtifactIds: reliedOnIds };
				const duplicate = restored === undefined ? this.duplicateCandidateReason(candidate) : undefined;
				if (duplicate !== undefined) {
					if (work.task.kind === "FORMALIZATION") {
						const reason = `${duplicate} Return changed Lean source that addresses the latest process feedback.`;
						await this.updateTaskStatus(work.task.taskId, "FAILED_RETRYABLE", reason);
						this.addOutput("formalization", work.task.summary, reason);
						continue;
					}
					await this.rejectCandidate(candidate, work.task, duplicate);
					continue;
				}
				if (restored === undefined) this.stateValue = { ...this.stateValue, candidates: [...this.stateValue.candidates, candidate] };
				await this.repositoryValue.writeItem({
					slug: `candidates/${candidate.candidateId}`,
					content: candidate.content,
					summary: candidate.strategy,
					...(work.task.kind === "FORMALIZATION" ? { format: "lean" as const } : {}),
				});
				if (work.task.kind === "FORMALIZATION") {
					await this.verifyFormalCandidate(work.task, candidate, signal, stepDirectory);
					continue;
				}
				candidates.push({ task: work.task, candidate });
				continue;
			}
			this.addOutput("spawn", work.task.summary, work.result.kind === "observation"
					? `${work.result.content}${work.result.suggestedNext === undefined ? "" : `\nNext: ${work.result.suggestedNext}`}`
					: work.result.kind === "partial"
						? `PARTIAL: ${work.result.reason}\n${work.result.content}${work.result.suggestedNext === undefined ? "" : `\nNext: ${work.result.suggestedNext}`}`
						: work.result.reason);
		}

		if (!this.autoVerify || candidates.length === 0) {
			this.addOutput("spawn", "Merged Worker results", mergeWorkerVerifierOutput(researchWorks, []));
			if (providerBlockedResearch === accepted.length && accepted.length > 0) await this.changeStatus("BLOCKED_PROVIDER", "All proof workers were blocked by their providers.");
			return;
		}

		const verificationWorks = await mapConcurrent(candidates, this.maxWorkers, async (work): Promise<VerificationWork> => {
			const completed = this.stateValue.verifications[work.candidate.candidateId];
			if (completed !== undefined) return { ...work, providerBlocked: false, result: completed };
			if (!this.consumeBudget("verifierCalls")) {
				return { ...work, providerBlocked: false, result: { verdict: "UNFINISHED", feedback: "Verifier budget exhausted; candidate was not verified." } };
			}
			if (signal?.aborted) return { ...work, providerBlocked: false, error: "Proof run was aborted" };
			try {
				const repositoryMaterials = await this.repositoryValue.resolveWikilinks(work.task.description);
				const evidenceMaterials = await this.evidenceResolver?.(work.candidate.evidence) ?? "";
				const referencedMaterials = [repositoryMaterials, evidenceMaterials].filter(Boolean).join("\n\n");
				const context: ProofVerifierContext = {
					runId: this.runIdValue,
					step: this.stateValue.step,
					obligation: this.obligation,
					task: work.task,
					referencedMaterials,
				};
				const modelResult = await withProofToolScope({ role: "verifier", logicalTaskId: work.task.taskId }, () => this.verifier.verify(work.candidate, context, signal));
				const evidence = await this.evidenceProvider?.("verifier", work.task.taskId, "BODY_READ") ?? [];
				return { ...work, providerBlocked: false, result: { ...modelResult, evidence: uniqueEvidence(evidence) } };
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
			if (this.stateValue.verifications[work.candidate.candidateId] === undefined) await this.recordVerification(work.task, work.candidate, work.result);
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
		if (this.targetGate !== undefined) {
			this.addOutput("submit_proof", "Submission rejected", "Research targets require submit_target_proof with exact obligation and claim identity.");
			return;
		}
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
		if (this.stateValue.candidates.some((item) => item.scope === "TARGET" && this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT") && candidate.scope !== "TARGET") {
			this.addOutput("submit_proof", "Submission rejected", "A decomposition produced an exact-target candidate; supporting contributions cannot be submitted as the final proof.");
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

	private persistedResearchResult(taskId: string): ResearchResult | undefined {
		for (let index = this.eventList.length - 1; index >= 0; index -= 1) {
			const event = this.eventList[index];
			if (event?.type === "proof/research_result" && event.taskId === taskId) return event.result;
		}
		return undefined;
	}

	private async submitTargetProof(action: Extract<ProofAction, { action: "submit_target_proof" }>, stepDirectory: string): Promise<void> {
		const gate = this.targetGate;
		if (gate === undefined || action.targetObligationId !== gate.targetObligationId || action.targetClaimId !== gate.targetClaimId) {
			this.addOutput("submit_target_proof", "Submission rejected", "Target identity does not match the active research obligation gate.");
			return;
		}
		const candidate = this.stateValue.candidates.find((item) => item.candidateId === action.candidateId);
		if (candidate === undefined || candidate.scope !== "TARGET" || candidate.targetClaimId !== gate.targetClaimId) {
			this.addOutput("submit_target_proof", "Submission rejected", "Candidate was not Planner-designated for the exact target claim.");
			return;
		}
		const verification = this.stateValue.verifications[candidate.candidateId];
		if (verification?.verdict !== "CORRECT") {
			this.addOutput("submit_target_proof", "Submission rejected", "The target candidate has no independent CORRECT verification.");
			return;
		}
		if (!this.noveltyGate(candidate)) {
			this.addOutput("submit_target_proof", "Submission rejected", "Candidate novelty gate rejected the target submission.");
			return;
		}
		const proofPath = join(this.runDirectory, "PROOF.md");
		const proof = [`# Proof: ${this.obligation.theorem}`, "", candidate.content, "", `<!-- target_obligation_id: ${gate.targetObligationId} -->`, `<!-- target_claim_id: ${gate.targetClaimId} -->`, `<!-- candidate_id: ${candidate.candidateId} -->`, ""].join("\n");
		await writeFile(proofPath, proof, "utf8");
		await writeFile(join(stepDirectory, "submitted_target_proof.md"), proof, "utf8");
		this.proofPathValue = proofPath;
		this.stateValue = { ...this.stateValue, submittedCandidateId: candidate.candidateId, targetSubmission: { candidateId: candidate.candidateId, targetObligationId: gate.targetObligationId, targetClaimId: gate.targetClaimId, scope: "TARGET" } };
		await this.emit({ type: "proof/submitted", eventId: randomUUID(), runId: this.runIdValue, timestamp: Date.now(), step: this.stateValue.step, candidateId: candidate.candidateId, proofPath });
		await this.checkCompletion("Exact research target submission passed the tactical gate.");
	}

	private async verifyFormalCandidate(task: ProofTask, candidate: ProofCandidate, signal: AbortSignal | undefined, stepDirectory: string): Promise<void> {
		const existing = this.stateValue.formalAttempts.find((attempt) => attempt.candidateId === candidate.candidateId);
		if (existing !== undefined) {
			if (!existing.result.ok) await this.updateTaskStatus(task.taskId, "FAILED_RETRYABLE", existing.result.feedback);
			return;
		}
		if (this.formalVerifier === undefined) {
			await this.updateTaskStatus(task.taskId, "BLOCKED", "No Lean process verifier is configured.");
			await this.changeStatus("BLOCKED_FORMAL", "No Lean process verifier is configured.");
			return;
		}
		const source = normalizeLeanCandidate(candidate.content);
		let result: FormalVerificationResult;
		try {
			result = await this.formalVerifier.verify(source, {
				runId: this.runIdValue,
				step: this.stateValue.step,
				obligation: this.obligation,
				theoremText: this.leanTheorem,
				workDirectory: join(stepDirectory, "lean"),
			}, signal);
		} catch (error) {
			result = { ok: false, feedback: errorMessage(error), failureKind: "UNAVAILABLE" };
		}
		await mkdir(join(stepDirectory, "lean"), { recursive: true });
		await writeFile(join(stepDirectory, "lean", `candidate_${slugify(candidate.candidateId)}.lean`), source, "utf8");
		await writeJson(join(stepDirectory, "lean", `candidate_${slugify(candidate.candidateId)}_result.json`), result);
		await this.recordFormalAttempt({
			sourceId: candidate.candidateId,
			taskId: task.taskId,
			candidateId: candidate.candidateId,
			result,
		});
		if (!result.ok) {
			this.addOutput("formalization", `Lean process gate failed for ${task.taskId}`, result.feedback);
			if (result.failureKind === "UNAVAILABLE") {
				await this.updateTaskStatus(task.taskId, "BLOCKED", result.feedback);
				await this.changeStatus("BLOCKED_FORMAL", result.feedback);
				return;
			}
			if (result.failureKind === "ABORTED" && signal?.aborted) {
				await this.updateTaskStatus(task.taskId, "BLOCKED", result.feedback);
				await this.changeStatus("CANCELLED", result.feedback);
				return;
			}
			await this.updateTaskStatus(task.taskId, "FAILED_RETRYABLE", result.feedback);
			return;
		}
		const proofLeanPath = join(this.runDirectory, "PROOF.lean");
		await writeFile(proofLeanPath, source, "utf8");
		this.stateValue = { ...this.stateValue, proofLeanPath };
		await this.updateTaskStatus(task.taskId, "COMPLETED");
		await this.checkCompletion(`PROOF.lean accepted from ${task.taskId}; Lean process verification passed.`);
	}

	private async recordFormalAttempt(input: Omit<FormalVerificationAttempt, "attempt" | "step" | "timestamp">): Promise<FormalVerificationAttempt> {
		const attempt: FormalVerificationAttempt = {
			...input,
			attempt: this.stateValue.formalAttempts.length + 1,
			step: this.stateValue.step,
			timestamp: Date.now(),
		};
		this.stateValue = { ...this.stateValue, formalAttempts: [...this.stateValue.formalAttempts, attempt] };
		await this.emit({
			type: "proof/formal_verification_result",
			eventId: randomUUID(),
			runId: this.runIdValue,
			timestamp: attempt.timestamp,
			step: attempt.step,
			attempt,
			...(attempt.proofSlug === undefined ? {} : { proofSlug: attempt.proofSlug }),
			...(attempt.taskId === undefined ? {} : { taskId: attempt.taskId }),
			...(attempt.candidateId === undefined ? {} : { candidateId: attempt.candidateId }),
			result: attempt.result,
		});
		return attempt;
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
			await this.changeStatus("BLOCKED_FORMAL", "submit_lean_proof requires a configured formal verifier.");
			return;
		}
		const source = normalizeLeanCandidate(item.content);
		let result: FormalVerificationResult;
		try {
			result = await this.formalVerifier.verify(source, {
				runId: this.runIdValue,
				step: this.stateValue.step,
				obligation: this.obligation,
				theoremText: this.leanTheorem,
				workDirectory: join(stepDirectory, "lean"),
			}, signal);
		} catch (error) {
			result = { ok: false, feedback: errorMessage(error), failureKind: "UNAVAILABLE" };
		}
		await mkdir(join(stepDirectory, "lean"), { recursive: true });
		await writeFile(join(stepDirectory, "lean", "proof_attempt.lean"), source, "utf8");
		await writeJson(join(stepDirectory, "lean", "proof_result.json"), result);
		await this.recordFormalAttempt({ sourceId: proofSlug, proofSlug, result });
		if (!result.ok) {
			this.addOutput("submit_lean_proof", `Formal verification failed for [[${proofSlug}]]`, result.feedback);
			if (result.failureKind === "UNAVAILABLE") await this.changeStatus("BLOCKED_FORMAL", result.feedback);
			return;
		}
		const proofLeanPath = join(this.runDirectory, "PROOF.lean");
		await writeFile(proofLeanPath, source, "utf8");
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
			workflowMode: this.stateValue.workflowMode,
			status: this.stateValue.status,
			whiteboard: this.stateValue.whiteboard,
			repository: items.map((item) => ({ ...item, content: "" })),
			repositoryIndex: await this.repositoryValue.formatIndex(),
			candidates: [...this.stateValue.candidates],
			tasks: [...this.stateValue.tasks],
				failedRoutes: [...this.stateValue.failedRoutes],
				recentOutputs: [...this.stateValue.recentOutputs],
				stepHistory: [...this.stateValue.stepHistory],
				decomposition: this.decomposition,
				budget: this.stateValue.budget,
				artifacts: this.artifactStatus(),
				formalAttempts: [...this.stateValue.formalAttempts],
				...(this.tacticalDirective === undefined ? {} : { tacticalDirective: this.tacticalDirective }),
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

	private materializeTask(input: ProofTaskInput, index = 0): ProofTask {
		const taskId = input.taskId ?? `${this.runIdValue}-task-${this.stateValue.tasks.length + index + 1}`;
		if (this.targetGate !== undefined && input.scope === "TARGET" && input.targetClaimId !== this.targetGate.targetClaimId) throw new ProofProtocolError("Planner TARGET task must carry the exact runtime target claim id");
		const routeKey = input.routeKey ?? input.description;
		const dependsOn = [...new Set(input.dependsOn ?? [])];
		if (dependsOn.includes(taskId)) throw new ProofProtocolError(`Task ${taskId} cannot depend on itself`);
			return {
				taskId,
				summary: input.summary,
				description: input.description,
			routeFingerprint: fingerprint(`${this.obligation.theorem}\n${routeKey}`),
			scope: input.scope ?? "CONTRIBUTION",
			...(input.routeKey === undefined ? {} : { routeKey: input.routeKey }),
			...(input.targetClaimId === undefined ? {} : { targetClaimId: input.targetClaimId }),
			...(input.contributionKind === undefined ? {} : { contributionKind: input.contributionKind }),
			dependsOn,
			...(input.agent === undefined ? {} : { agent: input.agent }),
			...(input.successCriteria === undefined ? {} : { successCriteria: input.successCriteria }),
				...(input.continuationOf === undefined ? {} : { continuationOf: input.continuationOf }),
				kind: input.kind ?? "MATHEMATICAL",
				status: "PENDING",
			attempt: 0,
			updatedAt: new Date().toISOString(),
		};
	}

	private async updateTaskStatus(taskId: string, status: ProofTaskStatus, reason?: string): Promise<void> {
		const operation = this.taskStatusQueue.then(async () => {
			const current = this.stateValue.tasks.find((task) => task.taskId === taskId);
			if (current === undefined || (current.status === status && (reason === undefined || current.lastError === reason))) return;
			const updated: ProofTask = {
				...current,
				status,
				attempt: status === "RUNNING" && current.status !== "RUNNING" ? current.attempt + 1 : current.attempt,
				updatedAt: new Date().toISOString(),
				lastError: reason,
			};
			this.stateValue = { ...this.stateValue, tasks: this.stateValue.tasks.map((task) => task.taskId === taskId ? updated : task) };
			await this.emit({
				type: "proof/task_status_changed",
				eventId: randomUUID(),
				runId: this.runIdValue,
				timestamp: Date.now(),
				step: this.stateValue.step,
				taskId,
				previousStatus: current.status,
				status,
				task: updated,
				...(reason === undefined ? {} : { reason }),
			});
		});
		this.taskStatusQueue = operation.catch(() => undefined);
		await operation;
	}

	private materializeCandidate(task: ProofTask, result: Extract<ResearchResult, { kind: "candidate" }>): ProofCandidate {
		const content = result.candidate.content.trim();
		const claim = result.candidate.claim;
		const declaredEvidence = result.candidate.evidence ?? [];
		if (result.candidate.scope !== undefined && result.candidate.scope !== task.scope) throw new ProofProtocolError(`Worker scope ${result.candidate.scope} disagrees with Planner task scope ${task.scope}`);
		if (task.contributionKind !== undefined && result.candidate.contribution?.kind !== task.contributionKind) throw new ProofProtocolError(`Worker contribution kind disagrees with Planner task kind ${task.contributionKind}`);
		return {
			candidateId: result.candidate.candidateId ?? `${task.taskId}-candidate`,
			taskId: task.taskId,
			content,
			strategy: result.candidate.strategy,
			routeFingerprint: task.routeFingerprint,
			claimFingerprint: result.candidate.claimFingerprint ?? fingerprint(claim ?? this.obligation.theorem),
			candidateFingerprint: fingerprint(`${this.obligation.theorem}\n${content}`),
			evidence: [],
			discoveredEvidence: [],
			bodyReadEvidence: [],
			declaredEvidence,
			reliedOnArtifactIds: result.candidate.reliedOnArtifactIds ?? [],
			scope: task.scope,
			...(task.targetClaimId === undefined ? {} : { targetClaimId: task.targetClaimId }),
			assumptions: result.candidate.assumptions ?? result.candidate.contribution?.assumptions ?? [],
			dependencyClaims: result.candidate.dependencyClaims ?? result.candidate.contribution?.dependencyClaims ?? [],
			...(result.candidate.contribution === undefined ? {} : { contribution: result.candidate.contribution }),
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

	private consumeBudget(counter: "plannerCalls" | "workerCalls" | "verifierCalls" | "literatureSearches" | "toolCalls"): boolean {
		const budget = this.stateValue.budget;
		const limitKey = {
			plannerCalls: "maxPlannerCalls",
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

	private hasVerifiedTargetCandidate(): boolean {
		return this.stateValue.candidates.some((candidate) => candidate.scope === "TARGET" && this.stateValue.verifications[candidate.candidateId]?.verdict === "CORRECT");
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
		const candidate = (this.stateValue.submittedCandidateId === undefined ? undefined : this.stateValue.candidates.find((item) => item.candidateId === this.stateValue.submittedCandidateId))
			?? this.stateValue.candidates.find((item) => item.scope === "TARGET" && this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT")
			?? this.stateValue.candidates.find((item) => this.stateValue.verifications[item.candidateId]?.verdict === "CORRECT");
		return {
			runId: this.runIdValue,
			status: this.stateValue.status,
			mode: this.stateValue.mode,
			workflowMode: this.stateValue.workflowMode,
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
				case "proof/task_status_changed":
					this.stateValue = {
						...this.stateValue,
						tasks: this.stateValue.tasks.some((task) => task.taskId === event.taskId)
							? this.stateValue.tasks.map((task) => task.taskId === event.taskId ? event.task : task)
							: [...this.stateValue.tasks, event.task],
					};
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
					{
						const attempt = event.attempt ?? {
							attempt: this.stateValue.formalAttempts.length + 1,
							step: event.step,
							sourceId: event.proofSlug ?? event.candidateId ?? `legacy-formal-attempt-${event.step}`,
							...(event.proofSlug === undefined ? {} : { proofSlug: event.proofSlug }),
							...(event.taskId === undefined ? {} : { taskId: event.taskId }),
							...(event.candidateId === undefined ? {} : { candidateId: event.candidateId }),
							result: event.result,
							timestamp: event.timestamp,
						};
					this.stateValue = {
						...this.stateValue,
						formalAttempts: this.stateValue.formalAttempts.some((item) => item.attempt === attempt.attempt)
							? this.stateValue.formalAttempts
							: [...this.stateValue.formalAttempts, attempt],
						...(event.result.ok ? { proofLeanPath: join(this.runDirectory, "PROOF.lean") } : {}),
					};
					}
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

class InjectedProofTaskFault extends Error { constructor(message: string) { super(message); this.name = "InjectedProofTaskFault"; } }

function actionExecution(planId: string, action: ProofAction, ordinal: number): ProofPlanActionExecution { return { actionId: fingerprint(`${planId}\n${ordinal}\n${JSON.stringify(action)}`), planId, ordinal, action, status: "PENDING", resultArtifactIds: [], effectIds: [] }; }
function normalizeExecutionPlan(plan: ProofExecutionPlan): ProofExecutionPlan {
	const actions = Array.isArray((plan as Partial<ProofExecutionPlan>).actionExecutions) ? plan.actionExecutions : undefined;
	if (actions !== undefined) return plan;
	return { ...plan, actionExecutions: plan.plan.actions.map((action, ordinal) => actionExecution(plan.planId, action, ordinal)), ...(plan.status === "RUNNING" ? { status: "STALE" as const, staleReason: "Legacy persisted plan lacks per-action completion receipts; fail-closed replanning is required" } : {}) };
}
interface ActionResultCursor { readonly eventCount: number; readonly candidateIds: ReadonlySet<string>; readonly taskIds: ReadonlySet<string>; readonly outputCount: number; readonly submittedCandidateId?: string; readonly proofLeanPath?: string; }
function actionResultCursor(state: ProofState, events: readonly ProofEvent[]): ActionResultCursor { return { eventCount: events.length, candidateIds: new Set(state.candidates.map((item) => item.candidateId)), taskIds: new Set(state.tasks.map((item) => item.taskId)), outputCount: state.recentOutputs.length, ...(state.submittedCandidateId === undefined ? {} : { submittedCandidateId: state.submittedCandidateId }), ...(state.proofLeanPath === undefined ? {} : { proofLeanPath: state.proofLeanPath }) }; }
function collectActionResult(action: ProofAction, before: ActionResultCursor, state: ProofState, events: readonly ProofEvent[]): { readonly resultArtifactIds: readonly string[]; readonly effectIds: readonly string[]; readonly result: Readonly<Record<string, unknown>> } {
	const candidateIds = state.candidates.filter((item) => !before.candidateIds.has(item.candidateId)).map((item) => item.candidateId), taskIds = state.tasks.filter((item) => !before.taskIds.has(item.taskId)).map((item) => item.taskId), repositorySlugs = action.action === "write_items" ? action.items.map((item) => item.slug) : action.action === "literature_search" ? [`literature/${slugify(action.query)}`] : [], submittedCandidateId = state.submittedCandidateId !== before.submittedCandidateId ? state.submittedCandidateId : undefined, resultArtifactIds = [...repositorySlugs, ...candidateIds, ...(submittedCandidateId === undefined ? [] : [submittedCandidateId]), ...(state.proofLeanPath !== before.proofLeanPath && state.proofLeanPath !== undefined ? [state.proofLeanPath] : [])], effectIds = events.slice(before.eventCount).map((event) => event.eventId), outputs = state.recentOutputs.slice(Math.min(before.outputCount, state.recentOutputs.length));
	return { resultArtifactIds: [...new Set(resultArtifactIds)], effectIds: [...new Set(effectIds)], result: { action: action.action, taskIds, candidateIds, repositorySlugs, outputs, ...(submittedCandidateId === undefined ? {} : { submittedCandidateId }), ...(action.action === "stop" ? { terminalStatus: state.status, terminalReason: state.lastError ?? action.reason ?? "Planner stopped the run" } : {}) } };
}

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
		plannerCalls: 0,
		workerCalls: 0,
		verifierCalls: 0,
		literatureSearches: 0,
		toolCalls: 0,
		startedAt: Date.now(),
		...(options?.maxPlannerCalls === undefined ? {} : { maxPlannerCalls: options.maxPlannerCalls }),
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
		executionPlans: [...state.executionPlans],
		formalAttempts: [...state.formalAttempts],
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

function analyzeProofDecomposition(obligation: ProofObligation): ProofDecomposition {
	const source = formatTheorem(obligation).trim();
	const explicitUnits = extractProblemUnits(source, problemHeadingLabel);
	if (explicitUnits.length >= 2) {
		return {
			complexity: "COMPOSITE",
			unitCount: explicitUnits.length,
			recommendedMinimumTasks: explicitUnits.length + 1,
			signals: [
				explicitUnits.length + " explicit problem sections detected",
				"each section receives focused work before final synthesis",
			],
			units: explicitUnits,
		};
	}
	const numberedUnits = extractProblemUnits(source, numberedProblemHeadingLabel);
	if (numberedUnits.length >= 2) {
		return {
			complexity: "COMPOSITE",
			unitCount: numberedUnits.length,
			recommendedMinimumTasks: numberedUnits.length + 1,
			signals: [
			numberedUnits.length + " top-level numbered requirements detected",
			"numbered requirements are independently assignable before final synthesis",
		],
			units: numberedUnits,
		};
	}
	const requirementCount = (source.match(/prove|show|establish|derive|compute|classify|verify|construct|证明|证明出|推导|计算|分类|验证|构造/giu) ?? []).length;
	if (source.length >= 1800 || (source.length >= 900 && requirementCount >= 4)) {
		const units: ProofDecompositionUnit[] = [
			{
				unitId: "primary-route",
				label: "Independent primary proof route",
				description: "Develop a complete proof route for the original obligation, making the key lemmas, definitions, and dependency chain explicit.",
			},
			{
				unitId: "adversarial-audit",
				label: "Adversarial edge-case and gap audit",
				description: "Independently inspect the original obligation for hidden assumptions, boundary cases, counterexamples, and missing justifications; supply repairs or precise constraints.",
			},
		];
		return {
			complexity: "COMPOSITE",
			unitCount: units.length,
			recommendedMinimumTasks: units.length + 1,
			signals: [
				"long or multi-requirement obligation detected",
				"independent derivation and adversarial audit are required before synthesis",
			],
			units,
		};
	}
	return {
		complexity: "SIMPLE",
		unitCount: 1,
		recommendedMinimumTasks: 1,
		signals: ["no independent decomposition signal detected"],
		units: [{ unitId: "whole-obligation", label: "Whole obligation", description: "Solve the original obligation completely and self-containedly." }],
	};
}

function extractProblemUnits(source: string, labelOf: (line: string) => string | undefined): ProofDecompositionUnit[] {
	const lines = source.split(/\r?\n/u);
	const starts = lines.flatMap((line, lineIndex) => {
		const label = labelOf(line);
		return label === undefined ? [] : [{ lineIndex, label }];
	});
	return starts.map((start, index) => {
		const end = starts[index + 1]?.lineIndex ?? lines.length;
		return {
			unitId: "unit-" + (index + 1),
			label: start.label,
			description: lines.slice(start.lineIndex, end).join("\\n").trim(),
		};
	});
}

function problemHeadingLabel(line: string): string | undefined {
	const trimmed = line.trim().replace(/^#{1,6}\s*/u, "");
	return /^(?:第[0-9一二三四五六七八九十百千万]+\s*问|Question\s+\d+)(?:\s|[:：.)、-]|$)/iu.test(trimmed) ? trimmed : undefined;
}

function numberedProblemHeadingLabel(line: string): string | undefined {
	const trimmed = line.trim().replace(/^#{1,6}\s*/u, "");
	return /^(?:\(?\d+\)?[.)、:：-])\s+\S+/u.test(trimmed) ? trimmed : undefined;
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

function stableEventId(runId: string, phase: string, logicalId: string): string {
	return `${phase}-${fingerprint(`${runId}:${phase}:${logicalId}`).slice(0, 32)}`;
}

function uniqueEvidence<T extends { readonly artifactId: string; readonly contentHash: string }>(refs: readonly T[]): T[] {
	const seen = new Set<string>();
	return refs.filter((ref) => { const key = `${ref.artifactId}:${ref.contentHash}`; if (seen.has(key)) return false; seen.add(key); return true; });
}

function mergeWorkerVerifierOutput(research: readonly ResearchWork[], verifications: readonly VerificationWork[]): string {
	const byTask = new Map(verifications.map((work) => [work.task.taskId, work]));
	return research.map((work, index) => {
		const workerText = work.result.kind === "candidate"
			? work.result.candidate.content
			: work.result.kind === "observation"
				? work.result.content
				: work.result.kind === "partial"
					? `PARTIAL: ${work.result.reason}\n${work.result.content}`
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

function taskStatusForResearchResult(result: ResearchResult, providerBlocked: boolean): ProofTaskStatus {
	if (result.kind === "partial") return "PARTIAL";
	if (result.kind === "blocked") return providerBlocked ? "BLOCKED" : "FAILED_RETRYABLE";
	return "COMPLETED";
}

function formatContinuationMaterials(taskId: string, result: ResearchResult | undefined): string {
	if (result === undefined) return `Continuation source ${taskId} is not available in the durable result log.`;
	const content = result.kind === "candidate"
		? result.candidate.content
		: result.kind === "observation"
			? result.content
			: result.kind === "partial"
				? result.content
				: result.reason;
	return `Previous result from task ${taskId} (${result.kind}); preserve valid work and repair only the missing part:\n${content}`;
}

function createContinuationTask(
	task: ProofTask,
	events: readonly ProofEvent[],
	existingTasks: readonly ProofTask[],
	occupiedIds: Set<string>,
): ProofTaskInput {
	const taskId = nextContinuationTaskId(task.taskId, existingTasks, occupiedIds);
	occupiedIds.add(taskId);
	const result = latestResearchResult(events, task.taskId);
	const suggestedNext = result?.kind === "partial" ? result.suggestedNext : undefined;
	return {
		taskId,
		summary: `Continue incomplete task: ${task.summary}`,
			description: [
				`Continue the incomplete task ${task.taskId} from its preserved worker result.`,
				`Original task description:\n${task.description}`,
				task.successCriteria === undefined ? "" : `Original success criteria:\n${task.successCriteria}`,
				task.lastError === undefined ? "" : `Failure feedback that must be repaired:\n${task.lastError}`,
				suggestedNext === undefined ? "" : `Worker continuation hint:\n${suggestedNext}`,
				task.kind === "FORMALIZATION"
					? "Return a complete replacement Lean source file in candidate.content. Preserve the exact configured theorem declaration, remove every sorry/admit, and repair the compiler error."
					: "Return a complete candidate or a clearly delimited contribution. Preserve valid work from the previous result and finish the missing portion.",
			].filter((part) => part.length > 0).join("\n\n"),
		routeKey: `${task.routeKey ?? task.description}\ncontinuationOf=${task.taskId}`,
		scope: task.scope,
		...(task.targetClaimId === undefined ? {} : { targetClaimId: task.targetClaimId }),
		...(task.contributionKind === undefined ? {} : { contributionKind: task.contributionKind }),
		dependsOn: task.dependsOn,
		// Reuse the same logical identity when the original task relied on the
		// runtime's implicit agent. Explicit model-selected agents are preserved.
			agent: task.agent ?? { agentId: task.taskId, purpose: task.summary },
			...(task.successCriteria === undefined ? {} : { successCriteria: task.successCriteria }),
			continuationOf: task.taskId,
			kind: task.kind,
		};
}

function createFormalizationTask(state: ProofState, occupiedIds: Set<string>, hasConfiguredTarget: boolean): ProofTaskInput {
	let ordinal = state.formalAttempts.length + 1;
	let taskId = `formal-proof-attempt-${ordinal}`;
	const knownIds = new Set([...state.tasks.map((task) => task.taskId), ...occupiedIds]);
	while (knownIds.has(taskId)) {
		ordinal += 1;
		taskId = `formal-proof-attempt-${ordinal}`;
	}
	occupiedIds.add(taskId);
	const previous = state.formalAttempts.at(-1);
	const targetInstructions = hasConfiguredTarget
		? [
			"Produce one complete Lean 4 source file for the exact configured THEOREM.lean declaration.",
			"Replace only the declared `sorry` proof hole. Do not add axioms, constants, opaque declarations, `sorry`, or `admit`.",
			"Preserve the configured theorem statement exactly; compiler feedback is authoritative.",
		]
		: [
			"Translate the original mathematical theorem into a precise Lean 4 declaration and prove that declaration in one complete source file.",
			"You own the first formal target because the user supplied mathematics, not Lean code. Do not weaken, replace, or trivialize the theorem while translating it.",
			"Include only ordinary definitions/imports needed to express the stated theorem. Do not add axioms, constants, opaque declarations, `sorry`, or `admit`.",
			"The local Lean process is the authoritative checker for the generated source; return the entire source file in candidate.content, without Markdown fences.",
		];
	return {
		taskId,
		summary: previous === undefined ? "Formalize the accepted proof in Lean 4" : "Repair the Lean proof after process verification failed",
		description: [
			...targetInstructions,
			"Use the accepted informal proof as guidance.",
			previous?.result.ok === false ? `Latest Lean process feedback:\n${previous.result.feedback}` : "",
		].filter((part) => part.length > 0).join("\n\n"),
		routeKey: `formalization-process-attempt-${ordinal}`,
		scope: "TARGET",
		agent: { agentId: "formalizer", purpose: "Draft and repair the exact Lean 4 proof", capabilities: ["lean4", "compiler-feedback-repair"], role: "formalizer" },
		successCriteria: hasConfiguredTarget
			? "The full source preserves THEOREM.lean exactly, contains no trust escape hatch, and exits successfully under the configured Lean process verifier."
			: "The generated Lean declaration faithfully encodes the original theorem, contains no trust escape hatch, and exits successfully under the configured Lean process verifier.",
		kind: "FORMALIZATION",
	};
}

function nextContinuationTaskId(taskId: string, existingTasks: readonly ProofTask[], occupiedIds: ReadonlySet<string>): string {
	const knownIds = new Set([...existingTasks.map((task) => task.taskId), ...occupiedIds]);
	for (let attempt = 1; ; attempt += 1) {
		const candidate = `${taskId}:continuation-${attempt}`;
		if (!knownIds.has(candidate)) return candidate;
	}
}

function uniquePlanTaskId(baseId: string, occupiedIds: Set<string>): string {
	let candidate = baseId;
	for (let suffix = 2; occupiedIds.has(candidate); suffix += 1) candidate = `${baseId}-${suffix}`;
	occupiedIds.add(candidate);
	return candidate;
}

function latestResearchResult(events: readonly ProofEvent[], taskId: string): ResearchResult | undefined {
	for (let index = events.length - 1; index >= 0; index -= 1) {
		const event = events[index];
		if (event?.type === "proof/research_result" && event.taskId === taskId) return event.result;
	}
	return undefined;
}

function taskDependencyReason(task: ProofTask, tasks: readonly ProofTask[]): { readonly reason: string; readonly terminal: boolean } | undefined {
	for (const dependencyId of task.dependsOn) {
		const dependency = tasks.find((item) => item.taskId === dependencyId);
		if (dependency === undefined) return { reason: `Dependency ${dependencyId} is unknown.`, terminal: true };
		if (["FAILED_TERMINAL", "BLOCKED"].includes(dependency.status)) return { reason: `Dependency ${dependencyId} is ${dependency.status}; the controller must create a replacement or change the graph.`, terminal: true };
		if (dependency.status !== "COMPLETED") return { reason: `Waiting for dependency ${dependencyId} (${dependency.status}).`, terminal: false };
	}
	return undefined;
}

function validateTaskDependencies(tasks: readonly ProofTask[], existing: readonly ProofTask[]): void {
	const graph = new Map([...existing, ...tasks].map((task) => [task.taskId, task]));
	const known = new Set(graph.keys());
	for (const task of tasks) {
		if (new Set(task.dependsOn).size !== task.dependsOn.length) throw new ProofProtocolError(`Task ${task.taskId} contains duplicate dependencies`);
		for (const dependencyId of task.dependsOn) {
			if (!known.has(dependencyId)) throw new ProofProtocolError(`Task ${task.taskId} depends on unknown task ${dependencyId}`);
		}
	}
	const visiting = new Set<string>();
	const visited = new Set<string>();
	const visit = (taskId: string): void => {
		if (visited.has(taskId)) return;
		if (visiting.has(taskId)) throw new ProofProtocolError(`Dynamic task graph contains a dependency cycle at ${taskId}`);
		visiting.add(taskId);
		for (const dependencyId of graph.get(taskId)?.dependsOn ?? []) visit(dependencyId);
		visiting.delete(taskId);
		visited.add(taskId);
	};
	for (const taskId of graph.keys()) visit(taskId);
}

function normalizePersistedTask(task: ProofTask): ProofTask {
	const raw = task as Partial<ProofTask>;
	const status = isTaskStatus(raw.status) ? raw.status : "PENDING";
	return {
		...task,
		kind: raw.kind === "FORMALIZATION" ? "FORMALIZATION" : "MATHEMATICAL",
		dependsOn: Array.isArray(raw.dependsOn) ? raw.dependsOn.filter((item): item is string => typeof item === "string") : [],
		status,
		attempt: Number.isInteger(raw.attempt) && (raw.attempt as number) >= 0 ? raw.attempt as number : 0,
		updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : new Date(0).toISOString(),
	};
}

function isTaskStatus(value: unknown): value is ProofTaskStatus {
	return value === "PENDING" || value === "RUNNING" || value === "COMPLETED" || value === "PARTIAL" || value === "FAILED_RETRYABLE" || value === "FAILED_TERMINAL" || value === "BLOCKED";
}

function isWorkflowMode(value: unknown): value is ProofWorkflowMode {
	return value === "dynamic" || value === "legacy";
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

function normalizeLeanCandidate(content: string): string {
	const trimmed = content.trim();
	const fenced = trimmed.match(/^```(?:lean)?\s*\n([\s\S]*?)\n```$/u);
	return `${(fenced?.[1] ?? trimmed).trim()}\n`;
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

function validatePlanTaskIdentities(plan: ProofPlan): void {
	const ids = plan.actions.flatMap((action) => action.action === "spawn" ? action.tasks.map((task) => task.taskId).filter((item): item is string => item !== undefined) : []);
	if (new Set(ids).size !== ids.length) throw new ProofProtocolError("Planner task ids must be unique inside a persisted plan");
}
