// Users page: table, filters, detail drawer, create flow.
"use strict";

let users = [];
let filter = "all";
let currentId = null;
let currentUser = null;
let userChart = null;
let lastCreated = null;

const FILTERS = {
  all: () => true,
  online: (u) => u.online,
  active: (u) => u.status === "active",
  ending: (u) => u.ending_soon,
  expired: (u) => u.status === "expired",
  over_quota: (u) => u.status === "over_quota",
  disabled: (u) => u.status === "disabled",
};

const SORTS = {
  new: (a, b) => (b.created_at || "").localeCompare(a.created_at || ""),
  usage: (a, b) => b.data_used_bytes - a.data_used_bytes,
  expiry: (a, b) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9),
  seen: (a, b) => (b.online - a.online) || (b.last_connected_at || "").localeCompare(a.last_connected_at || ""),
  name: (a, b) => a.username.localeCompare(b.username),
};

const PROTO_LABEL = { udp: "UDP", tcp: "TCP", xray: "Xray" };
const protos = (u) => u.online_protos.map((p) => PROTO_LABEL[p] || p.toUpperCase()).join(" + ");
const shortDate = (iso) => jDate(iso, { month: "long", day: "numeric" });

/* ------------------------------------------------------------ list view */

function usageCell(u) {
  const used = u.data_used_bytes || 0;
  if (!u.data_limit_bytes) {
    return `<div class="t"><span><b>${bytesHtml(used)}</b></span><span>نامحدود</span></div><div class="bar thin inf"><span></span></div>`;
  }
  const p = pct(used, u.data_limit_bytes);
  return `<div class="t"><span><b>${bytesHtml(used)}</b> از ${bytesHtml(u.data_limit_bytes)}</span><span class="num">${fa(p)}٪</span></div>
          <div class="bar thin ${levelBar(p)}"><span style="width:${Math.max(p, 2)}%"></span></div>`;
}

function expiryCell(u) {
  if (u.days_left === null || u.days_left === undefined) return `<span class="dim">بدون محدودیت</span>`;
  const warn = u.days_left <= 3;
  return `<span style="${warn ? "color:var(--amber);font-weight:600" : ""}">${daysLeftLabel(u.days_left)}</span><span class="sub">تا ${shortDate(u.expire_at)}</span>`;
}

// Clients behind a relay reach us with the relay's IP: name the relay instead.
function whereFrom(u) {
  if (u.last_via) return `<span class="via">${ic("route")}${esc(u.last_via)}</span>`;
  return u.last_ip ? `<span class="mono ltr">${esc(u.last_ip)}</span>` : "";
}

function seenCell(u) {
  const main = u.online
    ? `<span style="color:var(--green)">الان · ${protos(u)}${u.devices_online > 1 ? ` · ${fa(u.devices_online)} دستگاه` : ""}</span>`
    : `<span>${relTime(u.last_connected_at)}</span>`;
  return `${main}<span class="sub">${whereFrom(u) || "&nbsp;"}</span>`;
}

function noteLine(u) {
  const bits = [];
  if (!u.openvpn_enabled) bits.push(`<span class="svc-tag">${ic("layers")}فقط Xray</span>`);
  else if (!u.xray_enabled) bits.push(`<span class="svc-tag">${ic("shield")}فقط OpenVPN</span>`);
  if (u.openvpn_enabled && u.auth_mode && u.auth_mode !== "cert") bits.push(`<span class="pw-tag">${ic("lock")}${AUTH[u.auth_mode].label}</span>`);
  bits.push(u.note ? esc(u.note) : `ساخته شده ${relTime(u.created_at)}`);
  return bits.join(" · ");
}

function rowHtml(u) {
  const state = u.online && u.status === "active" ? `<span class="status online">آنلاین</span>` : statusBadge(u.status);
  return `<div class="urow" data-id="${u.id}" tabindex="0">
    <div class="who">
      ${avatar(u.username, u.online)}
      <div class="grow">
        <div class="n">${esc(u.username)}</div>
        <div class="note">${noteLine(u)}</div>
      </div>
    </div>
    <div class="cell c-status">${state}</div>
    <div class="usage">${usageCell(u)}</div>
    <div class="cell c-exp">${expiryCell(u)}</div>
    <div class="cell c-seen">${seenCell(u)}</div>
    <div class="acts">
      <button class="btn icon sm ghost" type="button" data-copy="${esc(u.sub_link)}" data-copy-msg="لینک اشتراک کپی شد" title="کپی لینک اشتراک">${ic("link")}</button>
      ${u.openvpn_enabled
        ? `<a class="btn icon sm ghost js-stop" href="/api/users/${u.id}/config/udp" title="دانلود کانفیگ UDP">${ic("download")}</a>`
        : `<button class="btn icon sm ghost" type="button" data-copy="${esc(u.sub_link)}/xray" data-copy-msg="لینک اشتراک Xray کپی شد" title="کپی اشتراک Xray">${ic("layers")}</button>`}
      <label class="switch js-stop" title="${u.enabled ? "غیرفعال کردن" : "فعال کردن"}">
        <input type="checkbox" class="js-toggle" data-id="${u.id}" ${u.enabled ? "checked" : ""} aria-label="فعال بودن ${esc(u.username)}" /><span></span>
      </label>
    </div>
  </div>`;
}

