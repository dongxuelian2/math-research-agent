"""Filesystem-backed mathematical notes used by the research engine."""

from __future__ import annotations

import re
from pathlib import Path


class KnowledgeRepository:
    """Read and write Markdown/Lean items referenced by a candidate proof."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, slug: str, suffix: str) -> Path:
        path = self.root / f"{slug}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, slug: str) -> Path | None:
        for suffix in (".md", ".lean"):
            path = self.root / f"{slug}{suffix}"
            if path.is_file():
                return path
        return None

    def read_item(self, slug: str) -> str | None:
        path = self.resolve(slug)
        return path.read_text(encoding="utf-8") if path else None

    def write_item(self, slug: str, content: str, *, fmt: str = "markdown") -> Path:
        suffix = ".lean" if fmt == "lean" else ".md"
        path = self._path(slug, suffix)
        other = path.with_suffix(".lean" if suffix == ".md" else ".md")
        other.unlink(missing_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path

    def index(self) -> str:
        entries: list[str] = []
        for path in sorted((*self.root.rglob("*.md"), *self.root.rglob("*.lean"))):
            slug = str(path.relative_to(self.root).with_suffix(""))
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()
            summary = next((line.strip() for line in first if line.strip()), "(empty)")
            entries.append(f"- [[{slug}]]: {summary[:240]}")
        return "\n".join(entries) or "(no research notes yet)"

    def referenced_items(self, text: str) -> str:
        slugs = list(dict.fromkeys(re.findall(r"\[\[([a-zA-Z0-9_./-]+)\]\]", text)))
        parts = []
        for slug in slugs:
            parts.append(f"## [[{slug}]]\n\n{self.read_item(slug) or '(not found)'}")
        return "\n\n".join(parts)
