"""
Django's built-in auth.User has NO database-level unique constraint on
email — RegistrationForm.clean_email() only checks uniqueness at the
application layer (a SELECT before the INSERT), which is a real
time-of-check-to-time-of-use race: two near-simultaneous registrations
with the same email could both pass that check before either commits,
leaving two accounts sharing one email. EmailOrUsernameModelBackend
already has to defensively handle exactly that
(MultipleObjectsReturned) — this migration removes the need for that
defense to ever trigger, by making it impossible at the database level.

Deliberately NOT done by swapping AUTH_USER_MODEL to a custom user model:
that would mean unwinding already-applied Phase 1 migrations and the
Subscription.user / Profile.user foreign keys. A raw index on Django's
own auth_user table, added from this app's migrations, is the
established pattern for adding a constraint Django's model class doesn't
know about without a full user-model migration.

Case-insensitive (matches `email__iexact` in RegistrationForm) and
partial (`WHERE email <> ''`) — Django's User.email is blank=True, so
more than one legacy/admin-created account could otherwise legitimately
have no email at all; only non-blank emails must be unique.
"""
from django.db import migrations

CREATE_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS auth_user_email_uniq_ci "
    "ON auth_user (LOWER(email)) WHERE email <> '';"
)
DROP_SQL = "DROP INDEX IF EXISTS auth_user_email_uniq_ci;"


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
