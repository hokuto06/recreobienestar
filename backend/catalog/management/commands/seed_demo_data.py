"""
Development-only seed data: enough of everything to click through
registration, login, the dashboard, and every membership-access scenario
by hand, without inventing real member data.

    docker exec recreo-django python manage.py seed_demo_data

Safe to run more than once — everything is get_or_create'd, keyed on
stable slugs/usernames, so re-running just confirms the same state rather
than duplicating rows.

⚠️  DEV-ONLY CREDENTIALS — never use these in production. They're fixed
and printed in this file on purpose so anyone can find and reset them.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import Category, Program, Video
from memberships.models import MembershipPlan, Subscription

User = get_user_model()

DEV_PASSWORD = 'RecreoDemo#2026'  # noqa: S105 — intentionally public, dev-only, documented.

DEMO_USERS = [
    # (username, email, plan_tier or None, subscription_status, ends_at_offset_days)
    ('demo_free', 'demo_free@example.com', None, None, None),
    ('demo_activo', 'demo_activo@example.com', 'plan1', 'active', +30),
    ('demo_vencido', 'demo_vencido@example.com', 'plan1', 'active', -5),
]


class Command(BaseCommand):
    help = 'Creates development-only demo users, plans, categories, and videos.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            f'Seeding development-only demo data. All demo users share the '
            f'password: {DEV_PASSWORD} — DO NOT use this in production.'
        ))

        category, _ = Category.objects.get_or_create(
            slug='movimiento-consciente',
            defaults={'name': 'Movimiento consciente', 'description': 'Sesiones guiadas de movimiento.'},
        )
        program, _ = Program.objects.get_or_create(
            slug='reset-21-dias', defaults={'name': 'Reset 21 días', 'description': 'Programa de 21 días.'},
        )

        plan1, _ = MembershipPlan.objects.get_or_create(
            tier='plan1',
            defaults={'name': 'Plan Esencial', 'price': 5000, 'currency': 'ARS', 'is_active': True},
        )
        MembershipPlan.objects.get_or_create(
            tier='plan2',
            defaults={'name': 'Plan Premium', 'price': 9000, 'currency': 'ARS', 'is_active': True},
        )

        free_video, _ = Video.objects.get_or_create(
            slug='bienvenida-gratuita',
            defaults=dict(
                title='Bienvenida (video gratuito)',
                short_description='Una probada de Recreo Bienestar, sin membresía.',
                youtube_url='https://youtu.be/dQw4w9WgXcQ',
                category=category, program=program,
                access_level='free', is_published=True, is_featured=True,
                publication_date=timezone.now(),
            ),
        )
        Video.objects.get_or_create(
            slug='sesion-plan-esencial',
            defaults=dict(
                title='Sesión exclusiva — Plan Esencial',
                short_description='Contenido disponible solo para el Plan Esencial.',
                youtube_url='https://youtu.be/jNQXAC9IVRw',
                category=category, program=program,
                access_level='plan1', is_published=True,
                publication_date=timezone.now(),
            ),
        )
        Video.objects.get_or_create(
            slug='borrador-proxima-sesion',
            defaults=dict(
                title='(Borrador) Próxima sesión',
                short_description='Sin publicar todavía — visible solo para staff.',
                youtube_url='https://youtu.be/dQw4w9WgXcQ',
                category=category, access_level='free', is_published=False,
            ),
        )

        for username, email, tier, status, offset_days in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username, defaults={'email': email})
            if created:
                user.set_password(DEV_PASSWORD)
                user.save()
            if tier and status:
                plan = plan1 if tier == 'plan1' else MembershipPlan.objects.get(tier=tier)
                Subscription.objects.get_or_create(
                    user=user, plan=plan,
                    defaults={
                        'status': status,
                        'ends_at': timezone.now() + timedelta(days=offset_days),
                    },
                )
            self.stdout.write(f'  user={username} email={email} plan={tier or "free"}')

        self.stdout.write(self.style.SUCCESS(
            'Demo data ready: 1 free user (demo_free), 1 active member (demo_activo), '
            '1 expired member (demo_vencido), 2 plans, 1 free video, 1 plan1 video, '
            '1 unpublished draft.'
        ))
