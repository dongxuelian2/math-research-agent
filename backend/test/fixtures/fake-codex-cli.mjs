import { readFile } from "node:fs/promises";

let input = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) input += chunk;
const schemaIndex = process.argv.indexOf("--output-schema"), schemaPath = schemaIndex < 0 ? undefined : process.argv[schemaIndex + 1];
const schema = schemaPath === undefined ? undefined : JSON.parse(await readFile(schemaPath, "utf8"));
const confidence = schema?.properties?.dependencies?.items?.properties?.confidence;
if (input.length < 70000 || schemaPath === undefined || JSON.stringify(confidence?.enum) !== JSON.stringify(["EXPLICIT", "INFERRED"])) {
	process.stderr.write("expected a long stdin prompt\n");
	process.exitCode = 2;
} else {
	process.stdout.write(`${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: JSON.stringify({ proposals: [], dependencies: [], warnings: [] }) } })}\n`);
	process.stdout.write(`${JSON.stringify({ type: "turn.completed" })}\n`);
}
