"use strict";
/* Player for QEMU's VNC audio extension (raw PCM). Small jitter buffer, bounded latency.
   Browsers keep audio locked until a tap, so start() must run from a click handler. */

class QemuAudio {
  constructor(opts) {
    opts = opts || {};
    this.freq = opts.frequency || 22050; this.channels = opts.channels === 2 ? 2 : 1; this.latency = opts.latency || 0.12;
    this.ctx = null; this.gain = null; this.next = 0; this.rest = new Uint8Array(0); this.vol = 1;
  }
  start() {
    if (this.ctx) { this.ctx.resume(); return true; }
    const C = window.AudioContext || window.webkitAudioContext; if (!C) return false;
    try { this.ctx = new C({ sampleRate: this.freq }); } catch (e) { return false; }
    this.gain = this.ctx.createGain(); this.gain.gain.value = this.vol; this.gain.connect(this.ctx.destination);
    this.ctx.resume(); return true;
  }
  volume(v) { this.vol = Math.max(0, Math.min(1, v)); if (this.gain) this.gain.gain.value = this.vol; }
  reset() { this.next = 0; this.rest = new Uint8Array(0); }
  push(bytes) {
    const ctx = this.ctx; if (!ctx || ctx.state !== "running" || !bytes.length) return;
    let data = bytes;
    if (this.rest.length) { data = new Uint8Array(this.rest.length + bytes.length); data.set(this.rest); data.set(bytes, this.rest.length); }
    const size = 2 * this.channels, frames = Math.floor(data.length / size), used = frames * size;
    this.rest = data.slice(used); if (!frames) return;
    const now = ctx.currentTime;
    if (this.next < now + 0.005) this.next = now + this.latency; else if (this.next - now > 0.6) return;
    const buf = ctx.createBuffer(this.channels, frames, this.freq), dv = new DataView(data.buffer, data.byteOffset, used);
    for (let c = 0; c < this.channels; c++) {
      const out = buf.getChannelData(c);
      for (let i = 0; i < frames; i++) out[i] = dv.getInt16((i * this.channels + c) * 2, true) / 32768;
    }
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(this.gain); src.start(this.next); this.next += buf.duration;
  }
  stop() { if (this.ctx) { this.ctx.close().catch(() => {}); } this.ctx = null; this.gain = null; this.reset(); }
}
window.QemuAudio = QemuAudio;
