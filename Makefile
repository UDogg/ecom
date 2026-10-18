# Convenience wrapper around Docker Compose. Run `make help` for the list.
COMPOSE ?= docker compose
CORE    := $(COMPOSE) --profile core
RUN     := $(CORE) run --rm --no-deps commerce-core
SERVICE ?= commerce-core
ROLE    ?= shop_owner

.PHONY: help env up up-full down reset migrate seed logs test test-local token shell

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
	$(CORE) run --rm commerce-core pytest

test-local: ## Run tests natively (needs Postgres on localhost; see services/commerce-core/README.md)
	cd services/commerce-core && pytest

token: ## Print a dev access token (ROLE=shop_owner|customer)
	@$(RUN) python manage.py dev_token --role $(ROLE)

shell: ## Django shell
	$(RUN) python manage.py shell
