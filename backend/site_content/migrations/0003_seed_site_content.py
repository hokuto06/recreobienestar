# Data migration (Phase 3.7): seeds SiteSettings with Carla's approved
# copy — her real bio, contact email, Instagram and podcast links — so the
# home page stops showing the "Biografía completa: próximamente." /
# "A confirmar" fallbacks baked into index.html.
#
# Seed-if-empty, field by field: any field Carla (or a previous migration)
# already populated from the Admin is left untouched. Only fields that are
# still blank get the approved value. This is deliberately NOT a blind
# get_or_create-and-overwrite, so re-running this on a database where
# someone already edited SiteSettings from the Admin can never clobber
# their edit.
from django.db import migrations

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
CONTACT_EMAIL = "recreobienestar@gmail.com"
INSTAGRAM_URL = "https://www.instagram.com/recreobienestar/"
PODCAST_NAME = "El éxito del cuerpo"
PODCAST_URL = "https://open.spotify.com/show/0Yrd2DLH8MvpOGrCPAdZ9s"

SEED_VALUES = {
    'carla_bio': CARLA_BIO,
    'carla_bio_highlight': CARLA_BIO_HIGHLIGHT,
    'contact_email': CONTACT_EMAIL,
    'instagram_url': INSTAGRAM_URL,
    'podcast_name': PODCAST_NAME,
    'podcast_url': PODCAST_URL,
}


def seed_site_content(apps, schema_editor):
    SiteSettings = apps.get_model('site_content', 'SiteSettings')
    obj, _ = SiteSettings.objects.get_or_create(pk=1)
    changed = False
    for field, value in SEED_VALUES.items():
        if not getattr(obj, field):
            setattr(obj, field, value)
            changed = True
    if changed:
        obj.save()


def noop_reverse(apps, schema_editor):
    # Deliberately a no-op: reversing this migration must never delete
    # real content Carla may have since edited from the Admin.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('site_content', '0002_sitesettings_carla_bio_highlight'),
    ]

    operations = [
        migrations.RunPython(seed_site_content, noop_reverse),
    ]
