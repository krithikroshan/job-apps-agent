const el = (id) => document.getElementById(id);

const STATUS_LABEL = {
  new: "New", shortlisted: "Shortlisted", drafted: "Drafted",
  approved: "Approved", submitted: "Submitted", rejected: "Rejected",
};
//: Stage order in the rail, and the order the rail is built in.
const STAGES = ["new", "shortlisted", "drafted", "approved", "submitted", "rejected"];
//: The forward path through the lifecycle, drawn as the dot track on a card.
//: "rejected" is off to the side of it, so it gets a caption and no dots.
const PIPELINE = ["new", "shortlisted", "drafted", "approved", "submitted"];
const TRACK_CAPTION = {
  new: "Not reviewed yet",
  shortlisted: "Shortlisted — draft when ready",
  drafted: "Draft ready to review",
  approved: "Approved — ready to submit",
  submitted: "Submitted",
  rejected: "Set aside",
};
//: How the heading counts what's on screen, as [singular, plural].
const STAGE_COUNT = {
  new: ["posting scored and waiting", "postings scored and waiting"],
  shortlisted: ["posting shortlisted", "postings shortlisted"],
  drafted: ["letter waiting on you", "letters waiting on you"],
  approved: ["application ready to submit", "applications ready to submit"],
  submitted: ["application sent", "applications sent"],
  rejected: ["posting set aside", "postings set aside"],
};
// Status transitions offered per current status, as [newStatus, buttonLabel].
// "submitted" only ever appears from "approved" — the server enforces this
// too, so it isn't just a UI nicety.
const TRANSITIONS = {
  new:         [["shortlisted", "Shortlist"], ["rejected", "Reject"]],
  shortlisted: [["new", "Back to New"], ["rejected", "Reject"]],
  drafted:     [["approved", "Approve letter"], ["new", "Back to New"], ["rejected", "Reject"]],
  approved:    [["submitted", "Mark as submitted"], ["drafted", "Back to Drafted"], ["rejected", "Reject"]],
  submitted:   [["approved", "Reopen"]],
  rejected:    [["new", "Reset to New"]],
};
const DRAFTABLE = new Set(["new", "shortlisted", "drafted"]);
const HAS_LETTER_BOX = new Set(["drafted", "approved", "submitted"]);
//: The one action that leads, rendered as the primary button for the stage.
const LEAD_ACTION = {
  new: "draft", shortlisted: "draft", drafted: "approved", approved: "submit",
};

//: The stage lives in the URL hash, so a reload — or a link — keeps its place.
let stage = STAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "new";
let totalPostings = 0;
//: Per-stage totals from /api/stats, before this page's filters narrow them.
let stageTotals = {};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* — the status banner —
 * tone is "info" for something that finished, "progress" for something still
 * running, or "error" for a refusal. A refusal gets the message as a headline
 * and, where there is one, a second line explaining the rule.
 */
function setMessage(text, tone, detail) {
  const m = el("status-msg");
  m.className = "banner";
  if (!text) {
    m.innerHTML = "";
    return;
  }
  const body = tone === "error"
    ? `<div><b>${escapeHtml(text)}</b>${
        detail ? `<div class="banner-detail">${escapeHtml(detail)}</div>` : ""}</div>`
    : escapeHtml(text);
  m.innerHTML = `<span class="dot"></span>${body}`;
  m.classList.add("show", `banner-${tone || "info"}`);
}

/* — left rail — */

function renderStages(stats) {
  stageTotals = stats;
  totalPostings = STAGES.reduce((sum, key) => sum + (stats[key] || 0), 0);
  el("stages").innerHTML = STAGES.map((key) => `
    <button class="stage" role="tab" data-stage="${key}"
            aria-selected="${key === stage}">
      <span class="name">${STATUS_LABEL[key]}</span>
      <span class="n">${stats[key] || 0}</span>
    </button>`).join("");
}

async function loadStats() {
  const res = await fetch("/api/stats");
  renderStages(await res.json());
}

/* — a posting — */

function salaryText(row) {
  if (!row.salary_min) return "salary not stated";
  const min = Math.round(row.salary_min).toLocaleString();
  return row.salary_max
    ? `£${min}–£${Math.round(row.salary_max).toLocaleString()}`
    : `£${min}+`;
}

/* The store writes `updated` as a naive UTC isoformat with no offset, which
 * Date() would otherwise read as local time — hence the appended Z. */
