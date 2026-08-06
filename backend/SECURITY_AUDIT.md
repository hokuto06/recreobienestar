# Recreo Bienestar — Production Security Audit

**Date**: 2026-08-06
**Scope**: `recreo-nginx` + `recreo-django` + `recreo-db` production stack at `https://recreobienestar.com`
**Method**: Read-only. No files, containers, users, passwords, firewall rules, DNS, certificates, or database data were modified during this audit. All findings are backed by live evidence (commands and their output are described inline; raw secrets are masked).
**Status**: Audit complete. **No remediation has been implemented.** Everything below is a finding and a proposal, pending your approval.

---

## Summary

| Severity | Count (at audit time) | Open after Stage A |
|---|---|---|
| Critical | 0 | 0 |
| High | 2 | **0** — all 3 HIGH findings (HIGH-2/3 pre-existed the audit, HIGH-1 was live-confirmed here) resolved, see "Findings — full list by severity" below |
| Medium | 7 | 4 (MEDIUM-2/3/4 resolved as one fix; MEDIUM-1/5/6/7 unchanged, out of Stage A's scope) |
| Low | 6 | 6 (unchanged, out of Stage A's scope) |

No critical (immediately exploitable, high-impact) issues were found. The application-layer access-control logic (the part most likely to cause real harm if wrong — who can see which video) is solid and was verified live, not just by code review. The findings below are concentrated in **operational hardening** (rate limiting, backups, log rotation, container privilege) and **one concrete configuration bug** (an nginx header-inheritance mistake) that also turns out to be the direct cause of the YouTube embed issue you asked about.

**Stage A (see `deploy/` and this file's "Findings" section below for detail) resolved every HIGH finding and the MEDIUM-2/3/4 header bug.** MEDIUM-1/5/6/7 and all LOW findings remain open — none were in Stage A's explicit scope.

---

## 1. Public exposure

**Confirmed safe:**
- `ss -tlnp` on the host shows only three listening ports: `22` (SSH), `80`, `443` (both via `docker-proxy` for `recreo-nginx`). Postgres (`5432`) and Django/gunicorn (`8100`) are **not bound to the host at all** — not reachable from outside the Docker network under any circumstance, confirmed at the kernel socket level, not just by container config.
- `docker network inspect` on both `recreo_public` and `recreo_internal`: `recreo-db` (172.19.0.2) exists **only** on `recreo_internal`; it has no path to `recreo-nginx` or the internet. `recreo-django` bridges both networks (correct — it needs to talk to both `recreo-nginx` and `recreo-db`).
- Directory listing is disabled (`GET /css/`, `/js/` → `403 Forbidden`, nginx `autoindex` is off, which is the default — never explicitly enabled).
- Requested 19 sensitive-looking paths (`.env`, `.git/config`, `.git/HEAD`, `docker-compose.yml`, `backend/.env`, `db.sqlite3`, `backup.sql`, `wp-config.php.bak`, etc.) — all returned `200`, which looked alarming at first. **Verified this is not a real exposure**: the response body is byte-for-byte identical (matching SHA-256) to `index.html` in every case. This is the static site's `try_files $uri $uri/ /index.html;` SPA fallback serving the landing page for any unmatched path, not real file disclosure. Confirmed on disk (`nginx/static-root/`) that only the intended 8 static files exist — no `.git`, `.env`, or Python source anywhere under the nginx mount.

**Findings:**
- **[LOW-1]** The SPA fallback returns `200` (not `404`) for arbitrary non-existent paths, including sensitive-looking filenames. No data is exposed, but it's poor hygiene: automated scanners will flag false positives, and it silently masks the fact that these paths don't really exist. *File: `backend/nginx/conf.d/recreobienestar.conf`, `location /` block.*
- **[LOW-2]** `robots.txt` still disallows the old static demo pages (`login.html`, `miembros.html`, no longer the real auth pages) and does not disallow `/gestion/` or `/mi-cuenta/`. Not a real vulnerability — Django's admin page already ships its own `<meta name="robots" content="NONE,NOARCHIVE">` (verified live), which is the operative protection — but `robots.txt` is stale and should be updated for defense-in-depth and to stop pointing at pages that no longer exist. *File: `robots.txt` (repo root).*

---

## 2. Django production security

```
$ docker exec recreo-django python manage.py check --deploy
System check identified no issues (0 silenced).
```

All settings verified directly against the live container's runtime config (not just the source file):

| Setting | Value | Assessment |
|---|---|---|
| `DEBUG` | `False` | ✅ |
| `SECRET_KEY` | set, 50 chars, never in git (verified against full history, see §8) | ✅ |
| `ALLOWED_HOSTS` | `['recreobienestar.com', 'www.recreobienestar.com']` | ✅ no wildcard |
| `CSRF_TRUSTED_ORIGINS` | `['https://recreobienestar.com', 'https://www.recreobienestar.com']` | ✅ |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | `True` / `True` | ✅ |
| `SESSION_COOKIE_HTTPONLY` / `CSRF_COOKIE_HTTPONLY` | `True` / `True` | ✅ |
| `SESSION_COOKIE_SAMESITE` / `CSRF_COOKIE_SAMESITE` | `Lax` / `Lax` | ✅ reasonable default |
| `SECURE_PROXY_SSL_HEADER` | `('HTTP_X_FORWARDED_PROTO', 'https')` | ✅ matches nginx's header |
| `SECURE_SSL_REDIRECT` | `True` | ✅ (verified: `http://` → 301 → `https://`) |
| `SECURE_HSTS_SECONDS` / `INCLUDE_SUBDOMAINS` / `PRELOAD` | `31536000` / `True` / `True` | ✅ full year, preload-eligible |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | ✅ |
| `X_FRAME_OPTIONS` | `DENY` | ✅ |
| `SESSION_COOKIE_AGE` | `1209600` (14 days) | ⚠️ see [LOW-3] |
| `PASSWORD_RESET_TIMEOUT` | `259200` (72h) | ✅ Django default, reasonable |
| `AUTH_PASSWORD_VALIDATORS` | Similarity, MinLength(8), CommonPassword, AllNumeric | ⚠️ see [LOW-4] |

**Findings:**
- **[LOW-3]** `SESSION_COOKIE_AGE` (14 days, Django's default, never overridden) applies identically to member sessions and to Carla's staff/admin session at `/gestion/` — there's no shorter session lifetime for the higher-privilege admin path. Worth considering a separate, shorter timeout for admin sessions.
- **[LOW-4]** `MinimumLengthValidator` uses Django's default of 8 characters. Reasonable, but for a paid-membership site, 10–12 is a common stronger baseline. Low priority given the other three validators (similarity, common-password, all-numeric) already meaningfully raise the bar.

---

## 3. Authentication and authorization — live-tested

**Methodology note**: per your instruction not to modify database data, all tests below reused the **existing** demo accounts already seeded in a prior phase (`demo_free`, `demo_activo`, `demo_vencido` — none staff) rather than creating new users. Inactive-user rejection (there is no existing inactive account to test against) was verified by code review instead of live testing — see below.

| Test | Result |
|---|---|
| Unauthenticated `GET /mi-cuenta/` | `302 → /ingresar/?next=/mi-cuenta/` ✅ |
| Unauthenticated `GET /mi-cuenta/perfil/` | `302 → /ingresar/?next=/mi-cuenta/perfil/` ✅ |
| Unauthenticated `GET` unpublished video | `403` ✅ |
| `demo_free` (no subscription) → paid video | `403`, response body confirmed to contain no `youtube` reference ✅ |
| `demo_free` → `/gestion/` (member session, not staff) | `302 → /gestion/login/?next=/gestion/` (Admin's own login gate, not granted) ✅ |
| `demo_free`'s **real, correct credentials** submitted to the **admin login form itself** | `200` (form re-rendered with a permission error, not a redirect into `/gestion/`) — confirms `is_staff` is enforced even with a valid password ✅ |
| `demo_activo` (active plan1) → paid video | `200` ✅ |
| `demo_vencido` (plan1, `ends_at` in the past) → paid video | `403` — expired membership loses access immediately, live-confirmed ✅ |
| Password reset: known email vs. a nonexistent email | Identical `302 → /recuperar-clave/enviado/` for both — **no account enumeration** ✅ |
| Horizontal privilege check | No user ID appears in any private-data URL (`/mi-cuenta/`, `/mi-cuenta/perfil/` are always scoped to `request.user` server-side — verified in `accounts/views.py`); dashboard only ever showed the logged-in account's own data ✅ |
| Inactive user (`is_active=False`) login | **Not live-tested** (would require creating a test account). Verified by code review: `accounts/backends.py`'s `EmailOrUsernameModelBackend` calls `self.user_can_authenticate(user)`, inherited from Django's `ModelBackend`, which checks `user.is_active` before allowing authentication — this is Django's standard, well-tested mechanism. |
| 6 rapid wrong-password `POST /ingresar/` attempts | **All 6 returned `200`** (form re-render), no `429`, no lockout, no increasing delay | ❌ see [HIGH-1] |

**Findings:**
- **[HIGH-1] No brute-force protection or rate limiting on any authentication endpoint.** Confirmed live: 6 consecutive wrong-password submissions to `/ingresar/` all succeeded in reaching the form-validation stage with zero throttling. This applies equally to `/gestion/login/` (Carla's admin login — the highest-value target on the site) and `/recuperar-clave/` (could be used to spam password-reset emails at a target address, or as a request-volume amplifier). No `django-axes`/`django-ratelimit`-style package is installed, and nginx has no `limit_req_zone`/`limit_req` configured anywhere. *Files: `backend/config/settings.py` (no throttling package), `backend/nginx/conf.d/recreobienestar.conf` (no `limit_req`).*

---

## 4. Django Admin

- **Staff requirement**: confirmed live — a non-staff user with correct credentials is rejected at the admin login form itself (see §3 table). Django's `AdminSite` permission checks (`is_staff`) are unmodified/default.
- **Not indexed**: `GET /gestion/login/` returns `<meta name="robots" content="NONE,NOARCHIVE">` — Django's built-in admin behavior, confirmed present.
- **nginx rate limiting**: none exists (see [HIGH-1] above — `/gestion/` is covered by the same gap).
- **Sensitive field exposure**: reviewed `accounts/admin.py`, `catalog/admin.py`, `memberships/admin.py`. No payment/financial data exists yet (out of scope for this phase). Django's built-in `UserAdmin` (unmodified) never renders a raw password, only a "change password" link. Nothing flagged.
- **Admin actions creating invalid access states**: reviewed all bulk actions (`publish`/`unpublish`/`mark_free` on Video; `activate`/`deactivate` on MembershipPlan; `mark_cancelled`/`mark_active` on Subscription). All use `QuerySet.update()`, which bypasses `full_clean()` — standard, low-risk Django pattern for simple field toggles; none of these actions can produce a state `can_access_video()` would misinterpret. One UX (not security) observation: `mark_active` on an already-expired `Subscription` sets `status='active'` but does not extend `ends_at` — the access-control logic correctly still denies access (expiry is checked independently of status), and the action's own success message already tells Carla to check `ends_at` if she meant to extend it. No finding here beyond what's already self-documented in the UI.

---

## 5. API

Enumerated via `config/api_urls.py` and live-tested every method against every endpoint:

| Endpoint | GET | POST/PUT/PATCH/DELETE |
|---|---|---|
| `/api/categories/` | 200 | 405 (all four) |
| `/api/programs/` | 200 | 405 (all four) |
| `/api/plans/` | 200 | 405 (all four) |
| `/api/videos/` | 200 | 405 (all four) |
| `/api/videos/<slug>/` | 200 (or 403 if locked to caller) | 405 (all four) |

- **Read-only guarantee holds in production**, not just in the test suite — 20/20 method checks returned the expected code.
- **Unpublished/inactive exclusion**: confirmed via serializer inspection — querysets filter `is_published=True`/`is_active=True` before serialization; `access_level`-gated content additionally goes through `can_access_video()` (added in the Phase 2b audit) before `youtube_video_id` or a derived thumbnail is ever included.
- **Serializer field exposure**: live-pulled `/api/videos/bienvenida-gratuita/` — exposes exactly the intended fields (`id, title, slug, short_description, thumbnail, category{id,name,slug}, program{id,name,slug}, access_level, is_featured, display_order, duration_label, publication_date, full_description, youtube_video_id`). No `is_active`, `is_published`, `created_at`, `updated_at`, or raw `youtube_url`.
- **Authentication/permission classes**: `DEFAULT_PERMISSION_CLASSES = ['AllowAny']`, `DEFAULT_AUTHENTICATION_CLASSES = ['SessionAuthentication']` — explicit, intentional (this API is meant to be public-read; `SessionAuthentication` exists so `can_access_video` can identify a logged-in caller, not to gate the API itself).
- **Pagination**: `PageNumberPagination`, `PAGE_SIZE=20`, `page_size_query_param` not set — confirmed live that `?page_size=9999` is silently ignored, not an abuse vector.

**Findings:**
- **[MEDIUM-1] No API throttling.** `DEFAULT_THROTTLE_CLASSES` is absent from `REST_FRAMEWORK` settings entirely. Confirmed live: 20 rapid, unauthenticated requests to `/api/videos/` all returned `200` with no degradation or rejection. On a t2.micro with ~950MB RAM and no swap (§9), sustained scraping or a trivial script-kiddie flood is a real resource-exhaustion/availability risk, not just a theoretical one. *File: `backend/config/settings.py`, `REST_FRAMEWORK` dict.*

---

## 6. Nginx and TLS

**TLS** (via `nmap --script ssl-enum-ciphers` and direct `openssl s_client` against the public endpoint):
- Only TLS 1.2 and TLS 1.3 offered. TLS 1.0 and 1.1 explicitly tested and rejected (`no protocols available`).
- All offered ciphers rated **A** grade (`ECDHE`+`GCM`/`ChaCha20-Poly1305` — no weak/legacy ciphers, no compression/CRIME exposure).
- Certificate chain verifies (`Verify return code: 0 (ok)`), issued by Let's Encrypt, `CN=recreobienestar.com`, SAN covers both apex and `www`.

**A real configuration bug found — headers, not just cosmetic:**

nginx's `add_header` directive does **not** inherit from a parent context into any `location` block that defines its own `add_header`. `recreobienestar.conf`'s server block does `include snippets/seguridad.conf;` (setting `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`, HSTS) — but several `location` blocks *also* set their own `add_header Cache-Control ...`, which silently discards the inherited set for that location. Verified precisely, per route:

| Route | Result |
|---|---|
| `/` (no competing `add_header`) | All 6 nginx headers present, no duplicates. ✅ |
| `/api/...` (no competing `add_header`) | nginx's headers **and** Django's own equivalents both present — **duplicated, with different values** (e.g. two `Strict-Transport-Security` lines, one with `preload` one without; two `Referrer-Policy` lines with different policies). Per RFC 6797 a UA must use the *first* HSTS header, so this happens to still work for HSTS, but it's fragile and not guaranteed across all header types/clients. |
| `/gestion/`, `/registro/`, `/ingresar/`, `/mi-cuenta/`, `/videoteca/`, `/videos/...`, `/static/`, `/media/` (all have their own `add_header Cache-Control`) | **`Permissions-Policy` and `X-XSS-Protection` are completely absent.** Django doesn't set equivalents for either. |

- **[MEDIUM-2]** Missing `Permissions-Policy`/`X-XSS-Protection` on the majority of the site's routes, including the admin and every auth form. *File: `backend/nginx/conf.d/recreobienestar.conf`.*
- **[MEDIUM-3]** Duplicate/conflicting `X-Frame-Options`, `Referrer-Policy`, and `Strict-Transport-Security` headers on `/api/...`. *Same file.*
- **[MEDIUM-4] This same bug is the direct root cause of the YouTube embed issue — see the dedicated §"YouTube error 153" below.**
- **[MEDIUM-5] No Content-Security-Policy header anywhere on the site** (confirmed on every route tested). For a site rendering member-submitted-adjacent content and a third-party iframe (YouTube), a CSP — even a modest one — is a meaningful additional layer that's currently entirely absent.

**Proxy headers**: `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` are correctly set on every Django-proxying location *except* they were briefly missing on `/static/`/`/media/` in a prior phase (already fixed then; re-confirmed present now).

**Request limits**: `client_max_body_size 20M;` is enforced — live-tested with a 25MB upload to `/registro/`, got `413 Request Entity Too Large` as expected. No `proxy_connect_timeout`/`proxy_read_timeout`/`proxy_send_timeout` are explicitly set anywhere, so nginx's defaults apply (60s each) — reasonable, but not hardened against slow-connection resource-holding attacks.
- **[LOW-5]** No explicit proxy timeouts configured (relying on nginx's 60s defaults). Minor hardening opportunity, not an active issue.

**Logs**: grepped `recreobienestar.access.log` for password/token/session-id patterns in URLs — none found (expected: credentials are POSTed in the request body, never logged by nginx's access log, which only records the request line).

**Certificate renewal** (carried over from the Phase 2.5 audit, re-verified unchanged): the cert lives in a **custom certbot config directory** (`nginx-flask-prod/letsencrypt`), not the host's default `/etc/letsencrypt` — the host's automatic `certbot.timer` (systemd, twice daily) has no knowledge of it.
- **[HIGH-2] No automatic renewal exists for the production certificate.** Pre-existing, not introduced by this or the prior phase, but now more consequential since this is the box's only public site. **Expires 2026-11-03.** The exact safe manual renewal command is already documented in `ARCHITECTURE.md` §12.

---

## 7. YouTube embed error 153 — dedicated diagnosis

### What was inspected
1. **The generated iframe** (`backend/static/site/js/site.js`, `loadVideo()`): builds `<iframe src="https://www.youtube-nocookie.com/embed/<id>?rel=0&modestbranding=1&playsinline=1" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen>`. **No `referrerpolicy` attribute is set on the iframe itself.**
2. **The page's `Referrer-Policy` response header**, live-fetched on the actual video detail page (`GET /videos/bienvenida-gratuita/`):
   ```
   Referrer-Policy: same-origin
   ```
   This is **not** the site-wide policy intended in `nginx/snippets/seguridad.conf` (`strict-origin-when-cross-origin`) — it's Django's own default (`SecurityMiddleware`'s `SECURE_REFERRER_POLICY`, which defaults to `'same-origin'` and was never overridden in `settings.py`). It's reaching the browser at all only because of the §6 `add_header` bug: this route has its own `add_header Cache-Control`, which drops nginx's intended policy and lets Django's default show through unmasked.
3. **CSP `frame-src`**: no CSP header exists at all (confirmed §6) — CSP is **not** blocking or restricting the embed in either direction. Ruled out as a contributing cause.
4. **Direct test against YouTube itself**, independent of our site:
   ```
   $ curl 'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&format=json'
   ```
   YouTube's own oEmbed API returns its **officially recommended embed markup**, which explicitly includes:
   ```html
   referrerpolicy="strict-origin-when-cross-origin"
   ```
   This was confirmed for **both** demo video IDs currently in the catalog (`dQw4w9WgXcQ`, `jNQXAC9IVRw`) — both embed fine per YouTube's own API (i.e., neither video has embedding disabled by its owner; that's not the cause).

### Diagnosis

Under `Referrer-Policy: same-origin`, a browser sends **zero** referrer information on any cross-origin request — including the iframe's own load of `youtube-nocookie.com`. Because `site.js`'s dynamically-created iframe also never sets its own `referrerpolicy` attribute, it inherits the page's (wrong) policy with nothing to override it.

YouTube's player uses the request's origin/referrer information as part of validating the embedding context. Sending **none at all** — rather than the origin-only referrer `strict-origin-when-cross-origin` would produce — is a well-documented, common cause of YouTube embed failures surfaced as error 153, independent of whether the video owner actually restricted embedding (confirmed above that neither test video does).

**Two compounding causes, one fix location:**
1. The `add_header` bug (§6) means the video detail page doesn't get the site's intended `strict-origin-when-cross-origin` policy.
2. The iframe itself has no `referrerpolicy` attribute of its own to compensate, unlike YouTube's own recommended markup.

### Minimal safe correction (proposed, not applied)

Add the single attribute YouTube's own oEmbed API recommends, directly on the dynamically-created iframe in `site.js`:

```js
iframe.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
```

This is the **narrowest possible fix**: an iframe's own `referrerpolicy` attribute takes precedence over the page's `Referrer-Policy` header for that iframe's own requests, so this resolves the YouTube issue **regardless of whether the broader §6 nginx bug is also fixed**, and touches nothing else — no nginx change, no Django setting change, no change to any other page's behavior, no weakening of any existing control (`strict-origin-when-cross-origin` is *more* private than sending no referrer only in the sense that "none" isn't a privacy-protective choice YouTube is asking for — it's simply what YouTube's own player expects to function correctly; the origin-only policy still never leaks the specific page path to YouTube).

Fixing the underlying §6 `add_header` bug (recommended anyway, restores `Permissions-Policy`/`X-XSS-Protection` sitewide) would make this consistent site-wide as defense-in-depth, but is **not required** to resolve error 153 — the iframe-level attribute alone is sufficient and is the recommended first fix given the "minimal" and "do not weaken unrelated controls" instructions.

---

## 8. Secrets and repository hygiene

- **Tracked files**: `git grep` for secret-shaped patterns (`SECRET_KEY=`, `PASSWORD=`, AWS access key pattern, PEM private key headers) across the working tree — clean, aside from the intentionally-public, clearly-documented dev-only seed password in `catalog/management/commands/seed_demo_data.py` (already flagged as such in its own source comments; not a real secret, never used in production, printed to the console on purpose so it's easy to find and reset).
- **Full git history** (`git log --all -p`, every commit, every branch): scanned for the same patterns — clean. The one match was a docker-compose variable reference (`POSTGRES_PASSWORD=${DB_PASSWORD}`), not a literal secret.
- **`.env` never committed**: `git log --all -- backend/.env` returns nothing, at any point in history.
- **Certs/keys never committed**: `git log --all --diff-filter=A --name-only` grepped for `.pem`/`.key`/`privkey`/`fullchain` — nothing.
- **`.gitignore` coverage**: confirmed `backend/.env`, `backend/media/`, `backend/staticfiles/`, `backend/nginx/logs/*`, `backend/nginx/static-root/*` (deploy-time-populated, not double-tracked) are all correctly ignored.
- **Deployment docs** (`ARCHITECTURE.md`, `deploy/*.md`): grepped for password/secret-shaped strings — only placeholders (`changeme`) and the same documented dev seed password. No real credentials anywhere in prose.

No findings in this category.

---

## 9. Database and operational safety

- **Persistence**: `docker volume ls` confirms `recreo-bienestar-backend_recreo_db_data` and `..._recreo_media` exist as named volumes, independent of container lifecycle (survive `up`/`down`/rebuild; only removed by explicit `down -v`).
- **Container hardening**: none of the 3 containers run `--privileged`, none have added Linux capabilities (`CapAdd: []` on all three).
- **Process ownership** (`docker top`, the *actual* running process, not just the exec shell):
  - `recreo-nginx`: master process is root (required, to bind ports 80/443), but the **worker process — which handles every request — correctly runs as a non-root user**. ✅ correct, standard pattern.
  - `recreo-db`: **all** postgres processes run as the postgres image's own non-root `uid 70`. ✅ correct.
  - `recreo-django`: **both the gunicorn master and worker processes run as root.** ❌ see [MEDIUM-6].
- **Restart policies**: all three containers set to `unless-stopped`. ✅
- **Healthchecks**: `recreo-nginx` and `recreo-db` both report `healthy`. **`recreo-django` has no healthcheck defined at all.**
- **Memory** (report-only, no changes made): `free -m` → 76MB free / 954MB total / 290MB available / **0 swap**. `docker stats`: `recreo-nginx` 8.9MB, `recreo-django` 94MB, `recreo-db` 37MB — combined stack footprint is modest (~140MB); the tight number is the box's baseline OS/daemon overhead, already documented in `ARCHITECTURE.md` §15, unchanged by this audit.
- **Disk**: 3.8GB free of 11GB (65% used) — unchanged from prior phases, worth continued monitoring given the log-rotation gap below.

**Findings:**
- **[MEDIUM-6] `recreo-django`'s application process runs as root inside its container.** No `USER` directive in `backend/Dockerfile`. Contrast with `recreo-nginx` (worker drops privilege) and `recreo-db` (fully non-root) in the same stack. If the Django process or any dependency were ever compromised, the attacker has root inside that container rather than a constrained user — meaningfully larger blast radius for privilege escalation or container-escape attempts. *File: `backend/Dockerfile`.*
- **[MEDIUM-7] Postgres application role (`recreo_admin`) is a full superuser** (`rolsuper=t, rolcreaterole=t, rolcreatedb=t`) — this is simply what the official `postgres` image does with `POSTGRES_USER` by default, never narrowed afterward. Django only needs DML on its own schema plus `CREATE TABLE`/`ALTER TABLE` for migrations — not the ability to create/drop other roles or databases or bypass row-level security. Blast radius is somewhat contained today (this is a single-purpose container with one database), but it's a clear least-privilege gap that matters more once real payment-adjacent data exists. *Scope: `recreo-db` role configuration, no file — would need a `docker exec` role change plus a Django `DB_USER` swap.*
- **[LOW-6] No log rotation anywhere in the stack.**
  - Docker's default `json-file` log driver has **no size cap** — confirmed no `/etc/docker/daemon.json` exists, so `docker logs recreo-django`/`recreo-db` output grows unbounded, limited only by disk.
  - `recreo-nginx`'s access/error logs are bind-mounted to the host (`nginx/logs/`, currently 412KB) with **no host-level `logrotate` entry** — will also grow unbounded.
  - Postgres's own `logging_collector` is `off` (`log_destination = stderr`, captured by Docker's own unrotated driver above — same underlying gap, not a second one).
  - Given disk is already at 65% used, this is a real, if slow-moving, availability risk. *No single file — needs a Docker daemon `log-opts` change and/or a host `logrotate.d` entry.*
- **[HIGH-3, re-classified up from the general operational note] No automated database backup exists.** `crontab -l` (both `ubuntu` and `root`) and `systemctl list-timers` show no backup job of any kind — only the manually-documented `pg_dump`/`pg_restore` commands in `ARCHITECTURE.md` §14, which have been tested to work but have never been run on a schedule. This is a production system holding real member accounts today, and will hold subscription/payment-adjacent data soon. An unrecoverable volume loss (disk failure, `docker volume rm` mistake, `down -v` typo) would currently mean **total, permanent data loss** with no way back.

---

## Findings — full list by severity

### High
1. **[HIGH-1] ✅ RESOLVED (Stage A)** No brute-force/rate-limit protection on `/ingresar/`, `/gestion/login/`, or `/recuperar-clave/` — confirmed live. Fixed with two layers: `django-axes` (5 failures per username+IP → 1h cooloff, covers `/ingresar/` and `/gestion/login/` uniformly via `AUTHENTICATION_BACKENDS`) plus nginx `limit_req` on all three endpoints (`login_zone` 5r/m burst 3, `pwreset_zone` 3r/m burst 2, both `nodelay`, HTTP 429). Verified live: axes lockout via a dedicated test (`accounts.tests.test_auth.BruteForceLockoutTests`, real POSTs through the real view) and nginx rate limiting via direct rapid-fire requests against production (both return 429 as expected).
2. **[HIGH-2] ✅ RESOLVED (Stage A)** Production TLS certificate has no automatic renewal; expires 2026-11-03 (carried over, re-confirmed). Fixed with `certbot-recreobienestar.timer`/`.service` (installed, enabled, twice daily). See `ARCHITECTURE.md` §12 for the full flow and the `--no-random-sleep-on-renew` note. Verified via `certbot renew --dry-run` before install — all simulated renewals succeeded.
3. **[HIGH-3] ✅ RESOLVED (Stage A)** No automated database backup exists for a live production database. Fixed with `backup-recreobienestar.timer`/`.service` (daily 02:15) running `deploy/scripts/backup_db.sh` — `pg_dump` + self-validation (`pg_restore --list`) + 14-day retention. See `ARCHITECTURE.md` §14. Verified via full dump→restore→row-count cycle against an isolated throwaway `postgres:16-alpine` container (never `recreo-db`) — counts matched the live database exactly.

### Medium
4. **[MEDIUM-1]** No throttling on the public read-only API — confirmed live (20/20 requests succeeded, no degradation). *Not in Stage A's scope; still open.*
5. **[MEDIUM-2] ✅ RESOLVED (Stage A, as a side effect of the MEDIUM-2/3/4 fix)** `Permissions-Policy`/`X-XSS-Protection` headers missing on most routes (nginx `add_header` inheritance bug). Re-verified live on `/ingresar/`, `/gestion/login/`, and `/api/videos/` — both headers now present everywhere `seguridad.conf` is included.
6. **[MEDIUM-3] ✅ RESOLVED (Stage A)** Duplicate/conflicting security headers on `/api/...` (same root cause as MEDIUM-2). Root cause fixed via `proxy_hide_header` (inherits normally, unlike `add_header`) for `X-Frame-Options`/`X-Content-Type-Options`/`Referrer-Policy`/`Strict-Transport-Security`/`Cache-Control`, plus re-including `seguridad.conf` in every location that declares its own competing `add_header`. Verified live: exactly one instance of each header on `/ingresar/`, `/gestion/login/`, and `/api/videos/`.
7. **[MEDIUM-4] ✅ RESOLVED (Stage A)** The MEDIUM-2/3 bug is the direct root cause of the YouTube error 153 issue (§7) — resolved as part of the same fix. The `site.js` `referrerpolicy` fix (already shipped) and the nginx-side header fix now agree on `strict-origin-when-cross-origin` everywhere.
8. **[MEDIUM-5]** No Content-Security-Policy anywhere on the site.
9. **[MEDIUM-6]** `recreo-django`'s process runs as root inside its container (no `USER` in Dockerfile).
10. **[MEDIUM-7]** Postgres application role is a full superuser, not scoped to least privilege.

### Low
11. **[LOW-1]** SPA fallback returns 200 (not 404) for arbitrary/sensitive-looking paths — confirmed not a real data leak, just scanner noise.
12. **[LOW-2]** `robots.txt` is stale (references removed demo pages, doesn't disallow `/gestion/`/`/mi-cuenta/`).
13. **[LOW-3]** Admin sessions share the same 14-day cookie lifetime as member sessions.
14. **[LOW-4]** Password minimum length is Django's default of 8, not raised for a paid-membership context.
15. **[LOW-5]** No explicit nginx proxy timeouts (defaults to 60s).
16. **[LOW-6]** No log rotation configured anywhere in the stack.

---

## Recommended remediation order

Grouped by what's safe to batch together, ordered by risk reduction per unit of effort/risk:

1. **YouTube `referrerpolicy` fix** (resolves the reported bug) — one line in `site.js`, zero infrastructure risk, no service restart even required (static asset).
2. **Fix the nginx `add_header` inheritance bug** (MEDIUM-2/3/4) — restores missing headers site-wide, removes the duplicates on `/api/`. Config-only change, requires `nginx -t` + reload (same low-risk pattern used throughout this project's history).
3. **Add a minimal Content-Security-Policy** (MEDIUM-5) — do this alongside #2 since both touch the same header logic; start permissive (report-only or a conservative allow-list including `frame-src https://www.youtube-nocookie.com`) to avoid breaking anything unexpectedly.
4. **Add nginx `limit_req` for `/ingresar/`, `/gestion/login/`, `/recuperar-clave/`, and a lighter one for `/api/`** (HIGH-1, MEDIUM-1) — config-only, no restart of Django/DB needed.
5. **Schedule automated `pg_dump` backups** (HIGH-3) — a cron job using the already-tested, already-documented command; purely additive, zero risk to running services.
6. **Add a `USER` directive to the Django Dockerfile** (MEDIUM-6) — requires a rebuild + redeploy of `recreo-django` only (the established, low-risk pattern used throughout this project); needs care around file permissions (`/app/media`, `/app/staticfiles`, `.env` readability) — worth a supervised test before shipping.
7. **Narrow the Postgres role's privileges** (MEDIUM-7) — the highest-care item on this list: touches live database credentials directly. Should be planned as its own change with an explicit rollback snapshot, not bundled with anything else.
8. **Certificate renewal automation** (HIGH-2) — your call on approach (documented in `ARCHITECTURE.md` §12); flagged again here because it's the most consequential expiring-clock item on this whole list.
9. **Log rotation** (LOW-6), **robots.txt update** (LOW-2), **session/password-policy tuning** (LOW-3/4), **explicit proxy timeouts** (LOW-5) — low-urgency cleanup, safe to batch whenever convenient.

## Operational impact and rollback for every proposed change

| # | Change | Downtime | Rollback |
|---|---|---|---|
| 1 | `referrerpolicy` on iframe | None (static JS file) | `git checkout` the file, no redeploy even needed if served directly; otherwise redeploy previous version |
| 2 | Fix `add_header` inheritance | None if `nginx -t` passes before reload (`nginx -s reload` is graceful, zero dropped connections) | Restore previous `recreobienestar.conf` from git, `nginx -t` + reload |
| 3 | Add CSP | None, same reload mechanism as #2 | Same as #2 |
| 4 | nginx `limit_req` | None, same reload mechanism | Same as #2; if overly aggressive, raise the limit or remove the block |
| 5 | Scheduled backups | None (new cron job, doesn't touch running services) | `crontab -e` to remove the entry; no effect on the app either way |
| 6 | Non-root Django container | ~10-30s (same pattern as every prior `recreo-django` redeploy in this project) | Redeploy the prior image tag, or revert the Dockerfile and rebuild |
| 7 | Narrow Postgres role privileges | Requires a `recreo-django` restart to pick up new credentials if the username also changes; a few seconds | Keep a `pg_dump` immediately before the change (already-tested command); revert role grants via `ALTER ROLE` if anything breaks |
| 8 | Cert renewal automation | None if done as a new cron/systemd entry (additive) | Remove the new scheduled job; existing manual renewal path is untouched either way |
| 9 | Log rotation / robots.txt / timeouts / password policy | None to minimal | Plain `git checkout` / config revert, same reload pattern as #2 |

---

**End of audit. No changes have been made. Awaiting your approval before implementing any of the above, in whichever order you'd like.**
