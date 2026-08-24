"""Official OpenAI Responses API client for the math-research layer."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import openai
from openai import OpenAI

from math_research_agent.providers.support import Interrupted, archive
from math_research_agent.providers.responses import ResponsesRequest, response_text

from .schemas import SchemaError, json_schema_for, strict_json_schema_for


logger = logging.getLogger("math_research_agent.providers.openai")

OPENAI_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})

_REASONING_FALLBACKS: dict[str | None, tuple[str, ...]] = {
    None: ("none",),
    "low": ("none",),
    "medium": ("low", "none"),
    "high": ("medium", "none"),
    "xhigh": ("high", "medium"),
    "max": ("xhigh", "high"),
    "none": (),
}


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    value = str(text)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


class OpenAIProviderError(RuntimeError):
    """Structured, secret-safe provider failure."""

    def __init__(
        self,
        *,
        error_type: str,
        status: int | None,
        role: str,
        model: str,
        retry_count: int,
        retryable: bool,
        retry_exhausted: bool,
        human_explanation: str,
        provider: str = "openai",
        upstream_message: str = "",
    ):
        self.retry_exhausted = retry_exhausted
        self.details = {
            "provider": provider,
            "error_type": error_type,
            "status": status,
            "role": role,
            "model": model,
            "retry_count": retry_count,
            "retryable": retryable,
            "retry_exhausted": retry_exhausted,
            "human_explanation": human_explanation,
        }
        if upstream_message:
            self.details["upstream_message"] = upstream_message
        super().__init__(json.dumps(self.details, ensure_ascii=False, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.details)


def _usage_dict(response: Any) -> dict[str, int | bool]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "api_reported": False,
        }
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(output_details, "reasoning_tokens", 0) or 0),
        "cached_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(input_details, "cache_write_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "api_reported": True,
    }


def _raw_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    if isinstance(response, dict):
        return response
    return {"status": getattr(response, "status", "unknown")}


def _response_text(response: Any) -> str:
    return response_text(response)


def _tool_calls(response: Any) -> list[dict[str, Any]] | None:
    calls = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "function_call":
            continue
        calls.append(
            {
                "id": getattr(item, "call_id", "") or getattr(item, "id", ""),
                "type": "function",
                "function": {
                    "name": getattr(item, "name", ""),
                    "arguments": getattr(item, "arguments", "{}") or "{}",
                },
            }
        )
    return calls or None


def _response_output_items(response: Any) -> list[dict[str, Any]]:
    """Convert SDK response items into Responses input items for a tool turn."""

    items: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "function_call":
            continue
        items.append(
            {
                "type": "function_call",
                "call_id": getattr(item, "call_id", "") or getattr(item, "id", ""),
                "name": getattr(item, "name", ""),
                "arguments": getattr(item, "arguments", "{}") or "{}",
            }
        )
    return items


def _finish_reason(response: Any, tool_calls: list[dict] | None) -> str:
    if tool_calls:
        return "tool_calls"
    if getattr(response, "status", "") == "incomplete":
        details = getattr(response, "incomplete_details", None)
        if getattr(details, "reason", "") == "max_output_tokens":
            return "length"
        return "incomplete"
    if getattr(response, "status", "") == "failed":
        return "error"
    return "stop"


def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    converted = []
    for tool in tools:
        if tool.get("type") != "function":
            converted.append(dict(tool))
            continue
        function = tool.get("function", {})
        converted.append(
            {
                "type": "function",
                "name": function.get("name", ""),
                "description": function.get("description"),
                "parameters": function.get("parameters", {}),
                "strict": function.get("strict", False),
            }
        )
    return converted


def _convert_messages(messages: list[dict]) -> list[dict]:
    """Convert OpenAI chat-shaped history to Responses input items."""
    items: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "developer", "user", "assistant"}:
            content = message.get("content") or ""
            if content:
                items.append({"role": role, "content": content})
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.get("id", ""),
                            "name": function.get("name", ""),
                            "arguments": function.get("arguments", "{}"),
                        }
                    )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": message.get("content") or "",
                }
            )
    return items


class OpenAIResponsesClient:
    """Math Research Agent-compatible client backed by the official Responses API."""

    vllm = True  # Select Math Research Agent's OpenAI-format function-tool path.

    def __init__(
        self,
        model: str,
        archive_dir: Path,
        *,
        api_key: str,
        role_name: str,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 600.0,
        max_retries: int = 2,
        retry_base_seconds: float = 1.0,
        max_output_tokens: int = 4096,
        answer_reserve: int = 4096,
        context_length: int = 200_000,
        store: bool = False,
        base_url: str | None = None,
        provider_name: str = "openai",
        api_key_env: str = "OPENAI_API_KEY",
        client: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
        max_tool_rounds: int = 8,
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        if reasoning_effort not in OPENAI_REASONING_EFFORTS | {None}:
            allowed = ", ".join(sorted(OPENAI_REASONING_EFFORTS))
            raise ValueError(f"Invalid OpenAI reasoning_effort; expected one of: {allowed}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if not 0 <= max_tool_rounds <= 32:
            raise ValueError("max_tool_rounds must be between 0 and 32")

        self.model = model
        self.archive_dir = Path(archive_dir)
        self.role_name = role_name
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_base_seconds = float(retry_base_seconds)
        self.max_output_tokens = int(max_output_tokens)
        self.answer_reserve = int(answer_reserve)
        self.context_length = int(context_length)
        self.store = bool(store)
        self.base_url = base_url
        self.provider_name = provider_name
        self.api_key_env = api_key_env
        self.call_count = 0
        self.request_count = 0
        self.total_retries = 0
        self.total_cost = 0.0  # The Responses API does not report USD cost.
        self.total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "api_reported": False,
        }
        self._secrets = (api_key,)
        self._sleep = sleep_fn
        self.tool_executor = tool_executor
        self.max_tool_rounds = int(max_tool_rounds)
        self._lock = threading.Lock()
        self._interrupted = threading.Event()
        self._client = client or OpenAI(
            api_key=api_key,
            timeout=self.timeout_seconds,
            max_retries=0,
            **({"base_url": base_url} if base_url else {}),
        )

    def interrupt(self):
        self._interrupted.set()

    def soft_interrupt(self):
        self._interrupted.set()

    def clear_interrupt(self):
        self._interrupted.clear()

    def clear_soft_interrupt(self):
        self._interrupted.clear()

    def cleanup(self):
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def call(
        self,
        prompt: str,
        system_prompt: str,
        json_schema: dict | None = None,
        response_schema=None,
        label: str = "",
        web_search: bool = False,
        stream_callback=None,
        archive_path: Path | None = None,
        max_tokens: int | None = None,
        no_thinking: bool = False,
        tools: list[dict] | None = None,
        **_kwargs,
    ) -> dict:
        if response_schema is not None:
            if json_schema is not None:
                raise ValueError("Pass only one of json_schema or response_schema")
            json_schema = json_schema_for(response_schema)
        input_items = []
        if system_prompt:
            input_items.append({"role": "system", "content": system_prompt})
        input_items.append({"role": "user", "content": prompt})
        configured_tools = list(tools or [])
        has_local_web_search = any(
            item.get("function", {}).get("name") == "web_search"
            for item in configured_tools
            if isinstance(item, dict)
        )
        if web_search and not has_local_web_search:
            configured_tools.append({"type": "web_search"})
        return self._execute(
            input_items=input_items,
            prompt_for_archive=prompt,
            system_prompt=system_prompt,
            json_schema=json_schema,
            tools=configured_tools or None,
            label=label,
            stream_callback=stream_callback,
            archive_path=archive_path,
            max_tokens=max_tokens,
            no_thinking=no_thinking,
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        label: str = "",
        stream_callback=None,
        archive_path: Path | None = None,
        response_schema=None,
        **_kwargs,
    ) -> dict:
        return self._execute(
            input_items=_convert_messages(messages),
            prompt_for_archive=json.dumps(messages, ensure_ascii=False),
            system_prompt="",
            json_schema=(json_schema_for(response_schema) if response_schema is not None else None),
            tools=_convert_tools(tools),
            label=label,
            stream_callback=stream_callback,
            archive_path=archive_path,
            max_tokens=max_tokens,
            no_thinking=False,
        )

    def _execute(
        self,
        *,
        input_items: list[dict],
        prompt_for_archive: str,
        system_prompt: str,
        json_schema: dict | None,
        tools: list[dict] | None,
        label: str,
        stream_callback,
        archive_path: Path | None,
        max_tokens: int | None,
        no_thinking: bool,
    ) -> dict:
        with self._lock:
            self.call_count += 1
            call_num = self.call_count
        self._archive(
            call_num,
            label,
            prompt_for_archive,
            system_prompt,
            json_schema,
            None,
            None,
            0,
            archive_path,
        )
        if self._interrupted.is_set():
            raise Interrupted()

        effort = "none" if no_thinking else self.reasoning_effort
        remote_schema = None
        schema_mode = "none"
        schema_fallback_reason = ""
        if json_schema is not None:
            try:
                remote_schema = strict_json_schema_for(json_schema)
                schema_mode = "strict_json_schema"
            except SchemaError as exc:
                # A free-form mapping cannot be expressed by strict JSON
                # Schema without changing its meaning. JSON-object mode keeps
                # the model expressive; Pydantic remains the final validator.
                remote_schema = None
                schema_mode = "json_object_fallback"
                schema_fallback_reason = str(exc)
                logger.warning(
                    "[%s] structured schema is not portable; using JSON-object mode: %s",
                    label,
                    schema_fallback_reason,
                )
        if json_schema is None:
            response_text_format = None
        elif remote_schema is not None:
            response_text_format = {
                "format": {
                    "type": "json_schema",
                    "name": "math_research_agent_response",
                    "schema": remote_schema,
                    "strict": True,
                }
            }
        else:
            response_text_format = {"format": {"type": "json_object"}}
        output_budget = int(max_tokens or self.max_output_tokens)

        active_input = list(input_items)

        def build_payload(active_effort: str | None) -> dict[str, Any]:
            return ResponsesRequest(
                model=self.model,
                input=active_input,
                max_output_tokens=output_budget,
                reasoning_effort=active_effort,
                tools=tools,
                text=response_text_format,
                store=self.store,
            ).to_payload()

        payload = build_payload(effort)

        started = time.perf_counter()
        retry_count = 0
        adaptive_reasoning_retries = 0
        reasoning_fallbacks = _REASONING_FALLBACKS.get(effort, ())
        while True:
            try:
                with self._lock:
                    self.request_count += 1
                if stream_callback:
                    response, streamed_text = self._stream_response(
                        payload,
                        stream_callback,
                    )
                else:
                    response = self._client.responses.create(**payload)
                    streamed_text = ""
                response_status = getattr(response, "status", "")
                if response_status == "incomplete":
                    details = getattr(response, "incomplete_details", None)
                    reason = getattr(details, "reason", None) or "unknown"
                    # Thinking-heavy models can spend the whole output budget
                    # on reasoning and never emit the typed answer. When the
                    # caller did not disable retries, lower reasoning
                    # progressively and retry the exact same request.
                    if (
                        reason == "max_output_tokens"
                        and retry_count < self.max_retries
                        and adaptive_reasoning_retries < len(reasoning_fallbacks)
                    ):
                        next_effort = reasoning_fallbacks[adaptive_reasoning_retries]
                        adaptive_reasoning_retries += 1
                        retry_count += 1
                        with self._lock:
                            self.total_retries += 1
                        logger.warning(
                            "[%s] incomplete output exhausted the %s reasoning budget; "
                            "retrying with reasoning effort %s (%d/%d)",
                            label,
                            effort or "default",
                            next_effort,
                            adaptive_reasoning_retries,
                            len(reasoning_fallbacks),
                        )
                        effort = next_effort
                        payload = build_payload(effort)
                        continue
                break
            except Interrupted:
                raise
            except Exception as exc:
                info = self._classify_error(exc)
                if info["retryable"] and retry_count < self.max_retries:
                    delay = self.retry_base_seconds * (2**retry_count)
                    retry_count += 1
                    with self._lock:
                        self.total_retries += 1
                    logger.warning(
                        "[%s] OpenAI transient failure; retry %d/%d in %.1fs",
                        label,
                        retry_count,
                        self.max_retries,
                        delay,
                    )
                    if delay:
                        self._sleep(delay)
                    continue
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                provider_error = self._provider_error(
                    exc,
                    retry_count=retry_count,
                    retry_exhausted=bool(info["retryable"]),
                )
                self._archive(
                    call_num,
                    label,
                    prompt_for_archive,
                    system_prompt,
                    remote_schema or json_schema,
                    None,
                    provider_error,
                    elapsed_ms,
                    archive_path,
                )
                raise provider_error from exc

        tool_rounds = 0
        tool_trace: list[dict[str, Any]] = []
        calls = _tool_calls(response)
        while calls and self.tool_executor is not None:
            if tool_rounds >= self.max_tool_rounds:
                raise self._provider_error(
                    RuntimeError("Responses API tool loop exceeded max_tool_rounds"),
                    retry_count=retry_count,
                    retry_exhausted=False,
                    error_type="tool_loop_exhausted",
                )
            active_input.extend(_response_output_items(response))
            for call in calls:
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    tool_result = self.tool_executor(call["function"]["name"], arguments)
                except Exception as exc:
                    tool_result = {"status": "ERROR", "error": str(exc)[:800]}
                if not isinstance(tool_result, dict):
                    tool_result = {"output": str(tool_result)}
                tool_trace.append(
                    {
                        "tool_name": call["function"]["name"],
                        "args": arguments,
                        "result": tool_result,
                        "round": tool_rounds + 1,
                    }
                )
                active_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["id"],
                        "output": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
            payload = build_payload(effort)
            with self._lock:
                self.request_count += 1
            response = self._client.responses.create(**payload)
            streamed_text = ""
            tool_rounds += 1
            calls = _tool_calls(response)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if self._interrupted.is_set():
            raise Interrupted()
        if getattr(response, "status", "") == "failed":
            error = getattr(response, "error", None)
            exc = RuntimeError(getattr(error, "message", "Responses API returned failed"))
            provider_error = self._provider_error(
                exc,
                retry_count=retry_count,
                retry_exhausted=False,
                error_type=getattr(error, "code", None) or "response_failed",
            )
            self._archive(
                call_num,
                label,
                prompt_for_archive,
                system_prompt,
                remote_schema or json_schema,
                _raw_response(response),
                provider_error,
                elapsed_ms,
                archive_path,
            )
            raise provider_error

        response_status = getattr(response, "status", "")
        if response_status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "unknown"
            exc = RuntimeError(f"Responses API returned incomplete output: {reason}")
            provider_error = self._provider_error(
                exc,
                retry_count=retry_count,
                retry_exhausted=False,
                error_type="response_incomplete",
            )
            self._archive(
                call_num,
                label,
                prompt_for_archive,
                system_prompt,
                remote_schema or json_schema,
                _raw_response(response),
                provider_error,
                elapsed_ms,
                archive_path,
            )
            raise provider_error

        usage = _usage_dict(response)
        with self._lock:
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cached_tokens",
                "cache_write_tokens",
                "total_tokens",
            ):
                self.total_usage[key] += int(usage[key])
            self.total_usage["api_reported"] = bool(
                self.total_usage["api_reported"] or usage["api_reported"]
            )
        result_text = _response_text(response) or streamed_text
        if json_schema is not None and not result_text.strip() and not calls:
            exc = RuntimeError("Responses API returned no structured output text")
            provider_error = self._provider_error(
                exc,
                retry_count=retry_count,
                retry_exhausted=False,
                error_type="response_empty_structured_output",
            )
            self._archive(
                call_num,
                label,
                prompt_for_archive,
                system_prompt,
                remote_schema or json_schema,
                _raw_response(response),
                provider_error,
                elapsed_ms,
                archive_path,
            )
            raise provider_error
        raw = _raw_response(response)
        self._archive(
            call_num,
            label,
            prompt_for_archive,
            system_prompt,
            remote_schema or json_schema,
            raw,
            None,
            elapsed_ms,
            archive_path,
            result_text=result_text,
        )
        result = {
            "result": result_text,
            "thinking": "",
            "cost": 0.0,
            "cost_api_reported": False,
            "duration_ms": elapsed_ms,
            "raw": raw,
            "finish_reason": _finish_reason(response, calls),
            "usage": usage,
            "retry_count": retry_count,
            "structured_output_mode": schema_mode,
        }
        if schema_fallback_reason:
            result["structured_output_fallback_reason"] = schema_fallback_reason
        if calls:
            result["tool_calls"] = calls
        if tool_trace:
            result["tool_rounds"] = tool_rounds
            result["tool_trace"] = tool_trace
        return result

    def _stream_response(self, payload: dict, callback) -> tuple[Any, str]:
        stream = self._client.responses.create(**payload, stream=True)
        final_response = None
        text_parts: list[str] = []
        for event in stream:
            if self._interrupted.is_set():
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
                raise Interrupted()
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    text_parts.append(delta)
                    callback(delta, "text")
            elif event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = getattr(event, "delta", "") or ""
                if delta:
                    callback(delta, "thinking")
            elif event_type in {
                "response.completed",
                "response.incomplete",
                "response.failed",
            }:
                final_response = getattr(event, "response", None)
        if final_response is None:
            raise RuntimeError("Responses stream ended without a terminal response")
        return final_response, "".join(text_parts)

    def _classify_error(self, exc: Exception) -> dict[str, Any]:
        status = getattr(exc, "status_code", None)
        code = str(getattr(exc, "code", "") or "").lower()
        safe_message = _redact(str(exc), self._secrets)
        lowered = safe_message.lower()
        quota = code in {"insufficient_quota", "billing_hard_limit_reached"} or any(
            phrase in lowered
            for phrase in (
                "insufficient_quota",
                "billing hard limit",
                "quota exceeded",
            )
        )
        retryable = False
        if isinstance(
            exc, (openai.APIConnectionError, openai.APITimeoutError, ConnectionError, TimeoutError)
        ):
            retryable = True
        elif isinstance(exc, openai.APIStatusError):
            retryable = bool(
                status in {408, 409, 429} or (isinstance(status, int) and 500 <= status < 600)
            )
        if quota:
            retryable = False
        return {
            "status": status,
            "code": code,
            "retryable": retryable,
            "quota": quota,
            "safe_message": safe_message,
        }

    def _provider_error(
        self,
        exc: Exception,
        *,
        retry_count: int,
        retry_exhausted: bool,
        error_type: str | None = None,
    ) -> OpenAIProviderError:
        info = self._classify_error(exc)
        kind = error_type or type(exc).__name__
        if error_type == "response_incomplete":
            explanation = "The Responses API stopped before producing a complete response."
        elif error_type == "response_empty_structured_output":
            explanation = "The Responses API completed without returning structured output text."
        elif info["quota"]:
            kind = "quota_exceeded"
            explanation = (
                "OpenAI API quota or billing limit rejected the request; no retry was attempted."
            )
        elif isinstance(exc, openai.AuthenticationError) or info["status"] == 401:
            explanation = "OPENAI_API_KEY was rejected by the OpenAI API."
        elif info["status"] in {400, 404}:
            explanation = "The OpenAI API rejected the request or model identifier; check the role configuration."
        elif retry_exhausted:
            explanation = (
                "A transient OpenAI API failure persisted after the configured bounded retries."
            )
        else:
            explanation = "The OpenAI API request failed and is not safe to retry automatically."
        return OpenAIProviderError(
            error_type=kind,
            status=info["status"],
            role=self.role_name,
            model=self.model,
            retry_count=retry_count,
            retryable=bool(info["retryable"]),
            retry_exhausted=retry_exhausted,
            human_explanation=explanation,
            provider=self.provider_name,
            upstream_message=_redact(str(exc), self._secrets)[:2000],
        )

    def _archive(
        self,
        call_num,
        label,
        prompt,
        system_prompt,
        json_schema,
        response,
        error,
        elapsed_ms,
        archive_path=None,
        *,
        thinking="",
        result_text="",
    ):
        archive(
            self.model,
            self.archive_dir,
            call_num,
            label,
            prompt,
            system_prompt,
            json_schema,
            response,
            error,
            elapsed_ms,
            archive_path,
            thinking=thinking,
            result_text=result_text,
        )


class OpenAICompatibleResponsesClient(OpenAIResponsesClient):
    """Responses client for any endpoint implementing the OpenAI API shape."""

    def __init__(self, model: str, archive_dir: Path, *, base_url: str, **kwargs: Any):
        if not base_url.strip():
            raise ValueError("OpenAI-compatible Responses base_url is required")
        super().__init__(
            model,
            archive_dir,
            base_url=base_url,
            provider_name="openai_compatible",
            **kwargs,
        )
