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

const protos = (u) => u.online_protos.map((p) => p.toUpperCase()).join(" + ");
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
  if (u.auth_mode && u.auth_mode !== "cert") bits.push(`<span class="pw-tag">${ic("lock")}${AUTH[u.auth_mode].label}</span>`);
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
      <a class="btn icon sm ghost js-stop" href="/api/users/${u.id}/config/udp" title="دانلود کانفیگ UDP">${ic("download")}</a>
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
      <a class="btn" href="/api/users/${u.id}/config/udp">${ic("download")} UDP</a>
      <a class="btn" href="/api/users/${u.id}/config/tcp">${ic("download")} TCP</a>
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

  ${loginSection(u)}
  ${keySection(u)}

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
      <div class="dsec-title">روش ورود</div>
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
      <div class="dsec-title">کلید اتصال</div>
      <div class="setting-row">
        <div><div class="t">کلید شخصی <span class="mono muted" style="font-weight:400">tls-crypt-v2</span></div><div class="s">${state}</div></div>
        <button class="btn sm" type="button" data-act="new-key">${ic("key")} کلید جدید</button>
      </div>
      <div class="hint">اگر فایل کانفیگ این کاربر دست کس دیگری افتاده، کلید جدید بسازید: همه‌ی نسخه‌های قبلی همان لحظه از کار می‌افتند و بقیه‌ی کاربران دست نمی‌خورند.</div>
    </div>`;
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
  if (!el || el.dataset.act === "toggle") return;
  const act = el.dataset.act;
  const u = currentUser;
  try {
    if (act === "share") return shareOrCopy(shareText(u), u.username);
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

$("#drawer").addEventListener("change", async (ev) => {
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

function resetCreateForm() {
  $("#create-form").reset();
  $$("#presets .chip").forEach((c) => c.classList.remove("active"));
  let mode = "cert";
  try { mode = localStorage.getItem("waze-auth-mode") || "cert"; } catch (e) {}
  $("#c-pass").value = "";
  setCreateMode(mode); // the admin's last choice: resellers tend to stick to one
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
  };
  await withBusy($("#create-submit"), async () => {
    try {
      const u = await api("/api/users", { method: "POST", body });
      lastCreated = u;
      $("#done-name").textContent = u.username;
      $("#done-link").value = u.sub_link;
      $("#done-udp").href = `/api/users/${u.id}/config/udp`;
      $("#done-tcp").href = `/api/users/${u.id}/config/tcp`;
      const creds = $("#done-creds");
      creds.classList.toggle("hide", u.auth_mode === "cert");
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
