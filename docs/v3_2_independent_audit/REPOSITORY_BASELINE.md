# Repository Baseline

## Scope

This is a read-only forensic map of the candidate implementation at the frozen baseline. It is not a repair, certification, refactor, proof-research run, root synthesis run, or truth-promotion run. Existing handoff labels remain candidate evidence only.

## Exact Git boundary

| Field | Value |
|---|---|
| Canonical repository | E:\tool\math\agent\math-research-agent |
| Expected remote | https://github.com/dongxuelian2/math-research-agent.git (dongxuelian2/math-research-agent) |
| Baseline HEAD | 3229aced9fa9bcae41c5ddfea6b6291a6e68d725 |
| Local branch during audit | audit/v3.2-forensic-20260823 |
| Source branch before isolation | main |
| origin/main at precheck | 3229aced9fa9bcae41c5ddfea6b6291a6e68d725 |
| Annotated baseline tag | v3.2-audit-baseline-20260823 |
| Tag target | 3229aced9fa9bcae41c5ddfea6b6291a6e68d725 |
| Tag message | Canonical filesystem-clean v3.2 independent-audit baseline |
| Working tree before audit branch creation | clean |
| Remote drift at precheck | no |

The tag was checked for local and remote collisions before creation. The audit branch was created from the tag; main was not changed.

## Inventory

The inventory is a baseline snapshot of tracked files before adding this audit directory. The complete machine-readable index is [REPOSITORY_FILE_INDEX.tsv](REPOSITORY_FILE_INDEX.tsv).

| Measure | Count / result |
|---|---:|
| Tracked files | 276 |
| Python files | 132 |
| Bash files | 7 |
| PowerShell files | 2 |
| Markdown files | 106 |
| Tracked test files | 41 |
| Tracked files under docs/ | 90 |
| Tracked files under docs/v3_2_migration/ | 87 |
| Tracked production files under openprover/openprover/math_research/ | 63 |
| Tracked test files under openprover/tests/math_research/ | 38 |
| Workflow files | 1: .github/workflows/ci.yml |

Top-level tracked counts are: .env.example 1, .github 1, .gitignore 1, benchmarks 1, configs 4, docs 90, LICENSE 1, openprover 161, projects 9, pytest.ini 1, README.md 1, run_math_agent.ps1 1, scripts 3, and THIRD_PARTY_NOTICES.md 1.

## Tooling and configuration

- openprover/pyproject.toml defines package openprover version 1.0.1, Python >=3.10, runtime dependencies mcp, openai, and pydantic, development dependencies pytest and ruff, and console scripts openprover and math-research.
- openprover/uv.lock is the dependency lock boundary.
- pytest.ini supplies repository test discovery/configuration.
- configs/ contains model-provider examples only: models.codex.example.json, models.gemini.example.json, models.mock.json, and models.openai.example.json.
- run_math_agent.ps1 is the Windows dispatcher for init, import, context, run, status, provider smoke, formalization, campaigns, demo, observatory, and benchmark commands.
- scripts/bootstrap.ps1 and scripts/bootstrap.sh bootstrap environments; scripts/run_benchmark.sh invokes the benchmark lane.
- CI is .github/workflows/ci.yml. It runs Ubuntu and Windows jobs, locked uv sync, Ruff, pytest, compile checks, demo/launcher checks, and Windows bootstrap coverage.

No test suite, provider, benchmark, demo, campaign, root-synthesis, or truth-promotion command was executed by this audit pass. Recorded test results in migration documents are historical candidate evidence.

## Executable entry points

| Entry | Actual target |
|---|---|
| openprover console script | openprover.cli:main |
| math-research console script | openprover.math_research.cli:main |
| python -m openprover.math_research | __main__.py; dispatches core CLI, campaign CLI, demo, benchmark, or observatory |
| run_math_agent.ps1 | Project/config wrapper around uv run --project openprover python -m openprover.math_research |
| benchmark subcommand | benchmark.main |
| observatory subcommand | observatory.main |
| demo subcommand | showcase_demo.main |
| formalize subcommand | formalization.run_formalization through core CLI |

## Interpretation rules

1. A tracked document under docs/v3_2_migration/ is retained historical evidence, not a normative specification by default.
2. A test, probe result, or self-authored handoff can show observed behavior at its tested seam; it cannot independently certify the architecture.
3. The baseline’s own candidate status remains: specification compliance not established, self-certification not trusted, root synthesis blocked for re-audit, and truth promotion blocked for re-audit.
4. The only permitted changes in this pass are files under docs/v3_2_independent_audit/; no production, test, configuration, dependency, or source-tree reorganization is part of this pass.
