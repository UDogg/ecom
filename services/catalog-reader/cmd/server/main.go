// Command server is catalog-reader: Babel Bazaar's public catalogue, read-only, behind the gateway guard.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"babelbazaar/catalog-reader/internal/catalog"
	"babelbazaar/catalog-reader/internal/gate"
	"babelbazaar/catalog-reader/internal/jwt"
)

// privileged admits verified SYSTEM and break-glass tokens (aud=core).
func privileged(v *jwt.Verifier) gate.Privileged {
	return func(r *http.Request) (string, bool) {
		h := r.Header.Get("Authorization")
		if !strings.HasPrefix(h, "Bearer ") {
			return "", false
		}
		c, err := v.Verify(r.Context(), strings.TrimSpace(h[7:]))
		switch {
		case err != nil:
			return "", false
		case c.BreakGlass:
			return "BREAK-GLASS " + c.BgBy + " as " + c.Subject + " (grant " + c.BgGrant + ", reason " + strconv.Quote(c.BgReason) + ")", true
		case c.IsSystem():
			return "SYSTEM " + c.Service, true
		}
		return "", false
	}
}

func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func main() {
	addr := env("CATALOG_LISTEN_ADDR", ":8082")
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" { // the image has no shell or curl
		resp, err := (&http.Client{Timeout: 2 * time.Second}).Get("http://127.0.0.1" + addr + "/healthz")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		os.Exit(0)
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "catalog-reader")

	guard, err := gate.New(os.Getenv("GATEWAY_SECRET"), os.Getenv("GATEWAY_ALLOW_UNGUARDED") == "true",
		[]string{"/healthz", "/readyz"}, log)
	if err != nil {
		log.Error("refusing to start", "err", err)
		os.Exit(1)
	}
	// JWKS over the internal network; privileged tokens must be aud=core.
	guard.Privileged = privileged(&jwt.Verifier{
		Issuer:   env("PUBLIC_AUTH_ISSUER_URL", "http://localhost:8080"),
		Audience: "core",
		Leeway:   30 * time.Second,
		Source: jwt.HTTPKeySource(&http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil}},
			env("INTERNAL_AUTH_JWKS_URL", "http://auth-service:8081/oauth2/jwks")),
	})

	cfg, err := pgxpool.ParseConfig("")
	if err != nil {
		log.Error("bad database config", "err", err)
		os.Exit(1)
	}
	cc := cfg.ConnConfig
	cc.Host, cc.Database = env("POSTGRES_HOST", "postgres"), env("POSTGRES_DB", "shop")
	port, _ := strconv.Atoi(env("POSTGRES_PORT", "5432"))
	cc.Port = uint16(port)
	cc.User, cc.Password = env("CATALOG_READER_DB_USER", "catalog_reader"), os.Getenv("CATALOG_READER_DB_PASSWORD")
	cc.ConnectTimeout = 5 * time.Second
	cc.RuntimeParams["search_path"] = env("CATALOG_DB_SCHEMA", "commerce")
	cc.RuntimeParams["statement_timeout"] = "3000"
	cc.RuntimeParams["default_transaction_read_only"] = "on" // belt and braces on top of the SELECT-only grant
	cfg.MaxConns = 20
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		log.Error("cannot create database pool", "err", err)
		os.Exit(1)
	}
	defer pool.Close()

	pageSize, _ := strconv.Atoi(env("CATALOG_PAGE_SIZE", "24"))
	mux := http.NewServeMux()
	(&catalog.Handler{DB: pool, AssetBaseURL: env("PUBLIC_ASSET_BASE_URL", "http://localhost:9000/product-images"),
		PageSize: pageSize, Log: log}).Routes(mux)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"status":"ok","service":"catalog-reader"}`))
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		w.Header().Set("Content-Type", "application/json")
		if err := pool.Ping(ctx); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte(`{"status":"unavailable","postgres":"down"}`))
			return
		}
		w.Write([]byte(`{"status":"ready","postgres":"ok"}`))
	})

	srv := &http.Server{Addr: addr, Handler: guard.Wrap(mux), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
	stop, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	go func() {
		<-stop.Done()
		ctx, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		srv.Shutdown(ctx)
	}()
	log.Info("listening", "addr", addr)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Error("server failed", "err", err)
		os.Exit(1)
	}
}
