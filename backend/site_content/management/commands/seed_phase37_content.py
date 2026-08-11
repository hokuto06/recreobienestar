"""
Seeds Carla's approved Phase 3.7 content: her real biography and the
standalone paid programs/courses ("Offering" — see site_content/models.py
for why these are kept separate from MembershipPlan). Content approved by
Carla directly, not invented.

Deliberately a management command, NOT a data migration: Django's test
runner applies every migration (including RunPython ones) to build the
test database, so a data migration here would create real content rows
that collide with test fixtures using the same names/slugs (e.g. a test
creating its own "Curso Neuro Postural" Offering would hit this command's
same slug). A management command only ever runs when explicitly invoked
(deployment), leaving the test database exactly what the schema
migrations define — nothing more.

Idempotent and non-destructive, safe to re-run:
- SiteSettings bio fields are only set if currently BLANK, so a manual
  edit Carla already made in the Admin is never overwritten.
- Offerings are get_or_create'd by slug, then only their presentational
  fields are updated (never payment_url_ars/usd, which stay whatever an
  admin already set — this command never touches payment links).

Hipopresivos is deliberately NOT created here: the brief asks to
"preserve/add [it] if already represented in the Offering model", but it
wasn't (the table was empty before Phase 3.7) and no price was
specified — inventing one would violate "don't hardcode prices where a
backend model exists". Carla can add it from the Admin once she has a
price; it already appears as a benefit line inside two of the membership
cards (see seed_columna_sana_plans) so it isn't missing from the site
entirely in the meantime.

Usage: python manage.py seed_phase37_content
"""
from django.core.management.base import BaseCommand

from site_content.models import Offering, SiteSettings

CARLA_BIO = (
    "Desde pequeña estuve ligada al arte y siendo tan inquieta mi única "
    "meditación era moverme. Así me convertí en bailarina, egresé del ISA "
    "del Teatro Colón, participé en distintas compañías, tales como: el "
    "Ballet estable del Teatro Colón, el Ballet Argentino de la Plata, "
    "Ballet Preljocaj en Francia, fui solista en Cisne Negro Cia y la "
    "compañía nacional de Caxias do Sul Brasil, pasé por muchos escenarios "
    "pero el más grande fue el interno, dentro de mi SER, en donde "
    "investigo cada rincón con cada paso, con cada movimiento."
)

CARLA_BIO_HIGHLIGHT = (
    "ReCREO, me permite expresar mi esencia, une todo aquello que "
    "construí y todo aquello que se derrumbó para hoy ayudar a quienes "
    "quieran comenzar a recrearse."
)

OFFERINGS = [
    {
        'slug': 'programa-neuro-postural',
        'name': 'Programa Neuro-Postural',
        'description': 'Acompañamiento profundo de 4 meses.',
        'price': '220000',
        'display_order': 1,
    },
    {
        'slug': 'curso-neuro-postural',
        'name': 'Curso Neuro Postural',
        'description': 'La esencia del método de manera abreviada.',
        'price': '55000',
        'display_order': 2,
    },
    {
        'slug': 'reset-express',
        'name': 'Reset Express',
        'description': 'Pack de audios: Pausas para habitar la calma.',
        'price': '24000',
        'display_order': 3,
    },
    {
        'slug': 'bitacora-anti-estres',
        'name': 'Bitácora Anti-estrés',
        'description': 'Tu guía física para la regulación diaria y registro de expansión.',
        'price': '20000',
        'display_order': 4,
    },
]


class Command(BaseCommand):
    help = "Seeds Carla's approved Phase 3.7 bio and standalone programs/courses (Offering)."

    def handle(self, *args, **options):
        settings_obj = SiteSettings.load()
        changed = []
        if not settings_obj.carla_bio:
            settings_obj.carla_bio = CARLA_BIO
            changed.append('carla_bio')
        if not settings_obj.carla_bio_highlight:
            settings_obj.carla_bio_highlight = CARLA_BIO_HIGHLIGHT
            changed.append('carla_bio_highlight')
        if changed:
            settings_obj.save()
            self.stdout.write(self.style.SUCCESS(f'SiteSettings: seeded {", ".join(changed)}.'))
        else:
            self.stdout.write('SiteSettings: bio fields already set, left untouched.')

        created_count = 0
        updated_count = 0
        for data in OFFERINGS:
            obj, created = Offering.objects.get_or_create(
                slug=data['slug'],
                defaults={
                    'name': data['name'],
                    'description': data['description'],
                    'price': data['price'],
                    'currency': 'ARS',
                    'display_order': data['display_order'],
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
            else:
                obj.name = data['name']
                obj.description = data['description']
                obj.price = data['price']
                obj.display_order = data['display_order']
                obj.save()
                updated_count += 1
        self.stdout.write(self.style.SUCCESS(
            f'Offerings: {created_count} created, {updated_count} updated.'
        ))