function editedAgo(iso) {
  if (!iso) return "";
  const then = new Date(/[Z+]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (!Number.isFinite(mins) || mins < 0) return "";
  if (mins < 1) return "edited just now";
  if (mins < 60) return `edited ${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `edited ${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `edited ${days} day${days === 1 ? "" : "s"} ago`;
}

function track(status) {
  const caption = `<span class="caption">${TRACK_CAPTION[status]}</span>`;
  if (status === "rejected") return `<div class="track">${caption}</div>`;
  const at = PIPELINE.indexOf(status);
  const parts = [];
  PIPELINE.forEach((_, i) => {
    if (i) parts.push(`<span class="l${i <= at ? " done" : ""}"></span>`);
    const state = i < at ? " done" : i === at ? " now" : "";
    parts.push(`<span class="d${state}"></span>`);
  });
  return `<div class="track">${parts.join("")}${caption}</div>`;
}

function letterBox(row, open) {
  return `
    <details class="letter-box"${open ? " open" : ""}>
      <summary>
        <span class="when-closed btn btn-secondary">Read letter</span>
        <span class="when-open kicker">Cover letter</span>
        <span class="when-open letter-when">${escapeHtml(editedAgo(row.updated))}</span>
      </summary>
      <div class="letter-body">
        <textarea class="letter-text" data-letter-key="${escapeHtml(row.key)}"
                  aria-label="Cover letter">${escapeHtml(row.letter || "")}</textarea>
        <input class="input" type="text" data-feedback-key="${escapeHtml(row.key)}"
               placeholder="What should change? e.g. lead with the clerkship instead"
               aria-label="Redraft feedback">
        <div class="letter-actions">
          <button class="btn btn-secondary" data-redraft-key="${escapeHtml(row.key)}">Redraft with feedback</button>
          <button class="btn btn-ghost" data-save-key="${escapeHtml(row.key)}">Save edits</button>
          <span class="saved-note" data-saved-for="${escapeHtml(row.key)}" hidden>Saved.</span>
        </div>
      </div>
    </details>`;
}

function card(row, open) {
  const status = row.status;
  const key = escapeHtml(row.key);
  const url = escapeHtml(row.url || "");
  const lead = LEAD_ACTION[status];

  const btn = (kind, label) => {
    const primary = lead === kind ? "btn-primary" : "btn-secondary";
    if (kind === "draft") {
      return `<button class="btn ${primary}" data-draft-key="${key}">${label}</button>`;
    }
    if (kind === "submit") {
      return `<button class="btn ${primary}" data-submit-key="${key}" data-url="${url}">${label}</button>`;
    }
    return "";
  };

  const actions = [];
  if (DRAFTABLE.has(status)) {
    actions.push(btn("draft", status === "drafted" ? "Redraft" : "Prepare application"));
  }
  if (status === "approved") actions.push(btn("submit", "Submit application"));
  for (const [next, label] of TRANSITIONS[status] || []) {
    const primary = lead === next ? "btn-primary" : "btn-secondary";
    actions.push(`<button class="btn ${primary}" data-key="${key}" data-status="${next}">${label}</button>`);
  }
  actions.push(`<button class="btn btn-ghost" data-delete-key="${key}">Delete</button>`);
  if (url) {
    actions.push(`<a class="open-listing" href="${url}" target="_blank" rel="noopener">Open listing ↗</a>`);
  }

  const reasons = [
    `score ${row.score}`,
    escapeHtml(row.source || ""),
    escapeHtml(row.score_reasons || ""),
  ].filter(Boolean).join(" · ");

  const hasLetter = HAS_LETTER_BOX.has(status);
  return `
    <article class="job${hasLetter && open ? " is-open" : ""}" data-row="${key}">
      <div class="job-head">
        <div class="job-tagline">
          <span class="tag tag-status-${status}">${STATUS_LABEL[status]}</span>
          <span class="job-posted">posted ${escapeHtml(row.posted || "date unknown")}</span>
        </div>
        <h3>${url
          ? `<a href="${url}" target="_blank" rel="noopener">${escapeHtml(row.title)}</a>`
          : escapeHtml(row.title)}</h3>
        <div class="job-meta">${escapeHtml(row.employer || "unknown employer")} · ${
          escapeHtml(row.location || "location unknown")} · ${
          escapeHtml(row.contract_type || "type n/a")} · ${salaryText(row)}</div>
        <div class="job-reasons">${reasons}</div>
        ${track(status)}
      </div>
      ${hasLetter ? letterBox(row, open) : ""}
      <div class="job-actions">${actions.join("")}</div>
    </article>`;
}

/* — the empty states —
 * Nothing fetched yet is a different situation from a filter that matched
 * nothing, and only the first one deserves the explanation.
 */
function emptyState() {
  if (totalPostings) {
    return `<p class="empty-line">No postings in ${STATUS_LABEL[stage]} match these filters.</p>`;
  }
  return `
    <div class="empty-state">
      <div class="orb"></div>
      <h2>Nothing in the queue yet</h2>
      <p>Fetching pulls postings matching your target titles from Reed and Adzuna,
         scores them against your profile, and drops duplicates. It takes about a minute.</p>
      <div class="row">
        <button class="btn btn-primary" data-empty-fetch>Fetch new listings</button>
        <a class="btn btn-ghost" href="/documents#scoring">Check your scoring profile first</a>
      </div>
    </div>`;
}

async function loadQueue() {
  const limit = el("f-limit").value || 50;
  const minScore = el("f-min-score").value || 0;
  const location = el("f-location").value.trim();
  const minSalary = el("f-min-salary").value.trim();
  const maxSalary = el("f-max-salary").value.trim();
  const contractType = el("f-contract-type").value;
  el("stage-title").textContent = STATUS_LABEL[stage];
  el("stage-count").textContent = "";
  el("results").innerHTML = `<p class="empty-line">Loading…</p>`;

  const params = new URLSearchParams({ status: stage, limit, min_score: minScore });
  if (location) params.set("location", location);
  if (minSalary) params.set("min_salary", minSalary);
  if (maxSalary) params.set("max_salary", maxSalary);
  if (contractType) params.set("contract_type", contractType);
  const res = await fetch(`/api/queue?${params}`);
  const rows = await res.json();

  // The rail counts the whole stage; this counts what got past the filters.
  // Where those differ, say so, or the two numbers look like a bug.
  const [one, many] = STAGE_COUNT[stage];
  const total = stageTotals[stage] || 0;
  const shown = rows.length < total ? `${rows.length} of ${total}` : String(rows.length);
  el("stage-count").textContent = `${shown} ${rows.length === 1 ? one : many}`;

  if (!rows.length) {
    el("results").innerHTML = emptyState();
    return;
  }
  // Only the first letter opens: a stage full of expanded letters is a wall
  // of text, and the one at the top is the one being worked on.
  let opened = false;
  el("results").innerHTML = rows.map((row) => {
    const open = !opened && HAS_LETTER_BOX.has(row.status);
    if (open) opened = true;
    return card(row, open);
  }).join("");
  el("results").querySelectorAll(".letter-box[open] .letter-text").forEach(autoExpand);
}

function autoExpand(ta) {
  const max = Math.max(window.innerHeight - 220, 200);
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, max) + "px";
}

// Stats first, then the queue: the heading compares the two, so fetching them
// in parallel would race and leave the counts disagreeing for a beat.
async function reload() {
  await loadStats();
  await loadQueue();
}

/* — events — */

function selectStage(next) {
  if (!STAGES.includes(next) || next === stage) return;
  stage = next;
  el("stages").querySelectorAll(".stage").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.stage === stage));
  });
  setMessage("");
  loadQueue();
}

