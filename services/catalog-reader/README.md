# catalog-reader

The public catalogue, in Go (pgx), read-only. It serves
`/api/catalog/products`, `/api/catalog/products/{id}` and
`/api/catalog/categories` with exactly the JSON commerce-core would produce.

- **Gated:** `internal/gate` refuses anything the BFF did not sign, with an
  empty 403 (shared vectors in `gate_test.go`).
- **Least privilege:** the `catalog_reader` role may `SELECT`
  `commerce.catalog_public` (active products) and
  `commerce.catalog_category_public`, and nothing else. Its connections are
  also `default_transaction_read_only`.
- All SQL is fixed text with bound parameters, and `ILIKE` input is escaped.
- Malformed query strings are refused, not partly parsed.

```bash
go test ./...                                                     # gate tests
CATALOG_TEST_DATABASE_URL=postgres://postgres:...@localhost:5432/shop go test ./...  # + catalogue
```

Not yet: the Redis response cache (README section 3).
