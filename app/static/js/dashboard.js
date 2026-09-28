// Dashboard: live stats, charts and quick renewals.
"use strict";

let stats = null;
let users = [];
const charts = {};

const STATUS_COLORS = () => ({
  active: cssVar("--green"),
  over_quota: cssVar("--amber"),
  expired: "#f97316",
  disabled: cssVar("--slate"),
  revoked: cssVar("--rose"),
});

function destroyCharts() {
  Object.values(charts).forEach((c) => c && c.destroy());
  Object.keys(charts).forEach((k) => delete charts[k]);
}

function gradientFill(ctx, area, alpha) {
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, `rgba(124,92,255,${alpha})`);
  g.addColorStop(1, "rgba(124,92,255,0)");
  return g;
}

function renderTrafficChart() {
  const labels = stats.chart_dates.map(jShort);
  const data = stats.chart_values;
  if (charts.traffic) {
    charts.traffic.data.labels = labels;
    charts.traffic.data.datasets[0].data = data;
    charts.traffic.update("none");
    return;
  }
  charts.traffic = new Chart($("#traffic-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [{
        data,
        borderColor: "#7c5cff",
        borderWidth: 2.5,
        tension: 0.38,
        fill: true,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: "#7c5cff",
        pointHoverBorderColor: "#fff",
        pointHoverBorderWidth: 2,
        backgroundColor: (c) => (c.chart.chartArea ? gradientFill(c.chart.ctx, c.chart.chartArea, 0.35) : "transparent"),
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 650 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          rtl: true,
          textDirection: "rtl",
          displayColors: false,
          padding: 10,
          callbacks: {
            title: (items) => jDate(stats.chart_dates[items[0].dataIndex] + "T12:00:00", { weekday: "long", month: "long", day: "numeric" }),
            label: (item) => `ترافیک: ${bytesTxt(item.raw)}`,
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
        y: {
          beginAtZero: true,
          grid: { color: cssVar("--border") },
          border: { display: false },
          ticks: { maxTicksLimit: 5, callback: (v) => bytesTxt(v) },
        },
      },
    },
  });
}

