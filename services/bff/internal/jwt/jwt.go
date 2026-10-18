// Package jwt verifies auth-service access tokens: RS256 only, against a
// locally cached JWKS, checking issuer, audience, expiry and not-before.
//
// The key cache keeps the last good keys when auth-service is unreachable,
// refetches early for an unknown kid (rate limited), and never accepts any
// algorithm but RS256: "none", HS256 and friends are refused by construction.
package jwt

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

var (
	ErrMalformed    = errors.New("malformed token")
	ErrAlgorithm    = errors.New("unsupported signing algorithm")
	ErrSignature    = errors.New("bad signature")
	ErrUnknownKey   = errors.New("unknown signing key")
	ErrKeysMissing  = errors.New("signing keys unavailable")
	ErrExpired      = errors.New("token expired")
	ErrNotYetValid  = errors.New("token not yet valid")
	ErrIssuer       = errors.New("wrong issuer")
	ErrAudience     = errors.New("wrong audience")
	ErrMissingClaim = errors.New("missing required claim")
)

// Claims are the parts of an access token the BFF uses.
type Claims struct {
	Subject   string   `json:"sub"`
	Issuer    string   `json:"iss"`
	Email     string   `json:"email"`
	Roles     []string `json:"roles"`
	ExpiresAt int64    `json:"exp"`
	NotBefore int64    `json:"nbf"`
	Audience  audience `json:"aud"`
}

func (c Claims) HasRole(role string) bool {
	for _, r := range c.Roles {
		if r == role {
			return true
		}
	}
	return false
}

// audience accepts the string or array forms of "aud".
type audience []string

func (a *audience) UnmarshalJSON(b []byte) error {
	var one string
	if err := json.Unmarshal(b, &one); err == nil {
		*a = audience{one}
		return nil
	}
	var many []string
	if err := json.Unmarshal(b, &many); err != nil {
		return err
	}
	*a = many
	return nil
}

// KeySource fetches a JWKS document.
type KeySource func(ctx context.Context) ([]byte, error)

// HTTPKeySource fetches JWKS from url with client.
func HTTPKeySource(client *http.Client, url string) KeySource {
	return func(ctx context.Context) ([]byte, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("jwks: HTTP %d", resp.StatusCode)
		}
		var buf strings.Builder
		_, err = ioCopyLimited(&buf, resp, 1<<20)
		return []byte(buf.String()), err
	}
}

// Verifier checks tokens. Safe for concurrent use.
type Verifier struct {
	Issuer        string
	Audience      string
	Leeway        time.Duration
	RefreshEvery  time.Duration
	MinRefetchGap time.Duration
	Source        KeySource
	Now           func() time.Time

	mu          sync.Mutex
	keys        map[string]*rsa.PublicKey
	fetchedAt   time.Time
	attemptedAt time.Time
}

func (v *Verifier) now() time.Time {
	if v.Now != nil {
		return v.Now()
	}
	return time.Now()
}

func (v *Verifier) refresh(ctx context.Context, now time.Time) {
	v.attemptedAt = now
	raw, err := v.Source(ctx)
	if err != nil {
		return // keep the last good keys
	}
	keys, err := parseJWKS(raw)
	if err != nil || len(keys) == 0 {
		return
	}
	v.keys, v.fetchedAt = keys, now
}

func (v *Verifier) key(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	now := v.now()
	gap := v.MinRefetchGap
	if gap == 0 {
		gap = 30 * time.Second
	}
	every := v.RefreshEvery
	if every == 0 {
		every = 5 * time.Minute
	}
	if (v.keys == nil || now.Sub(v.fetchedAt) >= every) && (v.attemptedAt.IsZero() || now.Sub(v.attemptedAt) >= gap) {
		v.refresh(ctx, now)
	}
	if k, ok := v.keys[kid]; ok {
		return k, nil
	}
	if now.Sub(v.attemptedAt) >= gap {
		v.refresh(ctx, now)
		if k, ok := v.keys[kid]; ok {
			return k, nil
		}
	}
	if v.keys == nil {
		return nil, ErrKeysMissing
	}
	return nil, ErrUnknownKey
}

// Ready reports whether signing keys are loaded (fetching them if not).
func (v *Verifier) Ready(ctx context.Context) error {
	v.mu.Lock()
	defer v.mu.Unlock()
	now := v.now()
	if v.keys == nil && (v.attemptedAt.IsZero() || now.Sub(v.attemptedAt) >= time.Second) {
		v.refresh(ctx, now)
	}
	if v.keys == nil {
		return ErrKeysMissing
	}
	return nil
}

// Verify returns the token's claims if, and only if, it is a valid access token.
func (v *Verifier) Verify(ctx context.Context, token string) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return Claims{}, ErrMalformed
	}
	var header struct {
		Alg string `json:"alg"`
		Kid string `json:"kid"`
	}
	if err := decodeSegment(parts[0], &header); err != nil {
		return Claims{}, ErrMalformed
	}
	if header.Alg != "RS256" {
		return Claims{}, ErrAlgorithm
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return Claims{}, ErrMalformed
	}
	key, err := v.key(ctx, header.Kid)
	if err != nil {
		return Claims{}, err
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig) != nil {
		return Claims{}, ErrSignature
	}
	var claims Claims
	if err := decodeSegment(parts[1], &claims); err != nil {
		return Claims{}, ErrMalformed
	}
	now := v.now()
	switch {
	case claims.Subject == "" || claims.ExpiresAt == 0:
		return Claims{}, ErrMissingClaim
	case now.After(time.Unix(claims.ExpiresAt, 0).Add(v.Leeway)):
		return Claims{}, ErrExpired
	case claims.NotBefore != 0 && now.Add(v.Leeway).Before(time.Unix(claims.NotBefore, 0)):
		return Claims{}, ErrNotYetValid
	case claims.Issuer != v.Issuer:
		return Claims{}, ErrIssuer
	case v.Audience != "" && !contains(claims.Audience, v.Audience):
		return Claims{}, ErrAudience
	}
	return claims, nil
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func decodeSegment(seg string, into any) error {
	raw, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, into)
}

func parseJWKS(raw []byte) (map[string]*rsa.PublicKey, error) {
	var doc struct {
		Keys []struct {
			Kty, Kid, Use, N, E string
		} `json:"keys"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range doc.Keys {
		if k.Kty != "RSA" || (k.Use != "" && k.Use != "sig") {
			continue
		}
		n, err1 := base64.RawURLEncoding.DecodeString(k.N)
		e, err2 := base64.RawURLEncoding.DecodeString(k.E)
		if err1 != nil || err2 != nil || len(e) > 4 {
			continue
		}
		pub := &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
		if pub.N.BitLen() < 2048 {
			continue // refuse weak keys outright
		}
		keys[k.Kid] = pub
	}
	return keys, nil
}
