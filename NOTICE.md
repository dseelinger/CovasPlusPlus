# NOTICE — Third-party attributions & bill-of-materials

COVAS++ itself is licensed under the [MIT License](LICENSE) and covers **the COVAS++ source code
only**. The application depends on, and the packaged Windows installer **redistributes**, a number
of third-party components — Python libraries, the native libraries they bundle, and community game
data. This file is the bill-of-materials for those components, with each one's license and its
redistribution status.

> **Why this exists.** *Using* a dependency and *redistributing* it are different acts with
> different obligations. Because COVAS++ ships a frozen (PyInstaller) build that includes copies of
> these components, we document what's inside and confirm each is compatible with redistribution
> under our MIT license — including the handful that carry weak-copyleft (LGPL) or attribution
> obligations, called out explicitly below.

Licenses were confirmed from each project's PyPI metadata / `LICENSE` file, and — for the native
DLLs bundled inside wheels — from the **actually installed wheel's** contents, not just upstream's
latest source. Versions below reflect the pinned/resolved dependency set at the time of writing;
re-verify against `requirements.txt` and the installed wheels when they change. (Historical note:
COVAS++ once pulled FFmpeg/PyAV transitively via faster-whisper, which bundled GPL x264/x265 codec
DLLs; issue #206 removed that stack entirely by moving STT to whisper.cpp, so the installer is now
permissive by construction.)

---

## 1. What is *not* bundled (user-supplied at runtime)

To set the record straight, since the models are the usual worry: COVAS++ does **not** redistribute
any speech models or voices.

- **Whisper STT models** (whisper.cpp `ggml-*.bin`) are downloaded on first use by `pywhispercpp`
  from Hugging Face onto the user's machine. They are not committed to this repo and not included in
  the installer.
- **Piper TTS voices** (`.onnx`) are downloaded by the user (`python -m piper.download_voices …`).
  Not committed, not bundled.
- **Sound cues** in `sounds/` and any user-added voice models are the user's own assets
  (git-ignored). The default cues under `covas/assets/cues/` are project-original.

If you add third-party voices or sound assets locally, you are responsible for their licenses (see
the note at the bottom of [`LICENSE`](LICENSE)).

---

## 2. Python libraries (bundled in the installer)

The frozen build includes these libraries and their bundled native code. Unless noted in
[§4](#4-components-with-copyleft-or-attribution-obligations), all are permissive (MIT / BSD /
Apache-2.0 / MIT-CMU/HPND) and are redistributed with their notices retained — fully compatible
with our MIT license.