function renderList() {
  const q = $("#q").value.trim().toLowerCase();
  const sort = SORTS[$("#sort").value] || SORTS.new;
  const rows = users
    .filter(FILTERS[filter])
    .filter((u) => !q || u.username.toLowerCase().includes(q) || (u.note || "").toLowerCase().includes(q))
    .sort(sort);

  const list = $("#users-list");
  if (!users.length) {
    list.innerHTML = `<div class="empty"><b>هنوز کاربری ندارید</b>اولین کاربر را بسازید و لینک اشتراکش را برایش بفرستید.
      <div><button class="btn primary" type="button" onclick="openCreate()">${ic("plus")} ساخت اولین کاربر</button></div></div>`;
    return;
  }
  if (!rows.length) {
    list.innerHTML = `<div class="empty"><b>کاربری با این مشخصات نیست</b>فیلتر یا عبارت جستجو را تغییر دهید.</div>`;
    return;
  }
  list.innerHTML = rows.map(rowHtml).join("");
}

function renderCounts() {
  for (const key of Object.keys(FILTERS)) {
    const el = $(`[data-c="${key}"]`);
    if (el) el.textContent = fa(users.filter(FILTERS[key]).length);
  }
  const online = users.filter((u) => u.online).length;
  setNavOnline(online);
  $("#users-sub").innerHTML = `${fa(users.length)} کاربر<span class="sep"></span><span style="color:var(--green)">${fa(online)} آنلاین</span>`;
}

