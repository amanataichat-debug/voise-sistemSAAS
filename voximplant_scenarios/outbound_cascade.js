/**
 * outbound_cascade — ИСХОДЯЩИЙ full-cascade Voice AI сценарий Voksy AI.
 *
 *   STT:  встроенный ASR (Yandex v2, streaming + interim), язык из конфига
 *   LLM:  OpenAI gpt-realtime-2.1-mini — Realtime API (WebSocket), режим ТОЛЬКО ТЕКСТ
 *   TTS:  VoxTTS (realtime-стриминг, голоса Anna/Sergey)
 *   Turn-taking: Silero VAD + Pipecat Smart Turn через VoxTurnTaking
 *
 * ПОЧЕМУ Realtime, а не Chat Completions (переход v2 -> v3):
 *  WS открыт с начала звонка (нет установки соединения на каждый ход), контекст
 *  диалога живёт на стороне OpenAI (шлём только новую реплику, старое идёт как
 *  cached-токены), сама модель дистиллирована под низкую задержку. TTFT падает
 *  с ~600мс до ~250мс. Прогрев (warmup) за ненадобностью удалён.
 *
 * ВАЖНО — TURN DETECTION У МОДЕЛИ ВЫКЛЮЧЕН:
 *  Realtime — чистый ТЕКСТОВЫЙ мозг: аудио в него не заводится вообще, в сессии
 *  audio.input.turn_detection = null. Момент «пора отвечать» определяет НАШ
 *  VoxTurnTaking и явно дёргает responseCreate().
 *
 * ОТЛИЧИЯ ОТ inbound_cascade:
 *  1. Точка входа — AppEvents.Started + VoxEngine.customData() (не CallAlerting).
 *  2. Мы САМИ звоним: VoxEngine.callPSTN(phone, caller_id).
 *  3. Конфиг — /api/telephony/outbound-config?assistant_id=...&assistant_type=cascade.
 *  4. Контекст звонка (задача + CRM) инжектится в system-промпт.
 *  5. READINESS-BEFORE-DIAL: WS к OpenAI / TTS / ASR готовим ДО дозвона — если
 *     что-то не поднялось, PSTN не набираем (0₽ телефонии).
 *  6. MUTE-окно: первые mute_duration_ms после ответа мик абонента закрыт
 *     (флаг micOpen) — «Алло»/шум при поднятии трубки не обрывают приветствие.
 *  7. TOOL CALLING: ассистент может вызывать функции (в т.ч. hangup_call, чтобы
 *     самому завершить звонок). Вызов приходит целиком в ResponseOutputItemDone.
 *  8. Silence hard-timeout ~180с (защита от автоответчика/тишины). У Realtime-
 *     сессии есть TTL, поэтому обрыв WS переживаем реконнектом (см. ниже).
 *
 * СПЕКУЛЯЦИЯ здесь не используется (как и в inbound после перехода на Realtime):
 * при TTFT ~250мс выигрыш копеечный, а с серверным контекстом спекулятивная
 * реплика отравляет историю диалога.
 *
 * ТРЕБОВАНИЯ:
 * 1. Правило outbound_cascade — цепочка [vox-turn-taking, outbound_cascade].
 * 2. Запуск: StartScenarios с script_custom_data (phone_number, assistant_id,
 *    caller_id, contact_name, task_title, task_description, custom_greeting, task).
 */

require(Modules.ASR);
require(Modules.OpenAI);
require(Modules.VoxTTS);
require(Modules.Recorder);
require(Modules.Silero);
require(Modules.Pipecat);

