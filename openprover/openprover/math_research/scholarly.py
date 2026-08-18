"""Bounded public scholarly metadata and full-text retrieval adapters.

The adapter has a deliberately small trust surface: only a caller-supplied
public query is sent to a provider, responses are cached verbatim, records are
normalized into stable identifiers, and full text is retained as a hashed local
artifact before theorem extraction is attempted.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from .project import ProjectError, utc_now


class ScholarlyProviderError(ProjectError):
    """Structured provider failure with a stable, auditable classification."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        error_type: str,
        status: int | None = None,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.error_type = error_type
        self.status = status
        self.retryable = bool(retryable)
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "error_type": self.error_type,
            "status": self.status,
            "retryable": self.retryable,
            "message": str(self),
            "details": copy.deepcopy(self.details),
        }


@dataclass(slots=True)
class ScholarlyRecord:
    source_id: str
    provider: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    stable_ids: list[str] = field(default_factory=list)
    abstract: str | None = None
    source_url: str | None = None
    full_text_url: str | None = None
    source_type: str = "abstract_or_metadata"
    retrieved_at: str | None = None
    query: str | None = None
    related_versions: list[dict[str, Any]] = field(default_factory=list)
    provider_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["DOI_or_stable_identifier"] = self.doi or self.arxiv_id or self.source_id
        value["stable_identifier"] = self.doi or self.arxiv_id or self.source_id
        return value

    def to_literature_source(self) -> dict[str, Any]:
        """Map to the scheduler's source shape without asserting authority."""

        return {
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "venue": self.venue,
            "DOI_or_stable_identifier": self.doi or self.arxiv_id or self.source_id,
            "source": self.source_url or self.full_text_url or self.source_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "abstract": self.abstract,
            "full_text_url": self.full_text_url,
            "related_versions": copy.deepcopy(self.related_versions),
            "provider": self.provider,
        }


class ScholarlyProvider(Protocol):
    name: str

    def search(
        self, query: str, *, limit: int = 10, force_refresh: bool = False
    ) -> list[ScholarlyRecord]: ...


def _normal_query(query: str) -> str:
    return " ".join(str(query or "").strip().split())


def _hash_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _strip_doi(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I)
    return text.rstrip("/ ").lower() or None


def _arxiv_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+)", text, re.I)
    if match:
        return match.group(1).removesuffix(".pdf")
    if re.fullmatch(r"(?:[a-z-]+/)?\d{4}\.\d{4,5}(?:v\d+)?", text, re.I):
        return text
    return None


