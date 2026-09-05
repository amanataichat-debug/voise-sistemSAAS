# infra/sip-gateway — собственная SIP-телефония (VPS + Asterisk + мост к бэкенду)

## Назначение
Телефония без Voximplant. Оператор O! (ООО «НУР Телеком») отдаёт SIP-транк по
IP-авторизации на наш VPS (Hetzner, `178.105.79.237`, Ubuntu 24.04). На VPS
стоит Asterisk 20 и мост `bridge.py`, который гонит звук звонка по WebSocket в
бэкенд на Render. На бэкенде звонок проходит через **те же** голосовые
хендлеры, что и браузерный виджет (`handler_realtime_new` для OpenAI,
`handler_gemini` для Gemini, `handler_fish` для Fish), через адаптер `backend/websockets/sip_media_adapter.py`.
Функции, транскрипты, запись диалогов и CRM работают для телефона так же, как
для виджета. Правило проекта: **для телефонии и виджета поведение одинаковое**.

Поддерживаются ассистенты OpenAI, Gemini и Fish (`SIP_HANDLERS` в `api/sip_gateway.py`). Входящие и исходящие звонки.
Человеческая памятка по серверу: `SERVER.md`; спецификация протокола и
установка: `README.md`.

## Состав
| Файл | Что |
|---|---|
| `install.sh` | Идемпотентный установщик/обновлятор для VPS. Тянет файлы этой папки с raw GitHub по ветке (`VOKSY_BRANCH`, по умолчанию `2308-agent-v2`), пишет `/etc/asterisk/*.conf`, ставит мост в `/opt/voksy-bridge`, генерирует секреты в `/etc/voksy-bridge/bridge.env` (не перезаписывает при повторе), ждёт `core waitfullybooted`, печатает сводку |
| `asterisk/pjsip.conf` | Транспорты UDP 5060 (транк) и 5080 (временный софтфон). Endpoint `o-trunk`: identify по 195.216.237.6/.7, два контакта `:5070`, `alaw`/`ulaw`, `rfc4733`, `send_pai`, `from_domain=__PUBLIC_IP__`. Endpoint `test` с паролем `__TEST_PASSWORD__` |
| `asterisk/extensions.conf` | `[from-operator]`/`[from-test]` → `Gosub(voksy-inbound)`: `CURL` в мост `/asterisk/inbound?did&cid&channel` → UUID → `Answer()` → `AudioSocket(uuid,127.0.0.1:9092)`. `[outbound-answered]` для исходящих после ответа. `100` в `[from-test]` — эхо-тест |
| `asterisk/rtp.conf` | RTP 10000–20000 |
| `asterisk/manager.conf` | AMI пользователь `bridge` на loopback, `__AMI_SECRET__` |
| `asterisk/modules.conf` | noload `chan_sip` и прочих устаревших |
| `bridge/bridge.py` | Мост (VERSION в шапке). Классы `Call`, `Bridge`, `AmiClient`. AudioSocket-сервер (TCP 9092), HTTP (9091: `/asterisk/inbound`, `/health`), AMI Originate с `Async: true`, failover по `TRUNK_HOSTS`, лимит `MAX_OUTBOUND`, 20 мс pacer с `mark`/`clear`/`hangup`, silence keepalive |
| `bridge/voksy-bridge.service` | systemd, пользователь `voksy`, `EnvironmentFile=/etc/voksy-bridge/bridge.env`, `Restart=always` |
| `bridge/requirements.txt` | `websockets`, `aiohttp` |

Плейсхолдеры `__PUBLIC_IP__`, `__TEST_PASSWORD__`, `__AMI_SECRET__` подставляет `install.sh`.

## Протокол мост ⇄ бэкенд (кратко, полностью в README.md)
- Мост инициирует **оба** соединения к бэкенду, входящих на VPS с Render нет.
- Control: `wss://voksyai.online/ws/sip-gateway/control?token=&gateway=`. Мост шлёт `hello` и `call.event` (started/answered/ended/failed), бэкенд шлёт `originate` (из очереди `sip_calls`) и `hangup`.
- Media: `wss://voksyai.online/ws/sip/{call_id}?token=&gateway=`. Первое сообщение JSON `start` (did, caller, direction…), далее бинарные кадры PCM16 8 кГц 320 байт = 20 мс в обе стороны. Текстовые команды бэкенд → мост: `clear` (сбросить очередь воспроизведения), `mark` (эхо, когда очередь проиграна), `hangup`. Мост → бэкенд: `mark`, `dtmf`, `ended`, `ping`.
- AudioSocket: 3-байтный заголовок (тип, длина BE16); типы `0x00` terminate, `0x01` uuid, `0x03` dtmf, `0x10` audio slin 8 кГц, `0xff` error.