// ────────────────────────────────────────────────────────────────────────────
// ВСТРОЕННЫЙ VoxTurnTaking (самодостаточность).
// Идемпотентно: если правило — цепочка [vox-turn-taking, outbound_cascade], то
// vox-turn-taking.js уже объявил глобальный VoxTurnTaking (const) → typeof !==
// "undefined" → это определение ПРОПУСКАЕТСЯ (никакого переобъявления). Если же
// правило одиночное — объявляем VoxTurnTaking здесь. Так сценарий работает при
// любой конфигурации правила. Присваивание без const/var — чтобы не конфликтовать
// с const-объявлением в цепочечном режиме.
// ВАЖНО: держать в синхроне с voximplant_scenarios/vox-turn-taking.js.
// ────────────────────────────────────────────────────────────────────────────
if (typeof VoxTurnTaking === "undefined") {
    // eslint-disable-next-line no-global-assign, no-undef
    VoxTurnTaking = {

        DEFAULTS: {
            vadOptions: {
                // Для русской разговорной речи паузы 0.3-0.5с внутри фразы — норма
                // («пока не знаю... машина у вас»). 200-300мс дробили одну фразу на
                // сегменты: отсюда лишние barge-in и закрытие хода на границе
                // ложного сегмента.
                threshold: 0.5,
                minSilenceDurationMs: 650,
                // 10мс срезало края сегментов. 200мс — слышимый запас по краям.
                speechPadMs: 200,
            },
            turnDetectorOptions: {
                threshold: 0.5,
            },
            policy: {
                // ── Закрытие хода по вердикту детектора ────────────────────────
                //  - p >= confidentEouProbability: фраза уверенно закончена ->
                //    быстрый settle;
                //  - серая зона -> осторожный settle.
                confidentEouProbability: 0.95,
                transcriptSettleFastMs: 120,
                transcriptSettleMs: 350,

                // ── Страховочный таймаут (фолбэк) ──────────────────────────────
                // Взводится на speechEnd. Больше НЕ является основным механизмом
                // закрытия: детектор может его отодвинуть (см. вето ниже).
                userSpeechTimeoutMs: 900,

                // ── Вето детектора (лечение причины №3) ────────────────────────
                // endOfTurn=false -> отодвигаем закрытие тем сильнее, чем увереннее
                // детектор в том, что фраза не закончена.
                vetoStrongProbability: 0.15, // p < 0.15 — «точно не договорил»
                vetoSoftProbability: 0.4,    // p < 0.40 — «скорее не договорил»
                vetoHoldStrongMs: 2000,
                vetoHoldSoftMs: 1400,
                maxVetoHolds: 3,
                // Абсолютный потолок удержания одного хода от первого speechEnd —
                // чтобы вето не подвесило разговор.
                maxTurnHoldMs: 4000,

                // ── Стабильность транскрипта (лечение причины №1, часть 2) ─────
                // Не закрываем ход, пока ASR ещё дописывает текст. Признак по
                // факту, а не по таймеру: сколько прошло с последнего изменения.
                interimStableMs: 400,
                maxInterimWaitMs: 1500,

                // Перепроверка «клиент всё ещё говорит?».
                userSpeakingRecheckMs: 250,
                // Аварийный потолок: если VAD залип в speech, через столько мс
                // закрываем ход принудительно, иначе агент немой до конца звонка.
                userSpeakingMaxHoldMs: 15000,

                // ── Реконсиляция позднего финала ───────────────────────────────
                reconcileWindowMs: 5000,

                // ── Barge-in ───────────────────────────────────────────────────
                bargeInMinSpeechMs: 150,

                // ── Эвристики коротких реплик (из v1) ──────────────────────────
                shortUtteranceExtensionMs: 900,
                fastShortUtteranceTimeoutMs: 500,
                shortUtteranceMaxChars: 12,
                shortUtteranceMaxWords: 2,
                lowConfidenceShortUtteranceThreshold: 0.75,
                continuationTokens: ["and", "but", "so", "well", "then", "uh", "um"],
                trailingContinuationTokens: [],
                completeShortAnswers: [],

                // Порог вероятности EOU, при котором ход считается ГОТОВЫМ к ранней
                // (спекулятивной) подаче через onSpeculativeTurn.
                speculativeEouProbability: 0.7,
            },
        },

        async create(options) {
            const {
                call,
                stt,
                onUserTurn,
                onInterrupt,
                // Ранний сигнал для спекулятивной генерации. Опционален.
                onSpeculativeTurn,
                // Поздний ASR-финал уточнил уже отправленный текст. Опционален.
                // (fullText, turnId, { sentText, lagMs, reason })
                onTurnCorrection,
                // Звучит ли агент прямо сейчас. Нужен, чтобы barge-in считался
                // только настоящим перебиванием, а не любым сегментом VAD.
                isAgentSpeaking,
                enableLogging = false,
                logger = (line) => Logger.write(line),
            } = options;
            const vadOptions = Object.assign({}, this.DEFAULTS.vadOptions, options.vadOptions);
            const turnDetectorOptions = Object.assign(
                {},
                this.DEFAULTS.turnDetectorOptions,
                options.turnDetectorOptions
            );
            const policy = Object.assign({}, this.DEFAULTS.policy, options.policy);

            const vad = await Silero.createVAD(vadOptions);
            const turnDetector = await Pipecat.createTurnDetector(turnDetectorOptions);

            call.sendMediaTo(vad);
            call.sendMediaTo(turnDetector);

            const log = (line) => {
                if (enableLogging) logger(line);
            };
            const emitModuleEvent = (eventName, event) => {
                logger(`===${eventName}===`);
                if (event) logger(JSON.stringify(event));
            };

            // ── Таймеры ────────────────────────────────────────────────────────
            let closeTimer = null;   // фолбэк / отложенное закрытие
            let settleTimer = null;  // окно ожидания после вердикта детектора
            let bargeInTimer = null; // дебаунс перебивания
            let stuckTimer = null;   // сторож залипшего VAD

            // ── Транскрипт ─────────────────────────────────────────────────────
            let finalTranscript = "";
            let interimTranscript = "";
            let transcriptSeparator = "";
            let lastTranscriptChangeAt = 0;
            let stabilityWaitStartedAt = 0;
            let lastFinalConfidence = 1;
            let replaceableShortFinal = false;
            let shortExtensionApplied = false;
            let acceptingTranscript = false;

            // ── Счётчики поколений (лечение причины №2) ────────────────────────
            // turnId    — растёт ТОЛЬКО при сабмите. Отдаётся сценарию как version.
            // segmentId — растёт на каждом speechStart. Гейтит таймеры/предсказания.
            let turnId = 0;
            let segmentId = 0;

            // ── Состояние речи клиента (лечение причины №1) ────────────────────
            let userSpeaking = false;
            let userSpeakingSince = 0;
            let turnHoldStartedAt = 0; // первый speechEnd текущего хода
            let vetoHolds = 0;

            // ── Состояние детектора ────────────────────────────────────────────
            let smartTurnComplete = false;
            let predictSegment = -1;   // segmentId в момент predict()
            let speculativeFiredForSegment = -1;

            // ── Прочее ─────────────────────────────────────────────────────────
            let allowAgentAudio = true;
            // { turnId, norm, raw, at, seg } — для реконсиляции. seg — сегмент VAD,
            // в котором ход был закрыт: если он не сменился, поздний финал физически
            // относится к тому же куску аудио.
            let submitted = null;

            const stats = {
                segments: 0,
                closes: {},
                holdsUserSpeaking: 0,
                holdsUnstable: 0,
                holdsVeto: 0,
                stalePredictions: 0,
                bargeIns: 0,
                bargeInsSuppressed: 0,
                corrections: 0,
                correctionCharsGained: 0,
                forcedCloses: 0,
            };

            const clearTimers = () => {
                if (closeTimer) clearTimeout(closeTimer);
                if (settleTimer) clearTimeout(settleTimer);
                closeTimer = null;
                settleTimer = null;
            };

            const normalize = (text) =>
                (text || "")
                    .trim()
                    .toLowerCase()
                    .replace(/[.,!?;:…—-]+/gu, " ")
                    .replace(/\s+/gu, " ")
                    .trim();

            const normalizeConfidence = (value) => {
                if (typeof value !== "number" || Number.isNaN(value)) return null;
                return value > 1 ? value / 100 : value;
            };

            const isShortUtterance = (text) => {
                if (!text) return false;
                const words = text.trim().split(/\s+/).filter(Boolean);
                return (
                    text.length <= policy.shortUtteranceMaxChars &&
                    words.length <= policy.shortUtteranceMaxWords
                );
            };

            const startsWithContinuationToken = (text) => {
                if (!text) return false;
                const firstWord = text.trim().split(/\s+/)[0]?.toLowerCase();
                return policy.continuationTokens.includes(firstWord);
            };

            // Завершённый короткий ответ из вайтлиста («да», «нет», «ок»...).
            const isCompleteShortAnswer = (text) => {
                const list = policy.completeShortAnswers || [];
                if (!list.length || !text) return false;
                const norm = text.trim().toLowerCase().replace(/[.,!?;:…]+$/u, "");
                return list.includes(norm);
            };

            // Фраза выглядит оборванной, если её последнее слово — предлог/союз/
            // вопросительное слово, после которого обычно следует продолжение.
            const endsWithContinuationToken = (text) => {
                if (!text) return false;
                const tokens = policy.trailingContinuationTokens || [];
                if (!tokens.length) return false;
                const words = text.trim().split(/\s+/);
                const lastWord = words[words.length - 1]
                    ?.toLowerCase()
                    .replace(/[.,!?;:…]+$/u, "");
                return tokens.includes(lastWord);
            };

            const buildInput = () => {
                let input = finalTranscript;
                if (interimTranscript) {
                    if (input) input += transcriptSeparator;
                    input += interimTranscript;
                }
                return input.trim();
            };

            const noteTranscriptChange = () => {
                lastTranscriptChangeAt = Date.now();
            };

            // Решение опирается на частичный результат, если финала ещё нет вовсе
            // либо поверх финала уже течёт новый interim (клиент продолжает).
            const leaningOnPartial = () => !finalTranscript || !!interimTranscript;

            // Отложить закрытие хода, сохранив исходную причину для логов/статистики.
            const deferClose = (delayMs, reason) => {
                if (closeTimer) clearTimeout(closeTimer);
                if (settleTimer) clearTimeout(settleTimer);
                settleTimer = null;
                const seg = segmentId;
                closeTimer = setTimeout(() => {
                    if (seg !== segmentId) return;
                    submitCurrentTurn(reason);
                }, delayMs);
            };

            const bumpClose = (reason, count) => {
                stats.closes[reason] = (stats.closes[reason] || 0) + (count || 1);
            };

            function submitCurrentTurn(reason) {
                const input = buildInput();
                if (!input) return false;

                const forced = reason === "FORCED_CLOSE";

                // ── ИНВАРИАНТ 1. Пока клиент говорит — ход не закрывается. ─────
                // Ровно этой проверки не было в v1, и именно её отсутствие давало
                // перебивание на микропаузе внутри фразы.
                if (!forced && userSpeaking) {
                    const held = userSpeakingSince ? Date.now() - userSpeakingSince : 0;
                    if (held < policy.userSpeakingMaxHoldMs) {
                        stats.holdsUserSpeaking += 1;
                        log(`===HOLD_USER_SPEAKING=== (${reason}) held=${held}ms :: ${input}`);
                        deferClose(policy.userSpeakingRecheckMs, reason);
                        return false;
                    }
                    // Аварийный выход: VAD, похоже, залип в speech.
                    log(`===VAD_STUCK=== speech активен ${held}ms, закрываем принудительно`);
                }

                // ── ИНВАРИАНТ 2. Текст ещё дописывается — ждём. ────────────────
                // Признак по факту («ASR дописал слово N мс назад»), а не по слепому
                // таймеру: это то, что реально отличает «фраза кончилась» от
                // «человек ещё говорит, а мы просто быстрее ASR».
                if (!forced && leaningOnPartial()) {
                    const sinceChange = Date.now() - (lastTranscriptChangeAt || 0);
                    if (sinceChange < policy.interimStableMs) {
                        if (!stabilityWaitStartedAt) stabilityWaitStartedAt = Date.now();
                        if (Date.now() - stabilityWaitStartedAt < policy.maxInterimWaitMs) {
                            stats.holdsUnstable += 1;
                            const wait = Math.max(60, policy.interimStableMs - sinceChange);
                            log(`===HOLD_TRANSCRIPT_UNSTABLE=== (${reason}) +${wait}ms :: ${input}`);
                            deferClose(wait, reason);
                            return false;
                        }
                        log(`===STABILITY_CAP=== ${policy.maxInterimWaitMs}ms исчерпаны, закрываем`);
                    }
                }

                // ── Эвристики коротких/оборванных реплик (из v1) ───────────────
                const isWhitelisted = isCompleteShortAnswer(input);

                if (
                    !forced &&
                    !isWhitelisted &&
                    reason !== "FALLBACK_END_OF_TURN" &&
                    endsWithContinuationToken(input) &&
                    !shortExtensionApplied
                ) {
                    shortExtensionApplied = true;
                    log(`===HOLD_TRAILING=== ${input}`);
                    deferClose(policy.shortUtteranceExtensionMs, reason);
                    return false;
                }

                if (!forced && !isWhitelisted && replaceableShortFinal && !shortExtensionApplied) {
                    shortExtensionApplied = true;
                    log(`===HOLD_SHORT_FINAL=== ${input}`);
                    deferClose(policy.shortUtteranceExtensionMs, reason);
                    return false;
                }

                // ── Закрываем ход ─────────────────────────────────────────────
                if (forced) stats.forcedCloses += 1;
                bumpClose(reason);
                log(`===${reason}===`);
                log(`===USER=== ${input}`);

                const closedTurnId = turnId;
                submitted = {
                    turnId: closedTurnId,
                    norm: normalize(input),
                    raw: input,
                    at: Date.now(),
                    seg: segmentId,
                };

                allowAgentAudio = true;
                onUserTurn(input, closedTurnId, reason);

                finalTranscript = "";
                interimTranscript = "";
                transcriptSeparator = "";
                smartTurnComplete = false;
                acceptingTranscript = false;
                lastFinalConfidence = 1;
                replaceableShortFinal = false;
                shortExtensionApplied = false;
                stabilityWaitStartedAt = 0;
                turnHoldStartedAt = 0;
                vetoHolds = 0;
                predictSegment = -1;
                turnId += 1;
                clearTimers();
                if (stuckTimer) { clearTimeout(stuckTimer); stuckTimer = null; }
                return true;
            }

            const startHardTimeout = (delay = policy.userSpeechTimeoutMs) => {
                clearTimers();
                const seg = segmentId;
                closeTimer = setTimeout(() => {
                    if (seg !== segmentId) return;
                    if (!buildInput()) {
                        // Сегмент речи закончился, а текста в нём не оказалось —
                        // кашель, шум линии, эхо. Возвращаем агенту право говорить:
                        // иначе allowAgentAudio, снятый на speechStart, останется
                        // снятым до следующего настоящего хода, и агент онемеет.
                        if (!allowAgentAudio && !userSpeaking) {
                            allowAgentAudio = true;
                            log("===EMPTY_SEGMENT=== текста нет, агенту возвращён голос");
                        }
                        return;
                    }
                    submitCurrentTurn("FALLBACK_END_OF_TURN");
                }, delay);
            };

            [
                Silero.VADEvents.ConnectorInformation,
                Silero.VADEvents.Error,
            ].forEach((eventName) => {
                vad.addEventListener(eventName, (event) => emitModuleEvent(eventName, event));
            });

            [
                Pipecat.TurnEvents.ConnectorInformation,
                Pipecat.TurnEvents.Error,
            ].forEach((eventName) => {
                turnDetector.addEventListener(eventName, (event) =>
                    emitModuleEvent(eventName, event)
                );
            });

            // ── ASR: промежуточные результаты ──────────────────────────────────
            stt.addEventListener(ASREvents.InterimResult, (event) => {
                if (!acceptingTranscript) return;
                const text = event?.text?.trim();
                if (!text) return;
                if (text === interimTranscript) return; // текст не изменился
                if (!transcriptSeparator && finalTranscript) transcriptSeparator = " ";
                interimTranscript = text;
                noteTranscriptChange();

                // Настоящее перебивание: агент звучит, а от клиента уже пошёл текст.
                // Это надёжнее таймера дебаунса — есть подтверждённая речь.
                if (userSpeaking && agentIsSpeaking()) fireBargeIn("interim");
            });

            // ── ASR: финальные результаты (+ реконсиляция) ─────────────────────
            stt.addEventListener(ASREvents.Result, (event) => {
                const text = event?.text?.trim();
                if (!text) return;
                const norm = normalize(text);

                // РЕКОНСИЛЯЦИЯ. Ход уже закрыт по interim, а полный текст пришёл
                // только сейчас (в проде — в среднем +4.6с). Отдаём его сценарию как
                // исправление вместо того, чтобы открыть новый ход (клиент задавал
                // один вопрос — он не должен получить два ответа).
                if (submitted && Date.now() - submitted.at <= policy.reconcileWindowMs) {
                    if (norm === submitted.norm) return; // дубль отправленного

                    // Путь 1 (v2.1, основной): VAD не фиксировал новой речи после
                    // сабмита -> финал физически относится к тому же куску аудио.
                    // Ловит правки В СЕРЕДИНЕ фразы, которые префиксная проверка
                    // пропускала («может ___ в моей» -> «может быть в моей»).
                    const sameSegment = submitted.seg === segmentId && !userSpeaking;

                    // Путь 2 (из v2): сегмент уже сменился, но текст дословно
                    // продолжает отправленный. Сверка по границе слова — иначе «да»
                    // ложно совпадёт с «дальше» и мы починим ход чужим текстом.
                    const extendsSent =
                        submitted.norm &&
                        norm.length > submitted.norm.length &&
                        norm.indexOf(submitted.norm) === 0 &&
                        norm.charAt(submitted.norm.length) === " ";

                    if (sameSegment || extendsSent) {
                        const lagMs = Date.now() - submitted.at;
                        const gained = text.length - submitted.raw.length;
                        stats.corrections += 1;
                        stats.correctionCharsGained += Math.max(0, gained);
                        log(
                            `===TURN_TRUNCATED=== lag=${lagMs}ms ` +
                            `via=${sameSegment ? "same_segment" : "prefix"} ` +
                            `sent="${submitted.raw}" full="${text}"`
                        );
                        const sentText = submitted.raw;
                        const correctedTurnId = submitted.turnId;
                        submitted = {
                            turnId: correctedTurnId,
                            norm,
                            raw: text,
                            at: submitted.at,
                            seg: submitted.seg,
                        };
                        if (onTurnCorrection) {
                            onTurnCorrection(text, correctedTurnId, {
                                sentText,
                                lagMs,
                                reason: sameSegment ? "same_segment" : "prefix",
                            });
                        }
                        return;
                    }
                }

                // Финал пришёл раньше, чем VAD доложил о начале речи (Silero
                // отстаёт ~1с). Не теряем его — открываем ход этим текстом.
                let asrLedTurn = false;
                if (!acceptingTranscript) {
                    acceptingTranscript = true;
                    asrLedTurn = true;
                    finalTranscript = "";
                    interimTranscript = "";
                    transcriptSeparator = "";
                    log(`===ASR_LED_TURN=== финал раньше VAD :: ${text}`);
                }

                const confidence = normalizeConfidence(event?.confidence);
                const hadCommittedPrefix = !!finalTranscript;

                if (replaceableShortFinal) {
                    finalTranscript = text;
                } else {
                    if (finalTranscript) finalTranscript += transcriptSeparator || " ";
                    finalTranscript += text;
                }

                interimTranscript = "";
                transcriptSeparator = " ";
                noteTranscriptChange();
                lastFinalConfidence = confidence === null ? 1 : confidence;
                replaceableShortFinal =
                    isShortUtterance(text) &&
                    (
                        hadCommittedPrefix ||
                        lastFinalConfidence < policy.lowConfidenceShortUtteranceThreshold ||
                        startsWithContinuationToken(text)
                    );
                shortExtensionApplied = false;

                log(`===STT Final: ${event.text}`);

                if (
                    isShortUtterance(text) &&
                    !replaceableShortFinal &&
                    !smartTurnComplete &&
                    !userSpeaking
                ) {
                    startHardTimeout(
                        Math.min(policy.userSpeechTimeoutMs, policy.fastShortUtteranceTimeoutMs)
                    );
                } else if (asrLedTurn) {
                    // Ход открыт финалом ASR, а не VAD — значит speechEnd по нему не
                    // придёт и взводить закрытие некому. Без своего таймера такой ход
                    // повиснет навсегда (речь уже распознана, но в LLM не уходит).
                    startHardTimeout();
                }
                if (smartTurnComplete) submitCurrentTurn("TURN_DETECT: FINAL_TRANSCRIPT");
            });

            // ── Barge-in: только настоящее перебивание ─────────────────────────
            const agentIsSpeaking = () => {
                if (typeof isAgentSpeaking !== "function") return true;
                try {
                    return !!isAgentSpeaking();
                } catch (e) {
                    return true;
                }
            };

            let bargeInFiredForSegment = -1;
            const fireBargeIn = (why) => {
                if (bargeInFiredForSegment === segmentId) return;
                bargeInFiredForSegment = segmentId;
                if (bargeInTimer) { clearTimeout(bargeInTimer); bargeInTimer = null; }
                stats.bargeIns += 1;
                log(`===BARGE-IN=== (${why})`);
                if (onInterrupt) onInterrupt();
            };

            // Сторож залипшего VAD. Аварийная проверка внутри submitCurrentTurn
            // недостижима сама по себе: если speechEnd не пришёл, закрывать ход
            // просто некому — все таймеры взводятся именно на speechEnd. Поэтому
            // сторож взводится на speechStart и работает независимо.
            const armStuckWatchdog = () => {
                if (stuckTimer) clearTimeout(stuckTimer);
                const seg = segmentId;
                stuckTimer = setTimeout(() => {
                    stuckTimer = null;
                    if (seg !== segmentId || !userSpeaking) return;
                    if (!buildInput()) return;
                    log(`===VAD_WATCHDOG=== speech активен ${policy.userSpeakingMaxHoldMs}ms без speechEnd`);
                    submitCurrentTurn("FORCED_CLOSE");
                }, policy.userSpeakingMaxHoldMs);
            };

            vad.addEventListener(Silero.VADEvents.Result, (event) => {
                // Именно «поле присутствует», а не «истинно»: speechStartAt === 0
                // (начало сессии) — валидное значение и не должно теряться.
                const hasStart = event.speechStartAt !== undefined && event.speechStartAt !== null;
                const hasEnd = event.speechEndAt !== undefined && event.speechEndAt !== null;

                if (hasStart) {
                    segmentId += 1;
                    stats.segments += 1;
                    userSpeaking = true;
                    if (!userSpeakingSince) userSpeakingSince = Date.now();
                    clearTimers();
                    smartTurnComplete = false;
                    acceptingTranscript = true;
                    allowAgentAudio = false;
                    stabilityWaitStartedAt = 0;
                    armStuckWatchdog();
                    if (finalTranscript || interimTranscript) transcriptSeparator = " ... ";

                    // Раньше onInterrupt звался на КАЖДЫЙ speechStart — то есть был
                    // счётчиком сегментов VAD, а не перебиваний (184 за 22 минуты).
                    // Теперь: агент должен реально звучать, а речь — продержаться.
                    if (!agentIsSpeaking()) {
                        stats.bargeInsSuppressed += 1;
                    } else if (policy.bargeInMinSpeechMs > 0) {
                        if (bargeInTimer) clearTimeout(bargeInTimer);
                        const seg = segmentId;
                        bargeInTimer = setTimeout(() => {
                            bargeInTimer = null;
                            if (seg !== segmentId) return;
                            if (!userSpeaking) { stats.bargeInsSuppressed += 1; return; }
                            if (!agentIsSpeaking()) { stats.bargeInsSuppressed += 1; return; }
                            fireBargeIn("vad");
                        }, policy.bargeInMinSpeechMs);
                    } else {
                        fireBargeIn("vad");
                    }
                }

                if (hasEnd) {
                    userSpeaking = false;
                    userSpeakingSince = 0;
                    if (stuckTimer) { clearTimeout(stuckTimer); stuckTimer = null; }
                    if (!turnHoldStartedAt) turnHoldStartedAt = Date.now();
                    startHardTimeout();
                    // Штампуем предсказание текущим сегментом: результат, пришедший
                    // уже после начала следующего сегмента, будет отброшен.
                    predictSegment = segmentId;
                    turnDetector.predict();
                }
            });

            // ── Turn detector ─────────────────────────────────────────────────
            turnDetector.addEventListener(Pipecat.TurnEvents.Result, (event) => {
                const probability = event?.probability;
                log(
                    `===Pipecat.TurnEvents.Result=== p=${JSON.stringify(probability)} ` +
                    `eot=${!!event.endOfTurn} seg=${segmentId}/${predictSegment}`
                );

                // Ранний сигнал для спекуляции — не решение о закрытии, гейтим мягко.
                if (
                    !event.endOfTurn &&
                    typeof probability === "number" &&
                    probability >= policy.speculativeEouProbability &&
                    speculativeFiredForSegment !== segmentId
                ) {
                    const speculativeInput = buildInput();
                    if (speculativeInput) {
                        speculativeFiredForSegment = segmentId;
                        log(`===SPECULATIVE_READY=== p=${probability} :: ${speculativeInput}`);
                        if (onSpeculativeTurn) onSpeculativeTurn(speculativeInput, turnId);
                    }
                }

                // ЛЕЧЕНИЕ ПРИЧИНЫ №2. Вердикт относится к сегменту, который уже
                // закончился и сменился новым — клиент продолжил говорить, пока
                // детектор думал. Применять такой вердикт к текущей речи нельзя.
                if (predictSegment !== -1 && predictSegment !== segmentId) {
                    stats.stalePredictions += 1;
                    log(`===STALE_PREDICTION=== вердикт для сегмента ${predictSegment}, сейчас ${segmentId}`);
                    return;
                }

                // ЛЕЧЕНИЕ ПРИЧИНЫ №3. Детектор говорит «фраза не закончена» —
                // раньше это игнорировалось и фолбэк всё равно закрывал ход через
                // 700мс. Теперь вердикт отодвигает закрытие.
                if (!event.endOfTurn) {
                    if (typeof probability !== "number") return;
                    if (probability >= policy.vetoSoftProbability) return; // серая зона
                    const heldFor = turnHoldStartedAt ? Date.now() - turnHoldStartedAt : 0;
                    if (vetoHolds >= policy.maxVetoHolds || heldFor >= policy.maxTurnHoldMs) {
                        log(`===VETO_EXHAUSTED=== holds=${vetoHolds} held=${heldFor}ms`);
                        return;
                    }
                    vetoHolds += 1;
                    stats.holdsVeto += 1;
                    const hold =
                        probability < policy.vetoStrongProbability
                            ? policy.vetoHoldStrongMs
                            : policy.vetoHoldSoftMs;
                    log(`===VETO_NOT_EOT=== p=${probability} -> ждём ещё ${hold}ms (#${vetoHolds})`);
                    startHardTimeout(hold);
                    return;
                }

                smartTurnComplete = true;

                // Двухскоростной выбор окна ожидания.
                const confident =
                    (typeof probability === "number" &&
                        probability >= policy.confidentEouProbability) ||
                    isCompleteShortAnswer(buildInput());
                const settleMs = confident
                    ? policy.transcriptSettleFastMs
                    : policy.transcriptSettleMs;

                if (settleTimer) clearTimeout(settleTimer);

                if (finalTranscript && !interimTranscript && confident) {
                    submitCurrentTurn("TURN_DETECT: END_OF_TURN");
                    return;
                }

                const seg = segmentId;
                const reason = finalTranscript
                    ? "TURN_DETECT: END_OF_TURN_SETTLED"
                    : "TURN_DETECT: ASR_GRACE";
                settleTimer = setTimeout(() => {
                    if (seg !== segmentId) return;
                    submitCurrentTurn(reason);
                }, settleMs);
            });

            return {
                vad,
                turnDetector,
                canPlayAgentAudio() {
                    return allowAgentAudio;
                },
                currentVersion() {
                    return turnId;
                },
                isUserSpeaking() {
                    return userSpeaking;
                },
                stats() {
                    return stats;
                },
                statsSummary() {
                    const closes = Object.keys(stats.closes)
                        .map((k) => `${k}=${stats.closes[k]}`)
                        .join(" ");
                    return (
                        `segments=${stats.segments} | closes: ${closes || "none"} | ` +
                        `holds: speaking=${stats.holdsUserSpeaking} unstable=${stats.holdsUnstable} ` +
                        `veto=${stats.holdsVeto} | stale_predictions=${stats.stalePredictions} | ` +
                        `barge-in: real=${stats.bargeIns} suppressed=${stats.bargeInsSuppressed} | ` +
                        `truncations=${stats.corrections} (+${stats.correctionCharsGained} chars) | ` +
                        `forced=${stats.forcedCloses}`
                    );
                },
                close() {
                    clearTimers();
                    if (bargeInTimer) clearTimeout(bargeInTimer);
                    if (stuckTimer) clearTimeout(stuckTimer);
                    bargeInTimer = null;
                    stuckTimer = null;
                    vad?.close();
                    turnDetector?.close();
                },
            };
        },
    };
}

