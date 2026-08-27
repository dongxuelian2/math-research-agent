import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { corpusPublishingConfigOf, MathAgentConfigService, type MathAgentConfig } from "../config.js";
import { CorpusArchiveCoordinator } from "./corpus-archive.js";
import { ResearchStore } from "./store.js";
import type { ResearchProjectState } from "./types.js";

const repositoryRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const parsed = parseArguments(process.argv.slice(2));
void main(parsed).catch((error: unknown) => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });

interface Arguments { readonly command: string; readonly projectId: string; readonly intentId?: string; readonly configPath: string; readonly dataDirectory?: string; }

async function main(argumentsValue: Arguments): Promise<void> {
	const configService = new MathAgentConfigService(argumentsValue.configPath); await configService.load();
	const configuredData = argumentsValue.dataDirectory ?? process.env.MATH_AGENT_DATA_DIR ?? configService.config.runtime.dataDir, dataDirectory = resolve(argumentsValue.dataDirectory !== undefined || process.env.MATH_AGENT_DATA_DIR !== undefined ? configuredData : join(repositoryRoot, configuredData)), researchStore = new ResearchStore(join(dataDirectory, "research")), coordinator = new CorpusArchiveCoordinator({ researchStore, configForState: (state) => corpusPublishingConfigOf(projectSnapshot(state)) });
	await researchStore.read(argumentsValue.projectId);
	const archive = await coordinator.archiveStore.readOptional(argumentsValue.projectId);
	switch (argumentsValue.command) {
		case "status": print({ projectId: argumentsValue.projectId, status: archive === undefined ? "NOT_ACTIVATED" : "ACTIVE", activatedAt: archive?.activatedAt, counts: archive === undefined ? {} : counts(archive.intents), receipts: archive === undefined ? 0 : Object.keys(archive.receipts).length }); return;
		case "pending": print({ projectId: argumentsValue.projectId, intents: archive === undefined ? [] : Object.values(archive.intents).filter((intent) => intent.status !== "COMPLETE" && intent.status !== "PERMANENT_FAILURE") }); return;
		case "inspect": { const intentId = requireIntent(argumentsValue), intent = archive?.intents[intentId]; if (intent === undefined) throw new Error(`Corpus archive intent not found: ${intentId}`); print({ intent, receipt: archive?.receipts[intentId] }); return; }
		case "retry": { const intentId = requireIntent(argumentsValue); print({ intent: await coordinator.archiveStore.resetForRetry(argumentsValue.projectId, intentId) }); return; }
		case "publish": { const intentId = requireIntent(argumentsValue); print(await coordinator.reconcile(argumentsValue.projectId, intentId)); return; }
		case "reconcile": print(await coordinator.reconcile(argumentsValue.projectId)); return;
		default: throw new Error("Usage: corpus <status|pending|inspect|retry|publish|reconcile> --project <id> [--intent <id>] [--config <path>] [--data-dir <path>]");
	}
}

function parseArguments(values: readonly string[]): Arguments {
	const command = values[0] ?? "status", option = (name: string): string | undefined => { const index = values.indexOf(name); return index < 0 ? undefined : values[index + 1]; }, projectId = option("--project"); if (projectId === undefined || projectId.trim().length === 0) throw new Error("--project is required");
	return { command, projectId, ...(option("--intent") === undefined ? {} : { intentId: option("--intent") }), configPath: resolve(option("--config") ?? process.env.MATH_AGENT_CONFIG ?? join(repositoryRoot, "configs", "math-agent.toml")), ...(option("--data-dir") === undefined ? {} : { dataDirectory: option("--data-dir") }) };
}

function projectSnapshot(state: ResearchProjectState): MathAgentConfig | undefined { const value: unknown = state.effectiveConfig; return typeof value === "object" && value !== null && "version" in value && "corpus" in value ? value as MathAgentConfig : undefined; }
function requireIntent(value: Arguments): string { if (value.intentId === undefined || value.intentId.trim().length === 0) throw new Error("--intent is required for this command"); return value.intentId; }
function counts(intents: Readonly<Record<string, { readonly status: string }>>): Readonly<Record<string, number>> { const result: Record<string, number> = {}; for (const intent of Object.values(intents)) result[intent.status] = (result[intent.status] ?? 0) + 1; return result; }
function print(value: unknown): void { process.stdout.write(`${JSON.stringify(value, null, 2)}\n`); }
