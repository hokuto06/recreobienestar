"""
Storage for files that must NEVER be publicly reachable — Offering
deliverables (paid PDFs).

MEDIA_ROOT is public: nginx proxies every /media/<path> to Django's
serve_media (config/urls.py), which serves any file under MEDIA_ROOT to
anyone. So paid files live in a separate directory, PRIVATE_MEDIA_ROOT,
that nothing serves:
  - it's outside MEDIA_ROOT, and serve_media's safe_join can't climb out
    of MEDIA_ROOT (../ is rejected);
  - in production it's its own Docker volume (recreo_private_media),
    mounted ONLY into recreo-django — recreo-nginx has no view of it;
  - this storage has no URL: url() returns None, so nothing can render a
    link to the raw file by accident.
The only way out is site_content.public_views.offering_download, which
checks for a COMPLETED purchase first.

The location is read from settings on every access (not frozen at import),
so tests can point it at a temp dir with override_settings.
"""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None

    def url(self, name):
        # Never a public URL. (FileSystemStorage would raise ValueError here,
        # which crashes the Admin's file widget; None just means "no link".)
        return None
