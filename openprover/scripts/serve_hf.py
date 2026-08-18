#!/usr/bin/env python3
"""Serve HuggingFace models on multiple GPUs with simple load balancing.

Loads model replicas across GPUs (potentially different models on different
GPU subsets) and exposes an OpenAI-compatible API at /v1/chat/completions
(streaming and non-streaming) and /v1/models.

Usage:
    # Single model on 8 GPUs (backward compatible):
    python serve_hf.py --model lm-provers/QED-Nano:8

    # Two models on different GPU subsets:
    python serve_hf.py --model lm-provers/QED-Nano:4 --model Qwen/Qwen3-4B-Thinking-2507:4
"""

import argparse
import json
import logging
import os
import select
import socket
import threading
import time
import traceback
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
from transformers import LogitsProcessor, StoppingCriteria, StoppingCriteriaList


class ThinkBudgetProcessor(LogitsProcessor):
    """Force </think> after a token budget to reserve room for the answer.

    Thinking models (e.g. QED-Nano) start generating reasoning tokens
    immediately and emit </think> before the answer.  If thinking exceeds
    `budget` new tokens, this processor forces the </think> token sequence
    so the model transitions to answer generation.
    """

    def __init__(
        self,
        end_think_ids: list[int],
        forced_close_ids: list[int],
        max_thinking_tokens: int,
        max_output_tokens: int,
        eos_token_id: int | None,
        prompt_len: int,
    ):
        self.end_ids = end_think_ids
        self.forced_ids = forced_close_ids if forced_close_ids else end_think_ids
        self.max_thinking_tokens = max_thinking_tokens
        self.max_output_tokens = max_output_tokens
        self.eos_token_id = eos_token_id
        self.prompt_len = prompt_len
        self.in_thinking = True
        self.force_idx = 0  # which token of forced close sequence to emit next
        self.answer_start_len = None

    def __call__(self, input_ids: torch.LongTensor,
                 scores: torch.FloatTensor) -> torch.FloatTensor:
        seq_len = input_ids.shape[1]

        if not self.in_thinking:
            if self.answer_start_len is None:
                self.answer_start_len = seq_len
            answer_tokens = seq_len - self.answer_start_len
            if self.eos_token_id is not None and answer_tokens >= self.max_output_tokens:
                forced = torch.full_like(scores, float("-inf"))
                forced[:, self.eos_token_id] = 0
                return forced
            return scores

        # Check if </think> was naturally generated
        n = len(self.end_ids)
        if n > 0 and seq_len >= n:
            last_n = input_ids[0, -n:].tolist()
            if last_n == self.end_ids:
                self.in_thinking = False
                self.answer_start_len = seq_len
                return scores

        generated = seq_len - self.prompt_len

        # Budget exceeded — force fallback close sequence token by token
        if generated >= self.max_thinking_tokens:
            if self.force_idx < len(self.forced_ids):
                target = self.forced_ids[self.force_idx]
                self.force_idx += 1
                forced = torch.full_like(scores, float('-inf'))
                forced[:, target] = 0
                return forced
            else:
                self.in_thinking = False
                self.answer_start_len = seq_len

        return scores


