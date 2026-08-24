"""Strict provider contracts for values that are allowed to affect state.

The research layer intentionally keeps natural-language proof text separate
from control-plane messages.  A provider may still return prose for a proof,
but every value consumed by an audit gate, scheduler, or repair state machine
must arrive as a complete JSON document and validate against one of these
models first.
"""

from __future__ import annotations

import copy
import json
import re
from enum import Enum
from typing import Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
)


class SchemaError(ValueError):
    """Raised when a provider violates a typed control-plane contract."""


class StrictSchemaModel(BaseModel):
    """Base model that does not coerce or silently discard provider fields."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
    )


class ProjectSubproblemSchema(StrictSchemaModel):
    """One typed child obligation proposed by the project supervisor."""

    id: StrictStr
    title: StrictStr
    statement: StrictStr
    dependencies: list[StrictStr] = Field(default_factory=list)
    tags: list[StrictStr] = Field(default_factory=list)
    branch: StrictStr = "main"
    proof_type: StrictStr = "NATURAL_LANGUAGE"
    claim_type: Literal["implication", "iff", "classification", "equality", "unclassified"] = (
        "implication"
    )


class ProjectPlanSchema(StrictSchemaModel):
    """Structured project-level decomposition; prose never mutates state."""

    # Version 1 plans remain readable; new planners emit version 2 together
    # with a short title so clients never need to render the full purpose as a
    # persistent heading.
    schema_version: Literal[1, 2]
    project_title: StrictStr = ""
    analysis_summary: StrictStr
    subproblems: list[ProjectSubproblemSchema] = Field(min_length=1, max_length=12)
    open_questions: list[StrictStr] = Field(default_factory=list)


class AuthorityUseSchema(StrictSchemaModel):
    claim: StrictStr
    claim_class: StrictStr
    authority_id: StrictStr = ""
    authority_type: StrictStr = ""
    proof_location: StrictStr = ""


class AuditCriteriaSchema(StrictSchemaModel):
    """Explicit audit gate booleans; no free-form criteria keys."""

    forward_implication: StrictBool = False
    converse_if_applicable: StrictBool = False
    exhaustive_cases: StrictBool = False
    parameter_ranges: StrictBool = False
    boundary_cases: StrictBool = False
    dependencies_valid: StrictBool = False
    no_counterexample: StrictBool = False
    auditors_pass: StrictBool = False
    computational_evidence_separated: StrictBool = False


class AuditResultSchema(StrictSchemaModel):
    """Provider-facing schema for specialist and final audit responses."""

    schema_version: Literal[3]
    role: StrictStr
    domain_verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    execution_status: Literal["OK", "ERROR"] = "OK"
    findings: list[StrictStr] = Field(default_factory=list)
    failure_reasons: list[StrictStr] = Field(default_factory=list)
    cross_audit_notes: list[StrictStr] = Field(default_factory=list)
    computational_evidence: list[StrictStr] = Field(default_factory=list)
    summary: StrictStr = ""
    criteria: AuditCriteriaSchema = Field(default_factory=AuditCriteriaSchema)
    authority_uses: list[AuthorityUseSchema] = Field(default_factory=list)
    execution_error: StrictStr = ""
    audited_claim_snapshot_hash: StrictStr = ""


class WorkerEventKind(str, Enum):
    PROGRESS = "PROGRESS"
    NO_PROGRESS = "NO_PROGRESS"
    FAILED_ROUTE = "FAILED_ROUTE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class WorkerVerdict(str, Enum):
    CORRECT = "CORRECT"
    FLAWED = "FLAWED"
    CRITICALLY_FLAWED = "CRITICALLY_FLAWED"
    UNCERTAIN = "UNCERTAIN"


class WorkerEventSchema(StrictSchemaModel):
    """Canonical worker event consumed by routing and pipeline state."""

    event: WorkerEventKind
    verdict: WorkerVerdict = WorkerVerdict.UNCERTAIN
    failure_kind: StrictStr = ""
    details: list[StrictStr] = Field(default_factory=list)
    progress_signals: list[StrictStr] = Field(default_factory=list)
    literature_request: dict[str, Any] | None = None
    high_value: StrictBool = False

    @field_validator("event", mode="before")
    @classmethod
    def validate_event(cls, value):
        try:
            return value if isinstance(value, WorkerEventKind) else WorkerEventKind(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("event must be an exact WorkerEventKind value") from exc

    @field_validator("verdict", mode="before")
    @classmethod
    def validate_verdict(cls, value):
        try:
            return value if isinstance(value, WorkerVerdict) else WorkerVerdict(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("verdict must be an exact WorkerVerdict value") from exc


WORKER_EVENT_FOOTER_START = "<!-- MRA_WORKER_EVENT"
WORKER_EVENT_FOOTER_END = "-->"
_WORKER_EVENT_FOOTER = re.compile(
    r"<!--\s*MRA_WORKER_EVENT\s*(\{.*?\})\s*-->",
    re.DOTALL,
)


def parse_worker_event_footer(text: str) -> WorkerEventSchema:
    """Parse the one explicit typed event footer from a Worker/Verifier body.

    Free-form prose is never interpreted as control state.  Exactly one
    delimited JSON object must be present and must satisfy the strict schema.
    """

    matches = _WORKER_EVENT_FOOTER.findall(text or "")
    if len(matches) != 1:
        raise SchemaError("Worker output must contain exactly one MRA_WORKER_EVENT footer")
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise SchemaError("Worker event footer is not complete JSON") from exc
    try:
        return WorkerEventSchema.model_validate(payload)
    except ValidationError as exc:
        raise SchemaError(f"Worker event footer failed validation: {exc}") from exc


class LiteratureResultSchema(StrictSchemaModel):
    """Exact result contract for literature tasks that enter the DAG."""

    schema_version: Literal[3]
    literature_verdict: StrictStr = ""
    verdict: StrictStr = ""
    reader_verdict: StrictStr = ""
    reason: StrictStr = ""
    details: list[StrictStr] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    search_tasks: list[dict[str, Any]] = Field(default_factory=list)
    theorems: list[dict[str, Any]] = Field(default_factory=list)
    citation_chain: list[dict[str, Any]] = Field(default_factory=list)
    create_reader: StrictBool = True
    deep_read_required: StrictBool = False
    source_id: StrictStr = ""
    synthesis_path: StrictStr = ""
    authority_status: StrictStr = ""
    authority_record: dict[str, Any] | None = None
    authority_candidate: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    authority_verification_errors: list[StrictStr] = Field(default_factory=list)
    deterministic_verification: StrictBool = False
    architecture_changing: StrictBool = False
    governance_review_trigger: StrictStr = ""


class PipelineResultSchema(StrictSchemaModel):
    """Exact result contract for proof and verification DAG handlers."""

    schema_version: Literal[3]
    verdict: StrictStr = "UNCERTAIN"
    detail: StrictStr = ""
    evidence: StrictStr = ""
    proof_candidate: StrictBool = False
    success: StrictBool = False
    high_value: StrictBool = False
    all_required_gates: StrictBool = False
    applicability_id: StrictStr = ""
    authority_id: StrictStr = ""
    source_theorem_id: StrictStr = ""
    assumption_snapshot_hash: StrictStr = ""
    result_artifact: StrictStr = ""
    applicability_status: StrictStr = ""
    applicability_verification_errors: list[StrictStr] = Field(default_factory=list)
    authority_status: StrictStr = ""
    deterministic_applicability_promotion: StrictBool = False
    external_hypotheses: list[StrictStr] = Field(default_factory=list)
    required_local_lemmas: list[StrictStr] = Field(default_factory=list)
    unresolved_conditions: list[StrictStr] = Field(default_factory=list)
    notation_map: dict[str, Any] = Field(default_factory=dict)
    hypothesis_mapping: dict[str, Any] | None = None
    conclusion_mapping: dict[str, Any] | None = None
    exception_analysis: dict[str, Any] | None = None
    direction_analysis: dict[str, Any] | None = None
    normalization_analysis: dict[str, Any] | None = None
    routing: dict[str, Any] = Field(default_factory=dict)
    literature_request: dict[str, Any] | None = None
    new_obligation: dict[str, Any] | None = None
    new_lemma: dict[str, Any] | None = None
    new_subobligation: dict[str, Any] | None = None
    new_dependency: dict[str, Any] | None = None


class GeminiToolResultSchema(StrictSchemaModel):
    """Small typed envelope for tool-backed Gemini calls."""

    schema_version: Literal[3]
    tool_name: StrictStr
    status: Literal["OK", "ERROR"]
    result: StrictStr = ""
    error: StrictStr = ""


class FormalizationResultSchema(StrictSchemaModel):
    """Typed result for the optional compiler-backed formalization lane."""

    schema_version: Literal[3]
    status: Literal["VERIFIED", "FAILED", "PENDING_FORMALIZATION"]
    theorem_id: StrictStr = ""
    lean_code: StrictStr = ""
    compiler_output: StrictStr = ""
    certificate_path: StrictStr = ""
    certificate_sha256: StrictStr = ""
    summary: StrictStr = ""
    error: StrictStr = ""


T = TypeVar("T", bound=BaseModel)


def response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON Schema sent to providers for structured output."""

    return model.model_json_schema()


