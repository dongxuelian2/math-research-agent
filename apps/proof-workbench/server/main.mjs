import { createReadStream } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createConfiguredProofRoleFactory, MathAgentConfigService, ProofApiServer } from "../../../backend/dist/src/index.js";

const appRoot = resolve(fileURLToPath(new URL("../", import.meta.url)));
const repositoryRoot = resolve(appRoot, "../..");
const webRoot = resolve(appRoot, "web");
if ((process.env.GOOGLE_APPLICATION_CREDENTIALS ?? "").trim().length === 0) {
  const credentialFile = await discoverServiceAccount(repositoryRoot);
  if (credentialFile !== undefined) process.env.GOOGLE_APPLICATION_CREDENTIALS = credentialFile;
}
const configPath = resolve(process.env.MATH_AGENT_CONFIG ?? resolve(repositoryRoot, "configs/math-agent.toml"));
const args = new Set(process.argv.slice(2));

const configService = new MathAgentConfigService(configPath);
await configService.load();
const dataDirectory = resolve(process.env.MATH_AGENT_DATA_DIR ?? resolve(repositoryRoot, configService.config.runtime.dataDir));
const proofApi = new ProofApiServer({
  rootDirectory: dataDirectory,
  configService,
  createRoles: createConfiguredProofRoleFactory({ config: configService, rootDirectory: dataDirectory }),
  defaultMode: configService.config.proof.defaultMode,
  defaultMaxWorkers: configService.config.proof.maxWorkers,
  defaultMaxSteps: configService.config.proof.maxSteps,
});

const apiHost = process.env.MATH_AGENT_PROOF_API_HOST ?? configService.config.runtime.host;
const apiPort = parsePort(process.env.MATH_AGENT_PROOF_API_PORT, configService.config.runtime.proofApiPort);
const apiUrl = await proofApi.start({ host: apiHost, port: apiPort });
let webServer;
let shuttingDown = false;

if (!args.has("--api-only")) {
  const webHost = process.env.MATH_AGENT_WEB_HOST ?? configService.config.runtime.host;
  const webPort = parsePort(process.env.MATH_AGENT_WEB_PORT, configService.config.runtime.webPort);
  webServer = createServer((request, response) => { void serveWeb(request, response, apiUrl); });
  await listen(webServer, webHost, webPort);
  console.log(`math-proof: GUI ready at http://${webHost}:${webPort}`);
}
console.log(`math-proof: API ready at ${apiUrl}`);

async function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  await closeServer(webServer);
  await proofApi.stop();
  if (exitCode !== 0) process.exitCode = exitCode;
}

process.once("SIGINT", () => { void shutdown(); });
process.once("SIGTERM", () => { void shutdown(); });

async function serveWeb(request, response, apiOrigin) {
  try {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    if (url.pathname === "/runtime-config.js") {
      response.statusCode = 200;
      response.setHeader("Content-Type", "application/javascript; charset=utf-8");
      response.setHeader("Cache-Control", "no-store");
      response.end(`window.__MATH_PROOF_RUNTIME__ = ${JSON.stringify({ apiOrigin })};\n`);
      return;
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.statusCode = 405;
      response.setHeader("Allow", "GET, HEAD");
      response.end("Method Not Allowed");
      return;
    }
    const requested = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
    const candidate = resolve(webRoot, `.${requested}`);
    const relativePath = relative(webRoot, candidate);
    if (relativePath.startsWith(`..${sep}`) || relativePath === ".." || relativePath.includes(`${sep}..${sep}`)) {
      response.statusCode = 403;
      response.end("Forbidden");
      return;
    }
    let filePath = candidate;
    try {
      const info = await stat(filePath);
      if (info.isDirectory()) filePath = join(filePath, "index.html");
    } catch {
      filePath = join(webRoot, "index.html");
    }
    const info = await stat(filePath);
    if (!info.isFile()) throw new Error("Not a file");
    response.statusCode = 200;
    response.setHeader("Content-Type", contentType(extname(filePath)));
    response.setHeader("Cache-Control", "no-cache");
    response.setHeader("Content-Length", info.size);
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    createReadStream(filePath).on("error", () => response.destroy()).pipe(response);
  } catch {
    if (!response.headersSent) {
      response.statusCode = 404;
      response.end("Not Found");
    } else response.destroy();
  }
}

function contentType(extension) {
  return {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
  }[extension] ?? "application/octet-stream";
}

function parsePort(value, fallback) {
  if (value === undefined || value.length === 0) return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) throw new Error(`Invalid port: ${value}`);
  return parsed;
}

function listen(server, host, port) {
  return new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.off("error", reject);
      resolvePromise();
    });
  });
}

function closeServer(server) {
  if (server === undefined) return Promise.resolve();
  return new Promise((resolvePromise, reject) => {
    server.close((error) => error === undefined ? resolvePromise() : reject(error));
  });
}

async function discoverServiceAccount(rootDirectory) {
  const entries = (await readdir(rootDirectory, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && extname(entry.name) === ".json" && entry.name !== "package.json")
    .sort((left, right) => left.name.localeCompare(right.name));
  const candidates = [];
  for (const entry of entries) {
    try {
      const value = JSON.parse(await readFile(join(rootDirectory, entry.name), "utf8"));
      if (value?.type === "service_account" && typeof value.client_email === "string" && typeof value.private_key === "string" && typeof value.project_id === "string") {
        candidates.push(join(rootDirectory, entry.name));
      }
    } catch {
      // Non-credential JSON files are not candidates.
    }
  }
  return candidates.length === 1 ? candidates[0] : undefined;
}
