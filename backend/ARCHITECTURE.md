# Recreo Bienestar — Backend Architecture

Status snapshot as of Phase 2.5 (fully isolated production stack — own
nginx, own networks, sole public frontend on this EC2 for now). Companion
docs: `deploy/PHASE1_DELIVERABLES.md`, `deploy/PHASE2_DELIVERABLES.md`,
`deploy/PHASE2B_DELIVERABLES.md`, `deploy/PHASE2_5_DELIVERABLES.md`. This
file is the living reference; the phase docs are point-in-time delivery
records.

## 1. System overview

Recreo Bienestar is now a **fully self-contained production stack**:
`recreo-nginx` + `recreo-django` + `recreo-db`, with no dependency on
`nginx-flask-prod`'s containers or Docker network. It serves Django Admin
(Carla's content management), a read-only public API, and the full public
member experience (registration, login, dashboard, video library/detail
with real membership-gated access) — all live at
`https://recreobienestar.com`.

```mermaid
flowchart LR
    Visitor[Visitor browser] -->|HTTPS :80/:443| RN[recreo-nginx]
    Carla[Carla] -->|HTTPS /gestion/| RN
    RN -->|"/ (static files, bundled in this stack)"| Static[recreobienestar static site]
    RN -->|"/gestion/ /api/ /registro/ /ingresar/\n/mi-cuenta/ /videoteca/ /videos/..."| Django[recreo-django]
    Django --> DB[(recreo-db\nPostgreSQL 16)]
```

**Phase 2.5 also made this the sole public site on the EC2 instance**, by
explicit decision: `nginx-flask-prod`'s `nginx-proxy` was stopped (not
removed) to free ports 80/443. `jeref.com.ar`, `estebanmartins.com.ar`,
and `silviorodriguez.com.ar` (all sharing this box/IP) are consequently
offline until migrated to their own infrastructure later — a deliberate,
approved tradeoff, not a side effect. See §12 for the reasoning and §18
for rollback.

## 2. Docker architecture

Recreo Bienestar is its own Compose project (`recreo-bienestar-backend`),
with **no network, container, or file dependency on `nginx-flask-prod`
except one deliberate, read-only exception**: the Let's Encrypt
certificate and ACME webroot (§14) — kept because the cert is a live,
renewed credential, not static content that can simply be copied once.

```mermaid
flowchart TB
    subgraph "nginx-flask-prod (stopped, not removed — Phase 2.5)"
        NGINX[nginx-proxy — Exited]
        FLASK[flask-prod — still running, unreachable]
        CERTBOT[certbot — still running, unreachable]
    end
    subgraph "recreo-bienestar-backend (fully isolated stack)"
        RN[recreo-nginx\nowns host ports 80/443]
        DJ[recreo-django\ngunicorn :8100]
        PG[(recreo-db\npostgres:16-alpine)]
    end
    NET1{{recreo_public\nbridge}}
    NET2{{recreo_internal\nbridge}}
    HOSTFS[/host: nginx-flask-prod/letsencrypt\n+ certbot/www — read-only mount/]

    RN --- NET1
    DJ --- NET1
    DJ --- NET2
    PG --- NET2
    RN -.->|ro mount| HOSTFS
```

Only `recreo-nginx` publishes host ports. `recreo-db` is reachable only
from `recreo-django` on `recreo_internal`; `recreo-django` is reachable
only from `recreo-nginx` on `recreo_public`.

## 3. Container and network relationships

