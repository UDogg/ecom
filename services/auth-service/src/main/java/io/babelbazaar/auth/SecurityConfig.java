package io.babelbazaar.auth;

import java.nio.file.Path;

import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.proc.SecurityContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.core.oidc.OidcScopes;
import org.springframework.security.oauth2.core.oidc.endpoint.OidcParameterNames;
import org.springframework.security.oauth2.server.authorization.JdbcOAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;
import org.springframework.security.oauth2.server.authorization.client.InMemoryRegisteredClientRepository;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository;
import org.springframework.security.oauth2.server.authorization.config.annotation.web.configurers.OAuth2AuthorizationServerConfigurer;
import org.springframework.security.oauth2.server.authorization.settings.AuthorizationServerSettings;
import org.springframework.security.oauth2.server.authorization.settings.ClientSettings;
import org.springframework.security.oauth2.server.authorization.settings.TokenSettings;
import org.springframework.security.oauth2.server.authorization.token.JwtEncodingContext;
import org.springframework.security.oauth2.server.authorization.token.OAuth2TokenCustomizer;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.LoginUrlAuthenticationEntryPoint;
import org.springframework.security.web.util.matcher.MediaTypeRequestMatcher;

@Configuration
public class SecurityConfig {

    /** OAuth 2.0 / OIDC protocol endpoints: authorize, token, jwks, discovery, userinfo. */
    @Bean
    @Order(1)
    SecurityFilterChain authorizationServerChain(HttpSecurity http) throws Exception {
        OAuth2AuthorizationServerConfigurer authorizationServer = OAuth2AuthorizationServerConfigurer.authorizationServer();
        http.securityMatcher(authorizationServer.getEndpointsMatcher())
                .with(authorizationServer, as -> as.oidc(Customizer.withDefaults()))
                .authorizeHttpRequests(a -> a.anyRequest().authenticated())
                // Browsers that hit /oauth2/authorize unauthenticated are sent to the login form.
                .exceptionHandling(e -> e.defaultAuthenticationEntryPointFor(
                        new LoginUrlAuthenticationEntryPoint("/login"),
                        new MediaTypeRequestMatcher(MediaType.TEXT_HTML)))
                .oauth2ResourceServer(rs -> rs.jwt(Customizer.withDefaults()));
        return http.build();
    }

    /** Sign-in form, registration, health. */
    @Bean
    @Order(2)
    SecurityFilterChain applicationChain(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests(a -> a
                        // /api/auth/**: registration, plus the privileged doors that check their own credentials
                        .requestMatchers("/healthz", "/readyz", "/error", "/login", "/api/auth/**").permitAll()
                        .anyRequest().authenticated())
                .formLogin(Customizer.withDefaults())
                // The JSON registration API uses no cookies (the BFF calls it, signed), so CSRF does not apply to it.
                .csrf(c -> c.ignoringRequestMatchers("/api/auth/**"));
        return http.build();
    }

    @Bean
    RegisteredClientRepository registeredClients(AuthProperties props, PasswordEncoder passwords) {
        // The BFF is the only client: confidential (it holds a secret and keeps every token server-side), PKCE as well,
        // rotating refresh tokens. No browser ever receives a token.
        if (props.clientSecret() == null || props.clientSecret().length() < 32) {
            throw new IllegalStateException("OIDC_CLIENT_SECRET must be at least 32 characters (openssl rand -hex 32).");
        }
        RegisteredClient bff = RegisteredClient.withId(props.clientId())
                .clientId(props.clientId())
                .clientSecret(passwords.encode(props.clientSecret()))
                .clientAuthenticationMethod(ClientAuthenticationMethod.CLIENT_SECRET_BASIC)
                .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                .authorizationGrantType(AuthorizationGrantType.REFRESH_TOKEN)
                .redirectUris(uris -> uris.addAll(props.redirectUris()))
                .scope(OidcScopes.OPENID)
                .scope(OidcScopes.PROFILE)
                .scope(OidcScopes.EMAIL)
                .clientSettings(ClientSettings.builder()
                        .requireProofKey(true)
                        .requireAuthorizationConsent(false) // first-party client
                        .build())
                .tokenSettings(TokenSettings.builder()
                        .accessTokenTimeToLive(props.accessTokenTtl())
                        .refreshTokenTimeToLive(props.refreshTokenTtl())
                        .reuseRefreshTokens(false) // every refresh rotates; a replayed refresh token is refused
                        .authorizationCodeTimeToLive(java.time.Duration.ofMinutes(2))
                        .build())
                .build();
        return new InMemoryRegisteredClientRepository(bff);
    }

