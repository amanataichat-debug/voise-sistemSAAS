package main

// Запись звонка: обе стороны сводятся в один моно WAV 16 кГц → MP3 (lame) →
// POST {BACKEND_HTTP_URL}/api/sip/recordings/{call_id} (заголовок X-Gateway-Token) →
// бэкенд кладёт в R2 и ставит sip_calls.recording_url — тот же путь, что у SIP-моста.
//
// Часы записи — цикл отправки звука в WhatsApp (ровно раз в 60 мс, кадр агента или
// тишины). На каждый его кадр берём столько же звука собеседника из буфера: стороны
// не расходятся во времени, а буфер собеседника ограничен, чтобы задержка не копилась.
// Файл удаляется только после того, как бэкенд его принял (или сказал, что звонка нет).

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

const (
	recMinBytes      = 44 + LineRate*2 // меньше секунды звука — нечего хранить
	recCallerMaxBuf  = 5 * WAFrameSamples
	recSettleSeconds = 120
)

// CallRecorder пишет сведённый звонок в WAV.
type CallRecorder struct {
	mu      sync.Mutex
	f       *os.File
	w       *bufio.Writer
	path    string
	dataLen uint32
	caller  []float32
	closed  bool
	scratch []byte
}

// NewCallRecorder открывает <dir>/<callID>.wav.
func NewCallRecorder(dir, callID string) (*CallRecorder, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, callID+".wav")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	r := &CallRecorder{f: f, w: bufio.NewWriterSize(f, 64*1024), path: path}
	if _, err := r.w.Write(wavHeader(0)); err != nil {
		f.Close()
		return nil, err
	}
	return r, nil
}

func wavHeader(dataLen uint32) []byte {
	h := make([]byte, 44)
	copy(h[0:4], "RIFF")
	binary.LittleEndian.PutUint32(h[4:8], 36+dataLen)
	copy(h[8:12], "WAVE")
	copy(h[12:16], "fmt ")
	binary.LittleEndian.PutUint32(h[16:20], 16)
	binary.LittleEndian.PutUint16(h[20:22], 1) // PCM
	binary.LittleEndian.PutUint16(h[22:24], 1) // моно
	binary.LittleEndian.PutUint32(h[24:28], LineRate)
	binary.LittleEndian.PutUint32(h[28:32], LineRate*2)
	binary.LittleEndian.PutUint16(h[32:34], 2)
	binary.LittleEndian.PutUint16(h[34:36], 16)
	copy(h[36:40], "data")
	binary.LittleEndian.PutUint32(h[40:44], dataLen)
	return h
}

// Caller — кадр собеседника (из Sink).
func (r *CallRecorder) Caller(frame []float32) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return
	}
	r.caller = append(r.caller, frame...)
	if over := len(r.caller) - recCallerMaxBuf; over > 0 {
		r.caller = r.caller[over:]
	}
}

// Agent — кадр агента (звук или тишина), часы записи.
func (r *CallRecorder) Agent(frame []float32) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return
	}
	mixed := make([]float32, len(frame))
	n := len(frame)
	if n > len(r.caller) {
		n = len(r.caller)
	}
	for i := range frame {
		v := frame[i]
		if i < n {
			v += r.caller[i]
		}
		// мягкое насыщение суммы двух голосов
		if v > 1 {
			v = 1
		} else if v < -1 {
			v = -1
		}
		mixed[i] = v
	}
	r.caller = r.caller[n:]
	r.scratch = FloatToPCM16(r.scratch, mixed)
	if _, err := r.w.Write(r.scratch); err == nil {
		r.dataLen += uint32(len(r.scratch))
	}
}

// Close дописывает заголовок и закрывает файл. Возвращает путь (пусто — запись пустая и удалена).
func (r *CallRecorder) Close() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return ""
	}
	r.closed = true
	_ = r.w.Flush()
	if _, err := r.f.Seek(0, io.SeekStart); err == nil {
		_, _ = r.f.Write(wavHeader(r.dataLen))
	}
	_ = r.f.Close()
	if 44+int(r.dataLen) < recMinBytes {
		_ = os.Remove(r.path)
		return ""
	}
	return r.path
}

// RecordingUploader — WAV → MP3 → бэкенд, с повтором по таймеру (деплой бэкенда ничего не теряет).
type RecordingUploader struct {
	cfg    Config
	log    *slog.Logger
	client *http.Client
	mu     sync.Mutex
	busy   map[string]bool
	sem    chan struct{}
}

// NewRecordingUploader создаёт загрузчик.
func NewRecordingUploader(cfg Config, log *slog.Logger) *RecordingUploader {
	return &RecordingUploader{
		cfg: cfg, log: log,
		client: &http.Client{Timeout: 180 * time.Second},
		busy:   map[string]bool{},
		sem:    make(chan struct{}, 2),
	}
}

