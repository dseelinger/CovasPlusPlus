Fix: <one-line symptom>
Branch: fix/<short-name>

Repro: <when/how it happens in-game>
Observed: <paste the traceback / session-log excerpt>
Expected: <what should happen>
Evidence: <the real data that triggers it — journal line, .binds snippet, API response, config>

Do this:
1. Write a FAILING unit test reproducing the bug from the evidence (fixture; no network).
2. Fix the smallest thing that makes it pass, without breaking other tests.
3. Bare `pytest` green and offline. List anything I must retest manually in-game.
Stop for my review.