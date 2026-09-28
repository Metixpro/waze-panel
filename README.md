# ⚡ Waze Panel

پنل مدیریت OpenVPN با نصب یک‌دستوری، داشبورد زنده، کاربران با پروتکل **UDP و TCP همزمان**،
لینک اشتراک اختصاصی و کنترل لحظه‌ای حجم و انقضا — روی هسته‌ی اصلی OpenVPN.

<p>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-7c5cff">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-35d0ff">
  <img alt="openvpn" src="https://img.shields.io/badge/OpenVPN-2.5%2B-33d69f">
  <img alt="e2e" src="https://img.shields.io/badge/e2e-15%2F15%20passing-33d69f">
</p>

<p align="center">
  <img src="docs/screenshots/dashboard-dark.png" alt="داشبورد" width="100%">
</p>

---

## ✨ امکانات

**سرور و OpenVPN**
- نصب خودکار با یک دستور: OpenVPN و easy-rsa، CA و گواهی سرور، **دو سرویس همزمان** (UDP و TCP)، NAT و فایروال، و پنل به‌صورت سرویس systemd.
- هر کاربر یک **گواهی TLS واقعی** دارد که هم روی UDP و هم TCP کار می‌کند (AES-256-GCM + tls-crypt).
- **کنترل لحظه‌ای**: اتمام حجم، انقضا یا غیرفعال‌سازی، اتصال را **وسط کار** قطع می‌کند (با پیام HALT، تا کلاینت پشت سر هم تلاش نکند).
- حذف کاربر گواهی را **باطل** می‌کند (CRL) — رد شدن در همان مرحله‌ی TLS.
- گواهی‌ها و CRL با اعتبار ۱۰ ساله، به‌همراه امضای مجدد روزانه‌ی CRL.

**پنل**
- داشبورد زنده: آنلاین‌ها با پروتکل و IP، مصرف امروز با مقایسه‌ی دیروز، نمودار ۱۴ روزه با تاریخ شمسی، سلامت سرور (CPU/RAM/دیسک)، وضعیت کاربران، پرمصرف‌های امروز و لیست **نیاز به تمدید** با تمدید یک‌کلیکی.
- مدیریت کاربران با فیلتر (آنلاین، رو به اتمام، منقضی، …)، جستجو، مرتب‌سازی و کشوی جزئیات: حلقه‌ی مصرف، نمودار مصرف هر کاربر، **تمدید سریع** (+۳۰ روز، +۱۰ گیگ، …)، لینک اشتراک با QR.
- ساخت کاربر با پلن‌های آماده و صفحه‌ی «آماده است» برای اشتراک‌گذاری فوری لینک و QR.
- تم تاریک و روشن، فارسی کامل با اعداد فارسی و تاریخ شمسی، طراحی مخصوص موبایل (ناوبری پایین صفحه).
- **بدون وابستگی به CDN**: فونت و کتابخانه‌ها روی خود سرور هستند؛ در شبکه‌هایی که CDNها فیلتر یا کند هستند هم کامل کار می‌کند.
- پشتیبان‌گیری کامل (دیتابیس + تنظیمات + CA) از داخل پنل.

**لینک اشتراک (برای کاربر نهایی)**
- بدون لاگین: درصد مصرف، باقیمانده، روزهای اعتبار و دانلود کانفیگ UDP/TCP.
- راهنمای اتصال مخصوص اندروید، آیفون، ویندوز و مک (تشخیص خودکار دستگاه).
- QR برای باز کردن همان صفحه روی دستگاه دیگر.

## 📸 تصاویر

| | |
|---|---|
| ![کاربران](docs/screenshots/users.png) | ![جزئیات کاربر](docs/screenshots/user-drawer.png) |
| ![ساخت کاربر](docs/screenshots/create-user.png) | ![داشبورد روشن](docs/screenshots/dashboard-light.png) |

<p align="center">
  <img src="docs/screenshots/users-phone.png" alt="موبایل" width="260">
  &nbsp;&nbsp;
  <img src="docs/screenshots/subscription-phone.png" alt="صفحه اشتراک" width="260">
</p>

## 🚀 نصب

روی سرور **Ubuntu 22.04 / 24.04** یا **Debian 12** با دسترسی root:

```bash
curl -fsSL https://raw.githubusercontent.com/Metixpro/waze-panel/main/install.sh -o install.sh
sudo bash install.sh
```

نصب‌کننده آدرس سرور، یوزرنیم ادمین، پورت‌ها (با بررسی اشغال نبودن) و در صورت تمایل دامنه را می‌پرسد و در پایان آدرس پنل و رمز را نشان می‌دهد. نصب بدون سؤال: `sudo bash install.sh --yes`

> پنل به Python 3.10 یا جدیدتر نیاز دارد؛ روی Ubuntu 20.04 و Debian 11 نصب‌کننده همان ابتدا با پیام واضح متوقف می‌شود.

