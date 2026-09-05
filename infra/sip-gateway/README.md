# SIP-шлюз Voksy AI (собственная телефония, без Voximplant)

Отдельный VPS с постоянным IP, на котором стоит Asterisk и «мост». Оператор
(O! / НУР Телеком) отдаёт и принимает звонки по SIP-транку с IP-авторизацией;
звук звонка через мост попадает в бэкенд по WebSocket точно так же, как звук
из браузерного виджета.

```
Оператор O!                      VPS (Hetzner, 178.105.79.237)                    Render
195.216.237.6/.7:5070  ─SIP──►  Asterisk 20 (PJSIP)                               бэкенд Voksy
195.216.237.6-.9       ─RTP──►    │ AudioSocket, PCM16 8 кГц, loopback :9092
                                  ▼
                                bridge.py  ──WSS /ws/sip/{call_id}──────────────►  медиа звонка
                                           ──WSS /ws/sip-gateway/control────────►  команды/события
                                           ◄─AMI :5038 (исходящие)  Asterisk
```

Все соединения с бэкендом исходящие с VPS (TLS). Снаружи на VPS открыты только
SSH, SIP 5060/UDP и RTP 10000-20000/UDP с адресов оператора, плюс временный
5080/UDP для тестового софтфона.

## Файлы

| Файл | Назначение |
|---|---|
| `install.sh` | Установка и обновление одной командой (идемпотентно) |
| `asterisk/pjsip.conf` | Транк оператора (два сервера, IP-auth, G.711 A-law) и тестовый софтфон-аккаунт |
| `asterisk/extensions.conf` | Маршрутизация: входящие → мост, исходящие после ответа → мост |
| `asterisk/rtp.conf` | Порты RTP 10000-20000 |
| `asterisk/manager.conf` | AMI на loopback для моста |
| `asterisk/modules.conf` | Отключение устаревших драйверов (chan_sip и др.) |
| `bridge/bridge.py` | Мост: AudioSocket ⇄ WebSocket бэкенда, AMI-originate, лимит каналов |
| `bridge/voksy-bridge.service` | systemd-юнит моста |

## Установка / обновление (на VPS, под root)

```bash
curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/2308-agent-v2/infra/sip-gateway/install.sh | bash
```

Скрипт печатает в конце: публичный IP для анкеты оператора, логин/пароль
тестового софтфона и `GATEWAY_TOKEN`. Токен нужно прописать на Render как
переменную `SIP_GATEWAY_TOKEN` — по нему бэкенд узнаёт наш шлюз.

Секреты хранятся в `/etc/voksy-bridge/bridge.env` и при повторном запуске не
меняются. Переменные `VOKSY_BRANCH`, `VOKSY_BACKEND_WS_URL`, `VOKSY_GATEWAY_ID`
переопределяют ветку, адрес бэкенда и имя шлюза.

## Проверка

```bash
systemctl status asterisk voksy-bridge
journalctl -u voksy-bridge -f                 # лог моста
asterisk -rvvv                                # консоль Asterisk
  pjsip show endpoints                        # o-trunk и test
  pjsip show identifies                       # IP оператора → o-trunk
  core show channels                          # активные звонки
curl -s http://127.0.0.1:9091/health          # состояние моста
```

Тест без оператора: в софтфоне (Zoiper, MicroSIP, Linphone) сервер
`178.105.79.237:5080`, пользователь `test`, пароль из вывода `install.sh`.
Набрать `100` — эхо-тест Asterisk (проверяет SIP и звук). Набрать любой
номер, например `996705579977`, — звонок уходит в мост и дальше в бэкенд как
входящий на этот номер.

## Протокол мост ⇄ бэкенд

### Медиа-сокет `wss://<backend>/ws/sip/{call_id}?token=<GATEWAY_TOKEN>&gateway=<id>`

Мост → бэкенд, первое сообщение (текст, JSON):

```json
{"type":"start","call_id":"…","direction":"inbound|outbound",
 "did":"996705579977","caller":"996555…","to":"","assistant_id":"…","assistant_type":"openai|gemini",
 "metadata":{},"format":{"encoding":"pcm16","sample_rate":8000,"channels":1,"frame_ms":20}}
```

- `did` — набранный номер (входящие) или наш caller ID (исходящие);
  формат такой, как прислал оператор, бэкенд нормализует.
- Дальше мост шлёт **бинарные** кадры: PCM16 LE, 8 кГц, моно, 320 байт = 20 мс.
- Текстовые события: `{"type":"dtmf","digit":"5"}`, `{"type":"mark","name":"…"}`
  (достигнута метка в воспроизведении), `{"type":"ended","reason":"…"}`.