async function load() {
  try {
    users = await api("/api/users");
    renderCounts();
    renderList();
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------------------------------------------------------------- drawer */

const STATUS_NOTE = {
  disabled: "این کاربر غیرفعال است و نمی‌تواند وصل شود.",
  expired: "اعتبار این کاربر تمام شده؛ برای اتصال دوباره تمدید کنید.",
  over_quota: "حجم این کاربر تمام شده؛ حجم اضافه کنید یا مصرف را صفر کنید.",
  revoked: "گواهی این کاربر باطل شده است.",
};

function drawerHtml(u) {
  const limit = u.data_limit_bytes;
  const p = limit ? pct(u.data_used_bytes, limit) : 0;
  const remaining = limit ? Math.max(0, limit - u.data_used_bytes) : null;
  const total14 = (u.chart_values || []).reduce((a, b) => a + b, 0);
  const alert = u.status !== "active"
    ? `<div class="alert ${u.status === "disabled" ? "" : "warn"}" style="margin-top:12px">${ic("alert")}<span>${STATUS_NOTE[u.status] || ""}</span></div>`
    : "";
  const connection = u.online
    ? `<span class="status online">آنلاین · ${protos(u)}</span>`
    : `<span>${u.last_connected_at ? relTime(u.last_connected_at) : "هنوز وصل نشده"}</span>`;

  return `
  <div class="drawer-head">
    ${avatar(u.username, u.online, "lg")}
    <div class="grow">
      <div class="title"><span class="n">${esc(u.username)}</span>${statusBadge(u.status)}</div>
      <div class="sub">${u.note ? esc(u.note) + " · " : ""}ساخته شده ${jDate(u.created_at)}</div>
    </div>
    <button class="btn icon sm ghost" data-close type="button" title="بستن">${ic("x")}</button>
  </div>

  <div class="dsec">
    <div class="dactions">
      <button class="btn primary" type="button" data-act="share">${ic("send")} ارسال</button>
      <button class="btn" type="button" data-copy="${esc(u.sub_link)}" data-copy-msg="لینک اشتراک کپی شد">${ic("link")} لینک</button>
      ${u.openvpn_enabled ? `<a class="btn" href="/api/users/${u.id}/config/udp">${ic("download")} UDP</a>
      <a class="btn" href="/api/users/${u.id}/config/tcp">${ic("download")} TCP</a>` : ""}
      ${u.xray_enabled ? `<button class="btn" type="button" data-copy="${esc(u.xray_sub)}" data-copy-msg="لینک اشتراک Xray کپی شد">${ic("layers")} Xray</button>` : ""}
    </div>
    ${alert}
  </div>

  <div class="dsec">
    <div class="usage-hero">
      ${ring(limit ? p : 100, { size: 96, stroke: 9, color: limit ? levelColor(p) : null, label: limit ? `<b>${fa(p)}٪</b><small>مصرف</small>` : `<b>∞</b><small>نامحدود</small>` })}
      <div class="grow">
        <div class="usage-big" style="margin-bottom:2px"><b>${bytesHtml(u.data_used_bytes)}</b><span>${limit ? `از ${bytesHtml(limit)}` : "مصرف شده"}</span></div>
        <div class="dim" style="font-size:13px">${remaining === null ? "بدون سقف حجم" : `${bytesHtml(remaining)} باقیمانده`}</div>
        <div class="dim" style="font-size:13px">${u.days_left === null || u.days_left === undefined ? "بدون تاریخ انقضا" : `${daysLeftLabel(u.days_left)} · تا ${shortDate(u.expire_at)}`}</div>
      </div>
    </div>
    <div class="chips" style="margin-top:14px">
      <button class="chip" type="button" data-act="add-days" data-v="7">+۷ روز</button>
      <button class="chip" type="button" data-act="add-days" data-v="30">+۳۰ روز</button>
      <button class="chip" type="button" data-act="add-days" data-v="90">+۹۰ روز</button>
      ${limit ? `<button class="chip" type="button" data-act="add-gb" data-v="10">+۱۰ گیگ</button>
      <button class="chip" type="button" data-act="add-gb" data-v="50">+۵۰ گیگ</button>` : ""}
      <button class="chip" type="button" data-act="reset">صفر کردن مصرف</button>
    </div>
  </div>

  <div class="dsec">
    <div class="dsec-title">۱۴ روز اخیر <span class="muted">مجموع ${bytesHtml(total14)}</span></div>
    <div class="chart-box sm"><canvas id="user-chart" dir="ltr"></canvas></div>
  </div>

  <div class="dsec">
    <div class="dsec-title">اتصال</div>
    <div class="kv-list">
      <div><span class="k">وضعیت</span><span class="v">${connection}</span></div>
      <div><span class="k">${u.last_via ? "از طریق" : "آخرین آی‌پی"}</span><span class="v">${whereFrom(u) || "—"}</span></div>
      <div><span class="k">دستگاه همزمان</span><span class="v">${fa(u.devices_online || 0)} از ${u.max_devices ? fa(u.max_devices) : "نامحدود"}</span></div>
    </div>
  </div>

  ${servicesSection(u)}
  ${u.openvpn_enabled ? loginSection(u) : ""}
  ${u.openvpn_enabled ? keySection(u) : ""}

  <div class="dsec">
    <div class="dsec-title">لینک اشتراک</div>
    <div class="input-group">
      <input type="text" class="mono" dir="ltr" readonly value="${esc(u.sub_link)}" aria-label="لینک اشتراک" />
      <button class="btn" type="button" data-copy="${esc(u.sub_link)}" data-copy-msg="لینک اشتراک کپی شد" title="کپی">${ic("copy")}</button>
    </div>
    <div class="row" style="margin-top:10px; gap:8px">
      <a class="btn sm" href="${esc(u.sub_link)}" target="_blank" rel="noopener">${ic("external")} باز کردن صفحه</a>
      <button class="btn sm" type="button" data-act="regen">${ic("refresh")} لینک جدید</button>
    </div>
    <details class="fold bare" style="margin-top:14px">
      <summary>نمایش QR ${ic("chevron-down")}</summary>
      <div class="body"><div class="qr-box" id="drawer-qr"></div></div>
    </details>
  </div>

  ${xraySection(u)}

  <form class="dsec" id="edit-form" autocomplete="off">
    <div class="dsec-title">ویرایش</div>
    <div class="field">
      <label for="e-note">یادداشت</label>
      <input id="e-note" type="text" name="note" maxlength="200" value="${esc(u.note || "")}" />
    </div>
    <div class="field-row">
      <div class="field">
        <label for="e-gb">سقف حجم (گیگ)</label>
        <input id="e-gb" type="number" name="gb" min="0" step="0.5" placeholder="نامحدود" value="${limit ? +(limit / 1024 ** 3).toFixed(2) : ""}" />
      </div>
      <div class="field">
        <label for="e-days">اعتبار (روز از امروز)</label>
        <input id="e-days" type="number" name="days" min="1" step="1" placeholder="نامحدود" value="${u.days_left ?? ""}" />
      </div>
    </div>
    <div class="field">
      <label for="e-dev">حداکثر دستگاه همزمان</label>
      <input id="e-dev" type="number" name="devices" min="0" max="100" step="1" placeholder="نامحدود" value="${u.max_devices || ""}" />
      <div class="hint">خالی یعنی نامحدود. با پر شدن سقف دستگاه، اتصال جدیدتر می‌ماند و قدیمی‌تر قطع می‌شود.</div>
    </div>
    <button class="btn primary" type="submit">ذخیره تغییرات</button>
  </form>

  <div class="dsec">
    <div class="setting-row">
      <div><div class="t">فعال</div><div class="s">با غیرفعال کردن، اتصال فعلی همان لحظه قطع می‌شود.</div></div>
      <label class="switch"><input type="checkbox" data-act="toggle" ${u.enabled ? "checked" : ""} aria-label="فعال بودن کاربر" /><span></span></label>
    </div>
    <div class="setting-row">
      <div><div class="t">حذف کاربر</div><div class="s">گواهی باطل می‌شود و کانفیگ‌هایش دیگر کار نمی‌کنند.</div></div>
      <button class="btn sm danger" type="button" data-act="delete">حذف</button>
    </div>
  </div>`;
}

const MODE_HINTS = {
  cert: "با فایل کانفیگ اختصاصی خودش وصل می‌شود؛ رمزی لازم نیست.",
  cert_pass: "فایل اختصاصی و نام کاربری و رمز، هر دو لازم است. اگر فایل لو برود هم بدون رمز کار نمی‌کند.",
  pass: "بدون گواهی اختصاصی: با نام کاربری و رمز خودش وارد می‌شود؛ فایل مشترک «فقط رمز» در تنظیمات هم برایش کار می‌کند.",
};

function loginSection(u) {
  const mode = u.auth_mode || "cert";
  const modes = Object.entries(AUTH).map(([k, a]) =>
    `<button type="button" class="mode ${k === mode ? "active" : ""}" data-act="mode" data-mode="${k}" role="radio" aria-checked="${k === mode}">${a.label}</button>`
  ).join("");
  const pw = mode === "cert" ? "" : `
      <form id="pw-form" autocomplete="off" style="margin-top:14px">
        <label for="pw-input">رمز اتصال</label>
        <div class="input-group two">
          <input id="pw-input" type="password" name="password" dir="ltr" class="mono" maxlength="64" spellcheck="false" autocomplete="off" value="${esc(u.password || "")}" />
          <button class="btn" type="button" data-act="pw-eye" title="نمایش">${ic("eye")}</button>
          <button class="btn" type="button" data-act="pw-copy" title="کپی">${ic("copy")}</button>
        </div>
        <div class="row" style="margin-top:10px; gap:8px">
          <button type="button" class="btn sm" data-act="pw-gen">${ic("dice")} رمز تصادفی</button>
          <button type="submit" class="btn sm primary" id="pw-save" disabled>ذخیره رمز</button>
        </div>
        <div class="hint">با تغییر رمز یا روش ورود، اتصال فعلی کاربر قطع می‌شود.</div>
      </form>`;
  return `
    <div class="dsec">
      <div class="dsec-title">روش ورود OpenVPN</div>
      <div class="modes" role="radiogroup">${modes}</div>
      <div class="hint">${MODE_HINTS[mode]}</div>
      ${pw}
    </div>`;
}

// Personal tls-crypt-v2 key: only once the server hands them out.
function keySection(u) {
  if (!u.tls_mode || u.tls_mode === "shared") return "";
  const state = u.tls_key_seen_at
    ? `آخرین اتصال با این کلید ${relTime(u.tls_key_seen_at)}`
    : u.tls_key ? "هنوز با این کلید وصل نشده" : "با اولین دانلود کانفیگ ساخته می‌شود";
  return `
    <div class="dsec">
      <div class="dsec-title">کلید اتصال OpenVPN</div>
      <div class="setting-row">
        <div><div class="t">کلید شخصی <span class="mono muted" style="font-weight:400">tls-crypt-v2</span></div><div class="s">${state}</div></div>
        <button class="btn sm" type="button" data-act="new-key">${ic("key")} کلید جدید</button>
      </div>
      <div class="hint">اگر فایل کانفیگ این کاربر دست کس دیگری افتاده، کلید جدید بسازید: همه‌ی نسخه‌های قبلی همان لحظه از کار می‌افتند و بقیه‌ی کاربران دست نمی‌خورند.</div>
    </div>`;
}

/* ------------------------------------------------ services and inbounds */
// Which Xray inbounds a user gets: null = all of them, also ones created
// later; else a list of ids. In "all" the chips sit neutral and "all" is lit;
// picking a chip narrows the user down to it, further clicks add/remove.
function inboundChips(list, selected) {
  const all = selected === null || selected === undefined;
  const on = new Set(all ? [] : selected);
  const shown = list.filter((i) => i.enabled || on.has(i.id));
  if (!shown.length) {
    return `<div class="hint" style="margin:0">هنوز ورودی Xray ندارید؛ با ساختن اولین ورودی در صفحه‌ی <a class="link" href="/xray">Xray</a> لینکش خودکار به کاربر اضافه می‌شود.</div>`;
  }
  return `<div class="chips ib-chips" role="group" aria-label="ورودی‌های Xray">
      <button type="button" class="chip all ${all ? "active" : ""}" data-ib="all" aria-pressed="${all}">همه‌ی ورودی‌ها</button>
      ${shown.map((i) => `<button type="button" class="chip ${on.has(i.id) ? "active" : ""}" data-ib="${i.id}" aria-pressed="${on.has(i.id)}" title="${esc(i.label)}">${esc(i.name)}</button>`).join("")}
    </div>
    <div class="hint">${all ? "ورودی‌هایی که بعدا بسازید هم خودکار به این کاربر اضافه می‌شوند." : "فقط ورودی‌های انتخاب‌شده؛ ورودی‌های جدید اضافه نمی‌شوند."}</div>`;
}

// next selection after a click on chip `key` ("all" or an id); undefined = refuse
function nextInbounds(selected, key) {
  if (key === "all") return null;
  const id = +key;
  if (selected === null || selected === undefined) return [id];
  const ids = selected.filter((x) => x !== id);
  if (ids.length === selected.length) ids.push(id);
  if (!ids.length) {
    toast("دست‌کم یک ورودی بماند، یا «همه‌ی ورودی‌ها» را بزنید", "error");
    return undefined;
  }
  return ids;
}

function inboundSummary(u) {
  const enabled = (u.xray_inbound_list || []).filter((i) => i.enabled);
  if (u.xray_inbounds === null) return enabled.length ? `همه‌ی ورودی‌ها (${fa(enabled.length)})، ورودی‌های جدید هم` : "همه‌ی ورودی‌ها، از اولین ورودی";
  const mine = enabled.filter((i) => u.xray_inbounds.includes(i.id));
  return mine.length ? mine.map((i) => i.name).join("، ") : "هیچ ورودی روشنی انتخاب نشده";
}

function servicesSection(u) {
  const ovpn = u.openvpn_enabled;
  const xr = u.xray_enabled;
  return `
    <div class="dsec">
      <div class="dsec-title">سرویس‌ها</div>
      <div class="setting-row">
        <div><div class="t">OpenVPN</div><div class="s">${ovpn ? `UDP و TCP · ورود با ${AUTH[u.auth_mode || "cert"].label}` : "خاموش: فایل‌های کانفیگ این کاربر وصل نمی‌شوند."}</div></div>
        <label class="switch"><input type="checkbox" data-act="svc" data-svc="openvpn_enabled" ${ovpn ? "checked" : ""} aria-label="OpenVPN" /><span></span></label>
      </div>
      <div class="setting-row">
        <div><div class="t">Xray</div><div class="s">${xr ? esc(inboundSummary(u)) : "خاموش: لینک‌های VLESS، VMess، Trojan و Shadowsocks کار نمی‌کنند."}</div></div>
        <label class="switch"><input type="checkbox" data-act="svc" data-svc="xray_enabled" ${xr ? "checked" : ""} aria-label="Xray" /><span></span></label>
      </div>
      ${xr ? `<div class="ib-pick" id="d-ib">${inboundChips(u.xray_inbound_list || [], u.xray_inbounds)}</div>` : ""}
    </div>`;
}

// Xray: one share link per inbound the user has, the auto-updating Xray
// subscription, and new credentials if a link leaked.
const XPROTO_TAG = { vless: "VL", vmess: "VM", trojan: "TR", shadowsocks: "SS" };
function xraySection(u) {
  if (!u.xray_enabled) return "";
  const links = u.xray_links || [];
  if (!links.length) return "";
  const rows = links.map((l, i) => `
      <div class="xlink">
        <span class="xproto sm p-${l.protocol}">${XPROTO_TAG[l.protocol]}</span>
        <div class="grow"><div class="t">${esc(l.name)}${l.via ? ` <span class="via">از طریق ${esc(l.via)}</span>` : ""}</div><div class="s ltr">${esc(l.label)}</div></div>
        <button class="btn icon sm ghost" type="button" data-act="xl-qr" data-i="${i}" title="QR">${ic("qr")}</button>
        <button class="btn icon sm ghost" type="button" data-copy="${esc(l.link)}" data-copy-msg="لینک ${esc(l.name)} کپی شد" title="کپی">${ic("copy")}</button>
      </div>`).join("");
  return `
    <div class="dsec">
      <div class="dsec-title">لینک‌های Xray <span class="muted">${fa(links.length)} لینک</span></div>
      <div class="xlinks" style="margin-top:0">${rows}</div>
      <div class="qr-box xl-qr hide" id="xl-qr"></div>
      <div class="field" style="margin:14px 0 0">
        <label>لینک اشتراک Xray</label>
        <div class="input-group">
          <input type="text" class="mono" dir="ltr" readonly value="${esc(u.xray_sub)}" aria-label="لینک اشتراک Xray" />
          <button class="btn" type="button" data-copy="${esc(u.xray_sub)}" data-copy-msg="لینک اشتراک Xray کپی شد" title="کپی">${ic("copy")}</button>
        </div>
        <div class="hint">در v2rayNG، Hiddify یا Streisand با «افزودن اشتراک» وارد شود تا ورودی‌های بعدی و حجم باقیمانده خودکار به‌روز شوند.</div>
      </div>
      <div class="row" style="margin-top:10px; gap:8px; flex-wrap:wrap">
        <button class="btn sm" type="button" data-copy="${esc(links.map((l) => l.link).join("\n"))}" data-copy-msg="همه‌ی لینک‌ها کپی شد">${ic("copy")} کپی همه</button>
        <button class="btn sm" type="button" data-act="xl-sub-qr">${ic("qr")} QR اشتراک</button>
        <button class="btn sm" type="button" data-act="xray-regen">${ic("refresh")} لینک‌های جدید</button>
      </div>
    </div>`;
}

function showXrayQr(btn, text) {
  const box = $("#xl-qr");
  const same = box.dataset.for === text && !box.classList.contains("hide");
  $$("#drawer .xlink .btn.on, #drawer [data-act=xl-sub-qr].on").forEach((b) => b.classList.remove("on"));
  if (same) { box.classList.add("hide"); box.dataset.for = ""; return; }
  box.dataset.for = text;
  box.classList.remove("hide");
  renderQr(box, text, 220);
  btn.classList.add("on");
}

function drawUserChart(u) {
  if (userChart) { userChart.destroy(); userChart = null; }
  const canvas = $("#user-chart");
  if (!canvas || !window.Chart || !u.chart_dates) return;
  userChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: u.chart_dates.map(jShort),
      datasets: [{ data: u.chart_values, backgroundColor: barColors(u.chart_values.length), hoverBackgroundColor: cssVar("--accent-fill"), borderRadius: 3, borderSkipped: false, maxBarThickness: 16 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: (i) => jDate(u.chart_dates[i[0].dataIndex] + "T12:00:00", { weekday: "long", month: "long", day: "numeric" }), label: (i) => bytesTxt(i.raw) } },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 8 } },
        y: { beginAtZero: true, border: { display: false }, grid: { color: cssVar("--line") }, ticks: { maxTicksLimit: 3, callback: (v) => bytesTxt(v) } },
      },
    },
  });
}

