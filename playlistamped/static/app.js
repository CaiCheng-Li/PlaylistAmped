"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = (el, on = true) => el.classList.toggle("hidden", !on);

async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function showErr(el, message) {
  el.textContent = message;
  show(el, true);
}

/* ==================================================================== boot */

let current = {};

async function boot() {
  const s = await api("/api/state");
  current = s;
  if (s.configured) enterMain(s);
  else show($("setup"), true);
}

function enterMain(s) {
  current = { ...current, ...s };
  show($("setup"), false);
  show($("main"), true);
  show($("btnSettings"), true);
  $("url").focus();
}

/* =============================================================== connecting */

$("btnPin").onclick = async () => {
  show($("setupChoose"), false);
  show($("setupPin"), true);
  try {
    const { pin } = await api("/api/pin", {});
    $("pinCode").textContent = pin;
    pollPin();
  } catch (e) { showErr($("setupErr"), e.message); }
};

async function pollPin() {
  try {
    const data = await api("/api/pin/poll");
    if (!data.linked) return setTimeout(pollPin, 1500);
    $("pinStatus").textContent = `Signed in as ${data.username}`;
    show($("setupPin"), false);
    chooseServer(data.servers);
  } catch (e) {
    showErr($("setupErr"), e.message);
    show($("setupPin"), false);
    show($("setupChoose"), true);
  }
}

function serverButtons(servers, container, onPick) {
  container.innerHTML = servers.map((s, i) => `
    <button class="choice" data-name="${esc(s.name)}">
      <span class="choice-n">${i + 1}</span>
      <span><span class="choice-t">${esc(s.name)}</span>
      <span class="choice-d">${s.owned ? "Your server" : "Shared with you"}</span></span>
    </button>`).join("");
  container.querySelectorAll("button").forEach((btn) => {
    btn.onclick = async () => {
      container.querySelectorAll("button").forEach((b) => (b.disabled = true));
      btn.querySelector(".choice-d").textContent = "Locating…";
      try {
        await onPick(btn.dataset.name);
      } catch (e) {
        container.querySelectorAll("button").forEach((b) => (b.disabled = false));
        throw e;
      }
    };
  });
}

function chooseServer(servers) {
  show($("setupServers"), true);
  serverButtons(servers, $("serverList"), async (name) => {
    try {
      const data = await api("/api/server", { server_name: name });
      show($("setupServers"), false);
      pickSection(data.sections);
    } catch (e) { showErr($("setupErr"), e.message); throw e; }
  });
}

function pickSection(list) {
  show($("setupSection"), true);
  $("sectionPick").innerHTML = list.map((s) => `<option>${esc(s)}</option>`).join("");
}

$("btnManual").onclick = () => {
  show($("setupChoose"), false);
  show($("setupManual"), true);
};

$("btnManualGo").onclick = async () => {
  const btn = $("btnManualGo");
  btn.disabled = true; btn.textContent = "Connecting…";
  try {
    const data = await api("/api/connect/manual", {
      baseurl: $("mBaseurl").value, token: $("mToken").value,
    });
    show($("setupManual"), false);
    show($("setupErr"), false);
    pickSection(data.sections);
  } catch (e) { showErr($("setupErr"), e.message); }
  finally { btn.disabled = false; btn.textContent = "Connect"; }
};

$("btnFinish").onclick = async () => {
  try {
    enterMain(await api("/api/finish", { section: $("sectionPick").value }));
  } catch (e) { showErr($("setupErr"), e.message); }
};

/* ================================================================ settings */

let settingsOpen = false;

function closeSettings({ restoreFocus = false } = {}) {
  if (!settingsOpen) return;
  settingsOpen = false;
  $("btnSettings").setAttribute("aria-expanded", "false");
  $("btnSettings").setAttribute("aria-label", "Open settings");
  show($("settings"), false);
  if (restoreFocus) $("btnSettings").focus();
}

$("btnSettings").onclick = async () => {
  if (settingsOpen) return closeSettings();
  settingsOpen = true;
  $("btnSettings").setAttribute("aria-expanded", "true");
  $("btnSettings").setAttribute("aria-label", "Close settings");
  show($("settings"), true);
  show($("switchList"), false);
  $("setMsg").textContent = "";
  show($("setErr"), false);
  try { await fillSettings(); }
  catch (e) { showErr($("setErr"), e.message); }
};

