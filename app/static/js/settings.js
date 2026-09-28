// Settings page.
"use strict";

$("#settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  await withBusy(ev.target.querySelector("button[type=submit]"), async () => {
    try {
      await api("/api/settings", {
        method: "POST",
        body: {
          server_address: f.get("server_address"),
          panel_title: f.get("panel_title"),
          subscription_base_url: f.get("subscription_base_url"),
        },
      });
      toast("تنظیمات ذخیره شد");
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

function strength(pw) {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw)) s++;
  return Math.min(4, s);
}
$("#p-new").addEventListener("input", (ev) => {
  const s = strength(ev.target.value);
  const labels = ["خیلی ضعیف", "ضعیف", "متوسط", "خوب", "قوی"];
  const meter = $("#p-meter");
  meter.style.width = `${ev.target.value ? (s + 1) * 20 : 0}%`;
  meter.parentElement.className = `bar ${s <= 1 ? "bad" : s === 2 ? "warn" : ""}`;
  $("#p-hint").textContent = ev.target.value ? `قدرت رمز: ${labels[s]}` : "حداقل ۶ کاراکتر؛ ترکیب حروف بزرگ و کوچک، عدد و نماد امن‌تر است.";
});
$("#p-show").addEventListener("click", () => {
  const p = $("#p-new");
  p.type = p.type === "password" ? "text" : "password";
});

$("#password-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  await withBusy(ev.target.querySelector("button[type=submit]"), async () => {
    try {
      await api("/api/settings/password", {
        method: "POST",
        body: { current_password: f.get("current_password"), new_password: f.get("new_password") },
      });
      toast("رمز عبور تغییر کرد");
      ev.target.reset();
      $("#p-meter").style.width = "0%";
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

function markTheme() {
  let saved = null;
  try { saved = localStorage.getItem("waze-theme"); } catch (e) {}
  $$("#theme-seg button").forEach((b) => b.classList.toggle("active", b.dataset.themeSet === (saved || "auto")));
}
$("#theme-seg").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-theme-set]");
  if (!b) return;
  const choice = b.dataset.themeSet;
  if (choice === "auto") {
    try { localStorage.removeItem("waze-theme"); } catch (e) {}
    document.documentElement.dataset.theme = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    window.dispatchEvent(new CustomEvent("themechange"));
  } else {
    setTheme(choice);
  }
  markTheme();
});
window.addEventListener("themechange", markTheme);
markTheme();

/* ---------------------------------------------------------------- relays */
let relayData = null; // {relays, options, health, direct}
let editingRelay = null;

const RELAY_ERR = {
  timeout: "جواب نمی‌دهد",
  refused: "پورت بسته است؛ دستور نصب اجرا شده؟",
  not_forwarded: "به این سرور نمی‌رسد",
};

function relayStat(h) {
  if (!h) return `<span class="status">بررسی نشده</span>`;
  if (h.ok) return `<span class="status ok">سالم · <bdi>${fa(h.ms)}ms</bdi></span>`;
  return `<span class="status bad">${esc(RELAY_ERR[h.error] || h.error || "خطا")}</span>`;
}

