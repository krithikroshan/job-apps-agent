const el = (id) => document.getElementById(id);

//: [{id, label, key_url, user_key_last4, has_server_key, model, default_model}]
let providers = [];
//: provider ids, in fallback order
let order = [];
//: Each provider's mark, from /static.
const LOGOS = {
  gemini: "/static/logo-gemini.svg",
  openai: "/static/logo-openai.svg",
  anthropic: "/static/logo-anthropic.svg",
  openrouter: "/static/logo-openrouter.svg",
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function setNote(node, text, isErr) {
  node.textContent = text || "";
  node.className = "saved-note" + (isErr ? " err" : "");
}

function status(p) {
  if (p.user_key_unreadable) {
    return { text: `Your key ····${p.user_key_last4} can't be read — enter it again`, ready: false };
  }
  if (p.user_key_last4) return { text: `Your key ····${p.user_key_last4}`, ready: true };
  if (p.has_server_key) return { text: "Using the server's key", ready: true };
  return { text: "Not set up — skipped", ready: false };
}

function card(p, rank) {
  const s = status(p);
  const placeholder = p.default_model
    ? `Default: ${p.default_model}`
    : "Default: free models, tried in turn";
  // The server's key always runs the default model; only your own key
  // can be pointed at another.
  const serverOnly = !p.user_key_last4 && p.has_server_key;
  return `
    <li class="provider" data-id="${escapeHtml(p.id)}">
      <div class="provider-head">
        <span class="provider-rank" title="Tried ${rank === 1 ? "first" : `in position ${rank}`}">${rank}</span>
        ${LOGOS[p.id] ? `<img class="provider-logo" src="${LOGOS[p.id]}" alt="" width="22" height="22">` : ""}
        <h2>${escapeHtml(p.label)}</h2>
        <span class="provider-status ${s.ready ? "ready" : ""}">${escapeHtml(s.text)}</span>
        <span class="provider-move">
          <button class="btn btn-ghost" data-move="-1" aria-label="Try ${escapeHtml(p.label)} earlier"
                  ${rank === 1 ? "disabled" : ""}>↑</button>
          <button class="btn btn-ghost" data-move="1" aria-label="Try ${escapeHtml(p.label)} later"
                  ${rank === order.length ? "disabled" : ""}>↓</button>
        </span>
      </div>
      <div class="provider-row">
        <label for="key-${escapeHtml(p.id)}">Key</label>
        <input class="input" id="key-${escapeHtml(p.id)}" type="password" autocomplete="off"
               spellcheck="false" placeholder="${p.user_key_last4 ? "Paste a new key to replace yours" : "Paste an API key"}">
        <button class="btn btn-secondary" data-action="test">Test</button>
        <button class="btn btn-secondary" data-action="save">Save key</button>
        ${p.user_key_last4 ? `<button class="btn btn-danger" data-action="remove">Remove</button>` : ""}
      </div>
      <div class="provider-row">
        <label for="model-${escapeHtml(p.id)}">Model</label>
        <input class="input" id="model-${escapeHtml(p.id)}" type="text" spellcheck="false"
               list="models-${escapeHtml(p.id)}" value="${escapeHtml(p.model)}"
               placeholder="${escapeHtml(placeholder)}">
        <datalist id="models-${escapeHtml(p.id)}"></datalist>
      </div>
      ${serverOnly ? `<p class="provider-hint">The server's key always uses the default
        model. Add your own key to choose a different one.</p>` : ""}
      <span class="saved-note" data-note></span>
      <a class="key-link" href="${escapeHtml(p.key_url)}" target="_blank" rel="noopener noreferrer">Get a ${escapeHtml(p.label)} key</a>
    </li>`;
}

/* What the user has typed or fetched into each card but not saved: kept
 * across re-renders (reordering, a key save) so nothing silently vanishes. */
function snapshot() {
  const out = {};
  for (const item of document.querySelectorAll(".provider")) {
    const id = item.dataset.id;
    const note = item.querySelector("[data-note]");
    out[id] = {
      key: el(`key-${id}`).value,
      model: el(`model-${id}`).value,
      models: el(`models-${id}`).innerHTML,
      note: note.textContent,
      noteClass: note.className,
    };
  }
  return out;
}

function restore(saved) {
  for (const [id, s] of Object.entries(saved)) {
    if (!el(`key-${id}`)) continue;
    el(`key-${id}`).value = s.key;
    el(`model-${id}`).value = s.model;
    el(`models-${id}`).innerHTML = s.models;
    const note = document.querySelector(`.provider[data-id="${id}"] [data-note]`);
    note.textContent = s.note;
    note.className = s.noteClass;
  }
}

function render() {
  const saved = snapshot();
  const byId = Object.fromEntries(providers.map((p) => [p.id, p]));
  el("providers").innerHTML = order.map((id, i) => card(byId[id], i + 1)).join("");
  restore(saved);
}

/* ``keepOrder``: refreshing key status after a key save shouldn't undo a
 * reorder the user hasn't saved yet. */
async function load({ keepOrder = false } = {}) {
  const res = await fetch("/api/llm/settings");
  if (!res.ok) {
    setNote(el("settings-msg"), "Couldn't load settings.", true);
    return;
  }
  const data = await res.json();
  providers = data.providers;
  const sameSet = order.length === data.order.length
    && data.order.every((id) => order.includes(id));
  if (!(keepOrder && sameSet)) order = data.order;
  el("no-storage").classList.toggle("show", !data.can_store_keys);
  render();
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

el("providers").addEventListener("click", async (ev) => {
  const item = ev.target.closest(".provider");
  if (!item) return;
  const id = item.dataset.id;
  const note = item.querySelector("[data-note]");

  const move = ev.target.closest("[data-move]");
  if (move) {
    const from = order.indexOf(id);
    const to = from + Number(move.dataset.move);
    if (to < 0 || to >= order.length) return;
    [order[from], order[to]] = [order[to], order[from]];
    render();
    setNote(el("settings-msg"), "Order changed — save to keep it.");
    return;
  }

  const button = ev.target.closest("[data-action]");
  if (!button) return;
  const keyInput = el(`key-${id}`);
  button.disabled = true;
  try {
    if (button.dataset.action === "test") {
      setNote(note, "Testing…");
      const { ok, data } = await post("/api/llm/test", { provider: id, key: keyInput.value });
      if (!ok) return setNote(note, data.error || "Test failed.", true);
      el(`models-${id}`).innerHTML = data.models
        .map((m) => `<option value="${escapeHtml(m)}">`).join("");
      const which = { typed: "This key", yours: "Your saved key", server: "The server's key" }[data.source];
      setNote(note, `${which} works — ${data.models.length} models available `
                    + "(pick one in the Model box, or leave it blank for the default).");
    } else if (button.dataset.action === "save") {
      const { ok, data } = await post("/api/llm/key", { provider: id, key: keyInput.value });
      if (!ok) return setNote(note, data.error || "Couldn't save the key.", true);
      keyInput.value = "";  // saved: don't carry the plaintext through the re-render
      await load({ keepOrder: true });
      setNote(document.querySelector(`.provider[data-id="${id}"] [data-note]`), "Key saved.");
    } else if (button.dataset.action === "remove") {
      if (!confirm("Remove your saved key for this provider?")) return;
      const { ok, data } = await post("/api/llm/key/delete", { provider: id });
      if (!ok) return setNote(note, data.error || "Couldn't remove the key.", true);
      await load({ keepOrder: true });
      setNote(document.querySelector(`.provider[data-id="${id}"] [data-note]`), "Key removed.");
    }
  } finally {
    button.disabled = false;
  }
});

el("btn-save-settings").addEventListener("click", async () => {
  const btn = el("btn-save-settings");
  btn.disabled = true;
  try {
    const models = {};
    for (const p of providers) models[p.id] = el(`model-${p.id}`).value.trim();
    const { ok, data } = await post("/api/llm/settings", { order, models });
    setNote(el("settings-msg"), ok ? "Saved." : data.error || "Couldn't save.", !ok);
  } finally {
    btn.disabled = false;
  }
});

load();
