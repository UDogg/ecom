package web

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
)

func site(t *testing.T) *Site {
	t.Helper()
	s, err := Load(fstest.MapFS{
		"index.html":            {Data: []byte("<!doctype html><title>shop</title>" + strings.Repeat(" ", 100))},
		"index.html.br":         {Data: []byte("BR")},
		"index.html.gz":         {Data: []byte("GZ")},
		"assets/app-1a2b.js":    {Data: []byte("console.log(1)")},
		"assets/app-1a2b.js.gz": {Data: []byte("a much longer gzip than the file itself")},
		"favicon.svg":           {Data: []byte("<svg/>")},
	})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func get(s *Site, target string, headers ...string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("GET", target, nil)
	for i := 0; i+1 < len(headers); i += 2 {
		r.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}

func TestPrecompressedVariantsByAcceptEncoding(t *testing.T) {
	s := site(t)
	for accept, want := range map[string]string{"br, gzip": "BR", "gzip": "GZ", "br;q=0, gzip": "GZ", "": "<!doctype"} {
		w := get(s, "/", "Accept-Encoding", accept)
		if !strings.HasPrefix(w.Body.String(), want) {
			t.Errorf("Accept-Encoding %q: got %q", accept, w.Body.String()[:2])
		}
	}
	// A "compressed" variant larger than the file is not used.
	if w := get(s, "/assets/app-1a2b.js", "Accept-Encoding", "gzip"); w.Header().Get("Content-Encoding") != "" {
		t.Error("served a larger gzip")
	}
}

func TestCaching(t *testing.T) {
	s := site(t)
	asset := get(s, "/assets/app-1a2b.js")
	if !strings.Contains(asset.Header().Get("Cache-Control"), "immutable") || asset.Header().Get("Content-Type") != "text/javascript; charset=utf-8" {
		t.Errorf("asset headers: %v", asset.Header())
	}
	page := get(s, "/")
	if page.Header().Get("Cache-Control") != "no-cache" || !strings.Contains(page.Header().Get("Content-Security-Policy"), "script-src 'self'") {
		t.Errorf("page headers: %v", page.Header())
	}
	if w := get(s, "/", "If-None-Match", page.Header().Get("ETag")); w.Code != http.StatusNotModified || w.Body.Len() != 0 {
		t.Errorf("revalidation: %d", w.Code)
	}
}

func TestClientRoutesGetTheAppButMissingFilesDoNot(t *testing.T) {
	s := site(t)
	for _, route := range []string{"/products/7520ad71-763d-470f-b7a4-754f1949f811", "/cart", "/admin/orders"} {
		if w := get(s, route); w.Code != 200 || !strings.Contains(w.Header().Get("Content-Type"), "text/html") {
			t.Errorf("%s: %d", route, w.Code)
		}
	}
	for _, missing := range []string{"/assets/gone-0000.js", "/robots.txt", "/assets/x"} {
		if w := get(s, missing); w.Code != 404 {
			t.Errorf("%s: %d", missing, w.Code)
		}
	}
	r := httptest.NewRequest("POST", "/", nil)
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST: %d", w.Code)
	}
}

func TestAnAppIsRequired(t *testing.T) {
	if _, err := Load(fstest.MapFS{"app.js": {Data: []byte("x")}}); err == nil {
		t.Fatal("loaded without index.html")
	}
}
