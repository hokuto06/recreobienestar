from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Category, Program, Video


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
