package main

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func tone(n int, amp float64, freq float64) []float32 {
	out := make([]float32, n)
	for i := range out {
		out[i] = float32(amp * math.Sin(2*math.Pi*freq*float64(i)/LineRate))
	}
	return out
}

func TestPCMRoundTrip(t *testing.T) {
	src := tone(960, 0.5, 440)
	pcm := FloatToPCM16(nil, src)
	if len(pcm) != 1920 {
		t.Fatalf("pcm len %d", len(pcm))
	}
	back := PCM16ToFloat(pcm)
	for i := range src {
		if d := math.Abs(float64(src[i] - back[i])); d > 1.0/32768*1.5 {
			t.Fatalf("sample %d differs by %g", i, d)
		}
	}
	// насыщение вместо переполнения
	sat := PCM16ToFloat(FloatToPCM16(nil, []float32{2, -2}))
	if sat[0] < 0.99 || sat[1] > -0.99 {
		t.Fatalf("no saturation: %v", sat)
	}
}

func TestFramerMakes20msFrames(t *testing.T) {
	var f Framer
	got := f.Push(tone(WAFrameSamples, 0.3, 300)) // 60 мс → три кадра по 20 мс
	if len(got) != 3 {
		t.Fatalf("want 3 frames, got %d", len(got))
	}
	for _, fr := range got {
		if len(fr) != LineFrameBytes || LineFrameBytes != 640 {
			t.Fatalf("frame %d bytes", len(fr))
		}
	}
	if len(f.Push(tone(100, 0.3, 300))) != 0 { // кусок меньше кадра ждёт продолжения
		t.Fatal("partial chunk must wait")
	}
}

type fakeClock struct{ t time.Time }

func (c *fakeClock) now() time.Time      { return c.t }
func (c *fakeClock) add(d time.Duration) { c.t = c.t.Add(d) }
func newQueue(onMark func(string)) (*PlayoutQueue, *fakeClock) {
	clk := &fakeClock{t: time.Unix(1_700_000_000, 0)}
	q := NewPlayoutQueue(onMark)
	q.now = clk.now
	return q, clk
}

func TestQueueNeverBlocksAndSendsSilence(t *testing.T) {
	q, _ := newQueue(nil)
	done := make(chan struct{})
	go func() {
		for i := 0; i < 50; i++ {
			f, err := q.ReadFrame()
			if err != nil || len(f) != WAFrameSamples || RMS(f) != 0 {
				t.Errorf("empty queue must give a silence frame, err=%v len=%d", err, len(f))
			}
		}
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("ReadFrame blocked on an empty queue")
	}
}

func TestQueuePlaysInOrderAndKeepsPace(t *testing.T) {
	q, _ := newQueue(nil)
	// бэкенд прислал 1 с звука одним куском (Gemini шлёт быстрее реального времени)
	q.Push(FloatToPCM16(nil, tone(LineRate, 0.4, 500)))
	if q.Queued() != time.Second {
		t.Fatalf("queued %v", q.Queued())
	}
	frames := 0
	for {
		f, _ := q.ReadFrame()
		if RMS(f) == 0 {
			break
		}
		frames++
		if frames > 100 {
			t.Fatal("queue never drains")
		}
	}
	// 1 с = 16 полных кадров по 60 мс + хвост 40 мс, который ждёт продолжения tailFlushAfter
	if frames != 16 {
		t.Fatalf("one ReadFrame = one 60 ms frame (pace is the library ticker); frames=%d", frames)
	}
}

func TestQueueTailFlush(t *testing.T) {
	q, clk := newQueue(nil)
	q.Push(FloatToPCM16(nil, tone(400, 0.4, 500))) // 25 мс — меньше кадра
	if f, _ := q.ReadFrame(); RMS(f) != 0 {
		t.Fatal("short tail must wait for continuation first")
	}
	clk.add(tailFlushAfter + time.Millisecond)
	f, _ := q.ReadFrame()
	if RMS(f) == 0 {
		t.Fatal("tail was not flushed")
	}
	if RMS(f[400:]) != 0 {
		t.Fatal("tail must be padded with silence")
	}
	if q.Queued() != 0 {
		t.Fatal("queue must be empty")
	}
}

func TestQueueClearDropsAudioAndMarks(t *testing.T) {
	var mu sync.Mutex
	var fired []string
	q, _ := newQueue(func(n string) { mu.Lock(); fired = append(fired, n); mu.Unlock() })
	q.Push(FloatToPCM16(nil, tone(LineRate*3, 0.4, 500)))
	q.Mark("before-clear")
	dropped := q.Clear()
	if dropped != 3*time.Second {
		t.Fatalf("dropped %v", dropped)
	}
	if f, _ := q.ReadFrame(); RMS(f) != 0 {
		t.Fatal("audio after clear")
	}
	if len(fired) != 0 {
		t.Fatalf("mark before clear must be dropped like the SIP bridge, fired %v", fired)
	}
}

func TestQueueMarkFiresWhenPlaybackReachesIt(t *testing.T) {
	var fired []string
	q, _ := newQueue(func(n string) { fired = append(fired, n) })
	q.Push(FloatToPCM16(nil, tone(WAFrameSamples*3, 0.4, 500))) // 180 мс
	q.Mark("hangup")
	for i := 0; i < 2; i++ {
		q.ReadFrame()
		if len(fired) != 0 {
			t.Fatalf("mark fired early after frame %d", i+1)
		}
	}
	q.ReadFrame()
	if len(fired) != 1 || fired[0] != "hangup" {
		t.Fatalf("mark must fire with the last frame, got %v", fired)
	}
	// метка на пустой очереди — со следующим кадром
	q.Mark("now")
	q.ReadFrame()
	if len(fired) != 2 || fired[1] != "now" {
		t.Fatalf("mark on empty queue: %v", fired)
	}
}