func validCallID(s string) (string, bool) {
	id, err := uuid.Parse(s)
	if err != nil {
		return "", false
	}
	return id.String(), true
}

// Submit — запись звонка закрыта, отправить в фоне.
func (u *RecordingUploader) Submit(callID string) {
	go u.process(callID)
}

func (u *RecordingUploader) process(callID string) {
	callID, ok := validCallID(callID)
	if !ok {
		return
	}
	u.mu.Lock()
	if u.busy[callID] {
		u.mu.Unlock()
		return
	}
	u.busy[callID] = true
	u.mu.Unlock()
	defer func() {
		u.mu.Lock()
		delete(u.busy, callID)
		u.mu.Unlock()
	}()
	u.sem <- struct{}{}
	defer func() { <-u.sem }()

	wav := filepath.Join(u.cfg.RecordingsDir, callID+".wav")
	mp3 := filepath.Join(u.cfg.RecordingsDir, callID+".mp3")
	if st, err := os.Stat(wav); err == nil {
		if st.Size() < recMinBytes {
			removeFiles(wav, mp3)
			return
		}
		if _, err := os.Stat(mp3); err != nil && u.encode(wav, mp3) {
			removeFiles(wav)
		}
	}
	path, ctype := mp3, "audio/mpeg"
	if _, err := os.Stat(mp3); err != nil {
		if _, err := os.Stat(wav); err != nil {
			return
		}
		path, ctype = wav, "audio/wav"
	}
	status, detail := u.upload(callID, path, ctype)
	switch {
	case status == http.StatusOK:
		u.log.Info("recording uploaded", "call_id", callID)
		removeFiles(wav, mp3)
	case status == http.StatusNotFound && detail == "call_not_found":
		u.log.Warn("recording: backend does not know this call, dropped", "call_id", callID)
		removeFiles(wav, mp3)
	default:
		u.log.Warn("recording upload failed, will retry", "call_id", callID, "status", status, "detail", detail)
	}
}

func (u *RecordingUploader) encode(wav, mp3 string) bool {
	tmp := mp3 + ".tmp"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "lame", "--quiet", "-m", "m", "-b", u.cfg.RecordingBitrate, wav, tmp)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		u.log.Warn("lame failed, uploading WAV", "error", err, "stderr", strings.TrimSpace(stderr.String()))
		removeFiles(tmp)
		return false
	}
	return os.Rename(tmp, mp3) == nil
}

func (u *RecordingUploader) upload(callID, path, ctype string) (int, string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return 0, err.Error()
	}
	req, err := http.NewRequest(http.MethodPost, u.cfg.BackendHTTP+"/api/sip/recordings/"+callID, bytes.NewReader(data))
	if err != nil {
		return 0, err.Error()
	}
	req.Header.Set("X-Gateway-Token", u.cfg.Token)
	req.Header.Set("X-Gateway-Id", u.cfg.GatewayID)
	req.Header.Set("Content-Type", ctype)
	resp, err := u.client.Do(req)
	if err != nil {
		return 0, err.Error()
	}
	defer resp.Body.Close()
	var body struct {
		Detail any `json:"detail"`
	}
	_ = json.NewDecoder(io.LimitReader(resp.Body, 64*1024)).Decode(&body)
	return resp.StatusCode, fmt.Sprint(valueOr(body.Detail, ""))
}

func valueOr(v any, def string) any {
	if v == nil {
		return def
	}
	return v
}

// SweepLoop раз в минуту повторяет неотправленные записи и удаляет слишком старые.
func (u *RecordingUploader) SweepLoop(ctx context.Context, active func() map[string]bool) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		entries, err := os.ReadDir(u.cfg.RecordingsDir)
		if err != nil {
			continue
		}
		live := active()
		for _, e := range entries {
			name := e.Name()
			info, err := e.Info()
			if err != nil {
				continue
			}
			full := filepath.Join(u.cfg.RecordingsDir, name)
			age := time.Since(info.ModTime())
			if age > time.Duration(u.cfg.RecordingKeepDays)*24*time.Hour {
				u.log.Warn("recording not uploaded for too long, deleted", "file", name)
				removeFiles(full)
				continue
			}
			if strings.HasSuffix(name, ".tmp") {
				if age > 10*time.Minute {
					removeFiles(full)
				}
				continue
			}
			id, ok := validCallID(strings.SplitN(name, ".", 2)[0])
			if !ok || live[id] || age < recSettleSeconds*time.Second {
				continue
			}
			u.process(id)
		}
	}
}

// Pending — сколько файлов ждут отправки.
func (u *RecordingUploader) Pending() int {
	entries, err := os.ReadDir(u.cfg.RecordingsDir)
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".wav") || strings.HasSuffix(e.Name(), ".mp3") {
			n++
		}
	}
	return n
}

func removeFiles(paths ...string) {
	for _, p := range paths {
		_ = os.Remove(p)
	}
}
