package server

import (
	"bytes"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"babelbazaar/bff/internal/config"
	"babelbazaar/bff/internal/contract"
	"babelbazaar/bff/internal/seal"
)

var (
	secret        = []byte("bff-test-gateway-secret-0123456789abcdef")
	clientSecret  = "bff-test-client-secret-0123456789abcdef"
	signingKey, _ = rsa.GenerateKey(rand.Reader, 2048)
)

const publicURL = "http://localhost:8080"

// ---- fake core services ------------------------------------------------------

type recorded struct {
	Method, Path, Query, Auth, Body string
	Header                          http.Header
}

type fakeCore struct {
	mu       sync.Mutex
	t        *testing.T
	requests []recorded
	handler  func(w http.ResponseWriter, r *http.Request)
	seen     map[string]bool
}

// verify is what every real core guard does; an unsigned or replayed call fails the test.
func (f *fakeCore) verify(r *http.Request) bool {
	ts, nonce, sig := r.Header.Get("X-Gateway-Timestamp"), r.Header.Get("X-Gateway-Nonce"), r.Header.Get("X-Gateway-Signature")
	want := seal.Sign(secret, ts, nonce, r.Method, r.URL.EscapedPath(), r.URL.RawQuery, r.Header.Get("Authorization"))
	if sig == "" || sig != want || f.seen[nonce] {
		return false
	}
	f.seen[nonce] = true
	return true
}

func newCore(t *testing.T, h func(w http.ResponseWriter, r *http.Request)) (*fakeCore, *httptest.Server) {
	f := &fakeCore{t: t, handler: h, seen: map[string]bool{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		ok := f.verify(r)
		body, _ := io.ReadAll(r.Body)
		f.requests = append(f.requests, recorded{r.Method, r.URL.EscapedPath(), r.URL.RawQuery, r.Header.Get("Authorization"), string(body), r.Header.Clone()})
		f.mu.Unlock()
		if !ok {
			t.Errorf("core received an unsigned or replayed request: %s %s", r.Method, r.URL)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		f.mu.Lock()
		handler := f.handler
		f.mu.Unlock()
		handler(w, r)
	}))
	t.Cleanup(srv.Close)
	return f, srv
}

func (f *fakeCore) last() recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) == 0 {
		f.t.Fatal("core received nothing")
	}
	return f.requests[len(f.requests)-1]
}

func (f *fakeCore) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.requests)
}

func jsonReply(status int, body string) func(http.ResponseWriter, *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		io.WriteString(w, body)
	}
}

// ---- fake auth-service -------------------------------------------------------

type fakeAuth struct {
	mu        sync.Mutex
	challenge string
	code      string
	issued    int
	revoked   []string
	ttl       time.Duration
	roles     []string
	saved     string // the authorize query, resumed after the login form (like Spring's saved request)
}

