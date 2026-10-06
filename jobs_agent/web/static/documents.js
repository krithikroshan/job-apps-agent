const el = (id) => document.getElementById(id);

/* The two tabs, each with the heading and standfirst that belong to it. */
const TABS = {
  identity: {
    title: "Profile",
    blurb: "Stored once, reused for every draft. Changes affect future drafts only — "
         + "letters already written keep what they were drafted with.",
  },
  scoring: {
    title: "Scoring profile",
    blurb: "What counts as a good match. Applied on the next fetch — postings already "
         + "in the queue keep the score they were stored with.",
  },
};

/* The four scoring lists. "weights" fields carry a number per term and
 * round-trip as `term = weight`; "lines" fields are bare terms, one per line,
 * where a trailing space is meaningful and must survive editing.
 */
const FIELDS = {
  target_titles: { kind: "weights", add: "Add a title" },
  domain_terms: { kind: "weights", add: "Add a term" },
  title_blockers: { kind: "lines", add: "Add a blocker" },
  experience_blockers: { kind: "lines", add: "Add a blocker" },
  salary_bands: { kind: "weights", add: "Add a band" },
};

/* Single-value search settings: profile text key -> input id. */
const SETTINGS = {
  locations: "locations",
  radius_miles: "radius",
  job_category: "job-category",
  domain_only_threshold: "domain-only",
  contract_bonus: "contract-bonus",
};
//: Chips shown before the list folds behind a "+ N more".
const VISIBLE_CHIPS = 14;

const CV_HINT = 'A <code>.docx</code> or <code>.pdf</code>. The original file is kept; '
              + 'its text is extracted for drafting, without the layout.';

//: field -> { items: [{term, weight}], expanded: bool }
const state = {};
let tab = "identity";
//: The preset the editor was last filled from; saved with the profile.
let currentPreset = "";
//: [{id, label, description, profile}] from /api/presets.
let presets = [];

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function setMsg(id, text, isErr) {
  const m = el(id);
  m.textContent = text || "";
  m.className = "saved-note" + (isErr ? " err" : "");
}

/* — tabs — */

function showTab(name) {
  tab = TABS[name] ? name : "identity";
  el("profile-title").textContent = TABS[tab].title;
  el("profile-blurb").textContent = TABS[tab].blurb;
  document.querySelectorAll(".tab").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  });
  el("panel-identity").hidden = tab !== "identity";
  el("panel-scoring").hidden = tab !== "scoring";
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    showTab(btn.dataset.tab);
    history.replaceState(null, "", "#" + tab);
  });
});
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

/* — the scoring lists: text on the wire, chips on the screen —
 * The model below is the single source of truth. The chips and the "Edit as
 * text" box are two views of it, and both write straight back into it.
 */

function parseField(field, text) {
  const { kind } = FIELDS[field];
  const items = [];
  for (const raw of String(text || "").split("\n")) {
    if (kind === "lines") {
      // Only the line ending is stripped: `lead ` and `lead` are different
      // blockers, and the trailing space is the whole point of the former.
      const term = raw.replace(/\r$/, "").toLowerCase();
      if (term.trim() && !term.trimStart().startsWith("#")) items.push({ term });
      continue;
    }
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.lastIndexOf("=");
    const term = (at === -1 ? line : line.slice(0, at)).trim().toLowerCase();
    const weight = parseInt(at === -1 ? "0" : line.slice(at + 1).trim(), 10);
    if (term) items.push({ term, weight: Number.isFinite(weight) ? weight : 0 });
  }
  return items;
}

function formatField(field) {
  const { kind } = FIELDS[field];
  return state[field].items
    .filter((i) => i.term.trim())
    .map((i) => (kind === "weights" ? `${i.term} = ${i.weight}` : i.term))
    .join("\n");
}

