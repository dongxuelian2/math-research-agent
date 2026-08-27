import { mkdir, access } from "node:fs/promises";
import { join } from "node:path";
import { AgentCore } from "./agent/agent.js";
import type { Agent, AgentEventListener } from "./agent/types.js";
import type { AgentRunResult, UserMessage } from "./models/index.js";
import { createAgentProofResearcher, createAgentProofRoles, type AgentProofRoles } from "./proof/agent-role.js";
import type { ProofAgentFactory, ProofResearcher } from "./proof/types.js";
import type { ProofApiRoleFactory } from "./api/server.js";
import { createProvider } from "./providers/registry.js";
import { Session } from "./session/session.js";
import {
	createMockResponses,
	modelConfigOf,
	type MathAgentConfig,
	type MathAgentConfigService,
	type ProofRole,
} from "./config.js";

export type ConfiguredProofRoleFactoryOptions = {
	readonly config: MathAgentConfigService | (() => MathAgentConfig);
	readonly rootDirectory: string;
};

/**
 * Create the production Proof API role factory. A config snapshot is captured
 * at run creation, so editing settings never changes a live run halfway through.
 */
export function createConfiguredProofRoleFactory(options: ConfiguredProofRoleFactoryOptions): ProofApiRoleFactory {
	const factory: ProofApiRoleFactory = async ({ sessionId, runId, config: snapshot, tools, targetGate }) => {
		const config = snapshot ?? (typeof options.config === "function" ? options.config() : options.config.config);
		const rolesDirectory = join(options.rootDirectory, "role-sessions", sessionId, runId);
		await mkdir(rolesDirectory, { recursive: true });

		const planner = await createRoleAgent("planner", config, rolesDirectory, undefined, targetGate);
		const worker = await createRoleAgent("worker", config, rolesDirectory, tools, targetGate);
		const verifier = await createRoleAgent("verifier", config, rolesDirectory, tools, targetGate);
		const dynamicResearchers = new Map<string, Promise<ProofResearcher>>();
		const agentFactory: ProofAgentFactory = (spec) => {
			const existing = dynamicResearchers.get(spec.agentId);
			if (existing !== undefined) return existing;
			const created = createRoleAgent("worker", config, rolesDirectory, tools, targetGate, spec.agentId).then(createAgentProofResearcher);
			dynamicResearchers.set(spec.agentId, created);
			return created;
		};
		return createAgentProofRoles({ planner, researcher: worker, verifier, agentFactory });
	};
	factory.createAgent = async ({ role, sessionId, runId, tools, config: snapshot }) => { const config = snapshot ?? (typeof options.config === "function" ? options.config() : options.config.config); const directory = join(options.rootDirectory, "research-role-sessions", sessionId, runId); await mkdir(directory, { recursive: true }); return createRoleAgent(role, config, directory, tools); };
	return factory;
}

async function createRoleAgent(role: ProofRole, config: MathAgentConfig, directory: string, tools?: readonly import("./models/tools.js").RuntimeTool[], targetGate?: { readonly targetObligationId: string; readonly targetClaimId: string }, instanceId?: string): Promise<Agent> {
	const profile = config.roles[role];
	const modelProfile = config.models[profile.model];
	if (modelProfile === undefined) throw new Error(`Role ${role} references unknown model ${profile.model}`);
	const identity = instanceId === undefined ? role : `${role}-${safeAgentSegment(instanceId)}`;
	const sessionId = `proof-${identity}`;
	const sessionDirectory = join(directory, identity);
	const session = await openOrCreateSession(sessionId, sessionDirectory, directory);
	const model = modelConfigOf(modelProfile);
	const provider = createProvider(model, modelProfile.provider === "mock" ? { mockResponses: createMockResponses(role, targetGate) } : {});
	const agent = new AgentCore({
		session,
		model,
		provider,
		...(tools === undefined ? {} : { tools }),
		// proof.max_steps bounds controller rounds, not the number of model/tool
		// turns inside one logical agent. Keep the agent default aligned with the
		// AgentCore default unless a role explicitly configures max_turns.
		maxTurns: profile.maxTurns ?? 32,
	});
	return profile.timeoutSeconds === undefined ? agent : timeoutAgent(agent, profile.timeoutSeconds);
}

function safeAgentSegment(value: string): string {
	return value.trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "agent";
}

function timeoutAgent(agent: Agent, timeoutSeconds: number): Agent {
	const timeoutMs = Math.max(1, timeoutSeconds) * 1000;
	return {
		get state() { return agent.state; },
		async prompt(input: UserMessage | string): Promise<AgentRunResult> { let timer: NodeJS.Timeout | undefined; try { return await Promise.race([agent.prompt(input), new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { void agent.abort(); reject(new Error(`Configured role timeout exceeded after ${timeoutSeconds}s`)); }, timeoutMs); })]); } finally { if (timer !== undefined) clearTimeout(timer); } },
		steer(message: UserMessage | string) { agent.steer(message); }, followUp(message: UserMessage | string) { agent.followUp(message); }, abort() { return agent.abort(); }, subscribe(listener: AgentEventListener) { return agent.subscribe(listener); },
	};
}

async function openOrCreateSession(sessionId: string, directory: string, cwd: string): Promise<Session> {
	const filePath = join(directory, `${sessionId}.jsonl`);
	try {
		await access(filePath);
		return Session.resume(filePath);
	} catch (error) {
		if (!isMissingFile(error)) throw error;
		return Session.create({ projectId: sessionId, sessionId, cwd, directory });
	}
}

function isMissingFile(error: unknown): boolean {
	return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT";
}

export type { AgentProofRoles };
