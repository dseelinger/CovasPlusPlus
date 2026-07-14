# COVAS++ — Audio / Comms / Chatter content checklist

What to add to make the audio subsystem actually *heard*. The code (C1–C11) provides the
machinery; this is the content it plays. **Run order:** C9 (wiring) → C10 (voice cast) →
C11 (drop-in loader) → then add the content below. C11 creates the folder skeleton and a
README in each folder; until it lands, dropping files won't auto-register.

Formats: **wav / flac / ogg are safe**; mp3 works only if your libsndfile build supports it —
prefer wav/ogg. All content folders below are git-ignored (supply your own, with your own
rights).

Legend: 📁 = folder name must match exactly (drop any-named files inside) · 📄 = file name must
match exactly.

---

## 1. SFX samples  →  `audio/sfx/<cue>/`   (drop any files; multiple = rotation)

- [ ] 📁 `audio/sfx/thargoid_voices/` — eerie Thargoid vocalizations (ambient bus)
- [ ] 📁 `audio/sfx/space_radiation/` — radiation / static bed (ambient bus)
- [ ] 📁 `audio/sfx/hyperspace_weirdness/` — hyperspace-transition weirdness (ambient bus)
- [ ] 📁 `audio/sfx/interdiction_sting/` — the interdiction warning sting (alert bus)

*(Pre-C11 only: the sting is looked for at the exact path `sounds/interdiction_sting.wav`;
C11 migrates it to the folder above.)*

## 2. Music tracks  →  `audio/music/<context>/`   (drop any track files)

- [ ] 📁 `audio/music/deep_space/`
- [ ] 📁 `audio/music/populated/`
- [ ] 📁 `audio/music/unpopulated/`
- [ ] 📁 `audio/music/nebula/`
- [ ] 📁 `audio/music/near_star/`
- [ ] 📁 `audio/music/combat_adjacent/`
- [ ] 📁 `audio/music/scooping_fuel/`
- [ ] 📁 `audio/music/default/` — fallback when no context matches

## 3. Line pools  →  text files, EXACT names (one spoken line per row, `#` lines ignored)

Keep these **fact-free flavor** — nothing checkable. They are template lines, not AI-generated,
so they must never assert a game fact (system name, price, count, etc.).

- [ ] 📄 `content/chatter/station_traffic.txt` — busy-system / station traffic chatter (populated only)
- [ ] 📄 `content/chatter/system_patrol.txt` — local security / patrol chatter (populated only)
- [ ] 📄 `content/chatter/market_buzz.txt` — trade / hauler chatter (populated only)
- [ ] 📄 `content/chatter/populated_musing.txt` — "nice to have company" flavor (populated only)
- [ ] 📄 `content/interdiction_threat.txt` — the assistant's threat-assessment lines

## 4. Voices (for the C10 voice cast)

- [ ] Install a handful of **Piper voice models** (each is a `.onnx` + matching `.onnx.json`).
      These become the NPC / comms / chatter cast pool. List them in `[audio.voices]`.
- [ ] Set your **ElevenLabs persona voice** (COVAS) in config.
- [ ] Set the **fixed male voice** used for real-player direct messages.

---

## Notes

- Missing folder or empty file = that cue is simply **silent** — no error (fail-closed-silent).
  So you can add content incrementally and only what's present plays.
- After C11, run the app and check the **content-status** report — it lists, per cue/context,
  how many files/lines were found and what's still empty.
- Exact-name summary: the SFX **folder** names and music **context** names must match (files
  inside are free-named); the chatter/threat **`.txt` file** names must match. Everything else
  is drop-any-name.
