import { execFile } from "node:child_process";
import { access, cp, lstat, mkdir, readFile, readdir, symlink, writeFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import type { MathAgentConfig } from "../config.js";
import type { ProofLeanProjectContext } from "./types.js";

const execFileAsync = promisify(execFile);

export const DEFAULT_LEAN_TOOLCHAIN = "leanprover/lean4:v4.33.1";
export const DEFAULT_MATHLIB_REVISION = "v4.33.1";
export const MATHLIB_GIT_URL = "https://github.com/leanprover-community/mathlib4.git";

export type LeanProjectRequest = {
	readonly packages?: readonly string[];
	readonly imports?: readonly string[];
};

export type LeanProjectDescriptor = ProofLeanProjectContext & {
	readonly sessionId: string;
	readonly projectFile: string;
	readonly importsFile: string;
	readonly status: "READY";
	readonly setupCommand: string;
};

export class LeanProjectSetupError extends Error {
	readonly code = "LEAN_PROJECT_UNAVAILABLE" as const;

	constructor(message: string, readonly causeCode?: string | number) {
		super(message);
		this.name = "LeanProjectSetupError";
	}
}

/**
 * Creates the isolated Lake project used by one proof session. Dependencies
 * are shared read-only from the image/local checkout when possible; generated
 * project metadata and future build products remain session-local.
 */
export class LeanProjectManager {
	private readonly pending = new Map<string, Promise<LeanProjectDescriptor>>();
	private readonly rootDirectory: string;
	private readonly repositoryDirectory: string;

	constructor(options: { readonly rootDirectory: string; readonly repositoryDirectory: string }) {
		this.rootDirectory = resolve(options.rootDirectory);
		this.repositoryDirectory = resolve(options.repositoryDirectory);
	}

	async prepare(sessionId: string, config: MathAgentConfig, request?: LeanProjectRequest): Promise<LeanProjectDescriptor> {
		const previous = this.pending.get(sessionId);
		if (previous !== undefined) return previous;
		const current = this.prepareOnce(sessionId, config, request);
		this.pending.set(sessionId, current);
		try {
			return await current;
		} finally {
			if (this.pending.get(sessionId) === current) this.pending.delete(sessionId);
		}
	}

	private async prepareOnce(sessionId: string, config: MathAgentConfig, request?: LeanProjectRequest): Promise<LeanProjectDescriptor> {
		const packages = normalizePackages(request?.packages ?? config.formalization.packages ?? ["mathlib"]);
		const imports = normalizeImports(request?.imports ?? config.formalization.defaultImports ?? ["Mathlib.Data.Nat.Basic"]);
		const projectBase = resolve(this.rootDirectory, config.formalization.sessionProjectsDir ?? "lean-projects");
		assertInside(this.rootDirectory, projectBase, "formalization.sessionProjectsDir");
		const projectDirectory = resolve(projectBase, sessionId);
		assertInside(projectBase, projectDirectory, "sessionId");
		await mkdir(projectDirectory, { recursive: true, mode: 0o700 });
		const templateDirectory = resolve(this.repositoryDirectory, config.formalization.projectDir ?? "formalization");
		const toolchain = await firstNonEmpty([
			readFirstLine(join(templateDirectory, "lean-toolchain")),
			readFirstLine(join(this.repositoryDirectory, "lean-toolchain")),
			Promise.resolve(DEFAULT_LEAN_TOOLCHAIN),
		]);
		const detectedMathlibRoot = packages.includes("mathlib")
			? await this.findMathlibRoot(config, templateDirectory)
			: undefined;
		const mathlibRevision = await readMathlibRevision(templateDirectory, detectedMathlibRoot);
		const mathlibRoot = detectedMathlibRoot === undefined
			? undefined
			: await materializeMathlibPackage(projectDirectory, detectedMathlibRoot, imports);
		const cachedImports = mathlibRoot !== undefined && await hasCachedImports(mathlibRoot, imports);
		const projectFile = join(projectDirectory, "lakefile.toml");
		const importsFile = join(projectDirectory, "MathResearchAgentSession.lean");
		await writeFile(join(projectDirectory, "lean-toolchain"), `${toolchain}\n`, "utf8");
		await writeFile(projectFile, formatLakefile(sessionId, projectDirectory, packages, mathlibRoot, mathlibRevision), "utf8");
		await writeFile(importsFile, formatImports(imports), "utf8");
		const manifestReady = mathlibRoot === undefined ? false : await writeSessionManifest(projectDirectory, mathlibRoot, sessionId);
		if (mathlibRoot !== undefined) await linkSharedPackages(projectDirectory, mathlibRoot, detectedMathlibRoot);

		const timeoutMs = Math.max(1, config.formalization.setupTimeoutSeconds ?? 180) * 1000;
		// The production image already contains the pinned Mathlib manifest and
		// package checkouts. Updating from a Cloud Run request can contact moving
		// upstream branches and hang behind restricted egress. Copy that manifest
		// into the session first; without it, Lake treats every session as a fresh
		// package and may download thousands of cache files during an HTTP request.
		if (mathlibRoot === undefined || !manifestReady) await runLake(projectDirectory, ["update"], timeoutMs, "Lake dependency setup");
		const buildRequired = packages.length > 0 && (mathlibRoot === undefined || !cachedImports);
		if (buildRequired) await runLake(projectDirectory, ["build", "MathResearchAgentSession"], timeoutMs, "Lean package build");
		await runLake(projectDirectory, ["env", "lean", importsFile], timeoutMs, "Lean import validation");
		const packageSources: Record<string, string> = {};
		if (packages.includes("mathlib")) packageSources.mathlib = mathlibRoot === undefined ? `${MATHLIB_GIT_URL}@${mathlibRevision}` : `path:${mathlibRoot}`;
		return {
			sessionId,
			projectDirectory,
			toolchain,
			packages,
			imports,
			packageSources,
			projectFile,
			importsFile,
			status: "READY",
			setupCommand: `${mathlibRoot === undefined || !manifestReady ? "lake update && " : ""}${buildRequired ? "lake build MathResearchAgentSession && " : ""}lake env lean ${importsFile}`,
		};
	}

	private async findMathlibRoot(config: MathAgentConfig, templateDirectory: string): Promise<string | undefined> {
		const configured = config.formalization.packageRoot ?? process.env.MATH_AGENT_MATHLIB_ROOT;
		if (configured !== undefined && configured.trim().length > 0) {
			const candidate = resolve(this.repositoryDirectory, configured);
			await assertMathlibRoot(candidate, true);
			return candidate;
		}
		const candidates = [
			"/opt/mathlib",
			join(templateDirectory, ".lake", "packages", "mathlib"),
		];
		for (const candidate of candidates) if (await hasMathlibLakefile(candidate)) return resolve(candidate);
		return undefined;
	}
}

export function parseLeanProjectRequest(value: unknown): LeanProjectRequest | undefined {
	if (value === undefined) return undefined;
	if (!isRecord(value)) throw new Error("leanProject must be an object");
	const packages = value.packages === undefined ? undefined : stringArray(value.packages, "leanProject.packages");
	const imports = value.imports === undefined ? undefined : stringArray(value.imports, "leanProject.imports");
	return {
		...(packages === undefined ? {} : { packages: normalizePackages(packages) }),
		...(imports === undefined ? {} : { imports: normalizeImports(imports) }),
	};
}

function formatLakefile(sessionId: string, projectDirectory: string, packages: readonly string[], mathlibRoot: string | undefined, mathlibRevision: string): string {
	const packageName = sessionPackageName(sessionId);
	const lines = [`name = ${quote(packageName)}`, `version = ${quote("0.1.0")}`, `defaultTargets = [${quote("MathResearchAgentSession")}]`, ""];
	if (packages.includes("mathlib")) {
		lines.push("[[require]]", "name = \"mathlib\"");
		if (mathlibRoot === undefined) {
			lines.push(`git = ${quote(MATHLIB_GIT_URL)}`, `rev = ${quote(mathlibRevision)}`);
		} else {
			lines.push(`path = ${quote(toPosix(relative(projectDirectory, mathlibRoot)))}`);
		}
		lines.push("");
	}
	lines.push("[[lean_lib]]", "name = \"MathResearchAgentSession\"");
	return `${lines.join("\n")}\n`;
}

function sessionPackageName(sessionId: string): string {
	return `math_agent_session_${stableSegment(sessionId)}`;
}

/**
 * Seed a session with the immutable package lock from the image checkout.
 * Lake otherwise creates a new manifest and may invoke the Mathlib cache
 * service even when all required package sources are already local.
 */
async function writeSessionManifest(projectDirectory: string, mathlibRoot: string, sessionId: string): Promise<boolean> {
	const source = await readOptional(join(mathlibRoot, "lake-manifest.json"));
	if (source === undefined) return false;
	let parsed: unknown;
	try {
		parsed = JSON.parse(source) as unknown;
	} catch (error) {
		throw new LeanProjectSetupError(`Mathlib lake-manifest.json is invalid: ${error instanceof Error ? error.message : String(error)}`);
	}
	if (!isRecord(parsed) || !Array.isArray(parsed.packages)) throw new LeanProjectSetupError("Mathlib lake-manifest.json does not contain a package list");
	const dependencies = parsed.packages.filter((entry): entry is Record<string, unknown> => isRecord(entry) && entry.name !== "mathlib").map((entry) => ({ ...entry, inherited: true }));
	const packageConfig = await readOptional(join(mathlibRoot, "lakefile.lean")) === undefined ? "lakefile.toml" : "lakefile.lean";
	const manifest = {
		version: parsed.version ?? "1.2.0",
		packagesDir: ".lake/packages",
		packages: [
			{
				type: "path",
				scope: "",
				name: "mathlib",
				manifestFile: "lake-manifest.json",
				inherited: false,
				dir: toPosix(relative(projectDirectory, mathlibRoot)),
				configFile: packageConfig,
			},
			...dependencies,
		],
		name: `«${sessionPackageName(sessionId)}»`,
		lakeDir: ".lake",
		fixedToolchain: false,
	};
	await writeFile(join(projectDirectory, "lake-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
	return true;
}

function formatImports(imports: readonly string[]): string {
	return `${imports.length === 0 ? "-- No package imports were requested for this session." : imports.map((module) => `import ${module}`).join("\n")}\n`;
}

async function readMathlibRevision(...directories: readonly (string | undefined)[]): Promise<string> {
	for (const directory of directories) {
		if (directory === undefined) continue;
		const text = await readOptional(join(directory, "lakefile.toml"));
		const revision = text?.match(/\brev\s*=\s*["']([^"']+)["']/u)?.[1];
		if (revision !== undefined && revision.length > 0) return revision;
	}
	return DEFAULT_MATHLIB_REVISION;
}

async function runLake(projectDirectory: string, args: readonly string[], timeoutMs: number, operation: string): Promise<void> {
	try {
		await execFileAsync("lake", args, {
			cwd: projectDirectory,
			env: { ...process.env, MATHLIB_NO_CACHE_ON_UPDATE: "1" },
			timeout: timeoutMs,
			maxBuffer: 8 * 1024 * 1024,
			encoding: "utf8",
		});
	} catch (error) {
		const failure = error as { readonly stdout?: string; readonly stderr?: string; readonly message?: string; readonly code?: string | number; readonly killed?: boolean };
		const output = [failure.stdout, failure.stderr, failure.message].filter((part): part is string => typeof part === "string" && part.trim().length > 0).join("\n").trim();
		const suffix = output.length > 6000 ? `\n[output truncated]\n${output.slice(-6000)}` : output;
		throw new LeanProjectSetupError(`${operation} failed: ${suffix || "lake returned a non-zero exit status"}`, failure.code);
	}
}

async function assertMathlibRoot(directory: string, explicit: boolean): Promise<void> {
	if (await hasMathlibLakefile(directory)) return;
	throw new LeanProjectSetupError(`${explicit ? "Configured" : "Detected"} Mathlib package root is unavailable or has no Lakefile: ${directory}`);
}

async function hasMathlibLakefile(directory: string): Promise<boolean> {
	try {
		await Promise.any([access(join(directory, "lakefile.toml")), access(join(directory, "lakefile.lean"))]);
		return true;
	} catch {
		return false;
	}
}

async function materializeMathlibPackage(projectDirectory: string, detectedMathlibRoot: string, imports: readonly string[]): Promise<string> {
	if (await hasCachedImports(detectedMathlibRoot, imports)) return detectedMathlibRoot;
	const writableRoot = join(projectDirectory, ".packages", "mathlib");
	await cp(detectedMathlibRoot, writableRoot, {
		recursive: true,
		filter: (source) => {
			const remainder = relative(detectedMathlibRoot, source);
			return remainder === "" || (remainder !== ".git" && remainder !== ".lake" && !remainder.startsWith(`.git${sep}`) && !remainder.startsWith(`.lake${sep}`));
		},
	});
	await linkPackageDependencies(writableRoot, detectedMathlibRoot);
	return writableRoot;
}

async function hasCachedImports(mathlibRoot: string, imports: readonly string[]): Promise<boolean> {
	const buildRoot = join(mathlibRoot, ".lake", "build", "lib", "lean");
	for (const module of imports) {
		try {
			await access(join(buildRoot, ...module.split(".")) + ".olean");
		} catch {
			return false;
		}
	}
	return true;
}

/** Reuse immutable package sources/builds while keeping the session manifest local. */
async function linkSharedPackages(projectDirectory: string, mathlibRoot: string, detectedMathlibRoot: string = mathlibRoot): Promise<void> {
	const sharedDirectory = packageDependenciesDirectory(detectedMathlibRoot);
	const sessionDirectory = join(projectDirectory, ".lake", "packages");
	await mkdir(sessionDirectory, { recursive: true });
	const names = new Set<string>(["mathlib"]);
	try {
		for (const entry of await readdir(sharedDirectory, { withFileTypes: true })) if (entry.isDirectory() && entry.name !== ".git") names.add(entry.name);
	} catch (error) {
		if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") throw error;
	}
	for (const name of names) {
		const source = name === "mathlib" ? mathlibRoot : join(sharedDirectory, name);
		if (!(await hasMathlibLakefile(source)) && name === "mathlib") continue;
		const target = join(sessionDirectory, name);
		try {
			await lstat(target);
			continue;
		} catch (error) {
			if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") throw error;
		}
		try {
			await symlink(source, target, "dir");
		} catch (error) {
			if ((error as NodeJS.ErrnoException | undefined)?.code !== "EEXIST") throw error;
		}
	}
}

async function linkPackageDependencies(packageDirectory: string, detectedMathlibRoot: string): Promise<void> {
	const packageDirectoryTarget = join(packageDirectory, ".lake", "packages");
	const sharedDirectory = packageDependenciesDirectory(detectedMathlibRoot);
	await mkdir(packageDirectoryTarget, { recursive: true });
	try {
		for (const entry of await readdir(sharedDirectory, { withFileTypes: true })) {
			if (!entry.isDirectory() || entry.name === "mathlib") continue;
			const target = join(packageDirectoryTarget, entry.name);
			try {
				await lstat(target);
				continue;
			} catch (error) {
				if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") throw error;
			}
			await symlink(join(sharedDirectory, entry.name), target, "dir");
		}
	} catch (error) {
		if ((error as NodeJS.ErrnoException | undefined)?.code !== "ENOENT") throw error;
	}
}

function packageDependenciesDirectory(mathlibRoot: string): string {
	return mathlibRoot.includes(`${sep}.lake${sep}packages${sep}mathlib`)
		? resolve(mathlibRoot, "..")
		: join(mathlibRoot, ".lake", "packages");
}

async function readOptional(path: string): Promise<string | undefined> {
	try {
		return await readFile(path, "utf8");
	} catch (error) {
		if ((error as NodeJS.ErrnoException | undefined)?.code === "ENOENT") return undefined;
		throw error;
	}
}

async function readFirstLine(path: string): Promise<string | undefined> {
	const text = await readOptional(path);
	return text?.trim().split(/\r?\n/u)[0]?.trim() || undefined;
}

async function firstNonEmpty(values: readonly (Promise<string | undefined> | string)[]): Promise<string> {
	for (const value of values) {
		const resolved = typeof value === "string" ? value : await value;
		if (resolved !== undefined && resolved.trim().length > 0) return resolved.trim();
	}
	return DEFAULT_LEAN_TOOLCHAIN;
}

function normalizePackages(value: readonly string[]): readonly string[] {
	const normalized = [...new Set(value.map((item) => item.trim().toLocaleLowerCase()))];
	const invalid = normalized.filter((item) => item !== "mathlib");
	if (invalid.length > 0) throw new LeanProjectSetupError(`Unsupported Lean package(s): ${invalid.join(", ")}`);
	return normalized;
}

function normalizeImports(value: readonly string[]): readonly string[] {
	const normalized = [...new Set(value.map((item) => item.trim()))];
	const invalid = normalized.filter((item) => !/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/u.test(item));
	if (invalid.length > 0) throw new LeanProjectSetupError(`Invalid Lean import module(s): ${invalid.join(", ")}`);
	return normalized;
}

function stringArray(value: unknown, name: string): readonly string[] {
	if (!Array.isArray(value) || !value.every((item) => typeof item === "string" && item.trim().length > 0)) throw new Error(`${name} must be an array of non-empty strings`);
	return value;
}

function stableSegment(value: string): string {
	return value.replace(/[^A-Za-z0-9_-]+/gu, "-").replace(/^-+|-+$/gu, "").slice(0, 60) || "session";
}

function assertInside(base: string, candidate: string, name: string): void {
	const remainder = relative(resolve(base), resolve(candidate));
	if (remainder === "" || remainder === "." || remainder === ".." || remainder.startsWith(`..${sep}`) || isAbsolute(remainder)) throw new LeanProjectSetupError(`${name} must remain inside ${resolve(base)}`);
}

function toPosix(value: string): string {
	return value.split(sep).join("/");
}

function quote(value: string): string {
	return JSON.stringify(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
