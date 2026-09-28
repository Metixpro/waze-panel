// About page: live GitHub numbers, the update check, the changelog timeline.
"use strict";

(function () {
  const REPO_URL = "https://github.com/Metixpro/waze-panel";
  const dateOpts = { year: "numeric", month: "long", day: "numeric" };

  // `code` and **bold** inside an already-escaped changelog line
  const md = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const ago = (iso) => (iso ? relTime(iso) : "—");
  // version numbers are isolated, or "v1.6.0 · ۲" gets reordered inside Persian text
  const ver = (v) => `<bdi class="mono">v${esc(v)}</bdi>`;

  /* ------------------------------------------------------------ static bits */
  $$("time.tl-date").forEach((t) => { t.textContent = jDate(t.getAttribute("datetime") + "T12:00:00", dateOpts); });
  const releases = $("#h-releases");
  releases.textContent = fa(+releases.dataset.n);

  const updated = $("#sys-updated");
  if (updated.dataset.at) {
    updated.textContent = relTime(updated.dataset.at);
    updated.title = jDate(updated.dataset.at, { ...dateOpts, hour: "2-digit", minute: "2-digit" });
  }

  const more = $("#tl-more");
  function moreLabel(open) {
    more.querySelector("span").textContent = open ? "بستن نسخه‌های قبلی" : `${fa(+more.dataset.n)} نسخه‌ی قبلی`;
    more.classList.toggle("open", open);
  }
  if (more) {
    moreLabel(false);
    more.addEventListener("click", () => {
      const open = !more.classList.contains("open");
      $$(".tl-item.older").forEach((li) => li.classList.toggle("hide", !open));
      moreLabel(open);
    });
  }

  /* ------------------------------------------------ this server / reporting */
  function sysText() {
    const d = $("#sys-list").dataset;
    return [
      `Waze Panel ${d.version}${d.commit ? ` (${d.commit})` : ""}`,
      `OS: ${d.os || "?"}`,
      `OpenVPN: ${d.openvpn || "?"}`,
      `Python: ${d.python || "?"}`,
    ].join("\n");
  }
  $("#sys-copy").addEventListener("click", (ev) => copyText(sysText(), "مشخصات سرور کپی شد", ev.currentTarget));
  const body = `### چه اتفاقی افتاد؟\n\n\n### چطور تکرار می‌شود؟\n1. \n\n### محیط\n\`\`\`\n${sysText()}\n\`\`\`\n`;
  $("#sys-report").href = `${REPO_URL}/issues/new?body=${encodeURIComponent(body)}`;

  /* ------------------------------------------------------------- live data */
  function renderGithub(gh) {
    const repo = gh && gh.repo;
    $("#h-stars").textContent = repo ? fa(repo.stars) : "—";
    $("#h-forks").textContent = repo ? fa(repo.forks) : "—";
    $("#h-pushed").textContent = repo ? ago(repo.pushed_at) : "—";
    const star = $("#star-count");
    star.textContent = repo ? fa(repo.stars) : "";
    star.classList.toggle("hide", !repo || !repo.stars);

    const owner = gh && gh.owner;
    if (!owner) return;
    if (owner.bio) $("#dev-bio").textContent = owner.bio;
    const meta = [];
    if (owner.location) meta.push(`<span>${ic("map-pin")}${esc(owner.location)}</span>`);
    if (owner.followers) meta.push(`<span><b>${fa(owner.followers)}</b> دنبال‌کننده</span>`);
    if (owner.repos) meta.push(`<span><b>${fa(owner.repos)}</b> مخزن عمومی</span>`);
    $("#dev-meta").innerHTML = meta.join("");
    const blog = $("#dev-blog");
    if (owner.blog) { blog.href = owner.blog; blog.classList.remove("hide"); }
  }

  const stateHtml = (kind, icon, title, sub) => `
    <div class="upd-state ${kind}">
      <span class="upd-ic">${ic(icon)}</span>
      <div class="grow"><div class="upd-title">${title}</div><div class="upd-sub">${sub}</div></div>
    </div>`;

  const howTo = () => `
    <div class="upd-how">
      <div class="label">این دستور را روی سرور با کاربر root بزنید:</div>
      <div class="code-box"><code dir="ltr">waze-panel update</code><button class="btn icon sm" type="button" data-copy="waze-panel update" data-copy-msg="دستور کپی شد" title="کپی">${ic("copy")}</button></div>
      <div class="hint">کاربران، گواهی‌ها و تنظیمات سر جایشان می‌مانند؛ اتصال‌ها چند ثانیه قطع و دوباره وصل می‌شوند.</div>
    </div>`;

  function newsHtml(entries) {
    return `<div class="upd-news">${entries.slice(0, 3).map((e) => `
      <div class="un">
        <div class="un-head">${ver(e.version)}${e.title ? `<span>${esc(e.title)}</span>` : ""}</div>
        <ul>${(e.items || []).slice(0, 5).map((i) => `<li>${md(i)}</li>`).join("")}</ul>
      </div>`).join("")}
      ${entries.length > 3 ? `<div class="hint">و ${fa(entries.length - 3)} نسخه‌ی دیگر؛ همه در تاریخچه‌ی پایین.</div>` : ""}
    </div>`;
  }

  function commitsHtml(commits) {
    if (!commits.length) return "";
    return `<ul class="upd-commits">${commits.slice(0, 6).map((c) => `
      <li><a href="${REPO_URL}/commit/${esc(c.sha)}" target="_blank" rel="noopener">
        <span class="sha mono">${esc(c.sha.slice(0, 7))}</span><span class="msg" dir="auto">${esc(c.message)}</span><span class="t">${ago(c.date)}</span>
      </a></li>`).join("")}</ul>`;
  }

  // Versions that exist upstream but not here go on top of the timeline.
  function renderUpcoming(entries) {
    $$(".tl-item.upcoming").forEach((li) => li.remove());
    const list = $("#timeline");
    entries.slice().reverse().forEach((e) => {
      const li = document.createElement("li");
      li.className = "tl-item upcoming";
      li.innerHTML = `
        <div class="tl-head"><bdi class="tl-ver mono">v${esc(e.version)}</bdi><span class="badge green">جدید</span>
          ${e.date ? `<time class="tl-date">${jDate(e.date + "T12:00:00", dateOpts)}</time>` : ""}</div>
        ${e.title ? `<div class="tl-title">${esc(e.title)}</div>` : ""}
        <ul>${(e.items || []).map((i) => `<li>${md(i)}</li>`).join("")}</ul>`;
      list.prepend(li);
    });
  }

  function renderUpdate(d) {
    const u = d.update;
    const checked = d.checked_at ? relTime(new Date(d.checked_at * 1000).toISOString()) : "";
    const gh = d.github || {};
    let html;
    if (u.available && u.kind === "release") {
      const n = u.entries.length;
      html = stateHtml("new", "arrow-up-circle",
        `نسخه‌ی ${ver(u.latest)} آماده است`,
        `نسخه‌ی شما ${ver(u.current)}${n > 1 ? ` · ${fa(n)} نسخه عقب‌تر` : ""}`)
        + newsHtml(u.entries) + howTo();
    } else if (u.available && u.kind === "commits") {
      html = stateHtml("new", "arrow-up-circle", `${fa(u.behind)} اصلاح تازه برای همین نسخه`,
        "تغییرهای کوچکی که هنوز روی این سرور نیامده")
        + commitsHtml(u.commits) + howTo();
    } else if (!d.checked_at) {
      html = d.error
        ? stateHtml("off", "cloud-off", "گیت‌هاب در دسترس نیست", "این سرور به گیت‌هاب وصل نشد؛ کمی بعد دوباره «بررسی» را بزنید.")
        : d.auto_check
          ? stateHtml("idle", "refresh", "هنوز بررسی نشده", "«بررسی» را بزنید تا آخرین نسخه از گیت‌هاب خوانده شود.")
          : stateHtml("idle", "info", "بررسی خودکار خاموش است", "هر وقت خواستید «بررسی» را بزنید.");
    } else {
      html = stateHtml("ok", "check-circle", "آخرین نسخه را دارید",
        `${ver(u.current)} · بررسی ${checked}`);
      if (d.error) html += `<div class="hint upd-note">${ic("cloud-off")}آخرین تلاش برای بررسی ناموفق بود؛ این نتیجه مربوط به ${checked} است.</div>`;
      if (gh.head) {
        html += `
          <a class="upd-last" href="${REPO_URL}/commit/${esc(gh.head.sha)}" target="_blank" rel="noopener">
            <span class="k">آخرین تغییر در گیت‌هاب</span>
            <span class="row"><span class="sha mono">${esc(gh.head.sha.slice(0, 7))}</span><span class="msg" dir="auto">${esc(gh.head.message)}</span></span>
            <span class="t">${ago(gh.head.date)}</span>
          </a>`;
      }
    }
    $("#upd-body").innerHTML = html;
    renderUpcoming(u.kind === "release" ? u.entries : []);
  }

  function render(d) {
    $("#auto-check").checked = !!d.auto_check;
    renderGithub(d.github);
    renderUpdate(d);
  }

  async function load(force) {
    try {
      render(await api(force ? "/api/about/check" : "/api/about", force ? { method: "POST" } : {}));
    } catch (e) {
      $("#upd-body").innerHTML = stateHtml("off", "cloud-off", "اطلاعات دریافت نشد", esc(e.message));
    }
  }

  $("#upd-check").addEventListener("click", (ev) => withBusy(ev.currentTarget, () => load(true)));

  $("#auto-check").addEventListener("change", async (ev) => {
    const on = ev.target.checked;
    try {
      await api("/api/about/options", { method: "POST", body: { auto_check: on } });
      toast(on ? "بررسی خودکار روشن شد" : "بررسی خودکار خاموش شد");
    } catch (e) {
      ev.target.checked = !on;
      toast(e.message, "error");
    }
  });

  load(false);
})();
