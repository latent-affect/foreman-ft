"use strict";

// Single escaping helper -- every piece of agent-generated content goes through this
// before insertion into the DOM. No other innerHTML assignment in this file bypasses it.
function esc(value) {
  const div = document.createElement("div");
  div.textContent = value === null || value === undefined ? "" : String(value);
  return div.innerHTML;
}

// TESS-44: severity/priority are stored as a real, sortable 0-4 int (0=Highest,
// 4=Lowest) -- displayed as S0-S4 / P0-P4, Jira-style. prefix is "S" or "P".
function formatLevel(prefix, value) {
  return value === null || value === undefined ? "" : prefix + value;
}

const statusEl = document.getElementById("connection-status");

function setStatus(state, label) {
  statusEl.className = "status status-" + state;
  statusEl.textContent = label;
}

// ---- navigation -------------------------------------------------------

const views = {
  open: document.getElementById("view-open"),
  diff: document.getElementById("view-diff"),
  docs: document.getElementById("view-docs"),
  sql: document.getElementById("view-sql"),
};
const navButtons = {
  open: document.getElementById("nav-open"),
  diff: document.getElementById("nav-diff"),
  docs: document.getElementById("nav-docs"),
  sql: document.getElementById("nav-sql"),
};

function showView(name) {
  for (const key of Object.keys(views)) {
    views[key].hidden = key !== name;
    navButtons[key].classList.toggle("active", key === name);
  }
}

navButtons.open.addEventListener("click", () => showView("open"));
navButtons.diff.addEventListener("click", () => showView("diff"));
navButtons.docs.addEventListener("click", () => showView("docs"));
navButtons.sql.addEventListener("click", () => showView("sql"));

// ---- open bugs / todos -------------------------------------------------

// Aesthetic-redesign addition: tickets are fetched once per refresh and cached here,
// then re-rendered client-side on every filter/group toggle -- no extra network round
// trip just to change how the same data is displayed. ticket_id itself always carries
// its project prefix ("TESS-52", "AREM-1"), so grouping/filtering reads that instead of
// depending on a project_prefix field being present on the list response.
let allTickets = [];
let groupByProject = true;
let projectFilter = "";
let selectedTicketId = null;

function projectPrefixOf(ticketId) {
  const idx = String(ticketId).indexOf("-");
  return idx === -1 ? String(ticketId) : String(ticketId).slice(0, idx);
}

function populateProjectFilter(tickets) {
  const selectEl = document.getElementById("ticket-project-filter");
  const prefixes = Array.from(new Set(tickets.map((t) => projectPrefixOf(t.ticket_id)))).sort();
  const previous = selectEl.value;
  selectEl.innerHTML = '<option value="">All projects</option>';
  for (const prefix of prefixes) {
    const opt = document.createElement("option");
    opt.value = prefix;
    opt.textContent = prefix;
    selectEl.appendChild(opt);
  }
  if (prefixes.includes(previous)) selectEl.value = previous;
}

function buildTicketLi(ticket) {
  const li = document.createElement("li");
  li.dataset.ticketId = ticket.ticket_id;
  li.tabIndex = 0;
  li.classList.toggle("selected", ticket.ticket_id === selectedTicketId);
  li.innerHTML =
    '<strong>' + esc(ticket.ticket_id) + '</strong> ' +
    '<span class="ticket-type">' + esc(ticket.type) + '</span> ' +
    '<span class="ticket-status">' + esc(ticket.status) + '</span>' +
    (ticket.summary ? '<div class="ticket-summary">' + esc(ticket.summary) + '</div>' : '');
  const reporterLine = document.createElement("div");
  reporterLine.textContent = "reporter: " + (ticket.reporter || "");
  li.appendChild(reporterLine);
  li.addEventListener("click", () => {
    selectedTicketId = ticket.ticket_id;
    document.querySelectorAll("#ticket-list li.selected").forEach((el) => el.classList.remove("selected"));
    li.classList.add("selected");
    loadTicketDetail(ticket.ticket_id);
  });
  return li;
}

function renderTicketList() {
  const listEl = document.getElementById("ticket-list");
  const countBadge = document.getElementById("ticket-count-badge");
  listEl.innerHTML = "";

  const filtered = projectFilter
    ? allTickets.filter((t) => projectPrefixOf(t.ticket_id) === projectFilter)
    : allTickets;

  countBadge.textContent = filtered.length + (filtered.length === 1 ? " ticket" : " tickets");

  if (!groupByProject || projectFilter) {
    for (const ticket of filtered) listEl.appendChild(buildTicketLi(ticket));
    return;
  }

  const groups = new Map();
  for (const ticket of filtered) {
    const prefix = projectPrefixOf(ticket.ticket_id);
    if (!groups.has(prefix)) groups.set(prefix, []);
    groups.get(prefix).push(ticket);
  }
  for (const prefix of Array.from(groups.keys()).sort()) {
    const header = document.createElement("li");
    header.className = "ticket-group-header";
    header.innerHTML =
      '<span class="prefix-dot"></span>' + esc(prefix) +
      '<span class="count">' + groups.get(prefix).length + '</span>';
    listEl.appendChild(header);
    for (const ticket of groups.get(prefix)) listEl.appendChild(buildTicketLi(ticket));
  }
}

document.getElementById("ticket-project-filter").addEventListener("change", (e) => {
  projectFilter = e.target.value;
  renderTicketList();
});

document.getElementById("ticket-group-toggle").addEventListener("click", (e) => {
  groupByProject = !groupByProject;
  e.target.textContent = "Group by project: " + (groupByProject ? "On" : "Off");
  renderTicketList();
});

// Real registered projects, fetched once and cached -- backs both the Assignee select
// (team handoff, see assigneeDd) and the new-ticket form's Project select. Re-fetched by
// name (loadKnownProjects()) rather than reused from allTickets' derived prefixes, since a
// registered project with zero tickets so far (a brand-new team, or one nobody's filed
// against yet) still needs to be assignable/selectable.
let knownProjects = [];

async function loadKnownProjects() {
  try {
    const resp = await fetch("/projects");
    if (!resp.ok) throw new Error("bad response " + resp.status);
    const data = await resp.json();
    knownProjects = data.projects || [];
    populateNewTicketProjectSelect();
  } catch (err) {
    console.error("loadKnownProjects failed:", err.message);
  }
}

function populateNewTicketProjectSelect() {
  const selectEl = document.getElementById("new-ticket-project");
  const previous = selectEl.value;
  selectEl.innerHTML = "";
  for (const p of knownProjects) {
    const opt = document.createElement("option");
    opt.value = p.prefix;
    opt.textContent = p.prefix + " (" + p.codename + ")";
    selectEl.appendChild(opt);
  }
  if (Array.from(selectEl.options).some((o) => o.value === previous)) selectEl.value = previous;
}

// Assignee is now a real, editable field (was locked at create-time) -- Jon's direct call:
// a ticket's real working ownership can move between teams (originates in FORE, handed to
// TESS to actually execute), tracked as a project prefix rather than a person's name so the
// CLI's own comment-provenance check (assignee_provenance_error, tessera/api/cli.py) can
// verify a comment is actually coming from the team it's assigned to. Rendered as a select
// over real registered prefixes (not free text) so a typo can't silently create an
// unmatchable assignee value.
function assigneeDd(ticketId, currentAssignee) {
  const dd = document.createElement("dd");
  const select = document.createElement("select");
  select.className = "field-edit-select";
  const blankOpt = document.createElement("option");
  blankOpt.value = "";
  blankOpt.textContent = "(unassigned)";
  select.appendChild(blankOpt);
  for (const p of knownProjects) {
    const opt = document.createElement("option");
    opt.value = p.prefix;
    opt.textContent = p.prefix;
    select.appendChild(opt);
  }
  // currentAssignee might be a legacy free-text value (a person's name, or a prefix that's
  // since been deregistered) that isn't in knownProjects -- add it as its own option rather
  // than silently discarding it via select.value failing to match any <option>.
  if (currentAssignee && !knownProjects.some((p) => p.prefix === currentAssignee)) {
    const legacyOpt = document.createElement("option");
    legacyOpt.value = currentAssignee;
    legacyOpt.textContent = currentAssignee + " (not a registered project prefix)";
    select.appendChild(legacyOpt);
  }
  select.value = currentAssignee || "";
  const statusSpan = document.createElement("span");
  statusSpan.className = "field-save-status";
  select.addEventListener("change", async () => {
    statusSpan.textContent = "saving...";
    const result = await patchTicketField(ticketId, "assignee", { assignee: select.value || null });
    statusSpan.textContent = result.ok ? "saved" : "save failed: " + result.error;
    if (!result.ok) select.value = currentAssignee || "";
  });
  dd.appendChild(select);
  dd.appendChild(statusSpan);
  return dd;
}

