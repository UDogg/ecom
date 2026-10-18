package server

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"babelbazaar/bff/internal/session"
)

const (
	pendingLoginTTL = 10 * time.Minute
	refreshMargin   = 30 * time.Second
	authCookie      = "JSESSIONID" // auth-service's own sign-in session, scoped to the proxied pages
)

func (s *Server) cookieName() string {
	if s.cfg.CookieSecure {
		return "__Host-bb_session" // __Host-: Secure, Path=/, no Domain, so no subdomain can set or read it
	}
	return "bb_session"
}

func (s *Server) setSessionCookie(w http.ResponseWriter, id string) {
	http.SetCookie(w, &http.Cookie{Name: s.cookieName(), Value: id, Path: "/", HttpOnly: true, Secure: s.cfg.CookieSecure,
		SameSite: http.SameSiteLaxMode, MaxAge: int(s.cfg.SessionLifetime.Seconds())})
}

func (s *Server) expireCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", HttpOnly: true, Secure: s.cfg.CookieSecure,
		SameSite: http.SameSiteLaxMode, MaxAge: -1})
}

// currentSession returns the caller's session, if any.
func (s *Server) currentSession(r *http.Request) (string, *session.Session) {
	c, err := r.Cookie(s.cookieName())
	if err != nil {
		return "", nil
	}
	sess, ok := s.sessions.Get(c.Value)
	if !ok {
		return "", nil
	}
	return c.Value, sess
}

// ensureSession returns the caller's session, creating an anonymous one if needed.
func (s *Server) ensureSession(w http.ResponseWriter, r *http.Request) (string, *session.Session, error) {
	if id, sess := s.currentSession(r); sess != nil {
		return id, sess, nil
	}
	id, sess, err := s.sessions.New()
	if err != nil {
		return "", nil, err
	}
	s.setSessionCookie(w, id)
	return id, sess, nil
}

// safeReturnTo only allows local paths, so /auth/login cannot be an open redirect.
func safeReturnTo(raw string) string {
	if len(raw) > 512 || !strings.HasPrefix(raw, "/") || strings.HasPrefix(raw, "//") || strings.ContainsAny(raw, "\\\r\n\t") {
		return "/"
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "" || u.Host != "" {
		return "/"
	}
	return raw
}

func (s *Server) redirectURI() string { return s.cfg.Origin() + "/auth/callback" }

// GET /auth/login: start the authorization-code flow with PKCE.
func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if ok, wait := s.limits["auth"].allow("ip:" + s.clientIP(r)); !ok {
		s.tooMany(w, r, wait)
		return
	}
	_, sess, err := s.ensureSession(w, r)
	if err != nil {
		s.problem(w, r, http.StatusServiceUnavailable, "busy", "Busy", "Too many active sessions; try again shortly.")
		return
	}
	pending := &session.PendingLogin{State: session.Random(32), Verifier: session.Random(48),
		ReturnTo: safeReturnTo(r.URL.Query().Get("return_to")), Started: s.now()}
	sess.Pending = pending
	challenge := sha256.Sum256([]byte(pending.Verifier))
	q := url.Values{
		"response_type":         {"code"},
		"client_id":             {s.cfg.ClientID},
		"redirect_uri":          {s.redirectURI()},
		"scope":                 {"openid email profile"},
		"state":                 {pending.State},
		"code_challenge":        {base64.RawURLEncoding.EncodeToString(challenge[:])},
		"code_challenge_method": {"S256"},
	}
	http.Redirect(w, r, "/oauth2/authorize?"+q.Encode(), http.StatusFound)
}

type tokenResponse struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	IDToken      string `json:"id_token"`
	ExpiresIn    int    `json:"expires_in"`
}

// tokenRequest calls auth-service's token endpoint as the confidential BFF client (signed, like every core call).
func (s *Server) tokenRequest(ctx context.Context, form url.Values) (*tokenResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, s.cfg.UpstreamTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.cfg.Upstreams["auth"].String()+"/oauth2/token",
		strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	req.SetBasicAuth(url.QueryEscape(s.cfg.ClientID), url.QueryEscape(s.cfg.ClientSecret))
	resp, err := s.upstream.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, errors.New("token endpoint answered " + resp.Status)
	}
	var tokens tokenResponse
	if err := json.Unmarshal(raw, &tokens); err != nil || tokens.AccessToken == "" {
		return nil, errors.New("token endpoint returned no access token")
	}
	return &tokens, nil
}

// store verifies the access token and keeps everything server-side.
func (s *Server) store(ctx context.Context, sess *session.Session, tokens *tokenResponse) error {
	claims, err := s.verifier.Verify(ctx, tokens.AccessToken)
	if err != nil {
		return err
	}
	sess.AccessToken, sess.IDToken = tokens.AccessToken, tokens.IDToken
	if tokens.RefreshToken != "" {
		sess.RefreshToken = tokens.RefreshToken
	}
	sess.AccessExpiry = time.Unix(claims.ExpiresAt, 0)
	sess.Claims = &claims
	return nil
}