const BACKEND_URL = "https://voicyfy.ru";
const LOG_URL = BACKEND_URL + "/api/voximplant/log";
const FUNCTIONS_URL = BACKEND_URL + "/api/voximplant/functions/execute";
const LLM_MODEL = "gpt-realtime-2.1-mini";
// У 2.1-mini есть reasoning-токены (тарифицируются как output). "low" —
// компромисс качество/задержка, проверенный в cartesia_inbound.
const LLM_REASONING_EFFORT = "low";
const SILENCE_HARD_TIMEOUT_MS = 180000;
// Сколько ждём SessionCreated после подключения WS.
const SESSION_READY_TIMEOUT_MS = 6000;
// Сколько раз пробуем поднять WS заново, если он оборвался посреди звонка.
const MAX_RECONNECTS = 2;

// Оценка длительности синтезированной речи: используется, чтобы понимать,
// звучит ли агент прямо сейчас (для фильтрации ложных перебиваний).
const MS_PER_CHAR = 95;

// Пресеты паузы перед ответом (настройка агента, поле silence_duration_ms).
// Значение = сколько тишины ждёт Silero, прежде чем счесть, что человек
// замолчал. Это НЕ таймер ответа: вето детектора, стабильность транскрипта и
// инвариант «клиент говорит» всё равно могут отложить закрытие хода.
// 300 — быстрый ответ, риск вступить в паузу посреди фразы;
// 650 — сбалансированный; 1000 — терпеливый.
const SILENCE_MS_DEFAULT = 300;
const SILENCE_MS_MIN = 200;
const SILENCE_MS_MAX = 1500;
// Страховочный таймаут держим на фиксированный запас выше тишины: иначе он
// либо срабатывает раньше VAD, либо висит без нужды.
const SILENCE_TO_TIMEOUT_MS = 250;