$("btnCloseSettings").onclick = () => closeSettings({ restoreFocus: true });

document.addEventListener("click", (e) => {
  if (!settingsOpen) return;
  if ($("settings").contains(e.target) || $("btnSettings").contains(e.target)) return;
  closeSettings();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && settingsOpen) closeSettings({ restoreFocus: true });
});

async function fillSettings() {
  const s = await api("/api/state");
  current = s;
  $("setServer").textContent = s.server;
  $("setIndexed").textContent = s.indexed
    ? `${s.indexed.toLocaleString()} tracks cached`
    : "Not loaded yet — built on the next sync";
  $("setAuto").value = Math.round(s.auto_accept);
  $("setFloor").value = Math.round(s.review_floor);

  const btn = $("btnSwitchServer");
  btn.disabled = !s.can_switch_servers;
  btn.title = s.can_switch_servers
    ? "" : "Only available when signed in through plex.tv";

  try {
    const libs = await api("/api/libraries");
    $("setSection").innerHTML = libs.sections
      .map((x) => `<option${x === libs.current ? " selected" : ""}>${esc(x)}</option>`)
      .join("");
  } catch (e) {
    $("setSection").innerHTML = `<option>${esc(current.section || "—")}</option>`;
    showErr($("setErr"), e.message);
  }
}

$("btnSwitchServer").onclick = async () => {
  const box = $("switchList");
  if (!box.classList.contains("hidden")) return show(box, false);
  try {
    const data = await api("/api/servers");
    show(box, true);
    serverButtons(data.servers, box, async (name) => {
      const picked = await api("/api/server", { server_name: name });
      const done = await api("/api/finish", { section: picked.sections[0] });
      show(box, false);
      enterMain(done);
      await fillSettings();
      $("setMsg").textContent = `Now using ${done.server} · ${done.section}.`;
      resetResults();
    });
  } catch (e) { showErr($("setErr"), e.message); }
};

$("setSection").onchange = async () => {
  try {
    await api("/api/library", { section: $("setSection").value });
    const s = await api("/api/state");
    enterMain(s);
    $("setMsg").textContent = `Library switched to ${s.section}.`;
    resetResults();
  } catch (e) { showErr($("setErr"), e.message); }
};

$("btnRefreshIndex").onclick = async () => {
  const btn = $("btnRefreshIndex");
  btn.disabled = true; btn.textContent = "Rebuilding…";
  $("setMsg").textContent = "Reading the whole library — this can take a few minutes.";
  try {
    const data = await api("/api/index/refresh", {});
    $("setIndexed").textContent = `${data.indexed.toLocaleString()} tracks cached`;
    $("setMsg").textContent = "Index rebuilt.";
  } catch (e) { showErr($("setErr"), e.message); $("setMsg").textContent = ""; }
  finally { btn.disabled = false; btn.textContent = "Rebuild"; }
};

$("btnSaveThresholds").onclick = async () => {
  try {
    const data = await api("/api/settings", {
      auto_accept: Number($("setAuto").value),
      review_floor: Number($("setFloor").value),
    });
    $("setMsg").textContent =
      `Saved. Matches at ${data.auto_accept} or above go straight in; ` +
      `below ${data.review_floor} counts as missing.`;
    show($("setErr"), false);
  } catch (e) { showErr($("setErr"), e.message); }
};

$("btnSignOut").onclick = async () => {
  const btn = $("btnSignOut");
  if (btn.dataset.armed !== "1") {
    // Two-step rather than a modal: signing out drops the token and the
    // cached library, and the next run re-downloads the whole thing.
    btn.dataset.armed = "1";
    btn.textContent = "Really sign out?";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Sign out"; }, 5000);
    return;
  }
  try {
    await api("/api/signout", {});
    location.reload();
  } catch (e) { showErr($("setErr"), e.message); }
};

/* ==================================================================== sync */

const STAGES = ["playlist", "index", "match", "writing"];
let poller = null;

function resetResults() {
  show($("results"), false);
  show($("truncNote"), false);
  show($("applybar"), false);
  $("applybar").classList.remove("show");
}

