from django.urls import path

from . import views_checkins

app_name = "checkins"

urlpatterns = [
    path("", views_checkins.lista, name="lista"),
    path("registrar/", views_checkins.registrar, name="registrar"),
    path("registrar-lote/", views_checkins.registrar_lote, name="registrar_lote"),
]
