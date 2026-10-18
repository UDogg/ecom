package io.babelbazaar.auth;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.text.ParseException;
import java.util.UUID;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Loads the token signing key from disk, generating it on first start.
 *
 * <p>An in-memory key would change on every restart and silently invalidate
 * every token already issued, which breaks the promise that an auth-service
 * outage leaves existing sessions working.
 */
public final class SigningKeys {

    private static final Logger log = LoggerFactory.getLogger(SigningKeys.class);

    private SigningKeys() {
    }

    public static RSAKey loadOrCreate(Path path) {
        try {
            if (Files.exists(path)) {
                RSAKey key = RSAKey.parse(Files.readString(path));
                log.info("Loaded signing key kid={}", key.getKeyID());
                return key;
            }
            RSAKey key = new RSAKeyGenerator(2048)
                    .keyID(UUID.randomUUID().toString())
                    .keyUse(KeyUse.SIGNATURE)
                    .generate();
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            Files.writeString(path, key.toJSONString());
            try {
                Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"));
            } catch (UnsupportedOperationException ignored) {
                // non-POSIX filesystem; rely on the volume's permissions
            }
            log.info("Generated new signing key kid={} at {}", key.getKeyID(), path);
            return key;
        } catch (IOException | ParseException | JOSEException e) {
            throw new IllegalStateException("Cannot load or create signing key at " + path, e);
        }
    }
}
