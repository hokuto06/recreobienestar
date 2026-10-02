"""
Backfills Video.poster — our own copy of each video's YouTube thumbnail,
shown on LOCKED cards instead of the img.youtube.com URL (which contains
the video id). See catalog/posters.py.

Safe to re-run: videos that already have a poster are skipped (including
ones Carla uploaded by hand) unless --force is given. A failed download is
reported and left for the next run; it never stops the others.

Usage:
    python manage.py fetch_video_posters            # every video missing a poster
    python manage.py fetch_video_posters --force    # re-download all (replaces existing posters)
    python manage.py fetch_video_posters --slug mi-video
"""
from django.core.management.base import BaseCommand

from catalog.models import Video
from catalog.posters import fetch_youtube_poster


class Command(BaseCommand):
    help = 'Downloads and stores our own poster image for videos that do not have one yet.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Re-download even if a poster exists.')
        parser.add_argument('--slug', help='Only this video.')

    def handle(self, *args, force=False, slug=None, **options):
        videos = Video.objects.order_by('id')
        if slug:
            videos = videos.filter(slug=slug)
        fetched = skipped_has_poster = skipped_no_id = failed = 0
        for video in videos:
            label = f'#{video.pk} "{video.title}"'
            if not video.youtube_video_id:
                skipped_no_id += 1
                self.stdout.write(f'skip   {label}: no YouTube id')
                continue
            if video.poster and not force:
                skipped_has_poster += 1
                self.stdout.write(f'skip   {label}: already has a poster')
                continue
            try:
                name = fetch_youtube_poster(video)
            except Exception as exc:
                failed += 1
                self.stderr.write(f'FAILED {label}: {type(exc).__name__}: {exc}')
                continue
            fetched += 1
            self.stdout.write(f'ok     {label} -> {name}')
        summary = (
            f'Posters: {fetched} fetched, {skipped_has_poster} already had one, '
            f'{skipped_no_id} without a YouTube id, {failed} failed.'
        )
        self.stdout.write(self.style.SUCCESS(summary) if not failed else self.style.WARNING(summary))
