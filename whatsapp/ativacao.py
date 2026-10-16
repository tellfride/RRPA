"""Liga e desliga o disparo automático por API (RRPA_WHATSAPP_API). Desligado, as telas e o webhook não existem (404)."""
from functools import wraps

from django.conf import settings
from django.http import Http404


def api_ligada():
    return bool(settings.RRPA_WHATSAPP_API)


def exige_api(view):
    @wraps(view)
    def interna(request, *args, **kwargs):
        if not api_ligada():
            raise Http404
        return view(request, *args, **kwargs)

    return interna
