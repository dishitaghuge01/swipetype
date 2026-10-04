/* overlay-app/ui/overlay.js
 *
 * SwipeType ghost overlay client (PRD v2 rev2 section 8.7). Runs
 * either inside the real overlay host app's WebKitGTK view (Python
 * injects real data via window.swipetype.init(), section 8.6), or
 * standalone in a plain browser for design iteration, via
 * index.html?demo (section 8.8) using demo/keys.js + demo/demo-path.js.
 *
 * STAGE 2b: key generation + coordinate mapping + demo bootstrap.
 * STAGE 2c: comet renderer + demo playback loop.
 * STAGE 2g (this version): layout mode -- drag to move, aspect-locked
 * corner-handle resize, Fit screen / Reset / Done toolbar, persisted
 * via postMessage to the Python host (section 8.7.6).
 */

(function () {
  "use strict";

  // ---- comet tunables (PRD section 8.7.4) -- all in one place ----
  const TRAIL_MS = 420;
  const HEAD_WIDTH_U = 0.13;
  const TAIL_WIDTH_U = 0.02;
  const SMOOTHING = 0.55;
  const GLOW_COLOR = "rgba(140, 190, 255, 0.85)";
  const GLOW_BLUR_U = 0.10;
  const HEAD_DISC_FRESH_MS = 80;

  // ---- module state ----
  let KEYS = {};       // letter -> [kx, ky]
  let STAGE = null;    // {xmin, xmax, ymin, ymax}
  let LAYOUT = null;   // {cx, cy, w} or null = fit-to-screen ("contain")
  let lastRect = null; // most recent computeLayoutRect() result

  let cometPoints = [];
  let cometHeadKx = null;
  let cometHeadKy = null;
  let cometRafId = null;

  let dragState = null; // {type:'move'|'resize', handle, pointerId, ...}

  const stageEl = document.getElementById("stage");
  const keysEl = document.getElementById("keys");
  const cometCanvas = document.getElementById("comet");
  const cometCtx = cometCanvas.getContext("2d");
  const flashEl = document.getElementById("flash");
  const outlineEl = document.getElementById("outline");
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
    }
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
    cometCtx.clearRect(0, 0, cometCanvas.width, cometCanvas.height);
  }

  function ensureCometLoop() {
    if (cometRafId === null) {
      cometRafId = requestAnimationFrame(drawCometFrame);
    }
  }

  function midpoint(a, b) {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  function strokeQuad(from, ctrl, to, pointT, now, u, isGlow) {
    const age = Math.min(1, (now - pointT) / TRAIL_MS);
    const alpha = Math.pow(1 - age, 1.5);
    if (alpha <= 0.01) return;

    const widthFrac = 1 - age;
    const baseWidth = (TAIL_WIDTH_U + (HEAD_WIDTH_U - TAIL_WIDTH_U) * widthFrac) * u;

    cometCtx.beginPath();
    cometCtx.moveTo(from.x, from.y);
    cometCtx.quadraticCurveTo(ctrl.x, ctrl.y, to.x, to.y);
    cometCtx.lineCap = "round";

    if (isGlow) {
      cometCtx.strokeStyle = `rgba(140, 190, 255, ${0.35 * alpha})`;
      cometCtx.lineWidth = baseWidth * 3.2;
      cometCtx.shadowColor = GLOW_COLOR;
      cometCtx.shadowBlur = GLOW_BLUR_U * u;
    } else {
      cometCtx.strokeStyle = `rgba(255, 255, 255, ${0.95 * alpha})`;
      cometCtx.lineWidth = baseWidth;
      cometCtx.shadowBlur = 0;
    }
    cometCtx.stroke();
  }

  function drawCometPass(pxPoints, now, u, isGlow) {
    const n = pxPoints.length;
    if (n === 2) {
      strokeQuad(pxPoints[0], pxPoints[0], pxPoints[1], pxPoints[1].t, now, u, isGlow);
      return;
    }
    for (let i = 1; i < n - 1; i++) {
      const prevMid = midpoint(pxPoints[i - 1], pxPoints[i]);
      const nextMid = midpoint(pxPoints[i], pxPoints[i + 1]);
      strokeQuad(prevMid, pxPoints[i], nextMid, pxPoints[i].t, now, u, isGlow);
    }
  }

  function drawCometFrame() {
    cometRafId = null;
    const now = performance.now();
    cometPoints = cometPoints.filter((p) => now - p.t <= TRAIL_MS);

    cometCtx.clearRect(0, 0, cometCanvas.width, cometCanvas.height);

    if (cometPoints.length >= 2 && lastRect) {
      const u = lastRect.u;
      const pxPoints = cometPoints.map((p) => ({
        x: (p.kx - STAGE.xmin) * u,
        y: (p.ky - STAGE.ymin) * u,
        t: p.t,
      }));

      drawCometPass(pxPoints, now, u, true);
      drawCometPass(pxPoints, now, u, false);

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

    if (cometPoints.length > 0) {
      cometRafId = requestAnimationFrame(drawCometFrame);
    }
  }

  // ---- word flash (disabled by design -- see stage 2c follow-up) ----

  function showWordFlash(_word) {
    // Intentionally a no-op: the committed word already appears in
    // the real focused text field, so flashing it again here would be
    // redundant. Left in place (unused) in case this is revisited.
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

    // The anchor is the OPPOSITE corner, in viewport px -- it must
    // stay fixed while the dragged corner follows the pointer.
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
      // Clamping cx/cy (the stage CENTER, as a fraction of the
      // viewport) to [0, 1] guarantees at least half the stage stays
      // on screen on every edge, regardless of its width -- PRD
      // 8.7.6's "at least 50% of the stage must stay on screen".
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
        } else if (ev.event === "commit") {
          showWordFlash(ev.word);
        }
      }
    },

    setMode(mode) {
      document.body.dataset.mode = mode; // "idle" | "swipe" | "layout"
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
        setTimeout(step, 8);
      } else {
        window.swipetype.events([{ event: "commit", word: demo.word }]);
        setTimeout(() => {
          idx = 0;
          setTimeout(step, 700);
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
  });
})();