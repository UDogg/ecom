package io.babelbazaar.auth;

import java.time.Duration;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Everything deployment-specific comes from the environment (README section 6).
 *
 * @param issuer          public issuer URL; must equal the {@code iss} claim services expect
 * @param clientSecret    the BFF's client secret; the BFF is the only OAuth client and keeps tokens server-side
 * @param redirectUris    exact OIDC redirect URIs (the BFF's /auth/callback)
 * @param signingKeyPath  where the RSA signing key is persisted so restarts keep issued tokens valid
 */
@ConfigurationProperties(prefix = "auth")
public record AuthProperties(
        String issuer,
        String clientId,
        String clientSecret,
        List<String> redirectUris,
        Duration accessTokenTtl,
        Duration refreshTokenTtl,
        String signingKeyPath,
        Seed seed,
        Gateway gateway) {

    /** The BFF's signature check (docs/request-signing.md). */
    public record Gateway(String secret, boolean allowUnguarded) {
    }

    public record Seed(boolean enabled, String ownerEmail, String ownerPassword,
                       String customerEmail, String customerPassword) {
    }
}
