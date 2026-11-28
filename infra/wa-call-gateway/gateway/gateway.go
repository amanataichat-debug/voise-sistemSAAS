package main

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/polymorfa/hypermeow/store/sqlstore"
	"github.com/polymorfa/hypermeow/types"
	"github.com/purpshell/meowcaller"
)

// Gateway — процесс шлюза: аккаунты, звонки, связь с бэкендом.
type Gateway struct {
	cfg       Config
	log       *slog.Logger
	container *sqlstore.Container
	ctl       *Control
	uploader  *RecordingUploader

	mu       sync.Mutex
	accounts map[string]*Account
	calls    map[string]*CallBridge // наш call_id → звонок
	filesMu  sync.Mutex
	started  time.Time
}

// NewGateway собирает шлюз (без сети).
func NewGateway(cfg Config, log *slog.Logger, container *sqlstore.Container) *Gateway {
	g := &Gateway{
		cfg: cfg, log: log, container: container,
		accounts: map[string]*Account{},
		calls:    map[string]*CallBridge{},
		started:  time.Now(),
	}
	g.uploader = NewRecordingUploader(cfg, log)
	g.ctl = NewControl(cfg, log, g.hello, g.handleCommand)
	return g
}

// LoadAccounts поднимает сохранённые сессии.
func (g *Gateway) LoadAccounts(ctx context.Context) {
	for id, raw := range g.loadAccountsFile() {
		if id == echoAccountID && !g.cfg.EchoTest {
			g.log.Warn("echo-test session is not used outside ECHO_TEST: unlink the device in WhatsApp → Linked devices")
			continue
		}
		jid, err := types.ParseJID(raw)
		if err != nil {
			g.log.Warn("bad device JID in accounts.json", "account_id", id)
			continue
		}
		dev, err := g.container.GetDevice(ctx, jid)
		acc := g.account(id, true)
		if err != nil || dev == nil {
			g.log.Warn("session of account is gone, a new QR is needed", "account_id", id, "error", err)
			acc.setState(StateLoggedOut, "сессия не найдена на шлюзе", nil)
			continue
		}
		acc.start(dev)
	}
}

func (g *Gateway) account(id string, create bool) *Account {
	g.mu.Lock()
	defer g.mu.Unlock()
	acc := g.accounts[id]
	if acc == nil && create {
		acc = &Account{ID: id, g: g, state: StateLoggedOut, offers: map[string]offerInfo{}}
		g.accounts[id] = acc
	}
	return acc
}

func (g *Gateway) activeCalls() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	n := 0
	for _, c := range g.calls {
		if !c.isEnded() {
			n++
		}
	}
	return n
}

func (g *Gateway) register(b *CallBridge) {
	g.mu.Lock()
	g.calls[b.ID] = b
	g.mu.Unlock()
}

// forget убирает звонок из таблицы через 2 минуты (поздние команды бэкенда ещё находят его).
func (g *Gateway) forget(b *CallBridge) {
	time.AfterFunc(2*time.Minute, func() {
		g.mu.Lock()
		if g.calls[b.ID] == b {
			delete(g.calls, b.ID)
		}
		g.mu.Unlock()
	})
}

func (g *Gateway) call(id string) *CallBridge {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.calls[id]
}

func (g *Gateway) liveCallIDs() map[string]bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	out := map[string]bool{}
	for id, c := range g.calls {
		if !c.isEnded() {
			out[id] = true
		}
	}
	return out
}

// ---------------------------------------------------------------- события к бэкенду

func (g *Gateway) emit(event string, b *CallBridge, extra map[string]any) {
	if b.echo {
		return
	}
	msg := b.Public()
	for k, v := range extra {
		msg[k] = v
	}
	msg["type"] = "call.event"
	msg["event"] = event
	msg["ts"] = float64(time.Now().UnixMilli()) / 1000.0
	msg["gateway_id"] = g.cfg.GatewayID
	g.ctl.Send(msg)
}

func (g *Gateway) sendAccountState(a *Account, extra map[string]any) {
	msg := a.Describe()
	for k, v := range extra {
		msg[k] = v
	}
	msg["type"] = "account.state"
	msg["gateway_id"] = g.cfg.GatewayID
	g.ctl.Send(msg)
}

