// Xray page: core status, inbounds (create from a preset, edit, order,
// enable, delete).
"use strict";

let xd = null;            // /api/xray
let editing = null;       // inbound being edited, or null when creating
let draft = null;         // {protocol, transport, security, preset}

const PROTO_NAME = { vless: "VLESS", vmess: "VMess", trojan: "Trojan", shadowsocks: "Shadowsocks" };
const PROTO_TAG = { vless: "VL", vmess: "VM", trojan: "TR", shadowsocks: "SS" };
const TRANSPORT_NAME = { raw: "TCP", ws: "WebSocket", xhttp: "XHTTP", grpc: "gRPC", httpupgrade: "HTTPUpgrade" };
const SECURITY_NAME = { none: "بدون TLS", tls: "TLS", reality: "Reality" };
const STATE = {
  running: { dot: "ok", label: "در حال اجرا" },
  stopped: { dot: "bad", label: "متوقف" },
  missing: { dot: "", label: "نصب نشده" },
};
const PRESET_ICON = {
  "vless-reality": "shield-check", "vless-xhttp-reality": "bolt", "vless-ws": "globe",
  "vmess-ws": "devices", "trojan-tls": "lock", shadowsocks: "key",
};

const randomPath = () => "/" + Array.from(crypto.getRandomValues(new Uint8Array(5)), (b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");

/* ------------------------------------------------------------- status */
function renderStats() {
  const st = STATE[xd.state] || STATE.stopped;
  $("#x-missing").classList.toggle("hide", xd.state !== "missing");
  $("#xs-core").innerHTML = xd.version ? `<bdi class="num">${esc(xd.version)}</bdi>` : "—";
  $("#xs-core-sub").innerHTML = `<span class="dot ${st.dot}"></span>${st.label}`;

  const on = xd.inbounds.filter((i) => i.enabled);
  $("#xs-inbounds").textContent = fa(on.length);
  $("#xs-inbounds-sub").textContent = xd.inbounds.length > on.length ? `${fa(xd.inbounds.length - on.length)} خاموش` : xd.inbounds.length ? "همه روشن" : "هنوز ورودی ندارید";

  $("#xs-users").textContent = fa(xd.users_active);
  $("#xs-users-sub").textContent = xd.users > xd.users_active ? `از ${fa(xd.users)} کاربر؛ بقیه غیرفعال یا تمام‌شده` : "همه‌ی کاربران فعال";

  $("#xs-online").textContent = fa(xd.online);
  $("#xs-online-sub").innerHTML = xd.online ? `${bytesHtml(xd.online_bytes)} در این جلسه` : "کسی روی Xray نیست";
  $("#x-new").disabled = false;
}

function detail(ib) {
  const o = ib.options;
  const bits = [];
  if (ib.security === "reality") bits.push(`<span class="mono ltr">${esc(o.sni)}</span>`);
  if (ib.security === "tls") bits.push(ib.cert === "letsencrypt" ? `<span class="mono ltr">${esc(o.sni || o.host || "")}</span>` : "گواهی خودامضا");
  if (o.path) bits.push(`<span class="mono ltr">${esc(o.path)}</span>`);
  if (o.service_name) bits.push(`<span class="mono ltr">${esc(o.service_name)}</span>`);
  if (o.method) bits.push(`<span class="mono ltr">${esc(o.method.replace("2022-blake3-", ""))}</span>`);
  if (o.link_address) bits.push(`${ic("link")}<span class="mono ltr">${esc(o.link_address)}</span>`);
  else if (o.relay !== false && xd.relays) bits.push(`${ic("route")}${fa(xd.relays)} سرور واسط`);
  return bits.join("<span>·</span>");
}

function inboundState(ib) {
  if (!ib.enabled) return `<span class="status">خاموش</span>`;
  if (xd.state === "missing") return `<span class="status warn">منتظر نصب Xray</span>`;
  if (xd.state !== "running" || ib.listening === false) return `<span class="status bad">روی پورت بالا نیامده</span>`;
  return `<span class="status ok">فعال</span>`;
}

function rowHtml(ib, i, n) {
  return `<div class="list-row xrow ${ib.enabled ? "" : "off"}" data-id="${ib.id}">
    <span class="xproto p-${ib.protocol}" title="${PROTO_NAME[ib.protocol]}">${PROTO_TAG[ib.protocol]}</span>
    <div class="grow info">
      <div class="row" style="gap:8px;flex-wrap:wrap"><span class="name">${esc(ib.name)}</span>${inboundState(ib)}</div>
      <div class="meta"><span class="ltr">${esc(ib.label)}</span><span>·</span><span>پورت <bdi class="num">${faPort(ib.port)}</bdi></span>${detail(ib) ? `<span>·</span>${detail(ib)}` : ""}</div>
    </div>
    <div class="end hide-mobile" title="کاربرانی که این ورودی را دارند">${fa(ib.users || 0)}<small>کاربر</small></div>
    <div class="end hide-mobile" title="ترافیک این ورودی">${bytesHtml(ib.traffic)}<small>ترافیک</small></div>
    <div class="relay-ops">
      <button class="btn icon sm ghost" type="button" data-x="up" title="بالاتر در لیست" ${i === 0 ? "disabled" : ""}>${ic("arrow-up")}</button>
      <button class="btn icon sm ghost" type="button" data-x="down" title="پایین‌تر در لیست" ${i === n - 1 ? "disabled" : ""}>${ic("arrow-down")}</button>
      <button class="btn icon sm ghost" type="button" data-x="edit" title="ویرایش">${ic("edit")}</button>
      <label class="switch" title="${ib.enabled ? "خاموش کردن" : "روشن کردن"}"><input type="checkbox" data-x="toggle" ${ib.enabled ? "checked" : ""} aria-label="روشن بودن ${esc(ib.name)}" /><span></span></label>
      <button class="btn icon sm ghost danger" type="button" data-x="delete" title="حذف">${ic("trash")}</button>
    </div>
  </div>`;
}

function renderList() {
  const list = xd.inbounds;
  if (!list.length) {
    $("#x-list").innerHTML = `<div class="empty">
      <b>هنوز ورودی ندارید</b>
      با اولین ورودی، همه‌ی کاربران فعال لینک Xray می‌گیرند. برای شروع Reality را پیشنهاد می‌کنیم.
      <div><button class="btn primary" type="button" data-x="new">${ic("plus")} ساخت اولین ورودی</button></div>
    </div>`;
    return;
  }
  $("#x-list").innerHTML = list.map((ib, i) => rowHtml(ib, i, list.length)).join("");
}

async function load() {
  try {
    xd = await api("/api/xray");
    renderStats();
    renderList();
  } catch (e) {
    toast(e.message, "error");
  }
}

const APPLIED = {
  restarted: "Xray با تنظیم جدید راه‌اندازی شد",
  live: "اعمال شد",
  same: "اعمال شد",
  written: "ذخیره شد؛ بعد از نصب Xray اعمال می‌شود",
};

/* -------------------------------------------------------- list actions */
$("#x-list").addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-x]");
  if (!el || el.dataset.x === "toggle") return;
  if (el.dataset.x === "new") return openPicker();
  const row = el.closest(".xrow");
  const id = +row.dataset.id;
  const ib = xd.inbounds.find((x) => x.id === id);
  const act = el.dataset.x;
  try {
    if (act === "edit") return openForm(ib);
    if (act === "up" || act === "down") {
      const ids = xd.inbounds.map((x) => x.id);
      const i = ids.indexOf(id);
      const j = act === "up" ? i - 1 : i + 1;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await withBusy(el, () => api("/api/xray/inbounds/order", { method: "POST", body: { ids } }));
      return load();
    }
    if (act === "delete") {
      if (!(await confirmDialog({
        title: `ورودی «${ib.name}» حذف شود؟`,
        message: "لینک‌های این ورودی برای همه‌ی کاربران از کار می‌افتد و از اشتراکشان حذف می‌شود.",
        ok: "حذف ورودی",
      }))) return;
      await api(`/api/xray/inbounds/${id}`, { method: "DELETE" });
      toast("ورودی حذف شد");
      return load();
    }
  } catch (e) {
    toast(e.message, "error");
  }
});

