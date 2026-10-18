package io.babelbazaar.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Service accounts: machine identities that sign in with client credentials for a SYSTEM token.
 *
 * <p>They are declared in configuration ({@code SERVICE_ACCOUNTS=name=client_id:sha256hex[|sha256hex],...}), so a
 * secret manager owns them: on start the table is brought in line, and credentials no longer declared are revoked.
 * Listing two hashes for one account rotates without downtime. Secrets are 384-bit random, so SHA-256 is an adequate
 * hash; comparison is constant time and does the same work whether or not the client exists.
 */
@Service
public class ServiceAccounts implements ApplicationRunner {

    public record Account(UUID id, String name, String clientId) {
    }

    record Declared(String name, String clientId, List<String> hashes) {
    }

    private static final Logger log = LoggerFactory.getLogger(ServiceAccounts.class);
    private static final String DUMMY = sha256("no-such-client");

    private final JdbcClient jdbc;
    private final AuthProperties props;

    public ServiceAccounts(JdbcClient jdbc, AuthProperties props) {
        this.jdbc = jdbc;
        this.props = props;
    }

    static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    static List<Declared> parse(String spec) {
        Map<String, Declared> out = new LinkedHashMap<>();
        if (spec == null || spec.isBlank()) {
            return List.of();
        }
        for (String entry : spec.split(",")) {
            String e = entry.trim();
            if (e.isEmpty()) {
                continue;
            }
            int eq = e.indexOf('='), colon = e.indexOf(':');
            if (eq < 1 || colon < eq + 2) {
                throw new IllegalStateException("SERVICE_ACCOUNTS entry must be name=client_id:sha256[|sha256]: " + e.split("[:=]")[0]);
            }
            String name = e.substring(0, eq), clientId = e.substring(eq + 1, colon);
            List<String> hashes = new ArrayList<>();
            for (String h : e.substring(colon + 1).split("\\|")) {
                if (!h.matches("[0-9a-f]{64}")) {
                    throw new IllegalStateException("SERVICE_ACCOUNTS: " + name + " has a hash that is not 64 lower-case hex characters");
                }
                hashes.add(h);
            }
            if (!name.matches("[a-z][a-z0-9-]{1,40}") || !clientId.matches("svc_[A-Za-z0-9_-]{8,64}")) {
                throw new IllegalStateException("SERVICE_ACCOUNTS: bad name or client id for " + name + " (client ids start with svc_)");
            }
            out.put(name, new Declared(name, clientId, hashes));
        }
        return List.copyOf(out.values());
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        List<Declared> declared = parse(props.serviceAccounts());
        List<String> names = declared.stream().map(Declared::name).toList();
        // Accounts no longer declared are switched off, never silently left working.
        jdbc.sql("UPDATE service_account SET active = false WHERE active AND NOT (name = ANY(?))")
                .param(names.toArray(String[]::new)).update();
        for (Declared d : declared) {
            UUID id = jdbc.sql("""
                    INSERT INTO service_account (id, name, client_id) VALUES (?, ?, ?)
                    ON CONFLICT (name) DO UPDATE SET client_id = EXCLUDED.client_id, active = true
                    RETURNING id""").params(UUID.randomUUID(), d.name(), d.clientId()).query(UUID.class).single();
            jdbc.sql("UPDATE service_credential SET revoked_at = now() WHERE account_id = ? AND revoked_at IS NULL "
                    + "AND NOT (secret_hash = ANY(?))").params(id, d.hashes().toArray(String[]::new)).update();
            for (String h : d.hashes()) {
                jdbc.sql("""
                        INSERT INTO service_credential (id, account_id, secret_hash) VALUES (?, ?, ?)
                        ON CONFLICT (account_id, secret_hash) DO UPDATE SET revoked_at = NULL""")
                        .params(UUID.randomUUID(), id, h).update();
            }
        }
        log.info("service accounts in effect: {}", names);
    }

    /** Empty for any failure; the caller must not say which part was wrong. */
    public Optional<Account> authenticate(String clientId, String secret) {
        if (clientId == null || secret == null || clientId.length() > 80 || secret.length() > 200) {
            return Optional.empty();
        }
        Optional<Account> account = jdbc.sql("SELECT id, name, client_id FROM service_account WHERE client_id = ? AND active")
                .param(clientId).query((rs, n) -> new Account(rs.getObject("id", UUID.class), rs.getString("name"),
                        rs.getString("client_id"))).optional();
        List<String> hashes = account.map(a -> jdbc.sql(
                        "SELECT secret_hash FROM service_credential WHERE account_id = ? AND revoked_at IS NULL")
                .param(a.id()).query(String.class).list()).orElse(List.of(DUMMY));
        byte[] presented = sha256(secret).getBytes(StandardCharsets.US_ASCII);
        boolean ok = false;
        for (String h : hashes) {
            ok |= MessageDigest.isEqual(h.getBytes(StandardCharsets.US_ASCII), presented);
        }
        if (account.isEmpty() || !ok) {
            return Optional.empty();
        }
        jdbc.sql("UPDATE service_account SET last_used_at = now() WHERE id = ?").param(account.get().id()).update();
        return account;
    }
}
