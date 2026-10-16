from django.urls import path

from . import views

app_name = "vinculos"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("chamar/", views.chamar, name="chamar"),
    path("previa-prazo/", views.previa_prazo, name="previa_prazo"),
    path("<int:pk>/", views.detalhe, name="detalhe"),
    path("<int:pk>/encerrar/", views.encerrar, name="encerrar"),
]
