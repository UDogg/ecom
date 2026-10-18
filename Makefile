# Convenience wrapper around Docker Compose. Run `make help` for the list.
COMPOSE ?= docker compose
CORE    := $(COMPOSE) --profile core
RUN     := $(CORE) run --rm --no-deps commerce-core
SERVICE ?= commerce-core
ROLE    ?= owner

.PHONY: help env up up-full down reset migrate seed logs test test-local test-auth test-async test-go api dev-token \
        shell worker-status dlq-replay scenario-c

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || cp .env.example .env

up: env ## Start the core profile
	$(CORE) up --build -d

up-full: env ## Start every profile
	$(COMPOSE) --profile full up --build -d

down: ## Stop everything
	$(COMPOSE) --profile full down

reset: ## Stop and wipe all volumes (clean slate)
	$(COMPOSE) --profile full down -v

migrate: ## Apply database migrations
	$(RUN) python manage.py migrate

seed: ## Load the demo catalog
	$(RUN) python manage.py seed

logs: ## Tail logs (SERVICE=commerce-core)
	$(CORE) logs -f $(SERVICE)

test: env ## Run the commerce-core test suite against the Compose Postgres
	$(CORE) up -d postgres
	$(CORE) run --rm -v $(CURDIR)/docs/contracts:/contracts:ro -e CONTRACTS_DIR=/contracts/events commerce-core pytest

test-local: ## Run commerce-core tests natively (needs Postgres on localhost; see services/commerce-core/README.md)
	cd services/commerce-core && pytest

test-auth: ## Run auth-service tests natively (Java 21, Postgres on localhost with an auth_test database)
	cd services/auth-service && ./mvnw -B test

test-async: ## Run outbox-publisher and worker tests natively (Postgres and RabbitMQ on localhost)
	cd services/outbox-publisher && pytest
	cd services/worker && pytest

test-go: ## Run the Go BFF and catalog-reader tests (catalog DB tests need CATALOG_TEST_DATABASE_URL)
	cd services/bff && go vet ./... && go test ./...
	cd services/catalog-reader && go vet ./... && go test ./...

api: ## Call the BFF as the seeded user: make api ROLE=owner CALL="GET /api/admin/dashboard"
	@python3 scripts/bff.py --role $(if $(filter shop_owner owner,$(ROLE)),owner,customer) $(CALL)

dev-token: ## Print an HS256 dev token (only if JWT_DEV_HS256_SECRET is set)
	@$(RUN) python manage.py dev_token --role $(if $(filter owner shop_owner,$(ROLE)),shop_owner,customer)

worker-status: ## Queue depths, including retry and dead-letter queues
	@$(CORE) exec worker python -m worker status

dlq-replay: ## Replay dead-lettered messages (CONSUMER=order-email|low-stock)
	$(CORE) exec worker python -m worker replay $(CONSUMER)

scenario-c: ## Scenario C: broker outage loses no events (needs `make up seed`)
	python3 tests/scenarios/scenario_c.py

shell: ## Django shell
	$(RUN) python manage.py shell
