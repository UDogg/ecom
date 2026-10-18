package gate

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

const secret = "gate-test-secret-0123456789abcdef0123"

func TestMatchesSharedCrossLanguageVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "docs", "contracts", "gateway-signing.vectors.json"))
	if err != nil {
		t.Skip("shared vectors not available")
	}
	var vf struct {
		Secret  string
		Vectors []struct{ Name, Timestamp, Nonce, Method, Path, Query, Authorization, Canonical, Signature string }
	}
	json.Unmarshal(raw, &vf)
	for _, v := range vf.Vectors {
		if Canonical(v.Timestamp, v.Nonce, v.Method, v.Path, v.Query, v.Authorization) != v.Canonical ||
			Sign([]byte(vf.Secret), v.Timestamp, v.Nonce, v.Method, v.Path, v.Query, v.Authorization) != v.Signature {
			t.Errorf("%s: mismatch", v.Name)
		}
	}
}

func guarded(t *testing.T) (http.Handler, *Guard) {
	g, err := New(secret, false, []string{"/healthz"}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return g.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "ok") })), g
}

func signed(method, target string, ts int64, nonce string) *http.Request {
	r := httptest.NewRequest(method, target, nil)
	s := strconv.FormatInt(ts, 10)
	r.Header.Set("X-Gateway-Timestamp", s)
	r.Header.Set("X-Gateway-Nonce", nonce)
	r.Header.Set("X-Gateway-Signature", Sign([]byte(secret), s, nonce, method, r.URL.EscapedPath(), r.URL.RawQuery, ""))
	return r
}

func serve(h http.Handler, r *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, r)
	return rec
}

func TestUnsignedGetsAnEmpty403(t *testing.T) {
	h, _ := guarded(t)
	rec := serve(h, httptest.NewRequest("GET", "/api/catalog/products", nil))
	if rec.Code != 403 || rec.Body.Len() != 0 {
		t.Fatalf("%d %q", rec.Code, rec.Body)
	}
}

func TestSignedOnceThenReplayRefused(t *testing.T) {
	h, _ := guarded(t)
	r := signed("GET", "/api/catalog/products?q=mug", time.Now().Unix(), "0123456789abcdef01234567")
	if serve(h, r).Code != 200 {
		t.Fatal("valid signature refused")
	}
	if serve(h, r).Code != 403 {
		t.Fatal("replay accepted")
	}
}

func TestBoundToPathQueryAndTime(t *testing.T) {
	h, _ := guarded(t)
	r := signed("GET", "/api/catalog/products?q=mug", time.Now().Unix(), "aaaaaaaaaaaaaaaaaaaaaaaa")
	r.URL.RawQuery = "q=other"
	if serve(h, r).Code != 403 {
		t.Error("query swap accepted")
	}
	if serve(h, signed("GET", "/x", time.Now().Add(-61*time.Second).Unix(), "bbbbbbbbbbbbbbbbbbbbbbbb")).Code != 403 {
		t.Error("stale timestamp accepted")
	}
	if serve(h, signed("GET", "/x", time.Now().Add(61*time.Second).Unix(), "cccccccccccccccccccccccc")).Code != 403 {
		t.Error("future timestamp accepted")
	}
}

func TestOpenPathsAndFailClosed(t *testing.T) {
	h, _ := guarded(t)
	if serve(h, httptest.NewRequest("GET", "/healthz", nil)).Code != 200 {
		t.Error("health probe blocked")
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	if _, err := New("", false, nil, log); err == nil {
		t.Error("started without a secret")
	}
	if _, err := New("short", false, nil, log); err == nil {
		t.Error("started with a short secret")
	}
}

func TestFullCacheRefuses(t *testing.T) {
	_, g := guarded(t)
	now := time.Now()
	for i := 0; i < MaxNonces; i++ {
		g.seen[strconv.Itoa(i)] = now
	}
	if g.remember("fresh-nonce-0123456789", now) {
		t.Fatal("full cache accepted a nonce (it must refuse, never forget a live one)")
	}
}
