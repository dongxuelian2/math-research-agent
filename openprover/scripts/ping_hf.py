#!/usr/bin/env python3
"""Test a local serve_hf.py server by sending a sample query and printing the response."""

import argparse
import json
import sys
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser(description="Test a local serve_hf.py server")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Server base URL (default: http://localhost:8000)")
    parser.add_argument("--model", default=None,
                        help="Model name (default: auto-detect from /v1/models)")
    parser.add_argument("--prompt", default="Prove that the square root of 2 is irrational.",
                        help="Prompt to send")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--max-thinking-tokens", type=int, default=None)
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Stream tokens to console (default: true)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    # Check server health
    print(f"Connecting to {base} ...")
    try:
        resp = urllib.request.urlopen(f"{base}/health", timeout=5)
        health = json.loads(resp.read())
        print(f"Health: {health.get('status', '?')}")
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot reach server at {base}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = urllib.request.urlopen(f"{base}/v1/models", timeout=5)
        models_data = json.loads(resp.read())
        available = [m["id"] for m in models_data.get("data", [])]
        # Build reverse alias map for display
        ALIASES = {}
        for m in available:
            alias = ALIASES.get(m, "")
            print(f"  [{available.index(m)}] {m}" + (f"  (alias: {alias})" if alias else ""))
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot list models: {e}", file=sys.stderr)
        sys.exit(1)

    if args.model is not None:
        # Accept index, alias, or full name
        ALIAS_MAP = {}
        if args.model.isdigit() and int(args.model) < len(available):
            model = available[int(args.model)]
        else:
            model = ALIAS_MAP.get(args.model, args.model)
    elif available:
        model = available[0]
    else:
        print("ERROR: No model found and none specified with --model", file=sys.stderr)
        sys.exit(1)
    print(f"Using model: {model}\n")

    # Send chat completion request
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_output_tokens": args.max_output_tokens,
        "max_thinking_tokens": args.max_thinking_tokens,
        "temperature": 0.6,
        "stream": args.stream,
        **({"stream_options": {"include_usage": True}} if args.stream else {}),
    }).encode()

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
        if e.code == 400 and "streaming disabled in batched mode" in body.lower():
            print(
                "ERROR: Streaming is disabled when serve_hf runs with --batch-size > 1.\n"
                "       Start server with --batch-size 1 for streaming.",
                file=sys.stderr,
            )
            sys.exit(1)
        if e.code == 499:
            print("ERROR: Request was cancelled (client disconnected)", file=sys.stderr)
            sys.exit(130)
        print(f"ERROR: HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.stream:
        finish_reason = None
        usage = {}
        for line in resp:
            line = line.decode().strip()
            if not line.startswith("data: "):
                continue
            data_str = line[len("data: "):]
            if data_str == "[DONE]":
                break
            chunk = json.loads(data_str)
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content", "")
            if content:
                sys.stdout.write(content)
                sys.stdout.flush()
            fr = chunk["choices"][0].get("finish_reason")
            if fr:
                finish_reason = fr
            if "usage" in chunk:
                usage = chunk["usage"]
        print()
    else:
        data = json.loads(resp.read())
        choice = data["choices"][0]
        print(choice["message"]["content"])
        finish_reason = choice.get("finish_reason", "?")
        usage = data.get("usage", {})

    print(f"{'─' * 60}")
    print(f"Finish reason: {finish_reason or '?'}")
    print(f"Prompt tokens:     {usage.get('prompt_tokens', '?')}")
    print(f"Completion tokens: {usage.get('completion_tokens', '?')}")
    print(f"Total tokens:      {usage.get('total_tokens', '?')}")


if __name__ == "__main__":
    main()