## Бэкенд (файлы в основном репозитории)
| Файл | Что |
|---|---|
| `backend/api/sip_gateway.py` | Роутер. WS `/ws/sip-gateway/control` (реестр `GATEWAYS`, `_originate_loop` раз в 1 с, `_sweep_stale_calls`), WS `/ws/sip/{call_id}` (поиск номера → загрузка ассистента → override приветствия/промпта → запуск хендлера через `HandlerSocket` → лог `finished frames_in=… deltas_out=… audio_out=… barge_ins=… reply_latencies=…`). HTTP `/api/sip/numbers` (GET, POST admin, PATCH, DELETE), `/api/sip/calls` (GET, POST ручной исходящий, `/{id}/hangup`), `/api/sip/gateways` (admin). `_ensure_tables()` создаёт таблицы лениво при первом обращении, потому что startup-воркер Gunicorn может быть убит по таймауту 120 с |
| `backend/websockets/sip_media_adapter.py` | `HandlerSocket` — псевдо-WebSocket для хендлера: ресемплинг 8↔24 кГц (OpenAI, Fish) и 8→16 / 24→8 (Gemini) через `audioop.ratecv`; батчинг входа `INBOUND_BATCH_MS` (OpenAI 20 мс, Gemini 100 мс); barge-in на `speech.started`/`conversation.interrupted`/`response.cancelled` → `clear` + подкладывает хендлеру `audio_playback.stopped`; функция `hangup_call` → дождаться прощания → `mark` → `hangup`; замер `reply latency` (событие «пользователь договорил» → первый аудио-дельта) |
| `backend/services/sip_gateway_service.py` | `find_number` (точное, затем по последним 9 цифрам), `queue_outbound_call`, `claim_queued_calls` (`FOR UPDATE SKIP LOCKED`), `apply_bridge_event` (статусы, requeue до 6 попыток с паузой 30 с при `RETRYABLE_FAIL_REASONS`, `_finish_task` обновляет `Task`/`AgentCall`), `tag_conversations` (проставляет `caller_number`/`direction` в `conversations`, `gemini_conversations` или `fish_conversations` по `assistant_type`), `resolve_greeting`, `call_context_text` |
| `backend/models/sip_gateway.py` | `SipPhoneNumber` (user_id, phone_number — только цифры, unique; gateway_id; assistant_type/assistant_id; first_phrase; allow_outbound; is_active), `SipCall` (direction, `SipCallStatus` queued/dialing/ringing/answered/completed/failed, did/caller/to_number, task_id, call_metadata JSON, trunk_host, timestamps, duration_sec, end_reason, attempts), `normalize_sip_number()` |
| `backend/core/task_scheduler.py` | `_sip_number_for()`, `_execute_via_sip_gateway()`, `_agent_call_via_sip_gateway()`: если у пользователя есть активный номер шлюза с `allow_outbound` и ассистент OpenAI/Gemini/Fish, запланированный звонок ставится в очередь `sip_calls`; без номера задача падает в мёртвые ветки Voximplant и завершится ошибкой |
| `backend/core/config.py` | `SIP_GATEWAY_TOKEN` (равен `GATEWAY_TOKEN` на VPS), `SIP_GATEWAY_DEFAULT_ID` (`sip-gw-1`) |
| `backend/websockets/gemini_client.py` | VAD-профиль Gemini для всех сессий: `GEMINI_VAD_PROFILE` (`fast`/`default`), `GEMINI_VAD_START_SENSITIVITY` (`low` по умолчанию: high на телефонной линии принимает шум за речь и обрывает приветствие), `GEMINI_VAD_END_SENSITIVITY` (`high`), `GEMINI_VAD_SILENCE_MS` (500) |
| `backend/websockets/handler_gemini.py` | Если Gemini прервал приветствие до первого аудио-чанка, приветствие отправляется повторно один раз (`interruption_state["greeting_retries"]`) |
| `backend/services/conversation_service.py` | `save_conversation(assistant_type="gemini")` пишет в `gemini_conversations` (FK `conversations.assistant_id` → `assistant_configs`, поэтому Gemini-диалоги туда писать нельзя) |
| `backend/api/conversations.py` | Страница «Диалоги» объединяет `conversations` и `gemini_conversations` через `union_all` |

