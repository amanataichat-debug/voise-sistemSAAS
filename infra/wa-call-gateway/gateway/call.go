package main

// Один звонок WhatsApp ↔ медиа-сокет бэкенда {BACKEND_WS_URL}/ws/sip/{call_id}.
// Протокол сокета — как у SIP-моста (infra/sip-gateway/bridge/bridge.py), чтобы адаптер
// бэкенда (sip_media_adapter.HandlerSocket) работал без своей ветки для WhatsApp:
//   шлюз → бэкенд: {"type":"start", …, "channel":"whatsapp", "format":{pcm16,16000,1,20}},
//                  затем бинарные кадры PCM16 LE 16 кГц по 20 мс, {"type":"ended","reason":…}
//   бэкенд → шлюз: бинарный PCM16 16 кГц любыми кусками, {"type":"clear"}, {"type":"hangup"},
//                  {"type":"mark","name":…} (эхо, когда проигрывание дошло до метки), {"type":"ping"}

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/purpshell/meowcaller"
)

// CallBridge — состояние одного звонка.
type CallBridge struct {
	g *Gateway

	ID        string // наш id = sip_calls.id на бэкенде (UUID)
	WAID      string // id звонка в WhatsApp
	Direction string // inbound | outbound
	Account   *Account

	Caller     string // номер звонящего (входящий), E.164 без плюса; может быть пустым (только LID)
	CallerJID  string
	CallerName string
	To         string // кому звоним (исходящий)

	AssistantID   string
	AssistantType string
	Metadata      map[string]any

	echo bool

	mu         sync.Mutex
	wa         *meowcaller.Call
	queue      *PlayoutQueue
	framer     Framer
	rec        *CallRecorder
	ws         *websocket.Conn
	createdAt  time.Time
	answeredAt time.Time
	endedAt    time.Time
	endReason  string
	ended      bool
	accepted   bool // исходящий: собеседник взял трубку
	ready      bool // пошёл живой звук от собеседника
	mediaUp    bool
	decision   chan map[string]any

	in        chan []byte // кадры собеседника к бэкенду
	framesIn  atomic.Int64
	framesOut atomic.Int64
	dropped   atomic.Int64
	// кадров тишины, вставленных вместо молчания WhatsApp
	silenceOut atomic.Int64

	ctx    context.Context
	cancel context.CancelFunc
}

func newCallBridge(g *Gateway, id, direction string, acc *Account) *CallBridge {
	ctx, cancel := context.WithCancel(context.Background())
	b := &CallBridge{
		g: g, ID: id, Direction: direction, Account: acc,
		Metadata:  map[string]any{},
		createdAt: time.Now(),
		in:        make(chan []byte, 250), // 5 с звука: бэкенд подключается к модели
		decision:  make(chan map[string]any, 1),
		ctx:       ctx, cancel: cancel,
	}
	b.queue = NewPlayoutQueue(b.onMark)
	return b
}

// Public — поля звонка для событий и start (как Call.public() у SIP-моста).
func (b *CallBridge) Public() map[string]any {
	underruns := 0
	if b.queue != nil {
		underruns = b.queue.UnderrunCount()
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	var duration any
	if !b.answeredAt.IsZero() {
		end := b.endedAt
		if end.IsZero() {
			end = time.Now()
		}
		duration = roundTo(end.Sub(b.answeredAt).Seconds(), 1)
	}
	acc := ""
	phone := ""
	if b.Account != nil {
		acc = b.Account.ID
		phone = b.Account.Phone()
	}
	out := map[string]any{
		"call_id":        b.ID,
		"wa_call_id":     b.WAID,
		"channel":        "whatsapp",
		"direction":      b.Direction,
		"account_id":     acc,
		"did":            phone, // наш номер WhatsApp — поле did, как у SIP
		"caller":         b.Caller,
		"caller_jid":     b.CallerJID,
		"caller_name":    b.CallerName,
		"to":             b.To,
		"assistant_id":   b.AssistantID,
		"assistant_type": b.AssistantType,
		"metadata":       b.Metadata,
		"created_at":     unix(b.createdAt),
		"answered_at":    unix(b.answeredAt),
		"ended_at":       unix(b.endedAt),
		"duration_sec":   duration,
		"reason":         b.endReason,
		"frames_in":      b.framesIn.Load(),
		"frames_out":     b.framesOut.Load(),
		"underruns":      underruns,
	}
	return out
}

func unix(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return float64(t.UnixMilli()) / 1000.0
}

func roundTo(v float64, digits int) float64 {
	p := 1.0
	for i := 0; i < digits; i++ {
		p *= 10
	}
	return float64(int64(v*p+0.5)) / p
}

func (b *CallBridge) isEnded() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.ended
}

