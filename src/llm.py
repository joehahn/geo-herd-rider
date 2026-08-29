"""llm.py — provider-agnostic LLM seam (Anthropic + OpenRouter). PARKED.

Built for the retired causal-ladder curator; not wired into the firehose (which calls the
Anthropic SDK directly). Kept as the provider-agnostic seam should the firehose scan want a
cheap-model path. It hides the provider so the SAME call runs on Anthropic (Opus/Haiku) or any
OpenRouter model (DeepSeek, Qwen, Llama, ...) — the seam for the cheap-model bake-off (TODO + the diplomacy-A2A
LLMClient pattern this mirrors). The 10x-cheaper path: ladder on a cheap-but-capable OpenRouter
model instead of Opus, synchronously (no Batch latency), validated by the scoreboard.

Cost is recorded centrally via costs.py. Both providers run synchronously.

  client = make_client("anthropic", "claude-opus-4-8")
  client = make_client("openrouter", "deepseek/deepseek-chat-v3.2")   # needs OPENROUTER_API_KEY
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

import costs
import trace

ANTHROPIC_DEFAULT = "claude-opus-4-8"
# Adaptive thinking + effort + dynamic-filtering web search work on these; cheaper Anthropic
# models (Haiku) reject them and use the basic web-search variant.
_ADVANCED = ("opus-4", "sonnet-4-6", "sonnet-5", "fable", "mythos")


def _supports_advanced(model: str) -> bool:
    return any(k in model for k in _ADVANCED)


class LLMClient(ABC):
    """One call: reason over (system, user), optionally web-search, return final text.
    Implementations record their own token/$ cost via costs.record()."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def complete(self, system: str, user: str, *, use_web_search: bool, label: str,
                 stage: str = "ladder", json_schema: dict | None = None,
                 search_query: str | None = None, before_date: str | None = None,
                 effort: str = "high") -> str: ...


class AnthropicClient(LLMClient):
    """Anthropic Messages API with server-side web search (look-ahead via before:<date> in
    the model's queries) and adaptive thinking on the advanced models."""

    def __init__(self, model: str = ANTHROPIC_DEFAULT):
        import anthropic
        super().__init__(model)
        self._c = anthropic.Anthropic()

    def complete(self, system, user, *, use_web_search, label, stage="ladder",
                 json_schema=None, search_query=None, before_date=None, effort="high") -> str:
        # json_schema/search_query/before_date are ignored here: the Anthropic path parses
        # free-form fenced JSON and uses its own server-side, before:<date> web search.
        m = self.model
        if use_web_search:
            ws = "web_search_20260209" if _supports_advanced(m) else "web_search_20250305"
            tools = [{"type": ws, "name": "web_search"}]
        else:
            tools = []
        messages = [{"role": "user", "content": user}]
        kw = {"model": m, "max_tokens": 8000, "system": system, "tools": tools, "messages": messages}
        # GREEDY DECODING. Temperature was never set, so every call sampled at the provider default
        # (1.0) -- measured 2026-08-10: two byte-identical 13-scan backtests produced 10 vs 21 tickers,
        # a same-config Jaccard of 0.29. The scout runs first each week and decides which events exist,
        # so one sampled difference in week 1 cascades through every later week. At that noise floor no
        # single-run A/B can resolve anything, which is why the event_news_cap 20-vs-10 comparison came
        # back BELOW the floor and measured nothing. Pinned to 0 to make sweeps interpretable.
        # NOTE this does not give bit-exact determinism (provider batching/hardware still jitter), and
        # it CHANGES the curator -- greedy decoding picks systematically differently from sampling --
        # so runs before and after this are different configs.
        if _supports_advanced(m):  # Haiku rejects effort + adaptive thinking
            kw["thinking"] = {"type": "adaptive"}
            kw["output_config"] = {"effort": effort}   # 'high' default; picker passes 'low' (ranking needs little thinking)
        else:
            kw["temperature"] = 0    # adaptive-thinking models reject an explicit temperature
        tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
        text = ""
        ws_queries: list = []
        # Server-side web search loops internally; pause_turn means it hit the tool-iteration
        # cap — re-send to resume (the API detects the trailing server_tool_use).
        import anthropic as _an, sys as _sys, time as _t
        _TRANSIENT = (_an.OverloadedError, _an.RateLimitError, _an.APITimeoutError,
                      _an.InternalServerError, _an.APIConnectionError)   # 529/429/timeout/5xx/conn
        for _ in range(6):
            for _a in range(6):                       # retry transient API errors with exponential backoff
                try:
                    r = self._c.messages.create(**kw)
                    break
                except _TRANSIENT as _e:
                    if _a == 5:
                        raise
                    _w = min(45, 3 * 2 ** _a)
                    print(f"  llm transient {type(_e).__name__} for {label}; retry {_a + 1}/5 in {_w}s",
                          file=_sys.stderr, flush=True)
                    _t.sleep(_w)
            u = costs.extract(r.usage)
            for k in tally:
                tally[k] += u.get(k, 0)
            text = "".join(b.text for b in r.content if b.type == "text")
            ws_queries += [b.input["query"] for b in r.content
                           if getattr(b, "type", "") == "server_tool_use"
                           and isinstance(getattr(b, "input", None), dict) and b.input.get("query")]
            if r.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": r.content})
                continue
            if r.stop_reason == "refusal":
                raise RuntimeError(f"model refused for {label}")
            break
        costs.record(stage, m, label, tally)
        trace.log("llm", stage=stage, label=label, model=m, system=system, user=user,
                  response=text, web_search_queries=ws_queries, **tally)
        return text


