# Babel Bazaar

> *Babel* for the many tongues it speaks, *Bazaar* for the market where they all trade.
>
> A deliberately polyglot, scalable e-commerce platform: a showcase of fault isolation, workload-specific runtimes, and recoverable asynchronous design. **Runs entirely on localhost, for free.**

| | |
|---|---|
| **Type** | Architecture showcase + proof of concept |
| **Status** | Phases 1 to 3 built, plus a Go BFF with every core service gated ([ADR 012](docs/adr/012-bff-and-gated-core.md)), payments, the storefront and admin, and an AWS deployment; see [Implementation Roadmap](#22-implementation-roadmap) |
| **Runs on** | Docker Compose, free and open-source components only |
| **Shop owner can** | Sign in to an admin panel, add products (with images), edit them, adjust stock, and remove them |
| **Live demo** | GitHub Pages: the real storefront with an in-browser backend, plus the docs (`site/`, [section 24](#24-github-pages-demo-mode)) |
| **Deploys by** | Changing environment variables (base URLs, secrets, hostnames), with no code changes |

### Start here

```bash
./run.sh                         # everything, end to end: checks, secrets, build, start, seed → http://localhost:8080
./run.sh test | scenario | bench # the suites, the broker-outage scenario, the benchmark
./run.sh stress                  # the gamified stress arena: thousands of shoppers + a red team at the gate
AWS_REGION=eu-west-1 deploy/aws/deploy.sh   # one Graviton instance with HTTPS, about $19 a month
```

| | |
|---|---|
| The storefront and admin | [`web/`](web/README.md), served by the BFF |
| The BFF and its route table | [`services/bff`](services/bff/README.md) |
| Speed, footprint, vertical scaling | [`docs/performance.md`](docs/performance.md) |
| Load and attack demo (gamified) | [`stress/`](stress/README.md), [`docs/stress-test.md`](docs/stress-test.md) |
| Deploying and operating on AWS, cost | [`docs/aws.md`](docs/aws.md) |
| Break-glass and service accounts | [`docs/break-glass.md`](docs/break-glass.md) |
| Keeping dependencies current | [`docs/dependency-updates.md`](docs/dependency-updates.md) |

The sections below are the original design document; where the build differs, the files above are current.

---

## Table of Contents

1. [What This Is (and Isn't)](#1-what-this-is-and-isnt)
2. [Feature Overview](#2-feature-overview)
3. [Architecture at a Glance](#3-architecture-at-a-glance)
4. [Localhost-First, Free-by-Design](#4-localhost-first-free-by-design)
5. [Quick Start](#5-quick-start)
6. [Configuration and the Base URL Strategy](#6-configuration-and-the-base-url-strategy)
7. [Shop Owner Admin Panel](#7-shop-owner-admin-panel)
8. [Key Request Flows](#8-key-request-flows)
9. [Data Model and Invariants](#9-data-model-and-invariants)
10. [API Reference](#10-api-reference)
11. [Events and Contracts](#11-events-and-contracts)
12. [Security Model](#12-security-model)
13. [Resilience and Failure Containment](#13-resilience-and-failure-containment)
14. [Observability](#14-observability)
15. [Testing Strategy](#15-testing-strategy)
16. [Real-World Scenario Tests (A to G)](#16-real-world-scenario-tests-a-to-g)
17. [Performance Methodology](#17-performance-methodology)
18. [A/B and Comparative Evaluation](#18-ab-and-comparative-evaluation)
19. [Acceptance Criteria](#19-acceptance-criteria)
20. [Repository Layout](#20-repository-layout)
21. [Development Workflow](#21-development-workflow)
22. [Implementation Roadmap](#22-implementation-roadmap)
23. [Risks and Trade-offs](#23-risks-and-trade-offs)
24. [GitHub Pages Demo Mode](#24-github-pages-demo-mode)
25. [Deploying Beyond Localhost](#25-deploying-beyond-localhost)
26. [Architecture Decision Records](#26-architecture-decision-records)
27. [FAQ](#27-faq)
28. [Final Recommendation](#28-final-recommendation)

---

## 1. What This Is (and Isn't)

Babel Bazaar is a proof of concept for an e-commerce platform that assigns each workload to the runtime best suited to it, instead of forcing one framework to do everything.

| Concern | Runtime | Why |
|---|---|---|
| Identity, tokens, security-sensitive admin authorization | **Java + Spring Boot + Spring Security** | Mature OAuth 2.0 / OIDC ecosystem |
| Orders, inventory, products, transactions | **Python + Django + PostgreSQL** | Productive business framework, strong relational integrity |
| Typed APIs, recommendations, mock payment provider | **Python + FastAPI + Pydantic** | Contract-first APIs and the Python data ecosystem |
| High-concurrency public catalog reads | **Go + Gin** | Cheap concurrency, small footprint, simple deployment |
| Asynchronous jobs | **Celery + RabbitMQ** | Durable delivery, retries, dead-lettering |
| Storefront and admin panel | **Next.js + React + TypeScript** | Server rendering, SEO, rich admin UI |

### Be honest about the goal

This project is **intentionally over-engineered**. A modular Django monolith would ship a shop faster and cheaper. The point here is to:

- **Showcase** a realistic, scalable, multi-runtime architecture.
- **Measure** whether polyglot boundaries deliver real fault containment and performance benefits.
- **Document** when the simpler architecture should win.

### Core hypothesis

> A carefully designed polyglot architecture can achieve better fault isolation and workload-specific performance than a monolithic backend, without unacceptable increases in latency, infrastructure cost, development effort, or operational complexity.

This is a hypothesis to test, not an assumed outcome. A baseline monolith is built alongside the polyglot system so the claim can be evaluated with evidence.

### What this project does *not* claim

- Microservices are always more scalable than monoliths.
- Python is always better for business logic, or Go always faster for real workloads.
- Spring Security makes a service inherently secure.
- Asynchronous execution eliminates bottlenecks.
- Multiple languages automatically reduce vulnerabilities.
- GPU support improves ordinary API throughput.
- A message broker guarantees exactly-once business execution.

Each claim requires qualification and, where applicable, empirical testing.

---

## 2. Feature Overview

### Storefront (customers)

- Browse and filter the catalog, with full-text search and facets.
- Product detail pages with images.
- Cart, checkout, order history.
- Recommendations that degrade gracefully (popular products) when the recommendation service is down.
- Idempotent checkout: double-clicks and retries never create duplicate orders.

### Admin panel (shop owner)

- Secure sign-in with the `shop_owner` role.
- **Add products**: name, description, SKU, price, category, initial stock, images.
- **Edit products**: details, price, images, visibility.
- **Remove products**: archive (default) or permanently delete when safe.
- **Adjust inventory** with an audit trail.
- View and manage orders and their payment state.
- Dashboard: recent orders, low-stock alerts, queue and worker health summary.

### Platform

- Transactional outbox for reliable events.
- Durable background workers with retries and dead-letter queues.
- Search index that is a rebuildable projection, never a source of truth.
- Per-service database roles and network segmentation.
- Distributed tracing, metrics, and structured logs across all languages.
- Built-in chaos switches to reproduce failure scenarios on a laptop.

---

## 3. Architecture at a Glance

> **As built:** the `gateway` below is the **Go BFF** (`services/bff`). It is
> the only service with a public port, it keeps every token server-side, it
> enforces the API contract, and it signs every call. Each core service
> answers anything unsigned with an empty 403. This model is adopted from
> Ensuretech; see [ADR 012](docs/adr/012-bff-and-gated-core.md),
> [request signing](docs/request-signing.md) and
> [core lockdown](docs/core-lockdown.md).

```mermaid
flowchart TB
    Browser["Browser (Customer / Shop Owner)"]
    Web["Next.js Storefront + Admin Panel :3000"]
    GW["API Gateway (reverse proxy) :8080"]

    Auth["auth-service<br/>Spring Boot + Spring Security :8081"]
    Core["commerce-core<br/>Django :8000"]
    Catalog["catalog-reader<br/>Go + Gin :8082"]
    Intel["intelligence<br/>FastAPI :8001"]
    Pay["payments-sim<br/>FastAPI :8003"]

    PG[("PostgreSQL :5432")]
    Redis[("Redis :6379")]
    MQ[["RabbitMQ :5672"]]
    OS[("OpenSearch :9200")]
    S3[("MinIO (S3) :9000")]

    Worker["celery-worker"]
    Pub["outbox-publisher"]
    Mail["Mailpit :8025"]

    Browser --> Web
    Browser --> GW
    Web --> GW
    GW --> Auth
    GW --> Core
    GW --> Catalog
    GW --> Intel

    Core --> PG
    Auth --> PG
    Catalog --> PG
    Catalog --> Redis
    Intel --> PG
    Core --> Pay
    Core --> S3

    Core -- "outbox rows" --> PG
    Pub --> PG
    Pub --> MQ
    MQ --> Worker
    Worker --> OS
    Worker --> Redis
    Worker --> Mail
    Catalog -. "search queries" .-> OS
```

### Service catalog

| Service | Stack | Port | Responsibility | Owns data? |
|---|---|---|---|---|
| `web` | Next.js, React, TypeScript | 3000 | Storefront and admin UI | No |
| `gateway` | Nginx (or Caddy) | 8080 | Single entry point, routing, request-size and rate limits | No |
| `auth-service` | Spring Boot, Spring Authorization Server | 8081 | Sign-in, OIDC/OAuth 2.0, JWT issuance, JWKS | Identity schema |
| `commerce-core` | Django, DRF or Django Ninja | 8000 | Products, inventory, carts, orders, payments state, admin API | Commerce schema |
| `catalog-reader` | Go, Gin | 8082 | Fast public catalog reads and search proxy | No (read-only) |
| `intelligence` | FastAPI, Pydantic | 8001 | Recommendations, scoring, optional embeddings | Its own projections |
| `payments-sim` | FastAPI | 8003 | Local stand-in for a payment provider with chaos controls | Simulator state |
| `outbox-publisher` | Python | n/a | Moves outbox rows to RabbitMQ | No |
| `celery-worker` | Python, Celery | n/a | Indexing, cache invalidation, email, reconciliation | No |

### Authoritative data ownership

Every critical business domain has **one authoritative owner**. No secondary service may independently update another domain's authoritative tables.

| Domain | Authoritative owner | Primary store |
|---|---|---|
| Customers and identity | `auth-service` | PostgreSQL (`identity` schema) |
| Product catalog | `commerce-core` | PostgreSQL (`commerce` schema) |
| Inventory and reservations | `commerce-core` | PostgreSQL |
| Orders and order items | `commerce-core` | PostgreSQL |
| Payment attempts and reconciliation | `commerce-core` (talking to the payment provider) | PostgreSQL |
| Product images | `commerce-core` (metadata), MinIO (bytes) | PostgreSQL + S3 |
| Search index | Worker-built projection | OpenSearch (rebuildable) |
| Cached catalog responses | `catalog-reader` | Redis (disposable) |
| Background job state | Worker system | RabbitMQ + job records |

Logical ownership does not require physical separation. All domains may live in one PostgreSQL instance with separate schemas and roles while the PoC runs locally.

---

## 4. Localhost-First, Free-by-Design

**Hard requirement:** everything works on a developer laptop with no paid accounts, cloud credentials, or API keys.

| Need | Production-style choice | Free localhost implementation |
|---|---|---|
| Identity provider | Managed OIDC provider | Spring Authorization Server in `auth-service` |
| Payment provider | Hosted payment API | `payments-sim` (timeouts, duplicate webhooks, out-of-order events on demand) |
| Object storage | S3 or compatible | MinIO (S3-compatible) |
| Email | Transactional email provider | Mailpit (captures mail locally) |
| Message broker | Managed queue | RabbitMQ container |
| Search | Managed search | OpenSearch container (optional profile; falls back to PostgreSQL full-text search) |
| Tracing | Hosted APM | OpenTelemetry Collector + Jaeger |
| Metrics | Hosted metrics | Prometheus + Grafana |
| Load testing | Cloud load tools | k6 (local binary or container) |
| Fault injection | Chaos platform | Toxiproxy + built-in chaos endpoints |
| TLS | Public certificates | Plain HTTP on localhost (TLS optional via mkcert) |

### Compose profiles

The full stack is heavy. Profiles let you run only what you need.

| Profile | Starts | Approx. memory | Use for |
|---|---|---|---|
| `core` (default) | Postgres, Redis, RabbitMQ, MinIO, Mailpit, gateway, auth, commerce-core, catalog-reader, payments-sim, workers, web | ~4 GB | Day-to-day development, admin panel, checkout |
| `search` | OpenSearch | +1 to 2 GB | Full-text search and facets |
| `ai` | `intelligence` service with model dependencies | +1 to 2 GB | Recommendations and inference scenarios |
| `observability` | OTel Collector, Jaeger, Prometheus, Grafana | +1 GB | Tracing and dashboards |
| `chaos` | Toxiproxy | small | Fault-injection scenarios |
| `full` | Everything | 8 GB+ | Demos and benchmarks |

> **Tip:** Without the `search` profile, catalog search transparently uses PostgreSQL full-text search so the shop remains fully usable on a modest laptop. The GPU is never required; inference runs on CPU locally.

---

## 5. Quick Start

### Prerequisites

- Docker Desktop (or Docker Engine) with Compose v2.
- 8 GB RAM free for the `full` profile; 4 GB is enough for `core`.
- `make` (optional convenience wrapper).
- Optional for running services outside containers: Java 21, Python 3.14, Go 1.27, Node.js 24.

### Run it

```bash
git clone https://github.com/<you>/babel-bazaar.git
cd babel-bazaar
./run.sh
```

That is all. `./run.sh` checks the machine, creates `.env` with **fresh random
secrets** on first run (and `.env.private` for the operator secrets), builds
the images, starts everything, waits until every service is healthy, loads
the demo catalogue, and prints where everything is and how to sign in.

```bash
./run.sh status            # health of every container
./run.sh logs bff          # follow one service
./run.sh api --role owner GET /api/admin/dashboard     # call the BFF as the seeded owner
./run.sh breakglass ...    # privileged access when the BFF is down (docs/break-glass.md)
./run.sh test | scenario   # test suites; the broker-outage scenario
./run.sh down | reset      # stop (keep data); delete everything
```

The `Makefile` targets (`make up`, `make seed`, `make test`, ...) still work
for finer control.

### Local URLs

Only the BFF is published; every core service is on private networks.

| What | URL |
|---|---|
| **Storefront** (served by the BFF) | http://localhost:8080 |
| **Admin panel** (sign in as the owner) | http://localhost:8080/admin |
| Sign in (through the BFF) | http://localhost:8080/auth/login |
| API (through the BFF) | http://localhost:8080/api/... |
| RabbitMQ management (developer tool) | http://localhost:15672 |
| Mailpit inbox (developer tool) | http://localhost:8025 |

### Seeded dev accounts

| Role | Email | Password |
|---|---|---|
| Shop owner | `owner@shop.local` | `SEED_OWNER_PASSWORD` from `.env` |
| Customer | `customer@shop.local` | `SEED_CUSTOMER_PASSWORD` from `.env` |

These values exist only in `.env.example` for local development. Never reuse them anywhere public.

### Smoke test

```bash
make smoke
```

Verifies, in order: every health endpoint responds, the owner can sign in, a product can be created and appears in the storefront, a customer can check out, the order reaches `paid`, and the product can be removed.

---

## 6. Configuration and the Base URL Strategy

**Rule:** no service contains a hard-coded hostname. Every URL comes from the environment, and the localhost values are simply the defaults in `.env.example`. Deploying means changing variables, not code.

### Two kinds of URLs

| Kind | Used by | Example (local) | Example (deployed) |
|---|---|---|---|
| **Public URLs** | Browsers, OIDC redirects, emails, CORS | `http://localhost:8080` | `https://shop.example.com` |
| **Internal URLs** | Service-to-service calls inside the network | `http://commerce-core:8000` | `http://commerce-core.internal:8000` |

Mixing them up is the most common source of bugs (especially OIDC issuer mismatches), so they have distinct variable names.

### `.env.example`

```dotenv
# ------------------------------------------------------------------
# Public URLs (what the BROWSER sees). Change these when deploying.
# ------------------------------------------------------------------
PUBLIC_API_BASE_URL=http://localhost:8080        # the BFF: storefront, API and sign-in on one origin
PUBLIC_WEB_URL=${PUBLIC_API_BASE_URL}
PUBLIC_AUTH_ISSUER_URL=${PUBLIC_API_BASE_URL}
PUBLIC_ASSET_BASE_URL=http://localhost:9000/product-images

# ------------------------------------------------------------------
# Internal URLs (service to service). Usually unchanged in Compose.
# ------------------------------------------------------------------
INTERNAL_COMMERCE_URL=http://commerce-core:8000
INTERNAL_AUTH_JWKS_URL=http://auth-service:8081/oauth2/jwks
INTERNAL_INTELLIGENCE_URL=http://intelligence:8001
INTERNAL_PAYMENTS_URL=http://payments-sim:8003

# ------------------------------------------------------------------
# Datastores
# ------------------------------------------------------------------
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=shop
POSTGRES_SUPERUSER=postgres
POSTGRES_SUPERUSER_PASSWORD=dev-only-change-me
COMMERCE_DB_PASSWORD=dev-only-commerce
CATALOG_READER_DB_PASSWORD=dev-only-reader
INTELLIGENCE_DB_PASSWORD=dev-only-intel
AUTH_DB_PASSWORD=dev-only-auth

REDIS_URL=redis://redis:6379/0
RABBITMQ_URL=amqp://shop:dev-only-rabbit@rabbitmq:5672/%2F
OPENSEARCH_URL=http://opensearch:9200

# ------------------------------------------------------------------
# Object storage (MinIO locally, any S3-compatible service later)
# ------------------------------------------------------------------
S3_ENDPOINT_URL=http://minio:9000
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000
S3_BUCKET=product-images
S3_ACCESS_KEY=dev-minio
S3_SECRET_KEY=dev-only-minio-secret

# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
OIDC_CLIENT_ID=storefront
OIDC_REDIRECT_URIS=${PUBLIC_WEB_URL}/auth/callback
CORS_ALLOWED_ORIGINS=${PUBLIC_WEB_URL}
ACCESS_TOKEN_TTL_SECONDS=600

# ------------------------------------------------------------------
# Email
# ------------------------------------------------------------------
SMTP_HOST=mailpit
SMTP_PORT=1025
MAIL_FROM=orders@shop.local

# ------------------------------------------------------------------
# Seed data (dev only)
# ------------------------------------------------------------------
SEED_OWNER_EMAIL=owner@shop.local
SEED_OWNER_PASSWORD=dev-only-owner-pass
SEED_CUSTOMER_EMAIL=customer@shop.local
SEED_CUSTOMER_PASSWORD=dev-only-customer-pass

# ------------------------------------------------------------------
# Behaviour switches
# ------------------------------------------------------------------
SEARCH_BACKEND=postgres        # postgres | opensearch
CHAOS_ENABLED=false            # enables chaos endpoints in payments-sim and intelligence
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
LOG_LEVEL=info
```

### Gotchas worth knowing

- **OIDC issuer:** the `iss` claim must equal `PUBLIC_AUTH_ISSUER_URL`, while services fetch keys from `INTERNAL_AUTH_JWKS_URL`. They differ in Docker, and that is intentional.
- **Product image URLs:** the browser cannot reach `minio:9000`. Uploads use presigned URLs signed against `S3_PUBLIC_ENDPOINT_URL`.
- **One origin:** the BFF serves the storefront, the API and sign-in, so there is no CORS to configure. Changing `PUBLIC_API_BASE_URL` needs no frontend rebuild.
- **Secrets:** `.env` is git-ignored. Only `.env.example` is committed, and it must contain placeholders only.

---

## 7. Shop Owner Admin Panel

The admin panel lives at `/admin` inside the Next.js app and talks to `commerce-core` through the gateway. Access requires a valid token with the `shop_owner` role; this is enforced **server-side in `commerce-core`**, not only by hiding UI.

> Django's built-in admin is also available at `/django-admin/` as a break-glass tool for operators. The custom panel is the supported shop-owner experience.

### Screens

| Route | Purpose |
|---|---|
| `/admin` | Dashboard: recent orders, low-stock list, system status |
| `/admin/products` | Searchable table of products with status, price, stock |
| `/admin/products/new` | **Add a product** |
| `/admin/products/[id]` | Edit a product, manage images, adjust stock |
| `/admin/orders` | Orders with payment and fulfillment state |
| `/admin/inventory` | Stock levels and adjustment history |
| `/admin/system` | Queue depth, dead-letter count, index freshness (read-only) |

### Adding a product

Fields:

| Field | Rules |
|---|---|
| Name | Required, 1 to 200 characters |
| SKU | Required, unique, immutable after creation |
| Description | Optional, sanitized rich text or Markdown |
| Price | Required, decimal stored as integer minor units plus currency |
| Category | Required, selected or created |
| Initial stock | Integer greater than or equal to 0 |
| Images | 0 to 8 files, JPEG/PNG/WebP, size-limited, content-type verified server-side |
| Status | `draft` or `active` |

Behavior:

1. The owner submits the form. The UI requests presigned upload URLs and uploads images **directly to object storage**, keeping large files off the API process.
2. `commerce-core` validates the payload and, **inside one database transaction**, inserts the product, inventory row, image records, and a `product.created.v1` outbox event.
3. The owner immediately sees the product in the admin list.
4. Asynchronously, the worker indexes the product in OpenSearch, invalidates relevant Redis cache keys, and warms the new entry. The storefront shows the product within seconds.

If indexing or caching is temporarily broken, the product is still created and visible through the database-backed catalog path. Only search freshness is affected, and the system recovers on its own when the dependency returns.

### Removing a product

"Remove" has two safe behaviors, because deleting rows that orders reference would corrupt order history.

| Action | When available | Effect |
|---|---|---|
| **Archive** (default "Remove") | Always | Sets `status = archived`. Disappears from storefront, search, and recommendations. Order history, invoices, and reporting keep working. Reversible via **Restore**. |
| **Delete permanently** | Only if the product has never appeared in any order | Hard-deletes the product, its inventory row, and its images. Irreversible; requires typing the SKU to confirm. |

Both actions publish an event (`product.archived.v1` or `product.deleted.v1`) so the search index, caches, and recommendation projections drop the product. Carts holding an archived product show it as unavailable at checkout.

### Editing and stock

- Price changes are versioned: orders store the price paid at purchase time (price snapshot) and never read the live price afterward.
- Stock adjustments are recorded as `inventory_adjustment` rows with a reason and the acting user, so the current quantity is always explainable.
- Concurrent edits use optimistic concurrency (`version` field). A stale edit returns `409 Conflict` instead of silently overwriting.

### Admin RBAC summary

| Capability | Customer | Shop owner |
|---|:---:|:---:|
| Browse catalog, place orders | Yes | Yes |
| View own orders | Yes | Yes |
| Create, edit, archive, delete products | No | Yes |
| Adjust inventory | No | Yes |
| View all orders | No | Yes |
| View system status | No | Yes |

---

## 8. Key Request Flows

### 8.1 Add product (write path and projection)

```mermaid
sequenceDiagram
    autonumber
    actor Owner as Shop Owner
    participant Web as Next.js Admin
    participant GW as Gateway
    participant Core as commerce-core (Django)
    participant S3 as MinIO
    participant PG as PostgreSQL
    participant Pub as outbox-publisher
    participant MQ as RabbitMQ
    participant W as celery-worker
    participant OS as OpenSearch
    participant R as Redis

    Owner->>Web: Fill product form, pick images
    Web->>GW: POST /api/commerce/admin/uploads/presign
    GW->>Core: (JWT validated, role = shop_owner)
    Core-->>Web: Presigned upload URLs
    Web->>S3: PUT image bytes directly
    Web->>GW: POST /api/commerce/admin/products
    GW->>Core: Create product
    Core->>PG: BEGIN; insert product, inventory, images, outbox event; COMMIT
    Core-->>Web: 201 Created
    Pub->>PG: Poll unpublished outbox rows
    Pub->>MQ: Publish product.created.v1
    MQ->>W: Deliver event
    W->>OS: Upsert document
    W->>R: Invalidate and warm cache keys
    W-->>MQ: ACK after success
```

### 8.2 Checkout (primary synchronous path)

1. The customer opens the storefront; the catalog is served by `catalog-reader` (cache, then database).
2. The customer initiates checkout with an **idempotency key**.
3. `commerce-core` validates the cart, **atomically reserves inventory**, and creates the order and its records in one transaction.
4. Payment proceeds through a defined payment workflow against the provider.
5. The customer receives a response reflecting the **actual** order state.

The system distinguishes *order created*, *payment confirmed*, and *fulfillment completed*:

```mermaid
stateDiagram-v2
    [*] --> pending_payment: order created, stock reserved
    pending_payment --> paid: payment captured
    pending_payment --> payment_failed: payment declined
    pending_payment --> expired: reservation timeout
    payment_failed --> pending_payment: customer retries
    payment_failed --> cancelled: customer or owner cancels
    expired --> cancelled: stock released
    paid --> fulfilled: shipped / delivered
    paid --> refunded: refund issued
    fulfilled --> refunded: refund issued
    cancelled --> [*]
    fulfilled --> [*]
    refunded --> [*]
```

Checkout never synchronously calls recommendations, search, email, or analytics. Payment authorization is the one business-critical external dependency, and it has an explicit timeout, retry, and reconciliation strategy.

### 8.3 Asynchronous path

1. A committed transaction creates an **outbox record**.
2. The publisher sends the event to the broker.
3. A worker processes it and acknowledges **only after success**.
4. Failures trigger bounded retries and then land in an observable dead-letter queue with a replay tool.

### 8.4 Degraded-mode behavior

| Failure | User-visible behavior |
|---|---|
| Recommendations down | Show popular products or omit the carousel |
| Search down | Fall back to database search or a reduced browse experience; orders unaffected |
| Email down | Messages are queued and delivered later |
| Analytics down | Events retained or retried; checkout unaffected |
| Redis down | Catalog reads fall through to PostgreSQL (slower, still correct) |
| Auth service down | Existing valid tokens keep working until expiry; new sign-ins fail |
| Broker down | Orders and product edits still commit; events wait in the outbox |
| Payment provider timeout | Order stays `pending_payment`; reconciliation resolves the true state |

---

## 9. Data Model and Invariants

### Core entities

```mermaid
erDiagram
    CATEGORY ||--o{ PRODUCT : contains
    PRODUCT ||--|| INVENTORY : has
    PRODUCT ||--o{ PRODUCT_IMAGE : has
    PRODUCT ||--o{ INVENTORY_ADJUSTMENT : audited_by
    ORDER ||--|{ ORDER_ITEM : contains
    PRODUCT ||--o{ ORDER_ITEM : referenced_by
    ORDER ||--o{ PAYMENT_ATTEMPT : has
    ORDER ||--o{ INVENTORY_RESERVATION : holds
    CHECKOUT_IDEMPOTENCY }o--|| ORDER : resolves_to

    PRODUCT {
        uuid id PK
        text sku UK
        text name
        text description
        bigint price_minor
        char currency
        text status "draft | active | archived"
        int version
        timestamptz created_at
    }
    INVENTORY {
        uuid product_id PK
        int available_quantity "CHECK >= 0"
    }
    ORDER {
        uuid id PK
        uuid customer_id
        text status
        bigint total_minor
        timestamptz created_at
    }
    ORDER_ITEM {
        uuid id PK
        uuid order_id FK
        uuid product_id FK
        int quantity
        bigint unit_price_minor "price snapshot"
    }
    OUTBOX_EVENT {
        uuid id PK
        text event_type
        jsonb payload
        timestamptz created_at
        timestamptz published_at
    }
```

### Critical invariants (enforced in the database, not just in code)

- `inventory.available_quantity >= 0` via a `CHECK` constraint.
- `product.sku` is unique.
- An idempotency key maps to at most one order.
- Order items store a price snapshot; live product price never rewrites history.
- Orders can never reference a hard-deleted product (foreign key `ON DELETE RESTRICT`).

### Inventory reservation (no overselling)

A naive read-check-decrement lets two concurrent requests see the same stock and oversell. The PoC uses a conditional atomic update:

```sql
UPDATE inventory
SET available_quantity = available_quantity - $2
WHERE product_id = $1
  AND available_quantity >= $2
RETURNING available_quantity;
```

Zero rows returned means insufficient stock and the transaction aborts. The reservation record, order, and outbox event are written in the **same transaction**. Reservations carry an expiry; a worker releases expired reservations, and failed payments release stock. Concurrent cancel and payment events are resolved through the order state machine and row locking.

### Idempotent checkout

```sql
CREATE TABLE checkout_idempotency (
    key            text        NOT NULL,
    customer_id    uuid        NOT NULL,
    request_hash   bytea       NOT NULL,   -- hash of the normalized request body
    order_id       uuid        REFERENCES orders(id),
    response_body  jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, key)
);
```

- Same key and equivalent input: return the **original** logical result.
- Same key and materially different input: reject with `422`.
- Retention window is defined (for example 24 hours) and documented.

### Transactional outbox

Publishing straight after commit opens a failure window (the transaction commits, the publish fails). The outbox writes the business change and an event record atomically:

```sql
CREATE TABLE outbox_event (
    id           uuid        PRIMARY KEY,
    event_type   text        NOT NULL,
    schema_ver   int         NOT NULL,
    payload      jsonb       NOT NULL,
    trace_ctx    jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE INDEX outbox_unpublished ON outbox_event (created_at) WHERE published_at IS NULL;
```

The publisher may deliver an event more than once, so **consumers are idempotent** (processed-event table keyed by event id). The design promises durable publication and safe duplicate handling, not end-to-end exactly-once execution.

### Payment reconciliation

Payment providers and application databases cannot share one atomic transaction. Payment states are explicit: `pending`, `authorized`, `captured`, `failed`, `refunded`. If a payment succeeds but the application fails before recording it, the system recovers through provider webhooks, idempotent API calls, status queries, and a scheduled reconciliation job. **A timeout is never treated as proof that payment failed.**

---

## 10. API Reference

Full machine-readable contracts are generated (OpenAPI). This is the human overview. Base URL is `${PUBLIC_API_BASE_URL}`.

### Authentication (`auth-service`)

| Method | Path | Description |
|---|---|---|
| GET | `/.well-known/openid-configuration` | OIDC discovery |
| GET | `/oauth2/authorize` | Authorization code flow with PKCE |
| POST | `/oauth2/token` | Token exchange and refresh |
| GET | `/oauth2/jwks` | Public signing keys |

### Public catalog (`catalog-reader`, anonymous)

| Method | Path | Description |
|---|---|---|
| GET | `/api/catalog/products` | List with filters (`category`, `q`, `min_price`, `max_price`, `page`) |
| GET | `/api/catalog/products/{id}` | Product detail |
| GET | `/api/catalog/categories` | Category tree |
| GET | `/api/catalog/search?q=` | Search (OpenSearch or Postgres FTS) |

### Customer commerce (`commerce-core`, role `customer` or higher)

| Method | Path | Description |
|---|---|---|
| GET/PUT | `/api/commerce/cart` | Read or update the cart |
| POST | `/api/commerce/checkout` | Place an order. **Requires** `Idempotency-Key` header |
| GET | `/api/commerce/orders` | Own orders |
| GET | `/api/commerce/orders/{id}` | Own order detail (object-level authorization enforced) |

### Admin (`commerce-core`, role `shop_owner`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/commerce/admin/uploads/presign` | Presigned image upload URLs |
| GET | `/api/commerce/admin/products` | List all statuses |
| **POST** | `/api/commerce/admin/products` | **Create product** |
| PATCH | `/api/commerce/admin/products/{id}` | Edit (requires `If-Match` version) |
| **DELETE** | `/api/commerce/admin/products/{id}` | **Archive** (default) |
| DELETE | `/api/commerce/admin/products/{id}?permanent=true` | Hard delete (only if never ordered) |
| POST | `/api/commerce/admin/products/{id}/restore` | Restore an archived product |
| POST | `/api/commerce/admin/inventory/{product_id}/adjust` | Adjust stock with reason |
| GET | `/api/commerce/admin/orders` | All orders |
| POST | `/api/commerce/admin/search/reindex` | Rebuild search projection |

### Example: create a product

```bash
TOKEN="<access token for owner@shop.local>"

curl -X POST "$PUBLIC_API_BASE_URL/api/commerce/admin/products" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "sku": "MUG-001",
        "name": "Ceramic Mug",
        "description": "350 ml, dishwasher safe.",
        "price_minor": 1299,
        "currency": "USD",
        "category": "kitchen",
        "initial_stock": 40,
        "image_keys": ["uploads/2026/10/mug-front.webp"],
        "status": "active"
      }'
```

### Example: remove a product

```bash
# Archive (safe default)
curl -X DELETE "$PUBLIC_API_BASE_URL/api/commerce/admin/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TOKEN"

# Permanent delete (rejected with 409 if the product was ever ordered)
curl -X DELETE "$PUBLIC_API_BASE_URL/api/commerce/admin/products/$PRODUCT_ID?permanent=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Error format

All services return a consistent problem-details shape:

```json
{
  "type": "https://shop.local/errors/insufficient-stock",
  "title": "Insufficient stock",
  "status": 409,
  "detail": "Requested 3, available 1.",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

---

## 11. Events and Contracts

Services communicate through **documented HTTP APIs or versioned events**. Every contract defines required and optional fields, types and validation, authentication, errors, timeouts, retry and idempotency behavior, ordering assumptions, and compatibility rules.

### Event catalog

| Event | Producer | Consumers | Purpose |
|---|---|---|---|
| `product.created.v1` | commerce-core | indexer, cache warmer, intelligence | New product visible |
| `product.updated.v1` | commerce-core | indexer, cache invalidator | Details or price changed |
| `product.archived.v1` | commerce-core | indexer, cache invalidator, intelligence | Product removed from storefront |
| `product.deleted.v1` | commerce-core | indexer, cache invalidator, intelligence | Product permanently removed |
| `inventory.changed.v1` | commerce-core | cache invalidator, low-stock alerts | Stock changed |
| `order.placed.v1` | commerce-core | email worker, intelligence | Order committed |
| `payment.captured.v1` | commerce-core | email worker, fulfillment | Payment confirmed |
| `reservation.expired.v1` | commerce-core | stock releaser | Release held stock |

### Envelope

```json
{
  "id": "0f7b6f0e-6b0e-4d1a-9c53-0a6a1d7c9e11",
  "type": "product.created",
  "version": 1,
  "occurred_at": "2026-10-18T09:30:00Z",
  "producer": "commerce-core",
  "trace": { "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" },
  "data": { "product_id": "…", "sku": "MUG-001" }
}
```

### Compatibility rules

- Additive changes only within a version; breaking changes require `v2` published alongside `v1`.
- Contract tests (consumer-driven) run in CI; incompatible changes fail the build.
- Consumers ignore unknown fields.
- Events are idempotent by `id`; ordering is only assumed per aggregate and never globally.

---

## 12. Security Model

### Threat model (what the PoC evaluates)

- Unauthenticated access to protected endpoints.
- Broken object-level authorization (customer A reading customer B's order).
- Overprivileged service identities.
- Injection and unsafe input handling (including uploaded files).
- Credential or token exposure.
- Denial of service through expensive endpoints.
- Resource exhaustion from queue backlogs or oversized requests.
- Compromised service credentials.
- Supply-chain vulnerabilities in dependencies.
- Malformed or replayed events.
- Misconfigured internal network access.

### Authentication and authorization

- `auth-service` issues short-lived JWT access tokens (`ACCESS_TOKEN_TTL_SECONDS`) plus refresh tokens through the authorization code flow with PKCE.
- **Resource services validate tokens themselves** using cached JWKS and enforce their own permissions. The auth service is not a per-request bottleneck, and its outage does not invalidate tokens already issued.
- Role (`customer`, `shop_owner`) is a claim; **authorization is enforced at every resource boundary**, including object-level checks.
- Spring Security cannot prevent insecure business authorization rules or compensate for overprivileged credentials, so the permission matrix below is tested independently.

### Service permission matrix

Each service has a distinct workload identity and the minimum permissions required.

| Service | PostgreSQL access | Broker | Object storage | Other |
|---|---|---|---|---|
| `commerce-core` | Owner of `commerce` schema (read/write) | Publish via outbox only | Read/write product images | Calls payments |
| `auth-service` | Owner of `identity` schema | None | None | Signs tokens |
| `catalog-reader` | **SELECT only** on `catalog_public` view | None | None | Redis, OpenSearch (read) |
| `intelligence` | SELECT on analytics views | Consume only | None | **No payment credentials** |
| `celery-worker` | Limited writes (processed-events, projections) | Consume | None | OpenSearch (write), SMTP |
| `outbox-publisher` | SELECT/UPDATE `outbox_event` only | Publish | None | None |
| `payments-sim` | None (own state) | None | None | Webhooks to commerce-core |

A catalog service must not be able to write orders. A recommendation service must not hold payment credentials. A search index must not be able to modify authoritative inventory.

```sql
-- Example: the Go catalog reader can only see a safe, read-only view
CREATE ROLE catalog_reader LOGIN PASSWORD :'CATALOG_READER_DB_PASSWORD';
REVOKE ALL ON ALL TABLES IN SCHEMA commerce FROM catalog_reader;
GRANT USAGE ON SCHEMA commerce TO catalog_reader;
GRANT SELECT ON commerce.catalog_public TO catalog_reader;  -- active products only
```

### Network segmentation (Compose networks)

| Network | Members | Purpose |
|---|---|---|
| `edge` | gateway, web | Only thing reachable from the host |
| `app` | gateway, services | Service-to-service traffic |
| `data` | services, Postgres, Redis, RabbitMQ, OpenSearch, MinIO | No direct access from `edge` |

Databases and the broker are **not** reachable from the edge network. Host port mappings for admin UIs are for local development only and are removed in deployment.

### Additional controls

- TLS for external traffic (and internal where appropriate) when deployed.
- Secret management and regular credential rotation; no secrets in images or git.
- Rate limits and request-size limits at the gateway.
- Upload validation: size, magic-byte content type, filename sanitization, no execution from the bucket.
- Dependency and container-image scanning (Trivy, `pip-audit`, `govulncheck`, OWASP Dependency-Check) in CI.
- Structured security logging without sensitive payloads.
- Content-Security-Policy and secure cookie settings on the web app.

> Polyglot diversity may reduce some correlated implementation failures, but it also adds dependency trees and security configurations. The assessment measures **both** effects.

---

## 13. Resilience and Failure Containment

Independent failure domains are implemented through **process boundaries, deployment boundaries, resource quotas, network policies, and separate credentials**, not merely separate Python modules.

### Per-service failure contract

Every service must define its critical dependencies, resource limits, timeouts and retries, health checks, fallback behavior, recovery procedure, and owner.

| Service | Critical dependencies | Optional dependencies | Fallback |
|---|---|---|---|
| `commerce-core` | PostgreSQL, payments | Redis, broker (outbox absorbs) | Continues without cache and broker |
| `catalog-reader` | PostgreSQL or Redis | OpenSearch | Postgres FTS |
| `auth-service` | Identity DB | None | Issued tokens remain valid |
| `intelligence` | None for checkout | Model files, DB | Popular-products fallback |
| `celery-worker` | Broker | OpenSearch, SMTP | Retry, then dead-letter |

### Resource limits (Compose example)

```yaml
services:
  intelligence:
    profiles: ["ai", "full"]
    deploy:
      resources:
        limits:
          cpus: "1.5"
          memory: 1536M
    environment:
      MAX_CONCURRENT_INFERENCES: "4"
  commerce-core:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
```

Heavy inference has its own limits so it cannot starve checkout.

### Rules of engagement for retries

- Retries are **bounded**, use exponential backoff with jitter, and respect backpressure.
- Retrying a failing dependency without limits amplifies outages.
- Circuit breakers apply to selected synchronous dependencies, **not** to operations whose semantics require a definitive outcome (such as payment capture).

### Compose healthchecks

Every container exposes `/healthz` (liveness) and `/readyz` (readiness, which verifies critical dependencies) and declares `healthcheck:` with `depends_on: condition: service_healthy` so startup order is deterministic.

---

## 14. Observability

Failures must be visible **across language boundaries**.

### Distributed tracing

OpenTelemetry-compatible instrumentation in every service. Trace context propagates across HTTP calls **and** asynchronous message headers (stored in `outbox_event.trace_ctx`), so a single trace can show: admin click, API call, transaction, outbox publish, worker indexing. A checkout trace should reveal where time was spent and whether latency came from the application, database, broker, or external provider.

### Metrics

- Request volume and latency by service (p50/p95/p99), HTTP error rates.
- Database pool saturation, lock contention, slow queries.
- Queue depth, **oldest-message age**, retries, dead-letter counts.
- Cache hit rate.
- CPU and memory per container.
- Payment reconciliation discrepancies.
- **Inventory invariant violations** (should always be zero).
- Search index lag (time between commit and indexed).

### Logs

Structured JSON with correlation identifiers, service identity, operation name, and outcome. Never log passwords, payment secrets, full tokens, or unnecessary customer data.

### Recovery runbooks (in `docs/runbooks/`)

- Database unavailability or failover.
- Broker outage and replay.
- Search index reconstruction.
- Compromised service credentials.
- Payment reconciliation.
- Queue backlog recovery.
- Failed deployment rollback.

> A system is not operationally resilient merely because its components restart automatically.

---

## 15. Testing Strategy

| Layer | Tooling | What it proves |
|---|---|---|
| Unit | pytest, JUnit 5, `go test`, Vitest | Business rules, validation |
| Integration | Testcontainers or Compose | Real PostgreSQL, Redis, RabbitMQ behavior |
| Contract | Schemathesis, Pact or OpenAPI diff, JSON Schema | API and event compatibility |
| Concurrency | pytest + threads, custom Go harness | No overselling, no duplicate orders |
| End-to-end | Playwright | Admin adds and removes a product; customer buys it |
| Load | k6 | Latency and throughput profiles |
| Fault injection | Toxiproxy, `docker kill`, chaos endpoints | Blast radius and recovery |
| Security | ZAP baseline, authz matrix tests, Trivy | Boundaries hold |

### End-to-end acceptance journey (Playwright)

1. Sign in as `owner@shop.local`.
2. Create a product with an image and 5 units of stock.
3. Verify it appears on the storefront.
4. Sign in as a customer and buy 2 units; verify the stock is 3.
5. As the owner, archive the product; verify it vanishes from the storefront and search.
6. Verify the customer's order history still renders it correctly.
7. Attempt permanent delete; verify `409` because the product was ordered.

---

## 16. Real-World Scenario Tests (A to G)

Each scenario runs against the baseline and the polyglot implementation where applicable. Scripts live in `tests/scenarios/` and run with `make scenario-<letter>`.

| # | Scenario | Failure risk | Expected result |
|---|---|---|---|
| **A** | Flash sale and inventory contention | Overselling, exhausted DB connections | Reservations never exceed stock; duplicates create no extra orders |
| **B** | Recommendation service failure | Checkout waits on recommendations or shares CPU | Recommendations degrade independently; checkout meets its objectives |
| **C** | Broker interruption | Committed orders lose downstream events | Events recover from the outbox; nothing silently lost |
| **D** | Payment timeout and duplicate webhook | Duplicate charges, wrong status | System converges to the correct state with no duplicate logical charges |
| **E** | Database connection exhaustion | Unbounded queues, total unresponsiveness | Controlled shedding and backpressure |
| **F** | Compromised catalog service | Lateral movement into orders or payments | Independent access controls deny unauthorized operations |
| **G** | Search index corruption | Discovery fails; stale stock or price shown | Checkout unaffected; index rebuilt from authoritative records |

### Scenario A: Flash sale

1. Seed a product with a known quantity.
2. Fire concurrent purchase attempts at rising concurrency.
3. Restart application instances mid-test.
4. Compare accepted orders, inventory delta, latency, and contention.

**Metrics:** successful orders/s, p95/p99 latency, lock waits, DB connections, rejected reservations, **invariant violations (must be 0)**.

```bash
make scenario-a STOCK=100 CONCURRENCY=50,200,500
```

### Scenario B: Recommendation failure

1. Run normal catalog and checkout traffic.
2. Delay inference responses (`POST /chaos/latency` on `intelligence`).
3. Exhaust the service's permitted concurrency.
4. Restart the service.
5. Compare checkout latency and error rates before, during, and after.

### Scenario C: Broker interruption

1. Generate orders while recording outbox entries.
2. Cut broker connectivity (`docker compose pause rabbitmq` or Toxiproxy).
3. Keep committing orders and **add or remove products from the admin panel** while the broker is down.
4. Restore connectivity; observe publication, duplicate delivery, and consumer processing.

### Scenario D: Payment timeout and duplicate webhook

1. Configure `payments-sim` to time out after provider-side completion.
2. Replay identical webhooks; deliver some out of order.
3. Restart the payment worker mid-processing.
4. Run reconciliation.

### Scenario E: Connection exhaustion

1. Raise concurrency progressively with injected query delay.
2. Measure pool wait time and queue depth.
3. Verify timeouts and backpressure, then restore normal latency.

### Scenario F: Compromised catalog service

1. Use the catalog service's credentials in a test harness.
2. Attempt to read or modify orders and payment records, and to reach internal endpoints.
3. Revoke the credentials and verify isolation.

### Scenario G: Search corruption

1. Corrupt or delete index documents, then stop the search service.
2. Run catalog and checkout operations.
3. Rebuild via `POST /api/commerce/admin/search/reindex`.

---

## 17. Performance Methodology

### Benchmark the workload, not just the framework

Comparisons use equivalent business operations, schemas, dataset sizes, validation rules, transaction boundaries, and response payloads. A static-JSON "hello world" benchmark is **not** sufficient to choose a production architecture. Benchmarks must include real PostgreSQL queries, authentication, validation, and transactions.

### Workload profiles

| Profile | Workload | Primary objective |
|---|---|---|
| Read-heavy | Product retrieval, filtering, catalog queries | Throughput and latency |
| Write-heavy | Inventory reservation and order creation | Correctness under contention |
| Mixed | Catalog reads, cart updates, checkout | Realistic overall behavior |
| Admin-write | Product create, edit, archive with images | Outbox and projection latency |
| I/O-heavy | Multiple external service calls | Async concurrency and resource usage |
| CPU-heavy | Image processing or fraud scoring | Worker isolation |
| ML inference | Recommendation or classification | Throughput, latency, accelerator use |
| Recovery | Broker interruption, worker restarts | Durability and recovery behavior |

### Measurement requirements

For every run, record:

- Requests/s **and successful business operations/s**.
- p50, p95, p99 latency; HTTP and application error rates.
- CPU, memory, DB query latency, pool wait time.
- Queue depth and oldest-message age; cache hit rate.
- Infrastructure cost per successful operation.
- Correctness violations and reconciliation discrepancies.

Include warm-up, repeated runs, controlled concurrency levels, and durations long enough to expose queue buildup and leaks. Reports include workload, configuration, hardware, software versions, and duration. **No performance advantage is claimed without reproducible evidence.**

```bash
make bench PROFILE=read-heavy VUS=200 DURATION=5m
# results written to bench/results/<timestamp>/ with the full environment manifest
```

---

## 18. A/B and Comparative Evaluation

### Phase 1: Baseline comparison

Implement a vertical slice in the simplest credible architecture (a modular Django application) and equivalent functionality in the specialized-service architecture. Keep comparable: schema and indexes, dataset, validation, transaction boundaries, cache policy, authentication, hardware limits, request mix, and external dependency behavior. When evaluating a single framework, change only that variable.

### Phase 2: Controlled A/B experiment

Route a controlled share of synthetic or internal traffic to implementation A and the rest to B (gateway weighted routing). **Do not route real payment or inventory mutations to experimental implementations** unless they share a single authoritative transaction owner and the experiment preserves correctness.

Set the minimum detectable improvement and sample size **before** running. Use repeated trials and confidence intervals; never declare a winner from a small difference in one run.

### Phase 3: Fault-injection comparison

Inject process termination, network delay, dependency timeouts, database contention, broker interruption, invalid or duplicated messages, and memory or CPU pressure into **both** architectures. Measure blast radius, recovery time, lost or delayed work, and impact on unrelated operations. Ordinary load testing cannot establish resilience; this phase can.

### A/B rules

1. Change one major variable at a time.
2. Use identical business invariants in both implementations.
3. Separate performance testing from security testing.
4. Do not infer reliability from average latency.
5. Record the complete configuration for every run.
6. **Reject results that violate data correctness, even if throughput improves.**
7. Evaluate total operational cost, not server performance in isolation.

---

## 19. Acceptance Criteria

Proposed targets for the PoC (not production promises). Review them against real requirements before testing begins.

| Area | Criterion |
|---|---|
| Inventory correctness | Zero overselling in the defined concurrency test |
| Checkout idempotency | Zero duplicate logical orders for repeated requests with the same key |
| Admin product lifecycle | Create, edit, archive, restore, and permanent delete work end to end; archived products never appear to customers |
| Payment recovery | Duplicate callbacks create no duplicate logical payment operations |
| Event durability | Committed outbox events remain recoverable after broker interruption |
| Service isolation | A noncritical service failure does not violate critical commerce invariants |
| Security boundaries | Cross-domain unauthorized access tests are denied |
| Search recovery | Index can be rebuilt from authoritative records |
| Performance | Agreed p95/p99 targets met at the specified load |
| Resource control | Connection pools, worker concurrency, and memory remain bounded |
| Observability | Every critical workflow has traceable state transitions and actionable failure signals |
| Deployment safety | A service can be deployed or rolled back without simultaneous upgrades of every consumer |
| Operational cost | Measured cost is acceptable relative to the baseline and demonstrated benefits |
| **Localhost/free** | `docker compose --profile core up` yields a working shop with an admin panel and no paid accounts |
| **Config portability** | Switching from localhost to a deployed hostname requires environment changes only |

A separate service-level objective should define availability, recovery time, and recovery-point expectations. Those cannot be meaningfully chosen without knowing the required scale and acceptable downtime.

---

## 20. Repository Layout

```text
babel-bazaar/
├── README.md
├── .env.example
├── Makefile
├── docker-compose.yml
├── docker-compose.chaos.yml
├── docs/
│   ├── adr/                      # architecture decision records
│   ├── runbooks/                 # recovery procedures
│   ├── contracts/                # OpenAPI + event JSON Schemas
│   └── diagrams/
├── services/
│   ├── web/                      # Next.js storefront + /admin
│   ├── gateway/                  # nginx.conf (routing, limits)
│   ├── auth-service/             # Spring Boot + Spring Authorization Server
│   ├── commerce-core/            # Django (products, inventory, orders, outbox)
│   ├── catalog-reader/           # Go + Gin
│   ├── intelligence/             # FastAPI recommendations
│   ├── payments-sim/             # FastAPI payment provider simulator
│   ├── outbox-publisher/         # outbox to RabbitMQ
│   └── worker/                   # Celery tasks (index, cache, email, reconcile)
├── baseline/
│   └── monolith/                 # modular Django baseline for comparison
├── db/
│   ├── init/                     # roles, schemas, grants
│   └── seed/                     # demo data
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── e2e/                      # Playwright
│   ├── scenarios/                # A to G
│   └── security/
├── bench/
│   ├── k6/
│   └── results/
└── .github/workflows/            # CI: build, test, scan, contract checks
```

---

## 21. Development Workflow

### Common commands

| Command | Purpose |
|---|---|
| `make up` / `make up-full` | Start core / all profiles |
| `make down` / `make reset` | Stop / stop and wipe volumes |
| `make migrate` | Apply Django and identity migrations |
| `make seed` | Load demo catalog, owner, and customer |
| `make logs SERVICE=commerce-core` | Tail logs |
| `make test` | Unit and integration tests |
| `make e2e` | Playwright journey |
| `make scenario-a` ... `make scenario-g` | Failure scenarios |
| `make bench PROFILE=mixed` | k6 benchmark |
| `make reindex` | Rebuild the search projection |
| `make lint` | Linters and formatters for all languages |

### Running one service outside Docker

Keep infrastructure in containers and run the service you are developing natively for fast iteration:

```bash
docker compose --profile core up -d postgres redis rabbitmq minio mailpit

# Django
cd services/commerce-core && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python manage.py runserver 0.0.0.0:8000

# Go
cd services/catalog-reader && go run ./cmd/server

# Spring Boot
cd services/auth-service && ./mvnw spring-boot:run

# Next.js
cd services/web && npm install && npm run dev
```

Point native processes at `localhost` datastores by using the host-mapped ports in a `.env.local` override.

### Conventions

- Every new service requires an ADR justifying its existence with a measurement or isolation property.
- Every endpoint is covered by an OpenAPI schema and a contract test.
- Every database change ships as a migration; no manual schema edits.
- Every event is versioned and documented in `docs/contracts/`.

---

## 22. Implementation Roadmap

Begin with a small but realistic workflow, **not** a large collection of services. Add Spring Boot, FastAPI, or Go only where a specific requirement justifies the complexity.

> **What runs today:** `make up` starts the **Go BFF on :8080, the only public port**. Behind it, on internal networks with no host route, run `auth-service` (Spring), `commerce-core` (Django), `catalog-reader` (Go) and `intelligence` (FastAPI); each refuses unsigned requests. Also running: PostgreSQL (per-service roles), RabbitMQ, `outbox-publisher`, `worker`, Mailpit (:8025), a one-shot migrator and the reservation expirer. `make seed`, `make api ROLE=owner CALL="GET /api/admin/dashboard"` (signs in through the BFF), `make test`, `make test-go`, `make worker-status`, `make dlq-replay` and `make scenario-c` work. The web app has not landed yet. Targets in [section 21](#21-development-workflow) that are not in the `Makefile` yet belong to later phases. Event consumers use plain AMQP rather than Celery tasks; see [ADR 010](docs/adr/010-event-consumers-use-plain-amqp.md).

### Phase 1: Baseline
- [x] Domain model and core invariants
- [x] Baseline commerce API (Django monolith) with admin add/remove product (`services/commerce-core`)
- [x] PostgreSQL schema and migration strategy (`commerce` schema, per-service roles in `db/init/`)
- [ ] Representative load tests and initial cost/latency measurements

**Exit:** baseline is correct and measurements are reproducible.

### Phase 2: Transactional core
- [x] Product and inventory operations, including archive and delete rules
- [x] Cart, checkout, order state machine (payment transitions arrive with `payments-sim`)
- [x] Inventory reservation, idempotent requests
- [x] Transactional outbox (relay to a log sink; RabbitMQ sink is Phase 3)

**Exit:** concurrency and transaction tests pass.

### Phase 3: Asynchronous processing
- [x] RabbitMQ integration, worker processes (`services/outbox-publisher`, `services/worker`)
- [x] Retry and dead-letter policies, idempotent consumers (TTL retry queues, DLQ + replay tool)
- [x] Recovery procedures (`docs/runbooks/`, `make scenario-c`)
- [ ] Payment reconciliation (needs `payments-sim`, Phase 4)

**Exit:** broker and worker failure scenarios pass.

### Phase 4: Specialized services
- [ ] Next.js storefront and admin panel against the commerce API
- [x] Go `catalog-reader` (standard `net/http` and pgx rather than Gin; SELECT-only role on two views). Not yet: Redis cache, benchmark
- [x] Go BFF in front of every service, core services gated by signed requests ([ADR 012](docs/adr/012-bff-and-gated-core.md))
- [x] FastAPI `intelligence` (recommendations, degrades gracefully)
- [ ] FastAPI `payments-sim`
- [x] Spring Boot `auth-service` and token validation (commerce-core validates via cached JWKS; later services follow the same pattern)
- [x] Versioned event contracts (`docs/contracts/events`), the BFF API contract, and the cross-language signing vectors
- [ ] OpenAPI documents for the public API

**Exit:** each added service demonstrates a measurable benefit or a required security or isolation property.

### Phase 5: Fault injection and security
- [ ] Failure-injection suite and service-permission matrix
- [ ] Recovery-time measurements
- [x] Privileged access like Ensuretech's: service accounts (SYSTEM tokens) and audited break-glass ([docs/break-glass.md](docs/break-glass.md))
- [x] Dependabot for every ecosystem, CI that fails on deprecated APIs ([docs/dependency-updates.md](docs/dependency-updates.md))
- [ ] Security findings and remediation plan
- [ ] Cross-service observability dashboards

**Exit:** critical invariants survive failures and security boundaries behave as designed.

### Phase 6: Comparative evaluation
- [ ] Baseline-vs-polyglot performance report
- [ ] Cost comparison and operational complexity assessment
- [ ] Recommendation to retain, replace, or remove each specialized component

**Exit:** each runtime and service boundary is justified by evidence.

---

## 23. Risks and Trade-offs

| Risk | Description | Mitigation |
|---|---|---|
| **Operational complexity** | Multiple build pipelines, dependency ecosystems, runtimes, and on-call skills | Limit runtime count, standardize containers and telemetry, require justification per service |
| **Distributed consistency** | Cross-service workflows cannot rely on one transaction | Keep strongly coupled operations together; explicit state machines, durable events, reconciliation |
| **Security configuration drift** | Frameworks differ in authn/authz, validation, error handling | Common requirements, automated contract and authz tests, standard identity protocols |
| **Duplicated business rules** | Pricing or inventory rules reimplemented in several languages | One authoritative owner per invariant; others call it or consume versioned projections |
| **Premature optimization** | Go or Rust introduced before a bottleneck is proven | Require a benchmark, a target, and an ADR before extraction |
| **Shared infrastructure failure** | Services still share the database, broker, network, or identity provider | Identify common dependencies, bound consumption, test infrastructure-level recovery |
| **Laptop resource limits** | Full stack is heavy locally | Compose profiles; Postgres FTS fallback; CPU-only inference |
| **Local/production drift** | Localhost shortcuts (HTTP, open ports) leak into deployment | Distinct public/internal URL variables, a deployment checklist, ports removed in prod config |

---

## 24. GitHub Pages Demo Mode

> **As built:** the demo is the real storefront (`web/`) built with `npm run build:demo`, which swaps the
> HTTP client for an in-browser backend implementing the same API and rules (`web/src/api/demo.ts`). The
> Pages site (`site/`, deployed by `.github/workflows/pages.yml`) adds a landing page and the docs. The rest
> of this section is the original design and is kept for reference.

GitHub Pages serves **static files only**: no servers, no containers, no databases, no custom response headers. The full stack cannot run there. What *can* run there is a **browser-hosted simulation** of the architecture, where each "service" is a Web Worker or WebAssembly module and the browser plays the role of the network. The same frontend then switches to the real backend with a single environment variable.

This gives you a public, zero-cost, click-and-try showcase (including the shop owner admin panel) while the Docker Compose stack remains the real proof of concept.

### 24.1 Two modes, one frontend

| Mode | Trigger | API base | State lives in |
|---|---|---|---|
| **Demo** (GitHub Pages) | `NEXT_PUBLIC_BACKEND_MODE=demo` | A Service Worker answering `${BASE_PATH}/api/*` in the browser | The visitor's browser (IndexedDB / OPFS) |
| **Live** (Compose, or any real deployment) | `NEXT_PUBLIC_BACKEND_MODE=live` | `NEXT_PUBLIC_API_BASE_URL` | Real PostgreSQL, Redis, RabbitMQ, etc. |

The frontend only ever calls `fetch(`${API_BASE}/api/...`)`. In demo mode a Service Worker intercepts those requests, so **no frontend code differs between modes**. This also means a Pages-hosted frontend can be pointed at a real backend later (for example a tunnel or free-tier host) by changing `NEXT_PUBLIC_API_BASE_URL` and rebuilding.

### 24.2 What each component becomes in the browser

| Real component | Demo-mode stand-in | Fidelity |
|---|---|---|
| `gateway` (Nginx) | **Service Worker** routing `/api/*`, enforcing size limits and rate limits | High: same routes and status codes |
| `web` (Next.js) | **Static export** (`output: "export"`) | Full |
| PostgreSQL | **PGlite** (Postgres compiled to WASM, persisted in IndexedDB) | High: real SQL, `CHECK` constraints, transactions, the same migrations |
| `commerce-core` (Django) | **Pyodide worker** running the Python domain layer (see tiers below) | Medium to high |
| `catalog-reader` (Go + Gin) | **Go compiled to WASM** (`GOOS=js GOARCH=wasm`, or TinyGo for a smaller binary) in a Worker | High for logic; Gin's HTTP server is replaced by a thin handler adapter |
| `intelligence` (FastAPI) | **Pyodide worker** exposing the ASGI app through an in-process call | Medium: popularity/co-purchase logic works; heavy ML does not |
| `payments-sim` (FastAPI) | Same Pyodide worker or a plain JS worker, with chaos controls as UI buttons | High |
| `auth-service` (Spring Boot) | **Java via TeaVM** (Java bytecode to JS/WASM) for token issuing and validation, backed by WebCrypto | Low to medium: Spring Boot itself cannot run here |
| RabbitMQ + `celery-worker` | **Broker Worker** with an IndexedDB-backed durable queue; consumer Workers with ack, bounded retry, and a dead-letter queue | Medium: same semantics, one browser |
| `outbox-publisher` | A Worker polling the `outbox_event` table in PGlite | High: the real pattern |
| Redis | In-memory `Map` with TTLs in the catalog Worker | Medium |
| OpenSearch | **MiniSearch** or PGlite full-text search, rebuilt from product rows | Medium |
| MinIO (S3) | **OPFS / IndexedDB** blob store; images served by the Service Worker via blob URLs | Medium |
| Mailpit | An in-app "Inbox" page | Cosmetic, but shows emails |
| Jaeger / Prometheus | A built-in trace and queue viewer fed by the Workers' `postMessage` events | Medium |
| Toxiproxy / chaos endpoints | UI toggles that pause, delay, or kill a Worker | High for demonstration |

### 24.3 Fidelity tiers (build in this order)

Each tier is independently shippable, so there is always something deployable.

| Tier | Adds | Result |
|---|---|---|
| **0: Static showcase** | Next.js static export; products, cart, admin forms backed by IndexedDB | A working shop and admin panel with no architecture shown |
| **1: Browser backend** | Service Worker gateway, PGlite with the real migrations, outbox table, broker Worker, projection Workers, Go/WASM catalog | The transactional core, outbox, and async path are real and observable |
| **2: Python in the browser** | Pyodide worker for the domain layer, intelligence, and payments-sim | The Python part of the polyglot story is genuinely Python |
| **3: Java in the browser** | TeaVM-compiled token module | All four languages are present, with the Java piece intentionally small |
| **4: Hybrid** | `live` mode pointing at a real backend | The same UI against the full Compose stack |

> **Validate with spikes before committing.** Three items are plausible but unproven for this project and should each get a one-day spike: (1) Django ORM against PGlite needs a custom database backend bridge, because `psycopg` is unavailable in Pyodide. The fallback is Django on Pyodide's bundled SQLite, which keeps `CHECK` constraints and conditional updates but loses real Postgres locking semantics, which must then be stated in the report. (2) TeaVM handling the chosen Java code. (3) Go/WASM payload size on a cold load.

### 24.4 What the shop owner can do in demo mode

Everything in [Shop Owner Admin Panel](#7-shop-owner-admin-panel): add products with images, edit them, adjust stock, archive or permanently delete, and view orders. A visitor acts as owner and customer in the same browser.

Important behaviors:

- **Per-visitor sandbox.** Each browser has its own private shop. Products one visitor adds are **not** visible to anyone else. A **Reset demo** button wipes IndexedDB and reseeds.
- **Persistence.** Data survives reloads in the same browser until storage is cleared.
- **Same rules.** Archive vs permanent delete, idempotent checkout, price snapshots, and optimistic `If-Match` versioning all run against the same SQL constraints.
- **Banner.** A persistent "Demo mode: data stays in your browser" banner is shown.

### 24.5 Scenarios that translate to the browser

| Scenario | In Pages demo | Notes |
|---|---|---|
| A: Flash sale | Partial | Concurrent clicks and multiple tabs exercise the reservation SQL, but real multi-connection contention needs the Compose stack |
| B: Recommendation failure | **Yes** | Kill or delay the intelligence Worker; checkout keeps working |
| C: Broker interruption | **Yes** | Pause the broker Worker, keep adding products and orders, resume, and watch the outbox drain |
| D: Payment timeout and duplicate webhook | **Yes** | Chaos buttons on payments-sim; reconciliation runs in a Worker |
| E: Connection exhaustion | No | Needs real pools and a real database server |
| F: Compromised catalog service | Illustrative only | The Go/WASM module has no database credentials by construction, but network and role isolation cannot be proven in a browser |
| G: Search corruption | **Yes** | Delete the index store, then rebuild from product rows |

Load testing and the A/B performance comparisons are **not meaningful** in demo mode (one user, one device) and must use the Compose stack.

### 24.6 Hard limits and honest caveats

**Security is demonstrative.** All code runs in the visitor's browser, so "login" and role checks protect nothing. Never enter real data. The README, the UI banner, and the admin page must all say so.

**GitHub Pages constraints that shape the design:**

| Constraint | Consequence |
|---|---|
| Published sites may be no larger than 1 GB; source repos have a recommended limit of 1 GB | Comfortable for WASM runtimes, but keep large assets out of git |
| Soft bandwidth limit of 100 GB per month | Lazy-load Pyodide, Go/WASM, and PGlite only when a feature needs them; enable long cache lifetimes via hashed filenames |
| Deployments time out after 10 minutes | Keep the build lean; cache dependencies in CI |
| Soft limit of 10 builds per hour, not applied to custom GitHub Actions workflows | Deploy with the Actions workflow below |
| No custom response headers | Cross-origin isolation (`COOP`/`COEP`) is unavailable, so `SharedArrayBuffer` cannot be relied on. Use message passing and IndexedDB, not shared memory |
| No server-side rewrites | Static export with dynamic admin routes needs `generateStaticParams` or query-string routes (for example `/admin/products/edit?id=…`), plus a `404.html` fallback |
| Site served under `/<repo>/` | Set `NEXT_PUBLIC_BASE_PATH=/babel-bazaar`, and register the Service Worker with that scope |
| Pages sites are public on the free plan | Treat everything shipped as public |

**Other caveats**

- First load downloads the WASM runtimes. Show a progress screen and cache aggressively.
- Safari and private-browsing modes limit IndexedDB and OPFS storage; detect failure and fall back to in-memory with a warning.
- Service Workers require HTTPS or `localhost`; both are satisfied by Pages and local dev.
- Cold-starting several Workers is slower than the Compose stack's warm containers. Report demo-mode timings separately and never mix them with benchmark results.

### 24.7 Environment variables for Pages builds

```dotenv
NEXT_PUBLIC_BACKEND_MODE=demo            # demo | live
NEXT_PUBLIC_BASE_PATH=/babel-bazaar       # repo name for project pages; empty for a custom domain
NEXT_PUBLIC_API_BASE_URL=                 # empty in demo mode; set in live mode
NEXT_PUBLIC_DEMO_RESET_ENABLED=true
NEXT_PUBLIC_RUNTIME_ASSET_BASE=           # optional CDN for large WASM assets
```

### 24.8 Build and deploy with GitHub Actions

`.github/workflows/pages.yml`:

```yaml
name: Deploy demo to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: services/web/package-lock.json

      - uses: actions/setup-go@v5
        with:
          go-version: "1.22"

      - name: Build Go catalog to WASM
        run: |
          cd services/catalog-reader
          GOOS=js GOARCH=wasm go build -o ../web/public/wasm/catalog.wasm ./cmd/catalog-wasm
          cp "$(go env GOROOT)/lib/wasm/wasm_exec.js" ../web/public/wasm/ \
            || cp "$(go env GOROOT)/misc/wasm/wasm_exec.js" ../web/public/wasm/

      - name: Build Java token module (TeaVM)
        run: |
          cd services/auth-service
          ./mvnw -q -Pteavm package
          cp target/teavm/* ../web/public/wasm/

      - name: Build static site
        working-directory: services/web
        env:
          NEXT_PUBLIC_BACKEND_MODE: demo
          NEXT_PUBLIC_BASE_PATH: /babel-bazaar
        run: |
          npm ci
          npm run build        # next.config.js sets output: "export"

      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: services/web/out

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

Enable it once under **Settings → Pages → Source: GitHub Actions**. Check for newer major versions of these actions when you set it up. The live site appears at `https://<you>.github.io/babel-bazaar/`.

### 24.9 Demo-mode source layout

```text
services/web/
├── public/
│   ├── wasm/                    # catalog.wasm, wasm_exec.js, teavm output
│   └── pyodide/                 # self-hosted Pyodide runtime and wheels
└── src/
    ├── demo-backend/
    │   ├── sw.ts                # Service Worker gateway
    │   ├── workers/
    │   │   ├── db.worker.ts     # PGlite + migrations
    │   │   ├── commerce.worker.ts   # domain layer (Pyodide or TypeScript)
    │   │   ├── catalog.worker.ts    # loads Go/WASM
    │   │   ├── broker.worker.ts     # durable queue, ack, retry, DLQ
    │   │   ├── projections.worker.ts# search index and cache invalidation
    │   │   └── payments.worker.ts   # simulator with chaos controls
    │   └── chaos-panel.tsx      # pause / delay / kill any "service"
    └── lib/api.ts               # one fetch wrapper, mode-agnostic
```

### 24.10 Recommended scope

Ship **Tier 1** first: it already demonstrates the transactional outbox, durable async processing, idempotent checkout, the admin add/remove lifecycle, and fault injection, all with real SQL. Add Pyodide (Tier 2) and TeaVM (Tier 3) afterward so that each language in the polyglot claim is demonstrably present. Treat the Compose stack as the source of truth for every performance and security claim.

---

## 25. Deploying Beyond Localhost

The design goal is that **moving off localhost changes configuration, not code**.

### Checklist

1. **Set public URLs:** `PUBLIC_WEB_URL`, `PUBLIC_API_BASE_URL`, `PUBLIC_AUTH_ISSUER_URL`, `PUBLIC_ASSET_BASE_URL`, `S3_PUBLIC_ENDPOINT_URL`.
2. **Rebuild the web image** so `NEXT_PUBLIC_*` values are baked in.
3. **Replace every dev secret** (database passwords, MinIO keys, broker credentials, seed passwords). Disable the seed accounts.
4. **Update OIDC redirect URIs and CORS origins** to the deployed hostname.
5. **Enable TLS** at the gateway or load balancer; set secure cookies.
6. **Remove host port mappings** for databases, broker, OpenSearch, and admin UIs.
7. **Swap local stand-ins for real services**, using only environment changes:
   - `payments-sim` to a real provider adapter.
   - MinIO to any S3-compatible object store (`S3_ENDPOINT_URL`).
   - Mailpit to a real SMTP provider (`SMTP_*`).
   - Container Postgres/Redis/RabbitMQ to managed equivalents (`*_URL`).
8. **Disable chaos endpoints:** `CHAOS_ENABLED=false`.
9. **Run migrations** and verify with `make smoke` pointed at the deployed base URL.

```bash
BASE_URL=https://shop.example.com make smoke
```

Pricing and free-tier availability of any hosting provider change over time, so verify current terms before choosing one. The local stack has no such dependency.

---

## 26. Architecture Decision Records

Initial decisions (full text in `docs/adr/`):

| ADR | Decision | Rationale |
|---|---|---|
| 001 | Django owns all transactional commerce data | One owner for invariants; avoids duplicated rules |
| 002 | Transactional outbox for all events | Eliminates the commit-then-publish failure window |
| 003 | Spring Authorization Server for local and primary identity | Free on localhost, standards-based, resource services validate independently |
| 004 | Go reads through a SELECT-only database role | Limits blast radius of a compromised read service |
| 005 | Search index is a rebuildable projection | Index loss must never affect orders |
| 006 | "Remove product" defaults to archive | Protects order history; permanent delete only if never ordered |
| 007 | Payments simulator instead of a hosted provider | Free, deterministic chaos testing on localhost |
| 008 | Rust deferred | No measured need; avoid adding variables before evaluating the initial design |
| 009 | Pages demo simulates services in-browser (Service Worker, WASM, Workers) behind the same API contract | Free public showcase; one frontend for demo and live modes |
| [010](docs/adr/010-event-consumers-use-plain-amqp.md) | Event consumers use plain AMQP, not Celery tasks | Events are a language-neutral contract; Celery's task protocol is Python RPC |
| [012](docs/adr/012-bff-and-gated-core.md) | A Go BFF is the only entry point; every core service refuses unsigned requests, except SYSTEM and audited break-glass tokens (adopted from Ensuretech) | Two independent proofs on every call: origin (HMAC signature) and user (JWT); a controlled way in when the BFF is down |
| [011](docs/adr/011-auth-service-token-design.md) | Token design: PKCE only, `sub` = account UUID, persisted signing key, JWKS cache that survives auth outages | Existing sessions survive auth-service restarts and outages |

### Rust (future evaluation)

Rust is a candidate for a later phase if a specific service shows a demonstrated need for memory efficiency, low-level control, or resource characteristics that justify its development overhead. It is excluded from the first PoC so the initial architecture can be evaluated with fewer variables.

---

## 27. FAQ

**Is this overkill?**
Yes, on purpose. It is a showcase. The evaluation phases exist specifically to document where the added complexity does and does not pay off.

**Does it really run for free?**
Every component is open source and runs in local containers. No cloud account, API key, or payment is required. Image sizes and memory are the only cost, which is why profiles exist.

**Is there a live demo without installing anything?**
Yes: the GitHub Pages build simulates the architecture in the browser. See [GitHub Pages Demo Mode](#24-github-pages-demo-mode) for what it can and cannot show.

**Can I run it without Docker?**
Yes, but you must provide PostgreSQL, Redis, and RabbitMQ yourself. See [Development Workflow](#21-development-workflow).

**Why not use Keycloak or a managed identity provider?**
Both are valid. A Spring Authorization Server keeps the stack small, free, and in-language for the Java showcase. Because everything speaks standard OIDC, swapping providers is a configuration change.

**Why is "remove product" an archive by default?**
Deleting rows referenced by past orders breaks history and reporting. Archiving hides the product everywhere customers look while preserving integrity.

**What happens if the search index is wrong or missing?**
Nothing critical. It is a projection. Rebuild it from authoritative data with `make reindex`.

**How do I change the base URL?**
Edit the `PUBLIC_*` variables in `.env` and rebuild `web`. See [Configuration](#6-configuration-and-the-base-url-strategy).

**Where does GPU fit in?**
Optionally in the `intelligence` service for heavy inference. FastAPI is not a GPU runtime; libraries such as PyTorch and the device runtime provide it. It is never required locally.

---

## 28. Final Recommendation

Establish authoritative data ownership, transactional correctness, idempotent processing, durable asynchronous events, and measurable service-level objectives **first**. Only then introduce Spring Boot, FastAPI, or Go where a specific requirement justifies the added complexity.

The architecture is successful only if diversity produces real benefits: improved resource efficiency, reduced blast radius, stronger access isolation, better workload separation, or capabilities that would be difficult to implement economically in a single stack. Conversely, if the simpler baseline meets the same performance, security, and reliability requirements at lower cost, **the simpler architecture should win.**

> **Optimize for containment and recoverability, not for the number of frameworks used.**

A resilient polyglot system is one in which components fail independently, critical business invariants stay protected, failures are observable, and recovery is repeatable. This project exists to demonstrate those properties with evidence rather than assume them from technology selection.

---

## License

Choose a license before publishing (MIT or Apache-2.0 are common for showcase projects).

## Contributing

1. Open an issue describing the change and which invariant, scenario, or ADR it touches.
2. Add or update tests and contracts with the change.
3. Run `make lint test e2e` before opening a pull request.
4. Any new service or runtime requires an ADR with a measured justification.
