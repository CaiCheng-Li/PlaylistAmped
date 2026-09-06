"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function showErr(el, message) {
  el.textContent = message;
  el.classList.remove("hidden");
}

/* ------------------------------------------------------------------ setup */

let sections = [];

async function boot() {
  const state = await api("/api/state");
  if (state.configured) {
    enterMain(state);
  } else {
    $("setup").classList.remove("hidden");
  }
}

function enterMain(state) {
  $("setup").classList.add("hidden");
  $("main").classList.remove("hidden");
  $("barMeta").classList.remove("hidden");
  $("barMeta").innerHTML = `<b>${esc(state.server)}</b> · ${esc(state.section)}`;
  $("url").focus();
}

$("btnPin").onclick = async () => {
  $("setupChoose").classList.add("hidden");
  $("setupPin").classList.remove("hidden");
  try {
    const { pin } = await api("/api/setup/pin", {});
    $("pinCode").textContent = pin;
    pollPin();
  } catch (e) {
    showErr($("setupErr"), e.message);
  }
};

async function pollPin() {
  try {
    const data = await api("/api/setup/pin/poll");
    if (!data.linked) return setTimeout(pollPin, 1500);
    $("pinStatus").textContent = `Signed in as ${data.username}`;
    $("setupPin").classList.add("hidden");
    chooseServer(data.servers);
  } catch (e) {
    showErr($("setupErr"), e.message);
    $("setupPin").classList.add("hidden");
    $("setupChoose").classList.remove("hidden");
  }
}

function chooseServer(servers) {
  const box = $("setupServers");
  box.classList.remove("hidden");
  $("serverList").innerHTML = servers
    .map((s, i) => `<button class="choice" data-name="${esc(s.name)}">
        <span class="choice-n">${i + 1}</span>
        <span><span class="choice-t">${esc(s.name)}</span>
        <span class="choice-d">${s.owned ? "Your server" : "Shared with you"}</span></span>
      </button>`).join("");

  $("serverList").querySelectorAll("button").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      btn.querySelector(".choice-d").textContent = "Locating…";
      try {
        const data = await api("/api/setup/server", { server_name: btn.dataset.name });
        box.classList.add("hidden");
        pickSection(data.sections);
      } catch (e) {
        showErr($("setupErr"), e.message);
        btn.disabled = false;
      }
    };
  });
}

function pickSection(list) {
  sections = list;
  $("setupSection").classList.remove("hidden");
  $("sectionPick").innerHTML = list.map((s) => `<option>${esc(s)}</option>`).join("");
}

$("btnManual").onclick = () => {
  $("setupChoose").classList.add("hidden");
  $("setupManual").classList.remove("hidden");
};

$("btnManualGo").onclick = async () => {
  const btn = $("btnManualGo");
  btn.disabled = true;
  btn.textContent = "Connecting…";
  try {
    const data = await api("/api/setup/manual", {
      baseurl: $("mBaseurl").value, token: $("mToken").value,
    });
    $("setupManual").classList.add("hidden");
    $("setupErr").classList.add("hidden");
    pickSection(data.sections);
  } catch (e) {
    showErr($("setupErr"), e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect";
  }
};

$("btnFinish").onclick = async () => {
  try {
    const data = await api("/api/setup/finish", { section: $("sectionPick").value });
    enterMain(data);
  } catch (e) {
    showErr($("setupErr"), e.message);
  }
};

/* ------------------------------------------------------------------- sync */

const STAGES = ["playlist", "index", "match", "writing"];
let poller = null;

function startSync(opts = {}) {
  const url = $("url").value.trim();
  if (!url) return $("url").focus();

  $("syncErr").classList.add("hidden");
  $("truncNote").classList.add("hidden");
  $("results").classList.add("hidden");
  $("progress").classList.remove("hidden");
  $("btnSync").disabled = true;
  $("btnResync").disabled = true;

  api("/api/sync", {
    url,
    refresh_index: !!opts.refreshIndex,
    refresh_playlist: !!opts.refreshPlaylist,
  })
    .then(() => { poller = setInterval(pollJob, 900); pollJob(); })
    .catch((e) => {
      showErr($("syncErr"), e.message);
      $("progress").classList.add("hidden");
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
  try {
    job = await api("/api/job");
  } catch { return; }

  $("progMsg").textContent = job.message || "Working…";
  paintStages(job.stage);

  if (job.stage === "error") {
    clearInterval(poller);
    $("progress").classList.add("hidden");
    showErr($("syncErr"), job.error);
    $("btnSync").disabled = false;
    $("btnResync").disabled = false;
    return;
  }
  if (job.stage !== "done") return;

  clearInterval(poller);
  $("progress").classList.add("hidden");
  $("btnSync").disabled = false;
  $("btnResync").disabled = false;
  $("btnResync").classList.remove("hidden");
  render(job);
}

/* ---------------------------------------------------------------- results */

let job = null;
let sourceLabel = "Source";

function render(data) {
  job = data;
  sourceLabel = data.source_short || "Source";
  $("results").classList.remove("hidden");
  paintCounts(data.counts);
  $("writtenNote").textContent = data.written
    ? `${data.source_label || "Playlist"} → “${data.playlist_title}” ${data.written} on Plex.`
    : "";

  // A partial read must never pass unremarked -- syncing 100 of 300 tracks
  // silently would look like a complete playlist.
  const trunc = $("truncNote");
  if (data.truncated) {
    trunc.textContent = data.truncated;
    trunc.classList.remove("hidden");
  } else {
    trunc.classList.add("hidden");
  }

  renderQueue(data.review);
  renderList($("listMatched"), $("nMatched"), data.matched, true);
  renderList($("listMissing"), $("nMissing"), data.missing, false);
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
  if (!items.length) {
    container.innerHTML = `<div class="empty">Nothing here.</div>`;
    return;
  }
  container.innerHTML = items.map((i) => `<div class="row">
      <div class="row-main">${esc(i.title)} <span class="row-sub">— ${esc(i.artists)}</span></div>
      ${matched && i.chosen
        ? `<div class="row-to">→ ${esc(i.chosen.title)} — ${esc(i.chosen.artist)}</div>`
        : `<div class="row-to">${esc(i.label)}</div>`}
    </div>`).join("");
}

/* -------------------------------------------------------------- decisions */

let pending = 0;

async function decide(card, action, ratingKey) {
  if (!card || card.dataset.busy) return;
  card.dataset.busy = "1";
  const index = Number(card.dataset.index);

  try {
    const data = await api("/api/decide", { index, action, rating_key: ratingKey });
    pending = data.pending;
    paintCounts(data.counts);

    // Fade the card out, then remove it, so the queue visibly shortens.
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
  btn.disabled = true;
  btn.textContent = "Updating…";
  try {
    const data = await api("/api/apply", {});
    $("applyMsg").textContent = `Playlist ${data.action} — ${data.total} tracks`;
    $("applybar").classList.remove("show");
    pending = 0;
  } catch (e) {
    showErr($("syncErr"), e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Update playlist";
  }
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
