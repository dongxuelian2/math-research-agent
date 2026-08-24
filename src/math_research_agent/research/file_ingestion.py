"""Project-owned ingestion of unstructured research files.

Original files stay under ``inbox/``.  A normalized Markdown working copy is
written under ``work/imported/`` and is the only representation injected into
research prompts.  The manifest binds the original hash, extracted text, and
working artifact so importing the same file twice is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import ProjectError, ProjectStore


MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".rst",
    ".tex",
    ".pdf",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return result[:120] or "source"


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return candidate[:160]
    return fallback[:160]


class ProjectFileIngestor:
    """Copy, extract, normalize, and track project research files."""

    def __init__(self, project: ProjectStore):
        self.project = project
        self.manifest_path = project.root / "inbox" / "manifest.json"

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return {"schema_version": MANIFEST_SCHEMA_VERSION, "files": []}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Invalid imported-file manifest: {self.manifest_path}") from exc
        if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ProjectError("Unsupported imported-file manifest schema")
        if not isinstance(data.get("files"), list):
            raise ProjectError("Imported-file manifest files must be a list")
        return data

    def add(self, source: str | Path) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ProjectError(f"Imported file not found: {source_path}")
        suffix = source_path.suffix.casefold()
        if suffix not in SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
            raise ProjectError(f"Unsupported imported file type {suffix or '(none)'}; use {supported}")
        digest = _sha256(source_path)
        manifest = self.load_manifest()
        for record in manifest["files"]:
            if record.get("sha256") == digest:
                return {**record, "duplicate": True}

        file_id = f"file-{digest[:16]}"
        inbox_name = f"{file_id}-{_safe_name(source_path.stem)}{suffix}"
        inbox_path = self.project.root / "inbox" / inbox_name
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, inbox_path)
        record = {
            "schema_version": 1,
            "id": file_id,
            "original_name": source_path.name,
            "source_path": str(source_path),
            "inbox_path": inbox_path.relative_to(self.project.root).as_posix(),
            "suffix": suffix,
            "kind": "pdf" if suffix == ".pdf" else "text",
            "sha256": digest,
            "size_bytes": source_path.stat().st_size,
            "status": "PENDING",
            "added_at": _now(),
            "processed_at": None,
            "work_path": f"work/imported/{file_id}.md",
            "analysis_path": f"work/imported/{file_id}.analysis.json",
            "error": None,
        }
        manifest["files"].append(record)
        self._save_manifest(manifest)
        return record

    def prepare_pending(self) -> list[dict[str, Any]]:
        """Materialize all pending files and return their updated records."""

        manifest = self.load_manifest()
        changed: list[dict[str, Any]] = []
        for record in manifest["files"]:
            if record.get("status") == "READY" and self._work_exists(record):
                continue
            inbox = self.project.root / str(record.get("inbox_path", ""))
            try:
                text = self._extract(inbox, str(record.get("suffix", "")))
                if not text.strip():
                    raise ProjectError("file text extraction returned no content")
                work_path = self.project.root / str(record["work_path"])
                analysis_path = self.project.root / str(record["analysis_path"])
                work_path.parent.mkdir(parents=True, exist_ok=True)
                analysis_path.parent.mkdir(parents=True, exist_ok=True)
                title = _title(text, str(record.get("original_name") or record["id"]))
                analysis = self._analyze(record, text, title)
                work_path.write_text(
                    self._working_markdown(record, title, text, analysis), encoding="utf-8"
                )
                analysis_path.write_text(
                    json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                record.update(
                    {
                        "status": "READY",
                        "processed_at": _now(),
                        "title": title,
                        "character_count": len(text),
                        "analysis": analysis,
                        "error": None,
                    }
                )
            except (OSError, ProjectError, subprocess.SubprocessError) as exc:
                record.update({"status": "ERROR", "error": str(exc), "processed_at": _now()})
            changed.append(dict(record))
        if changed:
            self._save_manifest(manifest)
        return changed

    def ready_sources(self, *, max_chars: int = 40000) -> list[dict[str, Any]]:
        """Return prompt-safe imported sources with bounded excerpts."""

        manifest = self.load_manifest()
        sources = []
        for record in manifest["files"]:
            if record.get("status") != "READY":
                continue
            path = self.project.root / str(record.get("work_path", ""))
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            sources.append(
                {
                    "id": record["id"],
                    "title": record.get("title") or record.get("original_name", record["id"]),
                    "source_file": record["work_path"],
                    "original_name": record.get("original_name", ""),
                    "sha256": record.get("sha256", ""),
                    "content": content[:max_chars],
                    "truncated": len(content) > max_chars,
                    "analysis": record.get("analysis") or {},
                }
            )
        return sources

    def is_materialized(self, record: dict[str, Any]) -> bool:
        """Return whether a manifest record has both durable work artifacts."""

        return self._work_exists(record)

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.manifest_path)

    def _work_exists(self, record: dict[str, Any]) -> bool:
        work = self.project.root / str(record.get("work_path", ""))
        analysis = self.project.root / str(record.get("analysis_path", ""))
        return work.is_file() and analysis.is_file()

    @staticmethod
    def _extract(path: Path, suffix: str) -> str:
        if not path.is_file():
            raise ProjectError(f"Imported inbox file is missing: {path}")
        if suffix.casefold() == ".pdf":
            completed = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or "pdftotext failed"
                raise ProjectError(detail[:500])
            return completed.stdout
        return path.read_text(encoding="utf-8-sig", errors="replace")

    @staticmethod
    def _analyze(record: dict[str, Any], text: str, title: str) -> dict[str, Any]:
        headings = [
            line.strip().lstrip("#").strip()
            for line in text.splitlines()
            if line.lstrip().startswith("#") and line.strip("# ")
        ][:80]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        return {
            "schema_version": 1,
            "file_id": record["id"],
            "title": title,
            "kind": record.get("kind", "text"),
            "headings": headings,
            "paragraph_count": len(paragraphs),
            "character_count": len(text),
            "preview": text.strip()[:2000],
            "analysis_note": (
                "This is a deterministic source inventory. The project Planner and proof Worker "
                "must perform the mathematical interpretation before using it as evidence."
            ),
        }

    @staticmethod
    def _working_markdown(
        record: dict[str, Any], title: str, text: str, analysis: dict[str, Any]
    ) -> str:
        return (
            f"# Imported working file: {title}\n\n"
            f"> Original: `{record.get('original_name', '')}`\n"
            f"> SHA-256: `{record.get('sha256', '')}`\n"
            f"> Source kind: `{record.get('kind', 'text')}`\n\n"
            "## Ingestion note\n\n"
            "This file is imported project material, not an automatically proved theorem. "
            "Treat its claims as unverified until the normal context, dependency, and audit gates pass.\n\n"
            "## Inventory\n\n"
            f"- Paragraphs: `{analysis['paragraph_count']}`\n"
            f"- Characters: `{analysis['character_count']}`\n"
            f"- Headings: `{', '.join(analysis['headings']) or '(none)'}`\n\n"
            "## Extracted content\n\n"
            f"{text.rstrip()}\n"
        )