document.getElementById("new-ticket-toggle").addEventListener("click", () => {
  const form = document.getElementById("new-ticket-form");
  form.hidden = !form.hidden;
  if (!form.hidden) {
    const actorField = document.getElementById("new-ticket-actor");
    if (!actorField.value) actorField.value = actorInput.value;
  }
});

document.getElementById("new-ticket-create").addEventListener("click", async () => {
  const statusSpan = document.getElementById("new-ticket-status");
  const summary = document.getElementById("new-ticket-summary").value.trim();
  const reporter = document.getElementById("new-ticket-reporter").value.trim();
  const actor = document.getElementById("new-ticket-actor").value.trim();
  if (!summary || !reporter || !actor) {
    statusSpan.textContent = "project/type are always set; summary, reporter, and actor are required";
    return;
  }
  const priorityVal = document.getElementById("new-ticket-priority").value;
  const severityVal = document.getElementById("new-ticket-severity").value;
  statusSpan.textContent = "creating...";
  try {
    const resp = await fetch("/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: document.getElementById("new-ticket-project").value,
        ticket_type: document.getElementById("new-ticket-type").value,
        reporter: reporter,
        actor: actor,
        summary: summary,
        description: document.getElementById("new-ticket-description").value,
        priority: priorityVal === "" ? null : Number(priorityVal),
        severity: severityVal === "" ? null : Number(severityVal),
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      statusSpan.textContent = "create failed: " + (data.error || "error");
      return;
    }
    document.getElementById("new-ticket-summary").value = "";
    document.getElementById("new-ticket-description").value = "";
    statusSpan.textContent = "created " + data.ticket_id;
    await loadTicketList();
    selectedTicketId = data.ticket_id;
    renderTicketList();
    await loadTicketDetail(data.ticket_id);
  } catch (err) {
    statusSpan.textContent = "create failed: " + err.message;
  }
});

async function loadTicketList() {
  const listEl = document.getElementById("ticket-list");
  try {
    const resp = await fetch("/tickets");
    if (!resp.ok) throw new Error("bad response " + resp.status);
    const data = await resp.json();
    allTickets = data.tickets;
    populateProjectFilter(allTickets);
    renderTicketList();
  } catch (err) {
    listEl.innerHTML = "";
    setStatus("disconnected", "load failed: " + err.message);
  }
}

// ---- ticket detail (click-through from the list) -----------------------

let currentDetailTicketId = null;

async function loadTicketDetail(ticketId, resetStatus = true) {
  const panel = document.getElementById("ticket-detail");
  const statusEl = document.getElementById("ticket-detail-status");
  // TESS-65: previously any non-ok status (404, 400, 500 -- a real ticket that doesn't
  // exist, a genuine backend error, anything) just hid the panel with zero message,
  // indistinguishable from "no ticket selected." Now the real server error is shown.
  const resp = await fetch("/tickets/" + encodeURIComponent(ticketId));
  if (!resp.ok) {
    panel.hidden = true;
    let message = "error";
    try {
      const errBody = await resp.json();
      message = errBody.error || message;
    } catch (err) {
      // response body wasn't JSON -- fall through with the generic "error" text rather
      // than let a second exception here mask the real (already-known) failure
    }
    statusEl.textContent = "could not load ticket " + ticketId + ": " + message;
    return;
  }
  statusEl.textContent = "";
  const ticket = await resp.json();
  currentDetailTicketId = ticket.ticket_id;
  panel.hidden = false;

  document.getElementById("detail-id").textContent =
    ticket.ticket_id + (ticket.summary ? " — " + ticket.summary : "");

  document.getElementById("detail-summary-input").value = ticket.summary || "";
  document.getElementById("detail-summary-status").textContent = "";
  document.getElementById("detail-description-input").value = ticket.description || "";
  document.getElementById("detail-description-status").textContent = "";

  const fieldsEl = document.getElementById("detail-fields");
  fieldsEl.innerHTML = "";

  // TESS-98's own FIRST_CLASS_TICKET_FIELDS (store.py) is the real, authoritative source
  // of which fields are editable post-creation and which are locked -- mirrored here, not
  // re-derived, so this UI never offers an edit control the backend will just reject.
  // Locked fields render with a title tooltip carrying the exact reason from store.py
  // rather than silently looking the same as an editable one.
  const LOCKED_FIELD_REASON = {
    type: "set at create time; not updatable",
    assignee: "set at create time; not updatable",
    tier: "set at create time; not updatable",
    reporter: "set at create time; not updatable",
  };

  function lockedDd(value, reason) {
    const dd = document.createElement("dd");
    dd.textContent = value === "" || value === null || value === undefined ? "(none)" : String(value);
    dd.className = "field-locked";
    dd.title = reason;
    return dd;
  }

  function appendFieldRow(label, ddEl) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    fieldsEl.appendChild(dt);
    fieldsEl.appendChild(ddEl);
  }

  // Status -- select over every WORKFLOW_TRANSITIONS state (schema.py); the backend, not
  // this list, is what actually enforces which transitions are legal from the ticket's
  // current status, so an illegal pick surfaces the real server error rather than being
  // silently pre-filtered by a client-side copy of that state machine that could drift.
  const statusDd = document.createElement("dd");
  const statusSelect = document.createElement("select");
  statusSelect.className = "field-edit-select";
  for (const s of ["open", "in_progress", "in_review", "closed"]) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    if (s === ticket.status) opt.selected = true;
    statusSelect.appendChild(opt);
  }
  const statusStatus = document.createElement("span");
  statusStatus.className = "field-save-status";
  statusSelect.addEventListener("change", async () => {
    statusStatus.textContent = "saving...";
    const result = await patchTicketField(ticket.ticket_id, "transition", { status: statusSelect.value });
    if (result.ok) {
      statusStatus.textContent = "saved";
      await loadTicketDetail(ticket.ticket_id, false);
    } else {
      statusSelect.value = ticket.status; // revert the select to the last known-good value
      statusStatus.textContent = "save failed: " + result.error;
    }
  });
  statusDd.appendChild(statusSelect);
  statusDd.appendChild(statusStatus);
  appendFieldRow("Status", statusDd);

  appendFieldRow("Type", lockedDd(ticket.type, LOCKED_FIELD_REASON.type));
  appendFieldRow("Tier", lockedDd(ticket.tier, LOCKED_FIELD_REASON.tier));

  // Priority/severity share one shape -- 0-4 or "(none)" -- so one builder renders both,
  // matching set_priority_like's own "share one handler, share one validation rule" (http_api.py).
  function priorityLikeDd(field, prefix, currentValue) {
    const dd = document.createElement("dd");
    const select = document.createElement("select");
    select.className = "field-edit-select";
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "(none)";
    select.appendChild(noneOpt);
    for (let i = 0; i <= 4; i++) {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = prefix + i;
      select.appendChild(opt);
    }
    select.value = currentValue === null || currentValue === undefined ? "" : String(currentValue);
    const statusSpan = document.createElement("span");
    statusSpan.className = "field-save-status";
    select.addEventListener("change", async () => {
      statusSpan.textContent = "saving...";
      const value = select.value === "" ? null : Number(select.value);
      const result = await patchTicketField(ticket.ticket_id, field, { value: value });
      statusSpan.textContent = result.ok ? "saved" : "save failed: " + result.error;
      if (!result.ok) select.value = currentValue === null || currentValue === undefined ? "" : String(currentValue);
    });
    dd.appendChild(select);
    dd.appendChild(statusSpan);
    return dd;
  }
  appendFieldRow("Priority", priorityLikeDd("priority", "P", ticket.priority));
  appendFieldRow("Assignee", assigneeDd(ticket.ticket_id, ticket.assignee));
  appendFieldRow("Project", lockedDd(ticket.project_prefix,
    "reassign-project moves this ticket to a new ticket id -- not offered as an inline edit here"));
  appendFieldRow("Reporter", lockedDd(ticket.reporter, LOCKED_FIELD_REASON.reporter));
  appendFieldRow("Severity", priorityLikeDd("severity", "S", ticket.severity));

  const refDocsListEl = document.getElementById("detail-reference-docs-list");
  refDocsListEl.innerHTML = "";
  const currentRefDocs = ticket.reference_docs || [];
  for (const path of currentRefDocs) {
    const li = document.createElement("li");
    li.textContent = path + " ";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "remove";
    removeBtn.addEventListener("click", () => saveReferenceDocs(ticket.ticket_id,
      currentRefDocs.filter((p) => p !== path)));
    li.appendChild(removeBtn);
    refDocsListEl.appendChild(li);
  }

  // repro_steps/environment are free text, often multi-line/long -- rendered as their
  // own labeled blocks rather than crammed into the compact dt/dd field grid above,
  // same reasoning as watchers/criteria already getting their own divs. Previously not
  // rendered at all despite existing in the data (found via user report, not by
  // inspection -- real ticket data already had these populated, TESS-33 in particular).
  const reproEl = document.getElementById("detail-repro-steps");
  reproEl.innerHTML = ticket.repro_steps
    ? "<strong>Repro steps:</strong><p>" + esc(ticket.repro_steps) + "</p>"
    : "<strong>Repro steps:</strong> (none)";
  const envEl = document.getElementById("detail-environment");
  envEl.innerHTML = ticket.environment
    ? "<strong>Environment:</strong><p>" + esc(ticket.environment) + "</p>"
    : "<strong>Environment:</strong> (none)";

  renderNeighborhood("detail-blocked-by", "Blocked by", ticket.blocked_by);
  renderNeighborhood("detail-blocks", "Blocks", ticket.blocks);

  const watchersEl = document.getElementById("detail-watchers");
  watchersEl.innerHTML = "<strong>Watchers:</strong> " +
    (ticket.watchers.length ? esc(ticket.watchers.map((w) => w.watcher).join(", ")) : "(none)");

  const criteriaEl = document.getElementById("detail-criteria");
  const criteriaResp = await fetch("/tickets/" + encodeURIComponent(ticketId) + "/criteria");
  if (criteriaResp.ok) {
    const criteria = await criteriaResp.json();
    criteriaEl.innerHTML = "<strong>Criteria (frozen " + esc(criteria.criteria_frozen_at) +
      (criteria.hash_matches ? ", hash OK" : ", HASH MISMATCH") + "):</strong><ul>" +
      criteria.criteria.map((c) => "<li>" + esc(c.statement) + "</li>").join("") + "</ul>";
  } else if (criteriaResp.status === 404) {
    // TESS-66: the ONLY status get_criteria uses for the genuine "not frozen yet"
    // business state (http_api.py's get_criteria route) -- any other status is a real
    // error, not this, and must not be silently relabeled as it.
    criteriaEl.innerHTML = "<strong>Criteria:</strong> not frozen";
  } else {
    let message = "error";
    try {
      const errBody = await criteriaResp.json();
      message = errBody.error || message;
    } catch (err) {
      // non-JSON error body -- fall through with the generic message
    }
    criteriaEl.innerHTML = "<strong>Criteria:</strong> load failed: " + esc(message);
  }

  const commentsEl = document.getElementById("detail-comments");
  commentsEl.innerHTML = "";
  for (const comment of ticket.comments || []) {
    const li = document.createElement("li");
    li.textContent = "[" + comment.created_at + "] " + comment.actor + ": " + comment.body;
    commentsEl.appendChild(li);
  }

  if (resetStatus) {
    document.getElementById("detail-comment-status").textContent = "";
  }
}

