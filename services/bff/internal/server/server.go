// Package server is the BFF's HTTP surface: the only thing users can reach.
//
//	browser --(session cookie, CSRF token)--> BFF --(signed call, user's JWT)--> core service
//
// What it does for every request, in order: security headers, honeypot check,
// rate limit, route by the compiled-in route table (undeclared routes 404), session and role
// check, CSRF for state changes, validate query and body against the contract,
// refresh the user's token if needed, sign and send the upstream call, shape
// the response to the contract. Tokens never leave the server.
package server

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"babelbazaar/bff/internal/config"
	"babelbazaar/bff/internal/contract"
	"babelbazaar/bff/internal/jwt"
	"babelbazaar/bff/internal/seal"
	"babelbazaar/bff/internal/session"
)

const errorBase = "https://shop.local/errors/"

// Honeypots look like administrative or leaked endpoints. They answer exactly
// like any unknown path, and raise a silent alert.
var honeypots = []string{
	"/.env", "/.git", "/wp-login.php", "/wp-admin", "/phpmyadmin", "/administrator", "/admin.php",
	"/actuator", "/django-admin", "/server-status", "/api/internal", "/api/v1/admin", "/api/debug",
	"/oauth2/token", "/api/commerce", // core paths: never exposed by the BFF, so only a scanner asks
}

var traceparentRE = regexp.MustCompile(`^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$`)

// Server holds everything a request needs.
type Server struct {
	cfg      *config.Config
	contract *contract.Contract
	sessions *session.Store
	verifier *jwt.Verifier
	upstream *http.Client // every request it sends is signed (seal.Transport)
	log      *slog.Logger
	security *slog.Logger
	limits   map[string]*limiter
	site     http.Handler // the storefront (nil: not served by this BFF)
	cache    *microCache
	now      func() time.Time
}

// ServeSite makes the BFF serve the storefront app at every path that is not an API or sign-in route.
func (s *Server) ServeSite(site http.Handler) { s.site = site }

// New wires a server. upstreamBase is the transport under the signer (tests inject one).
func New(cfg *config.Config, c *contract.Contract, log *slog.Logger, upstreamBase http.RoundTripper) *Server {
	if upstreamBase == nil {
		upstreamBase = &http.Transport{
			Proxy:                 nil, // never route core traffic through an environment proxy
			DialContext:           (&net.Dialer{Timeout: 3 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
			MaxIdleConnsPerHost:   64,
			IdleConnTimeout:       90 * time.Second,
			ResponseHeaderTimeout: 30 * time.Second,
		}
	}
	client := &http.Client{
		Transport: &seal.Transport{Secret: cfg.GatewaySecret, Base: upstreamBase},
		// Never follow redirects: a core service's Location goes back to the browser through the proxy checks.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	s := &Server{
		cfg:      cfg,
		contract: c,
		sessions: session.NewStore(cfg.SessionIdle, cfg.SessionLifetime, cfg.MaxSessions),
		upstream: client,
		log:      log,
		security: log.With("channel", "security"),
		now:      time.Now,
	}
	s.verifier = &jwt.Verifier{
		Issuer:   cfg.Issuer,
		Audience: cfg.ClientID,
		Leeway:   30 * time.Second,
		Source:   jwt.HTTPKeySource(client, cfg.Upstreams["auth"].String()+"/oauth2/jwks"),
	}
	s.cache = newMicroCache(10_000, s.clock)
	s.limits = map[string]*limiter{
		"default":  newLimiter(cfg.RateDefaultPerMinute, s.clock),
		"auth":     newLimiter(cfg.RateAuthPerMinute, s.clock),
		"register": newLimiter(cfg.RateRegisterPerMinute, s.clock),
		"checkout": newLimiter(cfg.RateCheckoutPerMinute, s.clock),
	}
	return s
}

func (s *Server) clock() time.Time { return s.now() }

// LimitNames are the rate limits endpoints may name in the contract.
func LimitNames() []string { return []string{"register", "checkout"} }

// Sessions exposes the store (for the sweeper and tests).
func (s *Server) Sessions() *session.Store { return s.sessions }

// Handler is the whole HTTP surface.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.healthz)
	mux.HandleFunc("GET /readyz", s.readyz)
	mux.HandleFunc("GET /auth/login", s.login)
	mux.HandleFunc("GET /auth/callback", s.callback)
	mux.HandleFunc("POST /auth/logout", s.logout)
	mux.HandleFunc("GET /api/me", s.me)
	// auth-service's sign-in pages, proxied so the browser only ever talks to the BFF
	mux.HandleFunc("GET /oauth2/authorize", s.proxyAuthPage)
	mux.HandleFunc("GET /login", s.proxyAuthPage)
	mux.HandleFunc("POST /login", s.proxyAuthPage)
	mux.HandleFunc("GET /auth-ui.css", s.proxyAuthPage) // the sign-in page's stylesheet
	s.routes(mux)                                       // the contract's endpoints (internal/contract/routes.go)
	mux.HandleFunc("/api/", s.notFound)
	mux.HandleFunc("/auth/", s.notFound)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if s.site == nil {
			s.notFound(w, r)
			return
		}
		s.site.ServeHTTP(w, r)
	})
	return s.middleware(mux)
}