# OpenRouter model ids VERIFIED to accept `reasoning.effort` alongside structured output. Substring
# match. Adding a model here without smoke-testing it re-opens the silent-zero-output failure above.
_REASONING_OK = ("kimi", "deepseek-v4", "gpt-5.6", "glm-5", "minimax-m3", "qwen3", "grok-4")

# OpenRouter models whose providers REJECT an explicit temperature -- the same constraint the Anthropic
# client already handles for its adaptive-thinking models (see GREEDY DECODING above). Sending it anyway
# is not a soft failure: combined with require_parameters:True (set whenever json_schema is used)
# OpenRouter finds NO provider satisfying every parameter and returns 404 "No endpoints found that can
# handle the requested parameters" -- which reads like the model does not exist. Isolated 2026-08-17 on
# gpt-5.6-luna by bisecting the kwargs: dropping temperature ALONE fixed it.
_NO_TEMPERATURE = ("gpt-5.6",)


class OpenRouterClient(LLMClient):
    """Any OpenRouter model via the OpenAI-compatible API. Web search uses OpenRouter's
    `:online` plugin (Exa-backed). Caveat: `:online` has no clean before:<date> control, so
    its look-ahead hygiene is weaker than Anthropic's server search — acceptable for an
    already-upper-bound backtest, but a reason the forward eval stays the clean test."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str):
        from openai import OpenAI
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set — add it to .env (see .env.example).")
        super().__init__(model)
        # Explicit per-request timeout + retries: without these the client uses the SDK default
        # (600s), so a flaky/hung OpenRouter response blocks the whole scan at 0% CPU for minutes.
        # 90s/attempt + 4 retries (exp backoff on timeout/5xx/429) keeps a long scan progressing.
        self._c = OpenAI(base_url=self.BASE_URL, api_key=key, timeout=90.0, max_retries=4)

    def complete(self, system, user, *, use_web_search, label, stage="ladder",
                 json_schema=None, search_query=None, before_date=None, effort="high") -> str:
        # Real, look-ahead-safe web search via Tavily (end_date filter), injected as context —
        # OpenRouter's :online has no date control, so we don't use it.
        if use_web_search and search_query:
            import search as websearch
            ctx = websearch.context(search_query, before_date)
            if ctx:
                user = ctx + "\n\n" + user
        elif use_web_search:
            # A CALLER ASKED FOR SEARCH AND IS NOT GETTING IT. This path has no server-side search,
            # so without a search_query the request silently degrades to the model's parametric
            # memory -- which is how resolve_us_ticker stopped resolving anything on a non-Anthropic
            # scout without one line of evidence anywhere. Say so.
            import sys as _sys
            print(f"    !! {self.model}: use_web_search=True but no search_query -- "
                  f"answering from model memory, NOT the web ({label})", file=_sys.stderr, flush=True)
        kw = {"model": self.model, "max_tokens": 8000,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]}
        if not any(k in self.model for k in _NO_TEMPERATURE):
            kw["temperature"] = 0            # greedy: see AnthropicClient
        if json_schema is not None:  # structured outputs: guarantees parseable JSON (fixes the
            kw["response_format"] = {"type": "json_schema",            # ~27% JSON-format failures)
                                     "json_schema": {"name": "mapping", "strict": True,
                                                     "schema": json_schema}}
            # require_parameters: only route to OpenRouter providers that SUPPORT json_schema, so a
            # rate-limited primary doesn't fall back to one that 400s on structured output (the
            # StreamLake/DeepInfra failure that crashed deepseek BWET mid-scan).
            kw["extra_body"] = {"provider": {"require_parameters": True}}
        # REASONING EFFORT, previously DROPPED on this path (the signature said "Anthropic-only,
        # ignored here"). OpenRouter normalises `reasoning.effort` across the reasoning models, so the
        # knob the profile already carries now reaches them. Wired 2026-08-17 for the low-vs-high arm
        # of the event-agent bake-off: without it "does more thinking beat a bigger model" cannot be
        # asked outside Anthropic.
        #
        # ALLOWLIST, NOT BLANKET -- and this is not caution, it is a measured bug. The first version
        # sent `reasoning` on EVERY OpenRouter call. llama-4-maverick is not a reasoning model, and
        # `reasoning` together with the require_parameters:True set just above leaves OpenRouter with no
        # provider supporting BOTH json_schema and reasoning, so every scout chunk 404'd. The run did
        # not crash: it completed all 37 scans in 10 minutes at $0.00 with zero candidates, which reads
        # exactly like a finished curation. Only send this to models verified to take it.
        if any(k in self.model for k in _REASONING_OK) and effort in ("none", "low", "medium", "high"):
            kw.setdefault("extra_body", {})["reasoning"] = (
                {"enabled": False} if effort == "none" else {"effort": effort})
        # RETRY. This path had NO retry: one bad response killed the caller. A 3-year curation died at
        # scan 18/37 (2026-08-12) when OpenRouter returned a non-JSON body and the SDK's .json()
        # raised JSONDecodeError straight through. The Anthropic path above already backs off; this
        # one now matches it, and also treats a malformed/truncated body as transient -- because from
        # here it is indistinguishable from a 5xx, and the only safe response is to ask again.
        import json as _json, sys as _sys, time as _t
        _r = None
        # 3 attempts, NOT 6: the SDK client above already retries 4x per call at a 90s timeout, so
        # this outer loop multiplies with it. At 6 it could spend ~45 min on a single wedged call and
        # block the whole scan (24 workers, ex.map waits for all). This loop exists only for the
        # errors the SDK does NOT retry -- chiefly a malformed/truncated body -- so it stays short.
        for _a in range(3):
            try:
                _r = self._c.chat.completions.create(**kw)
                break
            except Exception as _e:  # noqa: BLE001 - classified below, re-raised if not transient
                _n = type(_e).__name__
                # CLASSIFY BY STATUS CODE, NOT CLASS NAME. `APIStatusError` is the base class for
                # EVERY http error the SDK raises -- 400, 401, 402, 403 as well as 429 and 5xx -- so
                # matching the string "APIStatus" made an out-of-credits 402 look transient. Measured
                # 2026-08-26: a 78-scan curation retried a 402 `in_flight_budget_exhausted` five
                # times, then died at scan 33 having burned the backoff window for nothing. Retrying
                # a billing error cannot help; it can only delay the failure and waste the run.
                _code = getattr(_e, "status_code", None)
                _PERMANENT = {400, 401, 402, 403, 404, 422}
                if _code in _PERMANENT:
                    if _code in (401, 402, 403):
                        print(f"  llm FATAL http {_code} for {label} -- this is a KEY or BILLING "
                              f"error and will not resolve on retry. Check the balance:\n"
                              f"    curl -s -H \"Authorization: Bearer $OPENROUTER_API_KEY\" "
                              f"https://openrouter.ai/api/v1/credits\n"
                              f"  (a 402 `in_flight_budget_exhausted` can also mean too many "
                              f"concurrent --workers for the remaining balance)",
                              file=_sys.stderr, flush=True)
                    raise
                _transient = isinstance(_e, _json.JSONDecodeError) or any(
                    k in _n for k in ("APIConnection", "APITimeout", "RateLimit", "InternalServer",
                                      "APIStatus", "ReadTimeout", "ConnectError", "RemoteProtocol"))
                if not _transient or _a == 2:
                    raise
                _w = min(45, 3 * 2 ** _a)
                print(f"  llm transient {_n} for {label}; retry {_a + 1}/5 in {_w}s",
                      file=_sys.stderr, flush=True)
                _t.sleep(_w)
        r = _r
        text = r.choices[0].message.content or ""
        u = r.usage
        # Record only the token cost (accurate). OpenRouter's :online web plugin is billed
        # separately (~$4/1k results) and isn't in `usage`; it's small relative to tokens, so
        # we don't fabricate it here — note it when reporting. (Watch input_tokens: if it stays
        # tiny, :online injected little web context and the ladder is reasoning from priors.)
        costs.record(stage, self.model, label, {
            "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(u, "completion_tokens", 0) or 0,
            "cache_read_tokens": 0,
            "web_searches": 0,
        })
        trace.log("llm", stage=stage, label=label, model=self.model, system=system,
                  user=user, response=text, search_query=search_query)
        return text


def make_client(provider: str, model: str | None = None) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient(model or ANTHROPIC_DEFAULT)
    if provider == "openrouter":
        if not model:
            raise RuntimeError("--model is required for openrouter (e.g. deepseek/deepseek-chat-v3.2)")
        return OpenRouterClient(model)
    raise ValueError(f"unknown provider: {provider}")
