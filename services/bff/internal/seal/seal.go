// Package seal signs every call the BFF makes to a core service
// (docs/request-signing.md):
//
//	X-Gateway-Signature = hex HMAC-SHA256(secret, "v1\n" ts "\n" nonce "\n" METHOD "\n" path "\n" query "\n" hex(sha256(Authorization)))
//
// The format is byte-compatible with Ensuretech's GatewaySeal and with every
// guard in this repository; docs/contracts/gateway-signing.vectors.json keeps
// them in step.
package seal

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const Version = "v1"

// Canonical is the exact text that gets signed.
func Canonical(ts, nonce, method, path, query, authorization string) string {
	sum := sha256.Sum256([]byte(authorization))
	return Version + "\n" + ts + "\n" + nonce + "\n" + strings.ToUpper(method) + "\n" + path + "\n" + query + "\n" + hex.EncodeToString(sum[:])
}

// Sign returns the lower-case hex HMAC-SHA256 of Canonical.
func Sign(secret []byte, ts, nonce, method, path, query, authorization string) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(Canonical(ts, nonce, method, path, query, authorization)))
	return hex.EncodeToString(mac.Sum(nil))
}

// Transport signs whatever is about to be sent, so no call site can forget.
type Transport struct {
	Secret []byte
	Base   http.RoundTripper
	Now    func() time.Time
}

func (t *Transport) RoundTrip(req *http.Request) (*http.Response, error) {
	now := time.Now
	if t.Now != nil {
		now = t.Now
	}
	nonce := make([]byte, 12)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	signed := req.Clone(req.Context())
	ts := strconv.FormatInt(now().Unix(), 10)
	n := hex.EncodeToString(nonce)
	path := signed.URL.EscapedPath()
	if path == "" {
		path = "/"
	}
	signed.Header.Set("X-Gateway-Timestamp", ts)
	signed.Header.Set("X-Gateway-Nonce", n)
	signed.Header.Set("X-Gateway-Signature", Sign(t.Secret, ts, n, signed.Method, path, signed.URL.RawQuery, signed.Header.Get("Authorization")))
	base := t.Base
	if base == nil {
		base = http.DefaultTransport
	}
	return base.RoundTrip(signed)
}
