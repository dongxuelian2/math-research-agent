#!/usr/bin/env node

import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const args = parseArgs(process.argv.slice(2));
const benchmarkDirectory = resolve(args.benchmark ?? "benchmarks/first-proof-long-horizon-20260831");
const recoveredDirectory = resolve(args.out ?? join(benchmarkDirectory, "recovered-results"));
const requested = csvValues(args["problem-ids"] ?? "prob-004,prob-006");
const tracks = csvValues(args.tracks ?? "agent");

await mkdir(recoveredDirectory, { recursive: true });
for (const problemId of requested) {

	for (const track of tracks) {
		const sessionDirectory = await bestSession(join(benchmarkDirectory, "sessions", problemId, track));
		if (sessionDirectory === undefined) {
			console.log(JSON.stringify({ event: "skipped", problemId, track, reason: "No session directory" }));
			continue;
		}
		const state = await readJson(join(sessionDirectory, "state.json"));
		if (state === null || !Array.isArray(state.candidates) || state.candidates.length === 0) {
			console.log(JSON.stringify({ event: "skipped", problemId, track, reason: "No persisted candidates", sessionDirectory }));
			continue;
		}
		const correct = state.candidates.filter((candidate) => state.verifications?.[candidate.candidateId]?.verdict === "CORRECT");
		const selected = (correct.at(-1) ?? state.candidates.at(-1));
		if (selected === undefined || typeof selected.content !== "string" || selected.content.length === 0) {
			console.log(JSON.stringify({ event: "skipped", problemId, track, reason: "No non-empty candidate", sessionDirectory }));
			continue;
		}
		const output = {
			schemaVersion: 1,
			status: "RECOVERED",
			recoveryStatus: correct.length > 0 ? "RECOVERED_VERIFIED" : "RECOVERED_UNVERIFIED",
			problemId,
			track,
			output: selected.content,
			outputChars: selected.content.length,
			candidateId: selected.candidateId,
			candidateVerdict: state.verifications?.[selected.candidateId]?.verdict ?? null,
			candidateCount: state.candidates.length,
			verificationCount: Object.keys(state.verifications ?? {}).length,
			stateStatus: state.status,
			step: state.step,
			budget: state.budget ?? null,
			sessionDirectory,
			provenance: "Persisted candidate from a session whose outer benchmark wrapper timed out or failed; not a replacement for the formal result file.",
		};
		const path = join(recoveredDirectory, problemId, `${track}.json`);
		await mkdir(join(recoveredDirectory, problemId), { recursive: true });
		await writeJson(path, output);
		console.log(JSON.stringify({ event: "recovered", problemId, track, recoveryStatus: output.recoveryStatus, candidateId: output.candidateId, candidateVerdict: output.candidateVerdict, outputChars: output.outputChars, sessionDirectory }));
	}
}

async function bestSession(directory) {
	try {
		const entries = await readdir(directory, { withFileTypes: true });
		const candidates = [];
		for (const entry of entries) {
			if (!entry.isDirectory()) continue;
			const path = join(directory, entry.name);
			const state = await readJson(join(path, "state.json"));
			if (state !== null) {
				const correctCount = Array.isArray(state.candidates)
					? state.candidates.filter((candidate) => state.verifications?.[candidate.candidateId]?.verdict === "CORRECT").length
					: 0;
				candidates.push({ path, correctCount, proved: state.status === "PROVED" ? 1 : 0, modified: (await stat(join(path, "state.json"))).mtimeMs });
			}
		}
		return candidates.sort((a, b) => b.correctCount - a.correctCount || b.proved - a.proved || b.modified - a.modified).at(0)?.path;
	} catch { return undefined; }
}

async function readJson(path) { try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; } }
async function writeJson(path, value) { await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8"); }
function csvValues(value) { return String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean); }
function parseArgs(values) { const parsed = {}; for (let index = 0; index < values.length; index += 1) { const value = values[index]; if (!value.startsWith("--")) continue; const key = value.slice(2); const next = values[index + 1]; if (next === undefined || next.startsWith("--")) parsed[key] = "true"; else { parsed[key] = next; index += 1; } } return parsed; }