def json_schema_for(schema: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """Accept a Pydantic model or an already materialized JSON Schema."""

    if isinstance(schema, dict):
        return dict(schema)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return response_schema(schema)
    raise TypeError("response_schema must be a Pydantic model class or JSON Schema object")


# OpenAI Responses structured outputs and many OpenAI-compatible servers
# implement a deliberately small JSON Schema profile.  In particular, every
# property of an object must be required and object schemas must reject
# unspecified keys.  Pydantic's schema correctly describes Python defaults,
# but that is not the same remote contract: a defaulted field is optional in
# ordinary JSON Schema.  This lowering makes the remote schema strict while
# the Pydantic model remains the authoritative local validator.
_STRICT_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "default",
        "examples",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)


def strict_json_schema_for(schema: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """Lower a schema to the portable strict-output subset.

    Default values remain meaningful to Pydantic after the response returns,
    but are not sent as remote JSON-Schema keywords because strict providers
    disagree about them. A free-form mapping cannot be represented faithfully
    by the strict object subset; callers should use JSON-object mode instead
    of silently throwing away arbitrary keys.
    """

    source = copy.deepcopy(json_schema_for(schema))

    def lower(value: Any, path: str) -> Any:
        if isinstance(value, list):
            return [lower(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value

        result = {
            key: lower(item, f"{path}.{key}")
            for key, item in value.items()
            if key not in _STRICT_UNSUPPORTED_KEYWORDS
        }

        properties = result.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise SchemaError(f"Strict schema properties must be an object at {path}")
            additional = result.get("additionalProperties")
            if additional not in (None, False):
                raise SchemaError(
                    f"Strict schema cannot represent free-form additional properties at {path}"
                )
            result["properties"] = {
                name: lower(child, f"{path}.properties.{name}")
                for name, child in properties.items()
            }
            result["required"] = list(properties)
            result["additionalProperties"] = False
        elif result.get("type") == "object":
            additional = result.get("additionalProperties")
            if additional not in (None, False):
                raise SchemaError(
                    f"Strict schema cannot represent free-form additional properties at {path}"
                )
            result["additionalProperties"] = False

        return result

    lowered = lower(source, "$")
    if not isinstance(lowered, dict):
        raise SchemaError("Strict response schema must be a JSON object")
    return lowered


def parse_structured_response(response: dict[str, Any], model: type[T]) -> T:
    """Validate a provider response without inspecting model prose.

    Providers may expose a native parsed payload as ``structured``.  The
    fallback is deliberately strict ``json.loads`` over the entire ``result``
    string: leading prose, Markdown fences, and trailing commentary are
    rejected rather than guessed around.
    """

    if not isinstance(response, dict):
        raise SchemaError("Provider response must be an object")
    payload = response.get("structured")
    if payload is None:
        raw = response.get("result")
        if not isinstance(raw, str) or not raw.strip():
            raise SchemaError("Provider did not return a structured JSON result")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaError(
                "Provider structured output must be one complete JSON document"
            ) from exc
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="python")
    if not isinstance(payload, dict):
        raise SchemaError("Provider structured output must be a JSON object")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise SchemaError(f"Provider response failed {model.__name__}: {exc}") from exc


def structured_payload(value: BaseModel) -> dict[str, Any]:
    """Serialize a validated model for durable artifacts and state events."""

    return value.model_dump(mode="json", exclude_none=True)
