from __future__ import annotations

import json

import pytest

from openprover.math_research.scholarly import (
    CrossrefProvider,
    FullTextRetriever,
    OpenAlexProvider,
    ScholarlyProviderError,
    ScholarlySearchAdapter,
)


def test_theorem_extraction_stops_before_unmistakable_paper_narrative():
    text = (
        "PROPOSITION 2. The sums of squared lengths for an orthonormal basis "
        "onto an m-dimensional subspace is m. Our eighth variation is an "
        "interesting alteration of the preceding statement. PROPOSITION 3. "
        "If H, then C. Proof: immediate."
    )
    extracts = FullTextRetriever.extract_theorems(text)
    proposition = next(item for item in extracts if item["theorem_label"] == "PROPOSITION 2")
    assert proposition["normalized_extracted_text"].endswith("is m.")
    assert "Our eighth variation" not in proposition["normalized_extracted_text"]


def _openalex_payload():
    return {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "title": "A public theorem about integers",
                "publication_year": 2020,
                "doi": "https://doi.org/10.1000/example",
                "ids": {
                    "doi": "https://doi.org/10.1000/example",
                    "arxiv": "https://arxiv.org/abs/2001.00001",
                },
                "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                "primary_location": {
                    "landing_page_url": "https://example.org/paper",
                    "pdf_url": "https://example.org/paper.pdf",
                    "source": {"display_name": "Journal of Public Mathematics"},
                },
                "open_access": {"oa_url": "https://example.org/paper.pdf"},
                "abstract_inverted_index": {
                    "Every": [0],
                    "integer": [1],
                    "has": [2],
                    "a": [3],
                    "property": [4],
                },
                "locations": [],
            }
        ]
    }


def test_openalex_normalizes_identifiers_and_uses_cache(tmp_path):
    calls = []

    def request(url, _headers, _timeout):
        calls.append(url)
        return 200, {"Content-Type": "application/json"}, json.dumps(_openalex_payload()).encode()

    provider = OpenAlexProvider(cache_dir=tmp_path, request_fn=request, minimum_interval=0)
    first = provider.search("integer theorem", limit=1)
    second = provider.search("integer theorem", limit=1)
    assert len(first) == len(second) == 1
    assert first[0].doi == "10.1000/example"
    assert first[0].arxiv_id == "2001.00001"
    assert first[0].abstract == "Every integer has a property"
    assert first[0].full_text_url.endswith("paper.pdf")
    assert len(calls) == 1


def test_adapter_deduplicates_cross_provider_versions(tmp_path):
    def request(url, _headers, _timeout):
        if "openalex" in url:
            return 200, {}, json.dumps(_openalex_payload()).encode()
        return (
            200,
            {},
            json.dumps(
                {
                    "message": {
                        "items": [
                            {
                                "title": ["A public theorem about integers"],
                                "DOI": "10.1000/example",
                                "author": [{"given": "Ada", "family": "Lovelace"}],
                                "issued": {"date-parts": [[2020]]},
                                "URL": "https://doi.org/10.1000/example",
                            }
                        ]
                    }
                }
            ).encode(),
        )

    adapter = ScholarlySearchAdapter(
        [
            OpenAlexProvider(cache_dir=tmp_path, request_fn=request, minimum_interval=0),
            CrossrefProvider(cache_dir=tmp_path, request_fn=request, minimum_interval=0),
        ]
    )
    records = adapter.search("integer theorem", limit=5)
    assert len(records) == 1
    assert any(item["provider"] == "crossref" for item in records[0].related_versions)


def test_provider_errors_have_stable_classification(tmp_path):
    def request(_url, _headers, _timeout):
        return 429, {"Retry-After": "0"}, b"{}"

    provider = OpenAlexProvider(
        cache_dir=tmp_path, request_fn=request, max_retries=1, minimum_interval=0
    )
    with pytest.raises(ScholarlyProviderError) as error:
        provider.search("rate limited")
    assert error.value.error_type == "RATE_LIMITED"
    assert error.value.retryable is True


def test_full_text_artifact_is_hashed_cached_and_theorem_extracted(tmp_path):
    body = b"<html><body><h1>Theorem 1.</h1><p>Every integer has a unique property under the stated hypotheses and this is enough for the smoke.</p></body></html>"
    calls = []

    def request(_url, _headers, _timeout):
        calls.append(True)
        return 200, {"Content-Type": "text/html"}, body

    retriever = FullTextRetriever(tmp_path, request_fn=request)
    first = retriever.retrieve("https://example.org/theorem.html", source_id="doi:10.1000/example")
    second = retriever.retrieve("https://example.org/theorem.html", source_id="doi:10.1000/example")
    assert first.sha256.startswith("sha256:") is False
    assert first.media_type == "text/html"
    assert first.extraction_method == "html_text"
    assert first.theorem_extracts[0]["label"].casefold() == "theorem 1"
    assert second.cache_hit is True
    assert len(calls) == 1
