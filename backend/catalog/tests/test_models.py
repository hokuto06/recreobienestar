from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import Category, Video


class YoutubeExtractionTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')

    def _make_video(self, url):
        return Video.objects.create(
            title='Clase de prueba',
            youtube_url=url,
            category=self.category,
        )

    def test_extracts_id_from_watch_url(self):
        video = self._make_video('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(video.youtube_video_id, 'dQw4w9WgXcQ')

    def test_extracts_id_from_short_url(self):
        video = self._make_video('https://youtu.be/dQw4w9WgXcQ')
        self.assertEqual(video.youtube_video_id, 'dQw4w9WgXcQ')

    def test_extracts_id_from_embed_url(self):
        video = self._make_video('https://www.youtube.com/embed/dQw4w9WgXcQ')
        self.assertEqual(video.youtube_video_id, 'dQw4w9WgXcQ')

    def test_extracts_id_with_extra_query_params(self):
        video = self._make_video('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s')
        self.assertEqual(video.youtube_video_id, 'dQw4w9WgXcQ')

    def test_invalid_youtube_url_rejected_on_full_clean(self):
        video = Video(
            title='URL inválida',
            youtube_url='https://vimeo.com/123456',
            category=self.category,
        )
        with self.assertRaises(ValidationError):
            video.full_clean()

    def test_non_youtube_domain_rejected(self):
        video = Video(
            title='No es youtube',
            youtube_url='https://example.com/watch?v=dQw4w9WgXcQ',
            category=self.category,
        )
        with self.assertRaises(ValidationError):
            video.full_clean()


class VideoSlugAndDefaultsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Meditación')

    def test_slug_auto_generated_from_title(self):
        video = Video.objects.create(
            title='Clase de respiración consciente',
            youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category,
        )
        self.assertEqual(video.slug, 'clase-de-respiracion-consciente')

    def test_duplicate_titles_get_distinct_slugs(self):
        v1 = Video.objects.create(
            title='Clase básica', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category,
        )
        v2 = Video.objects.create(
            title='Clase básica', youtube_url='https://youtu.be/jNQXAC9IVRw',
            category=self.category,
        )
        self.assertNotEqual(v1.slug, v2.slug)

    def test_unpublished_by_default(self):
        video = Video.objects.create(
            title='Borrador', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category,
        )
        self.assertFalse(video.is_published)
