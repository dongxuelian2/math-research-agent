import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { JsonObject } from "../models/json.js";
import type { ReasoningEffort } from "./types.js";
import { asRecord, asString, parseJson } from "./parse.js";
import {
	errorMessage,
	type ModelProvider,
	type ModelStreamEvent,
	type ProviderRequest,
} from "./types.js";

export interface CodexCliRequest {
	readonly model: string;
	readonly prompt: string;
	readonly signal?: AbortSignal;
	readonly responseSchema?: JsonObject;
	readonly reasoningEffort?: ReasoningEffort;
}

export interface CodexCliRunner {
	run(request: CodexCliRequest): AsyncIterable<string>;
}

export interface CodexCliProviderOptions {
	readonly command?: string;
	readonly commandArgs?: readonly string[];
	readonly runner?: CodexCliRunner;
}

export class CodexCliProvider implements ModelProvider {
	readonly id = "openai-codex" as const;
	private readonly runner: CodexCliRunner;

	constructor(options: CodexCliProviderOptions = {}) {
		this.runner = options.runner ?? new NodeCodexCliRunner(options.command ?? "codex", options.commandArgs ?? []);
	}

	async *stream(request: ProviderRequest): AsyncIterable<ModelStreamEvent> {
		try {
			let completed = false;
			for await (const line of this.runner.run({
				model: request.model.model,
				prompt: renderPrompt(request),
				signal: request.signal,
				...(request.responseSchema === undefined ? {} : { responseSchema: request.responseSchema }),
				...(request.model.reasoningEffort === undefined ? {} : { reasoningEffort: request.model.reasoningEffort }),
			})) {
				const payload = parseJson(line);
				if (payload === undefined) {
					if (line.length > 0) {
						yield { type: "text_delta", text: line };
					}
					continue;
				}

				const type = asString(payload.type);
				if (type === "response.output_text.delta") {
					const delta = asString(payload.delta);
					if (delta !== undefined) {
						yield { type: "text_delta", text: delta };
					}
					continue;
				}
				if (type === "item.completed") {
					const item = asRecord(payload.item);
					const itemType = item === undefined ? undefined : asString(item.type);
					if (itemType === "agent_message") {
						const text = item === undefined ? undefined : asString(item.text);
						if (text !== undefined) {
							yield { type: "text_delta", text };
						}
					}
					continue;
				}
				if (type === "response.completed" || type === "turn.completed") {
					if (!completed) {
						completed = true;
						yield { type: "complete", stopReason: "end_turn" };
					}
					continue;
				}
				if (type === "error") {
					yield { type: "failure", error: asString(payload.message) ?? "Codex CLI returned an error" };
					return;
				}
			}

			if (!completed) {
				yield { type: "complete", stopReason: "end_turn" };
			}
		} catch (error) {
			yield { type: "failure", error, retryable: false };
		}
	}
}

class NodeCodexCliRunner implements CodexCliRunner {
	private readonly command: string;
	private readonly commandArgs: readonly string[];

	constructor(command: string, commandArgs: readonly string[]) {
		this.command = command;
		this.commandArgs = commandArgs;
	}

	async *run(request: CodexCliRequest): AsyncIterable<string> {
		const schemaDirectory = request.responseSchema === undefined ? undefined : await mkdtemp(join(tmpdir(), "mrr-codex-schema-"));
		const schemaPath = schemaDirectory === undefined ? undefined : join(schemaDirectory, "response-schema.json");
		if (schemaPath !== undefined) await writeFile(schemaPath, `${JSON.stringify(request.responseSchema)}\n`, "utf8");
		const args = [...this.commandArgs, ...codexCliArguments(request.model, schemaPath, request.reasoningEffort)];
		const child = spawn(this.command, args, { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
		if (child.stdout === null) {
			throw new Error("Codex CLI did not expose stdout");
		}
		if (child.stdin === null) throw new Error("Codex CLI did not expose stdin");
		// A process that exits before consuming input reports EPIPE/EOF here; the
		// authoritative failure is still its captured exit status/stderr below.
		child.stdin.on("error", () => undefined);
		child.stdin.end(request.prompt, "utf8");

		let stderr = "";
		child.stderr?.on("data", (chunk: Buffer) => {
			stderr += chunk.toString();
		});
		const exit = new Promise<number | null>((resolve) => {
			child.once("close", resolve);
		});
		const abort = (): void => {
			child.kill("SIGTERM");
		};
		request.signal?.addEventListener("abort", abort, { once: true });

		try {
			let buffer = "";
			for await (const chunk of child.stdout) {
				buffer += chunk.toString();
				const lines = buffer.split(/\n/);
				buffer = lines.pop() ?? "";
				for (const line of lines) {
					yield line.endsWith("\r") ? line.slice(0, -1) : line;
				}
			}
			if (buffer.length > 0) {
				yield buffer;
			}
			const exitCode = await exit;
			if (exitCode !== 0 && !request.signal?.aborted) {
				throw new Error(`Codex CLI exited with ${String(exitCode)}: ${stderr.trim()}`);
			}
		} finally {
			request.signal?.removeEventListener("abort", abort);
			if (schemaDirectory !== undefined) await rm(schemaDirectory, { recursive: true, force: true });
		}
	}
}

export function codexCliArguments(model: string, schemaPath?: string, reasoningEffort?: ReasoningEffort): readonly string[] {
	return ["exec", "--json", "--model", model, ...(reasoningEffort === undefined ? [] : ["-c", `model_reasoning_effort=${JSON.stringify(reasoningEffort)}`]), ...(schemaPath === undefined ? [] : ["--output-schema", schemaPath]), "-"];
}

function renderPrompt(request: ProviderRequest): string {
		return request.messages
			.map((message) => {
				if (message.role === "user") {
					return `user:\n${message.content}`;
				}
				if (message.role === "tool_result") {
					return `tool(${message.toolName}):\n${message.content}`;
				}
				return `assistant:\n${message.content
					.map((part) => (part.kind === "text" ? part.text : `[tool call ${part.name} ${JSON.stringify(part.arguments)}]`))
					.join("\n")}`;
			})
			.join("\n\n");
}

export function codexErrorMessage(error: unknown): string {
	return errorMessage(error);
}
