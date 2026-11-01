# Справочник: Gemini Live API и модель `gemini-3.8-live`
## Как сделать голосовой разговор максимально человечным — по официальной документации Google

Собрано: 1 ноября 2026. Источники правды: `ai.google.dev/gemini-api/docs` (основной), `ai.google.dev/api/live` (WebSocket-референс), `cloud.google.com` / `docs.cloud.google.com` (Vertex AI / Gemini Enterprise Agent Platform — только где отличается), `deepmind.google` (model card). Блоги и форумы НЕ использовались как источник фактов — только как сигнал для дальнейшей проверки, и везде помечены «неофициальный источник».

Модель `gemini-3.8-live` **существует и официально подтверждена** (GA / «Stable», релиз 15 сентября 2026) — это не вымышленное имя.

---

## 1. Таблица «Возможность → поддерживается в 3.8? → поле/значения → ссылка»

| Возможность | В 3.8? | Поле (JSON) | Значения / default | Источник |
|---|---|---|---|---|
| **Affective dialogue** (эмоциональная подача) | **НЕТ — удалено** | `enable_affective_dialog` / `enableAffectiveDialog` (поле больше не действует) | — | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Affective dialogue is removed from the API. Remove any `enable_affective_dialog` configurations from your code.» |
| **Proactive audio** (проактивность) | **ДА, но принудительно включено, нельзя отключить** | `proactivity.proactiveAudio` (camelCase WS) / `proactivity={'proactive_audio': True}` (Python) | `true` всегда; `false` → ошибка | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Proactive audio is now permanently enabled. Setting `proactive_audio: false` returns an error.» Также [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices): «Proactive audio is permanently enabled in `gemini-3.8-live`» |
| **Barge-in / перебивание** | ДА (общий механизм Live API) | `realtimeInputConfig.activityHandling` (enum) | `ACTIVITY_HANDLING_UNSPECIFIED` → `START_OF_ACTIVITY_INTERRUPTS` (**default**); `NO_INTERRUPTION` | [WebSocket reference](https://ai.google.dev/api/live) |
| Сигнал клиенту о перебивании | ДА | `serverContent.interrupted` (bool) | при `true` — клиент должен остановить и очистить аудио-буфер | [WebSocket reference](https://ai.google.dev/api/live); формулировка клиентского поведения — [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices): «Stop client playback immediately when the server signals an interruption» |
| Отмена вызова функции при перебивании | ДА | `toolCallCancellation.ids[]` (server→client) | «Sent only when the client interrupts server turns» | [WebSocket reference](https://ai.google.dev/api/live) |
| **VAD / конец реплики** — автоматический режим | ДА (общий Live API) | `realtimeInputConfig.automaticActivityDetection` | `disabled` (bool, default `false`) | [WebSocket reference](https://ai.google.dev/api/live) |
| — чувствительность начала речи | ДА | `startOfSpeechSensitivity` | `START_SENSITIVITY_UNSPECIFIED` (→HIGH), `START_SENSITIVITY_HIGH`, `START_SENSITIVITY_LOW` | [WebSocket reference](https://ai.google.dev/api/live) |
| — чувствительность конца речи | ДА | `endOfSpeechSensitivity` | `END_SENSITIVITY_UNSPECIFIED` (→HIGH), `END_SENSITIVITY_HIGH`, `END_SENSITIVITY_LOW` | [WebSocket reference](https://ai.google.dev/api/live) |
| — пред-буфер | ДА | `prefixPaddingMs` (int32) | числового default не указано; пример `20` | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| — длительность тишины до конца реплики | ДА | `silenceDurationMs` (int32) | числового default на референс-странице не указано; на странице Capabilities упоминается ориентир ≈800 мс, рекомендация 500–800 мс для обычного режима, предупреждение что 100–200 мс «can split utterances» | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| — ручной режим VAD | ДА | `activityStart` / `activityEnd` (пустые сообщения в `realtimeInput`) | доступны только при `automaticActivityDetection.disabled=true` | [WebSocket reference](https://ai.google.dev/api/live) |
| — гибридный режим | ДА | `audioStreamEnd` (bool) в `realtimeInput` | клиент может форсировать завершение хода при паузе >1с, не отключая серверный VAD | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [Live transcribe](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe) |
| Рекомендации VAD для телефонии/шума | **В документации не найдено** | — | — | — |
| Темп речи, паузы, смех, вдохи, «ээ/ммм» как параметр | **В документации не найдено** (только промпт-инжиниринг) | — | — | [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices) даёт только текстовые рекомендации по тону/длине ответов |
| Аудио-теги / стилевые инструкции («говори тише/с улыбкой») | Не подтверждено для Live; есть для отдельного продукта Gemini TTS | — | — | [Gemini TTS prompting guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/text-to-speech/prompting-guide) — но это **другая модель** (TTS, не Live), перенос на `gemini-3.8-live` не подтверждён |
| **Голос** (`voiceName`) | ДА, через общий каталог TTS-голосов | `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName` | 30 «curated» голосов (список ниже) + «Extended Voice Library» (сотни голосов через `ListVoices`) | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation), [ListVoices](https://ai.google.dev/api/voices) |
| Пол/тембр голосов | Частично | — | Только короткий дескриптор на голос (напр. «Firm», «Breathy»), пол явно не указан | [Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation) |
| Несколько языков в одной сессии | ДА | — (не конфигурируется явным полем) | «native audio models can switch languages during a conversation» | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| **`languageCode`** как настраиваемое поле | **НЕТ для native-audio моделей (включая 3.8)** | — | «native audio models automatically choose the appropriate language and don't support explicitly setting the language code» — ограничение языка делается через `systemInstruction` | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| Русский язык | ДА | BCP-47 код `ru` | в списке поддерживаемых (99 языков по Capabilities) | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| Кыргызский язык | ДА | BCP-47 код `ky` | в списке поддерживаемых | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| **Thinking** (`thinking_level`/`thinking_config`) | **НЕТ — не поддерживается как настраиваемый параметр**, но сама «interleaved reasoning» встроена и работает всегда | — (поле нужно убрать из setup) | — | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «`thinking_level` is not supported for `gemini-3.8-live`»; [Thinking page](https://ai.google.dev/gemini-api/docs/live-api/thinking): «Interleaved reasoning with fixed latency profile» |
| Thinking с настройкой уровня | Доступно только в отдельной модели `gemini-3.8-live-extended-thinking` | `thinkingConfig.thinkingLevel` | `low` / `medium` / `high` (без `minimal`) | [Thinking page](https://ai.google.dev/gemini-api/docs/live-api/thinking) |
| **Function calling — behavior** | ДА, async по умолчанию для 3.8 | `behavior` (в декларации функции) | `NON_BLOCKING` (**default для 3.8**), `BLOCKING` | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Async execution (`behavior: NON_BLOCKING`) is the default mode, and blocking mode can be set with `behavior: BLOCKING`.» ⚠ противоречит общей странице tools (см. раздел 3) |
| **Function calling — scheduling** | ДА | `scheduling` (в `toolResponse.response`) | `WHEN_IDLE`, `SILENT`, и третье значение — расходится между страницами: `INTERRUPT` (tools-страница) vs `INTERRUPTED` (model page) | [Tools](https://ai.google.dev/gemini-api/docs/live-api/tools), [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) |
| Параллельные вызовы функций | Предположительно да (не формально гарантировано) | — | «From a single prompt, the model can generate multiple function calls» | [Tools](https://ai.google.dev/gemini-api/docs/live-api/tools) |
| `toolCallCancellation` | ДА | `toolCallCancellation.ids[]` | отправляется только когда клиент перебивает ход сервера | [WebSocket reference](https://ai.google.dev/api/live) |
| Лимиты числа функций / размера схемы | **В документации не найдено** | — | — | — |
| **Google Search grounding** | **ДА** | `tools: [{google_search: {}}]` | — | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) — «Supported» |
| **Code execution** | **НЕТ** | — | — | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) — «Not supported» (⚠ внутреннее противоречие на странице tools, см. раздел 3) |
| **URL context** | **НЕТ** | — | — | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) — «Not supported» |
| Session resumption | ДА | `sessionResumption.handle` (клиент) / `sessionResumptionUpdate.{newHandle, resumable}` (сервер) | handle валиден 2 часа после завершения сессии; режим «transparent» на странице Session management **не упомянут** | [Session management](https://ai.google.dev/gemini-api/docs/live-api/session-management) |
| Context window compression | ДА | `contextWindowCompression.{slidingWindow, triggerTokens}` | `triggerTokens` default = 80% окна контекста (по WS-референсу); `slidingWindow.targetTokens` default = `triggerTokens/2` | [WebSocket reference](https://ai.google.dev/api/live) (числовые defaults — только там; страница Session management их не называет) |
| GoAway | ДА | `goAway.timeLeft` (Duration) | минимум зависит от модели, указан «с лимитами запросов» (конкретное число не найдено) | [Session management](https://ai.google.dev/gemini-api/docs/live-api/session-management), [WebSocket reference](https://ai.google.dev/api/live) |
| Вход — аудио | ДА | PCM16, 16 кГц, little-endian, mono | MIME `audio/pcm;rate=16000`; любая частота принимается, API сам ресемплит | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| Вход — 8 кГц / телефония | **В документации не найдено явно** (только апсемплинг до 16кГц подразумевается) | — | — | — |
| Выход — аудио | ДА | PCM16, 24 кГц, little-endian | фиксировано | [Overview](https://ai.google.dev/gemini-api/docs/live) |
| Видео/изображения на входе | ДА (общий механизм; явного «для 3.8» подтверждения с точной формулировкой нет, кроме дефолта turnCoverage) | `realtimeInput.video` (JPEG/PNG, ≤1 fps) | — | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| «Видео отправляется по умолчанию» | ДА, подтверждено — через `turnCoverage` | `realtimeInputConfig.turnCoverage` | default для 3.8 (и 3.1+) = `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO` | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Defaults to `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO`» |
| `inputAudioTranscription` | ДА | `inputAudioTranscription.{languageCodes, customVocabulary, mode}` | `mode`: `VERBATIM` (default) / `SMART` | [WebSocket reference](https://ai.google.dev/api/live), [Live transcribe](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe) |
| `outputAudioTranscription` | ДА (в WS-референсе; на странице live-transcribe — не найдено для исходящей речи) | `outputAudioTranscription` (тот же тип `AudioTranscriptionConfig`) | — | [WebSocket reference](https://ai.google.dev/api/live) |
| Эфемерные токены | ДА | `expireTime` (default 30 мин), `newSessionExpireTime` (default 1 мин), `uses`, `liveConnectConstraints` | — | [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens) (страница помечена **Preview**) |
| Лимит длительности сессии | ДА | — | без сжатия: аудио-только ≤15 мин, аудио+видео ≤2 мин; соединение ~10 мин | [Session management](https://ai.google.dev/gemini-api/docs/live-api/session-management) |
| Максимум параллельных сессий / RPM / TPM для 3.8 | **В документации не найдено** (отдаётся в закрытую за логином панель AI Studio) | — | — | [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) только даёт лимиты по тратам ($/10 мин по тиру) |
| Цена — вход аудио | ДА | — | $3.00 / 1M токенов ≈ $0.005/мин | [Pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Цена — выход аудио | ДА | — | $12.00 / 1M токенов ≈ $0.018/мин | [Pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Vertex AI — наличие модели | **Не подтверждено официально** (есть только косвенные признаки, частично недоступны для извлечения) | — | — | см. раздел 3 |

---

## 2. Как сделать речь живой — приёмы из документации (по убыванию эффекта)

Это не рейтинг «силы эффекта» из документации (такого рейтинга там нет) — порядок составлен ВЫВОД-ом, по тому, насколько прямо и однозначно каждый приём документирован и насколько он структурно важен для диалога.

1. **Структурированный `systemInstruction`: персона → правила диалога → guardrails.** Официальная рекомендация — «having a clearly-defined set of system instructions (SIs)», разделять одноразовые элементы разговора (intake) и повторяющиеся циклы (open discussion), явно писать, что модель может оставаться в цикле «for as long as the user wants». Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

2. **Короткие ответы, раскрытие информации по частям.** Прямая цитата: «Keep your responses short and progressively disclose more information». Также: «DO NOT repeat what the client is saying back to them», «NO PLATITUDES.» Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

3. **Слово "unmistakably" как усилитель инструкции.** Документация прямо советует использовать это слово, чтобы модель точнее соблюдала капризные правила: «use the word *unmistakably* to guide the model to be precise», включая форсирование языка ответа: «YOU MUST RESPOND UNMISTAKABLY IN {OUTPUT_LANGUAGE}.» Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

4. **Приветствие первым — только через промпт, не через протокол.** «Live API expects user input before it responds» — то есть нет отдельного «служебного» сообщения для автоматического приветствия. Чтобы модель заговорила первой, нужно явно прописать это в системной инструкции и дать ей данные о пользователе: «Include information about the user to have Live API personalize that greeting». Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

5. **Prompt chaining вместо одного длинного промпта.** «consider using prompt chaining instead» — то есть несколько более коротких промптов, подменяемых по ходу разговора, а не один гигантский документ. Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

6. **Явные условия вызова функций — отдельными предложениями.** «Specify tool calls within a flow in distinct sentences», «Be sure to tell Gemini under what conditions a tool call should be invoked» — чтобы не было долгих пауз / неожиданных вызовов. Источник: [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

7. **Proactive audio уже встроен и не выключается — не нужно и не стоит пытаться его настраивать.** Это не «приём», а характеристика модели: 3.8 сама решает, когда не отвечать на нерелевантную/фоновую речь. Источник: [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live), [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices).

8. **Корректная настройка VAD под канал связи (в первую очередь при шуме/телефонии).** Понижать `endOfSpeechSensitivity`/увеличивать `silenceDurationMs` при шумных линиях — ВЫВОД по аналогии с общим описанием полей (документация не даёт готовых чисел именно «для телефонии», но описывает сам trade-off: высокая чувствительность конца речи = меньше задержка, но риск разрезать фразу; выше `silenceDurationMs` = более устойчиво к паузам, но больше латентность). Источник: [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [WebSocket reference](https://ai.google.dev/api/live).

9. **Ограничение языка через промпт, не через конфиг.** Для native-audio моделей (3.8 входит в их число) нет поля `languageCode` — язык выбирается автоматически и может переключаться в процессе разговора; единственный документированный рычаг — явно указать в system instruction, на каком языке отвечать. Источник: [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities).

10. **Пауз, смеха, вдохов, "ээ/ммм", темпа речи и аудио-тегов как отдельных параметров в Live API документация не описывает** — это зона чистого промпт-инжиниринга (см. п.1–3) либо (неподтверждённо) функциональность соседнего продукта Gemini TTS prompting guide, которая не подтверждена для `gemini-3.8-live`. Если нужна максимальная живость — весь рычаг управления, который реально документирован, это **текст системной инструкции**, а не JSON-поля.

---

## 3. Расхождения между страницами документации

| № | Тема | Страница A говорит | Страница B говорит | Как трактовать |
|---|---|---|---|---|
| 1 | **Affective dialogue** | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Affective dialogue is removed from the API. Remove any `enable_affective_dialog` configurations from your code.» | [Overview](https://ai.google.dev/gemini-api/docs/live) и [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) описывают `enable_affective_dialog`/`enableAffectiveDialog` как действующее `v1beta`-поле, и говорят только: «This feature is not supported in Gemini 3.1 Flash Live» — про 3.8 вообще не упоминают | Страница модели — самая конкретная и самая новая по теме 3.8; общие страницы (overview/capabilities) просто не обновлены под 3.8. **Доверять странице модели.** |
| 2 | **Function calling — default `behavior`** | [Tools](https://ai.google.dev/gemini-api/docs/live-api/tools): «Function calling executes sequentially by default... Asynchronous calling is available only on Gemini 2.5 Flash Live Preview» (т.е. BLOCKING — дефолт, NON_BLOCKING не называется явным литералом) | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Async execution (`behavior: NON_BLOCKING`) is the default mode, and blocking mode can be set with `behavior: BLOCKING`» | Не прямое противоречие — разный охват (общая страница написана про старые модели). **Для 3.8 дефолт — NON_BLOCKING**, страница модели это прямо переопределяет. |
| 3 | **Scheduling — написание третьего значения** | [Tools](https://ai.google.dev/gemini-api/docs/live-api/tools), код-пример: `"scheduling": "INTERRUPT"`, `FunctionResponseScheduling.INTERRUPT` | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live), текст миграции: «Scheduling options `SILENT`, `WHEN_IDLE`, and `INTERRUPTED` are supported» | Не разрешено документацией. Код-пример (`INTERRUPT`) выглядит более вероятным «настоящим» значением wire-формата, но нужна прямая проверка в SDK/рантайме — указывайте оба варианта и тестируйте. |
| 4 | **Proactive audio — настраиваемость** | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities): описывает `proactivity.proactiveAudio` как рычаг, который «lets you control when the model responds», говорит только «Not supported in Gemini 3.1 Flash Live» | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) и [Best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices): «Proactive audio is now permanently enabled [in gemini-3.8-live]. Setting `proactive_audio: false` returns an error» | Страница модели и best practices сходятся и явно называют 3.8 — **доверять им**; Capabilities не актуализирована под 3.8. |
| 5 | **Число поддерживаемых языков** | [Overview](https://ai.google.dev/gemini-api/docs/live): «Converse in 70 supported languages» | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities): список на **99** языков (с BCP-47 кодами, включая `ru`, `ky`) | Не устранено ни на одной из страниц. ВЫВОД: вероятно, 70 — это более старое число (для диалоговой речи), 99 — обновлённый список для native-audio моделей вроде 3.8; но однозначного подтверждения в документации нет. |
| 6 | **Code execution** | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): «Code execution — Not supported» | Та же страница [Tools](https://ai.google.dev/gemini-api/docs/live-api/tools) в прозе: «The model might generate and execute Python code to use Search», функция «executes in a sandbox environment» — но таблица поддержки на этой же странице пишет «Code execution: Not supported» для 3.1/2.5 | Внутреннее противоречие на странице tools (не между разными моделями, а в пределах одной страницы): проза говорит, что исполнение кода происходит (как часть Search), таблица — что не поддерживается. Для 3.8 ориентир — модельная страница («Not supported»). |
| 7 | **Контекстное окно** | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live): input 131 072 / output 65 536 токенов | [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities): «128k tokens for native audio output models» | Скорее согласуются (128k ≈ округление 131 072), output-лимит (65 536) на странице Capabilities не упомянут вовсе. |
| 8 | **Output modality — единственное число или нет** | [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live), таблица capabilities: «Audio generation» и «text and audio» (во множественном смысле) | Та же страница, прозa миграции: «Audio is the supported response modality» (в единственном числе) | Внутреннее несоответствие на одной и той же странице модели — вероятно, таблица относится к общим способностям модели, а прозa про Live-сессию конкретно (где выходной канал — только аудио, текст получают через `outputAudioTranscription`). Это ВЫВОД, прямого разъяснения в документации нет. |
| 9 | **`thinkingLevel` casing** внутри одной страницы | [Thinking page](https://ai.google.dev/gemini-api/docs/live-api/thinking): raw WS-пример показывает `"thinkingLevel": "LOW"` (верхний регистр) | Та же страница, SDK-пример показывает `"low"` (нижний регистр) | Вероятно, это разные уровни представления (enum-строка на wire vs Python-литерал), но страница сама не поясняет это явно — проверяйте под свой SDK. |
| 10 | **Vertex AI — наличие и доступность `gemini-3.8-live`** | Официальной страницы cloud.google.com/vertex-ai с полным описанием модели не обнаружено | Неофициальный форумный пост (discuss.google.dev) про отказ в доступе к `gemini-3.8-live` на Vertex AI через BidiGenerateContent («no access») | **Неофициальный источник**, не подтверждён документацией — приводится только как сигнал, не как факт. |

---

## 4. Что в документации не найдено

- Числовые значения по умолчанию для `prefixPaddingMs` и `silenceDurationMs`.
- Конкретные рекомендованные настройки VAD «для телефонии» или «для шумных линий» — слово «telephony» в документации Live API не встречается.
- Поддержка входного аудио на 8 кГц (документирован только вход 16 кГц, с автоматическим ресемплингом на стороне API любой присланной частоты — явного заявления про 8 кГц нет).
- Параметр скорости речи, управление паузами/вдохами/смехом/«ээ» как настраиваемое поле (а не промпт).
- Подтверждение того, что «аудио-теги» из Gemini TTS prompting guide применимы к `gemini-3.8-live` (это документация другого продукта — TTS, не Live).
- Явный список гендер/акцент для конкретных `voiceName` (только короткий дескриптор типа «Firm», «Breathy»).
- Является ли список голосов Live API идентичным списку голосов `generateContent`/TTS — страница Capabilities говорит, что набор «slightly different», но не даёт отдельный список именно для Live.
- Режим `sessionResumption` «transparent» — на странице Session management не упомянут вообще (хотя упоминается в общих описаниях Live API в иных источниках/версиях — не подтверждено для 3.8).
- Числовые значения по умолчанию для `contextWindowCompression.slidingWindow` (кроме выведенной формулы `targetTokens = triggerTokens/2`) — конкретные дефолтные числа токенов не приведены ни на одной просмотренной странице.
- Минимальное значение `GoAway.timeLeft` (сказано, что оно «указано с лимитами запросов», но сама страница с лимитами не даёт конкретного числа для 3.8).
- Лимиты на число function declarations или общий размер схемы инструментов.
- Явное указание, что говорит/делает модель, пока выполняется блокирующий (BLOCKING) вызов функции — чем заполняется пауза.
- Rate limits / максимум параллельных сессий / RPM / TPM конкретно для `gemini-3.8-live` — отдаётся на закрытую авторизацией панель AI Studio, в статической документации не найдено.
- Известные проблемы (known issues) именно для Live API 2026 в виде отдельного списка — только общие формулировки model card DeepMind про «occasional slowness or timeout issues» и ограничения Live Avatar «a few minutes of continuous interaction, rather than extended hours».
- Полноценная страница Vertex AI с ценами/регионами/квотами для `gemini-3.8-live` — тело страницы `configure-language-voice` не удалось получить (JS-рендеринг), доступность модели на Vertex AI не подтверждена.
- Цена context caching для `gemini-3.8-live` (строки в таблице цен нет — вероятно, кэширование и так не поддерживается, см. таблицу п.1).
- Точный default числового значения `languageCode`-подобного поведения для смешанной речи (сказано только, что модель переключает язык «во время разговора», без деталей про смешанную/code-switching речь).

---

## 5. Готовые JSON-примеры setup-сообщения для телефонного агента

⚠️ Важная оговорка по частоте дискретизации: в документации явно подтверждён только **вход 16 кГц** (`audio/pcm;rate=16000`); про 8 кГц явного заявления нет (см. раздел 4) — но указано, что API принимает «любую частоту» и сам делает ресемплинг. Поэтому ниже показан вариант на 16 кГц (документированный) и вариант с явным MIME на 8 кГц (не гарантирован документацией — используйте на свой риск, то есть с проверкой, и будьте готовы на стороне клиента апсемплить до 16 кГц, если 8 кГц не примется).

### 5.1. Setup-сообщение — вход 16 кГц (полностью по документации)

```json
{
  "setup": {
    "model": "models/gemini-3.8-live",
    "generationConfig": {
      "responseModalities": ["AUDIO"],
      "speechConfig": {
        "voiceConfig": {
          "prebuiltVoiceConfig": {
            "voiceName": "Kore"
          }
        }
      }
    },
    "systemInstruction": {
      "parts": [
        { "text": "You are a helpful phone support agent. Keep your responses short and progressively disclose more information. DO NOT repeat what the client is saying back to them. NO PLATITUDES. YOU MUST RESPOND UNMISTAKABLY IN Russian." }
      ]
    },
    "realtimeInputConfig": {
      "automaticActivityDetection": {
        "disabled": false,
        "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
        "prefixPaddingMs": 20,
        "silenceDurationMs": 100
      },
      "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
      "turnCoverage": "TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO"
    },
    "inputAudioTranscription": {
      "mode": "VERBATIM"
    },
    "outputAudioTranscription": {},
    "sessionResumption": {},
    "contextWindowCompression": {
      "slidingWindow": {}
    }
  }
}
```

Поля и значения взяты из: [WebSocket reference](https://ai.google.dev/api/live), [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [Session management](https://ai.google.dev/gemini-api/docs/live-api/session-management), [Model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live). Поля `enable_affective_dialog` и `thinking_level`/`thinking_config` **намеренно не включены** — модельная страница прямо требует их убрать для `gemini-3.8-live`. `proactivity` также не указан явно, так как на 3.8 он принудительно `true` и его указание не требуется (и `false` вызовет ошибку).

Аудио-чанки реального разговора отправляются отдельными сообщениями `realtimeInput.audio` (не частью setup), пример из документации:

```json
{
  "realtimeInput": {
    "audio": {
      "data": "<base64 PCM16 16kHz mono chunk>",
      "mimeType": "audio/pcm;rate=16000"
    }
  }
}
```

Источник: [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [Get started (WebSocket)](https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket).

### 5.2. Вариант для телефонии 8 кГц (⚠ НЕ подтверждён документацией — экспериментальный MIME)

```json
{
  "realtimeInput": {
    "audio": {
      "data": "<base64 PCM16 8kHz mono chunk>",
      "mimeType": "audio/pcm;rate=8000"
    }
  }
}
```

Документация не подтверждает и не отрицает поддержку `rate=8000` явно — только говорит, что «any sample rate accepted, API resamples». Рекомендуется либо протестировать именно это значение на реальном эндпоинте, либо подстраховаться и апсемплить аудио на стороне клиента/телефонного гейтвея до 16 кГц перед отправкой, что соответствует документированному пути.

---

## Сводка по самому важному вопросу (раздел 1 задания)

- **Affective dialogue** в `gemini-3.8-live` — **удалено**, однозначно подтверждено на странице модели («Affective dialogue is removed from the API»). Общие страницы (overview, capabilities) этот факт не отражают и продолжают описывать `enable_affective_dialog` как рабочее поле — но без упоминания 3.8, то есть это не прямое противоречие, а неактуализированная документация. Полноценной «замены» эмоциональности через отдельный параметр в документации не найдено — только обычный промпт-инжиниринг тона/персоны в `systemInstruction` (раздел 2 этого документа, best practices).
- **Proactive audio** в `gemini-3.8-live` — всегда включён, отключить нельзя (`false` → ошибка). Подтверждено дважды (model page + best practices).
- **Thinking** — `thinking_level`/`thinking_config` не поддерживаются как ручка, но «interleaved reasoning» работает автоматически. Для настраиваемого уровня рассуждений нужна отдельная модель `gemini-3.8-live-extended-thinking`.
- **Барж-ин** — есть (`activityHandling`, `interrupted`, `toolCallCancellation`), но точный набор полей документирован только в «сыром» WebSocket-референсе (`ai.google.dev/api/live»), а не в более читаемых guide-страницах.
- **Язык** — у native-audio моделей (3.8 включена) нет настраиваемого поля `languageCode`; язык/акцент/переключение языка регулируется только через текст системной инструкции. Русский (`ru`) и кыргызский (`ky`) официально в списке поддерживаемых языков.