func TestQueueUnderrunHeuristic(t *testing.T) {
	q, clk := newQueue(nil)
	q.Push(FloatToPCM16(nil, tone(WAFrameSamples, 0.4, 500)))
	q.ReadFrame() // очередь опустела после звука
	clk.add(100 * time.Millisecond)
	q.Push(FloatToPCM16(nil, tone(WAFrameSamples, 0.4, 500))) // продолжение через 100 мс — провал
	if q.UnderrunCount() != 1 {
		t.Fatalf("underruns %d", q.UnderrunCount())
	}
	q.ReadFrame()
	clk.add(2 * time.Second)
	q.Push(FloatToPCM16(nil, tone(WAFrameSamples, 0.4, 500))) // новая реплика через 2 с — не провал
	if q.UnderrunCount() != 1 {
		t.Fatalf("pause between replies counted as underrun: %d", q.UnderrunCount())
	}
}

func TestQueueBoundedMemory(t *testing.T) {
	q, _ := newQueue(nil)
	chunk := FloatToPCM16(nil, tone(LineRate*10, 0.1, 200))
	for i := 0; i < 30; i++ { // 300 с при потолке 180 с
		q.Push(chunk)
	}
	if q.Queued() > maxQueueSeconds*time.Second {
		t.Fatalf("queue grew to %v", q.Queued())
	}
}

func TestResamplerRatesAndContinuity(t *testing.T) {
	r := NewResampler(16000, 8000)
	total := 0
	for i := 0; i < 10; i++ {
		total += len(r.Process(tone(960, 0.3, 300)))
	}
	if total < 4790 || total > 4810 {
		t.Fatalf("16k→8k: %d samples for 9600 in", total)
	}
	up := NewResampler(8000, 16000)
	total = 0
	for i := 0; i < 10; i++ {
		total += len(up.Process(tone(480, 0.3, 300)))
	}
	if total < 9590 || total > 9610 {
		t.Fatalf("8k→16k: %d samples for 4800 in", total)
	}
	same := NewResampler(16000, 16000)
	in := tone(960, 0.3, 300)
	if out := same.Process(in); len(out) != len(in) {
		t.Fatal("equal rates must pass through")
	}
}

func TestRecorderMixesBothSides(t *testing.T) {
	dir := t.TempDir()
	rec, err := NewCallRecorder(dir, "11111111-2222-4333-8444-555555555555")
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 25; i++ { // 1,5 с
		rec.Caller(tone(WAFrameSamples, 0.2, 300))
		rec.Agent(tone(WAFrameSamples, 0.2, 700))
	}
	path := rec.Close()
	if path == "" {
		t.Fatal("recording dropped")
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := int64(44 + 25*WAFrameSamples*2); st.Size() != want {
		t.Fatalf("wav size %d, want %d", st.Size(), want)
	}
	if mode := st.Mode().Perm(); mode != 0o600 {
		t.Fatalf("recording must be 600, got %o", mode)
	}
	// слишком короткая запись не хранится
	short, _ := NewCallRecorder(dir, "99999999-2222-4333-8444-555555555555")
	short.Agent(tone(WAFrameSamples, 0.2, 700))
	if p := short.Close(); p != "" {
		t.Fatal("short recording must be dropped")
	}
	if _, err := os.Stat(filepath.Join(dir, "99999999-2222-4333-8444-555555555555.wav")); !os.IsNotExist(err) {
		t.Fatal("short recording file left on disk")
	}
}

func TestRecorderCallerBufferBounded(t *testing.T) {
	rec, _ := NewCallRecorder(t.TempDir(), "11111111-2222-4333-8444-555555555556")
	for i := 0; i < 100; i++ { // собеседник пришёл пачкой, а часы записи стоят
		rec.Caller(tone(WAFrameSamples, 0.2, 300))
	}
	rec.mu.Lock()
	n := len(rec.caller)
	rec.mu.Unlock()
	if n > recCallerMaxBuf {
		t.Fatalf("caller buffer %d > %d", n, recCallerMaxBuf)
	}
	rec.Close()
}

func TestMapEndReason(t *testing.T) {
	cases := []struct {
		dir      string
		answered bool
		raw      string
		want     string
	}{
		{"inbound", false, "accepted_elsewhere", "accepted_elsewhere"},
		{"outbound", false, "rejected", "rejected"},
		{"outbound", false, "timeout", "no_answer"},
		{"outbound", true, "", "wa_hangup"},
		{"inbound", false, "", "caller_hangup"},
		{"outbound", false, "", "failed"},
	}
	for _, c := range cases {
		if got := mapEndReason(c.dir, c.answered, c.raw); got != c.want {
			t.Errorf("mapEndReason(%s,%v,%q)=%q want %q", c.dir, c.answered, c.raw, got, c.want)
		}
	}
}

func TestMaskPhone(t *testing.T) {
	if got := maskPhone("996555123456"); got != "••••••••3456" {
		t.Fatalf("mask %q", got)
	}
}

func TestRedactToken(t *testing.T) {
	err := fmt.Errorf("Get \"https://x/ws?gateway=g&token=SECRET123\": context deadline exceeded")
	if got := redact(err, "SECRET123"); strings.Contains(got, "SECRET123") {
		t.Fatalf("token leaked: %s", got)
	}
}