$("#x-list").addEventListener("change", async (ev) => {
  if (ev.target.dataset.x !== "toggle") return;
  const id = +ev.target.closest(".xrow").dataset.id;
  const on = ev.target.checked;
  ev.target.disabled = true;
  try {
    await api(`/api/xray/inbounds/${id}`, { method: "PATCH", body: { enabled: on } });
    toast(on ? "ورودی روشن شد" : "ورودی خاموش شد");
  } catch (e) {
    ev.target.checked = !on;
    toast(e.message, "error");
  }
  load();
});

$("#x-refresh").addEventListener("click", (ev) => withBusy(ev.currentTarget, load));

/* -------------------------------------------------------------- picker */
function openPicker() {
  editing = null;
  $("#x-presets").innerHTML = xd.presets.map((p) => `
    <button type="button" class="kmode xpreset" data-preset="${p.id}">
      <span class="kt">${ic(PRESET_ICON[p.id] || "layers")} ${esc(p.label)}${p.badge ? ` <span class="badge ${p.id === "vless-reality" ? "green" : ""}">${esc(p.badge)}</span>` : ""}</span>
      <span class="ks">${esc(p.desc)}</span>
    </button>`).join("") + `
    <button type="button" class="kmode xpreset" data-preset="custom">
      <span class="kt">${ic("settings")} سفارشی</span>
      <span class="ks">پروتکل، انتقال و امنیت را خودتان انتخاب کنید (gRPC، HTTPUpgrade، ...).</span>
    </button>`;
  $("#x-pick-view").classList.remove("hide");
  $("#x-form").classList.add("hide");
  openOverlay($("#x-modal"));
}

