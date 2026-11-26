"""Мобильное приложение Voksy AI (26.11.2026): Android-приложение с сайта, вход внутри приложения.

Без сети и без БД:
- /.well-known/assetlinks.json — пакет online.voksyai.app и SHA-256 ключа подписи (+ из env
  ANDROID_APP_SHA256, кривые отпечатки отбрасываются) — без него Android покажет адресную строку;
- /app — страница «Скачать приложение» (Android — .apk, iPhone — «На экран Домой», компьютер — QR);
- /download/voksy-ai.apk — настоящий APK (zip с AndroidManifest.xml), тип для Android, имя файла;
- проект mobile/android: пакет, домен, стартовая страница внутри /static/, ключ и сборки не в git;
- экран входа /static/login.html: вход, регистрация, код из письма, новый пароль, «Назад», next только
  на страницы кабинета; словари RU/KY; кабинет при выходе и без входа ведёт на него, а не на сайт `/`;
- манифест: иконки maskable, ярлык «Записи»; кнопка «Установить приложение» на Android ведёт на /app;
- лендинг: сайт внутри приложения сразу открывает вход; страница Voksy Beauty — тарифы со сроками и
  экономией, ссылка на /app; /app в sitemap.
"""
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
os.environ.update(HOST_URL="https://t.voksyai.online", PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="mobile-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from fastapi.testclient import TestClient  # noqa: E402
import app as app_module  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


cl = TestClient(app_module.app, raise_server_exceptions=False)
FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")

# ── assetlinks
r = cl.get("/.well-known/assetlinks.json")
j = r.json() if r.status_code == 200 else []
t = (j[0] if j else {}).get("target", {})
check("assetlinks: 200 JSON, пакет и отпечаток", r.status_code == 200 and t.get("package_name") == "online.voksyai.app"
      and t.get("namespace") == "android_app" and j[0].get("relation") == ["delegate_permission/common.handle_all_urls"]
      and app_module.ANDROID_APP_SHA256[0] in t.get("sha256_cert_fingerprints", []), r.text[:200])
check("assetlinks: отпечатки в формате AA:BB:…", all(FP_RE.match(f) for f in t.get("sha256_cert_fingerprints", [])))
os.environ["ANDROID_APP_SHA256"] = "ab:" * 31 + "cd, кривой, " + app_module.ANDROID_APP_SHA256[0]
fps = cl.get("/.well-known/assetlinks.json").json()[0]["target"]["sha256_cert_fingerprints"]
check("assetlinks: ключ Google Play из env добавляется, кривой и повтор — нет",
      fps == [app_module.ANDROID_APP_SHA256[0], ("AB:" * 31 + "CD")], fps)
os.environ.pop("ANDROID_APP_SHA256", None)

# ── страница скачивания и сам APK
r = cl.get("/app")
check("/app: страница скачивания", r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
      and 'href="/download/voksy-ai.apk"' in r.text and "Скачать для Android" in r.text
      and "На экран" in r.text and "<svg" in r.text, r.status_code)
check("/app/ тоже открывается", cl.get("/app/").status_code == 200)
check("/app: без запрета индексации (страницу ищут)", "noindex" not in r.headers.get("x-robots-tag", ""))
r = cl.get("/download/voksy-ai.apk")
apk_ok = False
if r.status_code == 200:
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = set(z.namelist())
            apk_ok = "AndroidManifest.xml" in names and any(n.startswith("classes") and n.endswith(".dex") for n in names)
    except zipfile.BadZipFile:
        apk_ok = False
check("APK: тип для Android и имя файла", r.status_code == 200
      and r.headers.get("content-type") == "application/vnd.android.package-archive"
      and "Voksy-AI.apk" in r.headers.get("content-disposition", ""), dict(r.headers))
check("APK: настоящий файл приложения (манифест и код внутри), до 10 МБ", apk_ok and 200_000 < len(r.content) < 10_000_000,
      len(r.content))

# ── проект Android
tw = json.loads(read("mobile/android/twa-manifest.json"))
check("проект: пакет, домен, название", tw.get("packageId") == app_module.ANDROID_PACKAGE
      and tw.get("host") == "voksyai.online" and tw.get("name") == "Voksy AI" and tw.get("launcherName") == "Voksy AI")
check("проект: старт внутри кабинета, Android 7+", str(tw.get("startUrl", "")).startswith("/static/")
      and int(tw.get("minSdkVersion") or 0) >= 24 and tw.get("enableNotifications") is True)
gi = read("mobile/android/.gitignore")
check("проект: ключ подписи и сборки не попадают в git", "*.keystore" in gi and "*.apk" in gi and "*.aab" in gi)
# ── экран входа внутри приложения
lg = read("backend/static/login.html")
for name, needle in (("вход", 'id="v-login"'), ("регистрация", 'id="v-register"'), ("код из письма", 'id="v-code"'),
                     ("новый пароль", 'id="v-forgot"'), ("API входа", "'/auth/login'"), ("API регистрации", "'/auth/register'"),
                     ("API кода", "'/email-verification/verify'"), ("API нового пароля", "'/auth/reset-password-confirm'"),
                     ("«Назад» телефона", "popstate"), ("манифест приложения", 'rel="manifest"'),
                     ("свои шрифты (не Google)", "/static/css/fonts.css")):
    check("вход: " + name, needle in lg)
check("вход: без Google Fonts", "fonts.googleapis.com" not in lg)
check("вход: next — только страницы кабинета", r"/^\/static\/[\w\-./]+\.html" in lg)
ru, ky = json.loads(read("backend/static/i18n/ru.json"))["login"], json.loads(read("backend/static/i18n/ky.json"))["login"]
keys = set(re.findall(r'data-i18n(?:-html|-ph)?="login\.(\w+)"', lg)) | set(re.findall(r"\bt\('(\w+)'", lg))
missing = sorted(k for k in keys if k not in ru or k not in ky)
check("вход: все строки есть в словарях RU и KY", not missing, missing)
check("вход: кыргызский переведён, а не скопирован", ky.get("reg_title") != ru.get("reg_title") and ky.get("code_wrong") != ru.get("code_wrong"))
check("словарь: версия поднята (кэш браузера)", re.search(r"DICT_VERSION = '(\d+)'", read("backend/static/js/i18n.js"))
      and int(re.search(r"DICT_VERSION = '(\d+)'", read("backend/static/js/i18n.js")).group(1)) >= 16)

# ── кабинет не отправляет на сайт
bad = []
for dirpath, _dirs, files in os.walk("backend/static"):
    if "landing" in dirpath:
        continue
    for f in files:
        if f.endswith((".js", ".html")):
            p = os.path.join(dirpath, f)
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if re.search(r"location\.(href|replace|assign)\s*[=(]\s*['\"]/['\"?]", line):
                        bad.append(f"{p}:{i}")
check("кабинет: выход и «нет входа» ведут на экран входа, а не на сайт", not bad, bad[:5])
sb = read("backend/static/js/sidebar.js")
check("кабинет: «Выйти» → экран входа", "location.href = '/static/login.html?reason=logout'" in sb)
check("кабинет: «Установить приложение» на Android → /app", "location.href = '/app'" in sb and "function isAndroid" in sb)
check("кабинет: метка «открыто как приложение» (TWA, ?source=twa)", "android-app://" in sb and "vf_app" in sb)

# ── манифест
mf = json.loads(read("backend/static/manifest.json"))
check("манифест: иконки maskable", sum(1 for i in mf["icons"] if i.get("purpose") == "maskable") >= 2
      and all(os.path.isfile(os.path.join(ROOT, "backend", i["src"].lstrip("/"))) for i in mf["icons"]))
check("манифест: ярлык «Записи», старт внутри области приложения", any(s.get("name") == "Записи" for s in mf["shortcuts"])
      and mf["start_url"].startswith(mf["scope"]))

# ── лендинг
for page in ("backend/static/landing/index.html", "backend/static/landing/ru/index.html",
             "backend/static/landing/beauty-salon/index.html", "backend/static/landing/ru/beauty-salon/index.html"):
    h = read(page)
    check("лендинг: внутри приложения сразу вход — " + page.split("landing/")[1],
          "android-app://" in h and "/static/login.html?source=app" in h)
bs = read("backend/static/landing/ru/beauty-salon/index.html")
check("Beauty: тарифы со сроками и экономией", 'id="prices"' in bs and "экономия 1 197 сом" in bs and "33 516" in bs
      and "Выгодно" in bs and "без скидки" in bs)
check("Beauty: блок приложения и ссылка на /app", 'href="/app"' in bs and "Скачать приложение" in bs)
bk = read("backend/static/landing/beauty-salon/index.html")
check("Beauty (ky): тарифы по-кыргызски", "үнөмдөө 1 197 сом" in bk and "Пайдалуу" in bk)
check("лендинг: в подвале «Мобильное приложение»", 'href="/app"' in read("backend/static/landing/ru/index.html"))
check("sitemap: есть /app", "<loc>https://voksyai.online/app</loc>" in read("backend/static/landing/sitemap.xml"))
# цены лендинга = цены окна оплаты (те же скидки и тарифы по умолчанию)
from backend.services import plan_catalog as pc  # noqa: E402
sv = read("frontend/src/seo/services.js")
check("лендинг и окно оплаты: одинаковые скидки за срок",
      "BEAUTY_PERIODS = [[1, 0], [3, 10], [6, 20], [12, 30]]" in sv and pc.BEAUTY_PERIOD_DISCOUNTS == {1: 0, 3: 10, 6: 20, 12: 30})
check("лендинг и окно оплаты: одинаковые цены Beauty",
      all(f"price: {d['price']}" in sv for d in pc.BEAUTY_DEFAULTS))

print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