| Container | Image | Networks | Published ports | Notes |
|---|---|---|---|---|
| `recreo-nginx` | `nginx:latest` | `recreo_public` | **80, 443** | serves the static site + reverse-proxies Django; see §11 for config specifics |
| `recreo-django` | built from `backend/Dockerfile` (python:3.11-slim) | `recreo_internal`, `recreo_public` | none | gunicorn, 1 worker/2 threads (memory-sized), WhiteNoise serves static/media |
| `recreo-db` | `postgres:16-alpine` | `recreo_internal` only | none | `pg_isready` healthcheck, named volume |
| `nginx-proxy` (nginx-flask-prod) | existing | `nginx-flask-prod_default` | none (stopped) | **stopped, not removed** — freed 80/443 for `recreo-nginx` |
| `flask-prod`, `certbot` (nginx-flask-prod) | existing | `nginx-flask-prod_default` | none | still running but unreachable (no host port, no front door) |
| `django-api`, `blog-front` | existing | — | — | still absent (intentionally stopped since Phase 2), untouched by this phase |

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
│                        validators.py, models.py (shared abstract bases),
│                        forms.py (AccessibleFormMixin)
├── accounts/            Profile + auth backend/forms/views (register,
│                        login, logout, password reset, dashboard,
│                        profile edit) + templates + tests
├── catalog/            Category, Program, Video + admin + serializers +
│                        API views (views.py) + public HTML views
│                        (public_views.py) + filters + templates + tests +
│                        management/commands/seed_demo_data.py
├── memberships/         MembershipPlan, Subscription + access-control
│                        service + admin + serializers + views + tests
├── templates/            base.html, 404.html, 500.html, shared partials
├── static/site/          CSS/JS copied from the static site (unmodified)
│                        + one additive stylesheet
├── deploy/              nginx configs (proposed/snippet), phase docs
├── Dockerfile, entrypoint.sh, docker-compose.yml, requirements.txt
└── manage.py, .env.example
```

`common` exists specifically so `accounts`, `catalog`, and `memberships`
never need to import each other's models to agree on what a
"plan1/plan2/free/all_paid" access level means — all three read the same
`VideoAccessLevel`/`PlanTier` enums, and all three's forms share one
accessibility mixin.

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
    User ||--o| Profile : ""
    Profile {
        string display_name
        string avatar_url
        image avatar
    }
```

All models plus `MembershipPlan`/`Category`/`Program` inherit
`created_at`/`updated_at` from a shared `TimeStampedModel` mixin; the three
catalog-ish ones also share `is_active`/`display_order` via
`OrderedActiveModel` (now indexed — see §17). `Profile.email` is a
*property* reading `user.email`, not a stored column, so the two can never
drift apart; it's auto-created via a `post_save` signal on `User`.

## 7. Access-control logic

**Now wired into every place that shows video content** — Django Admin
(implicitly, staff bypass), the public video library/detail pages, the
member dashboard, and the read-only API. `memberships/services.py`
remains the single decision point; nothing else re-derives these rules:

```mermaid
flowchart TD
    Start["can_access_video(user, video)"] --> Staff{staff or superuser?}
    Staff -->|Yes| Allow[Allow — even unpublished,\nso Carla can preview drafts]
    Staff -->|No| Pub{video.is_published?}
    Pub -->|No| Deny[Deny]
    Pub -->|Yes| Level{access_level?}
    Level -->|free| Allow2[Allow — anyone, incl. anonymous]
    Level -->|all_paid| AnyPlan{ANY subscription active\nAND its plan is_active?}
    Level -->|plan1 / plan2| ThatPlan{subscription to THAT tier\nactive AND plan is_active?}
    AnyPlan -->|Yes| Allow2
    AnyPlan -->|No| Deny
    ThatPlan -->|Yes| Allow2
    ThatPlan -->|No| Deny
```

`Subscription.is_active()` checks `status in {trial, active, cancelled}`
**and** `not is_expired()` — a stale `active` status never overrides a
passed `ends_at`, and a *cancelled* subscription keeps access until
`ends_at` (cancelling stops renewal, not the period already paid for).
`plan.is_active` is checked separately: a deactivated plan grants no
access even to an otherwise-valid subscription.

**Performance**: every function in this module accepts an optional
`subscriptions` list — pass a pre-fetched
`list(user.subscriptions.select_related('plan'))` when checking many
videos in one request (dashboard, library, API list) or each video would
re-query the user's subscriptions individually (see §17).

**Where each surface enforces it:**
- `catalog/public_views.py:video_detail` — checked before any
  YouTube field enters the template context; locked → `video_locked.html`
  (403), which receives no YouTube fields at all.
- `catalog/views.py:VideoViewSet.retrieve` — same check, before the
  serializer runs; locked → HTTP 403, no body fields.
- `catalog/serializers.py:VideoListSerializer.get_thumbnail` — the
  thumbnail is itself sensitive (it embeds the video ID when no explicit
  `thumbnail_url` is set), so it's null for videos the caller can't
  access, even in list views.
