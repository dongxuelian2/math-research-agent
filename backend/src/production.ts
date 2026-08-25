import { mkdir, access } from "node:fs/promises";
import { join } from "node:path";
import { AgentCore } from "./agent/agent.js";
import { createAgentProofRoles, type AgentProofRoles } from "./proof/agent-role.js";
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
	return async ({ sessionId, runId, config: snapshot }) => {
		const config = snapshot ?? (typeof options.config === "function" ? options.config() : options.config.config);
		const rolesDirectory = join(options.rootDirectory, "role-sessions", sessionId, runId);
		await mkdir(rolesDirectory, { recursive: true });

		const planner = await createRoleAgent("planner", config, rolesDirectory);
		const worker = await createRoleAgent("worker", config, rolesDirectory);
		const verifier = await createRoleAgent("verifier", config, rolesDirectory);
		return createAgentProofRoles({ planner, researcher: worker, verifier });
	};
}

async function createRoleAgent(role: Extract<ProofRole, "planner" | "worker" | "verifier">, config: MathAgentConfig, directory: string): Promise<AgentCore> {
	const profile = config.roles[role];
	const modelProfile = config.models[profile.model];
	if (modelProfile === undefined) throw new Error(`Role ${role} references unknown model ${profile.model}`);
	const sessionId = `proof-${role}`;
	const sessionDirectory = join(directory, role);
	const session = await openOrCreateSession(sessionId, sessionDirectory, directory);
	const model = modelConfigOf(modelProfile);
	const provider = createProvider(model, modelProfile.provider === "mock" ? { mockResponses: createMockResponses(role) } : {});
	return new AgentCore({
		session,
		model,
		provider,
		maxTurns: profile.maxTurns ?? config.proof.maxSteps,
	});
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