function relayRow(r, i, n) {
  return `<div class="list-row relay-row ${r.enabled ? "" : "off"}" data-id="${r.id}">
    <span class="relay-num">${fa(i + 1)}</span>
    <div class="grow info">
      <div class="row" style="gap:8px;flex-wrap:wrap"><span class="name">${esc(r.name)}</span>${r.enabled ? relayStat(relayData.health[r.id]) : `<span class="status">غیرفعال</span>`}</div>
      <div class="meta"><span class="mono ltr">${esc(r.address)}</span><span>· UDP ${faPort(r.udp_port)} · TCP ${faPort(r.tcp_port)}</span></div>
    </div>
    <div class="relay-ops">
      <button class="btn icon sm ghost" type="button" data-r="up" title="اولویت بالاتر" ${i === 0 ? "disabled" : ""}>${ic("arrow-up")}</button>
      <button class="btn icon sm ghost" type="button" data-r="down" title="اولویت پایین‌تر" ${i === n - 1 ? "disabled" : ""}>${ic("arrow-down")}</button>
      <button class="btn icon sm ghost" type="button" data-r="setup" title="دستور نصب روی سرور ایران">${ic("terminal")}</button>
      <button class="btn icon sm ghost" type="button" data-r="edit" title="ویرایش">${ic("edit")}</button>
      <label class="switch" title="${r.enabled ? "غیرفعال کردن" : "فعال کردن"}"><input type="checkbox" data-r="toggle" ${r.enabled ? "checked" : ""} aria-label="فعال بودن ${esc(r.name)}" /><span></span></label>
      <button class="btn icon sm ghost danger" type="button" data-r="delete" title="حذف">${ic("trash")}</button>
    </div>
  </div>`;
}

function directRow() {
  const d = relayData.direct;
  const any = relayData.relays.length > 0;
  const on = !any || relayData.options.fallback_direct;
  const tag = !any ? "" : on ? `<span class="status">پشتیبان آخر</span>` : `<span class="status">خاموش</span>`;
  return `<div class="list-row relay-row ${on ? "" : "off"}">
    <span class="relay-num">${ic("server")}</span>
    <div class="grow info">
      <div class="row" style="gap:8px;flex-wrap:wrap"><span class="name">اتصال مستقیم به همین سرور</span>${tag}</div>
      <div class="meta"><span class="mono ltr">${esc(d.address)}</span><span>· UDP ${[d.udp_port, ...(d.udp_extra || [])].map(faPort).join("، ")} · TCP ${[d.tcp_port, ...(d.tcp_extra || [])].map(faPort).join("، ")}</span></div>
    </div>
  </div>`;
}

function renderRelays() {
  const rs = relayData.relays;
  $("#relay-list").innerHTML = (rs.length
    ? rs.map((r, i) => relayRow(r, i, rs.length)).join("")
    : `<div class="empty" style="padding:22px 18px"><b>هنوز سرور واسطی اضافه نشده</b>فعلا کاربران مستقیم به همین سرور وصل می‌شوند.</div>`) + directRow();
  const f = $("#relay-options");
  f.fallback_direct.checked = relayData.options.fallback_direct;
  f.balance.checked = relayData.options.balance;
  f.timeout.value = relayData.options.timeout;
  $("#relay-check").classList.toggle("hide", !rs.length);
}

async function loadRelays() {
  try {
    relayData = await api("/api/relays");
    renderRelays();
  } catch (e) {
    toast(e.message, "error");
  }
}

function openRelayForm(relay = null) {
  editingRelay = relay;
  const f = $("#relay-form");
  f.reset();
  $("#relay-modal-title").textContent = relay ? `ویرایش ${relay.name}` : "سرور واسط جدید";
  if (relay) {
    f.name.value = relay.name;
    f.address.value = relay.address;
    f.udp_port.value = relay.udp_port;
    f.tcp_port.value = relay.tcp_port;
  }
  $("#relay-form-view").classList.remove("hide");
  $("#relay-setup-view").classList.add("hide");
  openOverlay($("#relay-modal"));
  setTimeout(() => f.name.focus(), 50);
}

function showRelaySetup(relay, reopen = true) {
  $("#rs-name").textContent = relay.name;
  $("#rs-cmd").textContent = relay.command;
  $("#rs-cmd-alt").textContent = relay.command_alt;
  $("#relay-form-view").classList.add("hide");
  $("#relay-setup-view").classList.remove("hide");
  if (reopen) openOverlay($("#relay-modal"));
}

$("#relay-add").addEventListener("click", () => openRelayForm());

