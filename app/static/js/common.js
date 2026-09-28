// Waze Panel — shared front-end helpers (no dependencies).
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

/* ---------------------------------------------------------------- icons */
function ic(name, cls = "") {
  const p = (window.ICONS || {})[name] || "";
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}

/* ------------------------------------------------------------- escaping */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ------------------------------------------------------ numbers & bytes */
function fa(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return new Intl.NumberFormat("fa-IR", { maximumFractionDigits: digits }).format(n);
}

function bytesParts(n) {
  n = Number(n) || 0;
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  const digits = i === 0 ? 0 : n >= 100 ? 0 : n >= 10 ? 1 : 2;
  return { value: fa(n, digits), unit: units[i] };
}
const bytes = (n) => { const p = bytesParts(n); return `${p.value} ${p.unit}`; };
// Bytes inside RTL text must be isolated, or "12 GB" renders as "GB 12".
const bytesHtml = (n) => `<bdi class="num">${bytes(n)}</bdi>`;
// Same for canvas text / title attributes, via Unicode directional isolates.
const bytesTxt = (n) => `\u2066${bytes(n)}\u2069`;
const pct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/* ----------------------------------------------------------------- time */
function jDate(iso, opts) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("fa-IR", opts || { year: "numeric", month: "long", day: "numeric" });
}
// "2026-09-28" -> "۶ مهر"
function jShort(isoDate) {
  return new Date(isoDate + "T12:00:00").toLocaleDateString("fa-IR", { month: "short", day: "numeric" });
}
function relTime(iso) {
  if (!iso) return "هرگز";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "همین الان";
  if (s < 3600) return `${fa(Math.floor(s / 60))} دقیقه پیش`;
  if (s < 86400) return `${fa(Math.floor(s / 3600))} ساعت پیش`;
  if (s < 86400 * 30) return `${fa(Math.floor(s / 86400))} روز پیش`;
  return jDate(iso);
}
function durationFa(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${fa(d)} روز و ${fa(h)} ساعت`;
  if (h) return `${fa(h)} ساعت و ${fa(m)} دقیقه`;
  return `${fa(Math.max(1, m))} دقیقه`;
}
function daysLeftLabel(days) {
  if (days === null || days === undefined) return "بدون محدودیت زمانی";
  if (days <= 0) return "منقضی شده";
  if (days === 1) return "کمتر از ۱ روز";
  return `${fa(days)} روز مانده`;
}

/* --------------------------------------------------------------- status */
const STATUS = {
  active: { label: "فعال", cls: "active" },
  disabled: { label: "غیرفعال", cls: "disabled" },
  expired: { label: "منقضی", cls: "expired" },
  over_quota: { label: "اتمام حجم", cls: "over_quota" },
  revoked: { label: "باطل‌شده", cls: "revoked" },
};
function statusBadge(s) {
  const m = STATUS[s] || { label: s, cls: "" };
  return `<span class="status ${m.cls}">${m.label}</span>`;
}

/* -------------------------------------------------------------- avatars */
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function avatar(name, online = false, cls = "") {
  const initials = String(name).replace(/[^a-zA-Z0-9]/g, "").slice(0, 2) || "?";
  return `<span class="avatar ${cls}" style="--h:${hue(name)}">${esc(initials)}${online ? '<span class="on"></span>' : ""}</span>`;
}
function levelBar(p) { return p >= 100 ? "bad" : p >= 85 ? "warn" : ""; }
function levelColor(p) { return p >= 100 ? "var(--red)" : p >= 85 ? "var(--amber)" : null; }

// Progress ring. Default stroke is the brand's two blues; pass `color` for
// warning levels.
let _ringId = 0;
function ring(percent, { size = 76, stroke = 8, color = null, label = "" } = {}) {
  const id = `rg${++_ringId}`;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.min(100, Math.max(0, Number(percent) || 0));
  return `<div class="ring" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="1" x2="1" y2="0"><stop offset="0" style="stop-color:var(--brand-b)"/><stop offset="1" style="stop-color:var(--brand-a)"/></linearGradient></defs>
      <circle class="track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>
      <circle class="val" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color || `url(#${id})`}" stroke-width="${stroke}"
        stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - p / 100)}"/>
    </svg>
    <div class="center">${label}</div>
  </div>`;
}

/* ------------------------------------------------------------------ api */
async function api(path, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = "مقادیر وارد شده معتبر نیستند.";
    throw new Error(detail || `خطا (${res.status})`);
  }
  return data;
}

/* --------------------------------------------------------------- toasts */
function toast(message, type = "success") {
  const stack = $("#toasts");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `${ic(type === "error" ? "alert" : "check")}<span>${esc(message)}</span>`;
  stack.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 220); }, 3200);
}

/* ------------------------------------------------------------- overlays */
const overlays = [];
function openOverlay(el) {
  if (!el || overlays.includes(el)) return;
  el.classList.add("open");
  overlays.push(el);
  $("#scrim").classList.add("open");
  document.body.style.overflow = "hidden";
  if (el.classList.contains("drawer")) {
    // take focus without highlighting any particular control (Esc still works)
    el.setAttribute("tabindex", "-1");
    setTimeout(() => el.focus({ preventScroll: true }), 60);
    return;
  }
  const focusable = el.querySelector("[autofocus], input:not([type=hidden]):not([readonly])");
  if (focusable && window.matchMedia("(min-width: 641px)").matches) setTimeout(() => focusable.focus(), 60);
}
function closeOverlay(el) {
  el = el || overlays[overlays.length - 1];
  if (!el) return;
  el.classList.remove("open");
  overlays.splice(overlays.indexOf(el), 1);
  if (!overlays.length) {
    $("#scrim").classList.remove("open");
    document.body.style.overflow = "";
  }
  el.dispatchEvent(new Event("overlay-close"));
}
document.addEventListener("click", (ev) => {
  if (ev.target.id === "scrim") closeOverlay();
  const closer = ev.target.closest("[data-close]");
  if (closer) closeOverlay(closer.closest(".modal, .drawer"));
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && overlays.length) { ev.preventDefault(); closeOverlay(); }
});

function confirmDialog({ title, message = "", ok = "تایید", danger = true }) {
  return new Promise((resolve) => {
    const dlg = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-msg").textContent = message;
    const okBtn = $("#confirm-ok");
    okBtn.textContent = ok;
    okBtn.className = `btn ${danger ? "danger solid" : "primary"}`;
    let done = false;
    const finish = (val) => {
      if (done) return;
      done = true;
      okBtn.onclick = null; $("#confirm-cancel").onclick = null;
      dlg.removeEventListener("overlay-close", onClose);
      if (overlays.includes(dlg)) closeOverlay(dlg);
      resolve(val);
    };
    const onClose = () => finish(false);
    okBtn.onclick = () => finish(true);
    $("#confirm-cancel").onclick = () => finish(false);
    dlg.addEventListener("overlay-close", onClose);
    openOverlay(dlg);
  });
}

/* ------------------------------------------------------- copy, QR, busy */
// Brief checkmark on the button that was used, so a copy is felt where it happened.
function flashCopied(btn) {
  if (!btn) return;
  const svg = btn.querySelector("svg");
  if (!svg || btn.classList.contains("copied")) return;
  const html = svg.outerHTML;
  svg.outerHTML = ic("check");
  btn.classList.add("copied");
  setTimeout(() => {
    const now = btn.querySelector("svg");
    if (now) now.outerHTML = html;
    btn.classList.remove("copied");
  }, 1300);
}

async function copyText(text, message = "کپی شد", btn = null) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (err) { toast("کپی نشد", "error"); ta.remove(); return; }
    ta.remove();
  }
  haptic();
  flashCopied(btn);
  toast(message);
}
// Any [data-copy] button copies its value.
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-copy]");
  if (b) { ev.stopPropagation(); copyText(b.dataset.copy, b.dataset.copyMsg || "کپی شد", b); }
});

function renderQr(el, text, size = 168) {
  if (!el) return;
  el.innerHTML = "";
  if (typeof QRCode === "undefined" || !text) { el.style.display = "none"; return; }
  el.style.display = "";
  new QRCode(el, { text, width: size, height: size, colorDark: "#111316", colorLight: "#ffffff", correctLevel: QRCode.CorrectLevel.M });
}

/* ------------------------------------------------------ login / creds */
const AUTH = {
  cert: { label: "گواهی", icon: "cert" },
  cert_pass: { label: "گواهی + رمز", icon: "shield-check" },
  pass: { label: "فقط رمز", icon: "lock" },
};
// same alphabet as the server: no look-alikes (0/O, 1/l/I), no spaces
const PW_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
function randomPassword(len = 10) {
  const buf = new Uint32Array(len);
  crypto.getRandomValues(buf);
  return Array.from(buf, (n) => PW_ALPHABET[n % PW_ALPHABET.length]).join("");
}
// One cred row. The value lives in data-v so copy/reveal never read the DOM text.
function credRow(label, value, { secret = false } = {}) {
  const shown = secret ? "•".repeat(Math.min(12, Math.max(6, String(value).length))) : esc(value);
  return `<div class="cred" data-v="${esc(value)}">
    <span class="k">${label}</span>
    <span class="v mono ${secret ? "masked" : ""}" data-secret="${secret ? 1 : 0}">${shown}</span>
    ${secret ? `<button type="button" class="btn icon sm ghost" data-cred="reveal" title="نمایش">${ic("eye")}</button>` : ""}
    <button type="button" class="btn icon sm ghost" data-cred="copy" title="کپی">${ic("copy")}</button>
  </div>`;
}
// Ready-to-send text for the customer: the login only for password modes.
function shareText({ username, password, sub_link, auth_mode }) {
  const withLogin = auth_mode && auth_mode !== "cert" && password;
  return [`نام کاربری: ${username}`, withLogin ? `رمز: ${password}` : null, `لینک اشتراک: ${sub_link}`]
    .filter(Boolean).join("\n");
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-cred]");
  if (!b) return;
  const row = b.closest(".cred");
  const v = row.dataset.v;
  if (b.dataset.cred === "copy") return copyText(v, "کپی شد", b);
  const span = row.querySelector(".v");
  const hidden = span.classList.toggle("masked");
  span.textContent = hidden ? "•".repeat(Math.min(12, Math.max(6, v.length))) : v;
  b.innerHTML = ic(hidden ? "eye" : "eye-off");
});

// Busy state for the button that started an action. Refresh-style buttons
// ([data-spin]) turn their own icon; others swap it for a spinner. It stays
// visible for at least a moment, so a fast answer still reads as "done".
async function withBusy(btn, fn) {
  const cls = btn && btn.hasAttribute("data-spin") ? "spinning" : "loading";
  const started = Date.now();
  if (btn) btn.classList.add(cls);
  try {
    return await fn();
  } finally {
    const wait = Math.max(0, 450 - (Date.now() - started));
    if (btn) setTimeout(() => btn.classList.remove(cls), wait);
  }
}

/* ---------------------------------------------------------------- theme */
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
// The phone's status/address bar follows the panel theme.
function syncThemeColor() {
  const m = document.querySelector('meta[name="theme-color"]');
  if (m) m.content = document.documentElement.dataset.theme === "light" ? "#f6f7f9" : "#0f1012";
}
syncThemeColor();
window.addEventListener("themechange", syncThemeColor);
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("waze-theme", theme); } catch (e) {}
  window.dispatchEvent(new CustomEvent("themechange"));
}
document.addEventListener("click", (ev) => {
  if (ev.target.closest("[data-theme-toggle]")) {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  }
});

/* --------------------------------------------------------------- charts */
function chartDefaults() {
  if (!window.Chart) return;
  Chart.defaults.font.family = "Vazirmatn, Tahoma, sans-serif";
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = cssVar("--text-3");
  Chart.defaults.borderColor = cssVar("--line");
  const t = Chart.defaults.plugins.tooltip;
  t.backgroundColor = cssVar("--toast-bg");
  t.titleColor = "#f1f3f5";
  t.bodyColor = "#d5d9df";
  t.padding = 10;
  t.cornerRadius = 8;
  t.displayColors = false;
  t.rtl = true;
  t.textDirection = "rtl";
  t.titleFont = { weight: "600" };
}
// Daily bars: the latest day in the accent, earlier days quieter.
function barColors(n) {
  const strong = cssVar("--accent-fill");
  return Array.from({ length: n }, (_, i) => (i === n - 1 ? strong : strong + "73"));
}
chartDefaults();
window.addEventListener("themechange", chartDefaults);

/* ---------------------------------------------------------------- mobile */
// A short buzz on Android when something happens under the finger.
function haptic(ms = 8) {
  try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) {}
}
document.addEventListener("change", (ev) => { if (ev.target.closest(".switch")) haptic(); });

// Live count on the "online" tab of the bottom navigation.
function setNavOnline(n) {
  for (const b of [$("#nav-online"), $("#side-online")]) {
    if (!b) continue;
    b.textContent = fa(n);
    b.classList.toggle("hide", !n);
  }
}

// The phone's own share sheet (Telegram, WhatsApp, ...), or copy when the
// browser has none (e.g. the panel on plain HTTP).
async function shareOrCopy(text, title = document.title) {
  if (navigator.share) {
    try { await navigator.share({ title, text }); haptic(); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  copyText(text, "کپی شد؛ در تلگرام یا هر پیام‌رسانی بفرستید");
}

// On phones drawers and modals are bottom sheets: drag them down to close.
function enableSheetSwipe(el) {
  let startY = null, dy = 0, t0 = 0;
  el.addEventListener("touchstart", (e) => {
    if (!matchMedia("(max-width: 640px)").matches || !el.classList.contains("open")) return;
    const onHead = e.target.closest(".drawer-head, .modal-head, .sheet-grip");
    if ((!onHead && el.scrollTop > 0) || e.target.closest("input, textarea, select, canvas, .chips, .seg")) return;
    startY = e.touches[0].clientY; dy = 0; t0 = Date.now();
  }, { passive: true });
  el.addEventListener("touchmove", (e) => {
    if (startY === null) return;
    dy = e.touches[0].clientY - startY;
    if (dy <= 0) { el.style.transform = ""; return; }
    el.style.transition = "none";
    el.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  const end = () => {
    if (startY === null) return;
    const fast = dy / Math.max(1, Date.now() - t0) > 0.6;
    el.style.transition = "";
    el.style.transform = "";
    if (dy > 120 || (dy > 40 && fast)) { haptic(); closeOverlay(el); }
    startY = null;
  };
  el.addEventListener("touchend", end);
  el.addEventListener("touchcancel", end);
}
$$(".drawer, .modal").forEach(enableSheetSwipe);

// "Install as app" buttons ([data-install]) appear only when the browser
// offers installation (Chrome/Android over HTTPS).
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $$("[data-install]").forEach((b) => b.classList.remove("hide"));
});
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-install]");
  if (!b || !installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice.catch(() => null);
  installPrompt = null;
  $$("[data-install]").forEach((x) => x.classList.add("hide"));
});