// GET /auth/callback: finish sign-in.
func (s *Server) callback(w http.ResponseWriter, r *http.Request) {
	if ok, wait := s.limits["auth"].allow("ip:" + s.clientIP(r)); !ok {
		s.tooMany(w, r, wait)
		return
	}
	id, sess := s.currentSession(r)
	q := r.URL.Query()
	if sess == nil || sess.Pending == nil {
		s.problem(w, r, http.StatusBadRequest, "login-expired", "Sign-in expired", "Start signing in again.", "login_url", "/auth/login")
		return
	}
	pending := sess.Pending
	sess.Pending = nil // one shot, whatever happens next
	if subtle.ConstantTimeCompare([]byte(q.Get("state")), []byte(pending.State)) != 1 {
		s.security.Warn("sign-in state mismatch", "ip", s.clientIP(r), "trace_id", traceID(r))
		s.problem(w, r, http.StatusBadRequest, "login-failed", "Sign-in failed", "Start signing in again.", "login_url", "/auth/login")
		return
	}
	if s.now().Sub(pending.Started) > pendingLoginTTL || q.Get("error") != "" || q.Get("code") == "" || len(q.Get("code")) > 512 {
		s.problem(w, r, http.StatusBadRequest, "login-failed", "Sign-in failed", "Start signing in again.", "login_url", "/auth/login")
		return
	}
	tokens, err := s.tokenRequest(r.Context(), url.Values{
		"grant_type": {"authorization_code"}, "code": {q.Get("code")},
		"redirect_uri": {s.redirectURI()}, "code_verifier": {pending.Verifier},
	})
	if err == nil {
		err = s.store(r.Context(), sess, tokens)
	}
	if err != nil {
		s.log.Warn("sign-in exchange failed", "err", err, "trace_id", traceID(r))
		s.problem(w, r, http.StatusBadGateway, "login-failed", "Sign-in failed", "Start signing in again.", "login_url", "/auth/login")
		return
	}
	// New id at sign-in: an id planted before authentication is worthless now.
	newID, _, ok := s.sessions.Rotate(id)
	if !ok {
		s.problem(w, r, http.StatusBadRequest, "login-expired", "Sign-in expired", "Start signing in again.", "login_url", "/auth/login")
		return
	}
	s.setSessionCookie(w, newID)
	s.expireCookie(w, authCookie) // auth-service's sign-in session is no longer needed
	http.Redirect(w, r, pending.ReturnTo, http.StatusFound)
}

// POST /auth/logout: forget the session and revoke the refresh token.
func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	id, sess := s.currentSession(r)
	if sess != nil {
		if !s.csrfOK(r, sess) {
			s.problem(w, r, http.StatusForbidden, "csrf", "Forbidden", "Missing or wrong CSRF token.")
			return
		}
		if sess.RefreshToken != "" {
			s.revoke(r.Context(), sess.RefreshToken)
		}
		s.sessions.Delete(id)
	}
	s.expireCookie(w, s.cookieName())
	s.expireCookie(w, authCookie)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) revoke(ctx context.Context, token string) {
	ctx, cancel := context.WithTimeout(ctx, s.cfg.UpstreamTimeout)
	defer cancel()
	form := url.Values{"token": {token}, "token_type_hint": {"refresh_token"}}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.cfg.Upstreams["auth"].String()+"/oauth2/revoke",
		strings.NewReader(form.Encode()))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.SetBasicAuth(url.QueryEscape(s.cfg.ClientID), url.QueryEscape(s.cfg.ClientSecret))
	if resp, err := s.upstream.Do(req); err == nil {
		resp.Body.Close()
	} else {
		s.log.Warn("refresh token revocation failed", "err", err)
	}
}

// freshAccessToken refreshes the access token shortly before it expires.
// Signed out (empty string) if that is impossible.
func (s *Server) freshAccessToken(ctx context.Context, sess *session.Session) string {
	sess.Lock()
	defer sess.Unlock()
	if !sess.SignedIn() {
		return ""
	}
	if s.now().Add(refreshMargin).Before(sess.AccessExpiry) {
		return sess.AccessToken
	}
	if sess.RefreshToken != "" {
		tokens, err := s.tokenRequest(ctx, url.Values{"grant_type": {"refresh_token"}, "refresh_token": {sess.RefreshToken}})
		if err == nil && s.store(ctx, sess, tokens) == nil {
			return sess.AccessToken
		}
		s.log.Info("token refresh failed; signing the session out", "err", err)
	}
	sess.AccessToken, sess.RefreshToken, sess.Claims = "", "", nil
	return ""
}

// csrfOK: the session's CSRF token in X-CSRF-Token, and (when the browser sends one) our own Origin.
func (s *Server) csrfOK(r *http.Request, sess *session.Session) bool {
	if origin := r.Header.Get("Origin"); origin != "" && origin != s.cfg.Origin() {
		return false
	}
	return sess.CheckCSRF(r.Header.Get("X-CSRF-Token"))
}

// GET /api/me: who is signed in, and the CSRF token for state-changing calls.
func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	_, sess, err := s.ensureSession(w, r)
	if err != nil {
		s.problem(w, r, http.StatusServiceUnavailable, "busy", "Busy", "Too many active sessions; try again shortly.")
		return
	}
	body := map[string]any{"authenticated": false, "csrf_token": sess.CSRF, "login_url": "/auth/login"}
	if s.freshAccessToken(r.Context(), sess) != "" {
		body["authenticated"] = true
		body["user"] = map[string]any{"id": sess.Claims.Subject, "email": sess.Claims.Email, "roles": sess.Claims.Roles}
		delete(body, "login_url")
	}
	writeJSON(w, http.StatusOK, body)
}
