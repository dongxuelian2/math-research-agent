import {
	ProofRuntime as DurableProofRuntime,
	type ProofRuntimeOptions,
} from "./runtime-core.js";
import type {
	ProofAction,
	ProofEvent,
	ProofPlan,
	ProofPlanner,
	ProofPlannerContext,
	ProofPlannerTrace,
	ProofPlannerWithTrace,
	ProofResearchContext,
	ProofResearcher,
	ProofTask,
	ProofTaskInput,
	ProofVerifier,
	ResearchResult,
} from "./types.js";

export type { ProofRuntimeOptions } from "./runtime-core.js";

/**
 * Compile controller-authored task graphs into durable dependency frontiers.
 *
 * The core runtime already persists every action receipt and resumes actions
 * exactly where they stopped. By lowering one spawn DAG into ordered spawn
 * frontiers here, the runtime can execute all currently-known dependency
 * layers without paying for another Planner turn at each barrier.
 */
export function compileWorkflowPlan(plan: ProofPlan): ProofPlan {
	const actions = plan.actions.flatMap((action) => action.action === "spawn" ? compileSpawnFrontiers(action) : [action]);
	if (actions.length === plan.actions.length && actions.every((action, index) => action === plan.actions[index])) return plan;
	return { ...plan, actions };
}

/**
 * Public proof runtime with two workflow-controller upgrades layered over the
 * durable execution core:
 *
 * 1. spawn DAGs are compiled into autonomous dependency frontiers;
 * 2. completed predecessor results are injected into dependent Worker and
 *    Verifier contexts as explicit runtime dataflow.
 *
 * The durable state machine, action receipts, crash recovery, novelty gates,
 * formal gates, and provider accounting remain owned by runtime-core.
 */
export class ProofRuntime extends DurableProofRuntime {
	constructor(options: ProofRuntimeOptions) {
		let eventSource: () => readonly ProofEvent[] = () => [];
		const planner = new CompiledProofPlanner(options.planner);
		const researcher = withDependencyDataflow(options.researcher, () => eventSource());
		const verifier = withVerifierDependencyDataflow(options.verifier, () => eventSource());
		const agentFactory = options.agentFactory === undefined
			? undefined
			: async (spec: Parameters<NonNullable<ProofRuntimeOptions["agentFactory"]>>[0], context: ProofResearchContext): Promise<ProofResearcher> => {
				const enriched = enrichResearchContext(context, eventSource());
				const selected = await options.agentFactory!(spec, enriched);
				return withDependencyDataflow(selected, () => eventSource());
			};

		super({
			...options,
			planner,
			researcher,
			verifier,
			...(agentFactory === undefined ? {} : { agentFactory }),
		});
		eventSource = () => this.events;
	}
}

/** Named entry point retained for callers that prefer the workflow concept. */
export class ProofWorkflow extends ProofRuntime {}

class CompiledProofPlanner implements ProofPlanner, ProofPlannerWithTrace {
	constructor(private readonly planner: ProofPlanner) {}

	get lastTrace(): ProofPlannerTrace | undefined {
		return (this.planner as ProofPlannerWithTrace).lastTrace;
	}

	async plan(context: ProofPlannerContext, signal?: AbortSignal): Promise<ProofPlan> {
		return compileWorkflowPlan(await this.planner.plan(context, signal));
	}
}

function compileSpawnFrontiers(action: Extract<ProofAction, { readonly action: "spawn" }>): readonly ProofAction[] {
	if (action.tasks.length < 2 || action.tasks.some((task) => task.taskId === undefined)) return [action];
	const localIds = new Set(action.tasks.map((task) => task.taskId as string));
	if (localIds.size !== action.tasks.length) return [action];

	const remaining = [...action.tasks];
	const scheduled = new Set<string>();
	const frontiers: ProofTaskInput[][] = [];
	while (remaining.length > 0) {
		const ready = remaining.filter((task) => (task.dependsOn ?? []).every((dependencyId) => !localIds.has(dependencyId) || scheduled.has(dependencyId)));
		if (ready.length === 0) {
			// Keep the original action intact so the core validator can surface its
			// existing deterministic cycle/identity error instead of masking it.
			return [action];
		}
		frontiers.push(ready);
		for (const task of ready) {
			scheduled.add(task.taskId as string);
			const index = remaining.indexOf(task);
			if (index >= 0) remaining.splice(index, 1);
		}
	}
	if (frontiers.length === 1) return [action];

	return frontiers.map((tasks, index) => ({
		action: "spawn" as const,
		tasks,
		...(action.summary === undefined
			? { summary: `Dynamic workflow frontier ${index + 1}/${frontiers.length}` }
			: { summary: `${action.summary} — frontier ${index + 1}/${frontiers.length}` }),
	}));
}

function withDependencyDataflow(researcher: ProofResearcher, events: () => readonly ProofEvent[]): ProofResearcher {
	return {
		research(context, signal) {
			return researcher.research(enrichResearchContext(context, events()), signal);
		},
	};
}

function withVerifierDependencyDataflow(verifier: ProofVerifier, events: () => readonly ProofEvent[]): ProofVerifier {
	return {
		verify(candidate, context, signal) {
			const dependencyMaterials = formatDependencyMaterials(context.task, events());
			if (dependencyMaterials.length === 0) return verifier.verify(candidate, context, signal);
			const referencedMaterials = [context.referencedMaterials ?? "", dependencyMaterials].filter(Boolean).join("\n\n");
			return verifier.verify(candidate, { ...context, referencedMaterials }, signal);
		},
	};
}

function enrichResearchContext(context: ProofResearchContext, events: readonly ProofEvent[]): ProofResearchContext {
	const dependencyMaterials = formatDependencyMaterials(context.task, events);
	if (dependencyMaterials.length === 0) return context;
	return {
		...context,
		referencedMaterials: [context.referencedMaterials, dependencyMaterials].filter(Boolean).join("\n\n"),
	};
}

function formatDependencyMaterials(task: ProofTask, events: readonly ProofEvent[]): string {
	const sections = task.dependsOn.flatMap((dependencyId) => {
		const result = latestResearchResult(events, dependencyId);
		return result === undefined
			? []
			: [`## Dependency output: ${dependencyId}\n\n${researchResultText(result)}`];
	});
	return sections.length === 0
		? ""
		: ["# Runtime dependency dataflow", ...sections].join("\n\n");
}

function latestResearchResult(events: readonly ProofEvent[], taskId: string): ResearchResult | undefined {
	for (let index = events.length - 1; index >= 0; index -= 1) {
		const event = events[index];
		if (event?.type === "proof/research_result" && event.taskId === taskId) return event.result;
	}
	return undefined;
}

function researchResultText(result: ResearchResult): string {
	if (result.kind === "candidate") return result.candidate.content;
	if (result.kind === "observation") return result.content;
	if (result.kind === "partial") return `PARTIAL (${result.reason})\n${result.content}`;
	return `BLOCKED: ${result.reason}`;
}
