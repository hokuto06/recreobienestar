"""
Shared form accessibility wiring — used by RegistrationForm,
EmailOrUsernameAuthenticationForm, and ProfileForm (accounts/forms.py) so
every field in this app's own forms gets the same treatment without
repeating it three times.

Not applied to Django's built-in PasswordResetForm/SetPasswordForm (used
as-is via the stock PasswordResetView/PasswordResetConfirmView) — those
render with Django's normal accessible defaults (visible label, adjacent
error text) already; adding this mixin there would mean subclassing
Django's own auth forms for a marginal gain, which isn't worth it here.
"""


class AccessibleFormMixin:
    """
    - Every field gets `aria-describedby` pointing at a `<div id="…-hint">`
      the field templates render for help_text/errors — even before any
      error exists, so the id is stable and the association never has to
      be added/removed at request time.
    - After validation, fields with errors get `aria-invalid="true"` so
      screen readers announce the invalid state on the field itself, not
      just the error text sitting nearby in the DOM.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs['aria-describedby'] = f'{name}-hint'

    def full_clean(self):
        super().full_clean()
        for name in getattr(self, 'errors', {}):
            if name in self.fields:
                self.fields[name].widget.attrs['aria-invalid'] = 'true'