| Component | License | Redistribution note |
|-----------|---------|---------------------|
| [anthropic](https://pypi.org/project/anthropic/) | MIT | Permissive. |
| [pywhispercpp](https://pypi.org/project/pywhispercpp/) | MIT | Permissive; **bundles whisper.cpp + ggml native DLLs** — both MIT, no FFmpeg/GPL (issue #206). |
| [numpy](https://pypi.org/project/numpy/) | BSD-3-Clause | Permissive. |
| [sounddevice](https://pypi.org/project/sounddevice/) | MIT | Permissive; wraps PortAudio (MIT-style). |
| [soundfile](https://pypi.org/project/soundfile/) | BSD-3-Clause | Wrapper is permissive; **bundles libsndfile — see §4.** |
| [pillow](https://pypi.org/project/pillow/) | MIT-CMU (HPND) | Permissive, MIT-family. |
| [keyboard](https://pypi.org/project/keyboard/) | MIT | Permissive. |
| [requests](https://pypi.org/project/requests/) | Apache-2.0 | Permissive; retain NOTICE/attribution. |
| [aiohttp](https://pypi.org/project/aiohttp/) | Apache-2.0 AND MIT | Permissive; retain both notices. |
| [flask](https://pypi.org/project/Flask/) | BSD-3-Clause | Permissive. |
| [flask-sock](https://pypi.org/project/flask-sock/) | MIT | Permissive. |
| [pywebview](https://pypi.org/project/pywebview/) | BSD-3-Clause | Permissive. |
| [openvr (pyopenvr)](https://pypi.org/project/openvr/) | BSD-3-Clause | Permissive; **bundles `openvr_api.dll` — see §4.** |
| [edge-tts](https://pypi.org/project/edge-tts/) | **LGPL-3.0** | **Weak copyleft — see §4.** |

Development- and build-only tools (`pytest`, `ruff`, `mkdocs`, `mkdocs-material`, `pyinstaller`)
are **not** redistributed to end users and are listed here only for completeness — all are
permissive (MIT / BSD / Apache-2.0).

---

## 3. Bundled native libraries (via the wheels above)

- **Valve OpenVR SDK** — `openvr_api.dll`, bundled by the `openvr` wheel. **BSD-3-Clause**,
  Copyright © Valve Corporation. Permissive; notice retained.
- **libsndfile** — bundled by the `soundfile` wheel. **LGPL-2.1-or-later** — see §4.
- **whisper.cpp + ggml** — the STT inference DLLs bundled by the `pywhispercpp` wheel. **MIT**,
  Copyright © Georgi Gerganov & contributors. Permissive; notice retained. (Reads float32 PCM
  directly — no FFmpeg, so none of the old PyAV/GPL-codec exposure; issue #206.)

---

## 4. Components with copyleft or attribution obligations

These are redistributable inside an MIT-licensed application, but they carry obligations beyond
"keep the notice." Each is called out honestly here.

### edge-tts — LGPL-3.0 (weak copyleft)

edge-tts is the **default** free TTS provider and is bundled in the installer. Its repository
LICENSE places all files under **LGPLv3** except `src/edge_tts/srt_composer.py` (MIT). LGPL
obligations attach: the LGPL-3.0 license text and attribution must ship with any redistribution,
and users must be able to modify or replace the LGPL component. Because edge-tts is **pure Python**
(imported, not statically linked) and its `.py` sources are present and replaceable in the frozen
bundle, this is straightforward to satisfy. *Compatibility: OK to redistribute under MIT, with the
LGPL notice + replaceability preserved.*

### libsndfile (via soundfile) — LGPL-2.1-or-later (weak copyleft)

The `soundfile` wheel bundles the native `libsndfile` DLL, which is **LGPL-2.1-or-later**. It is a
separate dynamically-linked library, so satisfying the LGPL means shipping its license/notice and
preserving the ability to relink/replace the DLL. *Compatibility: OK to redistribute under MIT,
with the LGPL notice preserved.*

### Apache-2.0 components

`requests` and `aiohttp` are Apache-2.0; their attribution/NOTICE content is retained as part of
redistributing the wheels.

---

## 5. Community game data (Elite Dangerous reference data)

COVAS++ bundles small JSON tables (ship specs, module ids, engineering blueprints/materials, ship
roster) so it can answer offline. These are **derived from Frontier Developments' Elite Dangerous**
via community data projects, and are redistributed here under Frontier's fan-content context — they
are **not** claimed under COVAS++'s MIT license.

| Data | Source project | Note |
|------|----------------|------|
| Ship specifications; engineering blueprints/materials | [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) | The project's MIT license covers its **code only**; its `LICENSE.md` states the JSON **data** is "intellectual property and copyright of Frontier Developments plc." Redistributed as FDev-derived fan content, with attribution to EDCD / Coriolis. |
| Outfitting/module ids; material ids | [EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) | Community-collected from Frontier's Companion API. The repository publishes **no explicit license**; treated as FDev-derived reference data under fan-content, with attribution to EDCD/FDevIDs. |
| Ship roster (names + FDev symbols) | [Spansh](https://spansh.co.uk/) | Aggregated from EDDN / community submissions (ultimately FDev journal/CAPI data). No published data license located; attributed to Spansh as a community courtesy. |

Each dataset's provenance and refresh date are recorded in
[`covas/nav/data/datasets_manifest.json`](covas/nav/data/datasets_manifest.json) and surfaced in
the app ("how up to date is your ship data?"). See [docs/data-refresh.md](docs/data-refresh.md).

**Attribution & thanks:** COVAS++ gratefully depends on the work of the
[Elite Dangerous Community Developers (EDCD)](https://github.com/EDCD), the
[Coriolis](https://coriolis.io/) project, and [Spansh](https://spansh.co.uk/). Thank you.

---

## 6. Elite Dangerous — trademark & fan-content

Elite Dangerous is a trademark of **Frontier Developments plc**. COVAS++ is an unofficial,
fan-made companion and is **not affiliated with, endorsed by, or supported by Frontier**. All
game names, identifiers, and reference data derived from Elite Dangerous remain the property of
Frontier Developments and are used here under Frontier's fan-content policy. See the disclaimer in
the [README](README.md) and [`LICENSE`](LICENSE).

---

*If you believe an attribution here is incomplete or incorrect, please open an issue — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).*
