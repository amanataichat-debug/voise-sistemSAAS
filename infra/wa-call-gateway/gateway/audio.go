package main

// Звук звонка: перевод между форматом библиотеки звонков (float32, 16 кГц, кадры 60 мс)
// и форматом бэкенда (PCM16 LE, 16 кГц, моно, кадры 20 мс = 640 байт), как у SIP-моста.
//
//   звук собеседника: Sink (float32, 60 мс) → Framer → 3 кадра PCM16 по 20 мс → бэкенд
//   звук агента:      бэкенд (PCM16 любыми кусками) → PlayoutQueue → ReadFrame раз в 60 мс → WhatsApp
//
// Темп реального времени задаёт сама библиотека: её цикл отправки раз в 60 мс забирает
// ОДИН кадр из очереди и никогда не ждёт. Поэтому очередь отдаёт кадр сразу: звук есть —
// кадр звука, звука нет — кадр тишины (поток к WhatsApp не прерывается).

import (
	"encoding/binary"
	"math"
	"sync"
	"time"
)

const (
	// LineRate — частота звука между шлюзом и бэкендом.
	LineRate = 16000
	// LineFrameMs / LineFrameBytes — кадр к бэкенду: 20 мс PCM16 моно.
	LineFrameMs    = 20
	LineFrameBytes = LineRate * 2 * LineFrameMs / 1000 // 640
	// WAFrameSamples — кадр библиотеки звонков: 60 мс при 16 кГц.
	WAFrameSamples = 960
)

// tailFlushAfter — хвост реплики короче кадра ждёт продолжения не дольше этого, потом
// уходит, дополненный тишиной (иначе последние слова звучали бы только со следующей репликой).
const tailFlushAfter = 120 * time.Millisecond

// underrunGap — продолжение реплики, пришедшее быстрее этого после опустевшей очереди,
// считается провалом звука (в журнал), а не новой репликой.
const underrunGap = 400 * time.Millisecond

// maxQueueSeconds — потолок очереди воспроизведения (защита памяти, а не настройка звука).
const maxQueueSeconds = 180

// FloatToPCM16 пишет float32 [-1, 1) как PCM16 LE с насыщением.
func FloatToPCM16(dst []byte, src []float32) []byte {
	need := len(src) * 2
	if cap(dst) < need {
		dst = make([]byte, need)
	}
	dst = dst[:need]
	for i, s := range src {
		v := float64(s) * 32768.0
		if v > 32767 {
			v = 32767
		} else if v < -32768 {
			v = -32768
		}
		binary.LittleEndian.PutUint16(dst[2*i:], uint16(int16(math.Round(v))))
	}
	return dst
}

// PCM16ToFloat читает PCM16 LE в float32 [-1, 1). Нечётный последний байт отбрасывается.
func PCM16ToFloat(src []byte) []float32 {
	n := len(src) / 2
	out := make([]float32, n)
	for i := 0; i < n; i++ {
		out[i] = float32(int16(binary.LittleEndian.Uint16(src[2*i:]))) / 32768.0
	}
	return out
}

// Resampler — потоковый линейный ресемплер float32 моно. Нужен только если частота
// библиотеки звонков когда-нибудь станет отличаться от LineRate (сейчас обе 16 кГц и
// звук идёт без пересчёта). Состояние между кусками сохраняется — без щелчков на стыках.
type Resampler struct {
	from, to int
	pos      float64 // позиция следующего выходного отсчёта во входном потоке (относительно last)
	last     float32
	primed   bool
}

// NewResampler создаёт ресемплер from → to Гц.
func NewResampler(from, to int) *Resampler { return &Resampler{from: from, to: to} }

// Process пересчитывает очередной кусок.
func (r *Resampler) Process(in []float32) []float32 {
	if r.from == r.to || len(in) == 0 {
		return in
	}
	step := float64(r.from) / float64(r.to)
	// Поток = [last, in...]; индекс 0 — последний отсчёт прошлого куска.
	src := make([]float32, 0, len(in)+1)
	if r.primed {
		src = append(src, r.last)
	} else {
		src = append(src, in[0])
		r.primed = true
	}
	src = append(src, in...)
	out := make([]float32, 0, int(float64(len(in))/step)+2)
	for r.pos <= float64(len(src)-1) {
		i := int(r.pos)
		frac := float32(r.pos - float64(i))
		var v float32
		if i+1 < len(src) {
			v = src[i]*(1-frac) + src[i+1]*frac
		} else {
			v = src[i]
		}
		out = append(out, v)
		r.pos += step
	}
	r.pos -= float64(len(src) - 1)
	r.last = src[len(src)-1]
	return out
}

// Framer режет звук собеседника на кадры PCM16 по 20 мс.
type Framer struct {
	buf []byte
}

// Push добавляет кадр библиотеки и возвращает готовые кадры по LineFrameBytes.
func (f *Framer) Push(frame []float32) [][]byte {
	f.buf = append(f.buf, FloatToPCM16(nil, frame)...)
	var out [][]byte
	for len(f.buf) >= LineFrameBytes {
		chunk := make([]byte, LineFrameBytes)
		copy(chunk, f.buf[:LineFrameBytes])
		out = append(out, chunk)
		f.buf = f.buf[LineFrameBytes:]
	}
	return out
}