$("#x-new").addEventListener("click", openPicker);

$("#x-presets").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-preset]");
  if (!b) return;
  const id = b.dataset.preset;
  const p = xd.presets.find((x) => x.id === id);
  draft = p
    ? { protocol: p.protocol, transport: p.transport, security: p.security, preset: p }
    : { protocol: "vless", transport: "grpc", security: "reality", preset: null };
  openForm(null);
});

/* ---------------------------------------------------------------- form */
const F = (name) => $("#x-form").elements[name];

function fillSelect(sel, values, current, names = null) {
  sel.innerHTML = values.map((v) => `<option value="${v}" ${v === current ? "selected" : ""}>${names ? names[v] : v}</option>`).join("");
  if (!values.includes(current)) sel.value = values[0];
}

function syncCustom() {
  const allowed = xd.allowed;
  fillSelect(F("protocol"), Object.keys(allowed), draft.protocol, PROTO_NAME);
  draft.protocol = F("protocol").value;
  fillSelect(F("transport"), Object.keys(allowed[draft.protocol]), draft.transport, TRANSPORT_NAME);
  draft.transport = F("transport").value;
  fillSelect(F("security"), allowed[draft.protocol][draft.transport], draft.security, SECURITY_NAME);
  draft.security = F("security").value;
}

function showFields() {
  const { protocol, transport, security } = draft;
  const on = {
    custom: !editing && !draft.preset,
    reality: security === "reality",
    "reality-edit": security === "reality" && !!editing,
    tls: security === "tls",
    path: ["ws", "xhttp", "httpupgrade"].includes(transport),
    cdn: ["ws", "httpupgrade"].includes(transport) && security === "none",
    grpc: transport === "grpc",
    ss: protocol === "shadowsocks",
    fp: security !== "none",
  };
  $$("#x-form [data-show]").forEach((el) => el.classList.toggle("hide", !on[el.dataset.show]));
  const title = editing ? `ویرایش «${editing.name}»` : draft.preset ? draft.preset.label : "ورودی سفارشی";
  $("#x-form-title").textContent = title;
  $("#x-form-sub").textContent = editing
    ? `${editing.label} · تغییرات همان لحظه روی لینک‌های همه‌ی کاربران اعمال می‌شود.`
    : draft.preset ? draft.preset.desc : "ترکیب‌هایی که Xray پشتیبانی می‌کند در لیست‌ها هست.";
}

