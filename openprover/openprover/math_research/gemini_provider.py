"""Native Gemini and Vertex Gemini provider.

This adapter uses the official ``generateContent`` HTTP contracts directly so
the research layer does not depend on a second SDK at runtime.  Structured
calls use Gemini's JSON response mode and return the parsed object under
``structured``; the caller still performs the authoritative Pydantic check.
"""

from __future__ import annotations

import json
import io
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .schemas import json_schema_for


class GeminiProviderError(RuntimeError):
    """Secret-safe, resumable Gemini transport or contract failure."""

    def __init__(
        self,
        *,
        error_type: str,
        status: int | None,
        model: str,
        retry_count: int,
        retryable: bool,
        retry_exhausted: bool,
        message: str,
        provider: str = "gemini",
    ):
        self.retry_exhausted = retry_exhausted
        self.details = {
            "provider": provider,
            "error_type": error_type,
            "status": status,
            "model": model,
            "retry_count": retry_count,
            "retryable": retryable,
            "retry_exhausted": retry_exhausted,
            "human_explanation": message,
        }
        super().__init__(json.dumps(self.details, ensure_ascii=False, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.details)


def _usage(raw: dict[str, Any]) -> dict[str, int | bool]:
    metadata = raw.get("usageMetadata") if isinstance(raw, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "input_tokens": int(metadata.get("promptTokenCount", 0) or 0),
        "output_tokens": int(metadata.get("candidatesTokenCount", 0) or 0),
        "reasoning_tokens": int(metadata.get("thoughtsTokenCount", 0) or 0),
        "cached_tokens": int(metadata.get("cachedContentTokenCount", 0) or 0),
        "total_tokens": int(metadata.get("totalTokenCount", 0) or 0),
        "api_reported": bool(metadata),
    }


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Lower Pydantic JSON Schema to Gemini's OpenAPI subset."""

    definitions = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def expand(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [expand(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            if name in stack:
                raise ValueError(f"Recursive Gemini response schema: {name}")
            return expand(definitions[name], stack + (name,))
        result: dict[str, Any] = {}
        for key in (
            "type",
            "format",
            "title",
            "description",
            "enum",
            "nullable",
            "properties",
            "required",
            "items",
            "minItems",
            "maxItems",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
        ):
            if key not in value:
                continue
            if key == "properties" and isinstance(value[key], dict):
                result[key] = {name: expand(child, stack) for name, child in value[key].items()}
            else:
                result[key] = expand(value[key], stack)
        if "anyOf" in value:
            variants = [item for item in value["anyOf"] if isinstance(item, dict)]
            non_null = [item for item in variants if item.get("type") != "null"]
            if len(non_null) == 1 and len(non_null) != len(variants):
                result.update(expand(non_null[0], stack))
                result["nullable"] = True
            else:
                raise ValueError("Gemini response schema cannot represent this union")
        return result

    lowered = expand(schema)
    if not isinstance(lowered, dict):
        raise ValueError("Gemini response schema must be an object")
    return lowered


class GeminiClient:
    """OpenProver-compatible Gemini Developer API / Vertex client."""

    context_length = 1_000_000
    billing_mode = "gemini_api"

    def __init__(
        self,
        model: str,
        archive_dir: Path,
        *,
        api_key: str | None = None,
        project: str | None = None,
        location: str = "us-central1",
        access_token: str | None = None,
        vertex: bool = False,
        timeout_seconds: float = 600.0,
        max_retries: int = 2,
        retry_base_seconds: float = 1.0,
        max_output_tokens: int = 8192,
        answer_reserve: int = 4096,
        context_length: int = 1_000_000,
        temperature: float | None = None,
        http_open: Callable[..., Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
        max_tool_rounds: int = 8,
    ):
        if not model or not str(model).strip():
            raise ValueError("Gemini model is required")
        if timeout_seconds <= 0:
            raise ValueError("Gemini timeout_seconds must be positive")
        if not 0 <= max_retries <= 10:
            raise ValueError("Gemini max_retries must be between 0 and 10")
        if retry_base_seconds < 0:
            raise ValueError("Gemini retry_base_seconds cannot be negative")
        if max_output_tokens < 1:
            raise ValueError("Gemini max_output_tokens must be positive")
        if max_tool_rounds < 0 or max_tool_rounds > 32:
            raise ValueError("Gemini max_tool_rounds must be between 0 and 32")
        if vertex and not project:
            raise ValueError("Vertex Gemini requires a Google Cloud project")
        if not vertex and not api_key:
            raise ValueError("Gemini API requires GEMINI_API_KEY")

        self.model = str(model)
        self.archive_dir = Path(archive_dir)
        self.project = project
        self.location = location
        self.api_key = api_key
        self.access_token = access_token
        self.vertex = bool(vertex)
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_base_seconds = float(retry_base_seconds)
        self.max_output_tokens = int(max_output_tokens)
        self.answer_reserve = int(answer_reserve)
        self.context_length = int(context_length)
        self.temperature = temperature
        self.call_count = 0
        self.request_count = 0
        self.total_retries = 0
        self.total_cost = 0.0
        self.total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "api_reported": False,
        }
        self._open = http_open or urllib.request.urlopen
        self._sleep = sleep_fn
        self.tool_executor = tool_executor
        self.max_tool_rounds = int(max_tool_rounds)
        self._interrupted = threading.Event()
        self._lock = threading.Lock()

        self.billing_mode = "vertex_gemini" if self.vertex else "gemini_api"

    def interrupt(self):
        self._interrupted.set()

    def soft_interrupt(self):
        self._interrupted.set()

    def clear_interrupt(self):
        self._interrupted.clear()

    def clear_soft_interrupt(self):
        self._interrupted.clear()

    def cleanup(self):
        return None

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
        tools: list[dict] | None = None,
        **_: Any,
    ) -> dict:
        if response_schema is not None:
            if json_schema is not None:
                raise ValueError("Pass only one of json_schema or response_schema")
            json_schema = json_schema_for(response_schema)
        with self._lock:
            self.call_count += 1
            call_number = self.call_count
        if self._interrupted.is_set():
            raise GeminiProviderError(
                error_type="interrupted",
                status=None,
                model=self.model,
                retry_count=0,
                retryable=False,
                retry_exhausted=False,
                message="Gemini call interrupted before request",
                provider=self.billing_mode,
            )

        payload = self._payload(
            prompt,
            system_prompt,
            json_schema=(_gemini_schema(json_schema) if json_schema is not None else None),
            web_search=web_search,
            tools=tools,
            max_tokens=max_tokens,
        )
        self._archive(
            call_number,
            label,
            prompt,
            system_prompt,
            json_schema,
            None,
            None,
            archive_path,
        )
        started = time.perf_counter()
        raw, retry_count = self._request(payload, label=label)
        raw_responses = [raw]
        request_count = 1
        tool_rounds = 0
        tool_trace: list[dict[str, Any]] = []
        tool_calls = self._function_calls(raw)
        while tool_calls and self.tool_executor is not None:
            if tool_rounds >= self.max_tool_rounds:
                raise GeminiProviderError(
                    error_type="tool_loop_exhausted",
                    status=None,
                    model=self.model,
                    retry_count=retry_count,
                    retryable=False,
                    retry_exhausted=False,
                    message="Gemini tool loop exceeded max_tool_rounds",
                    provider=self.billing_mode,
                )
            payload["contents"] = list(payload.get("contents", []))
            payload["contents"].append(self._model_content(raw))
            response_parts = []
            for tool_call in tool_calls:
                try:
                    tool_result = self.tool_executor(tool_call["name"], tool_call["args"])
                except Exception as exc:
                    tool_result = {
                        "status": "ERROR",
                        "error": str(exc)[:800],
                    }
                if not isinstance(tool_result, dict):
                    tool_result = {"output": str(tool_result)}
                tool_trace.append(
                    {
                        "tool_name": tool_call["name"],
                        "args": tool_call["args"],
                        "result": tool_result,
                        "round": tool_rounds + 1,
                    }
                )
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": tool_call["name"],
                            "response": tool_result,
                        }
                    }
                )
            payload["contents"].append(
                {
                    "role": "user",
                    "parts": response_parts,
                }
            )
            raw, retries = self._request(payload, label=label)
            raw_responses.append(raw)
            retry_count += retries
            request_count += 1
            tool_rounds += 1
            tool_calls = self._function_calls(raw)
        if tool_calls:
            raise GeminiProviderError(
                error_type="tool_call_unhandled",
                status=None,
                model=self.model,
                retry_count=retry_count,
                retryable=False,
                retry_exhausted=False,
                message=("Gemini returned a tool call but no local executor was configured"),
                provider=self.billing_mode,
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage = self._sum_usage(raw_responses)
        with self._lock:
            self.request_count += request_count
            self.total_retries += retry_count
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cached_tokens",
                "total_tokens",
            ):
                self.total_usage[key] += int(usage[key])
            self.total_usage["api_reported"] = bool(
                self.total_usage["api_reported"] or usage["api_reported"]
            )
        result_text = self._result_text(raw)
        result: dict[str, Any] = {
            "result": result_text,
            "thinking": "",
            "cost": 0.0,
            "duration_ms": elapsed_ms,
            "raw": raw,
            "finish_reason": self._finish_reason(raw),
            "usage": usage,
            "retry_count": retry_count,
            "tool_rounds": tool_rounds,
            "tool_trace": tool_trace,
            "model": self.model,
            "billing_mode": self.billing_mode,
        }
        if json_schema is not None:
            try:
                result["structured"] = json.loads(result_text)
            except (TypeError, json.JSONDecodeError) as exc:
                self._archive(
                    call_number,
                    label,
                    prompt,
                    system_prompt,
                    json_schema,
                    raw,
                    f"structured output is not complete JSON: {exc}",
                    archive_path,
                    result_text=result_text,
                )
                raise GeminiProviderError(
                    error_type="structured_output_invalid",
                    status=None,
                    model=self.model,
                    retry_count=retry_count,
                    retryable=False,
                    retry_exhausted=False,
                    message="Gemini returned non-JSON text for a structured call",
                    provider=self.billing_mode,
                ) from exc
        if stream_callback and result_text:
            stream_callback(result_text, "text")
        self._archive(
            call_number,
            label,
            prompt,
            system_prompt,
            json_schema,
            raw,
            None,
            archive_path,
            result_text=result_text,
        )
        return result

    def chat(self, messages: list[dict], **kwargs) -> dict:
        system_parts = []
        prompt_parts = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = str(message.get("content") or "")
            if not content:
                continue
            if role in {"system", "developer"}:
                system_parts.append(content)
            else:
                prompt_parts.append(f"[{role}]\n{content}")
        return self.call(
            "\n\n".join(prompt_parts),
            "\n\n".join(system_parts),
            **kwargs,
        )

    def _payload(
        self,
        prompt: str,
        system_prompt: str,
        *,
        json_schema: dict | None,
        web_search: bool,
        tools: list[dict] | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        generation: dict[str, Any] = {
            "maxOutputTokens": int(max_tokens or self.max_output_tokens),
        }
        if self.temperature is not None:
            generation["temperature"] = float(self.temperature)
        if json_schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = json_schema
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        configured_tools = list(tools or [])
        if web_search:
            configured_tools.append({"google_search": {}})
        if configured_tools:
            payload["tools"] = configured_tools
        return payload

    def _endpoint(self) -> str:
        if self.vertex:
            model = urllib.parse.quote(self.model, safe=".-_")
            return (
                f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
                f"{urllib.parse.quote(str(self.project), safe='')}/locations/"
                f"{urllib.parse.quote(self.location, safe='')}/publishers/google/"
                f"models/{model}:generateContent"
            )
        model = urllib.parse.quote(self.model, safe=".-_")
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={urllib.parse.quote(str(self.api_key), safe='')}"
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.vertex:
            token = self.access_token or os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN")
            if not token:
                try:
                    token = subprocess.check_output(
                        ["gcloud", "auth", "application-default", "print-access-token"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    ).strip()
                except (OSError, subprocess.SubprocessError):
                    token = ""
            if not token:
                raise GeminiProviderError(
                    error_type="vertex_auth_missing",
                    status=None,
                    model=self.model,
                    retry_count=0,
                    retryable=False,
                    retry_exhausted=False,
                    message="Set GOOGLE_CLOUD_ACCESS_TOKEN or configure gcloud ADC",
                    provider=self.billing_mode,
                )
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(self, payload: dict[str, Any], *, label: str) -> tuple[dict[str, Any], int]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        retry_count = 0
        while True:
            if self._interrupted.is_set():
                raise GeminiProviderError(
                    error_type="interrupted",
                    status=None,
                    model=self.model,
                    retry_count=retry_count,
                    retryable=False,
                    retry_exhausted=False,
                    message="Gemini call interrupted",
                    provider=self.billing_mode,
                )
            request = urllib.request.Request(
                self._endpoint(), data=body, headers=self._headers(), method="POST"
            )
            try:
                with self._open(request, timeout=self.timeout_seconds) as response:
                    raw_body = response.read()
                    status = int(getattr(response, "status", 200))
                raw = json.loads(raw_body.decode("utf-8"))
                if status >= 400:
                    raise urllib.error.HTTPError(
                        request.full_url,
                        status,
                        "Gemini HTTP error",
                        {},
                        io.BytesIO(raw_body),
                    )
                if not isinstance(raw, dict):
                    raise ValueError("Gemini response is not an object")
                return raw, retry_count
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                error_type, retryable = self._classify(status, body_text)
                if retryable and retry_count < self.max_retries:
                    self._sleep(self.retry_base_seconds * (2**retry_count))
                    retry_count += 1
                    continue
                raise GeminiProviderError(
                    error_type=error_type,
                    status=status,
                    model=self.model,
                    retry_count=retry_count,
                    retryable=retryable,
                    retry_exhausted=retryable,
                    message=self._safe_error(body_text),
                    provider=self.billing_mode,
                ) from exc
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                retryable = not isinstance(exc, (ValueError, json.JSONDecodeError))
                if retryable and retry_count < self.max_retries:
                    self._sleep(self.retry_base_seconds * (2**retry_count))
                    retry_count += 1
                    continue
                raise GeminiProviderError(
                    error_type="transport_error" if retryable else "invalid_response",
                    status=None,
                    model=self.model,
                    retry_count=retry_count,
                    retryable=retryable,
                    retry_exhausted=retryable,
                    message=str(exc)[:500],
                    provider=self.billing_mode,
                ) from exc

    @staticmethod
    def _classify(status: int, body: str) -> tuple[str, bool]:
        lowered = body.casefold()
        if status == 429 and any(
            marker in lowered for marker in ("quota", "resource_exhausted", "rate")
        ):
            return "quota_exceeded", False
        if status in {408, 409, 425, 429} or status >= 500:
            return "transient_provider_error", True
        if status in {401, 403}:
            return "authentication_error", False
        return "provider_error", False

    @staticmethod
    def _safe_error(body: str) -> str:
        try:
            value = json.loads(body)
            error = value.get("error", value) if isinstance(value, dict) else value
            return json.dumps(error, ensure_ascii=False)[:800]
        except (TypeError, json.JSONDecodeError):
            return body[:800] or "Gemini request failed"

    def _result_text(self, raw: dict[str, Any]) -> str:
        candidates = raw.get("candidates") or []
        if not candidates:
            block = raw.get("promptFeedback") or raw.get("error") or {}
            raise GeminiProviderError(
                error_type="empty_response",
                status=None,
                model=self.model,
                retry_count=0,
                retryable=False,
                retry_exhausted=False,
                message=json.dumps(block, ensure_ascii=False)[:800],
            )
        content = candidates[0].get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("text") is not None
        )
        if not text:
            raise GeminiProviderError(
                error_type="empty_response",
                status=None,
                model=self.model,
                retry_count=0,
                retryable=False,
                retry_exhausted=False,
                message="Gemini returned no text candidate",
            )
        return text

    @staticmethod
    def _model_content(raw: dict[str, Any]) -> dict[str, Any]:
        candidates = raw.get("candidates") or []
        content = candidates[0].get("content", {}) if candidates else {}
        if not isinstance(content, dict):
            raise GeminiProviderError(
                error_type="invalid_tool_response",
                status=None,
                model="unknown",
                retry_count=0,
                retryable=False,
                retry_exhausted=False,
                message="Gemini tool response content is not an object",
            )
        return content

    @staticmethod
    def _function_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = raw.get("candidates") or []
        content = candidates[0].get("content", {}) if candidates else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        calls = []
        for part in parts:
            call = part.get("functionCall") if isinstance(part, dict) else None
            if not isinstance(call, dict) or not call.get("name"):
                continue
            args = call.get("args", {})
            if not isinstance(args, dict):
                raise GeminiProviderError(
                    error_type="invalid_tool_arguments",
                    status=None,
                    model="unknown",
                    retry_count=0,
                    retryable=False,
                    retry_exhausted=False,
                    message="Gemini functionCall args must be an object",
                )
            calls.append({"name": str(call["name"]), "args": args})
        return calls

    @staticmethod
    def _sum_usage(raws: list[dict[str, Any]]) -> dict[str, int | bool]:
        total: dict[str, int | bool] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "api_reported": False,
        }
        for raw in raws:
            item = _usage(raw)
            for key, value in item.items():
                if isinstance(value, bool):
                    total[key] = bool(total[key] or value)
                else:
                    total[key] = int(total[key]) + int(value)
        return total

    @staticmethod
    def _finish_reason(raw: dict[str, Any]) -> str:
        candidates = raw.get("candidates") or []
        return str(candidates[0].get("finishReason", "STOP")) if candidates else "ERROR"

    def _archive(
        self,
        call_number: int,
        label: str,
        prompt: str,
        system_prompt: str,
        schema: dict | None,
        raw: dict | None,
        error: str | None,
        archive_path: Path | None,
        *,
        result_text: str | None = None,
    ) -> None:
        if archive_path is None:
            return
        path = Path(archive_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sections = [
            "---",
            f"provider: {self.billing_mode}",
            f"model: {self.model}",
            f"label: {label}",
            f"call: {call_number}",
            "---",
            "",
            "# System prompt",
            "",
            system_prompt,
            "",
            "# Prompt",
            "",
            prompt,
        ]
        if schema is not None:
            sections.extend(["", "# Response schema", "", json.dumps(schema, indent=2)])
        if result_text is not None:
            sections.extend(["", "# Response", "", result_text])
        if raw is not None:
            sections.extend(
                ["", "# Raw response", "", json.dumps(raw, ensure_ascii=False, indent=2)]
            )
        if error:
            sections.extend(["", "# Error", "", error])
        path.write_text("\n".join(sections) + "\n", encoding="utf-8")
