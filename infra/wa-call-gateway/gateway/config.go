package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config — настройки шлюза из окружения (/etc/voksy-wa-call/gateway.env, см. gateway.env.example).
type Config struct {
	GatewayID   string
	Token       string // = WA_CALL_GATEWAY_TOKEN на бэкенде
	BackendWS   string // wss://voksyai.online
	BackendHTTP string // https://voksyai.online (запись звонков)

	DataDir string // сессии WhatsApp (SQLite) и список аккаунтов, права 600

	MaxCalls        int           // одновременных звонков на шлюз; сверх — входящий отклоняется
	MaxCallSeconds  int           // сторож длины звонка
	DecisionTimeout time.Duration // сколько ждать accept/reject бэкенда на входящий
	RingTimeout     time.Duration // исходящий: сколько ждать ответа
	MediaReadyWait  time.Duration // после «взял трубку»: сколько ждать живого звука, прежде чем открыть медиа-сокет так

	EchoTest bool // проба без бэкенда: шлюз берёт трубку сам и повторяет голос звонящего

	// SendPresence — объявлять устройство «в сети» после подключения. По умолчанию нет: пока любое
	// устройство «в сети», телефон номера не получает push-уведомлений. Включать, только если на пробе
	// окажется, что без этого WhatsApp не доставляет звонки связанному устройству.
	SendPresence bool

	RecordCalls       bool
	RecordingsDir     string
	RecordingBitrate  string
	RecordingKeepDays int

	DeviceName string // имя связанного устройства в WhatsApp на телефоне
	HealthAddr string // локальный HTTP со здоровьем шлюза
	LogLevel   string
	WALogLevel string // логи протокола WhatsApp (по умолчанию только предупреждения)
}

func env(name, def string) string {
	if v, ok := os.LookupEnv(name); ok && strings.TrimSpace(v) != "" {
		return strings.TrimSpace(v)
	}
	return def
}

func envInt(name string, def int) int {
	if v, err := strconv.Atoi(env(name, "")); err == nil {
		return v
	}
	return def
}

func envBool(name string, def bool) bool {
	switch strings.ToLower(env(name, "")) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	}
	return def
}

// LoadConfig читает окружение. Ошибка — нет обязательной настройки.
func LoadConfig() (Config, error) {
	ws := strings.TrimRight(env("BACKEND_WS_URL", "wss://voksyai.online"), "/")
	httpURL := strings.TrimRight(env("BACKEND_HTTP_URL", ""), "/")
	if httpURL == "" {
		httpURL = strings.Replace(strings.Replace(ws, "wss://", "https://", 1), "ws://", "http://", 1)
	}
	cfg := Config{
		GatewayID:         env("GATEWAY_ID", "wa-call-1"),
		Token:             env("GATEWAY_TOKEN", ""),
		BackendWS:         ws,
		BackendHTTP:       httpURL,
		DataDir:           env("DATA_DIR", "/opt/voksy-wa-call/data"),
		MaxCalls:          envInt("MAX_CALLS", 10),
		MaxCallSeconds:    envInt("MAX_CALL_SECONDS", 3600),
		DecisionTimeout:   time.Duration(envInt("ACCEPT_TIMEOUT_MS", 6000)) * time.Millisecond,
		RingTimeout:       time.Duration(envInt("OUTBOUND_RING_SECONDS", 45)) * time.Second,
		MediaReadyWait:    time.Duration(envInt("MEDIA_READY_WAIT_MS", 4000)) * time.Millisecond,
		EchoTest:          envBool("ECHO_TEST", false),
		SendPresence:      envBool("SEND_PRESENCE", false),
		RecordCalls:       envBool("RECORD_CALLS", true),
		RecordingsDir:     env("RECORDINGS_DIR", "/var/spool/voksy-wa-call-rec"),
		RecordingBitrate:  env("RECORDING_BITRATE_KBPS", "32"),
		RecordingKeepDays: envInt("RECORDING_KEEP_DAYS", 7),
		DeviceName:        env("DEVICE_NAME", "Voksy AI"),
		HealthAddr:        env("HEALTH_ADDR", "127.0.0.1:9095"),
		LogLevel:          strings.ToUpper(env("LOG_LEVEL", "INFO")),
		WALogLevel:        strings.ToUpper(env("WA_LOG_LEVEL", "WARN")),
	}
	if cfg.Token == "" && !cfg.EchoTest {
		return cfg, fmt.Errorf("GATEWAY_TOKEN is required (equals WA_CALL_GATEWAY_TOKEN on the backend)")
	}
	if cfg.MaxCalls < 1 {
		cfg.MaxCalls = 1
	}
	return cfg, nil
}
