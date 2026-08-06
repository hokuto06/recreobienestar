# Recreo Bienestar — Backend Architecture

Status snapshot as of Phase 2. Companion docs: `deploy/PHASE1_DELIVERABLES.md`,
`deploy/PHASE2_DELIVERABLES.md`. This file is the living reference; the
phase docs are point-in-time delivery records.

## 1. System overview

Recreo Bienestar is a Django backend/admin plus a read-only API, sitting
behind the same nginx reverse proxy that already serves the static
marketing site and several unrelated client sites on one shared EC2 host.
No public authentication, member dashboard, or payments exist yet — this
phase is content management (Carla, via Django Admin) and read-only public
data (via the API), full stop.

```mermaid
flowchart LR
    User[Visitor browser] -->|HTTPS| NGINX[nginx-proxy\nexisting, shared]
    Carla[Carla] -->|HTTPS /gestion/ - pending| NGINX
    NGINX -->|"/ (static files)"| Static[recreobienestar static site\nunchanged]
    NGINX -.->|"/gestion/ - not yet routed"| Django[recreo-django]
    NGINX -.->|"/api/ - not yet routed"| Django
    Django --> DB[(recreo-db\nPostgreSQL 16)]
```

Dotted lines mark routes that are built and tested but **not yet reachable
publicly** — see §12.

## 2. Docker architecture

Recreo Bienestar is its own Compose project (`recreo-bienestar-backend`),
deliberately **not** merged into the existing `nginx-flask-prod`
`docker-compose.yml`. It joins that stack's network as an external
dependency so nginx can reach it, without either project needing to know
about the other's internals.

```mermaid
flowchart TB
    subgraph "nginx-flask-prod (existing, unmodified)"
        NGINX[nginx-proxy]
        FLASK[flask-prod]
        CERTBOT[certbot]
        APIold[django-api\nintentionally stopped]
        BLOG[blog-front\nintentionally stopped]
    end
    subgraph "recreo-bienestar-backend (new, isolated project)"
        DJ[recreo-django\ngunicorn :8100]
        PG[(recreo-db\npostgres:16-alpine)]
    end
    NET1{{nginx-flask-prod_default\nexternal network}}
    NET2{{recreo_internal\nprivate bridge}}

    NGINX --- NET1
    FLASK --- NET1
    DJ --- NET1
    DJ --- NET2
    PG --- NET2
```

No new container publishes a host port. `recreo-db` is reachable only from
`recreo-django` on `recreo_internal`; `recreo-django` is reachable only
from `nginx-proxy` on the shared network (once routing is applied — see
§12).

## 3. Container and network relationships

| Container | Image | Networks | Published ports | Notes |
|---|---|---|---|---|
| `recreo-django` | built from `backend/Dockerfile` (python:3.11-slim) | `recreo_internal`, `nginx-flask-prod_default` | none | gunicorn, 1 worker/2 threads (memory-sized), WhiteNoise serves static/media |
| `recreo-db` | `postgres:16-alpine` | `recreo_internal` only | none | `pg_isready` healthcheck, named volume |
| `nginx-proxy` | existing | `nginx-flask-prod_default` | 80, 443 | unmodified |
| `django-api`, `blog-front` | existing | `nginx-flask-prod_default` | — | **intentionally stopped**, not part of this project |

## 4. PostgreSQL persistence model

```mermaid
flowchart LR
    DJ[recreo-django] -->|psycopg2| PG[(recreo-db process)]
    PG --> VOL[/recreo-bienestar-backend_recreo_db_data\nnamed Docker volume/]
    VOL --> HOST[/var/lib/docker/volumes/.../\non EC2 host disk/]
```

- Dedicated Postgres container — **not** the shared RDS instance used by
  `django-api`. RDS is live infra for an unrelated project, was mid a
  separate cleanup review, and attaching a new schema to it would have
  coupled two unrelated lifecycles. Full isolation was chosen instead.
- Data lives in the named volume `recreo-bienestar-backend_recreo_db_data`,
  which survives `docker compose restart`/`stop`/`up`/container recreation.
  It is only destroyed by an explicit `docker compose down -v`.
- No public port; reachable only from `recreo-django` on `recreo_internal`.
- Backup/restore flow: §14.

## 5. Django project structure

