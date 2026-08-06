/*!
 * Recreo y Bienestar — comportamiento del sitio (demostración)
 * Vanilla JS, sin dependencias externas.
 * No implementa autenticación real, pagos ni persistencia de datos.
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

  /* ---------- Acordeón de preguntas frecuentes ---------- */
  document.querySelectorAll('[data-faq-item]').forEach(function (item) {
    var question = item.querySelector('.faq-question');
    if (!question) { return; }
    question.addEventListener('click', function () {
      var isOpen = item.getAttribute('data-open') === 'true';
      // Cierra los demás ítems para mantener la lista prolija.
      item.closest('[data-faq-list]').querySelectorAll('[data-faq-item]').forEach(function (other) {
        other.setAttribute('data-open', 'false');
        other.querySelector('.faq-question').setAttribute('aria-expanded', 'false');
      });
      item.setAttribute('data-open', String(!isOpen));
      question.setAttribute('aria-expanded', String(!isOpen));
    });
  });

  /* ---------- Video facade: carga el iframe de YouTube-nocookie recién al hacer clic ----------
     Evita cargar el reproductor de YouTube por adelantado (peso inicial de página más liviano).
     Usa la miniatura pública del video (i.ytimg.com) como imagen de portada. */
  document.querySelectorAll('[data-video-facade]').forEach(function (facade) {
    var locked = facade.closest('[data-locked="true"]');
    facade.addEventListener('click', function () {
      if (locked) {
        openLockedModal(facade.getAttribute('data-video-title') || '');
        return;
      }
      loadVideo(facade);
    });
    facade.addEventListener('keydown', function (evt) {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        facade.click();
      }
    });
  });

  function loadVideo(facade) {
    var videoId = facade.getAttribute('data-video-id');
    var title = facade.getAttribute('data-video-title') || 'Video de Recreo y Bienestar';
    if (!videoId) { return; }
    var iframe = document.createElement('iframe');
    iframe.setAttribute('src', 'https://www.youtube-nocookie.com/embed/' + videoId + '?rel=0&modestbranding=1&playsinline=1&autoplay=1');
    iframe.setAttribute('title', title);
    iframe.setAttribute('loading', 'lazy');
    iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
    iframe.setAttribute('allowfullscreen', '');
    var frame = facade.closest('.video-frame');
    frame.innerHTML = '';
    frame.appendChild(iframe);
  }

  /* ---------- Modal de contenido bloqueado (demostración) ---------- */
  var lockedModal = document.querySelector('[data-locked-modal]');
  function openLockedModal(label) {
    if (!lockedModal) { return; }
    var labelEl = lockedModal.querySelector('[data-locked-label]');
    if (labelEl && label) {
      labelEl.textContent = label;
    }
    lockedModal.classList.add('is-open');
    var closeBtn = lockedModal.querySelector('.modal-close');
    if (closeBtn) { closeBtn.focus(); }
  }
  function closeLockedModal() {
    if (lockedModal) { lockedModal.classList.remove('is-open'); }
  }
  if (lockedModal) {
    lockedModal.addEventListener('click', function (evt) {
      if (evt.target === lockedModal || evt.target.closest('.modal-close')) {
        closeLockedModal();
      }
    });
    document.addEventListener('keydown', function (evt) {
      if (evt.key === 'Escape') { closeLockedModal(); }
    });
  }

  /* ---------- Categorías del área de miembras (filtro visual simple) ---------- */
  var categoryTabs = document.querySelectorAll('[data-category-tab]');
  var videoItems = document.querySelectorAll('[data-video-category]');
  if (categoryTabs.length && videoItems.length) {
    categoryTabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        categoryTabs.forEach(function (t) { t.setAttribute('aria-pressed', 'false'); });
        tab.setAttribute('aria-pressed', 'true');
        var category = tab.getAttribute('data-category-tab');
        videoItems.forEach(function (item) {
          var show = category === 'todas' || item.getAttribute('data-video-category') === category;
          item.style.display = show ? '' : 'none';
        });
      });
    });
  }

  /* ---------- Formulario de contacto (demostración, sin backend) ---------- */
  var contactForm = document.querySelector('[data-contact-form]');
  if (contactForm) {
    contactForm.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var successMsg = contactForm.querySelector('[data-form-success]') ||
        document.querySelector('[data-form-success]');
      if (successMsg) {
        successMsg.classList.add('is-visible');
        successMsg.setAttribute('role', 'status');
      }
      contactForm.reset();
    });
  }

  /* ---------- Acceso de demostración (sin autenticación real) ----------
     Simula un ingreso: acepta cualquier dato (o ninguno) y redirige al
     área de miembras de la plataforma. No valida credenciales ni las envía
     a ningún servidor. */
  var demoLoginForm = document.querySelector('[data-demo-login]');
  if (demoLoginForm) {
    demoLoginForm.addEventListener('submit', function (evt) {
      evt.preventDefault();
      window.location.href = 'miembros.html';
    });
  }

}());