function renderNeighborhood(elId, label, neighbors) {
  const el = document.getElementById(elId);
  if (!neighbors || !neighbors.length) {
    el.innerHTML = "<strong>" + esc(label) + ":</strong> (none)";
    return;
  }
  el.innerHTML = "<strong>" + esc(label) + ":</strong> " +
    neighbors.map((n) => esc(n.ticket_id) + " (" + esc(n.status) + ")").join(", ");
}

// Persisted across page loads (localStorage) so "who's editing" doesn't have to be
// re-typed every time a ticket is opened -- distinct from #detail-comment-actor, which
// stays its own per-save field (unchanged, existing behavior/tests depend on it), but
// pre-filled from the same stored value as a convenience since it's usually the same person.
const ACTOR_STORAGE_KEY = "tessera-reviewui-actor";
const actorInput = document.getElementById("detail-actor");
try {
  actorInput.value = localStorage.getItem(ACTOR_STORAGE_KEY) || "";
} catch (err) {
  // localStorage can throw (private browsing, disabled site data) -- editing still works,
  // it just won't remember the actor across page loads.
}
actorInput.addEventListener("input", () => {
  try {
    localStorage.setItem(ACTOR_STORAGE_KEY, actorInput.value);
  } catch (err) {
    // see above -- non-fatal if storage is unavailable
  }
  if (!document.getElementById("detail-comment-actor").value) {
    document.getElementById("detail-comment-actor").value = actorInput.value;
  }
});

// Shared POST helper for every inline field edit below. Same error-surfacing discipline
// as loadTicketDetail's own fetch calls (TESS-65) -- a non-ok response's real server
// error is logged, not swallowed, so a rejected edit (e.g. an illegal status transition)
// is debuggable from the console rather than just silently "save failed".
// Returns {ok, error} rather than a bare boolean -- found by a real user report (Jon
// tried to close a ticket, saw only "save failed (see console)", had to come ask what
// went wrong instead of the page just telling him). Every caller below now shows
// result.error directly in its own status span; console.error stays too, for anyone who
// does have devtools open, but it's no longer the ONLY place the real reason is visible.
async function patchTicketField(ticketId, path, payload) {
  const actor = actorInput.value.trim();
  if (!actor) {
    const error = "fill in “Editing as” above first";
    console.error("patchTicketField " + path + " failed:", error);
    return { ok: false, error: error };
  }
  try {
    const resp = await fetch("/tickets/" + encodeURIComponent(ticketId) + "/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ actor: actor }, payload)),
    });
    if (!resp.ok) {
      let message = "error";
      try {
        message = (await resp.json()).error || message;
      } catch (err) {
        // non-JSON error body -- fall through with the generic message
      }
      console.error("patchTicketField " + path + " failed:", message);
      return { ok: false, error: message };
    }
    return { ok: true, error: null };
  } catch (err) {
    console.error("patchTicketField " + path + " failed:", err.message);
    return { ok: false, error: err.message };
  }
}

async function saveReferenceDocs(ticketId, paths) {
  const statusSpan = document.getElementById("detail-reference-docs-status");
  statusSpan.textContent = "saving...";
  const result = await patchTicketField(ticketId, "reference_docs", { paths: paths });
  if (result.ok) {
    await loadTicketDetail(ticketId, false);
    document.getElementById("detail-reference-docs-status").textContent = "saved";
  } else {
    statusSpan.textContent = "save failed: " + result.error;
  }
}

document.getElementById("detail-reference-docs-add").addEventListener("click", async () => {
  if (!currentDetailTicketId) return;
  const input = document.getElementById("detail-reference-docs-input");
  const path = input.value.trim();
  if (!path) return;
  const existing = Array.from(document.querySelectorAll("#detail-reference-docs-list li"))
    .map((li) => li.textContent.replace(/remove\s*$/, "").trim());
  input.value = "";
  await saveReferenceDocs(currentDetailTicketId, existing.concat([path]));
});

document.getElementById("detail-summary-save").addEventListener("click", async () => {
  if (!currentDetailTicketId) return;
  const value = document.getElementById("detail-summary-input").value.trim();
  const statusSpan = document.getElementById("detail-summary-status");
  if (!value) {
    statusSpan.textContent = "summary can't be empty";
    return;
  }
  statusSpan.textContent = "saving...";
  const result = await patchTicketField(currentDetailTicketId, "summary", { summary: value });
  if (result.ok) {
    await loadTicketDetail(currentDetailTicketId, false);
    document.getElementById("detail-summary-status").textContent = "saved";
  } else {
    statusSpan.textContent = "save failed: " + result.error;
  }
});

document.getElementById("detail-description-save").addEventListener("click", async () => {
  if (!currentDetailTicketId) return;
  const value = document.getElementById("detail-description-input").value;
  const statusSpan = document.getElementById("detail-description-status");
  statusSpan.textContent = "saving...";
  const result = await patchTicketField(currentDetailTicketId, "description", { description: value });
  if (result.ok) {
    await loadTicketDetail(currentDetailTicketId, false);
    document.getElementById("detail-description-status").textContent = "saved";
  } else {
    statusSpan.textContent = "save failed: " + result.error;
  }
});
// Summary/description, like comments and docs, are explicit-save only -- no autosave
// listener on the input/textarea -- matching this file's own established discipline
// (see the comment above the comment-save handler, and the docs tab's explicit save).

