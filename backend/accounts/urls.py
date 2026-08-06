from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('registro/', views.RegisterView.as_view(), name='register'),
    path('ingresar/', views.MemberLoginView.as_view(), name='login'),
    path('salir/', views.MemberLogoutView.as_view(), name='logout'),

    path('recuperar-clave/', views.MemberPasswordResetView.as_view(), name='password_reset'),
    path('recuperar-clave/enviado/', views.MemberPasswordResetDoneView.as_view(), name='password_reset_done'),
    path(
        'recuperar-clave/confirmar/<uidb64>/<token>/',
        views.MemberPasswordResetConfirmView.as_view(),
        name='password_reset_confirm',
    ),
    path('recuperar-clave/completado/', views.MemberPasswordResetCompleteView.as_view(), name='password_reset_complete'),

    path('mi-cuenta/', views.dashboard, name='dashboard'),
    path('mi-cuenta/perfil/', views.ProfileEditView.as_view(), name='profile_edit'),
]