func (g *Gateway) hello() map[string]any {
	g.mu.Lock()
	accs := make([]*Account, 0, len(g.accounts))
	for _, a := range g.accounts {
		accs = append(accs, a)
	}
	calls := make([]*CallBridge, 0, len(g.calls))
	for _, c := range g.calls {
		calls = append(calls, c)
	}
	g.mu.Unlock()
	accounts := make([]map[string]any, 0, len(accs))
	for _, a := range accs {
		accounts = append(accounts, a.Describe())
	}
	active := make([]map[string]any, 0)
	for _, c := range calls {
		if !c.isEnded() {
			active = append(active, c.Public())
		}
	}
	return map[string]any{
		"gateway_id":   g.cfg.GatewayID,
		"version":      Version,
		"kind":         "whatsapp",
		"max_calls":    g.cfg.MaxCalls,
		"accounts":     accounts,
		"active_calls": active,
	}
}

// ---------------------------------------------------------------- команды бэкенда

func (g *Gateway) handleCommand(msg map[string]any) {
	switch str(msg, "type") {
	case "ping":
		g.ctl.Send(map[string]any{"type": "pong", "ts": float64(time.Now().UnixMilli()) / 1000.0})
	case "status":
		st := g.hello()
		st["type"] = "status"
		g.ctl.Send(st)
	case "accept", "reject":
		b := g.call(str(msg, "call_id"))
		if b == nil {
			g.log.Warn("decision for unknown call", "call_id", str(msg, "call_id"))
			return
		}
		select {
		case b.decision <- msg:
		default:
		}
	case "originate":
		g.originate(msg)
	case "hangup":
		if b := g.call(str(msg, "call_id")); b != nil {
			reason := str(msg, "reason")
			if reason == "" {
				reason = "backend_request"
			}
			b.Hangup(reason)
		}
	case "login":
		id := str(msg, "account_id")
		if _, err := uuid.Parse(id); err != nil {
			g.log.Warn("login: bad account_id", "account_id", id)
			return
		}
		acc := g.account(id, true)
		if acc.connected() && str(msg, "force") != "true" {
			g.sendAccountState(acc, nil)
			return
		}
		acc.login()
	case "logout":
		if acc := g.account(str(msg, "account_id"), false); acc != nil {
			acc.logout()
		}
	default:
		g.log.Warn("unknown control command", "type", str(msg, "type"))
	}
}

// ---------------------------------------------------------------- входящий звонок

// onIncomingCall вызывается библиотекой внутри обработки событий WhatsApp — здесь
// нельзя ждать (задержали бы сигнализацию этого же звонка), поэтому решение — в горутине.
func (g *Gateway) onIncomingCall(acc *Account, c *meowcaller.Call) {
	go g.handleIncoming(acc, c)
}

