package io.babelbazaar.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;

import com.nimbusds.jose.jwk.RSAKey;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SigningKeysTest {

    @Test
    void keySurvivesRestartsSoIssuedTokensStayValid(@TempDir Path dir) throws Exception {
        Path path = dir.resolve("keys/signing-key.json");

        RSAKey first = SigningKeys.loadOrCreate(path);
        RSAKey second = SigningKeys.loadOrCreate(path);

        assertThat(second.getKeyID()).isEqualTo(first.getKeyID());
        assertThat(second.toRSAPublicKey()).isEqualTo(first.toRSAPublicKey());
        assertThat(first.isPrivate()).isTrue();
        assertThat(PosixFilePermissions.toString(Files.getPosixFilePermissions(path))).isEqualTo("rw-------");
    }
}