| فلگ | توضیح | پیش‌فرض |
|---|---|---|
| `--yes` | بدون سؤال | - |
| `--panel-port PORT` | پورت پنل | `8000` |
| `--udp-port PORT` | پورت OpenVPN روی UDP | `1194` |
| `--tcp-port PORT` | پورت OpenVPN روی TCP | `443` (یا `8443` با دامنه) |
| `--admin-user NAME` | یوزرنیم ادمین | `admin` |
| `--admin-pass PASS` | رمز ادمین | تصادفی (در به‌روزرسانی بدون تغییر) |
| `--server-address ADDR` | آی‌پی/دامنه‌ی داخل کانفیگ‌ها | تشخیص خودکار |
| `--domain example.com` | Nginx + HTTPS رایگان (Let's Encrypt) برای پنل | - |
| `--no-nginx` | هرگز Nginx نصب نشود | - |

## 🧰 مدیریت از ترمینال

بعد از نصب، دستور `waze-panel` روی سرور در دسترس است:

```bash
waze-panel                   # منوی تعاملی
waze-panel status            # وضعیت سرویس‌ها، تعداد کاربران، اعتبار CRL
waze-panel restart
waze-panel logs [panel|udp|tcp]
waze-panel reset-password    # فراموشی رمز ادمین
waze-panel backup [dir]      # پشتیبان کامل (دیتابیس + تنظیمات + CA)
waze-panel restore <file>    # بازگردانی، مثلا روی سرور جدید
waze-panel update            # به‌روزرسانی به آخرین نسخه
waze-panel uninstall
```

**به‌روزرسانی امن است:** اجرای دوباره‌ی `install.sh` (یا `waze-panel update`) پورت‌ها، آدرس، کلیدها، کاربران، گواهی‌ها و رمز ادمین را نگه می‌دارد و فقط کد و سرویس‌ها را تازه می‌کند.

## 🏗️ معماری

```
 مرورگر ادمین / کاربر ──▶ FastAPI (waze-panel.service) ──▶ SQLite
                                   │  management interface (127.0.0.1)
                    ┌──────────────┴──────────────┐
            OpenVPN UDP                      OpenVPN TCP
      (openvpn-server@waze-udp)        (openvpn-server@waze-tcp)
                    │  client-connect / client-disconnect (nobody:nogroup)
                    └──────────────┬──────────────┘
                                   ▼
                 /internal/hooks/*  — فقط loopback + توکن (hook.env)
```

- **مجوز اتصال** در هوک `client-connect` بررسی می‌شود (فعال، منقضی، حجم) و رد شدن در سطح خود OpenVPN است.
- **حسابداری مصرف** از دو مسیر: خواندن دوره‌ای رابط مدیریتی (برای اتصال‌های طولانی و قطع آنی) و هوک `client-disconnect` (برای دقت نهایی هر سشن)، با قفل و شناسه‌ی سشن تا چیزی دوبار شمرده نشود.
- هوک‌ها بعد از افت دسترسی OpenVPN با کاربر `nobody` اجرا می‌شوند؛ به همین دلیل فایل جداگانه‌ی `hook.env` (فقط پورت و توکن، `root:nogroup 0640`) دارند.

## 🔐 امنیت

- ورود ادمین با محافظت در برابر حدس رمز (۵ تلاش ناموفق = ۱۰ دقیقه مسدودی برای آن IP).
- با `--domain` پنل فقط روی `127.0.0.1` گوش می‌دهد و از طریق Nginx + HTTPS در دسترس است؛ بدون دامنه، پنل روی HTTP است — حتما رمز قوی بگذارید.
- API داخلی هوک‌ها فقط به درخواست مستقیم از loopback جواب می‌دهد و در Nginx هم بسته است.
- دیتابیس و `panel.env` فقط برای root قابل خواندن‌اند (`0600`).
- لینک اشتراک بدون لاگین باز می‌شود؛ اگر لو رفت، از «لینک جدید» در جزئیات کاربر استفاده کنید.

## 🧪 تست

`tests/e2e.sh` روی یک نصب واقعی، کلاینت واقعی OpenVPN را از یک network namespace جدا به هر دو سرویس وصل می‌کند و ۱۵ مورد را بررسی می‌کند: اجازه‌ی اتصال، آنلاین شدن، حسابداری ترافیک بدون شمارش دوباره، قطع وسط اتصال با اتمام حجم، رد اتصال مجدد، باطل شدن گواهی پس از حذف و اعتبار CRL.

```bash
sudo bash tests/e2e.sh          # KEEP=1 برای نگه داشتن لاگ‌ها
```

## 📂 ساختار پروژه

```
app/
  main.py  config.py  models.py  cli.py  backup.py
  openvpn/   certs.py (easy-rsa) · templates.py (.ovpn) · mgmt.py · scheduler.py
  routers/   auth · dashboard · users · subscription · internal · settings
  templates/ صفحات Jinja2 (RTL)       static/  css · js · vendor (فونت و کتابخانه‌های محلی)
scripts/     هوک‌های OpenVPN · قالب کانفیگ‌ها و systemd · waze-panel-cli.sh
tests/e2e.sh
install.sh · uninstall.sh
```

## 🛠️ توسعه محلی

```bash
python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt
export DATA_DIR=$PWD/.devdata DB_PATH=$PWD/.devdata/dev.db && mkdir -p .devdata
python -m app.cli create-admin --username admin --password admin1234
uvicorn app.main:app --reload
```

ساخت گواهی و آمار زنده به OpenVPN و easy-rsa نیاز دارند؛ برای تست کامل از `tests/e2e.sh` روی یک سرور استفاده کنید.

## 🗑️ حذف

```bash
sudo waze-panel uninstall        # یا: sudo bash /opt/waze-panel/uninstall.sh --purge
```

## 📄 مجوز

MIT — [LICENSE](./LICENSE). فونت وزیرمتن تحت SIL OFL و کتابخانه‌های داخل `app/static/vendor` تحت MIT هستند ([جزئیات](app/static/vendor/LICENSES.md)).
