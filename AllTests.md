# COVAS++ — Test Suite (AllTests.md)

Work through these top to bottom. Tick each `- [ ]` as it passes; jot anything odd
in the **Notes** line under each section. Most tests are done by voice with the app
running; a few check the web panel or a file on disk.

**Keys:** hold **`[`** to talk · tap **`[`** briefly to cancel · **Ctrl+Alt+Q** to quit.
**Panel:** http://127.0.0.1:8765 (opens automatically on launch).

Legend for the sounds you should hear:
- **voiceinput1–4** — plays the instant you press to talk (random of 4)
- **processing1–3** — plays while working / searching (random of 3)
- **inputok2** — plays right before the spoken answer
- **inputfailed1** — plays on any failure

---

## 0. Setup & launch
- [ ] Double-click **`check_setup.bat`** → every line reads `[ OK ]`, ending in "All systems go."
- [ ] Double-click **`run_covas_ui.bat`** → a console window opens; the browser opens the control panel.
- [ ] Console banner shows your model, voice, Whisper size, Personality ON, and `TALK: hold [`.
- [ ] Panel status light reads **IDLE**.

Notes:

---

## 1. Core voice loop
- [ ] Hold **`[`** → you hear a **voiceinput** sound immediately.
- [ ] While holding, say *"Hello COVAS, can you hear me? Keep it short."* then release.
- [ ] On release you hear a **processing** sound.
- [ ] Panel log + status move through **LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE**.
- [ ] Your transcript appears in the log as **Commander: …**, the reply as **COVAS: …** (timestamped).
- [ ] Just before the voice answer you hear **inputok2**, then the spoken reply.
- [ ] The reply addresses you as **"Commander"** (personality is on).

Notes:

---

## 2. Sound cues
- [ ] Press to talk several times → the **voiceinput** sound **varies** (not always the same one).
- [ ] The **processing** sound also varies across turns.
- [ ] **Failure cue:** press and release **without speaking** (silence) → you hear **inputfailed1** and the log notes "(no speech detected)".
- [ ] No spoken "GalNet"/"looking it up" line ever plays (that was removed — a processing beep covers searches).

Notes:

---

## 3. Cancel (tap `[`) & barge-in
- [ ] Ask a long question (*"Tell me the history of the Elite Dangerous galaxy in detail."*). While it's **thinking or speaking**, **tap `[` briefly** → it stops instantly and returns to **IDLE**.
- [ ] Confirm a **hold** still records normally (a hold is well over the tap threshold).
- [ ] **Barge-in:** while a reply is being spoken, **hold `[`** to talk again → the speech cuts off and a new capture starts.
- [ ] The old **`]`** key does nothing (retired).
- [ ] The panel's **■ CANCEL / STOP** button also stops an in-progress reply.

Notes:

---

## 4. Control panel (live settings)
- [ ] **Status light** tracks state live as you talk.
- [ ] **Live log** scrolls with prompts, replies, and status/search lines (timestamped).
- [ ] Change **Claude model** to `claude-sonnet-5`, ask something → still replies. Set back to Opus.
- [ ] Change **Thinking depth** to **High**, ask a question → replies with **no error** in the log.
- [ ] Toggle **Personality OFF**, ask *"Who am I?"* → reply is plain and does **not** say "Commander". Toggle **ON** → "Commander" returns.
- [ ] Change **ElevenLabs voice** to another voice, ask something → the next reply is in the **new voice**.
- [ ] (Optional) Change **ElevenLabs model** and **Whisper model** — Whisper change logs "Whisper model reloaded".

Notes:

---

## 5. Settings persistence
- [ ] Set model, voice, thinking depth, personality to specific values in the panel.
- [ ] **Quit** (Ctrl+Alt+Q) and relaunch `run_covas_ui.bat`.
- [ ] The panel comes back with the **same settings** (they're saved in `overrides.json`).

Notes:

---

## 6. Web search (automatic)
- [ ] Ask a current-info question: *"What's the latest Elite Dangerous update?"* or *"Give me a recent real-world news headline."*
- [ ] Log shows **"Searching the web for \<query\>"**, the status light hits **SEARCHING** (cyan), and you hear a **processing** beep during the search.
- [ ] The spoken answer reflects **live/current info** (not just memory).
- [ ] **Cancel mid-search:** start another current-info question, then tap `[` while it's searching → it stops.

Notes:

---

## 7. Thinking summary
- [ ] Set **Thinking depth = High**. Ask a genuinely reasoning-heavy question (*"Talk me through the trade-offs of a neutron-highway route versus an economical route for a long trip."*).
- [ ] An amber **[approach] …** summary line appears in the log **before** the answer. *(Simple questions won't trigger it — that's expected.)*

Notes:

---

## 8. Checklist — read & mark (natural language)
- [ ] *"What should I knock out next?"* → speaks your next pending objective **and progress** (e.g. "66 of 807").
- [ ] *"Give me my next three objectives."* → reads a few upcoming items.
- [ ] *"Mark that one done."* → confirms it's checked off. Open `ultimate_checklist.md` and confirm the line is now `- [x]`.
- [ ] *"Actually reopen it."* → confirms it's back to `- [ ]`.
- [ ] *"How many are left?"* / *"How am I doing?"* → gives a progress count.
- [ ] **Disambiguation:** ask to mark something whose wording matches several lines (e.g. *"mark the Colonia one done"*) → it **asks which one** rather than guessing.

Notes:

---

## 9. Checklist — find / add / modify / delete
> Tip: test with a throwaway line (add → modify → delete it) so your real objectives are untouched. Watch `ultimate_checklist.md` change after each step.
- [ ] *"Find the line about jumping the carrier to Wolf 397."* → it locates it (that becomes the **current line**).
- [ ] *"Add a line right after it called 'Reload Carrier with Tritium'."* → new line is inserted **directly after**, with matching indentation/nesting.
- [ ] *"Change the current line to say 'Reload Carrier with 25000 tons of Tritium'."* → the line's text updates (checkbox state preserved).
- [ ] *"Delete the current line."* → the line is removed; the Wolf 397 line stays intact.
- [ ] External edit: hand-edit `ultimate_checklist.md`, save, then ask *"what's next?"* → it reflects your edit (reads the file fresh).

Notes:

---

## 10. Robustness & logging
- [ ] Ask for something likely to contain symbols/arrows/emoji (*"Draw me an ASCII arrow and explain it."*) → it speaks/streams without the console crashing.
- [ ] After a session, open the newest **`logs\session_*.log`** → it contains your prompts and replies with timestamps.
- [ ] (Optional) With **VoiceAttack** running at the same time, confirm COVAS++ still hears you (shared-mic; no conflict).

Notes:

---

## 11. Quit
- [ ] **Ctrl+Alt+Q** (or close the console window) shuts it down cleanly.

Notes:

---

### Summary
- Passed: ___ / (count) Failed: ___
- Anything to revisit:
