# Recreo Bienestar — Phase 2.5 Deliverables

Isolating Recreo Bienestar into its own fully independent production
stack: `recreo-nginx` + `recreo-django` + `recreo-db`, with no dependency
on `nginx-flask-prod`'s containers or network. Cutover completed and
publicly validated. Branch `feature/django-backend`, not merged to `main`.

## 1. Scope change during this phase

Original plan assumed Recreo Bienestar's nginx would coexist with
`nginx-flask-prod`'s other domains. Investigation found `nginx-proxy`
serves **four** domains on shared ports 80/443 (`recreobienestar.com`,
`jeref.com.ar`, `estebanmartins.com.ar` + subdomains, and
`silviorodriguez.com.ar` via CloudFront) — claiming those ports for a
Recreo-only nginx would silently take the other three offline. Flagged
this before proceeding; you explicitly decided: **Recreo Bienestar becomes
the sole public site on this EC2 for now**, no new Elastic IP, other
domains offline until migrated later. Proceeded on that basis.

## 2. Architecture

See `ARCHITECTURE.md` §1–3 for the full diagram. Summary: `recreo-nginx`
is the only container publishing host ports (80/443); `recreo-django` and
`recreo-db` are unreachable from outside their own Docker networks, same
as before. The only remaining dependency on `nginx-flask-prod` is a
deliberate, read-only one: the Let's Encrypt certificate and ACME webroot
(§5) — mounted, not copied, because a cert is a renewed credential, not
static content.

## 3. Static site handling

**Finding**: the local git repo's static files (`index.html`, `login.html`,
`miembros.html`, `css/style.css`, `js/main.js`) were stale relative to
what was actually live on the server; `robots.txt`/`sitemap.xml` were
never committed at all. Per "reuse without regenerating," treated the
**live server files as authoritative** — synced them down into the repo
root (not the other way around) before building the new stack, so git now
actually reflects what's deployed. No content was redesigned or
regenerated; every byte matches what was live before this phase, verified
via checksum before and after.

`nginx/static-root/` (in `backend/`) is deploy-time-populated from the
repo root's canonical copy, not a second committed copy — avoids drift
between two git-tracked locations for the same content. See
`.gitignore`.

## 4. Two real bugs found during pre-cutover testing

Both would have shipped silently if temp-port testing had only checked
status codes at the top level (`/`, `/gestion/`, `/api/`) rather than
specific nested paths and headers:

1. **Path truncation via variable-based `proxy_pass`.** Used
   `set $django_upstream recreo-django:8100;` + `proxy_pass
   http://$django_upstream/gestion/;` (etc.) specifically to get
   request-time DNS resolution (§6). nginx's documented behavior: when
   `proxy_pass` targets a *variable*, it does NOT do the normal "replace
   the matched location prefix with the given URI" substitution — it uses
   whatever literal path follows the variable as a fixed, exact target,
   discarding the rest of the real request URI. `/static/<any file>` was
   silently collapsing to a bare `/static/` on every single request.
   `/gestion/login/` had the identical bug but *looked* correct by
   coincidence (Django's admin already redirects `/gestion/` →
   `/gestion/login/?next=/gestion/` for anonymous users, so the truncated
   request produced a response that was plausible for the URL actually
   typed). Found by standing up a throwaway Python HTTP debug backend on
   the same Docker network and pointing `$django_upstream` at it
   temporarily, to see the exact path nginx forwarded — confirmed the
   truncation directly, then confirmed the fix the same way before
   trusting it against the real app. Fixed by dropping the trailing path
   from every `proxy_pass $django_upstream` — correct here because
   Django's own URL patterns already expect the exact original path
   everywhere in this app (no proxy-side rewriting needed at all).
2. **Missing `X-Forwarded-Proto` on `/static/`/`/media/`.** Without it,
   Django can't tell the request arrived over HTTPS, so
   `SECURE_SSL_REDIRECT` self-redirected every static asset request.
   Caught by the same debugging pass once the path issue was fixed and a
   *different* symptom (redirect to the correct path, wrong scheme)
   appeared.

Both fixed, and the full route matrix (see §7) re-verified afterward.

## 5. Certificate handling

- Inspected the actual cert: issued 2026-08-05 into a **custom certbot
  config directory** (`/home/ubuntu/nginx-flask-prod/letsencrypt`), not
  the host's default `/etc/letsencrypt` — confirmed by checking
  `sudo certbot certificates` (host default: no record of this domain at
  all) against `renewal/recreobienestar.com.conf`'s `config_dir` line.
- **Finding, pre-existing, unrelated to this phase**: the host's automatic
  `certbot.timer` only renews certs under the default config dir — this
  cert has **no automatic renewal** and would simply expire 2026-11-03
  without action. Not fixed here (would mean setting up new unattended
  automation touching live TLS certs — flagged per "stop and explain
  before changing anything risky," left for your decision).
- `recreo-nginx` mounts the existing cert directory and ACME webroot
  read-only, at the exact same container paths `nginx-flask-prod`'s nginx
  used — zero changes needed to how the cert is issued or where it lives,
  so the (manual) renewal command keeps working, just needs one addition:
  a `--deploy-hook` to reload `recreo-nginx` instead of the old container.
  Exact command in `ARCHITECTURE.md` §12.
- No cert, key, or renewal config was copied into git at any point.

## 6. Built-in resilience (proactive, not required by the original ask)

`nginx-flask-prod/nginx/default.conf` still hard-fails `nginx -t` if
`blog-front` is stopped (confirmed unchanged, still blocking that stack's
own reloads). Having lived through exactly that failure mode in Phase 2,
built `recreo-nginx`'s config to be immune to it from day one:
`resolver 127.0.0.11 valid=10s;` + a variable in every `proxy_pass` means
`recreo-django`'s hostname resolves at request time, not at config-load
time — `recreo-nginx` starts and reloads successfully even if
`recreo-django` is briefly down, returning 502 for actual requests during
that window instead of refusing to start at all. Verified with a fully
offline `nginx -t` (no Docker network attached to anything) — passed.

## 7. Pre-cutover validation (temp ports 8088/8444)

Full route matrix tested and passing before touching live ports:
HTTP→HTTPS redirect, www→apex redirect, static landing, static assets
(both the marketing site's own and Django's `/static/`), `/gestion/`
(bare and `/login/`), `/api/videos/`, `/api/categories/`, `/api/plans/`,
`/registro/`, `/ingresar/`, `/mi-cuenta/` (redirect-to-login),
`/videoteca/`, `/videos/<slug>/`, `/recuperar-clave/`, `/salir/`
(405 on GET), unknown-path SPA fallback. Full authenticated flow
(login → dashboard shows correct plan → paid video accessible → logout)
verified end-to-end through the temp-port nginx from the host, not just
against the container directly. TLS handshake, cert SANs/expiry, and
`nginx -t` all confirmed before cutover.

## 8. Cutover

1. Checkpoint: `docker ps` snapshot taken before any change.
2. `docker stop nginx-proxy` (not removed).
3. `docker compose -f docker-compose.yml -f docker-compose.prodports.yml
   up -d recreo-nginx` — recreated `recreo-nginx` with real port bindings.
4. Verified immediately: `https://recreobienestar.com/` → 200, `http://` →
   301 redirect, `recreo-nginx` healthy.
5. Actual downtime: under 30 seconds for `recreobienestar.com`.

## 9. Post-cutover validation (public, not just internal)

All confirmed against the live public domain (not the container directly):
`https://recreobienestar.com` (200), `https://www.recreobienestar.com`
(301→apex), `/gestion/` (200), `/api/videos/` (200), `/registro/` (200),
`/ingresar/` (200), `/mi-cuenta/` (302→login), `/videoteca/` (200),
static assets (200 both marketing-site and Django `/static/`), certificate
SANs/expiry (matches, valid), nginx access/error logs (clean — only
routine internet bot-scanning noise, no application errors), Django logs
(clean, no migrations pending), PostgreSQL (`pg_isready` → accepting
connections), memory (~69MB free, same steady-state range as before this
phase — no new containers were added, `recreo-nginx` replaced
`nginx-proxy`'s memory footprint roughly 1:1), `docker ps` (`recreo-nginx`
healthy on 80/443; `recreo-django`/`recreo-db` running; `nginx-proxy`
Exited(0), not removed; `django-api`/`blog-front` still absent, confirmed
still stopped).

## 10. Known consequence (explicitly approved, not a side effect)

`jeref.com.ar`, `estebanmartins.com.ar` (+ subdomains), and
`silviorodriguez.com.ar` are now offline — no process listens on 80/443
for them anymore. Their containers (`flask-prod`, `certbot`) are still
running but unreachable from outside; nothing about them was modified or
removed, only made externally unreachable by `nginx-proxy` being stopped.
This was your explicit, direct decision, not a Claude-introduced side
effect — recorded here for the historical record.

## 11. Manual steps remaining

1. Decide how/when to migrate `jeref.com.ar`, `estebanmartins.com.ar`, and
   `silviorodriguez.com.ar` to their own infrastructure (or bring
   `nginx-proxy` back in some form) — out of scope for this phase.
2. Decide on the certificate renewal gap (§5) — set up scheduled renewal,
   or continue renewing manually before 2026-11-03.
3. Consider the t3.small upgrade (`ARCHITECTURE.md` §15) now that this
   box's only job is Recreo Bienestar.
4. Optional polish: add a `default_server` catch-all in
   `recreobienestar.conf` so stray requests for the now-offline domains
   (bots, old bookmarks) get a clean response instead of silently landing
   on Recreo's own default server block. Not done — no functional impact,
   flagged as a nice-to-have.
5. Review this diff and, when ready, merge `feature/django-backend` — not
   done, per your instruction to wait until public validation succeeds
   (it has — see §9 — but the merge itself is still your call).

## 12. Rollback

See `ARCHITECTURE.md` §18 for full detail. One-line summary: `docker stop
recreo-nginx && docker start nginx-proxy` — fully reversible, nothing
destructive was done to `nginx-flask-prod` at any point.