```
backend/
├── config/            settings, URL root, WSGI/ASGI, api_urls.py
├── common/             choices.py, text.py (slug + YouTube parsing),
│                        validators.py, models.py (shared abstract bases)
├── catalog/            Category, Program, Video + admin + serializers +
│                        views + filters + tests
├── memberships/         MembershipPlan, Subscription + access-control
│                        service + admin + serializers + views + tests
├── deploy/              nginx configs (proposed/snippet), phase docs
├── Dockerfile, entrypoint.sh, docker-compose.yml, requirements.txt
└── manage.py, .env.example
```

`common` exists specifically so `catalog` and `memberships` never need to
import each other's models to agree on what a "plan1/plan2/free/all_paid"
access level means — both read the same `VideoAccessLevel`/`PlanTier`
enums.

## 6. Domain models

```mermaid
erDiagram
    Category ||--o{ Video : "PROTECT"
    Program ||--o{ Video : "SET_NULL, optional"
    MembershipPlan ||--o{ Subscription : "PROTECT"
    User ||--o{ Subscription : ""

    Category {
        string name
        string slug
        text description
        bool is_active
        int display_order
    }
    Program {
        string name
        string slug
        text description
        string cover_image_url
        image cover_image
        bool is_active
        int display_order
    }
    Video {
        string title
        string slug
        string short_description
        text full_description
        string youtube_url
        string youtube_video_id "auto-extracted"
        string access_level "free|plan1|plan2|all_paid"
        bool is_published
        bool is_featured
        int display_order
        string duration_label
        datetime publication_date
    }
    MembershipPlan {
        string tier "plan1|plan2, unique"
        string name
        string slug
        text description
        decimal price
        string currency
        int duration_days "optional"
        bool is_active
        int display_order
    }
    Subscription {
        string status "trial|active|past_due|cancelled|expired"
        datetime starts_at
        datetime ends_at
        datetime cancelled_at
    }
```

All five models plus `MembershipPlan`/`Category`/`Program` inherit
`created_at`/`updated_at` from a shared `TimeStampedModel` mixin; the three
catalog-ish ones also share `is_active`/`display_order` via
`OrderedActiveModel`.

## 7. Access-control logic

Implemented, unit-tested, **not wired to any view yet** — no public
endpoint currently checks it, since there's no public auth to check it
against. Lives in `memberships/services.py`:

```mermaid
flowchart TD
    Start["can_access_video(user, video)"] --> Pub{video.is_published?}
    Pub -->|No| Deny[Deny]
    Pub -->|Yes| Level{access_level?}
    Level -->|free| Allow[Allow — anyone, incl. anonymous]
    Level -->|all_paid| AnyPlan{user has ANY\nactive subscription?}
    Level -->|plan1 / plan2| ThatPlan{user has active\nsubscription to THAT tier?}
    AnyPlan -->|Yes| Allow
    AnyPlan -->|No| Deny
    ThatPlan -->|Yes| Allow
    ThatPlan -->|No| Deny
```

`Subscription.is_active()` checks both `status in {trial, active}` **and**
`not is_expired()` — a stale `active` status never overrides a passed
`ends_at`. This is why "expired membership loses access immediately" holds
even if a status-sync job hasn't run yet.

## 8. Django Admin responsibilities

Carla's entire workflow today. Reachable at `/gestion/` once nginx routing
lands (§12); currently only reachable directly on the server (not
publicly). Per model:

- **Category / Program**: create/edit, active toggle, drag-free manual
  `display_order` (list-editable), bulk activate/deactivate.
- **Video**: full CRUD, category/program autocomplete, access-level picker,
  YouTube URL validated + video ID auto-extracted on save, thumbnail
  preview, bulk publish/unpublish/mark-free, list-editable display order.
- **MembershipPlan**: name/description/price/currency/duration editing,
  price validated non-negative, bulk activate/deactivate, live subscriber
  count.
- **Subscription**: status with a colored "current access" badge (computed
  from `is_active()`, not just the raw status field), bulk cancel.

No public-facing admin functionality exists — everything here requires
Django staff auth (see §10).

## 9. REST API endpoints

All under `/api/` (routing to nginx pending — see §12; reachable directly
on the server today). Every endpoint is **read-only**: list/retrieve
handlers only exist, so POST/PUT/PATCH/DELETE return 405 everywhere — there
is no write path to secure because none was built.

