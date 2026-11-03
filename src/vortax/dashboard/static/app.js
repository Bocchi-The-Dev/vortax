"use strict";
/* Vortax dashboard. No framework, no CDN, no innerHTML with data: every value goes through textContent. */

const $ = (sel) => document.querySelector(sel);
const ICONS = {
  home: "M3 11l9-8 9 8M5 10v10h14V10",
  launch: "M5 3l14 9-14 9z",
  access: "M4 6h16v10H4zM8 20h8M12 16v4",
  disk: "M4 7c0-2 16-2 16 0v10c0 2-16 2-16 0zM4 12c0 2 16 2 16 0",
  logs: "M5 4h14v16H5zM9 9h6M9 13h6M9 17h3",
  health: "M3 12h4l3-8 4 16 3-8h4",
  console: "M3 5h18v12H3zM8 21h8M12 17v4M7 10l3 2-3 2M12 14h4",
  settings: "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
};
const TABS = [["overview", "Overview", "home"], ["launch", "Systems", "launch"], ["console", "Console", "console"], ["access", "Access", "access"],
              ["storage", "Storage", "disk"], ["logs", "Logs", "logs"], ["settings", "Settings", "settings"], ["health", "Health", "health"]];

/* ---------- preferences: theme, language, notifications (per browser, optional) ---------- */
const store = {
  get(k, d) { try { return localStorage.getItem("vortax-" + k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("vortax-" + k, v); } catch (e) { /* private mode */ } },
};
const AR = {
  "Overview": "نظرة عامة", "Systems": "الأنظمة", "Console": "الشاشة", "Access": "الوصول", "Storage": "التخزين", "Logs": "السجلات",
  "Settings": "الإعدادات", "Health": "الفحص", "Stop": "إيقاف", "Reboot": "إعادة تشغيل", "Kill": "إنهاء فوري", "Connect": "اتصال",
  "Start": "تشغيل", "Boot from disk": "التشغيل من القرص", "Cancel": "إلغاء", "Yes": "نعم", "Close": "إغلاق", "Delete": "حذف", "Save": "حفظ",
  "Load": "استرجاع", "Show": "إظهار", "Refresh": "تحديث", "running": "يعمل", "stopped": "متوقف", "Are you sure?": "هل أنت متأكد؟",
  "No virtual machines yet.": "لا توجد أجهزة افتراضية بعد.", "Pick a system": "اختر نظامًا", "Nothing matches.": "لا شيء مطابق.",
  "VM name": "اسم الجهاز", "Boot mode": "وضع الإقلاع", "Hardware profile": "ملف العتاد", "Firmware": "البرنامج الثابت", "vCPUs": "عدد المعالجات",
  "Tunnel": "النفق", "Working...": "جارٍ العمل...", "Done": "تم", "Failed": "فشل", "Theme": "المظهر", "Language": "اللغة",
  "Notifications": "الإشعارات", "Dark": "داكن", "Light": "فاتح", "Save settings": "حفظ الإعدادات", "Reset to default": "إعادة الافتراضي",
  "Connect to a running VM": "اتصل بجهاز يعمل", "Ctrl+Alt+Del": "Ctrl+Alt+Del", "Keyboard": "لوحة المفاتيح", "Screenshot": "لقطة شاشة",
  "Disconnect": "قطع الاتصال", "Type text": "اكتب نصًا", "Fullscreen": "ملء الشاشة", "Search": "بحث", "search": "بحث",
  "Start a VM to see how to connect to it.": "شغّل جهازًا لترى طريقة الاتصال به.", "Nothing stored yet.": "لا شيء مخزّن بعد.",
  "Source code (AGPL-3.0)": "الشيفرة المصدرية (AGPL-3.0)", "Presets": "القوالب", "Save as preset": "حفظ كقالب", "Run": "تشغيل",
  "Default settings for new VMs": "الإعدادات الافتراضية للأجهزة الجديدة", "RAM (MB, 0 = auto)": "الذاكرة (ميغابايت، 0 = تلقائي)",
  "Runtime (min, 0 = none)": "مدة التشغيل (دقيقة، 0 = بلا حد)", "Enable": "تفعيل", "Not supported by this browser": "غير مدعوم في هذا المتصفح",
  "Blocked in browser settings": "محظور من إعدادات المتصفح", "Saved": "تم الحفظ", "Source URL or path": "رابط المصدر أو المسار",
  "VNC password:": "كلمة مرور VNC:", "New random": "عشوائية جديدة", "Set...": "تعيين...", "Snapshots": "اللقطات", "Reset": "إعادة ضبط",
  "This browser": "هذا المتصفح", "total": "المجموع", "ram mb": "الذاكرة (ميغابايت)", "vcpus": "المعالجات", "disk gb": "القرص (غيغابايت)",
  "tunnel": "النفق", "accel": "التسريع", "firmware": "البرنامج الثابت", "nic": "بطاقة الشبكة", "audio": "الصوت", "access": "الوصول",
  "runtime": "مدة التشغيل (دقيقة)", "ssh": "SSH", "snapshot on stop": "حفظ الحالة عند الإيقاف", "notify": "الإشعارات", "Sound": "الصوت",
  "SSH forward": "تحويل SSH", "Resume where you left off": "المتابعة من حيث توقفت", "Notify on start/stop": "إشعار عند التشغيل/الإيقاف",
  "Save as preset (optional)": "حفظ كقالب (اختياري)", "Nothing changed": "لم يتغير شيء", "Logs": "السجلات", "Log": "السجل",
  "Your own image": "صورتك الخاصة", "Run a custom source": "تشغيل مصدر مخصص", "Open in browser (noVNC)": "فتح في المتصفح (noVNC)",
  "Live edit": "تعديل مباشر", "Trusted devices": "الأجهزة الموثوقة", "Remove": "إزالة", "Sign in": "تسجيل الدخول", "Password": "كلمة المرور",
  "New one-time link": "رابط جديد لمرة واحدة", "Copy": "نسخ", "Attach": "إرفاق", "Eject": "إخراج", "Add": "إضافة", "Sound card": "كارت الصوت",
};
const lang = () => store.get("lang", "en");
const T = (s) => (lang() === "ar" && AR[s]) || s;
function applyPrefs() {
  const theme = store.get("theme", "dark"), root = document.documentElement;
  root.setAttribute("data-theme", theme === "light" ? "light" : "dark");
  root.lang = lang(); root.dir = lang() === "ar" ? "rtl" : "ltr";
}
function translateTree(root) {
  if (lang() !== "ar") return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) { const t = n.nodeValue.trim(); if (t && AR[t]) n.nodeValue = n.nodeValue.replace(t, AR[t]); }
  root.querySelectorAll("[placeholder],[aria-label]").forEach((el) => { for (const a of ["placeholder", "aria-label"]) { const v = el.getAttribute(a); if (v && AR[v]) el.setAttribute(a, AR[v]); } });
}
function notifyOk() { return store.get("notify", "0") === "1" && "Notification" in window && Notification.permission === "granted"; }
function notifyUser(title, body) { if (notifyOk()) { try { new Notification(title, { body, icon: "icon.svg" }); } catch (e) { /* some mobile browsers need a service worker */ } } }
let lastRunning = null;

