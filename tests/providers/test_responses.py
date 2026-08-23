from types import SimpleNamespace

from math_research_agent.providers.responses import ResponsesRequest, response_text


def test_response_text_supports_sdk_objects_and_json_payloads():
    sdk_response = SimpleNamespace(
        output_text="direct",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="ignored")],
            )
        ],
    )
    assert response_text(sdk_response) == "direct"

    json_response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "part one"},
                    {"type": "output_text", "text": "part two"},
                ],
            }
        ]
    }
    assert response_text(json_response) == "part onepart two"


def test_responses_request_from_chat_preserves_messages():
    request = ResponsesRequest.from_chat(
        model="arbitrary-model",
        messages=[{"role": "user", "content": "hello"}],
        store=False,
    )
    assert request.to_payload() == {
        "model": "arbitrary-model",
        "input": [{"role": "user", "content": "hello"}],
        "store": False,
    }