type mark struct {
	at   int64 // в отсчётах от начала звонка
	name string
}

// PlayoutQueue — очередь звука агента. Реализует AudioSource библиотеки звонков
// (ReadFrame / Close) и протокол SIP-моста: Push (звук), Clear (перебивание), Mark
// (метка возвращается, когда проигрывание до неё дошло).
type PlayoutQueue struct {
	mu       sync.Mutex
	buf      []float32
	marks    []mark
	played   int64 // сколько отсчётов звука агента уже ушло в звонок
	lastPush time.Time
	// Очередь опустела после звука: если продолжение приходит быстрее underrunGap, это был
	// провал посреди реплики (бэкенд не успел), а не пауза между репликами.
	emptySince time.Time
	closed     bool

	// Статистика для журнала
	Underruns    int     // очередь опустела посреди реплики (бэкенд не успел)
	FramesOut    int     // кадров со звуком
	PeakQueueSec float64 // самая длинная очередь

	now    func() time.Time
	onMark func(name string)
	onTick func(frame []float32) // каждый отданный кадр (звук или тишина) — для записи звонка
}

// NewPlayoutQueue создаёт очередь. onMark вызывается вне блокировки.
func NewPlayoutQueue(onMark func(string)) *PlayoutQueue {
	return &PlayoutQueue{onMark: onMark, now: time.Now}
}

// Push добавляет PCM16 16 кГц любого размера.
func (q *PlayoutQueue) Push(pcm []byte) {
	samples := PCM16ToFloat(pcm)
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed {
		return
	}
	now := q.now()
	if len(q.buf) == 0 && !q.emptySince.IsZero() && now.Sub(q.emptySince) < underrunGap {
		q.Underruns++
	}
	q.emptySince = time.Time{}
	q.buf = append(q.buf, samples...)
	if over := len(q.buf) - maxQueueSeconds*LineRate; over > 0 {
		q.buf = q.buf[over:]
		q.played += int64(over)
	}
	q.lastPush = now
	if sec := float64(len(q.buf)) / LineRate; sec > q.PeakQueueSec {
		q.PeakQueueSec = sec
	}
}

// Clear сбрасывает очередь и метки (перебивание), как мост SIP-шлюза. Возвращает
// длительность сброшенного звука.
func (q *PlayoutQueue) Clear() time.Duration {
	q.mu.Lock()
	defer q.mu.Unlock()
	dropped := len(q.buf)
	q.played += int64(dropped)
	q.buf = q.buf[:0]
	q.marks = q.marks[:0]
	q.emptySince = time.Time{}
	return time.Duration(dropped) * time.Second / LineRate
}

// Mark ставит метку в конец текущей очереди. Пустая очередь — метка срабатывает со
// следующим кадром.
func (q *PlayoutQueue) Mark(name string) {
	q.mu.Lock()
	q.marks = append(q.marks, mark{at: q.played + int64(len(q.buf)), name: name})
	q.mu.Unlock()
}

// UnderrunCount — число провалов звука агента за звонок.
func (q *PlayoutQueue) UnderrunCount() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.Underruns
}

// Queued — сколько звука ждёт проигрывания.
func (q *PlayoutQueue) Queued() time.Duration {
	q.mu.Lock()
	defer q.mu.Unlock()
	return time.Duration(len(q.buf)) * time.Second / LineRate
}

// SetTick задаёт наблюдателя за каждым отданным кадром (запись звонка).
func (q *PlayoutQueue) SetTick(fn func([]float32)) {
	q.mu.Lock()
	q.onTick = fn
	q.mu.Unlock()
}

// ReadFrame отдаёт ровно один кадр 60 мс и никогда не ждёт и не возвращает конец потока.
func (q *PlayoutQueue) ReadFrame() ([]float32, error) {
	frame := make([]float32, WAFrameSamples)
	q.mu.Lock()
	n := 0
	switch {
	case len(q.buf) >= WAFrameSamples:
		n = WAFrameSamples
	case len(q.buf) > 0 && q.now().Sub(q.lastPush) >= tailFlushAfter:
		n = len(q.buf) // хвост реплики — дополняем тишиной
	}
	if n > 0 {
		copy(frame, q.buf[:n])
		q.buf = q.buf[n:]
		q.played += int64(n)
		q.FramesOut++
		if len(q.buf) == 0 {
			q.emptySince = q.now()
		}
	}
	var fired []string
	for len(q.marks) > 0 && q.marks[0].at <= q.played {
		fired = append(fired, q.marks[0].name)
		q.marks = q.marks[1:]
	}
	onMark, onTick := q.onMark, q.onTick
	q.mu.Unlock()
	if onTick != nil {
		onTick(frame)
	}
	if onMark != nil {
		for _, name := range fired {
			onMark(name)
		}
	}
	return frame, nil
}

// Close закрывает очередь (библиотека зовёт его при смене источника).
func (q *PlayoutQueue) Close() error {
	q.mu.Lock()
	q.closed = true
	q.mu.Unlock()
	return nil
}

// RMS — уровень кадра (для эхо-теста и диагностики).
func RMS(frame []float32) float64 {
	if len(frame) == 0 {
		return 0
	}
	var s float64
	for _, v := range frame {
		s += float64(v) * float64(v)
	}
	return math.Sqrt(s / float64(len(frame)))
}
