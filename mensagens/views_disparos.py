"""Telas dos disparos automáticos de WhatsApp: criar (a partir da lista de candidatos), acompanhar e controlar."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from candidatos.models import Candidato
from core.excecoes import RegraDeNegocio
from core.permissoes import EQUIPE_RECRUTAMENTO, perfil_requerido

from whatsapp.ativacao import exige_api

from . import disparos as servico
from .models import Disparo, MensagemDisparo, ModeloMensagem


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@require_POST
@exige_api
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def criar(request):
    ids = [i for i in (_int(x) for x in request.POST.get("ids", "").split(",")) if i]
    modelo = ModeloMensagem.objects.filter(pk=_int(request.POST.get("modelo")), ativo=True).first()
    candidatos = list(Candidato.objects.filter(pk__in=ids).order_by("nome"))
    if not candidatos:
        messages.warning(request, "Marque ao menos um candidato para enviar mensagem.")
        return redirect("candidatos:lista")
    try:
        disparo = servico.criar_disparo(request.user, candidatos, modelo, request.POST.get("ritmo"), request)
    except RegraDeNegocio as erro:
        messages.error(request, str(erro))
        destino = f"{reverse('mensagens:fila')}?ids={','.join(str(i) for i in ids)}"
        return redirect(destino + (f"&modelo={modelo.pk}" if modelo else ""))
    messages.success(request, "Disparo criado. O envio começa em instantes e segue no ritmo escolhido.")
    return redirect("mensagens:disparo", pk=disparo.pk)


@require_GET
@exige_api
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def lista(request):
    qs = Disparo.objects.select_related("criado_por").order_by("-criado_em", "-id").annotate(
        total=Count("mensagens"),
        enviadas=Count("mensagens", filter=Q(mensagens__status__in=("enviada", "entregue", "lida"))),
        entregues=Count("mensagens", filter=Q(mensagens__status__in=("entregue", "lida"))),
        lidas=Count("mensagens", filter=Q(mensagens__status="lida")),
        falhas=Count("mensagens", filter=Q(mensagens__status="falhou")),
    )
    pagina = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "mensagens/disparos.html", {"page_obj": pagina, "disparos": pagina.object_list})


@require_GET
@exige_api
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def detalhe(request, pk):
    disparo = get_object_or_404(Disparo.objects.select_related("criado_por", "modelo"), pk=pk)
    status = request.GET.get("status", "")
    mensagens = disparo.mensagens.select_related("candidato")
    if status in MensagemDisparo.Status.values:
        mensagens = mensagens.filter(status=status)
    pagina = Paginator(mensagens, 30).get_page(request.GET.get("page"))
    total = servico.contagens(disparo)
    return render(request, "mensagens/disparo_detalhe.html", {
        "disparo": disparo, "total": total, "page_obj": pagina, "itens": pagina.object_list, "status": status,
        "status_opcoes": MensagemDisparo.Status.choices,
        "pode_reenviar": total["reenviaveis"] > 0 and disparo.status in ("em_andamento", "concluido"),
    })


@require_POST
@exige_api
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def controle(request, pk):
    disparo = get_object_or_404(Disparo, pk=pk)
    try:
        messages.success(request, servico.controlar(disparo, request.POST.get("acao", ""), request.user, request))
    except RegraDeNegocio as erro:
        messages.error(request, str(erro))
    return redirect("mensagens:disparo", pk=disparo.pk)
