import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createBashTool, createEditTool, createReadTool, createWriteTool } from "../src/index.js";

test("read/write/edit/bash form a usable offline tool set", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-tools-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const write = createWriteTool({ cwd: directory });
	const read = createReadTool({ cwd: directory });
	const edit = createEditTool({ cwd: directory });
	const bash = createBashTool({ cwd: directory });

	await write.execute(write.validate({ path: "nested/file.txt", content: "one\ntwo\n" }));
	const readDetails = await read.execute(read.validate({ path: "nested/file.txt" }));
	assert.equal(readDetails.content, "one\ntwo\n");
	await edit.execute(edit.validate({ path: "nested/file.txt", oldText: "two", newText: "updated" }));
	assert.equal(await readFile(join(directory, "nested/file.txt"), "utf8"), "one\nupdated\n");

	const bashDetails = await bash.execute(
		bash.validate({ command: `${process.execPath} -e 'process.stdout.write("ok")'` }),
	);
	assert.equal(bashDetails.exitCode, 0);
	assert.equal(bashDetails.stdout, "ok");
	assert.equal(bashDetails.aborted, false);

	assert.throws(() => write.validate({ path: "missing-content" }), /content must be a string/);
});