document.getElementById("detail-comment-save").addEventListener("click", async () => {
  if (!currentDetailTicketId) return;
  const body = document.getElementById("detail-comment-body").value;
  const actor = document.getElementById("detail-comment-actor").value.trim();
  const statusSpan = document.getElementById("detail-comment-status");
  if (!body.trim() || !actor) {
    statusSpan.textContent = "comment body and actor are both required";
    return;
  }
  statusSpan.textContent = "saving...";
  const resp = await fetch("/tickets/" + encodeURIComponent(currentDetailTicketId) + "/comments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor: actor, body: body }),
  });
  if (resp.ok) {
    document.getElementById("detail-comment-body").value = "";
    // resetStatus=false, and "saved" is set AFTER the refresh completes, not before --
    // loadTicketDetail's own render path clears this same status span (so opening a
    // fresh ticket starts clean), and setting "saved" first then calling the refresh
    // un-awaited let that clear silently race out the "saved" message a moment later,
    // caught by the real headless-browser check (tests/reviewui_browser_check.py's
    // ticket_detail_and_comment_save), not by inspection.
    await loadTicketDetail(currentDetailTicketId, false);
    statusSpan.textContent = "saved";
  } else {
    statusSpan.textContent = "save failed";
  }
});
// Deliberately no autosave listener on #detail-comment-body -- the only network write
// for a comment happens inside the #detail-comment-save click handler above, same
// discipline as the docs tab's explicit-save (SCOPE.md).

// ---- diff-vs-claim ------------------------------------------------------

document.getElementById("diff-load").addEventListener("click", async () => {
  const tid = document.getElementById("diff-ticket-id").value.trim();
  const stage = document.getElementById("diff-stage").value.trim() || "dev";
  const resultEl = document.getElementById("diff-result");
  resultEl.innerHTML = "";
  if (!tid) return;
  try {
    const resp = await fetch(
      "/tickets/" + encodeURIComponent(tid) + "/discrepancy?stage=" + encodeURIComponent(stage)
    );
    const data = await resp.json();
    if (!resp.ok) {
      resultEl.innerHTML = '<p>' + esc(data.error || "error") + '</p>';
      return;
    }
    renderDiscrepancy(resultEl, data);
  } catch (err) {
    resultEl.innerHTML = '<p>' + esc(err.message) + '</p>';
  }
});

function renderDiscrepancy(container, data) {
  const claim = data.claim;
  const claimBlock = document.createElement("div");
  claimBlock.innerHTML =
    '<h3>Claim</h3>' +
    '<p><strong>Summary:</strong> ' + esc(claim.summary) + '</p>' +
    '<p><strong>Actor:</strong> ' + esc(claim.actor) + '</p>' +
    '<p><strong>Claimed files:</strong> ' + esc(claim.files_touched.join(", ")) + '</p>' +
    '<p><strong>Commit:</strong> ' + esc(claim.commit_sha) + '</p>';
  container.appendChild(claimBlock);

  const realBlock = document.createElement("div");
  realBlock.innerHTML =
    '<h3>Real diff</h3>' +
    '<p><strong>Files actually touched:</strong> ' + esc(data.real_files_touched.join(", ")) + '</p>';
  container.appendChild(realBlock);

  const discrepancyBlock = document.createElement("div");
  discrepancyBlock.className = "discrepancy-block" + (data.matches ? " match" : "");
  discrepancyBlock.dataset.matches = String(data.matches);
  if (data.matches) {
    discrepancyBlock.innerHTML = '<p class="discrepancy-title">Claim matches the real diff.</p>';
  } else {
    let html = '<p class="discrepancy-title">Discrepancy detected</p><ul class="discrepancy-list">';
    for (const f of data.touched_but_not_claimed) {
      html += '<li>touched but NOT claimed: ' + esc(f) + '</li>';
    }
    for (const f of data.claimed_but_not_touched) {
      html += '<li>claimed but NOT touched: ' + esc(f) + '</li>';
    }
    html += '</ul>';
    discrepancyBlock.innerHTML = html;
  }
  container.appendChild(discrepancyBlock);
}

// ---- docs (explicit save, no autosave) -----------------------------------

let lastLoadedDocPath = null;

// Encode each path SEGMENT (not the slashes themselves) so a doc path containing '#',
// '?', spaces, etc. survives the request instead of being silently mangled -- '#' is
// stripped client-side as a URL fragment before the request is even sent, and an
// unencoded '?' gets reinterpreted server-side as the query separator (code-review
// finding: this file's diff-view fetch already did this; the docs fetches did not).
function encodeDocPath(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

document.getElementById("doc-load").addEventListener("click", async () => {
  const path = document.getElementById("doc-path").value.trim();
  const loadStatus = document.getElementById("doc-load-status");
  const contentEl = document.getElementById("doc-content");
  document.getElementById("doc-save-status").textContent = "";
  if (!path) {
    loadStatus.textContent = "enter a doc path first";
    return;
  }
  loadStatus.textContent = "loading...";
  // TESS-63: previously no try/catch, and a failed load silently cleared #doc-content
  // to "" with zero indication anything went wrong -- indistinguishable from "this doc
  // is genuinely empty." Now the real server error (or a network/parse failure) is
  // shown, and the content box and lastLoadedDocPath are only updated on a real,
  // successful load, so a failed load can never be mistaken for a loaded-but-blank doc.
  try {
    const resp = await fetch("/docs/" + encodeDocPath(path));
    const data = await resp.json();
    if (!resp.ok) {
      loadStatus.textContent = "load failed: " + (data.error || "error");
      return;
    }
    contentEl.value = data.content;
    lastLoadedDocPath = path;
    loadStatus.textContent = "";
  } catch (err) {
    loadStatus.textContent = "load failed: " + err.message;
  }
});

document.getElementById("doc-save").addEventListener("click", async () => {
  const path = document.getElementById("doc-path").value.trim();
  const content = document.getElementById("doc-content").value;
  if (!path) return;
  const statusSpan = document.getElementById("doc-save-status");
  statusSpan.textContent = "saving...";
  const resp = await fetch("/docs/" + encodeDocPath(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: content }),
  });
  statusSpan.textContent = resp.ok ? "saved" : "save failed";
});
// Deliberately no 'input'/'blur'/interval listener on #doc-content that calls fetch --
// the only network write happens inside the #doc-save click handler above.

// ---- SQL query tab (TESS-38, read-only) -----------------------------------
// Per Clint Eastwood's review: tickets/comments/etc. are a DERIVED PROJECTION, not the
// ledger -- the highest-value thing this tab can do is make that visible, not just be
// an escape hatch. The first example walks the events hash chain directly.

const SQL_EXAMPLES = [
  {
    label: "Walk the event hash chain (the ledger, not a projection)",
    query:
      "WITH RECURSIVE chain(id, event_type, ticket_id, prev_hash, event_hash, depth) AS (\n" +
      "  SELECT id, event_type, ticket_id, prev_hash, event_hash, 0\n" +
      "  FROM events WHERE prev_hash = 'GENESIS'\n" +
      "  UNION ALL\n" +
      "  SELECT e.id, e.event_type, e.ticket_id, e.prev_hash, e.event_hash, c.depth + 1\n" +
      "  FROM events e JOIN chain c ON e.prev_hash = c.event_hash\n" +
      ")\n" +
      "SELECT depth, event_type, ticket_id FROM chain ORDER BY depth DESC LIMIT 50",
  },
  {
    label: "All projects and their ticket counts (derived, not ledger)",
    query:
      "SELECT p.prefix, p.codename, count(t.ticket_id) AS tickets\n" +
      "FROM projects p LEFT JOIN tickets t ON t.project_id = p.id\n" +
      "GROUP BY p.id ORDER BY p.id",
  },
  {
    label: "Open tickets by status, all projects (derived, not ledger)",
    query: "SELECT status, count(*) FROM tickets WHERE archived=0 GROUP BY status ORDER BY status",
  },
  {
    label: "Most recent 20 events (the ledger)",
    query: "SELECT id, event_type, ticket_id, actor, created_at FROM events ORDER BY id DESC LIMIT 20",
  },
];

