package config

import (
	"strings"
	"testing"
)

const good = "0123456789abcdef0123456789abcdef"

func TestRefusesToStartWithWeakSecrets(t *testing.T) {
	cases := map[string][2]string{
		"no gateway secret":   {"", good},
		"short gateway":       {"short", good},
		"no client secret":    {good, ""},
		"short client secret": {good, "short"},
	}
	for name, c := range cases {
		t.Setenv("GATEWAY_SECRET", c[0])
		t.Setenv("OIDC_CLIENT_SECRET", c[1])
		if _, err := Load(); err == nil {
			t.Errorf("%s: started anyway", name)
		}
	}
}

func TestDefaultsAndSecureCookies(t *testing.T) {
	t.Setenv("GATEWAY_SECRET", good)
	t.Setenv("OIDC_CLIENT_SECRET", good)
	t.Setenv("PUBLIC_API_BASE_URL", "https://shop.example.com")
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !c.CookieSecure || c.Issuer != "https://shop.example.com" || c.Origin() != "https://shop.example.com" {
		t.Fatalf("%+v", c)
	}
}

func TestRejectsBadUpstreamURLs(t *testing.T) {
	t.Setenv("GATEWAY_SECRET", good)
	t.Setenv("OIDC_CLIENT_SECRET", good)
	t.Setenv("INTERNAL_COMMERCE_URL", "commerce-core:8000")
	if _, err := Load(); err == nil || !strings.Contains(err.Error(), "INTERNAL_COMMERCE_URL") {
		t.Fatalf("got %v", err)
	}
}
