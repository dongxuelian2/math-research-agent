import type { ProofVerifier } from "./types.js";

type VerifierLane = {
	readonly verifier: Promise<ProofVerifier>;
	tail: Promise<void>;
};

/**
 * Give every candidate a stable independent verifier while still reusing that
 * verifier for deterministic resume of the same candidate.
 *
 * Different candidate ids never share a verifier instance, so verifier fan-out
 * can run concurrently without colliding on AgentCore.activeRun or leaking
 * conversation state between candidate audits. Calls for the same candidate
 * are serialized defensively because one AgentCore cannot accept two prompts
 * at the same time.
 */
export function createCandidateVerifierPool(
	factory: (candidateId: string) => ProofVerifier | Promise<ProofVerifier>,
): ProofVerifier {
	const lanes = new Map<string, VerifierLane>();
	return {
		async verify(candidate, context, signal) {
			let lane = lanes.get(candidate.candidateId);
			if (lane === undefined) {
				const verifier = Promise.resolve(factory(candidate.candidateId));
				lane = { verifier, tail: Promise.resolve() };
				lanes.set(candidate.candidateId, lane);
				void verifier.catch(() => {
					if (lanes.get(candidate.candidateId) === lane) lanes.delete(candidate.candidateId);
				});
			}

			const previous = lane.tail.catch(() => undefined);
			let release: () => void = () => undefined;
			const turn = new Promise<void>((resolve) => { release = resolve; });
			lane.tail = previous.then(() => turn);
			await previous;
			try {
				return await (await lane.verifier).verify(candidate, context, signal);
			} finally {
				release();
			}
		},
	};
}