function chip(field, item, index) {
  const { kind } = FIELDS[field];
  const term = escapeHtml(item.term);
  const label = escapeHtml(item.term.trim() || "this term");
  // A trailing space is invisible, so it gets a mark of its own rather than
  // being silently dropped the first time someone edits the chip.
  const ws = item.term !== item.term.trimEnd()
    ? `<span class="chip-ws" title="ends with a space">␣</span>` : "";
  const weight = kind === "weights"
    ? `<input class="chip-weight" type="number" step="1" data-index="${index}"
              value="${item.weight}" aria-label="Weight for ${label}">` : "";
  return `
    <span class="chip ${kind === "weights" ? "chip-weighted" : "chip-blocker"}">
      <input class="chip-term" data-index="${index}" value="${term}"
             style="width:${Math.max(item.term.length, 3)}ch"
             aria-label="Term" spellcheck="false">${ws}${weight}
      <button class="chip-x" data-remove="${index}" title="Remove"
              aria-label="Remove ${label}">×</button>
    </span>`;
}

function renderField(field) {
  const block = document.querySelector(`.block[data-field="${field}"]`);
  const box = block.querySelector("[data-chips]");
  const { items, expanded } = state[field];

  if (!items.length) {
    box.innerHTML = `<span class="chips-empty">Nothing here yet.</span>`;
  } else {
    const shown = expanded ? items : items.slice(0, VISIBLE_CHIPS);
    const hidden = items.length - shown.length;
    box.innerHTML = shown.map((item, i) => chip(field, item, i)).join("")
      + (hidden ? `<button class="chip-more" data-expand>+ ${hidden} more</button>` : "");
  }
  box.insertAdjacentHTML("beforeend",
    `<button class="btn btn-ghost" data-add>${FIELDS[field].add}</button>`);

  // Keep the text view honest if it happens to be open.
  const details = block.querySelector(".as-text");
  if (details.open) block.querySelector("[data-text]").value = formatField(field);
}

function renderScoring() {
  for (const field of Object.keys(FIELDS)) renderField(field);
}

function fillProfile(profile) {
  for (const [key, id] of Object.entries(SETTINGS)) el(id).value = profile[key] ?? "";
  currentPreset = profile.preset || "";
  if (currentPreset) el("preset-select").value = currentPreset;
  showPresetDescription();
  for (const field of Object.keys(FIELDS)) {
    state[field] = {
      items: parseField(field, profile[field]),
      expanded: state[field] ? state[field].expanded : false,
    };
  }
  renderScoring();
}

function readProfile() {
  const out = { preset: currentPreset };
  for (const [key, id] of Object.entries(SETTINGS)) out[key] = el(id).value;
  for (const field of Object.keys(FIELDS)) out[field] = formatField(field);
  return out;
}

/* — presets — */

function showPresetDescription() {
  const preset = presets.find((p) => p.id === el("preset-select").value);
  el("preset-description").textContent = preset ? preset.description : "";
}

function fillPresets(list) {
  presets = list;
  el("preset-select").innerHTML = presets
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label)}</option>`)
    .join("");
  if (currentPreset) el("preset-select").value = currentPreset;
  showPresetDescription();
}

el("preset-select").addEventListener("change", showPresetDescription);

el("btn-load-preset").addEventListener("click", () => {
  const preset = presets.find((p) => p.id === el("preset-select").value);
  if (!preset) return;
  if (!confirm(`Replace the editor's contents with "${preset.label}"? `
               + "Nothing is saved until you click Save scoring profile.")) return;
  fillProfile(preset.profile);
  setMsg("profile-msg", `Loaded "${preset.label}". Review it, then save.`);
});

/* One delegated listener per block, wired once, covering both views. */
for (const field of Object.keys(FIELDS)) {
  const block = document.querySelector(`.block[data-field="${field}"]`);

  block.querySelector("[data-chips]").addEventListener("click", (ev) => {
    const remove = ev.target.closest("[data-remove]");
    if (remove) {
      state[field].items.splice(Number(remove.dataset.remove), 1);
      renderField(field);
      return;
    }
    if (ev.target.closest("[data-expand]")) {
      state[field].expanded = true;
      renderField(field);
      return;
    }
    if (ev.target.closest("[data-add]")) {
      state[field].items.push(
        FIELDS[field].kind === "weights" ? { term: "", weight: 10 } : { term: "" });
      state[field].expanded = true;
      renderField(field);
      // The new chip is the last term input; put the caret in it.
      const inputs = block.querySelectorAll(".chip-term");
      if (inputs.length) inputs[inputs.length - 1].focus();
    }
  });

  // Live edits write into the model without re-rendering, so the caret stays
  // where it is. Only structural changes re-render.
  block.querySelector("[data-chips]").addEventListener("input", (ev) => {
    const target = ev.target;
    const index = Number(target.dataset.index);
    const item = state[field].items[index];
    if (!item) return;
    if (target.classList.contains("chip-term")) {
      item.term = target.value.toLowerCase();
      target.style.width = Math.max(item.term.length, 3) + "ch";
    } else if (target.classList.contains("chip-weight")) {
      const n = parseInt(target.value, 10);
      item.weight = Number.isFinite(n) ? n : 0;
    }
  });

  const details = block.querySelector(".as-text");
  const text = block.querySelector("[data-text]");
  details.addEventListener("toggle", () => {
    if (details.open) text.value = formatField(field);
    else renderField(field);
  });
  text.addEventListener("input", () => {
    state[field].items = parseField(field, text.value);
  });
  text.addEventListener("blur", () => renderField(field));
}

