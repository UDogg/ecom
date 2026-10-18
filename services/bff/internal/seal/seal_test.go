package seal

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

type vectorFile struct {
	Secret  string `json:"secret"`
	Vectors []struct {
		Name, Timestamp, Nonce, Method, Path, Query, Authorization, Canonical, Signature string
	} `json:"vectors"`
}

func TestMatchesSharedCrossLanguageVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "docs", "contracts", "gateway-signing.vectors.json"))
	if err != nil {
		t.Skip("shared vectors not available:", err)
	}
	var vf vectorFile
	if err := json.Unmarshal(raw, &vf); err != nil {
		t.Fatal(err)
	}
	for _, v := range vf.Vectors {
		if got := Canonical(v.Timestamp, v.Nonce, v.Method, v.Path, v.Query, v.Authorization); got != v.Canonical {
			t.Errorf("%s: canonical mismatch\n got %q\nwant %q", v.Name, got, v.Canonical)
		}
		if got := Sign([]byte(vf.Secret), v.Timestamp, v.Nonce, v.Method, v.Path, v.Query, v.Authorization); got != v.Signature {
			t.Errorf("%s: signature %s, want %s", v.Name, got, v.Signature)
		}
	}
}

func TestTransportSignsTheRequestActuallySent(t *testing.T) {
	secret := []byte("transport-test-secret-0123456789abcdef")
	var got *http.Request
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { got = r }))
	defer upstream.Close()

	client := &http.Client{Transport: &Transport{Secret: secret}}
	req, _ := http.NewRequest(http.MethodPost, upstream.URL+"/api/commerce/checkout?x=1&y=two", nil)
	req.Header.Set("Authorization", "Bearer abc")
	if _, err := client.Do(req); err != nil {
		t.Fatal(err)
	}
	want := Sign(secret, got.Header.Get("X-Gateway-Timestamp"), got.Header.Get("X-Gateway-Nonce"), "POST",
		"/api/commerce/checkout", "x=1&y=two", "Bearer abc")
	if got.Header.Get("X-Gateway-Signature") != want {
		t.Fatal("upstream received a signature that does not match the request it received")
	}
	if len(got.Header.Get("X-Gateway-Nonce")) != 24 {
		t.Fatalf("nonce %q is not 24 hex characters", got.Header.Get("X-Gateway-Nonce"))
	}
	if req.Header.Get("X-Gateway-Signature") != "" {
		t.Fatal("the caller's request was mutated; Transport must sign a clone")
	}
}

func TestNoncesAreNeverReused(t *testing.T) {
	seen := map[string]bool{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := r.Header.Get("X-Gateway-Nonce")
		if seen[n] {
			t.Errorf("nonce %s reused", n)
		}
		seen[n] = true
	}))
	defer upstream.Close()
	client := &http.Client{Transport: &Transport{Secret: []byte("s")}}
	for i := 0; i < 200; i++ {
		resp, err := client.Get(upstream.URL + "/x")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}
}
