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
    iframe.setAttribute('allow', 'accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
    iframe.setAttribute('allowfullscreen', '');
    var frame = facade.closest('.video-detail-frame');
    if (!frame) { return; }
    frame.innerHTML = '';
    frame.appendChild(iframe);
  }
})();
