/*!
 * Recreo Bienestar — contenido dinámico de la portada.
 * Vanilla JS, sin dependencias. Lee del API de solo lectura ya existente
 * (/api/programs/, /api/videos/, /api/plans/, /api/offerings/,
 * /api/site-settings/) para que Programas, Videoteca, Membresías,
 * Propuestas, el hero, la bio de Carla, el podcast y los datos de
 * contacto reflejen siempre lo que Carla carga en el Admin, en vez de
 * contenido de muestra escrito a mano en este HTML.
 *
 * Se ejecuta después de que la página ya se pintó (defer), así que nunca
 * bloquea ni retrasa la carga inicial — cada sección muestra un mensaje de
 * "Cargando…" (ya en el HTML) hasta que su fetch resuelve, y un mensaje
 * breve si falla, en vez de quedar en blanco o romper la página. Los
 * campos de SiteSettings/Offering que Carla todavía no cargó (texto
 * vacío, sin link de pago) simplemente no se muestran — nunca queda un
 * guion, un "#" o un placeholder visible.
 */
(function () {
  'use strict';

  var PROGRAM_ICONS = ['icon-leaf', 'icon-moon', 'icon-heart', 'icon-users', 'icon-sparkle', 'icon-calendar'];

  function esc(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function showError(mount, message) {
    mount.innerHTML = '<p class="placeholder-note">' + esc(message) + '</p>';
  }

  function fetchJSON(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (resp) {
      if (!resp.ok) { throw new Error('bad response: ' + resp.status); }
      return resp.json();
    });
  }

  /* ---------- Programas ---------- */
  var programsMount = document.querySelector('[data-programs-mount]');
  if (programsMount) {
    fetchJSON('/api/programs/').then(function (data) {
      var programs = data.results || data;
      if (!programs.length) {
        showError(programsMount, 'Muy pronto vamos a sumar programas por tipo de práctica.');
        return;
      }
      programsMount.innerHTML = programs.slice(0, 4).map(function (program, index) {
        var icon = PROGRAM_ICONS[index % PROGRAM_ICONS.length];
        return (
          '<a class="card program-card" href="/programas/' + esc(program.slug) + '/">' +
            '<div class="icon-tile"><svg><use href="#' + icon + '"></use></svg></div>' +
            '<h3>' + esc(program.name) + '</h3>' +
            '<p>' + esc(program.description) + '</p>' +
          '</a>'
        );
      }).join('');
    }).catch(function () {
      showError(programsMount, 'No pudimos cargar los programas. Probá recargando la página.');
    });
  }

  /* ---------- Videoteca (destacados + gratuitos) ---------- */
  var videosMount = document.querySelector('[data-videos-mount]');
  if (videosMount) {
    Promise.all([
      fetchJSON('/api/videos/?is_featured=true'),
      fetchJSON('/api/videos/?access_level=free'),
    ]).then(function (results) {
      var seen = {};
      var videos = [];
      results.forEach(function (data) {
        (data.results || data).forEach(function (video) {
          if (!seen[video.id]) {
            seen[video.id] = true;
            videos.push(video);
          }
        });
      });
      if (!videos.length) {
        showError(videosMount, 'Muy pronto vamos a sumar videos de muestra acá.');
        return;
      }
      videosMount.innerHTML = videos.slice(0, 6).map(function (video) {
        // La API ya evalúa el acceso según la sesión de quien mira la
        // página (memberships.services.can_access_video); thumbnail viene
        // null cuando el video está bloqueado PARA ESTA VISITA puntual —
        // nunca se expone un thumbnail derivado del ID de YouTube para un
        // video al que esta visita no tiene acceso (ver
        // catalog/serializers.py:VideoListSerializer.get_thumbnail).
        var locked = !video.thumbnail;
        // display:block;position:relative on the unlocked branch matters,
        // not just cosmetically: .video-frame is an <a> with no display
        // rule in CSS, so without it the anchor stays inline and
        // aspect-ratio (css/style.css .video-frame) never applies — the
        // box collapses to line-height instead of a 16:9 box, and the
        // thumbnail becomes an invisible sliver even though the
        // background-image itself is set correctly. Keep this in sync
        // with catalog/templates/catalog/partials/_video_card.html, the
        // server-rendered equivalent of this same card.
        var frameStyle = locked
          ? 'display:flex;align-items:center;justify-content:center;background:var(--color-bg-alt)'
          : "display:block;position:relative;background-image:url('" + esc(video.thumbnail) + "');background-size:cover;background-position:center";
        return (
          '<article class="video-card' + (locked ? ' video-locked' : '') + '">' +
            '<a class="video-frame" href="/videos/' + esc(video.slug) + '/" style="' + frameStyle + '" aria-label="' + esc(video.title) + '">' +
              '<span class="lock-chip">' +
                (locked
                  ? '<svg width="13" height="13"><use href="#icon-lock"></use></svg> ' + esc(video.access_level_display || 'Exclusivo')
                  : '<svg width="13" height="13"><use href="#icon-play"></use></svg> Vista previa') +
              '</span>' +
              '<span class="play-btn" aria-hidden="true"><svg><use href="#icon-' + (locked ? 'lock' : 'play') + '"></use></svg></span>' +
            '</a>' +
            '<div class="video-meta">' +
              '<h3>' + esc(video.title) + '</h3>' +
              '<p>' + esc((video.category && video.category.name) || '') + '</p>' +
            '</div>' +
          '</article>'
        );
      }).join('');
    }).catch(function () {
      showError(videosMount, 'No pudimos cargar los videos. Probá recargando la página.');
    });
  }

  /* ---------- Membresías: Columna Sana (Phase 3.7) ----------
     visual_variant viene del backend (MembershipPlan.visual_variant,
     elegido por Carla en el Admin — no ciclado por índice como las
     tarjetas de Propuestas/Programas, porque cada plan tiene una
     presentación comercial fija). badge/subtitle/cta_label son
     opcionales: si vienen vacíos, la tarjeta se ve como la variante
     "default" sin etiqueta ni CTA personalizado. */
  var PLAN_VARIANT_CLASS = { highlighted: 'plan-card--highlighted', premium: 'plan-card--premium' };
  var plansMount = document.querySelector('[data-plans-mount]');
  if (plansMount) {
    fetchJSON('/api/plans/').then(function (data) {
      var plans = data.results || data;
      if (!plans.length) {
        showError(plansMount, 'Los planes de membresía se van a publicar pronto.');
        return;
      }
      plansMount.innerHTML = plans.map(function (plan) {
        var priceNumber = Number(plan.price);
        var priceLabel = isNaN(priceNumber)
          ? esc(plan.price)
          : priceNumber.toLocaleString('es-AR', { maximumFractionDigits: 0 });
        // "/mes" para el caso de uso real (planes mensuales, 30 días);
        // "cada N días" como respaldo genérico para cualquier otra
        // duración que Carla cargue más adelante.
        var periodLabel = plan.duration_days === 30 ? '/mes' : (plan.duration_days ? 'cada ' + esc(plan.duration_days) + ' días' : '');
        var descriptionLines = (plan.description || '').split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
        var descriptionHtml = descriptionLines.length > 1
          ? '<ul class="plan-features">' + descriptionLines.map(function (line) {
              return '<li><svg><use href="#icon-check"></use></svg> ' + esc(line) + '</li>';
            }).join('') + '</ul>'
          : '<p style="margin-top:0.75rem;color:var(--color-text-muted)">' + esc(descriptionLines[0] || '') + '</p>';
        var variantClass = PLAN_VARIANT_CLASS[plan.visual_variant] || '';
        var ctaLabel = plan.cta_label || 'Sumarme';
        return (
          '<div class="card plan-card' + (variantClass ? ' ' + variantClass : '') + '">' +
            (plan.badge ? '<span class="plan-badge">' + esc(plan.badge) + '</span>' : '') +
            '<span class="plan-eyebrow">' + esc(plan.name) + '</span>' +
            '<span class="plan-name">' + esc(plan.subtitle || plan.name) + '</span>' +
            '<span class="plan-price">$' + priceLabel + ' ' + esc(plan.currency) + (periodLabel ? '<small>' + periodLabel + '</small>' : '') + '</span>' +
            descriptionHtml +
            '<a class="btn btn-primary btn-block" href="/registro/">' + esc(ctaLabel) + '</a>' +
          '</div>'
        );
      }).join('');
    }).catch(function () {
      showError(plansMount, 'No pudimos cargar los planes. Probá recargando la página.');
    });
  }

  /* ---------- Programas y Cursos (Offering: productos de pago único) ----------
     Sin link de pago cargado todavía (Fase 4 los conecta): el botón se
     muestra igual, marcado como no disponible en vez de desaparecer —
     nunca se oculta el producto entero por falta de link de pago. */
  var OFFERING_ACCENTS = ['', 'offering-card--pool', 'offering-card--earth'];
  var offeringsMount = document.querySelector('[data-offerings-mount]');
  if (offeringsMount) {
    fetchJSON('/api/offerings/').then(function (data) {
      var offerings = data.results || data;
      if (!offerings.length) {
        showError(offeringsMount, 'Muy pronto vamos a sumar programas acá.');
        return;
      }
      offeringsMount.innerHTML = offerings.map(function (offering, index) {
        var accent = OFFERING_ACCENTS[index % OFFERING_ACCENTS.length];
        var priceNumber = Number(offering.price);
        var priceLabel = isNaN(priceNumber)
          ? esc(offering.price)
          : priceNumber.toLocaleString('es-AR', { maximumFractionDigits: 0 });
        var buttons = '';
        if (offering.payment_url_ars) {
          buttons += '<a class="is-primary" href="' + esc(offering.payment_url_ars) + '" target="_blank" rel="noopener">Pagar en ARS</a>';
        }
        if (offering.payment_url_usd) {
          buttons += '<a class="is-outline" href="' + esc(offering.payment_url_usd) + '" target="_blank" rel="noopener">Pagar en USD</a>';
        }
        if (!buttons) {
          buttons = '<span class="is-disabled">Próximamente</span>';
        }
        return (
          '<div class="offering-card' + (accent ? ' ' + accent : '') + '">' +
            '<div>' +
              '<h3 class="offering-name">' + esc(offering.name) + '</h3>' +
              (offering.description ? '<p class="lede">' + esc(offering.description) + '</p>' : '') +
              '<p class="offering-price">$' + priceLabel + ' ' + esc(offering.currency) + '</p>' +
            '</div>' +
            '<div class="offering-actions">' + buttons + '</div>' +
          '</div>'
        );
      }).join('');
    }).catch(function () {
      showError(offeringsMount, 'No pudimos cargar los programas. Probá recargando la página.');
    });
  }

  /* ---------- SiteSettings: hero, bio de Carla, podcast, contacto ---------- */
  fetchJSON('/api/site-settings/').then(function (settings) {
    if (settings.hero_headline) {
      var headline = document.querySelector('[data-hero-headline]');
      if (headline) { headline.textContent = settings.hero_headline; }
    }
    if (settings.tagline) {
      var tagline = document.querySelector('[data-tagline]');
      if (tagline) { tagline.textContent = '"' + settings.tagline + '"'; tagline.hidden = false; }
    }
    if (settings.carla_bio) {
      var bioMount = document.querySelector('[data-carla-bio]');
      if (bioMount) {
        var paragraphs = settings.carla_bio.split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
        bioMount.innerHTML = paragraphs.map(function (p, i) {
          return '<p class="' + (i === 0 ? 'lede' : '') + '" style="' + (i > 0 ? 'margin-top:1rem;color:var(--color-text-muted)' : '') + '">' + esc(p) + '</p>';
        }).join('');
      }
    }
    if (settings.carla_bio_highlight) {
      var highlightEl = document.querySelector('[data-carla-bio-highlight]');
      if (highlightEl) {
        highlightEl.textContent = settings.carla_bio_highlight;
        highlightEl.hidden = false;
      }
    }
    if (settings.contact_email) {
      var emailEl = document.querySelector('[data-contact-email]');
      if (emailEl) {
        emailEl.innerHTML = '<a href="mailto:' + esc(settings.contact_email) + '">' + esc(settings.contact_email) + '</a>';
      }
    }
    if (settings.instagram_url) {
      var handle = settings.instagram_url.replace(/\/+$/, '').split('/').pop() || settings.instagram_url;
      document.querySelectorAll('[data-instagram-link]').forEach(function (link) {
        link.href = settings.instagram_url;
        if (link.closest('.contact-info-item')) { link.textContent = '@' + handle; }
      });
    }
    // El nombre del podcast sigue el mismo patrón que data-carla-bio: el
    // HTML ya trae el nombre aprobado como respaldo, y solo se reemplaza
    // si SiteSettings.podcast_name trae uno distinto. El botón, en
    // cambio, depende por completo de podcast_url — permanece oculto
    // (atributo `hidden` en el HTML) hasta que Carla cargue un link.
    if (settings.podcast_name) {
      var podcastName = document.querySelector('[data-podcast-name]');
      if (podcastName) { podcastName.textContent = settings.podcast_name; }
    }
    if (settings.podcast_url) {
      var podcastLink = document.querySelector('[data-podcast-link]');
      if (podcastLink) {
        podcastLink.href = settings.podcast_url;
        podcastLink.hidden = false;
      }
    }
  }).catch(function () {
    // Silencioso: si falla, la página simplemente conserva el texto de
    // respaldo ya presente en el HTML (headline/bio por defecto, podcast
    // oculto) — no hay nada roto que mostrarle a quien visita.
  });
}());