/* — identity & documents — */

function showCv(filename, chars) {
  const pick = el("btn-cv-pick");
  if (filename) {
    const ext = filename.toLowerCase().split(".").pop();
    el("cv-badge").textContent = ext === "pdf" || ext === "docx" ? ext.toUpperCase() : "CV";
    el("cv-name").textContent = filename;
    el("cv-meta").textContent =
      `${Number(chars || 0).toLocaleString()} characters extracted · original file kept`;
    el("cv-download").hidden = false;
    pick.textContent = "Replace";
  } else {
    el("cv-badge").textContent = "CV";
    el("cv-name").textContent = "No CV uploaded yet";
    el("cv-meta").innerHTML = CV_HINT;
    el("cv-download").hidden = true;
    pick.textContent = "Upload";
  }
}

async function load() {
  const [docsRes, profileRes, presetsRes] = await Promise.all([
    fetch("/api/documents"),
    fetch("/api/profile"),
    fetch("/api/presets"),
  ]);
  fillPresets(presetsRes.ok ? await presetsRes.json() : []);
  const data = await docsRes.json();
  el("name").value = data.candidate_name || "";
  el("template").value = data.cover_letter_template || "";
  el("cv-preview").value = data.cv || "";
  showCv(data.cv_filename, (data.cv || "").length);
  fillProfile(await profileRes.json());
}

function bytesToBase64(bytes) {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

el("btn-cv-pick").addEventListener("click", () => el("cv-file").click());

el("cv-file").addEventListener("change", async () => {
  const file = el("cv-file").files[0];
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (!(lower.endsWith(".docx") || lower.endsWith(".pdf"))) {
    setMsg("cv-msg", "Please choose a .docx or .pdf file.", true);
    el("cv-file").value = "";
    return;
  }
  setMsg("cv-msg", "Reading " + file.name + "…");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const res = await fetch("/api/cv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, data_b64: bytesToBase64(bytes) }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    setMsg("cv-msg", `Uploaded ${data.filename}.`);
    showCv(data.filename, data.chars);
    el("cv-preview").value = data.text;
  } else {
    setMsg("cv-msg", data.error || "Could not read that file.", true);
  }
  el("cv-file").value = "";
});

el("btn-save").addEventListener("click", async () => {
  const btn = el("btn-save");
  btn.disabled = true;
  try {
    const res = await fetch("/api/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_name: el("name").value,
        cover_letter_template: el("template").value,
      }),
    });
    setMsg("doc-msg", res.ok ? "Saved." : "Could not save.", !res.ok);
  } finally {
    btn.disabled = false;
  }
});

el("btn-save-profile").addEventListener("click", async () => {
  const btn = el("btn-save-profile");
  btn.disabled = true;
  setMsg("profile-msg", "Saving…");
  try {
    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readProfile()),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      // The server echoes the reformatted profile, so what you see is
      // exactly what was stored — including terms it normalised or merged.
      fillProfile(data.profile);
      setMsg("profile-msg", "Saved.");
    } else {
      setMsg("profile-msg", data.error || "Could not save the scoring profile.", true);
    }
  } finally {
    btn.disabled = false;
  }
});

el("btn-reset-profile").addEventListener("click", async () => {
  if (!confirm("Discard your scoring profile and restore the defaults?")) return;
  const btn = el("btn-reset-profile");
  btn.disabled = true;
  try {
    const res = await fetch("/api/profile/reset", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      fillProfile(data.profile);
      setMsg("profile-msg", "Restored the defaults.");
    } else {
      setMsg("profile-msg", data.error || "Could not reset.", true);
    }
  } finally {
    btn.disabled = false;
  }
});

