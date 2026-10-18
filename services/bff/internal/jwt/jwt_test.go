package jwt

import (
	"context"
	"crypto"
	"crypto/hmac"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math/big"
	"testing"
	"time"
)

var (
	keyA, _ = rsa.GenerateKey(rand.Reader, 2048)
	keyB, _ = rsa.GenerateKey(rand.Reader, 2048)
)

func jwk(kid string, k *rsa.PrivateKey) map[string]string {
	return map[string]string{"kty": "RSA", "kid": kid, "use": "sig",
		"n": base64.RawURLEncoding.EncodeToString(k.N.Bytes()),
		"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(k.E)).Bytes())}
}

func seg(v any) string {
	b, _ := json.Marshal(v)
	return base64.RawURLEncoding.EncodeToString(b)
}

func token(k *rsa.PrivateKey, kid string, claims map[string]any) string {
	head := seg(map[string]string{"alg": "RS256", "kid": kid})
	body := seg(claims)
	d := sha256.Sum256([]byte(head + "." + body))
	sig, _ := rsa.SignPKCS1v15(rand.Reader, k, crypto.SHA256, d[:])
	return head + "." + body + "." + base64.RawURLEncoding.EncodeToString(sig)
}

type fakeAuth struct {
	keys  []map[string]string
	up    bool
	calls int
}

func (f *fakeAuth) source(context.Context) ([]byte, error) {
	f.calls++
	if !f.up {
		return nil, errors.New("connection refused")
	}
	return json.Marshal(map[string]any{"keys": f.keys})
}

type clock struct{ t time.Time }

func setup() (*Verifier, *fakeAuth, *clock) {
	auth := &fakeAuth{keys: []map[string]string{jwk("a", keyA)}, up: true}
	c := &clock{time.Unix(1_800_000_000, 0)}
	return &Verifier{Issuer: "http://localhost:8080", Audience: "bff", Source: auth.source, Now: func() time.Time { return c.t }}, auth, c
}

func claims(c *clock, extra map[string]any) map[string]any {
	m := map[string]any{"sub": "u1", "iss": "http://localhost:8080", "aud": "bff", "roles": []string{"customer"},
		"email": "c@shop.local", "exp": c.t.Add(5 * time.Minute).Unix(), "nbf": c.t.Unix()}
	for k, v := range extra {
		m[k] = v
	}
	return m
}

func TestValidTokenYieldsClaims(t *testing.T) {
	v, _, c := setup()
	got, err := v.Verify(context.Background(), token(keyA, "a", claims(c, nil)))
	if err != nil || got.Subject != "u1" || !got.HasRole("customer") || got.Email != "c@shop.local" {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestRejections(t *testing.T) {
	v, _, c := setup()
	hs := func() string { // alg confusion: HS256 "signed" with the public key
		head, body := seg(map[string]string{"alg": "HS256", "kid": "a"}), seg(claims(c, nil))
		m := hmac.New(sha256.New, keyA.PublicKey.N.Bytes())
		m.Write([]byte(head + "." + body))
		return head + "." + body + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
	}
	cases := map[string]struct {
		tok  string
		want error
	}{
		"expired":        {token(keyA, "a", claims(c, map[string]any{"exp": c.t.Add(-time.Minute).Unix()})), ErrExpired},
		"not yet valid":  {token(keyA, "a", claims(c, map[string]any{"nbf": c.t.Add(time.Hour).Unix()})), ErrNotYetValid},
		"wrong issuer":   {token(keyA, "a", claims(c, map[string]any{"iss": "http://evil"})), ErrIssuer},
		"wrong audience": {token(keyA, "a", claims(c, map[string]any{"aud": "someone-else"})), ErrAudience},
		"no subject":     {token(keyA, "a", claims(c, map[string]any{"sub": ""})), ErrMissingClaim},
		"wrong key":      {token(keyB, "a", claims(c, nil)), ErrSignature},
		"unknown kid":    {token(keyB, "b", claims(c, nil)), ErrUnknownKey},
		"alg none":       {seg(map[string]string{"alg": "none"}) + "." + seg(claims(c, nil)) + ".", ErrAlgorithm},
		"alg confusion":  {hs(), ErrAlgorithm},
		"garbage":        {"not.a.jwt", ErrMalformed},
		"two segments":   {"a.b", ErrMalformed},
	}
	for name, tc := range cases {
		if _, err := v.Verify(context.Background(), tc.tok); !errors.Is(err, tc.want) {
			t.Errorf("%s: got %v, want %v", name, err, tc.want)
		}
	}
}

func TestAudienceArrayForm(t *testing.T) {
	v, _, c := setup()
	if _, err := v.Verify(context.Background(), token(keyA, "a", claims(c, map[string]any{"aud": []string{"x", "bff"}}))); err != nil {
		t.Fatal(err)
	}
}

func TestKeysCachedAndKeptThroughAnAuthOutage(t *testing.T) {
	v, auth, c := setup()
	for i := 0; i < 5; i++ {
		if _, err := v.Verify(context.Background(), token(keyA, "a", claims(c, nil))); err != nil {
			t.Fatal(err)
		}
	}
	if auth.calls != 1 {
		t.Fatalf("fetched JWKS %d times, want 1 (cached)", auth.calls)
	}
	auth.up = false
	c.t = c.t.Add(time.Hour)
	if _, err := v.Verify(context.Background(), token(keyA, "a", claims(c, nil))); err != nil {
		t.Fatalf("auth-service down should not break existing sessions: %v", err)
	}
}

func TestColdStartWithoutAuthService(t *testing.T) {
	v, auth, c := setup()
	auth.up = false
	if _, err := v.Verify(context.Background(), token(keyA, "a", claims(c, nil))); !errors.Is(err, ErrKeysMissing) {
		t.Fatalf("got %v", err)
	}
}

func TestRotationAndRateLimitedRefetch(t *testing.T) {
	v, auth, c := setup()
	v.Verify(context.Background(), token(keyA, "a", claims(c, nil)))
	for i := 0; i < 10; i++ {
		v.Verify(context.Background(), token(keyB, "forged", claims(c, nil)))
	}
	if auth.calls != 1 {
		t.Fatalf("unknown kids caused %d fetches inside the gap, want 1", auth.calls)
	}
	auth.keys = append(auth.keys, jwk("b", keyB))
	c.t = c.t.Add(31 * time.Second)
	if _, err := v.Verify(context.Background(), token(keyB, "b", claims(c, nil))); err != nil {
		t.Fatalf("rotated key not picked up: %v", err)
	}
}

func TestWeakKeysAreIgnored(t *testing.T) {
	weak, _ := rsa.GenerateKey(rand.Reader, 1024)
	keys, _ := parseJWKS([]byte(`{"keys":[` + string(must(json.Marshal(jwk("w", weak)))) + `]}`))
	if len(keys) != 0 {
		t.Fatal("a 1024-bit key was accepted")
	}
}

func must(b []byte, err error) []byte {
	if err != nil {
		panic(err)
	}
	return b
}
