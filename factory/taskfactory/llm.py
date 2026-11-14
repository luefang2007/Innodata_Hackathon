"""Model calls with structured (JSON-schema) output, prompt caching and per-task telemetry.

Two backends with the same interface:
  anthropic   the official Anthropic SDK (ANTHROPIC_API_KEY)
  openrouter  OpenRouter's HTTP API (OPENROUTER_API_KEY), same Claude models
Every request/response pair is written to the task's run directory so the token figures in
pipeline_run.json can be audited against the raw logs.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from . import config

_client_lock = threading.Lock()
_telemetry_lock = threading.Lock()
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    with _client_lock:
        if _client is None:
            _client = anthropic.Anthropic(max_retries=4, timeout=1800.0)
        return _client


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


# ------------------------------------------------------------------------------------------------
# budget guard: every call reserves its worst-case cost first, so parallel tasks can never push
# total spend past the cap
# ------------------------------------------------------------------------------------------------
_budget_lock = threading.Lock()
_spent = 0.0
_reserved = 0.0


def init_budget(already_spent: float) -> None:
    global _spent, _reserved
    with _budget_lock:
        _spent, _reserved = already_spent, 0.0


def budget_state() -> tuple[float, float]:
    with _budget_lock:
        return _spent, config.BUDGET_USD


def _reserve(tag: str, amount: float | None = None) -> float:
    global _reserved
    if config.OFFLINE:
        raise BudgetExceeded(f"offline mode (FACTORY_OFFLINE): not calling a model for '{tag}'")
    amount = config.RESERVE_PER_CALL_USD if amount is None else amount
    with _budget_lock:
        if _spent + _reserved + amount > config.BUDGET_USD:
            raise BudgetExceeded(f"budget cap ${config.BUDGET_USD:.2f}: spent ${_spent:.2f} + in flight "
                                 f"${_reserved:.2f}; not starting '{tag}'")
        _reserved += amount
    return amount


def _settle(cost: float, amount: float | None = None) -> None:
    global _spent, _reserved
    amount = config.RESERVE_PER_CALL_USD if amount is None else amount
    with _budget_lock:
        _reserved = max(0.0, _reserved - amount)
        _spent += cost


# ------------------------------------------------------------------------------------------------
# prompt-cache gate. The provider's cached prefix includes the output schema, so calls with the same
# schema share one cache entry - but only once the first of them has written it. Tasks start in
# parallel; without a gate each one writes the ~160k-token prefix again (run 1 paid for 7 writes).
# The first call per schema goes ahead; the others wait until it returns or CACHE_STAGGER_SEC passed.
# ------------------------------------------------------------------------------------------------
_gate_lock = threading.Lock()
_gates: dict[str, dict] = {}


def reset_cache_gates() -> None:
    with _gate_lock:
        _gates.clear()


def _enter_cache_gate(schema: dict) -> threading.Event | None:
    """Returns the Event to set when a leader call finishes, or None for a follower (after waiting)."""
    key = json.dumps(schema, sort_keys=True)
    with _gate_lock:
        gate = _gates.get(key)
        if gate is None:
            gate = _gates[key] = {"start": time.time(), "done": threading.Event()}
            return gate["done"]
    remaining = gate["start"] + config.CACHE_STAGGER_SEC - time.time()
    if remaining > 0:
        gate["done"].wait(timeout=remaining)
    return None


# ------------------------------------------------------------------------------------------------
# response cache: an identical request (model, system, schema, prompt) is answered from disk
# ------------------------------------------------------------------------------------------------

def _cache_key(system: list[dict], schema: dict, user_text: str, model: str | None = None) -> str:
    import hashlib
    blob = json.dumps([model or config.MODEL, config.EFFORT, system, schema, user_text], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _cache_path(key: str) -> Path:
    return config.CACHE / "llm_cache" / f"{key}.json"


def cache_store(system: list[dict], schema: dict, user_text: str, response_text: str, usage: dict,
                cost: float) -> None:
    p = _cache_path(_cache_key(system, schema, user_text))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"response_text": response_text, "usage": usage, "cost_usd": cost}), encoding="utf-8")


@dataclass
class Telemetry:
    model: str = config.MODEL
    model_calls: int = 0
    n_input_tokens: int = 0          # uncached input
    n_output_tokens: int = 0
    n_cache_read_tokens: int = 0
    n_cache_write_tokens: int = 0
    cost_usd: float = 0.0
    calls: list = field(default_factory=list)

    def add_counts(self, tag: str, inp: int, out: int, cr: int, cw: int, seconds: float,
                   cost: float | None = None) -> None:
        if cost is None:
            p = config.price_for(self.model)
            cost = (inp * p["in"] + out * p["out"] + cr * p["cache_read"] + cw * p["cache_write"]) / 1e6
        with _telemetry_lock:
            self._add(tag, inp, out, cr, cw, seconds, cost)

    def _add(self, tag, inp, out, cr, cw, seconds, cost) -> None:
        self.model_calls += 1
        self.n_input_tokens += inp
        self.n_output_tokens += out
        self.n_cache_read_tokens += cr
        self.n_cache_write_tokens += cw
        self.cost_usd += cost
        self.calls.append({"tag": tag, "input": inp, "output": out, "cache_read": cr, "cache_write": cw,
                           "cost_usd": round(cost, 4), "seconds": round(seconds, 1)})

    def add(self, tag: str, usage, seconds: float) -> None:
        self.add_counts(tag, usage.input_tokens or 0, usage.output_tokens or 0,
                        getattr(usage, "cache_read_input_tokens", 0) or 0,
                        getattr(usage, "cache_creation_input_tokens", 0) or 0, seconds)


def system_blocks(rules: str, repo_context: str) -> list[dict]:
    # Identical bytes for every call in a run -> one cache entry shared by all tasks and attempts.
    return [
        {"type": "text", "text": rules},
        {"type": "text", "text": repo_context, "cache_control": {"type": "ephemeral"}},
    ]


# ------------------------------------------------------------------------------------------------
# OpenRouter backend (plain HTTP)
# ------------------------------------------------------------------------------------------------

def _or_request(path: str, body: dict | None = None, timeout: float = 1800.0) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{config.OPENROUTER_URL}{path}", data=data, method="POST" if data else "GET",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "X-Title": "click-task-factory"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error") and not payload.get("choices"):
                err = payload["error"]
                code = err.get("code") if isinstance(err, dict) else None
                last = LLMError(f"OpenRouter error {code}: {err}")
                if code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise last
            return payload
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last = LLMError(f"OpenRouter HTTP {exc.code}: {detail}")
            if exc.code in (408, 429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise last from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = LLMError(f"OpenRouter connection error: {exc}")
            if attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise last from exc
    raise last or LLMError("OpenRouter request failed")


def _or_usage(payload: dict) -> tuple[int, int, int, int, float | None]:
    u = payload.get("usage") or {}
    prompt = u.get("prompt_tokens") or 0
    details = u.get("prompt_tokens_details") or {}
    cr = details.get("cached_tokens") or 0
    cw = details.get("cache_write_tokens") or 0
    out = u.get("completion_tokens") or 0
    cost = u.get("cost")
    return max(prompt - cr - cw, 0), out, cr, cw, (float(cost) if cost is not None else None)


def _or_body(system: list[dict], user_text: str, max_tokens: int, model: str | None = None,
             effort: str | None = None) -> dict:
    return {
        "model": model or config.MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
        "max_tokens": max_tokens,
        "reasoning": {"effort": effort or config.EFFORT},
        "usage": {"include": True},
        "provider": {"order": ["anthropic"], "allow_fallbacks": True},
    }


# ------------------------------------------------------------------------------------------------
# public interface
# ------------------------------------------------------------------------------------------------

def check_access() -> str:
    """Fail fast on a bad key / model before any work starts. Returns a short description."""
    if config.BACKEND == "openrouter":
        info = _or_request("/key").get("data", {})
        return (f"openrouter key ok (used ${info.get('usage', 0):.2f}"
                + (f" of ${info['limit']:.2f}" if info.get("limit") is not None else "") + ")")
    client().models.retrieve(config.MODEL)
    return "anthropic key ok"


def prewarm(system: list[dict], schema: dict, telemetry: Telemetry, log_dir: Path | None = None,
            name: str = "prewarm") -> None:
    """Write the shared prefix to the cache once before tasks fan out in parallel.

    The output schema is part of the cached prefix, so the warm-up request must carry the same
    schema and reasoning settings as the real calls that follow it.
    """
    t0 = time.time()
    try:
        _reserve(name)
    except BudgetExceeded:
        return
    cost = 0.0
    try:
        if config.BACKEND == "openrouter":
            body = _or_body(system, "warm", 16)
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "strict": True, "schema": schema}}
            payload = _or_request("/chat/completions", body)
            inp, out, cr, cw, cost = _or_usage(payload)
            telemetry.add_counts(name, inp, out, cr, cw, time.time() - t0, cost)
            usage = payload.get("usage")
        else:
            msg = client().messages.create(
                model=config.MODEL, max_tokens=0, system=system,
                output_config={"effort": config.EFFORT, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": "warm"}],
            )
            telemetry.add(name, msg.usage, time.time() - t0)
            usage = msg.usage.to_dict()
        cost = telemetry.calls[-1]["cost_usd"]
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{name}.json").write_text(json.dumps(
                {"tag": name, "model": config.MODEL, "backend": config.BACKEND, "usage": usage},
                indent=1), encoding="utf-8")
    except (anthropic.APIStatusError, LLMError):
        pass  # pre-warming is an optimisation only
    finally:
        _settle(cost)


def call_json(system: list[dict], user_text: str, schema: dict, telemetry: Telemetry,
              log_dir: Path, tag: str, model: str | None = None, effort: str | None = None,
              reserve: float | None = None, use_cache: bool = True) -> dict:
    if model or effort:
        return _call_with_model(system, user_text, schema, telemetry, log_dir, tag, model, effort, reserve)
    if config.RESPONSE_CACHE and use_cache:
        cp = _cache_path(_cache_key(system, schema, user_text))
        if cp.exists():
            hit = json.loads(cp.read_text(encoding="utf-8"))
            u = hit["usage"]
            # no new spend; the original cost stays attributed to the task that needed it
            telemetry.add_counts(f"{tag}(reused)", u.get("input_tokens", 0), u.get("output_tokens", 0),
                                 u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                                 0.0, hit.get("cost_usd", 0.0))
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{len(telemetry.calls):02d}-{tag}-reused.json").write_text(json.dumps({
                "tag": tag, "reused_from_cache": True, "original_cost_usd": hit.get("cost_usd"),
                "user_prompt": user_text, "response_text": hit["response_text"]}, indent=1), encoding="utf-8")
            return json.loads(hit["response_text"])
    if config.OFFLINE:
        raise BudgetExceeded(f"offline mode (FACTORY_OFFLINE): no cached response for '{tag}'")
    leader_done = _enter_cache_gate(schema)
    try:
        _reserve(tag)
        t0 = time.time()
        try:
            return _call_json_uncached(system, user_text, schema, telemetry, log_dir, tag, t0)
        finally:
            _settle(telemetry.calls[-1]["cost_usd"] if telemetry.calls and telemetry.calls[-1]["tag"] == tag
                    else 0.0)
    finally:
        if leader_done is not None:
            leader_done.set()


def _call_with_model(system, user_text, schema, telemetry, log_dir, tag, model, effort, reserve) -> dict:
    """Uncached call with a different model/effort (the solver jury). OpenRouter or the Anthropic SDK."""
    amount = _reserve(tag, reserve)
    cost = 0.0
    model = config.backend_model(model or config.MODEL)
    try:
        if config.BACKEND != "openrouter":
            if "/" in model:
                raise LLMError(f"model {model} needs the OpenRouter backend")
            t0 = time.time()
            try:
                with client().messages.stream(
                    model=model, max_tokens=config.MAX_OUTPUT_TOKENS, system=system,
                    messages=[{"role": "user", "content": user_text}],
                    output_config={"effort": effort or config.EFFORT,
                                   "format": {"type": "json_schema", "schema": schema}},
                ) as stream:
                    msg = stream.get_final_message()
            except anthropic.APIStatusError as exc:
                raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError(f"connection error: {exc}") from exc
            telemetry.add(tag, msg.usage, time.time() - t0)
            cost = telemetry.calls[-1]["cost_usd"]
            text = "".join(b.text for b in msg.content if b.type == "text")
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{tag}.json").write_text(json.dumps({
                "tag": tag, "backend": "anthropic", "model": msg.model, "stop_reason": msg.stop_reason,
                "usage": msg.usage.to_dict(), "user_prompt": user_text, "response_text": text}, indent=1),
                encoding="utf-8")
            if msg.stop_reason in ("refusal", "max_tokens"):
                raise LLMError(f"response stopped early ({msg.stop_reason})")
            return json.loads(text)
        if config.BACKEND == "openrouter":
            t0 = time.time()
            body = _or_body(system, user_text, config.MAX_OUTPUT_TOKENS, model, effort)
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "strict": True, "schema": schema}}
            payload = _or_request("/chat/completions", body)
            inp, out, cr, cw, cost = _or_usage(payload)
            telemetry.add_counts(tag, inp, out, cr, cw, time.time() - t0, cost)
            cost = telemetry.calls[-1]["cost_usd"]
            choice = (payload.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            stop = choice.get("finish_reason")
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{tag}.json").write_text(json.dumps({
                "tag": tag, "backend": "openrouter", "model": payload.get("model"), "stop_reason": stop,
                "usage": payload.get("usage"), "user_prompt": user_text, "response_text": text}, indent=1),
                encoding="utf-8")
            if stop == "length":
                raise LLMError("response truncated at max_tokens")
            return json.loads(text)
        raise LLMError("the probe currently needs the OpenRouter backend")
    except json.JSONDecodeError as exc:
        raise LLMError(f"response was not valid JSON: {exc}") from exc
    finally:
        _settle(cost, amount)


def _call_json_uncached(system: list[dict], user_text: str, schema: dict, telemetry: Telemetry,
                        log_dir: Path, tag: str, t0: float) -> dict:
    if config.BACKEND == "openrouter":
        body = _or_body(system, user_text, config.MAX_OUTPUT_TOKENS)
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "answer", "strict": True, "schema": schema}}
        payload = _or_request("/chat/completions", body)
        seconds = time.time() - t0
        inp, out, cr, cw, cost = _or_usage(payload)
        telemetry.add_counts(tag, inp, out, cr, cw, seconds, cost)
        choice = (payload.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        stop = choice.get("finish_reason")
        usage_log = {**(payload.get("usage") or {}), "input_tokens": inp, "output_tokens": out,
                     "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}
        model_used, request_id = payload.get("model"), payload.get("id")
        refused, truncated = stop in ("content_filter", "refusal"), stop == "length"
    else:
        try:
            with client().messages.stream(
                model=config.MODEL,
                max_tokens=config.MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_text}],
                output_config={"effort": config.EFFORT,
                               "format": {"type": "json_schema", "schema": schema}},
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc
        seconds = time.time() - t0
        telemetry.add(tag, msg.usage, seconds)
        text = "".join(b.text for b in msg.content if b.type == "text")
        stop = msg.stop_reason
        usage_log = msg.usage.to_dict()
        model_used, request_id = msg.model, getattr(msg, "_request_id", None)
        refused, truncated = stop == "refusal", stop == "max_tokens"

    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{len(telemetry.calls):02d}-{tag}.json").write_text(json.dumps({
        "tag": tag, "backend": config.BACKEND, "model": model_used, "request_id": request_id,
        "stop_reason": stop, "usage": usage_log, "seconds": round(seconds, 1),
        "user_prompt": user_text, "response_text": text,
    }, indent=1), encoding="utf-8")
    if refused:
        raise LLMError("model refused the request")
    if truncated:
        raise LLMError("response truncated at max_tokens")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"response was not valid JSON: {exc}") from exc
    if config.RESPONSE_CACHE:
        cache_store(system, schema, user_text, text, usage_log, telemetry.calls[-1]["cost_usd"])
    return parsed


def seed_cache_from_logs(runs_dir: Path, system: list[dict], schemas: dict[str, dict]) -> int:
    """Make calls already paid for in earlier runs reusable (same model, prompt and schema)."""
    n = 0
    if not runs_dir.exists():
        return 0
    for f in runs_dir.rglob("*.json"):
        if f.name in ("summary.json",) or f.name.startswith("prewarm") or f.stem.endswith("-reused"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not d.get("response_text") or "user_prompt" not in d or d.get("stop_reason") not in ("stop", "end_turn"):
            continue
        schema = schemas["design"] if d.get("tag") == "design" else schemas["impl"]
        try:
            json.loads(d["response_text"])
        except json.JSONDecodeError:
            continue
        u = d.get("usage") or {}
        cost = float(u["cost"]) if u.get("cost") is not None else 0.0
        if not _cache_path(_cache_key(system, schema, d["user_prompt"])).exists():
            cache_store(system, schema, d["user_prompt"], d["response_text"], u, cost)
            n += 1
    return n
