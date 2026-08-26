import { stableId } from "./ids.js";
import { ResearchStore } from "./store.js";
import type { ArtifactRef, EvidenceRole, ToolEvidenceReceipt } from "./types.js";

export class ResearchEvidenceRecorder {
	private sequence = 0;
	constructor(private readonly store: ResearchStore, private readonly projectId: string, private readonly attemptId: string) {}
	get attemptIdentity(): string { return this.attemptId; }

	async record(input: { readonly role: EvidenceRole; readonly logicalTaskId?: string; readonly operation: ToolEvidenceReceipt["operation"]; readonly artifact: ArtifactRef; readonly ranges?: readonly string[]; readonly toolCallId?: string }): Promise<ToolEvidenceReceipt> {
		this.sequence += 1; const toolCallId = input.toolCallId ?? stableId("tool-call", this.attemptId, input.role, input.logicalTaskId ?? "unscoped", String(this.sequence));
		const receipt: ToolEvidenceReceipt = { receiptId: stableId("tool-evidence", this.projectId, this.attemptId, toolCallId, input.artifact.artifactId, input.operation), attemptId: this.attemptId, role: input.role, ...(input.logicalTaskId === undefined ? {} : { logicalTaskId: input.logicalTaskId }), toolCallId, operation: input.operation, artifact: input.artifact, ranges: input.ranges ?? [], timestamp: new Date().toISOString() };
		await this.store.transaction(this.projectId, (draft) => { (draft as MutableState).toolEvidenceReceipts = { ...draft.toolEvidenceReceipts, [receipt.receiptId]: draft.toolEvidenceReceipts[receipt.receiptId] ?? receipt }; });
		return receipt;
	}

	async countToolCall(): Promise<void> { await this.store.transaction(this.projectId, (draft) => { (draft as MutableState).budget = { ...draft.budget, toolCalls: draft.budget.toolCalls + 1 }; }); }

	async list(role?: EvidenceRole, logicalTaskId?: string): Promise<readonly ToolEvidenceReceipt[]> { return Object.values((await this.store.read(this.projectId)).toolEvidenceReceipts).filter((receipt) => receipt.attemptId === this.attemptId && (role === undefined || receipt.role === role) && (logicalTaskId === undefined || receipt.logicalTaskId === logicalTaskId)); }
	async refs(role: EvidenceRole, logicalTaskId: string, classification: "BODY_READ" | "DISCOVERED" = "BODY_READ"): Promise<readonly (ArtifactRef & { readonly ranges?: readonly string[] })[]> { const values = (await this.list(role, logicalTaskId)).filter((receipt) => classification === "DISCOVERED" ? receipt.operation === "SEARCH" : receipt.operation === "READ" || receipt.operation === "COMPUTE"); const seen = new Set<string>(); return values.filter((receipt) => { const key = `${receipt.artifact.artifactId}:${receipt.artifact.contentHash}`; if (seen.has(key)) return false; seen.add(key); return true; }).map((receipt) => ({ ...receipt.artifact, ...(receipt.ranges.length === 0 ? {} : { ranges: receipt.ranges }) })); }
}
type MutableState = { -readonly [K in keyof import("./types.js").ResearchProjectState]: import("./types.js").ResearchProjectState[K] };
