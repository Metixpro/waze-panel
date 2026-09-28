// Users page: list, filters, detail drawer, create flow.
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

/* ------------------------------------------------------------ list view */

function usageCell(u) {
  const used = u.data_used_bytes || 0;
  if (!u.data_limit_bytes) {
    return `<div class="t"><span><b>${bytesHtml(used)}</b></span><span class="muted">نامحدود</span></div><div class="bar inf"><span></span></div>`;
  }
  const p = pct(used, u.data_limit_bytes);
  return `<div class="t"><span><b>${bytesHtml(used)}</b> از ${bytesHtml(u.data_limit_bytes)}</span><span>${fa(p)}٪</span></div>
          <div class="bar ${levelBar(p)}"><span style="width:${Math.max(p, 2)}%"></span></div>`;
}

function expiryCell(u) {
  if (u.days_left === null || u.days_left === undefined) return `<span class="dim">نامحدود</span>`;
  const cls = u.days_left <= 0 ? "badge expired" : u.days_left <= 3 ? "badge warn" : "";
  const label = daysLeftLabel(u.days_left);
  return `${cls ? `<span class="${cls}">${label}</span>` : label}<small>${jDate(u.expire_at, { month: "short", day: "numeric", year: "numeric" })}</small>`;
}

function seenCell(u) {
  if (u.online) {
    const devs = u.devices_online > 1 ? ` · ${fa(u.devices_online)} دستگاه` : "";
    return `<span class="badge active"><i></i>آنلاین · ${u.online_protos.map((p) => p.toUpperCase()).join(" + ")}${devs}</span><small class="mono">${esc(u.last_ip || "")}</small>`;
  }
  return `${relTime(u.last_connected_at)}${u.last_ip ? `<small class="mono">${esc(u.last_ip)}</small>` : ""}`;
}

function authTag(u) {
  if (!u.auth_mode || u.auth_mode === "cert") return "";
  const a = AUTH[u.auth_mode];
  return `<span class="auth-tag pw" title="روش ورود: ${a.label}">${ic(a.icon)}${a.label}</span> · `;
}