func (g *Gateway) handleIncoming(acc *Account, c *meowcaller.Call) {
	b := newCallBridge(g, uuid.NewString(), "inbound", acc)
	b.WAID = c.ID()
	b.Caller, b.CallerJID, b.CallerName = acc.callerOf(c)
	c.OnEnd(func(reason string) {
		b.mu.Lock()
		answered := !b.answeredAt.IsZero()
		b.mu.Unlock()
		b.finish(mapEndReason("inbound", answered, reason))
	})
	c.OnReady(b.onReady)
	c.OnMuteState(func(muted bool) { g.log.Debug("peer mute", "call_id", b.ID, "muted", muted) })
	g.log.Info("incoming WhatsApp call", "call_id", b.ID, "account_id", acc.ID,
		"caller", maskPhone(b.Caller), "lid_only", b.Caller == "")

	if g.cfg.EchoTest {
		b.echo = true
		g.register(b)
		b.attach(c)
		if err := c.Answer(); err != nil {
			g.log.Error("echo test: answer failed", "error", err)
			b.finish("answer_failed")
		}
		return
	}
	if g.activeCalls() >= g.cfg.MaxCalls {
		g.log.Warn("incoming call rejected: gateway is at MAX_CALLS", "max_calls", g.cfg.MaxCalls)
		_ = c.Reject()
		g.register(b)
		b.finish("gateway_busy")
		return
	}
	if !g.ctl.Connected() {
		// Деплой бэкенда (1–2 мин): отклоняем, а не держим звонящего в гудках
		g.log.Warn("incoming call rejected: backend is unavailable")
		_ = c.Reject()
		g.register(b)
		b.finish("backend_unavailable")
		return
	}
	g.register(b)
	g.emit("started", b, nil)

	var decision map[string]any
	select {
	case decision = <-b.decision:
	case <-time.After(g.cfg.DecisionTimeout):
		g.log.Warn("no accept/reject from backend in time", "call_id", b.ID)
		_ = c.Reject()
		b.finish("decision_timeout")
		return
	case <-b.ctx.Done(): // звонящий положил трубку, пока решали
		return
	}
	if str(decision, "type") != "accept" {
		reason := str(decision, "reason")
		if reason == "" {
			reason = "rejected_by_backend"
		}
		if ignore, _ := decision["ignore"].(bool); ignore {
			// Агент не отвечает (выключен владельцем, пуст кошелёк…): звонок не сбрасываем —
			// он звонит на телефоне владельца, как будто шлюза нет.
			g.log.Info("backend passed the call to the owner's phone", "call_id", b.ID, "reason", reason)
			b.finish(reason)
			return
		}
		g.log.Info("backend rejected the call", "call_id", b.ID, "reason", reason)
		_ = c.Reject()
		b.finish(reason)
		return
	}
	b.mu.Lock()
	b.AssistantID = str(decision, "assistant_id")
	b.AssistantType = str(decision, "assistant_type")
	if md, ok := decision["metadata"].(map[string]any); ok {
		b.Metadata = md
	}
	b.mu.Unlock()
	b.attach(c)
	if err := c.Answer(); err != nil {
		g.log.Error("answer failed", "call_id", b.ID, "error", err)
		b.finish("answer_failed")
		return
	}
	// Звук обычно идёт через доли секунды; если библиотека молчит — открываем разговор всё равно
	time.AfterFunc(g.cfg.MediaReadyWait, func() { b.startMedia("answer_timeout") })
}

// ---------------------------------------------------------------- исходящий звонок

