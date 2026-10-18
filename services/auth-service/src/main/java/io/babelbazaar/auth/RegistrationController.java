package io.babelbazaar.auth;

import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Customer self-registration. Shop owners are never created through this endpoint. */
@RestController
public class RegistrationController {

    static final String ERROR_BASE = "https://shop.local/errors/";

    public record RegisterRequest(
            @NotBlank @Email @Size(max = 254) String email,
            @NotBlank @Size(min = 12, max = 128) String password) {
    }

    private final UserAccounts accounts;

    public RegistrationController(UserAccounts accounts) {
        this.accounts = accounts;
    }

    @PostMapping("/api/auth/register")
    public ResponseEntity<Map<String, Object>> register(@Valid @RequestBody RegisterRequest request) {
        UserAccounts.Account account = accounts.create(UUID.randomUUID(), request.email(), request.password(),
                List.of(UserAccounts.CUSTOMER));
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(Map.of("id", account.id().toString(), "email", account.email(), "roles", account.roles()));
    }

    @ExceptionHandler(UserAccounts.EmailTaken.class)
    ProblemDetail emailTaken(UserAccounts.EmailTaken e) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, e.getMessage());
        problem.setType(URI.create(ERROR_BASE + "email-taken"));
        problem.setTitle("Email already registered");
        return problem;
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ProblemDetail invalid(MethodArgumentNotValidException e) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST,
                "One or more fields are invalid.");
        problem.setType(URI.create(ERROR_BASE + "validation"));
        problem.setTitle("Invalid request");
        problem.setProperty("errors", e.getBindingResult().getFieldErrors().stream()
                .collect(java.util.stream.Collectors.toMap(f -> f.getField(),
                        f -> String.valueOf(f.getDefaultMessage()), (a, b) -> a)));
        return problem;
    }
}