let token = "", view = "overview", data = null, catalog = null, timer = null, logName = "", logSource = "qemu";
let family = "all", search = "";

/* ---------- tiny DOM helper ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function icon(name) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const p = document.createElementNS(ns, "path"); p.setAttribute("d", ICONS[name]); svg.append(p);
  return svg;
}
const fmtBytes = (n) => { n = Number(n) || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + " " + u[i]; };

/* ---------- api ---------- */
async function api(path, body) {
  const opts = { headers: { "X-Vortax-Token": token } };
  if (body !== undefined) { opts.method = "POST"; opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch("/api/" + path, opts); } catch (e) { setBanner("Cannot reach the dashboard server.", true); throw e; }
  const json = await res.json().catch(() => ({}));
  if (res.status === 401) { forgetDevice(); showGate("This browser is not trusted (any more)."); throw new Error("auth"); }
  if (!res.ok) throw new Error(json.error || res.statusText);
  setBanner("");
  return json;
}
function setBanner(msg, err) { const b = $("#banner"); b.hidden = !msg; b.textContent = msg || ""; b.className = "banner" + (err ? " err" : ""); }
let toastTimer;
function toast(msg, err) { const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500); }
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { if (e.message !== "auth") toast(e.message, true); } }

/* ---------- modal ---------- */
function openModal(...kids) { const s = $("#sheet"); s.replaceChildren(...kids); $("#modal").hidden = false; translateTree(s); }
function closeModal() { $("#modal").hidden = true; $("#sheet").replaceChildren(); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ---------- shared pieces ---------- */
function bar(pct, cls) { const i = h("i"); i.style.width = Math.max(0, Math.min(100, pct)) + "%"; return h("div", { class: "bar " + (cls || "") }, i); }
function stat(label, big, pct, left, right, cls) {
  return h("div", { class: "card stat" }, h("h2", { text: label }), h("div", { class: "big", text: big }), bar(pct, cls),
    h("div", { class: "meta" }, h("span", { text: left }), h("span", { text: right })));
}
function kv(label, value, cls) { return h("div", {}, h("span", { text: label }), h("b", { class: cls || "", text: value || "-" })); }

/* ---------- views ---------- */
function renderOverview() {
  const s = data.stats, d = s.disk, root = h("div");
  const diskPct = d.total ? (100 * d.used / d.total) : 0;
  root.append(h("div", { class: "grid" },
    stat("CPU", s.cpu_pct + "%", s.cpu_pct, s.cpus + " cores", s.load.length ? "load " + s.load[0].toFixed(2) : "", "green"),
    stat("Memory", fmtBytes(s.ram_used), s.ram_pct, "of " + fmtBytes(s.ram_total), s.ram_pct + "%"),
    stat("Disk", fmtBytes(d.free) + " free", diskPct, "of " + fmtBytes(d.total), Math.round(diskPct) + "%", diskPct > 90 ? "amber" : "")));
  root.append(...data.jobs.filter((j) => j.status === "running" || j.status === "failed").map(jobCard));
  if (!data.instances.length) {
    root.append(h("div", { class: "card empty" }, h("p", { text: "No virtual machines yet." }),
      h("button", { class: "btn primary", text: "Pick a system", onclick: () => go("launch") })));
  }
  for (const vm of data.instances) root.append(vmCard(vm));
  return root;
}

function vmCard(vm) {
  const run = vm.running;
  const card = h("div", { class: "card vm" });
  card.append(h("div", { class: "vm-head" }, h("h3", { text: vm.name }),
    h("span", { class: "pill " + (run ? "on" : "off"), text: run ? "running" : "stopped" }),
    h("span", { class: "sub", text: [vm.system, vm.profile && "profile " + vm.profile, vm.arch].filter(Boolean).join(" · ") })));
  const cells = [kv("Disk", vm.disk ? fmtBytes(vm.disk_size) + " " + (vm.disk_format || "") : "none")];
  if (run) {
    cells.push(kv("Acceleration", vm.accel), kv("Memory", `${vm.ram_mb} MB (using ${fmtBytes(vm.rss)})`),
      kv("CPU", `${vm.vcpus} vCPU · ${vm.cpu_pct}%`), kv("VNC port", String(vm.vnc.port || "")));
    if (vm.deadline) cells.push(kv("Stops at", new Date(vm.deadline).toLocaleTimeString()));
  }
  card.append(h("div", { class: "kv" }, cells));
  const row = h("div", { class: "row" });
  if (run) {
    row.append(h("button", { class: "btn warn", text: "Stop", onclick: () => confirmThen(`Shut down ${vm.name}? The guest gets a power-button press.`, () => vmAct(vm.name, "stop")) }),
      h("button", { class: "btn good", text: "Reboot", onclick: () => confirmThen(`Reset ${vm.name}? Unsaved work in the guest is lost.`, () => vmAct(vm.name, "reboot")) }),
      h("button", { class: "btn bad", text: "Kill", onclick: () => confirmThen(`Kill ${vm.name} immediately? This is like pulling the plug.`, () => vmAct(vm.name, "kill")) }),
      h("button", { class: "btn", text: "Connect", onclick: () => { view = "access"; render(); } }),
      h("button", { class: "btn", text: "Live edit", onclick: () => liveEdit(vm.name) }));
  } else if (vm.disk) {
    row.append(h("button", { class: "btn primary", text: "Start", onclick: () => vmAct(vm.name, "start") }),
      h("button", { class: "btn", text: "Boot from disk", onclick: () => bootFromDisk(vm.name) }),
      h("button", { class: "btn", text: "Edit", onclick: () => editVm(vm.name) }));
  }
  card.append(row);
  return card;
}

/* Change the saved settings of an existing VM (RAM, CPUs, sound...). Its disk is not touched. */
async function editVm(name) {
  const r = await act(() => api("vm/edit", { name }));
  if (!r) return;
  const x = r.values, o = r.options, num = (id, val, min) => h("input", { id, type: "number", min, value: String(val) });
  const f = h("div", { class: "form" },
    field("RAM (MB, 0 = auto)", num("e_ram", x.ram_mb, "0")),
    field("vCPUs", num("e_cpu", x.vcpus, "1")),
    field("Sound", select("e_audio", o.audio, x.audio)),
    field("Network card", select("e_nic", o.nic, x.nic)),
    field("Acceleration", select("e_accel", o.accel, x.accel)),
    field("Several vCPUs on several cores (experimental)", select("e_mt", ["off", "on"], x.mttcg ? "on" : "off")));
  openModal(h("h3", { text: "Edit " + name }),
    h("p", { class: "sub", text: "Takes effect the next time the VM starts. The disk and your installed system are not touched." }), f,
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Save", onclick: async () => {
      const v = (id) => $("#" + id).value;
      const changes = { ram_mb: Number(v("e_ram")), vcpus: Number(v("e_cpu")), audio: v("e_audio"), nic: v("e_nic"), accel: v("e_accel"), mttcg: v("e_mt") === "on" };
      const done = await act(() => api("vm/edit", { name, changes }), "Saved. It applies the next time the VM starts.");
      if (done) closeModal();
    } }), h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
}

