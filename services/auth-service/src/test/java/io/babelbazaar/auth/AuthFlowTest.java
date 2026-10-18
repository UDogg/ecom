package io.babelbazaar.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.user;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.redirectedUrl;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.security.core.authority.FactorGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.util.UriComponentsBuilder;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AuthFlowTest {

    static final String REDIRECT = "http://localhost:8080/auth/callback";
    static final String CLIENT_SECRET = "test-bff-client-secret-0123456789abcdef";

    @Autowired MockMvc mvc;
    @Autowired JwtDecoder jwtDecoder;
    @Autowired ObjectMapper json;

    static String challenge(String verifier) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.US_ASCII));
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
    }

    MultiValueMap<String, String> authorizeParams(String verifier) throws Exception {
        MultiValueMap<String, String> p = new LinkedMultiValueMap<>();
        p.add("response_type", "code");
        p.add("client_id", "bff");
        p.add("redirect_uri", REDIRECT);
        p.add("scope", "openid email");
        p.add("state", "xyz");
        if (verifier != null) {
            p.add("code_challenge", challenge(verifier));
            p.add("code_challenge_method", "S256");
        }
        return p;
    }

    /** SAS reads authorize parameters from the query string, so put them there. */
    static org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder authorize(
            MultiValueMap<String, String> params) {
        return get(UriComponentsBuilder.fromPath("/oauth2/authorize").queryParams(params).encode().build().toUri());
    }

    /** A session that signed in with a password, as the login form leaves it (OIDC needs the sign-in time). */
    static org.springframework.test.web.servlet.request.RequestPostProcessor signedIn(String email) {
        return user(email).authorities(FactorGrantedAuthority.fromAuthority(FactorGrantedAuthority.PASSWORD_AUTHORITY));
    }

    /** Signs in as `email` (already authenticated) and returns the decoded access token. */
    Jwt accessTokenFor(String email) throws Exception {
        String verifier = UUID.randomUUID() + "-" + UUID.randomUUID();
        String location = mvc.perform(authorize(authorizeParams(verifier)).with(signedIn(email)))
                .andExpect(status().is3xxRedirection())
                .andReturn().getResponse().getRedirectedUrl();
        assertThat(location).startsWith(REDIRECT);
        var query = UriComponentsBuilder.fromUriString(location).build().getQueryParams();
        assertThat(query.getFirst("state")).isEqualTo("xyz");

        String body = mvc.perform(post("/oauth2/token").contentType(MediaType.APPLICATION_FORM_URLENCODED).with(httpBasic("bff", CLIENT_SECRET))
                        .param("grant_type", "authorization_code")
                        .param("code", query.getFirst("code"))
                        .param("redirect_uri", REDIRECT)
                        .param("client_id", "bff")
                        .param("code_verifier", verifier))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        JsonNode tokens = json.readTree(body);
        assertThat(tokens.has("id_token")).isTrue();
        lastTokens = tokens;
        return jwtDecoder.decode(tokens.get("access_token").asString());
    }

    JsonNode lastTokens;

    @Test
    void tokenEndpointRequiresTheBffClientSecret() throws Exception {
        String verifier = UUID.randomUUID() + "-" + UUID.randomUUID();
        String location = mvc.perform(authorize(authorizeParams(verifier)).with(signedIn("customer@shop.test")))
                .andReturn().getResponse().getRedirectedUrl();
        String code = UriComponentsBuilder.fromUriString(location).build().getQueryParams().getFirst("code");
        mvc.perform(post("/oauth2/token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .with(httpBasic("bff", "wrong-secret-wrong-secret-wrong-secret"))
                        .param("grant_type", "authorization_code").param("code", code)
                        .param("redirect_uri", REDIRECT).param("code_verifier", verifier))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void refreshTokensRotateAndCannotBeReused() throws Exception {
        accessTokenFor("customer@shop.test");
        String refresh = lastTokens.get("refresh_token").asString();
        var refreshRequest = (java.util.function.Supplier<org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder>)
                () -> post("/oauth2/token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .with(httpBasic("bff", CLIENT_SECRET))
                        .param("grant_type", "refresh_token").param("refresh_token", refresh);

        String body = mvc.perform(refreshRequest.get()).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        JsonNode rotated = json.readTree(body);
        assertThat(rotated.get("refresh_token").asString()).isNotEqualTo(refresh);
        assertThat(jwtDecoder.decode(rotated.get("access_token").asString()).getClaimAsStringList("roles"))
                .containsExactly("customer");

        mvc.perform(refreshRequest.get()).andExpect(status().isBadRequest()); // the old one is spent
    }

    @Test
    void discoveryAdvertisesThePublicIssuerAndPkce() throws Exception {
        mvc.perform(get("/.well-known/openid-configuration"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.issuer").value("http://issuer.test"))
                .andExpect(jsonPath("$.jwks_uri").value("http://issuer.test/oauth2/jwks"))
                .andExpect(jsonPath("$.code_challenge_methods_supported[0]").value("S256"));
    }

    @Test
    void jwksPublishesOnlyThePublicKey() throws Exception {
        mvc.perform(get("/oauth2/jwks"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.keys.length()").value(1))
                .andExpect(jsonPath("$.keys[0].kty").value("RSA"))
                .andExpect(jsonPath("$.keys[0].d").doesNotExist())
                .andExpect(jsonPath("$.keys[0].p").doesNotExist());
    }

    @Test
    void ownerTokenCarriesStableIdRolesAndEmail() throws Exception {
        Jwt token = accessTokenFor("owner@shop.test");

        assertThat(token.getSubject()).isEqualTo(SeedAccounts.OWNER_ID.toString());
        assertThat(token.getClaimAsStringList("roles")).containsExactlyInAnyOrder("customer", "shop_owner");
        assertThat(token.getClaimAsString("email")).isEqualTo("owner@shop.test");
        assertThat(token.getIssuer().toString()).isEqualTo("http://issuer.test");
        assertThat(token.getHeaders().get("alg")).isEqualTo("RS256");
        assertThat(token.getAudience()).containsExactly("bff");
    }

    @Test
    void customerTokenHasOnlyTheCustomerRole() throws Exception {
        Jwt token = accessTokenFor("customer@shop.test");
        assertThat(token.getSubject()).isEqualTo(SeedAccounts.CUSTOMER_ID.toString());
        assertThat(token.getClaimAsStringList("roles")).containsExactly("customer");
    }

    @Test
    void codeCanOnlyBeRedeemedWithTheRightVerifierAndOnlyOnce() throws Exception {
        String verifier = UUID.randomUUID() + "-" + UUID.randomUUID();
        String location = mvc.perform(authorize(authorizeParams(verifier))
                        .with(signedIn("customer@shop.test")))
                .andReturn().getResponse().getRedirectedUrl();
        String code = UriComponentsBuilder.fromUriString(location).build().getQueryParams().getFirst("code");

        java.util.function.Function<String, org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder>
                exchange = v -> post("/oauth2/token").contentType(MediaType.APPLICATION_FORM_URLENCODED).with(httpBasic("bff", CLIENT_SECRET))
                .param("grant_type", "authorization_code").param("code", code)
                .param("redirect_uri", REDIRECT).param("client_id", "bff").param("code_verifier", v);

        mvc.perform(exchange.apply("wrong-verifier-wrong-verifier-wrong-verifier-xx"))
                .andExpect(status().isBadRequest());
        mvc.perform(exchange.apply(verifier)).andExpect(status().isOk());
        mvc.perform(exchange.apply(verifier)).andExpect(status().isBadRequest()); // replayed code
    }

    @Test
    void authorizeWithoutPkceIsRefused() throws Exception {
        String location = mvc.perform(authorize(authorizeParams(null))
                        .with(signedIn("customer@shop.test")))
                .andExpect(status().is3xxRedirection())
                .andReturn().getResponse().getRedirectedUrl();
        assertThat(location).startsWith(REDIRECT).contains("error=invalid_request");
    }

    @Test
    void unknownRedirectUriIsNeverUsed() throws Exception {
        MultiValueMap<String, String> params = authorizeParams("v".repeat(50));
        params.set("redirect_uri", "https://evil.example/steal");
        mvc.perform(authorize(params).with(signedIn("customer@shop.test")))
                .andExpect(status().isBadRequest());
    }

    @Test
    void anonymousBrowserIsSentToTheLoginForm() throws Exception {
        mvc.perform(authorize(authorizeParams("v".repeat(50))).accept(MediaType.TEXT_HTML))
                .andExpect(status().is3xxRedirection())
                .andExpect(redirectedUrl("/login"));
    }

    @Test
    void formLoginChecksThePasswordAndIgnoresEmailCase() throws Exception {
        mvc.perform(post("/login").param("username", "CUSTOMER@shop.test")
                        .param("password", "test-customer-password").with(csrf()))
                .andExpect(redirectedUrl("/"));
        mvc.perform(post("/login").param("username", "customer@shop.test")
                        .param("password", "nope").with(csrf()))
                .andExpect(redirectedUrl("/login?error"));
        mvc.perform(post("/login").param("username", "customer@shop.test")
                        .param("password", "test-customer-password"))
                .andExpect(status().isForbidden()); // no CSRF token
    }

    @Test
    void customersCanRegisterAndThenSignIn() throws Exception {
        String email = "New-" + UUID.randomUUID() + "@Example.com";
        String body = json.writeValueAsString(java.util.Map.of("email", email, "password", "a-long-enough-password"));

        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.email").value(email.toLowerCase()))
                .andExpect(jsonPath("$.roles[0]").value("customer"))
                .andExpect(jsonPath("$.roles.length()").value(1));

        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.type").value("https://shop.local/errors/email-taken"));

        assertThat(accessTokenFor(email.toLowerCase()).getClaimAsStringList("roles")).isEqualTo(List.of("customer"));
    }

    @Test
    void registrationValidatesInput() throws Exception {
        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"not-an-email\",\"password\":\"short\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(jsonPath("$.errors.email").exists())
                .andExpect(jsonPath("$.errors.password").exists());
    }

    @Test
    void registrationCannotGrantShopOwner() throws Exception {
        String email = "sneaky-" + UUID.randomUUID() + "@example.com";
        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"" + email + "\",\"password\":\"a-long-enough-password\","
                                + "\"roles\":[\"shop_owner\"]}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.roles.length()").value(1));
        assertThat(accessTokenFor(email).getClaimAsStringList("roles")).containsExactly("customer");
    }

    @Test
    void healthEndpoints() throws Exception {
        mvc.perform(get("/healthz")).andExpect(status().isOk());
        mvc.perform(get("/readyz")).andExpect(jsonPath("$.status").value("ready"));
    }
}
