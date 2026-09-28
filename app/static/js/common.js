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
  return `<span class="badge ${m.cls}"><i></i>${m.label}</span>`;
}

/* ------------------------------------------------------ avatars & rings */
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function avatar(name, online = false, cls = "") {
  const h = hue(name);
  const initials = String(name).replace(/[^a-zA-Z0-9]/g, "").slice(0, 2) || "?";
  return `<div class="avatar ${cls}" style="background:linear-gradient(135deg,hsl(${h} 72% 60%),hsl(${(h + 45) % 360} 76% 46%))">${esc(initials)}${online ? '<span class="dot"></span>' : ""}</div>`;
}

let _ringId = 0;
function ring(percent, { size = 64, stroke = 7, color = null, label = "", sub = "" } = {}) {
  const id = `rg${++_ringId}`;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.min(100, Math.max(0, Number(percent) || 0));
  const strokeStyle = color || `url(#${id})`;
  return `<div class="ring" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#22d3ee"/></linearGradient></defs>
      <circle class="track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>
      <circle class="val" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" style="stroke:${strokeStyle}" stroke-width="${stroke}"
        stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - p / 100)}"/>
    </svg>
    <div class="center">${label}${sub ? `<small>${sub}</small>` : ""}</div>
  </div>`;
}
function levelColor(p) { return p >= 100 ? "var(--rose)" : p >= 85 ? "var(--amber)" : null; }
function levelBar(p) { return p >= 100 ? "bad" : p >= 85 ? "warn" : ""; }

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

function confirmDialog({ title, message = "", ok = "تایید", danger = true, icon = "alert" }) {
  return new Promise((resolve) => {
    const dlg = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-msg").textContent = message;
    const okBtn = $("#confirm-ok");
    okBtn.textContent = ok;
    okBtn.className = `btn ${danger ? "danger" : "primary"}`;
    const iconBox = $("#confirm-icon");
    iconBox.className = `dialog-icon ${danger ? "" : "info"}`;
    iconBox.innerHTML = ic(icon);
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
async function copyText(text, message = "کپی شد") {
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
  toast(message);
}

function renderQr(el, text, size = 168) {
  if (!el) return;
  el.innerHTML = "";
  if (typeof QRCode === "undefined" || !text) { el.style.display = "none"; return; }
  el.style.display = "";
  new QRCode(el, { text, width: size, height: size, colorDark: "#0b0e17", colorLight: "#ffffff", correctLevel: QRCode.CorrectLevel.M });
}

async function withBusy(btn, fn) {
  if (btn) btn.classList.add("loading");
  try { return await fn(); } finally { if (btn) btn.classList.remove("loading"); }
}

/* ---------------------------------------------------------------- theme */
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
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
  Chart.defaults.font.size = 11;
  Chart.defaults.color = cssVar("--text-3");
  Chart.defaults.borderColor = cssVar("--border");
}
chartDefaults();
window.addEventListener("themechange", chartDefaults);
