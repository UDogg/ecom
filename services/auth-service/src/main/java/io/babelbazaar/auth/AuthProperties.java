package io.babelbazaar.auth;

import java.time.Duration;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Everything deployment-specific comes from the environment (README section 6).
 *
 * @param issuer          public issuer URL; must equal the {@code iss} claim services expect
 * @param redirectUris    exact OIDC redirect URIs for the storefront client
 * @param signingKeyPath  where the RSA signing key is persisted so restarts keep issued tokens valid
 */
@ConfigurationProperties(prefix = "auth")
public record AuthProperties(
        String issuer,
        String clientId,
        List<String> redirectUris,
        List<String> corsAllowedOrigins,
        Duration accessTokenTtl,
        String signingKeyPath,
        Seed seed) {

    public record Seed(boolean enabled, String ownerEmail, String ownerPassword,
                       String customerEmail, String customerPassword) {
    }
}
