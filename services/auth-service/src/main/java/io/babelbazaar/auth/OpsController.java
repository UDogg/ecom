package io.babelbazaar.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * Privileged access, for when the BFF is not the way in (docs/break-glass.md).
 *
 * <ul>
 *   <li>{@code POST /api/auth/service-token}: client credentials for a SYSTEM token (an open door: no BFF signature).</li>
 *   <li>{@code POST /api/auth/break-glass}: a SYSTEM token plus the break-glass password buys a short token acting as one
 *       named user. Every attempt is audited. Without a configured password the endpoint does not exist (404).</li>
 *   <li>{@code GET /api/auth/ops/break-glass-grants}: the audit trail, SYSTEM only.</li>
 * </ul>
 */
@RestController
public class OpsController {

    static final int MIN_REASON = 10, MAX_REASON = 300;

    private static final Logger log = LoggerFactory.getLogger("break_glass");

    private final ServiceAccounts serviceAccounts;
    private final UserAccounts users;
    private final PrivilegedTokens tokens;
    private final JdbcClient jdbc;
    private final AuthProperties props;
    private final Throttle serviceTokenFailures;
    private final Throttle breakGlassAttempts;

    public OpsController(ServiceAccounts serviceAccounts, UserAccounts users, PrivilegedTokens tokens, JdbcClient jdbc,
                         AuthProperties props) {
        this.serviceAccounts = serviceAccounts;
        this.users = users;
        this.tokens = tokens;
        this.jdbc = jdbc;
        this.props = props;
        this.serviceTokenFailures = new Throttle(props.breakGlass().attemptsPerMinute() * 2, Clock.systemUTC());
        this.breakGlassAttempts = new Throttle(props.breakGlass().attemptsPerMinute(), Clock.systemUTC());
    }

    private static <T> ResponseEntity<T> noStore(HttpStatus status, T body) {
        return ResponseEntity.status(status).cacheControl(CacheControl.noStore()).header("Pragma", "no-cache").body(body);
    }

    // ---- service accounts ---------------------------------------------------------------------------------------

    @PostMapping(path = "/api/auth/service-token", consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE)
    public ResponseEntity<Map<String, Object>> serviceToken(@RequestBody MultiValueMap<String, String> form,
                                                            HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        if (!"client_credentials".equals(form.getFirst("grant_type"))) {
            return noStore(HttpStatus.BAD_REQUEST, Map.of("error", "unsupported_grant_type"));
        }
        Optional<ServiceAccounts.Account> account = serviceAccounts.authenticate(form.getFirst("client_id"), form.getFirst("client_secret"));
        if (account.isEmpty()) {
            boolean throttled = !serviceTokenFailures.allow("ip:" + ip);
            log.warn("service-token refused for client {} from {}", safe(form.getFirst("client_id")), ip);
            return noStore(throttled ? HttpStatus.TOO_MANY_REQUESTS : HttpStatus.UNAUTHORIZED, Map.of("error", "invalid_client"));
        }
        PrivilegedTokens.Minted minted = tokens.system(account.get());
        log.info("SYSTEM token issued to service account {} ({})", account.get().name(), account.get().clientId());
        return noStore(HttpStatus.OK, Map.of("access_token", minted.token(), "token_type", "Bearer",
                "expires_in", props.serviceTokenTtl().toSeconds()));
    }

    // ---- break glass --------------------------------------------------------------------------------------------

    private boolean breakGlassEnabled() {
        String hash = props.breakGlass().passwordSha256();
        return hash != null && hash.matches("[0-9a-f]{64}");
    }

