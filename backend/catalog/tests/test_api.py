from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Category, Program, Video
from memberships.models import MembershipPlan, Subscription

User = get_user_model()


class CategoryApiTests(APITestCase):
    def setUp(self):
        self.active = Category.objects.create(name='Yoga', is_active=True)
        self.inactive = Category.objects.create(name='Archivada', is_active=False)

    def test_only_active_categories_returned(self):
        resp = self.client.get(reverse('category-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [c['name'] for c in resp.data['results']]
        self.assertIn('Yoga', names)
        self.assertNotIn('Archivada', names)

    def test_write_methods_not_allowed(self):
        url = reverse('category-list')
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(url, {'name': 'Hackeada'})
            self.assertEqual(
                resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED,
                f'{verb.upper()} should not be allowed on {url}',
            )
        self.assertFalse(Category.objects.filter(name='Hackeada').exists())


class ProgramApiTests(APITestCase):
    def setUp(self):
        self.active = Program.objects.create(name='Programa Activo', is_active=True)
        self.inactive = Program.objects.create(name='Programa Archivado', is_active=False)

    def test_only_active_programs_returned(self):
        resp = self.client.get(reverse('program-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [p['name'] for p in resp.data['results']]
        self.assertIn('Programa Activo', names)
        self.assertNotIn('Programa Archivado', names)

    def test_write_methods_not_allowed(self):
        resp = self.client.post(reverse('program-list'), {'name': 'Hackeado'})
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class VideoApiTests(APITestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Pilates')
        self.other_category = Category.objects.create(name='Meditación')
        self.program = Program.objects.create(name='Reset 21 días')

        self.published = Video.objects.create(
            title='Clase publicada', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, program=self.program,
            is_published=True, access_level='free',
        )
        self.unpublished = Video.objects.create(
            title='Borrador', youtube_url='https://youtu.be/jNQXAC9IVRw',
            category=self.category, is_published=False, access_level='free',
        )
        self.plan1_video = Video.objects.create(
            title='Clase plan 1', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.other_category, is_published=True, access_level='plan1',
        )

    # ── visibility ───────────────────────────────────────────────────
    def test_unpublished_video_not_in_list(self):
        resp = self.client.get(reverse('video-list'))
        slugs = [v['slug'] for v in resp.data['results']]
        self.assertNotIn(self.unpublished.slug, slugs)
        self.assertIn(self.published.slug, slugs)

    def test_unpublished_video_detail_returns_404(self):
        resp = self.client.get(reverse('video-detail', args=[self.unpublished.slug]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_published_video_detail_returns_200(self):
        resp = self.client.get(reverse('video-detail', args=[self.published.slug]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['slug'], self.published.slug)

    def test_detail_exposes_full_description_list_does_not(self):
        self.published.full_description = 'Descripción completa secreta-ish'
        self.published.save()

        list_resp = self.client.get(reverse('video-list'))
        list_item = next(v for v in list_resp.data['results'] if v['slug'] == self.published.slug)
        self.assertNotIn('full_description', list_item)

        detail_resp = self.client.get(reverse('video-detail', args=[self.published.slug]))
        self.assertIn('full_description', detail_resp.data)

    def test_internal_fields_not_exposed(self):
        resp = self.client.get(reverse('video-detail', args=[self.published.slug]))
        for field in ('is_published', 'created_at', 'updated_at', 'youtube_url'):
            self.assertNotIn(field, resp.data)

    # ── access_level_display (Phase 3: home page dynamic content) ─────
    def test_access_level_display_is_human_readable(self):
        resp = self.client.get(reverse('video-detail', args=[self.published.slug]))
        self.assertEqual(resp.data['access_level_display'], 'Gratuito')

    # ── filtering ────────────────────────────────────────────────────
    def test_filter_by_category(self):
        resp = self.client.get(reverse('video-list'), {'category': self.category.slug})
        slugs = [v['slug'] for v in resp.data['results']]
        self.assertIn(self.published.slug, slugs)
        self.assertNotIn(self.plan1_video.slug, slugs)

    def test_filter_by_program(self):
        resp = self.client.get(reverse('video-list'), {'program': self.program.slug})
        slugs = [v['slug'] for v in resp.data['results']]
        self.assertIn(self.published.slug, slugs)
        self.assertNotIn(self.plan1_video.slug, slugs)

    def test_filter_by_access_level(self):
        resp = self.client.get(reverse('video-list'), {'access_level': 'plan1'})
        slugs = [v['slug'] for v in resp.data['results']]
        self.assertIn(self.plan1_video.slug, slugs)
        self.assertNotIn(self.published.slug, slugs)

    def test_filter_by_is_featured(self):
        self.published.is_featured = True
        self.published.save()
        resp = self.client.get(reverse('video-list'), {'is_featured': 'true'})
        slugs = [v['slug'] for v in resp.data['results']]
        self.assertIn(self.published.slug, slugs)
        self.assertNotIn(self.plan1_video.slug, slugs)

    # ── pagination ───────────────────────────────────────────────────
    def test_list_is_paginated(self):
        resp = self.client.get(reverse('video-list'))
        self.assertIn('count', resp.data)
        self.assertIn('results', resp.data)

    # ── read-only enforcement ────────────────────────────────────────
    def test_write_methods_not_allowed_on_list(self):
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(reverse('video-list'), {'title': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_write_methods_not_allowed_on_detail(self):
        url = reverse('video-detail', args=[self.published.slug])
        for verb in ('put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(url, {'title': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.published.refresh_from_db()
        self.assertEqual(self.published.title, 'Clase publicada')


class VideoApiAccessControlTests(APITestCase):
    """The API must enforce the exact same paywall as the HTML views —
    these guard against the API becoming a bypass for locked content."""

    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Pilates')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        self.locked_video = Video.objects.create(
            title='Exclusivo', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level='plan1',
            # No explicit thumbnail_url — thumbnail_display_url falls back
            # to a URL derived from youtube_video_id, which is exactly
            # what must NOT reach an unauthorized caller.
        )
        self.free_video = Video.objects.create(
            title='Libre', youtube_url='https://youtu.be/jNQXAC9IVRw',
            category=self.category, is_published=True, access_level='free',
        )

    def test_locked_video_detail_returns_403_for_anonymous(self):
        resp = self.client.get(reverse('video-detail', args=[self.locked_video.slug]))
        self.assertEqual(resp.status_code, 403)

    def test_locked_video_detail_body_has_no_youtube_id_or_description(self):
        resp = self.client.get(reverse('video-detail', args=[self.locked_video.slug]))
        body = str(resp.content)
        self.assertNotIn(self.locked_video.youtube_video_id, body)
        self.assertNotIn('youtube_video_id', body)
        self.assertNotIn('full_description', body)

    def test_locked_video_thumbnail_hidden_in_list_for_anonymous(self):
        resp = self.client.get(reverse('video-list'))
        item = next(v for v in resp.data['results'] if v['slug'] == self.locked_video.slug)
        self.assertIsNone(item['thumbnail'])

    def test_free_video_thumbnail_visible_in_list_for_anonymous(self):
        resp = self.client.get(reverse('video-list'))
        item = next(v for v in resp.data['results'] if v['slug'] == self.free_video.slug)
        self.assertIsNotNone(item['thumbnail'])
        self.assertIn(self.free_video.youtube_video_id, item['thumbnail'])

    def test_locked_video_accessible_via_api_to_active_member(self):
        user = User.objects.create_user(username='miembra_api', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now + timedelta(days=10),
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('video-detail', args=[self.locked_video.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['youtube_video_id'], self.locked_video.youtube_video_id)

    def test_locked_video_thumbnail_visible_in_list_to_active_member(self):
        user = User.objects.create_user(username='miembra_api2', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now + timedelta(days=10),
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('video-list'))
        item = next(v for v in resp.data['results'] if v['slug'] == self.locked_video.slug)
        self.assertIsNotNone(item['thumbnail'])

    def test_locked_video_still_403_for_logged_in_user_without_plan(self):
        user = User.objects.create_user(username='sin_plan', password='x')
        self.client.force_login(user)
        resp = self.client.get(reverse('video-detail', args=[self.locked_video.slug]))
        self.assertEqual(resp.status_code, 403)

    def test_video_list_query_count_does_not_scale_with_video_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        user = User.objects.create_user(username='queryuser_api', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now + timedelta(days=10),
        )
        self.client.force_login(user)

        def count_for(n):
            Video.objects.all().delete()
            for i in range(n):
                Video.objects.create(
                    title=f'V{i}', youtube_url='https://youtu.be/dQw4w9WgXcQ',
                    category=self.category, is_published=True,
                    access_level='plan1' if i % 2 == 0 else 'free',
                )
            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.get(reverse('video-list'))
            self.assertEqual(resp.status_code, 200)
            return len(ctx.captured_queries)

        small, large = count_for(3), count_for(10)
        self.assertEqual(
            small, large,
            f'/api/videos/ query count should not grow with video count (N+1?): {small} vs {large}.',
        )
