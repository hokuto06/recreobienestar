"""Small shared test helpers — not a full factory library, just enough to
stop every new test module from re-typing the same Video.objects.create()
boilerplate (a real YouTube-shaped URL, is_published=True by default)."""
from catalog.models import Video

_DEFAULT_YOUTUBE_URL = 'https://youtu.be/dQw4w9WgXcQ'


def make_video(category, title, access_level='free', is_published=True, **kwargs):
    kwargs.setdefault('youtube_url', _DEFAULT_YOUTUBE_URL)
    return Video.objects.create(
        title=title, category=category, access_level=access_level,
        is_published=is_published, **kwargs,
    )