/* Change a running VM without restarting it: swap a CD, plug in or pull out a sound card. */
function liveEdit(name) {
  const send = async (action, value) => {
    const r = await act(() => api("vm/live", { name, action, value }));
    if (r) toast(r.message);
  };
  const iso = h("input", { id: "l_iso", placeholder: "/data/data/com.termux/files/home/drivers.iso", autocapitalize: "off", spellcheck: "false" });
  const card = select("l_card", ["ac97", "es1370", "hda"], "ac97");
  openModal(h("h3", { text: "Live edit · " + name }),
    h("p", { class: "sub", text: "Applied immediately, no restart. Windows XP may need Device Manager > Action > Scan for hardware changes." }),
    h("h2", { class: "gap", text: "CD" }), field("ISO path on this machine", iso),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Attach", onclick: () => send("cd_attach", iso.value.trim()) }),
      h("button", { class: "btn", text: "Eject", onclick: () => send("cd_eject", "") })),
    h("h2", { class: "gap", text: "Sound card" }), field("Card", card),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Add", onclick: () => send("audio_add", card.value) }),
      h("button", { class: "btn", text: "Remove", onclick: () => send("audio_remove", "") })),
    h("div", { class: "row end" }, h("button", { class: "btn", text: "Close", onclick: closeModal })));
}

function bootFromDisk(name) {
  confirmThen("Start without the installer ISO? Use this once the OS is installed.", async () => {
    const j = await act(() => api("run", { name, boot: "disk" }));
    if (j) trackJob(j.job);
  });
}

async function vmAct(name, action) {
  const r = await act(() => api("vm", { name, action }));
  if (r && r.job) trackJob(r.job); else if (r) { toast(action + " sent"); refresh(); }
}

function jobCard(j) {
  const c = h("div", { class: "card" }, h("h2", { text: `${j.kind} · ${j.name} · ${j.status}` }));
  if (j.status === "running" && j.phase === "download") c.append(h("div", { class: "note", text: "Downloaded " + fmtBytes(j.downloaded || 0) }));
  if (j.error) c.append(h("div", { class: "fail", text: j.error }));
  c.append(h("pre", { class: "log", text: j.log.join("\n") || "waiting..." }));
  return c;
}

