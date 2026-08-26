import type { ArtifactRef, ArtifactType, ResearchArtifact, ResearchObligation, ResearchProjectState } from "./types.js";
import { activeAuthorityForClaim } from "./invariants.js";
import { ResearchStore } from "./store.js";

export interface RetrievalQuery { readonly text: string; readonly artifactTypes?: readonly ArtifactType[]; readonly claimIds?: readonly string[]; readonly routeIds?: readonly string[]; readonly limit?: number; }
export interface RetrievalHit { readonly artifact: ResearchArtifact; readonly authorityStatus: "CURRENT" | "STALE" | "HISTORICAL" | "NOT_APPLICABLE"; readonly score: number; readonly matchedTerms: readonly string[]; readonly excerpt: string; }

/** Deterministic retrieval across corpus, promoted mathematics, literature, computations, and route evidence. */
export class ResearchRetrievalService {
	constructor(private readonly store: ResearchStore) {}

	async search(projectId: string, query: RetrievalQuery): Promise<readonly RetrievalHit[]> {
		const state = await this.store.read(projectId); const terms = tokens(query.text); const allowed = query.artifactTypes === undefined ? undefined : new Set(query.artifactTypes);
		const proximity = new Set<ArtifactRef>();
		for (const claimId of query.claimIds ?? []) for (const ref of state.claims[claimId]?.at(-1)?.evidenceRefs ?? []) proximity.add(ref);
		for (const routeId of query.routeIds ?? []) for (const ref of state.routes[routeId]?.artifactRefs ?? []) proximity.add(ref);
		const proximityIds = new Set([...proximity].map((ref) => ref.artifactId)); const hits: RetrievalHit[] = [];
		for (const artifact of Object.values(state.artifacts)) {
			if (allowed !== undefined && !allowed.has(artifact.artifactType)) continue;
			if (["CONTEXT_MANIFEST", "CHECKPOINT", "EXECUTION_RECEIPT", "TACTICAL_RESULT"].includes(artifact.artifactType)) continue;
			const artifactAuthorities = Object.values(state.authorityReceipts).filter((receipt) => receipt.artifact.artifactId === artifact.artifactId && receipt.artifact.contentHash === artifact.contentHash); if (artifact.artifactType === "PROMOTED_PROOF" && artifactAuthorities.length === 0) continue;
			const authorityStatus: RetrievalHit["authorityStatus"] = artifact.artifactType !== "PROMOTED_PROOF" && artifact.artifactType !== "FINAL_PROOF" ? "NOT_APPLICABLE" : artifactAuthorities.some((receipt) => activeAuthorityForClaim(state, receipt.claimId, receipt.claimRevision)?.authorityReceiptId === receipt.authorityReceiptId) ? "CURRENT" : artifactAuthorities.some((receipt) => state.authorityValidation[receipt.authorityReceiptId]?.status === "STALE" || state.authorityValidation[receipt.authorityReceiptId]?.status === "INVALIDATED") || (artifact.artifactType === "FINAL_PROOF" && state.finalProofHistory.some((item) => item.artifact.artifactId === artifact.artifactId && item.status === "STALE")) ? "STALE" : "HISTORICAL";
			let body: string; try { body = (await this.store.resolveArtifact(projectId, artifact)).body; } catch { continue; }
			const lower = body.toLocaleLowerCase(); const matched = terms.filter((term) => lower.includes(term));
			let score = matched.reduce((sum, term) => sum + occurrences(lower, term) * (term.length > 6 ? 3 : 1), 0);
			if (query.text.trim().length > 0 && lower.includes(query.text.trim().toLocaleLowerCase())) score += 50;
			if (proximityIds.has(artifact.artifactId)) score += 40;
			if (artifact.artifactType === "PROMOTED_PROOF" || artifact.artifactType === "FINAL_PROOF") score += authorityStatus === "CURRENT" ? 12 : -8;
			if (artifact.authority === "VERIFIED_CURRENT" || artifact.authority === "VERIFIED_IMPORTED") score += 8;
			if (score <= 0 && terms.length > 0) continue;
			hits.push({ artifact, authorityStatus, score, matchedTerms: matched, excerpt: `${authorityStatus === "STALE" ? "[STALE HISTORICAL AUTHORITY] " : authorityStatus === "HISTORICAL" ? "[HISTORICAL] " : ""}${excerpt(body, matched[0] ?? terms[0])}` });
		}
		return hits.sort((a, b) => b.score - a.score || a.artifact.artifactId.localeCompare(b.artifact.artifactId)).slice(0, Math.max(1, query.limit ?? 12));
	}

