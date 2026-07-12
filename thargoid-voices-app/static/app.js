"use strict";

// ---- state ----
let TYPES = [];          // [{key,label,description,defaults}]
let activeType = null;
let variants = [];       // rendered variant metadata, newest first

const $ = (id) => document.getElementById(id);

// ---- helpers ----
async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* non-json (e.g. audio) */ }
  return { ok: res.ok, status: res.status, body };
}

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = "toast"; }, 3200);
}

function specOf(key) { return TYPES.find((t) => t.key === key); }

// ---- init ----
async function init() {
  const { body } = await api("/api/state");
  TYPES = body.types;
  $("outdir").value = body.output_dir || "";
  showOutdirMsg(body.output_dir_ok, body.output_dir_msg);

  buildTabs();
  selectType(TYPES[0].key);
  wireControls();
}

function buildTabs() {
  const nav = $("tabs");
  nav.innerHTML = "";
  for (const t of TYPES) {
    const b = document.createElement("button");
    b.className = "tab";
    b.textContent = t.label;
    b.dataset.key = t.key;
    b.onclick = () => selectType(t.key);
    nav.appendChild(b);
  }
}

function selectType(key) {
  activeType = key;
  const spec = specOf(key);
  document.querySelectorAll(".tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.key === key);
  });
  $("typeDesc").textContent = spec.description;
  applyDefaults(spec.defaults);
}

function applyDefaults(d) {
  setSlider("pitch", d.pitch);
  setSlider("harshness", d.harshness);
  setSlider("reverb", d.reverb);
}

function setSlider(id, val) {
  $(id).value = val;
  $(id + "Out").value = val;
}

function wireControls() {
  for (const id of ["pitch", "harshness", "reverb"]) {
    $(id).addEventListener("input", () => { $(id + "Out").value = $(id).value; });
  }
  $("resetDefaults").onclick = () => applyDefaults(specOf(activeType).defaults);
  $("genOne").onclick = () => generate(activeType);
  $("genAll").onclick = () => generate("all");
  $("clearResults").onclick = () => { variants = []; renderVariants(); };
  $("saveOutdir").onclick = setOutdir;
  $("outdir").addEventListener("keydown", (e) => { if (e.key === "Enter") setOutdir(); });
}

function showOutdirMsg(ok, msg) {
  const el = $("outdirMsg");
  el.textContent = msg || "";
  el.className = "hint " + (ok ? "ok" : "bad");
}

async function setOutdir() {
  const { body } = await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output_dir: $("outdir").value }),
  });
  showOutdirMsg(body.ok, body.msg);
  toast(body.ok ? "Output folder set." : body.msg, !body.ok);
}

// ---- generate ----
async function generate(type) {
  const payload = {
    type,
    count: parseInt($("count").value, 10) || 1,
    pitch: parseInt($("pitch").value, 10),
    harshness: parseInt($("harshness").value, 10),
    reverb: parseInt($("reverb").value, 10),
    seed: $("seed").value.trim() === "" ? null : parseInt($("seed").value, 10),
  };
  setGenerating(true);
  try {
    const { ok, body } = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!ok) { toast((body && body.error) || "Generate failed.", true); return; }
    // newest first
    variants = body.variants.reverse().concat(variants);
    renderVariants();
    toast(`Generated ${body.variants.length} variant(s).`);
  } catch (e) {
    toast("Generate error: " + e.message, true);
  } finally {
    setGenerating(false);
  }
}

function setGenerating(on) {
  $("genOne").disabled = on;
  $("genAll").disabled = on;
  $("genOne").textContent = on ? "Generating…" : "Generate";
}

// ---- render variant rows ----
function renderVariants() {
  const wrap = $("variants");
  wrap.innerHTML = "";
  $("emptyMsg").style.display = variants.length ? "none" : "block";
  $("resultCount").textContent = variants.length ? `${variants.length} in session` : "";

  for (const v of variants) {
    const row = document.createElement("div");
    row.className = "variant" + (v.clipping ? " clip" : "");

    const meterPct = Math.min(100, Math.round(v.peak * 100));
    row.innerHTML = `
      <span class="v-badge">${v.label}</span>
      <div class="v-mid">
        <div class="v-meta">
          <span>dur <b>${v.duration.toFixed(2)}s</b></span>
          <span>peak <b>${v.peak_db.toFixed(1)} dB</b></span>
          <span>rms <b>${v.rms_db.toFixed(1)} dB</b></span>
          <span class="meter"><i style="width:${meterPct}%"></i></span>
          <span class="seed" title="click to copy seed">seed ${v.seed}</span>
          ${v.clipping ? '<span class="clipwarn">&#9888; clipping</span>' : ""}
        </div>
        <audio controls preload="none" src="${v.url}"></audio>
      </div>
      <div class="v-right">
        <button class="btn primary small">Save</button>
        <div class="save-status"></div>
      </div>`;

    row.querySelector(".seed").onclick = () => {
      navigator.clipboard?.writeText(String(v.seed));
      toast("Seed copied: " + v.seed);
    };
    const btn = row.querySelector("button");
    const status = row.querySelector(".save-status");
    btn.onclick = () => saveVariant(v, btn, status);

    wrap.appendChild(row);
  }
}

async function saveVariant(v, btn, status) {
  btn.disabled = true;
  status.textContent = "Saving…";
  status.className = "save-status";
  try {
    const { ok, body } = await api("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: v.id }),
    });
    if (ok && body.ok) {
      status.textContent = "✔ " + body.path;
      status.className = "save-status ok";
      btn.textContent = "Save again";
      toast("Saved: " + body.path);
    } else {
      status.textContent = "✖ " + (body ? body.msg : "save failed");
      status.className = "save-status bad";
      toast((body && body.msg) || "Save failed.", true);
    }
  } catch (e) {
    status.textContent = "✖ " + e.message;
    status.className = "save-status bad";
  } finally {
    btn.disabled = false;
  }
}

init();