const sqlExamplesEl = document.getElementById("sql-examples");
const sqlQueryEl = document.getElementById("sql-query");

// Insert at the CURRENT CURSOR POSITION, not appended at the end -- selectionStart/
// selectionEnd track the caret (or the current selection, which this replaces, matching
// how a normal text insert behaves when something is selected). cursorOffsetFromEnd
// (TESS-46) lets a function insert like "COUNT()" land the cursor BETWEEN the parens
// (offset 1) instead of after the closing paren (offset 0, the default) -- so clicking
// COUNT() then immediately typing "*" produces COUNT(*) without any manual cursor
// repositioning, keeping "click through a query" actually fluent.
function insertAtCursor(textarea, text, cursorOffsetFromEnd = 0) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const value = textarea.value;
  textarea.value = value.slice(0, start) + text + value.slice(end);
  const newPos = start + text.length - cursorOffsetFromEnd;
  textarea.selectionStart = textarea.selectionEnd = newPos;
  textarea.focus();
}

// Comma-between-clicks, by default (TESS-48): clicking a second (or later) field/
// expression in a row should read the way a human would type a SELECT list --
// "col1, col2", not "col1col2". Deterministic rule, not a state flag tracking "was the
// last click also a field": look at the character immediately before the cursor. If it
// looks like the end of a previous token (a letter, digit, underscore, closing paren,
// quote, or *) -- i.e. NOT right after whitespace, an opening paren, a comma, or the
// start of the query -- prefix the insert with ", ". Never fires right after SELECT,
// WHERE, an opening paren, or at the very start, where a leading comma would be wrong.
function insertExpressionAtCursor(textarea, text, cursorOffsetFromEnd = 0) {
  const start = textarea.selectionStart;
  const precedingChar = textarea.value.slice(0, start).slice(-1);
  const looksLikeEndOfToken = /[A-Za-z0-9_)'"*]/.test(precedingChar);
  const finalText = looksLikeEndOfToken ? ", " + text : text;
  insertAtCursor(textarea, finalText, cursorOffsetFromEnd);
}

// TESS-58: a schema-field click landing just inside an aggregate's empty parens
// (COUNT(|), SUM(|), ...) should exit the parens afterward by default -- COUNT(a, b) is
// invalid SQL (aggregates take exactly one argument or *), and the operator's own usage
// is "count specific values, not combinations." If the character right after the new
// cursor position is ')', hop past it, so the NEXT field click lands outside the parens
// and reads as a new top-level SELECT-list item via insertExpressionAtCursor's existing
// comma rule -- already-correct behavior once outside, just not reached by default
// before this. Deliberately narrow: only fires when a ')' immediately follows (a
// multi-arg template like "substr(, , )" has its own literal commas in the way, so this
// never touches those).
function moveCursorPastTrailingCloseParen(textarea) {
  const pos = textarea.selectionStart;
  if (textarea.value[pos] === ")") {
    textarea.selectionStart = textarea.selectionEnd = pos + 1;
  }
}

// Same "is there already a separator before the cursor" check as
// insertExpressionAtCursor, but for CLAUSE keywords (FROM/WHERE/...), which need a
// single leading SPACE rather than a comma when the preceding token doesn't already
// end in whitespace. Found by direct testing, not by inspection: clicking "status" then
// "FROM" produced "tickets.statusFROM " -- SQL_PALETTE's clause entries have a
// TRAILING space ("FROM ") but nothing ensured a LEADING one, and every earlier test of
// this path happened to click FROM right after a manually-typed trailing space, masking
// the gap until a field-then-FROM sequence exposed it.
function insertClauseAtCursor(textarea, text, cursorOffsetFromEnd = 0) {
  const start = textarea.selectionStart;
  const precedingChar = textarea.value.slice(0, start).slice(-1);
  const needsSeparator = precedingChar !== "" && !/\s/.test(precedingChar);
  const finalText = needsSeparator ? " " + text : text;
  insertAtCursor(textarea, finalText, cursorOffsetFromEnd);
}

// Ephemeral field-preview popup (TESS-45): shows REAL distinct sample values for the
// clicked column, not a hand-written description -- "dates show dates, project_id shows
// its format" per the operator's own framing means letting the actual data answer the
// question, rather than a static per-type blurb that could go stale. Reuses the existing
// read-only /sql endpoint (same safety machinery, no new execution path) rather than a
// dedicated preview endpoint.
let fieldPreviewTimer = null;

// TESS-47: the currently-selected dataset's member project ids, or null for "no
// filtering" (the default dataset entries cover exactly one project each, but a custom
// dataset can span several). Only applied to tables with a direct project_id column --
// most tables reach their project only by joining through tickets.ticket_id, which a
// single-table preview query can't do generically. Disclosed via #sql-dataset-hint, not
// silently partial.
let currentDatasetProjectIds = null;

function tableHasProjectIdColumn(table) {
  if (!loadedSqlSchema) return false;
  const cols = loadedSqlSchema.tables[table] || [];
  return cols.some((c) => c.name === "project_id");
}

// TESS-55: same popup shape as Discovery mode's showTablePreview (value type /
// description / sample values), so the two modes look consistent -- Write mode's own
// difference is that clicking ALSO inserts at the cursor (handled by the caller), and
// this stays scoped to the single clicked field rather than the whole table.
function newPreviewPopup(anchorEl, loadingLabel) {
  const existing = document.getElementById("field-preview-popup");
  if (existing) existing.remove();
  if (fieldPreviewTimer) clearTimeout(fieldPreviewTimer);

  const popup = document.createElement("div");
  popup.id = "field-preview-popup";
  popup.className = "field-preview-popup";
  popup.textContent = "loading " + loadingLabel + "…";
  document.body.appendChild(popup);
  const rect = anchorEl.getBoundingClientRect();
  popup.style.left = (rect.right + window.scrollX + 8) + "px";
  popup.style.top = (rect.top + window.scrollY) + "px";
  return popup;
}

async function fetchSampleValues(table, column, extraFilter) {
  const resp = await fetch("/sql", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: "SELECT DISTINCT " + column + " FROM " + table +
        " WHERE " + column + " IS NOT NULL" + (extraFilter || "") + " LIMIT 5",
    }),
  });
  const data = await resp.json();
  if (!resp.ok) return { ok: false, text: data.error || "error" };
  if (data.rows.length === 0) return { ok: true, text: "(no non-null values yet)" };
  return { ok: true, text: data.rows.map((r) => String(r[0])).join(", ") };
}

async function showFieldPreview(table, column, colType, anchorEl) {
  const popup = newPreviewPopup(anchorEl, table + "." + column);

  const datasetFilter =
    currentDatasetProjectIds && tableHasProjectIdColumn(table)
      ? " AND project_id IN (" + currentDatasetProjectIds.join(",") + ")"
      : "";
  const description = (loadedSqlSchema.descriptions[table] || {})[column] || "(no description set)";

  try {
    const values = await fetchSampleValues(table, column, datasetFilter);
    // TESS-67: fetchSampleValues already computes an ok flag; previously neither caller
    // checked it, so a failed query's error string rendered in the exact table cell real
    // sample values would occupy, no visual distinction from real data.
    const sampleCell = values.ok
      ? esc(values.text)
      : "<span class=\"preview-error\">error: " + esc(values.text) + "</span>";
    popup.innerHTML =
      "<strong>" + esc(table + "." + column) + "</strong>" +
      "<table class=\"discovery-preview-table\">" +
      "<tr><th>Value type</th><td>" + esc(colType) + "</td></tr>" +
      "<tr><th>Description</th><td>" + esc(description) + "</td></tr>" +
      "<tr><th>Sample values</th><td>" + sampleCell + "</td></tr>" +
      "</table>";
  } catch (err) {
    popup.textContent = table + "." + column + ": preview failed (" + err.message + ")";
  }

  fieldPreviewTimer = setTimeout(() => popup.remove(), 8000);
}

// Populated by loadSqlSchema(), read by showFieldPreview() to decide whether a table can
// be dataset-filtered (TESS-47) -- only tables with a direct project_id column can.
let loadedSqlSchema = null;