function paintDrawer(u) {
  const drawer = $("#drawer");
  const scroll = drawer.scrollTop;
  drawer.innerHTML = drawerHtml(u);
  drawer.scrollTop = scroll;
  const qr = drawer.querySelector("details");
  if (qr) qr.addEventListener("toggle", () => { if (qr.open) renderQr($("#drawer-qr"), u.sub_link, 164); }, { once: true });
  drawUserChart(u);
}

async function openDrawer(id) {
  try {
    const u = await api(`/api/users/${id}`);
    currentId = id;
    currentUser = u;
    $("#drawer").scrollTop = 0;
    paintDrawer(u);
    openOverlay($("#drawer"));
  } catch (e) {
    toast(e.message, "error");
  }
}

async function refreshDrawer() {
  if (!currentId) return;
  currentUser = await api(`/api/users/${currentId}`);
  paintDrawer(currentUser);
}

async function patchUser(body, message) {
  await api(`/api/users/${currentId}`, { method: "PATCH", body });
  toast(message);
  await Promise.all([refreshDrawer(), load()]);
}

$("#drawer").addEventListener("overlay-close", () => { currentId = null; currentUser = null; });

$("#drawer").addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el || el.dataset.act === "toggle" || el.dataset.act === "svc") return;
  const act = el.dataset.act;
  const u = currentUser;
  try {
    if (act === "share") return shareOrCopy(shareText(u), u.username);
    if (act === "xl-qr") return showXrayQr(el, u.xray_links[+el.dataset.i].link);
    if (act === "xl-sub-qr") return showXrayQr(el, u.xray_sub);
    if (act === "xray-regen") {
      if (!(await confirmDialog({
        title: `لینک‌های Xray ${u.username} عوض شود؟`,
        message: "همه‌ی لینک‌های Xray قبلی این کاربر همان لحظه از کار می‌افتند و اگر وصل است قطع می‌شود. کاربر باید اشتراک را در اپ به‌روز کند؛ لینک اشتراک و OpenVPN عوض نمی‌شوند.",
        ok: "ساخت لینک‌های جدید",
      }))) return;
      await withBusy(el, () => api(`/api/users/${u.id}/regenerate_xray`, { method: "POST" }));
      toast("لینک‌های Xray عوض شد");
      return refreshDrawer();
    }
    if (act === "pw-copy") return copyText($("#pw-input").value, "رمز کپی شد", el);
    if (act === "pw-eye") {
      const inp = $("#pw-input");
      inp.type = inp.type === "password" ? "text" : "password";
      el.innerHTML = ic(inp.type === "password" ? "eye" : "eye-off");
      return;
    }
    if (act === "pw-gen") {
      const inp = $("#pw-input");
      inp.value = randomPassword();
      inp.type = "text";
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    if (act === "mode") {
      const mode = el.dataset.mode;
      if (mode === (u.auth_mode || "cert")) return;
      if (!(await confirmDialog({
        title: `روش ورود «${AUTH[mode].label}» شود؟`,
        message: `${MODE_HINTS[mode]} اتصال فعلی قطع می‌شود و کاربر باید کانفیگ را دوباره از لینک اشتراک دانلود کند.`,
        ok: "تغییر روش ورود", danger: false,
      }))) return;
      return await patchUser({ auth_mode: mode }, `روش ورود: ${AUTH[mode].label}`);
    }
    if (act === "add-days") return await withBusy(el, () => patchUser({ add_days: +el.dataset.v }, `${fa(+el.dataset.v)} روز به اعتبار اضافه شد`));
    if (act === "add-gb") return await withBusy(el, () => patchUser({ add_gb: +el.dataset.v }, `${fa(+el.dataset.v)} گیگ به حجم اضافه شد`));
    if (act === "reset") {
      if (!(await confirmDialog({ title: "مصرف صفر شود؟", message: `مصرف ${u.username} از صفر شمرده می‌شود. نمودار روزانه تغییری نمی‌کند.`, ok: "صفر کن", danger: false }))) return;
      await api(`/api/users/${u.id}/reset_usage`, { method: "POST" });
      toast("مصرف صفر شد");
      return Promise.all([refreshDrawer(), load()]);
    }
    if (act === "regen") {
      if (!(await confirmDialog({ title: "لینک اشتراک عوض شود؟", message: "لینک فعلی دیگر باز نمی‌شود و باید لینک جدید را برای کاربر بفرستید. کانفیگ‌های دانلودشده همچنان کار می‌کنند.", ok: "ساخت لینک جدید", danger: false }))) return;
      await api(`/api/users/${u.id}/regenerate_token`, { method: "POST" });
      toast("لینک جدید ساخته شد");
      return Promise.all([refreshDrawer(), load()]);
    }
    if (act === "new-key") {
      if (!(await confirmDialog({
        title: `کلید جدید برای ${u.username}؟`,
        message: "همه‌ی فایل‌های کانفیگی که این کاربر تا الان داشته، همان لحظه از کار می‌افتند و اتصال فعلی‌اش قطع می‌شود. باید کانفیگ را دوباره از لینک اشتراک دانلود کند؛ لینک اشتراک عوض نمی‌شود.",
        ok: "ساخت کلید جدید",
      }))) return;
      await withBusy(el, () => api(`/api/users/${u.id}/regenerate_key`, { method: "POST" }));
      toast("کلید جدید ساخته شد؛ کاربر کانفیگ را دوباره دانلود کند");
      return Promise.all([refreshDrawer(), load()]);
    }
    if (act === "delete") {
      if (!(await confirmDialog({ title: `${u.username} حذف شود؟`, message: "گواهی این کاربر باطل و اتصالش همان لحظه قطع می‌شود. این کار برگشت‌پذیر نیست.", ok: "حذف کاربر" }))) return;
      await api(`/api/users/${u.id}`, { method: "DELETE" });
      toast(`${u.username} حذف شد`);
      closeOverlay($("#drawer"));
      return load();
    }
  } catch (e) {
    toast(e.message, "error");
  }
});

