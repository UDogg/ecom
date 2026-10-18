package io.babelbazaar.auth;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.stereotype.Component;

/**
 * The two kinds of token that may reach a core service without the BFF's signature (docs/break-glass.md):
 *
 * <ul>
 *   <li><b>SYSTEM</b>: from a service account's super credentials. {@code roles=["system"]}, no user, no email, so it can
 *       never act as a customer or shop owner; it reaches only explicit ops endpoints.</li>
 *   <li><b>Break-glass</b>: acts as one named user (their real roles), {@code break_glass=true}, carrying the operator and
 *       the reason, short-lived, no refresh token.</li>
 * </ul>
 *
 * Both are RS256, signed with the same key as every other token, with audience {@code core} (never the BFF's).
 */
@Component
public class PrivilegedTokens {

    public static final String AUDIENCE = "core";
    public static final String SYSTEM_ROLE = "system";

    private final JwtEncoder encoder;
    private final JwtDecoder decoder;
    private final AuthProperties props;

    public PrivilegedTokens(JwtEncoder encoder, JwtDecoder decoder, AuthProperties props) {
        this.encoder = encoder;
        this.decoder = decoder;
        this.props = props;
    }

    public record Minted(String token, String jti, Instant expiresAt) {
    }

    private Minted mint(String subject, Duration ttl, Map<String, Object> claims) {
        Instant now = Instant.now();
        String jti = UUID.randomUUID().toString();
        JwtClaimsSet.Builder set = JwtClaimsSet.builder()
                .issuer(props.issuer()).subject(subject).audience(List.of(AUDIENCE))
                .issuedAt(now).notBefore(now).expiresAt(now.plus(ttl)).id(jti);
        claims.forEach(set::claim);
        String token = encoder.encode(JwtEncoderParameters.from(JwsHeader.with(SignatureAlgorithm.RS256).build(), set.build()))
                .getTokenValue();
        return new Minted(token, jti, now.plus(ttl));
    }

    public Minted system(ServiceAccounts.Account account) {
        return mint("svc:" + account.clientId(), props.serviceTokenTtl(),
                Map.of("roles", List.of(SYSTEM_ROLE), "client_id", account.clientId(), "service", account.name()));
    }

    public Minted breakGlass(UserAccounts.Account user, String operator, String reason, Duration ttl, UUID grantId) {
        return mint(user.id().toString(), ttl, Map.of(
                "roles", user.roles(), "email", user.email(),
                "break_glass", true, "bg_by", operator, "bg_reason", reason, "bg_grant", grantId.toString()));
    }

    /** A verified token of ours: signature, expiry, issuer, audience. */
    public Optional<Jwt> verify(String bearer) {
        try {
            Jwt jwt = decoder.decode(bearer);
            if (jwt.getIssuer() == null || !props.issuer().equals(jwt.getIssuer().toString())
                    || jwt.getAudience() == null || !jwt.getAudience().contains(AUDIENCE)) {
                return Optional.empty();
            }
            return Optional.of(jwt);
        } catch (JwtException e) {
            return Optional.empty();
        }
    }

    public static boolean isSystem(Jwt jwt) {
        List<String> roles = jwt.getClaimAsStringList("roles");
        return roles != null && roles.contains(SYSTEM_ROLE) && jwt.getSubject().startsWith("svc:");
    }

    public static boolean isBreakGlass(Jwt jwt) {
        return Boolean.TRUE.equals(jwt.getClaimAsBoolean("break_glass"));
    }

    /** What the gateway guard asks: may this bearer token pass without the BFF's signature? */
    public boolean admits(String authorizationHeader) {
        if (authorizationHeader == null || !authorizationHeader.startsWith("Bearer ")) {
            return false;
        }
        return verify(authorizationHeader.substring(7).trim()).filter(j -> isSystem(j) || isBreakGlass(j)).isPresent();
    }
}
