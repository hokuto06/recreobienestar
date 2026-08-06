# Recreo Bienestar — Phase 2 Deliverables

Extends Phase 1 (`PHASE1_DELIVERABLES.md`) with a read-only public API and
production-deployment prep. Still on branch `feature/django-backend`. No
payments, no public registration, no member dashboard — same boundary as
Phase 1, just admin + read-only API this time.

## 1. What was added

- **Django REST Framework** (`djangorestframework==3.15.2`,
  `django-filter==24.3`), `AllowAny`, JSON-only in production
  (`BrowsableAPIRenderer` only when `DEBUG=True`), no CORS package —
  same-origin only, no wildcard.
- **Endpoints** (all read-only — `ReadOnlyModelViewSet`/`ListAPIView` never
  define write handlers, so POST/PUT/PATCH/DELETE are 405 everywhere,
  confirmed by tests and a live check against the running container):
  - `GET /api/categories/` — active only
  - `GET /api/programs/` — active only
  - `GET /api/videos/` — published only, filterable by `?category=<slug>`,
    `?program=<slug>`, `?access_level=<free|plan1|plan2|all_paid>`, paginated
  - `GET /api/videos/<slug>/` — 404 for unpublished/nonexistent
  - `GET /api/plans/` — active only
- **Field hygiene**: serializers hand-pick fields; `is_active`,
  `is_published`, `created_at`, `updated_at`, raw `youtube_url`, and raw FK
  ids are never serialized (verified by `test_internal_fields_not_exposed`).
- **19 new tests** (53 total now, all passing): unpublished exclusion,
  inactive-plan exclusion, category/program/access_level filtering, detail
  404 for unpublished, pagination shape, and write-method rejection on
  every list/detail endpoint.

## 2. Production readiness

| Check | Result |
|---|---|
| EC2 memory | t2.micro, 954MB total, **~65MB free / ~300MB "available", 0 swap** — see §5 |
| `recreo-django` / `recreo-db` | both running, `recreo-db` healthy, redeployed cleanly (`recreo-db` was never recreated — confirmed `Running` throughout the `recreo-django` rebuild) |
| DB volume persistence | `recreo-bienestar-backend_recreo_db_data`, local named volume, survives container recreate/restart (only removed by explicit `down -v`) |
| `DEBUG` | `False`, confirmed via `docker exec recreo-django env` |
| `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` | locked to `recreobienestar.com`/`www.recreobienestar.com`, no wildcard |
| Required env vars | all 11 present in server-only `.env` (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `ADMIN_URL`, `DB_NAME/USER/PASSWORD/HOST/PORT`, `DJANGO_SECURE_SSL_REDIRECT`) |
| Production stack (`django-api`/`blog-front`) | confirmed still stopped, not touched |

Migrations: DRF added no models, so `makemigrations --check` reports **no
changes** and the deploy's `migrate` step found nothing to apply.

## 3. PostgreSQL backup / restore

Tested live against `recreo-db` (dump created, listed with `pg_restore
--list`, then deleted — no artifacts left behind):

**Backup:**
```bash
docker exec recreo-db pg_dump -U recreo_admin -d recreo_bienestar \
  --format=custom > recreo_bienestar_$(date +%Y%m%d_%H%M%S).dump
```

**Restore** (to a fresh/empty database — do not run against a database with
data you want to keep without reviewing `--clean`/`--if-exists` first):
```bash
docker cp recreo_bienestar_YYYYMMDD_HHMMSS.dump recreo-db:/tmp/restore.dump
docker exec recreo-db pg_restore -U recreo_admin -d recreo_bienestar \
  --clean --if-exists /tmp/restore.dump
```

**Volume-level backup** (whole `recreo-db` data directory, container stopped):
```bash
docker compose stop recreo-db
docker run --rm -v recreo-bienestar-backend_recreo_db_data:/data \
  -v $(pwd):/backup alpine \
  tar czf /backup/recreo_db_volume_$(date +%Y%m%d).tar.gz -C /data .
docker compose start recreo-db
```

## 4. Nginx — NOT applied, blocked by a pre-existing, unrelated issue

**What I found:** `nginx -t` inside `nginx-proxy` fails — but on the
**existing, untouched** `default.conf` (line 154), not on my change:

```
nginx: [emerg] host not found in upstream "blog-front" in /etc/nginx/conf.d/default.conf:154
```

`default.conf` (estebanmartins.com.ar routing — unrelated to Recreo
Bienestar) proxies to `blog-front` by container name. Docker's embedded DNS
only resolves names for **running** containers; since you intentionally
stopped `blog-front`, nginx can't resolve it, and nginx refuses to reload
**the entire config** — not just the recreobienestar.com server block —
until every upstream referenced anywhere resolves.

**I confirmed this is not caused by my change**: I deployed my updated
`recreobienestar.conf` (adding `/gestion/` and `/api/` locations), got this
error, restored the original file from a timestamped backup, and reran
`nginx -t` — **it fails identically with the original, untouched file.**
The diff against the backup after restoring is empty, confirmed.

**What this means:** I did not reload nginx (per your instruction:
validate first, reload only if it passes — it doesn't). Nothing about the
live site changed. `nginx-proxy`'s already-running process is unaffected
(it hasn't been reloaded since long before today); this only blocks
*future* reloads — including mine, and including a plain container
restart, which would currently fail to start nginx at all.

**Your options, none applied without your say-so:**
1. **Bring `blog-front` back up** (even briefly) — resolves it immediately,
   but you told me to keep it stopped for memory headroom.
2. **Let me apply the standard nginx fix for optional upstreams** — using a
   variable + `resolver` directive in `default.conf` so it doesn't hard-fail
   at reload time when `blog-front` is down. This touches a file outside
   Recreo Bienestar's scope (estebanmartins.com.ar routing), which you told
   me not to modify — so I did not do this without asking.
3. **Leave nginx as-is for now** — `/gestion/` and `/api/` stay unreachable
   publicly until one of the above happens. My prepared config
   (`backend/deploy/recreobienestar.conf.proposed`) is ready to apply the
   moment either is resolved.

My snippet itself has no known syntax issues — I just have no way to prove
that with `nginx -t` while this pre-existing coupling blocks the whole
file from validating.

## 5. Memory / EC2 sizing recommendation

Not blocking today's work (no new containers were needed — DRF just added
Python packages to the already-running `recreo-django` process), but worth
flagging plainly: **t2.micro (1GB RAM, 0 swap) is undersized** for this
box's actual job — 5+ containers plus `dockerd`/`containerd`/`fail2ban`/
`snapd`/`amazon-ssm-agent` baseline overhead (~240MB) leaves very little
margin, which is exactly why `django-api`/`blog-front` had to be stopped to
make room for Recreo Bienestar's development. Recommendation: **t3.small
(2GB RAM)** — same burstable family, roughly doubles usable memory, cheap
enough to be a low-risk upgrade — before running the full original stack
(`django-api` + `blog-front` + `flask-prod` + `recreo-django` + `recreo-db`)
simultaneously long-term. Also consider a small swap file (1–2GB) as a
cheap safety net regardless of instance size — not applied here, since
that's a host-level change outside this phase's scope.

## 6. Admin superuser — prepared, not run

```bash
docker exec -it recreo-django python manage.py createsuperuser
```
Confirmed working against the live container (`--help` executed
successfully). Waiting on Carla's real email/username; will not run this
or invent credentials.

## 7. Test results

```
Ran 53 tests in 7.270s
OK
```
Run against the exact image currently deployed as `recreo-django`
(`recreo-bienestar-backend-recreo-django`), not just a throwaway build.