function resolveSilenceMs(raw) {
    const v = Number(raw);
    if (!Number.isFinite(v) || v <= 0) return SILENCE_MS_DEFAULT;
    return Math.min(SILENCE_MS_MAX, Math.max(SILENCE_MS_MIN, Math.round(v)));
}

const RT = OpenAI.RealtimeAPIEvents;

// --- Извлечение данных из событий Realtime (провайдерский payload в data) ---
function rtPayload(event) {
    return event?.data?.payload ?? event?.data ?? {};
}
// Дельта текста (response.output_text.delta).
function rtDelta(event) {
    const d = event?.data;
    if (typeof d?.delta === "string" && d.delta) return d.delta;
    const p = d?.payload;
    if (typeof p?.delta === "string" && p.delta) return p.delta;
    return "";
}
// Полный текст ответа (response.output_text.done).
function rtDoneText(event) {
    const d = event?.data;
    if (typeof d?.text === "string" && d.text) return d.text;
    const p = d?.payload;
    if (typeof p?.text === "string" && p.text) return p.text;
    return "";
}
// id генерации: в дельтах лежит плоско (response_id), в response.* — вложенно.
function rtResponseId(event) {
    const p = rtPayload(event);
    return p?.response_id ?? p?.response?.id ?? "";
}
// usage приходит в response.done.
function rtUsage(event) {
    const p = rtPayload(event);
    return p?.response?.usage ?? p?.usage ?? null;
}
// Модель иногда пишет ответ в несколько строк — "  \n" ломает потоковый синтез.
function cleanForTTS(text) {
    return (text || "").replace(/\s*\n+\s*/g, " ");
}

