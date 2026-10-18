package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"unicode"

	"babelbazaar/bff/internal/contract"
	"babelbazaar/bff/internal/session"
)

const maxUpstreamBody = 5 << 20

func unsafeMethod(m string) bool {
	return m != http.MethodGet && m != http.MethodHead && m != http.MethodOptions
}

// routes registers one handler per path in the contract, and nothing else: a path that is not
// declared there falls through to the 404 handler, a method that is not declared gets 405.
func (s *Server) routes(mux *http.ServeMux) {
	byPath := map[string]map[string]*contract.Endpoint{}
	var paths []string
	for _, e := range s.contract.Endpoints {
		if byPath[e.Path] == nil {
			byPath[e.Path] = map[string]*contract.Endpoint{}
			paths = append(paths, e.Path)
		}
		byPath[e.Path][e.Method] = e
	}
	for _, path := range paths {
		methods := byPath[path]
		allow := make([]string, 0, len(methods))
		for m := range methods {
			allow = append(allow, m)
		}
		sort.Strings(allow)
		allowHeader := strings.Join(allow, ", ")
		names := methods[allow[0]].Params() // the same for every method of a path
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			var params map[string]string
			if len(names) > 0 {
				params = make(map[string]string, len(names))
				for _, name := range names {
					v := r.PathValue(name)
					if !contract.ValidUUID(v) {
						s.notFound(w, r) // ids are UUIDs: anything else is not a resource here
						return
					}
					params[name] = strings.ToLower(v)
				}
			}
			e := methods[r.Method]
			if e == nil {
				w.Header().Set("Allow", allowHeader)
				s.problem(w, r, http.StatusMethodNotAllowed, "method-not-allowed", "Method not allowed", "This endpoint does not accept "+r.Method+".")
				return
			}
			s.api(w, r, e, params)
		})
	}
}

// api serves one declared endpoint.
func (s *Server) api(w http.ResponseWriter, r *http.Request, e *contract.Endpoint, params map[string]string) {
	_, sess := s.currentSession(r)
	var token string
	if e.Auth != contract.Public {
		if sess != nil {
			token = s.freshAccessToken(r.Context(), sess)
		}
		if token == "" {
			s.problem(w, r, http.StatusUnauthorized, "unauthenticated", "Sign in required", "Sign in to continue.", "login_url", "/auth/login")
			return
		}
		// The BFF checks the role too, so obviously wrong calls never travel; the core service decides for real.
		if !sess.Claims.HasRole(e.Auth) && !(e.Auth == contract.Customer && sess.Claims.HasRole(contract.ShopOwner)) {
			s.problem(w, r, http.StatusForbidden, "forbidden", "Forbidden", "Your account cannot use this.")
			return
		}
	}
	if unsafeMethod(r.Method) && (sess == nil || !s.csrfOK(r, sess)) {
		s.security.Info("CSRF check failed", "path", r.URL.Path, "ip", s.clientIP(r), "trace_id", traceID(r))
		s.problem(w, r, http.StatusForbidden, "csrf", "Forbidden", "Missing or wrong CSRF token; read it from GET /api/me.")
		return
	}
	if e.Limit != "" {
		key := "ip:" + s.clientIP(r)
		if sess != nil && sess.Claims != nil {
			key = "user:" + sess.Claims.Subject
		}
		if ok, wait := s.limits[e.Limit].allow(key); !ok {
			s.tooMany(w, r, wait)
			return
		}
	}

	// ParseQuery, not URL.Query(): the latter silently drops malformed pairs, which would forward a different request.
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		s.problem(w, r, http.StatusBadRequest, "validation", "Invalid request", "Malformed query string.")
		return
	}
	query, err := e.QueryString(values)
	if err != nil {
		s.invalid(w, r, err)
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, s.cfg.MaxBodyBytes))
	if err != nil {
		s.problem(w, r, http.StatusRequestEntityTooLarge, "too-large", "Request too large", "The body is larger than allowed.")
		return
	}
	if e.Body != nil && !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		s.problem(w, r, http.StatusUnsupportedMediaType, "unsupported-media-type", "Unsupported media type", "Send application/json.")
		return
	}
	body, err := e.ValidateBody(raw)
	if err != nil {
		s.invalid(w, r, err)
		return
	}
	var upstreamBody []byte
	if body != nil {
		// Re-encode what was validated: duplicate keys and other parser quirks never reach the core.
		upstreamBody, _ = json.Marshal(body)
	}
	if e.Cache > 0 && s.cfg.MicroCache { // public and anonymous by construction (contract.New checks it)
		s.cache.serve(w, e.Name+" "+e.UpstreamPath(params)+"?"+query, e.Cache, func(w http.ResponseWriter) {
			s.forward(w, r, e, params, query, nil, "", nil)
		})
		return
	}
	s.forward(w, r, e, params, query, upstreamBody, token, sess)
}

func (s *Server) invalid(w http.ResponseWriter, r *http.Request, err error) {
	var verr contract.ValidationError
	if errors.As(err, &verr) {
		s.problem(w, r, http.StatusBadRequest, "validation", "Invalid request", "One or more fields are invalid.", "errors", verr)
		return
	}
	s.problem(w, r, http.StatusBadRequest, "validation", "Invalid request", err.Error())
}

