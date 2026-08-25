import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	ConfigConflictError,
	DEFAULT_CONFIG,
	MathAgentConfigService,
	stringifyMathAgentConfig,
} from "../src/index.js";

test("loads one TOML authority, masks credentials, round-trips request parameters, and serializes revisions", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-config-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const path = join(directory, "math-agent.toml");
	const service = new MathAgentConfigService(path);
	await service.load();

	const initial = service.publicSnapshot();
	assert.equal(initial.models.mock?.credentialConfigured, false);
	assert.match(service.tomlText, /\[models\.mock\]/);
	assert.doesNotMatch(service.tomlText, /api_key\s*=/iu);

	const updated = await service.update({
		models: {
			mock: {
				apiKeyEnv: "MATH_AGENT_TEST_SECRET",
				requestParameters: { temperature: 0.2, tags: ["proof", "offline"] },
			},
		},
	}, initial.revision);
	assert.equal(updated.models.mock?.apiKeyEnv, "MATH_AGENT_TEST_SECRET");
	assert.equal(updated.models.mock?.credentialConfigured, false);
	assert.match(service.tomlText, /request_parameters\s*=/u);
	assert.doesNotMatch(service.tomlText, /SUPER_SECRET/iu);
	assert.equal((await readFile(path, "utf8")).includes("MATH_AGENT_TEST_SECRET"), true);

	const reloaded = new MathAgentConfigService(path);
	await reloaded.load();
	assert.deepEqual(reloaded.config.models.mock?.requestParameters, { temperature: 0.2, tags: ["proof", "offline"] });

	const revision = reloaded.revision;
	const writes = await Promise.allSettled([
		reloaded.update({ proof: { historyLimit: 9 } }, revision),
		reloaded.update({ proof: { historyLimit: 10 } }, revision),
	]);
	assert.equal(writes.filter((result) => result.status === "fulfilled").length, 1);
	const rejected = writes.find((result) => result.status === "rejected");
	assert.equal(rejected?.status, "rejected");
	if (rejected?.status === "rejected") assert.equal(rejected.reason instanceof ConfigConflictError, true);
});

test("canonical TOML output never serializes unsupported secret fields", () => {
	const text = stringifyMathAgentConfig(DEFAULT_CONFIG);
	assert.doesNotMatch(text, /api_key\s*=/iu);
	assert.match(text, /proof_api_port\s*=\s*4310/u);
});