type ctxKey int

const traceKey ctxKey = iota

func traceID(r *http.Request) string {
	if v, ok := r.Context().Value(traceKey).(string); ok {
		return v
	}
	return ""
}

func (s *Server) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := s.now()
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Cross-Origin-Opener-Policy", "same-origin")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		h.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
		h.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
		h.Set("Cache-Control", "no-store")
		if s.cfg.CookieSecure {
			h.Set("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
		}

		trace := ""
		if m := traceparentRE.FindStringSubmatch(r.Header.Get("traceparent")); m != nil {
			trace = m[1]
		} else {
			b := make([]byte, 16)
			rand.Read(b)
			trace = hex.EncodeToString(b)
		}
		r = r.WithContext(context.WithValue(r.Context(), traceKey, trace))

		defer func() {
			if rec := recover(); rec != nil {
				s.log.Error("panic", "err", rec, "path", r.URL.Path, "trace_id", trace)
				s.problem(w, r, http.StatusInternalServerError, "internal", "Internal error", "Something went wrong.")
			}
		}()

		if s.honeypot(r.URL.Path) {
			s.security.Warn("honeypot touched", "path", r.URL.Path, "method", r.Method, "ip", s.clientIP(r),
				"user_agent", r.UserAgent(), "trace_id", trace)
			s.notFound(w, r) // indistinguishable from any other unknown path
			return
		}
		// Fingerprinted app files are served from memory and cached for a year: they do not use the API's budget.
		if strings.HasPrefix(r.URL.Path, "/assets/") && s.site != nil {
			next.ServeHTTP(w, r)
			return
		}
		if ok, wait := s.limits["default"].allow("ip:" + s.clientIP(r)); !ok {
			s.tooMany(w, r, wait)
			return
		}
		rw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rw, r)
		s.log.Info("request", "method", r.Method, "path", r.URL.Path, "status", rw.status,
			"ms", s.now().Sub(start).Milliseconds(), "trace_id", trace)
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func (s *Server) honeypot(path string) bool {
	p := strings.ToLower(path)
	for _, h := range honeypots {
		if p == h || strings.HasPrefix(p, h+"/") {
			return true
		}
	}
	return false
}

// clientIP is the connecting address, or the right-most untrusted
// X-Forwarded-For hop when the connection comes from a trusted proxy.
func (s *Server) clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	if !s.trusted(host) {
		return host
	}
	hops := strings.Split(r.Header.Get("X-Forwarded-For"), ",")
	for i := len(hops) - 1; i >= 0; i-- {
		hop := strings.TrimSpace(hops[i])
		if hop != "" && !s.trusted(hop) {
			return hop
		}
	}
	return host
}

func (s *Server) trusted(ip string) bool {
	parsed := net.ParseIP(ip)
	for _, n := range s.cfg.TrustedProxies {
		if parsed != nil && n.Contains(parsed) {
			return true
		}
	}
	return false
}

func (s *Server) healthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "service": "bff"})
}

// readyz: the BFF can verify tokens (it has auth-service's signing keys).
func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	if err := s.verifier.Ready(r.Context()); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "unavailable", "signing_keys": "missing"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ready", "sessions": s.sessions.Len()})
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

// problem writes an RFC 9457 problem-details response.
func (s *Server) problem(w http.ResponseWriter, r *http.Request, status int, slug, title, detail string, extra ...any) {
	body := map[string]any{"type": errorBase + slug, "title": title, "status": status, "detail": detail, "trace_id": traceID(r)}
	for i := 0; i+1 < len(extra); i += 2 {
		body[extra[i].(string)] = extra[i+1]
	}
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

func (s *Server) notFound(w http.ResponseWriter, r *http.Request) {
	s.problem(w, r, http.StatusNotFound, "not-found", "Not found", "No such endpoint.")
}

func (s *Server) tooMany(w http.ResponseWriter, r *http.Request, wait time.Duration) {
	w.Header().Set("Retry-After", itoa(int(wait.Seconds())))
	s.problem(w, r, http.StatusTooManyRequests, "rate-limited", "Too many requests", "Slow down and retry shortly.")
}

func itoa(n int) string {
	if n < 1 {
		n = 1
	}
	return strconv.Itoa(n)
}