    private UUID audit(String outcome, String clientId, UUID user, String reason, String jti, java.time.Instant expires, String ip) {
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                INSERT INTO break_glass_grant (id, outcome, client_id, target_user_id, reason, jti, expires_at, remote_addr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""")
                .params(id, outcome, clientId, user, reason, jti,
                        expires == null ? null : java.sql.Timestamp.from(expires), ip)
                .update();
        return id;
    }

    private ResponseEntity<Map<String, Object>> refuse(String outcome, HttpStatus status, String clientId, UUID user, String reason, String ip) {
        audit(outcome, clientId, user, reason, "", null, ip);
        log.warn("BREAK-GLASS refused ({}) client={} user={} from {}", outcome, clientId.isEmpty() ? "-" : clientId, user, ip);
        return noStore(status, Map.of("error", outcome.toLowerCase()));
    }

    @PostMapping(path = "/api/auth/break-glass", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> breakGlass(@RequestHeader(value = "Authorization", required = false) String authorization,
                                                          @RequestBody(required = false) Map<String, Object> body,
                                                          HttpServletRequest request) {
        if (!breakGlassEnabled()) {
            return ResponseEntity.notFound().build(); // switched off: the door does not exist
        }
        String ip = request.getRemoteAddr();
        Map<String, Object> in = body == null ? Map.of() : body;
        String reason = String.valueOf(in.getOrDefault("reason", "")).strip();
        reason = reason.length() > MAX_REASON ? reason.substring(0, MAX_REASON) : reason;

        Optional<Jwt> system = Optional.ofNullable(authorization).filter(a -> a.startsWith("Bearer "))
                .flatMap(a -> tokens.verify(a.substring(7).trim())).filter(PrivilegedTokens::isSystem);
        String clientId = system.map(j -> j.getClaimAsString("client_id")).orElse("");
        if (!breakGlassAttempts.allow("ip:" + ip) || (!clientId.isEmpty() && !breakGlassAttempts.allow("client:" + clientId))) {
            return refuse("THROTTLED", HttpStatus.TOO_MANY_REQUESTS, clientId, null, reason, ip);
        }
        if (system.isEmpty()) {
            return refuse("NOT_SYSTEM", HttpStatus.FORBIDDEN, "", null, reason, ip);
        }
        byte[] presented = ServiceAccounts.sha256(String.valueOf(in.getOrDefault("break_glass_password", "")))
                .getBytes(StandardCharsets.US_ASCII);
        if (!MessageDigest.isEqual(presented, props.breakGlass().passwordSha256().getBytes(StandardCharsets.US_ASCII))) {
            return refuse("WRONG_PASSWORD", HttpStatus.FORBIDDEN, clientId, null, reason, ip);
        }
        UUID userId;
        try {
            userId = UUID.fromString(String.valueOf(in.get("user_id")));
        } catch (IllegalArgumentException e) {
            userId = null;
        }
        if (userId == null || reason.length() < MIN_REASON) {
            return refuse("BAD_REQUEST", HttpStatus.BAD_REQUEST, clientId, userId, reason, ip);
        }
        Optional<UserAccounts.Account> user = users.findActiveById(userId);
        if (user.isEmpty()) {
            return refuse("NO_SUCH_USER", HttpStatus.NOT_FOUND, clientId, userId, reason, ip);
        }
        Duration ttl = Duration.ofMinutes(Math.max(1, Math.min(60, props.breakGlass().minutes())));
        UUID grantId = UUID.randomUUID();
        PrivilegedTokens.Minted minted = tokens.breakGlass(user.get(), clientId, reason, ttl, grantId);
        jdbc.sql("""
                INSERT INTO break_glass_grant (id, outcome, client_id, target_user_id, reason, jti, expires_at, remote_addr)
                VALUES (?, 'GRANTED', ?, ?, ?, ?, ?, ?)""")
                .params(grantId, clientId, userId, reason, minted.jti(), java.sql.Timestamp.from(minted.expiresAt()), ip).update();
        log.warn("BREAK-GLASS granted: {} acts as user {} ({}, roles {}) for {} min. grant={} reason={}",
                clientId, userId, user.get().email(), user.get().roles(), ttl.toMinutes(), grantId, reason);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("access_token", minted.token());
        out.put("token_type", "Bearer");
        out.put("expires_in", ttl.toSeconds());
        out.put("acting_as", Map.of("user_id", userId.toString(), "email", user.get().email(), "roles", user.get().roles()));
        out.put("grant_id", grantId.toString());
        return noStore(HttpStatus.OK, out);
    }

    @GetMapping("/api/auth/ops/break-glass-grants")
    public ResponseEntity<Map<String, Object>> grants(@RequestHeader(value = "Authorization", required = false) String authorization) {
        boolean system = authorization != null && authorization.startsWith("Bearer ")
                && tokens.verify(authorization.substring(7).trim()).filter(PrivilegedTokens::isSystem).isPresent();
        if (!system) {
            return noStore(HttpStatus.FORBIDDEN, Map.of("error", "system_only"));
        }
        List<Map<String, Object>> rows = jdbc.sql("""
                SELECT id, attempted_at, outcome, client_id, target_user_id, reason, expires_at, remote_addr
                FROM break_glass_grant ORDER BY attempted_at DESC LIMIT 100""")
                .query((rs, n) -> {
                    Map<String, Object> r = new LinkedHashMap<>();
                    r.put("id", rs.getString("id"));
                    r.put("attempted_at", rs.getTimestamp("attempted_at").toInstant().toString());
                    r.put("outcome", rs.getString("outcome"));
                    r.put("client_id", rs.getString("client_id"));
                    r.put("target_user_id", rs.getString("target_user_id"));
                    r.put("reason", rs.getString("reason"));
                    r.put("expires_at", rs.getTimestamp("expires_at") == null ? null : rs.getTimestamp("expires_at").toInstant().toString());
                    r.put("remote_addr", rs.getString("remote_addr"));
                    return r;
                }).list();
        return noStore(HttpStatus.OK, Map.of("items", rows));
    }

    private static String safe(String s) {
        return s == null ? "-" : s.replaceAll("[^A-Za-z0-9_-]", "?").substring(0, Math.min(s.length(), 64));
    }
}
