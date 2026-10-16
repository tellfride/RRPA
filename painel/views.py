import secrets

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.models import Configuracao

from . import services


@login_required
@require_GET
def inicio(request):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    vaga_id = services._int(request.GET.get("vaga"))
    dados = services.painel_completo(request.user, config, hoje, vaga_id)
    return render(request, "painel/inicio.html", {
        **dados, "hoje": hoje, "vaga_id": vaga_id, "links": True, "tag": "a",
        "tv": request.GET.get("tv") == "1", "aba": "analises" if request.GET.get("aba") == "analises" else "geral",
    })


@login_required
@require_GET
def detalhe(request):
    """Lista aberta na gaveta lateral (ou, sem JavaScript, em página própria)."""
    config = Configuracao.atual()
    contexto = services.lista_detalhe(request.user, config, request.GET)
    contexto["hoje"] = timezone.localdate()
    modelo = "painel/_detalhe.html" if request.GET.get("gaveta") == "1" else "painel/detalhe_pagina.html"
    return render(request, modelo, contexto)


@require_GET
def publico(request, token):
    """Painel compartilhável: só números e quadro de vagas, sem nomes nem dados pessoais."""
    config = Configuracao.atual()
    esperado = config.painel_publico_token
    if not esperado or not secrets.compare_digest(token.encode(), esperado.encode()):
        raise Http404
    hoje = timezone.localdate()
    dados = services.painel_completo(None, config, hoje, anonimo=True)
    return render(request, "painel/publico.html", {**dados, "hoje": hoje, "links": False, "tag": "div"})