function rowHtml(u) {
  return `<div class="urow" data-id="${u.id}">
    <div class="who">
      ${avatar(u.username, u.online)}
      <div class="grow">
        <div class="n mono" style="text-align:right">${esc(u.username)}</div>
        <div class="note">${authTag(u)}${u.note ? esc(u.note) : `ساخته شده ${relTime(u.created_at)}`}</div>
      </div>
    </div>
    <div class="st">${statusBadge(u.status)}</div>
    <div class="usage">${usageCell(u)}</div>
    <div class="exp">${expiryCell(u)}</div>
    <div class="seen">${seenCell(u)}</div>
    <div class="acts">
      <button class="btn icon sm ghost js-copy" data-copy="${esc(u.sub_link)}" title="کپی لینک اشتراک">${ic("link")}</button>
      <a class="btn icon sm ghost js-stop" href="/api/users/${u.id}/config/udp" title="دانلود کانفیگ UDP">${ic("download")}</a>
      <label class="switch js-stop" title="${u.enabled ? "غیرفعال کردن" : "فعال کردن"}">
        <input type="checkbox" class="js-toggle" data-id="${u.id}" ${u.enabled ? "checked" : ""} /><span></span>
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
    list.innerHTML = `<div class="empty">${ic("users")}<b>هنوز کاربری نساخته‌اید</b>با دکمه‌ی «کاربر جدید» اولین کاربر را بسازید.
      <div style="margin-top:14px"><button class="btn primary" onclick="openCreate()">${ic("user-plus")} ساخت اولین کاربر</button></div></div>`;
    return;
  }
  if (!rows.length) {
    list.innerHTML = `<div class="empty">${ic("search")}<b>موردی پیدا نشد</b>فیلتر یا عبارت جستجو را تغییر دهید.</div>`;
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
  $("#users-sub").innerHTML = `${fa(users.length)} کاربر · <span class="live"><i></i>${fa(online)} آنلاین</span>`;
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

function drawerHtml(u) {
  const limit = u.data_limit_bytes;
  const p = limit ? pct(u.data_used_bytes, limit) : 0;
  const ringLabel = limit ? `<b>${fa(p)}٪</b><small>مصرف</small>` : `<b>∞</b><small>نامحدود</small>`;
  const remaining = limit ? Math.max(0, limit - u.data_used_bytes) : null;
  const alert = u.status !== "active"
    ? `<div class="alert ${u.status === "disabled" ? "" : "warn"}">${ic("alert")}<span>${
        { disabled: "این کاربر غیرفعال است و نمی‌تواند وصل شود.", expired: "اعتبار این کاربر تمام شده؛ برای اتصال دوباره تمدید کنید.",
          over_quota: "حجم این کاربر تمام شده؛ حجم اضافه کنید یا مصرف را صفر کنید.", revoked: "گواهی این کاربر باطل شده است." }[u.status] || ""
      }</span></div>`
    : "";

  return `
  <div class="drawer-head">
    ${avatar(u.username, u.online, "lg")}
    <div class="grow">
      <div class="row" style="gap:8px"><b class="mono" style="font-size:17px">${esc(u.username)}</b>${statusBadge(u.status)}</div>
      <div class="muted" style="font-size:12px">${u.note ? esc(u.note) + " · " : ""}ساخته شده ${jDate(u.created_at)}</div>
    </div>
    <button class="btn icon sm ghost" data-close title="بستن">${ic("x")}</button>
  </div>
  <div class="drawer-body">
    ${alert}
    <div class="section">
      <div class="usage-hero">
        ${ring(limit ? p : 100, { size: 108, stroke: 10, color: limit ? levelColor(p) : "var(--accent)", label: ringLabel })}
        <div class="kv">
          <div><div class="k">مصرف شده</div><div class="v">${bytesHtml(u.data_used_bytes)}</div></div>
          <div><div class="k">سقف حجم</div><div class="v">${limit ? bytesHtml(limit) : "نامحدود"}</div></div>
          <div><div class="k">باقیمانده</div><div class="v">${remaining === null ? "نامحدود" : bytesHtml(remaining)}</div></div>
          <div><div class="k">اعتبار</div><div class="v">${daysLeftLabel(u.days_left)}</div></div>
          <div><div class="k">آخرین اتصال</div><div class="v">${u.online ? `<span style="color:var(--green)">آنلاین · ${u.online_protos.map((x) => x.toUpperCase()).join(" + ")}</span>` : relTime(u.last_connected_at)}</div></div>
          <div><div class="k">آی‌پی</div><div class="v mono">${esc(u.last_ip || "—")}</div></div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">${ic("gift")} تمدید سریع</div>
      <div class="chips">
        <button class="chip" data-act="add-days" data-v="7">+۷ روز</button>
        <button class="chip" data-act="add-days" data-v="30">+۳۰ روز</button>
        <button class="chip" data-act="add-days" data-v="90">+۹۰ روز</button>
        ${limit ? `<button class="chip" data-act="add-gb" data-v="10">+۱۰ گیگ</button>
        <button class="chip" data-act="add-gb" data-v="50">+۵۰ گیگ</button>` : ""}
        <button class="chip" data-act="reset">صفر کردن مصرف</button>
      </div>
      <div class="hint">تمدید از تاریخ انقضای فعلی حساب می‌شود (یا از امروز، اگر گذشته باشد).</div>
    </div>

    <div class="section">
      <div class="section-title">${ic("trending")} مصرف ۱۴ روز اخیر</div>
      <div class="chart-box sm"><canvas id="user-chart" dir="ltr"></canvas></div>
    </div>

    <div class="section">
      <div class="section-title">${ic("link")} لینک اشتراک</div>
      <div class="input-group">
        <input type="text" class="mono" readonly value="${esc(u.sub_link)}" />
        <button class="btn ghost" data-act="copy" title="کپی">${ic("copy")}</button>
      </div>
      <div class="qr-box" id="drawer-qr" style="margin-top:14px"></div>
      <div class="row wrap" style="margin-top:12px">
        <a class="btn sm grow" href="${esc(u.sub_link)}" target="_blank" rel="noopener">${ic("external")} باز کردن صفحه</a>
        <button class="btn sm grow" data-act="regen">${ic("refresh")} لینک جدید</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">${ic("download")} ${u.auth_mode === "pass" ? "کانفیگ مشترک (بدون گواهی)" : "دانلود کانفیگ"}</div>
      <div class="row">
        <a class="btn grow" href="/api/users/${u.id}/config/udp">${ic("download")} UDP</a>
        <a class="btn grow" href="/api/users/${u.id}/config/tcp">${ic("download")} TCP</a>
      </div>
      ${u.auth_mode === "pass" ? `<div class="hint">همه‌ی کاربران «فقط رمز» از همین فایل استفاده می‌کنند؛ فرقشان فقط نام کاربری و رمز است.</div>` : ""}
      ${u.auth_mode === "cert_pass" ? `<div class="hint">فایل اختصاصی این کاربر است و هنگام اتصال، نام کاربری و رمز هم پرسیده می‌شود.</div>` : ""}
    </div>

    ${loginSection(u)}

    <form class="section" id="edit-form" autocomplete="off">
      <div class="section-title">${ic("edit")} ویرایش</div>
      <div class="field">
        <label>یادداشت</label>
        <input type="text" name="note" maxlength="200" value="${esc(u.note || "")}" />
      </div>
      <div class="field-row">
        <div class="field">
          <label>سقف حجم (گیگ)</label>
          <input type="number" name="gb" min="0" step="0.5" placeholder="نامحدود" value="${limit ? +(limit / 1024 ** 3).toFixed(2) : ""}" />
        </div>
        <div class="field">
          <label>اعتبار (روز از امروز)</label>
          <input type="number" name="days" min="1" step="1" placeholder="نامحدود" value="${u.days_left ?? ""}" />
        </div>
      </div>
      <div class="field">
        <label>حداکثر دستگاه همزمان</label>
        <input type="number" name="devices" min="0" max="100" step="1" placeholder="نامحدود" value="${u.max_devices || ""}" />
      </div>
      <div class="hint" style="margin-top:-6px; margin-bottom:12px">خالی بگذارید تا نامحدود شود. با پر شدن سقف دستگاه، اتصال جدیدتر می‌ماند و قدیمی‌تر قطع می‌شود.</div>
      <button class="btn primary block" type="submit">${ic("check")} ذخیره تغییرات</button>
    </form>

    <div class="section danger-zone">
      <div class="setting-row">
        <div><div class="t">فعال بودن کاربر</div><div class="s">با غیرفعال کردن، اتصال فعلی فورا قطع می‌شود.</div></div>
        <label class="switch"><input type="checkbox" data-act="toggle" ${u.enabled ? "checked" : ""} /><span></span></label>
      </div>
      <div class="setting-row">
        <div><div class="t">حذف کاربر</div><div class="s">گواهی باطل می‌شود و کانفیگ‌ها دیگر کار نمی‌کنند.</div></div>
        <button class="btn sm danger" data-act="delete">${ic("trash")} حذف</button>
      </div>
    </div>
  </div>`;
}

function loginSection(u) {
  const mode = u.auth_mode || "cert";
  const modes = Object.entries(AUTH).map(([k, a]) =>
    `<button type="button" class="mode ${k === mode ? "active" : ""}" data-act="mode" data-mode="${k}" role="radio" aria-checked="${k === mode}">${ic(a.icon)}<b>${a.label}</b></button>`
  ).join("");
  const devices = `${fa(u.devices_online || 0)} دستگاه آنلاین · سقف ${u.max_devices ? fa(u.max_devices) : "نامحدود"}`;
  const pw = mode === "cert" ? "" : `
      <div class="creds" style="margin-bottom:10px">${credRow("نام کاربری", u.username)}</div>
      <form id="pw-form" autocomplete="off">
        <div class="input-group two">
          <input type="password" name="password" dir="ltr" class="mono" maxlength="64" spellcheck="false" autocomplete="off" value="${esc(u.password || "")}" aria-label="رمز اتصال" />
          <button class="btn ghost" type="button" data-act="pw-eye" title="نمایش">${ic("eye")}</button>
          <button class="btn ghost" type="button" data-act="pw-copy" title="کپی">${ic("copy")}</button>
        </div>
        <div class="row" style="margin-top:10px">
          <button type="button" class="btn sm grow" data-act="pw-gen">${ic("dice")} رمز تصادفی</button>
          <button type="submit" class="btn sm primary grow" id="pw-save" disabled>${ic("check")} ذخیره رمز</button>
        </div>
        <button type="button" class="btn sm block" data-act="share" style="margin-top:8px">${ic("copy")} کپی مشخصات ورود برای ارسال</button>
        <div class="hint">با تغییر رمز یا روش ورود، اتصال فعلی کاربر قطع می‌شود.</div>
      </form>`;
  return `
    <div class="section">
      <div class="section-title">${ic("key")} ورود و امنیت <span class="muted" style="margin-inline-start:auto; font-size:11.5px; font-weight:600">${devices}</span></div>
      <div class="modes" role="radiogroup">${modes}</div>
      ${pw}
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
      datasets: [{ data: u.chart_values, backgroundColor: "rgba(124,92,255,0.75)", hoverBackgroundColor: "#7c5cff", borderRadius: 6, maxBarThickness: 18 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { rtl: true, textDirection: "rtl", displayColors: false,
        callbacks: { label: (i) => bytesTxt(i.raw) } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 8 } },
        y: { beginAtZero: true, border: { display: false }, grid: { color: cssVar("--border") }, ticks: { maxTicksLimit: 4, callback: (v) => bytesTxt(v) } },
      },
    },
  });
}

