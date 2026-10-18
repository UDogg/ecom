package server

import (
	"math"
	"sync"
	"time"
)

// limiter is a token bucket per key (an IP, a session, a user). It is
// in-memory and per replica, which is fine for abuse control; volumetric
// floods belong to the CDN or firewall in front of the BFF.
type limiter struct {
	perMinute int
	now       func() time.Time

	mu      sync.Mutex
	buckets map[string]*bucket
}

type bucket struct {
	tokens float64
	last   time.Time
}

func newLimiter(perMinute int, now func() time.Time) *limiter {
	return &limiter{perMinute: perMinute, now: now, buckets: map[string]*bucket{}}
}

// allow takes one token; when the bucket is empty it says how long to wait.
func (l *limiter) allow(key string) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	rate := float64(l.perMinute) / 60
	b, ok := l.buckets[key]
	if !ok {
		if len(l.buckets) > 200_000 {
			l.sweepLocked(now)
		}
		b = &bucket{tokens: float64(l.perMinute), last: now}
		l.buckets[key] = b
	}
	b.tokens = math.Min(float64(l.perMinute), b.tokens+now.Sub(b.last).Seconds()*rate)
	b.last = now
	if b.tokens >= 1 {
		b.tokens--
		return true, 0
	}
	return false, time.Duration((1-b.tokens)/rate*float64(time.Second)) + time.Second
}

func (l *limiter) sweepLocked(now time.Time) {
	for k, b := range l.buckets {
		if now.Sub(b.last) > 10*time.Minute {
			delete(l.buckets, k)
		}
	}
}
