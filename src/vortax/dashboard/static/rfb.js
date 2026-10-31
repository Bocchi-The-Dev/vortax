"use strict";
/* Minimal RFB (VNC) client for the Vortax console. The server proxy authenticates to QEMU itself and
   offers us "no security", so there is no password handling here. Encodings: Raw, CopyRect, DesktopSize. */

class RFB {
  constructor(canvas, url, protocols, handlers) {
    this.canvas = canvas; this.ctx = canvas.getContext("2d"); this.h = handlers || {};
    this.buf = new Uint8Array(0); this.state = "version"; this.pending = null; this.buttons = 0;
    this.ws = new WebSocket(url, protocols); this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (e) => { this._push(new Uint8Array(e.data)); };
    this.ws.onclose = () => { this.state = "closed"; if (this.h.onclose) this.h.onclose(); };
    this.ws.onerror = () => { if (this.h.onerror) this.h.onerror(); };
  }
  close() { try { this.ws.close(); } catch (e) { /* already closed */ } }
  _send(bytes) { if (this.ws.readyState === 1) this.ws.send(bytes); }
  _push(chunk) {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf); merged.set(chunk, this.buf.length); this.buf = merged;
    try { while (this._step()) { /* keep consuming */ } } catch (e) { if (this.h.onerror) this.h.onerror(e); this.close(); }
  }
  _take(n) { const out = this.buf.subarray(0, n); this.buf = this.buf.subarray(n); return out; }
  _step() {
    const b = this.buf;
    switch (this.state) {
      case "version": {
        if (b.length < 12) return false;
        this._take(12); this._send(new TextEncoder().encode("RFB 003.008\n")); this.state = "security"; return true;
      }
      case "security": {
        if (b.length < 2) return false;
        const n = b[0]; if (n === 0) throw new Error("server refused");
        if (b.length < 1 + n) return false;
        this._take(1 + n); this._send(new Uint8Array([1])); this.state = "secresult"; return true;
      }
      case "secresult": {
        if (b.length < 4) return false;
        this._take(4); this._send(new Uint8Array([1])); this.state = "serverinit"; return true;     // ClientInit: shared
      }
      case "serverinit": {
        if (b.length < 24) return false;
        const dv = new DataView(b.buffer, b.byteOffset, b.length), nameLen = dv.getUint32(20);
        if (b.length < 24 + nameLen) return false;
        this.w = dv.getUint16(0); this.hgt = dv.getUint16(2);
        this.name = new TextDecoder().decode(b.subarray(24, 24 + nameLen)); this._take(24 + nameLen);
        this._resize(this.w, this.hgt);
        // 32 bpp, true colour, little endian, bytes arrive as R,G,B,pad which is exactly RGBA order.
        const pf = new Uint8Array(20); pf[0] = 0; pf.set([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 0, 8, 16, 0, 0, 0], 4);
        this._send(pf);
        const enc = [0, 1, -223], msg = new DataView(new ArrayBuffer(4 + 4 * enc.length));
        msg.setUint8(0, 2); msg.setUint16(2, enc.length); enc.forEach((e, i) => msg.setInt32(4 + 4 * i, e));
        this._send(new Uint8Array(msg.buffer));
        this.state = "run"; this._request(false);
        if (this.h.onready) this.h.onready(this.name, this.w, this.hgt);
        return true;
      }
      case "run": return this._runMessage();
      default: return false;
    }
  }
  _resize(w, h) { this.w = w; this.hgt = h; this.canvas.width = w; this.canvas.height = h; this.img = this.ctx.createImageData(w, h); this.ctx.fillStyle = "#000"; this.ctx.fillRect(0, 0, w, h); }
  _request(incremental) {
    const m = new DataView(new ArrayBuffer(10)); m.setUint8(0, 3); m.setUint8(1, incremental ? 1 : 0);
    m.setUint16(6, this.w); m.setUint16(8, this.hgt); this._send(new Uint8Array(m.buffer));
  }
  _runMessage() {
    if (this.pending) return this._rects();
    const b = this.buf; if (b.length < 1) return false;
    const type = b[0];
    if (type === 0) { if (b.length < 4) return false; const n = (b[2] << 8) | b[3]; this._take(4); this.pending = { left: n }; return true; }
    if (type === 1) { if (b.length < 6) return false; const n = (b[4] << 8) | b[5]; const need = 6 + n * 6; if (b.length < need) return false; this._take(need); return true; }
    if (type === 2) { this._take(1); return true; }
    if (type === 3) { if (b.length < 8) return false; const n = new DataView(b.buffer, b.byteOffset).getUint32(4); if (b.length < 8 + n) return false; this._take(8 + n); return true; }
    throw new Error("unknown server message " + type);
  }
  _rects() {
    const p = this.pending;
    if (p.left === 0) { this.pending = null; this._request(true); return true; }
    const b = this.buf; if (b.length < 12) return false;
    const dv = new DataView(b.buffer, b.byteOffset, b.length);
    const x = dv.getUint16(0), y = dv.getUint16(2), w = dv.getUint16(4), h = dv.getUint16(6), enc = dv.getInt32(8);
    if (enc === 0) {
      const need = 12 + w * h * 4; if (b.length < need) return false;
      const px = b.subarray(12, need), out = new ImageData(w, h);
      for (let i = 0; i < w * h; i++) { out.data[i * 4] = px[i * 4]; out.data[i * 4 + 1] = px[i * 4 + 1]; out.data[i * 4 + 2] = px[i * 4 + 2]; out.data[i * 4 + 3] = 255; }
      this.ctx.putImageData(out, x, y); this._take(need);
    } else if (enc === 1) {
      if (b.length < 16) return false;
      const sx = dv.getUint16(12), sy = dv.getUint16(14); this.ctx.drawImage(this.canvas, sx, sy, w, h, x, y, w, h); this._take(16);
    } else if (enc === -223) {
      this._take(12); this._resize(w, h); this._request(false);
    } else throw new Error("unsupported encoding " + enc);
    p.left--; return true;
  }
  pointer(x, y, buttons) {
    if (this.state !== "run") return;
    x = Math.max(0, Math.min(this.w - 1, Math.round(x))); y = Math.max(0, Math.min(this.hgt - 1, Math.round(y))); this.buttons = buttons;
    const m = new DataView(new ArrayBuffer(6)); m.setUint8(0, 5); m.setUint8(1, buttons); m.setUint16(2, x); m.setUint16(4, y); this._send(new Uint8Array(m.buffer));
  }
  key(keysym, down) {
    if (this.state !== "run" || !keysym) return;
    const m = new DataView(new ArrayBuffer(8)); m.setUint8(0, 4); m.setUint8(1, down ? 1 : 0); m.setUint32(4, keysym >>> 0); this._send(new Uint8Array(m.buffer));
  }
  tap(keysym) { this.key(keysym, true); this.key(keysym, false); }
  type(text) { for (const ch of text) { const k = RFB.keysymFor(ch); if (k) { if (ch === "\n") this.tap(0xff0d); else this.tap(k); } } }
  combo(keysyms) { keysyms.forEach((k) => this.key(k, true)); keysyms.slice().reverse().forEach((k) => this.key(k, false)); }
  static keysymFor(key) {
    const named = { Backspace: 0xff08, Tab: 0xff09, Enter: 0xff0d, Escape: 0xff1b, Delete: 0xffff, Home: 0xff50, ArrowLeft: 0xff51, ArrowUp: 0xff52,
      ArrowRight: 0xff53, ArrowDown: 0xff54, PageUp: 0xff55, PageDown: 0xff56, End: 0xff57, Insert: 0xff63, Shift: 0xffe1, Control: 0xffe3, Alt: 0xffe9, Meta: 0xffeb,
      CapsLock: 0xffe5, " ": 0x20, "\n": 0xff0d };
    if (named[key]) return named[key];
    const f = /^F(\d{1,2})$/.exec(key); if (f && +f[1] >= 1 && +f[1] <= 12) return 0xffbe + (+f[1] - 1);
    const cp = [...key]; if (cp.length !== 1) return 0;
    const c = cp[0].codePointAt(0); return c < 0x100 ? c : 0x01000000 + c;
  }
}
window.RFB = RFB;