/* — profile chat — */

let chatHistory = [];       // [{role: "user"|"assistant", content}]
let pendingProposal = null; // the assistant's last structured proposal, or null
let pendingPreview = null;  // the same proposal, merged and formatted for /api/profile

const PROPOSAL_LABELS = {
  target_titles: "Target titles",
  domain_terms: "Domain terms",
  title_blockers: "Title blockers",
  experience_blockers: "Experience blockers",
};

function renderChatLog() {
  const log = el("chat-log");
  log.innerHTML = chatHistory
    .map((m) => `<div class="chat-msg chat-msg-${m.role}">${escapeHtml(m.content)}</div>`)
    .join("");
  log.scrollTop = log.scrollHeight;
}

function describeProposalValue(value) {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "(none)";
  const terms = Object.entries(value).map(([term, weight]) => `${term} = ${weight}`);
  return terms.length ? terms.join(", ") : "(none)";
}

function renderProposal() {
  const box = el("chat-proposal");
  box.hidden = !pendingProposal;
  if (!pendingProposal) return;
  el("chat-proposal-list").innerHTML = Object.entries(pendingProposal)
    .map(([field, value]) => `<li><strong>${PROPOSAL_LABELS[field] || field}:</strong> `
      + `${escapeHtml(describeProposalValue(value))}</li>`)
    .join("");
}

async function sendChat() {
  const input = el("chat-input");
  const message = input.value.trim();
  if (!message) return;

  chatHistory.push({ role: "user", content: message });
  renderChatLog();
  input.value = "";

  const btn = el("chat-send");
  btn.disabled = true;
  input.disabled = true;
  setMsg("chat-msg", "");
  try {
    const res = await fetch("/api/profile/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: chatHistory.slice(0, -1),
        pending_proposal: pendingProposal,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      chatHistory.push({ role: "assistant", content: data.reply });
      renderChatLog();
      pendingProposal = data.proposal || null;
      pendingPreview = data.preview || null;
      renderProposal();
    } else {
      setMsg("chat-msg", data.error || "The assistant couldn't respond.", true);
    }
  } catch {
    setMsg("chat-msg", "The assistant couldn't respond.", true);
  } finally {
    btn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

/* Suggestions arrive like an assistant turn: a reply, and a proposal in the
 * same preview box the chat uses, so applying one is the same click. */
document.querySelectorAll("[data-suggest]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const buttons = document.querySelectorAll("[data-suggest]");
    buttons.forEach((b) => { b.disabled = true; });
    setMsg("chat-msg", btn.dataset.suggest === "cv"
      ? "Reading your CV…" : "Looking at what you've shortlisted and rejected…");
    try {
      const res = await fetch("/api/suggest/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: btn.dataset.suggest }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return setMsg("chat-msg", data.error || "Couldn't make a suggestion.", true);
      setMsg("chat-msg", "");
      chatHistory.push({ role: "assistant", content: data.reply });
      renderChatLog();
      pendingProposal = data.proposal || null;
      pendingPreview = data.preview || null;
      renderProposal();
    } catch {
      setMsg("chat-msg", "Couldn't make a suggestion.", true);
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  });
});

el("chat-send").addEventListener("click", sendChat);
el("chat-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendChat();
  }
});

el("chat-apply").addEventListener("click", async () => {
  if (!pendingPreview) return;
  const btn = el("chat-apply");
  btn.disabled = true;
  try {
    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pendingPreview),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      // Same echoed-canonical-representation contract as the manual save
      // button — the chip editors refresh from exactly what was stored.
      fillProfile(data.profile);
      pendingProposal = null;
      pendingPreview = null;
      renderProposal();
      setMsg("chat-msg", "Applied to the scoring profile.");
    } else {
      setMsg("chat-msg", data.error || "Could not apply the proposal.", true);
    }
  } finally {
    btn.disabled = false;
  }
});

el("chat-discard").addEventListener("click", () => {
  pendingProposal = null;
  pendingPreview = null;
  renderProposal();
});

showTab(location.hash.slice(1));
load();