def _abstract_from_inverted(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    words: list[tuple[int, str]] = []
    for word, positions in value.items():
        if isinstance(positions, list):
            for position in positions:
                try:
                    words.append((int(position), str(word)))
                except (TypeError, ValueError):
                    continue
    if not words:
        return None
    return " ".join(word for _, word in sorted(words))


class _RateLimiter:
    def __init__(self, minimum_interval: float):
        self.minimum_interval = max(0.0, float(minimum_interval))
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self.minimum_interval - (now - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


RequestFn = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]


def _urllib_request(
    url: str, headers: Mapping[str, str], timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ScholarlyProviderError(
            f"public request failed: {exc}",
            provider="http",
            error_type="TEMPORARY_NETWORK_FAILURE",
            retryable=True,
        ) from exc


class _CachedJSONProvider:
    name = "provider"

    def __init__(
        self,
        *,
        cache_dir: str | Path,
        timeout: float = 20.0,
        max_retries: int = 2,
        minimum_interval: float = 0.15,
        request_fn: RequestFn | None = None,
        user_agent: str = "OpenProver-ScholarlyAdapter/1.0 (public metadata only)",
    ):
        self.cache_dir = Path(cache_dir)
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.request_fn = request_fn or _urllib_request
        self.user_agent = user_agent
        self._rate = _RateLimiter(minimum_interval)

    def _cache_path(self, query: str, limit: int) -> Path:
        return self.cache_dir / self.name / f"{_hash_key(self.name, query, str(limit))}.json"

    def _get_json(self, url: str, *, query: str, limit: int, force_refresh: bool) -> dict[str, Any]:
        cache_path = self._cache_path(query, limit)
        if cache_path.exists() and not force_refresh:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
                    return cached["payload"]
            except (OSError, json.JSONDecodeError):
                pass
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        last_error: ScholarlyProviderError | None = None
        for attempt in range(self.max_retries + 1):
            self._rate.wait()
            try:
                status, response_headers, body = self.request_fn(url, headers, self.timeout)
            except ScholarlyProviderError as exc:
                last_error = ScholarlyProviderError(
                    str(exc),
                    provider=self.name,
                    error_type=exc.error_type,
                    status=exc.status,
                    retryable=exc.retryable,
                    details=exc.details,
                )
                if not exc.retryable or attempt >= self.max_retries:
                    raise last_error
                time.sleep(min(2.0, 0.25 * (2**attempt)))
                continue
            if status == 429 or status >= 500:
                last_error = ScholarlyProviderError(
                    f"{self.name} returned HTTP {status}",
                    provider=self.name,
                    error_type="RATE_LIMITED" if status == 429 else "TEMPORARY_NETWORK_FAILURE",
                    status=status,
                    retryable=True,
                    details={"retry_after": response_headers.get("Retry-After")},
                )
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.25 * (2**attempt)))
                    continue
                raise last_error
            if status in {401, 403}:
                raise ScholarlyProviderError(
                    f"{self.name} rejected the public request (HTTP {status})",
                    provider=self.name,
                    error_type="AUTH_FAILURE",
                    status=status,
                )
            if status == 404:
                raise ScholarlyProviderError(
                    f"{self.name} endpoint was not found",
                    provider=self.name,
                    error_type="NOT_FOUND",
                    status=status,
                )
            if status < 200 or status >= 300:
                raise ScholarlyProviderError(
                    f"{self.name} returned HTTP {status}",
                    provider=self.name,
                    error_type="PROVIDER_UNAVAILABLE",
                    status=status,
                )
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ScholarlyProviderError(
                    f"{self.name} returned malformed JSON",
                    provider=self.name,
                    error_type="MALFORMED_RESPONSE",
                    status=status,
                ) from exc
            if not isinstance(payload, dict):
                raise ScholarlyProviderError(
                    f"{self.name} response is not an object",
                    provider=self.name,
                    error_type="MALFORMED_RESPONSE",
                    status=status,
                )
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {"retrieved_at": utc_now(), "payload": payload}, ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            return payload
        raise last_error or ScholarlyProviderError(
            "provider request failed", provider=self.name, error_type="PROVIDER_UNAVAILABLE"
        )