    /** Before Spring Security: unsigned requests never reach a session, form or token endpoint. */
    @Bean
    org.springframework.boot.web.servlet.FilterRegistrationBean<GatewayGuardFilter> gatewayGuard(AuthProperties props,
                                                                                                PrivilegedTokens privileged) {
        var gateway = props.gateway();
        var filter = new GatewayGuardFilter(gateway == null ? "" : gateway.secret(), gateway != null && gateway.allowUnguarded(),
                // health probes, the public signing keys, and the super-credential sign-in (it checks its own credentials)
                java.util.Set.of("/healthz", "/readyz", "/oauth2/jwks", "/api/auth/service-token"), java.time.Clock.systemUTC(),
                privileged::admits);
        var registration = new org.springframework.boot.web.servlet.FilterRegistrationBean<>(filter);
        registration.setOrder(org.springframework.core.Ordered.HIGHEST_PRECEDENCE);
        registration.addUrlPatterns("/*");
        return registration;
    }

    /** Authorizations (codes, issued tokens) survive restarts and are shared across replicas. */
    @Bean
    OAuth2AuthorizationService authorizationService(JdbcTemplate jdbc, RegisteredClientRepository clients) {
        return new JdbcOAuth2AuthorizationService(jdbc, clients);
    }

    @Bean
    AuthorizationServerSettings authorizationServerSettings(AuthProperties props) {
        // iss must be the PUBLIC issuer, even though services fetch JWKS over the internal network.
        return AuthorizationServerSettings.builder().issuer(props.issuer()).build();
    }

    @Bean
    RSAKey signingKey(AuthProperties props) {
        return SigningKeys.loadOrCreate(Path.of(props.signingKeyPath()));
    }

    @Bean
    JWKSource<SecurityContext> jwkSource(RSAKey signingKey) {
        return new ImmutableJWKSet<>(new JWKSet(signingKey));
    }

    /** One encoder for every token: the authorization server's and the privileged ones (same key, same kid). */
    @Bean
    org.springframework.security.oauth2.jwt.JwtEncoder jwtEncoder(JWKSource<SecurityContext> jwkSource) {
        return new org.springframework.security.oauth2.jwt.NimbusJwtEncoder(jwkSource);
    }

    @Bean
    org.springframework.security.oauth2.jwt.JwtDecoder jwtDecoder(JWKSource<SecurityContext> jwkSource) {
        return org.springframework.security.oauth2.server.authorization.config.annotation.web.configuration
                .OAuth2AuthorizationServerConfiguration.jwtDecoder(jwkSource);
    }

    /**
     * Resource services authorize from {@code sub} (stable account id), {@code roles} and {@code email}.
     */
    @Bean
    OAuth2TokenCustomizer<JwtEncodingContext> tokenClaims(UserAccounts accounts) {
        return context -> {
            boolean accessToken = OAuth2TokenType.ACCESS_TOKEN.equals(context.getTokenType());
            boolean idToken = OidcParameterNames.ID_TOKEN.equals(context.getTokenType().getValue());
            if (!accessToken && !idToken) {
                return;
            }
            accounts.findByEmail(context.getPrincipal().getName()).ifPresent(account -> context.getClaims()
                    .subject(account.id().toString())
                    .claim("email", account.email())
                    .claim("roles", account.roles()));
        };
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return PasswordEncoderFactories.createDelegatingPasswordEncoder(); // bcrypt by default
    }
}