$("#drawer").addEventListener("click", async (ev) => {
  const chip = ev.target.closest("#d-ib [data-ib]");
  if (!chip || !currentUser) return;
  const next = nextInbounds(currentUser.xray_inbounds, chip.dataset.ib);
  if (next === undefined || JSON.stringify(next) === JSON.stringify(currentUser.xray_inbounds)) return;
  try {
    await patchUser({ xray_inbounds: next }, next === null ? "همه‌ی ورودی‌ها، ورودی‌های جدید هم" : "ورودی‌های Xray این کاربر به‌روز شد");
  } catch (e) {
    toast(e.message, "error");
  }
});

const SVC_NAME = { openvpn_enabled: "OpenVPN", xray_enabled: "Xray" };
$("#drawer").addEventListener("change", async (ev) => {
  if (ev.target.dataset.act === "svc") {
    const key = ev.target.dataset.svc;
    const on = ev.target.checked;
    if (!on && !(await confirmDialog({
      title: `${SVC_NAME[key]} برای ${currentUser.username} خاموش شود؟`,
      message: key === "openvpn_enabled"
        ? "فایل‌های کانفیگ OpenVPN این کاربر دیگر وصل نمی‌شوند و اگر الان وصل است قطع می‌شود. Xray دست نمی‌خورد."
        : "لینک‌های Xray این کاربر دیگر وصل نمی‌شوند و اگر الان وصل است قطع می‌شود. OpenVPN دست نمی‌خورد.",
      ok: "خاموش کن",
    }))) { ev.target.checked = true; return; }
    try {
      await patchUser({ [key]: on }, `${SVC_NAME[key]} ${on ? "روشن" : "خاموش"} شد`);
    } catch (e) {
      ev.target.checked = !on;
      toast(e.message, "error");
    }
    return;
  }
  if (ev.target.dataset.act !== "toggle") return;
  try {
    const u = await api(`/api/users/${currentId}/toggle`, { method: "POST" });
    toast(u.enabled ? "کاربر فعال شد" : "کاربر غیرفعال شد و اتصالش قطع شد");
    await Promise.all([refreshDrawer(), load()]);
  } catch (e) {
    ev.target.checked = !ev.target.checked;
    toast(e.message, "error");
  }
});