// CDN without TLS needs a port Cloudflare passes as plain HTTP; everything
// else looks best on an HTTPS-like port.
const portKind = () => (draft.security === "none" && ["ws", "httpupgrade", "xhttp"].includes(draft.transport) ? "http" : "tls");

function portChips() {
  const current = +F("port").value;
  const list = (xd.suggest_ports[portKind()] || []).filter((p) => !editing || p !== editing.port);
  $("#xf-ports").innerHTML = list.length
    ? `<span>آزاد:</span>` + list.map((p) => `<button type="button" class="chip ${p === current ? "active" : ""}" data-port="${p}">${faPort(p)}</button>`).join("")
    : "";
}

function openForm(ib) {
  editing = ib;
  if (ib) draft = { protocol: ib.protocol, transport: ib.transport, security: ib.security, preset: null };
  const form = $("#x-form");
  form.reset();
  $("#xf-adv").open = false;
  $("#xf-sni-hint").className = "hint";
  $("#xf-sni-hint").textContent = "سایتی خارجی که TLS 1.3 دارد و از داخل ایران باز می‌شود. Reality ظاهر اتصال را شبیه همین سایت می‌کند.";
  $("#xf-targets").innerHTML = xd.reality_targets.map((t) => `<option value="${t}"></option>`).join("");
  fillSelect(F("method"), xd.ss_methods, (ib && ib.options.method) || xd.ss_methods[0]);
  if (!editing && !draft.preset) syncCustom();

  const o = ib ? ib.options : {};
  F("name").value = ib ? ib.name : (draft.preset ? draft.preset.name : customName());
  F("name").dataset.auto = ib || draft.preset ? "" : "1";
  F("port").value = ib ? ib.port : (xd.suggest_ports[portKind()][0] || "");
  F("sni").value = o.sni && ib && ib.security === "reality" ? o.sni : xd.reality_targets[0];
  F("tls_sni").value = ib && ib.security === "tls" ? (o.sni || "") : "";
  F("path").value = o.path || randomPath();
  F("host").value = o.host || "";
  F("service_name").value = o.service_name || randomPath().slice(1);
  F("link_address").value = o.link_address || "";
  F("relay").checked = o.relay !== false;
  F("fingerprint").value = o.fingerprint || "chrome";
  F("short_id").value = o.short_id || "";
  $("#xf-pbk").value = o.public_key || "";
  $("#xf-save").textContent = ib ? "ذخیره" : "ساخت ورودی";
  $("#xf-back").classList.toggle("hide", !!ib);
  showFields();
  portChips();
  $("#x-pick-view").classList.add("hide");
  form.classList.remove("hide");
  if (!$("#x-modal").classList.contains("open")) openOverlay($("#x-modal"));
  $("#x-modal").scrollTop = 0;
}

$("#xf-back").addEventListener("click", openPicker);

const customName = () => `${PROTO_NAME[draft.protocol]} ${TRANSPORT_NAME[draft.transport]}`.slice(0, 40);

$("#x-form").addEventListener("change", (ev) => {
  const n = ev.target.name;
  if (!["protocol", "transport", "security"].includes(n)) return;
  const kind = portKind();
  draft[n] = ev.target.value;
  syncCustom();
  showFields();
  if (F("name").dataset.auto) F("name").value = customName();
  // a suggested port of the other kind: switch to one that fits
  const port = +F("port").value;
  if (kind !== portKind() && (!port || xd.suggest_ports[kind].includes(port))) F("port").value = xd.suggest_ports[portKind()][0] || "";
  portChips();
});
$("#xf-name").addEventListener("input", (ev) => { ev.target.dataset.auto = ""; });