function startSync(opts = {}) {
  const url = $("url").value.trim();
  if (!url) return $("url").focus();

  show($("syncErr"), false);
  show($("truncNote"), false);
  show($("results"), false);
  show($("progress"), true);
  $("btnSync").disabled = true;
  $("btnResync").disabled = true;

  api("/api/sync", {
    url,
    name: $("optName").value.trim(),
    preview: $("optPreview").checked,
    no_reorder: $("optNoReorder").checked,
    refresh_playlist: !!opts.refreshPlaylist,
  })
    .then(() => { poller = setInterval(pollJob, 900); pollJob(); })
    .catch((e) => {
      showErr($("syncErr"), e.message);
      show($("progress"), false);
      $("btnSync").disabled = false;
      $("btnResync").disabled = false;
    });
}

$("btnSync").onclick = () => startSync();
// Re-sync must refetch the playlist, or it would replay the cached copy and
// never pick up tracks added at the source since the first run.
$("btnResync").onclick = () => startSync({ refreshPlaylist: true });
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") startSync(); });

function paintStages(stage) {
  const at = STAGES.indexOf(stage);
  document.querySelectorAll(".stage-pip").forEach((pip, i) => {
    pip.classList.toggle("done", at > i || stage === "done");
    pip.classList.toggle("active", at === i);
  });
}

async function pollJob() {
  let job;
  try { job = await api("/api/job"); } catch { return; }

  $("progMsg").textContent = job.message || "Working…";
  paintStages(job.stage);

  if (job.stage === "error") {
    clearInterval(poller);
    show($("progress"), false);
    showErr($("syncErr"), job.error);
    $("btnSync").disabled = false;
    $("btnResync").disabled = false;
    return;
  }
  if (job.stage !== "done") return;

  clearInterval(poller);
  show($("progress"), false);
  $("btnSync").disabled = false;
  $("btnResync").disabled = false;
  show($("btnResync"), true);
  render(job);
}

/* ================================================================= results */

let sourceLabel = "Source";
let previewMode = false;

function render(data) {
  sourceLabel = data.source_short || "Source";
  previewMode = !!data.preview;
  show($("results"), true);
  paintCounts(data.counts);

  $("writtenNote").textContent = previewMode
    ? "Preview only — nothing written to Plex yet."
    : data.written
      ? `${data.source_label || "Playlist"} → “${data.playlist_title}” ${data.written} on Plex.`
      : "";

  // A partial read must never pass unremarked -- syncing 100 of 300 tracks
  // silently would look like a complete playlist.
  const trunc = $("truncNote");
  if (data.truncated) { trunc.textContent = data.truncated; show(trunc, true); }
  else show(trunc, false);

  renderQueue(data.review);
  renderList($("listMatched"), $("nMatched"), data.matched, true);
  renderList($("listMissing"), $("nMissing"), data.missing, false);

  if (previewMode) {
    $("applyMsg").textContent = "Preview — write this playlist to Plex?";
    $("applybar").classList.add("show");
  }
}

function paintCounts(c) {
  $("cMatched").textContent = c.matched;
  $("cReview").textContent = c.review;
  $("cMissing").textContent = c.missing + c.skipped;
}

function meter(score) {
  const filled = Math.round(score / 10);
  const high = score >= 80;
  let out = "";
  for (let i = 0; i < 10; i++) {
    out += `<span class="seg${i < filled ? " on" : ""}${i < filled && high ? " high" : ""}"></span>`;
  }
  return out;
}