| Endpoint | Returns | Filters |
|---|---|---|
| `GET /api/categories/` | active categories | — |
| `GET /api/programs/` | active programs | — |
| `GET /api/videos/` | published videos | `?category=<slug>` `?program=<slug>` `?access_level=<level>` |
| `GET /api/videos/<slug>/` | one published video, 404 if unpublished/missing | — |
| `GET /api/plans/` | active plans | — |

Paginated (`PageNumberPagination`, 20/page). Serializers hand-pick fields —
`is_active`/`is_published`/timestamps/raw FK ids/raw `youtube_url` are
never exposed. Same-origin only; no CORS package installed (nothing to
configure yet — add an explicit allow-list, never a wildcard, if a
separate frontend origin appears later).

## 10. Current authentication status

- **Django Admin**: standard Django session auth (`django.contrib.auth`),
  staff/superuser only. No superuser exists yet — Carla's account is
  prepared but not created (§18).
- **REST API**: `AllowAny`, no authentication classes configured. Safe
  today only because every queryset is pre-filtered to
  active/published/public data before a serializer ever touches it — there
  is no gated content this API can leak.
- **Public registration/login**: does not exist. Planned for a future
  phase (§16).

## 11. Current production deployment status

| Component | Status |
|---|---|
| `recreo-django` | running, healthy, DRF deployed |
| `recreo-db` | running, healthy, migrations applied, never recreated during the API deploy |
| Django Admin (`/gestion/`) | working — verified directly against the container; **not publicly routed** |
| REST API (`/api/...`) | working — verified directly against the container; **not publicly routed** |
| Static site (`/`) | untouched, verified live over HTTPS |
| `django-api` / `blog-front` | intentionally stopped (memory headroom), untouched |
| nginx routing for `/gestion/` and `/api/` | prepared, **not applied** (§12) |
| Superuser | not created (§18) |

## 12. Nginx routing — still pending

`backend/deploy/recreobienestar.conf.proposed` adds `/gestion/` and `/api/`
`location` blocks to the existing `recreobienestar.com` server block,
before its catch-all `location /`. It does not touch the static site's
routing, `ssl_certificate` lines, or any other domain's config.

**Blocked by a pre-existing, unrelated issue**, not by this config:
`nginx -t` fails on `nginx-flask-prod/nginx/conf.d/default.conf` (a
different file, for `estebanmartins.com.ar`), because it references the
`blog-front` upstream by container name and `blog-front` is intentionally
stopped — Docker's embedded DNS won't resolve a stopped container, and
nginx refuses to reload the *entire* config file set until every
referenced upstream resolves. This was confirmed by restoring the
untouched original `recreobienestar.conf` and reproducing the identical
`nginx -t` failure.

Unblocks when either: `blog-front` comes back up, or `default.conf` is
changed to resolve upstreams dynamically (a `resolver` directive + variable
in `proxy_pass`) — the second option touches a file outside this project's
scope and needs explicit sign-off first.

## 13. Environment variables

Defined in `backend/.env.example`; the real `.env` exists only on the
server (`/home/ubuntu/recreo-bienestar-backend/.env`, `chmod 600`), never
committed.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django cryptographic signing |
| `DEBUG` | must be `False` in production (confirmed) |
| `ALLOWED_HOSTS` | comma-separated, locked to the real domain |
| `CSRF_TRUSTED_ORIGINS` | comma-separated, `https://` origins |
| `ADMIN_URL` | mount point for Django Admin, default `gestion/` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Postgres connection |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` in production; nginx terminates TLS and forwards `X-Forwarded-Proto` |

## 14. Backup and restore flow

```mermaid
flowchart LR
    PG[(recreo-db)] -->|pg_dump --format=custom| Dump[.dump file]
    Dump -->|pg_restore --clean --if-exists| PG2[(recreo-db\nor a replacement)]
    PG -.->|docker compose stop + tar| Vol[volume-level tarball]
    Vol -.-> PG3[(restored volume)]
```

Logical backup (preferred for routine use):
```bash
docker exec recreo-db pg_dump -U recreo_admin -d recreo_bienestar \
  --format=custom > recreo_bienestar_$(date +%Y%m%d_%H%M%S).dump
```
Restore:
```bash
docker cp recreo_bienestar_YYYYMMDD_HHMMSS.dump recreo-db:/tmp/restore.dump
docker exec recreo-db pg_restore -U recreo_admin -d recreo_bienestar \
  --clean --if-exists /tmp/restore.dump
