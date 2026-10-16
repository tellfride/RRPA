from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("configuracoes/whatsapp/", views.conexao, name="conexao"),
    path("webhook/whatsapp/<str:token>/", views.webhook, name="webhook"),
]
