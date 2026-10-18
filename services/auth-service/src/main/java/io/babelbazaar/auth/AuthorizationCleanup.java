package io.babelbazaar.auth;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Deletes authorization rows whose codes and tokens have all expired, so the table stays bounded. */
@Component
public class AuthorizationCleanup {

    private static final Logger log = LoggerFactory.getLogger(AuthorizationCleanup.class);

    private final JdbcClient jdbc;

    public AuthorizationCleanup(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Scheduled(fixedDelayString = "PT15M", initialDelayString = "PT1M")
    public int purgeExpired() {
        int deleted = jdbc.sql("""
                DELETE FROM oauth2_authorization
                WHERE greatest(coalesce(authorization_code_expires_at, '-infinity'),
                               coalesce(access_token_expires_at, '-infinity'),
                               coalesce(oidc_id_token_expires_at, '-infinity'),
                               coalesce(refresh_token_expires_at, '-infinity')) < now() - interval '1 hour'
                """).update();
        if (deleted > 0) {
            log.info("Purged {} expired authorizations", deleted);
        }
        return deleted;
    }
}