$("#relay-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    name: f.name.value.trim(),
    address: f.address.value.trim(),
    udp_port: f.udp_port.value ? +f.udp_port.value : null,
    tcp_port: f.tcp_port.value ? +f.tcp_port.value : null,
  };
  await withBusy($("#relay-save"), async () => {
    try {
      const relay = editingRelay
        ? await api(`/api/relays/${editingRelay.id}`, { method: "PATCH", body })
        : await api("/api/relays", { method: "POST", body });
      await loadRelays();
      if (editingRelay) {
        toast("سرور واسط ذخیره شد");
        closeOverlay($("#relay-modal"));
      } else {
        toast("سرور واسط اضافه شد؛ حالا روی سرور ایران راه‌اندازی‌اش کنید");
        showRelaySetup(relay, false);
      }
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

$("#relay-list").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-r]");
  if (!b) return;
  const id = +b.closest(".relay-row").dataset.id;
  const rs = relayData.relays;
  const idx = rs.findIndex((r) => r.id === id);
  const relay = rs[idx];
  try {
    if (b.dataset.r === "setup") return showRelaySetup(relay);
    if (b.dataset.r === "edit") return openRelayForm(relay);
    if (b.dataset.r === "up" || b.dataset.r === "down") {
      const ids = rs.map((r) => r.id);
      const to = b.dataset.r === "up" ? idx - 1 : idx + 1;
      [ids[idx], ids[to]] = [ids[to], ids[idx]];
      await api("/api/relays/order", { method: "POST", body: { ids } });
      return loadRelays();
    }
    if (b.dataset.r === "delete") {
      if (!(await confirmDialog({ title: `حذف ${relay.name}؟`, message: "این سرور از کانفیگ‌هایی که از این به بعد دانلود شوند حذف می‌شود. روی خود سرور ایران هم می‌توانید waze-relay uninstall را بزنید.", ok: "حذف", icon: "trash" }))) return;
      await api(`/api/relays/${id}`, { method: "DELETE" });
      toast(`${relay.name} حذف شد`);
      return loadRelays();
    }
  } catch (e) {
    toast(e.message, "error");
  }
});

$("#relay-list").addEventListener("change", async (ev) => {
  const t = ev.target.closest("[data-r=toggle]");
  if (!t) return;
  const id = +t.closest(".relay-row").dataset.id;
  try {
    await api(`/api/relays/${id}`, { method: "PATCH", body: { enabled: t.checked } });
    toast(t.checked ? "سرور واسط فعال شد" : "سرور واسط غیرفعال شد");
    loadRelays();
  } catch (e) {
    t.checked = !t.checked;
    toast(e.message, "error");
  }
});

$("#relay-check").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  try {
    relayData.health = await api("/api/relays/check", { method: "POST" });
    renderRelays();
    const enabled = relayData.relays.filter((r) => r.enabled);
    const good = enabled.filter((r) => relayData.health[r.id] && relayData.health[r.id].ok).length;
    toast(`${fa(good)} از ${fa(enabled.length)} سرور واسط سالم است`, good === enabled.length ? "success" : "error");
  } catch (e) {
    toast(e.message, "error");
  }
}));

$("#relay-options").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  await withBusy(f.querySelector("button[type=submit]"), async () => {
    try {
      relayData.options = await api("/api/relays/options", {
        method: "POST",
        body: { fallback_direct: f.fallback_direct.checked, balance: f.balance.checked, timeout: +f.timeout.value || 8 },
      });
      renderRelays();
      toast("تنظیمات سرور واسط ذخیره شد");
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-copy-from]");
  if (b) copyText($(b.dataset.copyFrom).textContent, "دستور کپی شد", b);
});

loadRelays();

