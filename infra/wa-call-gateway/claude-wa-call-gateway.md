# Шлюз звонков WhatsApp (wa-call-1) — для агентов

ТЗ и этапы: `docs/tz_whatsapp_calls.md`. Сервер: `wa-call-1`, **2.31.204.51**, вход `ssh root@2.31.204.51` только по ключу с Mac владельца; номер +996 705 701 707 → агент «Анри» (подробно — таблица «Наш сервер» в `SERVER.md`). Памятка для человека (установка, проверки): `SERVER.md`.
Ветка: `2811-wa-calls` (от `2511-beauty`).

## Зачем и как

Агент принимает звонки WhatsApp и сам звонит клиентам в WhatsApp — на любой номер с WhatsApp,
не только O!; звук широкой полосой (16 кГц) вместо телефонных 8 кГц; оператору не платим.
Звонок WhatsApp — **третий вход в тех же голосовых агентов** (телефон, виджет, WhatsApp):
своей логики разговора нет, промпт голоса тот же, что у телефона.

```
Клиент (WhatsApp, MLow) ⇄ wa-call-1: voksy-wa-call (Go)
   ├─ /ws/wa-call-gateway/control   (api/wa_call_gateway.py, токен WA_CALL_GATEWAY_TOKEN)
   └─ /ws/sip/{call_id}             (api/sip_gateway.py — ОБЩИЙ медиа-сокет с SIP-мостом)
          → HandlerSocket(line_rate=16000, channel="whatsapp") → SIP_HANDLERS[...] → тот же хендлер
```

Отдельный VPS: падение шлюза звонков не задевает переписку (Evolution, `wa-gateway-1`) и
телефонию (`infra/sip-gateway/`). Входящих портов нет, кроме SSH.

## Шлюз (`gateway/`, Go)

Библиотеки: `github.com/purpshell/meowcaller` (MIT, звонки + кодек MLow, чистый Go) и
`github.com/polymorfa/hypermeow` (MPL 2.0) — **форк whatsmeow** с теми же именами пакетов;
meowcaller построен на нём, поэтому шлюз использует hypermeow, а не `go.mau.fi/whatsmeow`.
SQLite без CGO — `modernc.org/sqlite`. Лицензии — `THIRD_PARTY_LICENSES.md`, `licenses/`.
Версии зафиксированы в `go.mod`/`go.sum` (нужен Go 1.26 — `install.sh` скачивает сам, `GOTOOLCHAIN=auto`).

| Файл | Что делает |
|---|---|
| `main.go` | конфиг, хранилище сессий (`DATA_DIR/sessions.db`), запуск, остановка (звонки кладутся) |
| `config.go` | переменные `/etc/voksy-wa-call/gateway.env` (см. `install.sh`) |
| `control.go` | управляющий сокет: hello, переподключение 1→30 с, очередь сообщений (2000) |
| `account.go` | аккаунт = номер = связанное устройство: вход по QR, состояния, номер звонящего по LID |
| `gateway.go` | входящий (решение бэкенда), исходящий (originate), команды, `/health` на 127.0.0.1:9095, эхо-тест |
| `call.go` | один звонок ⇄ медиа-сокет: start, кадры 20 мс, clear / mark / hangup / ping, сторож длины |
| `audio.go` | float32 60 мс ⇄ PCM16 20 мс, очередь воспроизведения (кадр тишины, когда звука нет), ресемплер |
| `recorder.go` | запись звонка: обе стороны в WAV → MP3 (lame) → `POST /api/sip/recordings/{id}` с повтором |

Звук библиотеки — 16 кГц моно float32, кадры 60 мс (960 отсчётов), т. е. частота совпадает
с линией бэкенда — пересчёта нет. Цикл отправки библиотеки сам держит темп (тикер 60 мс) и
**никогда не ждёт**: `PlayoutQueue.ReadFrame` отдаёт кадр звука или тишины сразу; хвост реплики
короче кадра уходит через 120 мс, дополненный тишиной. `clear` сбрасывает очередь и метки
(как мост SIP), `mark` возвращается, когда проигрывание дошло до неё.

Обработчик событий WhatsApp регистрируется ДО библиотеки звонков: из `CallOffer` берётся
`CallCreatorAlt` (номер рядом с LID). Номер звонящего: alt → JID на `s.whatsapp.net` → таблица
LID→номер сессии; иначе бэкенд ищет номер по переписке Evolution с тем же JID. Имя — из контактов
сессии. `OnIncomingCall` вызывается внутри обработки событий — решение ждём в горутине.

Только звонки: `ManualHistorySyncDownload`, `DisableHistorySyncStorage`, без `MarkRead` и без
присутствия «в сети» (иначе на телефоне пропали бы уведомления). Если на пробе окажется, что WhatsApp
не доставляет звонки устройству не «в сети», — `SEND_PRESENCE=1` (`VOKSY_SEND_PRESENCE=1 bash install.sh`).
Устройство называется `DEVICE_NAME`.

