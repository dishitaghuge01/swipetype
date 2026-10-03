/* overlay-app/ui/overlay.js
 *
 * SwipeType ghost overlay client (PRD v2 rev2 section 8.7). Runs
 * either inside the real overlay host app's WebKitGTK view (Python
 * injects real data via window.swipetype.init(), section 8.6), or
 * standalone in a plain browser for design iteration, via
 * index.html?demo (section 8.8) using demo/keys.js + demo/demo-path.js.
 *
 * STAGE 2b: key generation + coordinate mapping + demo bootstrap.
 * STAGE 2c (this version): comet renderer + word flash + demo
 * playback loop. events() now actually does something.
 */

(function () {
  "use strict";

  // ---- comet tunables (PRD section 8.7.4) -- all in one place ----
  const TRAIL_MS = 420;          // how long a point stays in the tail
  const HEAD_WIDTH_U = 0.13;     // stroke width at the head, in key-units
  const TAIL_WIDTH_U = 0.02;     // stroke width at the tail, in key-units
  const SMOOTHING = 0.55;        // head lerp factor, removes touchpad jitter
  const GLOW_COLOR = "rgba(140, 190, 255, 0.85)";
  const GLOW_BLUR_U = 0.10;      // shadowBlur radius, in key-units
  const HEAD_DISC_FRESH_MS = 80; // head glow disc shows while last point < this old

  // ---- module state ----
  let KEYS = {};       // letter -> [kx, ky]
  let STAGE = null;    // {xmin, xmax, ymin, ymax}
  let LAYOUT = null;   // {cx, cy, w} or null = fit-to-screen ("contain")
  let lastRect = null; // most recent computeLayoutRect() result

  let cometPoints = [];        // [{kx, ky, t}], t = performance.now()
  let cometHeadKx = null;      // smoothed head, in key-space
  let cometHeadKy = null;
  let cometRafId = null;

  const stageEl = document.getElementById("stage");
  const keysEl = document.getElementById("keys");
  const cometCanvas = document.getElementById("comet");
  const cometCtx = cometCanvas.getContext("2d");
  const flashEl = document.getElementById("flash");

  // ---- coordinate mapping (PRD section 8.7.2 -- single source of truth) ----

  function computeLayoutRect() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const stageW = STAGE.xmax - STAGE.xmin;
    const stageH = STAGE.ymax - STAGE.ymin;
    const aspect = stageW / stageH;

    let w, cx, cy;
    if (LAYOUT) {
      w = Math.min(1, Math.max(0.2, LAYOUT.w));
      cx = LAYOUT.cx;
      cy = LAYOUT.cy;
    } else {
      w = Math.min(1, (vh / vw) * aspect);
      cx = 0.5;
      cy = 0.5;
    }

    const W_px = w * vw;
    const u = W_px / stageW;
    const H_px = stageH * u;

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

    const widthFrac = 1 - age; // 1 at head, 0 at tail
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
    // Standard smooth-polyline technique (PRD section 8.7.4): each
    // interior point is a Bezier control point between the midpoints
    // of its neighboring segments -- no visible corners, and each
    // little quad segment can still be given its own width/alpha so
    // the taper-by-age effect works.
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

      drawCometPass(pxPoints, now, u, true);  // wide, low-alpha glow pass
      drawCometPass(pxPoints, now, u, false); // narrow, bright core pass

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

  // ---- word flash (PRD section 8.7.4, CSS transition) ----

  function showWordFlash(word) {
    if (!lastRect || cometHeadKx === null) return;
    const u = lastRect.u;
    const px = (cometHeadKx - STAGE.xmin) * u;
    const py = (cometHeadKy - STAGE.ymin) * u;
    flashEl.textContent = word;
    flashEl.style.left = px + "px";
    flashEl.style.top = py - u * 0.9 + "px"; // sits a bit above the swipe end
    flashEl.classList.remove("show");
    void flashEl.offsetWidth; // force reflow so the CSS animation restarts
    flashEl.classList.add("show");
  }

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
          // Word flash disabled by design: the committed word already
          // appears in the real focused text field (chat input, etc.),
          // so flashing it again on the overlay would be redundant.
          // showWordFlash() is left intact below -- re-enabling this is
          // a one-line change if that decision ever changes.
        }
        // 'mode' events are handled via setMode(), not events() -- stage 2f.
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
        setTimeout(step, 8); // PRD section 8.8.1: 8ms per point
      } else {
        window.swipetype.events([{ event: "commit", word: demo.word }]);
        setTimeout(() => {
          idx = 0;
          setTimeout(step, 700); // pause so the tail/flash fully finish first
        }, 900);
      }
    }

    step();
  }

  function initDemo() {
    document.body.classList.add("demo-preview");
    const keys = window.SWIPETYPE_DEMO_KEYS || {};
    const stage = { xmin: -0.75, xmax: 10.0, ymin: -0.75, ymax: 2.75 };
    const params = new URLSearchParams(location.search);

    window.swipetype.init({ keys, stage, layout: null, dev: true });
    window.swipetype.setMode(params.get("mode") || "swipe");
    startDemoPlayback();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (isDemo() || !hasRealBridge()) {
      initDemo();
    }
  });
})();