import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { Session, createAssistantMessage, createToolResult, createUserMessage } from "../src/index.js";

test("resumes, branches, and preserves append-only JSONL entries", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-session-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({
		projectId: "session-project",
		cwd: directory,
		directory,
		metadata: { purpose: "test" },
	});
	const user = createUserMessage("question");
	const assistant = createAssistantMessage([{ kind: "text", text: "answer" }], {
		provider: "mock",
		model: "mock-model",
		stopReason: "end_turn",
	});
	const tool = createToolResult({
		toolCallId: "call",
		toolName: "read",
		content: "ok",
		details: { path: "x" },
		isError: false,
	});
	await session.appendMessage(user);
	await session.appendMessage(assistant);
	await session.appendToolResult(tool);
	await session.updateMetadata({ version: 1 });

	const resumed = await Session.resume(session.filePath);
	assert.equal(resumed.sessionId, resumed.projectId);
	assert.deepEqual(resumed.metadata, { purpose: "test", version: 1 });
	assert.deepEqual(resumed.contextProjection().map((message) => message.role), ["user", "assistant", "tool_result"]);

	const branch = await resumed.fork();
	assert.notEqual(branch.sessionId, resumed.sessionId);
	assert.equal(branch.entries.some((entry) => entry.kind === "branch"), true);
	assert.deepEqual(branch.contextProjection().map((message) => message.role), ["user", "assistant", "tool_result"]);
	await branch.appendMessage(createUserMessage("branch question"));
	assert.equal((await Session.resume(resumed.filePath)).contextProjection().length, 3);
	assert.equal((await Session.resume(branch.filePath)).contextProjection().length, 4);

	const lines = (await readFile(resumed.filePath, "utf8")).trim().split("\n");
	assert.equal(lines.length, resumed.entries.length);
	assert.equal(JSON.parse(lines[0] ?? "{}").kind, "session");
});
