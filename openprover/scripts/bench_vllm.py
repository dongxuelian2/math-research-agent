#!/usr/bin/env python3
"""Benchmark tokens/second on a local vLLM server.

Measures:
  - Time to first token (TTFT)
  - Output tokens/second (streaming)
  - Total throughput including prompt processing
  - Runs multiple iterations and reports stats
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request
import urllib.error


PROMPTS = {
    "short": "What is 2+2?",
    "medium": "Explain the Cauchy-Schwarz inequality and give a proof.",
    "long": (
        "You are a research mathematician. Give a detailed proof of the "
        "Banach fixed-point theorem, including all epsilon-delta arguments. "
        "Be rigorous and complete."
    ),
}


def get_model(base: str, model_arg: str | None) -> str:
    resp = urllib.request.urlopen(f"{base}/v1/models", timeout=5)
    models_data = json.loads(resp.read())
    available = [m["id"] for m in models_data.get("data", [])]
    if not available and not model_arg:
        print("ERROR: No models available and none specified", file=sys.stderr)
        sys.exit(1)
    model = model_arg or available[0]
    print(f"Model: {model}")
    return model


def bench_non_streaming(base: str, model: str, prompt: str, max_tokens: int) -> dict:
    """Single non-streaming request, measure total time and token counts."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    t0 = time.perf_counter()
    resp = urllib.request.urlopen(req, timeout=300)
    data = json.loads(resp.read())
    elapsed = time.perf_counter() - t0

    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    return {
        "elapsed_s": elapsed,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "output_tok_per_s": completion_tokens / elapsed if elapsed > 0 else 0,
        "total_tok_per_s": (prompt_tokens + completion_tokens) / elapsed if elapsed > 0 else 0,
    }


def bench_streaming(base: str, model: str, prompt: str, max_tokens: int) -> dict:
    """Streaming request, measure TTFT and per-token throughput."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    t0 = time.perf_counter()
    resp = urllib.request.urlopen(req, timeout=300)

    ttft = None
    token_count = 0
    chunks = []

    for raw_line in resp:
        line = raw_line.decode().strip()
        if not line or not line.startswith("data: "):
            continue
        data_str = line[len("data: "):]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content", "")
        if content:
            if ttft is None:
                ttft = time.perf_counter() - t0
            token_count += 1
            chunks.append(content)

    elapsed = time.perf_counter() - t0
    generation_time = elapsed - (ttft or 0)

    return {
        "elapsed_s": elapsed,
        "ttft_s": ttft or 0,
        "stream_chunks": token_count,
        "generation_s": generation_time,
        "chunks_per_s": token_count / generation_time if generation_time > 0 else 0,
    }


def fmt(val: float, unit: str = "", decimals: int = 1) -> str:
    return f"{val:.{decimals}f}{unit}"


def run_bench(args):
    base = args.base_url.rstrip("/")

    # Check server
    try:
        model = get_model(base, args.model)
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot reach server at {base}: {e}", file=sys.stderr)
        sys.exit(1)

    prompt_text = PROMPTS.get(args.prompt, args.prompt)
    print(f"Prompt ({args.prompt}): {prompt_text[:80]}{'...' if len(prompt_text) > 80 else ''}")
    print(f"Max tokens: {args.max_tokens}, Iterations: {args.iterations}")
    print(f"Mode: {'streaming' if args.stream else 'non-streaming'}")
    print()

    results = []
    for i in range(args.iterations):
        sys.stdout.write(f"  Run {i+1}/{args.iterations}...")
        sys.stdout.flush()
        try:
            if args.stream:
                r = bench_streaming(base, model, prompt_text, args.max_tokens)
                print(f" {fmt(r['chunks_per_s'])} chunks/s, TTFT={fmt(r['ttft_s'], 's', 3)}, total={fmt(r['elapsed_s'], 's')}")
            else:
                r = bench_non_streaming(base, model, prompt_text, args.max_tokens)
                print(f" {fmt(r['output_tok_per_s'])} tok/s, {r['completion_tokens']} tokens in {fmt(r['elapsed_s'], 's')}")
            results.append(r)
        except Exception as e:
            print(f" ERROR: {e}")

    if not results:
        print("\nNo successful runs.")
        return

    # Summary
    print(f"\n{'═' * 60}")
    print("SUMMARY")
    print(f"{'═' * 60}")

    if args.stream:
        ttfts = [r["ttft_s"] for r in results]
        rates = [r["chunks_per_s"] for r in results]
        print(f"Time to first token:  mean={fmt(statistics.mean(ttfts), 's', 3)}", end="")
        if len(ttfts) > 1:
            print(f"  stdev={fmt(statistics.stdev(ttfts), 's', 3)}", end="")
        print()
        print(f"Streaming chunks/s:   mean={fmt(statistics.mean(rates))}", end="")
        if len(rates) > 1:
            print(f"  stdev={fmt(statistics.stdev(rates))}", end="")
        print()
    else:
        out_rates = [r["output_tok_per_s"] for r in results]
        total_rates = [r["total_tok_per_s"] for r in results]
        comp_tokens = [r["completion_tokens"] for r in results]
        print(f"Output tok/s:   mean={fmt(statistics.mean(out_rates))}", end="")
        if len(out_rates) > 1:
            print(f"  stdev={fmt(statistics.stdev(out_rates))}", end="")
        print()
        print(f"Total tok/s:    mean={fmt(statistics.mean(total_rates))}", end="")
        if len(total_rates) > 1:
            print(f"  stdev={fmt(statistics.stdev(total_rates))}", end="")
        print()
        print(f"Completion tokens:  mean={fmt(statistics.mean(comp_tokens), decimals=0)}")

    elapsed_all = [r["elapsed_s"] for r in results]
    print(f"Wall time:      mean={fmt(statistics.mean(elapsed_all), 's')}", end="")
    if len(elapsed_all) > 1:
        print(f"  stdev={fmt(statistics.stdev(elapsed_all), 's')}", end="")
    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark tokens/second on a vLLM server")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="vLLM server base URL (default: http://localhost:8000)")
    parser.add_argument("--model", default=None,
                        help="Model name (default: auto-detect)")
    parser.add_argument("--prompt", default="medium", choices=list(PROMPTS.keys()),
                        help="Prompt preset: short, medium, long (default: medium)")
    parser.add_argument("--custom-prompt", default=None,
                        help="Custom prompt text (overrides --prompt)")
    parser.add_argument("--max-tokens", type=int, default=512,
                        help="Max output tokens (default: 512)")
    parser.add_argument("--iterations", "-n", type=int, default=3,
                        help="Number of iterations (default: 3)")
    parser.add_argument("--stream", action="store_true",
                        help="Use streaming mode (measures TTFT and chunk rate)")
    args = parser.parse_args()

    if args.custom_prompt:
        args.prompt = args.custom_prompt

    run_bench(args)


if __name__ == "__main__":
    main()