function renderSpark() {
  const data = stats.chart_values;
  if (charts.spark) {
    charts.spark.data.datasets[0].data = data;
    charts.spark.update("none");
    return;
  }
  charts.spark = new Chart($("#spark-today"), {
    type: "line",
    data: { labels: data.map((_, i) => i), datasets: [{ data, borderColor: "#7c5cff", borderWidth: 2, tension: 0.4, pointRadius: 0, fill: true,
      backgroundColor: (c) => (c.chart.chartArea ? gradientFill(c.chart.ctx, c.chart.chartArea, 0.25) : "transparent") }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
      layout: { padding: 0 },
    },
  });
}

function renderStatus() {
  const order = ["active", "over_quota", "expired", "disabled", "revoked"];
  const colors = STATUS_COLORS();
  const counts = stats.status_counts;
  const values = order.map((k) => counts[k] || 0);
  const total = values.reduce((a, b) => a + b, 0);
  $("#donut-total").textContent = fa(total);
  $("#status-legend").innerHTML = order
    .filter((k) => counts[k] || k === "active")
    .map((k) => `<div class="legend-row"><i style="background:${colors[k]}"></i>${STATUS[k].label}<b>${fa(counts[k] || 0)}</b></div>`)
    .join("");
  const data = total ? values : [1];
  const bg = total ? order.map((k) => colors[k]) : [cssVar("--surface-3")];
  if (charts.status) {
    charts.status.data.datasets[0].data = data;
    charts.status.data.datasets[0].backgroundColor = bg;
    charts.status.update("none");
    return;
  }
  charts.status = new Chart($("#status-chart"), {
    type: "doughnut",
    data: { labels: order.map((k) => STATUS[k].label), datasets: [{ data, backgroundColor: bg, borderWidth: 0, spacing: total ? 2 : 0, borderRadius: 4 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "74%",
      plugins: { legend: { display: false }, tooltip: { enabled: !!total, rtl: true, textDirection: "rtl",
        callbacks: { label: (i) => ` ${i.label}: ${fa(i.raw)}` } } },
    },
  });
}

function renderKpis() {
  const s = stats;
  $("#k-online").textContent = fa(s.online_count);
  const udp = s.online_sessions.filter((x) => x.proto === "udp").length;
  const tcp = s.online_sessions.length - udp;
  $("#k-online-protos").innerHTML = `<span class="badge outline">UDP ${fa(udp)}</span><span class="badge outline">TCP ${fa(tcp)}</span>`;

  const today = bytesParts(s.today_bytes);
  $("#k-today").innerHTML = `<bdi class="num">${today.value}<span class="unit">${today.unit}</span></bdi>`;
  const vals = s.chart_values;
  const yesterday = vals[vals.length - 2] || 0;
  const delta = document.getElementById("k-today-delta");
  if (yesterday) {
    const d = Math.round(((s.today_bytes - yesterday) / yesterday) * 100);
    delta.textContent = `${d >= 0 ? "+" : ""}${fa(d)}٪ نسبت به دیروز`;
    delta.className = `badge ${d >= 0 ? "accent" : "outline"}`;
  } else {
    delta.textContent = "";
  }

  const total = bytesParts(s.total_bytes);
  $("#k-total").innerHTML = `<bdi class="num">${total.value}<span class="unit">${total.unit}</span></bdi>`;
  const avg = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
  $("#k-avg").innerHTML = `میانگین روزانه ${bytesHtml(avg)}`;

  $("#k-users").textContent = fa(s.total_users);
  $("#k-users-foot").innerHTML =
    `<span class="badge active"><i></i>${fa(s.status_counts.active)} فعال</span>` +
    (s.ending_soon ? `<span class="badge warn"><i></i>${fa(s.ending_soon)} رو به اتمام</span>` : "");

  const sum = vals.reduce((a, b) => a + b, 0);
  const peakIdx = vals.indexOf(Math.max(...vals));
  $("#traffic-sub").innerHTML = `مجموع ${bytesHtml(sum)} · اوج ${bytesHtml(vals[peakIdx] || 0)} در ${jShort(s.chart_dates[peakIdx])}`;
}

function renderHealth() {
  const sys = stats.system;
  $("#g-cpu").innerHTML = ring(sys.cpu_percent, { size: 78, stroke: 8, color: levelColor(sys.cpu_percent), label: `<b>${fa(Math.round(sys.cpu_percent))}٪</b>` });
  $("#g-cpu-sub").textContent = `${fa(sys.cpu_count)} هسته`;
  $("#g-mem").innerHTML = ring(sys.mem_percent, { size: 78, stroke: 8, color: levelColor(sys.mem_percent), label: `<b>${fa(Math.round(sys.mem_percent))}٪</b>` });
  $("#g-mem-sub").innerHTML = `${bytesHtml(sys.mem_used)} از ${bytesHtml(sys.mem_total)}`;
  $("#g-disk").innerHTML = ring(sys.disk_percent, { size: 78, stroke: 8, color: levelColor(sys.disk_percent), label: `<b>${fa(Math.round(sys.disk_percent))}٪</b>` });
  $("#g-disk-sub").innerHTML = `${bytesHtml(sys.disk_used)} از ${bytesHtml(sys.disk_total)}`;
  $("#uptime-badge").textContent = `روشن از ${durationFa(sys.uptime_seconds)} پیش`;
  $("#s-addr").textContent = stats.server_address;
  $("#s-load").textContent = `${fa(sys.load1, 2)} (${fa(sys.cpu_count)} هسته)`;
}

function renderOnline() {
  const list = $("#online-list");
  const sessions = stats.online_sessions;
  $("#online-count-badge").innerHTML = `<i></i>${fa(sessions.length)}`;
  if (!sessions.length) {
    list.innerHTML = `<div class="empty">${ic("wifi")}<b>کسی متصل نیست</b>اتصال‌های جدید اینجا لحظه‌ای نمایش داده می‌شوند.</div>`;
    return;
  }
  list.innerHTML = sessions.map((s) => `
    <a class="list-item" href="/users?open=${encodeURIComponent(s.username)}">
      ${avatar(s.username, true)}
      <div class="grow">
        <div class="name mono" style="text-align:right">${esc(s.username)}</div>
        <div class="meta"><span class="badge ${s.proto === "udp" ? "accent" : "info"}" style="height:20px">${s.proto.toUpperCase()}</span><span class="mono">${esc(s.ip)}</span></div>
      </div>
      <div class="side"><div style="font-weight:800">${bytesHtml(s.bytes)}</div><div class="muted">${durationFa(stats.now - s.since)}</div></div>
    </a>`).join("");
}

function renderTop() {
  const list = $("#top-list");
  const top = stats.top_today;
  if (!top.length) {
    list.innerHTML = `<div class="empty">${ic("activity")}<b>هنوز مصرفی ثبت نشده</b>مصرف امروز کاربران اینجا رتبه‌بندی می‌شود.</div>`;
    return;
  }
  const max = top[0].bytes || 1;
  list.innerHTML = top.map((t, i) => `
    <a class="hbar-row" href="/users?open=${encodeURIComponent(t.username)}">
      <div class="top"><span class="n mono">${fa(i + 1)}. ${esc(t.username)}</span><span>${bytesHtml(t.bytes)}</span></div>
      <div class="bar"><span style="width:${Math.max(4, (t.bytes / max) * 100)}%"></span></div>
    </a>`).join("");
}

function renderEnding() {
  const list = $("#ending-list");
  const ending = users
    .filter((u) => u.ending_soon)
    .sort((a, b) => (a.days_left ?? 999) - (b.days_left ?? 999))
    .slice(0, 6);
  if (!ending.length) {
    list.innerHTML = `<div class="empty">${ic("check")}<b>همه چیز مرتب است</b>کاربری در آستانه اتمام اعتبار یا حجم نیست.</div>`;
    return;
  }
  list.innerHTML = ending.map((u) => {
    const p = pct(u.data_used_bytes, u.data_limit_bytes);
    const why = u.days_left !== null && u.days_left <= 3 ? daysLeftLabel(u.days_left) : `${fa(p)}٪ حجم مصرف شده`;
    return `<div class="list-item" data-open="${esc(u.username)}">
      ${avatar(u.username, u.online)}
      <div class="grow"><div class="name mono" style="text-align:right">${esc(u.username)}</div><div class="meta"><span class="badge warn" style="height:20px">${why}</span></div></div>
      <div class="row" style="gap:6px">
        <button class="btn xs soft" data-renew="${u.id}" data-days="30">+۳۰ روز</button>
        ${u.data_limit_bytes ? `<button class="btn xs soft" data-renew="${u.id}" data-gb="10">+۱۰ گیگ</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

function renderServices() {
  const inst = stats.instances;
  const count = (p) => stats.online_sessions.filter((s) => s.proto === p).length;
  $("#services").innerHTML = ["udp", "tcp"].map((p) => {
    const up = inst[p].reachable;
    return `<div class="svc">
      <div class="svc-icon" style="${up ? "" : "background:var(--rose-soft);color:var(--rose)"}">${ic("shield")}</div>
      <div class="grow"><div class="t">OpenVPN ${p.toUpperCase()}</div><div class="s">پورت <span class="num">${inst[p].port}</span> · ${fa(count(p))} اتصال فعال</div></div>
      <span class="badge ${up ? "active" : "bad"}"><i></i>${up ? "در حال اجرا" : "متوقف"}</span>
    </div>`;
  }).join("");
}

async function load(withUsers = false) {
  try {
    const [s, u] = await Promise.all([api("/api/dashboard/stats"), withUsers ? api("/api/users") : Promise.resolve(null)]);
    stats = s;
    if (u) users = u;
    renderKpis();
    renderTrafficChart();
    renderSpark();
    renderStatus();
    renderHealth();
    renderOnline();
    renderTop();
    renderServices();
    if (u) renderEnding();
    $("#updated-at").textContent = `بروزرسانی ${new Date().toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  } catch (e) {
    toast(e.message, "error");
  }
}

document.addEventListener("click", async (ev) => {
  const renew = ev.target.closest("[data-renew]");
  if (renew) {
    ev.stopPropagation();
    const body = renew.dataset.days ? { add_days: Number(renew.dataset.days) } : { add_gb: Number(renew.dataset.gb) };
    await withBusy(renew, async () => {
      try {
        const u = await api(`/api/users/${renew.dataset.renew}`, { method: "PATCH", body });
        toast(`${u.username} تمدید شد`);
        await load(true);
      } catch (e) { toast(e.message, "error"); }
    });
    return;
  }
  const open = ev.target.closest("[data-open]");
  if (open) window.location.href = `/users?open=${encodeURIComponent(open.dataset.open)}`;
});

window.addEventListener("themechange", () => { destroyCharts(); if (stats) { renderTrafficChart(); renderSpark(); renderStatus(); } });

$("#today-date").textContent = new Date().toLocaleDateString("fa-IR", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
load(true);
setInterval(() => load(false), 10000);
setInterval(() => api("/api/users").then((u) => { users = u; renderEnding(); }).catch(() => {}), 30000);
