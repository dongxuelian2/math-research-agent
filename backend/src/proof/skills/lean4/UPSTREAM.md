# Upstream provenance

This directory is vendored from the public MIT-licensed `lean4` skill in
[cameronfreer/lean4-skills](https://github.com/cameronfreer/lean4-skills),
path `plugins/lean4/skills/lean4`.

Pinned source revision: `74febda7679a858af666903756a191f7a0437482` (2026-08-29).

The Math Research Agent loads `SKILL.md` as runtime context for its Lean
Formalizer. It does not assume that the optional LSP/MCP integrations shipped
by the upstream plugin are installed; Cloud Run uses the session's `lake env
lean` process as the authoritative check.
