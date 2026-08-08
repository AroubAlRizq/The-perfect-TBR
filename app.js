/* ════════════════════════════════════════════════════════════════════
   Readerprint front end.

   Vanilla, no build step. Someone should be able to clone this repo and
   run it with one command, without installing a toolchain first.
   ════════════════════════════════════════════════════════════════════ */

const $  = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

const state = {
  meta: null,
  shelf: [],
  counts: {},
  searchTimer: null,
};

/* Public domain, first published 1813. Here so the page is never a blank
   box on arrival — a specimen slot with nothing in it explains nothing. */
const DEFAULT_SPECIMEN =
`It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.

However little known the feelings or views of such a man may be on his first entering a neighbourhood, this truth is so well fixed in the minds of the surrounding families, that he is considered as the rightful property of some one or other of their daughters.

"My dear Mr. Bennet," said his lady to him one day, "have you heard that Netherfield Park is let at last?"

Mr. Bennet replied that he had not.

"But it is," returned she; "for Mrs. Long has just been here, and she told me all about it."`;

/* ── Utilities ────────────────────────────────────────────────────── */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try { detail = (await response.json()).detail || detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return response.json();
}

let toastTimer;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const countWords = (text) => (text.trim().match(/\b[\w'-]+\b/g) || []).length;

/* ── Tabs ─────────────────────────────────────────────────────────── */

function showView(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
  if (name === "shelf") loadShelf();
  if (name === "recommend") loadRecommendations();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$$(".tab").forEach((tab) => tab.addEventListener("click", () => showView(tab.dataset.view)));

/* ── Measurement rail ─────────────────────────────────────────────── */

/* Each metric declares the range it is drawn across, so a bar length means
   something consistent rather than being scaled to whatever is on screen. */
const RAIL_METRICS = [
  { key: "prose_density",        name: "Density",     min: 0,  max: 100, format: (v) => `${Math.round(v)}/100` },
  { key: "mean_sentence_length", name: "Sentence",    min: 5,  max: 35,  format: (v) => `${v.toFixed(1)} words` },
  { key: "sentence_length_sd",   name: "Variation",   min: 0,  max: 20,  format: (v) => `± ${v.toFixed(1)}` },
  { key: "dialogue_share",       name: "Dialogue",    min: 0,  max: 0.55, format: (v) => `${Math.round(v * 100)}%` },
  { key: "ornament_index",       name: "Figurative",  min: 0,  max: 80,  format: (v) => `${Math.round(v)}/100` },
  { key: "cliche_rate",          name: "Stock phrases", min: 0, max: 25, format: (v) => `${v.toFixed(1)}/10k`, caution: 8 },
  { key: "grade_level",          name: "Reading level", min: 3, max: 16, format: (v) => `grade ${v.toFixed(1)}` },
];

function renderRail(data) {
  const rail = $("#rail");
  rail.hidden = false;

  /* An "unknown" chip is worse than no chip: it fills the same space and
     tells the reader nothing. Short samples simply show fewer flags. */
  const narration = [];
  if (data.pov && data.pov !== "unknown") narration.push(data.pov_label || data.pov);
  if (data.tense && data.tense !== "unknown") narration.push(data.tense_label || data.tense);
  const chips = narration.length
    ? `<div style="margin-top:.7rem" class="flags">` +
      narration.map((n) => `<span class="flag is-signal">${escapeHtml(n)}</span>`).join("") +
      `</div>`
    : `<div class="excerpt-source" style="margin-top:.7rem">Narration undetermined — try a longer sample.</div>`;
  $("#railVerdict").innerHTML = `${escapeHtml(data.summary || "")}${chips}`;

  $("#railMetrics").innerHTML = RAIL_METRICS.map((metric) => {
    const raw = data[metric.key];
    if (raw === undefined || raw === null) return "";
    const pct = Math.max(0, Math.min(100, ((raw - metric.min) / (metric.max - metric.min)) * 100));
    const flat = metric.caution !== undefined && raw >= metric.caution;
    return `
      <div class="metric ${flat ? "is-flat" : ""}">
        <div class="metric-top">
          <span class="metric-name">${metric.name}</span>
          <span class="metric-value">${metric.format(raw)}</span>
        </div>
        <div class="meter"><div class="meter-fill" data-pct="${pct}"></div></div>
      </div>`;
  }).join("");

  // Draw the bars on the next frame so the transition actually plays.
  requestAnimationFrame(() => {
    $$("#railMetrics .meter-fill").forEach((fill) => { fill.style.width = `${fill.dataset.pct}%`; });
  });
}

async function measureSpecimen() {
  const text = $("#specimenText").value;
  if (countWords(text) < 60) {
    toast("Paste a little more — around three hundred words gives a reliable reading.");
    return;
  }
  const button = $("#measureBtn");
  button.disabled = true;
  button.textContent = "Measuring";
  try {
    renderRail(await api("/api/analyse", { method: "POST", body: JSON.stringify({ text }) }));
    if (countWords(text) < 250) {
      $("#measureHint").textContent =
        "Short sample. The numbers will shift with a fuller page.";
    }
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Measure this prose";
  }
}

$("#measureBtn").addEventListener("click", measureSpecimen);

$("#specimenText").addEventListener("input", (event) => {
  $("#wordCounter").textContent = `${countWords(event.target.value)} words`;
});

$("#clearSpecimen").addEventListener("click", () => {
  $("#specimenText").value = "";
  $("#wordCounter").textContent = "0 words";
  $("#rail").hidden = true;
});

/* ── Flags shown on a book ────────────────────────────────────────── */

function flagsFor(book) {
  const flags = [];
  if (book.pov && book.pov !== "unknown") flags.push({ text: book.pov_label, kind: "signal" });
  if (book.tense && book.tense !== "unknown") flags.push({ text: book.tense_label, kind: "signal" });
  if (book.length_label && book.length_band !== "unknown") flags.push({ text: book.length_label, kind: "" });
  if (book.translated) flags.push({ text: book.translator ? `Tr. ${book.translator}` : "Translated", kind: "" });
  if (book.tier && book.tier !== "unknown") flags.push({ text: book.tier_label, kind: "" });
  if (book.web_origin) flags.push({ text: "Web serial origin", kind: "caution" });
  if (book.provisional) flags.push({ text: "Not yet measured", kind: "provisional" });
  return flags;
}

const renderFlags = (book) =>
  flagsFor(book)
    .map((f) => `<span class="flag ${f.kind ? "is-" + f.kind : ""}">${escapeHtml(f.text)}</span>`)
    .join("");

/* ── Shelf ────────────────────────────────────────────────────────── */

async function loadShelf() {
  const data = await api("/api/shelf");
  state.shelf = data.items;
  state.counts = data.counts;
  $("#shelfCount").textContent = data.counts.total;

  const note = $("#shelfNote");
  if (!data.counts.total) {
    note.textContent = "Nothing here yet. Import a CSV or search for a book you have read.";
  } else if (data.counts.dnf_without_reason) {
    note.innerHTML = `${data.counts.total} books. <strong>${data.counts.dnf_without_reason}</strong> ` +
      `abandoned without a reason — those are the most useful ones to fill in.`;
  } else {
    note.textContent = `${data.counts.total} books, ${data.counts.loved} of them loved.`;
  }

  $("#shelfList").innerHTML = data.items.map(renderShelfRow).join("");
  renderFingerprint();
}

function renderShelfRow({ book, event }) {
  const needsReason = event.verdict === "dnf" && !event.dnf_reasons.length;
  const verdicts = state.meta.verdicts.map((v) =>
    `<button class="verdict-btn ${event.verdict === v.key ? "is-on" : ""}"
             data-verdict="${v.key}" data-book="${book.id}">${escapeHtml(v.label)}</button>`).join("");

  let reasonBlock = "";
  if (event.verdict === "dnf" || event.verdict === "disliked") {
    const chips = state.meta.dnf_reasons.map((r) =>
      `<button class="chip ${event.dnf_reasons.includes(r.key) ? "is-on" : ""}"
               data-reason="${r.key}" data-book="${book.id}"
               title="${escapeHtml(r.hint)}">${escapeHtml(r.label)}</button>`).join("");
    reasonBlock = `
      <div class="reason-block">
        <div class="reason-prompt">${needsReason ? "What stopped you?" : "What stopped you"}</div>
        <div class="reason-chips">${chips}</div>
      </div>`;
  }

  return `
    <div class="shelf-row ${needsReason ? "needs-reason" : ""}">
      <div class="shelf-main">
        <div>
          <div class="shelf-title">${escapeHtml(book.title)}</div>
          <div class="shelf-author">${escapeHtml(book.author || "Unknown author")}</div>
        </div>
        <button class="ghost" data-remove="${book.id}">Remove</button>
      </div>
      <div class="verdicts">${verdicts}</div>
      ${reasonBlock}
    </div>`;
}

$("#shelfList").addEventListener("click", async (event) => {
  const target = event.target;
  try {
    if (target.dataset.verdict) {
      const item = state.shelf.find((i) => i.book.id === target.dataset.book);
      await api("/api/shelf", {
        method: "POST",
        body: JSON.stringify({
          book_id: target.dataset.book,
          verdict: target.dataset.verdict,
          dnf_reasons: item ? item.event.dnf_reasons : [],
        }),
      });
      await loadShelf();
    } else if (target.dataset.reason) {
      const item = state.shelf.find((i) => i.book.id === target.dataset.book);
      const reasons = new Set(item.event.dnf_reasons);
      reasons.has(target.dataset.reason)
        ? reasons.delete(target.dataset.reason)
        : reasons.add(target.dataset.reason);
      await api("/api/shelf", {
        method: "POST",
        body: JSON.stringify({
          book_id: target.dataset.book,
          verdict: item.event.verdict,
          dnf_reasons: [...reasons],
        }),
      });
      await loadShelf();
    } else if (target.dataset.remove) {
      await api(`/api/shelf/${target.dataset.remove}`, { method: "DELETE" });
      await loadShelf();
    }
  } catch (error) {
    toast(error.message);
  }
});

/* ── Fingerprint ──────────────────────────────────────────────────── */

/* A shelf plotted on the axes that drive the recommender. Its job is to make
   the profile inspectable — if the marks sit somewhere you don't recognise,
   the recommendations that follow will be wrong and you can see why. */
const FP_AXES = [
  { key: "prose_density", label: "Prose density", min: 0, max: 100, low: "Plain", high: "Dense" },
  { key: "mean_sentence_length", label: "Sentence length", min: 5, max: 35, low: "Short", high: "Long" },
  { key: "dialogue_share", label: "Dialogue", min: 0, max: 0.55, low: "Narration", high: "Dialogue" },
];

async function renderFingerprint() {
  const panel = $("#fingerprintPanel");
  const profile = await api("/api/profile");

  if (!profile.usable) {
    panel.hidden = false;
    $("#fingerprint").innerHTML =
      `<p class="panel-note">Rate three books you liked and the profile appears here. ` +
      `You have ${profile.n_liked}.</p>`;
    return;
  }

  panel.hidden = false;
  const rows = FP_AXES.map((axis) => {
    const ticks = state.shelf.map((item) => {
      const value = item.book[axis.key] ?? (item.book.style || {})[axis.key];
      if (value === undefined || value === null) return "";
      const pct = Math.max(0, Math.min(100, ((value - axis.min) / (axis.max - axis.min)) * 100));
      const negative = item.event.weight < 0;
      return `<span class="fp-tick ${negative ? "is-negative" : ""}" style="left:${pct}%"
                    title="${escapeHtml(item.book.title)}"></span>`;
    }).join("");

    const centre = profile.style_summary[axis.key];
    const centreMark = centre !== undefined
      ? `<span class="fp-centre" style="left:${Math.max(0, Math.min(100, ((centre - axis.min) / (axis.max - axis.min)) * 100))}%"></span>`
      : "";

    return `
      <div class="fp-row">
        <div class="fp-label"><span>${axis.label}</span>
          <span>${centre !== undefined ? centre.toFixed(1) : "—"}</span></div>
        <div class="fp-track">${ticks}${centreMark}</div>
        <div class="fp-scale"><span>${axis.low}</span><span>${axis.high}</span></div>
      </div>`;
  }).join("");

  const aversions = Object.keys(profile.aversions);
  const note = aversions.length
    ? `<p class="panel-note" style="margin-top:1rem">Steering away from: ${
        aversions.map((a) => a.replace(/_/g, " ")).join(", ")}.</p>`
    : "";

  $("#fingerprint").innerHTML = rows + note +
    `<p class="panel-note" style="margin-top:.6rem;font-size:.75rem">` +
    `Blue marks your centre. Amber marks books you abandoned. ` +
    `${profile.corpus_measured} of ${profile.corpus_size} books measured from real text.</p>`;
}

/* ── Search and add ───────────────────────────────────────────────── */

$("#bookSearch").addEventListener("input", (event) => {
  clearTimeout(state.searchTimer);
  const query = event.target.value.trim();
  if (query.length < 2) { $("#searchResults").innerHTML = ""; return; }
  state.searchTimer = setTimeout(async () => {
    const data = await api(`/api/books?q=${encodeURIComponent(query)}&limit=12`);
    $("#searchResults").innerHTML = data.books.length
      ? data.books.map((book) => `
          <div class="result">
            <div>
              <div class="result-title">${escapeHtml(book.title)}</div>
              <div class="result-author">${escapeHtml(book.author || "Unknown")}</div>
            </div>
            <button class="ghost" data-add="${book.id}">Add</button>
          </div>`).join("")
      : `<p class="panel-note">Nothing matching. Books outside the corpus can be added once you have measured a page of them.</p>`;
  }, 220);
});

$("#searchResults").addEventListener("click", async (event) => {
  const id = event.target.dataset.add;
  if (!id) return;
  try {
    await api("/api/shelf", { method: "POST", body: JSON.stringify({ book_id: id, verdict: "liked" }) });
    toast("Added as liked. Change the verdict on your shelf.");
    await loadShelf();
  } catch (error) { toast(error.message); }
});

/* ── CSV import ───────────────────────────────────────────────────── */

const dropzone = $("#dropzone");
dropzone.addEventListener("click", () => $("#csvInput").click());
$("#csvInput").addEventListener("change", (event) => {
  if (event.target.files[0]) uploadCsv(event.target.files[0]);
});

["dragenter", "dragover"].forEach((name) =>
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-over");
  }));

["dragleave", "drop"].forEach((name) =>
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-over");
    if (name === "drop" && event.dataTransfer.files[0]) uploadCsv(event.dataTransfer.files[0]);
  }));

