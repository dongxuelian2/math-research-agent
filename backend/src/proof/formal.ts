import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import type { ProofFormalVerifier, FormalVerificationResult } from "./types.js";

const execFileAsync = promisify(execFile);

export type CommandProofFormalVerifierOptions = {
	readonly projectDirectory: string;
	readonly command?: string;
	readonly args?: readonly string[];
	readonly timeoutMs?: number;
};

/**
 * Lean-compatible formal gate. The default command is exactly the command
 * used by OpenProver (`lake env lean <file>`), while the adapter keeps the
 * workflow testable with a fake verifier in environments without Lean.
 */
export class CommandProofFormalVerifier implements ProofFormalVerifier {
	private readonly projectDirectory: string;
	private readonly command: string;
	private readonly args: readonly string[];
	private readonly timeoutMs: number;

	constructor(options: CommandProofFormalVerifierOptions) {
		this.projectDirectory = options.projectDirectory;
		this.command = options.command ?? "lake";
		this.args = options.args ?? ["env", "lean"];
		this.timeoutMs = options.timeoutMs ?? 300_000;
	}

	async verify(content: string, context: Parameters<ProofFormalVerifier["verify"]>[1], signal?: AbortSignal): Promise<FormalVerificationResult> {
		if (signal?.aborted) {
			return { ok: false, feedback: "Formal verification was aborted.", failureKind: "ABORTED" };
		}
		const unsafeReason = checkUnsafeLeanSource(content);
		if (unsafeReason !== undefined) {
			return { ok: false, feedback: unsafeReason, failureKind: "REJECTED", command: `${this.command} ${this.args.join(" ")}` };
		}
		const theoremReason = context.theoremText === undefined ? undefined : checkTheoremPreserved(context.theoremText, content);
		if (theoremReason !== undefined) {
			return { ok: false, feedback: theoremReason, failureKind: "REJECTED", command: `${this.command} ${this.args.join(" ")}` };
		}
		const workDirectory = context.workDirectory ?? this.projectDirectory;
		await mkdir(workDirectory, { recursive: true });
		const file = join(workDirectory, `proof-attempt-${randomUUID().slice(0, 8)}.lean`);
		await writeFile(file, content, "utf8");
		const commandArgs = [...this.args, file];
		try {
			const result = await execFileAsync(this.command, commandArgs, {
				cwd: this.projectDirectory,
				timeout: this.timeoutMs,
				signal,
				maxBuffer: 4 * 1024 * 1024,
				encoding: "utf8",
			});
			const feedback = [result.stdout, result.stderr].filter((part) => part.length > 0).join("\n").trim();
			return {
				ok: true,
				feedback: feedback.length > 0 ? feedback : "Lean verification passed.",
				command: [this.command, ...commandArgs].join(" "),
				artifactPath: file,
			};
		} catch (error) {
			const failure = error as { readonly stdout?: string; readonly stderr?: string; readonly message?: string; readonly code?: string | number; readonly killed?: boolean; readonly name?: string };
			const feedback = [failure.stdout, failure.stderr, failure.message].filter((part): part is string => typeof part === "string" && part.length > 0).join("\n").trim();
			const failureKind = signal?.aborted || failure.name === "AbortError"
				? "ABORTED"
				: failure.code === "ENOENT"
					? "UNAVAILABLE"
					: failure.killed === true || failure.code === "ETIMEDOUT"
						? "TIMEOUT"
						: "REJECTED";
			return {
				ok: false,
				feedback: feedback || "Lean verification failed.",
				failureKind,
				command: [this.command, ...commandArgs].join(" "),
				artifactPath: file,
			};
		}
	}
}

/** Preserve the theorem declaration while replacing only `sorry` holes. */
export function checkTheoremPreserved(theoremText: string, proofText: string): string | undefined {
	const theorem = normalizeLean(stripLeanComments(theoremText));
	const proof = normalizeLean(stripLeanComments(proofText));
	const declaration = theorem.match(/\b(?:theorem|lemma|def)\s/);
	if (declaration === null) return "Configured Lean target must contain a theorem, lemma, or def declaration.";
	const relevant = theorem.slice(declaration.index ?? 0);
	const fragments = relevant.split(/\bsorry\b/);
	if (fragments.length === 1) {
		return proof === theorem ? undefined : "Submitted Lean source does not exactly match the configured completed target.";
	}
	let position = 0;
	for (let index = 0; index < fragments.length; index += 1) {
		const fragment = fragments[index] ?? "";
		if (fragment.length === 0) continue;
		const found = proof.indexOf(fragment, position);
		if (found < 0) {
			return `Submitted Lean proof does not preserve theorem declaration fragment ${index + 1}.`;
		}
		if (index > 0 && /\bsorry\b/.test(proof.slice(position, found))) {
			return "Submitted Lean proof still contains `sorry`.";
		}
		position = found + fragment.length;
	}
	if (/\bsorry\b/.test(proof.slice(position))) {
		return "Submitted Lean proof still contains `sorry`.";
	}
	return undefined;
}

/** Reject proof-local escape hatches that Lean accepts as declarations or warnings. */
export function checkUnsafeLeanSource(proofText: string): string | undefined {
	const proof = stripLeanComments(proofText);
	if (/\b(?:sorry|admit)\b/u.test(proof)) return "Submitted Lean source contains an unresolved `sorry` or `admit`.";
	if (/^\s*(?:unsafe\s+)?(?:axiom|constant|opaque)\b/mu.test(proof)) {
		return "Submitted Lean source introduces an axiom, constant, or opaque declaration; formal proofs must not add unverified assumptions.";
	}
	return undefined;
}

function stripLeanComments(value: string): string {
	return value.replace(/\/\-[\s\S]*?\-\//g, "").replace(/--[^\n]*/g, "");
}

function normalizeLean(value: string): string {
	return value.replace(/\s+/g, " ").trim();
}
