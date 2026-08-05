# Recreo Bienestar

Sitio de demostración funcional para **Recreo Bienestar**, una plataforma de
membresía de bienestar y movimiento consciente. Ofrece a las socias acceso a
una videoteca de clases guiadas en video, pensada especialmente para mujeres
interesadas en bienestar personal, movimiento consciente, relajación y
hábitos saludables.

Este repositorio contiene una **demostración visual y funcional**: landing
page pública, pantalla de acceso simulada y un área de miembras simulada con
categorías, progreso y contenido bloqueado/desbloqueado. No incluye backend,
autenticación real, pagos ni base de datos — está pensado como base de
diseño y estructura lista para integrar esas piezas más adelante.

## Índice

- [Descripción del proyecto](#descripción-del-proyecto)
- [Estructura de carpetas](#estructura-de-carpetas)
- [Tecnologías utilizadas](#tecnologías-utilizadas)
- [Vista previa local](#vista-previa-local)
- [Compatibilidad de navegadores](#compatibilidad-de-navegadores)
- [Despliegue en cualquier servidor estático](#despliegue-en-cualquier-servidor-estático)
- [Funcionalidades futuras](#funcionalidades-futuras)

## Descripción del proyecto

El sitio incluye tres páginas:

| Página | Descripción |
|---|---|
| `index.html` | Landing pública: encabezado y navegación, hero, presentación de la plataforma, sección sobre Carla, beneficios y enfoque, programas, vista previa de la videoteca, planes de membresía, testimonios de ejemplo, preguntas frecuentes, contacto y pie de página. |
| `login.html` | Pantalla de acceso de demostración. No valida credenciales reales: cualquier dato ingresado redirige al área de miembras. |
| `miembros.html` | Área de miembras simulada: filtro por categorías, tarjetas de video con estados disponible/exclusivo, indicadores de progreso de ejemplo y un modal de contenido bloqueado. |

Los videos de muestra se sirven mediante `youtube-nocookie.com` y se cargan
recién al hacer clic (miniatura + botón de reproducción), para mantener la
carga inicial liviana y evitar reproducción automática.

Todo el contenido de precios, biografía, credenciales y datos de contacto
está marcado explícitamente como **placeholder** en el propio sitio, a la
espera de la información definitiva del cliente.

## Estructura de carpetas

```
recreo-bienestar/
├── index.html          Landing page pública (12 secciones)
├── login.html           Pantalla de acceso de demostración
├── miembros.html         Área de miembras simulada
├── favicon.svg           Ícono del sitio (monograma de marca)
├── css/
│   └── style.css         Sistema de diseño: tokens de color/tipografía/espaciado,
│                         layout, componentes y breakpoints responsivos
├── js/
│   └── main.js            Comportamiento: menú móvil, acordeón de FAQ,
│                         reproducción diferida de video, modal de contenido
│                         bloqueado, filtro de categorías, formularios de demo
└── README.md
```

No hay carpetas de build, dependencias ni assets generados: todo el
repositorio son archivos fuente listos para servir tal cual.

## Tecnologías utilizadas

- **HTML5** semántico (sin frameworks)
- **CSS3** plano, con variables nativas (custom properties), Grid y Flexbox
- **JavaScript vanilla** (ES5/ES6 básico, sin librerías ni bundlers)
- **YouTube (modo privado, `-nocookie`)** para los videos incrustados
- Sin CDN, sin Node, sin paso de build: el sitio se ejecuta directamente
  desde los archivos fuente

## Vista previa local

No requiere instalación de dependencias. Alcanza con cualquier servidor
estático simple. Por ejemplo, desde la raíz del proyecto:

```bash
# Python 3
python3 -m http.server 8000

# Node (si preferís usar npx)
npx serve .
```

Luego abrí `http://localhost:8000` en el navegador.

> Abrir los archivos `.html` directamente con `file://` también funciona
> para una revisión rápida, aunque se recomienda usar un servidor local para
> evitar restricciones de navegador con módulos y rutas relativas.

## Compatibilidad de navegadores

Probado en las últimas versiones de:

- Chrome / Edge (Chromium)
- Firefox
- Safari (macOS e iOS)

El sitio usa CSS Grid, Flexbox, custom properties y `<template>`/`fetch`
implícitos del navegador — todas features con soporte amplio en navegadores
modernos. No se usan polyfills ni transpilación; no está pensado para
Internet Explorer.

## Despliegue en cualquier servidor estático

El sitio es 100% estático: cualquier hosting que sirva archivos HTML/CSS/JS
tal cual funciona sin configuración adicional (Nginx, Apache, Netlify,
Vercel, GitHub Pages, S3 + CloudFront, etc.).

Pasos generales:

1. Copiar el contenido del repositorio a la raíz pública del servidor
   (o a la carpeta que el hosting use como raíz estática).
2. Confirmar que `index.html` se sirva como documento por defecto.
3. Servir todo bajo HTTPS (los embeds de YouTube y buenas prácticas de SEO
   lo requieren).
4. Antes de publicar en el dominio definitivo, completar en `index.html`,
   `login.html` y `miembros.html`:
   - Metaetiquetas `og:url` / `canonical` con el dominio final (se dejaron
     sin valor a propósito para no atar el repositorio a ningún dominio de
     prueba).
   - Los placeholders de biografía de Carla, precios de membresía y datos
     de contacto.
5. No se requiere ninguna configuración especial de servidor (rewrites,
   redirects, headers) más allá de servir los archivos estáticos; cada
   página es un archivo `.html` independiente con su propia URL.

## Funcionalidades futuras

Este repositorio es la base visual y estructural de la plataforma. El
frontend está organizado para que estas funcionalidades se puedan integrar
más adelante sin rehacer el diseño:

- Autenticación real de miembras
- Integración de pagos / cobro de membresías
- Dashboard de miembra con datos reales
- Seguimiento de progreso persistente
- Favoritos
- Listas de reproducción personalizadas
- Buscador de contenido
- Categorías dinámicas (actualmente el filtro es estático, en el cliente)
- Panel de administración para Carla (carga de videos, precios, contenido)
- Aplicación móvil
- Notificaciones push

---

Demostración funcional — no incluye backend, autenticación real, pagos ni
base de datos.
