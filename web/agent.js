// agent.js — Gemini Live (Vertex) voice agent, push-to-talk, over a backend WebSocket proxy.
//
// Transport: browser <-> wss://<backend>/live <-> Gemini Live. Mic audio is captured as
// PCM16 16 kHz and streamed only while the talk button is held; agent audio arrives as
// PCM16 24 kHz and is played back gaplessly. No OpenAI, no API key in the client.

const el = (id) => document.getElementById(id);
const stateEl = el("state"), stateText = el("stateText"), caption = el("caption"), itemsEl = el("items");

let ws = null, micStream = null, inCtx = null, outCtx = null, proc = null;
let holding = false, state = "connecting", agentLine = "", nextPlayTime = 0;
let sweepId = null;
window.setSweepId = (id) => { sweepId = id; };

function setState(s, label) {
  state = s; stateEl.className = s; stateText.textContent = label;
  talkBtn.disabled = (s !== "ready");
}

function addInventory(kind, label, note) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<div class="k">${kind || "item"}</div><div class="v">${label || ""}` +
    (note ? ` <span style="opacity:.6">— ${note}</span>` : "") + `</div>`;
  itemsEl.prepend(row);
}

function syncObservation(a) {
  if (!sweepId) return;
  fetch(`/sweeps/${sweepId}/observation`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: a.kind || "note", label: a.label || "", note: a.note || "", frame_ref: "", at: 0 }),
  }).catch(() => {});
}

function wsSend(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

// ---- Audio playback (24 kHz PCM16 -> gapless) ----
function playChunk(arrayBuffer) {
  const i16 = new Int16Array(arrayBuffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
  const buf = outCtx.createBuffer(1, f32.length, 24000);
  buf.getChannelData(0).set(f32);
  const node = outCtx.createBufferSource();
  node.buffer = buf; node.connect(outCtx.destination);
  const now = outCtx.currentTime;
  if (nextPlayTime < now) nextPlayTime = now;
  node.start(nextPlayTime); nextPlayTime += buf.duration;
  if (state !== "speaking") setState("speaking", "Speaking…");
}

async function initAudio() {
  micStream = await navigator.mediaDevices.getUserMedia({ audio: {
    echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  inCtx = new AudioContext({ sampleRate: 16000 });
  outCtx = new AudioContext({ sampleRate: 24000 });
  const src = inCtx.createMediaStreamSource(micStream);
  proc = inCtx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e) => {
    if (!holding || !ws || ws.readyState !== 1) return;
    const f32 = e.inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 32768 : s * 32767;
    }
    ws.send(i16.buffer);
  };
  src.connect(proc); proc.connect(inCtx.destination);
}

function resumeAudio() { inCtx && inCtx.resume(); outCtx && outCtx.resume(); }

// ---- Connect ----
async function start() {
  try {
    setState("connecting", "Connecting…");
    await initAudio();
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/live";
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onmessage = onMessage;
    ws.onerror = () => setState("error", "ws error");
    ws.onclose = (e) => setState("error", `closed ${e.code}${e.reason ? " " + e.reason : ""}`);
  } catch (err) {
    console.error(err);
    setState("error", "mic/connect error: " + err.message);
  }
}

function onMessage(e) {
  if (e.data instanceof ArrayBuffer) { playChunk(e.data); return; }
  let ev; try { ev = JSON.parse(e.data); } catch { return; }
  switch (ev.type) {
    case "ready":
      setState("ready", "Ready — hold to talk");
      wsSend({ type: "text", text: "Greet the user in one short sentence and tell them to tap Start sweep, then hold the blue button to talk." });
      break;
    case "out_transcript": agentLine += ev.text || ""; caption.textContent = agentLine; break;
    case "in_transcript": caption.textContent = "you: " + (ev.text || ""); break;
    case "turn_complete": agentLine = ""; if (state !== "listening") setState("ready", "Ready — hold to talk"); break;
    case "tool_call":
      if (ev.name === "log_observation") { addInventory(ev.args.kind, ev.args.label, ev.args.note); syncObservation(ev.args); }
      wsSend({ type: "tool_result", id: ev.id, name: ev.name, output: "logged" });
      break;
    case "error": setState("error", "⚠︎ " + ev.message); break;
  }
}

// ---- Push-to-talk ----
const talkBtn = el("talk");
function pttDown(e) {
  e.preventDefault();
  if (state !== "ready") return;
  resumeAudio();
  holding = true;
  talkBtn.classList.add("holding");
  wsSend({ type: "activity_start" });
  setState("listening", "● Listening — speak now");
}
function pttUp(e) {
  e.preventDefault();
  if (!holding) return;
  holding = false;
  talkBtn.classList.remove("holding");
  wsSend({ type: "activity_end" });
  setState("thinking", "Thinking…");
}
talkBtn.addEventListener("pointerdown", pttDown);
talkBtn.addEventListener("pointerup", pttUp);
talkBtn.addEventListener("pointercancel", pttUp);
talkBtn.addEventListener("pointerleave", (e) => { if (holding) pttUp(e); });

// ---- Sweep button ----
let sweeping = false;
const sweepBtn = el("sweepBtn");
sweepBtn.onclick = () => {
  resumeAudio();
  sweeping = !sweeping;
  sweepBtn.textContent = sweeping ? "Stop sweep" : "Start sweep";
  sweepBtn.className = sweeping ? "active" : "";
  const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sweep;
  if (bridge) bridge.postMessage(sweeping ? "start" : "stop");
  if (sweeping) wsSend({ type: "text", text: "The sweep has started and the camera is on. Tell me which area to point at first." });
  else { caption.textContent = "Sweep ended — compiling packet…";
         wsSend({ type: "text", text: "The sweep has ended. One short closing sentence; the packet is being compiled." }); }
};

// Vision frames aren't streamed to Gemini Live in this build (audio-only turns); the
// recognition pipeline runs on the uploaded frames after Stop. Kept as a no-op so native
// calls don't error.
window.pushFrame = function () {};

// Resume audio contexts on the first user gesture (autoplay policy).
document.addEventListener("pointerdown", resumeAudio, { once: true });

start();
