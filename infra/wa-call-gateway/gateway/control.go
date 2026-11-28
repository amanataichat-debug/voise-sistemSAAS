package main

// Управляющий сокет к бэкенду: {BACKEND_WS_URL}/ws/wa-call-gateway/control?token=…&gateway=…
// Соединение исходящее (входящие порты на сервере не нужны), переподключение 1 → 30 с.
// Шлюз → бэкенд: hello, account.state, call.event, pong, status.
// Бэкенд → шлюз: accept, reject (с ignore=true — не сбрасывать, пусть звонит на телефоне), originate, hangup, login, logout, ping, status.

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/url"
	"strings"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
)

// Control — постоянное соединение с бэкендом.
type Control struct {
	cfg       Config
	log       *slog.Logger
	out       chan []byte
	connected atomic.Bool
	onMessage func(map[string]any)
	hello     func() map[string]any
}

// NewControl создаёт клиент. onMessage вызывается в своей горутине на каждую команду.
func NewControl(cfg Config, log *slog.Logger, hello func() map[string]any, onMessage func(map[string]any)) *Control {
	return &Control{cfg: cfg, log: log, out: make(chan []byte, 2000), onMessage: onMessage, hello: hello}
}

// Connected — соединение с бэкендом сейчас живо.
func (c *Control) Connected() bool { return c.connected.Load() }

// Send ставит сообщение в очередь (не ждёт). Полная очередь — сообщение теряется с предупреждением.
func (c *Control) Send(msg map[string]any) {
	data, err := json.Marshal(msg)
	if err != nil {
		c.log.Error("control: cannot encode message", "type", msg["type"], "error", err)
		return
	}
	select {
	case c.out <- data:
	default:
		c.log.Warn("control queue full, message dropped", "type", msg["type"])
	}
}

func (c *Control) url() string {
	q := url.Values{}
	q.Set("token", c.cfg.Token)
	q.Set("gateway", c.cfg.GatewayID)
	return c.cfg.BackendWS + "/ws/wa-call-gateway/control?" + q.Encode()
}

// Run держит соединение до отмены ctx.
func (c *Control) Run(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		err := c.session(ctx)
		c.connected.Store(false)
		if ctx.Err() != nil {
			return
		}
		c.log.Warn("control socket down", "error", redact(err, c.cfg.Token), "retry_in", backoff.String())
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff *= 2
		if backoff > 30*time.Second {
			backoff = 30 * time.Second
		}
		if err == nil {
			backoff = time.Second
		}
	}
}

func (c *Control) session(ctx context.Context) error {
	dialCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	conn, _, err := websocket.Dial(dialCtx, c.url(), nil)
	cancel()
	if err != nil {
		return err
	}
	conn.SetReadLimit(1 << 20)
	defer conn.CloseNow()

	sctx, stop := context.WithCancel(ctx)
	defer stop()

	hello := c.hello()
	hello["type"] = "hello"
	data, _ := json.Marshal(hello)
	if err := conn.Write(sctx, websocket.MessageText, data); err != nil {
		return err
	}
	c.connected.Store(true)
	c.log.Info("control socket connected", "backend", c.cfg.BackendWS)

	errc := make(chan error, 3)
	go func() { // отправка
		for {
			select {
			case <-sctx.Done():
				errc <- nil
				return
			case msg := <-c.out:
				wctx, wcancel := context.WithTimeout(sctx, 10*time.Second)
				err := conn.Write(wctx, websocket.MessageText, msg)
				wcancel()
				if err != nil {
					select { // вернуть сообщение для следующего соединения
					case c.out <- msg:
					default:
					}
					errc <- err
					return
				}
			}
		}
	}()
	go func() { // проверка живости
		t := time.NewTicker(20 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-sctx.Done():
				return
			case <-t.C:
				pctx, pcancel := context.WithTimeout(sctx, 20*time.Second)
				err := conn.Ping(pctx)
				pcancel()
				if err != nil {
					errc <- err
					return
				}
			}
		}
	}()
	go func() { // приём команд
		for {
			typ, data, err := conn.Read(sctx)
			if err != nil {
				errc <- err
				return
			}
			if typ != websocket.MessageText {
				continue
			}
			var msg map[string]any
			if json.Unmarshal(data, &msg) != nil {
				continue
			}
			go c.onMessage(msg)
		}
	}()
	err = <-errc
	stop()
	conn.Close(websocket.StatusNormalClosure, "")
	return err
}

// str — строковое поле команды.
func str(m map[string]any, key string) string {
	if v, ok := m[key]; ok && v != nil {
		switch t := v.(type) {
		case string:
			return t
		case float64:
			return jsonNumber(t)
		default:
			b, _ := json.Marshal(t)
			return string(b)
		}
	}
	return ""
}

func jsonNumber(f float64) string {
	b, _ := json.Marshal(f)
	return string(b)
}

// redact убирает токен шлюза из текста ошибки (в ошибках соединения есть полный адрес с ?token=…).
func redact(err error, token string) string {
	if err == nil {
		return ""
	}
	msg := err.Error()
	if token != "" {
		msg = strings.ReplaceAll(msg, token, "***")
		msg = strings.ReplaceAll(msg, url.QueryEscape(token), "***")
	}
	return msg
}
