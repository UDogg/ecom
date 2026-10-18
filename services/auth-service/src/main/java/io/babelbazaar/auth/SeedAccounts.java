package io.babelbazaar.auth;

import java.util.List;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

/**
 * Creates the dev shop owner and customer when {@code AUTH_SEED_ENABLED=true}.
 * The fixed ids match commerce-core's dev tokens and seed data.
 */
@Component
public class SeedAccounts implements ApplicationRunner {

    static final UUID OWNER_ID = UUID.fromString("00000000-0000-4000-8000-000000000001");
    static final UUID CUSTOMER_ID = UUID.fromString("00000000-0000-4000-8000-000000000002");

    private static final Logger log = LoggerFactory.getLogger(SeedAccounts.class);

    private final UserAccounts accounts;
    private final AuthProperties.Seed seed;

    public SeedAccounts(UserAccounts accounts, AuthProperties props) {
        this.accounts = accounts;
        this.seed = props.seed();
    }

    @Override
    public void run(ApplicationArguments args) {
        if (seed == null || !seed.enabled()) {
            return;
        }
        ensure(OWNER_ID, seed.ownerEmail(), seed.ownerPassword(),
                List.of(UserAccounts.CUSTOMER, UserAccounts.SHOP_OWNER));
        ensure(CUSTOMER_ID, seed.customerEmail(), seed.customerPassword(), List.of(UserAccounts.CUSTOMER));
    }

    private void ensure(UUID id, String email, String password, List<String> roles) {
        if (email == null || email.isBlank() || password == null || password.isBlank()) {
            return;
        }
        if (accounts.findByEmail(email).isPresent()) {
            return;
        }
        try {
            accounts.create(id, email, password, roles);
            log.info("Seeded dev account {} with roles {}", email, roles);
        } catch (UserAccounts.EmailTaken raced) {
            // another replica seeded it first
        }
    }
}
