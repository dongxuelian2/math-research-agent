import { sha256, stableId } from "./ids.js";
import { ResearchStore } from "./store.js";
import type { ArtifactRef } from "./types.js";

export type LiteratureAuthorityTier = "DISCOVERED_METADATA" | "SEARCH_SNIPPET" | "ABSTRACT_ONLY" | "LANDING_PAGE" | "EXACT_EXCERPT" | "FULL_TEXT" | "FULL_TEXT_BINARY";
export interface LiteratureCandidate { readonly sourceId: string; readonly title: string; readonly authors: readonly string[]; readonly identifier?: string; readonly url?: string; readonly acquisitionUrl?: string; readonly snippet?: string; readonly abstract?: string; readonly publication?: string; }
export interface LiteratureAcquisition { readonly body: string; readonly authorityTier: LiteratureAuthorityTier; readonly mediaType?: string; readonly sourceUrl?: string; readonly sourceContentHash?: string; readonly sourceByteLength?: number; readonly truncated?: boolean; }
export interface LiteratureProvider { search(query: string, signal?: AbortSignal): Promise<readonly LiteratureCandidate[]>; acquire(candidate: LiteratureCandidate, signal?: AbortSignal): Promise<string | LiteratureAcquisition | undefined>; }
export interface ApplicabilityAssessment { readonly assessmentId: string; readonly source: ArtifactRef; readonly targetObligationId: string; readonly literatureClaim: string; readonly assumptions: readonly string[]; readonly applicable: boolean; readonly reason: string; readonly scopeLimitations: readonly string[]; readonly authorityTier: LiteratureAuthorityTier; readonly createdAt: string; }
export type LiteratureAuthorityStage = "DISCOVERED" | "SOURCE_ACQUIRED" | "APPLICABILITY_PROPOSED" | "APPLICABILITY_VERIFIED" | "ACCEPTED_FOR_USE";
export interface LiteratureDiscoveryResult { readonly candidate: LiteratureCandidate; readonly artifact: ArtifactRef; readonly authority: "EXACT_SOURCE" | "SOURCE_DISCOVERED_AUTHORITY_INSUFFICIENT"; readonly authorityTier: LiteratureAuthorityTier; readonly stage: LiteratureAuthorityStage; readonly assessment?: ApplicabilityAssessment; }

export class LiteratureService {
	constructor(private readonly store: ResearchStore, private readonly provider: LiteratureProvider) {}
	async discover(projectId: string, query: string, targetObligationId: string, assess: (candidate: LiteratureCandidate, body: string) => Promise<Omit<ApplicabilityAssessment, "assessmentId" | "source" | "targetObligationId" | "authorityTier" | "createdAt">> | Omit<ApplicabilityAssessment, "assessmentId" | "source" | "targetObligationId" | "authorityTier" | "createdAt">, signal?: AbortSignal): Promise<readonly LiteratureDiscoveryResult[]> {
		const results: LiteratureDiscoveryResult[] = [];
		for (const candidate of await this.provider.search(query, signal)) {
			const raw = await this.provider.acquire(candidate, signal), acquisition = normalizeAcquisition(candidate, raw), exact = acquisition.authorityTier === "EXACT_EXCERPT" || acquisition.authorityTier === "FULL_TEXT";
			const artifact = await this.store.putArtifact(projectId, { artifactType: "LITERATURE_SOURCE", body: acquisition.body, provenance: JSON.stringify({ query, title: candidate.title, authors: candidate.authors, identifier: candidate.identifier, url: candidate.url, acquisitionUrl: candidate.acquisitionUrl, publication: candidate.publication, authorityTier: acquisition.authorityTier, mediaType: acquisition.mediaType, sourceUrl: acquisition.sourceUrl, sourceContentHash: acquisition.sourceContentHash, sourceByteLength: acquisition.sourceByteLength, truncated: acquisition.truncated, retrievedAt: new Date().toISOString() }), authority: "LITERATURE_SOURCE", metadata: { title: candidate.title, authors: candidate.authors, identifier: candidate.identifier, url: candidate.url, query, authorityTier: acquisition.authorityTier, mediaType: acquisition.mediaType, sourceUrl: acquisition.sourceUrl, sourceContentHash: acquisition.sourceContentHash, sourceByteLength: acquisition.sourceByteLength, truncated: acquisition.truncated } });
			if (!exact) { results.push({ candidate, artifact, authority: "SOURCE_DISCOVERED_AUTHORITY_INSUFFICIENT", authorityTier: acquisition.authorityTier, stage: acquisition.authorityTier === "DISCOVERED_METADATA" || acquisition.authorityTier === "SEARCH_SNIPPET" || acquisition.authorityTier === "ABSTRACT_ONLY" ? "DISCOVERED" : "SOURCE_ACQUIRED" }); continue; }
			const basis = await assess(candidate, acquisition.body), assessment: ApplicabilityAssessment = { ...basis, assessmentId: stableId("literature-assessment", projectId, artifact.artifactId, targetObligationId), source: artifact, targetObligationId, authorityTier: acquisition.authorityTier, createdAt: new Date().toISOString() };
			await this.store.putArtifact(projectId, { artifactType: "AUDIT_RECEIPT", body: `${JSON.stringify(assessment, null, 2)}\n`, provenance: "LiteratureApplicabilityAssessment", references: [artifact] });
			results.push({ candidate, artifact, authority: "EXACT_SOURCE", authorityTier: acquisition.authorityTier, stage: assessment.applicable ? "ACCEPTED_FOR_USE" : "APPLICABILITY_VERIFIED", assessment });
		}
		return results;
	}
}

