/*!
 * Recreo Bienestar — comportamiento del sitio (demostración)
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
    var title = facade.getAttribute('data-video-title') || 'Video de Recreo Bienestar';
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

  /* ---------- Formulario de contacto (Fase 3.7) ----------
     Sin backend de envío todavía (fase posterior), así que en vez de
     simular un "mensaje enviado" que en realidad no viajó a ningún lado,
     "Enviar" abre el cliente de correo de quien visita con un mailto:
     prellenado a recreobienestar@gmail.com — honesto sobre lo que
     realmente pasa al hacer click (ver la nota junto al botón en
     index.html). */
  var contactForm = document.querySelector('[data-contact-form]');
  if (contactForm) {
    contactForm.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var nombre = contactForm.querySelector('#nombre');
      var email = contactForm.querySelector('#email');
      var mensaje = contactForm.querySelector('#mensaje');
      var subject = 'Consulta desde recreobienestar.com' +
        (nombre && nombre.value ? ' — ' + nombre.value : '');
      var bodyLines = [];
      if (email && email.value) { bodyLines.push('Correo de contacto: ' + email.value, ''); }
      if (mensaje && mensaje.value) { bodyLines.push(mensaje.value); }
      window.location.href = 'mailto:recreobienestar@gmail.com' +
        '?subject=' + encodeURIComponent(subject) +
        '&body=' + encodeURIComponent(bodyLines.join('\n'));
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

  /* ---------- Animación de entrada al hacer scroll (Phase 3.6) ----------
     Agrega .visible a los elementos .fade-in cuando entran en pantalla —
     ver css/style.css para la transición. No depende de ninguna
     biblioteca (a diferencia de la referencia de Carla, que usaba esto
     mismo vía Lucide/Tailwind); IntersectionObserver es nativo del
     navegador. Si el elemento ya está visible al cargar (por ejemplo, en
     una pantalla muy alta), el observer lo marca igual en el primer
     chequeo, así que nunca queda invisible por error. */
  if ('IntersectionObserver' in window) {
    var fadeObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          fadeObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1 });
    document.querySelectorAll('.fade-in').forEach(function (el) { fadeObserver.observe(el); });
  } else {
    // Navegadores muy antiguos sin soporte: mostrar todo directamente en
    // vez de dejarlo invisible para siempre.
    document.querySelectorAll('.fade-in').forEach(function (el) { el.classList.add('visible'); });
  }

}());
