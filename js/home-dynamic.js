/*!
 * Recreo Bienestar — contenido dinámico de la portada.
 * Vanilla JS, sin dependencias. Lee del API de solo lectura ya existente
 * (/api/programs/, /api/videos/, /api/plans/) para que Programas,
 * Videoteca y Membresías reflejen siempre lo que Carla carga en el Admin,
 * en vez de contenido de muestra escrito a mano en este HTML.
 *
 * Se ejecuta después de que la página ya se pintó (defer), así que nunca
 * bloquea ni retrasa la carga inicial — cada sección muestra un mensaje de
 * "Cargando…" (ya en el HTML) hasta que su fetch resuelve, y un mensaje
 * breve si falla, en vez de quedar en blanco o romper la página.
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
        var frameStyle = locked
          ? 'display:flex;align-items:center;justify-content:center;background:var(--color-bg-alt)'
          : "background-image:url('" + esc(video.thumbnail) + "');background-size:cover;background-position:center";
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

  /* ---------- Membresías ---------- */
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
        var periodLabel = plan.duration_days ? 'cada ' + esc(plan.duration_days) + ' días' : '';
        var descriptionLines = (plan.description || '').split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
        var descriptionHtml = descriptionLines.length > 1
          ? '<ul class="plan-features">' + descriptionLines.map(function (line) {
              return '<li><svg><use href="#icon-check"></use></svg> ' + esc(line) + '</li>';
            }).join('') + '</ul>'
          : '<p style="margin-top:0.75rem;color:var(--color-text-muted)">' + esc(descriptionLines[0] || '') + '</p>';
        return (
          '<div class="card plan-card">' +
            '<span class="plan-name">' + esc(plan.name) + '</span>' +
            '<span class="plan-price">' + esc(plan.currency) + ' ' + priceLabel + (periodLabel ? '<small>' + periodLabel + '</small>' : '') + '</span>' +
            descriptionHtml +
            '<a class="btn btn-primary btn-block" href="/registro/">Sumarme</a>' +
          '</div>'
        );
      }).join('');
    }).catch(function () {
      showError(plansMount, 'No pudimos cargar los planes. Probá recargando la página.');
    });
  }
}());
