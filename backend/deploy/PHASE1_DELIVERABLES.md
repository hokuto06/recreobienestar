# Recreo Bienestar — Phase 1 Deliverables

Django backend + admin for Carla. Branch: `feature/django-backend` (off
checkpoint tag `checkpoint-before-django-backend-20260805` on `main`).
**Nothing has been committed or pushed.** Nothing payment-, registration-,
or public-dashboard-related was built, per scope.

## 1. Architecture summary

The actual Docker/nginx stack lives on the remote EC2 `web` instance
(`i-0e29388fdbea11fd8`, public host `ec2-52-201-37-75.compute-1.amazonaws.com`),
managed by the separate `nginx-flask-prod` git repo on that host — not by
this local `terraform-prod`/`recreo-bienestar` checkout. Terraform in
`terraform-prod` only provisions the AWS layer (EIP, security groups, RDS,
Route53) around it and was not touched.

Recreo Bienestar's backend is a **separate Compose project**
(`recreo-bienestar-backend`, deployed at
`/home/ubuntu/recreo-bienestar-backend` on the server), not a merge into
`nginx-flask-prod/docker-compose.yml`:

```
                         nginx-flask-prod_default (existing network)
                                     │
        ┌────────────────────────────────────────────────┐
        │ nginx-proxy (existing, unmodified)              │
        │  recreobienestar.com → static files (unchanged) │
        │  recreobienestar.com/gestion/ → recreo-django ★ │  ★ config drafted,
        └────────────────────────────────────────────────┘    NOT yet applied
                                     │
                         (joins existing network)
                                     │
                          ┌─────────────────┐
                          │  recreo-django  │  gunicorn, port 8100
                          │  (new)          │  internal only, no host port
                          └─────────────────┘
                                     │
                          recreo_internal (new, private network)
                                     │
                          ┌─────────────────┐
                          │   recreo-db     │  postgres:16-alpine
                          │  (new)          │  internal only, no host port,
                          └─────────────────┘  named volume, no public 5432
```

Existing `django-api`/`blog-front`/RDS Postgres were left completely alone —
Recreo Bienestar has its own dedicated Postgres container rather than
attaching to the shared RDS instance, which is live production infra for
the unrelated blog project and was mid-review for its own cleanup phase.

## 2. Files created

All under `recreo-bienestar/backend/` (new directory, untracked):

```
backend/
├── manage.py, requirements.txt, Dockerfile, entrypoint.sh
├── .dockerignore, .env.example                  (real .env is gitignored, server-only)
├── config/               settings.py, urls.py, wsgi.py, asgi.py,
│                         settings_test_sqlite.py (local dev convenience only)
├── common/               choices.py, text.py (slug + YouTube parsing), validators.py, models.py
├── catalog/              models.py, admin.py, apps.py, migrations/, tests/
│                         (Category, Program, Video)
├── memberships/          models.py, services.py, admin.py, apps.py, migrations/, tests/
│                         (MembershipPlan, Subscription, access-control logic)
└── deploy/
    ├── nginx-gestion.snippet.conf     (location block to insert — not applied)
    ├── recreobienestar.conf.proposed  (full file preview with the change)
    ├── docker-compose.yml             (Django + Postgres, own project)
    └── PHASE1_DELIVERABLES.md         (this file)
```

**Modified:** `.gitignore` (added Django/venv/pycache/`.env` entries).

38 files total, ~1,300 lines of Python.

## 3. Model diagram

```
Category ──┐                          MembershipPlan
           │  FK (PROTECT)             (tier: plan1 | plan2, unique)
           ▼                                   ▲
         Video ◄── FK (SET_NULL, optional) ── Program        Subscription
  access_level:                                              ├─ user (FK)
    free | plan1 | plan2 | all_paid                           ├─ plan (FK, PROTECT)
  is_published, is_featured, display_order                    ├─ status: trial | active |
  youtube_url → youtube_video_id (auto)                        │   past_due | cancelled | expired
                                                                ├─ starts_at, ends_at, cancelled_at
                                                                └─ is_active() / is_expired()

memberships.services.can_access_video(user, video) → bool
  unpublished → False | free → True | all_paid → any active paid plan
  plan1/plan2 → active subscription to that exact tier
```

## 4. Docker changes

- **New**, isolated Compose project at `recreo-bienestar-backend/docker-compose.yml`
  (mirrored locally at `backend/docker-compose.yml`).