/* ------------------------------------------------- section nav + scroll spy */
(() => {
  const nav = $("#section-nav");
  const links = $$("a", nav);
  const byId = Object.fromEntries(links.map((a) => [a.getAttribute("href").slice(1), a]));
  const mark = (id) => {
    links.forEach((a) => a.classList.toggle("active", a === byId[id]));
    const a = byId[id];
    if (a) nav.scrollTo({ left: a.offsetLeft - nav.clientWidth / 2 + a.clientWidth / 2, behavior: "smooth" });
  };
  const seen = new Map();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => seen.set(e.target.id, e.isIntersecting ? e.intersectionRatio : 0));
    const best = [...seen.entries()].sort((a, b) => b[1] - a[1])[0];
    if (best && best[1] > 0) mark(best[0]);
  }, { threshold: [0, 0.25, 0.5, 0.75, 1], rootMargin: "-120px 0px -35% 0px" });
  Object.keys(byId).forEach((id) => { const el = document.getElementById(id); if (el) io.observe(el); });
  // the last section is too short to ever win the observer: at the very bottom, it's the one
  window.addEventListener("scroll", () => {
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) mark(links[links.length - 1].getAttribute("href").slice(1));
  }, { passive: true });
  nav.addEventListener("click", (ev) => {
    const a = ev.target.closest("a");
    if (!a) return;
    ev.preventDefault();
    haptic();
    document.getElementById(a.getAttribute("href").slice(1)).scrollIntoView({ behavior: "smooth", block: "start" });
  });
  if (location.hash && byId[location.hash.slice(1)]) setTimeout(() => document.getElementById(location.hash.slice(1)).scrollIntoView({ block: "start" }), 300);
})();

/* ----------------------------------------------- ports, HTTPS cover, keys */
let conn = null;          // /api/settings/connection
let portDraft = null;     // {udp: [...], tcp: [...]} being edited
let keySel = null;        // key mode picked but not applied yet

const latinDigits = (s) => String(s).replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d));
const sameList = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

const PROTO_NOTE = { udp: "سریع‌تر؛ اولین انتخاب کانفیگ‌ها", tcp: "پایدارتر روی شبکه‌های سخت‌گیر" };

function portGroup(proto) {
  const info = conn.ports[proto];
  const list = portDraft[proto];
  const arrow = `<i class="pf-arrow" aria-hidden="true">${ic("chevron-left")}</i>`;
  // each arrow travels with the port after it, so a wrapped line never ends on one
  const chips = list.map((p) => `<span class="pf-step">${arrow}<span class="port"><bdi>${faPort(p)}</bdi><button type="button" class="x" data-rm="${p}" title="حذف ${faPort(p)}" aria-label="حذف پورت ${faPort(p)}">${ic("x")}</button></span></span>`).join("");
  const add = list.length < conn.ports.max
    ? `<span class="pf-step">${arrow}<span class="port-add"><input type="text" inputmode="numeric" maxlength="5" placeholder="پورت" aria-label="پورت ${proto.toUpperCase()} اضافه" /><button type="button" class="btn icon sm ghost" data-add title="افزودن">${ic("plus")}</button></span></span>`
    : "";
  const sug = info.suggest.filter((p) => !list.includes(p)).slice(0, 5);
  return `
    <div class="port-group" data-proto="${proto}">
      <div class="pg-head"><span class="pg-proto">${proto.toUpperCase()}</span><span class="pg-sub">${PROTO_NOTE[proto]}</span></div>
      <div class="port-flow">
        <span class="port is-main" title="پورت اصلی"><bdi>${faPort(info.main)}</bdi><small>اصلی</small></span>${chips}${add}
      </div>
      ${sug.length && list.length < conn.ports.max ? `<div class="pg-suggest"><span>پیشنهاد:</span>${sug.map((p) => `<button type="button" class="chip" data-sug="${p}"><bdi>${faPort(p)}</bdi></button>`).join("")}</div>` : ""}
    </div>`;
}

function renderPorts() {
  $("#port-groups").innerHTML = portGroup("udp") + portGroup("tcp");
  const dirty = !sameList(portDraft.udp, conn.ports.udp.extra) || !sameList(portDraft.tcp, conn.ports.tcp.extra);
  $("#ports-save").disabled = !dirty;
}

