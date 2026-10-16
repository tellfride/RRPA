from django.urls import path

from . import views

app_name = "painel"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("painel/detalhe/", views.detalhe, name="detalhe"),
    path("painel/publico/<str:token>/", views.publico, name="publico"),
]
