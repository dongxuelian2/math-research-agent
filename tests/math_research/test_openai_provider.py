import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from math_research_agent.providers.support import is_transient_error
from math_research_agent.research.openai_provider import (
    OpenAIProviderError,
    OpenAIResponsesClient,
)
from math_research_agent.research.project import ProjectError
from math_research_agent.research.providers import (
    create_client,
    load_model_config,
    resolve_role_config,
)


class FakeResponse:
    def __init__(self, text="OK", *, output=None, status="completed"):
        self.output_text = text
        self.output = output or []
        self.status = status
        self.incomplete_details = None
        self.error = None
        self.usage = SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
            input_tokens_details=SimpleNamespace(
                cached_tokens=3,
                cache_write_tokens=2,
            ),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        )

    def model_dump(self, **_kwargs):
        return {
            "id": "resp_test",
            "status": self.status,
            "output_text": self.output_text,
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
        }


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.responses = self
        self.closed = False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if kwargs.get("stream"):
            return iter(outcome)
        return outcome

    def close(self):
        self.closed = True


class StructuredContract(BaseModel):
    verdict: str


def make_client(tmp_path, fake, **kwargs):
    return OpenAIResponsesClient(
        "configured-model",
        tmp_path,
        api_key="sk-test-secret",
        role_name="planner",
        reasoning_effort="high",
        timeout_seconds=17,
        max_retries=kwargs.pop("max_retries", 2),
        retry_base_seconds=0,
        client=fake,
        **kwargs,
    )


def api_response(status, message, body):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, request=request)
    if status == 401:
        return openai.AuthenticationError(message, response=response, body=body)
    return openai.RateLimitError(message, response=response, body=body)


def test_text_call_preserves_roles_reasoning_and_usage(tmp_path):
    fake = FakeClient([FakeResponse("answer"), FakeResponse("chat")])
    client = make_client(tmp_path, fake)
    archive_path = tmp_path / "call.md"

    response = client.call(
        "user text",
        "system text",
        label="planner",
        archive_path=archive_path,
    )
    assert [item["role"] for item in fake.calls[0]["input"]] == ["system", "user"]
    assert fake.calls[0]["reasoning"] == {"effort": "high"}
    assert response["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "reasoning_tokens": 5,
        "cached_tokens": 3,
        "cache_write_tokens": 2,
        "total_tokens": 18,
        "api_reported": True,
    }

    client.chat(
        [
            {"role": "system", "content": "s"},
            {"role": "developer", "content": "d"},
            {"role": "user", "content": "u"},
        ]
    )
    assert [item["role"] for item in fake.calls[1]["input"]] == [
        "system",
        "developer",
        "user",
    ]
    assert "sk-test-secret" not in archive_path.read_text(encoding="utf-8")


def test_pydantic_response_contract_is_materialized_as_responses_json_schema(tmp_path):
    fake = FakeClient([FakeResponse('{"verdict":"PASS"}')])
    response = make_client(tmp_path, fake).call(
        "return JSON", "system", response_schema=StructuredContract
    )
    schema = fake.calls[0]["text"]["format"]["schema"]
    assert schema["properties"]["verdict"]["type"] == "string"
    assert response["result"] == '{"verdict":"PASS"}'


def test_streaming_and_tool_call_parsing(tmp_path):
    final = FakeResponse(
        "streamed",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                id="item_1",
                name="lean_verify",
                arguments='{"code":"example"}',
            )
        ],
    )
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="stream"),
        SimpleNamespace(type="response.output_text.delta", delta="ed"),
        SimpleNamespace(type="response.completed", response=final),
    ]
    fake = FakeClient([events])
    chunks = []
    response = make_client(tmp_path, fake).call(
        "u",
        "s",
        stream_callback=lambda text, kind: chunks.append((text, kind)),
    )
    assert chunks == [("stream", "text"), ("ed", "text")]
    assert response["finish_reason"] == "tool_calls"
    assert response["tool_calls"][0]["function"]["name"] == "lean_verify"


def test_retryable_429_is_bounded_and_reported(tmp_path):
    error = api_response(
        429,
        "rate limited",
        {"error": {"code": "rate_limit_exceeded"}},
    )
    fake = FakeClient([error, FakeResponse("recovered")])
    client = make_client(tmp_path, fake, max_retries=1)
    response = client.call("u", "s")
    assert response["result"] == "recovered"
    assert response["retry_count"] == 1
    assert client.request_count == 2
    assert client.total_retries == 1


def test_auth_and_quota_errors_do_not_retry(tmp_path):
    auth = api_response(
        401,
        "bad key sk-test-secret",
        {"error": {"code": "invalid_api_key"}},
    )
    auth_archive = tmp_path / "auth.md"
    client = make_client(tmp_path, FakeClient([auth]))
    with pytest.raises(OpenAIProviderError) as caught:
        client.call("u", "s", archive_path=auth_archive)
    assert caught.value.details["status"] == 401
    assert caught.value.details["retry_count"] == 0
    assert client.request_count == 1
    assert "sk-test-secret" not in str(caught.value)
    assert "sk-test-secret" not in auth_archive.read_text(encoding="utf-8")

    quota = api_response(
        429,
        "insufficient_quota",
        {"error": {"code": "insufficient_quota"}},
    )
    quota_client = make_client(tmp_path, FakeClient([quota]))
    with pytest.raises(OpenAIProviderError) as quota_caught:
        quota_client.call("u", "s")
    assert quota_caught.value.details["error_type"] == "quota_exceeded"
    assert quota_client.request_count == 1


def test_retry_exhaustion_does_not_trigger_outer_infinite_retry(tmp_path):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    errors = [
        openai.APIConnectionError(message="connection reset", request=request),
        openai.APIConnectionError(message="connection reset", request=request),
    ]
    client = make_client(tmp_path, FakeClient(errors), max_retries=1)
    with pytest.raises(OpenAIProviderError) as caught:
        client.call("u", "s")
    assert caught.value.retry_exhausted is True
    assert caught.value.details["retry_count"] == 1
    assert client.request_count == 2
    assert not is_transient_error(caught.value)


def config_data(provider="openai", effort="low"):
    role = {
        "provider": provider,
        "model": "configured-model",
        "reasoning_effort": effort,
    }
    return {
        "roles": {
            "planner": dict(role),
            "worker": dict(role),
            "counterexample": dict(role),
            "auditor": dict(role),
            "final_auditor": dict(role),
        }
    }


def test_config_parsing_role_aliases_and_validation(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config_data()), encoding="utf-8")
    config = load_model_config(path)
    assert resolve_role_config(config, "counterexample_hunter") is config["roles"]["counterexample"]
    assert resolve_role_config(config, "dependency_auditor") is config["roles"]["auditor"]

    invalid = config_data(provider="unknown")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ProjectError, match="Unsupported provider"):
        load_model_config(path)

    invalid = config_data(effort="minimal")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ProjectError, match="reasoning_effort"):
        load_model_config(path)


def test_missing_key_and_key_in_config_are_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    role = config_data()["roles"]["planner"]
    with pytest.raises(ProjectError, match="OPENAI_API_KEY"):
        create_client(role, tmp_path, role_name="planner")

    config = config_data()
    config["roles"]["planner"]["api_key"] = "must-not-be-here"
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ProjectError, match="must not contain api_key"):
        load_model_config(path)
