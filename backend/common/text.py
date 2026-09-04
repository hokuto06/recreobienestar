"""Slug generation and YouTube URL parsing helpers, shared by models that
need them (Video, MembershipPlan, Category, Program)."""
import re

from django.utils.text import slugify

# Matches the 11-character YouTube video ID out of the common URL shapes:
#   https://www.youtube.com/watch?v=VIDEOID
#   https://youtu.be/VIDEOID
#   https://www.youtube.com/embed/VIDEOID
#   https://www.youtube.com/shorts/VIDEOID
# with optional extra query params/trailing slash, and optional scheme/www.
YOUTUBE_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?'
    r'(?:'
    r'youtube\.com/watch\?(?:[^#]*&)?v=(?P<id1>[A-Za-z0-9_-]{11})'
    r'|youtu\.be/(?P<id2>[A-Za-z0-9_-]{11})'
    r'|youtube\.com/embed/(?P<id3>[A-Za-z0-9_-]{11})'
    r'|youtube\.com/shorts/(?P<id4>[A-Za-z0-9_-]{11})'
    r')'
    r'(?:[/?&#].*)?$'
)


def extract_youtube_id(url):
    """Return the 11-character video ID from a YouTube URL, or None if the
    URL doesn't match a recognized YouTube URL shape."""
    if not url:
        return None
    match = YOUTUBE_URL_RE.match(url.strip())
    if not match:
        return None
    return next(g for g in match.groups() if g is not None)


def generate_unique_slug(instance, source_text, slug_field_name='slug'):
    """Slugify source_text and disambiguate against existing rows of the
    same model (excluding the instance itself), appending -2, -3, ... on
    collision. Used from model save() methods when slug is left blank."""
    model = type(instance)
    base_slug = slugify(source_text)[:220] or 'item'
    slug = base_slug
    counter = 2
    qs = model.objects.exclude(pk=instance.pk) if instance.pk else model.objects.all()
    filter_kwargs = {slug_field_name: slug}
    while qs.filter(**filter_kwargs).exists():
        slug = f'{base_slug}-{counter}'
        filter_kwargs[slug_field_name] = slug
        counter += 1
    return slug
