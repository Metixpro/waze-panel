// Dashboard: live numbers, traffic, server health and what needs attention.
"use strict";

let stats = null;
let users = [];
const charts = {};

const STATUS_ORDER = ["active", "over_quota", "expired", "disabled", "revoked"];
const statusColor = (k) => ({
  active: cssVar("--green"), over_quota: cssVar("--amber"), expired: "#e0823d", disabled: cssVar("--gray"), revoked: cssVar("--red"),
})[k];

function big(bytesN) {
  const p = bytesParts(bytesN);
  return `<bdi class="num">${p.value}<span class="unit">${p.unit}</span></bdi>`;
}

// Soft fill under a line in the brand blue.
function areaFill(ctx, area, alpha) {
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, `rgba(79, 124, 255, ${alpha})`);
  g.addColorStop(1, "rgba(79, 124, 255, 0)");
  return g;
}

function renderKpis() {
  const s = stats;
  const count = (p) => s.online_sessions.filter((x) => x.proto === p).length;
  const parts = [["udp", "UDP", "var(--accent-fill)"], ["tcp", "TCP", "var(--teal)"], ["xray", "Xray", "var(--violet)"]]
    .map(([p, label, color]) => ({ label, color, n: count(p) }))
    .filter((x) => x.label !== "Xray" || x.n || (s.xray && s.xray.inbounds));
  const n = parts.reduce((a, x) => a + x.n, 0);
  $("#k-online").textContent = fa(s.online_count);
  $("#k-split").innerHTML = n ? parts.map((x) => `<i style="width:${(x.n / n) * 100}%;background:${x.color}"></i>`).join("") : "";
  $("#k-split-legend").innerHTML = parts.map((x) => `<span><i style="background:${x.color}"></i>${x.label} ${fa(x.n)}</span>`).join("");

  $("#k-today").innerHTML = big(s.today_bytes);
  const vals = s.chart_values;
  const yesterday = vals[vals.length - 2] || 0;
  if (yesterday) {
    const d = Math.round(((s.today_bytes - yesterday) / yesterday) * 100);
    $("#k-delta").innerHTML = `<span class="delta ${d < 0 ? "neg" : ""}" title="نسبت به دیروز">${d >= 0 ? "▲" : "▼"} ${fa(Math.abs(d))}٪</span>`;
  } else {
    $("#k-delta").innerHTML = "";
  }

  const sum14 = vals.reduce((a, b) => a + b, 0);
  $("#k-total").innerHTML = big(s.total_bytes);
  $("#k-avg").innerHTML = bytesHtml(sum14 / (vals.length || 1));
  $("#k-14").innerHTML = bytesHtml(sum14);

  const c = s.status_counts;
  const total = s.total_users || 0;
  $("#k-users").textContent = fa(total);
  $("#k-users-split").innerHTML = total
    ? STATUS_ORDER.filter((k) => c[k]).map((k) => `<i style="width:${(c[k] / total) * 100}%;background:${statusColor(k)}" title="${STATUS[k].label}: ${fa(c[k])}"></i>`).join("")
    : "";
  const inactive = (c.expired || 0) + (c.over_quota || 0) + (c.disabled || 0) + (c.revoked || 0);
  $("#k-users-sub").innerHTML =
    `<span><i style="background:var(--green)"></i>${fa(c.active || 0)} فعال</span>` +
    (inactive ? `<span><i style="background:var(--amber)"></i>${fa(inactive)} غیرفعال یا تمام‌شده</span>` : "");
  $("#k-ending").innerHTML = s.ending_soon ? `<span class="delta warn" title="کمتر از ۳ روز یا بیش از ۸۵٪ حجم">${fa(s.ending_soon)} رو به اتمام</span>` : "";

  const peak = Math.max(...vals);
  $("#traffic-sub").innerHTML = peak
    ? `مجموع ${bytesHtml(sum14)} · بیشترین ${bytesHtml(peak)} در ${jShort(s.chart_dates[vals.indexOf(peak)])}`
    : "هنوز ترافیکی ثبت نشده";
}

