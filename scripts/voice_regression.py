#!/usr/bin/env python3
"""
Регрессионные проверки голоса — ОДНА команда перед деплоем (без сети, ключей и базы данных):

    python scripts/voice_regression.py

Каждый файл из scripts/voice_tests/ запускается отдельным процессом (они подменяют внутренности
модулей и не должны мешать друг другу). Что покрыто:
  - test_hybrid_search.py     — поиск по базе: слова + смысл, откаты при таймауте/ошибке, индексация;
  - test_call_logic.py        — защита hangup_call, диагностика звонка, повторы, бюджет промпта, конфиг сессии;
  - test_selfcheck_script.py  — сам скрипт самопроверки voice_selfcheck.py.
  - test_start_tier.py        — тариф Старт: агент «только отвечает» (agent_mode).
  - test_owner_notify.py      — оповещения владельцу в Telegram.
  - test_sheets.py            — Google Таблица агента: ссылка, разбор прайса.

Правило ветки: любая правка голоса (подсказки, бюджет промпта, поиск, хендлеры) — сначала этот прогон.
Нашли новую поломку на живом звонке — допишите проверку в test_call_logic.py, чтобы она больше не вернулась.

Код возврата 0 — всё прошло, 1 — есть провалы.
"""

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    files = sorted(glob.glob(os.path.join(HERE, "voice_tests", "test_*.py")))
    if not files:
        print("Нет файлов проверок в scripts/voice_tests/")
        return 1
    total_pass = total_fail = 0
    bad_files = []
    for path in files:
        name = os.path.basename(path)
        proc = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True, timeout=300)
        lines = proc.stdout.splitlines()
        passed = sum(1 for ln in lines if ln.startswith("PASS "))
        failed = [ln for ln in lines if ln.startswith("FAIL ")]
        crashed = proc.returncode != 0 and not failed and "ПРОВАЛОВ" not in proc.stdout
        total_pass += passed
        total_fail += len(failed) + (1 if crashed else 0)
        status = "OK  " if not failed and not crashed else "FAIL"
        print(f"[{status}] {name}: прошло {passed}, провалов {len(failed)}" + ("  (ТЕСТ УПАЛ)" if crashed else ""))
        for ln in failed:
            print("       " + ln)
        if crashed:
            bad_files.append(name)
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-8:]
            print("       " + "\n       ".join(tail))
    print("-" * 70)
    print(f"ИТОГО: прошло {total_pass}, провалов {total_fail}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