// Dataset selector (TESS-47): "right now is probably just projects, but wired so custom
// datasets can be selected too" -- list_datasets() already returns exactly that (one
// implicit entry per project, plus any real custom ones), this just renders it.
async function loadDatasets() {
  const selectEl = document.getElementById("sql-dataset-select");
  const hintEl = document.getElementById("sql-dataset-hint");
  try {
    const resp = await fetch("/datasets");
    if (!resp.ok) throw new Error("bad response " + resp.status);
    const data = await resp.json();
    selectEl.innerHTML = "";
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "(all -- no dataset filter)";
    selectEl.appendChild(allOption);
    for (const ds of data.datasets) {
      const opt = document.createElement("option");
      opt.value = JSON.stringify(ds.projects.map((p) => p.id));
      opt.textContent = ds.name + (ds.kind === "custom" ? " (custom)" : "") +
        " [" + ds.projects.map((p) => p.prefix).join(", ") + "]";
      selectEl.appendChild(opt);
    }
    selectEl.addEventListener("change", () => {
      currentDatasetProjectIds = selectEl.value ? JSON.parse(selectEl.value) : null;
      hintEl.textContent = currentDatasetProjectIds
        ? "Field previews scoped to this dataset for tables with a direct project_id " +
          "column; other tables (most of them -- they reach their project only via " +
          "tickets.ticket_id) are shown unfiltered."
        : "";
    });
  } catch (err) {
    hintEl.textContent = "dataset load failed: " + err.message;
  }
}

// TESS-50: Discovery mode (browse/explore, click shows value+type+description, never
// touches the query) vs Write mode (click inserts at cursor, list narrows to tables
// already referenced in the query being typed -- the "grouped by whatever is present
// in the table [you're querying]" framing from the operator's own request). Write is
// the default since it matches every prior release's click-to-insert behavior --
// switching the default would silently change what clicking a field does for anyone
// used to TESS-43 through TESS-48.
let sqlSchemaMode = "write";

function referencedTablesInQuery(query) {
  const tables = new Set();
  const re = /\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)/gi;
  let match;
  while ((match = re.exec(query)) !== null) tables.add(match[1].toLowerCase());
  return tables;
}

// Re-filters the already-rendered schema tree in place rather than re-fetching/re-
// building it on every keystroke -- narrowing is pure DOM visibility, no new data.
function applyWriteModeNarrowing() {
  const treeEl = document.getElementById("sql-schema-tree");
  if (sqlSchemaMode !== "write") {
    for (const el of treeEl.querySelectorAll(".schema-table")) el.classList.remove("schema-table-hidden");
    return;
  }
  const referenced = referencedTablesInQuery(sqlQueryEl.value);
  for (const el of treeEl.querySelectorAll(".schema-table")) {
    // No FROM/JOIN typed yet (empty query, or a query with neither) -- show everything
    // rather than narrowing to nothing, since an empty "referenced" set isn't a real
    // signal about what the user wants to see.
    const show = referenced.size === 0 || referenced.has(el.dataset.table);
    el.classList.toggle("schema-table-hidden", !show);
  }
}

function setSqlSchemaMode(mode) {
  sqlSchemaMode = mode;
  document.getElementById("sql-mode-discovery").classList.toggle("active", mode === "discovery");
  document.getElementById("sql-mode-write").classList.toggle("active", mode === "write");
  applyWriteModeNarrowing();
}

// TESS-57: Discovery mode shows the WHOLE table when any one field is clicked -- every
// column's type, description, and sample values in one popup, not just the clicked
// field -- "so you could go through the schema and explore it" with one click giving a
// full working picture. Runs each column's sample-values query in parallel (a table has
// a handful of columns, not hundreds, so this stays cheap).
async function showTablePreview(table, anchorEl) {
  const popup = newPreviewPopup(anchorEl, table);
  popup.classList.add("discovery-table-popup");

  const columns = loadedSqlSchema.tables[table] || [];
  const descriptions = loadedSqlSchema.descriptions[table] || {};

  try {
    const results = await Promise.all(
      columns.map((col) => fetchSampleValues(table, col.name).catch((err) => ({ ok: false, text: err.message })))
    );
    let rows = "<tr><th>Column</th><th>Type</th><th>Description</th><th>Sample values</th></tr>";
    columns.forEach((col, i) => {
      // TESS-67: same fix as showFieldPreview -- results[i].ok distinguishes a real
      // failed lookup from real sample data instead of rendering both identically.
      const sampleCell = results[i].ok
        ? esc(results[i].text)
        : "<span class=\"preview-error\">error: " + esc(results[i].text) + "</span>";
      rows += "<tr><td>" + esc(col.name) + "</td><td>" + esc(col.type) + "</td><td>" +
        esc(descriptions[col.name] || "(no description set)") + "</td><td>" +
        sampleCell + "</td></tr>";
    });
    popup.innerHTML = "<strong>" + esc(table) + "</strong><table class=\"discovery-preview-table\">" + rows + "</table>";
  } catch (err) {
    popup.textContent = table + ": preview failed (" + err.message + ")";
  }

  fieldPreviewTimer = setTimeout(() => popup.remove(), 15000);
}

// BigQuery-style schema/type browser (TESS-43): table names + column types, click a
// field to insert "table.column" at the cursor. table.column, not just column, because
// this store has 15 tables and a query joining several of them needs the qualifier to
// stay unambiguous -- clicking should never insert something that could collide.
async function loadSqlSchema() {
  const treeEl = document.getElementById("sql-schema-tree");
  try {
    const resp = await fetch("/sql/schema");
    if (!resp.ok) throw new Error("bad response " + resp.status);
    const data = await resp.json();
    loadedSqlSchema = data;
    treeEl.innerHTML = "";
    for (const table of Object.keys(data.tables).sort()) {
      const tableEl = document.createElement("div");
      tableEl.className = "schema-table";
      tableEl.dataset.table = table;
      const tableHeader = document.createElement("div");
      tableHeader.className = "schema-table-name";
      tableHeader.textContent = table;
      tableHeader.addEventListener("click", () => {
        colsEl.classList.toggle("collapsed");
        // TESS-54: "the dataset field doesn't map to the FROM function" -- clicking a
        // table name in Write mode inserts "FROM tablename", pre-scoped with the
        // selected dataset's project_id filter when the table has a direct project_id
        // column. Tables that only reach their project via a join (most of them) get
        // an unscoped FROM instead of a fabricated filter -- already disclosed via
        // #sql-dataset-hint's "other tables ... shown unfiltered" text.
        if (sqlSchemaMode === "write") {
          const datasetFilter =
            currentDatasetProjectIds && tableHasProjectIdColumn(table)
              ? " WHERE project_id IN (" + currentDatasetProjectIds.join(",") + ")"
              : "";
          insertClauseAtCursor(sqlQueryEl, "FROM " + table + datasetFilter + " ");
        }
      });
      tableEl.appendChild(tableHeader);

      const colsEl = document.createElement("div");
      // Collapsed by default, matching BigQuery's own schema-tree convention -- click a
      // table name to expand it. Also avoids every table's columns being simultaneously
      // visible with duplicate names (e.g. "ticket_id" appears in several tables),
      // which is confusing to scan and was the direct cause of a real bug: an early
      // version left every table expanded by default, so a headless-browser click test
      // targeting "the ticket_id column" matched the wrong table's column first,
      // whichever sorted earlier alphabetically -- found by that test failing, not by
      // inspection.
      colsEl.className = "schema-columns collapsed";
      for (const col of data.tables[table]) {
        const colEl = document.createElement("div");
        colEl.className = "schema-column";
        colEl.innerHTML =
          '<span class="schema-col-name">' + esc(col.name) + '</span>' +
          '<span class="schema-col-type">' + esc(col.type) + '</span>';
        colEl.addEventListener("click", () => {
          if (sqlSchemaMode === "discovery") {
            showTablePreview(table, colEl);
          } else {
            insertExpressionAtCursor(sqlQueryEl, table + "." + col.name);
            moveCursorPastTrailingCloseParen(sqlQueryEl);
            showFieldPreview(table, col.name, col.type, colEl);
          }
        });
        colsEl.appendChild(colEl);
      }
      tableEl.appendChild(colsEl);
      treeEl.appendChild(tableEl);
    }
    applyWriteModeNarrowing();
  } catch (err) {
    treeEl.textContent = "schema load failed: " + err.message;
  }
}