func (g *Gateway) originate(cmd map[string]any) {
	id := str(cmd, "call_id")
	if _, err := uuid.Parse(id); err != nil {
		id = uuid.NewString()
	}
	acc := g.account(str(cmd, "account_id"), false)
	b := newCallBridge(g, id, "outbound", acc)
	b.To = strings.TrimPrefix(strings.TrimSpace(str(cmd, "to")), "+")
	b.AssistantID = str(cmd, "assistant_id")
	b.AssistantType = str(cmd, "assistant_type")
	if md, ok := cmd["metadata"].(map[string]any); ok {
		b.Metadata = md
	}
	g.register(b)
	fail := func(reason, detail string) {
		g.log.Warn("outbound call failed", "call_id", id, "reason", reason, "detail", detail)
		b.mu.Lock()
		b.endReason = reason
		b.mu.Unlock()
		b.finishWith(map[string]any{"error": detail})
	}
	if !digitsOnly(b.To) || len(b.To) < 8 {
		fail("bad_number", "")
		return
	}
	if acc == nil || !acc.connected() {
		fail("account_unavailable", "аккаунт звонков WhatsApp не подключён к шлюзу")
		return
	}
	if g.activeCalls() > g.cfg.MaxCalls {
		fail("gateway_busy", "")
		return
	}
	g.emit("started", b, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	acc.mu.Lock()
	calls := acc.calls
	acc.mu.Unlock()
	c, err := calls.Call(ctx, "+"+b.To)
	if err != nil {
		fail(classifyCallError(err), err.Error())
		return
	}
	b.WAID = c.ID()
	c.OnEnd(func(reason string) {
		b.mu.Lock()
		answered := !b.answeredAt.IsZero()
		b.mu.Unlock()
		b.finish(mapEndReason("outbound", answered, reason))
	})
	c.OnStateChange(func(p meowcaller.CallPhase) {
		if p == meowcaller.CallPhaseRinging {
			g.emit("ringing", b, nil)
		}
	})
	c.OnPeerAccept(b.onPeerAccept)
	c.OnReady(b.onReady)
	b.attach(c)
	g.log.Info("outbound WhatsApp call placed", "call_id", id, "to", maskPhone(b.To))
	time.AfterFunc(g.cfg.RingTimeout, func() {
		b.mu.Lock()
		waiting := b.answeredAt.IsZero() && !b.ended
		b.mu.Unlock()
		if waiting {
			b.Hangup("no_answer")
		}
	})
}

// finishWith — завершение с доп. полями в событии (ошибка набора).
func (b *CallBridge) finishWith(extra map[string]any) {
	b.mu.Lock()
	if b.ended {
		b.mu.Unlock()
		return
	}
	b.ended = true
	b.endedAt = time.Now()
	b.mu.Unlock()
	b.cancel()
	_ = b.queue.Close()
	b.g.emit("failed", b, extra)
	b.g.forget(b)
}

func digitsOnly(s string) bool {
	if s == "" {
		return false
	}
	for _, ch := range s {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func classifyCallError(err error) string {
	e := strings.ToLower(err.Error())
	switch {
	case strings.Contains(e, "not on whatsapp") || strings.Contains(e, "no devices") || strings.Contains(e, "unreachable"):
		return "not_on_whatsapp"
	case strings.Contains(e, "context deadline") || strings.Contains(e, "timeout"):
		return "wa_timeout"
	case strings.Contains(e, "privacy"):
		return "privacy_blocked"
	}
	return "wa_error"
}

// maskPhone — в логах только хвост номера.
func maskPhone(p string) string {
	if len(p) <= 4 {
		return p
	}
	return strings.Repeat("•", len(p)-4) + p[len(p)-4:]
}

// ---------------------------------------------------------------- здоровье (только 127.0.0.1)

func (g *Gateway) serveHealth(ctx context.Context) {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		st := g.hello()
		st["ok"] = true
		st["control_connected"] = g.ctl.Connected()
		st["echo_test"] = g.cfg.EchoTest
		st["uptime_sec"] = int(time.Since(g.started).Seconds())
		st["recordings_pending"] = g.uploader.Pending()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(st)
	})
	srv := &http.Server{Addr: g.cfg.HealthAddr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		_ = srv.Close()
	}()
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		g.log.Warn("health server stopped", "error", err)
	}
}

// Shutdown кладёт все звонки (деплой шлюза) и отключает аккаунты.
func (g *Gateway) Shutdown() {
	g.mu.Lock()
	calls := make([]*CallBridge, 0, len(g.calls))
	for _, c := range g.calls {
		calls = append(calls, c)
	}
	accs := make([]*Account, 0, len(g.accounts))
	for _, a := range g.accounts {
		accs = append(accs, a)
	}
	g.mu.Unlock()
	for _, c := range calls {
		if !c.isEnded() {
			c.Hangup("gateway_shutdown")
			c.finish("gateway_shutdown")
		}
	}
	time.Sleep(500 * time.Millisecond) // ended успевают уйти в управляющий сокет
	for _, a := range accs {
		a.mu.Lock()
		wa := a.wa
		a.mu.Unlock()
		if wa != nil {
			wa.Disconnect()
		}
	}
}

// echoAccountID — аккаунт эхо-теста (проба без бэкенда).
const echoAccountID = "00000000-0000-4000-8000-0000000ec400"

// echoLogin — в эхо-тесте нет бэкенда, который пришлёт login: если сохранённой сессии нет,
// шлюз сам показывает QR в журнале (journalctl -u voksy-wa-call -f).
func (g *Gateway) echoLogin(ctx context.Context) {
	g.mu.Lock()
	n := len(g.accounts)
	g.mu.Unlock()
	if n > 0 {
		return
	}
	g.log.Info("echo test: no saved session, showing a QR code to link this number")
	g.account(echoAccountID, true).login()
}
