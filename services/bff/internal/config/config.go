// Package config reads the BFF's settings from the environment and refuses to
// start with anything unsafe: no weak secrets, no missing upstreams.
package config

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	ListenAddr   string
	WebRoot      string   // the built storefront (index.html and assets/); empty: API only
	PublicURL    *url.URL // the BFF's public origin; also the OIDC issuer the browser sees
	Issuer       string
	CookieSecure bool

	GatewaySecret []byte
	ClientID      string
	ClientSecret  string

	Upstreams map[string]*url.URL // service name -> internal base URL

	SessionIdle     time.Duration
	SessionLifetime time.Duration
	MaxSessions     int
	MaxBodyBytes    int64
	UpstreamTimeout time.Duration
	TrustedProxies  []*net.IPNet

	RateDefaultPerMinute  int
	RateAuthPerMinute     int
	RateRegisterPerMinute int
	RateCheckoutPerMinute int
}

const minSecret = 32

func env(name, def string) string {
	if v, ok := os.LookupEnv(name); ok && v != "" {
		return v
	}
	return def
}

func envInt(name string, def int) (int, error) {
	v := env(name, "")
	if v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil || n <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return n, nil
}

func parseURL(name, def string) (*url.URL, error) {
	u, err := url.Parse(env(name, def))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("%s must be an absolute http(s) URL", name)
	}
	return u, nil
}

// Load reads and validates the configuration.
func Load() (*Config, error) {
	c := &Config{ListenAddr: env("BFF_LISTEN_ADDR", ":8080"), ClientID: env("OIDC_CLIENT_ID", "bff"), WebRoot: env("WEB_ROOT", "")}
	var err error
	if c.PublicURL, err = parseURL("PUBLIC_API_BASE_URL", "http://localhost:8080"); err != nil {
		return nil, err
	}
	c.Issuer = env("PUBLIC_AUTH_ISSUER_URL", strings.TrimRight(c.PublicURL.String(), "/"))
	// Secure cookies whenever the public URL is HTTPS; COOKIE_SECURE can force it on for localhost too.
	c.CookieSecure = c.PublicURL.Scheme == "https" || env("COOKIE_SECURE", "") == "true"

	c.GatewaySecret = []byte(env("GATEWAY_SECRET", ""))
	if len(c.GatewaySecret) < minSecret {
		return nil, fmt.Errorf("GATEWAY_SECRET must be at least %d characters: without it the core services cannot tell the BFF from anyone else", minSecret)
	}
	c.ClientSecret = env("OIDC_CLIENT_SECRET", "")
	if len(c.ClientSecret) < minSecret {
		return nil, fmt.Errorf("OIDC_CLIENT_SECRET must be at least %d characters", minSecret)
	}

	c.Upstreams = map[string]*url.URL{}
	for name, def := range map[string][2]string{
		"auth":         {"INTERNAL_AUTH_URL", "http://auth-service:8081"},
		"commerce":     {"INTERNAL_COMMERCE_URL", "http://commerce-core:8000"},
		"catalog":      {"INTERNAL_CATALOG_URL", "http://catalog-reader:8082"},
		"intelligence": {"INTERNAL_INTELLIGENCE_URL", "http://intelligence:8001"},
	} {
		if c.Upstreams[name], err = parseURL(def[0], def[1]); err != nil {
			return nil, err
		}
	}

	ints := []struct {
		name string
		def  int
		dst  *int
	}{
		{"SESSION_IDLE_MINUTES", 30, new(int)},
		{"SESSION_LIFETIME_MINUTES", 480, new(int)},
		{"MAX_SESSIONS", 100_000, &c.MaxSessions},
		{"MAX_BODY_KB", 256, new(int)},
		{"UPSTREAM_TIMEOUT_MS", 5000, new(int)},
		{"RATE_DEFAULT_PER_MINUTE", 300, &c.RateDefaultPerMinute},
		{"RATE_AUTH_PER_MINUTE", 10, &c.RateAuthPerMinute},
		{"RATE_REGISTER_PER_MINUTE", 5, &c.RateRegisterPerMinute},
		{"RATE_CHECKOUT_PER_MINUTE", 20, &c.RateCheckoutPerMinute},
	}
	for _, i := range ints {
		if *i.dst, err = envInt(i.name, i.def); err != nil {
			return nil, err
		}
	}
	c.SessionIdle = time.Duration(*ints[0].dst) * time.Minute
	c.SessionLifetime = time.Duration(*ints[1].dst) * time.Minute
	c.MaxBodyBytes = int64(*ints[3].dst) * 1024
	c.UpstreamTimeout = time.Duration(*ints[4].dst) * time.Millisecond

	for _, cidr := range strings.Split(env("TRUSTED_PROXY_CIDRS", ""), ",") {
		if cidr = strings.TrimSpace(cidr); cidr == "" {
			continue
		}
		_, n, err := net.ParseCIDR(cidr)
		if err != nil {
			return nil, fmt.Errorf("TRUSTED_PROXY_CIDRS: %w", err)
		}
		c.TrustedProxies = append(c.TrustedProxies, n)
	}
	return c, nil
}

// Origin is scheme://host[:port] of the public URL.
func (c *Config) Origin() string { return c.PublicURL.Scheme + "://" + c.PublicURL.Host }