// attach подключает очередь агента и приёмник звука собеседника к звонку WhatsApp.
func (b *CallBridge) attach(wa *meowcaller.Call) {
	b.mu.Lock()
	b.wa = wa
	b.WAID = wa.ID()
	if b.g.cfg.RecordCalls && !b.echo && b.rec == nil {
		if rec, err := NewCallRecorder(b.g.cfg.RecordingsDir, b.ID); err == nil {
			b.rec = rec
		} else {
			b.g.log.Warn("call recording disabled for this call", "call_id", b.ID, "error", err)
		}
	}
	rec := b.rec
	b.mu.Unlock()
	if rec != nil {
		b.queue.SetTick(rec.Agent)
	}
	wa.Receive(meowcaller.SinkFunc(b.onPeerFrame))
	wa.Play(b.queue)
}

// onPeerFrame — кадр собеседника (60 мс, float32 16 кГц). Не блокирует цикл библиотеки.
func (b *CallBridge) onPeerFrame(frame []float32) {
	b.mu.Lock()
	rec := b.rec
	b.mu.Unlock()
	if rec != nil {
		rec.Caller(frame)
	}
	if b.echo {
		b.queue.Push(FloatToPCM16(nil, frame))
		return
	}
	for _, chunk := range b.framer.Push(frame) {
		b.framesIn.Add(1)
		select {
		case b.in <- chunk:
		default: // бэкенд не успевает — старый кадр выбрасываем, задержка не копится
			select {
			case <-b.in:
				b.dropped.Add(1)
			default:
			}
			select {
			case b.in <- chunk:
			default:
			}
		}
	}
}

// onMark — проигрывание дошло до метки бэкенда: вернуть её, как мост SIP.
func (b *CallBridge) onMark(name string) {
	b.sendText(map[string]any{"type": "mark", "name": name})
}

func (b *CallBridge) sendText(msg map[string]any) {
	b.mu.Lock()
	ws := b.ws
	b.mu.Unlock()
	if ws == nil {
		return
	}
	data, _ := json.Marshal(msg)
	ctx, cancel := context.WithTimeout(b.ctx, 5*time.Second)
	_ = ws.Write(ctx, websocket.MessageText, data)
	cancel()
}

// onReady — от собеседника пошёл живой звук.
func (b *CallBridge) onReady() {
	b.mu.Lock()
	b.ready = true
	ok := b.Direction == "inbound" || b.accepted
	b.mu.Unlock()
	if ok {
		b.startMedia("ready")
	}
}

// onPeerAccept — исходящий: собеседник взял трубку.
func (b *CallBridge) onPeerAccept() {
	b.mu.Lock()
	b.accepted = true
	ready := b.ready
	b.mu.Unlock()
	b.g.log.Info("outbound call answered", "call_id", b.ID)
	if ready {
		b.startMedia("ready")
		return
	}
	// Звук ещё не пошёл — подождать немного, потом открыть разговор всё равно
	time.AfterFunc(b.g.cfg.MediaReadyWait, func() { b.startMedia("accept_timeout") })
}

// startMedia — разговор начался: событие answered и медиа-сокет к бэкенду (один раз).
func (b *CallBridge) startMedia(why string) {
	b.mu.Lock()
	if b.mediaUp || b.ended {
		b.mu.Unlock()
		return
	}
	b.mediaUp = true
	if b.answeredAt.IsZero() {
		b.answeredAt = time.Now()
	}
	b.mu.Unlock()
	b.g.log.Info("call media up", "call_id", b.ID, "direction", b.Direction, "why", why, "echo", b.echo)
	if b.echo {
		return
	}
	b.g.emit("answered", b, nil)
	go b.watchdog()
	go b.runMedia()
}

