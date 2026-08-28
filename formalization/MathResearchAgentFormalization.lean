/- The runtime writes each proof attempt into its durable run directory and
   invokes `lake env lean <absolute-file>`. This library anchors the configured
   Lean toolchain without granting any proof-local axioms. -/

theorem mathResearchAgent_identity (n : Nat) : n = n := by
  rfl
