#!/bin/sh
# Runs once, on first start of an empty Postgres volume.
# One login role per service, each with only what it needs (README section 12).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v commerce_pw="$COMMERCE_DB_PASSWORD" \
  -v reader_pw="$CATALOG_READER_DB_PASSWORD" \
  -v intel_pw="$INTELLIGENCE_DB_PASSWORD" \
  -v auth_pw="$AUTH_DB_PASSWORD" \
  -v db="$POSTGRES_DB" <<'SQL'
-- commerce-core owns the commerce schema. CREATEDB is only so the test
-- suite can create its throwaway test database in local development.
CREATE ROLE commerce LOGIN CREATEDB PASSWORD :'commerce_pw';
CREATE SCHEMA commerce AUTHORIZATION commerce;

-- catalog-reader: SELECT on commerce.catalog_public only (granted by the
-- migration that creates the view). Nothing else, ever.
CREATE ROLE catalog_reader LOGIN PASSWORD :'reader_pw';

-- intelligence: analytics views only (granted when they exist).
CREATE ROLE intelligence LOGIN PASSWORD :'intel_pw';

-- auth-service owns identity.
CREATE ROLE auth LOGIN PASSWORD :'auth_pw';
CREATE SCHEMA identity AUTHORIZATION auth;

-- Nobody gets implicit access to public.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO commerce, catalog_reader, intelligence, auth;
SQL
