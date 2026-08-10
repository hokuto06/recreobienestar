import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Favorite
from memberships.models import MembershipPlan

from .factories import make_video

User = get_user_model()


class ToggleFavoriteTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.user = User.objects.create_user(username='fav1', password='x')
        self.video = make_video(self.category, title='Sesión', access_level='free')
        self.url = reverse('catalog:toggle_favorite', args=[self.video.slug])

    def test_requires_login(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp.url)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_toggle_adds_then_removes(self):
        self.client.force_login(self.user)
        self.client.post(self.url)
        self.assertTrue(Favorite.objects.filter(user=self.user, video=self.video).exists())
        self.client.post(self.url)
        self.assertFalse(Favorite.objects.filter(user=self.user, video=self.video).exists())

    def test_json_response_when_requested(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['favorited'])
        self.assertEqual(data['video'], self.video.slug)

    def test_plain_post_redirects_without_json_accept_header(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_can_favorite_a_locked_video(self):
        """Bookmarking is independent of access — a member should be able
        to save a paid video for later even without an active plan yet."""
        MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        locked_video = make_video(self.category, title='Exclusivo', access_level='plan1')
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('catalog:toggle_favorite', args=[locked_video.slug]),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Favorite.objects.filter(user=self.user, video=locked_video).exists())

    def test_csrf_enforced_without_token(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        resp = csrf_client.post(self.url)
        self.assertEqual(resp.status_code, 403)


class FavoritesListViewTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.user = User.objects.create_user(username='fav2', password='x')
        self.video = make_video(self.category, title='Guardado', access_level='free')
        Favorite.objects.create(user=self.user, video=self.video)
        self.url = reverse('accounts:favorites')

    def test_requires_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_lists_favorited_video(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Guardado')

    def test_only_shows_own_favorites(self):
        other = User.objects.create_user(username='fav3', password='x')
        other_video = make_video(self.category, title='De otra persona', access_level='free')
        Favorite.objects.create(user=other, video=other_video)
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'De otra persona')

    def test_empty_state(self):
        empty_user = User.objects.create_user(username='fav4', password='x')
        self.client.force_login(empty_user)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Todavía no guardaste')

    def test_favorite_marker_shown_in_library(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('catalog:video_library'))
        self.assertContains(resp, 'favorite-mark')
