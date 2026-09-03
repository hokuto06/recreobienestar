# Data migration (Phase 3.8): seeds 5 provisional example testimonials so
# the new "Reseñas" carousel on the home page has content to show from day
# one, instead of an empty section, before Carla replaces them with real
# reviews from the Admin.
#
# Seed-if-empty, table-wide (not field-by-field like 0003_seed_site_content
# — Testimonial isn't a singleton, so the equivalent safe check is "only
# seed if nobody has created any row yet"). Once Carla adds, edits or
# deletes a single testimonial from the Admin, this migration becomes a
# permanent no-op on that database — it will never re-run, duplicate rows,
# or clobber her content.
from django.db import migrations

TESTIMONIALS = [
    {
        'author_name': 'María Fernández',
        'text': (
            'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '
            'Desde que empecé con Columna Sana noto muchísima diferencia '
            'en mi postura y en cómo sostengo el cuerpo día a día.'
        ),
        'rating': 5,
        'display_order': 1,
    },
    {
        'author_name': 'Julián Rossi',
        'text': (
            'Sed do eiusmod tempor incididunt ut labore et dolore magna '
            'aliqua. Las sesiones son claras, cercanas y se nota el '
            'cuidado en cada detalle de la propuesta.'
        ),
        'rating': 5,
        'display_order': 2,
    },
    {
        'author_name': 'Carolina Méndez',
        'text': (
            'Ut enim ad minim veniam, quis nostrud exercitation ullamco '
            'laboris nisi ut aliquip ex ea commodo consequat. Recomiendo '
            'la plataforma a quien busque empezar de a poco.'
        ),
        'rating': 4,
        'display_order': 3,
    },
    {
        'author_name': 'Diego Paredes',
        'text': (
            'Duis aute irure dolor in reprehenderit in voluptate velit '
            'esse cillum dolore eu fugiat nulla pariatur. Un antes y un '
            'después en mi rutina de movimiento consciente.'
        ),
        'rating': 5,
        'display_order': 4,
    },
    {
        'author_name': 'Lucía Ferreyra',
        'text': (
            'Excepteur sint occaecat cupidatat non proident, sunt in '
            'culpa qui officia deserunt mollit anim id est laborum. Muy '
            'buena guía y acompañamiento en cada práctica.'
        ),
        'rating': 4,
        'display_order': 5,
    },
]


def seed_testimonials(apps, schema_editor):
    Testimonial = apps.get_model('site_content', 'Testimonial')
    if Testimonial.objects.exists():
        return
    for data in TESTIMONIALS:
        Testimonial.objects.get_or_create(author_name=data['author_name'], defaults=data)


def noop_reverse(apps, schema_editor):
    # Deliberately a no-op: reversing this migration must never delete
    # real reviews Carla may have since edited or replaced from the Admin.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('site_content', '0004_testimonial'),
    ]

    operations = [
        migrations.RunPython(seed_testimonials, noop_reverse),
    ]
