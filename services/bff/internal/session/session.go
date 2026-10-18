// Package session keeps every token server-side. The browser only holds an
// opaque, random session id in an HttpOnly cookie.
//
// Sessions are indexed by the SHA-256 of their id, so the store (or a memory
// dump of it) never holds a usable cookie value. They expire on an idle
// timeout and on an absolute lifetime, and the id is rotated at sign-in to
// defeat session fixation. This store is in-memory: one BFF replica, or
// sticky sessions, until a shared store (Redis) is added.
package session

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"sync"
	"time"

	"babelbazaar/bff/internal/jwt"
)

var ErrFull = errors.New("session store full")

// PendingLogin is the state of an authorization-code flow in progress.
type PendingLogin struct {
	State    string
	Verifier string
	ReturnTo string
	Started  time.Time
}

// Session is one browser's server-side state.
type Session struct {
	CSRF         string
	AccessToken  string
	RefreshToken string
	IDToken      string
	AccessExpiry time.Time
	Claims       *jwt.Claims
	Pending      *PendingLogin

	created  time.Time
	lastSeen time.Time
	mu       sync.Mutex // serialises token refresh for this session
}

func (s *Session) SignedIn() bool { return s.Claims != nil && s.AccessToken != "" }

// Lock serialises work that must not run twice concurrently (token refresh).
func (s *Session) Lock()   { s.mu.Lock() }
func (s *Session) Unlock() { s.mu.Unlock() }

// CheckCSRF compares in constant time.
func (s *Session) CheckCSRF(token string) bool {
	return token != "" && subtle.ConstantTimeCompare([]byte(token), []byte(s.CSRF)) == 1
}

// Store is safe for concurrent use.
type Store struct {
	Idle     time.Duration
	Lifetime time.Duration
	Max      int
	Now      func() time.Time

	mu       sync.Mutex
	sessions map[[32]byte]*Session
}

func NewStore(idle, lifetime time.Duration, max int) *Store {
	return &Store{Idle: idle, Lifetime: lifetime, Max: max, sessions: map[[32]byte]*Session{}}
}

func (st *Store) now() time.Time {
	if st.Now != nil {
		return st.Now()
	}
	return time.Now()
}

// Random returns n random bytes, base64url-encoded.
func Random(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err) // the OS random source failing is not recoverable
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

func key(id string) [32]byte { return sha256.Sum256([]byte(id)) }

// New creates an empty session and returns its id.
func (st *Store) New() (string, *Session, error) {
	st.mu.Lock()
	defer st.mu.Unlock()
	now := st.now()
	if len(st.sessions) >= st.Max {
		st.sweepLocked(now)
		if len(st.sessions) >= st.Max {
			return "", nil, ErrFull
		}
	}
	id := Random(32)
	s := &Session{CSRF: Random(32), created: now, lastSeen: now}
	st.sessions[key(id)] = s
	return id, s, nil
}

// Get returns a live session and marks it used.
func (st *Store) Get(id string) (*Session, bool) {
	if id == "" || len(id) > 128 {
		return nil, false
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	k := key(id)
	s, ok := st.sessions[k]
	if !ok {
		return nil, false
	}
	now := st.now()
	if now.Sub(s.lastSeen) > st.Idle || now.Sub(s.created) > st.Lifetime {
		delete(st.sessions, k)
		return nil, false
	}
	s.lastSeen = now
	return s, true
}

// Rotate moves a session to a fresh id (and a fresh CSRF token): call it at
// sign-in so an id planted before authentication is worthless afterwards.
func (st *Store) Rotate(oldID string) (string, *Session, bool) {
	st.mu.Lock()
	defer st.mu.Unlock()
	s, ok := st.sessions[key(oldID)]
	if !ok {
		return "", nil, false
	}
	delete(st.sessions, key(oldID))
	id := Random(32)
	s.CSRF = Random(32)
	s.created, s.lastSeen = st.now(), st.now()
	st.sessions[key(id)] = s
	return id, s, true
}

func (st *Store) Delete(id string) {
	st.mu.Lock()
	defer st.mu.Unlock()
	delete(st.sessions, key(id))
}

func (st *Store) Len() int {
	st.mu.Lock()
	defer st.mu.Unlock()
	return len(st.sessions)
}

// Sweep drops expired sessions; run it periodically.
func (st *Store) Sweep() {
	st.mu.Lock()
	defer st.mu.Unlock()
	st.sweepLocked(st.now())
}

func (st *Store) sweepLocked(now time.Time) {
	for k, s := range st.sessions {
		if now.Sub(s.lastSeen) > st.Idle || now.Sub(s.created) > st.Lifetime {
			delete(st.sessions, k)
		}
	}
}