/** Real, credential-free discovery/acquisition seam with fail-closed source tiers. */
export class OpenAlexLiteratureProvider implements LiteratureProvider {
	constructor(private readonly options: { readonly maxResults?: number; readonly maxBytes?: number; readonly mailto?: string } = {}) {}
	async search(query: string, signal?: AbortSignal): Promise<readonly LiteratureCandidate[]> {
		const url = new URL("https://api.openalex.org/works"); url.searchParams.set("search", query); url.searchParams.set("per-page", String(Math.min(25, Math.max(1, this.options.maxResults ?? 5)))); if (this.options.mailto !== undefined) url.searchParams.set("mailto", this.options.mailto);
		const response = await fetch(url, { signal, headers: { accept: "application/json", "user-agent": "math-research-agent/0.1" } }); if (!response.ok) throw new Error(`OpenAlex search failed: HTTP ${response.status}`);
		const value: unknown = await response.json(); if (!record(value) || !Array.isArray(value.results)) throw new Error("OpenAlex response schema changed");
		return value.results.flatMap((raw): LiteratureCandidate[] => { if (!record(raw) || typeof raw.id !== "string" || typeof raw.title !== "string") return []; const authors = Array.isArray(raw.authorships) ? raw.authorships.flatMap((item): string[] => record(item) && record(item.author) && typeof item.author.display_name === "string" ? [item.author.display_name] : []) : []; const primary = record(raw.primary_location) ? raw.primary_location : undefined, best = record(raw.best_oa_location) ? raw.best_oa_location : undefined, acquisitionUrl = string(best?.pdf_url) ?? string(best?.landing_page_url) ?? string(primary?.landing_page_url), identifier = record(raw.ids) ? string(raw.ids.doi) ?? string(raw.ids.openalex) : undefined, abstract = reconstructAbstract(raw.abstract_inverted_index); return [{ sourceId: raw.id, title: raw.title, authors, ...(identifier === undefined ? {} : { identifier }), ...(string(primary?.landing_page_url) === undefined ? {} : { url: string(primary?.landing_page_url) }), ...(acquisitionUrl === undefined ? {} : { acquisitionUrl }), ...(abstract === undefined ? {} : { abstract }), ...(record(primary?.source) && typeof primary.source.display_name === "string" ? { publication: primary.source.display_name } : {}) }]; });
	}
	async acquire(candidate: LiteratureCandidate, signal?: AbortSignal): Promise<LiteratureAcquisition | undefined> {
		if (candidate.acquisitionUrl === undefined) return undefined;
		const response = await fetch(candidate.acquisitionUrl, { signal, redirect: "follow", headers: { accept: "application/pdf,text/plain,text/html,application/xhtml+xml", "user-agent": "math-research-agent/0.1" } }); if (!response.ok) return undefined;
		const mediaType = response.headers.get("content-type")?.toLocaleLowerCase() ?? "application/octet-stream", max = Math.max(10_000, this.options.maxBytes ?? 2_000_000);
		if (mediaType.includes("application/pdf")) { const bytes = Buffer.from(await response.arrayBuffer()), truncated = bytes.byteLength > max, bounded = truncated ? bytes.subarray(0, max) : bytes; return { body: `data:application/pdf;base64,${bounded.toString("base64")}`, authorityTier: "FULL_TEXT_BINARY", mediaType, sourceUrl: response.url, sourceContentHash: sha256(bytes), sourceByteLength: bytes.byteLength, truncated }; }
		const body = await response.text(), bounded = Buffer.byteLength(body) > max ? body.slice(0, max) : body;
		if (mediaType.includes("html") || mediaType.includes("xhtml") || mediaType.includes("xml")) return { body: bounded, authorityTier: "LANDING_PAGE", mediaType, sourceUrl: response.url };
		if (mediaType.includes("text/plain")) return { body: bounded, authorityTier: "EXACT_EXCERPT", mediaType, sourceUrl: response.url };
		return undefined;
	}
}

function normalizeAcquisition(candidate: LiteratureCandidate, value: string | LiteratureAcquisition | undefined): LiteratureAcquisition {
	if (typeof value === "string") return { body: value, authorityTier: "EXACT_EXCERPT" };
	if (value !== undefined) return value;
	if (candidate.abstract !== undefined) return { body: candidate.abstract, authorityTier: "ABSTRACT_ONLY", sourceUrl: candidate.url };
	if (candidate.snippet !== undefined) return { body: candidate.snippet, authorityTier: "SEARCH_SNIPPET", sourceUrl: candidate.url };
	return { body: `${JSON.stringify({ sourceId: candidate.sourceId, title: candidate.title, authors: candidate.authors, identifier: candidate.identifier, url: candidate.url, publication: candidate.publication }, null, 2)}\n`, authorityTier: "DISCOVERED_METADATA", sourceUrl: candidate.url };
}
function reconstructAbstract(value: unknown): string | undefined { if (!record(value)) return undefined; const words: { word: string; at: number }[] = []; for (const [word, positions] of Object.entries(value)) if (Array.isArray(positions)) for (const position of positions) if (typeof position === "number") words.push({ word, at: position }); return words.length === 0 ? undefined : words.sort((a, b) => a.at - b.at).map((item) => item.word).join(" "); }
function record(value: unknown): value is Record<string, any> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function string(value: unknown): string | undefined { return typeof value === "string" && value.length > 0 ? value : undefined; }