async function uploadCsv(file) {
  const report = $("#importReport");
  report.hidden = false;
  report.classList.remove("is-warning");
  report.textContent = "Reading…";

  const form = new FormData();
  form.append("file", file);
  try {
    const { report: result } = await api("/api/import", { method: "POST", body: form });
    const lines = [
      `${result.imported} books imported`,
      `${result.matched_existing} matched the corpus`,
      `${result.added_new} added as new records`,
      `${result.skipped_unrated} skipped, unrated`,
      `${result.skipped_to_read} skipped, not read yet`,
    ];
    if (result.dnf_found) {
      lines.push(`${result.dnf_found} marked did-not-finish — add a reason for each`);
      report.classList.add("is-warning");
    }
    report.innerHTML = lines.map(escapeHtml).join("<br>");
    await loadShelf();
  } catch (error) {
    report.classList.add("is-warning");
    report.textContent = error.message;
  }
}

/* ── Recommendations ──────────────────────────────────────────────── */

async function loadRecommendations() {
  const params = new URLSearchParams({ limit: "12", diversity: $("#filterDiversity").value });
  if ($("#filterPov").value) params.set("pov", $("#filterPov").value);
  if ($("#filterLength").value) params.set("max_length", $("#filterLength").value);

  $("#recsMessage").textContent = "Working…";
  $("#recsList").innerHTML = "";

  try {
    const data = await api(`/api/recommendations?${params}`);
    if (!data.usable) {
      $("#recsMessage").textContent = data.message;
      return;
    }
    if (!data.recommendations.length) {
      $("#recsMessage").textContent = "No book in the corpus clears those filters. Try loosening one.";
      return;
    }
    $("#recsMessage").textContent = "";
    $("#recsList").innerHTML = data.recommendations.map(renderRec).join("");
    requestAnimationFrame(() => {
      $$("#recsList .meter-fill").forEach((f) => { f.style.width = `${f.dataset.pct}%`; });
    });
  } catch (error) {
    $("#recsMessage").textContent = error.message;
  }
}