function addPort(proto, raw) {
  const p = parseInt(latinDigits(raw).trim(), 10);
  if (!p || p < 1 || p > 65535) return toast("پورت باید عددی بین ۱ تا ۶۵۵۳۵ باشد", "error");
  if (p === conn.ports[proto].main) return toast("این همان پورت اصلی است", "error");
  if (portDraft[proto].includes(p)) return toast("این پورت در لیست هست", "error");
  portDraft[proto].push(p);
  renderPorts();
  const input = $(`.port-group[data-proto="${proto}"] .port-add input`);
  if (input) input.focus();
}

$("#port-groups").addEventListener("click", (ev) => {
  const group = ev.target.closest(".port-group");
  if (!group) return;
  const proto = group.dataset.proto;
  const rm = ev.target.closest("[data-rm]");
  if (rm) {
    portDraft[proto] = portDraft[proto].filter((p) => p !== +rm.dataset.rm);
    return renderPorts();
  }
  const sug = ev.target.closest("[data-sug]");
  if (sug) return addPort(proto, sug.dataset.sug);
  if (ev.target.closest("[data-add]")) {
    const input = group.querySelector(".port-add input");
    if (input.value.trim()) addPort(proto, input.value);
    else input.focus();
  }
});
$("#port-groups").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || !ev.target.closest(".port-add")) return;
  ev.preventDefault();
  if (ev.target.value.trim()) addPort(ev.target.closest(".port-group").dataset.proto, ev.target.value);
});

