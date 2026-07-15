"""Anthropic Messages API — streaming, with hooks for thinking and web search.

stream_reply() is a generator that yields ("text", chunk) for spoken/printed reply
text, and calls on_event(kind, data) for side-channel status:
    on_event("thinking", text_delta)   # extended-thinking summary text
    on_event("search", query)          # web_search fired with this query
Breaking out of the generator (or the caller setting `cancel`) aborts the HTTP call.
"""
from __future__ import annotations
import threading
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:  # only for type hints — keep the offline stack importable without the SDK
    import anthropic


def build_system(cfg: dict) -> str | None:
    """The composed system prompt when personality is ON; else None (neutral). Composition
    (Base + selected Persona + Campaign) lives in `personality.compose_system` (N7).

    When CREW voicing is on ([crew].enabled, issue #69) a STATIC instruction is appended so the
    model may voice named crew via a `[Name]` line prefix. It's a constant for a given config, so
    it rides the cached prefix and never busts the prompt cache turn-to-turn (only the once when
    the setting/roster changes). It applies even with personality OFF — otherwise there'd be no
    system prompt to carry it — and stays static in either case."""
    from .crew import system_instruction
    from .personality import compose_system

    system = compose_system(cfg)
    crew = system_instruction(cfg)
    if not crew:
        return system
    return f"{system}\n\n{crew}" if system else crew


def _cache_control(cfg: dict) -> dict:
    """cache_control breakpoint for the static prefix (system + tools). The TTL comes
    from [anthropic].cache_ttl: "1h" adds the extended-TTL flag so the cache survives
    the long gaps between in-game voice turns; "5m" (or blank) is the API default."""
    ttl = str(cfg.get("anthropic", {}).get("cache_ttl", "1h")).strip()
    cc: dict = {"type": "ephemeral"}
    if ttl and ttl not in ("5m", "default"):
        cc["ttl"] = ttl
    return cc


def _rates_for(model: str, pricing: dict) -> dict | None:
    """Look up per-Mtok rates for `model` in the [pricing] table: exact id first, then
    a prefix match so a bare 'claude-haiku-4-5' entry covers date-suffixed ids."""
    rates = pricing.get(model)
    if isinstance(rates, dict):
        return rates
    for key, val in pricing.items():
        if isinstance(val, dict) and model.startswith(key):
            return val
    return None


def estimate_cost(model: str, usage: dict, pricing: dict) -> float:
    """Rough USD estimate for one API call from its token counts and the [pricing]
    table. Unknown models (no matching rate) estimate as 0.0."""
    rates = _rates_for(model, pricing)
    if not rates:
        return 0.0
    dollars = (
        usage.get("input_tokens", 0) * float(rates.get("input", 0.0))
        + usage.get("output_tokens", 0) * float(rates.get("output", 0.0))
        + usage.get("cache_creation_input_tokens", 0) * float(rates.get("cache_write", 0.0))
        + usage.get("cache_read_input_tokens", 0) * float(rates.get("cache_read", 0.0))
    )
    return dollars / 1_000_000.0


