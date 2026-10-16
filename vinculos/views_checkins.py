"""Check-ins: a validação mensal feita pelo responsável do setor."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.excecoes import RegraDeNegocio
from core.models import Configuracao
from core.permissoes import EQUIPE_SETOR, perfil_requerido
from core.utils import destino_seguro, rotulo_mes
from painel import services as painel

from . import apresentacao, services
from .forms import mes_valido
from .models import Checkin, Vinculo


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _destino(request, padrao="/checkins/"):
    return redirect(destino_seguro(request.POST.get("proximo"), padrao))


@require_GET
@perfil_requerido(*EQUIPE_SETOR)
def lista(request):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    vaga_id = _int(request.GET.get("vaga"))
    aba = request.GET.get("aba", "pendentes")
    pendencias = services.pendencias_checkin(request.user, hoje, vaga_id=vaga_id)
    pendentes_ids = {p["vinculo"].pk for p in pendencias}

    qs = painel.vinculos_do_usuario(request.user, vaga_id).filter(status="ativo").prefetch_related("checkins").order_by("vaga__nome", "data_fim", "candidato__nome")
    vinculos = apresentacao.preparar(qs, config, hoje)
    for v in vinculos:
        v.tem_pendencia = v.pk in pendentes_ids
    em_dia = [v for v in vinculos if not v.tem_pendencia]
    pendentes = [v for v in vinculos if v.tem_pendencia]
    return render(request, "vinculos/checkins.html", {
        "aba": aba, "vinculos": pendentes if aba == "pendentes" else vinculos, "total_pendencias": len(pendencias),
        "atrasadas": sum(1 for p in pendencias if p["atrasado"]), "n_pendentes": len(pendentes), "n_em_dia": len(em_dia), "n_todos": len(vinculos),
        "vaga_id": vaga_id, "vagas": painel.vagas_do_usuario(request.user).filter(ativa=True).order_by("nome"),
        "mes_atual": hoje.replace(day=1), "mes_atual_rotulo": rotulo_mes(hoje, longo=True),
    })


@require_POST
@perfil_requerido(*EQUIPE_SETOR)
def registrar(request):
    vinculo = painel.vinculos_do_usuario(request.user).filter(pk=_int(request.POST.get("vinculo"))).select_related("candidato", "vaga").first()
    competencia = mes_valido(request.POST.get("competencia", "")[:7])
    if vinculo is None or competencia is None:
        messages.error(request, "Não encontrei esse check-in. Atualize a página e tente de novo.")
        return _destino(request)
    try:
        checkin, criado = services.registrar_checkin(
            vinculo, competencia, request.POST.get("resultado", ""), request.user, request.POST.get("observacao", ""), request,
        )
        messages.success(request, f"Check-in de {rotulo_mes(competencia, longo=True)} salvo para {vinculo.candidato.nome}: {checkin.get_resultado_display().lower()}.")
    except RegraDeNegocio as erro:
        messages.error(request, str(erro))
    return _destino(request)


@require_POST
@perfil_requerido(*EQUIPE_SETOR)
def registrar_lote(request):
    """Confirma vários colaboradores de uma vez como 'cumpriu o RPA' em um mês."""
    competencia = mes_valido(request.POST.get("competencia", "")[:7])
    ids = [i for i in (_int(x) for x in request.POST.getlist("ids")) if i]
    if competencia is None or not ids:
        messages.warning(request, "Marque os colaboradores e escolha o mês.")
        return _destino(request)
    feitos = 0
    for vinculo in painel.vinculos_do_usuario(request.user).filter(pk__in=ids).select_related("candidato", "vaga"):
        if vinculo.checkins.filter(competencia=competencia).exists() or competencia not in vinculo.competencias_exigidas():
            continue
        try:
            services.registrar_checkin(vinculo, competencia, Checkin.Resultado.CUMPRIU, request.user, "", request)
            feitos += 1
        except RegraDeNegocio:
            continue
    messages.success(request, f"{feitos} check-in(s) de {rotulo_mes(competencia, longo=True)} registrados como “cumpriu o RPA”.")
    return _destino(request)
