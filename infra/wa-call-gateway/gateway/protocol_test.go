package main

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
)

// fakeBackend — бэкенд для проверки протокола: управляющий сокет и медиа-сокет.
type fakeBackend struct {
	t       *testing.T
	mu      sync.Mutex
	control []map[string]any
	ctlConn *websocket.Conn
	start   map[string]any
	media   [][]byte
	texts   []map[string]any
	mediaC  chan *websocket.Conn
	ctlC    chan *websocket.Conn
}

func newFakeBackend(t *testing.T) (*fakeBackend, *httptest.Server) {
	fb := &fakeBackend{t: t, mediaC: make(chan *websocket.Conn, 4), ctlC: make(chan *websocket.Conn, 4)}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("token") != "secret" {
			http.Error(w, "bad token", http.StatusForbidden)
			return
		}
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		ctx := context.Background()
		switch {
		case r.URL.Path == "/ws/wa-call-gateway/control":
			fb.ctlC <- c
			for {
				_, data, err := c.Read(ctx)
				if err != nil {
					return
				}
				var m map[string]any
				_ = json.Unmarshal(data, &m)
				fb.mu.Lock()
				fb.control = append(fb.control, m)
				fb.mu.Unlock()
			}
		case strings.HasPrefix(r.URL.Path, "/ws/sip/"):
			_, data, err := c.Read(ctx)
			if err != nil {
				return
			}
			var start map[string]any
			_ = json.Unmarshal(data, &start)
			fb.mu.Lock()
			fb.start = start
			fb.mu.Unlock()
			fb.mediaC <- c
			for {
				typ, data, err := c.Read(ctx)
				if err != nil {
					return
				}
				fb.mu.Lock()
				if typ == websocket.MessageBinary {
					fb.media = append(fb.media, data)
				} else {
					var m map[string]any
					_ = json.Unmarshal(data, &m)
					fb.texts = append(fb.texts, m)
				}
				fb.mu.Unlock()
			}
		}
	}))
	return fb, srv
}

