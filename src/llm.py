"""llm.py — provider-agnostic LLM seam for the curator (Anthropic + OpenRouter).

The curator's laddering (map_event) and forward logging call an LLM that must reason over a
prompt, optionally web-search under look-ahead discipline, and return text. This hides the
provider so the SAME pipeline runs on Anthropic (Opus/Haiku) or any OpenRouter model
(DeepSeek, Qwen, Llama, ...) — the seam for the cheap-model bake-off (TODO + the diplomacy-A2A
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

ANTHROPIC_DEFAULT = "claude-opus-4-8"
# Adaptive thinking + effort + dynamic-filtering web search work on these; cheaper Anthropic
# models (Haiku) reject them and use the basic web-search variant.
_ADVANCED = ("opus-4", "sonnet-4-6", "fable", "mythos")


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
                 search_query: str | None = None, before_date: str | None = None) -> str: ...


class AnthropicClient(LLMClient):
    """Anthropic Messages API with server-side web search (look-ahead via before:<date> in
    the model's queries) and adaptive thinking on the advanced models."""

    def __init__(self, model: str = ANTHROPIC_DEFAULT):
        import anthropic
        super().__init__(model)
        self._c = anthropic.Anthropic()

    def complete(self, system, user, *, use_web_search, label, stage="ladder",
                 json_schema=None, search_query=None, before_date=None) -> str:
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
        if _supports_advanced(m):  # Haiku rejects effort + adaptive thinking
            kw["thinking"] = {"type": "adaptive"}
            kw["output_config"] = {"effort": "high"}
        tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
        text = ""
        # Server-side web search loops internally; pause_turn means it hit the tool-iteration
        # cap — re-send to resume (the API detects the trailing server_tool_use).
        for _ in range(6):
            r = self._c.messages.create(**kw)
            u = costs.extract(r.usage)
            for k in tally:
                tally[k] += u.get(k, 0)
            text = "".join(b.text for b in r.content if b.type == "text")
            if r.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": r.content})
                continue
            if r.stop_reason == "refusal":
                raise RuntimeError(f"model refused for {label}")
            break
        costs.record(stage, m, label, tally)
        return text


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
        self._c = OpenAI(base_url=self.BASE_URL, api_key=key)

    def complete(self, system, user, *, use_web_search, label, stage="ladder",
                 json_schema=None, search_query=None, before_date=None) -> str:
        # Real, look-ahead-safe web search via Tavily (end_date filter), injected as context —
        # OpenRouter's :online has no date control, so we don't use it.
        if use_web_search and search_query:
            import search as websearch
            ctx = websearch.context(search_query, before_date)
            if ctx:
                user = ctx + "\n\n" + user
        kw = {"model": self.model, "max_tokens": 8000,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]}
        if json_schema is not None:  # structured outputs: guarantees parseable JSON (fixes the
            kw["response_format"] = {"type": "json_schema",            # ~27% JSON-format failures)
                                     "json_schema": {"name": "mapping", "strict": True,
                                                     "schema": json_schema}}
        r = self._c.chat.completions.create(**kw)
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
        return text


def make_client(provider: str, model: str | None = None) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient(model or ANTHROPIC_DEFAULT)
    if provider == "openrouter":
        if not model:
            raise RuntimeError("--model is required for openrouter (e.g. deepseek/deepseek-chat-v3.2)")
        return OpenRouterClient(model)
    raise ValueError(f"unknown provider: {provider}")
