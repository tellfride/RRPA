from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("saude/", views.saude, name="saude"),
    path("api/cep/<str:cep>/", views.consultar_cep_api, name="cep"),
    path("configuracoes/", views.configuracoes, name="configuracoes"),
    path("auditoria/", views.AuditoriaView.as_view(), name="auditoria"),
    path("favicon.ico", RedirectView.as_view(url="/static/icons/favicon.svg", permanent=True)),
]
