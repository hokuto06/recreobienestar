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
})();
