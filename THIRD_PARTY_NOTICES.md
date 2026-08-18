# Third-Party Software Notices and Attribution

This repository incorporates and builds upon open-source software. Below are details regarding the third-party components included in this project.

---

## 1. OpenProver

- **Component / Directory**: `openprover/`
- **Original Author**: Matěj Kripner (`kripner@ufal.mff.cuni.cz`)
- **Upstream Repository**: https://github.com/kripner/OpenProver
- **Upstream Commit at Baseline**: `e200251b34349ab6c34548d30319abde86cb6bc6`
- **License**: MIT License
- **License File**: [`openprover/LICENSE`](openprover/LICENSE)

### Modifications and Architecture Extension

The directory `openprover/` contains the base OpenProver 1.0.1 engine (providing LLM client abstractions, planner loop, worker execution, and CLI foundations) extended with the project's custom `openprover.math_research` package:
- Multi-agent independent audit gate (Counterexample Hunter, Dependency Auditor, Exhaustiveness/Converse Auditor, Boundary Auditor, Final Proof Auditor).
- Hard state machine and Archivist-gated theorem transitions (`OPEN -> IN_RESEARCH -> CANDIDATE_PROOF -> AUDITING -> PROVED/REJECTED`).
- Long-horizon research campaign orchestration, DAG-based scheduling, and asynchronous literature verification pipelines.
- Non-cost mock demo verification and multi-provider execution adapters (OpenAI Responses API, Codex CLI, and upcoming Gemini provider).

The original copyright and license terms of OpenProver are preserved in full in [`openprover/LICENSE`](openprover/LICENSE).