// headerValueOK keeps forwarded header values short and printable.
func headerValueOK(v string) bool {
	if len(v) == 0 || len(v) > 200 {
		return false
	}
	for _, c := range v {
		if c > unicode.MaxASCII || !unicode.IsPrint(c) {
			return false
		}
	}
	return true
}

func (s *Server) forward(w http.ResponseWriter, r *http.Request, e *contract.Endpoint, params map[string]string,
	query string, body []byte, token string, sess *session.Session) {
	timeout := s.cfg.UpstreamTimeout
	if e.Timeout > 0 {
		timeout = e.Timeout
	}
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	target := *s.cfg.Upstreams[e.Service]
	target.Path = e.UpstreamPath(params)
	target.RawQuery = query
	out, err := http.NewRequestWithContext(ctx, e.Method, target.String(), bytes.NewReader(body))
	if err != nil {
		s.problem(w, r, http.StatusInternalServerError, "internal", "Internal error", "Could not build the request.")
		return
	}
	out.ContentLength = int64(len(body))
	out.Header.Set("Accept", "application/json")
	if body != nil {
		out.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		out.Header.Set("Authorization", "Bearer "+token)
	}
	out.Header.Set("traceparent", "00-"+traceID(r)+"-0000000000000001-01")
	for _, h := range e.RequestHeaders {
		if v := r.Header.Get(h); v != "" {
			if !headerValueOK(v) {
				s.problem(w, r, http.StatusBadRequest, "validation", "Invalid request", h+" is not a valid header value.")
				return
			}
			out.Header.Set(h, v)
		}
	}

	resp, err := s.upstream.Do(out)
	if err != nil {
		s.upstreamFailed(w, r, e, err)
		return
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody+1))
	if err != nil || len(raw) > maxUpstreamBody {
		s.upstreamFailed(w, r, e, errors.New("upstream body unreadable or too large"))
		return
	}
	if resp.StatusCode >= 500 {
		s.upstreamFailed(w, r, e, errors.New("upstream answered "+resp.Status))
		return
	}
	if resp.StatusCode == http.StatusForbidden && len(raw) == 0 {
		// An empty 403 is a core's gateway guard refusing us: a BFF/core secret mismatch, not the user's fault.
		s.log.Error("core service refused the BFF's signature (GATEWAY_SECRET mismatch or clock skew?)",
			"service", e.Service, "trace_id", traceID(r))
		s.problem(w, r, http.StatusBadGateway, "upstream-error", "Service unavailable", "Try again shortly.")
		return
	}
	if resp.StatusCode == http.StatusUnauthorized && sess != nil {
		sess.Lock()
		sess.AccessToken, sess.RefreshToken, sess.Claims = "", "", nil // the core refused the token: sign out
		sess.Unlock()
	}
	for _, h := range e.ResponseHeaders {
		if v := resp.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}
	if resp.StatusCode == http.StatusNoContent || len(bytes.TrimSpace(raw)) == 0 {
		w.WriteHeader(resp.StatusCode)
		return
	}

	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		s.upstreamFailed(w, r, e, errors.New("upstream sent non-JSON"))
		return
	}
	schema, contentType := e.Response, "application/json"
	if resp.StatusCode >= 400 {
		schema, contentType = "problem", "application/problem+json"
	}
	if resp.StatusCode < 400 && s.contract.Fits(schema, decoded) {
		// Nothing to drop or rename: the upstream bytes are the public answer (checked, not trusted).
		w.Header().Set("Content-Type", contentType)
		w.WriteHeader(resp.StatusCode)
		w.Write(raw)
		return
	}
	shaped, err := s.contract.Shape(schema, decoded)
	if err != nil {
		// The core broke the contract. Fail closed rather than guess what is safe to show.
		s.log.Error("upstream response does not fit the contract", "endpoint", e.Name, "err", err, "trace_id", traceID(r))
		s.problem(w, r, http.StatusBadGateway, "upstream-error", "Service unavailable", "Try again shortly.")
		return
	}
	if m, ok := shaped.(map[string]any); ok && resp.StatusCode >= 400 {
		m["trace_id"] = traceID(r) // always ours, so a user can quote it
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(resp.StatusCode)
	json.NewEncoder(w).Encode(shaped)
}

func (s *Server) upstreamFailed(w http.ResponseWriter, r *http.Request, e *contract.Endpoint, err error) {
	s.log.Warn("upstream failed", "endpoint", e.Name, "service", e.Service, "err", err, "trace_id", traceID(r))
	if e.Degrade != nil {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Degraded", "true")
		w.WriteHeader(e.Degrade.Status)
		io.WriteString(w, e.Degrade.Body)
		return
	}
	if errors.Is(err, context.DeadlineExceeded) {
		s.problem(w, r, http.StatusGatewayTimeout, "upstream-timeout", "Timed out", "The service took too long; try again.")
		return
	}
	s.problem(w, r, http.StatusBadGateway, "upstream-unavailable", "Service unavailable", "Try again shortly.")
}
