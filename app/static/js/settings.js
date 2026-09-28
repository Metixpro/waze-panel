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
  if (!h) return `<span class="rstat"><i></i>بررسی نشده</span>`;
  if (h.ok) return `<span class="rstat ok"><i></i>سالم · <bdi>${fa(h.ms)}ms</bdi></span>`;
  return `<span class="rstat bad"><i></i>${esc(RELAY_ERR[h.error] || h.error || "خطا")}</span>`;
}

function relayRow(r, i, n) {
  return `<div class="relay ${r.enabled ? "" : "off"}" data-id="${r.id}">
    <div class="rank">${fa(i + 1)}</div>
    <div class="info">
      <div class="nm">${esc(r.name)} ${r.enabled ? relayStat(relayData.health[r.id]) : `<span class="rstat"><i></i>غیرفعال</span>`}</div>
      <div class="ad"><bdi class="mono">${esc(r.address)}</bdi> <span class="muted">· UDP ${fa(r.udp_port)} · TCP ${fa(r.tcp_port)}</span></div>
    </div>
    <div class="ops">
      <button class="btn icon sm ghost" data-r="up" title="اولویت بالاتر" ${i === 0 ? "disabled" : ""}>${ic("arrow-up")}</button>
      <button class="btn icon sm ghost" data-r="down" title="اولویت پایین‌تر" ${i === n - 1 ? "disabled" : ""}>${ic("arrow-down")}</button>
      <button class="btn icon sm ghost" data-r="setup" title="دستور نصب روی سرور ایران">${ic("terminal")}</button>
      <button class="btn icon sm ghost" data-r="edit" title="ویرایش">${ic("edit")}</button>
      <label class="switch" title="${r.enabled ? "غیرفعال کردن" : "فعال کردن"}"><input type="checkbox" data-r="toggle" ${r.enabled ? "checked" : ""} /><span></span></label>
      <button class="btn icon sm ghost danger-text" data-r="delete" title="حذف">${ic("trash")}</button>
    </div>
  </div>`;
}

function directRow() {
  const d = relayData.direct;
  const any = relayData.relays.length > 0;
  const on = !any || relayData.options.fallback_direct;
  const tag = !any ? "" : on ? `<span class="rstat"><i></i>پشتیبان آخر</span>` : `<span class="rstat"><i></i>خاموش</span>`;
  return `<div class="relay direct ${on ? "" : "off"}">
    <div class="rank">${ic("server")}</div>
    <div class="info">
      <div class="nm">اتصال مستقیم به همین سرور ${tag}</div>
      <div class="ad"><bdi class="mono">${esc(d.address)}</bdi> <span class="muted">· UDP ${fa(d.udp_port)} · TCP ${fa(d.tcp_port)}</span></div>
    </div>
  </div>`;
}

function renderRelays() {
  const rs = relayData.relays;
  $("#relay-list").innerHTML = (rs.length
    ? rs.map((r, i) => relayRow(r, i, rs.length)).join("")
    : `<div class="relay-empty">${ic("route")}<div><b>هنوز سرور واسطی اضافه نشده</b><span>فعلا کاربران مستقیم به همین سرور وصل می‌شوند.</span></div></div>`) + directRow();
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
  const id = +b.closest(".relay").dataset.id;
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
  const id = +t.closest(".relay").dataset.id;
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
  if (b) copyText($(b.dataset.copyFrom).textContent, "دستور کپی شد");
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
  nav.addEventListener("click", (ev) => {
    const a = ev.target.closest("a");
    if (!a) return;
    ev.preventDefault();
    haptic();
    document.getElementById(a.getAttribute("href").slice(1)).scrollIntoView({ behavior: "smooth", block: "start" });
  });
  if (location.hash && byId[location.hash.slice(1)]) setTimeout(() => document.getElementById(location.hash.slice(1)).scrollIntoView({ block: "start" }), 300);
})();
