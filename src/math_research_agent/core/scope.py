"""Submission guard for unresolved mathematical scope or dependency gaps."""

from __future__ import annotations

import re


_CLOSURE_MARKERS = (
    "SCOPE_CLOSURE: PASS",
    "H_SCOPE_BRIDGE: PASS",
    "H-SCOPE BRIDGE: PASS",
    "SCOPE BRIDGE: PASS",
)
_BLOCKERS = (
    re.compile(r"\bSCOPE[_ -]?GAP\b", re.IGNORECASE),
    re.compile(r"\bUNRESOLVED[_ -]?SCOPE\b", re.IGNORECASE),
    re.compile(r"\bREQUIRED[_ -]?DEPENDENCY[_ -]?EXPANSION\b", re.IGNORECASE),
    re.compile(r"\bEXTERNAL DEPENDENCY EXPANSION REQUIRED\b", re.IGNORECASE),
    re.compile(r"\b(?:SCOPE|H[- ]?SCOPE).{0,160}\bBLOCKED\b", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"\bBLOCKED\b.{0,160}\b(?:SCOPE|H[- ]?SCOPE|EXPANSION|h\s*(?:!=|≠))",
        re.IGNORECASE | re.DOTALL,
    ),
)


def submission_blocker(whiteboard: str) -> str | None:
    if not whiteboard or any(marker in whiteboard.upper() for marker in _CLOSURE_MARKERS):
        return None
    for pattern in _BLOCKERS:
        match = pattern.search(whiteboard)
        if match:
            excerpt = " ".join(match.group(0).split())
            return f"unresolved scope/dependency blocker: {excerpt[:200]}"
    return None