$("#drawer").addEventListener("input", (ev) => {
  if (ev.target.id === "pw-input") {
    const v = ev.target.value.trim();
    $("#pw-save").disabled = !v || v === (currentUser.password || "");
  }
});

$("#drawer").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (ev.target.id === "pw-form") {
    const password = $("#pw-input").value.trim();
    return withBusy($("#pw-save"), async () => {
      try { await patchUser({ password }, "رمز جدید ذخیره شد"); } catch (e) { toast(e.message, "error"); }
    });
  }
  if (ev.target.id !== "edit-form") return;
  const f = new FormData(ev.target);
  const u = currentUser;
  const body = { note: f.get("note") };
  const gb = f.get("gb");
  const days = f.get("days");
  const origGb = u.data_limit_bytes ? String(+(u.data_limit_bytes / 1024 ** 3).toFixed(2)) : "";
  const origDays = u.days_left === null || u.days_left === undefined ? "" : String(u.days_left);
  // only send what actually changed, so saving a note doesn't nudge the expiry
  if (gb !== origGb) { if (gb === "" || +gb === 0) body.clear_limit = true; else body.data_limit_gb = +gb; }
  if (days !== origDays) { if (days === "") body.clear_expiry = true; else body.expire_days = +days; }
  const devices = f.get("devices");
  if (devices !== String(u.max_devices || "")) body.max_devices = devices ? +devices : 0;
  await withBusy(ev.target.querySelector("button[type=submit]"), async () => {
    try { await patchUser(body, "تغییرات ذخیره شد"); } catch (e) { toast(e.message, "error"); }
  });
});