// SQL keyword/function click-through palette (TESS-46): "as long as they understand
// SQL", click a token instead of typing it. insert lands via insertAtCursor; cursorOffset
// (see insertAtCursor's own docstring) places the caret INSIDE a function's parens for
// the simple zero-arg-so-far cases (COUNT(), date(), etc.) so the next keystroke goes
// where a human would expect it, not after the closing paren. A deliberately curated
// set, not an exhaustive SQLite function reference -- covers what's actually useful
// against THIS schema (including json_extract/json_each, since events.payload,
// ticket_fields.field_value, and ticket_criteria.criteria are all JSON-valued TEXT).
const SQL_PALETTE = [
  {
    group: "Clauses",
    items: [
      { label: "SELECT", insert: "SELECT " }, { label: "DISTINCT", insert: "DISTINCT " },
      { label: "FROM", insert: "FROM " }, { label: "WHERE", insert: "WHERE " },
      { label: "AND", insert: "AND " }, { label: "OR", insert: "OR " },
      { label: "JOIN", insert: "JOIN " }, { label: "LEFT JOIN", insert: "LEFT JOIN " },
      { label: "ON", insert: "ON " }, { label: "GROUP BY", insert: "GROUP BY " },
      { label: "ORDER BY", insert: "ORDER BY " }, { label: "ASC", insert: "ASC " },
      { label: "DESC", insert: "DESC " }, { label: "LIMIT", insert: "LIMIT " },
      { label: "WITH", insert: "WITH " }, { label: "AS", insert: "AS " },
    ],
  },
  {
    group: "Aggregates",
    items: [
      { label: "COUNT(*)", insert: "COUNT(*)" },
      { label: "COUNT()", insert: "COUNT()", cursorOffset: 1 },
      { label: "SUM()", insert: "SUM()", cursorOffset: 1 },
      { label: "AVG()", insert: "AVG()", cursorOffset: 1 },
      { label: "MIN()", insert: "MIN()", cursorOffset: 1 },
      { label: "MAX()", insert: "MAX()", cursorOffset: 1 },
      { label: "GROUP_CONCAT()", insert: "GROUP_CONCAT()", cursorOffset: 1 },
    ],
  },
  {
    group: "Date / time",
    items: [
      { label: "date()", insert: "date()", cursorOffset: 1 },
      { label: "datetime()", insert: "datetime()", cursorOffset: 1 },
      { label: "julianday()", insert: "julianday()", cursorOffset: 1 },
      { label: "strftime('%Y-%m-%d', )", insert: "strftime('%Y-%m-%d', )", cursorOffset: 1 },
    ],
  },
  {
    group: "String",
    items: [
      { label: "length()", insert: "length()", cursorOffset: 1 },
      { label: "upper()", insert: "upper()", cursorOffset: 1 },
      { label: "lower()", insert: "lower()", cursorOffset: 1 },
      { label: "trim()", insert: "trim()", cursorOffset: 1 },
      { label: "ltrim()", insert: "ltrim()", cursorOffset: 1 },
      { label: "rtrim()", insert: "rtrim()", cursorOffset: 1 },
      { label: "substr(, , )", insert: "substr(, , )", cursorOffset: 5 },
      { label: "replace(, , )", insert: "replace(, , )", cursorOffset: 5 },
    ],
  },
  {
    // TESS-51: REGEXP/LIKE/GLOB are infix operators, not function calls -- each item
    // sets clauseStyle so the click handler inserts with a leading SPACE (like a
    // clause keyword) instead of a leading comma (like an expression/function).
    // CONTAINS has no SQLite equivalent; the item stands in the standard
    // INSTR(haystack, needle) > 0 idiom, still an expression (comma-prefixed).
    group: "Matching",
    items: [
      { label: "REGEXP", insert: "REGEXP ''", cursorOffset: 1, clauseStyle: true },
      { label: "LIKE", insert: "LIKE '%%'", cursorOffset: 2, clauseStyle: true },
      { label: "NOT LIKE", insert: "NOT LIKE '%%'", cursorOffset: 2, clauseStyle: true },
      { label: "GLOB", insert: "GLOB ''", cursorOffset: 1, clauseStyle: true },
      { label: "CONTAINS (instr)", insert: "instr(, ) > 0", cursorOffset: 8 },
      { label: "IN ()", insert: "IN ()", cursorOffset: 1, clauseStyle: true },
    ],
  },
  {
    group: "Conditional / cast",
    items: [
      { label: "coalesce(, )", insert: "coalesce(, )", cursorOffset: 3 },
      { label: "ifnull(, )", insert: "ifnull(, )", cursorOffset: 3 },
      { label: "cast( as )", insert: "cast( as )", cursorOffset: 3 },
      { label: "round(, )", insert: "round(, )", cursorOffset: 3 },
    ],
  },
  {
    group: "JSON",
    items: [
      { label: "json_extract(, '$.')", insert: "json_extract(, '$.')", cursorOffset: 6 },
      { label: "json_each()", insert: "json_each()", cursorOffset: 1 },
    ],
  },
];

function loadSqlFunctionPalette() {
  const treeEl = document.getElementById("sql-functions-tree");
  treeEl.innerHTML = "";
  for (const group of SQL_PALETTE) {
    const groupEl = document.createElement("div");
    groupEl.className = "schema-table"; // reuse the schema panel's table/column styling
    const header = document.createElement("div");
    header.className = "schema-table-name";
    header.textContent = group.group;
    const itemsEl = document.createElement("div");
    itemsEl.className = "schema-columns collapsed";
    header.addEventListener("click", () => itemsEl.classList.toggle("collapsed"));
    for (const item of group.items) {
      const itemEl = document.createElement("div");
      itemEl.className = "schema-column";
      itemEl.innerHTML = '<span class="schema-col-name">' + esc(item.label) + '</span>';
      itemEl.addEventListener("click", () => {
        // Clauses (SELECT/FROM/WHERE/...) never get a leading COMMA -- only the
        // expression-like groups (Aggregates/Date-time/String/JSON) do -- but they DO
        // still need a leading SPACE if the preceding token doesn't already end in
        // whitespace (see insertClauseAtCursor's own comment for the bug this fixes).
        // TESS-51: infix operators (REGEXP, LIKE, GLOB) read like clause keywords --
        // "col REGEXP 'x'" wants a SPACE before them, not a comma, even though they
        // live outside the Clauses group. item.clauseStyle marks those explicitly
        // rather than growing the group-name check into an ever-longer list.
        if (group.group === "Clauses" || item.clauseStyle) {
          insertClauseAtCursor(sqlQueryEl, item.insert, item.cursorOffset || 0);
        } else {
          insertExpressionAtCursor(sqlQueryEl, item.insert, item.cursorOffset || 0);
        }
      });
      itemsEl.appendChild(itemEl);
    }
    groupEl.appendChild(header);
    groupEl.appendChild(itemsEl);
    treeEl.appendChild(groupEl);
  }
}

const blankOption = document.createElement("option");
blankOption.value = "";
blankOption.textContent = "(pick an example, or write your own below)";
sqlExamplesEl.appendChild(blankOption);
SQL_EXAMPLES.forEach((ex, i) => {
  const opt = document.createElement("option");
  opt.value = String(i);
  opt.textContent = ex.label;
  sqlExamplesEl.appendChild(opt);
});
sqlExamplesEl.addEventListener("change", () => {
  const idx = sqlExamplesEl.value;
  if (idx === "") return;
  sqlQueryEl.value = SQL_EXAMPLES[Number(idx)].query;
});

// TESS-59: deterministic (no ML, no real SQL parser -- same philosophy as TESS-48's
// self-healing) pretty-printer. Normalizes whitespace, then inserts a newline before
// each major clause keyword (2-space indent for AND/OR so they read as nested under
// WHERE), longest keyword first in the alternation so "LEFT JOIN" matches before the
// bare "JOIN" inside it would.
const FORMAT_CLAUSE_KEYWORDS = [
  "LEFT JOIN", "GROUP BY", "ORDER BY", "SELECT", "FROM", "WHERE", "JOIN", "LIMIT", "HAVING", "ON",
];
const FORMAT_INDENTED_KEYWORDS = ["AND", "OR"];

function formatSqlQuery(query) {
  const normalized = query.replace(/\s+/g, " ").trim();
  if (!normalized) return normalized;
  const allKeywords = [...FORMAT_CLAUSE_KEYWORDS, ...FORMAT_INDENTED_KEYWORDS];
  const pattern = new RegExp(
    "\\b(" + allKeywords.map((k) => k.replace(" ", "\\s+")).join("|") + ")\\b", "gi"
  );
  const withBreaks = normalized.replace(pattern, (match) => {
    const isIndented = FORMAT_INDENTED_KEYWORDS.some((k) => k.toLowerCase() === match.toLowerCase());
    return (isIndented ? "\n  " : "\n") + match;
  });
  return withBreaks
    .split("\n")
    .map((line) => line.replace(/\s+$/, ""))
    .join("\n")
    .replace(/^\n+/, "");
}

