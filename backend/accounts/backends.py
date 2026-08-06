from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    """Lets members log in with either their username or their email —
    Django's default backend only accepts username. Falls back to the
    normal ModelBackend behavior for anything else (permissions, etc.)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.get(
                Q(username__iexact=username) | Q(email__iexact=username)
            )
        except UserModel.DoesNotExist:
            # Run the hasher anyway to keep timing consistent regardless of
            # whether the account exists (avoids leaking valid usernames
            # through response-time differences).
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            # Two accounts collide on email vs username case-insensitively —
            # fall back to an exact username match rather than guessing.
            user = UserModel._default_manager.filter(username=username).first()
            if user is None:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