function renderRec(rec) {
  const book = rec.book;
  const density = book.prose_density ?? 0;
  const reasons = rec.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  const cautions = rec.cautions.map((c) => `<li class="is-caution">${escapeHtml(c)}</li>`).join("");

  return `
    <article class="rec" data-book="${book.id}" tabindex="0">
      <div class="rec-head">
        <div>
          <div class="rec-title">${escapeHtml(book.title)}</div>
          <div class="rec-author">${escapeHtml(book.author || "Unknown")}${book.year ? " · " + book.year : ""}</div>
        </div>
        <div class="rec-score">match ${(rec.score * 100).toFixed(0)}</div>
      </div>
      <div class="rec-density">
        <span class="rec-density-label">Density ${Math.round(density)}</span>
        <div class="meter"><div class="meter-fill" data-pct="${density}"></div></div>
      </div>
      <div class="flags">${renderFlags(book)}</div>
      <ul class="rec-reasons">${reasons}${cautions}</ul>
    </article>`;
}

$("#refreshRecs").addEventListener("click", loadRecommendations);
["filterPov", "filterLength", "filterDiversity"].forEach((id) =>
  $(`#${id}`).addEventListener("change", loadRecommendations));

$("#recsList").addEventListener("click", (event) => {
  const card = event.target.closest(".rec");
  if (card) openDrawer(card.dataset.book);
});

