# Credits & currencies

> *"I tell you your real balance from the game journal — and if you ask about a currency I've
> never heard of, I say so instead of making a number up."*

Ask COVAS++ how much you're carrying and it answers from your **game journal**, not from the
language model's imagination. The same grounding ethos behind [ship specifications](ship-specs.md)
applies to money: a real number when COVAS has one, an honest "I don't have that yet" when it
doesn't — never an invented amount.

**Example:** *"how many credits do I have?"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"How many credits do I have?"* / *"What's my balance?"* | Your credit balance, from the journal, hedged to when it was read |
| *"How much is on my fleet carrier?"* | Your carrier's balance (if you own one), same hedge |
| *"How many merc coins do I have?"* (a currency COVAS doesn't track) | An honest *"I don't have details on that currency yet"* — and an offer to web-search |

## Why the answers are hedged ("as of login")

Elite Dangerous only writes your credit balance to the journal at **login** (the `LoadGame`
event), and your fleet carrier's balance when it reports carrier stats. COVAS++ reads those and
nothing else — it does **not** try to add up every bounty, trade, and rebuy as you play. So it's
honest about the age of the figure: *"as of login you had 1,204,553 credits."* If you've earned or
spent a lot since you launched, treat it as a starting point, not a live tally. (Summing
intra-session credit changes is a possible future addition, deliberately left out for now so the
number COVAS speaks is always one the game actually reported.)

## The honest-degradation contract (new currencies)

Frontier occasionally adds a whole new currency with a new mechanic — the kind of thing that
didn't exist when the language model was trained. COVAS++ tracks the currencies it **knows** in a
small internal registry (today: **credits** and **fleet carrier balance**). Anything outside that
list is, by design, invisible to the wallet:

- COVAS++ never reports a balance for a currency it doesn't track — there's simply no number to
  invent.
- A built-in prompt rule tells the companion that currencies come **only** from this wallet, so
  when you ask about an untracked one it says plainly it doesn't have data on that currency yet
  (its game knowledge may predate it) and offers to **web-search** instead of guessing.

That's the same promise as the rest of COVAS++'s Elite data: **current the moment the community is,
and honest about everything else.** Competitors that lean on the model's training memory will
happily quote a balance for a currency that didn't exist at training time; COVAS++ won't.

### Adding a currency later is a data change, not a code change

When a new currency becomes well-understood, teaching COVAS++ to ground it is a **single row** in
the currency registry (`covas/ed/currencies.py`): the journal event and field its balance lives in,
a display name, and the phrases players use to ask about it. No new journal handler, no new context
field, no detector edit. That row instantly makes the balance appear in your status readout and the
`ship_status` reply, and adds its question phrases to what triggers a live lookup.

A more aggressive option — a journal **heuristic** that notices an *unrecognised* event carrying a
balance-like number and repeats it back verbatim ("the journal shows a MercCoins balance of 250 —
that's new to me") — was **designed but deliberately not built** for now. The catch is that plenty
of non-currency journal fields are integers (cargo count, for one), so a naive sniffer would misfire
immediately. The prompt rule above already meets the "honest, not invented" bar, so the heuristic
waits until a real new currency proves it's worth the risk.

## Keeping the data fresh

The wallet needs no dataset — it reads your own journal live. The broader "how COVAS++ keeps up with
new Frontier content over time" story (ships, modules, and the release-time data refresh) is covered
in [Keeping data fresh](../data-refresh.md).

## Requirements

Balances need **game-state monitoring** on (`[elite].enabled = true`) and Elite Dangerous to have
been launched this session, so the journal exists to read. Out of the game, COVAS++ has no balance
to report and will say so.
