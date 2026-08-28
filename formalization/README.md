# Lean process gate

This Lake project pins the Lean 4 toolchain and Mathlib revision used by the
proof runtime. The runtime checks complete source files with `lake env lean`
and accepts a formal proof only when the process exits successfully.

Mathlib is pinned to the Lean-compatible `v4.33.1` tag so generated proofs can
use the standard finite-dimensional linear algebra and graph libraries without
silently relying on a global installation.
