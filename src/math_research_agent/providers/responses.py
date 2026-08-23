"""Provider-neutral request shape for OpenAI Responses-compatible backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


ResponsesInput = str | list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ResponsesRequest:
    """Canonical request passed to every OpenAI Responses-compatible model."""

    model: str
    input: ResponsesInput
    instructions: str | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    tools: list[dict[str, Any]] | None = None
    text: dict[str, Any] | None = None
    store: bool | None = None
    temperature: float | None = None
    metadata: Mapping[str, str] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Render the exact keyword payload expected by ``responses.create``."""

        if not self.model.strip():
            raise ValueError("Responses model must be non-empty")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("Responses max_output_tokens must be positive")

        payload: dict[str, Any] = {
            "model": self.model,
            "input": self.input,
        }
        optional = {
            "instructions": self.instructions,
            "max_output_tokens": self.max_output_tokens,
            "tools": self.tools,
            "text": self.text,
            "store": self.store,
            "temperature": self.temperature,
            "metadata": dict(self.metadata) if self.metadata is not None else None,
        }
        for key, value in optional.items():
            if value is not None:
                payload[key] = value
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        payload.update(self.extra)
        return payload

    @classmethod
    def from_chat(
        cls,
        *,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> "ResponsesRequest":
        """Build a canonical request without changing OpenAI chat-shaped history."""

        return cls(model=model, input=list(messages), **kwargs)


class ResponsesTransport(Protocol):
    """Minimal transport implemented by the official SDK and compatible servers."""

    def create(self, **payload: Any) -> Any: ...


def response_text(response: Any) -> str:
    """Extract text from an SDK object or a JSON-like Responses result."""

    if isinstance(response, Mapping):
        if response.get("output_text"):
            return str(response["output_text"])
        output = response.get("output") or []
    else:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return str(output_text)
        output = getattr(response, "output", []) or []

    parts: list[str] = []
    for item in output:
        item_type = item.get("type") if isinstance(item, Mapping) else getattr(item, "type", "")
        if item_type != "message":
            continue
        content = (
            item.get("content", []) if isinstance(item, Mapping) else getattr(item, "content", [])
        )
        for part in content or []:
            part_type = part.get("type") if isinstance(part, Mapping) else getattr(part, "type", "")
            if part_type == "output_text":
                text = (
                    part.get("text", "") if isinstance(part, Mapping) else getattr(part, "text", "")
                )
                parts.append(str(text or ""))
    return "".join(parts)
