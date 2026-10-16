from django.urls import path

from . import views

app_name = "contas"

urlpatterns = [
    path("login/", views.EntrarView.as_view(), name="login"),
    path("sair/", views.sair, name="sair"),
    path("minha-senha/", views.MinhaSenhaView.as_view(), name="minha_senha"),
    path("usuarios/", views.UsuariosView.as_view(), name="usuarios"),
    path("usuarios/novo/", views.UsuarioCriarView.as_view(), name="usuario_novo"),
    path("usuarios/<int:pk>/", views.UsuarioEditarView.as_view(), name="usuario_editar"),
    path("usuarios/<int:pk>/senha/", views.redefinir_senha, name="redefinir_senha"),
]