$("#xf-ports").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-port]");
  if (!b) return;
  F("port").value = b.dataset.port;
  portChips();
});
$("#xf-port").addEventListener("input", portChips);

$("#xf-pbk-copy").addEventListener("click", (ev) => copyText($("#xf-pbk").value, "کلید عمومی کپی شد", ev.currentTarget));

$("#xf-check").addEventListener("click", async (ev) => {
  const sni = F("sni").value.trim();
  const hint = $("#xf-sni-hint");
  if (!sni) return;
  await withBusy(ev.currentTarget, async () => {
    try {
      const r = await api("/api/xray/check-target", { method: "POST", body: { sni } });
      if (r.ok) {
        hint.className = "hint ok";
        hint.innerHTML = `${ic("check-circle")} سرور به این سایت می‌رسد: TLS 1.3${r.h2 ? " و HTTP/2" : ""} · <bdi class="num">${fa(r.ms)}ms</bdi>`;
      } else {
        hint.className = "hint bad";
        hint.textContent = r.error === "no_tls13"
          ? "این سایت TLS 1.3 ندارد؛ برای Reality مناسب نیست."
          : r.error === "unreachable" ? "سرور به این سایت نمی‌رسد؛ یکی دیگر انتخاب کنید." : "دست‌دادن TLS با این سایت انجام نشد.";
      }
    } catch (e) {
      hint.className = "hint bad";
      hint.textContent = e.message;
    }
  });
});

function collectOptions() {
  const { transport, security, protocol } = draft;
  const o = { link_address: F("link_address").value.trim(), relay: F("relay").checked };
  if (["ws", "xhttp", "httpupgrade"].includes(transport)) {
    o.path = F("path").value.trim() || "/";
    o.host = F("host").value.trim();
  }
  if (transport === "grpc") o.service_name = F("service_name").value.trim();
  if (security === "reality") {
    o.sni = F("sni").value.trim();
    o.short_id = F("short_id").value.trim();
  }
  if (security === "tls") o.sni = F("tls_sni").value.trim();
  if (security !== "none") o.fingerprint = F("fingerprint").value;
  if (protocol === "shadowsocks") o.method = F("method").value;
  return o;
}

$("#x-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = F("name").value.trim();
  const port = +F("port").value;
  if (!name) return toast("نام را بنویسید", "error");
  if (!port) return toast("پورت را بنویسید", "error");
  if (draft.security === "reality" && !F("sni").value.trim()) return toast("سایت هدف را بنویسید", "error");
  const btn = $("#xf-save");
  await withBusy(btn, async () => {
    try {
      let r;
      if (editing) {
        if (editing.protocol === "shadowsocks" && editing.options.method !== F("method").value &&
            !(await confirmDialog({ title: "رمزنگاری عوض شود؟", message: "همه‌ی لینک‌های Shadowsocks این ورودی عوض می‌شوند و کاربران باید اشتراک را به‌روز کنند.", ok: "تغییر رمزنگاری", danger: false }))) return;
        r = await api(`/api/xray/inbounds/${editing.id}`, { method: "PATCH", body: { name, port, options: collectOptions() } });
      } else {
        r = await api("/api/xray/inbounds", {
          method: "POST",
          body: { name, port, protocol: draft.protocol, transport: draft.transport, security: draft.security, options: collectOptions() },
        });
      }
      closeOverlay($("#x-modal"));
      toast(editing ? APPLIED[r.applied] || "ذخیره شد" : "ورودی ساخته شد؛ لینک‌هایش به اشتراک کاربران اضافه شد");
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

load();
setInterval(() => { if (!document.hidden && !$("#x-modal").classList.contains("open")) load(); }, 15000);
