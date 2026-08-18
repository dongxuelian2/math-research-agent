#!/usr/bin/env python3
"""Standalone lean_search tool for testing and benchmarking."""

import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Standalone lean_search")
    parser.add_argument("query", nargs="?", help="Search query (omit for interactive mode)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--no-rerank", action="store_true", help="Disable reranking")
    parser.add_argument("--local-data", action="store_true", default=False,
                        help="Use package-bundled data instead of fetched cache")
    args = parser.parse_args()

    print("Initializing SearchEngine...", flush=True)
    t0 = time.time()
    from lean_explore.search import SearchEngine, Service
    engine = SearchEngine(use_local_data=args.local_data)
    service = Service(engine=engine)
    print(f"Engine ready in {time.time() - t0:.1f}s\n")

    import torch
    rerank = 0 if args.no_rerank else (25 if torch.cuda.is_available() else 0)
    if rerank:
        print(f"Reranking top {rerank} (CUDA available)")
    else:
        print("Reranking disabled" if args.no_rerank else "Reranking disabled (no CUDA)")
    print()

    import asyncio

    def run_query(query: str):
        t0 = time.time()
        response = asyncio.run(service.search(query, limit=args.limit, rerank_top=rerank))
        elapsed = time.time() - t0
        results = response.results
        print(f"--- {len(results)} results in {elapsed:.2f}s ---\n")
        if not results:
            print("No results found.\n")
            return
        for i, r in enumerate(results, 1):
            print(f"{i}. {r.name}")
            if r.module:
                print(f"   module: {r.module}")
            if r.source_text:
                # Show first 3 lines of source
                lines = r.source_text.strip().splitlines()
                for line in lines[:3]:
                    print(f"   {line}")
                if len(lines) > 3:
                    print(f"   ... ({len(lines) - 3} more lines)")
            if r.docstring:
                print(f"   -- {r.docstring.strip().splitlines()[0]}")
            if r.informalization:
                print(f"   info: {r.informalization.strip().splitlines()[0]}")
            print()

    if args.query:
        run_query(args.query)
    else:
        print("Interactive mode — type queries, Ctrl+C to exit\n")
        while True:
            try:
                query = input("query> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not query:
                continue
            run_query(query)


if __name__ == "__main__":
    main()