- `catalog/templates/catalog/partials/_video_card.html` — locked cards
  render a generic placeholder, never `thumbnail_display_url`.
- `accounts/views.py:dashboard` — stamps `video.unlocked` on every video
  once, server-side; templates read that, never re-derive it.

## 8. Django Admin responsibilities

Carla's entire workflow today. **Publicly reachable at
`https://recreobienestar.com/gestion/`** as of Phase 2.5 (§12). Per model:

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
- **Profile**: read/search only from the admin (`has_add_permission` is
  disabled — profiles are only ever created via the `post_save` signal).

No public-facing admin functionality exists — everything here requires
Django staff auth (see §10).

## 9. REST API endpoints

All under `/api/`, **publicly live at `https://recreobienestar.com/api/`**
as of Phase 2.5 (§12). Every endpoint is **read-only**: list/retrieve
handlers only exist, so POST/PUT/PATCH/DELETE return 405 everywhere — there
is no write path to secure because none was built.

| Endpoint | Returns | Filters |
|---|---|---|
| `GET /api/categories/` | active categories | — |
| `GET /api/programs/` | active programs | — |
| `GET /api/videos/` | published videos, per-viewer thumbnail (null if locked to them) | `?category=<slug>` `?program=<slug>` `?access_level=<level>` |
| `GET /api/videos/<slug>/` | one video if accessible to the caller, **403 (not 404) if locked** | — |
| `GET /api/plans/` | active plans | — |

Paginated (`PageNumberPagination`, 20/page). Serializers hand-pick fields —
`is_active`/`is_published`/timestamps/raw FK ids/raw `youtube_url` are
never exposed, and (since the Phase 2b audit — §17) neither is
`youtube_video_id` or the derived thumbnail for a video the caller can't
access, checked via the same `can_access_video` used everywhere else.
Same-origin only; no CORS package installed (nothing to configure yet —
add an explicit allow-list, never a wildcard, if a separate frontend
origin appears later).

## 10. Current authentication status

- **Django Admin**: standard Django session auth (`django.contrib.auth`),
  staff/superuser only, entirely separate flow from public member login.
  Carla's superuser (`admincarla`) **exists**, created outside this
  codebase as instructed (credentials never handled by Claude).
- **Public members**: full registration/login/logout/password-reset flow
  now exists (`accounts` app). Login accepts username OR email via a
  custom `EmailOrUsernameModelBackend`. Sessions are the only mechanism —
  no tokens, no social login. See §17 for the security properties audited.
- **REST API**: `AllowAny` + `SessionAuthentication` (added in the Phase
  2b audit — see §17 for why `AllowAny` alone was no longer sufficient
  once the API needed to know *who* was asking).

## 11. Current production deployment status

| Component | Status |
|---|---|
| `recreo-nginx` | **live on ports 80/443**, healthy, serving all routes |
| `recreo-django` | running, healthy, all Phase 2b + audit fixes deployed |
| `recreo-db` | running, healthy, all migrations applied, never recreated across any redeploy or cutover in this project's history |
| `https://recreobienestar.com` and `https://www.recreobienestar.com` | **publicly live** — verified from the public internet |
| Django Admin (`/gestion/`), REST API (`/api/...`), public auth + dashboard + video library/detail | **all publicly live**, verified via real HTTPS requests to the domain, not just against the container directly |
| Static site (`/`) | **publicly live**, unchanged content, verified over HTTPS |
| `django-api` / `blog-front` | still absent (intentionally stopped since Phase 2), untouched |
| `nginx-proxy` (nginx-flask-prod) | **stopped** (Phase 2.5 cutover), container/volumes/image intact, not removed |

## 12. Nginx routing — applied (Phase 2.5)

