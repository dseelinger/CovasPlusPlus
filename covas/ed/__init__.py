"""Elite Dangerous game-state monitoring (DESIGN §5).

ED continuously writes game state to disk — the same source EDMC/EDDN read, no memory
reading or API keys. Two watcher threads tail those files and publish semantic events on
the EventBus, and keep a rolling `EDContext` the companion can reference:

    JournalWatcher  -> tails the newest Journal.*.log (NDJSON, one event per line)
    StatusWatcher   -> polls Status.json, decodes the Flags bitfield into transitions
    EDContext       -> rolling "what's happening now" (system, station, ship, fuel, cargo)

The watchers publish events ONLY and never touch the voice loop; capabilities decide what
to do with them (see covas/capabilities/ed_context_capability.py). Proactive speech is a
later phase — nothing here initiates a reply.
"""
from .context import EDContext
from .detector import ContextDetector, ContextRef
from .journal import (JournalWatcher, apply_journal_event, default_journal_dir,
                      describe_journal_event, parse_journal_line, resolve_journal_dir)
from .status import (StatusWatcher, apply_status, decode_flags, describe_transition,
                     flag_transitions, status_path)

__all__ = [
    "ContextDetector",
    "ContextRef",
    "EDContext",
    "JournalWatcher",
    "StatusWatcher",
    "apply_journal_event",
    "apply_status",
    "decode_flags",
    "default_journal_dir",
    "describe_journal_event",
    "describe_transition",
    "flag_transitions",
    "parse_journal_line",
    "resolve_journal_dir",
    "status_path",
]
