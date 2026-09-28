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
