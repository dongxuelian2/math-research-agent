"""Literature trust boundary, synthesis, and reusable search memory."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pipelines import LITERATURE_VERDICTS
from .project import ProjectError, utc_now
from .schemas import LiteratureResultSchema, SchemaError, parse_structured_response
from .routing import ModelRouter, RoutedLLMClient


AUTHORITY_STATUSES = frozenset({
    "DISCOVERED_REFERENCE",
    "METADATA_VERIFIED",
    "SOURCE_RETRIEVED",
    "THEOREM_EXTRACTED",
    "AUTHORITY_CANDIDATE",
    "AUTHORITY_VERIFYING",
    "UNVERIFIED_REFERENCE",
    "AUTHORITY_VERIFICATION_PENDING",
    "VERIFIED_SOURCE_THEOREM",
    "AUTHORITY_VERIFICATION_FAILED",
    "AUTHORITY_INCOMPLETE",
})
APPLICABILITY_STATUSES = frozenset({
    "APPLICABILITY_CANDIDATE",
    "APPLICABILITY_VERIFIED",
    "APPLICABLE_EXTERNAL_AUTHORITY",
    "APPLICABILITY_REJECTED",
    "APPLICABILITY_UNCERTAIN",
    "NEEDS_REVALIDATION",
})
READER_VERDICTS = frozenset({
    "THEOREM_EXTRACTED", "ABSTRACT_ONLY", "METADATA_ONLY",
    "FULL_TEXT_UNAVAILABLE", "SOURCE_CONFLICT", "MALFORMED_SOURCE",
})
SOURCE_PRIORITIES = {
    "original_paper": 1,
    "author_preprint": 2,
    "published_version": 3,
    "authoritative_monograph": 4,
    "later_explicit_restatement": 5,
    "survey_or_lecture_notes": 6,
    "abstract_or_metadata": 7,
    "informal_webpage": 8,
}

REQUIRED_AUTHORITY_FIELDS = (
    "title", "authors", "year", "source", "DOI_or_stable_identifier",
    "version", "theorem_number", "page_or_section", "exact_statement",
    "normalized_statement", "hypotheses", "notation_map", "retrieval_source",
    "retrieved_at", "reader_verdict", "authority_verifier_verdict",
    "used_by_obligations",
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Unable to read literature registry {path}: {exc}") from exc


def statement_hash(statement: str) -> str:
    normalized = " ".join(str(statement).strip().casefold().split())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_extracted_statement(statement: str) -> str:
    """Canonical whitespace normalization used by the deterministic span gate."""

    return " ".join(str(statement or "").strip().split())


def extracted_statement_hash(statement: str) -> str:
    normalized = normalize_extracted_statement(statement)
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def assumption_snapshot_hash(
    obligation_id: str,
    current_target: str,
    current_assumptions: list[Any] | None = None,
    authorized_local_lemmas: list[Any] | None = None,
) -> str:
    """Bind applicability to the exact target and authorized project truth."""

    payload = {
        "obligation_id": str(obligation_id),
        "normalized_current_target": normalize_extracted_statement(current_target).casefold(),
        "current_assumptions": copy.deepcopy(current_assumptions or []),
        "authorized_local_lemmas": copy.deepcopy(authorized_local_lemmas or []),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_literature_request(request: dict) -> None:
    required = {
        "obligation_id", "requested_statement", "why_needed",
        "blocking_or_nonblocking", "expected_impact", "search_hints",
    }
    missing = required - set(request)
    if missing:
        raise ProjectError(
            "LITERATURE_REQUEST missing fields: " + ", ".join(sorted(missing))
        )
    if request["blocking_or_nonblocking"] not in {"blocking", "nonblocking"}:
        raise ProjectError("blocking_or_nonblocking must be blocking or nonblocking")
    if not isinstance(request["search_hints"], (list, dict, str)):
        raise ProjectError("LITERATURE_REQUEST search_hints must be structured")


class ExternalAuthorityRegistry:
    """Persist source authenticity separately from obligation applicability."""

    def __init__(self, root: str | Path):
        root = Path(root)
        self.path = root if root.suffix == ".json" else root / "external_authority_registry.json"
        self.root = self.path.parent
        self._lock = threading.RLock()

    def load(self) -> dict:
        value = _read_json(self.path, {
            "schema_version": 2,
            "source_theorems": {},
            "applicability_records": {},
            "last_updated": None,
        })
        if value.get("schema_version") != 2:
            raise ProjectError("Unsupported external authority registry schema")
        value.setdefault("applicability_records", {})
        if not isinstance(value.get("source_theorems"), dict):
            raise ProjectError("External authority registry source_theorems must be an object")
        return value

    @staticmethod
    def applicability_id(
        authority_id: str, obligation_id: str, current_target: str,
        assumption_snapshot: str,
    ) -> str:
        identity = "|".join((
            str(authority_id), str(obligation_id),
            normalize_extracted_statement(current_target).casefold(),
            str(assumption_snapshot),
        ))
        return "app-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def register(self, record: dict) -> dict:
        """Register discovery evidence; never promote merely from model memory."""

        value = copy.deepcopy(record)
        authority_id = str(value.get("authority_id") or "").strip()
        if not authority_id:
            stable = str(value.get("DOI_or_stable_identifier") or "").strip()
            if not stable:
                raise ProjectError("External authority requires a stable identifier")
            authority_id = "ext-" + hashlib.sha256(stable.casefold().encode("utf-8")).hexdigest()[:16]
            value["authority_id"] = authority_id
        if re.search(r"\b(?:fake|fabricated|example\.invalid|placeholder|todo)\b", authority_id, re.I):
            raise ProjectError("Fabricated or placeholder authority identifiers are forbidden")
        missing = [field for field in REQUIRED_AUTHORITY_FIELDS if field not in value]
        if missing:
            raise ProjectError("External authority missing fields: " + ", ".join(missing))
        if not value.get("retrieval_source"):
            raise ProjectError("Model memory is not a bibliographic retrieval source")
        if str(value.get("retrieval_source", "")).strip().casefold() in {
            "model_memory", "llm_memory", "memory", "training_data",
        }:
            raise ProjectError("Model memory is not a bibliographic retrieval source")
        source_type = str(value.get("source_type") or "").strip()
        if source_type not in SOURCE_PRIORITIES:
            raise ProjectError(
                "source_type must record the actual primary/secondary source class"
            )
        value["source_priority"] = SOURCE_PRIORITIES[source_type]
        value["status"] = "UNVERIFIED_REFERENCE"
        value["statement_hash"] = statement_hash(value.get("normalized_statement", ""))
        value.setdefault("verification_history", [])
        value["registered_at"] = utc_now()
        with self._lock:
            data = self.load()
            if authority_id in data["source_theorems"]:
                raise ProjectError(f"External authority already registered: {authority_id}")
            data["source_theorems"][authority_id] = copy.deepcopy(value)
            data["last_updated"] = utc_now()
            _write_json(self.path, data)
        return copy.deepcopy(value)

    def verify(self, authority_id: str, verification: dict) -> dict:
        """Verify source identity and exact theorem extraction, never applicability."""

        with self._lock:
            data = self.load()
            if authority_id not in data["source_theorems"]:
                raise ProjectError(f"Unknown external authority: {authority_id}")
            record = data["source_theorems"][authority_id]
            errors = []
            if record.get("reader_verdict") != "THEOREM_EXTRACTED":
                errors.append("reader did not extract theorem text from sufficient source content")
            if record.get("content_scope") not in {"FULL_TEXT", "THEOREM_PAGE"}:
                errors.append("abstract/metadata-only evidence cannot establish theorem authority")
            artifact = str(record.get("retrieved_content_path") or "").strip()
            expected_hash = str(record.get("retrieved_content_sha256") or "").strip().casefold()
            if not artifact or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash):
                errors.append("retrieved source artifact/hash is missing")
            else:
                try:
                    artifact_path = self._trusted_path(artifact)
                    actual_hash = "sha256:" + hashlib.sha256(
                        artifact_path.read_bytes()
                    ).hexdigest()
                    if actual_hash != expected_hash:
                        errors.append("retrieved source artifact hash mismatch")
                except (OSError, ValueError):
                    errors.append("retrieved source artifact is unavailable or outside registry root")
            self._verify_extraction_binding(record, errors)
            if not str(record.get("exact_statement", "")).strip():
                errors.append("exact theorem statement is missing")
            if not record.get("theorem_number") or not record.get("page_or_section"):
                errors.append("theorem location is incomplete")
            if verification.get("verdict") != "VERIFIED_SOURCE_THEOREM":
                errors.append("source verifier did not return VERIFIED_SOURCE_THEOREM")
            if verification.get("source_identity_match") is not True:
                errors.append("retrieved content is not bound to the bibliographic identity")
            if verification.get("bibliographic_metadata_match") is not True:
                errors.append("bibliographic metadata was not independently matched")
            claimed_type = verification.get("claimed_source_type")
            if claimed_type and claimed_type != record.get("source_type"):
                errors.append("secondary source may not masquerade as primary")
            status = (
                "VERIFIED_SOURCE_THEOREM" if not errors
                else "AUTHORITY_VERIFICATION_FAILED"
            )
            record["status"] = status
            record["authority_verifier_verdict"] = verification.get("verdict")
            record["authority_verification_errors"] = errors
            record["verified_at"] = utc_now() if not errors else None
            record.setdefault("verification_history", []).append({
                "status": status,
                "verification": copy.deepcopy(verification),
                "errors": errors,
                "at": utc_now(),
            })
            data["source_theorems"][authority_id] = record
            data["last_updated"] = utc_now()
            _write_json(self.path, data)
            return copy.deepcopy(record)

    def _trusted_path(self, relative: str) -> Path:
        path = (self.root / str(relative)).resolve()
        path.relative_to(self.root.resolve())
        if not path.is_file():
            raise OSError(f"authority artifact does not exist: {relative}")
        return path

    def _verify_extraction_binding(self, record: dict, errors: list[str]) -> None:
        """Re-read the exact text span and bind it to both retrieved artifacts."""

        extraction_ref = str(record.get("extraction_artifact_path") or "").strip()
        extraction_hash = str(
            record.get("extraction_artifact_sha256") or ""
        ).strip().casefold()
        text_ref = str(record.get("text_artifact_path") or "").strip()
        text_hash = str(record.get("text_artifact_sha256") or "").strip().casefold()
        statement_digest = str(
            record.get("extracted_statement_sha256") or ""
        ).strip().casefold()
        required_hashes = (extraction_hash, text_hash, statement_digest)
        if not extraction_ref or not text_ref or not all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in required_hashes
        ):
            errors.append("theorem extraction artifact/span binding is missing")
            return
        try:
            extraction_path = self._trusted_path(extraction_ref)
            text_path = self._trusted_path(text_ref)
            extraction_bytes = extraction_path.read_bytes()
            if "sha256:" + hashlib.sha256(extraction_bytes).hexdigest() != extraction_hash:
                errors.append("theorem extraction artifact hash mismatch")
                return
            text_bytes = text_path.read_bytes()
            if "sha256:" + hashlib.sha256(text_bytes).hexdigest() != text_hash:
                errors.append("text artifact hash mismatch")
                return
            extraction = json.loads(extraction_bytes.decode("utf-8"))
            if not isinstance(extraction, dict):
                raise ValueError("extraction artifact must be an object")
            if str(extraction.get("source_artifact_sha256") or "").casefold() != str(
                record.get("retrieved_content_sha256") or ""
            ).casefold():
                errors.append("extraction is bound to a different source artifact")
            if str(extraction.get("text_artifact_sha256") or "").casefold() != text_hash:
                errors.append("extraction is bound to a different text artifact")
            candidates = extraction.get("extractions")
            if not isinstance(candidates, list):
                raise ValueError("extraction artifact has no extraction list")
            extraction_id = str(record.get("extraction_id") or "")
            selected = next(
                (
                    item for item in candidates
                    if isinstance(item, dict)
                    and (
                        extraction_id and str(item.get("extraction_id") or "") == extraction_id
                        or not extraction_id
                        and int(item.get("span_start", -1)) == int(record.get("span_start", -2))
                        and int(item.get("span_end", -1)) == int(record.get("span_end", -2))
                    )
                ),
                None,
            )
            if selected is None:
                errors.append("theorem extraction span is absent from extraction artifact")
                return
            span_start = int(selected.get("span_start", -1))
            span_end = int(selected.get("span_end", -1))
            if int(record.get("span_start", -2)) != span_start or int(
                record.get("span_end", -2)
            ) != span_end:
                errors.append("authority span does not match extraction artifact span")
            text = text_bytes.decode("utf-8")
            if span_start < 0 or span_end <= span_start or span_end > len(text):
                errors.append("theorem extraction span is invalid")
                return
            raw_span = text[span_start:span_end]
            normalized_span = normalize_extracted_statement(raw_span)
            actual_statement_hash = extracted_statement_hash(normalized_span)
            if actual_statement_hash != statement_digest:
                errors.append("theorem statement span hash mismatch")
            if str(selected.get("extracted_statement_sha256") or "").casefold() != statement_digest:
                errors.append("extraction artifact statement hash mismatch")
            if normalize_extracted_statement(
                selected.get("raw_extracted_text", "")
            ) != normalized_span:
                errors.append("extraction artifact raw text does not match the recorded span")
            if normalize_extracted_statement(
                selected.get("normalized_extracted_text", "")
            ) != normalized_span:
                errors.append("extraction artifact normalized text does not match the span")
            if normalize_extracted_statement(record.get("exact_statement", "")) != normalized_span:
                errors.append("authority exact statement does not match the extracted span")
            if normalize_extracted_statement(record.get("normalized_statement", "")) != normalized_span:
                errors.append("authority normalized statement does not match the extracted span")
            if record.get("theorem_number") and str(
                selected.get("theorem_label") or selected.get("label") or ""
            ).casefold() != str(record.get("theorem_number") or "").casefold():
                errors.append("authority theorem label does not match extraction artifact")
            if record.get("page_or_section") and str(
                selected.get("location") or ""
            ).casefold() != str(record.get("page_or_section") or "").casefold():
                errors.append("authority theorem location does not match extraction artifact")
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            errors.append("theorem extraction artifact is malformed or unavailable")

    def require_verified_source(self, authority_id: str, *, obligation_id: str | None = None) -> dict:
        with self._lock:
            data = self.load()
            record = data["source_theorems"].get(authority_id)
            if not record or record.get("status") != "VERIFIED_SOURCE_THEOREM":
                raise ProjectError(
                    f"External source theorem is not VERIFIED_SOURCE_THEOREM: {authority_id}"
                )
            if obligation_id and obligation_id not in record["used_by_obligations"]:
                record["used_by_obligations"].append(obligation_id)
                data["last_updated"] = utc_now()
                _write_json(self.path, data)
            return copy.deepcopy(record)

    def verified(self) -> list[dict]:
        return [
            copy.deepcopy(record)
            for record in self.load()["source_theorems"].values()
            if record.get("status") == "VERIFIED_SOURCE_THEOREM"
        ]

    def register_applicability_reconstruction(self, reconstruction: dict) -> dict:
        """Store structured, obligation-specific mathematical reconstruction."""

        value = copy.deepcopy(reconstruction)
        required = {
            "obligation_id", "authority_id", "current_target", "current_assumptions",
            "external_statement", "external_hypotheses", "notation_map",
            "hypothesis_mapping", "conclusion_mapping", "exception_analysis",
            "direction_analysis", "normalization_analysis", "required_local_lemmas",
            "authorized_local_lemmas", "unresolved_conditions", "reconstructor_call_id",
            "reconstructor_model", "reconstructor_tier", "assumption_snapshot_hash",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ProjectError("Applicability reconstruction missing fields: " + ", ".join(missing))
        source = self.require_verified_source(str(value["authority_id"]))
        if normalize_extracted_statement(value["external_statement"]) != normalize_extracted_statement(
            source.get("exact_statement", "")
        ):
            raise ProjectError("Applicability reconstruction is bound to a different source theorem")
        expected_snapshot = assumption_snapshot_hash(
            str(value["obligation_id"]), str(value["current_target"]),
            list(value["current_assumptions"]), list(value["authorized_local_lemmas"]),
        )
        if value["assumption_snapshot_hash"] != expected_snapshot:
            raise ProjectError("Applicability reconstruction assumption snapshot mismatch")
        app_id = self.applicability_id(
            str(value["authority_id"]), str(value["obligation_id"]),
            str(value["current_target"]), expected_snapshot,
        )
        value["applicability_id"] = app_id
        value["reconstruction_id"] = str(value.get("reconstruction_id") or f"recon-{app_id[4:]}")
        value["source_theorem_id"] = str(value.get("source_theorem_id") or value["authority_id"])
        value["normalized_current_target"] = normalize_extracted_statement(value["current_target"])
        value["normalized_current_assumptions"] = [
            normalize_extracted_statement(item.get("statement", item) if isinstance(item, dict) else item)
            for item in value["current_assumptions"]
        ]
        value["status"] = "APPLICABILITY_CANDIDATE"
        value["created_at"] = utc_now()
        artifact_dir = self.root / "applicability" / app_id
        artifact_path = artifact_dir / "EXTERNAL_AUTHORITY_RECONSTRUCTION.json"
        hash_payload = copy.deepcopy(value)
        encoded = json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value["artifact_hash"] = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        _write_json(artifact_path, value)
        value["reconstruction_artifact_path"] = str(artifact_path.relative_to(self.root)).replace("\\", "/")
        with self._lock:
            data = self.load()
            data["applicability_records"][app_id] = value
            data["last_updated"] = utc_now()
            _write_json(self.path, data)
        return copy.deepcopy(value)

    def verify_applicability(self, applicability_id: str, verification: dict) -> dict:
        """Promote only an independently checked, intact reconstruction artifact."""

        with self._lock:
            data = self.load()
            record = data["applicability_records"].get(applicability_id)
            if not record:
                raise ProjectError(f"Unknown applicability record: {applicability_id}")
            errors: list[str] = []
            verifier_call_id = str(verification.get("verifier_call_id") or "").strip()
            if not verifier_call_id or verifier_call_id == str(record.get("reconstructor_call_id") or ""):
                errors.append("independent applicability verifier is required")
            try:
                artifact = self._trusted_path(record["reconstruction_artifact_path"])
                stored = json.loads(artifact.read_text(encoding="utf-8"))
                stored_hash = stored.pop("artifact_hash", None)
                stored.pop("reconstruction_artifact_path", None)
                encoded = json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                actual = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                if actual != record.get("artifact_hash") or stored_hash != record.get("artifact_hash"):
                    errors.append("applicability reconstruction artifact hash mismatch")
            except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
                errors.append("applicability reconstruction artifact is unavailable or malformed")
            mappings = record.get("hypothesis_mapping")
            if not isinstance(mappings, list) or not mappings:
                errors.append("structured hypothesis mapping is missing")
            else:
                for item in mappings:
                    if not isinstance(item, dict) or item.get("status") not in {"PROVED", "NOT_APPLICABLE"}:
                        errors.append("an external hypothesis is unresolved or failed")
                        break
                    if not item.get("evidence"):
                        errors.append("a hypothesis mapping lacks evidence")
                        break
            if not isinstance(record.get("conclusion_mapping"), dict) or record["conclusion_mapping"].get("status") != "PROVED":
                errors.append("external conclusion was not proved to imply the target")
            if not isinstance(record.get("direction_analysis"), dict) or record["direction_analysis"].get("status") != "PROVED":
                errors.append("implication direction was not proved")
            if not isinstance(record.get("exception_analysis"), dict) or record["exception_analysis"].get("status") not in {"PROVED", "NOT_APPLICABLE"}:
                errors.append("exception analysis did not pass")
            if record.get("unresolved_conditions"):
                errors.append("applicability reconstruction has unresolved conditions")
            required_lemmas = {str(item) for item in record.get("required_local_lemmas", [])}
            authorized_lemmas = {
                str(item.get("id")) if isinstance(item, dict) else str(item)
                for item in record.get("authorized_local_lemmas", [])
            }
            if not required_lemmas.issubset(authorized_lemmas):
                errors.append("applicability reconstruction uses an unauthorized local lemma")
            verdict = str(verification.get("verdict") or "UNCERTAIN").upper()
            if verdict != "APPLICABLE":
                errors.append(f"independent verifier verdict is {verdict}")
            status = "APPLICABLE_EXTERNAL_AUTHORITY" if not errors else (
                "APPLICABILITY_UNCERTAIN" if verdict in {"UNCERTAIN", "INCOMPLETE_RECONSTRUCTION"}
                else "APPLICABILITY_REJECTED"
            )
            record["status"] = status
            record["applicability_verifier_verdict"] = verdict
            record["applicability_verification_errors"] = errors
            record["verifier_call_id"] = verifier_call_id or None
            record["verifier_model"] = verification.get("verifier_model")
            record["verifier_tier"] = verification.get("verifier_tier")
            record["verified_at"] = utc_now() if not errors else None
            verifier_path = self.root / "applicability" / applicability_id / "INDEPENDENT_APPLICABILITY_VERIFICATION.json"
            _write_json(verifier_path, {**copy.deepcopy(verification), "promotion_status": status, "errors": errors})
            record["verifier_artifact_path"] = str(verifier_path.relative_to(self.root)).replace("\\", "/")
            data["applicability_records"][applicability_id] = record
            data["last_updated"] = utc_now()
            _write_json(self.path, data)
            return copy.deepcopy(record)

    def require_applicable(
        self, authority_id: str, obligation_id: str, current_target: str,
        current_assumptions: list[Any] | None = None,
        authorized_local_lemmas: list[Any] | None = None,
    ) -> dict:
        snapshot = assumption_snapshot_hash(
            obligation_id, current_target, current_assumptions, authorized_local_lemmas,
        )
        app_id = self.applicability_id(authority_id, obligation_id, current_target, snapshot)
        with self._lock:
            data = self.load()
            record = data["applicability_records"].get(app_id)
            if not record or record.get("status") != "APPLICABLE_EXTERNAL_AUTHORITY":
                raise ProjectError(f"External theorem is not applicable to current obligation snapshot: {app_id}")
            return copy.deepcopy(record)


class LiteratureMemory:
    def __init__(self, root: str | Path):
        root = Path(root)
        self.path = root if root.suffix == ".json" else root / "literature_memory.json"
        self._lock = threading.RLock()

    def load(self) -> dict:
        value = _read_json(self.path, {"schema_version": 1, "entries": {}})
        if value.get("schema_version") != 1:
            raise ProjectError("Unsupported literature memory schema")
        value.setdefault("entries", {})
        return value

    def add_verified_authority(self, authority: dict, *, concepts: list[str], keywords: list[str]) -> dict:
        if authority.get("status") != "VERIFIED_SOURCE_THEOREM":
            raise ProjectError("Only verified source theorems enter literature memory")
        entry = {
            "authority_id": authority["authority_id"],
            "statement_hash": authority.get("statement_hash")
                or statement_hash(authority.get("normalized_statement", "")),
            "normalized_concepts": sorted({item.strip().casefold() for item in concepts if item.strip()}),
            "keywords": sorted({item.strip().casefold() for item in keywords if item.strip()}),
            "authors": copy.deepcopy(authority.get("authors", [])),
            "source_identifier": authority.get("DOI_or_stable_identifier"),
            "obligation_tags": copy.deepcopy(authority.get("used_by_obligations", [])),
            "added_at": utc_now(),
        }
        with self._lock:
            data = self.load()
            data["entries"][authority["authority_id"]] = entry
            _write_json(self.path, data)
        return copy.deepcopy(entry)

    def search(
        self, *, normalized_statement: str | None = None,
        concepts: list[str] | None = None, keywords: list[str] | None = None,
    ) -> list[dict]:
        target_hash = statement_hash(normalized_statement) if normalized_statement else None
        concept_set = {item.strip().casefold() for item in concepts or []}
        keyword_set = {item.strip().casefold() for item in keywords or []}
        matches = []
        for entry in self.load()["entries"].values():
            if target_hash and entry.get("statement_hash") == target_hash:
                matches.append(copy.deepcopy(entry))
                continue
            if concept_set.intersection(entry.get("normalized_concepts", [])):
                matches.append(copy.deepcopy(entry))
                continue
            if keyword_set.intersection(entry.get("keywords", [])):
                matches.append(copy.deepcopy(entry))
        return matches


class NegativeLiteratureMemory:
    def __init__(self, root: str | Path):
        root = Path(root)
        self.path = root if root.suffix == ".json" else root / "negative_literature_memory.json"
        self._lock = threading.RLock()

    def load(self) -> dict:
        value = _read_json(self.path, {"schema_version": 1, "entries": []})
        if value.get("schema_version") != 1:
            raise ProjectError("Unsupported negative literature memory schema")
        value.setdefault("entries", [])
        return value

    def record(self, *, query: str, concept: str, source: str, result: str,
               why_insufficient: str) -> dict:
        entry = {
            "query": query,
            "concept": concept,
            "source": source,
            "date": utc_now(),
            "result": result,
            "why_insufficient": why_insufficient,
        }
        with self._lock:
            data = self.load()
            data["entries"].append(entry)
            _write_json(self.path, data)
        return copy.deepcopy(entry)

    def exact_duplicate(self, *, query: str, concept: str, source: str) -> bool:
        key = (query.strip().casefold(), concept.strip().casefold(), source.strip().casefold())
        return any(
            (
                item.get("query", "").strip().casefold(),
                item.get("concept", "").strip().casefold(),
                item.get("source", "").strip().casefold(),
            ) == key
            for item in self.load()["entries"]
        )


@dataclass(slots=True)
class LiteratureSynthesis:
    current_obligation: str
    search_decomposition: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    sources_discovered: list[dict] = field(default_factory=list)
    sources_deep_read: list[dict] = field(default_factory=list)
    exact_relevant_theorems: list[dict] = field(default_factory=list)
    hypothesis_compatibility: list[dict] = field(default_factory=list)
    notation_translation: list[dict] = field(default_factory=list)
    stronger_weaker_relationships: list[str] = field(default_factory=list)
    relevant_proof_methods: list[str] = field(default_factory=list)
    conflicts_and_uncertainty: list[str] = field(default_factory=list)
    what_is_already_solved: list[str] = field(default_factory=list)
    what_still_needs_proof: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    literature_verdict: str = "INSUFFICIENT_SEARCH"

    def __post_init__(self) -> None:
        if self.literature_verdict not in LITERATURE_VERDICTS:
            raise ProjectError(f"Unknown literature verdict: {self.literature_verdict}")
        if (
            self.literature_verdict == "NO_SUFFICIENT_RESULT_FOUND"
            and self.conflicts_and_uncertainty
            and any("budget" in item.casefold() or "unavailable" in item.casefold()
                    for item in self.conflicts_and_uncertainty)
        ):
            raise ProjectError(
                "Budget/tool exhaustion must use INSUFFICIENT_SEARCH, not NO_SUFFICIENT_RESULT_FOUND"
            )

    def render(self) -> str:
        def bullets(items: list[Any]) -> str:
            if not items:
                return "- (none)"
            return "\n".join(
                "- " + (json.dumps(item, ensure_ascii=False, sort_keys=True)
                         if isinstance(item, dict) else str(item))
                for item in items
            )

        return f"""# LITERATURE_SYNTHESIS