Бэкенд → мост:

- **бинарные** кадры PCM16 LE 8 кГц моно любого размера — мост режет по 20 мс
  и отдаёт в темпе реального времени;
- `{"type":"clear"}` — сбросить очередь воспроизведения (перебивание);
- `{"type":"mark","name":"…"}` — вернуть событие, когда воспроизведение дойдёт до этой точки;
- `{"type":"hangup","reason":"…"}` — положить трубку.

Закрытие медиа-сокета бэкендом = положить трубку после доигрывания очереди (до 2 с).

### Управляющий сокет `wss://<backend>/ws/sip-gateway/control?token=…&gateway=…`

Одно постоянное соединение на шлюз, переподключается само.

Мост → бэкенд:

```json
{"type":"hello","gateway_id":"sip-gw-1","version":"1.0.0","max_outbound":4,"public_ip":"…","active_calls":[]}
{"type":"call.event","event":"started|answered|ended|failed","call_id":"…","direction":"…",
 "did":"…","caller":"…","to":"…","assistant_id":"…","assistant_type":"…","metadata":{},
 "trunk_host":"195.216.237.6:5070","answered_at":…,"ended_at":…,"duration_sec":12.3,"reason":"…"}
```

Причины `failed`: `channel_limit` (заняты все исходящие каналы), `busy`,
`no_answer`, `trunk_unavailable`, `congestion`, `bad_number`, `ami_unavailable`.
Причины `ended`: `asterisk_hangup` (абонент положил трубку), `backend_hangup`,
`backend_closed`, `backend_unavailable`, `max_duration`, `gateway_shutdown`.

Бэкенд → мост:

```json
{"type":"originate","call_id":"<uuid, опционально>","to":"996555123456","caller_id":"996705579977",
 "assistant_id":"…","assistant_type":"openai","metadata":{"task_id":"…"}}
{"type":"hangup","call_id":"…"}
{"type":"ping"}   → {"type":"pong"}
{"type":"status"} → {"type":"status","active_calls":[…]}
```

Исходящий: мост пробует первый сервер оператора, при `trunk_unavailable` /
`congestion` — второй. `busy` / `no_answer` второй раз не набирает.
Одновременных исходящих не больше `MAX_OUTBOUND` (лимит транка, сейчас 4).

## Аудио и провайдеры

Телефонный звук 8 кГц. На стороне бэкенда:
- OpenAI Realtime принимает `g711_alaw` напрямую (audioop `lin2alaw`) либо PCM16 24 кГц после ресемплинга;
- Gemini Live: вход PCM16 16 кГц (ресемплинг 8→16), выход 24 кГц (ресемплинг 24→8).

## Бэкенд

Реализовано в репозитории:

| Файл | Что |
|---|---|
| `backend/api/sip_gateway.py` | `/ws/sip-gateway/control`, `/ws/sip/{call_id}`, HTTP `/api/sip/numbers`, `/api/sip/calls`, `/api/sip/gateways` |
| `backend/websockets/sip_media_adapter.py` | Адаптер: бинарный PCM16 8 кГц моста ⇄ JSON-протокол браузерных хендлеров OpenAI/Gemini, ресемплинг, перебивание, hangup через функцию `hangup_call` |
| `backend/services/sip_gateway_service.py` | Очередь исходящих (FOR UPDATE SKIP LOCKED), применение событий моста к `sip_calls`/`tasks`/`agent_calls`, простановка номера в `conversations` |
| `backend/models/sip_gateway.py` | Таблицы `sip_phone_numbers`, `sip_calls` |
| `backend/core/task_scheduler.py` | Если у пользователя есть номер оператора и ассистент OpenAI/Gemini — исходящий идёт через шлюз, иначе Voximplant как раньше |

Переменные окружения бэкенда (Render): `SIP_GATEWAY_TOKEN` (= `GATEWAY_TOKEN` шлюза),
опционально `SIP_GATEWAY_DEFAULT_ID` (по умолчанию `sip-gw-1`).

Добавить номер оператора (админ):

```bash
curl -X POST https://voksyai.online/api/sip/numbers -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{"phone_number":"996705579977","label":"O! основной","assistant_type":"openai","assistant_id":"<uuid ассистента>"}'
```

Тестовый исходящий: `POST /api/sip/calls` с `{"to":"996555123456"}`.

## Что ещё не сделано

- Страница в интерфейсе для номеров и журнала звонков (сейчас только API).
- Запись разговоров (MixMonitor в dialplan + загрузка в R2).
- После запуска с оператором: удалить правило 5080 в файрволе и секцию `test` в `pjsip.conf`.
- Второй шлюз для резервирования.
