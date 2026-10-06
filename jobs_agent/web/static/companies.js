const el = (id) => document.getElementById(id);

//: [{id, name, careers_url, host, ats, ats_label, last_checked, last_found, last_new, last_error}]
let companies = [];
let canAdd = true;
let maxCompanies = 40;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function setNote(node, text, isErr) {
  node.textContent = text || "";
  node.className = "saved-note" + (isErr ? " err" : "");
}

/* tone: "info" when something finished, "progress" while it runs, "error". */
function setMessage(text, tone) {
  const m = el("status-msg");
  m.className = "banner";
  if (!text) {
    m.innerHTML = "";
    return;
  }
  m.innerHTML = `<span class="dot"></span><span>${escapeHtml(text)}</span>`;
  m.classList.add("show", `banner-${tone || "info"}`);
}

/* last_checked carries its UTC offset; an older naive stamp is read as UTC. */
function ago(iso) {
  if (!iso) return "";
  const then = new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (!Number.isFinite(mins) || mins < 0) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : "";
}

function lastCheck(c) {
  if (!c.last_checked) {
    return `<span class="company-last"><span class="muted">Not checked yet</span></span>`;
  }
  const when = `Checked ${ago(c.last_checked)}`;
  if (c.last_error) {
    return `<span class="company-last err">${escapeHtml(when)}: ${escapeHtml(c.last_error)}</span>`;
  }
  const found = c.last_found || 0;
  let what = `${found} job${found === 1 ? "" : "s"} found, ${c.last_new || 0} new`;
  // A plain careers page that yields nothing is most likely drawn by JavaScript.
  if (!found && c.ats === "generic") {
    what += " — if the site does list jobs, try its job-board link instead";
  }
  return `<span class="company-last">${escapeHtml(when)}: ${escapeHtml(what)}</span>`;
}

function companyItem(c) {
  const url = safeUrl(c.careers_url);
  return `
    <li class="company" data-id="${escapeHtml(c.id)}">
      <div class="company-main">
        <span class="company-name">${escapeHtml(c.name)}
          <span class="tag ${c.ats === "generic" ? "tag-neutral" : "tag-accent"}">${escapeHtml(c.ats_label)}</span>
        </span>
        <span class="company-host">${url
          ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(c.host)}</a>`
          : escapeHtml(c.host)}</span>
        ${lastCheck(c)}
      </div>
      <div class="company-actions">
        <button class="btn btn-secondary btn-sm" data-action="check">Check now</button>
        <button class="btn btn-danger btn-sm" data-action="remove">Remove</button>
      </div>
    </li>`;
}

function renderCompanies() {
  el("companies").innerHTML = companies.length
    ? companies.map(companyItem).join("")
    : `<li class="company-empty">No companies yet. Add one below, or pick a suggestion.</li>`;
  el("watched-count").textContent = companies.length ? `${companies.length} of ${maxCompanies}` : "";
  el("btn-add").disabled = !canAdd;
  const note = el("add-msg");
  const full = `You're watching the most companies allowed (${maxCompanies}). Remove one to add another.`;
  if (!canAdd) setNote(note, full);
  else if (note.textContent === full) setNote(note, "");
}

function renderSuggested(list) {
  el("suggested-block").hidden = !list.length;
  el("suggested").innerHTML = list.map((s) => `
    <li class="suggestion">
      <span class="company-name">${escapeHtml(s.name)}</span>
      <span class="company-host">${escapeHtml(safeHost(s.careers_url))}</span>
      <button class="btn btn-secondary btn-sm" data-name="${escapeHtml(s.name)}"
              data-url="${escapeHtml(s.careers_url)}" ${canAdd ? "" : "disabled"}>Add</button>
    </li>`).join("");
}

function safeHost(url) {
  try {
    return new URL(url).hostname;
  } catch (e) {
    return url;
  }
}

async function load() {
  const res = await fetch("/api/companies");
  if (!res.ok) {
    setMessage("Couldn't load your companies. Refresh to try again.", "error");
    return;
  }
  const data = await res.json();
  companies = data.companies;
  canAdd = data.can_add;
  maxCompanies = data.max;
  renderCompanies();
  renderSuggested(data.suggested);
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

/* Check one company and say what happened. */
async function check(id, name) {
  setMessage(`Reading ${name}'s careers site… this can take a little while.`, "progress");
  const { ok, status, data } = await post("/api/companies/check", { id });
  if (status === 409) return setMessage(data.error || "Already checking in another tab.", "error");
  // Out of today's checks: the server says when they come back; not a fault.
  if (status === 429) return setMessage(data.error || "That's all the checks for today.", "info");
  if (!ok) return setMessage(data.error || "The check failed.", "error");
  // Checked minutes ago: the server sends that check's result, not a fresh one.
  if (data.cooldown) {
    setMessage(data.message || `${name} was checked a few minutes ago. Try again shortly.`, "info");
    return load();
  }
  const r = data.result;
  if (r.error) {
    setMessage(`${name}: ${r.error}`, "error");
  } else {
    setMessage(`${name}: ${r.found} job${r.found === 1 ? "" : "s"} found, ${r.kept} matched your profile, `
               + `${r.new} new in your queue.`, "info");
  }
  await load();
}

async function add(name, url, note) {
  const { ok, data } = await post("/api/companies", { name, careers_url: url });
  if (!ok) {
    setNote(note, data.error || "Couldn't add that company.", true);
    return false;
  }
  setNote(note, "");
  await load();
  // Read it straight away, so the user sees whether the link works.
  await check(data.company.id, data.company.name);
  return true;
}

el("add-company").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = el("company-name").value.trim();
  const url = el("company-url").value.trim();
  const note = el("add-msg");
  if (!name) return setNote(note, "Give the company a name.", true);
  if (!url) return setNote(note, "Paste the link to the company's careers page.", true);
  const btn = el("btn-add");
  btn.disabled = true;
  try {
    if (await add(name, url, note)) {
      el("company-name").value = "";
      el("company-url").value = "";
    }
  } finally {
    btn.disabled = !canAdd;
  }
});

el("companies").addEventListener("click", async (ev) => {
  const button = ev.target.closest("[data-action]");
  const item = ev.target.closest(".company");
  if (!button || !item) return;
  const c = companies.find((x) => x.id === item.dataset.id);
  if (!c) return;
  button.disabled = true;
  try {
    if (button.dataset.action === "check") {
      await check(c.id, c.name);
    } else if (button.dataset.action === "remove") {
      if (!confirm(`Stop watching ${c.name}? Jobs already in your queue stay there.`)) return;
      const { ok, data } = await post("/api/companies/delete", { id: c.id });
      if (!ok) return setMessage(data.error || "Couldn't remove that company.", "error");
      setMessage(`Stopped watching ${c.name}.`, "info");
      await load();
    }
  } catch (e) {
    setMessage("Lost contact with the server. Try again.", "error");
  } finally {
    button.disabled = false;
  }
});

el("suggested").addEventListener("click", async (ev) => {
  const button = ev.target.closest("button[data-url]");
  if (!button) return;
  button.disabled = true;
  try {
    await add(button.dataset.name, button.dataset.url, el("add-msg"));
  } catch (e) {
    setMessage("Lost contact with the server. Try again.", "error");
  } finally {
    button.disabled = false;
  }
});

load();