func mint(sub string, roles []string, ttl time.Duration) string {
	seg := func(v any) string { b, _ := json.Marshal(v); return base64.RawURLEncoding.EncodeToString(b) }
	head := seg(map[string]string{"alg": "RS256", "kid": "k1"})
	body := seg(map[string]any{"sub": sub, "iss": publicURL, "aud": "bff", "roles": roles, "email": sub + "@shop.local",
		"exp": time.Now().Add(ttl).Unix(), "iat": time.Now().Unix()})
	d := sha256.Sum256([]byte(head + "." + body))
	sig, _ := rsa.SignPKCS1v15(rand.Reader, signingKey, crypto.SHA256, d[:])
	return head + "." + body + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func (a *fakeAuth) handle(w http.ResponseWriter, r *http.Request) {
	a.mu.Lock()
	defer a.mu.Unlock()
	switch {
	case r.URL.Path == "/oauth2/jwks":
		json.NewEncoder(w).Encode(map[string]any{"keys": []map[string]string{{"kty": "RSA", "kid": "k1", "use": "sig",
			"n": base64.RawURLEncoding.EncodeToString(signingKey.N.Bytes()),
			"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(signingKey.E)).Bytes())}}})
	case r.URL.Path == "/oauth2/authorize":
		if c, err := r.Cookie("JSESSIONID"); err != nil || c.Value != "signed-in" {
			a.saved = r.URL.RawQuery
			http.SetCookie(w, &http.Cookie{Name: "JSESSIONID", Value: "anon", Path: "/"})
			http.SetCookie(w, &http.Cookie{Name: "evil", Value: "x", Path: "/"}) // must not reach the browser
			w.Header().Set("Location", "http://"+r.Header.Get("X-Forwarded-Host")+"/login")
			w.WriteHeader(http.StatusFound)
			return
		}
		a.challenge, a.code = r.URL.Query().Get("code_challenge"), "the-code"
		w.Header().Set("Location", r.URL.Query().Get("redirect_uri")+"?code=the-code&state="+url.QueryEscape(r.URL.Query().Get("state")))
		w.WriteHeader(http.StatusFound)
	case r.URL.Path == "/login" && r.Method == http.MethodGet:
		w.Header().Set("Content-Type", "text/html")
		io.WriteString(w, `<form method="post" action="/login"><input name="username"></form>`)
	case r.URL.Path == "/login" && r.Method == http.MethodPost:
		r.ParseForm()
		if r.PostForm.Get("password") != "right" {
			w.Header().Set("Location", "/login?error")
			w.WriteHeader(http.StatusFound)
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "JSESSIONID", Value: "signed-in", Path: "/"})
		w.Header().Set("Location", publicURL+"/oauth2/authorize?"+a.saved+"&continue")
		w.WriteHeader(http.StatusFound)
	case r.URL.Path == "/oauth2/token":
		id, sec, ok := r.BasicAuth()
		r.ParseForm()
		if !ok || id != "bff" || sec != clientSecret {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		roles := a.roles
		if roles == nil {
			roles = []string{"customer"}
		}
		switch r.PostForm.Get("grant_type") {
		case "authorization_code":
			sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
			if r.PostForm.Get("code") != a.code || base64.RawURLEncoding.EncodeToString(sum[:]) != a.challenge {
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			a.code = ""
		case "refresh_token":
			if !strings.HasPrefix(r.PostForm.Get("refresh_token"), "refresh-") {
				w.WriteHeader(http.StatusBadRequest)
				return
			}
		default:
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		a.issued++
		ttl := a.ttl
		if ttl == 0 {
			ttl = 10 * time.Minute
		}
		json.NewEncoder(w).Encode(map[string]any{"access_token": mint("user-1", roles, ttl), "token_type": "Bearer",
			"refresh_token": "refresh-" + string(rune('a'+a.issued)), "id_token": "id", "expires_in": int(ttl.Seconds())})
	case r.URL.Path == "/oauth2/revoke":
		r.ParseForm()
		a.revoked = append(a.revoked, r.PostForm.Get("token"))
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

// ---- harness -----------------------------------------------------------------

type harness struct {
	t        *testing.T
	srv      *Server
	h        http.Handler
	auth     *fakeAuth
	authCore *fakeCore
	commerce *fakeCore
	catalog  *fakeCore
	intel    *fakeCore
	intelSrv *httptest.Server
	logs     *bytes.Buffer
	cookies  map[string]string
}

func newHarness(t *testing.T) *harness {
	hs := &harness{t: t, auth: &fakeAuth{}, logs: &bytes.Buffer{}, cookies: map[string]string{}}
	var authSrv, commerceSrv, catalogSrv *httptest.Server
	hs.authCore, authSrv = newCore(t, hs.auth.handle)
	hs.commerce, commerceSrv = newCore(t, jsonReply(200, `{"items":[],"total":0,"page":1,"page_size":24,"pages":0}`))
	hs.catalog, catalogSrv = newCore(t, jsonReply(200, `{"items":[{"id":"p1","sku":"MUG","name":"Mug","price_minor":1299,"currency":"USD",
		"in_stock":true,"status":"active","available_quantity":40,"category":{"slug":"kitchen","name":"Kitchen"},
		"images":[{"key":"uploads/m.png","url":"http://img/m.png","position":0}]}],"total":1,"page":1,"page_size":24,"pages":1}`))
	hs.intel, hs.intelSrv = newCore(t, jsonReply(200, `{"items":[],"strategy":"popular","degraded":false}`))
	u := func(s *httptest.Server) *url.URL { p, _ := url.Parse(s.URL); return p }
	pub, _ := url.Parse(publicURL)
	cfg := &config.Config{PublicURL: pub, Issuer: publicURL, GatewaySecret: secret, ClientID: "bff", ClientSecret: clientSecret,
		Upstreams:   map[string]*url.URL{"auth": u(authSrv), "commerce": u(commerceSrv), "catalog": u(catalogSrv), "intelligence": u(hs.intelSrv)},
		SessionIdle: 30 * time.Minute, SessionLifetime: 8 * time.Hour, MaxSessions: 1000, MaxBodyBytes: 256 << 10,
		UpstreamTimeout: 2 * time.Second, RateDefaultPerMinute: 1000, RateAuthPerMinute: 100, RateRegisterPerMinute: 5,
		RateCheckoutPerMinute: 100}
	c, err := contract.Load(contract.Default, []string{"auth", "commerce", "catalog", "intelligence"}, LimitNames())
	if err != nil {
		t.Fatal(err)
	}
	hs.srv = New(cfg, c, slog.New(slog.NewJSONHandler(hs.logs, nil)), nil)
	hs.h = hs.srv.Handler()
	return hs
}

type reply struct {
	*httptest.ResponseRecorder
	json map[string]any
}

func (hs *harness) do(method, target, body string, headers ...string) reply {
	hs.t.Helper()
	req := httptest.NewRequest(method, publicURL+target, strings.NewReader(body))
	req.RemoteAddr = "203.0.113.7:5555"
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	for k, v := range hs.cookies {
		req.AddCookie(&http.Cookie{Name: k, Value: v})
	}
	rec := httptest.NewRecorder()
	hs.h.ServeHTTP(rec, req)
	for _, c := range rec.Result().Cookies() {
		if c.MaxAge < 0 {
			delete(hs.cookies, c.Name)
		} else {
			hs.cookies[c.Name] = c.Value
		}
	}
	var m map[string]any
	json.Unmarshal(rec.Body.Bytes(), &m)
	return reply{rec, m}
}

// signIn walks the real browser flow: /auth/login, proxied authorize and form, callback.
func (hs *harness) signIn() {
	hs.t.Helper()
	r := hs.do("GET", "/auth/login?return_to=/orders", "")
	authorize := r.Header().Get("Location")
	if r.Code != 302 || !strings.HasPrefix(authorize, "/oauth2/authorize?") {
		hs.t.Fatalf("login did not redirect to authorize: %d %s", r.Code, authorize)
	}
	r = hs.do("GET", authorize, "")
	if r.Header().Get("Location") != "/login" {
		hs.t.Fatalf("authorize -> %q, want /login", r.Header().Get("Location"))
	}
	r = hs.do("POST", "/login", "username=u&password=right", "Content-Type", "application/x-www-form-urlencoded")
	r = hs.do("GET", r.Header().Get("Location"), "")
	callback := r.Header().Get("Location")
	if !strings.HasPrefix(callback, "/auth/callback?") {
		hs.t.Fatalf("authorize after sign-in -> %q", callback)
	}
	before := hs.cookies["bb_session"]
	r = hs.do("GET", callback, "")
	if r.Code != 302 || r.Header().Get("Location") != "/orders" {
		hs.t.Fatalf("callback: %d %s %s", r.Code, r.Header().Get("Location"), r.Body.String())
	}
	if hs.cookies["bb_session"] == before {
		hs.t.Fatal("session id was not rotated at sign-in")
	}
}

func (hs *harness) csrf() string {
	return hs.do("GET", "/api/me", "").json["csrf_token"].(string)
}

// ---- tests -------------------------------------------------------------------

func TestUnknownPathsAndHoneypotsLookTheSame(t *testing.T) {
	hs := newHarness(t)
	unknown := hs.do("GET", "/nope", "")
	for _, bait := range []string{"/.env", "/wp-login.php", "/actuator/health", "/api/commerce/orders", "/oauth2/token", "/api/internal/x"} {
		r := hs.do("GET", bait, "")
		if r.Code != 404 || r.json["type"] != unknown.json["type"] || r.json["detail"] != unknown.json["detail"] {
			t.Errorf("%s: answered differently from an unknown path: %d %v", bait, r.Code, r.json)
		}
	}
	if strings.Count(hs.logs.String(), "honeypot touched") != 6 {
		t.Errorf("honeypots did not raise silent alerts:\n%s", hs.logs)
	}
	if hs.commerce.count()+hs.authCore.count() != 0 {
		t.Error("a honeypot reached a core service")
	}
}

func TestPublicCatalogIsSignedAnonymousAndShaped(t *testing.T) {
	hs := newHarness(t)
	r := hs.do("GET", "/api/catalog/products?q=ceramic+mug&page=1", "")
	if r.Code != 200 {
		t.Fatalf("%d %s", r.Code, r.Body)
	}
	got := hs.catalog.last()
	if got.Auth != "" || got.Path != "/api/catalog/products" || got.Query != "page=1&q=ceramic+mug" {
		t.Errorf("upstream call: %+v", got)
	}
	body := r.Body.String()
	for _, leaked := range []string{"available_quantity", `"status"`, `"key"`} {
		if strings.Contains(body, leaked) {
			t.Errorf("public catalog leaks %s: %s", leaked, body)
		}
	}
	for h, want := range map[string]string{"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Cache-Control": "no-store"} {
		if r.Header().Get(h) != want {
			t.Errorf("header %s = %q", h, r.Header().Get(h))
		}
	}
}

func TestQueryAndPathAreValidatedBeforeAnyUpstreamCall(t *testing.T) {
	hs := newHarness(t)
	for _, target := range []string{"/api/catalog/products?debug=1", "/api/catalog/products?page=-1", "/api/catalog/products/not-a-uuid",
		"/api/catalog/products?category=kitchen;debug=1", "/api/catalog/products?q=%zz"} {
		if r := hs.do("GET", target, ""); r.Code != 400 && r.Code != 404 {
			t.Errorf("%s -> %d", target, r.Code)
		}
	}
	if hs.catalog.count() != 0 {
		t.Error("an invalid request reached catalog-reader")
	}
	if r := hs.do("DELETE", "/api/catalog/products", ""); r.Code != 405 {
		t.Errorf("wrong method -> %d", r.Code)
	}
}

func TestProtectedEndpointsNeedASession(t *testing.T) {
	hs := newHarness(t)
	r := hs.do("GET", "/api/orders", "")
	if r.Code != 401 || r.json["login_url"] != "/auth/login" {
		t.Fatalf("%d %v", r.Code, r.json)
	}
	if hs.commerce.count() != 0 {
		t.Error("unauthenticated call reached commerce-core")
	}
}

func TestSignInFlowKeepsTokensServerSide(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	for name := range hs.cookies {
		if name != "bb_session" {
			t.Errorf("browser holds cookie %q after sign-in", name)
		}
	}
	me := hs.do("GET", "/api/me", "")
	if me.json["authenticated"] != true || strings.Contains(me.Body.String(), "eyJ") {
		t.Fatalf("me: %s", me.Body)
	}
	if r := hs.do("GET", "/api/orders", ""); r.Code != 200 {
		t.Fatalf("orders: %d %s", r.Code, r.Body)
	}
	if !strings.HasPrefix(hs.commerce.last().Auth, "Bearer eyJ") {
		t.Error("the user's token was not sent to commerce-core")
	}
	// The proxied sign-in pages: only auth-service's own cookie, forwarded headers set by the BFF.
	for _, req := range hs.authCore.requests {
		if strings.Contains(req.Header.Get("Cookie"), "bb_session") {
			t.Error("the BFF session cookie was forwarded to auth-service")
		}
		if req.Path == "/oauth2/authorize" && req.Header.Get("X-Forwarded-Host") != "localhost:8080" {
			t.Errorf("X-Forwarded-Host = %q", req.Header.Get("X-Forwarded-Host"))
		}
	}
}

func TestWrongPasswordStaysOnTheLoginPage(t *testing.T) {
	hs := newHarness(t)
	hs.do("GET", "/auth/login", "")
	r := hs.do("POST", "/login", "username=u&password=wrong", "Content-Type", "application/x-www-form-urlencoded")
	if r.Header().Get("Location") != "/login?error" {
		t.Fatalf("got %q", r.Header().Get("Location"))
	}
}

func TestOnlyTheSignInPagesAreProxied(t *testing.T) {
	hs := newHarness(t)
	for _, path := range []string{"/oauth2/introspect", "/userinfo", "/.well-known/openid-configuration", "/api/auth/users", "/logout"} {
		if r := hs.do("GET", path, ""); r.Code != 404 {
			t.Errorf("%s -> %d", path, r.Code)
		}
	}
	if hs.authCore.count() != 0 {
		t.Error("a non-allow-listed path reached auth-service")
	}
	if r := hs.do("GET", "/default-ui.css", ""); r.Code == 404 && hs.authCore.count() == 0 {
		t.Error("the sign-in stylesheet is not proxied")
	}
}

func TestLoginPageGetsAStrictCSP(t *testing.T) {
	hs := newHarness(t)
	r := hs.do("GET", "/login", "")
	if !strings.Contains(r.Header().Get("Content-Security-Policy"), "form-action 'self'") {
		t.Fatalf("CSP: %q", r.Header().Get("Content-Security-Policy"))
	}
}

func TestReturnToCannotBeAnOpenRedirect(t *testing.T) {
	for _, evil := range []string{"//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)"} {
		if got := safeReturnTo(evil); got != "/" {
			t.Errorf("%q -> %q", evil, got)
		}
	}
	if safeReturnTo("/orders?x=1") != "/orders?x=1" {
		t.Error("a local path was rejected")
	}
}

func TestCallbackRefusesAForgedState(t *testing.T) {
	hs := newHarness(t)
	hs.do("GET", "/auth/login", "")
	r := hs.do("GET", "/auth/callback?code=the-code&state=forged", "")
	if r.Code != 400 || hs.auth.issued != 0 {
		t.Fatalf("forged state: %d, tokens issued %d", r.Code, hs.auth.issued)
	}
	if !strings.Contains(hs.logs.String(), "sign-in state mismatch") {
		t.Error("no security log for a forged state")
	}
}

func TestCSRFIsRequiredForStateChanges(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	body := `{"items":[{"product_id":"7520ad71-763d-470f-b7a4-754f1949f811","quantity":1}]}`
	json := []string{"Content-Type", "application/json"}
	if r := hs.do("POST", "/api/checkout", body, json...); r.Code != 403 {
		t.Errorf("no CSRF token: %d", r.Code)
	}
	token := hs.csrf()
	if r := hs.do("POST", "/api/checkout", body, append(json, "X-CSRF-Token", token, "Origin", "https://evil.example")...); r.Code != 403 {
		t.Errorf("foreign Origin: %d", r.Code)
	}
	before := hs.commerce.count()
	r := hs.do("POST", "/api/checkout", body, append(json, "X-CSRF-Token", token, "Idempotency-Key", "k-1", "Origin", publicURL)...)
	if r.Code != 200 || hs.commerce.count() != before+1 {
		t.Fatalf("valid checkout: %d %s", r.Code, r.Body)
	}
	if got := hs.commerce.last(); got.Header.Get("Idempotency-Key") != "k-1" || got.Path != "/api/commerce/checkout" {
		t.Errorf("forwarded: %+v", got)
	}
}

func TestBodyIsValidatedAndReencoded(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	h := []string{"Content-Type", "application/json", "X-CSRF-Token", hs.csrf()}
	before := hs.commerce.count()
	r := hs.do("PUT", "/api/cart", `{"items":[{"product_id":"7520ad71-763d-470f-b7a4-754f1949f811","quantity":1,"unit_price_minor":1}]}`, h...)
	if r.Code != 400 || hs.commerce.count() != before {
		t.Fatalf("unknown field: %d", r.Code)
	}
	// Duplicate keys: the BFF forwards one canonical object, never the raw bytes.
	r = hs.do("PUT", "/api/cart", `{"items":[],"items":[{"product_id":"7520ad71-763d-470f-b7a4-754f1949f811","quantity":2}]}`, h...)
	if r.Code != 200 || strings.Count(hs.commerce.last().Body, `"items"`) != 1 {
		t.Fatalf("re-encoding: %d %s", r.Code, hs.commerce.last().Body)
	}
}

func TestCustomersCannotReachAdminEndpoints(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	before := hs.commerce.count()
	if r := hs.do("GET", "/api/admin/products", ""); r.Code != 403 {
		t.Fatalf("got %d", r.Code)
	}
	if hs.commerce.count() != before {
		t.Error("the call travelled to commerce-core")
	}
}

func TestOwnersReachAdminEndpoints(t *testing.T) {
	hs := newHarness(t)
	hs.auth.roles = []string{"customer", "shop_owner"}
	hs.signIn()
	if r := hs.do("GET", "/api/admin/products?status=archived", ""); r.Code != 200 {
		t.Fatalf("got %d %s", r.Code, r.Body)
	}
	if hs.commerce.last().Path != "/api/commerce/admin/products" {
		t.Error("wrong upstream path")
	}
}

func TestRecommendationsDegradeWhenIntelligenceIsDown(t *testing.T) {
	hs := newHarness(t)
	hs.intelSrv.Close()
	r := hs.do("GET", "/api/recommendations?limit=4", "")
	if r.Code != 200 || r.json["degraded"] != true || r.Header().Get("X-Degraded") != "true" {
		t.Fatalf("%d %s", r.Code, r.Body)
	}
}

func TestOtherUpstreamFailuresAre502(t *testing.T) {
	hs := newHarness(t)
	hs.catalog.handler = jsonReply(500, `{"detail":"stack trace with secrets"}`)
	r := hs.do("GET", "/api/catalog/products", "")
	if r.Code != 502 || strings.Contains(r.Body.String(), "secrets") {
		t.Fatalf("%d %s", r.Code, r.Body)
	}
}

func TestUpstreamThatBreaksTheContractFailsClosed(t *testing.T) {
	hs := newHarness(t)
	hs.catalog.handler = jsonReply(200, `{"items":[{"id":"p1","category":"not-an-object"}]}`)
	if r := hs.do("GET", "/api/catalog/products", ""); r.Code != 502 {
		t.Fatalf("got %d %s", r.Code, r.Body)
	}
}

func TestUpstreamErrorsAreShapedAsProblems(t *testing.T) {
	hs := newHarness(t)
	hs.catalog.handler = jsonReply(404, `{"type":"https://shop.local/errors/not-found","title":"Not found","status":404,
		"detail":"Product not found.","internal_sql":"SELECT secret"}`)
	r := hs.do("GET", "/api/catalog/products/7520ad71-763d-470f-b7a4-754f1949f811", "")
	if r.Code != 404 || strings.Contains(r.Body.String(), "SELECT") || r.Header().Get("Content-Type") != "application/problem+json" {
		t.Fatalf("%d %s", r.Code, r.Body)
	}
}

func TestGuardRefusalIsReportedAsAMisconfiguration(t *testing.T) {
	hs := newHarness(t)
	hs.catalog.handler = func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusForbidden) }
	if r := hs.do("GET", "/api/catalog/products", ""); r.Code != 502 {
		t.Fatalf("got %d", r.Code)
	}
	if !strings.Contains(hs.logs.String(), "GATEWAY_SECRET mismatch") {
		t.Error("no operator-facing log line")
	}
}

func TestRegistrationIsRateLimitedPerIP(t *testing.T) {
	hs := newHarness(t)
	hs.authCore.handler = jsonReply(201, `{"id":"u","email":"a@b.co","roles":["customer"]}`)
	token := hs.csrf()
	codes := []int{}
	for i := 0; i < 7; i++ {
		r := hs.do("POST", "/api/auth/register", `{"email":"a@b.co","password":"long-enough-password"}`,
			"Content-Type", "application/json", "X-CSRF-Token", token)
		codes = append(codes, r.Code)
	}
	if codes[4] == 429 || codes[6] != 429 {
		t.Fatalf("codes %v", codes)
	}
}

func TestOffOriginRedirectFromAuthServiceIsRefused(t *testing.T) {
	hs := newHarness(t)
	hs.authCore.handler = func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "https://evil.example/phish")
		w.WriteHeader(302)
	}
	if r := hs.do("GET", "/login", ""); r.Code != 502 || r.Header().Get("Location") != "" {
		t.Fatalf("%d %q", r.Code, r.Header().Get("Location"))
	}
}

func TestLogoutRevokesAndForgets(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	token := hs.csrf()
	if r := hs.do("POST", "/auth/logout", "", "X-CSRF-Token", token); r.Code != 204 {
		t.Fatalf("logout: %d", r.Code)
	}
	if len(hs.auth.revoked) != 1 || !strings.HasPrefix(hs.auth.revoked[0], "refresh-") {
		t.Errorf("refresh token not revoked: %v", hs.auth.revoked)
	}
	if r := hs.do("GET", "/api/orders", ""); r.Code != 401 {
		t.Errorf("still signed in after logout: %d", r.Code)
	}
}

func TestExpiringAccessTokenIsRefreshed(t *testing.T) {
	hs := newHarness(t)
	hs.auth.ttl = 10 * time.Second // inside the refresh margin
	hs.signIn()
	issued := hs.auth.issued
	hs.auth.ttl = 10 * time.Minute
	if r := hs.do("GET", "/api/orders", ""); r.Code != 200 {
		t.Fatalf("got %d", r.Code)
	}
	if hs.auth.issued != issued+1 {
		t.Fatalf("no refresh happened (issued %d -> %d)", issued, hs.auth.issued)
	}
}

func TestCoreRejectingTheTokenSignsTheSessionOut(t *testing.T) {
	hs := newHarness(t)
	hs.signIn()
	hs.commerce.handler = jsonReply(401, `{"type":"x","title":"t","status":401,"detail":"Invalid token"}`)
	hs.do("GET", "/api/orders", "")
	hs.commerce.handler = jsonReply(200, `{"items":[]}`)
	if r := hs.do("GET", "/api/orders", ""); r.Code != 401 {
		t.Fatalf("session survived a core 401: %d", r.Code)
	}
}
