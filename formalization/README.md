# Lean process gate

This Lake project pins the Lean 4 toolchain used by the proof runtime. The
runtime checks complete source files with `lake env lean` and accepts a formal
proof only when the process exits successfully.

The default environment intentionally contains Lean core only. Add audited
Lake dependencies here when a project needs a larger trusted library surface.