def usage_event(cfg: dict, model: str, usage) -> dict:
    """Normalize an Anthropic response `usage` object into a plain dict (token counts
    + estimated cost) suitable for logging and publishing on the EventBus."""
    def g(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    ev = {
        "model": model,
        "input_tokens": g("input_tokens"),
        "output_tokens": g("output_tokens"),
        "cache_creation_input_tokens": g("cache_creation_input_tokens"),
        "cache_read_input_tokens": g("cache_read_input_tokens"),
    }
    ev["cost_usd"] = estimate_cost(model, ev, cfg.get("pricing", {}))
    return ev


# Current-gen models use the effort parameter for thinking depth;
# older models (e.g. Haiku 4.5) still use a token budget.
EFFORT_MODELS = {
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
    "claude-sonnet-5", "claude-sonnet-4-6",
}


def _build_kwargs(cfg: dict, messages: list[dict],
                  tools: list[dict] | None = None,
                  model: str | None = None,
                  max_tokens: int | None = None) -> dict:
    a = cfg["anthropic"]
    # The router picks the model + cap per turn (DESIGN §4); fall back to the
    # [anthropic] defaults when it doesn't (routing off, or a non-router caller).
    model = model or a["model"]
    kwargs: dict = {
        "model": model,
        "max_tokens": int(max_tokens if max_tokens is not None else a["max_tokens"]),
        "messages": messages,
    }
    system = build_system(cfg)
    if system:
        # Send the (static) personality system prompt as a cacheable block. The
        # cache_control breakpoint lets Anthropic reuse it across turns for ~90% off
        # the input price instead of re-billing ~5.8KB every call. TTL from config.
        kwargs["system"] = [{
            "type": "text",
            "text": system,
            "cache_control": _cache_control(cfg),
        }]

    # Thinking depth. Newer models: adaptive thinking + effort. Older: budget_tokens.
    think = a.get("thinking", {})
    depth = think.get("default", "Off")
    if depth == "Off":
        kwargs["thinking"] = {"type": "disabled"}
    elif model in EFFORT_MODELS:
        effort = think.get("effort", {}).get(depth)
        if effort:
            # display "summarized" so we can surface a thinking summary (Phase 4)
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {"effort": effort}
            kwargs["max_tokens"] = max(kwargs["max_tokens"], 8192)
    else:
        budget = int(think.get("budget", {}).get(depth, 0))
        if budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["max_tokens"] = max(kwargs["max_tokens"], budget + 1024)

    tool_list: list = []
    # Native web search. We use the basic tool (not the _20260209 dynamic-filtering
    # variant) because it streams the query in a server_tool_use block, letting us
    # surface "Searching the web for <query>" on screen.
    if cfg.get("web_search", {}).get("enabled"):
        tool_list.append({
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(cfg["web_search"].get("max_uses", 5)),
        })
    # Client-side tools come from the capability registry (checklist, etc.); the
    # caller passes them in rather than this module hardcoding them.
    if tools:
        tool_list.extend(tools)
    if tool_list:
        # Cache the tool definitions too. A cache_control breakpoint on the LAST
        # tool caches every tool up to it, so the (verbose, static) checklist +
        # web-search schemas aren't re-sent at full price each turn. Same TTL.
        tool_list[-1] = {**tool_list[-1], "cache_control": _cache_control(cfg)}
        kwargs["tools"] = tool_list
    return kwargs


def stream_reply(
    client: anthropic.Anthropic,
    cfg: dict,
    messages: list[dict],
    cancel: threading.Event,
    on_event: Callable[[str, str], None],
    tool_handler: Callable[[str, dict], str] | None = None,
    tools: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Iterator[tuple[str, str]]:
    # Belt-and-braces: never send a message with empty content — the API rejects it
    # ("messages.N: … must have non-empty content") and 400s the whole turn. App now builds
    # history transactionally so an orphaned/empty turn shouldn't reach here, but a stray one
    # must degrade to a dropped message, not a hard failure. Content is a str (text) or a list of
    # blocks (tool results); both are falsy when empty.
    working = [m for m in messages if m.get("content")]
    # Loop to handle server-tool continuations (pause_turn) and client-tool calls
    # (tool_use). Each keeps re-sending until Claude produces a final answer.
    for _round in range(8):
        kwargs = _build_kwargs(cfg, working, tools, model=model, max_tokens=max_tokens)
        tool_json = ""
        final = None
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if cancel.is_set():
                    return
                etype = getattr(event, "type", "")
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", "") == "server_tool_use" and \
                            getattr(block, "name", "") == "web_search":
                        tool_json = ""
                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta":
                        yield ("text", delta.text)
                    elif dtype == "thinking_delta":
                        on_event("thinking", delta.thinking)
                    elif dtype == "input_json_delta":
                        tool_json += getattr(delta, "partial_json", "")
                        q = _extract_query(tool_json)
                        if q:
                            on_event("search", q)
            final = stream.get_final_message()
        if cancel.is_set():
            return

        # Report token usage + a rough cost for this API call (one per round, so a
        # tool-loop turn logs each round). The app logs it and puts it on the bus.
        if final is not None and getattr(final, "usage", None) is not None:
            on_event("usage", usage_event(cfg, kwargs["model"], final.usage))

        stop = final.stop_reason if final else None
        if stop == "pause_turn":  # server tool needs another round
            working = working + [{"role": "assistant", "content": final.content}]
            continue
        if stop == "tool_use" and tool_handler is not None:  # client tool call(s)
            results = []
            for block in final.content:
                if getattr(block, "type", "") == "tool_use":
                    on_event("tool", block.name)
                    try:
                        out = tool_handler(block.name, dict(block.input or {}))
                    except Exception as e:  # noqa: BLE001
                        out = f"Tool error: {e}"
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": out,
                    })
            working = working + [
                {"role": "assistant", "content": final.content},
                {"role": "user", "content": results},
            ]
            continue
        return


def _extract_query(partial_json: str) -> str:
    """Best-effort pull of the "query" value out of partial streamed JSON."""
    key = '"query"'
    i = partial_json.find(key)
    if i == -1:
        return ""
    j = partial_json.find(":", i)
    if j == -1:
        return ""
    k = partial_json.find('"', j)
    if k == -1:
        return ""
    end = partial_json.find('"', k + 1)
    if end == -1:
        return ""
    return partial_json[k + 1:end]