$("#ports-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  await withBusy($("#ports-save"), async () => {
    try {
      conn = await api("/api/settings/connection/ports", { method: "POST", body: portDraft });
      portDraft = { udp: [...conn.ports.udp.extra], tcp: [...conn.ports.tcp.extra] };
      renderPorts();
      toast("پورت‌ها ذخیره شد؛ کاربران با دانلود دوباره‌ی کانفیگ آن‌ها را می‌گیرند");
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

/* HTTPS cover: a little browser showing what a visitor of the port sees */
function renderCover() {
  const c = conn.cover;
  const sw = $("#cover-switch");
  sw.checked = c.enabled;
  sw.disabled = !conn.wired;
  const url = esc(c.url.replace(/^https:\/\//, ""));
  const on = c.enabled && c.running;
  const browser = `
    <div class="browser ${on ? "on" : "off"}" dir="ltr" aria-hidden="true">
      <div class="b-bar"><span class="dots"><i></i><i></i><i></i></span><span class="b-url">${ic(on ? "lock" : "info")}<span>${on ? "https://" : ""}${url}</span></span></div>
      <div class="b-page">${on
        ? `<b>Welcome to nginx!</b><span></span><span></span><span class="short"></span>`
        : `<div class="b-empty">${ic("cloud-off")}<div>This site can’t be reached</div><small>ERR_CONNECTION_CLOSED</small></div>`}</div>
    </div>`;
  let facts;
  if (!conn.wired) {
    facts = `<div class="alert warn">${ic("alert")}<span>کانفیگ سرور OpenVPN قدیمی است؛ اول روی سرور <code>waze-panel update</code> را بزنید.</span></div>`;
  } else if (!c.enabled) {
    facts = `<p class="cover-say">الان هر کسی این پورت را با مرورگر باز کند جوابی نمی‌گیرد؛ پورتی که فقط به VPN جواب می‌دهد برای سیستم‌های فیلترینگ نشانه است. با روشن کردن پوشش، همین آدرس یک سایت HTTPS معمولی نشان می‌دهد.</p>`;
  } else {
    const site = c.backend === "site" ? "سایت Nginx همین سرور" : "سایت ساده‌ی داخلی پنل";
    const cert = c.cert === "letsencrypt" ? "Let's Encrypt (معتبر)" : c.cert === "self_signed" ? "خودامضا" : "—";
    facts = `
      <div class="kv-list cover-facts">
        <div><span class="k">وضعیت</span><span class="v">${c.running ? `<span class="status ok">فعال</span>` : `<span class="status bad">سایت پوششی بالا نیست</span>`}</span></div>
        <div><span class="k">نمایش</span><span class="v">${site}</span></div>
        <div><span class="k">گواهی</span><span class="v">${cert}</span></div>
      </div>
      ${c.error ? `<div class="alert bad" style="margin-top:12px">${ic("alert")}<span dir="auto">${esc(c.error)}</span></div>` : ""}
      <div class="row cover-acts">
        <a class="btn sm" href="${esc(c.url)}" target="_blank" rel="noopener">${ic("external")} امتحان در مرورگر</a>
        ${c.cert === "self_signed" ? `<span class="hint" style="margin:0">مرورگر برای گواهی خودامضا هشدار می‌دهد؛ برای گواهی معتبر، دامنه به سرور وصل کنید و نصب را با <code>--domain</code> اجرا کنید.</span>` : ""}
      </div>`;
  }
  $("#cover-view").innerHTML = browser + facts;
}

$("#cover-switch").addEventListener("change", async (ev) => {
  const sw = ev.target;
  const on = sw.checked;
  if (!(await confirmDialog({
    title: on ? "پوشش HTTPS روشن شود؟" : "پوشش HTTPS خاموش شود؟",
    message: "سرویس OpenVPN TCP چند ثانیه راه‌اندازی مجدد می‌شود و کاربرانی که با TCP وصل‌اند خودکار دوباره وصل می‌شوند.",
    ok: on ? "روشن کن" : "خاموش کن", danger: false,
  }))) { sw.checked = !on; return; }
  sw.disabled = true;
  $("#cover-view").classList.add("busy");
  try {
    conn = await api("/api/settings/connection/cover", { method: "POST", body: { enabled: on } });
    toast(on ? "پوشش HTTPS روشن شد" : "پوشش HTTPS خاموش شد");
  } catch (e) {
    toast(e.message, "error");
    sw.checked = !on;
  } finally {
    $("#cover-view").classList.remove("busy");
    renderCover();
  }
});

/* key mode */
function renderKeys() {
  const k = conn.keys;
  if (keySel === null) keySel = k.mode;
  $$("#kmodes .kmode").forEach((b) => {
    const m = b.dataset.mode;
    const locked = !conn.wired || (m !== "shared" && !k.supported);
    b.disabled = locked;
    b.classList.toggle("sel", m === keySel);
    b.classList.toggle("current", m === k.mode);
    b.setAttribute("aria-checked", m === keySel);
    const tag = b.querySelector(".cur");
    if (m === k.mode && !tag) b.insertAdjacentHTML("beforeend", `<span class="cur">${ic("check")} فعلی</span>`);
    if (m !== k.mode && tag) tag.remove();
  });
  $("#keys-apply").disabled = keySel === k.mode;

  let note = "";
  if (!conn.wired) {
    note = `<div class="alert warn">${ic("alert")}<span>کانفیگ سرور OpenVPN قدیمی است؛ اول روی سرور <code>waze-panel update</code> را بزنید.</span></div>`;
  } else if (!k.supported) {
    note = `<div class="alert warn">${ic("alert")}<span>کلید جدا به OpenVPN نسخه‌ی ۲٫۵ یا جدیدتر نیاز دارد؛ این سرور ${k.openvpn ? `<bdi class="mono">${esc(k.openvpn)}</bdi>` : "نسخه‌ی نامشخص"} دارد.</span></div>`;
  } else if (k.mode === "shared") {
    note = `<p class="keys-say">${ic("info")}<span>از «انتقالی» شروع کنید: اتصال هیچ‌کس قطع نمی‌شود و هر کاربری که کانفیگش را دوباره دانلود کند، کلید شخصی می‌گیرد.</span></p>`;
  } else {
    const p = k.users ? Math.round((k.users_on_personal_key / k.users) * 100) : 100;
    const left = k.users - k.users_on_personal_key;
    note = `
      <div class="keys-progress">
        <div class="kp-top"><span>کاربرانی که با کلید شخصی وصل شده‌اند</span><b><bdi>${fa(k.users_on_personal_key)} از ${fa(k.users)}</bdi></b></div>
        <div class="bar"><span style="width:${p}%"></span></div>
        <div class="hint">${k.mode === "compat"
          ? (left > 0 ? `${fa(left)} کاربر هنوز فایل قدیمی دارند یا وصل نشده‌اند. وقتی همه منتقل شدند، «کلید جدا» را بزنید تا کلید مشترک بسته شود.` : "همه منتقل شده‌اند؛ حالا می‌توانید «کلید جدا» را بزنید تا کلید مشترک بسته شود.")
          : (left > 0 ? `${fa(left)} کاربر هنوز با کلید شخصی وصل نشده‌اند؛ باید کانفیگ را از لینک اشتراکشان دوباره دانلود کنند.` : "همه‌ی کاربران با کلید شخصی وصل می‌شوند.")}</div>
      </div>`;
  }
  $("#keys-note").innerHTML = note;
  $("#shared-key-row").classList.toggle("hide", k.mode === "shared");
}

$("#kmodes").addEventListener("click", (ev) => {
  const b = ev.target.closest(".kmode");
  if (!b || b.disabled) return;
  keySel = b.dataset.mode;
  renderKeys();
});

$("#keys-apply").addEventListener("click", async (ev) => {
  const k = conn.keys;
  const left = k.users - k.users_on_personal_key;
  const label = { shared: "مشترک", compat: "انتقالی", per_user: "کلید جدا" }[keySel];
  let message = "هر دو سرویس OpenVPN راه‌اندازی مجدد می‌شوند؛ همه‌ی کاربران چند ثانیه قطع و خودکار دوباره وصل می‌شوند.";
  if (keySel === "per_user" && left > 0) message += ` ${fa(left)} کاربر هنوز با کلید شخصی وصل نشده‌اند و تا کانفیگ جدید دانلود نکنند، وصل نمی‌شوند.`;
  if (keySel === "shared") message += " کانفیگ‌هایی که از این به بعد دانلود شوند دوباره کلید مشترک می‌گیرند.";
  if (!(await confirmDialog({ title: `حالت کلید «${label}» شود؟`, message, ok: "اعمال", danger: keySel === "per_user" && left > 0 }))) return;
  await withBusy(ev.currentTarget, async () => {
    try {
      conn = await api("/api/settings/connection/keys", { method: "POST", body: { mode: keySel } });
      toast(`حالت کلید: ${label}`);
    } catch (e) {
      toast(e.message, "error");
    }
    keySel = conn.keys.mode;
    renderKeys();
  });
});

$("#shared-key").addEventListener("click", async (ev) => {
  if (!(await confirmDialog({
    title: "کلید کانفیگ مشترک عوض شود؟",
    message: "فایل‌های مشترکی که تا الان پخش شده دیگر وصل نمی‌شوند؛ فایل جدید را دانلود و دوباره منتشر کنید. کاربرانی که فایل شخصی خودشان را دارند دست نمی‌خورند.",
    ok: "کلید جدید",
  }))) return;
  await withBusy(ev.currentTarget, async () => {
    try {
      await api("/api/settings/shared-key", { method: "POST" });
      toast("کلید جدید ساخته شد؛ فایل مشترک را دوباره دانلود کنید");
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

async function loadConnection() {
  try {
    conn = await api("/api/settings/connection");
    portDraft = { udp: [...conn.ports.udp.extra], tcp: [...conn.ports.tcp.extra] };
    renderPorts();
    renderCover();
    renderKeys();
  } catch (e) {
    toast(e.message, "error");
  }
}
loadConnection();
