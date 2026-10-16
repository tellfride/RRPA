from django.urls import path

from . import views, views_disparos

app_name = "mensagens"

urlpatterns = [
    path("", views.modelos, name="modelos"),
    path("novo/", views.ModeloCriarView.as_view(), name="modelo_novo"),
    path("<int:pk>/editar/", views.ModeloEditarView.as_view(), name="modelo_editar"),
    path("<int:pk>/alternar/", views.modelo_alternar, name="modelo_alternar"),
    path("enviar/", views.compor, name="compor"),
    path("fila/", views.fila, name="fila"),
    path("registrar/", views.registrar, name="registrar"),
    path("historico/", views.historico, name="historico"),
    path("disparos/", views_disparos.lista, name="disparos"),
    path("disparos/novo/", views_disparos.criar, name="disparo_criar"),
    path("disparos/<int:pk>/", views_disparos.detalhe, name="disparo"),
    path("disparos/<int:pk>/controle/", views_disparos.controle, name="disparo_controle"),
]
