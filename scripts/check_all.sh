#!/usr/bin/env bash
# Полная проверка перед коммитом/деплоем:  bash scripts/check_all.sh
# Хук перед коммитом (.githooks/pre-commit) включается один раз в клоне: git config core.hooksPath .githooks
#   1) весь Python компилируется;  2) нет неопределённых имён (pyflakes);
#   3) регрессия без сети (scripts/voice_regression.py, в т.ч. запуск всех фоновых циклов сервера);
#   4) с CHECK_DATABASE_URL (тестовая PostgreSQL, БУДЕТ ИЗМЕНЕНА) — механика, инбокс, сводка, фоновые циклы
#      на настоящей схеме и обход всех маршрутов кабинета (scripts/smoke_cabinet.py).
set -u
cd "$(dirname "$0")/.."
fail=0
echo "== компиляция"
python -m compileall -q backend app.py main.py alembic scripts >/dev/null || { echo "FAIL компиляция"; fail=1; }
echo "== неопределённые имена"
if python -c "import pyflakes" 2>/dev/null; then
  # telephony.py — мёртвый Voximplant (ждёт удаления); yandex_stt_client — ложное срабатывание (lambda в except)
  bad=$(python -m pyflakes backend app.py main.py 2>&1 | grep -E "undefined name" \
        | grep -vE "backend/api/telephony.py|yandex_stt_client.py:.*'exc'")
  if [ -n "$bad" ]; then echo "$bad"; echo "FAIL неопределённые имена"; fail=1; fi
else
  echo "(pyflakes не установлен: pip install pyflakes)"
fi
echo "== регрессия"
out=$(python scripts/voice_regression.py 2>&1)
echo "$out" | grep -E "^\[FAIL\]|^       FAIL|ИТОГО"
echo "$out" | grep -q "ИТОГО: прошло [0-9]*, провалов 0" || fail=1
if [ -n "${CHECK_DATABASE_URL:-}" ]; then
  echo "== тесты на PostgreSQL"
  for t in mechanics inbox owner_voice boot_jobs e2e_call_chain e2e_chat_chain; do
    if ! MECH_TEST_DATABASE_URL=$CHECK_DATABASE_URL INBOX_TEST_DATABASE_URL=$CHECK_DATABASE_URL \
         OWNER_VOICE_TEST_DATABASE_URL=$CHECK_DATABASE_URL BOOT_TEST_DATABASE_URL=$CHECK_DATABASE_URL E2E_TEST_DATABASE_URL=$CHECK_DATABASE_URL \
         python scripts/voice_tests/test_$t.py > "/tmp/check_$t.log" 2>&1; then
      grep -E "^FAIL|Error" "/tmp/check_$t.log" | head; echo "FAIL test_$t"; fail=1
    fi
  done
  echo "== кабинет: все маршруты"
  if ! SMOKE_DATABASE_URL=$CHECK_DATABASE_URL python scripts/smoke_cabinet.py > /tmp/check_smoke.log 2>&1; then
    grep -E "^FAIL" /tmp/check_smoke.log | head -20; fail=1
  fi
  grep -E "ПРОВАЛОВ" /tmp/check_smoke.log
fi
[ $fail = 0 ] && echo "ВСЁ ПРОШЛО" || echo "ЕСТЬ ПРОВАЛЫ"
exit $fail