func testGateway(t *testing.T, srv *httptest.Server) *Gateway {
	cfg := Config{
		GatewayID: "wa-call-test", Token: "secret",
		BackendWS: "ws" + strings.TrimPrefix(srv.URL, "http"), BackendHTTP: srv.URL,
		DataDir: t.TempDir(), MaxCalls: 2, MaxCallSeconds: 60,
		DecisionTimeout: time.Second, RingTimeout: 5 * time.Second, MediaReadyWait: time.Second,
		RecordingsDir: t.TempDir(), RecordingBitrate: "32", RecordingKeepDays: 1,
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return NewGateway(cfg, log, nil)
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for %s", what)
}

func TestControlHelloAndCommands(t *testing.T) {
	fb, srv := newFakeBackend(t)
	defer srv.Close()
	g := testGateway(t, srv)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go g.ctl.Run(ctx)
	conn := <-fb.ctlC
	waitFor(t, "hello", func() bool {
		fb.mu.Lock()
		defer fb.mu.Unlock()
		return len(fb.control) > 0
	})
	fb.mu.Lock()
	hello := fb.control[0]
	fb.mu.Unlock()
	if hello["type"] != "hello" || hello["gateway_id"] != "wa-call-test" || hello["kind"] != "whatsapp" {
		t.Fatalf("bad hello: %v", hello)
	}
	if hello["max_calls"].(float64) != 2 {
		t.Fatalf("max_calls: %v", hello["max_calls"])
	}
	// ping → pong
	_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"ping"}`))
	// исходящий на неподключённый аккаунт → failed account_unavailable
	_ = conn.Write(ctx, websocket.MessageText, []byte(
		`{"type":"originate","call_id":"3f0c1d2e-1111-4222-8333-444455556666","account_id":"aaaaaaaa-1111-4222-8333-444455556666","to":"996555123456"}`))
	waitFor(t, "pong and failed", func() bool {
		fb.mu.Lock()
		defer fb.mu.Unlock()
		var pong, failed bool
		for _, m := range fb.control {
			if m["type"] == "pong" {
				pong = true
			}
			if m["type"] == "call.event" && m["event"] == "failed" && m["reason"] == "account_unavailable" &&
				m["call_id"] == "3f0c1d2e-1111-4222-8333-444455556666" && m["channel"] == "whatsapp" {
				failed = true
			}
		}
		return pong && failed
	})
}

func TestControlReconnects(t *testing.T) {
	fb, srv := newFakeBackend(t)
	defer srv.Close()
	g := testGateway(t, srv)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go g.ctl.Run(ctx)
	first := <-fb.ctlC
	waitFor(t, "connected", g.ctl.Connected)
	first.CloseNow() // деплой бэкенда
	select {
	case <-fb.ctlC:
	case <-time.After(5 * time.Second):
		t.Fatal("control socket did not reconnect")
	}
	waitFor(t, "reconnected", g.ctl.Connected)
}

func TestMediaSocketProtocol(t *testing.T) {
	fb, srv := newFakeBackend(t)
	defer srv.Close()
	g := testGateway(t, srv)
	ctx := context.Background()
	acc := &Account{ID: "aaaaaaaa-1111-4222-8333-444455556666", g: g, state: StateConnected}
	b := newCallBridge(g, "3f0c1d2e-1111-4222-8333-444455556677", "inbound", acc)
	b.Caller, b.CallerName = "996555123456", "Айбек"
	b.AssistantID, b.AssistantType = "bbbbbbbb-1111-4222-8333-444455556666", "eleven"
	g.register(b)

	b.startMedia("test")
	conn := <-fb.mediaC
	fb.mu.Lock()
	start := fb.start
	fb.mu.Unlock()
	format := start["format"].(map[string]any)
	if start["type"] != "start" || start["channel"] != "whatsapp" || start["direction"] != "inbound" ||
		start["caller"] != "996555123456" || start["caller_name"] != "Айбек" ||
		start["account_id"] != acc.ID || start["assistant_type"] != "eleven" ||
		format["sample_rate"].(float64) != 16000 || format["frame_ms"].(float64) != 20 || format["encoding"] != "pcm16" {
		t.Fatalf("bad start: %v", start)
	}

	// пока собеседник молчит, шлюз всё равно шлёт тишину кадрами 20 мс (WhatsApp в паузах звук не передаёт)
	time.Sleep(300 * time.Millisecond)
	fb.mu.Lock()
	silent := len(fb.media)
	fb.mu.Unlock()
	if silent < 8 {
		t.Fatalf("no continuous silence stream: %d frames in 300 ms", silent)
	}
	// голос собеседника: кадр 60 мс → три кадра по 640 байт среди тишины
	b.onPeerFrame(tone(WAFrameSamples, 0.3, 300))
	waitFor(t, "3 voiced frames", func() bool {
		fb.mu.Lock()
		defer fb.mu.Unlock()
		n := 0
		for _, fr := range fb.media {
			if len(fr) != 640 {
				t.Errorf("frame %d bytes", len(fr))
			}
			if RMS(PCM16ToFloat(fr)) > 0.1 {
				n++
			}
		}
		return n == 3
	})

	// звук агента любыми кусками → очередь
	_ = conn.Write(ctx, websocket.MessageBinary, FloatToPCM16(nil, tone(LineRate/2, 0.3, 500)))
	waitFor(t, "agent audio queued", func() bool { return b.queue.Queued() == 500*time.Millisecond })
	// перебивание
	_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"clear"}`))
	waitFor(t, "clear", func() bool { return b.queue.Queued() == 0 })
	// метка возвращается, когда проигрывание до неё дошло
	_ = conn.Write(ctx, websocket.MessageBinary, FloatToPCM16(nil, tone(WAFrameSamples, 0.3, 500)))
	_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"mark","name":"hangup"}`))
	waitFor(t, "mark queued", func() bool {
		b.queue.mu.Lock()
		defer b.queue.mu.Unlock()
		return len(b.queue.marks) == 1
	})
	b.queue.ReadFrame() // цикл отправки WhatsApp забрал кадр
	waitFor(t, "mark echo", func() bool {
		fb.mu.Lock()
		defer fb.mu.Unlock()
		for _, m := range fb.texts {
			if m["type"] == "mark" && m["name"] == "hangup" {
				return true
			}
		}
		return false
	})
	// ping → pong
	_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"ping"}`))
	// бэкенд кладёт трубку
	_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"hangup","reason":"assistant_hangup"}`))
	waitFor(t, "call ended", b.isEnded)
	waitFor(t, "ended + pong", func() bool {
		fb.mu.Lock()
		defer fb.mu.Unlock()
		var ended, pong bool
		for _, m := range fb.texts {
			if m["type"] == "ended" && m["reason"] == "assistant_hangup" {
				ended = true
			}
			if m["type"] == "pong" {
				pong = true
			}
		}
		return ended && pong
	})
}

func TestBackendUnavailableHangsUp(t *testing.T) {
	_, srv := newFakeBackend(t)
	g := testGateway(t, srv)
	srv.Close() // бэкенд недоступен (деплой)
	acc := &Account{ID: "aaaaaaaa-1111-4222-8333-444455556666", g: g, state: StateConnected}
	b := newCallBridge(g, "3f0c1d2e-1111-4222-8333-444455556688", "inbound", acc)
	g.register(b)
	b.startMedia("test")
	waitFor(t, "call ended", b.isEnded)
	b.mu.Lock()
	reason := b.endReason
	b.mu.Unlock()
	if reason != "backend_unavailable" {
		t.Fatalf("reason %q", reason)
	}
}
