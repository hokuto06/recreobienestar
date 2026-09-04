from django.core.exceptions import ValidationError

from .text import YOUTUBE_URL_RE


def validate_youtube_url(value):
    if not YOUTUBE_URL_RE.match(value.strip()):
        raise ValidationError(
            '%(value)s no es una URL de YouTube válida. Usá un enlace de '
            'youtube.com/watch?v=..., youtu.be/..., youtube.com/embed/... '
            'o youtube.com/shorts/...',
            params={'value': value},
        )