func (b *CallBridge) watchdog() {
	limit := time.Duration(b.g.cfg.MaxCallSeconds) * time.Second
	if limit <= 0 {
		return
	}
	select {
	case <-b.ctx.Done():
	case <-time.After(limit):
		b.g.log.Warn("call exceeded MAX_CALL_SECONDS, hanging up", "call_id", b.ID)
		b.Hangup("max_duration")
	}
}

func (b *CallBridge) mediaURL() string {
	q := url.Values{}
	q.Set("token", b.g.cfg.Token)
	q.Set("gateway", b.g.cfg.GatewayID)
	return b.g.cfg.BackendWS + "/ws/sip/" + b.ID + "?" + q.Encode()
}

func (b *CallBridge) runMedia() {
	dctx, cancel := context.WithTimeout(b.ctx, 6*time.Second)
	ws, _, err := websocket.Dial(dctx, b.mediaURL(), nil)
	cancel()
	if err != nil {
		b.g.log.Error("cannot reach backend media socket", "call_id", b.ID, "error", redact(err, b.g.cfg.Token))
		b.Hangup("backend_unavailable")
		return
	}
	ws.SetReadLimit(4 << 20)
	b.mu.Lock()
	if b.ended {
		b.mu.Unlock()
		ws.CloseNow()
		return
	}
	b.ws = ws
	b.mu.Unlock()

	start := b.Public()
	start["type"] = "start"
	start["gateway_id"] = b.g.cfg.GatewayID
	start["format"] = map[string]any{"encoding": "pcm16", "sample_rate": LineRate, "channels": 1, "frame_ms": LineFrameMs}
	data, _ := json.Marshal(start)
	if err := ws.Write(b.ctx, websocket.MessageText, data); err != nil {
		b.Hangup("backend_unavailable")
		return
	}

	go b.pumpToBackend(ws)
	reason, alive := b.readBackend(ws)
	if alive {
		return // бэкенд сам положил трубку: сокет жив, finish отправит в него ended
	}

	b.mu.Lock()
	if b.ws == ws {
		b.ws = nil
	}
	ended := b.ended
	b.mu.Unlock()
	if ended {
		return
	}
	// Бэкенд закрыл сокет сам: даём доиграть хвост реплики и кладём трубку
	tail := b.queue.Queued()
	if tail > 2*time.Second {
		tail = 2 * time.Second
	}
	select {
	case <-b.ctx.Done():
	case <-time.After(tail):
	}
	b.Hangup(reason)
}

// pumpToBackend шлёт бэкенду звук собеседника НЕПРЕРЫВНО, кадр 20 мс на каждый тик, как линия оператора.
// WhatsApp, пока человек молчит, звук не передаёт (экономия трафика), и без вставки тишины бэкенд видел
// поток с дырами: голосовая модель не понимала, что собеседник замолчал, и не клала трубку после прощания
// (звонок 4c80dde7, 28.11.2026: из 98 с дошло 40 с звука, 165 обрывов). Нет кадра — шлём тишину;
// накопилось больше backlogMax кадров — старые выбрасываем, задержка не копится.
func (b *CallBridge) pumpToBackend(ws *websocket.Conn) {
	const backlogMax = 10 // 200 мс
	silence := make([]byte, LineFrameBytes)
	tick := time.NewTicker(LineFrameMs * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-b.ctx.Done():
			return
		case <-tick.C:
		}
		for len(b.in) > backlogMax {
			<-b.in
			b.dropped.Add(1)
		}
		frame := silence
		select {
		case chunk := <-b.in:
			frame = chunk
		default:
			b.silenceOut.Add(1)
		}
		wctx, cancel := context.WithTimeout(b.ctx, 5*time.Second)
		err := ws.Write(wctx, websocket.MessageBinary, frame)
		cancel()
		if err != nil {
			return
		}
	}
}

