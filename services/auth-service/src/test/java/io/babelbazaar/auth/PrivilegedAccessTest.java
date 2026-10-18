package io.babelbazaar.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class PrivilegedAccessTest {

    static final String CLIENT = "svc_testops01";
    static final String SECRET = "test-ops-secret-0123456789abcdef0123456789abcdef";
    static final String BG_PASSWORD = "test-break-glass-password";

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @Autowired JwtDecoder decoder;
    @Autowired JdbcClient jdbc;
    @Autowired PrivilegedTokens tokens;
    @Autowired UserAccounts users;

    String systemToken(String clientId, String secret) throws Exception {
        String body = mvc.perform(post("/api/auth/service-token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .param("grant_type", "client_credentials").param("client_id", clientId).param("client_secret", secret))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return json.readTree(body).get("access_token").asText();
    }

    JsonNode breakGlass(String bearer, Map<String, Object> body, int expectStatus) throws Exception {
        var request = post("/api/auth/break-glass").contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body));
        if (bearer != null) {
            request.header("Authorization", "Bearer " + bearer);
        }
        String out = mvc.perform(request).andExpect(status().is(expectStatus)).andReturn().getResponse().getContentAsString();
        return out.isEmpty() ? null : json.readTree(out);
    }

    Map<String, Object> grantRequest(String reason) {
        return Map.of("break_glass_password", BG_PASSWORD, "user_id", SeedAccounts.CUSTOMER_ID.toString(), "reason", reason);
    }

    String lastOutcome() {
        return jdbc.sql("SELECT outcome FROM break_glass_grant ORDER BY attempted_at DESC LIMIT 1").query(String.class).single();
    }

    @Test
    void serviceAccountGetsASystemTokenWithNoUserInIt() throws Exception {
        Jwt jwt = decoder.decode(systemToken(CLIENT, SECRET));
        assertThat(jwt.getSubject()).isEqualTo("svc:" + CLIENT);
        assertThat(jwt.getClaimAsStringList("roles")).containsExactly("system");
        assertThat(jwt.getAudience()).containsExactly("core");
        assertThat(jwt.getClaims()).doesNotContainKeys("email", "break_glass");
        assertThat(jwt.getExpiresAt()).isBefore(Instant.now().plusSeconds(601));
        assertThat(PrivilegedTokens.isSystem(jwt)).isTrue();
    }

    @Test
    void aRotatedSecretOverlapsDuringRollout() throws Exception {
        assertThat(decoder.decode(systemToken(CLIENT, "old-rotated-secret-0123456789abcdef0123456789")).getSubject())
                .isEqualTo("svc:" + CLIENT);
    }

    @Test
    void wrongCredentialsLookTheSameWhateverWasWrong() throws Exception {
        String unknownClient = mvc.perform(post("/api/auth/service-token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .param("grant_type", "client_credentials").param("client_id", "svc_nobody0000").param("client_secret", SECRET))
                .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString();
        String wrongSecret = mvc.perform(post("/api/auth/service-token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .param("grant_type", "client_credentials").param("client_id", CLIENT).param("client_secret", "nope"))
                .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString();
        assertThat(unknownClient).isEqualTo(wrongSecret).isEqualTo("{\"error\":\"invalid_client\"}");
    }

    @Test
    void breakGlassActsAsExactlyOneUserForAShortTime() throws Exception {
        JsonNode out = breakGlass(systemToken(CLIENT, SECRET), grantRequest("BFF down, finishing a customer's refund"), 200);
        Jwt jwt = decoder.decode(out.get("access_token").asText());

        assertThat(jwt.getSubject()).isEqualTo(SeedAccounts.CUSTOMER_ID.toString());
        assertThat(jwt.getClaimAsStringList("roles")).containsExactly("customer"); // the user's own, never SYSTEM
        assertThat(jwt.getClaimAsBoolean("break_glass")).isTrue();
        assertThat(jwt.getClaimAsString("bg_by")).isEqualTo(CLIENT);
        assertThat(jwt.getClaimAsString("bg_reason")).isEqualTo("BFF down, finishing a customer's refund");
        assertThat(jwt.getAudience()).containsExactly("core");
        assertThat(jwt.getExpiresAt()).isBetween(Instant.now().plusSeconds(14 * 60), Instant.now().plusSeconds(15 * 60 + 5));
        assertThat(out.has("refresh_token")).isFalse();
        assertThat(lastOutcome()).isEqualTo("GRANTED");
        assertThat(jdbc.sql("SELECT jti FROM break_glass_grant WHERE id = ?::uuid").param(out.get("grant_id").asText())
                .query(String.class).single()).isEqualTo(jwt.getId());
    }

    @Test
    void everyRefusalIsAudited() throws Exception {
        String system = systemToken(CLIENT, SECRET);

        breakGlass(null, grantRequest("no token at all, should fail"), 403);
        assertThat(lastOutcome()).isEqualTo("NOT_SYSTEM");

        breakGlass(system, Map.of("break_glass_password", "guess", "user_id", SeedAccounts.CUSTOMER_ID.toString(),
                "reason", "trying a password guess"), 403);
        assertThat(lastOutcome()).isEqualTo("WRONG_PASSWORD");

        breakGlass(system, grantRequest("short"), 400);
        assertThat(lastOutcome()).isEqualTo("BAD_REQUEST");

        breakGlass(system, Map.of("break_glass_password", BG_PASSWORD, "user_id", "00000000-0000-4000-8000-0000000000ff",
                "reason", "a user who does not exist"), 404);
        assertThat(lastOutcome()).isEqualTo("NO_SUCH_USER");
    }

    @Test
    void neitherAUserTokenNorABreakGlassTokenCanBreakGlass() throws Exception {
        var user = users.findActiveById(SeedAccounts.OWNER_ID).orElseThrow();
        String ownerToken = tokens.breakGlass(user, "x", "minted directly for the test", java.time.Duration.ofMinutes(1),
                java.util.UUID.randomUUID()).token();
        breakGlass(ownerToken, grantRequest("chaining break-glass tokens"), 403);
        assertThat(lastOutcome()).isEqualTo("NOT_SYSTEM");
    }

    @Test
    void auditTrailIsSystemOnlyAndAppendOnly() throws Exception {
        String system = systemToken(CLIENT, SECRET);
        breakGlass(system, grantRequest("populate the audit trail"), 200);
        mvc.perform(get("/api/auth/ops/break-glass-grants").header("Authorization", "Bearer " + system))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].outcome").value("GRANTED"));
        mvc.perform(get("/api/auth/ops/break-glass-grants")).andExpect(status().isForbidden());

        assertThatThrownBy(() -> jdbc.sql("UPDATE break_glass_grant SET outcome = 'GRANTED'").update())
                .hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM break_glass_grant").update()).hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.sql("TRUNCATE break_glass_grant").update()).hasMessageContaining("append-only");
    }

    @Autowired org.springframework.security.oauth2.jwt.JwtEncoder encoder;
    @Autowired AuthProperties props;

    String signed(Map<String, Object> claims, String audience, String subject) {
        var set = org.springframework.security.oauth2.jwt.JwtClaimsSet.builder().issuer(props.issuer()).subject(subject)
                .audience(java.util.List.of(audience)).issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(300));
        claims.forEach(set::claim);
        return encoder.encode(org.springframework.security.oauth2.jwt.JwtEncoderParameters.from(
                org.springframework.security.oauth2.jwt.JwsHeader.with(
                        org.springframework.security.oauth2.jose.jws.SignatureAlgorithm.RS256).build(), set.build())).getTokenValue();
    }

    @Test
    void ordinaryUserTokensAreNeverPrivileged() {
        // A real, validly signed access token as the BFF holds it: audience bff.
        String user = signed(Map.of("roles", java.util.List.of("customer", "shop_owner")), "bff", SeedAccounts.OWNER_ID.toString());
        assertThat(tokens.admits("Bearer " + user)).isFalse();
        // Even claiming break_glass or system with the BFF's audience does not work: privileged tokens are aud=core only.
        assertThat(tokens.admits("Bearer " + signed(Map.of("break_glass", true, "roles", java.util.List.of("system")), "bff", "svc:x"))).isFalse();
        // aud=core with the system role but a user subject is not a service account.
        assertThat(tokens.admits("Bearer " + signed(Map.of("roles", java.util.List.of("system")), "core", SeedAccounts.OWNER_ID.toString()))).isFalse();
        assertThat(tokens.admits(null)).isFalse();
        assertThat(tokens.admits("Basic abc")).isFalse();
    }

    @Test
    void serviceAccountSpecIsValidated() {
        assertThat(ServiceAccounts.parse("ops=svc_abcdefgh:" + "a".repeat(64) + "|" + "b".repeat(64)).get(0).hashes()).hasSize(2);
        for (String bad : new String[] {"ops", "ops=svc_abcdefgh:short", "Ops=svc_abcdefgh:" + "a".repeat(64),
                "ops=client:" + "a".repeat(64)}) {
            assertThatThrownBy(() -> ServiceAccounts.parse(bad)).as(bad).isInstanceOf(IllegalStateException.class);
        }
    }
}