	async read(projectId: string, artifactId: string, offset = 0, limit?: number): Promise<{ readonly artifact: ResearchArtifact; readonly content: string; readonly lineStart: number; readonly lineEnd: number }> {
		const state = await this.store.read(projectId); const artifact = state.artifacts[artifactId]; if (artifact === undefined) throw new Error(`Artifact not found: ${artifactId}`);
		const body = (await this.store.resolveArtifact(projectId, artifact)).body; const lines = body.split(/\r?\n/u); const end = Math.min(lines.length, offset + (limit ?? lines.length));
		return { artifact, content: lines.slice(offset, end).join("\n"), lineStart: offset, lineEnd: end };
	}

	async metadata(projectId: string, artifactId: string): Promise<ResearchArtifact> { const artifact = (await this.store.read(projectId)).artifacts[artifactId]; if (artifact === undefined) throw new Error(`Artifact not found: ${artifactId}`); return artifact; }
}

export class ResearchContextBuilder {
	constructor(private readonly retrieval: ResearchRetrievalService) {}

	async select(state: ResearchProjectState, obligation: ResearchObligation, limit = 12): Promise<{ readonly claimIds: readonly string[]; readonly artifactRefs: readonly ArtifactRef[]; readonly routeIds: readonly string[] }> {
		const target = state.claims[obligation.claimId]?.at(-1); const claimIds = [...new Set([obligation.claimId, ...(target?.dependencies ?? [])])];
		for (const [claimId, revisions] of Object.entries(state.claims)) if (revisions.at(-1)?.dependencies.includes(obligation.claimId)) claimIds.push(claimId);
		const routeIds = Object.values(state.routes).filter((route) => route.targetObligationId === obligation.obligationId).map((route) => route.routeId);
		const direct = claimIds.flatMap((claimId) => state.claims[claimId]?.at(-1)?.evidenceRefs ?? []); const routeRefs = routeIds.flatMap((routeId) => state.routes[routeId]?.artifactRefs ?? []);
		const hits = await this.retrieval.search(state.projectId, { text: obligation.statement, claimIds, routeIds, limit });
		return { claimIds: [...new Set(claimIds)].slice(0, 24), routeIds, artifactRefs: uniqueRefs([...direct, ...routeRefs, ...hits.map((hit) => hit.artifact)]).slice(0, limit) };
	}
}

function tokens(value: string): string[] { return [...new Set(value.toLocaleLowerCase().match(/[\p{L}\p{N}_-]{3,}/gu) ?? [])].filter((term) => !STOP.has(term)); }
const STOP = new Set(["the", "and", "for", "that", "with", "from", "prove", "show", "then", "every"]);
function occurrences(body: string, term: string): number { let count = 0, at = 0; while ((at = body.indexOf(term, at)) >= 0) { count += 1; at += term.length; } return Math.min(count, 20); }
function excerpt(body: string, term?: string): string { const at = term === undefined ? 0 : Math.max(0, body.toLocaleLowerCase().indexOf(term)); return body.slice(Math.max(0, at - 120), Math.min(body.length, at + 500)); }
function uniqueRefs(refs: readonly ArtifactRef[]): ArtifactRef[] { const seen = new Set<string>(); return refs.filter((ref) => { const key = `${ref.artifactId}:${ref.contentHash}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
