"use strict";
// Drives the dashboard's real rfb.js + audio.js with a fake WebSocket and a fake AudioContext.
// Byte layouts follow QEMU ui/vnc.c: ack rect -259, client msg 255/1/{0 enable,1 disable,2 format},
// server msg 255/1/{0 end,1 begin,2 data + u32 length}.
const fs = require("fs");
const path = require("path");
const dir = path.join(__dirname, "..", "src", "vortax", "dashboard", "static");

class FakeCtx {
  constructor(o) { this.sampleRate = o.sampleRate; this.currentTime = 0; this.state = "running"; this.scheduled = []; FakeCtx.last = this; }
  createGain() { return { gain: { value: 1 }, connect() {} }; }
  createBuffer(ch, n, rate) { const d = Array.from({ length: ch }, () => new Float32Array(n)); return { length: n, duration: n / rate, getChannelData: (c) => d[c] }; }
  createBufferSource() { const c = this; return { connect() {}, start(t) { c.scheduled.push({ t, b: this.buffer }); } }; }
  resume() { return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  get destination() { return {}; }
}
class FakeWS {
  constructor() { this.readyState = 1; this.sent = []; FakeWS.last = this; }
  send(d) { this.sent.push(...d); }
  close() { this.readyState = 3; }
}
global.ImageData = class { constructor(w, h) { this.width = w; this.height = h; this.data = new Uint8ClampedArray(w * h * 4); } };
global.window = global; global.AudioContext = FakeCtx; global.WebSocket = FakeWS;
global.TextEncoder = TextEncoder; global.TextDecoder = TextDecoder;
const canvas = { getContext: () => ({ createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }), fillRect() {}, putImageData() {}, drawImage() {} }), width: 0, height: 0 };
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(dir, "audio.js"), "utf8") + "\n" + fs.readFileSync(path.join(dir, "rfb.js"), "utf8"));

let failed = 0;
const check = (name, ok, extra) => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  -> " + extra)); if (!ok) failed++; };
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const s16 = (v) => v.flatMap((x) => [x & 255, (x >> 8) & 255]);

function connect() {
  const events = { audio: null, err: null };
  const rfb = new RFB(canvas, "ws://x", [], { onaudio: (v) => { events.audio = v; }, onerror: (e) => { events.err = e; } });
  const ws = FakeWS.last;
  const feed = (bytes) => rfb._push(new Uint8Array(bytes));
  feed([..."RFB 003.008\n"].map((c) => c.charCodeAt(0)));
  feed([1, 1]); feed([0, 0, 0, 0]);
  const name = [...("vm")].map((c) => c.charCodeAt(0));
  feed([0, 64, 0, 48, 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 0, 8, 16, 0, 0, 0, ...u32(name.length), ...name]);
  return { rfb, ws, feed, events };
}
// FramebufferUpdate with one rect: x y w h encoding
const ACK = [0, 0, 0, 1, 0, 0, 0, 0, 0, 64, 0, 48, 0xff, 0xff, 0xfe, 0xfd];

console.log("handshake");
let t = connect();
// 12 (version) + 1 (security choice) + 1 (ClientInit) + 20 (SetPixelFormat) bytes come first
const setEnc = t.ws.sent.slice(34);
const n = (setEnc[2] << 8) | setEnc[3];
const list = []; for (let i = 0; i < n; i++) list.push(((setEnc[4 + i * 4] << 24) | (setEnc[5 + i * 4] << 16) | (setEnc[6 + i * 4] << 8) | setEnc[7 + i * 4]) | 0);
check("SetEncodings offers -259 next to the old ones", eq(list, [0, 1, -223, -259]), list);

