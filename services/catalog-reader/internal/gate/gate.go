// Package gate refuses every request that did not come through the BFF
// (docs/request-signing.md): an empty 403 before routing, parsing or any
// database work. Fail closed: New refuses a missing or short secret.
//
// The same function lives in the BFF signer and in the Django, FastAPI and
// Spring guards; docs/contracts/gateway-signing.vectors.json keeps them (and
// Ensuretech's GatewaySeal) in step.
package gate

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	Version        = "v1"
	Window         = 60 * time.Second
	MaxNonces      = 200_000
	MinSecretBytes = 32
)

func Canonical(ts, nonce, method, path, query, authorization string) string {
	sum := sha256.Sum256([]byte(authorization))
	return Version + "\n" + ts + "\n" + nonce + "\n" + strings.ToUpper(method) + "\n" + path + "\n" + query + "\n" + hex.EncodeToString(sum[:])
}

func Sign(secret []byte, ts, nonce, method, path, query, authorization string) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(Canonical(ts, nonce, method, path, query, authorization)))
	return hex.EncodeToString(mac.Sum(nil))
}

// Guard is an http middleware.
type Guard struct {
	secret []byte
	open   map[string]bool
	log    *slog.Logger
	Now    func() time.Time

	mu   sync.Mutex
	seen map[string]time.Time
}

// New fails unless the guard can actually protect the service.
func New(secret string, allowUnguarded bool, openPaths []string, log *slog.Logger) (*Guard, error) {
	switch {
	case allowUnguarded:
		log.Warn("GATEWAY_ALLOW_UNGUARDED is on: requests that bypass the BFF are NOT refused. Local development and tests only.")
	case secret == "":
		return nil, errors.New("GATEWAY_SECRET is not set, so this service would answer anyone who can reach it")
	case len(secret) < MinSecretBytes:
		return nil, errors.New("GATEWAY_SECRET is shorter than 32 characters (openssl rand -hex 32)")
	}
	open := map[string]bool{}
	for _, p := range openPaths {
		open[p] = true
	}
	return &Guard{secret: []byte(secret), open: open, log: log, Now: time.Now, seen: map[string]time.Time{}}, nil
}

func (g *Guard) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if len(g.secret) == 0 || g.open[r.URL.Path] || g.verify(r) {
			next.ServeHTTP(w, r)
			return
		}
		g.log.Info("refused a request that did not come through the BFF", "method", r.Method, "path", r.URL.Path, "from", r.RemoteAddr)
		w.Header().Set("Content-Length", "0")
		w.WriteHeader(http.StatusForbidden)
	})
}

func (g *Guard) verify(r *http.Request) bool {
	ts, nonce, sig := r.Header.Get("X-Gateway-Timestamp"), r.Header.Get("X-Gateway-Nonce"), r.Header.Get("X-Gateway-Signature")
	if len(ts) == 0 || len(ts) > 12 || len(nonce) < 16 || len(nonce) > 64 || len(sig) != 64 {
		return false
	}
	secs, err := strconv.ParseInt(ts, 10, 64)
	if err != nil || secs < 0 {
		return false
	}
	now := g.Now()
	if d := now.Sub(time.Unix(secs, 0)); d > Window || d < -Window {
		return false
	}
	// EscapedPath/RawQuery: the raw request target, exactly what the BFF signed.
	want := Sign(g.secret, ts, nonce, r.Method, r.URL.EscapedPath(), r.URL.RawQuery, r.Header.Get("Authorization"))
	if !hmac.Equal([]byte(want), []byte(sig)) {
		return false
	}
	return g.remember(nonce, now)
}

// remember runs only after the signature checks out, so strangers cannot fill
// the cache. A full cache refuses rather than forgets.
func (g *Guard) remember(nonce string, now time.Time) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.seen) >= MaxNonces {
		for k, t := range g.seen {
			if now.Sub(t) > 2*Window {
				delete(g.seen, k)
			}
		}
		if len(g.seen) >= MaxNonces {
			return false
		}
	}
	if _, dup := g.seen[nonce]; dup {
		return false
	}
	g.seen[nonce] = now
	return true
}
