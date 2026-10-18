package io.babelbazaar.auth;

import java.util.function.Supplier;

import org.springframework.security.oauth2.server.authorization.OAuth2Authorization;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;

/**
 * Creates the real authorization store on first use. JdbcOAuth2AuthorizationService reads table
 * metadata in its constructor; deferring that keeps the database out of application start-up, so the
 * image's class-data-sharing training run (Dockerfile) can start the context without one.
 */
final class LazyAuthorizationService implements OAuth2AuthorizationService {

    private final Supplier<OAuth2AuthorizationService> factory;
    private volatile OAuth2AuthorizationService delegate;

    LazyAuthorizationService(Supplier<OAuth2AuthorizationService> factory) {
        this.factory = factory;
    }

    private OAuth2AuthorizationService delegate() {
        OAuth2AuthorizationService d = delegate;
        if (d == null) {
            synchronized (this) {
                if (delegate == null) {
                    delegate = factory.get();
                }
                d = delegate;
            }
        }
        return d;
    }

    @Override
    public void save(OAuth2Authorization authorization) {
        delegate().save(authorization);
    }

    @Override
    public void remove(OAuth2Authorization authorization) {
        delegate().remove(authorization);
    }

    @Override
    public OAuth2Authorization findById(String id) {
        return delegate().findById(id);
    }

    @Override
    public OAuth2Authorization findByToken(String token, OAuth2TokenType tokenType) {
        return delegate().findByToken(token, tokenType);
    }
}
