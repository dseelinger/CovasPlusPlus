"""Manual check for MANUAL_TESTS.md 4.3 transient-outage cases (issue #97): a configurable
OpenAI-compatible LLM endpoint that can FAIL a couple of times and/or respond SLOWLY, then succeed.

    .venv\\Scripts\\python.exe scripts\\flaky_llm_stub.py                       # retry-then-recover
    $env:STUB_FAIL_TIMES=0; $env:STUB_DELAY=8; .venv\\Scripts\\python.exe scripts\\flaky_llm_stub.py  # slow

Serves POST /v1/chat/completions. Per turn it returns 503 (Overloaded, Retry-After: 1) the first
STUB_FAIL_TIMES times, then — after an optional STUB_DELAY-second pause (simulating a slow/hung
provider) — streams a valid SSE completion, RESETTING after each success so every turn behaves the
same (no restart between turns). Two env knobs pick the case:
    STUB_FAIL_TIMES  (default 2)   failures before a success  -> "Retry then recover"
    STUB_DELAY       (default 0)   seconds before the reply streams -> "Slow heads-up (watchdog)"
    STUB_STATUS      (default 503) the failure status code. A retryable 5xx/429 exercises retry +
                                   "exhausted -> degraded"; a 404/401 exercises "Fail-fast" (the
                                   provider must NOT retry it — the turn fails immediately).
Cases: retry-recover = FAIL_TIMES 2, STATUS 503; exhausted->degraded = FAIL_TIMES 999, STATUS 503;
slow = FAIL_TIMES 0, DELAY 8; fail-fast = FAIL_TIMES 999, STATUS 404 (or 401).

Point COVAS at it (Settings page or overrides.json), then speak/type a turn:
    [llm].provider     = "openai"
    [openai].base_url  = "http://127.0.0.1:8799/v1"
The Authorization key and model id are ignored, so any throwaway key/model works (a hand-dropped
plaintext OpenAIAPIKey.txt is accepted and auto-migrated). No API calls, no ED, no cost.

Expect (retry-recover): two 503s here, COVAS pauses ~2 s then speaks the reply, and the COVAS log
shows two `retry: OpenAI HTTP 503 - retry N/4, backing off ...s` lines. No user-visible error.
Expect (slow, STUB_DELAY=8 + [llm].slow_warning_seconds=5): after ~5 s COVAS SPEAKS the "being slow,
still trying" line, then delivers the real reply at ~8 s.
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8799
FAIL_TIMES = int(os.environ.get("STUB_FAIL_TIMES", "2"))   # failures per turn before one succeeds
DELAY = float(os.environ.get("STUB_DELAY", "0"))           # seconds before the reply streams (slow test)
STATUS = int(os.environ.get("STUB_STATUS", "503"))         # failure status: 5xx/429 retryable, 4xx not
REPLY = "Systems nominal, Commander. The retry-recovery test came through clean."

_RETRYABLE = {429, 500, 502, 503, 529}  # mirrors covas/providers/_retry.RETRYABLE_STATUS
_fails = {"n": 0}  # consecutive failures served for the current (in-flight) turn


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence the default per-request noise; we print our own
        pass

    def do_GET(self):  # /v1/models and friends (catalog probes) -> harmless empty list, uncounted
        self._json(200, {"data": []})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)  # drain + discard the request body

        if _fails["n"] < FAIL_TIMES:
            _fails["n"] += 1
            kind = "retryable" if STATUS in _RETRYABLE else "fail-fast (non-retryable)"
            print(f"[stub] chat/completions -> {STATUS} {kind} (failure {_fails['n']}/{FAIL_TIMES})")
            self.send_response(STATUS)
            if STATUS in _RETRYABLE:
                self.send_header("Retry-After", "1")   # only meaningful for a retryable status
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": {"message": f"stub {STATUS}", "type": "server_error"}}).encode())
            return

        _fails["n"] = 0  # success re-arms the next turn
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if DELAY > 0:  # simulate a slow/hung provider so the latency watchdog fires
            print(f"[stub] chat/completions -> 200, holding {DELAY:.0f}s before streaming (slow)")
            time.sleep(DELAY)
        else:
            print("[stub] chat/completions -> 200, streaming the reply")
        for word in REPLY.split():
            chunk = {"choices": [{"index": 0, "delta": {"content": word + " "}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.02)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"flaky LLM stub on http://127.0.0.1:{PORT}/v1  "
          f"(each turn: {FAIL_TIMES}x {STATUS} then "
          f"{'a ' + str(int(DELAY)) + 's-slow' if DELAY > 0 else 'a'} streamed reply)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
