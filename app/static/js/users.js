let allUsers = [];
let currentUserId = null;

const STATUS_LABELS = {
  active: "فعال",
  disabled: "غیرفعال",
  expired: "منقضی شده",
  over_quota: "اتمام حجم",
  revoked: "لغو شده",
};

function statusBadge(status) {
  return `<span class="badge ${status}"><span class="dot"></span> ${STATUS_LABELS[status] || status}</span>`;
}

function usageBarHtml(user) {
  const used = user.data_used_bytes || 0;
  const limit = user.data_limit_bytes;
  if (!limit) {
    return `<div class="usage-cell"><div style="font-size:12px;">${humanBytes(used)}</div><div class="text-faint" style="font-size:11px;">نامحدود</div></div>`;
  }
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const cls = pct >= 100 ? "danger" : pct >= 80 ? "warn" : "";
  return `
    <div class="usage-cell">
      <div style="font-size:12px; margin-bottom:4px;">${humanBytes(used)} / ${humanBytes(limit)}</div>
      <div class="bar ${cls}"><span style="width:${pct}%"></span></div>
    </div>`;
}

function renderRows() {
  const q = document.getElementById("search-input").value.trim().toLowerCase();
  const tbody = document.getElementById("users-tbody");
  const filtered = allUsers.filter((u) => !q || u.username.toLowerCase().includes(q) || (u.note || "").toLowerCase().includes(q));

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-faint" style="text-align:center; padding:30px;">کاربری یافت نشد.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered
    .map(
      (u) => `
    <tr data-id="${u.id}">
      <td><span class="online-dot ${u.online ? "on" : ""}" title="${u.online ? "آنلاین" : "آفلاین"}"></span></td>
      <td>
        <div class="user-cell">
          <div>
            <div class="mono" style="font-weight:700;">${u.username}</div>
            ${u.note ? `<div class="text-faint" style="font-size:11.5px;">${u.note}</div>` : ""}
          </div>
        </div>
      </td>
      <td>${statusBadge(u.status)}</td>
      <td>${usageBarHtml(u)}</td>
      <td style="font-size:12.5px;">${u.expire_at ? fmtDate(u.expire_at) : "نامحدود"}</td>
      <td style="font-size:12.5px;">${u.last_connected_at ? fmtDate(u.last_connected_at) : "—"}</td>
      <td class="actions-cell">
        <button class="btn icon-only sm" title="کپی لینک" onclick="copyText('${u.sub_link}')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>
        <button class="btn icon-only sm" title="جزئیات" onclick="openDetail(${u.id})">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>
        </button>
      </td>
    </tr>`
    )
    .join("");
}

async function loadUsers() {
  try {
    allUsers = await api("/api/users");
    renderRows();
  } catch (e) {
    toast(e.message, "error");
  }
}

document.getElementById("search-input").addEventListener("input", renderRows);
document.getElementById("refresh-btn").addEventListener("click", loadUsers);
document.getElementById("new-user-btn").addEventListener("click", () => openModal("modal-create"));

document.getElementById("create-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const payload = {
    username: fd.get("username"),
    note: fd.get("note") || null,
    data_limit_gb: fd.get("data_limit_gb") ? parseFloat(fd.get("data_limit_gb")) : null,
    expire_days: fd.get("expire_days") ? parseInt(fd.get("expire_days"), 10) : null,
  };
  try {
    await api("/api/users", { method: "POST", body: payload });
    toast("کاربر با موفقیت ساخته شد.");
    closeModal("modal-create");
    ev.target.reset();
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

function fillDetail(u) {
  currentUserId = u.id;
  document.getElementById("detail-username").textContent = u.username;
  document.getElementById("detail-created").textContent = "تاریخ ساخت: " + fmtDate(u.created_at);
  document.getElementById("detail-sublink").value = u.sub_link;
  document.getElementById("detail-dl-udp").href = `/api/users/${u.id}/config/udp`;
  document.getElementById("detail-dl-tcp").href = `/api/users/${u.id}/config/tcp`;

  const used = u.data_used_bytes || 0;
  if (u.data_limit_bytes) {
    const pct = Math.min(100, Math.round((used / u.data_limit_bytes) * 100));
    document.getElementById("detail-usage-text").textContent = `${humanBytes(used)} / ${humanBytes(u.data_limit_bytes)} (${pct}%)`;
    document.getElementById("detail-usage-bar").style.width = pct + "%";
  } else {
    document.getElementById("detail-usage-text").textContent = `${humanBytes(used)} / نامحدود`;
    document.getElementById("detail-usage-bar").style.width = "6%";
  }

  document.getElementById("edit-note").value = u.note || "";
  document.getElementById("edit-limit").value = "";
  document.getElementById("edit-expire").value = "";

  document.getElementById("detail-toggle").textContent = u.enabled ? "غیرفعال کردن" : "فعال کردن";
}

async function openDetail(id) {
  try {
    const u = await api(`/api/users/${id}`);
    fillDetail(u);
    openModal("modal-detail");
  } catch (e) {
    toast(e.message, "error");
  }
}

document.getElementById("edit-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const limitVal = fd.get("data_limit_gb");
  const expireVal = fd.get("expire_days");
  const payload = { note: fd.get("note") };
  if (limitVal !== "") payload.data_limit_gb = parseFloat(limitVal);
  if (expireVal !== "") payload.expire_days = parseInt(expireVal, 10);

  try {
    const u = await api(`/api/users/${currentUserId}`, { method: "PATCH", body: payload });
    toast("تغییرات ذخیره شد.");
    fillDetail(u);
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

document.getElementById("detail-reset-usage").addEventListener("click", async () => {
  try {
    const u = await api(`/api/users/${currentUserId}/reset_usage`, { method: "POST" });
    toast("مصرف صفر شد.");
    fillDetail(u);
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

document.getElementById("detail-toggle").addEventListener("click", async () => {
  try {
    const u = await api(`/api/users/${currentUserId}/toggle`, { method: "POST" });
    toast(u.enabled ? "کاربر فعال شد." : "کاربر غیرفعال شد.");
    fillDetail(u);
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

document.getElementById("detail-regen").addEventListener("click", async () => {
  if (!confirm("لینک اشتراک قبلی از کار می‌افتد. ادامه می‌دهید؟")) return;
  try {
    const u = await api(`/api/users/${currentUserId}/regenerate_token`, { method: "POST" });
    toast("لینک جدید ساخته شد.");
    fillDetail(u);
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

document.getElementById("detail-delete").addEventListener("click", async () => {
  const u = allUsers.find((x) => x.id === currentUserId);
  if (!confirm(`کاربر «${u ? u.username : ""}» برای همیشه حذف می‌شود و گواهی آن باطل می‌گردد. مطمئن هستید؟`)) return;
  try {
    await api(`/api/users/${currentUserId}`, { method: "DELETE" });
    toast("کاربر حذف شد.");
    closeModal("modal-detail");
    loadUsers();
  } catch (e) {
    toast(e.message, "error");
  }
});

loadUsers();
setInterval(loadUsers, 20000);