document.getElementById("sql-format").addEventListener("click", () => {
  sqlQueryEl.value = formatSqlQuery(sqlQueryEl.value);
});

// TESS-60: chart view for the current result set -- the last-run query's data, kept
// around so switching Table/Chart or the X/Y column pickers redraws without re-running
// the query. Reset to null on every new run/error so a stale chart never survives a
// query that returned nothing (or failed).
let lastSqlResult = null;
const CHART_MAX_BARS = 50;

function populateChartColumnSelectors(columns) {
  const xSel = document.getElementById("sql-chart-x");
  const ySel = document.getElementById("sql-chart-y");
  xSel.innerHTML = "";
  ySel.innerHTML = "";
  columns.forEach((col, i) => {
    const optX = document.createElement("option");
    optX.value = String(i);
    optX.textContent = col;
    xSel.appendChild(optX);
    const optY = document.createElement("option");
    optY.value = String(i);
    optY.textContent = col;
    ySel.appendChild(optY);
  });
  if (columns.length > 1) ySel.value = "1";
}

function drawBarChart(canvas, labels, values, yLabel, truncatedCount) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const style = getComputedStyle(document.body);
  const bg = style.getPropertyValue("--bg").trim();
  const fg = style.getPropertyValue("--fg").trim();
  const accent = style.getPropertyValue("--accent").trim();
  const muted = style.getPropertyValue("--muted").trim();

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  const padding = { top: 24, right: 20, bottom: 64, left: 50 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const numericValues = values.map((v) => (Number.isFinite(v) ? v : 0));
  const maxVal = Math.max(1, ...numericValues);
  const barGap = 8;
  const barW = Math.max(2, chartW / Math.max(1, values.length) - barGap);

  ctx.strokeStyle = muted;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + chartH);
  ctx.lineTo(padding.left + chartW, padding.top + chartH);
  ctx.stroke();

  ctx.fillStyle = accent;
  numericValues.forEach((val, i) => {
    const barH = (val / maxVal) * chartH;
    const x = padding.left + i * (barW + barGap) + barGap / 2;
    const y = padding.top + chartH - barH;
    ctx.fillRect(x, y, barW, barH);
  });

  ctx.fillStyle = fg;
  ctx.font = "10px monospace";
  labels.forEach((label, i) => {
    const x = padding.left + i * (barW + barGap) + barGap / 2 + barW / 2;
    ctx.save();
    ctx.translate(x, padding.top + chartH + 10);
    ctx.rotate(-Math.PI / 4);
    ctx.textAlign = "right";
    ctx.fillText(String(label).slice(0, 14), 0, 0);
    ctx.restore();
  });

  ctx.textAlign = "left";
  ctx.fillText(
    yLabel + (truncatedCount ? " (first " + values.length + " of " + truncatedCount + " rows)" : ""),
    padding.left, padding.top - 8
  );
}

function renderChartView() {
  if (!lastSqlResult || !lastSqlResult.rows.length) return;
  const xIdx = Number(document.getElementById("sql-chart-x").value);
  const yIdx = Number(document.getElementById("sql-chart-y").value);
  const rows = lastSqlResult.rows;
  const shown = rows.slice(0, CHART_MAX_BARS);
  const labels = shown.map((r) => r[xIdx]);
  const values = shown.map((r) => Number(r[yIdx]));
  const canvas = document.getElementById("sql-chart");
  drawBarChart(canvas, labels, values, lastSqlResult.columns[yIdx], rows.length > CHART_MAX_BARS ? rows.length : 0);
}

function setSqlViewMode(mode) {
  const resultEl = document.getElementById("sql-result");
  const chartEl = document.getElementById("sql-chart");
  const chartControls = document.getElementById("sql-chart-controls");
  if (mode === "chart") {
    resultEl.classList.add("hidden");
    chartEl.classList.remove("hidden");
    chartControls.classList.remove("hidden");
    renderChartView();
  } else {
    resultEl.classList.remove("hidden");
    chartEl.classList.add("hidden");
    chartControls.classList.add("hidden");
  }
}

document.getElementById("sql-view-mode").addEventListener("change", (e) => setSqlViewMode(e.target.value));
document.getElementById("sql-chart-x").addEventListener("change", renderChartView);
document.getElementById("sql-chart-y").addEventListener("change", renderChartView);

document.getElementById("sql-run").addEventListener("click", async () => {
  const query = sqlQueryEl.value;
  const statusSpan = document.getElementById("sql-status");
  const resultEl = document.getElementById("sql-result");
  const resultControls = document.getElementById("sql-result-controls");
  if (!query.trim()) return;
  statusSpan.textContent = "running...";
  resultEl.innerHTML = "";
  resultControls.classList.add("hidden");
  lastSqlResult = null;
  try {
    const resp = await fetch("/sql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      statusSpan.textContent = "";
      resultEl.innerHTML = '<p class="sql-error">' + esc(data.error || "error") + '</p>';
      return;
    }
    statusSpan.textContent = data.row_count + " row(s)" + (data.truncated ? " (truncated)" : "");
    // TESS-48: always disclose self-healing, never a silent rewrite -- show the
    // original error AND exactly what was mechanically changed, right above the
    // results, so it's seen even by someone who'd have preferred it not happen.
    if (data.self_healed) {
      const healBanner = document.createElement("p");
      healBanner.className = "sql-self-healed-banner";
      healBanner.innerHTML =
        "<strong>Auto-fixed:</strong> " + esc(data.fixes_applied.join("; ")) +
        "<br><strong>Original error:</strong> " + esc(data.original_error) +
        "<br><strong>Ran instead:</strong> <code>" + esc(data.healed_query) + "</code>";
      resultEl.appendChild(healBanner);
    }
    renderSqlResult(resultEl, data);
    if (data.columns.length && data.rows.length) {
      lastSqlResult = data;
      resultControls.classList.remove("hidden");
      populateChartColumnSelectors(data.columns);
      setSqlViewMode(document.getElementById("sql-view-mode").value);
    }
  } catch (err) {
    statusSpan.textContent = "";
    resultEl.innerHTML = '<p class="sql-error">' + esc(err.message) + '</p>';
  }
});

function renderSqlResult(container, data) {
  if (!data.columns.length) {
    // appendChild, not innerHTML= -- this container may already hold the self-healing
    // banner (TESS-48), which innerHTML= would silently wipe out.
    const p = document.createElement("p");
    p.textContent = "(no columns returned)";
    container.appendChild(p);
    return;
  }
  const table = document.createElement("table");
  table.className = "sql-result-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const col of data.columns) {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of data.rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      const td = document.createElement("td");
      td.textContent = value === null ? "NULL" : String(value);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);
}

// ---- SSE live updates -----------------------------------------------------

function connectSSE() {
  const source = new EventSource("/events/stream");
  source.onopen = () => setStatus("connected", "connected");
  source.onerror = () => setStatus("disconnected", "disconnected");
  source.onmessage = (event) => {
    loadTicketList();
  };
  return source;
}

document.getElementById("sql-mode-discovery").addEventListener("click", () => setSqlSchemaMode("discovery"));
document.getElementById("sql-mode-write").addEventListener("click", () => setSqlSchemaMode("write"));
sqlQueryEl.addEventListener("input", applyWriteModeNarrowing);

// Ticket detail panel stays visible (position: sticky in styles.css) while the ticket
// list scrolls independently. Offset is MEASURED from nav's real rendered bottom edge,
// not a hardcoded pixel guess, so it stays correct if header/nav's height ever changes
// (a font-size tweak, an extra status badge, etc.) without needing a matching CSS edit.
function updateStickyOffset() {
  const nav = document.querySelector("nav");
  document.documentElement.style.setProperty("--sticky-offset", nav.getBoundingClientRect().bottom + 12 + "px");
}
window.addEventListener("resize", updateStickyOffset);
updateStickyOffset();

loadTicketList();
loadKnownProjects();
loadSqlSchema();
loadSqlFunctionPalette();
loadDatasets();
setStatus("connecting", "connecting…");
connectSSE();
