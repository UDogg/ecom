// Command bff is Babel Bazaar's backend-for-frontend: the only service users can reach.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"babelbazaar/bff/internal/config"
	"babelbazaar/bff/internal/contract"
	"babelbazaar/bff/internal/server"
)

func main() {
	// `bff healthcheck`: the image has no shell or curl, so the binary probes itself.
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		addr := os.Getenv("BFF_LISTEN_ADDR")
		if addr == "" || addr[0] == ':' {
			addr = "127.0.0.1" + addr
			if addr == "127.0.0.1" {
				addr += ":8080"
			}
		}
		resp, err := (&http.Client{Timeout: 2 * time.Second}).Get("http://" + addr + "/healthz")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		os.Exit(0)
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})).With("service", "bff")
	cfg, err := config.Load()
	if err != nil {
		log.Error("refusing to start", "err", err)
		os.Exit(1)
	}
	c, err := contract.Load(contract.Default, []string{"auth", "commerce", "catalog", "intelligence"}, server.LimitNames())
	if err != nil {
		log.Error("refusing to start: the API contract is invalid", "err", err)
		os.Exit(1)
	}
	srv := server.New(cfg, c, log, nil)

	httpServer := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	go func() {
		for range time.Tick(time.Minute) {
			srv.Sessions().Sweep()
		}
	}()

	stop, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	go func() {
		<-stop.Done()
		ctx, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		httpServer.Shutdown(ctx)
	}()
	log.Info("listening", "addr", cfg.ListenAddr, "public_url", cfg.Origin(), "endpoints", len(c.Endpoints))
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Error("server failed", "err", err)
		os.Exit(1)
	}
}
