package io.babelbazaar.auth;

import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {

    private final JdbcClient jdbc;

    public HealthController(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** Liveness: the process is up. */
    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Map.of("status", "ok", "service", "auth-service");
    }

    /** Readiness: the identity database is reachable. */
    @GetMapping("/readyz")
    public ResponseEntity<Map<String, String>> readyz() {
        try {
            jdbc.sql("SELECT 1").query(Integer.class).single();
            return ResponseEntity.ok(Map.of("status", "ready", "postgres", "ok"));
        } catch (RuntimeException e) {
            return ResponseEntity.status(503).body(Map.of("status", "unavailable", "postgres", e.getClass().getSimpleName()));
        }
    }
}