/* --- confirm --- */
function confirmThen(msg, fn) {
  openModal(h("h3", { text: "Are you sure?" }), h("p", { class: "sub", text: msg }),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Yes", onclick: () => { closeModal(); fn(); } }),
      h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
}

/* --- jobs --- */
async function trackJob(id) {
  openModal(h("h3", { text: "Working..." }), h("pre", { class: "log", id: "joblog", text: "starting" }), h("div", { class: "row", id: "jobbtns" }));
  for (;;) {
    let j;
    try { j = await api("job?id=" + encodeURIComponent(id)); } catch (e) { return; }
    const sheet = $("#sheet"); if (!sheet || !$("#joblog")) return;      // user closed the modal
    sheet.querySelector("h3").textContent = j.status === "running" ? `Working... (${j.phase}, ${j.elapsed}s${j.downloaded ? ", " + fmtBytes(j.downloaded) : ""})`
      : j.status === "done" ? "Done" : "Failed";
    $("#joblog").textContent = (j.error ? "ERROR: " + j.error + "\n\n" : "") + j.log.join("\n");
    if (j.status !== "running") {
      $("#jobbtns").replaceChildren(h("button", { class: "btn primary", text: "Close", onclick: () => { closeModal(); refresh(); } }));
      refresh(); return;
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
}

/* --- systems / launch --- */
async function renderLaunch() {
  if (!catalog) catalog = await api("systems");
  const root = h("div");
  const fams = ["all", ...new Set(catalog.systems.map((s) => s.family))];
  root.append(h("div", { class: "filters" }, fams.map((f) => h("button", { class: "chip", "aria-pressed": String(f === family), text: f, onclick: () => { family = f; render(); } })),
    h("input", { type: "search", placeholder: "search", value: search, "aria-label": "Search systems", style: false, oninput: (e) => { search = e.target.value; filterCat(); } })));
  const grid = h("div", { class: "cat", id: "cat" });
  root.append(grid, h("div", { class: "card", style: false }, h("h2", { text: "Your own image" }),
    h("p", { class: "sub", text: "Any ISO, disk image or archive, from a URL or a path on this machine." }),
    h("button", { class: "btn primary", text: "Run a custom source", onclick: () => launchForm(null) })));
  fillCat(grid);
  return root;
}
function fillCat(grid) {
  grid.replaceChildren();
  const q = search.trim().toLowerCase();
  for (const s of catalog.systems) {
    if (family !== "all" && s.family !== family) continue;
    if (q && !(s.id + " " + s.name).toLowerCase().includes(q)) continue;
    const byo = !s.modes.length;
    grid.append(h("button", { class: "sys", onclick: () => launchForm(s) }, h("b", { text: s.name }),
      s.modes.map((m) => h("span", { class: "tag", text: m })), byo ? h("span", { class: "tag byo", text: "bring your own" }) : null,
      h("small", { text: s.disk_gb ? ` ${s.disk_gb} GB disk` : "" })));
  }
  if (!grid.children.length) grid.append(h("div", { class: "empty", text: "Nothing matches." }));
}
function filterCat() { fillCat($("#cat")); }

function field(label, input, wide) { return h("div", { class: wide ? "wide" : "" }, h("label", { text: label }), input); }
function select(id, opts, value) { const s = h("select", { id }); for (const o of opts) s.append(h("option", { value: o, text: o || "(default)", selected: o === value })); return s; }

function launchForm(sys) {
  const modes = sys ? sys.modes : [];
  const f = h("div", { class: "form" },
    field("VM name", h("input", { id: "f_name", value: sys ? sys.id.replace(/[^A-Za-z0-9._-]/g, "-") : "vm1" })),
    sys && modes.length ? field("Boot mode", select("f_mode", modes, modes[0])) : null,
    !sys || !modes.length ? field("Source URL or path", h("input", { id: "f_source", placeholder: "https://... or /path/file.iso" }), true) : null,
    sys && sys.profile ? null : field("Hardware profile", select("f_profile", ["", ...Object.keys(catalog.profiles)], "")),
    field("Firmware", select("f_fw", ["bios", "uefi"], "bios")),
    field("RAM (MB, 0 = auto)", h("input", { id: "f_ram", type: "number", min: "0", value: "0" })),
    field("vCPUs", h("input", { id: "f_cpu", type: "number", min: "1", value: "2" })),
    field("Tunnel", select("f_tunnel", catalog.options.tunnels, "none")),
    field("Runtime (min, 0 = none)", h("input", { id: "f_rt", type: "number", min: "0", value: "0" })),
    field("Sound", select("f_audio", ["none", "hda", "ac97", "sb16", "es1370"], "none")),
    field("SSH forward", select("f_ssh", ["off", "on"], "off")),
    field("Resume where you left off", select("f_snap", ["off", "on"], "off")),
    field("Notify on start/stop", select("f_notify", ["off", "on"], "off")),
    field("Save as preset (optional)", h("input", { id: "f_preset", placeholder: "my-setup", maxlength: "40" })));
  openModal(h("h3", { text: sys ? sys.name : "Custom source" }),
    h("p", { class: "sub", text: sys && sys.notes ? sys.notes : "Downloads, prepares and starts the VM." }), f,
    h("p", { class: "note", text: "VNC listens on this machine only and gets a random password. Use the Access tab to see it." }),
    h("div", { class: "row", style: false }, h("button", { class: "btn primary", text: "Start", onclick: () => submitLaunch(sys) }),
      h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
}
async function submitLaunch(sys) {
  const v = (id) => { const e = $("#" + id); return e ? e.value : ""; };
  const body = { name: v("f_name"), firmware: v("f_fw"), ram_mb: Number(v("f_ram")), vcpus: Number(v("f_cpu")), tunnel: v("f_tunnel"), runtime: Number(v("f_rt")) };
  if (sys) { body.system = sys.id; if (v("f_mode")) body.boot_mode = v("f_mode"); } else body.source = v("f_source");
  if (v("f_profile")) body.profile = v("f_profile");
  if (v("f_audio") && v("f_audio") !== "none") body.audio = v("f_audio");
  if (v("f_ssh") === "on") body.ssh = true;
  if (v("f_snap") === "on") body.snapshot_on_stop = true;
  if (v("f_notify") === "on") body.notify = true;
  if (v("f_preset")) body.save_preset = v("f_preset");
  const r = await act(() => api("run", body));
  if (r) trackJob(r.job);
}

/* --- access --- */
function renderAccess() {
  const running = data.instances.filter((v) => v.running), root = h("div");
  if (!running.length) return h("div", { class: "card empty", text: "Start a VM to see how to connect to it." });
  for (const vm of running) {
    const card = h("div", { class: "card vm" }, h("div", { class: "vm-head" }, h("h3", { text: vm.name }), h("span", { class: "pill on", text: "running" })));
    const cells = [h("div", {}, h("span", { text: "VNC on this machine" }), h("b", { class: "addr", text: `${vm.vnc.bind}:${vm.vnc.port}` }))];
    if (vm.tunnel && vm.tunnel.address) cells.push(h("div", {}, h("span", { text: `${vm.tunnel.kind} tunnel` }), h("b", { class: "addr", text: vm.tunnel.address })));
    card.append(h("div", { class: "kv" }, cells));
    const pw = h("code", { class: "addr", text: vm.vnc.password ? "hidden" : "no password (loopback only)" });
    const row = h("div", { class: "row" }, h("span", { class: "sub", text: "VNC password:" }), pw);
    if (vm.vnc.password) {
      row.append(h("button", { class: "btn sm", text: "Show", onclick: async (e) => { const r = await act(() => api("password?name=" + encodeURIComponent(vm.name))); if (r) { pw.textContent = r.password; e.target.disabled = true; } } }),
        h("button", { class: "btn sm", text: "New random", onclick: () => confirmThen("Generate a new password? Connected viewers keep working until they disconnect.", async () => { const r = await act(() => api("password/set", { name: vm.name, generate: true }), "Password changed"); if (r) pw.textContent = r.password; }) }),
        h("button", { class: "btn sm", text: "Set...", onclick: () => passwordForm(vm.name, pw) }));
    }
    card.append(row);
    const t = h("div", { class: "row" }, h("span", { class: "sub", text: "Tunnel:" }));
    for (const kind of catalog ? catalog.options.tunnels.filter((k) => k !== "none") : ["bore", "ngrok", "tailscale"])
      t.append(h("button", { class: "btn sm", text: kind, onclick: async () => { const r = await act(() => api("tunnel", { name: vm.name, kind })); if (r && r.job) trackJob(r.job); } }));
    if (vm.tunnel && vm.tunnel.address) t.append(h("button", { class: "btn sm bad", text: "close tunnel", onclick: async () => { await act(() => api("tunnel", { name: vm.name, kind: "stop" }), "Tunnel closed"); refresh(); } }));
    card.append(t);
    card.append(h("div", { class: "row" }, h("button", { class: "btn", text: "Open in browser (noVNC)", onclick: async () => {
      const r = await act(() => api("webvnc", { name: vm.name, action: "start" }));
      if (r && r.port) { const url = `${location.protocol}//${location.hostname}:${r.port}` + r.path.replace("{host}", location.hostname); window.open(url, "_blank", "noopener"); }
    } }), h("button", { class: "btn", text: "Snapshots", onclick: () => snapshots(vm.name) })));
    card.append(h("p", { class: "note", text: "Point any VNC viewer at the address above. The password is checked by QEMU; only its first 8 characters count." }));
    root.append(card);
  }
  return root;
}
function passwordForm(name, out) {
  const input = h("input", { type: "password", maxlength: "8", id: "f_pw", autocomplete: "new-password" });
  openModal(h("h3", { text: "Set VNC password" }), h("p", { class: "sub", text: "Up to 8 characters (a VNC protocol limit)." }), field("New password", input),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Save", onclick: async () => { const r = await act(() => api("password/set", { name, password: input.value }), "Password changed"); if (r) { out.textContent = "hidden"; closeModal(); } } }), h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
}
async function snapshots(name) {
  const out = h("pre", { class: "log", text: "loading..." }), tag = h("input", { placeholder: "snapshot name", id: "f_tag" });
  const reload = async () => { const r = await act(() => api("snapshot", { name, action: "list" })); out.textContent = r ? (r.text || "no snapshots") : "unavailable"; };
  const call = (action) => async () => { const r = await act(() => api("snapshot", { name, action, tag: tag.value }), "Done"); if (r) reload(); };
  openModal(h("h3", { text: "Snapshots · " + name }), out, h("div", { class: "form", style: false }, field("Name", tag)),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Save", onclick: call("save") }), h("button", { class: "btn", text: "Load", onclick: call("load") }),
      h("button", { class: "btn bad", text: "Delete", onclick: call("delete") }), h("button", { class: "btn", text: "Close", onclick: closeModal })));
  reload();
}

/* --- storage --- */
async function renderStorage() {
  const s = await api("storage"), root = h("div", { class: "card" });
  root.append(h("h2", { text: s.data_dir }));
  if (!s.items.length) { root.append(h("div", { class: "empty", text: "Nothing stored yet." })); return root; }
  const tb = h("tbody");
  for (const it of s.items) {
    tb.append(h("tr", {}, h("td", { text: it.kind }), h("td", { class: "mono", text: it.name }), h("td", { class: "num", text: fmtBytes(it.size) }),
      h("td", {}, h("button", { class: "btn sm bad", text: "Delete", onclick: () => confirmThen(`Delete ${it.name}? This cannot be undone.`, async () => { await act(() => api("storage/delete", { kind: it.kind, name: it.name }), "Deleted"); render(); }) }))));
  }
  root.append(h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Type" }), h("th", { text: "File" }), h("th", { text: "Size" }), h("th"))), tb),
    h("p", { class: "note", text: `${fmtBytes(s.stats.free)} free of ${fmtBytes(s.stats.total)}` }));
  return root;
}

/* --- logs --- */
async function renderLogs() {
  const names = data.instances.map((v) => v.name);
  if (!names.length) return h("div", { class: "card empty", text: "No logs yet." });
  if (!names.includes(logName)) logName = names[0];
  const pre = h("pre", { class: "log", text: "loading..." });
  const load = async () => { const r = await act(() => api(`logs?name=${encodeURIComponent(logName)}&source=${logSource}&lines=200`)); pre.textContent = r ? (r.text || "(empty)") : ""; pre.scrollTop = pre.scrollHeight; };
  const sel = select("f_log", names, logName); sel.addEventListener("change", () => { logName = sel.value; load(); });
  const src = select("f_src", ["qemu", "tunnel"], logSource); src.addEventListener("change", () => { logSource = src.value; load(); });
  const root = h("div", { class: "card" }, h("div", { class: "form" }, field("VM", sel), field("Log", src)), pre,
    h("div", { class: "row" }, h("button", { class: "btn sm", text: "Refresh", onclick: load })));
  load(); return root;
}


/* --- console (VNC in the browser, through the dashboard's own WebSocket proxy) --- */
let rfb = null, consoleName = "";
function stopConsole() { if (rfb) { rfb.close(); rfb = null; } }
const MODS = { ctrl: 0xffe3, alt: 0xffe9, meta: 0xffeb };
function renderConsole() {
  const running = data.instances.filter((v) => v.running), root = h("div");
  if (!running.length) { stopConsole(); return h("div", { class: "card empty", text: "Start a VM to see how to connect to it." }); }
  if (!running.some((v) => v.name === consoleName)) consoleName = running[0].name;
  const status = h("span", { class: "sub", text: "" }), canvas = h("canvas", { id: "screen", tabindex: "0" });
  const typer = h("input", { id: "typer", class: "typer", autocapitalize: "off", autocomplete: "off", autocorrect: "off", spellcheck: "false", "aria-label": "Type text" });
  const sel = select("f_con", running.map((v) => v.name), consoleName);
  const sticky = new Set();
  const modBtn = (label, sym) => h("button", { class: "btn sm", "aria-pressed": "false", text: label, onclick: (e) => {
    const on = !sticky.has(sym); on ? sticky.add(sym) : sticky.delete(sym); if (rfb) rfb.key(sym, on); e.target.setAttribute("aria-pressed", String(on)); } });
  const releaseMods = () => { if (!rfb) return; for (const s of sticky) rfb.key(s, false); sticky.clear(); root.querySelectorAll("[aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", "false")); };
  const connect = () => {
    stopConsole(); status.textContent = "connecting...";
    rfb = new RFB(canvas, `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/vnc?name=${encodeURIComponent(sel.value)}`, ["vortax.v1", "tok." + token], {
      onready: (name, w, hh) => { status.textContent = `${name || sel.value} · ${w}×${hh}`; canvas.focus(); },
      onclose: () => { status.textContent = "disconnected"; }, onerror: () => { status.textContent = "connection failed"; } });
  };
  sel.addEventListener("change", () => { consoleName = sel.value; connect(); });
  const pos = (ev) => { const r = canvas.getBoundingClientRect(); return [(ev.clientX - r.left) * canvas.width / r.width, (ev.clientY - r.top) * canvas.height / r.height]; };
  const mask = (ev) => (ev.buttons & 1 ? 1 : 0) | (ev.buttons & 4 ? 2 : 0) | (ev.buttons & 2 ? 4 : 0);
  canvas.addEventListener("pointermove", (ev) => { if (rfb && ev.pointerType === "mouse") rfb.pointer(...pos(ev), mask(ev)); });
  canvas.addEventListener("pointerdown", (ev) => { canvas.focus(); if (!rfb) return; canvas.setPointerCapture(ev.pointerId); const p = pos(ev); rfb.pointer(...p, ev.pointerType === "mouse" ? mask(ev) : 1); ev.preventDefault(); });
  canvas.addEventListener("pointerup", (ev) => { if (!rfb) return; rfb.pointer(...pos(ev), 0); releaseMods(); });
  canvas.addEventListener("pointercancel", () => { if (rfb) rfb.pointer(0, 0, 0); });
  canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());
  canvas.addEventListener("wheel", (ev) => { if (!rfb) return; const [x, y] = pos(ev), b = ev.deltaY < 0 ? 8 : 16; rfb.pointer(x, y, b); rfb.pointer(x, y, 0); ev.preventDefault(); }, { passive: false });
  // long-press on touch = right click
  let press = null;
  canvas.addEventListener("touchstart", (ev) => { if (ev.touches.length === 1 && rfb) { const t = ev.touches[0]; press = setTimeout(() => { const p = pos(t); rfb.pointer(...p, 4); rfb.pointer(...p, 0); press = null; }, 600); } });
  for (const n of ["touchend", "touchmove", "touchcancel"]) canvas.addEventListener(n, () => { if (press) { clearTimeout(press); press = null; } });
  const keyEvent = (down) => (ev) => {
    if (!rfb || ev.target === typer) return;
    const sym = RFB.keysymFor(ev.key); if (!sym) return;
    rfb.key(sym, down); ev.preventDefault();
  };
  canvas.addEventListener("keydown", keyEvent(true)); canvas.addEventListener("keyup", keyEvent(false));
  typer.addEventListener("beforeinput", (ev) => {
    if (!rfb) return;
    if (ev.inputType === "deleteContentBackward") { rfb.tap(0xff08); ev.preventDefault(); }
    else if (ev.inputType === "insertLineBreak" || ev.inputType === "insertParagraph") { rfb.tap(0xff0d); ev.preventDefault(); }
    else if (ev.data) { rfb.type(ev.data); ev.preventDefault(); }
  });
  const toolbar = h("div", { class: "row toolbar" }, sel, status,
    h("button", { class: "btn sm", text: "Keyboard", onclick: () => typer.focus() }),
    modBtn("Ctrl", MODS.ctrl), modBtn("Alt", MODS.alt), modBtn("Win", MODS.meta),
    h("button", { class: "btn sm", text: "Esc", onclick: () => rfb && rfb.tap(0xff1b) }),
    h("button", { class: "btn sm", text: "Tab", onclick: () => rfb && rfb.tap(0xff09) }),
    h("button", { class: "btn sm", text: "Ctrl+Alt+Del", onclick: () => rfb && rfb.combo([0xffe3, 0xffe9, 0xffff]) }),
    h("button", { class: "btn sm", text: "Type text", onclick: () => pasteForm((t) => rfb && rfb.type(t)) }),
    h("button", { class: "btn sm", text: "Screenshot", onclick: () => canvas.toBlob((b) => { const a = h("a", { href: URL.createObjectURL(b), download: `${sel.value}.png` }); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }) }),
    h("button", { class: "btn sm", id: "snd", "aria-pressed": "false", text: "Sound", onclick: (e) => {
      if (!rfb) return;
      if (rfb.audioOn) { rfb.disableAudio(); e.target.setAttribute("aria-pressed", "false"); return; }
      if (!rfb.audioReady) return toast("This VM has no sound: start it with --audio hda (or ac97, sb16)", true);
      if (!rfb.enableAudio(new QemuAudio())) return toast("The browser blocked audio", true);
      e.target.setAttribute("aria-pressed", "true"); } }),
    h("button", { class: "btn sm", text: "Fullscreen", onclick: () => canvas.requestFullscreen && canvas.requestFullscreen() }),
    h("button", { class: "btn sm bad", text: "Disconnect", onclick: () => { stopConsole(); status.textContent = "disconnected"; } }));
  root.append(h("div", { class: "card console" }, toolbar, h("div", { class: "screenwrap" }, canvas), typer));
  setTimeout(connect, 0);
  return root;
}
function pasteForm(send) {
  const ta = h("textarea", { id: "f_paste", rows: "5", spellcheck: "false" });
  openModal(h("h3", { text: "Type text" }), h("p", { class: "sub", text: "Sent to the guest as key presses, one character at a time." }), ta,
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Send", onclick: () => { send(ta.value); closeModal(); } }), h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
}

/* --- settings --- */
async function renderSettings() {
  const cfg = await api("settings"), presets = (await api("presets")).presets, root = h("div");
  const inputs = {};
  const form = h("div", { class: "form" });
  for (const [key, val] of Object.entries(cfg.values)) {
    const opts = cfg.options[key];
    const el = opts ? select("s_" + key, opts, String(val)) : (typeof val === "boolean" ? select("s_" + key, ["false", "true"], String(val)) : h("input", { id: "s_" + key, type: typeof val === "number" ? "number" : "text", value: String(val) }));
    inputs[key] = el; form.append(field(key.replace(/_/g, " "), el));
  }
  const save = async () => {
    const out = {};
    for (const [k, el] of Object.entries(inputs)) {
      const orig = cfg.values[k]; let v = el.value;
      if (typeof orig === "number") v = Number(v); else if (typeof orig === "boolean") v = v === "true";
      if (v !== orig) out[k] = v;
    }
    if (!Object.keys(out).length) return toast("Nothing changed");
    if (await act(() => api("settings/save", out), T("Saved"))) render();
  };
  root.append(h("div", { class: "card" }, h("h2", { text: "Default settings for new VMs" }), form,
    h("div", { class: "row" }, h("button", { class: "btn primary", text: "Save settings", onclick: save }),
      h("button", { class: "btn", text: "Reset to default", onclick: () => confirmThen("Clear every saved default?", async () => { await act(() => api("settings/save", Object.fromEntries(cfg.custom.map((k) => [k, ""]))), "Saved"); render(); }) }))));
  // this browser only
  const themeSel = select("p_theme", ["dark", "light"], store.get("theme", "dark")), langSel = select("p_lang", ["en", "ar"], lang());
  themeSel.addEventListener("change", () => { store.set("theme", themeSel.value); applyPrefs(); });
  langSel.addEventListener("change", () => { store.set("lang", langSel.value); applyPrefs(); render(); });
  const noteBtn = h("button", { class: "btn", text: "Enable", onclick: async () => {
    if (!("Notification" in window)) return toast(T("Not supported by this browser"), true);
    const p = await Notification.requestPermission();
    if (p === "granted") { store.set("notify", "1"); toast(T("Saved")); } else toast(T("Blocked in browser settings"), true);
  } });
  root.append(h("div", { class: "card" }, h("h2", { text: "This browser" }), h("div", { class: "form" }, field("Theme", themeSel), field("Language", langSel), field("Notifications", noteBtn))));
  root.append(await devicesCard());
  const names = Object.keys(presets);
  const pc = h("div", { class: "card" }, h("h2", { text: "Presets" }));
  if (!names.length) pc.append(h("p", { class: "sub", text: "Save one from the launch form (\"Save as preset\") or with `vortax preset save`." }));
  for (const n of names) pc.append(h("div", { class: "row" }, h("b", { text: n }), h("span", { class: "sub", text: Object.entries(presets[n]).map(([k, v]) => `${k}=${v}`).join(", ") }),
    h("button", { class: "btn sm primary", text: "Run", onclick: async () => { const r = await act(() => api("run", { preset: n, name: n })); if (r) trackJob(r.job); } }),
    h("button", { class: "btn sm bad", text: "Delete", onclick: () => confirmThen(`Delete preset ${n}?`, async () => { await act(() => api("presets/delete", { name: n })); render(); }) })));
  root.append(pc);
  return root;
}

/* --- trusted devices (Settings) --- */
const when = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
async function devicesCard() {
  const r = await api("devices"), card = h("div", { class: "card" }, h("h2", { text: "Trusted devices" }));
  card.append(h("p", { class: "sub", text: "Only browsers listed here can open this dashboard. A one-time link trusts the first browser that opens it and then stops working." }));
  const list = h("div", { class: "devs" });
  for (const d of r.devices) {
    list.append(h("div", { class: "dev" }, h("div", {}, h("b", { text: d.label }), d.id === r.current ? h("span", { class: "tag me", text: "this device" }) : null,
      h("div", { class: "sub", text: `${d.how === "password" ? "password" : "link"} · last seen ${when(d.last_seen)}` })),
      h("button", { class: "btn sm bad", text: "Remove", onclick: () => confirmThen(`Remove ${d.label}? It must sign in again.`, async () => { await act(() => api("devices/remove", { id: d.id }), "Removed"); if (d.id === r.current) { forgetDevice(); location.reload(); } else render(); }) })));
  }
  if (!r.devices.length) list.append(h("div", { class: "sub", text: "No trusted browsers yet (you are using the owner token)." }));
  card.append(list);
  const limit = h("input", { id: "d_max", type: "number", min: "1", max: "50", value: String(r.max_devices) });
  card.append(h("div", { class: "form" }, field("Device limit for one-time links", limit)),
    h("div", { class: "row" },
      h("button", { class: "btn", text: "Save limit", onclick: async () => { if (await act(() => api("devices/limit", { max_devices: Number(limit.value) }), "Saved")) render(); } }),
      h("button", { class: "btn primary", text: "New one-time link", onclick: async () => {
        const c = await act(() => api("devices/invite", {}));
        if (!c) return;
        const url = `${location.origin}/#claim=${c.code}`, box = h("input", { readonly: true, value: url, id: "d_link" });
        openModal(h("h3", { text: "One-time link" }), h("p", { class: "sub", text: "Open it on the new phone. It works once, for one hour, and then that phone is trusted." }), box,
          h("div", { class: "row" }, h("button", { class: "btn primary", text: "Copy", onclick: async () => { box.select(); try { await navigator.clipboard.writeText(url); toast("Copied"); } catch (e) { toast("Select the text and copy it", true); } } }),
            h("button", { class: "btn", text: "Close", onclick: closeModal })));
      } })));
  card.append(h("h2", { class: "gap", text: "Password sign-in" }),
    h("p", { class: "sub", text: r.password ? "On: any browser that knows the password can sign in and becomes trusted." : "Off: browsers without a one-time link cannot sign in." }),
    h("div", { class: "row" }, h("button", { class: "btn", text: r.password ? "Change password" : "Set a password", onclick: () => {
      const pw = h("input", { type: "password", id: "d_pw", autocomplete: "new-password" });
      openModal(h("h3", { text: "Dashboard password" }), h("p", { class: "sub", text: "At least 6 characters. Wrong guesses are slowed down." }), field("Password", pw),
        h("div", { class: "row" }, h("button", { class: "btn primary", text: "Save", onclick: async () => { if (await act(() => api("devices/password", { password: pw.value }), "Saved")) { closeModal(); render(); } } }),
          h("button", { class: "btn", text: "Cancel", onclick: closeModal })));
    } }), r.password ? h("button", { class: "btn bad", text: "Turn off", onclick: async () => { await act(() => api("devices/password", { clear: true }), "Saved"); render(); } }) : null));
  return card;
}

/* --- health --- */
async function renderHealth() {
  const r = await api("doctor"), tb = h("tbody");
  for (const c of r.checks) tb.append(h("tr", {}, h("td", { class: c.ok ? "ok" : c.required ? "fail" : "warnc", text: c.ok ? "ok" : c.required ? "missing" : "optional" }),
    h("td", { text: c.name }), h("td", { class: "mono", text: c.detail + (!c.ok && c.hint ? "  → " + c.hint : "") })));
  return h("div", { class: "card" }, h("table", {}, tb));
}

/* ---------- shell ---------- */
function go(v) { view = v; render(); }
function buildNav() {
  const nav = $("#tabs"); nav.replaceChildren();
  for (const [id, label, ic] of TABS) nav.append(h("button", { class: "tab", "aria-current": id === view ? "page" : false, onclick: () => go(id) }, icon(ic), h("span", { text: T(label) })));
  const cur = nav.querySelector("[aria-current]");                       // on a phone the tab row scrolls: keep the open tab in view
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ inline: "center", block: "nearest" });
}
async function render() {
  if (view !== "console") stopConsole();
  buildNav(); $("#title").textContent = T(TABS.find((t) => t[0] === view)[1]);
  const target = $("#view");
  try {
    if (!data) data = await api("overview");
    if (!catalog && (view === "launch" || view === "access" || view === "console")) catalog = await api("systems");
    const node = await ({ overview: renderOverview, launch: renderLaunch, access: renderAccess, console: renderConsole, settings: renderSettings, storage: renderStorage, logs: renderLogs, health: renderHealth })[view]();
    target.replaceChildren(node); translateTree(target);
  } catch (e) { if (e.message !== "auth") target.replaceChildren(h("div", { class: "card fail", text: e.message })); }
  $("#subtitle").textContent = data ? subtitle() : "";
}
const subtitle = () => `${data.instances.filter((v) => v.running).length} ${T("running")} · ${data.instances.length} ${T("total")}`;
let lastSig = "";
async function refresh() {
  try { data = await api("overview"); } catch (e) { return; }
  $("#clock").textContent = data.stats.time;
  $("#subtitle").textContent = subtitle();
  const now = new Set(data.instances.filter((v) => v.running).map((v) => v.name));
  if (lastRunning) for (const n of lastRunning) if (!now.has(n)) notifyUser("Vortax", `${n} stopped`);
  if (lastRunning) for (const n of now) if (!lastRunning.has(n)) notifyUser("Vortax", `${n} is running`);
  lastRunning = now;
  if (!$("#modal").hidden) return;                       // never rebuild the page under an open dialog
  if (view === "overview") render();
  else if (view === "access") {                          // keep revealed passwords on screen unless something changed
    const sig = JSON.stringify(data.instances.map((v) => [v.name, v.running, v.vnc && v.vnc.port, v.tunnel && v.tunnel.address]));
    if (sig !== lastSig) { lastSig = sig; render(); }
  }
}

async function footer() {
  try {
    const a = await api("about"), link = h("a", { href: a.source, target: "_blank", rel: "noopener", text: "Source code (AGPL-3.0)" });
    $("#foot").replaceChildren(link, document.createTextNode(` · vortax ${a.version}`)); translateTree($("#foot"));
  } catch (e) { /* the banner already explains auth problems */ }
}
/* ---------- sign-in: one-time link, password, or the owner token for scripts ---------- */
function forgetDevice() { token = ""; store.set("device", ""); try { sessionStorage.removeItem("vortax-token"); } catch (e) { /* ignore */ } }
function deviceLabel() {
  const u = navigator.userAgent, os = /Android/.test(u) ? "Android" : /iPhone|iPad/.test(u) ? "iOS" : /Windows/.test(u) ? "Windows" : /Mac/.test(u) ? "Mac" : /Linux/.test(u) ? "Linux" : "device";
  const br = /Edg\//.test(u) ? "Edge" : /Firefox/.test(u) ? "Firefox" : /Chrome/.test(u) ? "Chrome" : /Safari/.test(u) ? "Safari" : "browser";
  return `${os} ${br}`;
}
async function authPost(name, body) {
  const res = await fetch("/api/auth/" + name, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || res.statusText);
  return json;
}
async function showGate(msg) {
  stopConsole(); clearInterval(timer);
  document.body.classList.add("gate");
  let info = { password: false };
  try { info = await (await fetch("/api/auth/info")).json(); } catch (e) { /* offline */ }
  const card = h("div", { class: "card gate-card" }, h("div", { class: "gate-logo" }), h("h3", { text: "Vortax" }),
    h("p", { class: "sub", text: msg || "This browser is not trusted yet." }));
  if (info.password) {
    const pw = h("input", { type: "password", id: "g_pw", placeholder: "Password", autocomplete: "current-password" }), err = h("div", { class: "fail", text: "" });
    const go1 = async () => { try { const r = await authPost("login", { password: pw.value, label: deviceLabel() }); store.set("device", r.key); location.replace(location.pathname); } catch (e) { err.textContent = e.message; } };
    pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go1(); });
    card.append(field("Password", pw), err, h("button", { class: "btn primary block", text: "Sign in", onclick: go1 }));
  } else {
    card.append(h("p", { class: "note", text: "Open the one-time link that `vortax dashboard` printed, or ask the owner to turn on a password or raise the device limit." }));
  }
  $("#view").replaceChildren(card); $("#title").textContent = ""; $("#subtitle").textContent = ""; $("#clock").textContent = ""; $("#tabs").replaceChildren();
  translateTree($("#view"));
}
async function boot() {
  applyPrefs();
  const hash = location.hash, legacy = hash.match(/token=([A-Za-z0-9_-]+)/), claim = hash.match(/claim=([A-Za-z0-9_-]+)/);
  if (legacy || claim) history.replaceState(null, "", location.pathname);
  token = store.get("device", "");
  if (!token) { try { token = sessionStorage.getItem("vortax-token") || ""; } catch (e) { token = ""; } }
  if (legacy) { token = legacy[1]; try { sessionStorage.setItem("vortax-token", token); } catch (e) { /* ignore */ } }
  let gateMsg = "";
  if (claim) {
    try { const r = await authPost("claim", { code: claim[1], label: deviceLabel() }); token = r.key; store.set("device", token); }
    catch (e) { gateMsg = e.message; token = ""; }
  }
  if (!token) return showGate(gateMsg);
  $("#ver").textContent = "v3";
  render().then(() => { $("#ver").textContent = data ? "v" + data.version : ""; });
  footer();
  timer = setInterval(() => { if (!document.hidden) refresh(); }, 2500);
}
boot();
