"""
Applies Carla's approved "Membresía Columna Sana" content (Phase 3.7) to
the three membership plan cards. plan1/plan2 already existed (Phase 1)
and keep their tier/FK identity — any existing Subscription rows pointing
at them are untouched, only the presentational/commercial fields below
are updated to the approved copy. plan3 is new (see
common/choices.PlanTier — extended this phase) and is created fresh.

Deliberately a management command, not a data migration — see
site_content/management/commands/seed_phase37_content.py's docstring for
why: Django's test runner applies every migration to the test database,
so a data migration here would create real MembershipPlan rows with
tier='plan1'/'plan2' that collide (tier is unique) with the many existing
tests that create their own plan1/plan2 fixtures in setUp().

Idempotent and safe to re-run. Never touches Subscription rows.

Usage: python manage.py seed_columna_sana_plans
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from memberships.models import MembershipPlan

PLANS = [
    {
        'tier': 'plan1',
        'name': 'Plan Lumbar',
        'subtitle': 'Alivio Lumbar',
        'description': (
            'Módulo 1: Dolor Lumbar Crónico\n'
            '3 sesiones de muestra\n'
            '2 clips breves\n'
            'Reset Express'
        ),
        'badge': '',
        'visual_variant': 'default',
        'cta_label': 'Empezar alivio',
        'price': Decimal('18000'),
        'display_order': 1,
    },
    {
        'tier': 'plan2',
        'name': 'Plan Profundo',
        'subtitle': 'Alineación Profunda',
        'description': (
            'Módulos 1, 2 y 3: Lumbar y Dorsal\n'
            '5 sesiones de muestra\n'
            '4 clips breves\n'
            'Hipopresivos\n'
            'Reset Express'
        ),
        'badge': 'Más recomendado',
        'visual_variant': 'highlighted',
        'cta_label': 'Empezar alineación',
        'price': Decimal('25000'),
        'display_order': 2,
    },
    {
        'tier': 'plan3',
        'name': 'Plan Integrador',
        'subtitle': 'Membresía Completa',
        'description': (
            'Módulos 1 a 6: Columna Completa\n'
            '8 sesiones de muestra\n'
            '6 clips breves\n'
            'Hipopresivos\n'
            'Reset Express\n'
            'Seguimiento Mensual 1-a-1'
        ),
        'badge': '',
        'visual_variant': 'premium',
        'cta_label': 'Empezar todo',
        'price': Decimal('45000'),
        'display_order': 3,
    },
]


class Command(BaseCommand):
    help = "Applies Carla's approved Membresía Columna Sana content to the 3 membership plans."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        for data in PLANS:
            obj, created = MembershipPlan.objects.get_or_create(
                tier=data['tier'],
                defaults={
                    'name': data['name'],
                    'subtitle': data['subtitle'],
                    'description': data['description'],
                    'badge': data['badge'],
                    'visual_variant': data['visual_variant'],
                    'cta_label': data['cta_label'],
                    'price': data['price'],
                    'currency': 'ARS',
                    'duration_days': 30,
                    'display_order': data['display_order'],
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
            else:
                obj.name = data['name']
                obj.subtitle = data['subtitle']
                obj.description = data['description']
                obj.badge = data['badge']
                obj.visual_variant = data['visual_variant']
                obj.cta_label = data['cta_label']
                obj.price = data['price']
                obj.currency = 'ARS'
                obj.duration_days = 30
                obj.display_order = data['display_order']
                obj.save()
                updated_count += 1
        self.stdout.write(self.style.SUCCESS(
            f'MembershipPlan: {created_count} created, {updated_count} updated.'
        ))
