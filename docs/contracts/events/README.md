# Event contracts

JSON Schemas for messages on the `commerce.events` topic exchange
(README section 11). Producers and consumers test against these files, so a
breaking change fails CI on both sides.

| File | Checked by |
|---|---|
| `envelope.v1.schema.json` | commerce-core (`tests/test_contracts.py`), outbox-publisher, worker |
| `order.placed.v1.schema.json` | commerce-core (producer), worker (email consumer) |
| `inventory.changed.v1.schema.json` | commerce-core (producer), worker (low-stock consumer) |

Rules: additive changes only within a version; a breaking change ships as
`v2` alongside `v1`; consumers ignore unknown fields and dedupe by `id`.
