package jwt

import (
	"errors"
	"io"
	"net/http"
	"strings"
)

func ioCopyLimited(dst *strings.Builder, resp *http.Response, limit int64) (int64, error) {
	n, err := io.Copy(dst, io.LimitReader(resp.Body, limit+1))
	if n > limit {
		return n, errors.New("jwks: response too large")
	}
	return n, err
}
