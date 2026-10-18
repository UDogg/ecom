package io.babelbazaar.auth;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/**
 * Account storage. Sign-in uses the e-mail as the username; tokens carry the
 * account's stable UUID as {@code sub}, so changing an e-mail never orphans
 * orders.
 */
@Service
public class UserAccounts implements UserDetailsService {

    public static final String CUSTOMER = "customer";
    public static final String SHOP_OWNER = "shop_owner";

    public record Account(UUID id, String email, List<String> roles, boolean enabled) {
    }

    public static class EmailTaken extends RuntimeException {
        EmailTaken(String email) {
            super("An account already exists for " + email);
        }
    }

    private final JdbcClient jdbc;
    private final PasswordEncoder passwords;

    public UserAccounts(JdbcClient jdbc, PasswordEncoder passwords) {
        this.jdbc = jdbc;
        this.passwords = passwords;
    }

    static String normalize(String email) {
        return email.trim().toLowerCase(Locale.ROOT);
    }

    public Account create(UUID id, String email, String rawPassword, List<String> roles) {
        String normalized = normalize(email);
        try {
            jdbc.sql("INSERT INTO app_user (id, email, password_hash, roles) VALUES (?, ?, ?, ?::text[])")
                    .params(id, normalized, passwords.encode(rawPassword), "{" + String.join(",", roles) + "}")
                    .update();
        } catch (DuplicateKeyException e) {
            throw new EmailTaken(normalized);
        }
        return new Account(id, normalized, List.copyOf(roles), true);
    }

    public Optional<Account> findByEmail(String email) {
        return jdbc.sql("SELECT id, email, roles, enabled FROM app_user WHERE email = ?")
                .param(normalize(email))
                .query((rs, n) -> new Account(
                        rs.getObject("id", UUID.class),
                        rs.getString("email"),
                        Arrays.asList((String[]) rs.getArray("roles").getArray()),
                        rs.getBoolean("enabled")))
                .optional();
    }

    @Override
    public UserDetails loadUserByUsername(String email) {
        return jdbc.sql("SELECT email, password_hash, roles, enabled FROM app_user WHERE email = ?")
                .param(normalize(email))
                .query((rs, n) -> User.withUsername(rs.getString("email"))
                        .password(rs.getString("password_hash"))
                        .disabled(!rs.getBoolean("enabled"))
                        .authorities(Arrays.stream((String[]) rs.getArray("roles").getArray())
                                .map(r -> new SimpleGrantedAuthority("ROLE_" + r))
                                .toList())
                        .build())
                .optional()
                .orElseThrow(() -> new UsernameNotFoundException("No such account"));
    }
}
