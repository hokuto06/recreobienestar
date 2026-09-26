/*!
 * Recreo Bienestar — comportamiento de las páginas de miembras (Django).
 * Vanilla JS, sin dependencias. Solo lo mínimo: menú móvil y carga diferida
 * del reproductor de YouTube (nunca autoplay).
 */
(function () {
  'use strict';

  /* ---------- Menú de navegación (móvil) ---------- */
  var navToggle = document.querySelector('[data-nav-toggle]');
  var navLinks = document.querySelector('[data-nav-links]');

  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      var isOpen = navLinks.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', String(isOpen));
    });
    navLinks.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        navLinks.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  /* ---------- Video facade: carga el iframe de youtube-nocookie.com recién
     al hacer clic. Deliberadamente SIN autoplay=1 — el video no arranca
     solo ni al hacer clic en la portada; recién al usar los controles
     nativos de YouTube una vez cargado el reproductor. ---------- */
  document.querySelectorAll('[data-video-facade]').forEach(function (facade) {
    facade.addEventListener('click', function () { loadVideo(facade); });
    facade.addEventListener('keydown', function (evt) {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        facade.click();
      }
    });
  });

  function loadVideo(facade) {
    var videoId = facade.getAttribute('data-video-id');
    var title = facade.getAttribute('data-video-title') || 'Video de Recreo Bienestar';
    if (!videoId) { return; }
    var iframe = document.createElement('iframe');
    iframe.setAttribute('src', 'https://www.youtube-nocookie.com/embed/' + videoId + '?rel=0&modestbranding=1&playsinline=1');
    iframe.setAttribute('title', title);
    iframe.setAttribute('loading', 'lazy');
    // SECURITY_AUDIT.md §7: the page's Referrer-Policy reaches the browser
    // as Django's `same-origin` default (a separate, already-documented
    // nginx header-inheritance bug — not touched here), which sends
    // YouTube no referrer at all and is a known trigger for embed error
    // 153. Setting it explicitly on the iframe itself overrides the page
    // policy for just this element, matching YouTube's own documented
    // oEmbed-recommended markup, regardless of the page-level header.
    iframe.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    iframe.setAttribute('allow', 'accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
    iframe.setAttribute('allowfullscreen', '');
    var frame = facade.closest('.video-detail-frame');
    if (!frame) { return; }
    frame.innerHTML = '';
    frame.appendChild(iframe);
  }

  /* ---------- Favoritos: agregar/quitar sin recargar la página ----------
     Progressive enhancement real: el botón vive dentro de un <form
     method="post"> normal (ver video_detail.html) — sin JS, el submit
     hace un POST+redirect común y corriente. Con JS, interceptamos ese
     mismo submit y usamos fetch() en su lugar, leyendo el token CSRF del
     <meta> en base.html (nunca de la cookie — CSRF_COOKIE_HTTPONLY=True,
     ver el comentario ahí) en vez del campo oculto del form. */
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

  document.querySelectorAll('[data-favorite-form]').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      var button = form.querySelector('button[type="submit"]');
      if (!csrfToken || !button || button.disabled) { return; } // sin token: dejar el submit normal
      evt.preventDefault();
      button.disabled = true;
      fetch(form.action, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken,
          'Accept': 'application/json',
        },
      })
        .then(function (resp) {
          if (!resp.ok) { throw new Error('request failed'); }
          return resp.json();
        })
        .then(function (data) {
          var label = button.querySelector('[data-favorite-label]');
          button.classList.toggle('is-active', data.favorited);
          button.setAttribute('aria-pressed', String(data.favorited));
          if (label) {
            label.textContent = data.favorited ? 'En favoritos' : 'Agregar a favoritos';
          }
          button.disabled = false;
        })
        .catch(function () {
          // Network/server error: fall back to the real form submission
          // (the one evt.preventDefault() just stopped) so the action
          // still completes instead of silently doing nothing.
          form.submit();
        });
    });
  });

  /* ---------- Comprar una Propuesta (Fase 4B-3) ----------
     Same CSRF-via-meta-tag pattern as Favoritos above — never the cookie
     (CSRF_COOKIE_HTTPONLY stays True either way). Unlike Favoritos, there
     is no sensible plain-<form> fallback here: /api/checkout/ answers
     with JSON (an init_point to redirect to), not an HTTP redirect, so a
     no-JS submit would only ever show raw JSON to the buyer. Instead, any
     failure (network error, 4xx/5xx, or a malformed response) shows a
     clear message in [data-checkout-error] — never a silent failure —
     and re-enables the button so the buyer can try again. */
  document.querySelectorAll('[data-checkout-form]').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var button = form.querySelector('[data-checkout-submit]');
      var errorBox = document.querySelector('[data-checkout-error]');
      var slug = form.getAttribute('data-offering-slug');
      if (!csrfToken || !button || !slug || button.disabled) { return; }
      if (errorBox) { errorBox.hidden = true; }
      button.disabled = true;
      var originalLabel = button.textContent;
      button.textContent = 'Redirigiendo a Mercado Pago…';

      fetch('/api/checkout/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'Accept': 'application/json',
        },
        body: JSON.stringify({ offering: slug }),
      })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            if (!resp.ok || !data.init_point) {
              throw new Error(data.detail || 'No se pudo iniciar el pago. Intentá de nuevo en unos minutos.');
            }
            // Full-page redirect to Mercado Pago's Checkout Pro — not a
            // fetch/XHR target, so this is the correct way to get there.
            window.location.href = data.init_point;
          });
        })
        .catch(function (err) {
          button.disabled = false;
          button.textContent = originalLabel;
          if (errorBox) {
            errorBox.textContent = (err && err.message) || 'No se pudo iniciar el pago. Intentá de nuevo en unos minutos.';
            errorBox.hidden = false;
          }
        });
    });
  });

  /* ---------- Empezar prueba gratuita (Fase 5B-1) ----------
     Same CSRF-via-meta-tag pattern as Comprar/Favoritos above. No card,
     no Mercado Pago, no external redirect involved — a successful
     /api/trial/ call just means "go straight to the videoteca". Any
     failure (already used the trial, already on a paid plan, network
     error) shows a clear message in [data-trial-error] instead of
     failing silently. */
  document.querySelectorAll('[data-trial-form]').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var button = form.querySelector('[data-trial-submit]');
      var errorBox = document.querySelector('[data-trial-error]');
      if (!csrfToken || !button || button.disabled) { return; }
      if (errorBox) { errorBox.hidden = true; }
      button.disabled = true;
      var originalLabel = button.textContent;
      button.textContent = 'Activando tu prueba…';

      fetch('/api/trial/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'Accept': 'application/json',
        },
      })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            if (!resp.ok) {
              throw new Error(data.detail || 'No se pudo activar la prueba. Intentá de nuevo en unos minutos.');
            }
            window.location.href = '/videoteca/';
          });
        })
        .catch(function (err) {
          button.disabled = false;
          button.textContent = originalLabel;
          if (errorBox) {
            errorBox.textContent = (err && err.message) || 'No se pudo activar la prueba. Intentá de nuevo en unos minutos.';
            errorBox.hidden = false;
          }
        });
    });
  });

  /* ---------- Suscribirme a un plan pago (Fase 5B-2a) ----------
     Same CSRF-via-meta-tag pattern and failure handling as Comprar
     above: /api/subscribe/ answers with JSON (Mercado Pago's init_point
     for the recurring-charge authorization), so the redirect happens
     here. Any failure shows a clear message in [data-subscribe-error]. */
  document.querySelectorAll('[data-subscribe-form]').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var button = form.querySelector('[data-subscribe-submit]');
      var errorBox = document.querySelector('[data-subscribe-error]');
      var slug = form.getAttribute('data-plan-slug');
      if (!csrfToken || !button || !slug || button.disabled) { return; }
      if (errorBox) { errorBox.hidden = true; }
      button.disabled = true;
      var originalLabel = button.textContent;
      button.textContent = 'Redirigiendo a Mercado Pago…';

      fetch('/api/subscribe/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'Accept': 'application/json',
        },
        body: JSON.stringify({ plan: slug }),
      })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            if (!resp.ok || !data.init_point) {
              throw new Error(data.detail || 'No se pudo iniciar la suscripción. Intentá de nuevo en unos minutos.');
            }
            window.location.href = data.init_point;
          });
        })
        .catch(function (err) {
          button.disabled = false;
          button.textContent = originalLabel;
          if (errorBox) {
            errorBox.textContent = (err && err.message) || 'No se pudo iniciar la suscripción. Intentá de nuevo en unos minutos.';
            errorBox.hidden = false;
          }
        });
    });
  });

  /* ---------- Cancelar suscripción (Fase 5B-2b) ----------
     Same CSRF-via-meta-tag pattern as above. Asks for confirmation first
     (cancelling is a real action at Mercado Pago), then reloads the page
     so it shows the new state — including until when access continues.
     Any failure shows [data-cancel-error]; the server leaves the
     subscription untouched in that case. */
  document.querySelectorAll('[data-cancel-form]').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var button = form.querySelector('[data-cancel-submit]');
      var errorBox = document.querySelector('[data-cancel-error]');
      var subscriptionId = form.getAttribute('data-subscription-id');
      if (!csrfToken || !button || !subscriptionId || button.disabled) { return; }
      if (!window.confirm('¿Seguro que querés cancelar tu suscripción?')) { return; }
      if (errorBox) { errorBox.hidden = true; }
      button.disabled = true;
      var originalLabel = button.textContent;
      button.textContent = 'Cancelando…';

      fetch('/api/subscription/cancel/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'Accept': 'application/json',
        },
        body: JSON.stringify({ subscription: subscriptionId }),
      })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            if (!resp.ok) {
              throw new Error(data.detail || 'No se pudo cancelar la suscripción. Intentá de nuevo en unos minutos.');
            }
            window.location.reload();
          });
        })
        .catch(function (err) {
          button.disabled = false;
          button.textContent = originalLabel;
          if (errorBox) {
            errorBox.textContent = (err && err.message) || 'No se pudo cancelar la suscripción. Intentá de nuevo en unos minutos.';
            errorBox.hidden = false;
          }
        });
    });
  });
})();
