from __future__ import annotations

import json
from pathlib import Path

import pytest

from math_research_agent.research.gemini_provider import GeminiClient
from math_research_agent.research.observatory import build_snapshot
from math_research_agent.research.schemas import (
    AuditResultSchema,
    SchemaError,
    WorkerEventSchema,
    parse_structured_response,
)
from math_research_agent.research.showcase_demo import run_showcase


def _audit(role: str = "counterexample_hunter") -> dict:
    return {
        "schema_version": 3,
        "role": role,
        "domain_verdict": "PASS",
        "execution_status": "OK",
        "findings": [],
        "failure_reasons": [],
        "cross_audit_notes": [],
        "computational_evidence": [],
        "summary": "ok",
        "criteria": {},
        "authority_uses": [],
        "execution_error": "",
    }


def test_structured_parser_rejects_prose_and_accepts_complete_document():
    response = {"result": "prefix " + json.dumps(_audit())}
    with pytest.raises(SchemaError):
        parse_structured_response(response, AuditResultSchema)

    parsed = parse_structured_response({"structured": _audit()}, AuditResultSchema)
    assert parsed.role == "counterexample_hunter"


def test_worker_state_uses_enum_values_not_spelling_guessing():
    event = WorkerEventSchema(event="NO_PROGRESS")
    assert event.event.value == "NO_PROGRESS"
    with pytest.raises(ValueError):
        WorkerEventSchema(event="NO PROGRESS")


def test_gemini_structured_call_sends_response_schema(tmp_path: Path):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": json.dumps(_audit())}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 7},
                }
            ).encode("utf-8")

    def fake_open(request, timeout):
        captured["request"] = request
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    client = GeminiClient(
        "gemini-3.5-flash",
        tmp_path / "archive",
        api_key="test-key",
        http_open=fake_open,
        max_retries=0,
    )
    response = client.call(
        "audit",
        "return JSON",
        response_schema=AuditResultSchema,
        label="audit_counterexample_hunter",
    )
    assert response["structured"]["schema_version"] == 3
    generation = captured["payload"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseSchema"]["type"] == "object"
    assert "$defs" not in json.dumps(generation["responseSchema"])


def test_gemini_tool_loop_executes_local_function(tmp_path: Path):
    payloads = []
    responses = [
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "lean_verify",
                                    "args": {"code": "theorem demo : 1 = 1 := rfl"},
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 3,
            },
        },
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "verified"}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 8,
                "candidatesTokenCount": 2,
            },
        },
    ]

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(responses.pop(0)).encode("utf-8")

    def fake_open(request, timeout):
        payloads.append(json.loads(request.data))
        return Response()

    client = GeminiClient(
        "gemini-3.5-flash",
        tmp_path / "archive",
        api_key="test-key",
        http_open=fake_open,
        max_retries=0,
        tool_executor=lambda name, args: {
            "status": "OK",
            "output": f"{name}:{args['code']}",
        },
    )
    response = client.call(
        "verify this",
        "Use the compiler tool.",
        tools=[
            {
                "functionDeclarations": [
                    {
                        "name": "lean_verify",
                        "description": "verify",
                        "parameters": {
                            "type": "object",
                            "properties": {"code": {"type": "string"}},
                            "required": ["code"],
                        },
                    }
                ]
            }
        ],
        label="formalization_agent",
    )

    assert response["result"] == "verified"
    assert response["tool_rounds"] == 1
    assert client.request_count == 2
    assert payloads[1]["contents"][-2]["role"] == "model"
    function_response = payloads[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert function_response["name"] == "lean_verify"
    assert function_response["response"]["status"] == "OK"


def test_showcase_persists_failed_route_and_successor(tmp_path: Path):
    manifest = run_showcase(tmp_path / "showcase")
    snapshot = build_snapshot(tmp_path / "showcase")
    assert manifest["status"] == "PROVED"
    assert [run["status"] for run in manifest["runs"]] == ["REJECTED", "PROVED"]
    assert snapshot["audit_gate"]["outcome"] == "PASS"
    assert any(item["category"] == "COUNTEREXAMPLE" for item in snapshot["failure_items"])
    assert snapshot["formal"]["status"] == "PENDING_FORMALIZATION"