- `recreo-db`: `postgres:16-alpine`, named volume `recreo_db_data`, **no
  published port**, only reachable from `recreo-django` on the private
  `recreo_internal` bridge network, healthchecked with `pg_isready`.
- `recreo-django`: builds from `backend/Dockerfile` (python:3.11-slim,
  gunicorn, single worker/2 threads — sized for the box's memory), **no
  published port**, joins both `recreo_internal` and the existing
  `nginx-flask-prod_default` network (external, unmodified) so nginx can
  reach it by container name once wired up.
- `nginx-flask-prod/docker-compose.yml` — **not touched**, not even read/write.
- No Redis, Celery, or other services added.

## 5. Environment variables

See `backend/.env.example`. Required: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `ADMIN_URL`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
`DB_HOST`, `DB_PORT`, `DJANGO_SECURE_SSL_REDIRECT`. A real `.env` (random
`SECRET_KEY` + `DB_PASSWORD`, `DEBUG=False`, `ALLOWED_HOSTS` locked to
`recreobienestar.com`/`www.recreobienestar.com`) exists **only on the
server** at `/home/ubuntu/recreo-bienestar-backend/.env`, `chmod 600`, not
committed anywhere.

## 6. Migration status

`catalog.0001_initial` and `memberships.0001_initial` generated and
applied — confirmed via `showmigrations` inside the running container
(both `[X]`) and a live `SELECT 1` round-trip against `recreo-db`.

## 7. Test results

```
Ran 34 tests in 7.171s
OK
```
Covers every scenario requested: expired membership denies access
(including when `status` field lags reality), active/trial membership
grants access, free video accessible without a plan (incl. anonymous),
unpublished video denied regardless of plan, YouTube ID extraction across
watch/short/embed/shorts URL shapes, invalid YouTube URL rejected,
negative price rejected, plan activate/deactivate (and that deactivating a
plan doesn't retroactively touch existing subscriptions).

Run via a disposable python:3.11-slim container against sqlite
(`config.settings_test_sqlite`) — matches the production Python version,
touches nothing persistent.

## 8. Current live state (server)

- `recreo-django` and `recreo-db` — **up and healthy**, migrations applied.
- `django-api`, `blog-front`, and the rest of the `nginx-flask-prod` stack
  — **intentionally stopped by you** for memory headroom. Not restarted,
  not modified, not rebuilt.
- nginx **not yet reconfigured** — `/gestion/` is not routed anywhere
  publicly yet (see §9).
- Memory: ~66MB free / 954MB total, 0 swap, both new containers combined
  use ~110MB RSS (`recreo-django` ~72MB, `recreo-db` ~42MB at idle).

## 9. Admin URL proposal

**`https://recreobienestar.com/gestion/`**, as you proposed — not `/admin/`.
Configurable via `ADMIN_URL` env var without a code change if you want a
different path later.

## 10. Manual steps remaining

1. **Apply the nginx change** (`backend/deploy/nginx-gestion.snippet.conf`
   into `nginx-flask-prod/nginx/conf.d/recreobienestar.conf`, before the
   existing `location /`), then `nginx -t` inside `nginx-proxy` and reload
   — deliberately **not done**, pending your approval, and blocked anyway
   while the production stack is intentionally down.
2. **Create Carla's superuser** once ready:
   ```
   docker exec -it recreo-django python manage.py createsuperuser
   ```
   (I did not invent her email/username/password — this is interactive.)
3. Decide when to bring `django-api`/`blog-front` back up (your call, per
   your last message).
4. Review and, when ready, `git add`/`commit`/push
   `feature/django-backend` — not done, per your instruction.
5. Populate real `Category`/`Program`/`MembershipPlan` data through the
   admin once it's reachable.

## 11. Rollback procedure

- **Server containers:** `cd /home/ubuntu/recreo-bienestar-backend && docker
  compose down -v` — removes `recreo-django`, `recreo-db`, their networks
  and volumes. Does not touch `nginx-flask-prod` in any way.
- **nginx:** no change was applied, so there is nothing to roll back there
  yet. If §10.1 is later applied and needs reverting, restore
  `nginx/conf.d/recreobienestar.conf` from the `nginx-flask-prod` git
  history (`git checkout -- nginx/conf.d/recreobienestar.conf`) and reload.
- **Local repo:** `git checkout main` (branch `feature/django-backend` and
  tag `checkpoint-before-django-backend-20260805` stay available for later
  resumption or diffing); nothing was merged into `main`.
- **Server files:** `rm -rf /home/ubuntu/recreo-bienestar-backend` removes
  the synced source, image, and env file from the host entirely.
