package server

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const maxAuthPageBody = 1 << 20

// The sign-in pages are HTML forms with inline styles; nothing else may load.
const authPageCSP = "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src data:; form-action 'self'; " +
	"frame-ancestors 'none'; base-uri 'none'"

// proxyAuthPage forwards GET /oauth2/authorize, GET /login, POST /login and the
// page's stylesheet (GET /default-ui.css) to
// auth-service, signed. These are the only auth-service routes a browser can
// reach, and only through here; the token, revocation and key endpoints are
// called by the BFF itself.
func (s *Server) proxyAuthPage(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		// Password guessing: a tight per-IP budget on top of the global one.
		if ok, wait := s.limits["auth"].allow("ip:" + s.clientIP(r)); !ok {
			s.security.Warn("sign-in rate limit hit", "ip", s.clientIP(r), "trace_id", traceID(r))
			s.tooMany(w, r, wait)
			return
		}
		if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/x-www-form-urlencoded") {
			s.problem(w, r, http.StatusUnsupportedMediaType, "unsupported-media-type", "Unsupported media type", "Send the sign-in form.")
			return
		}
	}
	body := http.MaxBytesReader(w, r.Body, 16<<10)
	target := *s.cfg.Upstreams["auth"]
	target.Path, target.RawQuery = r.URL.EscapedPath(), r.URL.RawQuery

	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.UpstreamTimeout)
	defer cancel()
	out, err := http.NewRequestWithContext(ctx, r.Method, target.String(), body)
	if err != nil {
		s.problem(w, r, http.StatusBadRequest, "bad-request", "Bad request", "Malformed request.")
		return
	}
	for _, h := range []string{"Content-Type", "Accept", "Accept-Language"} {
		if v := r.Header.Get(h); v != "" {
			out.Header.Set(h, v)
		}
	}
	// Only auth-service's own cookie goes upstream; never the BFF session.
	for _, c := range r.Cookies() {
		if c.Name == authCookie {
			out.AddCookie(&http.Cookie{Name: c.Name, Value: c.Value})
		}
	}
	// auth-service builds its redirects from these. The BFF sets them; a client's copies are dropped.
	out.Header.Set("X-Forwarded-Proto", s.cfg.PublicURL.Scheme)
	out.Header.Set("X-Forwarded-Host", s.cfg.PublicURL.Host)
	if port := s.cfg.PublicURL.Port(); port != "" {
		out.Header.Set("X-Forwarded-Port", port)
	}
	out.Header.Set("traceparent", "00-"+traceID(r)+"-0000000000000001-01")

	resp, err := s.upstream.Do(out)
	if err != nil {
		s.log.Warn("auth-service unreachable", "err", err, "trace_id", traceID(r))
		s.problem(w, r, http.StatusBadGateway, "upstream-unavailable", "Sign-in unavailable", "Try again shortly.")
		return
	}
	defer resp.Body.Close()

	if loc := resp.Header.Get("Location"); loc != "" {
		safe, ok := s.localRedirect(loc)
		if !ok {
			s.security.Warn("auth-service redirect off-origin refused", "location", loc, "trace_id", traceID(r))
			s.problem(w, r, http.StatusBadGateway, "upstream-error", "Sign-in failed", "Start signing in again.")
			return
		}
		w.Header().Set("Location", safe)
	}
	for _, c := range resp.Cookies() {
		if c.Name != authCookie {
			continue // auth-service may only set its own sign-in cookie
		}
		http.SetCookie(w, &http.Cookie{Name: c.Name, Value: c.Value, Path: "/", HttpOnly: true, Secure: s.cfg.CookieSecure,
			SameSite: http.SameSiteLaxMode, MaxAge: c.MaxAge})
	}
	ct := resp.Header.Get("Content-Type")
	if ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	if strings.HasPrefix(ct, "text/html") {
		w.Header().Set("Content-Security-Policy", authPageCSP)
	}
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, io.LimitReader(resp.Body, maxAuthPageBody))
}

// localRedirect accepts a relative Location, or an absolute one on the BFF's
// own origin (or auth-service's internal origin, rewritten to ours).
func (s *Server) localRedirect(loc string) (string, bool) {
	u, err := url.Parse(loc)
	if err != nil {
		return "", false
	}
	if u.Scheme == "" && u.Host == "" {
		return loc, strings.HasPrefix(loc, "/") && !strings.HasPrefix(loc, "//")
	}
	internal := s.cfg.Upstreams["auth"]
	if (u.Scheme == s.cfg.PublicURL.Scheme && u.Host == s.cfg.PublicURL.Host) ||
		(u.Scheme == internal.Scheme && u.Host == internal.Host) {
		rel := u.EscapedPath()
		if u.RawQuery != "" {
			rel += "?" + u.RawQuery
		}
		if rel == "" {
			rel = "/"
		}
		return rel, true
	}
	return "", false
}
