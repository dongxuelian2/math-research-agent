import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from math_research_agent.providers.support import is_transient_error
from math_research_agent.research.openai_provider import (
    OpenAIProviderError,
    OpenAICompatibleResponsesClient,
    OpenAIResponsesClient,
)
from math_research_agent.providers.responses import ResponsesRequest
from math_research_agent.research.project import ProjectError
from math_research_agent.research.schemas import strict_json_schema_for
from math_research_agent.research.providers import (
    create_client,
    load_model_config,
    resolve_role_config,
)


class FakeResponse:
    def __init__(self, text="OK", *, output=None, status="completed", incomplete_reason=None):
        self.output_text = text
        self.output = output or []
        self.status = status
        self.incomplete_details = (
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
        )
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
    note: str = ""


class NestedStructuredContract(BaseModel):
    required: str
    optional: str = ""


class StructuredEnvelope(BaseModel):
    item: NestedStructuredContract
    items: list[NestedStructuredContract] = []


class FreeFormContract(BaseModel):
    values: dict[str, object] = {}


def make_client(tmp_path, fake, **kwargs):
    return OpenAIResponsesClient(
        "configured-model",
        tmp_path,
        api_key="sk-test-secret",
        role_name="planner",
        reasoning_effort=kwargs.pop("reasoning_effort", "high"),
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


def test_responses_request_is_canonical_and_compatible_endpoint_is_configurable(tmp_path):
    request = ResponsesRequest(
        model="local-proof-model",
        input=[{"role": "user", "content": "prove it"}],
        reasoning_effort="high",
        max_output_tokens=123,
        store=False,
    )
    assert request.to_payload() == {
        "model": "local-proof-model",
        "input": [{"role": "user", "content": "prove it"}],
        "max_output_tokens": 123,
        "reasoning": {"effort": "high"},
        "store": False,
    }

    fake = FakeClient([FakeResponse("compatible response")])
    client = OpenAICompatibleResponsesClient(
        "local-proof-model",
        tmp_path,
        api_key="local-secret",
        api_key_env="LOCAL_RESPONSES_KEY",
        base_url="http://127.0.0.1:8000/v1",
        role_name="worker",
        client=fake,
    )
    result = client.call("prove it", "system")
    assert result["result"] == "compatible response"
    assert fake.calls[0]["model"] == "local-proof-model"
    assert client.base_url == "http://127.0.0.1:8000/v1"
    assert result["raw"]["id"] == "resp_test"


def test_pydantic_response_contract_is_materialized_as_responses_json_schema(tmp_path):
    fake = FakeClient([FakeResponse('{"verdict":"PASS"}')])
    response = make_client(tmp_path, fake).call(
        "return JSON", "system", response_schema=StructuredContract
    )
    schema = fake.calls[0]["text"]["format"]["schema"]
    assert schema["properties"]["verdict"]["type"] == "string"
    assert schema["required"] == ["verdict", "note"]
    assert "default" not in schema["properties"]["note"]
    assert response["structured_output_mode"] == "strict_json_schema"
    assert response["result"] == '{"verdict":"PASS"}'


def test_strict_schema_lowering_is_recursive_and_provider_portable():
    schema = strict_json_schema_for(StructuredEnvelope)
    assert schema["required"] == ["item", "items"]
    assert schema["additionalProperties"] is False
    nested = schema["$defs"]["NestedStructuredContract"]
    assert nested["required"] == ["required", "optional"]
    assert nested["additionalProperties"] is False
    assert "default" not in nested["properties"]["optional"]


def test_free_form_mapping_falls_back_to_json_object_mode(tmp_path):
    fake = FakeClient([FakeResponse('{"values": {"answer": 1}}')])
    response = make_client(tmp_path, fake).call(
        "return JSON", "system", response_schema=FreeFormContract
    )
    assert fake.calls[0]["text"] == {"format": {"type": "json_object"}}
    assert response["structured_output_mode"] == "json_object_fallback"
    assert response["result"] == '{"values": {"answer": 1}}'


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


def test_openai_tool_loop_executes_local_function(tmp_path):
    first = FakeResponse(
        "",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                id="item_1",
                name="read",
                arguments='{"path":"README.md"}',
            )
        ],
    )
    fake = FakeClient([first, FakeResponse("done")])
    seen = []

    def execute(name, args):
        seen.append((name, args))
        return {"status": "OK", "content": "README"}

    client = make_client(tmp_path, fake, tool_executor=execute)
    response = client.call(
        "inspect the readme",
        "Use tools when needed.",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert seen == [("read", {"path": "README.md"})]
    assert response["result"] == "done"
    assert response["tool_rounds"] == 1
    assert fake.calls[1]["input"][-1]["type"] == "function_call_output"


def test_incomplete_response_is_reported_at_provider_boundary(tmp_path):
    fake = FakeClient(
        [FakeResponse("", status="incomplete", incomplete_reason="max_output_tokens")]
    )
    client = make_client(tmp_path, fake, max_retries=0)
    with pytest.raises(OpenAIProviderError) as caught:
        client.call("return JSON", "system", response_schema=StructuredContract)
    assert caught.value.details["error_type"] == "response_incomplete"
    assert "max_output_tokens" in caught.value.details["upstream_message"]
    assert "complete response" in caught.value.details["human_explanation"]


def test_incomplete_reasoning_budget_downshifts_and_retries(tmp_path):
    fake = FakeClient(
        [
            FakeResponse("", status="incomplete", incomplete_reason="max_output_tokens"),
            FakeResponse('{"verdict":"PASS","note":"done"}'),
        ]
    )
    client = make_client(
        tmp_path,
        fake,
        reasoning_effort=None,
        max_retries=1,
        max_output_tokens=32768,
    )

    result = client.call("return JSON", "system", response_schema=StructuredContract)

    assert result["result"] == '{"verdict":"PASS","note":"done"}'
    assert result["retry_count"] == 1
    assert client.total_retries == 1
    assert "reasoning" not in fake.calls[0]
    assert fake.calls[1]["reasoning"] == {"effort": "none"}


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


def write_toml_config(path, config):
    roles = config["roles"]
    first = next(iter(roles.values()))
    model = {
        key: first[key]
        for key in ("provider", "model", "base_url", "base_url_env", "api_key", "api_key_env")
        if key in first and first[key] is not None
    }
    lines = ["version = 1", "", "[models.test]", "provider = " + json.dumps(model["provider"])]
    if model.get("model") is not None:
        lines.append("model = " + json.dumps(model["model"]))
    for key in ("base_url", "base_url_env", "api_key", "api_key_env"):
        if key in model:
            lines.append(f"{key} = {json.dumps(model[key])}")
    for role_name, role in roles.items():
        lines.extend(["", f"[roles.{role_name}]", 'model = "test"'])
        for key, value in role.items():
            if key in model or key in {"provider", "model"} or value is None:
                continue
            lines.append(f"{key} = {json.dumps(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
def test_config_parsing_role_aliases_and_validation(tmp_path):
    path = tmp_path / "models.toml"
    write_toml_config(path, config_data())
    config = load_model_config(path)
    assert resolve_role_config(config, "counterexample_hunter") is config["roles"]["counterexample"]
    assert resolve_role_config(config, "dependency_auditor") is config["roles"]["auditor"]

    invalid = config_data(provider="unknown")
    write_toml_config(path, invalid)
    with pytest.raises(ProjectError, match="Unsupported provider"):
        load_model_config(path)

    invalid = config_data(effort="minimal")
    write_toml_config(path, invalid)
    with pytest.raises(ProjectError, match="reasoning_effort"):
        load_model_config(path)


def test_missing_key_and_direct_key_in_config_are_supported(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    role = config_data()["roles"]["planner"]
    with pytest.raises(ProjectError, match="OPENAI_API_KEY"):
        create_client(role, tmp_path, role_name="planner")

    config = config_data()
    config["roles"]["planner"]["api_key"] = "must-not-be-here"
    path = tmp_path / "models.toml"
    write_toml_config(path, config)
    assert load_model_config(path)["roles"]["planner"]["api_key"] == "must-not-be-here"


def test_openai_compatible_config_accepts_arbitrary_model_and_endpoint_env(tmp_path):
    config = config_data(provider="openai_compatible")
    for role in config["roles"].values():
        role["base_url_env"] = "LOCAL_RESPONSES_BASE_URL"
        role["api_key_env"] = "LOCAL_RESPONSES_KEY"
    path = tmp_path / "models.toml"
    write_toml_config(path, config)
    loaded = load_model_config(path)
    assert loaded["roles"]["planner"]["model"] == "configured-model"

    invalid = config_data(provider="openai_compatible")
    write_toml_config(path, invalid)
    with pytest.raises(ProjectError, match="base_url or base_url_env"):
        load_model_config(path)
