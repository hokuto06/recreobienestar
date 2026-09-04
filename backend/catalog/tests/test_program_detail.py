from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalog.models import Category, Program
from catalog.services import record_video_view
from memberships.models import MembershipPlan

from .factories import make_video

User = get_user_model()


class ProgramDetailTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.program = Program.objects.create(name='Reset 21 días')
        self.video1 = make_video(
            self.category, title='Día 1', access_level='free',
            program=self.program, display_order=1,
        )
        self.video2 = make_video(
            self.category, title='Día 2', access_level='free',
            program=self.program, display_order=2,
        )

    def _url(self):
        return reverse('catalog:program_detail', args=[self.program.slug])

    def test_program_page_lists_its_videos_in_order(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertLess(content.index('Día 1'), content.index('Día 2'))

    def test_program_page_excludes_other_programs_videos(self):
        other_program = Program.objects.create(name='Otro programa')
        make_video(self.category, title='No debería aparecer', program=other_program)
        resp = self.client.get(self._url())
        self.assertNotContains(resp, 'No debería aparecer')

    def test_program_page_excludes_unpublished_videos(self):
        make_video(
            self.category, title='Borrador', program=self.program, is_published=False,
        )
        resp = self.client.get(self._url())
        self.assertNotContains(resp, 'Borrador')

    def test_inactive_program_404s(self):
        self.program.is_active = False
        self.program.save()
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_locked_video_in_program_shown_as_locked(self):
        MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        # Distinct YouTube URL from video2's (both default to the same one
        # otherwise) — video2 stays legitimately unlocked on this same
        # page, so its thumbnail must not accidentally satisfy this check.
        self.video1.youtube_url = 'https://youtu.be/jNQXAC9IVRw'
        self.video1.access_level = 'plan1'
        self.video1.save()
        resp = self.client.get(self._url())
        content = resp.content.decode()
        self.assertNotIn(self.video1.youtube_video_id, content)

    def test_progress_summary_for_member(self):
        user = User.objects.create_user(username='progu', password='x')
        record_video_view(user, self.video1)
        from catalog.models import VideoProgress
        VideoProgress.objects.get(user=user, video=self.video1).mark_completed()
        self.client.force_login(user)
        resp = self.client.get(self._url())
        self.assertContains(resp, '1 de 2 videos completados')

    def test_empty_program_shows_empty_state(self):
        empty_program = Program.objects.create(name='Vacío')
        resp = self.client.get(reverse('catalog:program_detail', args=[empty_program.slug]))
        self.assertContains(resp, 'Todavía no hay videos publicados')

    def test_video_detail_links_to_program(self):
        resp = self.client.get(reverse('catalog:video_detail', args=[self.video1.slug]))
        self.assertContains(resp, reverse('catalog:program_detail', args=[self.program.slug]))

    def test_next_video_navigation_within_program(self):
        resp = self.client.get(reverse('catalog:video_detail', args=[self.video1.slug]))
        self.assertContains(resp, reverse('catalog:video_detail', args=[self.video2.slug]))

    def test_program_page_query_count_does_not_scale_with_video_count(self):
        from catalog.models import Video

        def count_for(n):
            Video.objects.filter(program=self.program).delete()
            for i in range(n):
                make_video(
                    self.category, title=f'Video {i}', access_level='free',
                    program=self.program, display_order=i,
                )
            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.get(self._url())
            self.assertEqual(resp.status_code, 200)
            return len(ctx.captured_queries)

        small, large = count_for(3), count_for(10)
        self.assertEqual(
            small, large,
            f'Program page query count should not grow with video count (N+1?): {small} vs {large}.',
        )
