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

  /* ---------- Formulario de contacto (Fase 3.9) ----------
     Ya no es un mailto: — "Enviar" hace POST a /api/contacto/ (ver
     site_content.views.ContactMessageCreateView en el backend), que
     persiste el mensaje. Público y sin sesión a propósito (ver el
     docstring de esa vista): no hace falta CSRF token porque la vista
     nunca se autentica por sesión, así que no hay nada que exceptuar acá
     ni en ninguna otra parte del sitio. #website es el honeypot (oculto
     vía .field--honeypot en css/style.css) — si viene con contenido, el
     backend responde éxito igual pero no guarda nada, así que del lado
     del navegador no hay nada especial que hacer con él más que
     mandarlo tal cual. */
  var contactForm = document.querySelector('[data-contact-form]');
  if (contactForm) {
    var contactStatus = contactForm.querySelector('[data-contact-status]');
    var contactSubmitBtn = contactForm.querySelector('button[type="submit"]');

    function showContactStatus(kind, text) {
      if (!contactStatus) { return; }
      contactStatus.textContent = text;
      contactStatus.className = 'form-status is-visible form-status--' + kind;
    }

    contactForm.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var nombre = contactForm.querySelector('#nombre');
      var email = contactForm.querySelector('#email');
      var mensaje = contactForm.querySelector('#mensaje');
      var website = contactForm.querySelector('#website');

      // Validación básica en el cliente (UX) — el servidor es la fuente
      // de verdad y vuelve a validar todo esto igual.
      var nombreValue = nombre ? nombre.value.trim() : '';
      var emailValue = email ? email.value.trim() : '';
      var mensajeValue = mensaje ? mensaje.value.trim() : '';
      var emailLooksValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailValue);
      if (!nombreValue || !emailValue || !mensajeValue || !emailLooksValid) {
        showContactStatus('error', 'Completá tu nombre, un correo válido y tu mensaje.');
        return;
      }

      if (contactSubmitBtn) { contactSubmitBtn.disabled = true; }

      fetch('/api/contacto/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          name: nombreValue,
          email: emailValue,
          message: mensajeValue,
          website: website ? website.value : '',
        }),
      }).then(function (resp) {
        if (!resp.ok) { throw new Error('bad response: ' + resp.status); }
        return resp.json();
      }).then(function () {
        showContactStatus('success', '¡Gracias! Recibimos tu consulta y te responderemos a la brevedad.');
        contactForm.reset();
      }).catch(function () {
        // Se deja lo ya tipeado sin tocar (no se llama a contactForm.reset())
        // para que quien escribió no tenga que volver a redactar todo.
        showContactStatus('error', 'No pudimos enviar tu mensaje. Probá de nuevo en unos minutos.');
      }).then(function () {
        if (contactSubmitBtn) { contactSubmitBtn.disabled = false; }
      });
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