/* ---------------------------------------------------------------- create */

let createMode = "cert";

function setCreateMode(mode) {
  createMode = AUTH[mode] ? mode : "cert";
  $$("#c-modes .mode").forEach((b) => {
    const on = b.dataset.mode === createMode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
  const needs = createMode !== "cert";
  $("#c-pass-field").classList.toggle("hide", !needs);
  $("#c-pass").required = needs;
  if (needs && !$("#c-pass").value) $("#c-pass").value = randomPassword();
  $("#c-mode-hint").textContent = MODE_HINTS[createMode];
  try { localStorage.setItem("waze-auth-mode", createMode); } catch (e) {}
}

// services for the next user: remembered like the login mode
let createSvc = { openvpn: true, xray: true, inbounds: null };
let inboundList = [];

function paintCreateServices() {
  $$("#c-svcs .svc").forEach((b) => b.setAttribute("aria-pressed", String(createSvc[b.dataset.svc])));
  $("#c-ovpn-fields").classList.toggle("hide", !createSvc.openvpn);
  $("#c-ib").innerHTML = createSvc.xray ? inboundChips(inboundList, createSvc.inbounds) : "";
  try { localStorage.setItem("waze-services", JSON.stringify(createSvc)); } catch (e) {}
}

async function loadInboundChoices() {
  try {
    inboundList = (await api("/api/xray/inbounds")).inbounds;
  } catch (e) {
    inboundList = [];
  }
  if (createSvc.inbounds) {
    const known = new Set(inboundList.map((i) => i.id));
    createSvc.inbounds = createSvc.inbounds.filter((id) => known.has(id));
    if (!createSvc.inbounds.length) createSvc.inbounds = null;
  }
  paintCreateServices();
}

$("#c-svcs").addEventListener("click", (ev) => {
  const b = ev.target.closest(".svc");
  if (!b) return;
  const key = b.dataset.svc;
  const other = key === "openvpn" ? "xray" : "openvpn";
  if (createSvc[key] && !createSvc[other]) return toast("دست‌کم یکی از OpenVPN یا Xray باید روشن باشد", "error");
  createSvc[key] = !createSvc[key];
  paintCreateServices();
});
$("#c-ib").addEventListener("click", (ev) => {
  const chip = ev.target.closest("[data-ib]");
  if (!chip) return;
  const next = nextInbounds(createSvc.inbounds, chip.dataset.ib);
  if (next === undefined) return;
  createSvc.inbounds = next;
  paintCreateServices();
});

function resetCreateForm() {
  $("#create-form").reset();
  $$("#presets .chip").forEach((c) => c.classList.remove("active"));
  let mode = "cert";
  try { mode = localStorage.getItem("waze-auth-mode") || "cert"; } catch (e) {}
  $("#c-pass").value = "";
  setCreateMode(mode); // the admin's last choice: resellers tend to stick to one
  try {
    const saved = JSON.parse(localStorage.getItem("waze-services") || "null");
    if (saved && (saved.openvpn || saved.xray)) createSvc = { openvpn: !!saved.openvpn, xray: !!saved.xray, inbounds: saved.inbounds || null };
  } catch (e) {}
  paintCreateServices();
  loadInboundChoices();
  $("#create-form-view").classList.remove("hide");
  $("#create-done-view").classList.add("hide");
}

function openCreate() {
  resetCreateForm();
  openOverlay($("#modal-create"));
}
window.openCreate = openCreate;

$("#presets").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip");
  if (!chip) return;
  $$("#presets .chip").forEach((c) => c.classList.toggle("active", c === chip));
  $("#c-gb").value = chip.dataset.gb;
  $("#c-days").value = chip.dataset.days;
});
["#c-gb", "#c-days"].forEach((s) => $(s).addEventListener("input", () => $$("#presets .chip").forEach((c) => c.classList.remove("active"))));

$("#c-modes").addEventListener("click", (ev) => {
  const b = ev.target.closest(".mode");
  if (b) setCreateMode(b.dataset.mode);
});
$("#c-pass-gen").addEventListener("click", () => { $("#c-pass").value = randomPassword(); });
$("#c-pass-copy").addEventListener("click", (ev) => copyText($("#c-pass").value, "رمز کپی شد", ev.currentTarget));