el("stages").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-stage]");
  if (!btn) return;
  history.replaceState(null, "", "#" + btn.dataset.stage);
  selectStage(btn.dataset.stage);
});

window.addEventListener("hashchange", () => selectStage(location.hash.slice(1)));

el("results").addEventListener("input", (ev) => {
  if (ev.target.matches(".letter-text")) autoExpand(ev.target);
});

el("results").addEventListener("toggle", (ev) => {
  if (!ev.target.matches(".letter-box")) return;
  const job = ev.target.closest(".job");
  if (job) job.classList.toggle("is-open", ev.target.open);
  if (ev.target.open) ev.target.querySelectorAll(".letter-text").forEach(autoExpand);
}, true);

el("results").addEventListener("click", async (ev) => {
  const statusBtn = ev.target.closest("button[data-key]");
  const draftBtn = ev.target.closest("button[data-draft-key]");
  const saveBtn = ev.target.closest("button[data-save-key]");
  const redraftBtn = ev.target.closest("button[data-redraft-key]");
  const submitBtn = ev.target.closest("button[data-submit-key]");
  const deleteBtn = ev.target.closest("button[data-delete-key]");
  const emptyFetch = ev.target.closest("button[data-empty-fetch]");

  if (emptyFetch) {
    fetchListings();
    return;
  }

  if (deleteBtn) {
    if (!confirm("Delete this listing for good? This can't be undone.")) return;
    deleteBtn.disabled = true;
    const res = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: deleteBtn.dataset.deleteKey }),
    });
    if (res.ok) {
      setMessage("Listing deleted.");
      await reload();
    } else {
      const data = await res.json().catch(() => ({}));
      setMessage(data.error || "Could not delete the listing.", "error");
      deleteBtn.disabled = false;
    }
    return;
  }

  if (statusBtn) {
    statusBtn.disabled = true;
    const res = await fetch("/api/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: statusBtn.dataset.key, status: statusBtn.dataset.status }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      setMessage("");
      await reload();
    } else {
      setMessage(
        data.error || "Could not update status.", "error",
        "The server enforces the order, so a letter always gets read before it leaves here.",
      );
      statusBtn.disabled = false;
    }
    return;
  }

  if (draftBtn) {
    draftBtn.disabled = true;
    setMessage("Drafting a tailored cover letter…", "progress");
    const res = await fetch("/api/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: draftBtn.dataset.draftKey }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      setMessage("Draft ready — review the letter below.", "info");
      await reload();
    } else {
      setMessage(data.error || "Could not draft a letter.", "error");
      draftBtn.disabled = false;
    }
    return;
  }

  if (redraftBtn) {
    const key = redraftBtn.dataset.redraftKey;
    const feedbackEl = document.querySelector(`[data-feedback-key="${key}"]`);
    const feedback = feedbackEl.value.trim();
    if (!feedback) {
      setMessage("Add feedback before redrafting.", "error");
      return;
    }
    redraftBtn.disabled = true;
    const letterEl = document.querySelector(`[data-letter-key="${key}"]`);
    setMessage("Redrafting with your feedback…", "progress");
    const res = await fetch("/api/redraft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, feedback, letter: letterEl.value }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      setMessage("Redraft ready — review the letter below.", "info");
      await reload();
    } else {
      setMessage(data.error || "Could not redraft the letter.", "error");
      redraftBtn.disabled = false;
    }
    return;
  }

  if (submitBtn) {
    const key = submitBtn.dataset.submitKey;
    const letterEl = document.querySelector(`[data-letter-key="${key}"]`);
    const letter = letterEl ? letterEl.value : "";
    try {
      await navigator.clipboard.writeText(letter);
      setMessage(
        "Cover letter copied — paste it into the application form on the listing that just opened.",
        "info",
      );
    } catch (e) {
      setMessage(
        "Could not copy the letter automatically — opening the listing; copy it from the letter above.",
        "error",
      );
    }
    window.open(submitBtn.dataset.url, "_blank", "noopener");
    return;
  }

  if (saveBtn) {
    saveBtn.disabled = true;
    const key = saveBtn.dataset.saveKey;
    const textarea = document.querySelector(`[data-letter-key="${key}"]`);
    const res = await fetch("/api/letter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, letter: textarea.value }),
    });
    saveBtn.disabled = false;
    const savedTag = document.querySelector(`[data-saved-for="${key}"]`);
    if (res.ok) {
      savedTag.hidden = false;
      setTimeout(() => { savedTag.hidden = true; }, 2000);
    } else {
      setMessage("Could not save edits.", "error");
    }
    return;
  }
});

