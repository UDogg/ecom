package io.babelbazaar.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Set;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

@SpringBootTest(properties = {"auth.gateway.secret=" + GatewayGuardTest.SECRET, "auth.gateway.allow-unguarded=false"})
@AutoConfigureMockMvc
@ActiveProfiles("test")
class GatewayGuardTest {

    static final String SECRET = "guard-test-secret-0123456789abcdef0123456789";
    static final Path VECTORS = Path.of("../../docs/contracts/gateway-signing.vectors.json");

    @Autowired MockMvc mvc;

    static MockHttpServletRequestBuilder signed(MockHttpServletRequestBuilder request, String method, String path, String query) {
        String ts = Long.toString(Instant.now().getEpochSecond());
        byte[] raw = new byte[12];
        new SecureRandom().nextBytes(raw);
        String nonce = HexFormat.of().formatHex(raw);
        return request.header("X-Gateway-Timestamp", ts).header("X-Gateway-Nonce", nonce)
                .header("X-Gateway-Signature", GatewayGuardFilter.sign(SECRET.getBytes(StandardCharsets.UTF_8), ts, nonce,
                        method, path, query, ""));
    }

    @Test
    void matchesTheSharedCrossLanguageVectors() throws Exception {
        Assumptions.assumeTrue(Files.exists(VECTORS), "shared vectors not available");
        JsonNode data = new ObjectMapper().readTree(VECTORS.toFile());
        byte[] secret = data.get("secret").asText().getBytes(StandardCharsets.UTF_8);
        for (JsonNode v : data.get("vectors")) {
            String[] a = {v.get("timestamp").asText(), v.get("nonce").asText(), v.get("method").asText(),
                    v.get("path").asText(), v.get("query").asText(), v.get("authorization").asText()};
            assertThat(GatewayGuardFilter.canonical(a[0], a[1], a[2], a[3], a[4], a[5])).as(v.get("name").asText())
                    .isEqualTo(v.get("canonical").asText());
            assertThat(GatewayGuardFilter.sign(secret, a[0], a[1], a[2], a[3], a[4], a[5])).as(v.get("name").asText())
                    .isEqualTo(v.get("signature").asText());
        }
    }

    @Test
    void unsignedRequestsGetAnEmpty403BeforeSpringSecurity() throws Exception {
        for (var request : new MockHttpServletRequestBuilder[] {
                get("/.well-known/openid-configuration"), get("/login"), get("/oauth2/authorize"),
                post("/oauth2/token"), post("/api/auth/register").contentType(MediaType.APPLICATION_JSON).content("{}")}) {
            mvc.perform(request).andExpect(status().isForbidden()).andExpect(content().string(""));
        }
    }

    @Test
    void signedRequestsGetThrough() throws Exception {
        mvc.perform(signed(get("/.well-known/openid-configuration"), "GET", "/.well-known/openid-configuration", ""))
                .andExpect(status().isOk());
    }

    @Test
    void replayedSignatureIsRefused() throws Exception {
        var request = signed(get("/.well-known/openid-configuration"), "GET", "/.well-known/openid-configuration", "");
        mvc.perform(request).andExpect(status().isOk());
        mvc.perform(request).andExpect(status().isForbidden());
    }

    @Test
    void signatureIsBoundToThePathAndQuery() throws Exception {
        mvc.perform(signed(get("/login"), "GET", "/.well-known/openid-configuration", "")).andExpect(status().isForbidden());
        mvc.perform(signed(get("/login").queryParam("error", ""), "GET", "/login", "")).andExpect(status().isForbidden());
    }

    @Test
    void healthAndPublicKeysStayOpen() throws Exception {
        mvc.perform(get("/healthz")).andExpect(status().isOk());
        mvc.perform(get("/oauth2/jwks")).andExpect(status().isOk());
    }

    @Test
    void refusesToStartWithoutAProperSecret() {
        assertThatThrownBy(() -> new GatewayGuardFilter("", false, Set.of(), java.time.Clock.systemUTC()))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> new GatewayGuardFilter("too-short", false, Set.of(), java.time.Clock.systemUTC()))
                .isInstanceOf(IllegalStateException.class);
    }
}