class BatchThinkBudgetProcessor(LogitsProcessor):
    """Batch-aware variant of ThinkBudgetProcessor."""

    def __init__(
        self,
        end_think_ids: list[int],
        forced_close_ids: list[int],
        max_thinking_tokens: int,
        max_output_tokens: int,
        eos_token_id: int | None,
        prompt_len: int,
        batch_size: int,
    ):
        self.end_ids = end_think_ids
        self.forced_ids = forced_close_ids if forced_close_ids else end_think_ids
        self.max_thinking_tokens = max_thinking_tokens
        self.max_output_tokens = max_output_tokens
        self.eos_token_id = eos_token_id
        self.prompt_len = prompt_len
        self.in_thinking = [True] * batch_size
        self.force_idx = [0] * batch_size
        self.answer_start_len = [None] * batch_size

    def __call__(self, input_ids: torch.LongTensor,
                 scores: torch.FloatTensor) -> torch.FloatTensor:
        n = len(self.end_ids)
        seq_len = input_ids.shape[1]
        generated = seq_len - self.prompt_len
        forced_scores = None

        for i in range(input_ids.shape[0]):
            if not self.in_thinking[i]:
                if self.answer_start_len[i] is None:
                    self.answer_start_len[i] = seq_len
                answer_tokens = seq_len - self.answer_start_len[i]
                if self.eos_token_id is not None and answer_tokens >= self.max_output_tokens:
                    if forced_scores is None:
                        forced_scores = scores.clone()
                    forced_scores[i, :] = float("-inf")
                    forced_scores[i, self.eos_token_id] = 0
                continue

            # Check if </think> was naturally generated for this sequence.
            if n > 0 and seq_len >= n:
                last_n = input_ids[i, -n:].tolist()
                if last_n == self.end_ids:
                    self.in_thinking[i] = False
                    self.answer_start_len[i] = seq_len
                    continue

            if generated >= self.max_thinking_tokens:
                if self.force_idx[i] < len(self.forced_ids):
                    target = self.forced_ids[self.force_idx[i]]
                    self.force_idx[i] += 1
                    if forced_scores is None:
                        forced_scores = scores.clone()
                    forced_scores[i, :] = float("-inf")
                    forced_scores[i, target] = 0
                else:
                    self.in_thinking[i] = False
                    self.answer_start_len[i] = seq_len

        return forced_scores if forced_scores is not None else scores


class CancelBatchCriteria(StoppingCriteria):
    """Stop generation early when cancellation is requested."""

    def __init__(self, cancel_event: threading.Event):
        self.cancel_event = cancel_event

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        return self.cancel_event.is_set()


# Request batch item for generation scheduling.
BatchItem = namedtuple(
    "BatchItem",
    ["model", "messages", "max_output_tokens", "temperature", "top_p", "max_thinking_tokens"],
)