`backend/nginx/conf.d/recreobienestar.conf` is `recreo-nginx`'s own
config — no longer a proposal grafted onto `nginx-flask-prod`'s file.
Serves `/` (static site, bundled read-only into this stack's image via
`nginx/static-root/`), `/gestion/`, `/api/`, the public auth/dashboard/
library paths (one regex location), and `/static/`/`/media/` (with `^~`,
needed so they aren't shadowed by the `\.(css|js|...)$` regex block).

**Two real bugs were found and fixed during pre-cutover testing on
temporary ports 8088/8444** (not just confirmed working — see
`deploy/PHASE2_5_DELIVERABLES.md` for the full debugging trail):
1. Every `proxy_pass` used a variable (`$django_upstream`) to get
   request-time DNS resolution (see the resilience note below) — but
   nginx does NOT do prefix substitution when `proxy_pass` targets a
   variable; it was silently truncating every request to the literal path
   written in the directive (`/static/<file>` → bare `/static/`). Fixed by
   removing the trailing path everywhere, since Django's own URL patterns
   already expect the full original path unchanged.
2. `/static/` and `/media/` were missing `X-Forwarded-Proto`, so Django's
   `SECURE_SSL_REDIRECT` self-redirected every static asset request.

**Built resiliently against the exact failure that blocked Phase 2/2b**:
`resolver 127.0.0.11 valid=10s;` + a variable in `proxy_pass` means nginx
resolves `recreo-django` at *request time*, not at config-load/reload
time. Unlike `nginx-flask-prod/nginx/default.conf` (which still hard-fails
`nginx -t` if `blog-front` is stopped — confirmed still true, unrelated to
this stack), `recreo-nginx` starts and reloads successfully even if
`recreo-django` is briefly down (e.g. mid-deploy) — real requests during
that window get a 502, not a refusal to start at all.

**Cutover (Phase 2.5)**: `nginx-proxy` (nginx-flask-prod) was stopped to
free ports 80/443, by explicit decision — `recreobienestar.com` is now the
sole public site on this EC2 instance; `jeref.com.ar`,
`estebanmartins.com.ar`, and `silviorodriguez.com.ar` (all sharing this
box) are offline until migrated separately. See §18 for rollback.

**Certificate mounting and renewal flow** (inspected in full during
Phase 2.5 — this is not new automation, just documentation of what was
already true):
- The cert is issued into a **custom, non-default certbot config
  directory**: `/home/ubuntu/nginx-flask-prod/letsencrypt` (see
  `renewal/recreobienestar.com.conf`'s `config_dir` line) — NOT the host's
  real `/etc/letsencrypt`, which has no record of this domain at all.
- `recreo-nginx` mounts that exact directory read-only at
  `/etc/letsencrypt`, plus the matching webroot
  (`/home/ubuntu/nginx-flask-prod/certbot/www`) read-only at
  `/var/www/certbot`, so the ACME HTTP-01 challenge continues to be
  servable from the same location renewal already targets.
- **Finding, discovered Phase 2.5, resolved in Stage A (SECURITY_AUDIT.md
  HIGH-2)**: the host's automatic `certbot.timer`/`certbot.service`
  (systemd, twice daily) only renews certs under the *default*
  `/etc/letsencrypt` — it has no knowledge of this custom config-dir cert.
  A dedicated `certbot-recreobienestar.timer`/`.service` pair (installed
  at `/etc/systemd/system/`, source in `deploy/systemd/`) now handles it
  separately: twice daily (03:00/15:00, offset from the stock timer purely
  so the two never contend for certbot's lock file at the same instant),
  targeting the correct `--config-dir` and reloading `recreo-nginx` via
  `--deploy-hook` on success. `--no-random-sleep-on-renew` is set
  deliberately — certbot's own internal jitter for non-interactive runs
  (up to several minutes) is redundant on top of the timer's own
  `RandomizedDelaySec=1800`, and without it a routine renewal check looks
  like a hang. Validated via `certbot renew --dry-run` against this exact
  config before install (all simulated renewals succeeded).
- **The renewal command**, run automatically by the timer above:
  ```bash
  sudo certbot renew --config-dir /home/ubuntu/nginx-flask-prod/letsencrypt \
    --no-random-sleep-on-renew \
    --deploy-hook "docker exec recreo-nginx nginx -s reload"
  ```
- No certs, keys, or renewal config were copied into git at any point —
  the mount is the only mechanism, always read-only.

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

No new variables in Phase 2b. `STATIC_URL`/`MEDIA_URL` moved from
`/gestion/static//media/` to top-level `/static/`/`/media/` (harmless —
nginx was never reloaded with the old paths live). `EMAIL_BACKEND` is
hardcoded to the console backend (not env-configurable) since real email
is explicitly out of scope this phase — see
`deploy/PHASE2B_DELIVERABLES.md` §12 for what production email will need.

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

**Automated daily backups** (Stage A, SECURITY_AUDIT.md HIGH-3):
`deploy/scripts/backup_db.sh` runs the logical `pg_dump` above on a
schedule via `backup-recreobienestar.timer`/`.service` (daily 02:15,
installed at `/etc/systemd/system/`), writing to
`/home/ubuntu/backups/recreo-bienestar/` — deliberately outside
`recreo-bienestar-backend/` so a dump (real user data, password hashes)
can never be swept into an rsync or git operation. Each dump is
self-validated with `pg_restore --list` immediately after creation; a
truncated/corrupt dump is deleted rather than kept. Retention: 14 days,
enforced by the script on every run. Restore procedure is the same
`pg_restore --clean --if-exists` command shown above. The full
dump→restore→verify cycle was validated during Stage A against an
isolated, throwaway `postgres:16-alpine` container (never `recreo-db`
itself) — row counts for `auth_user`, `catalog_video`,
`catalog_category`, and `memberships_membershipplan` matched the live
database exactly before this was trusted as a working backup.

## 15. Known infrastructure constraints

- **t2.micro, 954MB RAM, 0 swap.** Baseline OS/daemon overhead
  (`dockerd`+`containerd`+`fail2ban`+`snapd`+`amazon-ssm-agent`) alone is
  ~240MB.
- **Recommendation**: t3.small (2GB RAM) before bringing `jeref.com.ar`,
  `estebanmartins.com.ar`, or `silviorodriguez.com.ar` back online
  alongside Recreo Bienestar; a small swap file (1–2GB) is also a cheap
  safety net regardless of instance size. Neither has been applied — both
  are host-level changes outside this phase.
- **Disk**: ~3.7GB free of 11GB at last check — enough for now, worth
  watching as more container images/log volume accumulate.
- **Single point of failure, now more so**: as of Phase 2.5,
  `recreobienestar.com` is the *only* public site this EC2 instance
  serves — `jeref.com.ar`, `estebanmartins.com.ar`, and
  `silviorodriguez.com.ar` are offline (§12), by explicit decision,
  until migrated to their own infrastructure. No HA, no staging
  environment for any of them.
- **Cert renewal gap (§12) — resolved in Stage A**: `recreobienestar.com`'s
  certificate now renews automatically via `certbot-recreobienestar.timer`.
  Expires 2026-11-03 if renewal ever silently stops working; the timer's
  own status (`systemctl list-timers certbot-recreobienestar.timer`) is
  the place to check first.

## 16. Future phases (not started)

- **Memberships going fully live** — Phase 2b wired real access control
  into every surface, but nothing yet lets a member *acquire* a paid
  subscription — that's §"Payments" below. `Subscription`/`MembershipPlan`
  are fully modeled, tested, and enforced; they just have no public
  purchase path.
- **Payments** — plan purchase/renewal, likely a provider webhook driving
  `Subscription.status`/`ends_at`. Explicitly out of scope through Phase 2b.
  Mercado Pago named explicitly as still not implemented.
- **Webhooks** — inbound (payment provider → subscription state) and
  possibly outbound (e.g. notifying on new video publish). Not designed
  yet.
- **Production email** — see `deploy/PHASE2B_DELIVERABLES.md` §12 for the
  SES/SMTP env vars this will need.

## 17. Security audit (Phase 2b)

Performed before committing, per explicit request, covering security,
membership rules, UX, database, performance, and accessibility. Found and
fixed four real issues rather than confirming everything was already
fine — full detail in `deploy/PHASE2B_DELIVERABLES.md` §"Audit findings";
summarized here since they changed the architecture:

1. **API authorization bypass** (most significant finding): the DRF API
   was built in Phase 2, before per-video membership access control
   existed as a public concept. `VideoDetailSerializer` exposed
   `youtube_video_id` for *any* published video regardless of
   `access_level`, and `VideoListSerializer`'s thumbnail did the same
   implicitly (the thumbnail fallback derives a URL from the video ID).
   `_video_card.html` had the identical bug for locked cards in the HTML
   library/dashboard. Fixed by wiring `can_access_video` into the API
   (`VideoViewSet.retrieve` returns 403 pre-serialization;
   `SessionAuthentication` added so the API can tell who's actually
   asking) and into the card partial. See §7 and §9.
2. **`User.email` had no database-level unique constraint** — only an
   application-level check, a real TOCTOU race. Fixed with a raw-SQL
   migration adding a case-insensitive partial unique index directly on
   `auth_user`, without swapping `AUTH_USER_MODEL`.
3. **N+1 queries**: `can_access_video` re-queried the user's subscriptions
   on every call; checking N videos (dashboard, library, API list) meant
   N extra queries. Fixed by threading an optional pre-fetched
   `subscriptions` list through the service layer — same function, same
   rules, batched. Verified with query-count regression tests (not just
   asserted) on all three surfaces.
4. **Login dropped `?next=`**: an overridden `get_success_url()` always
   redirected to the dashboard, silently breaking "take me back to what I
   was doing" after being bounced to login from a protected page. Removed
   the override — Django's own default already does this correctly.

Also added: missing indexes on `is_active`/`is_published` (the columns
nearly every query filters on), branded `404.html`/`500.html` (Django's
bare-bones English defaults were live in production under `DEBUG=False`),
and `aria-describedby`/`aria-invalid` wiring on this app's own forms via a
shared `AccessibleFormMixin` + `_form_field.html` partial (a mismatched-id
bug in the first attempt was itself caught by the test written for it).

## 18. Rollback procedures

**Cutover rollback (Phase 2.5 — recreo-nginx on 80/443)**:
```bash
sudo docker stop recreo-nginx
sudo docker start nginx-proxy
curl -s -o /dev/null -w '%{http_code}\n' https://recreobienestar.com/   # confirm restored
```
Nothing destructive either way: `nginx-proxy` was `docker stop`'d, never
`rm`'d — container, volumes, and image are all intact and this brings it
back exactly as it was. `recreo-nginx` can then be torn down
(`docker compose down`, no `-v`) with zero data loss, or just left stopped
alongside it.

- **Server containers (full stack)**: `cd /home/ubuntu/recreo-bienestar-backend &&
  docker compose down -v` — removes `recreo-nginx`, `recreo-django`,
  `recreo-db`, their networks and volumes (⚠️ `-v` deletes the database —
  take a backup first, §14). Does not touch `nginx-flask-prod` in any way.
- **Migration rollback**: `docker exec recreo-django python manage.py
  migrate accounts 0001` reverts the email unique index (and, further,
  `migrate accounts zero` removes Profile entirely — no other app depends
  on it). `migrate catalog 0001` / `migrate memberships 0001` drop the new
  indexes only, no data loss either way.
- **nginx config only**: `recreo-nginx`'s config lives entirely in this
  repo now (`nginx/conf.d/`, `nginx/snippets/`) — revert via normal
  `git checkout` of this repo, then `docker compose restart recreo-nginx`.
  `nginx-flask-prod`'s own files were never touched by Phase 2.5.
- **This repo**: `git checkout main` — `feature/django-backend` and the
  checkpoint tag `checkpoint-before-django-backend-20260805` stay available
  for diffing or resuming; nothing has been merged into `main`.
- **Server source**: `rm -rf /home/ubuntu/recreo-bienestar-backend` removes
  the synced source, built image, and `.env` from the host entirely.

## 19. Commands required to resume development

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

# Redeploy recreo-nginx after an nginx/ config change (validate first!)
docker compose exec recreo-nginx nginx -t
docker compose up -d recreo-nginx        # or: docker exec recreo-nginx nginx -s reload

# Test nginx changes on temp ports before touching live 80/443
docker compose -f docker-compose.yml -f docker-compose.tmpports.yml up -d recreo-nginx

# Renew the certificate manually (see §12 — normally handled automatically
# by certbot-recreobienestar.timer; only needed to force a check now)
sudo certbot renew --config-dir /home/ubuntu/nginx-flask-prod/letsencrypt \
  --no-random-sleep-on-renew \
  --deploy-hook "docker exec recreo-nginx nginx -s reload"
```
