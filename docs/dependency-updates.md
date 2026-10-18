# Dependency updates

The stack is kept current continuously, in small steps, so that nothing reaches end of life
and no deprecation turns into a migration project.

## What updates what

[Dependabot](../.github/dependabot.yml) checks every Monday at 04:00 UTC:

| Ecosystem | Where | Pull requests |
|---|---|---|
| pip | the five Python services (`requirements*.txt`, exact pins) | minor + patch grouped; each major alone |
| gomod | `services/bff`, `services/catalog-reader`, `bench/loadgen`, `tools/go` | minor + patch grouped; each major alone |
| npm | `web` (Preact, Vite, TypeScript, Vitest; exact pins) | minor + patch grouped; each major alone |
| maven | `services/auth-service` (Spring Boot parent and plugins) | minor + patch grouped; each major alone |
| docker | base images of every Dockerfile | patch grouped; a new runtime (`python:3.15`, `golang:1.28`) alone |
| docker-compose | Postgres, RabbitMQ, Mailpit, opsbox | patch grouped; others alone; no Postgres majors (see below) |
| github-actions | `.github/workflows/*` | minor + patch grouped; each major alone |

Python dependencies are pinned exactly (`==`), so every build is reproducible and every change
of version is a reviewed pull request rather than a silent drift at image build time.

The Go linters CI uses (`staticcheck`, `govulncheck`) are pinned as `tool` dependencies in
[`tools/go/go.mod`](../tools/go/go.mod), so they are updated the same way.

## How CI catches deprecations

[CI](../.github/workflows/ci.yml) runs on every pull request, and also once a week on a schedule
so a new advisory or a runner change shows up even when nothing was pushed.

| Language | Gate |
|---|---|
| Python | `pytest.ini` turns `DeprecationWarning` and `PendingDeprecationWarning` into errors (Django's `RemovedInDjangoXXWarning` included) |
| Go | `staticcheck -checks SA1019` fails on any use of a deprecated API; `go vet`; `govulncheck` |
| Java | `javac -Xlint:deprecation,removal` with `failOnWarning` |
| TypeScript | `tsc --strict` in every web build; Vitest |
| Docker | the `compose` job builds every image, so a broken base image fails the pull request |

A green grouped update is therefore safe to merge as is. A red one names exactly what was
deprecated. If the warning is raised inside a library (nothing we can change), silence that one
message with a targeted `ignore:` line in the service's `pytest.ini`, with a comment saying why.

## Repository settings

These live in GitHub, not in the repository (Settings, then Code security):

* **Dependabot alerts** and **Dependabot security updates**: on. Security updates are then opened
  as soon as an advisory is published, outside the weekly schedule.
* Optionally, **auto-merge** for the grouped minor and patch pull requests, once branch protection
  requires the CI jobs. It is not set up here: every update is merged by a person.

## Updates that need a person

* **Postgres majors** are ignored by Dependabot: a new major cannot open the old data directory.
  Plan it: `pg_dumpall` from the running stack (or `pg_upgrade` with both versions installed),
  change the image tag in `docker-compose.yml` and in the CI service containers, restore, and
  run `./run.sh test`. Postgres 16 is supported until November 2028.
* **RabbitMQ majors** come as their own pull request. A node enables the new version's required
  feature flags on boot; the broker runs as `user: rabbitmq` with a fixed `hostname`, so its data
  directory survives the container being re-created.
* **Spring Boot majors** usually move Spring Security and the Authorization Server too: run the
  auth-service tests and `./run.sh scenario` before merging. (Boot 4 moved the Authorization
  Server into Spring Security 7, renamed starters such as `-web` to `-webmvc`, needs
  `spring-boot-starter-flyway` for migrations to run, and switched to Jackson 3.)
* The CI service containers (`postgres`, `rabbitmq` in `ci.yml`) are not covered by any
  Dependabot ecosystem: bump them together with the matching `docker-compose.yml` update.

## Current baseline

| Component | Version |
|---|---|
| Python | 3.14 (Django 6.1, DRF 3.18, FastAPI 0.143, psycopg 3.3) |
| Go | 1.27 (pgx 5.11) |
| Java | 21 LTS (Spring Boot 4.1, Spring Security 7) |
| PostgreSQL | 16 |
| RabbitMQ | 4.3 |
| GitHub Actions | checkout v7, setup-python v7, setup-go v7, setup-java v6 |
