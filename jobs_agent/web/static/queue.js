const el = (id) => document.getElementById(id);

const STATUS_LABEL = {
  new: "New", shortlisted: "Shortlisted", drafted: "Drafted",
  approved: "Approved", submitted: "Submitted", rejected: "Rejected",
};
//: Stage order in the rail, and the order the rail is built in.
const STAGES = ["new", "shortlisted", "drafted", "approved", "submitted", "rejected"];
//: The forward path through the lifecycle, drawn as steps in the rail.
//: "rejected" sits apart from it.
const PIPELINE = ["new", "shortlisted", "drafted", "approved", "submitted"];
//: What each stage is for, under its heading.
const STAGE_HINT = {
  new: "Best match first. Shortlist the ones worth a letter.",
  shortlisted: "Prepare an application to draft a letter for each.",
  drafted: "Read each letter, edit it, then approve it.",
  approved: "Submit each one on the employer's site.",
  submitted: "",
  rejected: "Reset one to bring it back to New.",
};
//: Match scores (0-100) at or above this get the highlighter; below WEAK
//: they fade back.
const STRONG_MATCH = 70;
const WEAK_MATCH = 35;
const VISA_LABEL = {
  offered: ["Sponsors visas", "good"],
  not_offered: ["No sponsorship", "bad"],
  right_to_work_required: ["Needs right to work", "bad"],
};
const SOURCES = {
  reed: { label: "Reed", logo: "/static/logo-reed.png" },
  adzuna: { label: "Adzuna", logo: "/static/logo-adzuna.png" },
  jooble: { label: "Jooble", logo: "/static/logo-jooble.png" },
  careerjet: { label: "Careerjet", logo: "/static/logo-careerjet.png" },
  // A watched company's own careers site (see the Companies page).
  careers: { label: "the company's site", logo: "/static/logo-careers.svg" },
};
//: How much of a description the expanded row shows.
const DESCRIPTION_CHARS = 1400;
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
    <button class="stage${key === "rejected" ? " stage-aside" : ""}" role="tab"
            data-stage="${key}" aria-selected="${key === stage}">
      <span class="step" aria-hidden="true"></span>
      <span class="name">${STATUS_LABEL[key]}</span>
      <span class="n">${stats[key] || 0}</span>
    </button>`).join("");
}

async function loadStats() {
  const res = await fetch("/api/stats");
  renderStages(await res.json());
}

/* — a posting — */

/* Salaries as an accountant would jot them: £35k, £27.5k. */
function money(n) {
  return n >= 1000 ? `£${+(n / 1000).toFixed(1)}k` : `£${Math.round(n)}`;
}

/* Boards give hourly and daily rates as bare numbers; without a unit
 * "£16–£18" reads like a typo next to "£38k". */
function rateUnit(n) {
  if (n < 100) return `<span class="muted">/hr</span>`;
  if (n < 1000) return `<span class="muted">/day</span>`;
  return "";
}

function salaryCell(row) {
  if (!row.salary_min) return `<span class="muted">Not stated</span>`;
  const unit = rateUnit(row.salary_max || row.salary_min);
  return row.salary_max && row.salary_max !== row.salary_min
    ? `${money(row.salary_min)}<span class="muted">–</span>${money(row.salary_max)}${unit}`
    : `${money(row.salary_min)}${unit}`;
}

/* "posted" is a YYYY-MM-DD date with no time, so age is counted in days. */
function postedAgo(iso) {
  if (!iso) return `<span class="muted">Unknown</span>`;
  const days = Math.floor((Date.now() - new Date(iso + "T00:00:00").getTime()) / 86400000);
  if (!Number.isFinite(days) || days < 0) return escapeHtml(iso);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 14) return `${days} days ago`;
  return `${Math.round(days / 7)} weeks ago`;
}

/* Reed often gives a bare postcode ("SM26SP"); put its space back. */
function placeName(location) {
  if (!location) return `<span class="muted">Unknown</span>`;
  const postcode = location.trim().match(/^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$/i);
  return escapeHtml(postcode ? `${postcode[1]} ${postcode[2]}`.toUpperCase() : location);
}

function sourceMark(source) {
  const known = SOURCES[source];
  if (!known) return `<span class="muted">${escapeHtml(source || "")}</span>`;
  return `<img class="source-logo" src="${known.logo}" alt="${known.label}"
               title="Found on ${known.label}" width="20" height="20">`;
}

/* Scoring stores its reasons as one " | "-joined string of entries like
 * "title 'audit trainee' (+28)". Each becomes a ledger line: what it was,
 * and what it added or took away. */
function scoreLines(reasons) {
  return String(reasons || "").split(" | ").filter(Boolean).map((raw) => {
    const m = raw.match(/^(.*?)\s*\(([+-]\d+)\)\s*$/);
    const text = m ? m[1] : raw;
    const points = m ? Number(m[2]) : null;
    const title = text.match(/^title '(.*)'$/);
    const label = title ? `Title matches “${title[1]}”`
      : text === "domain terms" ? "Matches your field's keywords"
      : text === "contract/temp" ? "Contract or temp role"
      : text === "salary stated" ? "Salary is stated"
      : text.startsWith("no title match") ? "No target title, kept for its keywords"
      : text.replace(/^salary >= /, "Salary from £").replace(/^salary < /, "Salary under £")
            .replace(/^posted <= (\d+) days$/, "Posted in the last $1 days")
            .replace(/^posted > (\d+) days$/, "Posted over $1 days ago");
    return { label, points };
  });
}

function breakdown(row) {
  const lines = scoreLines(row.score_reasons).map(({ label, points }) => `
    <li class="${points == null ? "note" : points < 0 ? "minus" : "plus"}">
      <span>${escapeHtml(label)}</span>
      <span class="pts">${points == null ? "" : points > 0 ? `+${points}` : `−${-points}`}</span>
    </li>`).join("");
  const fit = row.analysis && row.analysis.fit != null ? row.analysis.fit : null;
  const blend = fit == null
    ? `<p class="blend">Not read by the AI yet, so the match is the keyword score rescaled to 100.</p>`
    : `<ul class="blend-lines">
         <li><span>Keyword score, 40%</span><span class="pts">${row.score}</span></li>
         <li><span>AI fit with your CV, 60%</span><span class="pts">${fit}</span></li>
       </ul>`;
  return `
    <aside class="breakdown" aria-label="How this posting was scored">
      <h3>Why it's a ${row.match} match</h3>
      <ul>${lines}</ul>
      <div class="subtotal"><span>Keyword score</span><span class="pts">${row.score}</span></div>
      ${blend}
      <div class="total"><span>Match</span><span class="pts">${row.match}</span></div>
    </aside>`;
}

function description(row) {
  const text = String(row.description || "").trim();
  const clipped = text.length > DESCRIPTION_CHARS
    ? text.slice(0, DESCRIPTION_CHARS).replace(/\s+\S*$/, "") + "…" : text;
  const source = (SOURCES[row.source] || {}).label || "the job board";
  return `
    <div class="desc">
      <h3>About the role</h3>
      ${clipped ? `<p>${escapeHtml(clipped)}</p>` : `<p class="muted">No description was given.</p>`}
      ${safeUrl(row.url) ? `<a class="listing-link" href="${escapeHtml(safeUrl(row.url))}" target="_blank"
                     rel="noopener">Read the full listing on ${escapeHtml(source)}</a>` : ""}
    </div>`;
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

function letterBox(row, open) {
  return `
    <details class="letter-box"${open ? " open" : ""}>
      <summary>
        <span class="when-closed btn btn-secondary btn-sm">Read the letter</span>
        <span class="when-open letter-title">Cover letter</span>
        <span class="when-open letter-when">${escapeHtml(editedAgo(row.updated))}</span>
      </summary>
      <div class="letter-body">
        <textarea class="letter-text" data-letter-key="${escapeHtml(row.key)}"
                  aria-label="Cover letter">${escapeHtml(row.letter || "")}</textarea>
        <input class="input" type="text" data-feedback-key="${escapeHtml(row.key)}"
               placeholder="What should change? e.g. open with the audit internship"
               aria-label="Redraft feedback">
        <div class="letter-actions">
          <button class="btn btn-secondary btn-sm" data-redraft-key="${escapeHtml(row.key)}">Redraft with feedback</button>
          <button class="btn btn-ghost btn-sm" data-save-key="${escapeHtml(row.key)}">Save edits</button>
          <span class="saved-note" data-saved-for="${escapeHtml(row.key)}" hidden>Saved.</span>
        </div>
      </div>
    </details>`;
}

/* Small marks on the row from the AI's reading: the facts worth seeing
 * without opening anything. */
function chips(a) {
  if (!a) return "";
  const out = [];
  const visa = Object.hasOwn(VISA_LABEL, a.visa) ? VISA_LABEL[a.visa] : null;
  if (visa) out.push(`<span class="chip-mini ${visa[1]}">${visa[0]}</span>`);
  if (a.graduate_scheme) out.push(`<span class="chip-mini">Grad scheme</span>`);
  if (a.study_support) out.push(`<span class="chip-mini good">Study support</span>`);
  if (a.deadline) out.push(`<span class="chip-mini">Closes ${escapeHtml(shortDate(a.deadline))}</span>`);
  if (a.red_flags && a.red_flags.length) out.push(`<span class="chip-mini bad">Red flag</span>`);
  return out.join("");
}

function shortDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return Number.isFinite(d.getTime())
    ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : iso;
}

function bullets(items, cls) {
  return (items || []).map((t) => `<li class="${cls}">${escapeHtml(t)}</li>`).join("");
}

function aiReading(a) {
  if (!a) return "";
  const years = a.min_years == null ? "" : a.min_years === 0
    ? "No experience required" : `Asks for ${a.min_years}+ year${a.min_years === 1 ? "" : "s"}`;
  const facts = [
    a.seniority && a.seniority !== "unknown" ? `${a.seniority[0].toUpperCase()}${a.seniority.slice(1)} level` : "",
    years,
    (a.qualifications || []).length ? `Needs ${a.qualifications.join(", ")}` : "",
  ].filter(Boolean);
  const visa = a.visa_evidence
    ? `<blockquote class="visa-quote"><b>${escapeHtml((Object.hasOwn(VISA_LABEL, a.visa) ? VISA_LABEL[a.visa] : ["Visa"])[0])}:</b>
         “${escapeHtml(a.visa_evidence)}”</blockquote>` : "";
  return `
    <section class="ai-reading" aria-label="The AI's reading of this posting">
      <h3>The AI's reading</h3>
      ${a.summary ? `<p class="ai-summary">${escapeHtml(a.summary)}</p>` : ""}
      ${facts.length ? `<p class="ai-facts">${facts.map(escapeHtml).join(". ")}.</p>` : ""}
      <ul class="ai-points">
        ${bullets(a.fit_reasons, "fits")}${bullets(a.gaps, "gap")}${bullets(a.red_flags, "flag")}
      </ul>
      ${visa}
    </section>`;
}

/* Listing links come from the job boards' feeds: only web addresses are
 * linked, never javascript: or data: ones. */
function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : "";
}

function card(row, open) {
  const status = row.status;
  const key = escapeHtml(row.key);
  const url = escapeHtml(safeUrl(row.url));
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
    const kind = lead === next ? "btn-primary" : next === "rejected" ? "btn-danger" : "btn-secondary";
    actions.push(`<button class="btn ${kind}" data-key="${key}" data-status="${next}">${label}</button>`);
  }

  // In New, triage happens from the row itself: tick to shortlist, cross to
  // set aside, without opening anything.
  const quick = status === "new" ? `
      <span class="quick">
        <button class="btn btn-icon quick-yes" data-key="${key}" data-status="shortlisted"
                aria-label="Shortlist ${escapeHtml(row.title)}" title="Shortlist">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="btn btn-icon quick-no" data-key="${key}" data-status="rejected"
                aria-label="Reject ${escapeHtml(row.title)}" title="Reject">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
      </span>` : `<span class="quick"></span>`;

  const strength = row.match >= STRONG_MATCH ? " strong" : row.match < WEAK_MATCH ? " weak" : "";
  const kind = row.contract_type && row.contract_type !== "permanent"
    ? `<span class="tag tag-neutral">${escapeHtml(row.contract_type[0].toUpperCase() + row.contract_type.slice(1))}</span>` : "";
  const hasLetter = HAS_LETTER_BOX.has(status);
  return `
    <article class="job${open ? " is-open" : ""}" data-row="${key}">
      <div class="job-row" data-toggle="${key}">
        <span class="score${strength}" title="${row.analysis ? "Keywords and AI fit" : "Keywords only, not yet read by the AI"}"><span>${row.match}</span></span>
        <div class="role">
          <button class="title" data-toggle="${key}" aria-expanded="${open}">${escapeHtml(row.title)}</button>
          <span class="employer"><span class="employer-name">${escapeHtml(row.employer || "Employer not named")}</span>${kind}${chips(row.analysis)}</span>
        </div>
        <span class="loc">${placeName(row.location)}</span>
        <span class="pay">${salaryCell(row)}</span>
        <span class="age">${postedAgo(row.posted)}</span>
        <span class="src">${sourceMark(row.source)}</span>
        ${quick}
      </div>
      <div class="job-detail">
        ${hasLetter ? letterBox(row, true) : ""}
        <div class="detail-grid"><div>${aiReading(row.analysis)}${description(row)}</div>${breakdown(row)}</div>
        <div class="job-actions">
          ${actions.join("")}
          <button class="btn btn-danger del" data-delete-key="${key}">Delete</button>
        </div>
      </div>
    </article>`;
}

/* — the empty states —
 * Nothing fetched yet is a different situation from a filter that matched
 * nothing, and only the first one deserves the explanation.
 */
function emptyState() {
  if (totalPostings) {
    return `<p class="empty-line">Nothing in ${STATUS_LABEL[stage]} matches these filters.</p>`;
  }
  return `
    <div class="empty-state">
      <img src="/static/logo-mark.svg" alt="" width="48" height="48">
      <h2>Your queue is empty</h2>
      <p>Fetching searches Reed and Adzuna for your target titles, scores every
         posting against your profile, and drops duplicates. It takes under a minute.</p>
      <div class="row">
        <button class="btn btn-primary" data-empty-fetch>Fetch new listings</button>
        <a class="btn btn-ghost" href="/documents#scoring">Check your scoring profile first</a>
      </div>
    </div>`;
}

//: Bumped by each loadQueue call; a response for an older call is dropped,
//: so a slow reply for the previous stage or filters can't overwrite this one.
let queueRequest = 0;
//: 1-based page of the current stage; back to 1 whenever the stage or a
//: filter changes, so a narrower result never opens on an empty page.
let page = 1;

/* Filters that are narrowing the list right now (page size isn't one). */
function activeFilters() {
  const on = [];
  if (el("f-location").value.trim()) on.push("location");
  if (el("f-min-salary").value.trim()) on.push("min salary");
  if (el("f-max-salary").value.trim()) on.push("max salary");
  if (el("f-contract-type").value) on.push("job type");
  if (Number(el("f-min-score").value || 0) > 0) on.push("minimum match");
  return on.concat(Object.keys(aiFilterParams()));
}

function showFilterSummary() {
  const n = activeFilters().length;
  el("filters-on").textContent = n ? `(${n} on)` : "";
  el("btn-clear-filters").hidden = !n;
}

function filtersChanged() {
  page = 1;
  setMessage("");
  showFilterSummary();
  loadQueue();
}

function showPager(total, limit) {
  const pages = Math.max(1, Math.ceil(total / limit));
  el("pager").hidden = pages <= 1;
  el("page-text").textContent = `Page ${page} of ${pages}`;
  el("page-prev").disabled = page <= 1;
  el("page-next").disabled = page >= pages;
}

async function loadQueue() {
  const mine = ++queueRequest;
  const limit = Math.max(1, Number(el("f-limit").value) || 50);
  const minScore = el("f-min-score").value || 0;
  const location = el("f-location").value.trim();
  const minSalary = el("f-min-salary").value.trim();
  const maxSalary = el("f-max-salary").value.trim();
  const contractType = el("f-contract-type").value;
  const ai = aiFilterParams();
  el("stage-title").textContent = STATUS_LABEL[stage];
  el("stage-count").textContent = STAGE_HINT[stage];
  el("results").innerHTML = `<p class="empty-line">Loading…</p>`;

  const params = new URLSearchParams({
    status: stage, limit, min_score: minScore, offset: (page - 1) * limit,
  });
  if (location) params.set("location", location);
  if (minSalary) params.set("min_salary", minSalary);
  if (maxSalary) params.set("max_salary", maxSalary);
  if (contractType) params.set("contract_type", contractType);
  for (const [name, value] of Object.entries(ai)) params.set(name, value);
  const res = await fetch(`/api/queue?${params}`);
  const { rows = [], total = 0 } = await res.json().catch(() => ({}));
  if (mine !== queueRequest) return;

  // Acting on the last row of the last page (shortlisting it, say) can
  // leave that page empty: step back to the new last page.
  if (!rows.length && total && page > 1) {
    page = Math.ceil(total / limit);
    return loadQueue();
  }

  // The rail counts the whole stage; with filters on, say how many of those
  // made it through, or the two numbers look like a bug.
  const [one, many] = STAGE_COUNT[stage];
  const stageTotal = stageTotals[stage] || 0;
  const filtered = activeFilters().length > 0;
  const noun = total === 1 ? one : many;  // "posting scored and waiting", …
  const counted = filtered
    ? `${total.toLocaleString("en-GB")} of ${stageTotal.toLocaleString("en-GB")} match your filters.`
    : `${total.toLocaleString("en-GB")} ${noun}.`;
  const first = (page - 1) * limit + 1;
  const range = total > limit && rows.length
    ? ` Showing ${first}–${first + rows.length - 1}.` : "";
  el("stage-count").textContent = `${counted}${range} ${STAGE_HINT[stage]}`.trim();
  showPager(total, limit);

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
  el("results").querySelectorAll(".job.is-open .letter-box[open] .letter-text").forEach(autoExpand);
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
  page = 1;
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
  if (ev.target.open) ev.target.querySelectorAll(".letter-text").forEach(autoExpand);
}, true);

/* A row opens and closes from its title, or from anywhere on the row that
 * isn't its own control or link. */
function toggleRow(job) {
  const open = job.classList.toggle("is-open");
  job.querySelector(".title").setAttribute("aria-expanded", String(open));
  if (open) job.querySelectorAll(".letter-box[open] .letter-text").forEach(autoExpand);
}

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

  const toggle = ev.target.closest("[data-toggle]");
  const control = ev.target.closest("button:not(.title), a, input, textarea, summary");
  if (toggle && !control) {
    toggleRow(toggle.closest(".job"));
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
  if (ev.key === "Enter") filtersChanged();
});
// Picking a suggestion from the datalist fires "change" without an Enter.
el("f-location").addEventListener("change", filtersChanged);

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
el("f-limit").addEventListener("change", filtersChanged);
el("f-min-score").addEventListener("change", filtersChanged);
el("f-min-salary").addEventListener("change", filtersChanged);
el("f-max-salary").addEventListener("change", filtersChanged);
el("f-contract-type").addEventListener("change", filtersChanged);

/* — AI filters, shared by the queue request and the search box — */

const AI_FIELDS = {
  visa: "f-visa", level: "f-level", max_years: "f-max-years",
};
const AI_FLAGS = {
  graduate_scheme: "f-graduate-scheme", study_support: "f-study-support",
  hide_red_flags: "f-hide-red-flags",
};

function aiFilterParams() {
  const out = {};
  for (const [name, id] of Object.entries(AI_FIELDS)) {
    const value = el(id).value.trim();
    if (value) out[name] = value;
  }
  for (const [name, id] of Object.entries(AI_FLAGS)) if (el(id).checked) out[name] = "1";
  return out;
}

for (const id of [...Object.values(AI_FIELDS), ...Object.values(AI_FLAGS)]) {
  el(id).addEventListener("change", filtersChanged);
}

/* — plain-English search: the AI fills in the filters, visibly — */

const SEARCH_CONTROLS = {
  location: "f-location", min_salary: "f-min-salary", max_salary: "f-max-salary",
  contract_type: "f-contract-type", min_score: "f-min-score", ...AI_FIELDS,
};

function applyFilters(filters = {}) {
  filters = filters || {};
  for (const [name, id] of Object.entries(SEARCH_CONTROLS)) {
    el(id).value = filters[name] != null ? filters[name] : (name === "min_score" ? "0" : "");
  }
  for (const [name, id] of Object.entries(AI_FLAGS)) el(id).checked = filters[name] === true;
}

el("search-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const query = el("search-input").value.trim();
  if (!query) return;
  const btn = el("btn-search");
  btn.disabled = true;
  setMessage("Reading your search…", "progress");
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage(data.error || "Couldn't read that search.", "error");
    applyFilters(data.filters);
    page = 1;
    showFilterSummary();
    const note = el("search-note");
    note.hidden = false;
    note.innerHTML = `${escapeHtml(data.note || "Filters set from your search.")}
      The filters on the left now match it. <button class="btn btn-ghost btn-sm" id="btn-clear-search" type="button">Clear</button>`;
    setMessage("");
    await loadQueue();
  } finally {
    btn.disabled = false;
  }
});

el("search-note").addEventListener("click", (ev) => {
  if (!ev.target.closest("#btn-clear-search")) return;
  clearFilters();
});

/* — AI analysis, one batch per request, with progress — */

let analysing = false;

function showAnalysis({ remaining, analysed, failed }) {
  const bar = el("analysis-bar");
  bar.hidden = !(remaining || analysed || failed);
  const parts = [`${analysed} posting${analysed === 1 ? "" : "s"} read by the AI`];
  if (remaining) parts.push(`${remaining} waiting`);
  if (failed) parts.push(`${failed} it couldn't read`);
  el("analysis-text").textContent = parts.join(", ") + ".";
  el("btn-analyse").hidden = !remaining || analysing;
  el("btn-retry-analysis").hidden = !failed || !!remaining || analysing;
  const progress = el("analysis-progress");
  progress.hidden = !analysing;
  const total = remaining + analysed + failed;
  progress.querySelector("span").style.width = total ? `${((analysed + failed) / total) * 100}%` : "0";
}