class OpenAlexProvider(_CachedJSONProvider):
    """OpenAlex public works search (no API key; metadata only)."""

    name = "openalex"
    endpoint = "https://api.openalex.org/works"

    def search(
        self, query: str, *, limit: int = 10, force_refresh: bool = False
    ) -> list[ScholarlyRecord]:
        query = _normal_query(query)
        if not query:
            raise ProjectError("scholarly query must not be empty")
        limit = max(1, min(50, int(limit)))
        params = urllib.parse.urlencode({"search": query, "per-page": limit})
        payload = self._get_json(
            f"{self.endpoint}?{params}", query=query, limit=limit, force_refresh=force_refresh
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise ScholarlyProviderError(
                "OpenAlex response has no results list",
                provider=self.name,
                error_type="MALFORMED_RESPONSE",
            )
        records: list[ScholarlyRecord] = []
        for item in results:
            if not isinstance(item, dict) or not str(item.get("title") or "").strip():
                continue
            source_id = str(item.get("id") or "").strip()
            if not source_id:
                continue
            doi = _strip_doi(item.get("doi") or (item.get("ids") or {}).get("doi"))
            ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
            arxiv = _arxiv_id(ids.get("arxiv"))
            authors = []
            for authorship in item.get("authorships") or []:
                if isinstance(authorship, dict):
                    author = authorship.get("author") or {}
                    if author.get("display_name"):
                        authors.append(str(author["display_name"]))
            primary = (
                item.get("primary_location")
                if isinstance(item.get("primary_location"), dict)
                else {}
            )
            source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
            best_oa = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
            full_text_url = (
                best_oa.get("oa_url")
                or primary.get("pdf_url")
                or ((primary.get("landing_page_url") or "") if isinstance(primary, dict) else "")
            )
            locations = []
            for location in item.get("locations") or []:
                if not isinstance(location, dict):
                    continue
                url = location.get("pdf_url") or location.get("landing_page_url")
                if url and url != full_text_url:
                    locations.append(
                        {
                            "url": url,
                            "version": location.get("version"),
                            "is_oa": location.get("is_oa"),
                        }
                    )
            source_type = (
                "author_preprint"
                if arxiv
                else ("published_version" if doi else "abstract_or_metadata")
            )
            records.append(
                ScholarlyRecord(
                    source_id=source_id,
                    provider=self.name,
                    title=str(item.get("title") or "").strip(),
                    authors=authors,
                    year=int(item["publication_year"])
                    if str(item.get("publication_year") or "").isdigit()
                    else None,
                    venue=str(source.get("display_name") or "").strip() or None,
                    doi=doi,
                    arxiv_id=arxiv,
                    stable_ids=[value for value in (doi, arxiv, source_id) if value],
                    abstract=_abstract_from_inverted(item.get("abstract_inverted_index")),
                    source_url=str(primary.get("landing_page_url") or item.get("doi") or source_id),
                    full_text_url=str(full_text_url) if full_text_url else None,
                    source_type=source_type,
                    retrieved_at=utc_now(),
                    query=query,
                    related_versions=locations,
                    provider_payload={
                        "id": source_id,
                        "cited_by_count": item.get("cited_by_count"),
                    },
                )
            )
        return records


class CrossrefProvider(_CachedJSONProvider):
    """Optional Crossref secondary metadata provider."""

    name = "crossref"
    endpoint = "https://api.crossref.org/works"

    def search(
        self, query: str, *, limit: int = 10, force_refresh: bool = False
    ) -> list[ScholarlyRecord]:
        query = _normal_query(query)
        if not query:
            raise ProjectError("scholarly query must not be empty")
        limit = max(1, min(50, int(limit)))
        params = urllib.parse.urlencode({"query": query, "rows": limit})
        payload = self._get_json(
            f"{self.endpoint}?{params}", query=query, limit=limit, force_refresh=force_refresh
        )
        items = (
            (payload.get("message") or {}).get("items")
            if isinstance(payload.get("message"), dict)
            else None
        )
        if not isinstance(items, list):
            raise ScholarlyProviderError(
                "Crossref response has no items list",
                provider=self.name,
                error_type="MALFORMED_RESPONSE",
            )
        records = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (
                (item.get("title") or [""])[0]
                if isinstance(item.get("title"), list)
                else item.get("title")
            )
            doi = _strip_doi(item.get("DOI"))
            if not doi or not str(title or "").strip():
                continue
            authors = []
            for author in item.get("author") or []:
                if isinstance(author, dict):
                    name = " ".join(
                        str(author.get(key) or "").strip() for key in ("given", "family")
                    ).strip()
                    if name:
                        authors.append(name)
            issued = item.get("issued") if isinstance(item.get("issued"), dict) else {}
            date_parts = (
                issued.get("date-parts") if isinstance(issued.get("date-parts"), list) else []
            )
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            records.append(
                ScholarlyRecord(
                    source_id=f"doi:{doi}",
                    provider=self.name,
                    title=str(title).strip(),
                    authors=authors,
                    year=int(year) if str(year or "").isdigit() else None,
                    venue=str(
                        (item.get("container-title") or [""])[0]
                        if isinstance(item.get("container-title"), list)
                        else item.get("container-title") or ""
                    ).strip()
                    or None,
                    doi=doi,
                    stable_ids=[doi],
                    source_url=str(item.get("URL") or f"https://doi.org/{doi}"),
                    source_type="published_version",
                    retrieved_at=utc_now(),
                    query=query,
                    provider_payload={"publisher": item.get("publisher"), "type": item.get("type")},
                )
            )
        return records


class ScholarlySearchAdapter:
    """Aggregate providers, deduplicate stable identities, and relate versions."""

    def __init__(self, providers: Iterable[ScholarlyProvider]):
        self.providers = {provider.name: provider for provider in providers}

    def search(
        self,
        query: str,
        *,
        provider_names: Iterable[str] | None = None,
        limit: int = 10,
        force_refresh: bool = False,
    ) -> list[ScholarlyRecord]:
        names = list(provider_names or self.providers)
        records: list[ScholarlyRecord] = []
        for name in names:
            provider = self.providers.get(str(name))
            if provider is None:
                raise ProjectError(f"Unknown scholarly provider: {name}")
            records.extend(provider.search(query, limit=limit, force_refresh=force_refresh))
        merged: dict[str, ScholarlyRecord] = {}
        for record in records:
            key = record.doi or record.arxiv_id or "title:" + _normal_query(record.title).casefold()
            if key not in merged:
                merged[key] = record
                continue
            existing = merged[key]
            if record.provider != existing.provider:
                existing.related_versions.append(
                    {
                        "provider": record.provider,
                        "source_id": record.source_id,
                        "url": record.source_url,
                        "version": record.source_type,
                    }
                )
            existing.related_versions.extend(record.related_versions)
            if not existing.abstract and record.abstract:
                existing.abstract = record.abstract
            if not existing.full_text_url and record.full_text_url:
                existing.full_text_url = record.full_text_url
        return list(merged.values())[: max(1, int(limit))]


@dataclass(slots=True)
class DocumentArtifact:
    source_id: str
    requested_url: str
    local_path: str
    media_type: str
    sha256: str
    byte_count: int
    retrieved_at: str
    cache_hit: bool = False
    text_path: str | None = None
    text_sha256: str | None = None
    extraction_method: str | None = None
    extracted_text: str | None = None
    theorem_extracts: list[dict[str, Any]] = field(default_factory=list)
    extraction_artifact_path: str | None = None
    extraction_artifact_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["sha256"] = (
            "sha256:" + value["sha256"]
            if not str(value["sha256"]).startswith("sha256:")
            else value["sha256"]
        )
        if value.get("text_sha256") and not str(value["text_sha256"]).startswith("sha256:"):
            value["text_sha256"] = "sha256:" + value["text_sha256"]
        if value.get("extraction_artifact_sha256") and not str(
            value["extraction_artifact_sha256"]
        ).startswith("sha256:"):
            value["extraction_artifact_sha256"] = "sha256:" + value["extraction_artifact_sha256"]
        return value


class FullTextRetriever:
    """Retrieve public HTML/PDF sources into a content-addressed cache."""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        timeout: float = 30.0,
        request_fn: RequestFn | None = None,
        pdftotext_path: str | Path | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.timeout = float(timeout)
        self.request_fn = request_fn or _urllib_request
        self.pdftotext_path = str(pdftotext_path) if pdftotext_path else shutil.which("pdftotext")

    def retrieve(
        self,
        source: ScholarlyRecord | Mapping[str, Any] | str,
        *,
        source_id: str | None = None,
        force_refresh: bool = False,
        extract_theorems: bool = True,
    ) -> DocumentArtifact:
        if isinstance(source, ScholarlyRecord):
            url = source.full_text_url or source.source_url
            source_key = source_id or source.source_id
        elif isinstance(source, Mapping):
            url = source.get("full_text_url") or source.get("source_url") or source.get("source")
            source_key = source_id or str(
                source.get("source_id") or source.get("DOI_or_stable_identifier") or "source"
            )
        else:
            url = str(source)
            source_key = source_id or url
        url = str(url or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ProjectError("full-text retrieval only permits http(s) URLs")
        key = _hash_key(str(source_key), url)
        meta_path = self.cache_dir / "documents" / f"{key}.json"
        if meta_path.exists() and not force_refresh:
            try:
                cached = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and Path(cached.get("local_path", "")).exists():
                    cached["cache_hit"] = True
                    return DocumentArtifact(
                        **{
                            field: cached.get(field)
                            for field in DocumentArtifact.__dataclass_fields__
                        }
                    )
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        status, headers, body = self.request_fn(
            url,
            {
                "Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "User-Agent": "OpenProver-FullTextRetriever/1.0",
            },
            self.timeout,
        )
        if status == 429 or status >= 500:
            raise ScholarlyProviderError(
                f"full-text endpoint returned HTTP {status}",
                provider="full_text",
                error_type="RATE_LIMITED" if status == 429 else "TEMPORARY_NETWORK_FAILURE",
                status=status,
                retryable=True,
            )
        if status in {401, 403}:
            raise ScholarlyProviderError(
                "full-text endpoint denied access",
                provider="full_text",
                error_type="AUTH_FAILURE",
                status=status,
            )
        if status == 404:
            raise ScholarlyProviderError(
                "full-text endpoint was not found",
                provider="full_text",
                error_type="NOT_FOUND",
                status=status,
            )
        if status < 200 or status >= 300:
            raise ScholarlyProviderError(
                f"full-text endpoint returned HTTP {status}",
                provider="full_text",
                error_type="PROVIDER_UNAVAILABLE",
                status=status,
            )
        digest = hashlib.sha256(body).hexdigest()
        content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].casefold()
        # A number of repository PDF URLs redirect to an HTML consent or
        # landing page.  Never label bytes as PDF from the URL suffix alone.
        is_pdf = body.startswith(b"%PDF-")
        if content_type == "application/pdf" and not is_pdf:
            raise ScholarlyProviderError(
                "full-text endpoint declared PDF but returned non-PDF bytes",
                provider="full_text",
                error_type="MALFORMED_RESPONSE",
                status=status,
            )
        suffix = ".pdf" if is_pdf else ".html"
        artifact_path = self.cache_dir / "documents" / f"{digest}{suffix}"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if not artifact_path.exists():
            with tempfile.NamedTemporaryFile(dir=artifact_path.parent, delete=False) as temp:
                temp.write(body)
                temp_path = Path(temp.name)
            temp_path.replace(artifact_path)
        text, method = self._extract_text(artifact_path, body, is_pdf)
        text_path = None
        text_digest = None
        if text is not None:
            text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            text_file = self.cache_dir / "text" / f"{text_digest}.txt"
            text_file.parent.mkdir(parents=True, exist_ok=True)
            if not text_file.exists():
                # Byte-stable UTF-8 is required because Registry verification
                # re-hashes the artifact; newline translation would
                # otherwise make the recorded digest unverifiable.
                text_file.write_bytes(text.encode("utf-8"))
            text_path = str(text_file)
        extracts = self.extract_theorems(text) if extract_theorems and text else []
        extraction_path = None
        extraction_digest = None
        if extracts and text_digest:
            source_digest = "sha256:" + digest
            text_digest_prefixed = "sha256:" + text_digest
            for item in extracts:
                item.update(
                    {
                        "source_artifact_sha256": source_digest,
                        "text_artifact_sha256": text_digest_prefixed,
                        "extractor_version": "openprover-theorem-span-v1",
                    }
                )
            extraction_payload = {
                "schema_version": 1,
                "source_id": str(source_key),
                "source_artifact_path": str(artifact_path),
                "source_artifact_sha256": source_digest,
                "text_artifact_path": str(text_path),
                "text_artifact_sha256": text_digest_prefixed,
                "extractor_version": "openprover-theorem-span-v1",
                "created_at": utc_now(),
                "extractions": extracts,
            }
            extraction_bytes = (
                json.dumps(extraction_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            extraction_digest = hashlib.sha256(extraction_bytes).hexdigest()
            extraction_file = self.cache_dir / "extractions" / f"{extraction_digest}.json"
            extraction_file.parent.mkdir(parents=True, exist_ok=True)
            if not extraction_file.exists():
                extraction_file.write_bytes(extraction_bytes)
            extraction_path = str(extraction_file)
        artifact = DocumentArtifact(
            source_id=str(source_key),
            requested_url=url,
            local_path=str(artifact_path),
            media_type="application/pdf" if is_pdf else (content_type or "text/html"),
            sha256=digest,
            byte_count=len(body),
            retrieved_at=utc_now(),
            text_path=text_path,
            text_sha256=text_digest,
            extraction_method=method,
            extracted_text=text,
            theorem_extracts=extracts,
            extraction_artifact_path=extraction_path,
            extraction_artifact_sha256=extraction_digest,
        )
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return artifact

    def _extract_text(self, path: Path, body: bytes, is_pdf: bool) -> tuple[str | None, str | None]:
        if is_pdf:
            if self.pdftotext_path:
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as temp:
                    output_path = Path(temp.name)
                try:
                    completed = subprocess.run(
                        [self.pdftotext_path, "-layout", str(path), str(output_path)],
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        check=False,
                    )
                    if completed.returncode == 0 and output_path.exists():
                        return output_path.read_text(
                            encoding="utf-8", errors="replace"
                        ), "pdftotext"
                finally:
                    output_path.unlink(missing_ok=True)
            return None, "pdf_unparsed"
        text = html.unescape(body.decode("utf-8", errors="replace"))
        text = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text, "html_text"

    @staticmethod
    def extract_theorems(text: str | None) -> list[dict[str, Any]]:
        if not text:
            return []
        extracts = []
        pattern = re.compile(
            r"(?is)\b((?:theorem|lemma|proposition|corollary)\s+"
            r"(?:\d+(?:\.\d+)*))"
            r"\s*[:.]?\s*(.{30,1600}?)(?=\s+(?:theorem|lemma|proposition|corollary)"
            r"\s+\d+(?:\.\d+)*\s*[:.]|\s+proof\s*:|\Z)"
        )
        for match in pattern.finditer(text):
            label = re.sub(r"\s+", " ", match.group(1)).strip()
            raw_group = match.group(2)
            cut = re.search(r"(?:†?E-mail:|\bProof:|\f)", raw_group, flags=re.I)
            raw_candidate = raw_group[: cut.start()] if cut else raw_group
            # PDF layouts sometimes omit a heading before expository prose
            # following a complete theorem.  Exclude only unmistakable paper-
            # narrative transitions; never truncate generic multi-sentence
            # mathematical statements.
            narrative = re.search(
                r"(?<=[.!?])\s+(?=(?:Our\s+\w+\s+variation\b|"
                r"We\s+(?:emphasize|present)\b))",
                raw_candidate,
                flags=re.I,
            )
            if narrative:
                raw_candidate = raw_candidate[: narrative.start()]
            trailing = re.search(r"\s+\d{3,4}\s*[–-].*$", raw_candidate)
            if trailing:
                raw_candidate = raw_candidate[: trailing.start()]
            leading_count = len(raw_candidate) - len(raw_candidate.lstrip())
            raw_candidate = raw_candidate.strip()
            span_start = match.start(2) + leading_count
            span_end = span_start + len(raw_candidate)
            raw_statement = text[span_start:span_end]
            statement = re.sub(r"\s+", " ", raw_statement).strip()
            page = text[: match.start()].count("\f") + 1
            normalized = " ".join(statement.split())
            extracts.append(
                {
                    "extraction_id": f"span-{span_start}-{span_end}",
                    "label": label,
                    "theorem_label": label,
                    "statement": statement,
                    "raw_extracted_text": raw_statement,
                    "normalized_extracted_text": normalized,
                    "extracted_statement_sha256": "sha256:"
                    + hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    "location": f"page {page}",
                    "page": page,
                    "section": None,
                    "char_start": match.start(),
                    "span_start": span_start,
                    "span_end": span_end,
                }
            )
        return extracts


__all__ = [
    "CrossrefProvider",
    "DocumentArtifact",
    "FullTextRetriever",
    "OpenAlexProvider",
    "ScholarlyProviderError",
    "ScholarlyRecord",
    "ScholarlySearchAdapter",
]
