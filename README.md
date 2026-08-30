<div align="center">
  <img src="docs/assets/math-research-agent-logo.png" alt="Math Research Agent" width="680" />
  <p><strong>Proof-oriented execution for difficult mathematics.</strong><br />A persistent runtime for planning, verifying, formalizing, and auditing mathematical work.</p>
  <p>
    <a href="#quick-start">Quick start</a> ·
    <a href="#architecture">Architecture</a> ·
    <a href="#api">API</a> ·
    <a href="#development">Development</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white" alt="Node.js 22+" />
    <img src="https://img.shields.io/badge/pnpm-11%2B-F69220?logo=pnpm&logoColor=white" alt="pnpm 11+" />
    <a href="LICENSE">
      <img src="https://img.shields.io/badge/license-MIT-1f883d" alt="MIT License" />
    </a>
  </p>
</div>

> Math Research Agent treats a theorem as a durable execution job rather than a single model request. Runtime-owned state, independent verification, formal process gates, and evidence receipts determine what can be accepted.

## Table of contents

<details>
<summary>Expand</summary>

- [Overview](#overview)
- [Hackathon submission](#hackathon-submission)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Core capabilities](#core-capabilities)
- [Proof execution model](#proof-execution-model)
- [Durability and evidence](#durability-and-evidence)
- [Runtime states](#runtime-states)
- [API](#api)
- [Formal verification](#formal-verification)
- [Long-running research](#long-running-research)
- [Configuration](#configuration)
- [Cloud Run](#cloud-run)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Documentation](#documentation)
- [Design invariants](#design-invariants)
- [License](#license)

</details>

## Overview

Math Research Agent is a TypeScript system for persistent mathematical proof execution. It accepts a theorem, creates a session, plans work, dispatches focused tasks, records intermediate artifacts, verifies candidate arguments independently, and exposes the result through an HTTP/SSE API.

The project has two execution scales:

| Scale | Runtime | Purpose |
| --- | --- | --- |
| Tactical | <code>ProofRuntime</code> / <code>ProofWorkflow</code> | Solve and verify a single proof obligation |
| Strategic | <code>ResearchRuntime</code> | Maintain a long-running research project with claims, evidence, routes, checkpoints, and root closure |

The model supplies mathematical work and planning proposals. The runtime owns task dependencies, persistence, recovery, provider boundaries, evidence metadata, and submission gates.

## Hackathon submission

<p align="center">
  <a href="https://allthingsagentichackathon.devpost.com/">
    <img src="docs/assets/all-things-agentic-hackathon-logo.png" alt="All Things Agentic Hackathon" width="220" />
  </a>
</p>

This project is submitted as an entry to the [All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/). The submission uses Gemini through Google Vertex AI and includes a Google Cloud Run deployment profile for the hosted demonstration.

## Quick start

### Requirements

- Node.js 22 or later
- pnpm 11 or later
- Google Cloud CLI and Vertex AI access for real model calls
- Lean 4, Lake, and the pinned Mathlib environment for local formalization

### Install and run

~~~bash
git clone https://github.com/dongxuelian2/math-research-agent.git
cd math-research-agent

bash scripts/install.sh
bash scripts/start.sh
~~~

The installer runs a frozen pnpm install and builds the TypeScript backend. Open:

- Workbench: [http://127.0.0.1:3080](http://127.0.0.1:3080)
- Proof API: [http://127.0.0.1:43100](http://127.0.0.1:43100)
- Health: [http://127.0.0.1:43100/health](http://127.0.0.1:43100/health)

To run only the proof API:

~~~bash
pnpm start:api
~~~

For Windows, use <code>scripts/install.ps1</code> and <code>scripts/start.ps1</code>.

### Local credentials

The default profile uses Google Vertex AI through <code>@google/genai</code>. Authenticate with Application Default Credentials:

~~~bash
gcloud auth application-default login
~~~

Credential values are read at runtime. Configuration stores environment-variable names, not credential JSON or private-key material.

## Architecture

<p align="center">
  <a href="docs/architecture/proof-ai-orchestration-architecture.pdf">
    <img src="docs/architecture/proof-ai-orchestration-architecture.png" alt="Proof and AI orchestration architecture" width="100%" />
  </a>
</p>

<p align="center">
  <sub>Click the diagram to open the original PDF reference.</sub>
</p>

The architecture is organized around six boundaries:

| Layer | Main components | Responsibility |
| --- | --- | --- |
| **Experience** | Proof Workbench, CLI/API clients, local mode, Cloud Run mode | Accept theorem requests and present proof state |
| **API control plane** | Proof API Server, session lifecycle, event stream, ConfigService | Expose stable HTTP resources and configuration snapshots |
| **Core execution** | ResearchRuntime, ProofRuntime, dynamic planner, workers, verifier pool, formalizer, state reducer | Execute proof and research state transitions |
| **Tool plane** | Role Factory, Agent Core, scoped tools, Provider Registry | Construct configured roles and constrain model/tool access |
| **External systems** | OpenAlex, Git remote, Cloud Identity | Literature, optional corpus publication, and cloud authentication |
| **Durable data and trust** | Session Store, Run Workspace, Research Store, evidence receipts, corpus archive | Persist state, artifacts, verification evidence, and controlled projections |

The original reference is available as [PNG](docs/architecture/proof-ai-orchestration-architecture.png) and [PDF](docs/architecture/proof-ai-orchestration-architecture.pdf).

## Core capabilities

| Capability | Implementation |
| --- | --- |
| Dynamic task planning | Planner action protocol with <code>spawn</code>, dependency edges, success criteria, and continuation tasks |
| Ready-frontier execution | Only tasks whose dependencies are complete are dispatched; independent tasks can run concurrently up to <code>maxWorkers</code> |
| Independent verification | Worker results are inspected by a separate verifier pool before candidate submission |
| Proof repository | Candidates, merged feedback, failed routes, and whiteboard state remain available across planning rounds |
| Durable recovery | Sessions, runs, configuration snapshots, step state, and partial outputs survive process restarts |
| Formal process gate | Lean source is checked by the configured <code>lake env lean</code> process before formal acceptance |
| Evidence-aware research | Research tools record artifact reads, searches, references, and trust receipts |
| Provider isolation | Provider adapters are selected by configuration; role definitions do not grant arbitrary tool permissions |
| HTTP/SSE integration | The GUI uses the public API and event stream without importing proof runtime internals |
| Controlled corpus projection | Canonical corpus publishing is a separate, opt-in outbox and reconciliation path |

## Proof execution model

### End-to-end lifecycle

~~~mermaid
flowchart LR
  theorem["Theorem"] --> session["Session"]
  session --> api["Proof API"]
  api --> planner["Planner"]
  planner --> frontier["Ready frontier"]
  frontier --> workers["Parallel workers"]
  workers --> verifiers["Independent verifiers"]
  verifiers --> candidate["Verified candidate"]
  candidate --> submission["Submission gate"]
  submission --> result["Proof result"]
  submission -. "prove_and_formalize" .-> formalizer["Formalizer"]
  formalizer --> lean["lake env lean"]
  lean --> result
  result --> artifacts["Durable artifacts"]
  artifacts --> planner
~~~

### Planner and task graph

The default workflow mode is <code>dynamic</code>. A planner round receives the theorem, whiteboard, repository index, previous outputs, failed routes, budget, and current task graph. It returns typed actions that the runtime validates before execution.

Each task may declare:

- <code>dependsOn</code> — predecessor task IDs that must complete first;
- <code>successCriteria</code> — the local result required from the task;
- <code>continuationOf</code> — the previous task when work is partial or retryable;
- a scope and contribution kind — for example a local lemma, route analysis, or synthesis task.

The runtime computes the ready frontier, runs independent tasks in bounded batches, sends merged worker/verifier feedback into the next planner round, and persists every transition. <code>maxWorkers</code> limits concurrency; it does not decide how a theorem should be decomposed.

<code>legacy</code> mode remains available for compatibility with the fixed Planner/Worker/Verifier workflow.

### Role separation

| Role | Responsibility |
| --- | --- |
| Planner | Select the next proof actions and task graph |
| Worker | Produce a focused mathematical contribution or candidate proof |
| Verifier | Independently check the worker result and report a verdict |
| Synthesizer | Assemble accepted contributions into a final research or proof artifact |
| Formalizer | Produce or repair complete Lean source |
| Research Director | Select strategic research actions and target obligations |
| Corpus Bootstrapper | Import configured mathematical corpus material |
| Secondary Auditor | Re-check final research evidence and closure conditions |

The role factory resolves model profiles and tools from trusted configuration. A task description can state what an agent is for, but it does not itself grant access to tools.

## Durability and evidence

### Proof-run workspace

Each proof run has its own workspace under <code>.math-agent/proof-runs/</code>:

~~~text
.math-agent/
├── sessions/
│   └── <session-id>.jsonl
├── proof-runs/
│   └── <session-id>/<run-id>/
│       ├── THEOREM.md
│       ├── THEOREM.lean
│       ├── WHITEBOARD.md
│       ├── run_config.json
│       ├── state.json
│       ├── PROOF.md
│       ├── PROOF.lean
│       ├── repo/
│       └── steps/
│           └── step_NNN/
│               ├── planner_context.json
│               ├── planner_response.txt
│               ├── planner_plan.json
│               ├── actions.json
│               ├── worker_*_output.md
│               ├── verifier_*.json
│               └── step_status.json
└── research/
    └── <project-id>/
        ├── state.json
        └── artifacts/
~~~

The session JSONL contains typed lifecycle entries. The run workspace contains the full theorem, plans, prompts, outputs, verifier records, whiteboard, repository, and formal attempts needed for recovery and audit.

### Evidence and authority

Research artifacts carry content hashes, provenance, references, and authority classifications. Tool use is recorded separately from the mathematical result. A worker may discover or read evidence, but a claim becomes authoritative only through the configured verification, reduction, and closure path.

The corpus archive is a projection layer. Raw planner, worker, verifier, scratch, candidate, and audit outputs are not published directly to the canonical Git corpus.

## Runtime states

| State | Meaning |
| --- | --- |
| <code>CANDIDATE_READY</code> | A candidate passed independent verification and is waiting for the submission gate |
| <code>PARTIAL</code> | A budget or step limit was reached while resumable work remains |
| <code>PROVED</code> | The configured submission path passed; formalization also passed when required by the selected mode |
| <code>FAILED</code> | The workflow ended without an accepted route |
| <code>BLOCKED_FORMAL</code> | The formalization project or Lean process is unavailable |
| <code>BLOCKED_PROVIDER</code> | A model, literature, or remote tool provider is unavailable |
| <code>CANCELLED</code> | The caller cancelled the run |

<code>CANDIDATE_READY</code> is not a final proof state. A model label, a generated file, or a successful worker response is not sufficient for <code>PROVED</code>.

## API

### Health and configuration

| Method | Endpoint | Purpose |
| --- | --- | --- |
| <code>GET</code> | <code>/health</code> | Runtime and deployment health evidence |
| <code>GET</code> | <code>/v1/config</code> | Read the effective configuration |
| <code>GET</code> | <code>/v1/config/document</code> | Read the editable TOML document |
| <code>PUT</code> | <code>/v1/config</code> | Apply a revision-checked configuration update |
| <code>GET</code> | <code>/v1/config/models</code> | Read the redacted model catalog |

### Proof resources

| Method | Endpoint | Purpose |
| --- | --- | --- |
| <code>POST</code> | <code>/v1/sessions</code> | Create a session |
| <code>GET</code> | <code>/v1/sessions</code> | List sessions |
| <code>GET</code> | <code>/v1/sessions/:sessionId</code> | Read session state |
| <code>POST</code> | <code>/v1/sessions/:sessionId/theorem</code> | Submit a theorem |
| <code>POST</code> | <code>/v1/sessions/:sessionId/proof-runs</code> | Start a proof run |
| <code>GET</code> | <code>/v1/sessions/:sessionId/proof-runs</code> | List runs for a session |
| <code>GET</code> | <code>/v1/sessions/:sessionId/proof-runs/:runId</code> | Read run state |
| <code>GET</code> | <code>/v1/sessions/:sessionId/proof-runs/:runId/events</code> | Stream typed events over SSE |
| <code>GET</code> | <code>/v1/sessions/:sessionId/proof-runs/:runId/result</code> | Read the final result |
| <code>POST</code> | <code>/v1/sessions/:sessionId/proof-runs/:runId/cancel</code> | Request cancellation |

### Minimal API flow

~~~bash
API_URL=http://127.0.0.1:43100
SESSION_ID=demo-session

curl -fsS -X POST "$API_URL/v1/sessions" \
  -H 'content-type: application/json' \
  -d '{"sessionId":"demo-session"}'

curl -fsS -X POST "$API_URL/v1/sessions/$SESSION_ID/theorem" \
  -H 'content-type: application/json' \
  -d '{"theorem":"For every integer n >= 1, 1 + 3 + ... + (2n - 1) = n^2."}'

curl -fsS -X POST "$API_URL/v1/sessions/$SESSION_ID/proof-runs" \
  -H 'content-type: application/json' \
  -d '{"mode":"prove"}'
~~~

Copy the returned <code>runId</code> and inspect the stream, state, and result:

~~~bash
RUN_ID=<run-id>

curl -N "$API_URL/v1/sessions/$SESSION_ID/proof-runs/$RUN_ID/events"
curl -fsS "$API_URL/v1/sessions/$SESSION_ID/proof-runs/$RUN_ID"
curl -fsS "$API_URL/v1/sessions/$SESSION_ID/proof-runs/$RUN_ID/result"
~~~

## Formal verification

### Proof-run modes

| Mode | Required output |
| --- | --- |
| <code>prove</code> | A candidate that passes the informal submission path |
| <code>formalize_only</code> | A process-verified <code>PROOF.lean</code> |
| <code>prove_and_formalize</code> | An accepted informal proof and a process-verified Lean proof |

The full local profile enables formalization. A Formalizer may generate a Lean declaration and proof, or preserve a caller-provided exact declaration and fill its proof. The result is stored as an untrusted draft first; the configured Lean process is the acceptance authority.

The runtime uses <code>lake env lean</code> in the session project. Compilation failure is persisted as a formal attempt and routed back into the workflow for repair. Proof-local <code>sorry</code>, <code>admit</code>, <code>axiom</code>, <code>constant</code>, and <code>opaque</code> escapes are rejected.

Run the repository formalization project directly:

~~~bash
cd formalization
lake env lean MathResearchAgentFormalization.lean
~~~

See [<code>formalization/README.md</code>](formalization/README.md) and [<code>docs/PROOF_WORKFLOW.md</code>](docs/PROOF_WORKFLOW.md) for the complete formalization contract.

## Long-running research

<code>ResearchRuntime</code> is the strategic layer above tactical proof runs:

~~~text
Research project
  -> corpus bootstrap
  -> research director
  -> tactical directive
  -> ProofRuntime
  -> evidence receipts
  -> ResearchStateReducer
  -> root readiness
  -> synthesis and independent audit
~~~

Research projects persist:

- root objective contracts and claims with revision history;
- dependencies, support edges, routes, coverage, and open obligations;
- corpus documents and bootstrap reports;
- execution plans, task attempts, artifacts, and evidence receipts;
- trust receipts, authority receipts, checkpoints, and final-proof history;
- formalization status and root-closure readiness.

Core research routes include:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| <code>POST</code> | <code>/v1/research/projects</code> | Create a research project |
| <code>GET</code> | <code>/v1/research/projects</code> | List research projects |
| <code>POST</code> | <code>/v1/research/projects/:projectId/root</code> | Set the root objective contract |
| <code>POST</code> | <code>/v1/research/projects/:projectId/start</code> | Start a research campaign |
| <code>POST</code> | <code>/v1/research/projects/:projectId/resume</code> | Resume a campaign |
| <code>GET</code> | <code>/v1/research/projects/:projectId/frontier</code> | Read the current research frontier |
| <code>GET</code> | <code>/v1/research/projects/:projectId/root-readiness</code> | Check root closure readiness |
| <code>POST</code> | <code>/v1/research/projects/:projectId/synthesis</code> | Run root synthesis and audit |
| <code>GET</code> | <code>/v1/research/projects/:projectId/audit</code> | Validate persisted research invariants |

Corpus publishing is disabled by default. Its outbox, reconciliation, retry, and optional Git projection are controlled independently from research truth.

## Configuration

Configuration is TOML-based and revisioned. An active proof run keeps the configuration snapshot created at run start; later edits affect subsequent runs.

| Profile | Use | Formalization |
| --- | --- | --- |
| [<code>configs/math-agent.toml</code>](configs/math-agent.toml) | Full local runtime | Enabled; creates session Lean projects |
| [<code>configs/math-agent-cloud-run.toml</code>](configs/math-agent-cloud-run.toml) | Lightweight Cloud Run demo | Disabled; uses non-formal <code>prove</code> mode |

The configuration controls:

- runtime host, web/API ports, and data directory;
- proof mode, workflow mode, worker concurrency, and step limits;
- formalization, literature, research, budget, and corpus policies;
- scoped tool capabilities and allowed executables;
- provider profiles, model parameters, and role mappings;
- corpus roots, import authority, archive behavior, and optional publication.

The provider registry includes Google Vertex AI, Google, OpenAI-compatible, OpenRouter, DeepSeek, Anthropic, Codex CLI, and Mock adapters. The default profile uses Google Vertex AI with <code>gemini-3.7-flash</code>.

## Cloud Run

The Cloud Run deployment runs the GUI and proof API in one service and exposes them from one <code>run.app</code> origin. The service routes <code>/health</code> and <code>/v1/...</code> to the in-process API and serves the Workbench from the same port.

~~~bash
export CLOUD_RUN_PROJECT="your-gcp-project-id"
export CLOUD_RUN_REGION="us-central1"
export GOOGLE_CLOUD_LOCATION="global"
bash scripts/deploy-cloud-run.sh
~~~

The deployment uses [<code>docs/CLOUD_RUN_DEMO.md</code>](docs/CLOUD_RUN_DEMO.md). Cloud Run storage is temporary, so this profile is for a bounded demonstration rather than a durable multi-instance research archive.

## Repository layout

~~~text
.
├── apps/
│   └── proof-workbench/       Browser Workbench and thin static server
├── backend/
│   ├── src/agent/             Agent core and role execution
│   ├── src/api/               HTTP API and resource routes
│   ├── src/proof/             ProofRuntime, planning, verification, and formalization
│   ├── src/providers/         Provider adapters and registry
│   └── src/research/          ResearchRuntime, evidence, closure, and corpus
├── configs/                   Local and Cloud Run TOML profiles
├── docs/                      Workflow, runtime, deployment, and architecture docs
├── formalization/             Lean 4/Lake project
├── projects/                  Local example and runtime project data
└── scripts/                   Install, start, deploy, and repository utilities
~~~

## Development

Install dependencies and run the main checks:

~~~bash
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run test:proof
pnpm run build
~~~

Useful commands:

| Command | Purpose |
| --- | --- |
| <code>pnpm run build:proof</code> | Compile the backend |
| <code>pnpm run build:gui</code> | Check the Workbench and server entrypoints |
| <code>pnpm run typecheck</code> | Run the backend TypeScript typecheck |
| <code>pnpm run test:proof</code> | Build and run backend proof/research tests |
| <code>pnpm run corpus -- status --project &lt;project-id&gt;</code> | Inspect the corpus archive outbox |
| <code>pnpm start:api</code> | Start only the proof API |
| <code>pnpm start</code> | Start the local Workbench and API |

The backend tests cover configuration parsing, role and model validation, provider behavior, persistence, proof planning, dynamic dependencies, continuations, formal gates, HTTP/SSE resources, research reduction, authority receipts, and restart recovery.

## Documentation

| Document | Scope |
| --- | --- |
| [<code>docs/PROOF_WORKFLOW.md</code>](docs/PROOF_WORKFLOW.md) | Tactical proof workflow, planner protocol, artifacts, states, and formal gate |
| [<code>docs/WORKFLOW_RUNTIME_V2.md</code>](docs/WORKFLOW_RUNTIME_V2.md) | Dynamic DAG execution, ready frontiers, dependencies, and recovery |
| [<code>docs/MATHEMATICAL_RESEARCH_RUNTIME.md</code>](docs/MATHEMATICAL_RESEARCH_RUNTIME.md) | Strategic research runtime and project lifecycle |
| [<code>docs/MRR_V1_INVARIANTS.md</code>](docs/MRR_V1_INVARIANTS.md) | Research truth, evidence, authority, and closure invariants |
| [<code>docs/CORPUS_ARCHIVE_PROTOCOL.md</code>](docs/CORPUS_ARCHIVE_PROTOCOL.md) | Corpus outbox, reconciliation, and publication policy |
| [<code>apps/proof-workbench/README.md</code>](apps/proof-workbench/README.md) | Browser Workbench boundary and local serving model |
| [<code>formalization/README.md</code>](formalization/README.md) | Lean toolchain and process verification |
| [<code>docs/CLOUD_RUN_DEMO.md</code>](docs/CLOUD_RUN_DEMO.md) | Cloud Run deployment and runtime evidence |

## Design invariants

1. Model prose is input to the runtime, not the authority for truth.
2. A worker result must pass independent verification before candidate submission.
3. <code>PROVED</code> is emitted only after the configured submission and formal gates pass.
4. Provider failure, formal-tool failure, and mathematical failure remain distinct states.
5. A resumed run uses its persisted configuration and artifacts rather than silently mixing current settings into old state.
6. Research claims require evidence and authority receipts; a verified subtask is not automatically a proof of the root objective.
7. Corpus publication is an explicit projection and cannot rewrite research truth when Git or remote publication fails.

## License

Math Research Agent is released under the [MIT License](LICENSE).