async function loadAnalysisStatus() {
  const res = await fetch("/api/analysis");
  if (res.ok) showAnalysis(await res.json());
}

async function runAnalysis() {
  if (analysing) return;
  analysing = true;
  el("btn-analyse").hidden = true;
  // Stop when two batches in a row leave the same number waiting: the
  // server gives up on a posting after a couple of tries, so real progress
  // always shrinks it.
  let last = Infinity;
  let stalled = 0;
  try {
    for (;;) {
      const res = await fetch("/api/analyse", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) break;   // another tab is already on it
      if (!res.ok) {
        setMessage(data.error || "The AI couldn't analyse postings.", "error");
        break;
      }
      await loadAnalysisStatus();
      if (!data.remaining) break;
      stalled = data.remaining < last ? 0 : stalled + 1;
      last = data.remaining;
      if (stalled >= 2) break;
    }
  } catch (e) {
    setMessage("Lost contact with the server while analysing. Try again.", "error");
  } finally {
    analysing = false;
    await loadAnalysisStatus().catch(() => {});
    await loadQueue();
  }
}

el("btn-analyse").addEventListener("click", runAnalysis);
el("btn-retry-analysis").addEventListener("click", async () => {
  const res = await fetch("/api/analyse/retry", { method: "POST" });
  if (res.ok) runAnalysis();
});

