#!/usr/bin/env python3
"""Benchmark aggregate completion tokens/second on a serve_hf.py server.

Measures black-box server throughput: total completion tokens completed
per second across all requests.
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed


# Fixed long prompt for consistent throughput measurement
LONG_PROMPT = (
    "Give a detailed, rigorous proof of the Bolzano-Weierstrass theorem: "
    "every bounded sequence in R^n has a convergent subsequence."
)


def get_model(base: str, model_arg: str | None) -> str:
    resp = urllib.request.urlopen(f"{base}/v1/models", timeout=5)
    data = json.loads(resp.read())
    available = [m["id"] for m in data.get("data", [])]
    if not available and not model_arg:
        print("ERROR: No models available and none specified", file=sys.stderr)
        sys.exit(1)
    model = model_arg or available[0]
    print(f"Model: {model}")
    return model


def single_request(
    base: str,
    model: str,
    prompt: str,
    max_output_tokens: int,
    max_thinking_tokens: int | None,
    request_timeout_s: float,
) -> dict:
    """One non-streaming request, return completion tokens and timing."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_output_tokens": max_output_tokens,
        "max_thinking_tokens": max_thinking_tokens,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    t0 = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=request_timeout_s)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
            raise RuntimeError(
                f"request timed out after {request_timeout_s:.1f}s "
                f"(increase --request-timeout)"
            ) from None
        raise RuntimeError(f"request failed: {e}") from None

    data = json.loads(resp.read())
    elapsed = time.perf_counter() - t0
    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)
    return {
        "completion_tokens": completion_tokens,
        "elapsed_s": elapsed,
    }




def run_bench(args):
    base = args.base_url.rstrip("/")

    try:
        model = get_model(base, args.model)
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot reach server at {base}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Prompt: {LONG_PROMPT[:80]}{'...' if len(LONG_PROMPT) > 80 else ''}")
    print(f"Max output tokens: {args.max_output_tokens}")
    print(f"Max thinking tokens: {args.max_thinking_tokens}")
    print(f"Total requests: {args.total_requests}")
    print(f"Client concurrency: {args.concurrency}")
    print()

    # Run all requests with specified concurrency
    t_start = time.perf_counter()
    results = []
    errors = []
    interrupted = False

    pool = ThreadPoolExecutor(max_workers=args.concurrency)
    try:
        futures = {
            pool.submit(
                single_request,
                base,
                model,
                LONG_PROMPT,
                args.max_output_tokens,
                args.max_thinking_tokens,
                args.request_timeout,
            ): i
            for i in range(args.total_requests)
        }
        try:
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    r = fut.result()
                    results.append(r)
                except Exception as e:
                    err_msg = str(e)
                    errors.append((idx, err_msg))
                    print(f"[ERROR] Request {idx}: {err_msg}")
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted. Aborting now; disconnects will cancel server requests.")
            # Shutdown executor immediately without waiting
            pool.shutdown(wait=False, cancel_futures=True)
            
            # Print brief interrupted summary
            print(f"\n{'─' * 60}")
            print("INTERRUPTED")
            print(f"{'─' * 60}")
            print(f"Completed before interrupt: {len(results)}/{args.total_requests}")
            print(f"Errors before interrupt:    {len(errors)}")
            if results:
                total_tokens = sum(r["completion_tokens"] for r in results)
                elapsed = time.perf_counter() - t_start
                print(f"Partial throughput:         {total_tokens / elapsed:.1f} completion tokens/sec")
            print(f"{'─' * 60}")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(130)
    finally:
        # Only wait for completion if not interrupted
        if not interrupted:
            pool.shutdown(wait=True)

    t_end = time.perf_counter()
    total_elapsed = t_end - t_start

    # Calculate aggregate throughput
    total_completion_tokens = sum(r["completion_tokens"] for r in results)
    throughput_tok_per_s = total_completion_tokens / total_elapsed if total_elapsed > 0 else 0

    # Print results
    print(f"{'═' * 60}")
    print("RESULTS")
    print(f"{'═' * 60}")
    print(f"Successful requests:  {len(results)}/{args.total_requests}")
    if errors:
        print(f"Failed requests:       {len(errors)}")
        for idx, err_msg in errors:
            print(f"  Request {idx}: {err_msg}")
    print(f"Total completion tokens: {total_completion_tokens}")
    print(f"Total wall-clock time:   {total_elapsed:.2f}s")
    print(f"{'─' * 60}")
    print(f"Aggregate throughput:    {throughput_tok_per_s:.1f} completion tokens/sec")
    print(f"{'═' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark aggregate completion tokens/second on a serve_hf.py server"
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Server base URL (default: http://localhost:8000)")
    parser.add_argument("--model", default=None,
                        help="Model name (default: auto-detect)")
    parser.add_argument("--max-output-tokens", type=int, default=512,
                        help="Max output tokens (default: 512)")
    parser.add_argument("--max-thinking-tokens", type=int, default=10000,
                        help="Max thinking tokens (default: no explicit cap)")
    parser.add_argument("--total-requests", "-n", type=int, default=100,
                        help="Total number of requests to send (default: 100)")
    parser.add_argument("--concurrency", "-c", type=int, default=32,
                        help="Client-side concurrent requests (default: 32)")
    parser.add_argument("--request-timeout", type=float, default=600.0,
                        help="Per-request HTTP timeout in seconds (default: 600)")
    args = parser.parse_args()

    run_bench(args)


if __name__ == "__main__":
    main()
