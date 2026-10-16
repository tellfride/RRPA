from django.urls import path

from . import views

app_name = "vagas"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nova/", views.VagaCriarView.as_view(), name="nova"),
    path("sugeridas/", views.criar_sugeridas, name="sugeridas"),
    path("<int:pk>/", views.detalhe, name="detalhe"),
    path("<int:pk>/editar/", views.VagaEditarView.as_view(), name="editar"),
    path("<int:pk>/modelo.xlsx", views.baixar_modelo, name="modelo"),
    path("<int:pk>/exportar.xlsx", views.exportar, name="exportar"),
]