Роутер `sip_gateway.router` подключён в `app.py` **до** `websocket.router`, чтобы `/ws/sip/...` не перехватывался общим `/ws/{assistant_id}`.

## Текущее состояние (сентябрь 2026)
- Полный путь доказан через софтфон: Zoiper → Asterisk → мост → Render → OpenAI/Gemini → обратно. OpenAI: задержка ответа ~0.5 с. Gemini медленнее (модельная задержка 3–5 с) и чувствительнее к шуму линии.
- Тестовые номера в БД: `996705579977` → Gemini-ассистент, `996706579977` → OpenAI-ассистент (номера выделены оператором, транк у оператора ещё не включён).
- Анкета оператора заполнена: наш IP `178.105.79.237`, SIP 5060/UDP, RTP 10000–20000/UDP, G.711 A-law, DTMF RFC 2833, оба SIP-сервера оператора. Открытый вопрос к оператору: «исходящие только на O!».
- Мост запускается одним воркером бэкенда: control-сокет живёт на том воркере Gunicorn, который его принял; очередь исходящих в БД, поэтому остальные воркеры могут ставить звонки.

## Как проверять
- На VPS: `journalctl -u voksy-bridge -f`, `curl -s 127.0.0.1:9091/health`, `asterisk -rvvv` → `pjsip show endpoints`.
- В логах Render искать `[SIP-GW]`, `[SIP-MEDIA]`, `call <id>`: `reply latency`, `barge-in`, `finished … deltas_out= audio_out=`. `deltas_out=0` означает, что провайдер не дал ни одного аудио-чанка.
- Локальные тесты писались в scratchpad сессии (не в репозитории): фейковый AMI/AudioSocket/бэкенд для `bridge.py` и фейковый хендлер для `HandlerSocket`. При правках адаптера или моста стоит воспроизвести такой тест заново.

## На что обратить внимание
- Конфиги на VPS не редактируются руками: правка в репозитории → коммит в ветку → `install.sh` на сервере.
- `GATEWAY_TOKEN` на VPS и `SIP_GATEWAY_TOKEN` на Render должны совпадать, иначе мост получает 403.
- Render во время деплоя недоступен 1–2 минуты: в логах моста `backend_unavailable`, в логах Render `bad_start`. Не баг.
- Render фактически запускает Python 3.14 (несмотря на runtime.txt 3.10); `audioop` даёт пакет `audioop-lts`.
- `assistant.telephony_mode = True` ставится как обычный атрибут объекта ассистента, не колонка. Его читает Fish-хендлер (телефонный профиль VAD в `fish_llm_client.TELEPHONY_VAD`). Overrides приветствия/промпта делаются через `set_committed_value` при `expire_on_commit=False`, чтобы не попасть в БД.
- Хендлеры считают «100 audio chunks» по сообщениям, для Gemini это 100 мс батчи, то есть 10 в секунду.
- После запуска с оператором удалить транспорт 5080 и endpoint `test` из `pjsip.conf` и правило 5080/UDP в файрволе Hetzner.

## Fish по телефону
Привязка номера: `PATCH /api/sip/numbers/{id}` с `{"assistant_type":"fish","assistant_id":"<uuid>"}`. Звонок идёт через `handler_fish` на серверных ключах `OPENAI_API_KEY`/`FISH_API_KEY`; в логах Render искать `[FISH]` и `[FISH-TTS]` рядом с `[SIP-MEDIA]`. Задержка ответа выше OpenAI (текст модели → синтез Fish), после перебивания Fish переподключается.

## Что не сделано
- UI-страница для номеров и журнала звонков (сейчас только API `/api/sip/*`).
- Запись разговоров (MixMonitor + R2).
- Передача номера звонящего в хендлер во время звонка (сейчас диалог помечается номером после звонка через `tag_conversations`).
- Gemini-диалоги без длительности/стоимости на странице «Диалоги».
- Тест исходящего звонка на реальный номер после включения транка.
- Второй шлюз для резервирования.

## Связанные файлы документации
- `README.md` — протокол и установка
- `SERVER.md` — памятка по серверу для человека
- `../../backend/api/claude-api.md`, `../../backend/websockets/claude-websockets.md`, `../../backend/services/claude-services.md`, `../../backend/models/claude-models.md`, `../../backend/core/claude-core.md`
- `../../claude-index.md` — корневой индекс
