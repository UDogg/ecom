package io.babelbazaar.auth;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = "auth.break-glass.password-sha256=")
@AutoConfigureMockMvc
@ActiveProfiles("test")
class BreakGlassDisabledTest {

    @Autowired MockMvc mvc;

    @Test
    void withoutAConfiguredPasswordTheDoorDoesNotExist() throws Exception {
        mvc.perform(post("/api/auth/break-glass").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isNotFound());
    }
}
