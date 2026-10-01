"""
Sets SiteSettings.carla_photo from an image file on disk — the one-time
seed for the "Sobre Carla" photo. After that, Carla replaces it from the
Admin (Configuración del sitio → Foto de Carla).

Deliberately a management command, not a data migration: uploaded media is
kept out of git (backend/media/ is gitignored), so the source image is not
in the repo and a migration would have nothing to read. The file is saved
through the default storage exactly like an Admin upload, so it lands in
MEDIA_ROOT/site/carla/ (the recreo_media volume in production).

The source image must live OUTSIDE the repo root — the repo root is
rsynced into the static site, so an image left there would be published.

Usage: python manage.py set_carla_photo /path/to/carla.jpeg
"""
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, UnidentifiedImageError

from site_content.models import SiteSettings


class Command(BaseCommand):
    help = 'Sets the "Sobre Carla" photo (SiteSettings.carla_photo) from an image file.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Path to the image file (JPEG/PNG/WebP).')

    def handle(self, *args, path, **options):
        source = Path(path)
        if not source.is_file():
            raise CommandError(f'No such file: {source}')
        try:
            with Image.open(source) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise CommandError(f'Not a readable image: {source} ({exc})')

        settings_row = SiteSettings.load()
        previous = settings_row.carla_photo.name if settings_row.carla_photo else None
        with source.open('rb') as fh:
            settings_row.carla_photo.save(source.name, File(fh), save=True)

        self.stdout.write(self.style.SUCCESS(
            f'carla_photo set: {settings_row.carla_photo.name} -> {settings_row.carla_photo.url}'
        ))
        if previous:
            self.stdout.write(f'(previous file left in storage: {previous})')