const TELEPHONY_STYLE_RULES = `

Правила голосового ответа (телефония):
- Отвечай коротко, обычно 1-2 предложения, без списков, markdown и эмодзи.
- Числа, даты и время произноси словами.
- Если реплика оборвана или неясна — вежливо переспроси одним вопросом.`;

function asrProfileForLang(lang) {
    const map = {
        ru: ASRProfileList.Yandex.ru_RU,
        en: ASRProfileList.Yandex.en_US,
        de: ASRProfileList.Yandex.de_DE,
        es: ASRProfileList.Yandex.es_ES,
        fr: ASRProfileList.Yandex.fr_FR,
    };
    return map[(lang || "ru").toLowerCase()] || ASRProfileList.Yandex.ru_RU;
}
function asrModelForLang() {
    return ASRModelList.Yandex.general;
}
function voxttsVoice(voiceId) {
    return VoxTTS.VoiceList[voiceId] || VoxTTS.VoiceList.Anna;
}

async function fetchOutboundConfig(assistantId) {
    const url =
        `${BACKEND_URL}/api/telephony/outbound-config` +
        `?assistant_id=${encodeURIComponent(assistantId)}&assistant_type=cascade`;
    const response = await Net.httpRequestAsync(url, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
    });
    if (response.code !== 200 || !response.text) {
        Logger.write(`[OUT-CASCADE] Config HTTP error: ${response.code}`);
        return null;
    }
    try {
        const config = JSON.parse(response.text);
        return config && config.success ? config : null;
    } catch (e) {
        Logger.write(`[OUT-CASCADE] Config parse error: ${e}`);
        return null;
    }
}

let call_session_history_id = null;