el("btn-refresh").addEventListener("click", () => { setMessage(""); reload(); });
el("f-location").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { setMessage(""); loadQueue(); }
});
// Picking a suggestion from the datalist fires "change" without an Enter.
el("f-location").addEventListener("change", () => { setMessage(""); loadQueue(); });

/* The profile's search locations, offered as filter suggestions. */
async function loadLocationOptions() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) return;
    const { locations = "" } = await res.json();
    el("f-location-options").innerHTML = locations.split(",")
      .map((place) => place.trim())
      .filter((place) => place && place.toLowerCase() !== "uk")
      .map((place) => `<option value="${escapeHtml(place)}">`)
      .join("");
  } catch {
    // Suggestions are a convenience; the free-text filter still works.
  }
}
el("f-limit").addEventListener("change", () => { setMessage(""); loadQueue(); });
el("f-min-score").addEventListener("change", () => { setMessage(""); loadQueue(); });
el("f-min-salary").addEventListener("change", () => { setMessage(""); loadQueue(); });
el("f-max-salary").addEventListener("change", () => { setMessage(""); loadQueue(); });
el("f-contract-type").addEventListener("change", () => { setMessage(""); loadQueue(); });

async function fetchListings() {
  const btn = el("btn-fetch");
  btn.disabled = true;
  setMessage("Fetching from Reed and Adzuna — this can take a minute…", "progress");
  try {
    const res = await fetch("/api/fetch", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      setMessage(data.error || "Fetch failed.", "error");
    } else {
      const skipped = (data.warnings || []).length
        ? ` Note: ${data.warnings.join("; ")}.`
        : "";
      setMessage(
        `Fetched ${data.raw} postings, ${data.kept} passed filters — ${data.new} new, ${data.duplicates} duplicates suppressed.${skipped}`,
        "info",
      );
      await reload();
    }
  } catch (e) {
    setMessage("Fetch failed: " + e, "error");
  } finally {
    btn.disabled = false;
  }
}

el("btn-fetch").addEventListener("click", fetchListings);

loadLocationOptions();
reload();
