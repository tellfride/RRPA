from django.urls import path

from . import views

app_name = "mapa"

urlpatterns = [
    path("", views.mapa, name="mapa"),
    path("dados/", views.dados, name="dados"),
    path("candidatos/", views.candidatos, name="candidatos"),
    path("localizar/", views.geocodificar, name="geocodificar"),
    path("relocalizar/", views.relocalizar, name="relocalizar"),
]