function reviewCard(item) {
  const best = item.candidates[0];
  if (!best) return "";
  const c = best.components;
  const alts = item.candidates.slice(1);

  return `<div class="review" data-index="${item.index}">
    <div class="side">
      <div class="side-tag">${esc(sourceLabel)}</div>
      <div class="side-main">
        <div class="side-title">${esc(item.title)}</div>
        <div class="side-sub">${esc(item.artists)}</div>
      </div>
      <div class="side-len mono">${esc(item.duration)}</div>
    </div>
    <div class="rule"></div>
    <div class="side library">
      <div class="side-tag">Library</div>
      <div class="side-main">
        <div class="side-title">${esc(best.title)}</div>
        <div class="side-sub">${esc(best.artist)}${best.album ? " · " + esc(best.album) : ""}</div>
      </div>
      <div class="side-len mono">${esc(best.duration)}</div>
    </div>

    <div class="meter-row">
      <div class="meter">${meter(best.score)}</div>
      <div class="score mono">${best.score}</div>
      <div class="why mono">title ${c.title} · artist ${c.artist} · length ${c.duration}</div>
      <div class="actions">
        <button class="ghost" data-act="ignore">Ignore</button>
        <button class="add" data-act="add" data-key="${esc(best.rating_key)}">Add</button>
      </div>
    </div>

    ${alts.length ? `<details class="alts">
      <summary>${alts.length} other possibilit${alts.length === 1 ? "y" : "ies"}</summary>
      ${alts.map((a) => `<div class="alt">
          <div class="alt-main">${esc(a.title)} <span class="alt-sub">— ${esc(a.artist)}</span></div>
          <div class="mono alt-sub">${a.score}</div>
          <button class="add" data-act="add" data-key="${esc(a.rating_key)}">Add</button>
        </div>`).join("")}
    </details>` : ""}
  </div>`;
}

function renderQueue(items) {
  const queue = $("queue");
  if (!items.length) {
    $("queueWrap").innerHTML =
      `<div class="queue-head"><h2>Needs a look</h2></div>
       <div class="card empty">Nothing to review — every track was decided automatically.</div>`;
    return;
  }
  queue.innerHTML = items.map(reviewCard).join("");
  queue.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.onclick = () => decide(btn.closest(".review"), btn.dataset.act, btn.dataset.key);
  });
}

function renderList(container, counter, items, matched) {
  counter.textContent = items.length;
  if (!items.length) { container.innerHTML = `<div class="empty">Nothing here.</div>`; return; }
  container.innerHTML = items.map((i) => `<div class="row">
      <div class="row-main">${esc(i.title)} <span class="row-sub">— ${esc(i.artists)}</span></div>
      ${matched && i.chosen
        ? `<div class="row-to">→ ${esc(i.chosen.title)} — ${esc(i.chosen.artist)}</div>`
        : `<div class="row-to">${esc(i.label)}</div>`}
    </div>`).join("");
}

/* =============================================================== decisions */

let pending = 0;

async function decide(card, action, ratingKey) {
  if (!card || card.dataset.busy) return;
  card.dataset.busy = "1";
  const index = Number(card.dataset.index);

  try {
    const data = await api("/api/decide", { index, action, rating_key: ratingKey });
    pending = data.pending;
    paintCounts(data.counts);

    card.classList.add("resolving");
    setTimeout(() => {
      card.remove();
      if (!$("queue").children.length) {
        $("queue").innerHTML = `<div class="card empty">Queue cleared.</div>`;
      }
    }, 180);

    $("applyMsg").textContent =
      `${pending} decision${pending === 1 ? "" : "s"} not yet written to Plex`;
    $("applybar").classList.add("show");
  } catch (e) {
    card.dataset.busy = "";
    showErr($("syncErr"), e.message);
  }
}

$("btnApply").onclick = async () => {
  const btn = $("btnApply");
  btn.disabled = true; btn.textContent = "Updating…";
  try {
    const data = await api("/api/apply", { no_reorder: $("optNoReorder").checked });
    $("applyMsg").textContent = `Playlist ${data.action} — ${data.total} tracks`;
    $("writtenNote").textContent = `“${$("optName").value.trim() || "Playlist"}” ${data.action} on Plex.`;
    $("applybar").classList.remove("show");
    pending = 0; previewMode = false;
  } catch (e) { showErr($("syncErr"), e.message); }
  finally { btn.disabled = false; btn.textContent = "Update playlist"; }
};

/* Keyboard triage: the queue can run to dozens of items, so the top card is
   always the target and A / I resolve it without touching the mouse. */
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea")) return;
  const key = e.key.toLowerCase();
  if (key !== "a" && key !== "i") return;
  const card = $("queue")?.querySelector(".review:not([data-busy])");
  if (!card) return;
  e.preventDefault();
  const btn = card.querySelector(`button[data-act="${key === "a" ? "add" : "ignore"}"]`);
  if (btn) decide(card, btn.dataset.act, btn.dataset.key);
});

boot().catch((e) => showErr($("setupErr"), e.message));
