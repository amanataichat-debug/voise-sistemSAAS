// Command voksy-wa-call — шлюз звонков WhatsApp для голосовых агентов Voksy AI.
//
//	Клиент в WhatsApp ⇄ (VoIP WhatsApp, кодек MLow) ⇄ ЭТОТ ШЛЮЗ ⇄ (WebSocket, PCM16 16 кГц) ⇄ бэкенд
//
// Шлюз — ещё одно связанное устройство номера агента (переписка номера остаётся в
// Evolution). Все соединения исходящие: управляющий сокет /ws/wa-call-gateway/control и
// медиа-сокет звонка /ws/sip/{call_id} — тот же, что у SIP-моста, поэтому бэкенд
// заворачивает звонок в тех же голосовых агентов, что телефон и виджет.
//
// Библиотеки: github.com/purpshell/meowcaller (MIT) — звонки WhatsApp, кодек MLow;
// github.com/polymorfa/hypermeow (MPL 2.0, форк go.mau.fi/whatsmeow) — протокол WhatsApp,
// вход по QR, хранение сессии. Тексты лицензий — в THIRD_PARTY_LICENSES.md.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/polymorfa/hypermeow/proto/waCompanionReg"
	"github.com/polymorfa/hypermeow/store"
	"github.com/polymorfa/hypermeow/store/sqlstore"
	_ "modernc.org/sqlite"
)

// Version шлюза (в hello и /health).
const Version = "0.1.0"

func main() {
	if len(os.Args) > 1 && (os.Args[1] == "-v" || os.Args[1] == "--version" || os.Args[1] == "version") {
		fmt.Println("voksy-wa-call", Version)
		return
	}
	cfg, err := LoadConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, "FATAL:", err)
		os.Exit(2)
	}
	log := newLogger(cfg.LogLevel)

	if err := os.MkdirAll(cfg.DataDir, 0o700); err != nil {
		log.Error("cannot create DATA_DIR", "dir", cfg.DataDir, "error", err)
		os.Exit(1)
	}
	if cfg.RecordCalls {
		_ = os.MkdirAll(cfg.RecordingsDir, 0o700)
	}

	// Имя связанного устройства, которое владелец видит в WhatsApp → Связанные устройства
	store.SetOSInfo(cfg.DeviceName, [3]uint32{0, 1, 0})
	store.DeviceProps.PlatformType = waCompanionReg.DeviceProps_DESKTOP.Enum()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	dbPath := filepath.Join(cfg.DataDir, "sessions.db")
	dsn := "file:" + dbPath + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)"
	container, err := sqlstore.New(ctx, "sqlite", dsn, newWALogger(log, cfg.WALogLevel, "store"))
	if err != nil {
		log.Error("cannot open session store", "error", err)
		os.Exit(1)
	}
	_ = os.Chmod(dbPath, 0o600)

	g := NewGateway(cfg, log, container)
	log.Info("voksy wa-call gateway starting", "version", Version, "gateway_id", cfg.GatewayID,
		"backend", cfg.BackendWS, "max_calls", cfg.MaxCalls, "echo_test", cfg.EchoTest)
	g.LoadAccounts(ctx)
	if !cfg.EchoTest {
		go g.ctl.Run(ctx)
	}
	if cfg.RecordCalls && !cfg.EchoTest {
		go g.uploader.SweepLoop(ctx, g.liveCallIDs)
	}
	if cfg.EchoTest {
		go g.echoLogin(ctx)
	}
	go g.serveHealth(ctx)

	<-ctx.Done()
	log.Info("shutting down")
	g.Shutdown()
}