function renderTraffic() {
  const labels = stats.chart_dates.map(jShort);
  const data = stats.chart_values;
  if (charts.traffic) {
    charts.traffic.data.labels = labels;
    charts.traffic.data.datasets[0].data = data;
    charts.traffic.update("none");
    return;
  }
  const line = cssVar("--accent-fill");
  charts.traffic = new Chart($("#traffic-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [{
        data, borderColor: line, borderWidth: 2.25, tension: 0.35, fill: true,
        pointRadius: (c) => (c.dataIndex === data.length - 1 ? 4 : 0),
        pointBackgroundColor: line, pointBorderColor: cssVar("--surface"), pointBorderWidth: 2,
        pointHoverRadius: 5, pointHoverBackgroundColor: line, pointHoverBorderColor: cssVar("--surface"), pointHoverBorderWidth: 2,
        backgroundColor: (c) => (c.chart.chartArea ? areaFill(c.chart.ctx, c.chart.chartArea, 0.28) : "transparent"),
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => jDate(stats.chart_dates[items[0].dataIndex] + "T12:00:00", { weekday: "long", month: "long", day: "numeric" }),
            label: (item) => bytesTxt(item.raw),
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
        y: { beginAtZero: true, border: { display: false }, grid: { color: cssVar("--line") }, ticks: { maxTicksLimit: 5, callback: (v) => bytesTxt(v) } },
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
  charts.spark = new Chart($("#spark"), {
    type: "line",
    data: { labels: data.map((_, i) => i), datasets: [{ data, borderColor: cssVar("--accent-fill"), borderWidth: 2, tension: 0.4, pointRadius: 0, fill: true,
      backgroundColor: (c) => (c.chart.chartArea ? areaFill(c.chart.ctx, c.chart.chartArea, 0.22) : "transparent") }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
      layout: { padding: { top: 4 } },
    },
  });
}

function renderStatus() {
  const counts = stats.status_counts;
  const values = STATUS_ORDER.map((k) => counts[k] || 0);
  const total = values.reduce((a, b) => a + b, 0);
  $("#donut-total").textContent = fa(total);
  $("#status-legend").innerHTML = STATUS_ORDER
    .filter((k) => counts[k] || k === "active")
    .map((k) => `<div class="legend-row"><i style="background:${statusColor(k)}"></i><span class="lbl">${STATUS[k].label}</span><b>${fa(counts[k] || 0)}</b><small>${total ? fa(Math.round(((counts[k] || 0) / total) * 100)) + "٪" : ""}</small></div>`)
    .join("");
  const data = total ? values : [1];
  const bg = total ? STATUS_ORDER.map(statusColor) : [cssVar("--surface-3")];
  if (charts.status) {
    charts.status.data.datasets[0].data = data;
    charts.status.data.datasets[0].backgroundColor = bg;
    charts.status.update("none");
    return;
  }
  charts.status = new Chart($("#status-chart"), {
    type: "doughnut",
    data: { labels: STATUS_ORDER.map((k) => STATUS[k].label), datasets: [{ data, backgroundColor: bg, borderWidth: 0, spacing: total ? 2 : 0, borderRadius: 3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "76%",
      plugins: { legend: { display: false }, tooltip: { enabled: !!total, callbacks: { label: (i) => ` ${i.label}: ${fa(i.raw)}` } } },
    },
  });
}

function renderHealth() {
  const sys = stats.system;
  const gauge = (p) => ring(p, { size: 84, stroke: 8, color: levelColor(p), label: `<b>${fa(Math.round(p))}٪</b>` });
  $("#g-cpu").innerHTML = gauge(sys.cpu_percent);
  $("#g-cpu-sub").textContent = `${fa(sys.cpu_count)} هسته`;
  $("#g-cpu-sub").title = `بار سیستم ${fa(sys.load1, 2)}`;
  $("#g-mem").innerHTML = gauge(sys.mem_percent);
  $("#g-mem-sub").innerHTML = `از ${bytesHtml(sys.mem_total)}`;
  $("#g-mem-sub").title = `${bytes(sys.mem_used)} مصرف شده`;
  $("#g-disk").innerHTML = gauge(sys.disk_percent);
  $("#g-disk-sub").innerHTML = `از ${bytesHtml(sys.disk_total)}`;
  $("#g-disk-sub").title = `${bytes(sys.disk_used)} پر شده`;
  $("#uptime").textContent = `روشن از ${durationFa(sys.uptime_seconds)} پیش`;
}

function renderServices() {
  const inst = stats.instances;
  const count = (p) => stats.online_sessions.filter((x) => x.proto === p).length;
  const rows = ["udp", "tcp"].map((p) => {
    const up = inst[p].reachable;
    return `<div class="svc-row">
      <span class="dot ${up ? "ok" : "bad"}"></span>
      <div class="grow"><div class="t">OpenVPN ${p.toUpperCase()}</div><div class="s">${up ? `در حال اجرا · ${fa(count(p))} اتصال` : `متوقف · ببینید: waze-panel logs ${p}`}</div></div>
    </div>`;
  });
  const x = stats.xray;
  if (x && (x.inbounds || x.state !== "missing")) {
    const up = x.state === "running";
    const sub = x.state === "missing" ? "نصب نشده · waze-panel xray install"
      : !x.inbounds ? "ورودی روشنی ندارد"
      : up ? `${fa(x.inbounds)} ورودی · ${fa(count("xray"))} کاربر` : "متوقف · ببینید: waze-panel logs xray";
    rows.push(`<a class="svc-row" href="/xray">
      <span class="dot ${up && x.inbounds ? "ok" : x.inbounds ? "bad" : ""}"></span>
      <div class="grow"><div class="t">Xray ${x.version ? `<span class="mono muted" style="font-weight:400">${esc(x.version)}</span>` : ""}</div><div class="s">${sub}</div></div>
    </a>`);
  }
  for (const r of stats.relays || []) {
    const state = r.ok === true ? `سالم · ${fa(r.ms)} میلی‌ثانیه` : r.ok === false ? "قطع است" : "هنوز بررسی نشده";
    rows.push(`<a class="svc-row" href="/settings#relay-card">
      <span class="dot ${r.ok === true ? "ok" : r.ok === false ? "bad" : ""}"></span>
      <div class="grow"><div class="t">${esc(r.name)}</div><div class="s">سرور واسط · ${state}${r.sessions ? ` · ${fa(r.sessions)} اتصال` : ""}</div></div>
    </a>`);
  }
  $("#services").innerHTML = rows.join("");
}

function renderOnline() {
  const sessions = stats.online_sessions;
  if (!sessions.length) {
    $("#online-list").innerHTML = `<div class="empty"><b>کسی متصل نیست</b>اتصال‌های جدید همین‌جا نمایش داده می‌شوند.</div>`;
    return;
  }
  $("#online-list").innerHTML = sessions.map((s) => `
    <a class="list-row" href="/users?open=${encodeURIComponent(s.username)}">
      ${avatar(s.username, true)}
      <div class="grow">
        <div class="name ltr" style="text-align:right">${esc(s.username)}</div>
        <div class="meta ellip">${s.proto === "xray" ? "Xray" : s.proto.toUpperCase()}${s.via ? ` · از طریق ${esc(s.via)}` : s.ip ? ` · <span class="mono ltr">${esc(s.ip)}</span>` : ""}</div>
      </div>
      <div class="end">${bytesHtml(s.bytes)}<small>${durationFa(stats.now - s.since)}</small></div>
    </a>`).join("");
}

function renderTop() {
  const top = stats.top_today;
  if (!top.length) {
    $("#top-list").innerHTML = `<div class="empty"><b>هنوز مصرفی ثبت نشده</b>مصرف امروز کاربران اینجا رتبه‌بندی می‌شود.</div>`;
    return;
  }
  const max = top[0].bytes || 1;
  $("#top-list").innerHTML = top.map((t, i) => `
    <a class="list-row" href="/users?open=${encodeURIComponent(t.username)}">
      ${avatar(t.username, false, "sm")}
      <div class="grow">
        <div class="row" style="justify-content:space-between;margin-bottom:7px"><span class="name ltr">${esc(t.username)}</span><span class="num" style="font-size:13px">${bytesHtml(t.bytes)}</span></div>
        <div class="bar thin"><span style="width:${Math.max(3, (t.bytes / max) * 100)}%"></span></div>
      </div>
    </a>`).join("");
}

function renderEnding() {
  const ending = users
    .filter((u) => u.ending_soon)
    .sort((a, b) => (a.days_left ?? 999) - (b.days_left ?? 999))
    .slice(0, 8);
  if (!ending.length) {
    $("#ending-list").innerHTML = `<div class="empty"><b>همه چیز مرتب است</b>کاربری نزدیک اتمام اعتبار یا حجم نیست.</div>`;
    return;
  }
  $("#ending-list").innerHTML = ending.map((u) => {
    const p = pct(u.data_used_bytes, u.data_limit_bytes);
    const byDays = u.days_left !== null && u.days_left <= 3;
    const why = byDays ? daysLeftLabel(u.days_left) : `${fa(p)}٪ حجم مصرف شده`;
    return `<div class="list-row clickable ending-row" data-open="${esc(u.username)}">
      ${avatar(u.username, u.online)}
      <div class="who-col">
        <div class="name ltr" style="text-align:right">${esc(u.username)}</div>
        <div class="meta ellip" style="color:var(--amber)">${why}</div>
      </div>
      <div class="use-col">
        ${u.data_limit_bytes
          ? `<div class="row" style="justify-content:space-between;font-size:12.5px;color:var(--text-3);margin-bottom:6px"><span>${bytesHtml(u.data_used_bytes)} از ${bytesHtml(u.data_limit_bytes)}</span><span class="num">${fa(p)}٪</span></div>
             <div class="bar thin ${levelBar(p)}"><span style="width:${Math.max(p, 2)}%"></span></div>`
          : `<span class="muted" style="font-size:12.5px">حجم نامحدود</span>`}
      </div>
      <div class="act-col">
        <button class="btn xs" type="button" data-renew="${u.id}" data-days="30">+۳۰ روز</button>
        ${u.data_limit_bytes ? `<button class="btn xs" type="button" data-renew="${u.id}" data-gb="10">+۱۰ گیگ</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

async function load(withUsers = false) {
  try {
    const [s, u] = await Promise.all([api("/api/dashboard/stats"), withUsers ? api("/api/users") : Promise.resolve(null)]);
    stats = s;
    if (u) users = u;
    setNavOnline(s.online_count);
    renderKpis();
    renderTraffic();
    renderSpark();
    renderStatus();
    renderHealth();
    renderServices();
    renderOnline();
    renderTop();
    if (u) renderEnding();
    $("#updated-at").textContent = `به‌روز شده ${new Date().toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" })}`;
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

$("#refresh-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, () => load(true)));
window.addEventListener("themechange", () => {
  Object.values(charts).forEach((c) => c.destroy());
  Object.keys(charts).forEach((k) => delete charts[k]);
  if (stats) { renderTraffic(); renderSpark(); renderStatus(); renderKpis(); }
});

$("#today-date").textContent = new Date().toLocaleDateString("fa-IR", { weekday: "long", month: "long", day: "numeric" });
load(true);
setInterval(() => { if (!document.hidden) load(false); }, 10000);
setInterval(() => { if (!document.hidden) api("/api/users").then((u) => { users = u; renderEnding(); }).catch(() => {}); }, 30000);