// readBackend читает команды бэкенда. alive=true — бэкенд попросил положить трубку и сокет ещё открыт.
func (b *CallBridge) readBackend(ws *websocket.Conn) (reason string, alive bool) {
	for {
		typ, data, err := ws.Read(b.ctx)
		if err != nil {
			if b.ctx.Err() != nil {
				return "ended", false
			}
			return "backend_closed", false
		}
		if typ == websocket.MessageBinary {
			b.queue.Push(data)
			b.framesOut.Add(1)
			continue
		}
		var msg map[string]any
		if json.Unmarshal(data, &msg) != nil {
			continue
		}
		switch str(msg, "type") {
		case "clear":
			dropped := b.queue.Clear()
			b.g.log.Debug("clear from backend", "call_id", b.ID, "dropped_ms", dropped.Milliseconds())
		case "hangup":
			reason := str(msg, "reason")
			if reason == "" {
				reason = "backend_hangup"
			}
			b.Hangup(reason)
			return reason, true
		case "mark":
			b.queue.Mark(str(msg, "name"))
		case "ping":
			b.sendText(map[string]any{"type": "pong"})
		}
	}
}

// Hangup кладёт трубку в WhatsApp и завершает звонок.
func (b *CallBridge) Hangup(reason string) {
	b.mu.Lock()
	wa := b.wa
	if b.endReason == "" {
		b.endReason = reason
	}
	b.mu.Unlock()
	if wa != nil {
		if err := wa.Hangup(); err != nil {
			b.g.log.Debug("hangup error", "call_id", b.ID, "error", err)
		}
	}
	// OnEnd обычно приходит сам; если библиотека уже забыла звонок — завершаем здесь
	time.AfterFunc(1500*time.Millisecond, func() { b.finish(reason) })
}

// finish — звонок окончен (один раз): бэкенду ended, запись на загрузку, событие ended/failed.
func (b *CallBridge) finish(reason string) {
	b.mu.Lock()
	if b.ended {
		b.mu.Unlock()
		return
	}
	b.ended = true
	b.endedAt = time.Now()
	if b.endReason == "" {
		b.endReason = reason
	}
	ws := b.ws
	rec := b.rec
	answered := !b.answeredAt.IsZero()
	b.mu.Unlock()

	pub := b.Public()
	if ws != nil {
		msg := map[string]any{}
		for k, v := range pub {
			msg[k] = v
		}
		msg["type"] = "ended"
		data, _ := json.Marshal(msg)
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_ = ws.Write(ctx, websocket.MessageText, data)
		cancel()
		ws.Close(websocket.StatusNormalClosure, "")
	}
	b.cancel()
	_ = b.queue.Close()
	if rec != nil {
		if path := rec.Close(); path != "" && answered {
			b.g.uploader.Submit(b.ID)
		}
	}
	b.g.log.Info("call ended", "call_id", b.ID, "direction", b.Direction, "reason", b.endReason,
		"duration", pub["duration_sec"], "frames_in", pub["frames_in"], "frames_out", pub["frames_out"],
		"underruns", b.queue.UnderrunCount(), "dropped_in", b.dropped.Load(), "silence_frames", b.silenceOut.Load())
	if b.echo {
		b.g.forget(b)
		return
	}
	if answered {
		b.g.emit("ended", b, nil)
	} else {
		b.g.emit("failed", b, nil)
	}
	b.g.forget(b)
}

// endReason переводит причину конца из библиотеки в слова журнала бэкенда.
func mapEndReason(direction string, answered bool, raw string) string {
	r := strings.ToLower(strings.TrimSpace(raw))
	switch {
	case strings.Contains(r, "elsewhere"):
		return "accepted_elsewhere"
	case r == "rejected" || strings.Contains(r, "reject") || strings.Contains(r, "decline"):
		return "rejected"
	case strings.Contains(r, "busy"):
		return "busy"
	case strings.Contains(r, "timeout") || strings.Contains(r, "no_answer"):
		if answered {
			return "timeout"
		}
		return "no_answer"
	}
	if answered {
		return "wa_hangup"
	}
	if direction == "inbound" {
		return "caller_hangup"
	}
	if r == "" {
		return "failed"
	}
	return r
}