async function openDrawer(id) {
  try {
    const u = await api(`/api/users/${id}`);
    currentId = id;
    currentUser = u;
    const drawer = $("#drawer");
    drawer.innerHTML = drawerHtml(u);
    openOverlay(drawer);
    renderQr($("#drawer-qr"), u.sub_link, 150);
    drawUserChart(u);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function refreshDrawer() {
  if (!currentId) return;
  const u = await api(`/api/users/${currentId}`);
  currentUser = u;
  const drawer = $("#drawer");
  const scroll = drawer.scrollTop;
  drawer.innerHTML = drawerHtml(u);
  drawer.scrollTop = scroll;
  renderQr($("#drawer-qr"), u.sub_link, 150);
  drawUserChart(u);
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
    if (act === "copy") return copyText(u.sub_link, "لینک اشتراک کپی شد");
    if (act === "share") return copyText(shareText(u), "مشخصات ورود کپی شد");
    if (act === "pw-copy") return copyText($("#pw-form [name=password]").value, "رمز کپی شد");
    if (act === "pw-eye") {
      const inp = $("#pw-form [name=password]");
      inp.type = inp.type === "password" ? "text" : "password";
      el.innerHTML = ic(inp.type === "password" ? "eye" : "eye-off");
      return;
    }
    if (act === "pw-gen") {
      const inp = $("#pw-form [name=password]");
      inp.value = randomPassword();
      inp.type = "text";
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    if (act === "mode") {
      const mode = el.dataset.mode;
      if (mode === (u.auth_mode || "cert")) return;
      const msg = {
        cert: "کاربر فقط با فایل اختصاصی خودش وصل می‌شود و رمز دیگر پرسیده نمی‌شود.",
        cert_pass: "از این به بعد فایل اختصاصی و رمز هر دو لازم است.",
        pass: "کاربر با فایل مشترک و نام کاربری و رمز وصل می‌شود.",
      }[mode];
      if (!(await confirmDialog({ title: `روش ورود: ${AUTH[mode].label}؟`,
        message: `${msg} اتصال فعلی قطع می‌شود و کاربر باید کانفیگ را دوباره از لینک اشتراک دانلود کند.`,
        ok: "تغییر روش ورود", danger: false, icon: "key" }))) return;
      return await patchUser({ auth_mode: mode }, `روش ورود: ${AUTH[mode].label}`);
    }
    if (act === "add-days") return await withBusy(el, () => patchUser({ add_days: +el.dataset.v }, `${fa(+el.dataset.v)} روز به اعتبار اضافه شد`));
    if (act === "add-gb") return await withBusy(el, () => patchUser({ add_gb: +el.dataset.v }, `${fa(+el.dataset.v)} گیگ به حجم اضافه شد`));
    if (act === "reset") {
      if (!(await confirmDialog({ title: "مصرف صفر شود؟", message: `مصرف ${u.username} از صفر شمرده می‌شود.`, ok: "صفر کن", danger: false, icon: "refresh" }))) return;
      await api(`/api/users/${u.id}/reset_usage`, { method: "POST" });
      toast("مصرف صفر شد");
      return Promise.all([refreshDrawer(), load()]);
    }
    if (act === "regen") {
      if (!(await confirmDialog({ title: "ساخت لینک جدید؟", message: "لینک اشتراک فعلی دیگر کار نمی‌کند و باید لینک جدید را برای کاربر بفرستید.", ok: "ساخت لینک جدید" }))) return;
      await api(`/api/users/${u.id}/regenerate_token`, { method: "POST" });
      toast("لینک جدید ساخته شد");
      return Promise.all([refreshDrawer(), load()]);
    }
    if (act === "delete") {
      if (!(await confirmDialog({ title: `حذف ${u.username}؟`, message: "گواهی این کاربر باطل و اتصالش فورا قطع می‌شود. این کار برگشت‌پذیر نیست.", ok: "حذف کاربر", icon: "trash" }))) return;
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
  if (ev.target.closest("#pw-form")) {
    $("#pw-save").disabled = ev.target.value.trim() === (currentUser.password || "") || !ev.target.value.trim();
  }
});

$("#drawer").addEventListener("submit", async (ev) => {
  if (ev.target.id === "pw-form") {
    ev.preventDefault();
    const password = ev.target.password.value.trim();
    return withBusy($("#pw-save"), async () => {
      try { await patchUser({ password }, "رمز جدید ذخیره شد"); } catch (e) { toast(e.message, "error"); }
    });
  }
  if (ev.target.id !== "edit-form") return;
  ev.preventDefault();
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
  const btn = ev.target.querySelector("button[type=submit]");
  await withBusy(btn, async () => {
    try { await patchUser(body, "تغییرات ذخیره شد"); } catch (e) { toast(e.message, "error"); }
  });
});

/* ---------------------------------------------------------------- create */

const MODE_HINTS = {
  cert: "کاربر با فایل کانفیگ اختصاصی خودش وصل می‌شود و رمزی لازم نیست.",
  cert_pass: "فایل اختصاصی + نام کاربری و رمز هنگام اتصال؛ اگر فایل لو برود هم بدون رمز کار نمی‌کند.",
  pass: "بدون گواهی اختصاصی: همه‌ی کاربران «فقط رمز» یک فایل مشترک دارند و با نام کاربری و رمز خودشان وارد می‌شوند.",
};
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

$("#c-modes").addEventListener("click", (ev) => {
  const b = ev.target.closest(".mode");
  if (b) setCreateMode(b.dataset.mode);
});
$("#c-pass-gen").addEventListener("click", () => { $("#c-pass").value = randomPassword(); });
$("#c-pass-copy").addEventListener("click", () => copyText($("#c-pass").value, "رمز کپی شد"));

$("#dice").addEventListener("click", () => {
  const words = ["user", "client", "vpn", "net", "sky", "nova", "zen", "star"];
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
      if (u.auth_mode !== "cert") {
        creds.innerHTML = credRow("نام کاربری", u.username) + credRow("رمز", u.password) +
          `<button type="button" class="btn sm" id="done-share">${ic("copy")} کپی مشخصات برای ارسال</button>`;
      }
      $("#create-form-view").classList.add("hide");
      $("#create-done-view").classList.remove("hide");
      renderQr($("#done-qr"), u.sub_link, 170);
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  });
});

$("#done-copy").addEventListener("click", () => copyText($("#done-link").value, "لینک اشتراک کپی شد"));
$("#done-creds").addEventListener("click", (ev) => {
  if (ev.target.closest("#done-share") && lastCreated) copyText(shareText(lastCreated), "مشخصات ورود کپی شد");
});
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
  const copy = ev.target.closest(".js-copy");
  if (copy) { ev.stopPropagation(); return copyText(copy.dataset.copy, "لینک اشتراک کپی شد"); }
  if (ev.target.closest(".js-stop")) { ev.stopPropagation(); return; }
  const row = ev.target.closest(".urow[data-id]");
  if (row) openDrawer(+row.dataset.id);
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

$("#filters").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  $$("#filters button").forEach((x) => x.classList.toggle("active", x === b));
  renderList();
});
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
  if (params.get("new")) openCreate();
  const openName = params.get("open");
  if (openName) {
    const u = users.find((x) => x.username === openName);
    if (u) openDrawer(u.id);
  }
  if (params.has("new") || params.has("open")) history.replaceState(null, "", "/users");
})();
setInterval(() => { if (!overlays.length) load(); }, 20000);