/* — company careers sites, one per request (each can take a while) —
 * Runs after the job boards, before AI analysis, so the AI reads what the
 * companies turned up too. ``summary`` is the fetch's own result, kept on
 * screen alongside the progress. Returns a sentence about what was found,
 * or "" when no company was due.
 */
async function checkCompanies(summary) {
  const listed = await fetch("/api/companies").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!listed || !listed.due) return "";
  const total = listed.due;
  let next = listed.next_due;
  let done = 0;
  let found = 0;
  let fresh = 0;
  let failed = 0;
  let stopped = "";
  let last = Infinity;
  while (next) {
    setMessage(`${summary} Checking company sites: ${next.name} (${done + 1} of ${total})…`, "progress");
    const res = await fetch("/api/companies/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).catch(() => null);
    if (!res || !res.ok) {
      // 409: another tab is on it. 429: today's check budget is spent —
      // say so, or the user wonders why some firms never update.
      if (res && res.status === 429) {
        stopped = (await res.json().catch(() => ({}))).error || "Today's company checks are used up.";
      }
      break;
    }
    const data = await res.json().catch(() => ({}));
    if (!data.company) break;
    done += 1;
    if (data.result.error) failed += 1;
    found += data.result.kept;
    fresh += data.result.new;
    // Every check moves a company out of "due"; if the count doesn't fall,
    // something's wrong and looping would hammer the server.
    if (data.remaining >= last) break;
    last = data.remaining;
    next = data.remaining ? data.next_due : null;
  }
  if (!done) return stopped;
  const parts = [`Checked ${done} company site${done === 1 ? "" : "s"}: ${found} matched, ${fresh} new`];
  if (failed) parts.push(`${failed} couldn't be read (see Companies)`);
  return parts.join("; ") + "." + (stopped ? ` ${stopped}` : "");
}