## Протокол

Медиа-сокет `/ws/sip/{call_id}` — как у SIP-моста (`infra/sip-gateway/README.md`), отличия:
```json
{"type":"start","call_id":"…","channel":"whatsapp","direction":"inbound",
 "caller":"996555123456","caller_jid":"…@s.whatsapp.net","caller_name":"Айбек",
 "account_id":"…","did":"<номер агента>","assistant_id":"…","assistant_type":"eleven",
 "format":{"encoding":"pcm16","sample_rate":16000,"channels":1,"frame_ms":20}}
```
Кадры 640 байт (20 мс, 16 кГц). Токен — `WA_CALL_GATEWAY_TOKEN`; звонок WhatsApp по токену SIP не
открывается, и наоборот, звонок по токену WhatsApp должен быть строкой `channel=whatsapp`.

Управляющий сокет `/ws/wa-call-gateway/control?token=&gateway=wa-call-1`:

| Шлюз → бэкенд | Поля |
|---|---|
| `hello` | `gateway_id, version, kind:"whatsapp", max_calls, accounts[{account_id,state,phone}], active_calls` |
| `account.state` | `account_id, state (qr│qr_expired│connecting│connected│disconnected│logged_out│banned│error), phone, push_name, qr, expires_in, error` |
| `call.event` | `event (started│ringing│answered│ended│failed), call_id, wa_call_id, account_id, direction, caller, caller_jid, caller_name, to, reason, duration_sec, underruns, error` |
| `pong`, `status` | — |

| Бэкенд → шлюз | Поля |
|---|---|
| `accept` / `reject` | ответ на `started` входящего: `call_id, assistant_id, assistant_type, metadata` / `reason` (+ `ignore: true` — агент не отвечает, но звонок НЕ отклоняется в WhatsApp: звонит на телефоне владельца; старая версия шлюза поле не знает и отклоняет). Нет ответа за `ACCEPT_TIMEOUT_MS` (6 с) → шлюз отклоняет |
| `originate` | `call_id, account_id, to, caller_id, assistant_*, metadata` |
| `hangup` | `call_id, reason` |
| `login` / `logout` | `account_id` (`force:"true"` — новый QR даже при живой сессии) |
| `ping`, `status` | — |

Причины конца: `wa_hangup`, `caller_hangup`, `accepted_elsewhere`, `rejected`, `no_answer`, `busy`,
`not_on_whatsapp`, `account_unavailable`, `gateway_busy`, `backend_unavailable`, `decision_timeout`,
`max_duration`, `gateway_shutdown` + причины бэкенда (`assistant_hangup`, `idle_silence` …).

## Бэкенд

| Файл | Что |
|---|---|
| `backend/models/wa_call.py` | `agent_wa_call_accounts` (ТЗ 5.1 + `qr_code`, `login/logout_requested_at`, `last_state_at`); `app.ensure_wa_call_tables()` |
| `backend/services/wa_call_service.py` | выключатели, `decide_inbound`, `apply_account_state`, `apply_call_event`, лимиты и очередь исходящих, `choose_outbound_channel`, `WaLine` |
| `backend/api/wa_call_gateway.py` | управляющий сокет, цикл команд/очереди (1 с), `GET /api/wa-calls/gateways` (админ) |
| `backend/api/sip_gateway.py` | медиа-сокет: токен шлюза WhatsApp, `channel`, `sample_rate` 16000 из `start`, строка «Звонок WhatsApp» в журнале; приём записи по любому из двух токенов |
| `backend/websockets/sip_media_adapter.py` | `HandlerSocket(line_rate, channel)`: 16 кГц без `PhoneVoice`, фильтр по частоте линии (`antialias_cutoff`), класс по умолчанию — 8 кГц |
| `handler_eleven.py` / `handler_gemini_live.py` / `wallet_meter.py` | `assistant.line_wideband` → распознавание 16 кГц, без АРУ линии оператора; кошелёк `channel="whatsapp"`, без минуты оператора |
| `core/task_scheduler.py`, `services/agent_tools.py` | канал исходящего решает код: O! → SIP, иначе WhatsApp (`check_phone_operator`, `agent_can_call_contact`, проверка звонка) |
| `services/agent_call_finalizer.py` | PostCall: входящий — по агенту аккаунта (`WaLine.agent_config_id`), исходящий без задачи — свой `AgentCall` |
| `scripts/wa_call_account.py` | Render Shell: `create` (QR в терминале), `qr`, `set`, `call`, `calls`, `logout`, `delete` |

