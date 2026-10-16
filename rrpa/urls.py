from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("painel.urls")),
    path("", include("core.urls")),
    path("", include("contas.urls")),
    path("", include("candidatos.urls")),
    path("vagas/", include("vagas.urls")),
    path("vinculos/", include("vinculos.urls")),
    path("checkins/", include("vinculos.urls_checkins")),
    path("mensagens/", include("mensagens.urls")),
    path("", include("whatsapp.urls")),
    path("mapa/", include("mapa.urls")),
    path("links/", include("candidatos.urls_links")),
    path("admin/", admin.site.urls),
]

handler403 = "django.views.defaults.permission_denied"