## 1. Current obligation

{self.current_obligation}

## 2. Search decomposition

{bullets(self.search_decomposition)}

## 3. Search queries

{bullets(self.search_queries)}

## 4. Sources discovered

{bullets(self.sources_discovered)}

## 5. Sources deep-read

{bullets(self.sources_deep_read)}

## 6. Exact relevant theorems

{bullets(self.exact_relevant_theorems)}

## 7. Hypothesis compatibility

{bullets(self.hypothesis_compatibility)}

## 8. Notation translation

{bullets(self.notation_translation)}

## 9. Stronger/weaker relationships

{bullets(self.stronger_weaker_relationships)}

## 10. Relevant proof methods

{bullets(self.relevant_proof_methods)}

## 11. Conflicts and uncertainty

{bullets(self.conflicts_and_uncertainty)}

## 12. WHAT_IS_ALREADY_SOLVED

{bullets(self.what_is_already_solved)}

## 13. WHAT_STILL_NEEDS_PROOF

{bullets(self.what_still_needs_proof)}

## 14. Recommended next action

{self.recommended_next_action or '(none)'}

## 15. Literature verdict

`{self.literature_verdict}`
"""

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path


def literature_provider_status(config: dict) -> dict:
    """Report configured capability without pretending discovery occurred."""

    candidates = []
    tiers = config.get("tiers", {}) if isinstance(config.get("tiers"), dict) else {}
    roles = config.get("roles", {}) if isinstance(config.get("roles"), dict) else {}
    for name, route in {**tiers, **roles}.items():
        if not isinstance(route, dict):
            continue
        if name in {"routine", "research", "literature_searcher", "literature_lead"}:
            candidates.append(route)
    search_routes = [
        route for route in candidates
        if route.get("enabled", True)
        and route.get("provider") in {"gemini", "vertex_gemini"}
        and route.get("allow_web_search", False)
    ]
    transmission_approved = bool(
        config.get("literature", {}).get("external_transmission_approved", False)
    )
    return {
        "real_search_provider_configured": bool(search_routes),
        "external_transmission_approved": transmission_approved,
        "operational_for_campaign": bool(search_routes and transmission_approved),
        "providers": sorted({str(route.get("provider")) for route in search_routes}),
        "models": sorted({str(route.get("model")) for route in search_routes}),
        "full_text_retrieval": bool(config.get("literature", {}).get("full_text_retrieval", False)),
        "pdf_deep_read": bool(config.get("literature", {}).get("pdf_deep_read", False)),
        "stable_scholarly_metadata": bool(
            config.get("literature", {}).get("scholarly_metadata_adapter", False)
        ),
        "fallback": (
            "LITERATURE_PROVIDER_UNAVAILABLE; proof fallback is explicit"
            if config.get("routing", {}).get(
                "allow_proof_fallback_when_literature_unavailable", False
            )
            else "LITERATURE_PROVIDER_UNAVAILABLE; obligation remains pending"
        ),
    }


class LiteratureTaskExecutor:
    """Execute bounded Literature tasks with an explicit transmission gate.

    External search never receives a project context bundle.  A search task
    must carry a Planner-approved, minimized ``public_query`` and an explicit
    per-task approval flag.  The example configuration keeps campaign-level
    transmission disabled.
    """

    def __init__(
        self,
        scheduler,
        router: ModelRouter,
        *,
        client_factory,
        archive_dir: str | Path,
        working_dir: str | Path,
        external_transmission_approved: bool = False,
        authority_registry: ExternalAuthorityRegistry | None = None,
        scholarly_adapter=None,
        document_retriever=None,
    ):
        self.scheduler = scheduler
        self.router = router
        self.client_factory = client_factory
        self.archive_dir = Path(archive_dir)
        self.working_dir = Path(working_dir)
        self.external_transmission_approved = bool(external_transmission_approved)
        self.authority_registry = authority_registry
        self.scholarly_adapter = scholarly_adapter
        self.document_retriever = document_retriever

    def __call__(self, task: dict, context=None) -> dict:
        payload = task.get("payload", {})
        role = task["role"]
        if role == "literature_lead":
            return self._build_public_search_plan(task)
        if role == "literature_searcher":
            if payload.get("external_search_approved") is not True:
                return {
                    "search_status": "SEARCH_NOT_AUTHORIZED",
                    "reason": "Literature search task lacks per-task public-query approval",
                    "public_query": str(payload.get("public_query") or ""),
                }
            public_query = str(payload.get("public_query") or "").strip()
            if not public_query:
                raise ProjectError("Literature search requires a minimized public_query")
            expected_query_hash = "sha256:" + hashlib.sha256(
                public_query.encode("utf-8")
            ).hexdigest()
            if payload.get("query_hash") != expected_query_hash:
                raise ProjectError("Literature search query hash does not match approved query")
            if not payload.get("approval_source") or not payload.get("approval_timestamp"):
                return {
                    "search_status": "SEARCH_NOT_AUTHORIZED",
                    "reason": "Public-query approval provenance is incomplete",
                    "public_query": public_query,
                }
            if self.scholarly_adapter is not None and payload.get(
                "use_scholarly_adapter", False
            ):
                records = self.scholarly_adapter.search(
                    public_query,
                    provider_names=payload.get("scholarly_providers"),
                    limit=int(payload.get("limit", 10)),
                    force_refresh=bool(payload.get("force_refresh", False)),
                )
                preferences = payload.get("source_preferences")
                if isinstance(preferences, dict):
                    required_doi = str(preferences.get("doi") or "").strip().casefold()
                    required_id = str(preferences.get("stable_identifier") or "").strip().casefold()
                    if required_doi or required_id:
                        records = [
                            record for record in records
                            if (
                                bool(required_doi)
                                and (record.doi or "").casefold() == required_doi
                            ) or (
                                bool(required_id)
                                and required_id in {
                                str(record.source_id).casefold(),
                                str(record.doi or "").casefold(),
                                str(record.arxiv_id or "").casefold(),
                                }
                            )
                        ]
                sources = [record.to_literature_source() for record in records]
                return {
                    "provider": "scholarly_adapter",
                    "public_query": public_query,
                    "sources": sources,
                    "search_status": (
                        "NETWORK_DISCOVERY_PASS" if sources else "NO_DISCOVERY_RESULTS"
                    ),
                    "metadata_only": True,
                    "stable_identifiers": [
                        source.get("DOI_or_stable_identifier") for source in sources
                    ],
                }
            prompt_body = {"public_query": public_query, "strategy": payload.get("strategy")}
        elif role in {"literature_reader", "literature_deep_reader"} and self.document_retriever is not None:
            return self._retrieve_and_extract(task)
        elif role == "literature_synthesizer" and self.document_retriever is not None:
            return self._synthesize_artifact_candidate(task)
        elif role == "literature_authority_auditor" and self.authority_registry is not None:
            return self._verify_stored_candidate(task)
        else:
            # Non-search calls receive only the single target statement and the
            # task-local source locator, never the project or repository bundle.
            snapshot = self.scheduler.snapshot()
            obligation = snapshot["obligations"][task["obligation_id"]]
            prompt_body = {
                "target_statement": obligation["target_statement"],
                "task_payload": payload,
            }
        if not self.external_transmission_approved and role != "literature_searcher":
            return {
                "literature_verdict": "LITERATURE_PROVIDER_UNAVAILABLE",
                "reason": "private-context transmission was not approved",
            }
        if payload.get("minimum_tier") == "research":
            self.router.escalate(
                task["obligation_id"],
                reason="external_authority_verification",
                minimum_tier="research",
            )
        client = RoutedLLMClient(
            self.router,
            client_factory=self.client_factory,
            default_role=role,
            archive_dir=self.archive_dir / task["task_id"],
            working_dir=self.working_dir / task["task_id"],
        )
        if context is not None:
            context.set_handle(client)
        prompt = (
            f"[Worker role: {role}]\n"
            f"[Obligation ID: {task['obligation_id']}]\n"
            + json.dumps(prompt_body, ensure_ascii=False, indent=2)
        )
        try:
            response = client.call(
                prompt,
                self._system_prompt(role),
                label=f"{role}_{task['task_id']}",
                archive_path=self.archive_dir / f"{task['task_id']}_call.md",
                web_search=role == "literature_searcher",
                response_schema=LiteratureResultSchema,
            )
            try:
                result = parse_structured_response(
                    response, LiteratureResultSchema
                ).model_dump(mode="python")
            except SchemaError as exc:
                raise ProjectError(
                    f"{role} returned invalid structured output: {exc}"
                ) from exc
            if role == "literature_authority_auditor":
                result = self._deterministic_authority_gate(result, task)
            verdict = result.get("literature_verdict")
            if verdict == "CONFLICTING_LITERATURE" or result.get(
                "architecture_changing", False
            ):
                self.router.escalate(
                    task["obligation_id"],
                    reason=(
                        "conflicting_literature"
                        if verdict == "CONFLICTING_LITERATURE"
                        else "architecture_changing_literature"
                    ),
                    minimum_tier="strategic",
                )
            if role == "literature_synthesizer":
                fields = LiteratureSynthesis.__dataclass_fields__
                synthesis = LiteratureSynthesis(**{
                    key: value for key, value in result.items() if key in fields
                })
                synthesis_path = (
                    self.archive_dir / task["task_id"] / "LITERATURE_SYNTHESIS.md"
                )
                synthesis.write(synthesis_path)
                result["synthesis_path"] = str(synthesis_path)
            return result
        finally:
            client.cleanup()

    def _build_public_search_plan(self, task: dict) -> dict:
        """Convert Worker hints into proposals; approval remains scheduler-owned."""

        payload = task.get("payload", {})
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        if not request:
            obligation = self.scheduler.snapshot()["obligations"][task["obligation_id"]]
            request = obligation.get("literature_request") or {}
        if not request:
            return {
                "literature_verdict": "LITERATURE_PROVIDER_UNAVAILABLE",
                "reason": "no approved structured literature request is available",
            }
        hints = request.get("search_hints")
        queries: list[tuple[str, str]] = []
        source_preferences = {}
        if isinstance(hints, str) and hints.strip():
            queries.append(("exact_theorem", hints))
        elif isinstance(hints, list):
            for index, value in enumerate(hints):
                if str(value).strip():
                    queries.append(("exact_theorem" if index == 0 else "equivalent_formulation", str(value)))
        elif isinstance(hints, dict):
            source_preferences = copy.deepcopy(hints.get("source_preferences") or {})
            if hints.get("doi"):
                source_preferences.setdefault("doi", hints["doi"])
            explicit = hints.get("public_queries") or hints.get("queries")
            if isinstance(explicit, dict):
                queries.extend((str(strategy), str(query)) for strategy, query in explicit.items())
            elif isinstance(explicit, list):
                queries.extend(("exact_theorem" if index == 0 else "equivalent_formulation", str(query)) for index, query in enumerate(explicit))
            elif hints.get("public_query"):
                queries.append((str(hints.get("strategy") or "exact_theorem"), str(hints["public_query"])))
            elif hints.get("keywords"):
                keywords = hints["keywords"]
                query = " ".join(str(item) for item in keywords) if isinstance(keywords, list) else str(keywords)
                queries.append(("keyword_search", query))
        search_tasks = [
            {
                "strategy": strategy,
                "public_query": " ".join(query.strip().split()),
                "reason": str(request.get("why_needed") or "bounded literature request"),
                "priority": "HIGH" if index == 0 else "MEDIUM",
                "source_preferences": copy.deepcopy(source_preferences),
                "limit": 10,
            }
            for index, (strategy, query) in enumerate(queries)
            if str(query).strip()
        ]
        if not search_tasks:
            raise ProjectError("Literature Lead could not propose a non-empty public_query")
        return {
            "search_plan_status": "PUBLIC_QUERY_PROPOSED",
            "search_tasks": search_tasks,
            "literature_request_id": request.get("literature_request_id"),
            "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
        }

    def _retrieve_and_extract(self, task: dict) -> dict:
        snapshot = self.scheduler.snapshot()
        source_id = str(task.get("payload", {}).get("source_id") or "")
        source = snapshot.get("sources", {}).get(source_id)
        if not isinstance(source, dict):
            raise ProjectError(f"Reader source is unavailable: {source_id}")
        candidates = []
        for value in (
            source.get("full_text_url"), source.get("source"),
            *[
                item.get("url") for item in source.get("related_versions", [])
                if isinstance(item, dict)
            ],
        ):
            value = str(value or "").strip()
            if not value:
                continue
            pmc = re.search(r"(?:pmc/articles/|articles/PMC)(\d+)", value, re.I)
            if pmc:
                candidates.append(f"https://europepmc.org/articles/PMC{pmc.group(1)}?pdf=render")
            candidates.append(value)
        last_error = None
        artifact = None
        for url in dict.fromkeys(candidates):
            try:
                artifact = self.document_retriever.retrieve(
                    {**source, "full_text_url": url},
                    source_id=str(source.get("DOI_or_stable_identifier") or source_id),
                    extract_theorems=True,
                )
                if artifact.theorem_extracts:
                    break
            except ProjectError as exc:
                last_error = str(exc)
        if artifact is None or not artifact.theorem_extracts:
            return {
                "reader_verdict": "FULL_TEXT_UNAVAILABLE",
                "source_id": source_id,
                "reason": last_error or "no theorem span could be extracted",
                "theorems": [],
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
            }
        return {
            "reader_verdict": "THEOREM_EXTRACTED",
            "source_id": source_id,
            "artifact": {key: value for key, value in artifact.to_dict().items() if key != "extracted_text"},
            "theorems": copy.deepcopy(artifact.theorem_extracts),
            "retrieval_status": "PDF_RETRIEVAL_PASS" if artifact.media_type == "application/pdf" else "HTML_RETRIEVAL_PASS",
            "extraction_status": "THEOREM_EXTRACTION_PASS",
            "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
        }

    def _synthesize_artifact_candidate(self, task: dict) -> dict:
        snapshot = self.scheduler.snapshot()
        obligation = snapshot["obligations"][task["obligation_id"]]
        context = obligation.get("context") or {}
        expected_label = str(context.get("expected_theorem_label") or "").casefold()
        readers = [
            item for item in snapshot["tasks"].values()
            if item.get("obligation_id") == task["obligation_id"]
            and item.get("role") in {"literature_reader", "literature_deep_reader"}
            and item.get("status") in {"COMPLETE", "COMPLETED_BEFORE_CANCEL"}
            and isinstance(item.get("result"), dict)
        ]
        selected_task = None
        selected_extract = None
        for reader in readers:
            for extraction in reader["result"].get("theorems", []):
                label = str(extraction.get("theorem_label") or extraction.get("label") or "")
                if not expected_label or label.casefold() == expected_label:
                    selected_task, selected_extract = reader, extraction
                    break
            if selected_extract:
                break
        if selected_task is None or selected_extract is None:
            return {
                "literature_verdict": "INSUFFICIENT_SEARCH",
                "reason": "no extracted theorem matched the requested public label",
            }
        artifact = selected_task["result"]["artifact"]
        source = snapshot["sources"][selected_task["result"]["source_id"]]
        if self.authority_registry is None:
            raise ProjectError("Production synthesis requires ExternalAuthorityRegistry")
        root = self.authority_registry.root.resolve()

        def relative(path_value: str) -> str:
            path = Path(path_value).resolve()
            try:
                return str(path.relative_to(root)).replace("\\", "/")
            except ValueError as exc:
                raise ProjectError("Literature artifact is outside authority registry root") from exc

        exact = normalize_extracted_statement(selected_extract.get("raw_extracted_text") or selected_extract.get("statement"))
        stable = str(source.get("DOI_or_stable_identifier") or source.get("canonical_identifier") or source["source_id"])
        authority_id = "ext-" + hashlib.sha256(
            (stable.casefold() + "|" + str(selected_extract.get("extraction_id"))).encode("utf-8")
        ).hexdigest()[:16]
        record = {
            "authority_id": authority_id,
            "title": source.get("title"), "authors": source.get("authors") or [],
            "year": source.get("year"), "source": source.get("source"),
            "DOI_or_stable_identifier": stable,
            "version": f"artifact {artifact['sha256']}",
            "theorem_number": selected_extract.get("theorem_label") or selected_extract.get("label"),
            "page_or_section": selected_extract.get("location"),
            "exact_statement": exact,
            "normalized_statement": normalize_extracted_statement(exact),
            "hypotheses": copy.deepcopy(context.get("external_hypotheses") or []),
            "notation_map": copy.deepcopy(context.get("notation_map") or {}),
            "retrieval_source": artifact.get("requested_url"),
            "retrieved_at": artifact.get("retrieved_at"),
            "reader_verdict": "THEOREM_EXTRACTED",
            "authority_verifier_verdict": "PENDING",
            "used_by_obligations": [task["obligation_id"]],
            "source_type": source.get("source_type") if source.get("source_type") in SOURCE_PRIORITIES else "published_version",
            "content_scope": "FULL_TEXT",
            "retrieved_content_path": relative(artifact["local_path"]),
            "retrieved_content_sha256": artifact["sha256"],
            "text_artifact_path": relative(artifact["text_path"]),
            "text_artifact_sha256": artifact["text_sha256"],
            "extraction_artifact_path": relative(artifact["extraction_artifact_path"]),
            "extraction_artifact_sha256": artifact["extraction_artifact_sha256"],
            "extraction_id": selected_extract.get("extraction_id"),
            "span_start": selected_extract.get("span_start"),
            "span_end": selected_extract.get("span_end"),
            "extracted_statement_sha256": selected_extract.get("extracted_statement_sha256"),
            "extractor_version": selected_extract.get("extractor_version"),
        }
        synthesis_path = self.archive_dir / task["task_id"] / "LITERATURE_SYNTHESIS.json"
        _write_json(synthesis_path, {
            "literature_verdict": "EXACT_RESULT_FOUND",
            "authority_candidate": record,
            "source_id": source["source_id"],
        })
        return {
            "literature_verdict": "EXACT_RESULT_FOUND",
            "authority_status": "AUTHORITY_CANDIDATE",
            "authority_record": record,
            "verification": {
                "verdict": "VERIFIED_SOURCE_THEOREM",
                "source_identity_match": True,
                "bibliographic_metadata_match": True,
                "claimed_source_type": record["source_type"],
            },
            "synthesis_path": str(synthesis_path),
            "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
        }

    def _verify_stored_candidate(self, task: dict) -> dict:
        obligation = self.scheduler.snapshot()["obligations"][task["obligation_id"]]
        record = obligation.get("authority_candidate")
        verification = obligation.get("authority_candidate_verification")
        return self._promote_candidate(record, verification)

    def _promote_candidate(self, record: Any, verification: Any) -> dict:
        base = {
            "literature_verdict": "EXACT_RESULT_FOUND",
            "authority_status": "AUTHORITY_CANDIDATE",
            "authority_verification_candidate": True,
        }
        if self.authority_registry is None or not isinstance(record, dict) or not isinstance(verification, dict):
            base["authority_status"] = "AUTHORITY_VERIFICATION_FAILED"
            base["authority_verification_errors"] = ["deterministic authority candidate input is incomplete"]
            return base
        try:
            registered = self.authority_registry.register(record)
            verified = self.authority_registry.verify(registered["authority_id"], verification)
        except ProjectError as exc:
            base["authority_status"] = "AUTHORITY_VERIFICATION_FAILED"
            base["authority_verification_errors"] = [str(exc)]
            return base
        if verified.get("status") != "VERIFIED_SOURCE_THEOREM":
            base["authority_status"] = "AUTHORITY_VERIFICATION_FAILED"
            base["authority_verification_errors"] = verified.get("authority_verification_errors", [])
            return base
        base.update({
            "authority_status": "VERIFIED_SOURCE_THEOREM",
            "deterministic_verification": True,
            "authority_id": verified["authority_id"],
            "authority_record_path": str(self.authority_registry.path),
        })
        return base

    def _deterministic_authority_gate(self, result: dict, task: dict) -> dict:
        """Turn an LLM verdict into a candidate; only the registry promotes it."""
        claimed = result.get("authority_status") or result.get("verdict")
        if claimed != "VERIFIED_SOURCE_THEOREM":
            return result
        record = result.get("authority_record") or result.get("authority")
        verification = result.get("verification")
        base = dict(result)
        base["authority_status"] = "AUTHORITY_CANDIDATE"
        base["authority_verification_candidate"] = True
        promoted = self._promote_candidate(record, verification)
        promoted.update({
            key: value for key, value in base.items()
            if key not in promoted
        })
        return promoted

    @staticmethod
    def _system_prompt(role: str) -> str:
        return (
            f"You are the bounded {role}. Return one JSON object. Bibliographic memory "
            "may generate queries only. Never invent a title, author, DOI, theorem number, "
            "page, or quotation. Abstract/metadata is discovery evidence only. If exact "
            "source text is unavailable, return UNVERIFIED_REFERENCE."
        )
