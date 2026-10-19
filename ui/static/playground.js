(() => {
  "use strict";
  const root = document.getElementById("playground");
  if (!root) return;

  // ---- DOM helper: text is always appended as text nodes, never parsed as HTML ----
  const h = (tag, props = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  };
  const fmtMs = v => (v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms");
  const fmtUsd = v => (v == null ? "—" : "$" + (Math.abs(v) < 0.01 ? v.toFixed(6) : v.toFixed(4)));
  const fmtPct = v => (v == null ? "—" : v.toFixed(1) + "%");
  const fmtNum = v => (v == null ? "—" : Math.round(v).toLocaleString());

  const HOOKS = {
    hook1_skills: "Hook 1 · Skills", hook2_router: "Hook 2 · Router",
    hook3_risk_gate: "Hook 3 · Risk gate", hook3_refund_gate: "Hook 3 · Refund gate",
    hook4_quality: "Hook 4 · Quality", cascade: "Hook 4 · Cascade",
    rag: "RAG re-rank", multimodal: "Multimodal", advisor: "Advisor",
  };
  const TIERS = ["low", "medium", "high"];
  const tone = d => {
    d = String(d).toUpperCase();
    if (["APPROVE", "SEND", "SUPPORTED", "MATCH"].includes(d)) return "good";
    if (["BLOCK", "HANDOFF", "UNSUPPORTED", "MISMATCH"].includes(d)) return "bad";
    if (["REVIEW", "DECLINED"].includes(d)) return "warn";
    return "info";
  };

  const SURFACES = {
    agent: {
      label: "Agent Run",
      blurb: "The real DeepAgents harness runs your request; every middleware hook fires in its natural order.",
      fields: [
        { k: "request", label: "User request", type: "textarea", value: "Run the project tests, then write src/app.ts." },
        { k: "cost_tier", label: "Router cost tier (live)", type: "select", options: TIERS, value: "medium" },
      ],
      presets: [
        { label: "Tests + destructive rm", values: { request: "Run the project tests, then write src/app.ts." } },
        { label: "Refund request", values: { request: "Customer asked for a refund on ORD-1234, delivered late. Please issue it." } },
      ],
    },
    skills: {
      label: "Skills (Hook 1)",
      blurb: "Two-pass cascade: category, then skill. Only the chosen skill text is injected.",
      fields: [{ k: "request", label: "User request", type: "textarea", value: "Someone sent an email pretending to be our CEO asking for gift cards!" }],
      presets: [
        { label: "Phishing email", values: { request: "Someone sent an email pretending to be our CEO asking for gift cards!" } },
        { label: "Run tests", values: { request: "Run the unit tests for the billing module" } },
        { label: "Prorate plan", values: { request: "Customer upgraded mid-cycle, compute the prorated invoice" } },
      ],
    },
    router: {
      label: "Router (Hook 2)",
      blurb: "Same messages to typesafe/jev-router and to the pinned frontier model; both really run and are measured.",
      fields: [
        { k: "request", label: "Request", type: "textarea", value: "Explain the difference between a mutex and a semaphore." },
        { k: "cost_tier", label: "cost_tier", type: "select", options: TIERS, value: "medium" },
        { k: "pattern", label: "Pattern", type: "select", options: ["direct", "probe"], value: "direct" },
        { k: "use_pool", label: "Restrict router to my model pool", type: "checkbox", value: false },
        { k: "max_tokens", label: "max_tokens", type: "number", value: 300 },
      ],
      presets: [
        { label: "Trivial lookup", values: { request: "What is the capital of France?" } },
        { label: "Architecture", values: { request: "Design a multi-tenant sync engine with conflict resolution and explain the tradeoffs." } },
      ],
    },
    risk: {
      label: "Risk gate (Hook 3)",
      blurb: "Static deny list first (no model call), then Jev nouls: reversible and serves_task.",
      fields: [
        { k: "command", label: "Proposed shell command", type: "text", value: "bun test" },
        { k: "task", label: "Task the agent was given", type: "text", value: "run the project tests" },
      ],
      presets: [
        { label: "bun test", values: { command: "bun test", task: "run the project tests" } },
        { label: "git push --force", values: { command: "git push --force origin main", task: "run the project tests" } },
        { label: "vague cleanup", values: { command: "python scripts/cleanup.py --older-than 90d", task: "run the project tests" } },
        { label: "rm -rf", values: { command: "rm -rf /data", task: "free some disk space" } },
      ],
    },
    refund: {
      label: "Refund gate",
      blurb: "Deterministic pre-checks (order, balance), then three nouls.",
      fields: [
        { k: "order_id", label: "Order", type: "select", options: () => Object.keys(cfg.orders), value: "ORD-1234" },
        { k: "amount_cents", label: "Amount (cents)", type: "number", value: 1200 },
        { k: "reason", label: "Reason", type: "text", value: "late delivery" },
        { k: "customer_text", label: "What the customer said", type: "textarea", value: "My kettle arrived two weeks late, please refund it." },
      ],
      presets: [
        { label: "Kettle · late (approve)", values: { order_id: "ORD-1234", amount_cents: 1200, reason: "late delivery", customer_text: "My kettle arrived two weeks late, please refund it." } },
        { label: "Goodwill (block)", values: { order_id: "ORD-7781", amount_cents: 40000, reason: "goodwill", customer_text: "I would like a goodwill refund on the espresso machine." } },
        { label: "Crushed box (review)", values: { order_id: "ORD-5520", amount_cents: 8900, reason: "crushed box on arrival", customer_text: "The box arrived crushed, can I get a refund?" } },
        { label: "Over balance (pre-check)", values: { order_id: "ORD-6800", amount_cents: 8000, reason: "changed my mind", customer_text: "Refund the toaster please." } },
      ],
    },
    quality: {
      label: "Quality gate (Hook 4)",
      blurb: "Is the answer supported by the context? supported / unsupported / declined.",
      fields: [
        { k: "question", label: "Question", type: "text", value: "How many seats does the Pro plan include?" },
        { k: "context", label: "Context excerpts", type: "textarea", value: "Workspace plans: Starter includes 5 seats and 10GB storage; Pro includes 25 seats, 100GB storage and SSO; Enterprise is unlimited seats with audit logs." },
        { k: "answer", label: "Answer to judge", type: "textarea", value: "The Pro plan includes 25 seats." },
      ],
      presets: [
        { label: "Supported", values: { answer: "The Pro plan includes 25 seats." } },
        { label: "Unsupported", values: { answer: "The Pro plan includes unlimited seats and a free laptop." } },
        { label: "Declined", values: { answer: "The excerpts do not cover that question." } },
      ],
    },
    cascade: {
      label: "Cascade (Hook 4)",
      blurb: "Cheap draft → Jev verify → escalate only if needed, compared against a real frontier answer.",
      fields: [
        { k: "question", label: "Question", type: "text", value: "How many seats does the Pro plan include?" },
        { k: "excerpts", label: "Excerpts", type: "textarea", value: "Workspace plans: Starter includes 5 seats and 10GB storage; Pro includes 25 seats, 100GB storage and SSO; Enterprise is unlimited seats with audit logs." },
        { k: "draft_answer", label: "Draft answer (optional; blank = the cheap model drafts it)", type: "textarea", value: "" },
      ],
      presets: [
        { label: "Answerable", values: { question: "How many seats does the Pro plan include?" } },
        { label: "Not covered (Jira)", values: { question: "How do I connect the Jira integration?" } },
        { label: "Enterprise audit logs", values: { question: "Do Enterprise seats include audit logs?" } },
      ],
    },
    rag: {
      label: "RAG re-rank",
      blurb: "Broad retrieval, then Jev scores every chunk 0–5 and prunes low scorers.",
      fields: [
        { k: "query", label: "Query", type: "text", value: "Why are my webhook requests failing with an error?" },
        { k: "passages", label: "Own passages (optional, separated by a blank line; empty = built-in help center)", type: "textarea", value: "", rows: 5 },
      ],
      presets: [
        { label: "Webhook errors", values: { query: "Why are my webhook requests failing with an error?" } },
        { label: "Refund timing", values: { query: "How long do refunds take?" } },
      ],
    },
    multimodal: {
      label: "Multimodal",
      blurb: "Image is validated and resized to Jev's limits, then shows_item (noul) and color (choice).",
      fields: [
        { k: "title", label: "Listing title", type: "text", value: "Vintage red enamel kettle" },
        { k: "image", label: "Photo (png/jpeg, ≤ 8 MB)", type: "file" },
      ],
      presets: [],
    },
  };

  const TH = [
    { k: "approve_at", label: "approve ≥", min: 0.5, max: 1, step: 0.01 },
    { k: "block_at", label: "block ≤", min: 0, max: 0.5, step: 0.01 },
    { k: "accept_confidence", label: "accept conf ≥", min: 0.5, max: 1, step: 0.01 },
    { k: "rag_threshold", label: "RAG keep ≥", min: 0, max: 5, step: 0.1 },
    { k: "rag_top_k", label: "RAG top-K", min: 1, max: 10, step: 1 },
  ];

  let cfg = null;
  const state = { surface: "agent", file: null, running: false, runTh: null };
  const ui = { inputs: {}, th: {} };

  // ---------------- layout ----------------
  function build() {
    ui.mode = h("select", { onchange: updateBanner },
      h("option", { value: "simulated" }, "simulated (offline, scripted)"),
      h("option", { value: "live" }, "live (real Jev + LLM judge)"));
    ui.force = h("input", { type: "checkbox" });
    ui.ref = h("input", { type: "checkbox", checked: true });
    ui.runBtn = h("button", { onclick: run }, "Run");
    ui.resetBtn = h("button", { class: "ghost", onclick: resetSession }, "Reset session");
    ui.spend = h("span", { class: "pg-muted" });
    ui.banner = h("div", { class: "pg-banner" });
    ui.tabs = h("div", { class: "pg-tabs" });
    ui.form = h("div", { class: "pg-form" });
    ui.thBox = h("div");
    ui.status = h("div", { class: "pg-status" });
    ui.trace = h("div");
    ui.out = h("div");
    ui.drawers = h("div", { class: "pg-drawers" });
    root.replaceChildren(
      h("div", { class: "pg-head" }, h("h2", {}, "🧪 Playground — measured decisions"),
        h("span", { class: "pg-chip" }, "Jev vs generative LLM, side by side")),
      ui.banner, ui.tabs,
      h("div", { class: "pg-grid" }, ui.form,
        h("div", { class: "pg-controls" },
          h("label", {}, "Mode"), ui.mode,
          h("label", {}, "Decision thresholds"), ui.thBox,
          h("div", { class: "row" }, ui.force, h("span", {}, "Force a Jev failure (shows fail-closed REVIEW)")),
          h("div", { class: "row" }, ui.ref, h("span", {}, "Reference run for router / cascade savings")),
          h("div", { class: "pg-run" }, ui.runBtn, ui.resetBtn), ui.spend)),
      ui.status, ui.trace, ui.out, ui.drawers);
    renderTabs(); renderForm(); renderThresholds(); updateBanner(); renderDrawers(); renderSpend(cfg.spend);
  }

  function renderTabs() {
    ui.tabs.replaceChildren(...Object.entries(SURFACES).map(([id, s]) =>
      h("button", { class: id === state.surface ? "on" : "", onclick: () => { state.surface = id; state.file = null; renderTabs(); renderForm(); } }, s.label)));
  }

  function fieldEl(f, value) {
    const opts = typeof f.options === "function" ? f.options() : f.options;
    if (f.type === "textarea") return h("textarea", { rows: f.rows || 3, value });
    if (f.type === "select") return h("select", {}, opts.map(o => h("option", { value: o, selected: String(o) === String(value) }, o)));
    if (f.type === "checkbox") return h("input", { type: "checkbox", checked: !!value });
    if (f.type === "file") return h("input", { type: "file", accept: "image/png,image/jpeg", onchange: onFile });
    return h("input", { type: f.type || "text", value: value ?? "" });
  }

  function renderForm(values = {}) {
    const spec = SURFACES[state.surface];
    ui.inputs = {};
    const rows = spec.fields.map(f => {
      const el = fieldEl(f, values[f.k] ?? f.value);
      ui.inputs[f.k] = el;
      return h("div", {}, f.type === "checkbox" ? null : h("label", {}, f.label), el, f.type === "checkbox" ? h("span", { class: "pg-muted" }, " " + f.label) : null);
    });
    ui.form.replaceChildren(h("div", { class: "pg-muted" }, spec.blurb),
      spec.presets.length ? h("div", { class: "pg-presets" }, spec.presets.map(p => h("button", { onclick: () => renderForm(p.values) }, p.label))) : null,
      ...rows);
  }

  function onFile(ev) {
    const file = ev.target.files[0];
    if (!file) { state.file = null; return; }
    const reader = new FileReader();
    reader.onload = () => { state.file = { name: file.name, b64: String(reader.result).split(",")[1] || "" }; };
    reader.readAsDataURL(file);
  }

  function renderThresholds() {
    ui.th = {};
    ui.thBox.replaceChildren(...TH.map(t => {
      const out = h("output", {}, cfg.thresholds[t.k]);
      const input = h("input", { type: "range", min: t.min, max: t.max, step: t.step, value: cfg.thresholds[t.k], oninput: () => { out.textContent = input.value; } });
      ui.th[t.k] = input;
      return h("div", { class: "row" }, h("span", { style: "width:92px" }, t.label), input, out);
    }));
  }
  const readThresholds = () => Object.fromEntries(Object.entries(ui.th).map(([k, el]) => [k, Number(el.value)]));

  function updateBanner() {
    const sim = ui.mode.value === "simulated";
    ui.banner.style.display = sim ? "block" : "none";
    ui.banner.textContent = "Simulated mode: scripted heuristics and canned usage. These numbers are NOT measurements and make no savings claim. Switch to live for real Jev and LLM-judge calls.";
  }

  function renderSpend(sp) {
    ui.spend.textContent = `Session spend: $${sp.total_usd.toFixed(4)} of $${sp.cap_usd.toFixed(2)} cap`;
  }

  // ---------------- run ----------------
  function collectInputs() {
    const inputs = {};
    for (const f of SURFACES[state.surface].fields) {
      const el = ui.inputs[f.k];
      if (f.type === "file") continue;
      inputs[f.k] = f.type === "checkbox" ? el.checked : f.type === "number" ? Number(el.value) : el.value;
    }
    if (state.surface === "multimodal") { inputs.image_b64 = state.file ? state.file.b64 : ""; inputs.image_name = state.file ? state.file.name : ""; }
    return inputs;
  }

  function setRunning(on) { state.running = on; ui.runBtn.disabled = on; ui.runBtn.textContent = on ? "Running…" : "Run"; }
  function setStatus(text, err = false) { ui.status.textContent = text; ui.status.className = "pg-status" + (err ? " err" : ""); }

  function run() {
    if (state.running) return;
    state.runTh = readThresholds();
    const msg = {
      surface: state.surface, inputs: collectInputs(), mode: ui.mode.value, thresholds: state.runTh,
      flags: { force_jev_failure: ui.force.checked, reference_run: ui.ref.checked },
    };
    ui.trace.replaceChildren(); ui.out.replaceChildren(); setStatus("Connecting…"); setRunning(true);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/playground`);
    ws.onopen = () => ws.send(JSON.stringify(msg));
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.type === "step_start") setStatus(`Running ${m.surface} (${m.mode})…`);
      else if (m.type === "decision") ui.trace.append(renderEvent(m.event));
      else if (m.type === "error") { setStatus(m.error, true); ws.close(); }
      else if (m.type === "done") { renderDone(m); setStatus(`Done · ${m.mode} · source: ${m.source}`); ws.close(); }
    };
    ws.onerror = () => setStatus("WebSocket error: is the server running?", true);
    ws.onclose = () => setRunning(false);
  }

  async function resetSession() {
    const r = await fetch("/api/playground/reset", { method: "POST" });
    renderSpend((await r.json()).spend);
    ui.trace.replaceChildren(); ui.out.replaceChildren(); setStatus("Session reset.");
  }

  // ---------------- decision cards ----------------
  const tag = (label, cls) => h("span", { class: "pg-tag " + cls }, label);
  const provTag = p => (p === "measured" ? tag("measured", "meas") : p === "simulated" ? tag("simulated", "sim") : p === "estimated" ? tag("estimated", "est") : tag(p || "not measured", "nm"));

  function meter(value, min, max, marks = []) {
    const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
    return h("div", { class: "pg-meter" }, h("div", { class: "pg-fill", style: `width:${pct}%` }),
      marks.map(m => h("i", { class: "pg-mark", style: `left:${((m.at - min) / (max - min)) * 100}%`, title: m.label })));
  }

  function answerView(ans, q) {
    if (!ans) return h("div", { class: "pg-muted" }, "—");
    if (ans.type === "noul") {
      const t = state.runTh || cfg.thresholds;
      return h("div", {}, h("span", { class: "pg-num" }, ans.value.toFixed(3)),
        meter(ans.value, 0, 1, [{ at: t.block_at, label: `block ≤ ${t.block_at}` }, { at: t.approve_at, label: `approve ≥ ${t.approve_at}` }]));
    }
    if (ans.type === "choice") {
      const probs = Object.entries(ans.probabilities || {}).sort((a, b) => b[1] - a[1]);
      return h("div", {}, h("b", {}, ans.value), probs.map(([k, p]) =>
        h("div", { class: "pg-num" }, `${k} ${p.toFixed(2)}`, meter(p, 0, 1))));
    }
    return h("div", {}, h("span", { class: "pg-num" }, ans.value.toFixed(1) + " / 5"), meter(ans.value, 0, 5, [{ at: (state.runTh || cfg.thresholds).rag_threshold, label: "RAG keep threshold" }]));
  }

  function sideView(name, s, key, q) {
    return h("div", { class: "pg-side" }, h("h4", {}, name),
      s.error ? h("div", { class: "pg-err" }, s.error) : answerView(s.answers[key], q),
      s.error ? null : h("div", { class: "pg-muted" }, `${fmtMs(s.latency_ms)} · ${s.tokens_in + s.tokens_out} tok · ${fmtUsd(s.cost_usd)}${s.cost_source && s.cost_source !== "usage" ? " (" + s.cost_source + ")" : ""}`));
  }

  function agreeBadge(v) {
    return v == null ? h("span", { class: "pg-chipd info" }, "no comparison") : v ? h("span", { class: "pg-chipd good" }, "✓ agree") : h("span", { class: "pg-chipd bad" }, "✗ disagree");
  }

  function barRow(label, a, b, fmt) {
    const max = Math.max(a, b, 1e-12);
    return h("div", { class: "pg-brow" }, h("span", {}, label),
      h("div", {}, h("div", { class: "pg-bar j", style: `width:${(a / max) * 100}%` }), h("span", { class: "pg-num" }, "Jev " + fmt(a))),
      h("div", {}, h("div", { class: "pg-bar l", style: `width:${(b / max) * 100}%` }), h("span", { class: "pg-num" }, "LLM " + fmt(b))));
  }

  function shadowView(sh) {
    const box = h("div", { class: "pg-shadow" });
    for (const key of sh.keys) {
      const q = sh.questions[key] || {};
      box.append(h("div", { class: "pg-q" },
        h("div", { class: "pg-qhead" }, h("b", {}, key), h("span", { class: "pg-qtype" }, q.type), agreeBadge(sh.agree ? sh.agree[key] : null)),
        h("div", { class: "pg-qtext" }, q.instructions),
        h("div", { class: "pg-sides" }, sideView("Jev", sh.jev, key, q), sideView("LLM judge", sh.judge, key, q))));
    }
    if (!sh.jev.error && !sh.judge.error) {
      box.append(h("div", { class: "pg-bars" },
        barRow("latency", sh.jev.latency_ms, sh.judge.latency_ms, fmtMs),
        barRow("tokens", sh.jev.tokens_in + sh.jev.tokens_out, sh.judge.tokens_in + sh.judge.tokens_out, fmtNum),
        barRow("cost", sh.jev.cost_usd, sh.judge.cost_usd, fmtUsd)));
    }
    return box;
  }

  function sideCompletion(title, c) {
    if (!c) return h("div", { class: "pg-side" }, h("h4", {}, title), h("div", { class: "pg-muted" }, "not run"));
    return h("div", { class: "pg-side" }, h("h4", {}, title),
      h("div", {}, h("b", {}, c.model)),
      h("div", { class: "pg-muted" }, `${fmtMs(c.latency_ms)} · ${c.tokens_in}+${c.tokens_out} tok · ${fmtUsd(c.cost_usd)} (${c.cost_source})`),
      h("details", {}, h("summary", {}, "answer"), h("pre", {}, c.content)));
  }

  function pairView(p) {
    const box = h("div", {});
    box.append(h("div", { class: "pg-muted" }, `pattern: ${p.pattern} · plugin: ${JSON.stringify(p.plugin)}`),
      h("div", { class: "pg-sides" }, sideCompletion("Routed (typesafe/jev-router)", p.routed), sideCompletion("Reference (pinned frontier)", p.reference)));
    if (p.overhead) box.append(h("div", { class: "pg-muted" }, `Routing probe overhead: ${fmtMs(p.overhead.latency_ms)} · ${fmtUsd(p.overhead.cost_usd)} (already included in Routed)`));
    const meta = p.routed.meta || {};
    if (meta.reason) box.append(h("div", { class: "pg-muted" }, "Router reason: " + meta.reason));
    if (meta.candidates) box.append(h("div", { class: "pg-muted" }, "Router candidate estimates (forecasts, NOT billed cost, not used in savings): " + meta.candidates.map(c => `${c.model} ${c.effort || ""} ~${fmtUsd(c.estimated_cost)}`).join(" · ")));
    box.append(p.savings
      ? h("div", {}, h("b", {}, `Saved ${fmtUsd(p.savings.cost_usd)} (${fmtPct(p.savings.cost_pct)}) and ${fmtMs(p.savings.latency_ms)} (${fmtPct(p.savings.latency_pct)})`), " ", provTag(p.savings.provenance))
      : h("div", { class: "pg-muted" }, "Savings: not measured. " + (p.note || "")));
    return box;
  }

  function renderEvent(ev) {
    const d = ev.detail || {};
    const card = h("div", { class: "pg-card" },
      h("h3", {}, HOOKS[ev.hook] || ev.hook, h("span", { class: "pg-qtype" }, ev.stage),
        h("span", { class: "pg-chipd " + tone(ev.decision) }, ev.decision),
        d.model_calls === 0 ? h("span", { class: "pg-free" }, "no model call · free") : null));
    if (d.pair) card.append(pairView(d.pair));
    (d.shadow || []).forEach(sh => card.append(shadowView(sh)));
    const brief = d.reason || d.reason_text || (d.scores ? JSON.stringify(d.scores) : "");
    if (brief) card.append(h("div", { class: "pg-muted" }, brief));
    if (d.jev_error) card.append(h("div", { class: "pg-err" }, "Jev error → fail-closed: " + d.jev_error));
    card.append(h("details", {}, h("summary", {}, "raw event"), h("pre", {}, JSON.stringify(ev, null, 2))));
    return card;
  }

  // ---------------- scoreboard ----------------
  const tile = (label, value, sub) => h("div", { class: "pg-tile" }, h("div", { class: "v" }, value), h("div", { class: "l" }, label), sub ? h("div", { class: "pg-muted" }, sub) : null);

  function benefitTile(b) {
    let value;
    if (b.value == null) value = "not measured";
    else if (b.kind === "prompt_tokens_avoided" || b.kind === "context_tokens_pruned") value = fmtNum(b.value) + " tok";
    else if (b.kind === "free_decisions") value = String(b.value);
    else if (b.kind === "gate_share") value = fmtPct(b.value) + " of cost";
    else value = fmtUsd(b.value);
    const d = b.detail || {};
    const sub = d.cost_pct != null ? `${fmtPct(d.cost_pct)} cost · ${fmtPct(d.latency_pct)} latency` : d.note || (d.full_registry_tokens != null ? `${d.full_registry_tokens} → ${d.injected_tokens} tok` : d.candidate_tokens != null ? `${d.candidate_tokens} → ${d.kept_tokens} tok` : "");
    return h("div", { class: "pg-tile" }, h("div", { class: "v" }, value), h("div", { class: "l" }, b.label, " ", provTag(b.provenance)), sub ? h("div", { class: "pg-muted" }, sub) : null);
  }

  function compareTable(sb) {
    const row = (label, j, l, pct, fmt) => h("tr", {}, h("td", {}, label), h("td", {}, fmt(j)), h("td", {}, fmt(l)), h("td", {}, fmtPct(pct)));
    return h("table", { class: "pg-table" },
      h("thead", {}, h("tr", {}, ...["", "Jev", "LLM judge", "saved"].map(t => h("th", {}, t)))),
      h("tbody", {},
        row("latency", sb.jev.latency_ms, sb.judge.latency_ms, sb.pct_saved.latency, fmtMs),
        row("tokens", sb.jev.tokens, sb.judge.tokens, sb.pct_saved.tokens, fmtNum),
        row("cost", sb.jev.cost_usd, sb.judge.cost_usd, sb.pct_saved.cost, fmtUsd)));
  }

  function renderDone(m) {
    renderSpend(m.spend);
    const sb = m.scoreboard, ss = m.session_scoreboard, sim = m.source === "simulated";
    const result = h("div", { class: "pg-card" }, h("h3", {}, "Result"));
    const r = m.result;
    if (r.final_answer) result.append(h("div", {}, h("b", {}, "Final answer: "), r.final_answer), (r.tool_results || []).map(t => h("div", { class: "pg-muted" }, "tool → " + t)),
      h("div", {}, (r.hooks_fired || []).map(x => h("span", { class: "pg-chipd info" }, HOOKS[x] || x), " ")));
    result.append(h("details", {}, h("summary", {}, "full result JSON"), h("pre", {}, JSON.stringify(r, null, 2))));

    const board = h("div", { class: "pg-card" }, h("h3", {}, "Scoreboard · this run ", tag(sim ? "simulated" : "measured", sim ? "sim" : "meas")));
    if (!sb.compared) board.append(h("p", { class: "pg-muted" }, "No Jev-vs-judge comparison in this run (static catches and pre-checks make no model calls)."));
    else board.append(compareTable(sb), h("p", { class: "pg-muted" }, sb.claims_enabled
      ? `${sb.compared} compared decisions.`
      : `Only ${sb.compared} compared decision(s): percentages are shown for transparency, not as a claim (minimum ${sb.min_decisions}).`));
    board.append(h("div", { class: "pg-tiles" },
      tile("agreement (Jev vs judge)", sb.agreement.rate == null ? "—" : fmtPct(sb.agreement.rate), `${sb.agreement.agreed}/${sb.agreement.questions} questions`),
      tile("failures", `${sb.jev_failures} Jev · ${sb.judge_failures} judge`, "judge failures are excluded from averages")));
    if (sb.disagreements.length) board.append(h("details", {}, h("summary", {}, `${sb.disagreements.length} disagreement(s)`),
      h("pre", {}, JSON.stringify(sb.disagreements, null, 2))));

    const benefits = h("div", { class: "pg-card" }, h("h3", {}, "Benefits beyond per-call cost"),
      sb.benefits.length ? h("div", { class: "pg-tiles" }, sb.benefits.map(benefitTile)) : h("p", { class: "pg-muted" }, "None for this surface."));

    const advice = h("div", { class: "pg-card" }, h("h3", {}, "Advisor"),
      (m.advice.recommendations || []).map(a => h("div", {}, h("span", { class: "pg-chipd warn" }, a.decision), " ", a.advice)),
      m.advice.auto_resolution ? h("div", { class: "pg-muted" }, m.advice.auto_resolution.advice) : null);

    const session = h("div", { class: "pg-card" }, h("h3", {}, "Session totals"),
      h("div", { class: "pg-tiles" },
        tile("compared decisions", ss.compared, `${ss.decisions} total`),
        tile("cost saved", fmtPct(ss.pct_saved.cost), ss.claims_enabled ? "claim enabled" : `needs ${ss.min_decisions}+ decisions`),
        tile("latency saved", fmtPct(ss.pct_saved.latency)),
        tile("agreement", ss.agreement.rate == null ? "—" : fmtPct(ss.agreement.rate))),
      projectionBox());

    const fit = h("div", { class: "pg-card" }, h("h3", {}, "When NOT to use a decision model"),
      h("ul", {}, h("li", {}, "2-second rule: use Jev when human experts could decide in seconds without writing an explanation."),
        h("li", {}, "No text output: Jev returns typed probabilities, never prose, code or explanations."),
        h("li", {}, "Large context decays accuracy" + (sb.max_state_tokens > 10000 ? ` — WARNING: this run sent ~${fmtNum(sb.max_state_tokens)} tokens of state.` : ".")),
        h("li", {}, "Prompt injection: your text reached Jev as decision state and this harness has no injection pre-check.")));

    ui.out.replaceChildren(result, board, benefits, advice, session, fit);
  }

  function projectionBox() {
    const rpd = h("input", { type: "number", value: 10000, min: 1 });
    const dpr = h("input", { type: "number", value: 5, min: 1 });
    const out = h("div", { class: "pg-muted" }, "Enter a workload to project from this session's measured averages.");
    const go = async () => {
      const r = await fetch(`/api/playground/projection?requests_per_day=${rpd.value}&decisions_per_request=${dpr.value}`);
      const p = await r.json();
      if (!p.projection) { out.textContent = "No compared decisions yet."; return; }
      const x = p.projection;
      out.replaceChildren(h("div", {}, `${fmtNum(x.decisions_per_day)} decisions/day → Jev ${fmtUsd(x.jev_cost_day)}/day vs LLM ${fmtUsd(x.judge_cost_day)}/day · saves ${fmtUsd(x.saved_cost_day)}/day (${fmtUsd(x.saved_cost_month)}/month)`),
        h("div", {}, `Decision wait: Jev ${x.jev_wait_hours_day.toFixed(2)} h/day vs LLM ${x.judge_wait_hours_day.toFixed(2)} h/day`),
        p.claims_enabled ? null : h("div", { class: "pg-muted" }, "Fewer than the minimum number of compared decisions: treat as illustrative."));
    };
    return h("div", { style: "margin-top:10px" }, h("div", { class: "row", style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px" },
      "requests/day", rpd, "decisions/request", dpr, h("button", { class: "ghost", onclick: go }, "Project")), out);
  }

  // ---------------- drawers ----------------
  function renderDrawers() {
    const prices = Object.entries(cfg.models).map(([slug, p]) => h("tr", {}, h("td", {}, slug), h("td", {}, "$" + p.input_per_m), h("td", {}, "$" + p.output_per_m), h("td", {}, fmtNum(p.context_tokens))));
    ui.drawers.replaceChildren(
      h("details", {}, h("summary", {}, "Rules & data the decisions are made against"),
        h("p", {}, h("b", {}, "Static deny list (no model call): ")), h("pre", {}, cfg.rules.deny_patterns.join("\n")),
        h("p", {}, h("b", {}, "Skill registry: ")), h("pre", {}, JSON.stringify(cfg.rules.skills, null, 2)),
        h("p", {}, h("b", {}, "Order ledger: ")), h("pre", {}, JSON.stringify(cfg.orders, null, 2)),
        h("p", {}, h("b", {}, "Model prices ($ per 1M tokens): ")),
        h("table", { class: "pg-table" }, h("thead", {}, h("tr", {}, ...["model", "in", "out", "context"].map(t => h("th", {}, t)))), h("tbody", {}, prices))),
      h("details", {}, h("summary", {}, "Judge prompt (what the generative baseline is asked)"),
        h("pre", {}, cfg.judge_prompt.system), h("pre", {}, cfg.judge_prompt.example)),
      h("details", {}, h("summary", {}, "Coverage: every decision system in the harness"),
        h("table", { class: "pg-table" }, h("thead", {}, h("tr", {}, ...["#", "decision system", "module", "baseline", "benefit"].map(t => h("th", {}, t)))),
          h("tbody", {}, cfg.coverage.map(c => h("tr", {}, h("td", {}, c.id), h("td", {}, c.name), h("td", {}, c.module), h("td", {}, c.baseline), h("td", {}, c.benefit)))))));
  }

  // ---------------- boot ----------------
  fetch("/api/playground/config").then(r => r.json()).then(c => {
    cfg = c; build();
    ui.mode.value = c.default_mode;
    if (!c.key_present) ui.mode.querySelector('option[value="live"]').textContent = "live (needs OPENROUTER_API_KEY)";
    updateBanner();
  }).catch(e => { root.textContent = "Playground failed to load: " + e; });
})();
