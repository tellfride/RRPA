from django.urls import path

from . import views, views_publicas

app_name = "candidatos"

urlpatterns = [
    # equipe
    path("candidatos/", views.lista, name="lista"),
    path("candidatos/novo/", views.CandidatoCriarView.as_view(), name="novo"),
    path("candidatos/acoes/", views.acoes_em_massa, name="acoes"),
    path("candidatos/importar/", views.importar, name="importar"),
    path("candidatos/importar/vaga/<int:vaga_id>/", views.importar, name="importar_vaga"),
    path("candidatos/importacoes/", views.importacoes, name="importacoes"),
    path("candidatos/importacoes/<int:pk>/", views.previa, name="previa"),
    path("candidatos/importacoes/<int:pk>/confirmar/", views.confirmar_importacao, name="confirmar_importacao"),
    path("candidatos/importacoes/<int:pk>/cancelar/", views.cancelar_importacao, name="cancelar_importacao"),
    path("candidatos/importacoes/<int:pk>/erros.xlsx", views.erros_xlsx, name="erros_xlsx"),
    path("candidatos/<int:pk>/", views.detalhe, name="detalhe"),
    path("candidatos/<int:pk>/editar/", views.CandidatoEditarView.as_view(), name="editar"),
    path("candidatos/<int:pk>/aprovar/", views.aprovar, name="aprovar"),
    path("candidatos/<int:pk>/reprovar/", views.reprovar, name="reprovar"),
    path("candidatos/<int:pk>/reabrir/", views.reabrir, name="reabrir"),
    path("candidatos/<int:pk>/anonimizar/", views.anonimizar, name="anonimizar"),
    path("candidatos/<int:pk>/convite/", views.enviar_convite, name="convite"),
    path("candidatos/<int:pk>/whatsapp/", views.whatsapp_consentimento, name="whatsapp"),
    # público (sem login)
    path("cadastro/concluido/", views_publicas.cadastro_concluido, name="cadastro_concluido"),
    path("cadastro/<str:token>/", views_publicas.cadastro, name="cadastro"),
]
