#!/usr/bin/env python3
"""Test a local vLLM server by sending a sample query and printing the response."""

import argparse
import json
import sys
import urllib.request
import urllib.error


def build_example_calculator_tool():
    """Return a simple calculator tool schema for tool-call testing."""
    return {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a basic arithmetic operation on two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "Arithmetic operation to perform.",
                    },
                    "a": {
                        "type": "number",
                        "description": "First operand.",
                    },
                    "b": {
                        "type": "number",
                        "description": "Second operand.",
                    },
                },
                "required": ["operation", "a", "b"],
                "additionalProperties": False,
            },
        },
    }


def merge_stream_tool_calls(aggregated, delta_tool_calls):
    """Merge streamed delta.tool_calls entries into per-index records."""
    if not isinstance(delta_tool_calls, list):
        return
    for call in delta_tool_calls:
        if not isinstance(call, dict):
            continue
        idx = call.get("index", 0)
        entry = aggregated.setdefault(
            idx,
            {"id": "", "type": "", "function": {"name": "", "arguments": ""}},
        )
        if call.get("id"):
            entry["id"] = call["id"]
        if call.get("type"):
            entry["type"] = call["type"]
        fn = call.get("function")
        if isinstance(fn, dict):
            if fn.get("name"):
                entry["function"]["name"] = fn["name"]
            args_piece = fn.get("arguments")
            if isinstance(args_piece, str):
                entry["function"]["arguments"] += args_piece


def print_tool_calls(tool_calls):
    """Print tool calls in a readable format."""
    if not tool_calls:
        return
    print("\n\nTool calls:")
    for i, call in enumerate(tool_calls, start=1):
        print(f"  [{i}] {json.dumps(call, ensure_ascii=False)}")


def main():
    parser = argparse.ArgumentParser(description="Test a local vLLM server")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="vLLM server base URL (default: http://localhost:8000)")
    parser.add_argument("--model", default=None,
                        help="Model name (default: auto-detect from /v1/models)")
    parser.add_argument("--prompt", default="What is the fundamental theorem of algebra? State it precisely.",
                        help="Prompt to send")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Stream tokens to console (default: true)")
    parser.add_argument("--debug-stream", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="Print raw SSE/debug info to stderr (default: false)")
    parser.add_argument("--print-reasoning", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Print streamed delta.reasoning tokens when present (default: true)")
    parser.add_argument("--example-tool", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="Include a sample calculator tool in request (default: false)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    # Check server health
    print(f"Connecting to {base} ...")
    try:
        resp = urllib.request.urlopen(f"{base}/v1/models", timeout=5)
        models_data = json.loads(resp.read())
        available = [m["id"] for m in models_data.get("data", [])]
        print(f"Available models: {available}")
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot reach server at {base}: {e}", file=sys.stderr)
        sys.exit(1)

    model = args.model or available[0] if available else None
    if not model:
        print("ERROR: No model found and none specified with --model", file=sys.stderr)
        sys.exit(1)
    print(f"Using model: {model}\n")

    # Send chat completion request
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0.7,
        "stream": args.stream,
        **({"stream_options": {"include_usage": True}} if args.stream else {}),
    }
    if args.example_tool:
        request_body["tools"] = [build_example_calculator_tool()]
        request_body["tool_choice"] = "auto"
        print("Included example tool: calculator")
    payload = json.dumps(request_body).encode()

    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    print(f"Prompt: {args.prompt}")
    print(f"{'─' * 60}")

    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"ERROR: HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.stream:
        finish_reason = None
        usage = {}
        printed_any = False
        saw_reasoning = False
        tool_calls_by_index = {}
        dim = "\033[2m" if sys.stdout.isatty() else ""
        reset = "\033[0m" if sys.stdout.isatty() else ""
        for line in resp:
            line = line.decode(errors="replace").strip()
            if args.debug_stream:
                print(f"[debug] raw line: {line[:300]}", file=sys.stderr)
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].lstrip()
            if data_str == "[DONE]":
                if args.debug_stream:
                    print("[debug] received [DONE]", file=sys.stderr)
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError as e:
                if args.debug_stream:
                    print(f"[debug] JSON decode error: {e}", file=sys.stderr)
                continue
            if args.debug_stream:
                top_keys = list(chunk.keys())
                print(f"[debug] chunk keys: {top_keys}", file=sys.stderr)
            if "usage" in chunk:
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                if args.debug_stream:
                    print("[debug] chunk has no choices", file=sys.stderr)
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content", "") if isinstance(delta, dict) else ""
            reasoning = ""
            delta_tool_calls = []
            if isinstance(delta, dict):
                reasoning = delta.get("reasoning", "") or delta.get("reasoning_content", "")
                delta_tool_calls = delta.get("tool_calls") or []
                merge_stream_tool_calls(tool_calls_by_index, delta_tool_calls)
            if args.debug_stream and isinstance(delta, dict):
                print(f"[debug] delta keys: {list(delta.keys())}", file=sys.stderr)
                if not content:
                    if reasoning:
                        print("[debug] delta has reasoning but no content", file=sys.stderr)
                if delta_tool_calls:
                    print(f"[debug] delta has tool_calls: {delta_tool_calls}", file=sys.stderr)
            if content:
                sys.stdout.write(content)
                sys.stdout.flush()
                printed_any = True
            elif args.print_reasoning and reasoning:
                if not saw_reasoning:
                    sys.stdout.write(dim)
                    saw_reasoning = True
                sys.stdout.write(reasoning)
                sys.stdout.flush()
                printed_any = True
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr
        if saw_reasoning:
            sys.stdout.write(reset)
        print()
        if tool_calls_by_index:
            ordered = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
            print_tool_calls(ordered)
        if args.debug_stream and not printed_any:
            print("[debug] stream completed, but no printable content tokens were found",
                  file=sys.stderr)
    else:
        data = json.loads(resp.read())
        choice = data["choices"][0]
        print(choice["message"]["content"])
        msg = choice.get("message", {})
        if isinstance(msg, dict):
            print_tool_calls(msg.get("tool_calls") or [])
        finish_reason = choice.get("finish_reason", "?")
        usage = data.get("usage", {})

    print(f"{'─' * 60}")
    print(f"Finish reason: {finish_reason or '?'}")
    print(f"Prompt tokens:     {usage.get('prompt_tokens', '?')}")
    print(f"Completion tokens: {usage.get('completion_tokens', '?')}")
    print(f"Total tokens:      {usage.get('total_tokens', '?')}")


if __name__ == "__main__":
    main()