console.log("ack and enable");
t.feed(ACK);
check("ack rect accepted, no error", t.events.err === null && t.events.audio === true, t.events.err);
check("audio is not started before the user asks", t.rfb.audioOn === false && FakeCtx.last === undefined);
const before = t.ws.sent.length;
const player = new QemuAudio({ frequency: 22050, channels: 1 });
check("enableAudio returns true", t.rfb.enableAudio(player) === true);
check("SetFormat(S16, mono, 22050) then Enable, byte exact",
  eq(t.ws.sent.slice(before, before + 14), [255, 1, 0, 2, 3, 1, 0, 0, 0x56, 0x22, 255, 1, 0, 0]), t.ws.sent.slice(before, before + 14));
check("second enable is ignored", t.rfb.enableAudio(player) === false);

console.log("data");
const ctx = FakeCtx.last;
t.feed([255, 1, 0, 2, ...u32(6), ...s16([0, 16384, -16384])]);
check("one buffer of 3 mono frames", ctx.scheduled.length === 1 && ctx.scheduled[0].b.length === 3, ctx.scheduled.length);
const ch = ctx.scheduled[0].b.getChannelData(0);
check("samples decoded", ch[0] === 0 && ch[1] === 0.5 && ch[2] === -0.5, Array.from(ch));
check("starts after the jitter buffer", Math.abs(ctx.scheduled[0].t - 0.12) < 1e-9, ctx.scheduled[0].t);
const msg = [255, 1, 0, 2, ...u32(4), ...s16([100, 200])];
const k = ctx.scheduled.length;
for (const b of msg) t.feed([b]);
const split = ctx.scheduled[ctx.scheduled.length - 1];
check("message split into single bytes still plays once, with every sample",
  ctx.scheduled.length === k + 1 && t.events.err === null && split.b.length === 2 &&
  Math.abs(split.b.getChannelData(0)[1] - 200 / 32768) < 1e-9, split.b.length);
t.feed([255, 1, 0, 2, ...u32(3), ...s16([1]).slice(0, 1), 5, 0]);   // 3 bytes: 1 frame + 1 byte left over
t.feed([255, 1, 0, 2, ...u32(1), 0]);                                // completes the second frame
check("a frame split across two messages is rejoined", ctx.scheduled.length === k + 3, ctx.scheduled.length - k);
t.feed([255, 1, 0, 0]); t.feed([255, 1, 0, 1]);
check("begin and end are accepted", t.events.err === null, t.events.err);
t.feed([0, 0, 0, 1, 0, 0, 0, 0, 0, 64, 0, 48, 0, 0, 0, 0, ...new Array(64 * 48 * 4).fill(7)]);
check("normal pixel updates still work next to audio", t.events.err === null, t.events.err);

console.log("disable and errors");
const b2 = t.ws.sent.length;
t.rfb.disableAudio();
check("Disable message sent, byte exact", eq(t.ws.sent.slice(b2), [255, 1, 0, 1]), t.ws.sent.slice(b2));
check("player closed", ctx.state === "closed");
t.feed([255, 1, 0, 2, ...u32(2), 1, 2]);
check("data after disable is ignored safely", t.events.err === null, t.events.err);

t = connect(); t.feed(ACK); t.feed([255, 9, 0, 0]);
check("unknown QEMU message raises an error", t.events.err !== null);
t = connect(); t.feed(ACK); t.feed([255, 1, 0, 7]);
check("unknown audio operation raises an error", t.events.err !== null);

console.log("no sound card");
t = connect();
check("enableAudio refuses when the server never acked", t.rfb.enableAudio(new QemuAudio()) === false && t.rfb.audioReady !== true);

console.log("blocked autoplay");
t = connect(); t.feed(ACK);
t.rfb.enableAudio(new QemuAudio()); const c3 = FakeCtx.last; c3.state = "suspended";
t.feed([255, 1, 0, 2, ...u32(2), 1, 2]);
check("data is dropped while the browser blocks audio", c3.scheduled.length === 0);

console.log(failed ? "\n" + failed + " FAILED" : "\nALL PASSED");
process.exit(failed ? 1 : 0);