VoxEngine.addEventListener(AppEvents.Started, async (e) => {
    call_session_history_id = e.sessionId;
    Logger.write(`[OUT-CASCADE] Session History ID: ${call_session_history_id}`);

    // --- Параметры звонка из customData ---
    let callData;
    try {
        callData = JSON.parse(VoxEngine.customData());
    } catch (err) {
        Logger.write(`[OUT-CASCADE] Bad customData: ${err}`);
        VoxEngine.terminate();
        return;
    }

    const PHONE_NUMBER = callData.phone_number;
    const ASSISTANT_ID = callData.assistant_id;
    const CALLER_ID = callData.caller_id || "";
    if (!PHONE_NUMBER || !ASSISTANT_ID) {
        Logger.write("[OUT-CASCADE] Missing phone_number/assistant_id");
        VoxEngine.terminate();
        return;
    }
    const MUTE_DURATION_MS = callData.mute_duration_ms || 3000;
    const CONTACT_NAME = callData.contact_name || "";
    const TASK_TITLE = callData.task_title || "";
    const TASK_DESCRIPTION = callData.task_description || "";
    const CUSTOM_GREETING = callData.custom_greeting || "";
    const API_TASK = callData.task || "";
    const FIRST_PHRASE_OVERRIDE = callData.first_phrase || "";

    const chat_id = "vox_" + Math.random().toString(36).substring(2, 15);
    const caller_number = "OUTBOUND: " + PHONE_NUMBER;

    let callType = "API";
    const isCrmCall = !!(CONTACT_NAME || TASK_TITLE || TASK_DESCRIPTION);
    if (isCrmCall && API_TASK) callType = "CRM+Task";
    else if (isCrmCall) callType = "CRM";
    else if (API_TASK) callType = "API+Task";

    // --- Состояние LLM/каскада ---
    let call;
    let stt;
    let realtime; // OpenAI.RealtimeAPIClient — текстовый мозг каскада
    let ttsPlayer;
    let turnTaking;
    let greeting = "";
    let systemPrompt = "";
    let apiKey = "";
    let reconnectAttempts = 0;

    // Локальная копия диалога. В запросы НЕ уходит (контекст живёт на стороне
    // OpenAI), нужна только для восстановления после обрыва WS.
    const messages = []; // [0]=system, далее user/assistant

    // Состояние текущей генерации. Одна активная за раз.
    const main = { active: false, responseId: null, text: "", version: -1 };

    let realtimeTools = [];
    const functionNameToIdMap = {};

    let micOpen = false; // мик абонента открывается после mute-окна
    let terminating = false;
    let firstDeltaLoggedForTurn = false;
    let turnStartedAt = 0;
    let silenceMs = SILENCE_MS_DEFAULT;
    const loggedShapes = new Set();

    // Звучит ли агент прямо сейчас — нужно VoxTurnTaking, чтобы отличать
    // настоящее перебивание от любого сегмента VAD. Считаем по «озвученному
    // запасу»: каждый отправленный в VoxTTS кусок продлевает ожидаемый конец
    // речи, так флаг не зависит от имён событий плеера и не залипает.
    let agentSpeakingUntil = 0;
    const noteAgentAudio = (text) => {
        const ms = Math.max(300, (text || "").length * MS_PER_CHAR);
        agentSpeakingUntil = Math.max(agentSpeakingUntil, Date.now()) + ms;
    };
    const stopAgentAudio = () => { agentSpeakingUntil = 0; };
    const isAgentSpeaking = () => Date.now() < agentSpeakingUntil;

    // --- Данные для /log ---
    let record_url = null;
    // Стоимость звонка по позициям, которые вообще отдают cost в события сценария.
    // Это НЕ полный счёт: TTS, WebSocket-потоки (VAD + turn-detector) и детекция
    // конца фразы тарифицируются Voximplant, но событий с cost не присылают —
    // их видно только в GetCallHistory (other_resource_usage). Поэтому cost
    // отсюда идёт в бэкенд лишь как fallback, а истина — GetCallHistory.
    let telephony_cost = 0; // Call.Disconnected / Call.Failed
    let asr_cost = 0;       // ASR.Stopped
    let record_cost = 0;    // Call.RecordStopped
    let call_duration = 0;
    const knownCost = () =>
        Math.round((telephony_cost + asr_cost + record_cost) * 1e6) / 1e6;
    // Учёт токенов LLM для списания кредитов каскада. Cached считаем отдельно:
    // ставка за них на порядок ниже, и именно в них уходит история диалога.
    let totalPromptTokens = 0;
    let totalCachedPromptTokens = 0;
    let totalCompletionTokens = 0;
    let usageEventsSeen = 0;
    const dialogLog = [];
    let userMessageBuffer = "";
    let assistantMessageBuffer = "";

    // --- Silence hard-timeout ---
    let hardTimer = null;
    const resetHardTimeout = () => {
        if (hardTimer) clearTimeout(hardTimer);
        hardTimer = setTimeout(() => {
            Logger.write(`[OUT-CASCADE] ===SILENCE_TIMEOUT=== ${SILENCE_HARD_TIMEOUT_MS / 1000}s`);
            terminateCall();
        }, SILENCE_HARD_TIMEOUT_MS);
    };

    const logShapeOnce = (tag, event) => {
        if (loggedShapes.has(tag)) return;
        loggedShapes.add(tag);
        try {
            Logger.write(`[OUT-CASCADE] ===SHAPE ${tag}=== ${JSON.stringify(event?.data)}`);
        } catch (e) {
            Logger.write(`[OUT-CASCADE] SHAPE ${tag} stringify error`);
        }
    };
    const logFirstDelta = (tag) => {
        if (firstDeltaLoggedForTurn) return;
        firstDeltaLoggedForTurn = true;
        const dt = turnStartedAt ? Date.now() - turnStartedAt : -1;
        Logger.write(`[OUT-CASCADE] ===FIRST_DELTA=== +${dt}ms${tag ? " " + tag : ""}`);
    };

    const logDialog = (role, text) => {
        if (!text) return;
        dialogLog.push({ role, text, ts: Date.now() });
        if (role === "user") {
            userMessageBuffer += (userMessageBuffer ? "\n" : "") + text;
        } else {
            assistantMessageBuffer += (assistantMessageBuffer ? "\n" : "") + text;
        }
    };

    const canPlay = () => turnTaking && turnTaking.canPlayAgentAudio();
    const ttsSendText = (text) => {
        if (!text) return;
        noteAgentAudio(text);
        ttsPlayer.send({ send_text: { text: cleanForTTS(text) } });
    };
    const ttsFlush = () => {
        ttsPlayer.send({ send_text: { text: " ", flush_context: {} } });
    };

    // --- Учёт токенов из response.done ---
    const accountUsage = (usage) => {
        usageEventsSeen++;
        const input = Number(usage.input_tokens) || 0;
        const output = Number(usage.output_tokens) || 0;
        const cachedRaw = Number(usage?.input_token_details?.cached_tokens) || 0;
        const cached = Math.min(Math.max(0, cachedRaw), input);
        totalCachedPromptTokens += cached;
        totalPromptTokens += input - cached;
        totalCompletionTokens += output;
    };

    // --- Гейтинг: событие относится к текущей активной генерации? ---
    const isCurrent = (event) => {
        if (!main.active) return false;
        const id = rtResponseId(event);
        if (!id) return true;
        if (!main.responseId) {
            main.responseId = id;
            return true;
        }
        return id === main.responseId;
    };

    // --- Отдать реплику абонента модели и запросить ответ ---
    const sendUserText = (text) => {
        realtime.conversationItemCreate({
            item: {
                type: "message",
                role: "user",
                content: [{ type: "input_text", text: text }],
            },
        });
    };
    const requestResponse = (version) => {
        main.active = true;
        main.responseId = null;
        main.text = "";
        main.version = version;
        realtime.responseCreate({});
        resetHardTimeout();
    };

    const finalizeTurn = (text) => {
        const clean = (text || "").trim();
        if (clean) {
            messages.push({ role: "assistant", content: clean });
            Logger.write(`[OUT-CASCADE] ===AGENT=== ${clean}`);
            logDialog("assistant", clean);
        }
        if (canPlay()) ttsFlush();
        main.active = false;
        main.responseId = null;
        main.text = "";
        resetHardTimeout();
    };

    const executeFunction = async (name, args) => {
        const function_id = args.function_id || functionNameToIdMap[name];
        const clean = Object.assign({}, args);
        delete clean.function_id;
        if (!function_id) return `Error: function_id not found for ${name}`;
        try {
            const resp = await Net.httpRequestAsync(FUNCTIONS_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                postData: JSON.stringify({
                    function_id: function_id,
                    arguments: Object.assign({}, clean, { assistant_id: ASSISTANT_ID }),
                    call_data: {
                        call_id: call ? call.id() : "unknown",
                        chat_id: chat_id,
                        assistant_id: ASSISTANT_ID,
                        // Чистые номера для функций (send_sms и т.п.): caller_number —
                        // клиент (кому звоним), called_number — наш номер (caller_id).
                        caller_number: PHONE_NUMBER,
                        called_number: CALLER_ID,
                    },
                }),
            });
            if (resp.code === 200) return resp.text || "{}";
            return `Error: HTTP ${resp.code}`;
        } catch (e) {
            return `Error: ${e}`;
        }
    };

    // Вызов функции приходит целиком в ResponseOutputItemDone (в отличие от
    // Chat Completions, где tool_calls собирались по дельтам).
    const handleFunctionCall = async (item) => {
        const name = item.name;
        const callIdRef = item.call_id;
        let args = {};
        try { args = JSON.parse(item.arguments || "{}"); } catch (e) { /* noop */ }
        Logger.write(`[OUT-CASCADE] ===FUNCTION=== ${name} ${item.arguments || ""}`);

        // Текущая генерация закончилась вызовом функции — снимаем гейт, чтобы
        // хвостовые события старого response не влияли на следующий.
        const pendingText = main.text;
        main.active = false;
        main.responseId = null;
        main.text = "";
        if (pendingText.trim()) {
            messages.push({ role: "assistant", content: pendingText.trim() });
            logDialog("assistant", pendingText.trim());
        }

        // hangup_call — ассистент сам завершает звонок.
        if (name === "hangup_call") {
            const farewell = args.farewell_message || "";
            Logger.write(`[OUT-CASCADE] ===HANGUP=== ${farewell}`);
            if (farewell) {
                logDialog("assistant", farewell);
                if (canPlay()) { ttsSendText(farewell); ttsFlush(); }
            }
            setTimeout(() => terminateCall(), farewell ? 3500 : 300);
            return;
        }

        const result = await executeFunction(name, args);
        if (terminating) return;

        realtime.conversationItemCreate({
            item: {
                type: "function_call_output",
                call_id: callIdRef,
                output: String(result),
            },
        });
        // Модель озвучит ответ по результату функции.
        requestResponse(main.version);
    };

    const sendConversationLog = async () => {
        if (!userMessageBuffer && !assistantMessageBuffer && dialogLog.length === 0) return;
        const payload = {
            assistant_id: ASSISTANT_ID,
            chat_id: chat_id,
            call_id: call ? call.id() : "unknown",
            caller_number: caller_number,
            type: "conversation",
            call_type: callType,
            // Fallback-стоимость: только те позиции, что видны сценарию.
            // Бэкенд предпочитает полный счёт из GetCallHistory.
            call_cost: knownCost(),
            call_cost_parts: {
                telephony: telephony_cost,
                asr: asr_cost,
                record: record_cost,
            },
            call_duration: call_duration,
            cascade_usage: {
                prompt_tokens: totalPromptTokens,
                cached_prompt_tokens: totalCachedPromptTokens,
                completion_tokens: totalCompletionTokens,
                model: LLM_MODEL,
            },
            context: {
                contact_name: CONTACT_NAME,
                task_title: TASK_TITLE,
                task_description: TASK_DESCRIPTION,
                api_task: API_TASK,
            },
            data: {
                user_message: userMessageBuffer,
                assistant_message: assistantMessageBuffer,
                dialog: dialogLog,
            },
        };
        if (record_url) payload.record_url = record_url;
        if (call_session_history_id) payload.call_session_history_id = String(call_session_history_id);
        try {
            const resp = await Net.httpRequestAsync(LOG_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                postData: JSON.stringify(payload),
            });
            Logger.write(`[OUT-CASCADE] /log HTTP ${resp.code} (turns=${dialogLog.length})`);
        } catch (e) {
            Logger.write(`[OUT-CASCADE] /log error: ${e}`);
        }
    };

    let terminatePromise = null;
    const terminateCall = () => {
        if (terminatePromise) return terminatePromise;
        terminating = true;
        terminatePromise = (async () => {
            if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
            try { stt?.stop(); } catch (e) { /* noop */ }
            try { turnTaking?.close(); } catch (e) { /* noop */ }
            try { realtime?.close(); } catch (e) { /* noop */ }

            if (main.active && main.text) logDialog("assistant", main.text);

            await new Promise((r) => setTimeout(r, 400));

            Logger.write(
                `[OUT-CASCADE] ===BILLING=== known_cost=${knownCost()} ` +
                `(telephony=${telephony_cost} asr=${asr_cost} record=${record_cost}) ` +
                `dur=${call_duration}s ` +
                `turns=${dialogLog.length} rec=${record_url ? "yes" : "no"} ` +
                `session=${call_session_history_id || "none"} ` +
                `tokens(in=${totalPromptTokens} cached=${totalCachedPromptTokens} out=${totalCompletionTokens} events=${usageEventsSeen})`
            );
            // Явно: это НЕ итоговый счёт. TTS, WebSocket-потоки и turn detection
            // не отдают cost в сценарий — полная сумма только в GetCallHistory.
            Logger.write(
                `[OUT-CASCADE] ===BILLING_NOTE=== known_cost не включает TTS/WebSocket/turn-detection; ` +
                `итог смотреть в GetCallHistory(other_resource_usage)`
            );
            // Эффект настроек turn-taking должен быть измерим на проде, а не «на слух».
            try {
                if (turnTaking && turnTaking.statsSummary) {
                    Logger.write(`[OUT-CASCADE] ===TURN_STATS=== silence=${silenceMs}ms | ${turnTaking.statsSummary()}`);
                }
            } catch (e) { /* noop */ }
            await sendConversationLog();

            try { call?.hangup(); } catch (e) { /* noop */ }
            setTimeout(() => VoxEngine.terminate(), 400);
        })();
        return terminatePromise;
    };

    function determineGreeting(configFirstPhrase) {
        if (CUSTOM_GREETING) return CUSTOM_GREETING;
        if (FIRST_PHRASE_OVERRIDE) return FIRST_PHRASE_OVERRIDE;
        if (configFirstPhrase) return configFirstPhrase;
        return "Здравствуйте!";
    }

    function buildContextBlock() {
        let block = "";
        if (API_TASK) {
            block +=
                "══════════════════════════════════════\n" +
                "ЗАДАЧА НА ЭТОТ ЗВОНОК\n" +
                "══════════════════════════════════════\n" +
                API_TASK + "\n" +
                "Выполни эту задачу — это главная цель звонка.\n" +
                "══════════════════════════════════════\n\n";
        }
        if (CONTACT_NAME || TASK_TITLE || TASK_DESCRIPTION) {
            block +=
                "══════════════════════════════════════\n" +
                "КОНТЕКСТ ЗВОНКА (CRM)\n" +
                "══════════════════════════════════════\n";
            if (CONTACT_NAME) block += `Клиент: ${CONTACT_NAME} (обращайся по имени).\n`;
            if (TASK_TITLE) block += `Задача: ${TASK_TITLE}\n`;
            if (TASK_DESCRIPTION) block += `Подробности: ${TASK_DESCRIPTION}\n`;
            block += "══════════════════════════════════════\n\n";
        }
        return block;
    }

    // --- Конфигурация Realtime-сессии ---
    // withHistory=true только при реконнекте: контекст на стороне OpenAI умер
    // вместе с сессией, отдаём его стенограммой прямо в instructions. Это
    // надёжнее, чем пересоздавать items (формат assistant-item зависит от версии API).
    const buildSessionConfig = (withHistory) => {
        let instructions = systemPrompt;
        if (withHistory && messages.length > 1) {
            const transcript = messages
                .filter((m) => m.role !== "system" && m.content)
                .map((m) => (m.role === "user" ? "Собеседник: " : "Ты: ") + m.content)
                .join("\n");
            if (transcript) {
                instructions +=
                    `\n\nСтенограмма уже состоявшейся части разговора ` +
                    `(соединение обрывалось, продолжай с этого места):\n${transcript}`;
            }
        }
        return {
            type: "realtime",
            output_modalities: ["text"], // голос синтезирует VoxTTS, не модель
            instructions: instructions,
            reasoning: { effort: LLM_REASONING_EFFORT },
            tools: realtimeTools,
            tool_choice: realtimeTools.length > 0 ? "auto" : "none",
            // Ход определяем МЫ (VoxTurnTaking), серверный VAD не нужен.
            // Аудио в клиент не заводится вовсе — это вторая линия защиты.
            audio: { input: { turn_detection: null } },
        };
    };

    // --- Обработчики стрима Realtime (единственный клиент; текст + функции) ---
    const attachRealtimeHandlers = (client) => {
        client.addEventListener(RT.ResponseCreated, (event) => {
            if (terminating) return;
            if (main.active && !main.responseId) {
                main.responseId = rtResponseId(event) || "";
            }
        });

        client.addEventListener(RT.ResponseOutputTextDelta, (event) => {
            if (terminating) return;
            logShapeOnce("ResponseOutputTextDelta", event);
            if (!isCurrent(event)) return;
            const delta = rtDelta(event);
            if (!delta) return;
            main.text += delta;
            if (canPlay()) {
                logFirstDelta("");
                ttsSendText(delta);
            }
        });

        client.addEventListener(RT.ResponseOutputTextDone, (event) => {
            if (terminating) return;
            if (!isCurrent(event)) return;
            finalizeTurn(rtDoneText(event) || main.text);
        });

        client.addEventListener(RT.ResponseOutputItemDone, async (event) => {
            if (terminating) return;
            try {
                const payload = rtPayload(event);
                const item = payload && payload.item;
                if (!item || item.type !== "function_call") return;
                logShapeOnce("ResponseOutputItemDone.function_call", event);
                await handleFunctionCall(item);
            } catch (e) {
                Logger.write(`[OUT-CASCADE] function handler error: ${e}`);
            }
        });

        // Финал генерации: токены для биллинга + страховка на случай, если
        // ResponseOutputTextDone не пришёл, а текст накоплен.
        client.addEventListener(RT.ResponseDone, (event) => {
            logShapeOnce("ResponseDone", event);
            const usage = rtUsage(event);
            if (usage) {
                accountUsage(usage);
            } else {
                Logger.write("[OUT-CASCADE] ⚠️ ResponseDone без usage — токены этого хода не учтены");
            }
            if (terminating) return;
            if (main.active && isCurrent(event) && main.text) {
                finalizeTurn(main.text);
            }
        });

        client.addEventListener(RT.Error, (event) => {
            Logger.write("[OUT-CASCADE] ===LLM_ERROR===");
            try { Logger.write(JSON.stringify(event?.data)); } catch (e) { /* noop */ }
        });
    };

    // Поднять WS и дождаться готовности сессии (SessionCreated -> sessionUpdate).
    const connectRealtime = async (withHistory) => {
        const client = await OpenAI.createRealtimeAPIClient({
            apiKey: apiKey,
            model: LLM_MODEL,
            type: OpenAI.RealtimeAPIClientType.REALTIME,
            onWebSocketClose: () => {
                Logger.write("[OUT-CASCADE] [OpenAI] WS closed");
                handleRealtimeClose();
            },
            onWebSocketError: (err) => {
                Logger.write(`[OUT-CASCADE] [OpenAI] WS error: ${JSON.stringify(err)}`);
            },
        });

        await new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                if (settled) return;
                settled = true;
                reject(new Error("SessionCreated timeout"));
            }, SESSION_READY_TIMEOUT_MS);

            client.addEventListener(RT.SessionCreated, () => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    client.sessionUpdate({ session: buildSessionConfig(withHistory) });
                    Logger.write("[OUT-CASCADE] [OpenAI] Session configured (text-only, turn_detection=null)");
                    resolve();
                } catch (e) {
                    reject(e);
                }
            });
        });

        attachRealtimeHandlers(client);
        return client;
    };

    // Обрыв WS посреди звонка: TTL сессии, сеть, рестарт на стороне OpenAI.
    // Поднимаем заново и отдаём историю стенограммой.
    const handleRealtimeClose = async () => {
        if (terminating) return;
        // До дозвона обрыв означает неготовность — звонок ещё не начался,
        // readiness-блок разберётся сам.
        if (!call) return;
        if (reconnectAttempts >= MAX_RECONNECTS) {
            Logger.write("[OUT-CASCADE] ===RT_RECONNECT_GIVEUP=== завершаем звонок");
            terminateCall();
            return;
        }
        reconnectAttempts++;
        Logger.write(`[OUT-CASCADE] ===RT_RECONNECT=== попытка ${reconnectAttempts}/${MAX_RECONNECTS}`);
        main.active = false;
        main.responseId = null;
        main.text = "";
        try {
            realtime = await connectRealtime(true);
            Logger.write("[OUT-CASCADE] ===RT_RECONNECT_OK===");
        } catch (e) {
            Logger.write(`[OUT-CASCADE] ===RT_RECONNECT_FAILED=== ${e}`);
            terminateCall();
        }
    };

    // --- Обработчики звонка (навешиваем на созданный call) ---
    const attachCallHandlers = () => {
        call.addEventListener(CallEvents.RecordStarted, (event) => {
            record_url = event.url;
            Logger.write(`[OUT-CASCADE] Recording started: ${event.url}`);
        });
        call.addEventListener(CallEvents.RecordStopped, (event) => {
            if (event && event.url) record_url = event.url;
            if (event && event.cost !== undefined) record_cost = Number(event.cost) || 0;
        });
        call.addEventListener(CallEvents.Failed, (event) => {
            if (event && event.cost !== undefined) telephony_cost = Number(event.cost) || 0;
            if (event && event.duration !== undefined) call_duration = event.duration;
            Logger.write(`[OUT-CASCADE] ===CALL_FAILED=== code=${event?.code} ${event?.reason || ""}`);
            terminateCall();
        });
        call.addEventListener(CallEvents.Disconnected, (event) => {
            if (event && event.cost !== undefined) telephony_cost = Number(event.cost) || 0;
            if (event && event.duration !== undefined) call_duration = event.duration;
            Logger.write(`[OUT-CASCADE] ===DISCONNECTED=== telephony=${telephony_cost} dur=${call_duration}s`);
            terminateCall();
        });

        call.addEventListener(CallEvents.Connected, async () => {
            Logger.write(`[OUT-CASCADE] ===CONNECTED=== ${caller_number}`);
            try {
                call.record({ stereo: false, lossless: false, hd_audio: true });
            } catch (e) {
                Logger.write(`[OUT-CASCADE] record() error: ${e}`);
            }

            ttsPlayer.sendMediaTo(call);

            turnTaking = await VoxTurnTaking.create({
                call,
                stt,
                // ВАЖНО: НЕ переопределяем vadOptions целиком и не трогаем
                // таймеры закрытия хода — дефолты v2 выверены на проде, и любое
                // их «улучшение» отсюда возвращает дробление фраз. Сценарий
                // управляет только паузой перед ответом (пресет из настроек агента).
                vadOptions: { minSilenceDurationMs: silenceMs },
                turnDetectorOptions: { threshold: 0.7 },
                policy: {
                    confidentEouProbability: 0.8,
                    userSpeechTimeoutMs: silenceMs + SILENCE_TO_TIMEOUT_MS,
                    // Языковые списки — то, что v2 намеренно оставляет пустыми
                    // и ждёт от сценария.
                    continuationTokens: [
                        "и", "а", "но", "ну", "вот", "так", "значит",
                        "короче", "эм", "ээ", "мм", "это",
                    ],
                    trailingContinuationTokens: [
                        "чтобы", "потому", "который", "которая", "которое", "которые",
                        "про", "либо", "если", "когда", "пока", "хотя",
                    ],
                    completeShortAnswers: [
                        "да", "нет", "ок", "окей", "хорошо", "ага", "угу", "не",
                        "стоп", "верно", "точно", "конечно", "давай", "давайте",
                        "спасибо", "понятно", "нет спасибо", "да давайте",
                    ],
                    speculativeEouProbability: 0.6,
                },
                enableLogging: true,

                // Без этого коллбэка VoxTurnTaking считает перебиванием ЛЮБОЙ
                // сегмент VAD — эхо, кашель, шум линии.
                isAgentSpeaking: isAgentSpeaking,

                onUserTurn: (input, version) => {
                    if (terminating || !micOpen) return;
                    firstDeltaLoggedForTurn = false;
                    turnStartedAt = Date.now();
                    Logger.write(`[OUT-CASCADE] ===USER=== ${input}`);
                    logDialog("user", input);
                    messages.push({ role: "user", content: input });
                    sendUserText(input);
                    requestResponse(version);
                },

                // Yandex досылает полный текст реплики через 2-5с после того,
                // как ход уже ушёл в модель по interim. Отвечать второй раз
                // нельзя — абонент сказал это один раз. Правим историю и молча
                // отдаём модели уточнение, БЕЗ responseCreate.
                onTurnCorrection: (fullText, correctedTurnId, meta) => {
                    if (terminating || !micOpen || !fullText) return;
                    const sent = (meta && meta.sentText) || "";
                    Logger.write(
                        `[OUT-CASCADE] ===USER_CORRECTED=== (+${meta && meta.lagMs}ms) ` +
                        `"${sent}" -> "${fullText}"`
                    );

                    for (let i = messages.length - 1; i >= 0; i--) {
                        if (messages[i].role === "user") {
                            messages[i].content = fullText;
                            break;
                        }
                    }
                    for (let i = dialogLog.length - 1; i >= 0; i--) {
                        if (dialogLog[i].role === "user") {
                            const old = dialogLog[i].text;
                            dialogLog[i].text = fullText;
                            if (userMessageBuffer.endsWith(old)) {
                                userMessageBuffer =
                                    userMessageBuffer.slice(0, userMessageBuffer.length - old.length) + fullText;
                            }
                            break;
                        }
                    }

                    try {
                        realtime.conversationItemCreate({
                            item: {
                                type: "message",
                                role: "user",
                                content: [{
                                    type: "input_text",
                                    text:
                                        "[Уточнение распознавания: предыдущая реплика собеседника " +
                                        `полностью звучала так: «${fullText}». Не отвечай на неё повторно, ` +
                                        "просто учитывай верный текст дальше.]",
                                }],
                            },
                        });
                    } catch (e) {
                        Logger.write(`[OUT-CASCADE] correction item error: ${e}`);
                    }
                },

                onInterrupt: () => {
                    if (terminating || !micOpen) return;
                    ttsPlayer?.clearBuffer();
                    stopAgentAudio();
                    if (main.active) {
                        // Останавливаем генерацию НА СЕРВЕРЕ — иначе модель
                        // досчитывает ответ, который никто не услышит, и мы за него платим.
                        try { realtime.responseCancel(); } catch (e) { /* noop */ }
                    }
                    main.active = false;
                    main.responseId = null;
                    main.text = "";
                },
            });

            // Приветствие (фиксированная фраза, мимо LLM).
            messages.push({ role: "assistant", content: greeting });
            logDialog("assistant", greeting);
            noteAgentAudio(greeting);
            ttsPlayer.send({ send_text: { text: cleanForTTS(greeting), flush_context: {} } });
            resetHardTimeout();

            // MUTE-окно: открываем мик абонента только после mute_duration_ms,
            // чтобы «Алло»/шум при поднятии трубки не оборвали приветствие.
            setTimeout(() => {
                if (terminating) return;
                call.sendMediaTo(stt);
                micOpen = true;
                Logger.write(`[OUT-CASCADE] MIC OPEN (after ${MUTE_DURATION_MS}ms mute)`);
            }, MUTE_DURATION_MS);
        });
    };

    try {
        Logger.write(`[OUT-CASCADE] Outbound -> ${PHONE_NUMBER} (caller_id=${CALLER_ID}) type=${callType}`);

        const config = await fetchOutboundConfig(ASSISTANT_ID);
        if (!config || !config.api_key) {
            Logger.write("[OUT-CASCADE] No outbound config / api_key — abort (no dial)");
            VoxEngine.terminate();
            return;
        }

        apiKey = config.api_key;
        greeting = determineGreeting(config.first_phrase);

        // system-промпт: контекст (задача + CRM) + база + стиль + анти-повтор приветствия.
        systemPrompt = buildContextBlock();
        systemPrompt += (config.system_prompt || "Ты — голосовой ассистент.") + TELEPHONY_STYLE_RULES;
        systemPrompt +=
            `\n\nТип звонка: ИСХОДЯЩИЙ (ты звонишь абоненту, номер ${PHONE_NUMBER}). ` +
            `Разговор уже начат: приветствие собеседнику уже произнесено. ` +
            `Не здоровайся и не представляйся заново — сразу переходи к цели звонка.`;
        // Информация о звонке в промпт: реальные номера и текущее время, чтобы
        // модель могла корректно вызывать функции (например, send_sms) и
        // ориентироваться во времени. Для исходящего caller_number — номер
        // клиента (кому звоним), called_number — наш номер (caller_id). МСК (UTC+3).
        const mskTime = new Date(Date.now() + 3 * 3600 * 1000).toISOString().replace("T", " ").slice(0, 16);
        systemPrompt +=
            `\n\nИнформация о звонке:\n` +
            `- Номер клиента (caller_number): ${PHONE_NUMBER}\n` +
            `- Наш номер (called_number): ${CALLER_ID}\n` +
            `- Текущее время: ${mskTime} (МСК)`;
        messages.push({ role: "system", content: systemPrompt });

        // Функции ассистента -> tools Realtime (ПЛОСКИЙ формат: name/description/
        // parameters на верхнем уровне, в отличие от вложенного у Chat Completions).
        if (config.functions && config.functions.length > 0) {
            const decls = [];
            for (let i = 0; i < config.functions.length; i++) {
                const t = config.functions[i];
                const fn = (t.type === "function" && t.function) ? t.function : (t.name ? t : null);
                if (!fn) continue;
                functionNameToIdMap[fn.name] = String(i + 1);
                let desc = fn.description;
                if (fn.name === "hangup_call") {
                    desc = "КРИТИЧЕСКИ ВАЖНО: вызови эту функцию НЕМЕДЛЕННО, когда задача звонка выполнена или собеседник хочет завершить разговор («пока», «до свидания», «всё, спасибо»). Не прощайся просто словами — вызови функцию.";
                }
                decls.push({
                    type: "function",
                    name: fn.name,
                    description: desc,
                    parameters: fn.parameters,
                });
            }
            realtimeTools = decls;
            Logger.write(`[OUT-CASCADE] Functions: ${JSON.stringify(functionNameToIdMap)}`);
        }

        // Пауза перед ответом: пресет из настроек агента (300/650/1000).
        silenceMs = resolveSilenceMs(config.silence_duration_ms);

        Logger.write(`[OUT-CASCADE] LLM: ${LLM_MODEL} via Realtime WS (text-only, наш turn-taking)`);
        Logger.write(
            `[OUT-CASCADE] Silence preset: ${silenceMs}ms ` +
            `(fallback timeout ${silenceMs + SILENCE_TO_TIMEOUT_MS}ms)`
        );

        // READINESS: готовим ASR / LLM / TTS ДО дозвона. WS к OpenAI поднимается
        // здесь же — если сессия не встала, PSTN не набираем вовсе (0₽ телефонии).
        stt = VoxEngine.createASR({
            profile: asrProfileForLang(config.asr_lang),
            model: asrModelForLang(config.asr_lang),
            interimResults: true,
        });
        // ASR тарифицируется отдельной строкой и в cost звонка НЕ входит.
        stt.addEventListener(ASREvents.Stopped, (event) => {
            if (event && event.cost !== undefined) asr_cost = Number(event.cost) || 0;
        });

        realtime = await connectRealtime(false);

        if (config.tts_provider && config.tts_provider !== "voxtts") {
            Logger.write(`[OUT-CASCADE] ⚠️ tts_provider='${config.tts_provider}' не поддержан, fallback VoxTTS/Anna`);
        }
        ttsPlayer = VoxTTS.createRealtimeTTSPlayer({
            createContextParameters: {
                create: {
                    modelId: VoxTTS.ModelList.VoxTTS,
                    voiceId: voxttsVoice(config.tts_voice),
                },
            },
        });

        // Прогрев больше не нужен: WS открыт и сессия сконфигурирована ещё до
        // дозвона, первый ход абонента и так уходит в тёплое соединение.
        Logger.write(`[OUT-CASCADE] Ready -> dialing PSTN ${PHONE_NUMBER}`);
        call = VoxEngine.callPSTN(PHONE_NUMBER, CALLER_ID);
        attachCallHandlers();
    } catch (error) {
        Logger.write("[OUT-CASCADE] ===UNHANDLED_ERROR===");
        Logger.write(String(error));
        if (call) terminateCall();
        else VoxEngine.terminate();
    }
});
