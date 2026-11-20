// TruthLens investigation workspace (vanilla JS, no build step).
// Everything shown here comes from the API response of the current case; nothing is computed or invented in the browser
// except layout (positions, colours) and the band thresholds, which mirror the backend.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const bandOf = (s) => (s >= 0.65 ? "high" : s >= 0.35 ? "medium" : "low");
const MOD = { image: ["img", "Image"], video: ["vid", "Video"], audio: ["aud", "Audio"], text: ["txt", "Text"] };
let picked = [];
let C = null;            // current case report
let sel = { ev: null, f: null, tab: "overview", kf: "all", heatOp: 45, boxes: true, people: false };

// ---------------------------------------------------------------- views
// One screen: the upload panel stays on the left; the main area shows the welcome text, the running notice or the open case.
function view(name) {
  ["empty", "running", "case"].forEach((v) => $("#" + v).classList.toggle("hidden", v !== name));
  document.body.classList.toggle("no-case", name !== "case");
  document.body.classList.remove("rail-open");
  $("#headerCase").textContent = name === "running" ? "Pending assignment" : C ? C.case_id : "No case open";
  const status = $("#analysisStatus"), failed = name === "case" && C.log.some((l) => l.step === "analysis.error");
  status.textContent = name === "running" ? "Analysis in progress" : name === "case" ? (failed ? "Analysis completed with errors" : "Analysis complete") : "Awaiting evidence";
  status.className = "status-pill" + (failed ? " bad" : name === "empty" ? " unavailable" : "");
  window.scrollTo({ top: 0 });
}
// Secondary workflows share the existing API integrations.
$$("[data-panel]").forEach((b) => b.onclick = () => $("#" + b.dataset.panel).showModal());
$$("[data-close]").forEach((b) => b.onclick = () => b.closest("dialog").close());
$$("dialog").forEach((d) => d.addEventListener("click", (ev) => { if (ev.target === d) { const r = d.getBoundingClientRect(); if (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom) d.close(); } }));
$$("[data-upload]").forEach((b) => b.onclick = () => { input.accept = b.dataset.upload; input.click(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#zoom").classList.add("hidden"); });
async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === "string" ? body.detail : response.statusText || `Request failed (${response.status})`); }
  return response.json();
}
const closePanels = () => $$("dialog[open]").forEach((d) => d.close());
function custodyState(value) {
  return value?.ok === true ? { cls: "ok", label: "Verified" } : value?.ok === false ? { cls: "bad", label: "Failed" } : { cls: "muted", label: "Unavailable" };
}
function updateCustody(value) {
  C.custody_verification = value;
  const state = custodyState(value);
  $$("[data-custody-state]").forEach((el) => { el.className = "footer-value " + state.cls; el.textContent = state.label; });
  const box = $("#chainBox");
  if (box) { box.className = "chainbox " + state.cls; box.textContent = value?.ok === true ? `Verified: ${value.entries} entries; head ${value.head}` : value?.ok === false ? `Custody chain failed at entry ${value.broken_at ?? "unknown"}` : "Custody verification unavailable."; }
}

