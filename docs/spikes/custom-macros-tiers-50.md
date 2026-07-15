# Spike #50 (follow-up) — custom-macro Tier-2 & Tier-3 conditions

**Status: follow-up spikes captured while shipping issue #50 Tier-1. NOT built.**
**Tier-1 (voice/UI-authored macros of allowlisted digital actions, with waits, status gates,
and journal/Status triggers) is shipped. The two harder tiers below are deferred, with the
reason each is out of reach today.**

---

## What shipped (Tier-1)

The Commander can author a named macro conversationally or in the control panel: an ordered list
of **allowlisted** ship actions, fixed **waits**, and **status gates** (`require_status` /
`await_status`), optionally bound to a folded journal/Status **trigger** (`docking_granted`,
`docked`, `supercruise_exit`, `arrival`, `low_fuel`, `overheating`, …). It's persisted, invoked
by name or fired by its trigger, and validated against the action/trigger registry so it can
never reference an action outside `[keybinds].allowlist` or a trigger the app doesn't fold. See
[Custom macros](../automation/custom-macros.md).

## Tier-2 (deferred) — continuous-distance conditions

**Example the Commander wants:** *"when we're within 7.5 km of the station, throttle to zero."*

**Why it's not buildable on telemetry alone.** Elite Dangerous does **not** stream a live
distance-to-target. `Status.json` carries flags, fuel, cargo, and (planet-side) lat/long/altitude,
but no continuous range to an arbitrary target (station, ship, nav beacon). The journal events a
supercruise approach emits (`SupercruiseDestinationDrop`, `Docked`, etc.) are **discrete moments**,
not a stream you can threshold at 7.5 km. So there is nothing to compare against a distance
condition between steps.

**Possible future paths (each its own spike):**

- **Vision-LLM read (see [spike #55](vision-spike-55.md)).** A screenshot read can extract the
  on-screen distance-to-target on demand — but only on demand: a polling loop to *watch* for
  "within 7.5 km" costs ~\$5–\$50/hour and adds seconds of latency per frame, which is exactly the
  real-time closed loop that spike recommends **against**. Not a fit for a live threshold.
- **Supercruise Assist proxy.** If the Commander has Supercruise Assist, ED auto-drops at ~the
  right range; a macro could react to the *drop* (a discrete event) rather than the distance. That's
  really just a Tier-1 trigger and needs no Tier-2 work — worth documenting as the pragmatic
  substitute.

**Recommendation:** keep Tier-2 out of the macro model. If distance conditions are ever wanted,
build them on the on-demand vision path, explicitly **not** as a polling watcher.

## Tier-3 (deferred) — analog / spatial actions

**Example the Commander wants:** *"boost toward the station", "line up on the pad."*

**Why it's not buildable.** These need **visual aiming / closed-loop flight control** — knowing
where a thing is on screen and steering toward it. COVAS++ deliberately **never flies the ship**
(DESIGN §6): it presses discrete, allowlisted controls behind a safety layer. Spatial aiming is a
different, much riskier capability class (continuous analog output, no clean abort semantics) and
is out of scope for the macro model.

**Recommendation:** NO-GO for the foreseeable future. Analog/aiming automation would need its own
design, its own safety story, and almost certainly the vision path — it is not an extension of
authored digital macros.

## Suggested tracking issues

- *"Spike: distance-conditioned macro steps via on-demand vision (Tier-2 of #50)"* — depends on
  #55; explicitly on-demand, no polling watcher.
- *"Spike: analog/aiming automation feasibility (Tier-3 of #50)"* — separate safety design; likely
  NO-GO. Do not fold into the digital-macro model.
