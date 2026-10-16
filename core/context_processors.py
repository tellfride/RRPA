from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .models import Configuracao


def _pendencias(usuario):
    from vinculos.services import contar_pendencias_checkin

    return contar_pendencias_checkin(usuario)


def rrpa(request):
    """Variáveis disponíveis em todos os templates."""
    contexto = {
        "RRPA_VERSAO": settings.RRPA_VERSAO,
        "RRPA_DEMO": settings.RRPA_DEMO,
        "RRPA_WHATSAPP_API": settings.RRPA_WHATSAPP_API,
        "config": SimpleLazyObject(Configuracao.atual),
    }
    usuario = getattr(request, "user", None)
    if usuario is not None and usuario.is_authenticated:
        contexto.update(
            eh_admin=usuario.eh_admin,
            eh_gestor=usuario.eh_gestor,
            eh_visualizador=usuario.eh_visualizador,
            pode_recrutar=usuario.pode_recrutar,
            pode_checkin=usuario.pode_checkin,
            pode_gerar_link=usuario.pode_gerar_link,
            pendencias_checkin=SimpleLazyObject(lambda: _pendencias(usuario)),
        )
    return contexto
