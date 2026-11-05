/* overlay-app/ui/overlay.js
 *
 * SwipeType ghost overlay client (PRD v2 rev2 section 8.7). Runs
 * either inside the real overlay host app's WebKitGTK view (Python
 * injects real data via window.swipetype.init(), section 8.6), or
 * standalone in a plain browser for design iteration, via
 * index.html?demo (section 8.8) using demo/keys.js + demo/demo-path.js.
 *
 * Key generation + coordinate mapping, comet renderer, layout mode
 * (drag / aspect-locked corner resize / Fit screen / Reset / Done),
 * and a cached nearest-key glow under the comet head.
 *
 * Perf note: the comet is drawn with layered translucent strokes, NOT
 * canvas shadowBlur. shadowBlur on ~40 segments per frame measured at
 * ~25% CPU in the web process; layered strokes look near-identical at
 * a fraction of the cost (PRD risk S5).
 */

(function () {
  "use strict";

  // ---- comet tunables (PRD section 8.7.4) -- all in one place ----
  const TRAIL_MS = 420;          // how long a point stays in the tail
  const HEAD_WIDTH_U = 0.13;     // stroke width at the head, in key-units
  const TAIL_WIDTH_U = 0.02;     // stroke width at the tail, in key-units
  const SMOOTHING = 0.55;        // head lerp factor, removes touchpad jitter
  const HEAD_DISC_FRESH_MS = 80; // head glow disc shows while last point < this old
  const ACTIVE_KEY_FRESH_MS = 150; // nearest-key glow shows while last point < this old
  const TRAIL_BUCKETS = 8;       // batch strokes by age, not one draw call per point

  // Stroke passes, drawn back to front for every segment. "width" is a
  // multiplier on the segment's tapered base width; "alpha" is the max
  // opacity (further scaled by the segment's age fade). Edit these to
  // restyle the glow -- no blur involved, so it stays cheap.
  const COMET_PASSES = [
    { width: 3.4, alpha: 0.10, rgb: "140, 190, 255" }, // wide, faint blue halo
    { width: 2.0, alpha: 0.22, rgb: "170, 205, 255" }, // medium glow
    { width: 1.0, alpha: 0.95, rgb: "255, 255, 255" }, // bright white core
  ];

  // ---- module state ----
  let KEYS = {};       // letter -> [kx, ky]
  let STAGE = null;    // {xmin, xmax, ymin, ymax}
  let LAYOUT = null;   // {cx, cy, w} or null = fit-to-screen ("contain")
  let lastRect = null; // most recent computeLayoutRect() result

  let keyEls = {};         // letter -> DOM element (rebuilt by renderKeys)
  let activeLetter = null;
  let activeGlowEl = null; // one cached, translated glow instead of repainting keys

  let cometPoints = [];        // [{kx, ky, t}], t = performance.now()
  let cometHeadKx = null;      // smoothed head, in key-space
  let cometHeadKy = null;
  let cometRafId = null;

  let dragState = null; // {type:'move'|'resize', ...}

  const stageEl = document.getElementById("stage");
  const keysEl = document.getElementById("keys");
  const cometCanvas = document.getElementById("comet");
  const cometCtx = cometCanvas.getContext("2d");
  const toolbarEl = document.getElementById("toolbar");

  // ---- coordinate mapping (PRD section 8.7.2 -- single source of truth) ----

  function computeDefaultLayoutValues() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const stageW = STAGE.xmax - STAGE.xmin;
    const stageH = STAGE.ymax - STAGE.ymin;
    const aspect = stageW / stageH;
    const w = Math.min(1, (vh / vw) * aspect);
    return { cx: 0.5, cy: 0.5, w };
  }

  function getCurrentLayoutValues() {
    return LAYOUT ? { ...LAYOUT } : computeDefaultLayoutValues();
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function computeLayoutRect() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const stageW = STAGE.xmax - STAGE.xmin;

    const layout = getCurrentLayoutValues();
    const w = clamp(layout.w, 0.2, 1);
    const cx = layout.cx;
    const cy = layout.cy;

    const W_px = w * vw;
    const u = W_px / stageW;
    const H_px = (STAGE.ymax - STAGE.ymin) * u;

    return {
      left: cx * vw - W_px / 2,
      top: cy * vh - H_px / 2,
      width: W_px,
      height: H_px,
      u,
    };
  }

  function applyLayoutRect(rect) {
    stageEl.style.left = rect.left + "px";
    stageEl.style.top = rect.top + "px";
    stageEl.style.width = rect.width + "px";
    stageEl.style.height = rect.height + "px";
    stageEl.style.setProperty("--u", rect.u + "px");
  }

  function keyToStagePx(kx, ky, rect) {
    return [(kx - STAGE.xmin) * rect.u, (ky - STAGE.ymin) * rect.u];
  }

  // ---- key rendering (PRD section 8.7.3) ----

  function renderKeys(rect) {
    keysEl.innerHTML = "";
    keyEls = {};
    activeLetter = null;
    for (const letter of Object.keys(KEYS)) {
      const [kx, ky] = KEYS[letter];
      const [px, py] = keyToStagePx(kx, ky, rect);
      const div = document.createElement("div");
      div.className = "gk-key";
      div.dataset.letter = letter;
      div.style.left = px + "px";
      div.style.top = py + "px";
      div.textContent = letter;
      keysEl.appendChild(div);
      keyEls[letter] = div;
    }
    activeGlowEl = document.createElement("div");
    activeGlowEl.className = "gk-active-glow";
    keysEl.appendChild(activeGlowEl);
  }

  // ---- nearest-key glow ----

  function setActiveKey(letter) {
    if (letter === activeLetter) return;
    activeLetter = letter;
    if (!activeGlowEl) return;
    if (letter && lastRect) {
      const [px, py] = keyToStagePx(...KEYS[letter], lastRect);
      activeGlowEl.style.transform = `translate3d(${px}px, ${py}px, 0) translate(-50%, -50%)`;
      activeGlowEl.style.opacity = "1";
    } else {
      activeGlowEl.style.opacity = "0";
    }
  }

  function nearestKey(kx, ky) {
    let best = null;
    let bestD = Infinity;
    for (const letter of Object.keys(KEYS)) {
      const dx = KEYS[letter][0] - kx;
      const dy = KEYS[letter][1] - ky;
      const d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        best = letter;
      }
    }
    return best;
  }

  // ---- comet canvas sizing ----

  function resizeCometCanvas(rect) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cometCanvas.width = Math.round(rect.width * dpr);
    cometCanvas.height = Math.round(rect.height * dpr);
    cometCanvas.style.width = rect.width + "px";
    cometCanvas.style.height = rect.height + "px";
    cometCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function relayout() {
    if (!STAGE) return; // a resize may precede the host's init() after reload
    lastRect = computeLayoutRect();
    applyLayoutRect(lastRect);
    resizeCometCanvas(lastRect);
    renderKeys(lastRect);
  }

  window.addEventListener("resize", relayout);

  // ---- comet renderer (PRD section 8.7.4) ----

  function pushCometPoint(kx, ky) {
    const now = performance.now();
    if (cometHeadKx === null) {
      cometHeadKx = kx;
      cometHeadKy = ky;
    } else {
      cometHeadKx += (kx - cometHeadKx) * SMOOTHING;
      cometHeadKy += (ky - cometHeadKy) * SMOOTHING;
    }
    cometPoints.push({ kx: cometHeadKx, ky: cometHeadKy, t: now });
    ensureCometLoop();
  }

  function clearComet() {
    cometPoints = [];
    cometHeadKx = null;
    cometHeadKy = null;
    if (cometRafId !== null) cancelAnimationFrame(cometRafId);
    cometRafId = null;
    cometCtx.clearRect(0, 0, cometCanvas.width, cometCanvas.height);
    setActiveKey(null);
  }

  function ensureCometLoop() {
    if (cometRafId === null) {
      cometRafId = requestAnimationFrame(drawCometFrame);
    }
  }

  function midpoint(a, b) {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  function drawCometPass(pxPoints, now, u, pass) {
    // Adjacent segments in the same age bucket share a single stroke.
    // Keep midpoint Beziers, taper and fade, but bound draw calls to
    // TRAIL_BUCKETS per pass regardless of touchpad sampling rate.
    const buckets = Array.from({ length: TRAIL_BUCKETS }, () => []);
    for (let i = 1; i < pxPoints.length; i++) {
      const p = pxPoints[i];
      const age = clamp((now - p.t) / TRAIL_MS, 0, 1);
      buckets[Math.min(TRAIL_BUCKETS - 1, Math.floor(age * TRAIL_BUCKETS))].push(i);
    }
    for (let b = 0; b < buckets.length; b++) {
      if (!buckets[b].length) continue;
      const age = (b + 0.5) / TRAIL_BUCKETS;
      const alpha = pass.alpha * Math.pow(1 - age, 1.5);
      if (alpha <= 0.01) continue;
      cometCtx.beginPath();
      let previous = -2;
      for (const i of buckets[b]) {
        const p = pxPoints[i];
        const from = i === 1 ? pxPoints[0] : midpoint(pxPoints[i - 1], p);
        const to = i === pxPoints.length - 1 ? p : midpoint(p, pxPoints[i + 1]);
        if (i !== previous + 1) cometCtx.moveTo(from.x, from.y);
        cometCtx.quadraticCurveTo(p.x, p.y, to.x, to.y);
        previous = i;
      }
      cometCtx.lineCap = "round";
      cometCtx.lineJoin = "round";
      cometCtx.strokeStyle = `rgba(${pass.rgb}, ${alpha})`;
      cometCtx.lineWidth = (TAIL_WIDTH_U + (HEAD_WIDTH_U - TAIL_WIDTH_U) * (1 - age)) * u * pass.width;
      cometCtx.stroke();
    }
  }

  function drawCometFrame() {
    cometRafId = null;
    const now = performance.now();
    cometPoints = cometPoints.filter((p) => now - p.t <= TRAIL_MS);

    // Nearest-key glow follows the head while the head is fresh, and
    // switches off as soon as the finger stops / lifts.
    if (cometPoints.length === 0) {
      setActiveKey(null);
    } else {
      const lastPt = cometPoints[cometPoints.length - 1];
      if (now - lastPt.t < ACTIVE_KEY_FRESH_MS) {
        setActiveKey(nearestKey(cometHeadKx, cometHeadKy));
      } else {
        setActiveKey(null);
      }
    }

    cometCtx.clearRect(0, 0, cometCanvas.width, cometCanvas.height);

    if (cometPoints.length >= 2 && lastRect) {
      const u = lastRect.u;
      const pxPoints = cometPoints.map((p) => ({
        x: (p.kx - STAGE.xmin) * u,
        y: (p.ky - STAGE.ymin) * u,
        t: p.t,
      }));

      for (const pass of COMET_PASSES) drawCometPass(pxPoints, now, u, pass);

      const last = pxPoints[pxPoints.length - 1];
      if (now - last.t < HEAD_DISC_FRESH_MS) {
        const r = HEAD_WIDTH_U * u * 2.2;
        const grad = cometCtx.createRadialGradient(last.x, last.y, 0, last.x, last.y, r);
        grad.addColorStop(0, "rgba(255,255,255,0.9)");
        grad.addColorStop(1, "rgba(255,255,255,0)");
        cometCtx.fillStyle = grad;
        cometCtx.beginPath();
        cometCtx.arc(last.x, last.y, r, 0, Math.PI * 2);
        cometCtx.fill();
      }
    }

    // rAF runs ONLY while the buffer is non-empty (idle cost ~ zero).
    if (cometPoints.length > 0) {
      cometRafId = requestAnimationFrame(drawCometFrame);
    }
  }

  // ---- host bridge (outbound) ----

  function sendToHost(payload) {
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.swipetype) {
      window.webkit.messageHandlers.swipetype.postMessage(payload);
    } else {
      console.log("[swipetype demo] would send to host:", payload);
    }
  }

  function sendLayoutToHost() {
    sendToHost({ type: "layout", layout: getCurrentLayoutValues() });
  }

  // ---- layout mode: drag to move, corner-handle resize (PRD 8.7.6) ----

  function onStagePointerDown(e) {
    if (document.body.dataset.mode !== "layout") return;
    if (e.target.closest(".handle")) return; // handles have their own listener
    e.preventDefault();
    dragState = {
      type: "move",
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startClientY: e.clientY,
      startLayout: getCurrentLayoutValues(),
    };
    stageEl.setPointerCapture(e.pointerId);
  }

  function onHandlePointerDown(e) {
    if (document.body.dataset.mode !== "layout") return;
    e.stopPropagation();
    e.preventDefault();

    const handle = e.currentTarget.dataset.handle;
    const rect = computeLayoutRect();

    // The anchor is the OPPOSITE corner, in viewport px -- it stays
    // fixed while the dragged corner follows the pointer.
    const anchors = {
      tl: { x: rect.left + rect.width, y: rect.top + rect.height },
      tr: { x: rect.left, y: rect.top + rect.height },
      bl: { x: rect.left + rect.width, y: rect.top },
      br: { x: rect.left, y: rect.top },
    };

    dragState = {
      type: "resize",
      handle,
      pointerId: e.pointerId,
      anchor: anchors[handle],
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e) {
    if (!dragState) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    if (dragState.type === "move") {
      const dx = e.clientX - dragState.startClientX;
      const dy = e.clientY - dragState.startClientY;
      // Clamping the stage CENTER to [0,1] of the viewport keeps at
      // least half the stage on screen on every edge.
      const newCx = clamp(dragState.startLayout.cx + dx / vw, 0, 1);
      const newCy = clamp(dragState.startLayout.cy + dy / vh, 0, 1);
      LAYOUT = { cx: newCx, cy: newCy, w: dragState.startLayout.w };
      relayout();
      return;
    }

    if (dragState.type === "resize") {
      const stageW = STAGE.xmax - STAGE.xmin;
      const stageH = STAGE.ymax - STAGE.ymin;
      const aspect = stageW / stageH; // locked -- square keys are the point
      const anchor = dragState.anchor;

      let W_px = Math.abs(e.clientX - anchor.x);
      W_px = clamp(W_px, 0.2 * vw, vw);
      const H_px = W_px / aspect;

      let left, top;
      if (dragState.handle === "br") { left = anchor.x; top = anchor.y; }
      else if (dragState.handle === "bl") { left = anchor.x - W_px; top = anchor.y; }
      else if (dragState.handle === "tr") { left = anchor.x; top = anchor.y - H_px; }
      else { left = anchor.x - W_px; top = anchor.y - H_px; } // "tl"

      const newCx = clamp((left + W_px / 2) / vw, 0, 1);
      const newCy = clamp((top + H_px / 2) / vh, 0, 1);
      const newW = clamp(W_px / vw, 0.2, 1.0);

      LAYOUT = { cx: newCx, cy: newCy, w: newW };
      relayout();
    }
  }

  function onPointerUp(_e) {
    if (!dragState) return;
    dragState = null;
    sendLayoutToHost(); // one message per drag, on release -- not per-move
  }

  stageEl.addEventListener("pointerdown", onStagePointerDown);
  document.querySelectorAll(".handle").forEach((h) => {
    h.addEventListener("pointerdown", onHandlePointerDown);
  });
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);

  toolbarEl.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === "fit") {
      LAYOUT = computeDefaultLayoutValues();
      relayout();
      sendLayoutToHost();
    } else if (action === "reset") {
      LAYOUT = null;
      relayout();
      sendToHost({ type: "layout-reset" });
    } else if (action === "done") {
      sendToHost({ type: "layout-done" });
    }
  });

  // ---- public API (PRD section 8.6) ----

  window.swipetype = {
    init(config) {
      KEYS = config.keys || {};
      STAGE = config.stage;
      LAYOUT = config.layout || null;
      relayout();
    },

    events(batch) {
      for (const ev of batch) {
        if (ev.event === "point") {
          pushCometPoint(ev.x, ev.y);
        } else if (ev.event === "clear") {
          clearComet();
        }
        // "commit": the word flash is intentionally disabled -- the
        // committed word already appears in the real focused text
        // field. "mode" is handled via setMode(), not events().
      }
    },

    setMode(mode) {
      document.body.dataset.mode = mode; // "idle" | "swipe" | "layout"
      if (mode !== "swipe") clearComet();
    },
  };

  // ---- demo bootstrap + playback (PRD section 8.8) ----

  function hasRealBridge() {
    return !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.swipetype);
  }

  function isDemo() {
    return new URLSearchParams(location.search).has("demo");
  }

  function startDemoPlayback() {
    const demo = window.SWIPETYPE_DEMO;
    if (!demo || !demo.path || demo.path.length === 0) return;

    let idx = 0;
    function step() {
      if (idx === 0) {
        window.swipetype.events([{ event: "clear" }]);
      }
      const [kx, ky] = demo.path[idx];
      window.swipetype.events([{ event: "point", x: kx, y: ky }]);
      idx++;

      if (idx < demo.path.length) {
        setTimeout(step, 8); // PRD section 8.8.1: 8ms per point
      } else {
        setTimeout(() => {
          idx = 0;
          setTimeout(step, 700); // pause so the tail fully finishes first
        }, 900);
      }
    }
    step();
  }

  function initDemo() {
    const keys = window.SWIPETYPE_DEMO_KEYS || {};
    const stage = { xmin: -0.75, xmax: 10.0, ymin: -0.75, ymax: 2.75 };
    const params = new URLSearchParams(location.search);

    window.swipetype.init({ keys, stage, layout: null, dev: true });
    window.swipetype.setMode(params.get("mode") || "swipe");

    if ((params.get("mode") || "swipe") === "swipe") {
      startDemoPlayback();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (isDemo() || !hasRealBridge()) {
      initDemo();
    }
    // else: the real overlay host calls window.swipetype.init() itself
    // once its WebKit load-changed FINISHED handler fires.
  });
})();