```
Volume-level (whole data directory, container briefly stopped):
```bash
docker compose stop recreo-db
docker run --rm -v recreo-bienestar-backend_recreo_db_data:/data \
  -v $(pwd):/backup alpine tar czf /backup/recreo_db_volume_$(date +%Y%m%d).tar.gz -C /data .
docker compose start recreo-db
```
Both tested end-to-end during Phase 2 (dump created, contents listed via
`pg_restore --list`, then deleted — no artifacts left behind).

## 15. Known infrastructure constraints

- **t2.micro, 954MB RAM, 0 swap.** Baseline OS/daemon overhead
  (`dockerd`+`containerd`+`fail2ban`+`snapd`+`amazon-ssm-agent`) alone is
  ~240MB. `django-api`/`blog-front` had to be stopped to make room for
  `recreo-django`/`recreo-db` during development — this is why nginx
  routing can't be finished without either bringing them back or fixing
  the `default.conf` coupling (§12).
- **Recommendation**: t3.small (2GB RAM) before running the full original
  container set alongside Recreo Bienestar long-term; a small swap file
  (1–2GB) is also a cheap safety net regardless of instance size. Neither
  has been applied — both are host-level changes outside this phase.
- **Disk**: ~3.8GB free of 11GB at last check — enough for now, worth
  watching as more container images/log volume accumulate.
- **Single point of failure**: one EC2 instance serves this and several
  unrelated client sites; no HA, no staging environment.

## 16. Future phases (not started)

- **Public authentication** — registration/login for real members, session
  or token-based, wired to the existing (untested-in-production)
  `can_access_video` logic.
- **Member dashboard** — "my videos", "my membership status", using the
  read-only API plus new authenticated endpoints.
- **Memberships going live** — actually enforcing `access_level` on video
  playback once auth exists; today `Subscription`/`MembershipPlan` are
  fully modeled and tested but nothing public checks them.
- **Payments** — plan purchase/renewal, likely a provider webhook driving
  `Subscription.status`/`ends_at`. Explicitly out of scope through Phase 2.
- **Webhooks** — inbound (payment provider → subscription state) and
  possibly outbound (e.g. notifying on new video publish). Not designed
  yet.

## 17. Rollback procedures

- **Server containers**: `cd /home/ubuntu/recreo-bienestar-backend &&
  docker compose down -v` — removes `recreo-django`, `recreo-db`, their
  networks and volumes (⚠️ `-v` deletes the database — take a backup first,
  §14). Does not touch `nginx-flask-prod` in any way.
- **nginx**: nothing has been applied yet (§12), so there's nothing to roll
  back. If it's later applied and needs reverting: restore
  `nginx/conf.d/recreobienestar.conf` from `nginx-flask-prod` git history
  (or from the timestamped `.bak-*` file left alongside it) and re-run
  `nginx -t` before reloading.
- **This repo**: `git checkout main` — `feature/django-backend` and the
  checkpoint tag `checkpoint-before-django-backend-20260805` stay available
  for diffing or resuming; nothing has been merged into `main`.
- **Server source**: `rm -rf /home/ubuntu/recreo-bienestar-backend` removes
  the synced source, built image, and `.env` from the host entirely.

## 18. Commands required to resume development

```bash
# Sync latest backend/ to the server (from this repo, local machine)
rsync -az --exclude='.git' --exclude='__pycache__' --exclude='.env' \
  backend/ ubuntu@<ec2-host>:/home/ubuntu/recreo-bienestar-backend/

# Rebuild + redeploy Django only (recreo-db untouched)
cd /home/ubuntu/recreo-bienestar-backend
docker compose up -d --build recreo-django

# Run the test suite (ephemeral, sqlite, no persistent state)
docker run --rm \
  -e SECRET_KEY=test -e DEBUG=False -e ALLOWED_HOSTS=localhost \
  -e CSRF_TRUSTED_ORIGINS=https://localhost \
  -e DB_NAME=x -e DB_USER=x -e DB_PASSWORD=x -e DB_HOST=x -e DB_PORT=5432 \
  -e DJANGO_SETTINGS_MODULE=config.settings_test_sqlite \
  --entrypoint python recreo-bienestar-backend-recreo-django manage.py test

# Create Carla's superuser (interactive — needs her real email/username)
docker exec -it recreo-django python manage.py createsuperuser

# Check container/DB health
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker exec recreo-db pg_isready -U recreo_admin -d recreo_bienestar
```
