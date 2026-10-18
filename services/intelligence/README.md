# intelligence

Recommendations, in FastAPI. It is optional by design: the BFF gives it an
800 ms budget and serves `{"items": [], "degraded": true}` when it is slow or
down, so it can never break a page (scenario B).

- **Gated:** `app/gateway.py` is a raw ASGI middleware, so an unsigned request
  gets an empty 403 before FastAPI routes or validates anything.
- **Least privilege:** the `intelligence` role reads two aggregate views,
  `intel_product_popularity` and `intel_copurchase`. They hold active
  products only, with no customer ids, e-mails or order ids. Connections are
  read-only.
- **Bounded:** `MAX_CONCURRENT_INFERENCES` caps work, and a burst gets 503
  instead of queueing.
- The OpenAPI docs and schema are not served.

`GET /api/intelligence/recommendations?product_id=&limit=` returns
"bought together" items first, then fills with popular ones. It never returns
the product itself.

```bash
INTEL_TEST_DATABASE_URL=postgresql://postgres:...@localhost:5432/shop pytest
```