function clearFilters() {
  applyFilters({});
  el("search-input").value = "";
  el("search-note").hidden = true;
  filtersChanged();
}

el("btn-clear-filters").addEventListener("click", clearFilters);

function goToPage(next) {
  page = next;
  loadQueue();
  document.querySelector(".ledger").scrollIntoView({ block: "start", behavior: "smooth" });
}

el("page-prev").addEventListener("click", () => { if (page > 1) goToPage(page - 1); });
el("page-next").addEventListener("click", () => goToPage(page + 1));

async function fetchListings() {
  const btn = el("btn-fetch");
  btn.disabled = true;
  setMessage("Searching the job boards for new postings…", "progress");
  try {
    const res = await fetch("/api/fetch", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    // A board failure (no keys, an outage) shouldn't stop the company
    // sites being read: they don't need any board.
    let summary;
    if (res.ok) {
      const skipped = (data.warnings || []).length
        ? ` Note: ${data.warnings.join("; ")}.`
        : "";
      summary = `Found ${data.raw} postings. ${data.kept} matched your profile: ${data.new} new, ${data.duplicates} already seen.${skipped}`;
      setMessage(summary, "info");
      await reload();
    } else {
      summary = `Job boards: ${data.error || "the search failed"}.`;
    }
    const companies = await checkCompanies(summary);
    setMessage(companies ? `${summary} ${companies}` : summary, res.ok || companies ? "info" : "error");
    await reload();
    runAnalysis();
  } catch (e) {
    setMessage("Fetch failed: " + e, "error");
  } finally {
    btn.disabled = false;
  }
}

el("btn-fetch").addEventListener("click", fetchListings);

loadLocationOptions();
showFilterSummary();
reload();
loadAnalysisStatus();
