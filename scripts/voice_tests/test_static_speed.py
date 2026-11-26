"""Скорость кабинета (ветка 1811-niche-admin-speed): метки содержимого у скриптов и стилей, кэш.

Без сети и без БД (статика отдаётся без запросов к базе):
- сервер дописывает в HTML ?v=h<хэш> к <script src> и <link href> на /static/*.js|css
  (ручные ?v=… заменяются), не трогает внешние адреса и картинки;
- ?v=h<хэш> → Cache-Control immutable на год; без метки → no-cache (как раньше);
- поменялся скрипт — поменялась его метка в HTML и ETag страницы; повтор с ETag → 304;
- service worker: страницы «сначала из кэша», метки — из кэша без сети, /api не трогает;
- словарь языка хранится в localStorage, общие запросы страниц запоминаются (ui.js).
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
os.environ.update(HOST_URL="https://t.voksyai.online", PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="static-test-secret-0123456789abcdef")
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


cl = TestClient(app_module.app, raise_server_exceptions=False)
STATIC = os.path.join(ROOT, "backend", "static")
tmp_js = os.path.join(STATIC, "zz-speedtest.js")
tmp_html = os.path.join(STATIC, "zz-speedtest.html")
try:
    with open(tmp_js, "w") as f:
        f.write("window.A = 1;\n")
    with open(tmp_html, "w") as f:
        f.write('<!doctype html><html><head>'
                '<link rel="stylesheet" href="/static/css/voksiai.css?v=20261113f">'
                '<script defer src="/static/zz-speedtest.js"></script>'
                '<script src="https://cdn.example.com/x.js"></script>'
                '<img src="/static/brand/logo.svg"></head><body>ok</body></html>')

    r = cl.get("/static/zz-speedtest.html")
    body = r.text
    m = re.search(r'src="/static/zz-speedtest\.js\?v=(h[0-9a-f]{10})"', body)
    check("HTML: скрипт получил метку содержимого", r.status_code == 200 and bool(m), body[:300])
    check("HTML: ручная метка стиля заменена хэшем", re.search(r'href="/static/css/voksiai\.css\?v=h[0-9a-f]{10}"', body) is not None)
    check("HTML: внешний скрипт и картинка не тронуты", 'src="https://cdn.example.com/x.js"' in body and 'src="/static/brand/logo.svg"' in body)
    check("HTML: no-cache + ETag", r.headers.get("cache-control") == "no-cache" and r.headers.get("etag"), dict(r.headers))
    etag1 = r.headers.get("etag")
    r304 = cl.get("/static/zz-speedtest.html", headers={"If-None-Match": etag1})
    check("HTML: повтор с ETag → 304", r304.status_code == 304, r304.status_code)

    v1 = m.group(1) if m else ""
    r = cl.get(f"/static/zz-speedtest.js?v={v1}")
    check("метка → immutable на год", "immutable" in (r.headers.get("cache-control") or ""), r.headers.get("cache-control"))
    r = cl.get("/static/zz-speedtest.js")
    check("без метки → no-cache", r.headers.get("cache-control") == "no-cache", r.headers.get("cache-control"))
    r = cl.get("/static/zz-speedtest.js?v=1")
    check("ручная метка ?v=1 → no-cache (не навсегда)", r.headers.get("cache-control") == "no-cache", r.headers.get("cache-control"))

    time.sleep(0.01)
    with open(tmp_js, "w") as f:
        f.write("window.A = 2; // новая версия\n")
    r = cl.get("/static/zz-speedtest.html")
    m2 = re.search(r'src="/static/zz-speedtest\.js\?v=(h[0-9a-f]{10})"', r.text)
    check("скрипт поменялся → новая метка и новый ETag", m2 and m2.group(1) != v1 and r.headers.get("etag") != etag1,
          (v1, m2 and m2.group(1)))
    r = cl.get("/static/zz-speedtest.html", headers={"If-None-Match": etag1})
    check("старый ETag после обновления скрипта → 200", r.status_code == 200, r.status_code)

    r = cl.get("/static/dashboard.html")
    check("настоящая страница кабинета: все свои скрипты с меткой",
          r.status_code == 200 and not re.search(r'<script[^>]+src="/static/[^"?]+\.js"', r.text), r.status_code)
    r = cl.head("/static/dashboard.html")
    check("HEAD страницы: 200 без тела", r.status_code == 200 and not r.text, r.status_code)
finally:
    for p in (tmp_js, tmp_html):
        try:
            os.remove(p)
        except OSError:
            pass

sw = open(os.path.join(STATIC, "sw.js"), encoding="utf-8").read()
check("SW: новая версия кэша (v4 — 26.11.2026, push ИИ-управляющего; v3 — телефон, финансы)", "voksy-app-v4" in sw)
check("SW: метка ?v=h… — из кэша", "v=h[0-9a-f]{10}" in sw and "immutable(url)" in sw)
check("SW: страницы — сначала из кэша, обновление в фоне", "isPage(req, url)" in sw and "e.waitUntil(net" in sw)
check("SW: /api не трогает", "url.pathname.indexOf('/static/') !== 0) return" in sw)
i18n = open(os.path.join(STATIC, "js", "i18n.js"), encoding="utf-8").read()
check("словарь языка — в localStorage", "localStorage.setItem(cacheKey" in i18n)
ui = open(os.path.join(STATIC, "js", "ui.js"), encoding="utf-8").read()
check("общие запросы страниц запоминаются (профиль, кошелёк, тариф, непрочитанные)",
      "MEMO_RULES" in ui and "users\\/me" in ui and "wallet\\/balance" in ui and "memoClear" in ui)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
