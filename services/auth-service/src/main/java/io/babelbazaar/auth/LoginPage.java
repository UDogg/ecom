package io.babelbazaar.auth;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.util.HtmlUtils;

/**
 * The sign-in page, in the storefront's look. The browser reaches it only through the BFF
 * (GET /login, POST /login, GET /auth-ui.css); it posts to Spring Security's form login.
 * No scripts and no inline styles, so the BFF's CSP for it can stay strict.
 */
@RestController
public class LoginPage {

    @GetMapping(value = "/login", produces = MediaType.TEXT_HTML_VALUE)
    public ResponseEntity<String> page(HttpServletRequest request) {
        CsrfToken csrf = (CsrfToken) request.getAttribute(CsrfToken.class.getName());
        boolean failed = request.getParameter("error") != null;
        boolean out = request.getParameter("logout") != null;
        String notice = failed
                ? "<p class=\"notice bad\" role=\"alert\">That e-mail and password do not match. Try again.</p>"
                : out ? "<p class=\"notice\" role=\"status\">You are signed out.</p>" : "";
        String html = """
                <!doctype html>
                <html lang="en">
                <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <meta name="color-scheme" content="light dark">
                <title>Sign in · Babel Bazaar</title>
                <link rel="stylesheet" href="/auth-ui.css">
                </head>
                <body>
                <main>
                  <a class="brand" href="/"><span class="mark">B</span> Babel Bazaar</a>
                  <form class="card" method="post" action="/login">
                    <h1>Welcome back</h1>
                    <p class="lede">Sign in to check out, track orders and keep your bag on every device.</p>
                    %s
                    <label>E-mail<input name="username" type="email" autocomplete="username" required autofocus></label>
                    <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
                    <input type="hidden" name="%s" value="%s">
                    <button type="submit">Sign in</button>
                    <p class="small">New here? <a href="/register">Create an account</a> · <a href="/">Back to the shop</a></p>
                  </form>
                  <p class="fine">Your password goes only to Babel Bazaar's identity service. The shop never sees it.</p>
                </main>
                </body>
                </html>
                """.formatted(notice, HtmlUtils.htmlEscape(csrf.getParameterName()), HtmlUtils.htmlEscape(csrf.getToken()));
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).contentType(MediaType.TEXT_HTML).body(html);
    }
}
