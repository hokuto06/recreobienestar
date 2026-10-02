"""
Own-hosted poster images for videos (Video.poster).

A locked video's card must not show Video.thumbnail_display_url: without an
explicit thumbnail_url that is https://img.youtube.com/vi/<youtube_video_id>/
hqdefault.jpg, and the id in that URL is all anyone needs to watch the video
on YouTube. So we download that image ONCE, re-encode it, and store our own
copy under a random name (catalog.models.video_poster_upload_to). Locked
cards show only that copy.

Two ways a poster gets filled:
  - automatically, after a video with a YouTube id and no poster is saved
    (schedule_poster_fetch, called from Video.save);
  - in bulk, with `manage.py fetch_video_posters` (backfill; re-runnable).
Carla can replace it from the Admin at any time.

SAVING A VIDEO CAN'T BREAK because of this:
  - the download runs in transaction.on_commit — after the video row is
    already committed — so it can neither roll back nor block the save's
    transaction;
  - every failure (network, HTTP error, timeout, not an image, storage) is
    caught and logged, and registered robust=True as a second net;
  - the download has a short timeout (POSTER_FETCH_TIMEOUT, 4s) and a size
    cap, so an unreachable or slow YouTube costs at most a few seconds.
If it fails, the video just has no poster: locked cards keep the plain
tinted placeholder, and the backfill command can retry later.
"""
import logging
import urllib.request
from functools import partial
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

logger = logging.getLogger(__name__)

POSTER_FETCH_TIMEOUT = 4  # seconds
MAX_POSTER_BYTES = 2 * 1024 * 1024
_YOUTUBE_POSTER_URL = 'https://img.youtube.com/vi/{}/hqdefault.jpg'


def fetch_youtube_poster(video, timeout=POSTER_FETCH_TIMEOUT):
    """Downloads YouTube's thumbnail for `video` and stores our own copy in
    video.poster. Raises on any failure — callers decide whether to log or
    report. Re-encoding as JPEG drops whatever metadata the source had, so
    only the pixels are ever served."""
    request = urllib.request.Request(
        _YOUTUBE_POSTER_URL.format(video.youtube_video_id),
        headers={'User-Agent': 'RecreoBienestar/1.0 (poster backfill)'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_POSTER_BYTES + 1)
    if len(data) > MAX_POSTER_BYTES:
        raise ValueError(f'poster larger than {MAX_POSTER_BYTES} bytes')

    with Image.open(BytesIO(data)) as image:
        image = image.convert('RGB')
        output = BytesIO()
        image.save(output, format='JPEG', quality=85, optimize=True)

    from .models import Video

    video.poster.save('poster.jpg', ContentFile(output.getvalue()), save=False)
    # .update(), not .save(): don't re-run Video.save() (and its own poster
    # scheduling) or touch any other column for this.
    Video.objects.filter(pk=video.pk).update(poster=video.poster.name)
    return video.poster.name


def schedule_poster_fetch(video_id):
    """Called from Video.save(). Never raises."""
    if not getattr(settings, 'VIDEO_POSTER_AUTO_FETCH', True):
        return
    try:
        transaction.on_commit(partial(_fetch_after_commit, video_id), robust=True)
    except Exception:
        logger.exception('Could not schedule poster fetch for video %s — the video itself is saved', video_id)


def _fetch_after_commit(video_id):
    try:
        from .models import Video

        video = Video.objects.filter(pk=video_id).first()
        if video is None or video.poster or not video.youtube_video_id:
            return
        name = fetch_youtube_poster(video)
        logger.info('Poster fetched for video %s -> %s', video_id, name)
    except Exception:
        logger.warning(
            'Poster fetch failed for video %s — saved without a poster (locked cards show the '
            'placeholder; retry with `manage.py fetch_video_posters`)',
            video_id, exc_info=True,
        )
