"""Textual integrity checks used before handing a candidate to Lean."""

from __future__ import annotations

import re


def _strip_comments(text: str) -> str:
    text = re.sub(r"/-.*?-", "", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", text)


def check_proof_preserves_theorem(theorem_text: str, proof_text: str) -> str | None:
    """Ensure a submitted Lean proof keeps the original declaration intact."""
    theorem = _strip_comments(theorem_text)
    start = re.search(r"^\s*(?:theorem|lemma|def)\s", theorem, re.MULTILINE)
    if not start:
        return None
    fragments = re.split(r"\bsorry\b", theorem[start.start() :])
    if len(fragments) == 1:
        return None
    fragments = [" ".join(fragment.split()) for fragment in fragments]
    proof = " ".join(_strip_comments(proof_text).split())
    position = 0
    for index, fragment in enumerate(fragments):
        if not fragment:
            if re.search(r"\bsorry\b", proof[position:]):
                return f"submitted proof still contains `sorry` for hole {index}"
            continue
        found = proof.find(fragment, position)
        if found < 0:
            label = "the theorem header" if index == 0 else f"the text after sorry #{index}"
            return f"{label} from THEOREM.lean was not found in the submitted proof"
        if index > 0 and re.search(r"\bsorry\b", proof[position:found]):
            return f"submitted proof still contains `sorry` for hole {index}"
        position = found + len(fragment)
    return None