$("#dice").addEventListener("click", () => {
  const words = ["user", "client", "home", "phone", "laptop", "office", "guest"];
  const rand = Math.random().toString(36).slice(2, 6);
  $("#c-username").value = `${words[Math.floor(Math.random() * words.length)]}_${rand}`;
  $("#c-username").focus();
});

$("#create-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = {
    username: f.get("username").trim(),
    note: f.get("note") || null,
    data_limit_gb: f.get("data_limit_gb") ? +f.get("data_limit_gb") : null,
    expire_days: f.get("expire_days") ? +f.get("expire_days") : null,
    auth_mode: createMode,
    password: createMode !== "cert" ? $("#c-pass").value.trim() : null,
    max_devices: f.get("max_devices") ? +f.get("max_devices") : 0,
    openvpn_enabled: createSvc.openvpn,
    xray_enabled: createSvc.xray,
    xray_inbounds: createSvc.inbounds,
  };
  await withBusy($("#create-submit"), async () => {
    try {
      const u = await api("/api/users", { method: "POST", body });
      lastCreated = u;
      $("#done-name").textContent = u.username;
      $("#done-link").value = u.sub_link;
      $("#done-udp").href = `/api/users/${u.id}/config/udp`;
      $("#done-tcp").href = `/api/users/${u.id}/config/tcp`;
      $("#done-udp").classList.toggle("hide", !u.openvpn_enabled);
      $("#done-tcp").classList.toggle("hide", !u.openvpn_enabled);
      $("#done-xray").classList.toggle("hide", !u.xray_enabled);
      const creds = $("#done-creds");
      creds.classList.toggle("hide", u.auth_mode === "cert" || !u.openvpn_enabled);
      creds.innerHTML = u.auth_mode === "cert" ? "" : credRow("نام کاربری", u.username) + credRow("رمز", u.password);
      $("#done-qr").innerHTML = "";
      $("#create-done-view details").open = false;
      $("#create-form-view").classList.add("hide");
      $("#create-done-view").classList.remove("hide");
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

$("#create-done-view details").addEventListener("toggle", (ev) => {
  if (ev.target.open && lastCreated && !$("#done-qr").innerHTML) renderQr($("#done-qr"), lastCreated.sub_link, 164);
});
$("#done-copy").addEventListener("click", (ev) => copyText($("#done-link").value, "لینک اشتراک کپی شد", ev.currentTarget));
$("#done-xray").addEventListener("click", (ev) => { if (lastCreated) copyText(`${lastCreated.sub_link}/xray`, "لینک اشتراک Xray کپی شد", ev.currentTarget); });
$("#done-share").addEventListener("click", () => { if (lastCreated) shareOrCopy(shareText(lastCreated), lastCreated.username); });
$("#done-another").addEventListener("click", () => {
  resetCreateForm();
  $("#c-username").focus();
});
$("#done-open").addEventListener("click", () => {
  closeOverlay($("#modal-create"));
  if (lastCreated) openDrawer(lastCreated.id);
});

/* ---------------------------------------------------------------- wiring */

$("#users-list").addEventListener("click", (ev) => {
  if (ev.target.closest(".js-stop, [data-copy]")) return;
  const row = ev.target.closest(".urow[data-id]");
  if (row) openDrawer(+row.dataset.id);
});
$("#users-list").addEventListener("keydown", (ev) => {
  const row = ev.target.closest(".urow[data-id]");
  if (row && (ev.key === "Enter" || ev.key === " ") && ev.target === row) { ev.preventDefault(); openDrawer(+row.dataset.id); }
});

$("#users-list").addEventListener("change", async (ev) => {
  const t = ev.target.closest(".js-toggle");
  if (!t) return;
  try {
    const u = await api(`/api/users/${t.dataset.id}/toggle`, { method: "POST" });
    toast(u.enabled ? `${u.username} فعال شد` : `${u.username} غیرفعال شد`);
    load();
  } catch (e) {
    t.checked = !t.checked;
    toast(e.message, "error");
  }
});

function setFilter(f) {
  filter = FILTERS[f] ? f : "all";
  $$("#filters button").forEach((x) => x.classList.toggle("active", x.dataset.f === filter));
  // the bottom nav has its own "online" tab
  $$(".bottom-nav [data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (filter === "online" ? "online" : "users")));
  renderList();
}
$("#filters").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-f]");
  if (b) { haptic(); setFilter(b.dataset.f); }
});
$(".bottom-nav [data-nav=online]").addEventListener("click", (ev) => { ev.preventDefault(); setFilter("online"); scrollTo({ top: 0, behavior: "smooth" }); });
$(".bottom-nav [data-nav=users]").addEventListener("click", (ev) => { ev.preventDefault(); setFilter("all"); scrollTo({ top: 0, behavior: "smooth" }); });
$("#fab-new").addEventListener("click", (ev) => { ev.preventDefault(); haptic(); openCreate(); });
$("#q").addEventListener("input", renderList);
$("#sort").addEventListener("change", renderList);
$("#refresh-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, load));
$("#new-user-btn").addEventListener("click", openCreate);

document.addEventListener("keydown", (ev) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (typing || overlays.length || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === "/") { ev.preventDefault(); $("#q").focus(); }
  if (ev.key === "n" || ev.key === "N" || ev.key === "د") { ev.preventDefault(); openCreate(); }
});

(async () => {
  await load();
  const params = new URLSearchParams(location.search);
  if (params.get("f")) setFilter(params.get("f"));
  if (params.get("new")) openCreate();
  const openName = params.get("open");
  if (openName) {
    const u = users.find((x) => x.username === openName);
    if (u) openDrawer(u.id);
  }
  if (params.has("new") || params.has("open") || params.has("f")) history.replaceState(null, "", "/users");
})();
setInterval(() => { if (!overlays.length && !document.hidden) load(); }, 20000);