$("#recsList").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const card = event.target.closest(".rec");
  if (card) openDrawer(card.dataset.book);
});

/* ── Drawer ───────────────────────────────────────────────────────── */

const STAT_ROWS = [
  ["Prose density", (s) => `${Math.round(s.prose_density ?? 0)}/100`],
  ["Sentence length", (s) => `${(s.mean_sentence_length ?? 0).toFixed(1)} words`],
  ["Variation", (s) => `± ${(s.sentence_length_sd ?? 0).toFixed(1)}`],
  ["Dialogue", (s) => `${Math.round((s.dialogue_share ?? 0) * 100)}%`],
  ["Figurative", (s) => `${Math.round(s.ornament_index ?? 0)}/100`],
  ["Stock phrases", (s) => `${(s.cliche_rate ?? 0).toFixed(1)} per 10k`],
  ["Subordination", (s) => `${(s.subordination_rate ?? 0).toFixed(2)}/sentence`],
  ["Reading level", (s) => `grade ${(s.grade_level ?? 0).toFixed(1)}`],
];

async function openDrawer(bookId) {
  const book = await api(`/api/books/${bookId}`);
  const style = book.style || {};

  const provisional = book.provisional
    ? `<div class="notice">These numbers are placeholders, not measurements. Paste a page below and this book joins the measured corpus.</div>`
    : "";

  const webOrigin = book.web_origin
    ? `<div class="notice" style="margin-top:.7rem">${escapeHtml(book.web_origin_note || "Began as a web serial.")}</div>`
    : "";

  const excerpt = book.excerpt
    ? `<div class="drawer-section">
         <h4>The prose itself</h4>
         <div class="excerpt">${escapeHtml(book.excerpt)}</div>
         <div class="excerpt-source">${escapeHtml(book.excerpt_licence || "")}</div>
       </div>`
    : "";

  const stats = STAT_ROWS.map(([label, format]) =>
    `<div class="stat"><span class="stat-k">${label}</span><span class="stat-v">${format(style)}</span></div>`).join("");

  $("#drawerBody").innerHTML = `
    <h3>${escapeHtml(book.title)}</h3>
    <p class="drawer-author">${escapeHtml(book.author || "Unknown")}${book.year ? " · " + book.year : ""}${
      book.publisher ? " · " + escapeHtml(book.publisher) : ""}</p>
    <div class="flags">${renderFlags(book)}</div>
    ${provisional}${webOrigin}
    ${book.style_note ? `<p style="margin-top:1.1rem">${escapeHtml(book.style_note)}</p>` : ""}
    ${excerpt}
    <div class="drawer-section">
      <h4>Measurements</h4>
      <div class="stat-grid">${stats}</div>
    </div>
    <div class="drawer-section measure-form">
      <h4>Measure it yourself</h4>
      <p class="panel-note">Paste a page from your copy. It is analysed here and never redistributed.</p>
      <textarea id="drawerExcerpt" placeholder="Paste roughly three hundred words."></textarea>
      <div style="margin-top:.6rem;display:flex;gap:.6rem;align-items:center">
        <button class="solid" id="drawerMeasure" data-book="${book.id}">Measure and save</button>
        <button class="ghost" id="drawerShelve" data-book="${book.id}">Add to shelf</button>
      </div>
    </div>`;

  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  document.body.style.overflow = "hidden";

  $("#drawerMeasure").addEventListener("click", async (event) => {
    const text = $("#drawerExcerpt").value;
    if (countWords(text) < 120) { toast("Around three hundred words works best."); return; }
    event.target.disabled = true;
    try {
      await api(`/api/books/${event.target.dataset.book}/excerpt`, {
        method: "POST", body: JSON.stringify({ text }),
      });
      toast("Measured. This book is no longer provisional.");
      openDrawer(event.target.dataset.book);
    } catch (error) {
      toast(error.message);
      event.target.disabled = false;
    }
  });

  $("#drawerShelve").addEventListener("click", async (event) => {
    try {
      await api("/api/shelf", {
        method: "POST",
        body: JSON.stringify({ book_id: event.target.dataset.book, verdict: "liked" }),
      });
      toast("Added to your shelf as liked.");
      await loadShelf();
    } catch (error) { toast(error.message); }
  });
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  document.body.style.overflow = "";
}

$("#drawerClose").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#drawer").hidden) closeDrawer();
});

/* ── Boot ─────────────────────────────────────────────────────────── */

(async function start() {
  try {
    state.meta = await api("/api/meta");
    $("#specimenText").value = DEFAULT_SPECIMEN;
    $("#wordCounter").textContent = `${countWords(DEFAULT_SPECIMEN)} words`;
    await measureSpecimen();
    const shelf = await api("/api/shelf");
    $("#shelfCount").textContent = shelf.counts.total;
  } catch (error) {
    toast(`Could not reach the server: ${error.message}`);
  }
})();
