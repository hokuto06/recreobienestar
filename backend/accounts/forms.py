from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from common.forms import AccessibleFormMixin

from .models import Profile

User = get_user_model()


class RegistrationForm(AccessibleFormMixin, UserCreationForm):
    """Registration by email + password, per spec. Username still exists
    under the hood (Django's User model requires it and Profile/Subscription
    already key off settings.AUTH_USER_MODEL), but the form's primary
    identity field is email; username is derived automatically if left
    blank so members never have to think about it."""
    email = forms.EmailField(
        required=True,
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'tu@email.com'}),
    )
    display_name = forms.CharField(
        required=False, max_length=150, label='Nombre para mostrar',
        widget=forms.TextInput(attrs={'placeholder': 'Como querés que te llamemos'}),
    )

    class Meta:
        model = User
        fields = ('email',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ('password1', 'password2'):
            self.fields[field_name].widget.attrs.setdefault('autocomplete', 'new-password')
        self.fields['password1'].label = 'Contraseña'
        self.fields['password2'].label = 'Confirmá la contraseña'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo electrónico.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        # Username is required by Django's User model and must be unique;
        # derive it from the email's local part rather than asking members
        # to invent one, disambiguating on collision.
        base_username = self.cleaned_data['email'].split('@')[0][:140] or 'miembro'
        username = base_username
        suffix = 2
        while User.objects.filter(username=username).exists():
            username = f'{base_username}{suffix}'
            suffix += 1
        user.username = username
        if commit:
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.display_name = self.cleaned_data.get('display_name', '')
            profile.save()
        return user


class EmailOrUsernameAuthenticationForm(AccessibleFormMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuario o correo electrónico'
        self.fields['username'].widget.attrs.update({
            'placeholder': 'tu@email.com',
            'autofocus': True,
        })

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': (
            'No encontramos una cuenta con esos datos. Revisá el usuario/correo '
            'y la contraseña e intentá de nuevo.'
        ),
    }


class ProfileForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['display_name', 'avatar_url', 'avatar']
        widgets = {
            'display_name': forms.TextInput(attrs={'placeholder': 'Como querés que te llamemos'}),
            'avatar_url': forms.URLInput(attrs={'placeholder': 'https://...'}),
        }
        labels = {
            'display_name': 'Nombre para mostrar',
            'avatar_url': 'URL de foto de perfil (opcional)',
            'avatar': 'o subir una imagen (opcional)',
        }
