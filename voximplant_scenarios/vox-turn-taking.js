/**
 * Voximplant turn-taking runtime for sequenced scenarios (v2).
 *
 * Include this scenario BEFORE any scenario that wants to use VoxTurnTaking
 * in the same routing rule sequence.
 *
 * ВАЖНО ПРО ASR: рантайм рассчитан на потоковый ASR с interim
 * (ASREvents.InterimResult) — Yandex v2 (ASRProfileList.Yandex.ru_RU) с
 * interimResults: true.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ЧТО ИЗМЕНИЛОСЬ В v2 И ПОЧЕМУ
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Разбор продовых логов (20 звонков, 132 реплики) показал: ассистент перебивал
 * клиента в 10% реплик, 88% срабатываний FALLBACK_END_OF_TURN шли ВОПРЕКИ
 * вердикту turn-detector. Три корневые причины и их лечение:
 *
 * 1. НЕ БЫЛО ИНВАРИАНТА «клиент говорит — ход не закрываем».
 *    submitCurrentTurn() мог закрыть ход в любой момент, в т.ч. посреди речи.
 *    -> v2: userSpeaking. Пока VAD держит речь активной, ход не закрывается
 *       никогда (кроме аварийного потолка userSpeakingMaxHoldMs — защита от
 *       залипшего VAD).
 *
 * 2. ГОНКА ВЕРСИЙ НА ПРЕДСКАЗАНИИ ДЕТЕКТОРА.
 *    predict() запрашивался для сегмента N, результат приходил через 250-1000мс
 *    (Silero+Pipecat лаг ~1с), когда клиент уже начал сегмент N+1 — и старый
 *    вердикт применялся к новой речи. Гейт `version !== signalVersion` не
 *    спасал: signalVersion инкрементился ДО прихода результата, и результат
 *    усыновлял новую версию.
 *    -> v2: два независимых счётчика. segmentId (растёт на каждом speechStart,
 *       гейтит таймеры и предсказания) и turnId (растёт только при сабмите,
 *       отдаётся сценарию как `version` для гейтинга LLM). Предсказание
 *       штампуется segmentId В МОМЕНТ predict(); результат с чужим штампом
 *       отбрасывается (STALE_PREDICTION).
 *
 * 3. ФОЛБЭК-ТАЙМЕР НЕ ВИДЕЛ ВЕРДИКТ ДЕТЕКТОРА.
 *    Ветки «endOfTurn === false» в обработчике просто не было: таймер на 700мс
 *    от speechEnd отрабатывал при любом p, включая p=0.021.
 *    -> v2: вето. При endOfTurn=false окно закрытия продлевается обратно
 *       пропорционально уверенности (vetoHoldStrongMs / vetoHoldSoftMs),
 *       с потолком maxTurnHoldMs и лимитом maxVetoHolds.
 *
 * Дополнительно:
 *  - СТАБИЛЬНОСТЬ ТЕКСТА. Ход не закрывается по interim, который ещё растёт:
 *    решение принимается по факту «ASR перестал дописывать» (interimStableMs),
 *    а не по слепому таймеру. Потолок ожидания — maxInterimWaitMs.
 *  - РЕКОНСИЛЯЦИЯ. Поздний ASR-финал (в проде — в среднем +4.6с) больше не
 *    выбрасывается: если он относится к уже отправленной реплике, зовётся
 *    onTurnCorrection(fullText, turnId, meta) — сценарий может перегенерить
 *    ответ и/или починить историю диалога. Побочно чинится протечка позднего
 *    финала в СЛЕДУЮЩИЙ ход (раньше он приклеивался к новой реплике).
 *  - BARGE-IN. onInterrupt зовётся только если агент реально звучит
 *    (isAgentSpeaking()) и речь продержалась bargeInMinSpeechMs. Раньше это был
 *    счётчик сегментов VAD: 184 «перебивания» за 22 минуты разговоров.
 *  - stats(): счётчики закрытий по причинам, удержаний, обрезок — чтобы эффект
 *    правок был измерим на проде, а не «на слух».
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ЧТО ИЗМЕНИЛОСЬ В v2.1
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * РЕКОНСИЛЯЦИЯ ПО СЕГМЕНТУ VAD, А НЕ ПО ПРЕФИКСУ ТЕКСТА.
 * v2 признавала поздний финал уточнением только если он ДОСЛОВНО продолжал
 * отправленное (`norm.indexOf(submitted.norm) === 0`). Но Yandex правит текст
 * в середине, а не дописывает в конец:
 *     отправлено: «...какая может может     в моей ситуации»
 *     финал:      «...какая может может быть в моей ситуации»
 * Префикс не совпадал -> финал проваливался в ветку ASR_LED_TURN и открывал
 * НОВЫЙ ход. Клиент задавал один вопрос и получал два ответа подряд.
 *
 * v2.1 опирается на факт, а не на текст: если после сабмита VAD не фиксировал
 * НОВОЙ речи (segmentId не изменился), то пришедший финал физически относится к
 * тому же куску аудио — это уточнение, чем бы оно ни отличалось по буквам.
 * Префиксная проверка сохранена как второй путь: она ловит случай, когда
 * сегмент уже сменился, но текст явно продолжает отправленный.
 */

require(Modules.ASR);
require(Modules.Silero);
require(Modules.Pipecat);

// eslint-disable-next-line no-unused-vars
const VoxTurnTaking = {
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
