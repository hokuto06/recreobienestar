"""
Shared choice enums.

Kept in one place (rather than duplicated on Video and MembershipPlan) so
the string values used to match a video's required access level against a
membership plan's tier can never silently drift apart.
"""
from django.db import models


class PlanTier(models.TextChoices):
    """The two initial paid membership plans. Free is not a plan — it's the
    absence of one, so it's intentionally not a member of this enum."""
    PLAN1 = 'plan1', 'Plan de membresía 1'
    PLAN2 = 'plan2', 'Plan de membresía 2'


class VideoAccessLevel(models.TextChoices):
    FREE = 'free', 'Gratuito'
    PLAN1 = PlanTier.PLAN1.value, PlanTier.PLAN1.label
    PLAN2 = PlanTier.PLAN2.value, PlanTier.PLAN2.label
    ALL_PAID = 'all_paid', 'Todos los planes pagos'


class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', 'Prueba'
    ACTIVE = 'active', 'Activa'
    PAST_DUE = 'past_due', 'Pago vencido'
    CANCELLED = 'cancelled', 'Cancelada'
    EXPIRED = 'expired', 'Expirada'


# Statuses that represent "currently entitled" states BEFORE the expiry
# check (Subscription.is_expired) is applied on top.
#
# CANCELLED is included deliberately: cancelling ends future renewal, but
# a member who already paid for the current period keeps access until
# ends_at passes — access is governed by ends_at, not by the act of
# cancelling itself. (Phase 2 requirement: "cancelled memberships retain
# or lose access according to ends_at".)
#
# PAST_DUE is intentionally excluded: a lapsed payment does not grant
# access, matching "an expired membership loses access immediately".
ENTITLED_STATUSES = (
    SubscriptionStatus.TRIAL,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.CANCELLED,
)
