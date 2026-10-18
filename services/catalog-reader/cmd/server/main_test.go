package main

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http/httptest"
	"testing"
	"time"

	"babelbazaar/catalog-reader/internal/jwt"
)

var key, _ = rsa.GenerateKey(rand.Reader, 2048)

func token(claims map[string]any) string {
	seg := func(v any) string { b, _ := json.Marshal(v); return base64.RawURLEncoding.EncodeToString(b) }
	base := map[string]any{"iss": "http://bff", "exp": time.Now().Add(time.Minute).Unix()}
	for k, v := range claims {
		base[k] = v
	}
	h, b := seg(map[string]string{"alg": "RS256", "kid": "k"}), seg(base)
	d := sha256.Sum256([]byte(h + "." + b))
	sig, _ := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, d[:])
	return h + "." + b + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func TestPrivilegedAdmission(t *testing.T) {
	v := &jwt.Verifier{Issuer: "http://bff", Audience: "core", Source: func(context.Context) ([]byte, error) {
		return json.Marshal(map[string]any{"keys": []map[string]string{{"kty": "RSA", "kid": "k",
			"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
			"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}}})
	}}
	admit := privileged(v)
	cases := map[string]struct {
		claims map[string]any
		want   bool
	}{
		"system":                {map[string]any{"sub": "svc:svc_ops", "roles": []string{"system"}, "aud": "core", "service": "ops"}, true},
		"break-glass":           {map[string]any{"sub": "u1", "roles": []string{"customer"}, "aud": "core", "break_glass": true, "bg_by": "svc_ops"}, true},
		"user token (aud bff)":  {map[string]any{"sub": "u1", "roles": []string{"customer"}, "aud": "bff"}, false},
		"break-glass, aud bff":  {map[string]any{"sub": "u1", "aud": "bff", "break_glass": true}, false},
		"system role on person": {map[string]any{"sub": "u1", "roles": []string{"system"}, "aud": "core"}, false},
		"plain core token":      {map[string]any{"sub": "u1", "roles": []string{"customer"}, "aud": "core"}, false},
	}
	for name, c := range cases {
		r := httptest.NewRequest("GET", "/", nil)
		r.Header.Set("Authorization", "Bearer "+token(c.claims))
		if who, ok := admit(r); ok != c.want {
			t.Errorf("%s: admitted=%v (%s), want %v", name, ok, who, c.want)
		}
	}
	r := httptest.NewRequest("GET", "/", nil)
	if _, ok := admit(r); ok {
		t.Error("no header admitted")
	}
}
