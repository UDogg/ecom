package io.babelbazaar.auth;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.util.HexFormat;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Nothing reaches auth-service except through the BFF (docs/request-signing.md). Runs before Spring Security, so an
 * unsigned request is refused with an empty 403 before any session, form or token is looked at.
 *
 * <p>Signature: hex HMAC-SHA256(secret, "v1\n" ts "\n" nonce "\n" METHOD "\n" path "\n" query "\n" hex(sha256(Authorization))),
 * good for one method, raw path, raw query and token, for 60 seconds, once. The same function lives in the Go BFF and the
 * Python and Go services; docs/contracts/gateway-signing.vectors.json keeps them (and Ensuretech's GatewaySeal) in step.
 */
public class GatewayGuardFilter extends OncePerRequestFilter {

    static final String VERSION = "v1";
    static final long WINDOW_SECONDS = 60;
    static final int MAX_NONCES = 200_000;
    static final int MIN_SECRET_LENGTH = 32;

    private static final Logger log = LoggerFactory.getLogger(GatewayGuardFilter.class);

    private final byte[] secret;
    private final Set<String> openPaths;
    private final Clock clock;
    private final ConcurrentHashMap<String, Long> seen = new ConcurrentHashMap<>();
    private final java.util.function.Predicate<String> privileged;

    public GatewayGuardFilter(String secret, boolean allowUnguarded, Set<String> openPaths, Clock clock) {
        this(secret, allowUnguarded, openPaths, clock, header -> false);
    }

    /**
     * @param privileged says whether an Authorization header holds a verified SYSTEM or break-glass token: the only
     *                   way in without the BFF's signature (docs/break-glass.md)
     */
    public GatewayGuardFilter(String secret, boolean allowUnguarded, Set<String> openPaths, Clock clock,
                              java.util.function.Predicate<String> privileged) {
        String s = secret == null ? "" : secret;
        if (allowUnguarded) {
            log.warn("GATEWAY_ALLOW_UNGUARDED is on: requests that bypass the BFF are NOT refused. Local development and tests only.");
        } else if (s.isEmpty()) {
            throw new IllegalStateException("GATEWAY_SECRET is not set, so auth-service would answer anyone who can reach it. "
                    + "Set it to the BFF's secret; GATEWAY_ALLOW_UNGUARDED=true is for local development only.");
        } else if (s.length() < MIN_SECRET_LENGTH) {
            throw new IllegalStateException("GATEWAY_SECRET is shorter than " + MIN_SECRET_LENGTH + " characters (openssl rand -hex 32).");
        }
        this.secret = s.getBytes(StandardCharsets.UTF_8);
        this.openPaths = Set.copyOf(openPaths);
        this.clock = clock;
        this.privileged = privileged;
    }

    static String canonical(String ts, String nonce, String method, String path, String query, String authorization) {
        return VERSION + "\n" + ts + "\n" + nonce + "\n" + method.toUpperCase(java.util.Locale.ROOT) + "\n" + path + "\n" + query + "\n"
                + sha256Hex(authorization == null ? "" : authorization);
    }

    static String sign(byte[] secret, String ts, String nonce, String method, String path, String query, String authorization) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            return HexFormat.of().formatHex(mac.doFinal(
                    canonical(ts, nonce, method, path, query, authorization).getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String sha256Hex(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (secret.length == 0 || openPaths.contains(request.getRequestURI()) || signed(request)) {
            chain.doFilter(request, response);
            return;
        }
        if (privileged.test(request.getHeader("Authorization"))) {
            log.warn("PRIVILEGED request without the BFF: {} {} from {}", request.getMethod(), request.getRequestURI(),
                    request.getRemoteAddr());
            chain.doFilter(request, response);
            return;
        }
        log.info("refused a request that did not come through the BFF: {} {} from {}", request.getMethod(),
                request.getRequestURI(), request.getRemoteAddr());
        response.setStatus(HttpServletResponse.SC_FORBIDDEN);
        response.setContentLength(0);
    }

    private boolean signed(HttpServletRequest request) {
        String ts = header(request, "X-Gateway-Timestamp");
        String nonce = header(request, "X-Gateway-Nonce");
        String signature = header(request, "X-Gateway-Signature");
        if (!ts.matches("\\d{1,12}") || nonce.length() < 16 || nonce.length() > 64 || signature.length() != 64) {
            return false;
        }
        long now = clock.millis() / 1000;
        if (Math.abs(now - Long.parseLong(ts)) > WINDOW_SECONDS) {
            return false;
        }
        // getRequestURI() and getQueryString() are raw (not decoded): exactly what the BFF signed.
        String query = request.getQueryString() == null ? "" : request.getQueryString();
        String expected = sign(secret, ts, nonce, request.getMethod(), request.getRequestURI(), query,
                header(request, "Authorization"));
        if (!MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII), signature.getBytes(StandardCharsets.US_ASCII))) {
            return false;
        }
        return remember(nonce, now);
    }

    /** Only after the signature checks out, so strangers cannot fill the cache. A full cache refuses rather than forgets. */
    private boolean remember(String nonce, long now) {
        if (seen.size() >= MAX_NONCES) {
            seen.entrySet().removeIf(e -> now - e.getValue() > WINDOW_SECONDS * 2);
            if (seen.size() >= MAX_NONCES) {
                return false;
            }
        }
        return seen.putIfAbsent(nonce, now) == null;
    }

    private static String header(HttpServletRequest request, String name) {
        String value = request.getHeader(name);
        return value == null ? "" : value;
    }
}