Звонок = строка `sip_calls` (`gateway_id=wa-call-1`, `did` = номер агента, `phone_number_id` NULL,
`call_metadata.channel="whatsapp"`, `wa_account_id`, `agent_config_id`, `caller_jid`, `caller_name`).
Входящую строку заводит `decide_inbound` (принят → `ringing`, отклонён → `failed` + причина, владельцу
«пропущенный звонок WhatsApp» в Telegram-бот агента), исходящую — очередь. `claim_queued_calls`
фильтрует по `gateway_id`, поэтому SIP-шлюз чужие звонки не берёт.

Правила:
- входящий: выключатель для владельца, привязка аккаунта, приём включён, агент включён,
  тариф с агентом (Старт и Profi), ассистент есть и включён, кошелёк (карточки Eleven);
  вне окна звонков входящие НЕ отклоняются (как линия SIP) — решение владельца при открытии;
- исходящий: Profi; аккаунт на связи с `outbound_enabled`; свой номер (SIP и WhatsApp) нельзя;
  анти-долбёж 30 мин; `WA_CALLS_HOURLY_OUTBOUND` (6) в час и `daily_outbound_limit` (20) в сутки
  на аккаунт; первым — только тем, кто сам писал в мессенджер или звонил; кошелёк; дальше — все
  обычные защиты звонков агента (окно, «один в день», недозвон, пауза, «не звонить»).

Переменные Render: `WA_CALL_GATEWAY_TOKEN`, `WA_CALL_GATEWAY_DEFAULT_ID` (`wa-call-1`),
`WA_CALLS_ENABLED` (`0`), `WA_CALLS_USERS` (пусто при включённом = всем), `WA_CALLS_DAILY_OUTBOUND` (20),
`WA_CALLS_HOURLY_OUTBOUND` (6).

## Проверки

- Шлюз: `cd infra/wa-call-gateway/gateway && go vet ./... && go test -race ./...` (очередь, темп, clear/mark,
  нарезка, запись, ресемплер, протокол с поддельным бэкендом, переподключение, «бэкенд недоступен»).
- Бэкенд: `python scripts/voice_tests/test_wa_calls.py` (часть 2 — PostgreSQL при `WA_CALLS_TEST_DATABASE_URL`,
  есть в `check_all.sh`), плюс `voice_regression.py` и `test_voice_standard.py`.

## Состояние (28.11.2026)

Сделано: шлюз (вход по QR, входящие, исходящие, звук в обе стороны, clear/mark/hangup, запись, сторож,
эхо-тест), `install.sh` + systemd, бэкенд этапа 1 и основа этапа 2 (таблица аккаунтов, решения, лимиты,
выбор канала в планировщике и инструментах агента, журнал, кошелёк, PostCall, сообщения владельцу).
**Эхо-тест на живом WhatsApp (28.11.2026) — пройден.** Сервер `wa-call-1` (Hetzner CX23, Falkenstein,
`2.31.204.51`, вход только по SSH-ключу владельца с Mac; `ва-шлюз-1` для пробы не подошёл — сброс пароля root
там не сработал). Вход по QR — сразу (`paired` → `connected`, номер на WhatsApp Business, `platform=smba`);
случайное удаление устройства на телефоне шлюз увидел как `logged_out (401)`, новый QR — `systemctl restart
voksy-wa-call`. Входящий звонок шлюз взял сам, голос вернулся с задержкой меньше секунды, чисто, без щелчков,
звонок не рвался (положил владелец). Без `SEND_PRESENCE` звонки доходят. Дальше — звонок с агентом (шаг Б SERVER.md).
Не сделано: кабинет (строка «Звонки WhatsApp» в каналах Старта и Profi, окно QR, предупреждение о риске
блокировки — ТЗ 5.2), отдельный цвет канала в аналитике/«Диалогах» (5.5), открытие клиентам (этап 3).

## Звук к бэкенду: голос сразу, тишина только в паузах (28.11.2026)

`pumpToBackend` (`gateway/call.go`): кадр голоса от WhatsApp уходит бэкенду СРАЗУ, без выравнивания по таймеру и
без выбрасывания (очередь режется только при 3 с затора); кадр тишины 20 мс — только если голоса нет дольше
120 мс (WhatsApp в паузах звук не передаёт). Прежняя схема 0aeb9e9 («ровно кадр на тик 20 мс, сверх 200 мс —
выбросить») рвала речь, которую WhatsApp отдаёт пачками: звонок c4b0c0fd — агент поздоровался, а потом не
услышал ни слова (реплик клиента 0). Тест — пачка 6×60 мс доходит целиком (`TestMediaSocketProtocol`).
Сообщение `ended` несёт `frames_in` (голос от WhatsApp), `silence_frames`, `dropped_in` — бэкенд пишет их в строку
журнала «Голос клиента во входящем звуке». `MEDIA_READY_WAIT_MS` по умолчанию 1500 (было 4000): столько шлюз
ждёт живого звука после «взял трубку», прежде чем подключить агента. Журнал звонка пишет «от начала звонка до
соединения / от соединения до сервера / подготовка звонка» — где теряется время до приветствия.
