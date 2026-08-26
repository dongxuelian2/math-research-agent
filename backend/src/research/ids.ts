import { createHash } from "node:crypto";

export function sha256(body: string | Buffer): string {
	return createHash("sha256").update(body).digest("hex");
}

export function stableId(prefix: string, ...parts: readonly string[]): string {
	return `${prefix}-${sha256(parts.join("\u0000")).slice(0, 24)}`;
}

export function effectIdentity(projectId: string, cycleId: string, logicalJobId: string, effectSlot: string): string {
	return stableId("effect", projectId, cycleId, logicalJobId, effectSlot);
}