// ---------------------------------------------------------------- intake
const drop = $("#drop"), input = $("#files");
const acceptedEvidence = "image/*,video/*,audio/*,.txt,.md,.srt,.vtt";
drop.addEventListener("click", (e) => { if (e.target !== input) input.accept = acceptedEvidence; });
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.accept = acceptedEvidence; input.click(); } });
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; input.accept = "image/*,video/*,audio/*,.txt,.md,.srt,.vtt"; });
["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
drop.addEventListener("drop", (ev) => { ev.preventDefault(); addFiles(ev.dataTransfer.files); });
function guessMod(f) {
  const t = f.type || "";
  if (t.startsWith("image/")) return "image";
  if (t.startsWith("video/")) return "video";
  if (t.startsWith("audio/")) return "audio";
  if (/\.(txt|md|srt|vtt)$/i.test(f.name)) return "text";
  return null;
}
function addFiles(list) { picked.push(...list); renderFiles(); }
function renderFiles() {
  $("#filelist").innerHTML = picked.map((f, i) => {
    const m = guessMod(f);
    return `<li>${m ? `<span class="mod ${MOD[m][0]}">${MOD[m][1]}</span>` : '<span class="mod">?</span>'}<span class="fn">${esc(f.name)}</span>
      <span class="muted">${f.size > 1048576 ? (f.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(f.size / 1024)) + " KB"}</span><a href="#" data-i="${i}">remove</a></li>`;
  }).join("") + (picked.length ? `<li class="muted small">The type is decided from the file content on the server, not from the name.</li>` : "");
  $$("#filelist a").forEach((a) => a.onclick = (e) => { e.preventDefault(); picked.splice(+a.dataset.i, 1); renderFiles(); });
}
$("#analyze").onclick = () => {
  if (!picked.length && !$("#caption").value.trim()) return intakeError("Add at least one file or some text.");
  const fd = new FormData();
  picked.forEach((f) => fd.append("files", f));
  const reference = $("#referenceImageFile").files[0];
  if (reference) fd.append("reference_image", reference);
  fd.append("caption", $("#caption").value);
  fd.append("title", $("#title").value);
  run(() => fetch("/api/analyze", { method: "POST", body: fd }), `${picked.length} file(s)${$("#caption").value.trim() ? " and a caption" : ""}`);
};
function intakeError(msg) {
  const e = $("#intakeMsg"); e.textContent = msg; e.classList.remove("hidden");
  view(C ? "case" : "empty"); document.body.classList.add("rail-open");
}

async function run(req, what) {
  $("#intakeMsg").classList.add("hidden");
  $("#runningInfo").textContent = `Hashing, validating and analysing ${what}. Large videos take longer.`;
  closePanels();
  view("running");
  $$("#analyze, #demos button").forEach((x) => x.disabled = true);
  try {
    const r = await req();
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const rep = await r.json();
    picked = []; renderFiles(); $("#caption").value = ""; $("#title").value = ""; $("#referenceImageFile").value = "";
    openCase(rep);
    await loadHistory();
  } catch (e) {
    intakeError("Analysis failed: " + e.message);
  } finally { $$("#analyze, #demos button").forEach((x) => x.disabled = false); }
}

// ---------------------------------------------------------------- left panel: history, samples, registry
let CASES = [], hq = "";
async function loadHistory() {
  try { CASES = await api("/api/cases"); renderHistory(); }
  catch (e) { $("#history").innerHTML = `<li class="error">Could not load investigations: ${esc(e.message)} <button class="small" id="retryHistory">Retry</button></li>`; $("#retryHistory").onclick = loadHistory; }
}
function renderHistory() {
  const q = hq.trim().toLowerCase();
  const list = CASES.filter((c) => !q || `${c.title} ${c.case_id} ${c.label || ""} ${(c.top_finding || {}).indicator || ""}`.toLowerCase().includes(q));
  $("#hcount").textContent = CASES.length ? `${list.length} of ${CASES.length}` : "";
  $("#history").innerHTML = list.length ? list.map((c) =>
    `<li data-id="${esc(c.case_id)}"><div class="t">${esc(c.title)}</div><div class="meta"><span>${esc(c.created_at.slice(0, 16).replace("T", " "))}
      ${Object.keys(c.modalities || {}).map((m) => MOD[m] ? `<i class="mdot ${MOD[m][0]}" title="${MOD[m][1]}"></i>` : "").join("")}</span>
     <b class="band-${c.band}" title="overall manipulation indicator">${c.score.toFixed(2)}</b></div></li>`).join("")
    : `<li class="muted">${CASES.length ? "No investigation matches this search." : "No investigations yet"}</li>`;
  $$("#history li[data-id]").forEach((li) => li.onclick = async () => {
    try { const report = await api(`/api/cases/${li.dataset.id}`); closePanels(); openCase(report); }
    catch (e) { li.textContent = "Could not open case: " + e.message; }
  });
  markHistory(C && C.case_id);
}
$("#hq").oninput = (ev) => { hq = ev.target.value; renderHistory(); };
function markHistory(id) { $$("#history li[data-id]").forEach((li) => li.classList.toggle("on", li.dataset.id === id)); }
async function loadRegistry() {
  let list;
  try { list = await api("/api/registry"); }
  catch (e) { $("#registry").innerHTML = `<li class="error">Could not load registry: ${esc(e.message)} <button class="small" id="retryRegistry">Retry</button></li>`; $("#retryRegistry").onclick = loadRegistry; return; }
  const c2 = { valid: "C2PA valid", valid_untrusted: "C2PA valid (untrusted signer)", invalid: "C2PA broken" };
  $("#regcount").textContent = `(${list.length})`;
  $("#registry").innerHTML = list.length ? list.map((e) => `<li><img src="/api/registry/${e.id}/file" alt=""><div><b>${esc(e.id)}</b> ${esc(e.filename)}
    <div class="src">${esc(e.source || "no source")}${c2[e.c2pa] ? " · " + c2[e.c2pa] : ""}</div></div></li>`).join("") : '<li class="muted">Registry is empty</li>';
}
$("#regfiles").addEventListener("change", async () => {
  const files = [...$("#regfiles").files]; if (!files.length) return;
  const fd = new FormData(); files.forEach((f) => fd.append("files", f)); fd.append("source", $("#regsource").value);
  $("#regfiles").disabled = true; $("#regmsg").textContent = "Registering originals…";
  try { const body = await api("/api/registry", { method: "POST", body: fd }); $("#regmsg").textContent = `Registered ${body.map((e) => e.id).join(", ")}`; }
  catch (e) { $("#regmsg").textContent = "Not registered: " + e.message; }
  finally { $("#regfiles").disabled = false; $("#regfiles").value = ""; await loadRegistry(); }
});
(async () => {
  view("empty");
  try {
    const s = await api("/api/status"), el = $("#status");
    el.textContent = s.ai_enabled ? `AI review enabled · ${s.model}` : "Rule-based forensic engine · AI off";
    el.classList.toggle("on", s.ai_enabled);
    $("#demos").innerHTML = s.demo_available ? Object.entries(s.demos || {}).map(([k, t]) => `<button class="small" data-demo="${k}">${esc(t.replace("Demo: ", ""))}<span aria-hidden="true"> ↗</span></button>`).join("") : '<p class="muted">Bundled sample files are unavailable.</p>';
    $$("#demos button").forEach((b) => b.onclick = () => run(() => fetch(`/api/demo?scenario=${b.dataset.demo}`, { method: "POST" }), "the sample files"));
  } catch (e) { $("#status").textContent = "Engine status unavailable"; $("#demos").innerHTML = `<p class="error">Could not load samples: ${esc(e.message)}</p>`; }
  await Promise.all([loadHistory(), loadRegistry()]);
})();

// ---------------------------------------------------------------- helpers
const art = (name) => `/api/cases/${C.case_id}/artifacts/${encodeURIComponent(name)}`;
const evSrc = (id) => `/api/cases/${C.case_id}/evidence/${id}`;
const chip = (id) => `<button class="chip" data-ref="${esc(id)}">${esc(id)}</button>`;
const allFindings = () => C.evidence.flatMap((e) => e.findings.map((f) => ({ ...f, ev: e.id })));
const submittedFindings = () => allFindings().filter(f => C.evidence.find(e => e.id === f.ev)?.role !== "reference");
const findingById = (id) => allFindings().find((f) => f.id === id);
const corrFor = (fid) => C.correlations.filter((c) => (c.finding_refs || []).includes(fid));
function locText(l) {
  if (!l) return "";
  if (l.type === "region") return `region x${l.bbox[0]} y${l.bbox[1]}, ${l.bbox[2]}×${l.bbox[3]} px`;
  if (l.type === "frame") return `frame ${l.time.toFixed(2)} s` + (l.times ? `, seen in ${l.times.length} frames` : "");
  if (l.type === "segment") return `${l.start.toFixed(2)}–${l.end.toFixed(2)} s`;
  if (l.type === "metadata") return `metadata: ${l.field}`;
  if (l.type === "text") return `text: “${(l.quote || "").slice(0, 50)}”`;
  return "whole file";
}
const seekTime = (l) => l && (l.type === "frame" ? l.time : l.type === "segment" ? l.start : null);
// What kind of evidence a finding is: verified facts are kept apart from heuristic and experimental indicators.
function kindOf(f) {
  if (f.experimental) return ["exp", "Experimental"];
  if (["registry", "registry-match", "registry-similar", "history"].includes(f.category) || /content credentials/i.test(f.indicator)) return ["ver", "Verified check"];
  if (f.modality === "provenance") return ["cmp", "Compared with original"];
  if (f.score <= 0) return ["info", "Info"];
  return ["heu", "Heuristic indicator"];
}
function meter(score, strength) {
  const b = bandOf(score);
  return `<span class="meter" title="${strength} (${score.toFixed(2)}); heuristic strength, not a probability"><span class="fill band-bg-${b}" style="width:${Math.max(4, score * 100)}%"></span></span>`;
}

// ---------------------------------------------------------------- case workspace
const TABS = [["overview", "Overview"], ["visual", "Visual"], ["audio", "Audio"], ["text", "Text"], ["cross", "Cross-modal"]];
const TAB_MODS = { visual: ["image", "video"], audio: ["audio"], text: ["text"] };
const tabOf = (e) => (e.modality === "audio" ? "audio" : e.modality === "text" ? "text" : "visual");
const curEv = () => C.evidence.find((x) => x.id === sel.ev) || C.evidence[0];

function openCase(rep) {
  C = rep; G = null;
  $("#headerCase").textContent = rep.case_id;
  const candidates = rep.evidence.filter(e => e.role !== "reference");
  const first = candidates.find(e => e.reference_comparison) || candidates.find((e) => e.findings.some((f) => f.score >= 0.4)) || candidates[0] || rep.evidence[0];
  sel = { ...sel, ev: first && first.id, f: null, tab: "overview", kf: "all" };
  view("case");
  markHistory(rep.case_id);
  renderCase();
}

const ICON = {
  warn: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M12 3 2 21h20L12 3z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v5M12 18v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  ok: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="m8 12 3 3 5-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};
// Strongest signal per analysis engine, read from the findings and correlations of this case.
function signals() {
  const F = submittedFindings();
  const grp = [["Image", (f) => f.modality === "image"], ["Provenance", (f) => f.modality === "provenance"], ["Video", (f) => f.modality === "video"],
    ["Audio", (f) => f.modality === "audio"], ["Text", (f) => f.modality === "text"]];
  const out = grp.map(([n, t]) => { const L = F.filter(t); return L.length ? { n, max: Math.max(...L.map((f) => f.score)), flags: L.filter((f) => f.score >= 0.4).length, total: L.length } : null; }).filter(Boolean);
  const X = C.correlations;
  if (X.length) out.push({ n: "Cross-modal", max: null, flags: X.filter((c) => c.effect === "strengthens").length, total: X.length, cross: true });
  return out;
}
function procTime() {
  const L = C.log || []; if (L.length < 2) return "n/a";
  const s = (Date.parse(L[L.length - 1].time) - Date.parse(L[0].time)) / 1000;
  return s < 1 ? "under 1 s" : `${s.toFixed(0)} s`;
}

function renderCase() {
  const e = curEv();
  $("#case").innerHTML = `
    <div class="case-top">
      <section class="card preview" aria-label="Evidence preview">
        <div class="pv-head"><div><span class="eyebrow">02 / EVIDENCE VIEWER</span><h3 title="${esc(C.title)}">${esc(C.title || "Evidence preview")}</h3></div><div class="ev-chips">${C.evidence.map(evBtn).join("")}</div></div>
        ${e ? previewHtml(e) : '<p class="muted">No evidence in this case.</p>'}
      </section>
      <aside class="side-r" aria-label="Separate content assessments">${verdictHtml()}${aiAssessmentHtml()}${originAssessmentHtml()}${tilesHtml()}</aside>
    </div>
    <nav class="tabs" role="tablist">${TABS.map(([k, n]) => `<button role="tab" id="tab-${k}" aria-controls="tabBody" data-tab="${k}">${n}${tabCount(k)}</button>`).join("")}</nav>
    <div id="tabBody" role="tabpanel" tabindex="0"></div>
    ${footerHtml(e)}
    <div class="card hidden" id="pdfBox"><h3>PDF report preview <button class="small ghost" id="pdfClose">Close</button></h3><iframe title="PDF report preview" class="pdfframe"></iframe></div>`;
  $$(".tabs button").forEach((b) => b.onclick = () => {
    const k = b.dataset.tab, cur = curEv();
    sel.tab = k;
    const mods = TAB_MODS[k], first = mods && C.evidence.find((x) => mods.includes(x.modality));
    if (mods && first && !(cur && mods.includes(cur.modality))) { sel.ev = first.id; sel.f = null; sel.kf = "all"; renderCase(); }
    else renderTab();
  });
  wire($(".case-top"));
  const syncAssessmentButton = $("[data-assessment-syncid]");
  if (syncAssessmentButton) syncAssessmentButton.onclick = () => $("#syncidPanel").showModal();
  wire($(".case-footer"));
  $$(".tabs button").forEach((button, i, buttons) => button.onkeydown = (ev) => { let next; if (ev.key === "ArrowRight") next = (i + 1) % buttons.length; if (ev.key === "ArrowLeft") next = (i + buttons.length - 1) % buttons.length; if (ev.key === "Home") next = 0; if (ev.key === "End") next = buttons.length - 1; if (next != null) { ev.preventDefault(); buttons[next].click(); $("#tab-" + TABS[next][0]).focus(); } });
  renderTab();
}
function tabCount(k) {
  if (k === "cross") return ` <small>${C.correlations.length}</small>`;
  const mods = TAB_MODS[k]; if (!mods) return "";
  const n = C.evidence.filter((x) => mods.includes(x.modality)).length;
  return n ? ` <small>${n}</small>` : ' <small class="np">none</small>';
}
function renderTab() {
  $$(".tabs button").forEach((b) => { const active = b.dataset.tab === sel.tab; b.classList.toggle("on", active); b.setAttribute("aria-selected", String(active)); b.tabIndex = active ? 0 : -1; });
  const body = $("#tabBody");
  body.setAttribute("aria-labelledby", "tab-" + sel.tab);
  body.innerHTML = { overview: overviewTab, visual: () => modTab("visual"), audio: () => modTab("audio"), text: () => modTab("text"), cross: crossTab }[sel.tab]();
  wire(body);
  if (sel.tab === "cross" || sel.tab === "overview") {
    drawGraph();
    $$("[data-eff], #gFind").forEach((i) => i.onchange = () => drawGraph(true));
    const rs = $("#gReset"); if (rs) rs.onclick = () => { G = null; drawGraph(); };
    $$(".corr[data-x]").forEach((d) => d.onmouseenter = () => graphDetail(d.dataset.x));
  }
}
function evBtn(e) {
  return `<button class="ev-item ${e.id === sel.ev ? "on" : ""}" data-ev="${e.id}" title="${esc(e.filename)}"><span class="mod ${MOD[e.modality][0]}">${MOD[e.modality][1]}</span>
    <span class="ev-name"><b>${esc(e.id)}</b> ${esc(e.filename)}</span><span class="ev-ind band-${bandOf(e.score)}">${e.score.toFixed(2)}</span></button>`;
}

// ---- right column: verdict, per-media tiles, report
function verdictHtml() {
  const o = C.overall, hasRange = Array.isArray(o.range) && o.range.length === 2 && o.range.every(Number.isFinite);
  const incomplete = C.analysis_status === "partial" || C.evidence.some(e => e.analysis_status === "failed" || (e.limitations || []).some(l => l.startsWith("Analysis failed:")));
  const compared = C.evidence.filter(e => e.reference_comparison?.regions?.length);
  return `<div class="verdict band-border-${o.band}"><span class="eyebrow">03 / MANIPULATION ASSESSMENT</span>
    ${incomplete ? '<p class="error"><b>Analysis incomplete.</b> A failed check is not a clean result. Original evidence is retained; review the evidence limitations.</p>' : ""}
    ${compared.length ? '<p class="reference-observation"><b>Changed regions observed versus supplied reference.</b> Review the difference map. This confirms pixel differences, not AI use; it does not add an artificial score boost.</p>' : ""}
    <div class="v-alert band-${o.band}">${o.band === "low" ? ICON.ok : ICON.warn}<div><b>${esc(o.label)}</b><span>${o.band === "low" ? "No strong manipulation indicator found" : "Human review required"}</span></div></div>
    <div class="v-num"><b class="band-${o.band}">${o.score.toFixed(2)}</b><span>overall manipulation indicator<br>0 = none · 1 = strong</span></div>
    ${hasRange ? `<div class="rangebar" title="Range ${o.range[0].toFixed(2)}–${o.range[1].toFixed(2)}: wider when less evidence is available">
      <div class="win" style="left:${o.range[0] * 100}%;width:${(o.range[1] - o.range[0]) * 100}%"></div><div class="pt" style="left:${o.score * 100}%"></div></div>
    <div class="v-range">uncertainty range ${o.range[0].toFixed(2)}–${o.range[1].toFixed(2)}</div>` : '<div class="v-range muted">Score range unavailable</div>'}
    ${(o.uncertainty_notes || []).length ? `<details class="v-note"><summary>Why this range?</summary>${esc(o.uncertainty_notes.join("; "))}</details>` : ""}
    <div class="proofnote">${ICON.info}<div><b>Heuristic indicator, not a probability.</b> A low score does not rule out AI generation or prove camera capture. Existing metadata/provenance contributions are preserved.</div></div>
  </div>`;
}
function aiAssessmentHtml() {
  const ai = C.assessments?.ai_generation;
  if (!ai) return `<section class="card ai-assessment" aria-label="AI-generation assessment"><span class="eyebrow">AI-GENERATION ASSESSMENT</span><h3>Not assessed</h3><p class="small muted">This saved report predates the separate assessment. Re-run analysis to assess available evidence.</p></section>`;
  const tone = ["evidence_detected", "possible"].includes(ai.status) ? "warn" : "muted";
  return `<section class="card ai-assessment" aria-label="AI-generation assessment"><span class="eyebrow">AI-GENERATION ASSESSMENT</span><h3 class="${tone}">${esc(ai.label)}</h3>
    <p class="small">${ai.evidence.length ? `${ai.evidence.length} supporting evidence item(s).` : "No positive generation evidence found in available checks."} This does not establish camera capture.</p>
    ${ai.confidence_calibrated === true && Number.isFinite(ai.confidence) ? `<p class="small">Calibrated confidence: ${esc(ai.confidence)}</p>` : '<p class="small muted">Confidence not reported: methods are not statistically calibrated.</p>'}
    <details><summary>Evidence, methods &amp; limitations</summary>
      ${(ai.items || []).map(item => `<div class="assessment-item"><b>${esc(item.evidence_id)} · ${esc(item.filename)}</b><p class="small">${esc(item.label)}</p>
        <ul class="plain small">${item.evidence.length ? item.evidence.map(entry => `<li>${esc(entry.detail)}${entry.refs?.length ? ` (${esc(entry.refs.join(", "))})` : ""}</li>`).join("") : '<li>No positive evidence in available checks; origin remains unknown.</li>'}</ul>
        <p class="small muted">Methods: ${item.methods.map(method => `${esc(method.name)} — ${esc(method.status)}`).join("; ")}</p></div>`).join("")}
      <ul class="plain small muted">${ai.limitations.map(limit => `<li>${esc(limit)}</li>`).join("")}</ul></details></section>`;
}
function originAssessmentHtml() {
  const origin = C.assessments?.content_provenance;
  return `<section class="card assessment-origin" aria-label="Content provenance assessment"><span class="eyebrow">CONTENT PROVENANCE</span><h3>Origin not independently established</h3>
    <details><summary>Credentials and declared origin</summary>${(origin?.items || []).map(item => `<p class="small"><b>${esc(item.evidence_id)}</b> · C2PA: ${esc(item.c2pa_status)}${item.declared_signer ? `<br>Declared signer: ${esc(item.declared_signer)}` : ""}${item.declared_device ? `<br>Declared device: ${esc(item.declared_device)}` : ""}<br>Issuer trust: ${esc(item.issuer_trust)}<br>${esc(item.note)}</p>`).join("") || '<p class="small muted">No separate provenance summary available. See evidence details for existing checks.</p>'}</details>
    <p class="small muted">An intact credential or registry match does not independently prove who created the content.</p></section>
    <section class="card assessment-sync" aria-label="Sync ID verification"><span class="eyebrow">SYNC ID VERIFICATION</span><h3>Not assessed in this report</h3><p class="small muted">Compare registered bytes and Ed25519 signatures explicitly. Issuer trust remains separate.</p><button class="ghost small" data-assessment-syncid>Open Sync ID verification</button></section>`;
}
function tilesHtml() {
  return `<div class="mtiles">${Object.entries(MOD).map(([m, [cls, name]]) => {
    const items = C.evidence.filter((x) => x.modality === m && x.role !== "reference");
    if (!items.length) return `<div class="mtile none"><span class="mod ${cls}">${name}</span><b>Not provided</b><i>no ${name.toLowerCase()} evidence submitted, so nothing was checked</i></div>`;
    const F = items.flatMap((x) => x.findings), max = Math.max(...items.map((x) => x.score)), ind = F.filter((f) => f.score >= 0.4).length;
    const failed = items.some((x) => (x.limitations || []).some((l) => l.startsWith("Analysis failed:")));
    const from = items.every((x) => x.derived_from) ? ` · extracted from ${items.map((x) => x.derived_from).join(", ")}` : "";
    return `<button class="mtile band-border-${bandOf(max)}" data-mtile="${m}" title="Strongest item indicator among ${items.length} ${name.toLowerCase()} item(s); click to open">
      <span class="mod ${cls}">${name}</span><b class="band-${bandOf(max)}">${max.toFixed(2)}</b><i>${items.length} item(s)${from} · ${ind} of ${F.length} finding(s) ≥ 0.40</i><span class="mod-status ${failed ? "bad" : ""}">${failed ? "Analysis failed / incomplete" : "Analyzed · " + (ind ? "Review indicators" : "No strong indicators")}</span></button>`;
  }).join("")}</div>`;
}
// ---- centre: the selected evidence item's main visual
function previewHtml(e) {
  const main = { image: imageMain, video: videoMain, audio: audioMain, text: textMain }[e.modality](e);
  return `<div class="pv-meta"><span class="mod ${MOD[e.modality][0]}">${MOD[e.modality][1]}</span><b>${esc(e.id)}</b><span class="pv-fn">${esc(e.filename)}</span>
      <span class="muted small">${esc(e.detected_type || "?")} · ${e.size_bytes.toLocaleString()} bytes${e.derived_from ? ` · extracted from ${esc(e.derived_from)}` : ""}</span>
      <span class="pv-score small">${e.role === "reference" ? "Reference context · excluded from case scoring" : `item indicator <b class="band-${bandOf(e.score)}">${e.score.toFixed(2)}</b>`}</span></div>
    ${(e.validation_warnings || []).map((w) => `<div class="warn small">${esc(w)}</div>`).join("")}
    <div class="stage">${main}</div>`;
}

// ---- tabs
function keyRow(f) {
  const e = C.evidence.find((x) => x.id === f.ev), [k, kl] = kindOf(f);
  return `<button class="keyf" data-go="${f.id}"><span class="fid">${f.id}</span><span class="mod ${MOD[e.modality][0]}">${MOD[e.modality][1]}</span>
    <span class="kf-ind">${esc(f.indicator)}<span class="small muted"> · ${esc(e.id)} · ${esc(locText(f.location))}</span></span>
    <span class="k ${k}">${kl}</span><span class="kf-sc band-${bandOf(f.score)}">${f.score.toFixed(2)}</span></button>`;
}
function regText(e) {
  if (!e.provenance) return '<span class="muted">not applicable (the registry holds images)</span>';
  const r = e.provenance.registry || {};
  if (r.exact) return `<span class="ok">exact SHA-256 match</span> ${chip(r.exact.id)} ${esc(r.exact.filename)}${r.exact.source ? " · " + esc(r.exact.source) : ""}`;
  if ((r.similar || []).length) return `near copy of ${r.similar.map((x) => `${chip(x.id)} ${esc(x.filename)} (perceptual distance ${x.distance}/64)`).join(", ")}; not an exact match`;
  return '<span class="muted">no match (not evidence of manipulation)</span>';
}
function footerHtml(e) {
  const p = e?.provenance, reg = p?.registry, similar = reg?.similar || [], state = custodyState(C.custody_verification);
  const match = !p ? "Unavailable for this evidence" : reg?.exact ? `Exact match · ${reg.exact.id}` : similar.length ? `Similar original · ${similar[0].id}` : "No registry match";
  const similarity = similar.length ? similar.map((r) => `${r.id}: ${r.distance}/64 distance`).join(" · ") : reg?.exact ? "Exact file identity (SHA-256)" : "No perceptual match available";
  return `<footer class="case-footer" aria-label="Selected evidence and case information"><div class="footer-field"><span class="footer-label">SHA-256 · ${esc(e?.id || "EVIDENCE")}</span><span class="footer-value mono" title="${esc(e?.sha256 || "Unavailable")}">${esc(e?.sha256 || "Unavailable")}</span></div><div class="footer-field footer-registry"><span class="footer-label">REGISTRY / PERCEPTUAL SIMILARITY</span><span class="footer-value" title="${esc(match)}">${esc(match)}</span><span class="footer-value muted" title="${esc(similarity)}">${esc(similarity)}</span></div><div class="footer-field footer-custody"><span class="footer-label">CUSTODY CHAIN</span><span class="footer-value ${state.cls}" data-custody-state>${state.label}</span></div><div class="footer-actions"><a class="btn primary" id="genPdf" href="/api/cases/${C.case_id}/report.pdf" target="_blank" rel="noopener">Generate PDF ↗</a><button class="ghost small" id="pdfPrev">Preview</button><a class="btn ghost small" href="/api/cases/${C.case_id}/report.json">JSON ↓</a></div></footer>`;
}
function overviewTab() {
  const all = submittedFindings(), weighted = all.filter((f) => f.score > 0).sort((a,b) => b.score-a.score);
  const audio = C.evidence.find((e) => e.modality === "audio"), video = C.evidence.find((e) => e.modality === "video");
  const kinds = KINDS.map(([key, label]) => { const count = all.filter((f) => kindOf(f)[0] === key).length; return count ? `<span class="k ${key}">${label} · ${count}</span>` : ""; }).join("");
  const segments = audio ? audio.findings.filter((f) => f.location?.type === "segment") : [];
  const graph = corrTab().split('<div class="card"><h3>Relations (')[0];
  return `<div class="overview-grid"><div class="card"><h3>Key findings <span class="muted small">${all.length} findings · ${all.filter((f) => f.score >= .4).length} review indicators</span></h3><div class="classification-tags">${kinds || '<span class="muted small">No findings recorded</span>'}</div><div class="keylist">${weighted.length ? weighted.slice(0,4).map(keyRow).join("") : '<p class="muted small">No weighted manipulation indicators. This does not establish authenticity.</p>'}</div>${C.evidence.filter((x) => x.people && !x.people.error).map((x) => `<button class="people-line" data-people-ev="${x.id}"><span class="k exp">Experimental</span> <b>People · ${esc(x.id)}</b> <span class="small">${esc(x.people.summary)}</span></button>`).join("")}</div><div class="card"><h3>Audio signal <span class="muted small">${audio ? esc(audio.id) + " · " + esc(audio.filename) : "Not provided"}</span></h3>${audio ? waveHtml(audio,audioDur(audio),segments) + `<div class="seglist">${segments.length ? segments.map((f) => `<button class="segbtn" data-evid="${audio.id}" data-seek="${f.location.start}" title="${esc(f.indicator)}">${esc(f.id)} · ${f.location.start.toFixed(2)}–${f.location.end.toFixed(2)} s · suspicious segment</button>`).join("") : '<span class="muted small">No suspicious audio segments flagged.</span>'}</div>` : '<p class="muted small">No audio evidence was submitted or extracted.</p>'}${video ? videoFrames(video, true) : '<h4>Video frames</h4><p class="muted small">Not provided</p>'}</div>${graph}</div><details class="overview-details"><summary>Full case details · summary, provenance, custody verification &amp; limitations</summary>${caseDetailsHtml()}</details>`;
}
function caseDetailsHtml() {
  const all = submittedFindings(), F = all.filter((f) => f.score > 0).sort((a, b) => b.score - a.score);
  const strong = C.correlations.filter((c) => c.effect === "strengthens");
  const integ = (id) => (C.integrity.find((i) => i.id === id) || {}).verified;
  return `<div class="ov-grid">
    <div class="card"><h3>Key findings <span class="muted small">${all.filter((f) => f.score >= 0.4).length} of ${all.length} finding(s) at 0.40 or above · strongest first · click to open the evidence</span></h3>
      <div class="keylist">${F.length ? F.slice(0, 6).map(keyRow).join("") : '<p class="muted">No manipulation indicator was raised. That does not prove the media is authentic.</p>'}</div>
      ${F.length > 6 ? `<p class="small muted">${F.length - 6} more weighted finding(s) in the Visual, Audio and Text tabs.</p>` : ""}
      ${strong.length ? `<h4>Cross-modal relations that strengthen the indicator</h4><ul class="plain small">${strong.map((c) => `<li>${chip(c.id)} ${esc(c.title)}</li>`).join("")}</ul>` : ""}
      <h4>Summary <span class="k ${C.summary_source === "ai" ? "ai" : "info"}">${C.summary_source === "ai" ? "AI-assisted" : "rule-based"}</span></h4><p>${linkify(esc(C.summary))}</p>
      <p class="small muted">${esc(C.ai.note)}</p></div>
    <div class="card"><h3>Case info</h3>
      <dl class="facts two">
        <div><dt>Case ID</dt><dd class="mono">${esc(C.case_id)}</dd></div>
        <div><dt>Investigation UUID</dt><dd class="mono">${esc(C.investigation_uuid || "n/a")}</dd></div>
        <div><dt>Engine</dt><dd>${esc((C.tool || {}).name || "TruthLens")} ${esc((C.tool || {}).version || "")} · ${C.summary_source === "ai" ? "AI-assisted review" : "rule-based"}</dd></div>
        <div><dt>Processing time</dt><dd>${procTime()} <span class="muted">(from the custody log)</span></dd></div></dl>
      <h4>Evidence, SHA-256 and registry match</h4>
      ${C.evidence.map((e) => { const ok = integ(e.id); return `<div class="evrow"><div class="er-top"><span class="mod ${MOD[e.modality][0]}">${MOD[e.modality][1]}</span>${chip(e.id)}<span class="pv-fn">${esc(e.filename)}</span>
        <span class="small ${ok === true ? "ok" : ok === false ? "bad" : "muted"}">${ok === true ? "✓ hash re-verified" : ok === false ? "✗ hash mismatch" : "Hash verification unavailable"}</span></div>
        <div class="mono small">SHA-256 ${esc(e.sha256)}</div><div class="small">Registry: ${regText(e)}</div></div>`; }).join("")}</div>
  </div>
  <div class="ov-grid">${custodyCard()}
    <div class="card"><h3>Recommended human checks</h3><ul class="plain">${C.recommended_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
      <h4>What this analysis cannot establish</h4><ul class="plain small">${(C.cannot_establish || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
      <details><summary>Limitations (${C.limitations.length})</summary><ul class="plain small">${C.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details></div>
  </div>`;
}
function modTab(k) {
  const items = C.evidence.filter((x) => TAB_MODS[k].includes(x.modality));
  const what = { visual: "image or video", audio: "audio", text: "text or caption" }[k];
  if (!items.length) return `<div class="card notprov"><h3>${TABS.find((t) => t[0] === k)[1]}</h3><p><b>Not provided.</b> No ${what} evidence was submitted in this case, so these checks did not run.
    This is not a result: it says nothing about whether the other media were manipulated.</p></div>`;
  const e = items.find((x) => x.id === sel.ev) || items[0];
  const extras = { image: (x) => imageExtras(x) + provCard(x), video: videoFrames, audio: audioExtras, text: textExtras }[e.modality](e);
  return `${items.length > 1 ? `<div class="ev-chips tabpick"><span class="small muted">Showing</span>${items.map(evBtn).join("")}</div>` : ""}
    <div class="split"><div class="col-find"><div class="card findcard"><h3>Findings · ${esc(e.id)} ${esc(e.filename)} <span class="muted small">(${e.findings.length}) · strongest first · hover to see where, click to select</span></h3>
      ${kindFilter(e)}
      <div class="findings">${findingsHtml(e)}</div>
      ${e.limitations.length ? `<details><summary>Limitations of these checks (${e.limitations.length})</summary><ul class="plain">${e.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>` : ""}
      ${metaTable(e)}</div></div>
    <div class="col-extra">${extras}</div></div>`;
}
function crossTab() { return corrTab() + routingCard() + invTable(); }

// Which findings are measured facts, which are compared with a reference, which are heuristic or experimental.
const KINDS = [["ver", "Verified check", "Exact, reproducible checks: hashes, registry matches, content credentials."],
  ["cmp", "Compared with original", "Measured against a registered original: pixel and sensor-noise differences."],
  ["heu", "Heuristic indicator", "Statistical signals tuned by hand; consistent with editing, not proof of it."],
  ["exp", "Experimental", "Not validated on labelled real-world data; never used as a verdict on its own."],
  ["info", "Info", "Context only; carries no weight in the indicator."]];
function kindFilter(e) {
  const n = (k) => e.findings.filter((f) => kindOf(f)[0] === k).length;
  return `<div class="kfilter">${[["all", "All", e.findings.length, "Show every finding"], ...KINDS.map(([k, l, d]) => [k, l, n(k), d])]
    .map(([k, l, c, d]) => `<button class="kf ${k !== "all" ? "k " + k : ""} ${sel.kf === k ? "on" : ""}" data-kf="${k}" title="${esc(d)}" ${c || k === "all" ? "" : "disabled"}>${l} <b>${c}</b></button>`).join("")}</div>`;
}
function findingsHtml(e) {
  const list = e.findings.filter((f) => sel.kf === "all" || kindOf(f)[0] === sel.kf).sort((a, b) => b.score - a.score);
  if (!e.findings.length) return '<div class="muted">No indicators flagged for this item. That does not prove it is authentic.</div>';
  return list.length ? list.map((f) => findingRow(f, e)).join("") : '<div class="muted small">No findings of this kind for this item.</div>';
}
function findingRow(f, e) {
  const [k, kl] = kindOf(f);
  const t = seekTime(f.location);
  const cs = corrFor(f.id);
  return `<div class="f ${sel.f === f.id ? "on" : ""}" id="f-${f.id}" data-f="${f.id}">
    <div class="f-top"><span class="fid">${f.id}</span><span class="k ${k}">${kl}</span>
      <span class="f-ind">${esc(f.indicator)}</span>${f.source === "ai" ? '<span class="k ai">AI</span>' : ""}
      <span class="f-str">${f.score > 0 ? `${meter(f.score, f.strength)}<span class="small">${f.strength} ${f.score.toFixed(2)}</span>` : '<span class="small muted">no weight</span>'}</span></div>
    <div class="f-loc"><button class="loc" data-f="${f.id}" ${t != null ? `data-seek="${t}"` : ""}>${esc(locText(f.location))}${t != null ? " ▶" : ""}</button>
      ${cs.length ? `<span class="small muted">linked: </span>${cs.map((c) => chip(c.id)).join("")}` : ""}</div>
    <div class="f-expl">${esc(f.explanation)}</div>
    <div class="f-trail small">${trail(f, e)}</div>
    ${f.alternatives && f.alternatives.length ? `<details><summary>Innocent explanations to rule out</summary><ul class="plain">${f.alternatives.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>` : ""}</div>`;
}

// Where this finding comes from: the evidence item, the check that produced it and the image layer that shows it.
function trail(f, e) {
  const A = e.artifacts || {};
  const layer = f.artifact && A[f.artifact];
  const parts = [`<span class="muted">evidence</span> ${chip(e.id)} <span class="mono">${esc(e.sha256.slice(0, 12))}…</span>`,
    `<span class="muted">check</span> <span class="mono">${esc(f.modality)}/${esc(f.category)}</span>`];
  if (layer && e.modality === "image") parts.push(`<button class="small ghost showlayer" data-show="${esc(art(layer))}" data-f="${f.id}">show in ${esc(layerName(f.artifact))}</button>`);
  else if (layer) parts.push(`<a href="${esc(art(layer))}" target="_blank" rel="noopener">open ${esc(layerName(f.artifact))}</a>`);
  return parts.join(" · ");
}
function layerName(k) {
  if (/_heatmap$/.test(k)) return "heatmap";
  if (/_ela$/.test(k)) return "error-level analysis";
  if (/_vs_R/.test(k)) return "difference vs original";
  if (/_spectrum$/.test(k)) return "frequency spectrum";
  if (/_spectrogram$/.test(k)) return "spectrogram";
  return k;
}
function metaTable(e) {
  const rows = flatMeta(e.metadata);
  return rows.length ? `<details><summary>Extracted metadata and measurements (${rows.length})</summary><table class="kv">${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table></details>` : "";
}
function flatMeta(m, prefix = "") {
  const out = [];
  for (const [k, v] of Object.entries(m || {})) {
    if (["timeline", "windows", "claims", "frames", "contact_sheet_times", "waveform"].includes(k)) continue;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flatMeta(v, prefix + k + "."));
    else if (v !== null && v !== "" && !(Array.isArray(v) && !v.length)) out.push([prefix + k, Array.isArray(v) ? JSON.stringify(v) : String(v)]);
  }
  return out;
}

// ---- image
function imageMain(e) {
  const A = e.artifacts || {};
  const cmp = Object.keys(A).find((k) => k.includes("_vs_R"));
  const heatLayer = A[`${e.id}_heat`] && art(A[`${e.id}_heat`]);
  const layers = [["upload", heatLayer ? "Image + heatmap" : "Uploaded image", evSrc(e.id)], ["heat", "Rendered heatmap", A[`${e.id}_heatmap`] && art(A[`${e.id}_heatmap`])],
    ["reference", "Reference difference map", A[`${e.id}_reference_diff`] && art(A[`${e.id}_reference_diff`])],
    ["cmp", "Difference vs original", cmp && art(A[cmp])], ["ela", "Error level analysis", A[`${e.id}_ela`] && art(A[`${e.id}_ela`])],
    ["spec", "Spectrum (experimental)", A[`${e.id}_spectrum`] && art(A[`${e.id}_spectrum`])]].filter((l) => l[2]);
  const regions = e.findings.filter((f) => f.location.type === "region");
  const pv = e.provenance || {};
  return `<div class="layer-tabs">${layers.map((l, i) => `<button class="small ${i ? "" : "on"}" data-layer="${l[2]}" data-kind="${l[0]}">${l[1]}</button>`).join("")}</div>
    <div class="stage-tools small">
      <label><input type="checkbox" id="showBoxes" ${sel.boxes ? "checked" : ""} ${regions.length ? "" : "disabled"}> Flagged regions (${regions.length})</label>
      ${peopleOf(e).length ? `<label title="Experimental: faces detected and head-and-shoulders regions estimated; not part of the manipulation score"><input type="checkbox" id="showPeople" ${sel.people ? "checked" : ""}> People <span class="k exp">Experimental</span> (${peopleOf(e).length})</label>` : ""}
      ${heatLayer ? `<label>Heatmap opacity <input type="range" id="heatOp" min="0" max="100" value="${sel.heatOp}"><b id="heatOpV">${sel.heatOp}%</b></label>` : ""}
      <span class="muted">double-click to enlarge</span></div>
    <div class="img-stage"><img id="stageImg" src="${layers[0][2]}" alt="${esc(e.filename)}">
      ${heatLayer ? `<img id="heatImg" class="heat-ov" src="${heatLayer}" alt="" style="opacity:${sel.heatOp / 100}">` : ""}
      <svg id="stageSvg" class="boxes" preserveAspectRatio="none" style="${sel.boxes ? "" : "display:none"}"></svg>
      ${peopleOf(e).length ? `<svg id="peopleSvg" class="boxes people-ov" preserveAspectRatio="none" style="${sel.people ? "" : "display:none"}"></svg>` : ""}</div>
    ${heatLayer ? `<div class="heatscale small"><span>heatmap: low local inconsistency</span><i></i><span>high</span></div>` : ""}
    <div class="small muted stage-note">${regions.length ? `${regions.length} flagged region(s) outlined; click a box to open its finding.` : "No region-level findings in this image."}
      The heatmap combines the sensor-noise and error-level measurements behind the region findings.
      ${pv.reference ? ` Compared with registered original ${esc(pv.reference.id)} (${esc(pv.reference.filename)}).` : ""}</div>`;
}
const peopleOf = (e) => ((e && e.people && e.people.people) || []);
const PMEAS = [["noise", "Noise"], ["sharpness", "Sharpness"], ["blockiness", "JPEG blockiness"], ["brightness", "Brightness"], ["colour", "Colour cast"], ["boundary", "Outline noise step"]];
// Experimental people-consistency results: shown apart from the findings, never part of the manipulation score.
function peopleCard(e) {
  const pp = e.people;
  if (!pp) return "";
  const head = `<h3>People consistency <span class="k exp">Experimental</span> <span class="muted small">not part of the manipulation score</span></h3>`;
  if (pp.error) return `<div class="card people-card" id="peopleCard">${head}<p class="muted small">Not available for this image: ${esc(pp.error)}</p></div>`;
  const L = pp.people || [];
  const cell = (p, m) => p.measures[m] == null ? '<td class="muted">–</td>'
    : `<td class="${p.outliers.includes(m) ? "out" : ""}" title="robust z ${p.z[m] == null ? "n/a" : p.z[m]} vs the other people">${(+p.measures[m]).toFixed(2)}${p.z[m] == null ? "" : `<small>z ${p.z[m] > 0 ? "+" : ""}${(+p.z[m]).toFixed(1)}</small>`}</td>`;
  return `<div class="card people-card" id="peopleCard">${head}
    <p class="small muted">${esc(pp.detector)}. ${esc(pp.detects)} Each person is compared with the others in this photo; someone is highlighted only when ${pp.thresholds.min_people}+ people are compared and they differ by robust z ≥ ${pp.thresholds.z} on at least ${pp.thresholds.min_outlier_measures} measurements, one of them noise, sharpness, blockiness or outline step.</p>
    <p class="people-summary ${(pp.flagged || []).length ? "warn" : ""}">${esc(pp.summary)}</p>
    ${L.length ? `<div class="tablewrap"><table class="ptable"><tr><th>Person</th>${PMEAS.map(([, n]) => `<th>${n}</th>`).join("")}<th>Review</th></tr>
      ${L.map((p) => `<tr id="p-${p.id}" data-person="${p.id}" class="${p.flagged ? "flag" : ""}"><td><b>${p.id}</b><small>face ${p.face_bbox.join(" ")} · conf ${p.confidence}</small></td>${PMEAS.map(([m]) => cell(p, m)).join("")}<td>${p.flagged ? '<span class="badge warn">Review</span>' : '<span class="muted">–</span>'}</td></tr>
        ${p.flagged ? `<tr class="reasons"><td colspan="${PMEAS.length + 2}">${p.reasons.map((r) => `<div>• ${esc(r)}</div>`).join("")}</td></tr>` : ""}`).join("")}</table></div>` : ""}
    <details><summary>Possible innocent causes (${(pp.false_positives || []).length})</summary><ul class="plain small">${(pp.false_positives || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>
    <details><summary>Limitations (${(pp.limitations || []).length})</summary><ul class="plain small">${(pp.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>
    <p class="small muted">A highlighted person is not a finding that they were inserted, removed or AI-generated; it marks where a human should look. Turn on “People” above the image to see the boxes.</p></div>`;
}
function imageExtras(e) {
  const gen = e.findings.filter((f) => f.experimental || f.category === "noise-absent" || f.category === "synthetic-generation");
  const gr = e.generator || {}, g = { ...(gr.features || {}), raw_score: gr.raw_score, peak_checked: gr.peak_checked };
  return `${referenceComparisonCard(e)}${peopleCard(e)}${compareCard(e)}
  <div class="card gen">
    <h3>AI-generation indicators <span class="k exp">Experimental</span></h3>
    <p class="small muted">Frequency-domain and noise statistics that generated images often show. Not validated on a labelled real-world set; never used as a verdict on its own and no generator is named.</p>
    ${g.hf_ratio != null || g.peak != null ? `<div class="mrow"><span>High-frequency energy ratio</span><b>${g.hf_ratio != null ? (+g.hf_ratio).toFixed(3) : "n/a"}</b></div>
      <div class="mrow"><span>Periodic spectral peak${g.peak_checked === false ? " (not used for JPEG)" : ""}</span><b>${g.peak != null ? (+g.peak).toFixed(2) : "n/a"}</b></div>
      <div class="mrow"><span>Combined raw score (finding only if ≥ 0.45)</span><b>${g.raw_score != null ? (+g.raw_score).toFixed(2) : "n/a"}</b></div>` : ""}
    ${e.metadata.noise_level != null ? `<div class="mrow"><span>Median sensor-noise level (camera photos in our tests: 3–5)</span><b>${e.metadata.noise_level}</b></div>` : ""}
    <div class="small">${gen.length ? gen.map((f) => `${chip(f.id)} ${esc(f.indicator)}`).join("<br>") : '<span class="muted">No AI-generation indicator raised.</span>'}</div>
  </div>`;
}

// Side-by-side panes with one magnifier that follows the pointer across all of them (same relative position in each).
function referenceComparisonCard(e) {
  const comparison = e.reference_comparison;
  if (!comparison) return "";
  return `<div class="card"><h3>Supplied reference comparison</h3><p class="small">${esc(comparison.detail || comparison.status)}</p>
    <p class="small muted">Reference ${esc(comparison.reference_id)} · ${esc(comparison.method)}. AI object attribution is not established.</p>
    ${comparison.significant_change_fraction != null ? `<p class="small">Measured changed area: ${(comparison.significant_change_fraction * 100).toFixed(2)}% · ${comparison.regions.length} region(s).</p>` : ""}
    ${e.artifacts?.[`${e.id}_reference_diff`] ? `<img class="reference-diff-preview" src="${art(e.artifacts[`${e.id}_reference_diff`])}" alt="Measured pixel differences versus the supplied reference">` : ""}
    <p class="small muted">${comparison.limitations.map(esc).join(" ")}</p></div>`;
}
function compareCard(e) {
  const A = e.artifacts || {}, pv = e.provenance || {};
  const panes = [["Submitted evidence", evSrc(e.id)], ["Detected inconsistencies", A[`${e.id}_heatmap`] && art(A[`${e.id}_heatmap`])]];
  if (pv.reference) panes.push([`Registered original ${pv.reference.id}`, `/api/registry/${encodeURIComponent(pv.reference.id)}/file`]);
  else if (A[`${e.id}_ela`]) panes.push(["Error level analysis", art(A[`${e.id}_ela`])]);
  if (panes.filter((x) => x[1]).length < 2) return "";
  return `<div class="card">
    <h3>Compare ${pv.reference ? "with the registered original" : "layers"} <span class="muted small">move the pointer over any image to magnify the same spot in all of them</span></h3>
    <div class="cmp-grid">${panes.filter((x) => x[1]).map(([t, src], i) => `<figure class="cmp-pane"><div class="cmp-img"><img src="${src}" alt="${esc(t)}" draggable="false"><div class="lens"></div></div>
      <figcaption><b>(${"abc"[i]})</b> ${esc(t)}</figcaption></figure>`).join("")}</div>
    ${pv.reference ? `<div class="small muted">The original was matched by perceptual hash${pv.registry && pv.registry.similar && pv.registry.similar[0] ? ` (distance ${pv.registry.similar[0].distance}/64)` : ""}; differences between (a) and (c) were measured pixel by pixel.</div>` : ""}
  </div>`;
}
function wireCompare(R) {
  const panes = $$(".cmp-img", R); if (!panes.length) return;
  const Z = 3;
  const move = (fx, fy) => panes.forEach((p) => {
    const img = $("img", p), L = $(".lens", p), w = img.clientWidth, h = img.clientHeight, r = L.offsetWidth / 2 || 60;
    if (!w) return;
    L.style.display = "block";
    L.style.left = fx * w - r + "px"; L.style.top = fy * h - r + "px";
    L.style.backgroundImage = `url("${img.currentSrc || img.src}")`;
    L.style.backgroundSize = `${w * Z}px ${h * Z}px`;
    L.style.backgroundPosition = `${-(fx * w * Z - r)}px ${-(fy * h * Z - r)}px`;
  });
  const hide = () => panes.forEach((p) => $(".lens", p).style.display = "none");
  panes.forEach((p) => {
    const at = (cx, cy) => { const b = $("img", p).getBoundingClientRect(); move(Math.min(1, Math.max(0, (cx - b.left) / b.width)), Math.min(1, Math.max(0, (cy - b.top) / b.height))); };
    p.onmousemove = (ev) => at(ev.clientX, ev.clientY);
    p.onmouseleave = hide;
    p.ontouchmove = (ev) => { ev.preventDefault(); at(ev.touches[0].clientX, ev.touches[0].clientY); };
    p.ontouchend = hide;
  });
}

// ---- video
function videoMain(e) {
  const audio = C.evidence.find((x) => x.derived_from === e.id);
  return `<div class="video-player"><video controls playsinline preload="metadata" src="${evSrc(e.id)}" data-ev="${e.id}" aria-label="Original uploaded video"></video>
    <p class="small muted" data-video-status>Original uploaded video · ${esc(e.metadata.width || "?")} × ${esc(e.metadata.height || "?")} · ${esc(e.metadata.codec || "codec unknown")}. Frame-strip thumbnails are separate analysis samples.</p>
    <p class="small warn hidden" data-video-error></p>
    <div class="video-actions"><a class="btn ghost small" href="${evSrc(e.id)}" download>Download original video</a> <button class="ghost small" data-compatible-preview="${e.id}">Use compatible preview (derived)</button></div></div>
    ${timeline(e, audio)}`;
}
function videoFrames(e, compact = false) {
  const m = e.metadata, frames = m.frames || [];
  return `<div class="card"><h3>${compact ? "Video frame strip" : "Frame-by-frame"} <span class="muted small">${frames.length} frames shown of ${m.frames_sampled || 0} spatially analysed (one every ${m.sample_interval_s || 0.5} s); cuts checked on all ${m.frames_read || "?"} decoded frames; click a frame to play from it</span></h3>
    <div class="frames">${frames.map((f) => {
      const flagged = f.findings.length > 0;
      const z = f.noise_deficit_z;
      return `<button class="frame ${flagged ? "flag" : ""} ${f.near_cut != null ? "cut" : ""}" data-fs="${f.findings.join(" ")}" data-seek="${f.t}" data-evid="${e.id}" title="${f.findings.length ? "Findings: " + f.findings.join(", ") : "No finding refers to this frame"}">
        <img loading="lazy" src="${art(f.thumb)}" alt=""><span class="ft">${f.t.toFixed(2)} s</span>
        <span class="fz">${z != null ? (z < 0 ? `noise z ${z}` : "noise ok") : "not checked"}</span>
        ${f.near_cut != null ? `<span class="fc">cut ${f.near_cut}s</span>` : ""}${flagged ? `<span class="ff">${f.findings.join(" ")}</span>` : ""}</button>`;
    }).join("") || '<span class="muted small">No frames recorded for this video (analysed before frame records were added, or too short).</span>'}</div>
    <div class="small muted">“noise z” is the strongest local sensor-noise deficit measured in that frame (more negative = a cleaner patch than the rest of the frame). Real footage often reaches −4 to −6 in flat areas; flags come from regions that persist across frames.</div>
  </div>`;
}
function timeline(e, audio) {
  const m = e.metadata, dur = m.duration_s || 1;
  const tl = m.timeline || [];
  const max = Math.max(1, ...tl.map((p) => p.diff));
  const pts = tl.map((p) => `${((p.t / dur) * 1000).toFixed(1)},${(96 - (p.diff / max) * 88).toFixed(1)}`).join(" ");
  // strongest local sensor-noise deficit per sampled frame (0 = none, more negative = cleaner patch), drawn downward from the top
  const fr = (m.frames || []).filter((f) => f.noise_deficit_z != null);
  const zmax = Math.max(8, ...fr.map((f) => -Math.min(0, f.noise_deficit_z)));
  const zpts = fr.map((f) => `${((f.t / dur) * 1000).toFixed(1)},${(4 + (-Math.min(0, f.noise_deficit_z) / zmax) * 60).toFixed(1)}`).join(" ");
  const seg = (f, cls) => `<div class="seg ${cls}" data-f="${f.id}" data-evid="${f.evidence || ""}" title="${f.id} ${esc(f.indicator)}" style="left:${f.location.start / dur * 100}%;width:${Math.max(0.4, (f.location.end - f.location.start) / dur * 100)}%"></div>`;
  const asegs = (audio ? audio.findings : []).filter((f) => f.location.type === "segment").map((f) => seg(f, "audio")).join("");
  const vsegs = e.findings.filter((f) => f.location.type === "segment").map((f) => seg(f, "")).join("");
  const cuts = (m.cuts || []).map((c) => `<div class="mark" title="hard cut at ${c}s" style="left:${c / dur * 100}%"><span>CUT ${(+c).toFixed(2)}s</span></div>`).join("");
  const candidates = (m.candidate_intervals || []).map(interval => `<button class="candidate-interval" data-seek="${interval.start}" data-evid="${e.id}" title="Candidate interval; not confirmed manipulation" style="left:${interval.start / dur * 100}%;width:${Math.max(.5, (interval.end - interval.start) / dur * 100)}%">Review</button>`).join("");
  return `<h4>Temporal consistency <span class="muted small">measured on the decoded frames; click to seek</span></h4>
    <div class="timeline tall" data-dur="${dur}" data-ev="${e.id}">${vsegs}${asegs}${cuts}${candidates}
    <svg viewBox="0 0 1000 100" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
      ${zpts ? `<polyline points="${zpts}" fill="none" stroke="var(--violet)" stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>
        ${fr.map((f) => `<circle cx="${((f.t / dur) * 1000).toFixed(1)}" cy="${(4 + (-Math.min(0, f.noise_deficit_z) / zmax) * 60).toFixed(1)}" r="2.2" fill="${f.findings.length ? "var(--high)" : "var(--violet)"}" vector-effect="non-scaling-stroke"/>`).join("")}` : ""}
    </svg><div class="head"></div></div>
    ${axisHtml(dur)}
    <div class="tl-legend small"><span><i class="lg-acc"></i>frame-to-frame change (all ${m.frames_read || "?"} frames, peak ${max.toFixed(1)})</span>
      ${zpts ? `<span><i class="lg-vio"></i>local noise deficit per sampled frame (down = stronger, max z −${zmax.toFixed(1)}; red dot = frame cited by a finding)</span>` : ""}
      <span><i class="lg-high"></i>hard cut / flagged segment</span><span><i class="lg-med"></i>flagged audio segment</span><span>${(m.cuts || []).length} cut(s)</span></div>`;
}

// ---- audio
const audioDur = (e) => { const w = e.metadata.windows || []; return e.metadata.duration_s || (w.length ? w[w.length - 1].t + 0.5 : 1); };
function audioMain(e) {
  const A = e.artifacts || {}, m = e.metadata, dur = audioDur(e);
  const segs = e.findings.filter((f) => f.location.type === "segment");
  const parent = e.derived_from;
  return `${parent ? `<div class="small muted">Audio track extracted from ${chip(parent)}; clicking the waveform plays that video.</div>` : `<audio controls preload="metadata" src="${evSrc(e.id)}" data-ev="${e.id}"></audio>`}
    ${waveHtml(e, dur, segs)}
    ${A[`${e.id}_spectrogram`] ? `<h4>Spectrogram <span class="muted small">0–${(m.sample_rate_analyzed || 16000) / 2000} kHz, as analysed; click to listen from that moment</span></h4>
      <div class="spec-wrap" data-dur="${dur}" data-evid="${e.id}">
        <div class="yaxis">${[8, 6, 4, 2, 0].map((k) => `<span>${k * (m.sample_rate_analyzed || 16000) / 16000}k</span>`).join("")}</div>
        <div class="spec-plot"><img class="spec" data-dur="${dur}" data-evid="${e.id}" src="${art(A[`${e.id}_spectrogram`])}" alt="spectrogram">
          ${segs.map((f) => `<div class="smark" data-f="${f.id}" style="left:${f.location.start / dur * 100}%"><span>${f.id} · ${f.location.start.toFixed(2)} s</span></div>`).join("")}
          <div class="phead"></div></div>
        ${axisHtml(dur)}</div>` : ""}`;
}
function audioExtras(e) {
  const m = e.metadata, w = m.windows || [], dur = audioDur(e);
  const segs = e.findings.filter((f) => f.location.type === "segment");
  const synth = e.findings.filter((f) => /synthetic/i.test(f.indicator));
  const line = (key, lo, hi) => w.filter((x) => x[key] != null).map((x) => `${(x.t / dur) * 1000},${60 - ((Math.min(hi, Math.max(lo, x[key])) - lo) / (hi - lo)) * 56}`).join(" ");
  return `<div class="card"><h3>Signal measurements</h3>
    ${w.length ? `<h4>Measured per half second</h4>
      <div class="wchart"><svg viewBox="0 0 1000 60" preserveAspectRatio="none">
        <polyline points="${line("floor_db", -90, 0)}" fill="none" stroke="var(--accent)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
        <polyline points="${line("jitter_pct", 0, 3)}" fill="none" stroke="var(--violet)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>
        ${segs.map((f) => `<div class="seg audio" data-f="${f.id}" title="${f.id} ${esc(f.indicator)}" style="left:${f.location.start / dur * 100}%;width:${(f.location.end - f.location.start) / dur * 100}%"></div>`).join("")}</div>
      ${axisHtml(dur)}
      <div class="tl-legend small"><span><i class="lg-acc"></i>background noise floor (dB, −90…0; median ${m.noise_floor_db_median ?? "n/a"})</span><span><i class="lg-vio"></i>pitch micro-variation (%, 0…3; median ${m.pitch_jitter_median_pct ?? "n/a"})</span><span><i class="lg-med"></i>flagged segment</span></div>` : '<p class="small muted">No per-window measurements recorded.</p>'}
    <h4>Flagged segments</h4>
    <div class="seglist">${segs.length ? segs.map((f) => `<button class="segbtn" data-f="${f.id}" data-seek="${f.location.start}" data-evid="${e.id}">${f.id} · ${f.location.start.toFixed(2)}–${f.location.end.toFixed(2)} s · ${esc(f.indicator)}</button>`).join("") : '<span class="muted small">No suspicious segments flagged.</span>'}</div>
    <div class="small"><b>Possible synthetic-voice cue:</b> ${synth.length ? synth.map((f) => chip(f.id)).join(" ") + " unnaturally steady pitch in a segment (heuristic, not a trained voice-clone classifier)" : '<span class="muted">none raised (heuristic; a good voice clone can pass)</span>'}</div>
  </div>`;
}

function axisHtml(dur) {
  const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300].find((k) => dur / k <= 10) || 600;
  const ticks = []; for (let t = 0; t <= dur + 1e-6; t += step) ticks.push(t);
  const fmt = (t) => dur >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}` : `${+t.toFixed(1)}s`;
  return `<div class="xaxis">${ticks.map((t) => `<span style="left:${t / dur * 100}%">${fmt(t)}</span>`).join("")}</div>`;
}
// Waveform of the analysed signal (min/max per time bin from the backend), with flagged segments marked on it.
function waveHtml(e, dur, segs) {
  const wf = e.metadata.waveform;
  if (!wf || !wf.bins) return '<div class="small muted">Waveform not recorded for this case (analysed before waveforms were stored).</div>';
  const n = wf.bins, peak = Math.max(1e-3, ...wf.max.map(Math.abs), ...wf.min.map(Math.abs));
  const inSeg = (t) => segs.find((f) => t >= f.location.start && t <= f.location.end);
  let paths = { n: "", f: "" };
  for (let i = 0; i < n; i++) {
    const x = (i + 0.5) / n * 1000, t = (i + 0.5) / n * dur;
    const y1 = 50 - wf.max[i] / peak * 46, y2 = 50 - wf.min[i] / peak * 46;
    paths[inSeg(t) ? "f" : "n"] += `M${x.toFixed(1)},${y1.toFixed(1)}V${Math.max(y2, y1 + 0.6).toFixed(1)}`;
  }
  return `<h4>Waveform <span class="muted small">${dur.toFixed(1)} s · ${n} bins of the decoded signal · flagged segments in amber</span></h4>
    <div class="wave" data-dur="${dur}" data-evid="${e.id}">
      <svg viewBox="0 0 1000 100" preserveAspectRatio="none"><path d="${paths.n}" class="wv"/><path d="${paths.f}" class="wv flag"/></svg>
      ${segs.map((f) => `<div class="wmark" data-f="${f.id}" style="left:${f.location.start / dur * 100}%"><span>${esc(f.id)} ${esc(/splice|boundary/i.test(f.indicator) ? "SPLICE?" : "ANOMALY")}</span></div>`).join("")}
      <div class="phead"></div></div>
    ${axisHtml(dur)}`;
}

// ---- text
function textMain(e) { return `<div class="textbox" data-text="${e.id}">loading…</div><p class="small muted">Extracted claims are highlighted.</p>`; }
function textExtras(e) {
  const claims = e.metadata.claims || [];
  return `<div class="card"><h3>Checkable claims extracted</h3>${claims.length ? `<ul class="claims">${claims.map((c) => `<li><span class="k info">${esc(c.type)}</span> ${esc(c.value)} <span class="muted">“${esc(c.text)}”</span>${claimLinks(c)}</li>`).join("")}</ul>` : '<p class="muted small">No dates, places or authenticity/liveness claims found.</p>'}
    <p class="small muted">Claims are checked against the other evidence in the Cross-modal tab. The text check finds claims and framing; it does not fact-check the world.</p></div>`;
}
function claimLinks(c) {
  const rel = C.correlations.filter((x) => (c.type === "date" && /date/i.test(x.title)) || (c.type === "authenticity" && /unedited|real/i.test(x.title)) || (c.type === "liveness" && /live/i.test(x.title)));
  return rel.length ? ` <span class="small">→ ${rel.map((x) => chip(x.id)).join("")}</span>` : "";
}

// ---------------------------------------------------------------- other tabs
const STATUS = { match: "provenance match", indicator: "indicator", neutral: "neutral info", clear: "nothing found", experimental: "experimental" };
const ENGINE = { "SHA-256": 1, "Perceptual hash": 1, "C2PA credentials": 1, "Metadata": 2, "Image analysis": 2, "Camera (sensor-noise) consistency": 2, "Generator analysis (experimental)": 2 };
function invTable() {
  const rows = C.investigation || [];
  const tr = (r, cls = "") => `<tr class="${cls}"><td><span class="dot st-${r.status}" title="${STATUS[r.status]}"></span>${esc(r.investigation)}</td><td class="muted">${esc(r.item)}</td><td>${esc(r.result)}</td><td>${r.refs.map(chip).join("")}</td></tr>`;
  const g = (n, list) => list.length ? `<tr class="group"><td colspan="4">${n}</td></tr>` + list.map((r) => tr(r)).join("") : "";
  const e1 = rows.filter((r) => ENGINE[r.investigation] === 1), e2 = rows.filter((r) => ENGINE[r.investigation] === 2);
  const other = rows.filter((r) => !ENGINE[r.investigation] && r.investigation !== "Final assessment");
  const fin = rows.find((r) => r.investigation === "Final assessment");
  return `<div class="card"><h3>Investigation table <span class="muted small">every check that ran, per engine</span></h3><div class="tablewrap"><table class="inv"><tr><th>Investigation</th><th>Evidence</th><th>Finding</th><th>Refs</th></tr>
    ${g("1 · Authenticity & provenance engine", e1)}${g("2 · Manipulation detection engine", e2)}${g("3 · Video, audio, text and cross-modal checks", other)}${fin ? tr(fin, "final") : ""}</table></div>
    <div class="legend small">${Object.entries(STATUS).map(([k, v]) => `<span><span class="dot st-${k}"></span>${v}</span>`).join("")}</div></div>`;
}
// How the per-engine signals feed the overall assessment and what it leads to; every number is from this case.
function routingCard() {
  const G = signals(), o = C.overall;
  const review = allFindings().filter((f) => f.score >= 0.4).length;
  return `<div class="card"><h3>Evidence routing <span class="muted small">strongest signal per engine → assessment → next step</span></h3>
    <div class="routing">
      <div class="r-in">${G.map((g) => `<div class="r-node ${g.cross ? "" : "band-border-" + bandOf(g.max)}"><span>${g.n}</span><b class="${g.cross ? "" : "band-" + bandOf(g.max)}">${g.cross ? `${g.flags}/${g.total}` : g.max.toFixed(2)}</b></div>`).join("")}</div>
      <svg class="r-lines" viewBox="0 0 60 100" preserveAspectRatio="none">${G.map((_, i) => `<path d="M0,${(i + 0.5) / G.length * 100} C30,${(i + 0.5) / G.length * 100} 30,50 60,50"/>`).join("")}</svg>
      <div class="r-mid"><div class="diamond band-border-${o.band}"><div><span>Assessment</span><b class="band-${o.band}">${o.score.toFixed(2)}</b><i>${o.range[0].toFixed(2)}–${o.range[1].toFixed(2)}</i></div></div></div>
      <svg class="r-lines" viewBox="0 0 60 100" preserveAspectRatio="none"><path d="M0,50 C30,50 30,20 60,20"/><path d="M0,50 C30,50 30,80 60,80"/></svg>
      <div class="r-out">
        <div class="r-node ${review ? "band-border-medium" : ""}"><span>Human review</span><b>${review}</b><i>finding(s) ≥ 0.40 to check</i></div>
        <div class="r-node band-border-low"><span>Evidence pack</span><b>${C.integrity.every((i) => i.verified) ? "sealed" : "mismatch"}</b><i>hashes ${C.integrity.every((i) => i.verified) ? "re-verified" : "changed"} · report hashed</i></div>
      </div></div></div>`;
}
const linkify = (s) => s.replace(/\b([EFX]\d+a?)\b/g, (m) => chip(m));

// ---------------------------------------------------------------- correlation graph
// Nodes: evidence items, the relations found between them (X), and the findings each relation rests on (F).
// Layout is computed here (positions only); every node, edge and label comes from this case's correlations.
let G = null;
function corrTab() {
  const eff = ["strengthens", "weakens", "neutral"];
  const n = (k) => C.correlations.filter((c) => c.effect === k).length;
  return `<div class="card"><h3>Cross-modal correlation graph <span class="muted small">drag nodes · hover to trace · click for details</span></h3>
    <div class="g-tools small">${eff.map((k) => `<label class="eff-${k}"><input type="checkbox" data-eff="${k}" ${n(k) ? "checked" : "disabled"}> ${k} (${n(k)})</label>`).join("")}
      <label><input type="checkbox" id="gFind" checked> show findings</label><button class="small ghost" id="gReset">Reset layout</button></div>
    <div class="g-wrap"><div class="graphwrap"><svg id="graph" viewBox="0 0 900 520" role="img" aria-label="Evidence correlation graph"></svg></div>
      <div class="g-detail" id="gDetail">${C.correlations.length ? '<p class="muted small">Select a node to see the evidence behind it.</p>' : '<p class="muted">No relations found. Add more than one item (for example the photo, the clip and its caption) to cross-check them.</p>'}</div></div>
    <div class="tl-legend small"><span><i class="lg-high"></i>strengthens the manipulation hypothesis</span><span><i class="lg-low"></i>weakens it</span><span><i class="lg-mut"></i>neutral context</span><span>● evidence item (colour = item indicator) · ◆ relation · • finding</span></div></div>
    <div class="card"><h3>Relations (${C.correlations.length})</h3>${C.correlations.length ? C.correlations.map((c) => `<div class="corr eff-${esc(c.effect)}" id="c-${c.id}" data-x="${c.id}"><div class="bar"></div><div>
      <div><span class="fid">${esc(c.id)}</span> <b>${esc(c.title)}</b> <span class="eff">${esc(c.effect)}</span>${c.source === "ai" ? ' <span class="k ai">AI</span>' : ""}</div>
      <div class="muted">${esc(c.explanation)}</div><div>${[...c.evidence_refs, ...(c.finding_refs || [])].map(chip).join("")}</div></div></div>`).join("")
    : '<p class="muted">None.</p>'}</div>`;
}
function graphModel() {
  const W = 900, H = 520, cx = W / 2, cy = H / 2;
  const ev = C.evidence, nodes = [], edges = [];
  const R = ev.length > 1 ? Math.min(W, H) * 0.36 : 0;
  ev.forEach((e, i) => { const a = -Math.PI / 2 + (2 * Math.PI * i) / ev.length;
    nodes.push({ id: e.id, t: "E", x: cx + R * 1.45 * Math.cos(a), y: cy + R * Math.sin(a), r: 26, band: bandOf(e.score), label: e.id, sub: `${MOD[e.modality][1]} · ${e.filename.slice(0, 20)}` }); });
  const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const fseen = {};
  C.correlations.forEach((c, k) => {
    const refs = c.evidence_refs.filter((x) => pos[x]);
    if (!refs.length) return;
    let x = refs.reduce((s, r) => s + pos[r].x, 0) / refs.length, y = refs.reduce((s, r) => s + pos[r].y, 0) / refs.length;
    if (refs.length === 1) { x += (x - cx) * 0.35 + 40; y += (y - cy) * 0.35 - 30; }
    x += ((k % 3) - 1) * 34; y += (((k / 3) | 0) % 3 - 1) * 26;
    nodes.push({ id: c.id, t: "X", x, y, r: 13, eff: c.effect, label: c.id });
    refs.forEach((r) => edges.push({ a: c.id, b: r, eff: c.effect, kind: "xe" }));
    (c.finding_refs || []).forEach((fid) => {
      const f = findingById(fid); if (!f) return;
      if (!fseen[fid]) {
        const host = pos[f.ev]; if (!host) return;
        const sib = Object.values(fseen).filter((n) => n.host === f.ev).length;
        const out = Math.atan2(host.y - cy, host.x - cx) + (sib - 2) * 0.45;
        fseen[fid] = { id: fid, t: "F", host: f.ev, x: host.x + Math.cos(out) * 68, y: host.y + Math.sin(out) * 58, r: 7, band: bandOf(f.score), label: fid };
        nodes.push(fseen[fid]); edges.push({ a: fid, b: f.ev, kind: "fe" });
      }
      edges.push({ a: c.id, b: fid, eff: c.effect, kind: "xf" });
    });
  });
  nodes.forEach((n) => { n.x = Math.max(30, Math.min(W - 30, n.x)); n.y = Math.max(24, Math.min(H - 40, n.y)); });
  return { nodes, edges, by: Object.fromEntries(nodes.map((n) => [n.id, n])) };
}
function drawGraph(keep) {
  const svg = $("#graph"); if (!svg) return;
  if (!keep || !G || G.case !== C.case_id) { G = graphModel(); G.case = C.case_id; }
  const showF = !$("#gFind") || $("#gFind").checked;
  const effOn = new Set($$("[data-eff]").filter((i) => i.checked).map((i) => i.dataset.eff));
  const vis = (n) => n.t === "E" || (n.t === "X" ? effOn.has(n.eff) : showF && G.edges.some((e) => e.kind === "xf" && e.b === n.id && effOn.has(e.eff)));
  const V = new Set(G.nodes.filter(vis).map((n) => n.id));
  const col = { strengthens: "var(--high)", weakens: "var(--low)", neutral: "var(--muted)" };
  let h = G.edges.filter((e) => V.has(e.a) && V.has(e.b)).map((e) => {
    const A = G.by[e.a], B = G.by[e.b];
    return `<line x1="${A.x}" y1="${A.y}" x2="${B.x}" y2="${B.y}" class="g-edge ${e.kind}" data-a="${e.a}" data-b="${e.b}" stroke="${e.kind === "fe" ? "var(--line2)" : col[e.eff] || col.neutral}"/>`;
  }).join("");
  h += G.nodes.filter((n) => V.has(n.id)).map((n) => {
    if (n.t === "E") return `<g class="g-node E" data-id="${n.id}" transform="translate(${n.x},${n.y})"><circle r="${n.r}" class="band-fill-${n.band}"/><text y="4" text-anchor="middle" class="nlabel">${n.label}</text><text y="${n.r + 16}" text-anchor="middle" class="nsub">${esc(n.sub)}</text></g>`;
    if (n.t === "X") return `<g class="g-node X" data-id="${n.id}" transform="translate(${n.x},${n.y})"><rect x="-${n.r}" y="-${n.r}" width="${n.r * 2}" height="${n.r * 2}" transform="rotate(45)" fill="var(--panel2)" stroke="${col[n.eff] || col.neutral}" stroke-width="2.5"/><text y="4" text-anchor="middle" class="xlabel">${n.label.slice(1)}</text></g>`;
    return `<g class="g-node F" data-id="${n.id}" transform="translate(${n.x},${n.y})"><circle r="${n.r}" class="band-fill-${n.band}"/><text x="${n.r + 3}" y="4" class="flabel">${n.label}</text></g>`;
  }).join("");
  svg.innerHTML = h;
  wireGraph(svg);
}
function wireGraph(svg) {
  const nb = (id) => new Set([id, ...G.edges.filter((e) => e.a === id || e.b === id).flatMap((e) => [e.a, e.b])]);
  const trace = (id) => { const N = nb(id); svg.classList.toggle("tracing", !!id);
    $$(".g-node", svg).forEach((g) => g.classList.toggle("lit", !!id && N.has(g.dataset.id)));
    $$(".g-edge", svg).forEach((l) => l.classList.toggle("lit", !!id && (l.dataset.a === id || l.dataset.b === id))); };
  const pt = (ev) => { const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
  let drag = null, moved = false;
  $$(".g-node", svg).forEach((g) => {
    g.onmouseenter = () => !drag && trace(g.dataset.id);
    g.onmouseleave = () => !drag && trace(null);
    g.onpointerdown = (ev) => { drag = g.dataset.id; moved = false; g.setPointerCapture(ev.pointerId); };
    g.onpointermove = (ev) => { if (drag !== g.dataset.id) return; const q = pt(ev), n = G.by[drag];
      if (Math.hypot(q.x - n.x, q.y - n.y) > 2) moved = true; n.x = Math.max(20, Math.min(880, q.x)); n.y = Math.max(20, Math.min(500, q.y));
      g.setAttribute("transform", `translate(${n.x},${n.y})`);
      $$(`.g-edge[data-a="${drag}"]`, svg).forEach((l) => { l.setAttribute("x1", n.x); l.setAttribute("y1", n.y); });
      $$(`.g-edge[data-b="${drag}"]`, svg).forEach((l) => { l.setAttribute("x2", n.x); l.setAttribute("y2", n.y); }); };
    g.onpointerup = () => { const id = drag; drag = null; if (!moved) graphDetail(id); };
  });
}
function graphDetail(id) {
  const box = $("#gDetail"); if (!box) return;
  $$(".g-node").forEach((g) => g.classList.toggle("sel", g.dataset.id === id));
  let h = "";
  if (/^X/.test(id)) {
    const c = C.correlations.find((x) => x.id === id);
    h = `<div class="eyebrow">Relation ${esc(c.id)}</div><h4 class="eff-t eff-${esc(c.effect)}">${esc(c.title)}</h4><div class="badge ${c.effect === "strengthens" ? "bad" : c.effect === "weakens" ? "ok" : "warn"}">${esc(c.effect)}</div>
      <p class="small">${esc(c.explanation)}</p><div class="small muted">Evidence</div><div>${c.evidence_refs.map(chip).join("")}</div>
      ${(c.finding_refs || []).length ? `<div class="small muted">Findings it rests on</div><ul class="plain small">${c.finding_refs.map((fid) => { const f = findingById(fid); return `<li>${chip(fid)} ${f ? esc(f.indicator) + ` <span class="muted">(${f.score.toFixed(2)})</span>` : ""}</li>`; }).join("")}</ul>` : ""}`;
  } else if (/^F/.test(id)) {
    const f = findingById(id), [k, kl] = kindOf(f);
    h = `<div class="eyebrow">Finding ${esc(id)} in ${esc(f.ev)}</div><h4>${esc(f.indicator)}</h4><span class="k ${k}">${kl}</span> ${meter(f.score, f.strength)}<span class="small">${f.strength} ${f.score.toFixed(2)}</span>
      <p class="small">${esc(f.explanation)}</p><div class="small muted">Location: ${esc(locText(f.location))}</div>
      <div class="small muted">Relations: ${corrFor(id).map((c) => chip(c.id)).join("") || "none"}</div><button class="small" data-open="${id}">Open this finding</button>`;
  } else {
    const e = C.evidence.find((x) => x.id === id), rel = C.correlations.filter((c) => c.evidence_refs.includes(id));
    h = `<div class="eyebrow">Evidence ${esc(id)}</div><h4>${esc(e.filename)}</h4><div class="small">${MOD[e.modality][1]} · item indicator <b class="band-${bandOf(e.score)}">${e.score.toFixed(2)}</b> · ${e.findings.length} finding(s)</div>
      <div class="small muted">sha256 <span class="mono">${esc(e.sha256.slice(0, 24))}…</span></div>
      <div class="small muted">In ${rel.length} relation(s)</div><div>${rel.map((c) => chip(c.id)).join("")}</div><button class="small" data-open="${id}">Open this evidence</button>`;
  }
  box.innerHTML = h;
  $$(".chip", box).forEach((c) => c.onclick = () => /^[XF]/.test(c.dataset.ref) && G.by[c.dataset.ref] ? graphDetail(c.dataset.ref) : goRef(c.dataset.ref));
  $$("[data-open]", box).forEach((b) => b.onclick = () => goRef(b.dataset.open));
}

function provCard(e) {
  if (!e.provenance) return "";
  const c2 = { valid: "valid", valid_untrusted: "valid (signer not on a trust list)", invalid: "INVALID (credentials broken)", none: "none", present_unverified: "present, not verified" };
  const dot = (sc) => sc == null ? "st-neutral" : sc >= 0.4 ? "st-indicator" : sc > 0 ? "st-weak" : "st-clear";
    const p = e.provenance, reg = p.registry || {}, c = p.c2pa || {}, m = e.metadata || {};
    const fFor = (re) => e.findings.filter((f) => (f.location.type === "metadata" && re.test(f.location.field || "")) || re.test(f.category));
    const row = (icon, name, value, fs) => {
      const sc = fs.length ? Math.max(...fs.map((f) => f.score)) : null;
      return `<div class="mrow2"><span class="mi">${icon}</span><span class="mn">${name}</span><span class="mv">${value}${fs.map((f) => chip(f.id)).join("")}</span><span class="dot ${dot(sc)}" title="${sc == null ? "no finding" : "strongest linked finding " + sc.toFixed(2)}"></span></div>`;
    };
    return `<div class="card"><h3>Metadata & provenance · ${esc(e.id)} ${esc(e.filename)}</h3>
      ${row("◎", "Exact registry match", reg.exact ? `${chip(reg.exact.id)} ${esc(reg.exact.filename)} · ${esc(reg.exact.source || "")}` : "none (not evidence of manipulation)", fFor(/^registry-match$/))}
      ${row("≈", "Perceptual hash", (reg.similar || []).length ? reg.similar.map((x) => `${chip(x.id)} ${esc(x.filename)} (distance ${x.distance}/64)`).join(", ") : `no similar registered image${p.phash ? ` · pHash <span class="mono">${esc(p.phash)}</span>` : ""}`, fFor(/^registry-similar$/))}
      ${p.diff ? row("▣", "Difference vs original", `${(p.diff.changed_fraction * 100).toFixed(1)}% of pixels changed in ${p.diff.boxes.length} area(s)`, fFor(/^modified-copy$/)) : ""}
      ${p.camera_note ? row("◍", "Sensor-noise comparison", esc(p.camera_note), fFor(/camera/)) : ""}
      ${row("⛨", "C2PA content credentials", `${esc(c2[c.status] || c.status || "not checked")}${c.signer ? ` · signer ${esc(c.signer)}` : ""}${c.generator ? ` · tool ${esc(c.generator)}` : ""}`, fFor(/C2PA|c2pa/))}
      ${fFor(/vs registered original/).length ? row("⇄", "Metadata vs original", "differs from the registered original", fFor(/vs registered original/)) : ""}
      ${row("⌁", "Device signature", esc(m.device || "none embedded"), [])}
      ${row("◷", "Capture date", esc(m.capture_date || "none embedded"), fFor(/DateTime|capture/i))}
      ${row("✎", "Software", esc(m.software || "none recorded"), fFor(/^Software$|^editing$/))}
      ${row("⌖", "GPS location", m.exif && m.exif.GPSInfo ? "present in EXIF" : "none embedded", [])}
      ${row("↺", "Seen in earlier investigations", (p.history || []).length ? p.history.slice(0, 3).map((h) => `${esc(h.case_id)} (indicator ${h.score != null ? (+h.score).toFixed(2) : "?"})`).join(", ") + (p.history.length > 3 ? ` +${p.history.length - 3} more` : "") : "no", fFor(/^history$/))}
      <div class="small muted">Dots: <span class="dot st-indicator"></span>linked finding ≥ 0.40 <span class="dot st-weak"></span>weak <span class="dot st-clear"></span>information only <span class="dot st-neutral"></span>no finding. Missing metadata or credentials are never treated as signs of manipulation.</div></div>`;
}
function custodyCard() {
  const cv = C.custody_verification || {};
  const L = C.log || [];
  const integ = (id) => (C.integrity.find((i) => i.id === id) || {}).verified;
  const ev = (id) => C.evidence.find((e) => e.id === id);
  const badge = (cls, t) => `<span class="badge ${cls}">${t}</span>`;
  const steps = [];
  const open = L.find((l) => l.step === "investigation.create");
  if (open) steps.push(["Investigation opened", esc(open.detail), open.time, badge("ok", "Logged")]);
  L.filter((l) => l.step === "evidence.ingest" || l.step === "evidence.derive").forEach((l) => {
    const e = ev(l.ref), ok = integ(l.ref);
    steps.push([`${l.step === "evidence.derive" ? "Derived" : "Received"} ${esc(l.ref)} ${e ? esc(e.filename) : ""}`,
      `SHA-256 <span class="mono">${esc((e ? e.sha256 : l.data_sha256 || "").slice(0, 16))}…</span> · ${esc(l.detail.slice(0, 90))}`, l.time,
      ok === false ? badge("bad", "Hash mismatch") : ok === true ? badge("ok", l.step === "evidence.derive" ? "Derived, hashed" : "Origin hashed") : badge("warn", "Verification unavailable")]);
  });
  const an = L.filter((l) => l.step.startsWith("analysis."));
  if (an.length) {
    const flagged = C.evidence.filter((e) => e.score >= 0.65).map((e) => e.id);
    steps.push(["Analysed", `${an.length} analysis step(s) on ${new Set(an.map((l) => l.ref)).size} item(s)${flagged.length ? ` · strong indicators in ${flagged.join(", ")}` : ""}`, an[an.length - 1].time,
      flagged.length ? badge("bad", "Indicators found") : badge("warn", "Analysed")]);
  }
  const cr = L.find((l) => l.step === "correlation.run"); if (cr) steps.push(["Correlated", esc(cr.detail), cr.time, badge("ok", `${C.correlations.length} relation(s)`)]);
  const as = L.find((l) => l.step === "assessment"); if (as) steps.push(["Assessed", esc(as.detail), as.time, badge(C.overall.band === "low" ? "ok" : C.overall.band === "medium" ? "warn" : "bad", C.overall.band === "low" ? "Low" : "Review")]);
  const iv = L.find((l) => l.step === "integrity.verify"); if (iv) steps.push(["Integrity re-verified", esc(iv.detail), iv.time, badge(C.integrity.every((i) => i.verified) ? "ok" : "bad", C.integrity.every((i) => i.verified) ? "Verified" : "Mismatch")]);
  const rg = L.find((l) => l.step === "report.generate");
  if (rg) steps.push(["Report sealed", `report.json SHA-256 <span class="mono">${esc((C.report_sha256 || "").slice(0, 16))}…</span> recorded in the custody log`, rg.time, badge(C.report_integrity === false ? "bad" : C.report_integrity === true ? "ok" : "warn", C.report_integrity === false ? "Changed since" : C.report_integrity === true ? "Sealed" : "Verification unavailable")]);
  return `<div class="card"><h3>Chain of custody</h3>
      <ol class="custody">${steps.map(([t, d, time, b], i) => `<li><span class="cn">${i + 1}</span><div class="cb"><b>${t}</b><span class="small muted">${d}</span></div><span class="ct mono">${esc((time || "").slice(11, 19))}</span>${b}</li>`).join("")}</ol>
      <div class="chainbox ${custodyState(cv).cls}" id="chainBox">${cv.ok ? `✓ Hash chain verified when this case was opened: ${cv.entries} entries, head <span class="mono">${esc((cv.head || "").slice(0, 16))}…</span>` : cv.ok === false ? `✗ Hash chain BROKEN at entry ${cv.broken_at}` : "Custody verification unavailable"}</div>
      <button class="small" id="verifyChain">Re-verify the whole custody log now</button>
      <details><summary>Raw custody log for this investigation (${L.length} entries, hash-chained)</summary><table class="kv log">${L.map((l) =>
        `<tr><td>#${l.seq} ${esc(l.time.slice(11, 19))}</td><td>[${esc(l.step)}] ${esc(l.detail)} ${l.ref ? chip(l.ref) : ""}<br><span class="muted mono">hash ${esc((l.entry_hash || "").slice(0, 16))}… prev ${esc((l.prev_hash || "").slice(0, 16))}…</span></td></tr>`).join("")}</table></details></div>`;
}

// ---------------------------------------------------------------- interaction
function focusEl(el) { if (!el) return; el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("hl"); setTimeout(() => el.classList.remove("hl"), 1600); }
function goRef(id) {
  if (/^X\d+/.test(id)) { sel.tab = "cross"; renderTab(); graphDetail(id); return focusEl($("#c-" + id)); }
  if (/^F\d+/.test(id)) {
    const f = findingById(id); if (!f) return;
    const e = C.evidence.find((x) => x.id === f.ev);
    sel = { ...sel, tab: tabOf(e), ev: f.ev, f: id, kf: "all" }; renderCase(); return focusEl($("#f-" + id));
  }
  if (/^E\d+a?$/.test(id)) {
    const e = C.evidence.find((x) => x.id === id); if (!e) return;
    sel = { ...sel, tab: tabOf(e), ev: id, f: null, kf: "all" }; renderCase(); return focusEl($(".preview"));
  }
  if (/^R\d+/.test(id)) { $("#registryPanel").showModal(); return focusEl($("#registry")); }
}
function seek(evId, t) {
  const e = C.evidence.find((x) => x.id === evId);
  const target = e && e.derived_from ? e.derived_from : evId;
  if (target !== sel.ev && !$(`video[data-ev="${target}"], audio[data-ev="${target}"]`)) { sel.ev = target; renderCase(); }
  const m = $(`video[data-ev="${target}"], audio[data-ev="${target}"]`);
  if (!m) return;
  m.scrollIntoView({ behavior: "smooth", block: "center" });
  const jump = () => {
    const end = Number.isFinite(m.duration) ? m.duration : Math.max(0, t);
    m.currentTime = Math.max(0, Math.min(t, end)); m.play().catch(() => {});
  };
  if (m.readyState >= 1) jump(); else m.addEventListener("loadedmetadata", jump, {once:true});
}

function wire(R) {
  $$(".video-player", R).forEach(player => {
    const video = $("video", player), error = $("[data-video-error]", player), status = $("[data-video-status]", player);
    video.onerror = () => { error.classList.remove("hidden"); error.textContent = "This browser could not decode the video. The original is preserved; try the compatible derived preview or download it."; };
    $("[data-compatible-preview]", player).onclick = () => {
      error.classList.add("hidden");
      video.src = `/api/cases/${C.case_id}/preview/${encodeURIComponent(video.dataset.ev)}`;
      video.setAttribute("aria-label", "Compatible derived video preview");
      status.textContent = "Browser-compatible derived preview at source resolution (even-pixel padding if required). The original evidence is unchanged.";
      video.load();
    };
  });
  $$(".chip", R).forEach((c) => c.onclick = (ev) => { ev.stopPropagation(); goRef(c.dataset.ref); });
  $$(".ev-item", R).forEach((b) => b.onclick = () => {
    const e = C.evidence.find((x) => x.id === b.dataset.ev);
    sel.ev = e.id; sel.f = null; sel.kf = "all";
    if (TAB_MODS[sel.tab]) sel.tab = tabOf(e);
    renderCase();
  });
  $$("[data-mtile]", R).forEach((b) => b.onclick = () => {
    const e = C.evidence.find((x) => x.modality === b.dataset.mtile);
    sel = { ...sel, tab: tabOf(e), ev: e.id, f: null, kf: "all" }; renderCase();
    focusEl($(".tabs"));
  });
  $$(".keyf", R).forEach((b) => b.onclick = () => goRef(b.dataset.go));
  $$("[data-people-ev]", R).forEach((b) => b.onclick = () => { sel = { ...sel, tab: "visual", ev: b.dataset.peopleEv, f: null, kf: "all", people: true }; renderCase(); focusEl($("#peopleCard")); });
  $$("[data-seek]", R).forEach((s) => s.onclick = (ev) => { ev.stopPropagation(); seek(s.dataset.evid || sel.ev, +s.dataset.seek); });
  wireFindings(R);
  $$(".kf", R).forEach((b) => b.onclick = () => {
    sel.kf = b.dataset.kf; $$(".kf", R).forEach((x) => x.classList.toggle("on", x === b));
    const box = $(".findings", R), e = C.evidence.find((x) => x.id === sel.ev);
    if (box && e) { box.innerHTML = findingsHtml(e); wireFindings(box); }
  });
  $$(".timeline .seg, .wchart .seg", R).forEach((s) => {
    s.onmouseenter = () => linkFinding(s.dataset.f, true); s.onmouseleave = () => linkFinding(s.dataset.f, false);
  });
  $$(".timeline", R).forEach((t) => {
    t.onclick = (ev) => { const r = t.getBoundingClientRect(); seek(t.dataset.ev, (ev.clientX - r.left) / r.width * +t.dataset.dur); };
    const v = $(`video[data-ev="${t.dataset.ev}"]`, R);
    if (v) v.ontimeupdate = () => $(".head", t).style.left = (v.currentTime / +t.dataset.dur * 100) + "%";
  });
  $$("img.spec", R).forEach((img) => img.onclick = (ev) => { const r = img.getBoundingClientRect(); seek(img.dataset.evid, (ev.clientX - r.left) / r.width * +img.dataset.dur); });
  $$(".wave", R).forEach((w) => w.onclick = (ev) => { const r = w.getBoundingClientRect(); seek(w.dataset.evid, (ev.clientX - r.left) / r.width * +w.dataset.dur); });
  $$(".wmark, .smark", R).forEach((m) => {
    m.onmouseenter = () => linkFinding(m.dataset.f, true); m.onmouseleave = () => linkFinding(m.dataset.f, false);
  });
  const ae = C.evidence.find((x) => x.id === sel.ev);
  if (ae && ae.modality === "audio") {
    const target = ae.derived_from || ae.id;
    const media = $(`video[data-ev="${target}"], audio[data-ev="${target}"]`, R);
    if (media) media.ontimeupdate = () => $$(".wave .phead, .spec-plot .phead", R).forEach((h) => h.style.left = (media.currentTime / (+ae.metadata.duration_s || 1) * 100) + "%");
  }
  // image layers and region boxes
  const img = $("#stageImg", R);
  if (img) {
    const draw = () => { drawBoxes(img); drawPeople(img); };
    img.onload = draw; if (img.complete) draw();
    $$("[data-layer]", R).forEach((b) => b.onclick = () => setLayer(b.dataset.layer));
    img.ondblclick = () => zoom(img.src);
    const cb = $("#showBoxes", R); if (cb) cb.onchange = () => { sel.boxes = cb.checked; setLayer(img.getAttribute("src")); };
    const pc = $("#showPeople", R); if (pc) pc.onchange = () => { sel.people = pc.checked; setLayer(img.getAttribute("src")); };
    const op = $("#heatOp", R);
    if (op) op.oninput = () => { sel.heatOp = +op.value; $("#heatImg").style.opacity = sel.heatOp / 100; $("#heatOpV").textContent = sel.heatOp + "%"; };
  }
  $$(".frame img", R).forEach((im) => im.ondblclick = () => zoom(im.src));
  $$("[data-text]", R).forEach(loadText);
  wireCompare(R);
  const pv = $("#pdfPrev", R);
  if (pv) pv.onclick = () => { const box = $("#pdfBox"), f = $("iframe", box); if (!f.src) f.src = `/api/cases/${C.case_id}/report.pdf?inline=true`; box.classList.toggle("hidden"); if (!box.classList.contains("hidden")) box.scrollIntoView({ behavior: "smooth" }); };
  const pc = $("#pdfClose"); if (pc) pc.onclick = () => $("#pdfBox").classList.add("hidden");
  const vb = $("#verifyChain", R);
  if (vb) vb.onclick = async () => {
    vb.disabled = true; const box = $("#chainBox");
    try {
      const v = await api("/api/custody/verify");
      updateCustody(v);
    } catch (e) { updateCustody(null); box.textContent = "Custody verification unavailable: " + e.message; }
    vb.disabled = false;
  };
}
function wireFindings(R) {
  $$(".f", R).forEach((f) => {
    f.onclick = (ev) => { if (ev.target.closest("summary, details, .chip, .loc, .showlayer, a")) return; selectFinding(f.dataset.f, true); };
    f.onmouseenter = () => linkFinding(f.dataset.f, true);
    f.onmouseleave = () => linkFinding(f.dataset.f, false);
  });
  $$(".f [data-seek]", R).forEach((s) => s.onclick = (ev) => { ev.stopPropagation(); seek(s.dataset.evid || sel.ev, +s.dataset.seek); });
  $$(".f .chip", R).forEach((c) => c.onclick = (ev) => { ev.stopPropagation(); goRef(c.dataset.ref); });
  $$(".loc:not([data-seek])", R).forEach((b) => b.onclick = (ev) => { ev.stopPropagation(); selectFinding(b.dataset.f, true); });
  $$(".showlayer", R).forEach((b) => b.onclick = (ev) => { ev.stopPropagation(); selectFinding(b.dataset.f); setLayer(b.dataset.show); $("#stageImg").scrollIntoView({ behavior: "smooth", block: "center" }); });
}
// Light up everything that shows one finding: its row, its box on the image, its timeline segment, the frames it refers to.
function linkFinding(id, on) {
  if (!id) return;
  $$(`.f[data-f="${id}"], #stageSvg [data-f="${id}"], .seg[data-f="${id}"], .segbtn[data-f="${id}"]`).forEach((x) => x.classList.toggle("lnk", on));
  $$(".frame[data-fs]").forEach((x) => { if (x.dataset.fs.split(" ").includes(id)) x.classList.toggle("lnk", on); });
}
// Switch the image under the overlays; region boxes and the heatmap overlay belong to the uploaded image only.
function setLayer(src) {
  const img = $("#stageImg"); if (!img) return;
  if (img.getAttribute("src") !== src) img.src = src;
  const up = src === evSrc(sel.ev);
  const svg = $("#stageSvg"); if (svg) svg.style.display = up && sel.boxes ? "" : "none";
  const ps = $("#peopleSvg"); if (ps) ps.style.display = up && sel.people ? "" : "none";
  const h = $("#heatImg"); if (h) h.style.display = up ? "" : "none";
  const nReg = C.evidence.find((x) => x.id === sel.ev).findings.filter((f) => f.location.type === "region").length;
  $$(".stage-tools input").forEach((i) => i.disabled = !up || (i.id === "showBoxes" && !nReg));
  $$("[data-layer]").forEach((x) => x.classList.toggle("on", x.dataset.layer === src));
}
function selectFinding(id, scroll) {
  sel.f = id;
  $$(".f").forEach((x) => x.classList.toggle("on", x.dataset.f === id));
  $$("#stageSvg rect").forEach((r) => r.classList.toggle("on", r.dataset.f === id));
  $$(".seg, .segbtn").forEach((r) => r.classList.toggle("on", r.dataset.f === id));
  $$(".frame[data-fs]").forEach((x) => x.classList.toggle("on", x.dataset.fs.split(" ").includes(id)));
  const f = findingById(id);
  if (!f || !scroll) return;
  if (f.location.type === "region" && $("#stageImg")) {
    if (!sel.boxes) { sel.boxes = true; const cb = $("#showBoxes"); if (cb) cb.checked = true; }
    setLayer(evSrc(sel.ev));
    $("#stageImg").scrollIntoView({ behavior: "smooth", block: "center" });
  } else if (f.location.type === "text") { const m = $(".textbox"); if (m) m.scrollIntoView({ behavior: "smooth", block: "center" }); }
}
function drawBoxes(img) {
  const svg = $("#stageSvg"); if (!svg || !img.naturalWidth) return;
  const e = C.evidence.find((x) => x.id === sel.ev);
  svg.setAttribute("viewBox", `0 0 ${img.naturalWidth} ${img.naturalHeight}`);
  const sw = Math.max(2, img.naturalWidth / 300), fs = sw * 7, W = img.naturalWidth, H = img.naturalHeight;
  const placed = [];
  const hit = (r) => placed.some((p) => r[0] < p[0] + p[2] && p[0] < r[0] + r[2] && r[1] < p[1] + p[3] && p[1] < r[1] + r[3]);
  const regs = e.findings.filter((f) => f.location.type === "region").sort((a, b) => b.score - a.score);
  // Label each box where it does not cover another label: above, inside the top, below, then stacked further up.
  // Labels are placed strongest first; boxes are drawn largest first so a smaller box inside a larger one stays clickable.
  svg.innerHTML = regs.map((f) => {
    const [x, y, w, h] = f.location.bbox, b = bandOf(f.score), txt = `${f.id} ${f.score.toFixed(2)}`, lw = txt.length * fs * 0.62, lh = fs * 1.15;
    const lx = Math.min(Math.max(0, x), W - lw);
    const tries = [y - sw * 2, y + lh + sw, y + h + lh + sw, ...[1, 2, 3, 4].map((k) => y - sw * 2 - k * lh)];
    let ly = tries.find((t) => t - lh >= 0 && t <= H && !hit([lx, t - lh, lw, lh])) ?? Math.max(lh, y - sw * 2);
    placed.push([lx, ly - lh, lw, lh]);
    return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" data-f="${f.id}" class="box band-stroke-${b} ${sel.f === f.id ? "on" : ""}" stroke-width="${sw}"><title>${f.id} ${esc(f.indicator)}</title></rect>
      <text x="${lx}" y="${ly}" data-f="${f.id}" class="boxlabel" style="font-size:${fs}px">${txt}</text></g>`;
  }).map((g, i) => [regs[i].location.bbox[2] * regs[i].location.bbox[3], g]).sort((a, b) => b[0] - a[0]).map((x) => x[1]).join("");
  $$("rect", svg).forEach((r) => {
    r.onclick = () => {
      const id = r.dataset.f;
      if (sel.tab !== "visual") { sel.tab = "visual"; renderTab(); }
      if (sel.kf !== "all" && !$("#f-" + id)) $('.kf[data-kf="all"]').click();
      selectFinding(id); focusEl($("#f-" + id));
    };
    r.onmouseenter = () => linkFinding(r.dataset.f, true); r.onmouseleave = () => linkFinding(r.dataset.f, false);
  });
}
// Experimental people overlay: estimated head-and-shoulders region (dashed) and detected face box for each person.
function drawPeople(img) {
  const svg = $("#peopleSvg"); if (!svg || !img.naturalWidth) return;
  const e = C.evidence.find((x) => x.id === sel.ev), L = peopleOf(e);
  svg.setAttribute("viewBox", `0 0 ${img.naturalWidth} ${img.naturalHeight}`);
  const sw = Math.max(2, img.naturalWidth / 400), fs = sw * 7;
  svg.innerHTML = L.map((p) => {
    const [x, y, w, h] = p.region_bbox, [fx, fy, fw, fh] = p.face_bbox;
    return `<g class="person ${p.flagged ? "flag" : ""}" data-person="${p.id}"><title>${p.id}${p.flagged ? ": highlighted for review (experimental)" : ""}</title>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" class="pregion" stroke-width="${sw}"/><rect x="${fx}" y="${fy}" width="${fw}" height="${fh}" class="pface" stroke-width="${sw / 2}"/>
      <text x="${x + sw * 2}" y="${y + fs * 1.1}" class="plabel" style="font-size:${fs}px">${p.id}${p.flagged ? " · review" : ""}</text></g>`;
  }).join("");
  $$("g.person", svg).forEach((g) => g.onclick = () => {
    if (sel.tab !== "visual") { sel.tab = "visual"; renderTab(); }
    focusEl($("#p-" + g.dataset.person));
  });
}
async function loadText(box) {
  const id = box.dataset.text;
  let txt;
  try { const response = await fetch(evSrc(id)); if (!response.ok) throw new Error(response.statusText); txt = await response.text(); } catch (e) { box.textContent = "Could not load evidence text: " + e.message; return; }
  const e = C.evidence.find((x) => x.id === id);
  const quotes = [...(e.metadata.claims || []).map((c) => c.text), ...e.findings.map((f) => f.location.quote)]
    .flatMap((q) => String(q || "").split(/, | \/ /)).filter((q) => q && q.length > 2);
  let h = esc(txt);
  for (const q of [...new Set(quotes)].sort((a, b) => b.length - a.length)) {
    const re = new RegExp("(" + esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")(?![^<]*>)", "gi");
    h = h.replace(re, "<mark>$1</mark>");
  }
  box.innerHTML = h;
}
function zoom(src) { const z = $("#zoom"); z.innerHTML = `<img src="${src}" alt="">`; z.classList.remove("hidden"); z.onclick = () => z.classList.add("hidden"); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#zoom").classList.add("hidden"); });
