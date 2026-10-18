package server

import (
	"bytes"
	"net/http"
	"sync"
	"time"
)

// microCache keeps successful answers of public, anonymous GET endpoints (the
// catalog) for a moment, and lets concurrent identical requests share one
// upstream call. A popular page under load then costs the catalog one query
// per second instead of one per visitor; nothing user-specific is ever stored
// (only endpoints with Auth public and no session token use it).
type microCache struct {
	mu       sync.Mutex
	entries  map[string]*cached
	inflight map[string]*flight
	max      int
	now      func() time.Time
}

type cached struct {
	status  int
	header  http.Header
	body    []byte
	expires time.Time
}

type flight struct {
	done chan struct{}
	res  *cached // nil when the leader's answer was not cacheable
}

func newMicroCache(max int, now func() time.Time) *microCache {
	return &microCache{entries: map[string]*cached{}, inflight: map[string]*flight{}, max: max, now: now}
}

// serve answers from the cache, waits for an identical request in flight, or
// runs fill and caches a 200 for ttl.
func (c *microCache) serve(w http.ResponseWriter, key string, ttl time.Duration, fill func(http.ResponseWriter)) {
	c.mu.Lock()
	if e := c.entries[key]; e != nil && c.now().Before(e.expires) {
		c.mu.Unlock()
		e.write(w, "HIT")
		return
	}
	if f := c.inflight[key]; f != nil {
		c.mu.Unlock()
		<-f.done
		if f.res != nil {
			f.res.write(w, "SHARED")
			return
		}
		fill(w) // the leader got an error: try for ourselves
		return
	}
	f := &flight{done: make(chan struct{})}
	c.inflight[key] = f
	c.mu.Unlock()

	rec := &recorder{header: http.Header{}, status: http.StatusOK}
	defer func() {
		c.mu.Lock()
		delete(c.inflight, key)
		if f.res != nil {
			if len(c.entries) >= c.max {
				c.evict()
			}
			c.entries[key] = f.res
		}
		c.mu.Unlock()
		close(f.done)
	}()
	fill(rec)
	res := &cached{status: rec.status, header: rec.header, body: rec.body.Bytes(), expires: c.now().Add(ttl)}
	if rec.status == http.StatusOK && rec.header.Get("X-Degraded") == "" {
		f.res = res
	}
	res.write(w, "MISS")
}

// evict drops expired entries, or everything when none has expired (bounded memory, simply).
func (c *microCache) evict() {
	now := c.now()
	for k, e := range c.entries {
		if now.After(e.expires) {
			delete(c.entries, k)
		}
	}
	if len(c.entries) >= c.max {
		clear(c.entries)
	}
}

func (e *cached) write(w http.ResponseWriter, state string) {
	h := w.Header()
	for k, v := range e.header {
		h[k] = v
	}
	h.Set("X-Cache", state)
	w.WriteHeader(e.status)
	w.Write(e.body)
}

// recorder buffers a response.
type recorder struct {
	header http.Header
	status int
	body   bytes.Buffer
	wrote  bool
}

func (r *recorder) Header() http.Header { return r.header }

func (r *recorder) WriteHeader(code int) {
	if !r.wrote {
		r.status, r.wrote = code, true
	}
}

func (r *recorder) Write(b []byte) (int, error) {
	r.wrote = true
	return r.body.Write(b)
}