class Worker:
    """One model replica on one GPU."""

    _load_lock = threading.Lock()

    def __init__(self, gpu_id: int, model_name: str, dtype: torch.dtype, tokenizer):
        self.gpu_id = gpu_id
        self.model_name = model_name
        self.device = f"cuda:{gpu_id}"
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.tokenizer = tokenizer
        self.active_requests = 0  # Track active requests for load balancing

        # Pre-compute </think> token IDs for thinking budget enforcement
        self.end_think_ids = tokenizer.encode("</think>", add_special_tokens=False)
        self.forced_think_close_ids = tokenizer.encode(
            "\n\nThinking budget depleted. We'll now provide answer.\n</think>\n",
            add_special_tokens=False,
        )

        print(f"  Loading on cuda:{gpu_id}...")
        # Serialize from_pretrained to avoid meta-tensor race conditions,
        # but .to(device) runs outside the lock so GPU transfers overlap.
        with Worker._load_lock:
            model = AutoModelForCausalLM.from_pretrained(model_name, dtype=dtype)
        self.model = model.to(self.device)
        self.model.eval()
        model_eos = self.model.config.eos_token_id
        if isinstance(model_eos, list):
            model_eos = model_eos[0] if model_eos else None
        self.eos_token_id = self.tokenizer.eos_token_id if self.tokenizer.eos_token_id is not None else model_eos
        print(f"  cuda:{gpu_id} ready")

    def generate(
        self,
        messages,
        max_output_tokens,
        temperature,
        top_p,
        stream=False,
        max_thinking_tokens=None,
        cancel_event: threading.Event | None = None,
    ):
        """Single-request generation (legacy, for streaming)."""
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = self.tokenizer([text], return_tensors="pt").to(self.device)
        input_len = inputs.input_ids.shape[1]

        gen_kwargs = dict(
            **inputs,
            max_new_tokens=(
                max_output_tokens
                + (max_thinking_tokens or 0)
                + (len(self.forced_think_close_ids) if max_thinking_tokens is not None else 0)
            ),
            do_sample=temperature > 0,
        )
        if temperature > 0:
            gen_kwargs["temperature"] = temperature
            gen_kwargs["top_p"] = top_p
        else:
            gen_kwargs["temperature"] = None
            gen_kwargs["top_p"] = None
            gen_kwargs["top_k"] = None

        # Thinking budget enforcement
        if max_thinking_tokens is not None and self.end_think_ids:
            processor = ThinkBudgetProcessor(
                self.end_think_ids,
                self.forced_think_close_ids,
                max_thinking_tokens,
                max_output_tokens,
                self.eos_token_id,
                input_len,
            )
            gen_kwargs["logits_processor"] = [processor]
        if cancel_event is not None:
            gen_kwargs["stopping_criteria"] = StoppingCriteriaList([
                CancelBatchCriteria(cancel_event),
            ])

        if stream:
            streamer = TextIteratorStreamer(
                self.tokenizer, skip_prompt=True, skip_special_tokens=True,
            )
            gen_kwargs["streamer"] = streamer
            result = {}

            def _run():
                with torch.inference_mode():
                    output = self.model.generate(**gen_kwargs)
                result["completion_tokens"] = output.shape[1] - input_len

            thread = threading.Thread(target=_run)
            thread.start()
            return streamer, input_len, thread, result
        else:
            with torch.inference_mode():
                output = self.model.generate(**gen_kwargs)
            new_tokens = output[0][input_len:]
            text_out = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
            return text_out, len(new_tokens), input_len

    def generate_batch(
        self,
        batch_items: list[BatchItem],
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Batched non-streaming generation.
        
        Returns list of dicts with keys: text, completion_tokens, prompt_tokens
        """
        # Build prompts for all items
        texts = []
        for item in batch_items:
            text = self.tokenizer.apply_chat_template(
                item.messages, tokenize=False, add_generation_prompt=True,
            )
            texts.append(text)

        # Tokenize batch with padding
        inputs = self.tokenizer(
            texts, return_tensors="pt", padding=True, truncation=True
        ).to(self.device)
        batch_size = inputs.input_ids.shape[0]
        input_lens = (inputs.attention_mask.sum(dim=1)).tolist()

        # All items in batch must have same generation params (enforced by scheduler)
        first = batch_items[0]
        gen_kwargs = dict(
            **inputs,
            max_new_tokens=(
                first.max_output_tokens
                + (first.max_thinking_tokens or 0)
                + (len(self.forced_think_close_ids) if first.max_thinking_tokens is not None else 0)
            ),
            do_sample=first.temperature > 0,
        )
        if first.temperature > 0:
            gen_kwargs["temperature"] = first.temperature
            gen_kwargs["top_p"] = first.top_p
        else:
            gen_kwargs["temperature"] = None
            gen_kwargs["top_p"] = None
            gen_kwargs["top_k"] = None

        if cancel_event is not None:
            gen_kwargs["stopping_criteria"] = StoppingCriteriaList([
                CancelBatchCriteria(cancel_event),
            ])

        # Thinking budget enforcement (applies to all items in this batch key).
        if first.max_thinking_tokens is not None:
            prompt_len = inputs.input_ids.shape[1]
            processor = BatchThinkBudgetProcessor(
                self.end_think_ids,
                self.forced_think_close_ids,
                first.max_thinking_tokens,
                first.max_output_tokens,
                self.eos_token_id,
                prompt_len,
                batch_size,
            )
            existing = gen_kwargs.get("logits_processor", [])
            gen_kwargs["logits_processor"] = [*existing, processor]

        # Generate batch
        with torch.inference_mode():
            output = self.model.generate(**gen_kwargs)

        # Decode per-sample and compute stats
        results = []
        prompt_seq_len = inputs.input_ids.shape[1]
        for i in range(batch_size):
            input_len = input_lens[i]
            new_tokens = output[i][prompt_seq_len:]
            text_out = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
            completion_tokens = len(new_tokens)
            results.append({
                "text": text_out,
                "completion_tokens": completion_tokens,
                "prompt_tokens": input_len,
            })

        return results


class PendingRequest:
    """Pending request with completion synchronization and cancellation."""
    def __init__(self, batch_item, enqueue_time, completion_event):
        self.batch_item = batch_item
        self.enqueue_time = enqueue_time
        self.completion_event = completion_event
        self.result = None
        self.error = None
        self._cancelled = False
        self._cancel_lock = threading.Lock()
        self.batch_state = None  # Set when request is added to a batch

    def mark_cancelled(self):
        """Mark this request as cancelled."""
        with self._cancel_lock:
            if self._cancelled:
                return  # Already cancelled
            self._cancelled = True
            self.error = RuntimeError("cancelled")
            # Notify batch state if in an active batch
            if self.batch_state is not None:
                self.batch_state.on_request_cancelled()
            self.completion_event.set()

    def is_cancelled(self) -> bool:
        """Check if this request is cancelled."""
        with self._cancel_lock:
            return self._cancelled


class ActiveBatchState:
    """Track live request count for an active batch."""
    def __init__(self, requests: list[PendingRequest]):
        self.lock = threading.Lock()
        self.live_count = len(requests)
        self.cancel_event = threading.Event()
        # Link requests to this batch state
        for req in requests:
            req.batch_state = self

    def on_request_cancelled(self):
        """Called when a request in this batch is cancelled."""
        with self.lock:
            self.live_count -= 1
            if self.live_count <= 0:
                # All requests cancelled - signal stop generation
                self.cancel_event.set()


class BatchScheduler:
    """Dynamic batching scheduler with timeout and batch-size triggers."""

    def __init__(self, workers_by_model: dict[str, list[Worker]], batch_size: int, batch_timeout_s: float, verbose: bool = False):
        self.workers_by_model = workers_by_model
        self.batch_size = batch_size
        self.batch_timeout_s = batch_timeout_s
        self.verbose = verbose
        self.pending_queue = []
        self.queue_lock = threading.Condition()
        self.running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    @staticmethod
    def _config_key(batch_item: BatchItem) -> tuple:
        """Get generation config key for grouping requests (model + gen params)."""
        return (batch_item.model, batch_item.max_output_tokens, batch_item.temperature,
                batch_item.top_p, batch_item.max_thinking_tokens)

    def enqueue(self, batch_item: BatchItem) -> PendingRequest:
        """Enqueue a request and return a PendingRequest to wait on."""
        req = PendingRequest(
            batch_item=batch_item,
            enqueue_time=time.time(),
            completion_event=threading.Event(),
        )
        with self.queue_lock:
            self.pending_queue.append(req)
            self.queue_lock.notify()
        return req

    def _scheduler_loop(self):
        """Main scheduler loop: dispatch batches to free workers."""
        while self.running:
            with self.queue_lock:
                # Wait until we have requests or shutdown
                while self.running and len(self.pending_queue) == 0:
                    self.queue_lock.wait(timeout=0.1)

                if not self.running:
                    break

                # Drop canceled requests before scheduling.
                self.pending_queue = [req for req in self.pending_queue if not req.is_cancelled()]
                if not self.pending_queue:
                    continue

                # Find free workers grouped by model
                free_workers_by_model = {}
                for model_name, workers in self.workers_by_model.items():
                    for w in workers:
                        with w.state_lock:
                            if w.active_requests == 0:
                                free_workers_by_model.setdefault(model_name, []).append(w)

                if not free_workers_by_model:
                    # No free workers, wait a bit before checking again
                    self.queue_lock.wait(timeout=0.05)
                    continue

                dispatched_any = False

                # Group pending requests by config key (includes model)
                config_groups = {}
                for req in self.pending_queue:
                    key = self._config_key(req.batch_item)
                    config_groups.setdefault(key, []).append(req)

                # Try to dispatch batches to free workers
                for model_name, free_workers in free_workers_by_model.items():
                    for worker in free_workers:
                        # Reserve this worker
                        with worker.state_lock:
                            if worker.active_requests != 0:
                                continue
                            worker.active_requests += 1

                        # Clean queue
                        self.pending_queue = [req for req in self.pending_queue if not req.is_cancelled()]
                        if not self.pending_queue:
                            with worker.state_lock:
                                worker.active_requests -= 1
                            break

                        # Recompute groups for this model only
                        model_groups = {}
                        for req in self.pending_queue:
                            if req.batch_item.model == model_name:
                                key = self._config_key(req.batch_item)
                                model_groups.setdefault(key, []).append(req)

                        if not model_groups:
                            with worker.state_lock:
                                worker.active_requests -= 1
                            continue

                        # Find oldest request for this model
                        model_reqs = [req for req in self.pending_queue if req.batch_item.model == model_name]
                        oldest_req = model_reqs[0] if model_reqs else None
                        oldest_wait = (time.time() - oldest_req.enqueue_time) if oldest_req else 0

                        batch_requests = None

                        # Priority 1: Full batch available for any config
                        for key, group in model_groups.items():
                            if len(group) >= self.batch_size:
                                batch_requests = group[:self.batch_size]
                                break

                        # Priority 2: Timeout trigger
                        if not batch_requests and oldest_req and oldest_wait >= self.batch_timeout_s:
                            oldest_key = self._config_key(oldest_req.batch_item)
                            if oldest_key in model_groups:
                                group = model_groups[oldest_key]
                                batch_requests = group[:min(self.batch_size, len(group))]

                        if not batch_requests:
                            with worker.state_lock:
                                worker.active_requests -= 1
                            continue

                        # Remove from queue
                        batch_items = [req.batch_item for req in batch_requests]
                        for req in batch_requests:
                            self.pending_queue.remove(req)

                        # Create batch state to track live requests
                        batch_state = ActiveBatchState(batch_requests)

                        # Dispatch to worker in background thread
                        dispatched_any = True
                        if self.verbose:
                            print(f"[BATCH] Dispatching batch of size {len(batch_items)} to GPU {worker.gpu_id} ({model_name})")
                        threading.Thread(
                            target=self._process_batch,
                            args=(worker, batch_requests, batch_items, batch_state),
                            daemon=True
                        ).start()

                if not dispatched_any and self.pending_queue:
                    oldest_req = self.pending_queue[0]
                    oldest_wait = time.time() - oldest_req.enqueue_time
                    sleep_time = max(0.01, self.batch_timeout_s - oldest_wait)
                    self.queue_lock.wait(timeout=sleep_time)

    def _process_batch(self, worker: Worker, requests: list[PendingRequest],
                       batch_items: list[BatchItem],
                       batch_state: ActiveBatchState):
        """Process a batch on a worker and signal completion."""
        batch_size = len(batch_items)
        gpu_id = worker.gpu_id
        try:
            with worker.lock:
                results = worker.generate_batch(batch_items, cancel_event=batch_state.cancel_event)
            
            # Check if batch was cancelled (all requests cancelled)
            if batch_state.cancel_event.is_set():
                # Mark all requests as cancelled if not already
                for req in requests:
                    if not req.is_cancelled():
                        req.mark_cancelled()
                if self.verbose:
                    print(f"[BATCH] Cancelled batch of size {batch_size} on GPU {gpu_id} (all requests cancelled)")
                return

            # Keep index alignment with the original batch order.
            for idx, req in enumerate(requests):
                if req.is_cancelled():
                    continue
                if idx >= len(results):
                    req.error = RuntimeError("missing result")
                    req.completion_event.set()
                    continue
                req.result = results[idx]
                req.completion_event.set()

            if self.verbose:
                completed_count = sum(1 for req in requests if not req.is_cancelled() and req.result is not None)
                total_completion_tokens = sum(
                    req.result["completion_tokens"]
                    for req in requests
                    if req.result is not None and not req.is_cancelled()
                )
                print(f"[BATCH] Completed batch of size {batch_size} on GPU {gpu_id} "
                      f"({completed_count} live, {total_completion_tokens} completion tokens)")
        except Exception as e:
            # Propagate error to non-cancelled requests only
            for req in requests:
                if not req.is_cancelled():
                    req.error = e
                    req.completion_event.set()
            if self.verbose:
                print(f"[BATCH] ERROR: batch of size {batch_size} on GPU {gpu_id} failed: {e}")
        finally:
            with worker.state_lock:
                worker.active_requests -= 1
            # Wake scheduler to check for more work
            with self.queue_lock:
                self.queue_lock.notify()

    def shutdown(self):
        """Stop scheduler and wait for completion."""
        self.running = False
        with self.queue_lock:
            self.queue_lock.notify_all()
        self.scheduler_thread.join(timeout=5.0)


class LoadBalancer:
    """Round-robin worker picker for direct mode, model-aware."""

    def __init__(self, workers_by_model: dict[str, list[Worker]]):
        self.workers_by_model = workers_by_model
        self._counters: dict[str, int] = {m: 0 for m in workers_by_model}
        self._lock = threading.Lock()

    def get_worker(self, model: str, prefer_free: bool = False) -> Worker:
        workers = self.workers_by_model[model]
        with self._lock:
            counter = self._counters[model]
            if prefer_free:
                for offset in range(len(workers)):
                    idx = (counter + offset) % len(workers)
                    worker = workers[idx]
                    with worker.state_lock:
                        if worker.active_requests == 0:
                            worker.active_requests += 1
                            self._counters[model] = idx + 1
                            return worker
            worker = workers[counter % len(workers)]
            with worker.state_lock:
                worker.active_requests += 1
            self._counters[model] = counter + 1
        return worker


# Globals set in main
scheduler: BatchScheduler = None
lb: LoadBalancer = None
available_models: list[str] = []
batching_enabled: bool = True


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # quiet

    def do_GET(self):
        if self.path == "/v1/models":
            self._json_response(200, {
                "object": "list",
                "data": [{"id": m, "object": "model"} for m in available_models],
            })
        elif self.path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._json_response(404, {"error": "not found"})
            return

        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        parsed = self._parse_generation_params(body)
        if parsed is None:
            return
        model, messages, max_output_tokens, temperature, top_p, stream, max_thinking_tokens = parsed

        if not batching_enabled:
            self._handle_direct_request(
                model, messages, max_output_tokens, temperature, top_p, stream, max_thinking_tokens,
            )
            return

        # Keep streaming simple: in batched mode, stream requests take
        # the direct single-request path (effectively batch size 1).
        if stream:
            self._handle_direct_request(
                model, messages, max_output_tokens, temperature, top_p, stream, max_thinking_tokens,
            )
            return

        # Enqueue for batched processing
        batch_item = BatchItem(
            model=model,
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            max_thinking_tokens=max_thinking_tokens,
        )
        req = scheduler.enqueue(batch_item)

        # Wait for completion with disconnect detection
        # Poll in short intervals to check for client disconnect
        timeout_remaining = 600.0  # 10 min total timeout
        poll_interval = 0.1  # 100ms polling

        while timeout_remaining > 0:
            wait_time = min(poll_interval, timeout_remaining)
            if req.completion_event.wait(timeout=wait_time):
                # Request completed
                break

            timeout_remaining -= wait_time

            # Check if client disconnected
            if self._is_client_disconnected():
                req.mark_cancelled()
                return  # Client disconnected, don't send response

        if timeout_remaining <= 0:
            req.mark_cancelled()
            self._json_response(504, {"error": "request timeout"})
            return

        # Request completed - check if it was cancelled
        if req.is_cancelled():
            return  # Client disconnected, don't send response

        try:
            if req.error:
                error_text = str(req.error)
                if "cancelled" in error_text.lower():
                    # Already cancelled, don't send response
                    return
                else:
                    self._json_response(500, {"error": error_text})
                return

            result = req.result
            if result is None:
                # Shouldn't happen, but handle gracefully
                return

            t0 = int(time.time())
            self._json_response(200, {
                "id": f"chatcmpl-{t0}",
                "object": "chat.completion",
                "created": t0,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": result["text"]},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": result["prompt_tokens"],
                    "completion_tokens": result["completion_tokens"],
                    "total_tokens": result["prompt_tokens"] + result["completion_tokens"],
                },
            })
        except BrokenPipeError:
            # Client disconnected during response write
            req.mark_cancelled()
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._json_response(500, {"error": str(e)})
            except BrokenPipeError:
                req.mark_cancelled()
                pass

    def _is_client_disconnected(self) -> bool:
        """Check if client connection is closed."""
        try:
            sock = self.connection
            if sock is None:
                return True

            # If socket is readable, peek one byte:
            # - b"" => peer closed
            # - BlockingIOError/no data => still connected
            readable, _, _ = select.select([sock], [], [], 0)
            if not readable:
                return False
            try:
                data = sock.recv(1, socket.MSG_PEEK)
                return len(data) == 0
            except BlockingIOError:
                return False
            except (ConnectionResetError, OSError):
                return True
        except Exception:
            # If we can't check, assume still connected (conservative)
            pass
        return False

    def _parse_generation_params(self, body):
        model = body.get("model")
        if not model or not isinstance(model, str):
            self._json_response(400, {"error": "missing required field: model"})
            return None
        model = _resolve_alias(model)
        if model not in available_models:
            self._json_response(400, {"error": f"unknown model: {model!r}. Available: {available_models}"})
            return None

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._json_response(400, {"error": "field 'messages' must be a non-empty list"})
            return None

        if "max_output_tokens" not in body:
            self._json_response(400, {"error": "missing required field: max_output_tokens"})
            return None
        max_output_tokens = body["max_output_tokens"]
        if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            self._json_response(400, {"error": "field 'max_output_tokens' must be a positive integer"})
            return None

        max_thinking_tokens = body.get("max_thinking_tokens")
        if max_thinking_tokens is not None:
            if not isinstance(max_thinking_tokens, int) or max_thinking_tokens < 0:
                self._json_response(400, {"error": "field 'max_thinking_tokens' must be a non-negative integer or null"})
                return None

        temperature = body.get("temperature", 0.6)
        if not isinstance(temperature, (int, float)) or temperature < 0:
            self._json_response(400, {"error": "field 'temperature' must be a non-negative number"})
            return None
        temperature = float(temperature)

        top_p = body.get("top_p", 0.95)
        if not isinstance(top_p, (int, float)) or top_p <= 0 or top_p > 1:
            self._json_response(400, {"error": "field 'top_p' must be in (0, 1]"})
            return None
        top_p = float(top_p)

        stream = body.get("stream", False)
        if not isinstance(stream, bool):
            self._json_response(400, {"error": "field 'stream' must be a boolean"})
            return None

        return model, messages, max_output_tokens, temperature, top_p, stream, max_thinking_tokens

    def _handle_direct_request(self, model, messages, max_output_tokens, temperature, top_p, stream, max_thinking_tokens):
        worker = lb.get_worker(model, prefer_free=True)
        try:
            with worker.lock:
                try:
                    if stream:
                        self._handle_streaming(worker, model, messages, max_output_tokens, temperature, top_p, max_thinking_tokens)
                    else:
                        self._handle_non_streaming(worker, model, messages, max_output_tokens, temperature, top_p, max_thinking_tokens)
                except BrokenPipeError:
                    pass
                except Exception as e:
                    traceback.print_exc()
                    try:
                        self._json_response(500, {"error": str(e)})
                    except BrokenPipeError:
                        pass
        finally:
            with worker.state_lock:
                worker.active_requests -= 1
            if scheduler is not None:
                with scheduler.queue_lock:
                    scheduler.queue_lock.notify()

    def _handle_non_streaming(self, worker, model, messages, max_output_tokens, temperature, top_p, max_thinking_tokens=None):
        t0 = time.time()
        text, completion_tokens, prompt_tokens = worker.generate(
            messages,
            max_output_tokens,
            temperature,
            top_p,
            stream=False,
            max_thinking_tokens=max_thinking_tokens,
        )
        self._json_response(200, {
            "id": f"chatcmpl-{int(t0)}",
            "object": "chat.completion",
            "created": int(t0),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": int(completion_tokens),
                "total_tokens": prompt_tokens + int(completion_tokens),
            },
        })

    def _handle_streaming(self, worker, model, messages, max_output_tokens, temperature, top_p, max_thinking_tokens=None):
        cancel_event = threading.Event()
        streamer, input_len, gen_thread, result = worker.generate(
            messages,
            max_output_tokens,
            temperature,
            top_p,
            stream=True,
            max_thinking_tokens=max_thinking_tokens,
            cancel_event=cancel_event,
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chunk_id = f"chatcmpl-{int(time.time())}"

        disconnected = False
        try:
            for token_text in streamer:
                if not token_text:
                    continue
                chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": token_text},
                        "finish_reason": None,
                    }],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
        except BrokenPipeError:
            disconnected = True
            cancel_event.set()
            raise
        finally:
            cancel_event.set()
            gen_thread.join()

        completion_tokens = result.get("completion_tokens", 0)
        final = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": input_len,
                "completion_tokens": completion_tokens,
                "total_tokens": input_len + completion_tokens,
            },
        }
        if not disconnected:
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()


    def _json_response(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # Default TCPServer backlog is small (typically 5), which can cause
    # connection resets under high client concurrency.
    request_queue_size = 1024


# Short aliases for common models.
MODEL_ALIASES = {
    "qed-nano": "lm-provers/QED-Nano",
    "qwen3-4b": "Qwen/Qwen3-4B-Thinking-2507",
}


def _resolve_alias(name: str) -> str:
    """Resolve a short alias to the full HuggingFace model name."""
    return MODEL_ALIASES.get(name, name)


def _parse_model_spec(spec: str) -> tuple[str, int]:
    """Parse 'ModelName:NumGPUs' spec. Returns (model_name, num_gpus).

    Accepts short aliases (e.g. 'qed-nano:4') or full names.
    """
    if ":" in spec:
        parts = spec.rsplit(":", 1)
        try:
            return _resolve_alias(parts[0]), int(parts[1])
        except ValueError:
            pass
    # No colon or invalid number — treat whole string as model name, default 1 GPU
    return _resolve_alias(spec), 1


def main():
    global scheduler, lb, available_models, batching_enabled

    parser = argparse.ArgumentParser(
        description="Serve HuggingFace models on multiple GPUs with dynamic batching",
    )
    parser.add_argument("--model", action="append", dest="models", metavar="MODEL:GPUS",
                        help="Model spec as MODEL_NAME:NUM_GPUS (repeatable, GPUs assigned sequentially)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Server port (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Server host (default: 0.0.0.0)")
    parser.add_argument("--dtype", default="bfloat16",
                        choices=["float16", "bfloat16", "float32"],
                        help="Model dtype (default: bfloat16)")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Batch size for dynamic batching (default: 32)")
    parser.add_argument("--batch-timeout", type=float, default=1.0,
                        help="Batch timeout in seconds (default: 1.0)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print batch processing information")
    args = parser.parse_args()

    # Parse model specs
    model_specs = [_parse_model_spec(s) for s in args.models]
    total_gpus = sum(n for _, n in model_specs)

    n_available = torch.cuda.device_count()
    if n_available < total_gpus:
        print(f"ERROR: requested {total_gpus} GPUs but only {n_available} available")
        return
    if total_gpus == 0:
        print("ERROR: no GPUs requested")
        return

    batching_enabled = args.batch_size > 1
    dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[args.dtype]

    # Load models onto GPU subsets
    workers_by_model: dict[str, list[Worker]] = {}
    gpu_offset = 0

    for model_name, num_gpus in model_specs:
        gpu_ids = list(range(gpu_offset, gpu_offset + num_gpus))
        gpu_offset += num_gpus

        print(f"Loading {num_gpus} replica(s) of {model_name} on GPUs {gpu_ids}...")
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "left"

        # Load first replica with full output (shows warnings like MISSING weights)
        model_workers = [Worker(gpu_ids[0], model_name, dtype, tokenizer)]

        if num_gpus > 1:
            os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
            logging.getLogger("transformers.modeling_utils").setLevel(logging.ERROR)
            with ThreadPoolExecutor(max_workers=num_gpus - 1) as pool:
                model_workers += list(pool.map(
                    lambda i: Worker(i, model_name, dtype, tokenizer),
                    gpu_ids[1:],
                ))

        workers_by_model[model_name] = model_workers

    available_models = list(workers_by_model.keys())

    # Direct lane is always available for streaming (and all requests when batch_size == 1).
    lb = LoadBalancer(workers_by_model)
    if batching_enabled:
        scheduler = BatchScheduler(workers_by_model, args.batch_size, args.batch_timeout, args.verbose)

    server = ThreadedHTTPServer((args.host, args.port), Handler)
    print(f"\nServing on http://{args.host}:{args.port}")
    for model_name, num_gpus in model_specs:
        gpu_start = sum(n for m, n in model_specs[:model_specs.index((model_name, num_gpus))])
        print(f"  {model_name}: {num_gpus} GPU(s) [{gpu_start}-{gpu_start + num_gpus - 1}]")
    print(f"  Mode:  {'batched' if batching_enabled else 'direct'}")
    print(f"  dtype: {args.dtype}")
    if batching_enabled:
        print(f"  Batch size: {args.batch_size}")
        print(f"  Batch timeout: {args.batch_timeout}s")
    else:
        print("  Batch size: 1 (streaming/direct path enabled)")
    if args.verbose:
        print(f"  Verbose mode: enabled")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        if scheduler is not None:
            scheduler.shutdown()
        server.shutdown()


if __name__ == "__main__":
    main()
