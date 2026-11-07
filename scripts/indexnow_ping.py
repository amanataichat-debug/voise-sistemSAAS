"""
IndexNow: сообщить Яндексу и Bing, что страницы сайта обновились (поиск ChatGPT опирается
на индекс Bing). Запуск из Render Shell после деплоя, когда менялись тексты лендинга:

    python scripts/indexnow_ping.py

Нужна переменная INDEXNOW_KEY в Render (любая строка 8–128 символов: латиница, цифры, «-»).
Сайт отдаёт её по адресу https://voksyai.online/<INDEXNOW_KEY>.txt (app.py), так поисковик
проверяет, что сайт наш. Адреса берутся из backend/static/landing/sitemap.xml.
"""
import json
import os
import re
import sys
import urllib.request

SITE = "https://voksyai.online"
HOST = "voksyai.online"
ENDPOINTS = ["https://api.indexnow.org/indexnow", "https://yandex.com/indexnow"]


def main() -> int:
    key = os.getenv("INDEXNOW_KEY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key or ""):
        print("INDEXNOW_KEY не задан или неверный (8–128 символов: латиница, цифры, «-»).")
        return 1
    sitemap = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "backend", "static", "landing", "sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", open(sitemap, encoding="utf-8").read())
    body = json.dumps({"host": HOST, "key": key, "keyLocation": f"{SITE}/{key}.txt", "urlList": urls}).encode()
    ok = True
    for endpoint in ENDPOINTS:
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                print(f"{endpoint}: {resp.status} ({len(urls)} адресов)")
        except Exception as e:  # 202 = принято, 422/403 = проблема с ключом
            ok = False
            print(f"{endpoint}: ошибка {e}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
