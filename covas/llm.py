"""Anthropic Messages API — streaming, with hooks for thinking and web search.

stream_reply() is a generator that yields ("text", chunk) for spoken/printed reply
text, and calls on_event(kind, data) for side-channel status:
    on_event("thinking", text_delta)   # extended-thinking summary text
    on_event("search", query)          # web_search fired with this query
Breaking out of the generator (or the caller setting `cancel`) aborts the HTTP call.
"""
from __future__ import annotations
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:  # only for type hints — keep the offline stack importable without the SDK
    import anthropic


def build_system(cfg: dict) -> str | None:
    """personality.txt as system prompt when personality is ON; else None (neutral)."""
    if cfg["personality"]["enabled"]:
        p = Path(cfg["personality"]["file"])
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


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

# Client-side tools that let Claude read/update the Commander's checklist.
# The list can be large, so these are scoped: fetch just the next items, search,
# or set one item — never dump the whole list.
CHECKLIST_TOOLS = [
    {
        "name": "get_next_objectives",
        "description": (
            "Return the Commander's next pending (unfinished) Elite Dangerous "
            "checklist objectives, in order, with overall progress. Use this for "
            "'what's next' / 'what should I do'. Returns each as '#<number>: <text>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "How many upcoming pending objectives to return (default 1, max 10).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_objectives",
        "description": (
            "Search the checklist for objectives matching a word or phrase. Returns "
            "matches as '#<number> [done|pending] <text>'. Use to locate an item "
            "before marking it, or to disambiguate an unclear request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Word or phrase to search for."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_objective",
        "description": (
            "Mark a checklist objective completed or reopened. Identify it by 'number' "
            "(from get_next_objectives / find_objectives), by 'query' text, or omit "
            "both to use the current line. If a query matches multiple objectives the "
            "tool changes nothing and returns the candidates so you can disambiguate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "description": "The objective's number, if known."},
                "query": {"type": "string", "description": "Text identifying the objective (used if number is omitted)."},
                "completed": {"type": "boolean", "description": "true to check off, false to reopen."},
            },
            "required": ["completed"],
        },
    },
    {
        "name": "add_objective",
        "description": (
            "Add a new (pending) objective to the checklist, positioned relative to an "
            "anchor line. By default it is inserted right after the current line. It "
            "inherits the anchor's indentation so it nests correctly. The new line "
            "becomes the current line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The objective text (without the '- [ ]')."},
                "position": {"type": "string", "enum": ["after", "before"],
                             "description": "Place the new line after or before the anchor. Default 'after'."},
                "anchor_number": {"type": "integer",
                                  "description": "Anchor objective number. Omit to use the current line."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "modify_objective",
        "description": (
            "Replace the text of an existing objective (its checkbox state is kept). "
            "Pass the full new text. Target it by 'number', or omit to modify the "
            "current line. Use for edits like changing a destination or quantity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_text": {"type": "string", "description": "The complete replacement text."},
                "number": {"type": "integer", "description": "Objective number. Omit to modify the current line."},
            },
            "required": ["new_text"],
        },
    },
    {
        "name": "delete_objective",
        "description": (
            "Delete an objective from the checklist. Target it by 'number', or omit to "
            "delete the current line. This is destructive — only do it when the "
            "Commander clearly asks to remove/delete a line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "description": "Objective number. Omit to delete the current line."},
            },
            "required": [],
        },
    },
]


def _build_kwargs(cfg: dict, messages: list[dict]) -> dict:
    a = cfg["anthropic"]
    model = a["model"]
    kwargs: dict = {
        "model": model,
        "max_tokens": int(a["max_tokens"]),
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

    tools: list = []
    # Native web search. We use the basic tool (not the _20260209 dynamic-filtering
    # variant) because it streams the query in a server_tool_use block, letting us
    # surface "Searching the web for <query>" on screen.
    if cfg.get("web_search", {}).get("enabled"):
        tools.append({
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(cfg["web_search"].get("max_uses", 5)),
        })
    # Checklist tools (client-side) when a checklist file is configured.
    if cfg.get("checklist", {}).get("file"):
        tools.extend(CHECKLIST_TOOLS)
    if tools:
        # Cache the tool definitions too. A cache_control breakpoint on the LAST
        # tool caches every tool up to it, so the (verbose, static) checklist +
        # web-search schemas aren't re-sent at full price each turn. Same TTL.
        tools[-1] = {**tools[-1], "cache_control": _cache_control(cfg)}
        kwargs["tools"] = tools
    return kwargs


def stream_reply(
    client: anthropic.Anthropic,
    cfg: dict,
    messages: list[dict],
    cancel: threading.Event,
    on_event: Callable[[str, str], None],
    tool_handler: Callable[[str, dict], str] | None = None,
) -> Iterator[tuple[str, str]]:
    working = list(messages)
    # Loop to handle server-tool continuations (pause_turn) and client-tool calls
    # (tool_use). Each keeps re-sending until Claude produces a final answer.
    for _round in range(8):
        kwargs = _build_kwargs(cfg, working)
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
