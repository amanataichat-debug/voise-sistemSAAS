package main

// Логи — в journald (stdout), без содержимого разговора: только события звонков и
// аккаунтов, номера в логах замаскированы (maskPhone).

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"

	waLog "github.com/polymorfa/hypermeow/util/log"
	"github.com/rs/zerolog"
)

func newLogger(level string) *slog.Logger {
	return slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: parseLevel(level)}))
}

func parseLevel(level string) slog.Level {
	switch strings.ToUpper(level) {
	case "DEBUG":
		return slog.LevelDebug
	case "WARN", "WARNING":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	}
	return slog.LevelInfo
}

// waLogger — логгер протокола WhatsApp поверх slog.
type waLogger struct {
	log    *slog.Logger
	level  slog.Level
	module string
}

func newWALogger(base *slog.Logger, level, module string) waLog.Logger {
	return &waLogger{log: base, level: parseLevel(level), module: module}
}

func (l *waLogger) emit(lv slog.Level, msg string, args ...any) {
	if lv < l.level {
		return
	}
	l.log.Log(context.Background(), lv, fmt.Sprintf(msg, args...), "module", l.module)
}

func (l *waLogger) Warnf(msg string, args ...any)  { l.emit(slog.LevelWarn, msg, args...) }
func (l *waLogger) Errorf(msg string, args ...any) { l.emit(slog.LevelError, msg, args...) }
func (l *waLogger) Infof(msg string, args ...any)  { l.emit(slog.LevelInfo, msg, args...) }
func (l *waLogger) Debugf(msg string, args ...any) { l.emit(slog.LevelDebug, msg, args...) }
func (l *waLogger) Sub(module string) waLog.Logger {
	return &waLogger{log: l.log, level: l.level, module: l.module + "/" + module}
}

// newCallLogger — логгер библиотеки звонков (zerolog) того же уровня.
func newCallLogger(level string) zerolog.Logger {
	lv := zerolog.WarnLevel
	switch strings.ToUpper(level) {
	case "DEBUG":
		lv = zerolog.DebugLevel
	case "INFO":
		lv = zerolog.InfoLevel
	case "ERROR":
		lv = zerolog.ErrorLevel
	}
	var w io.Writer = zerolog.ConsoleWriter{Out: os.Stdout, NoColor: true, TimeFormat: "15:04:05"}
	return zerolog.New(w).Level(lv).With().Timestamp().Str("module", "calls").Logger()
}
