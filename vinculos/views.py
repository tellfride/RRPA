from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from candidatos.models import Candidato
from core.constantes import PERFIL_ADMIN, PERFIL_GESTOR, PERFIL_RECRUTADOR, VINCULO_ATIVO
from core.excecoes import RegraDeNegocio
from core.models import Configuracao
from core.permissoes import EQUIPE_RECRUTAMENTO, perfil_requerido
from core.utils import meses_do_periodo, rotulo_mes
from painel import services as painel
from vagas.models import Vaga

from . import apresentacao, services
from .forms import ChamarForm, EncerrarForm, mes_valido
from .models import Vinculo

ACOMPANHAMENTO = (PERFIL_ADMIN, PERFIL_RECRUTADOR, PERFIL_GESTOR)
SITUACOES = [("abertos", "Em aberto"), ("ativo", "Dentro do prazo"), ("vencendo", "Vencendo"), ("vencido", "Prazo vencido"),
             ("concluido", "Concluídos"), ("encerrado", "Encerrados antes"), ("todos", "Todos")]


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _vinculo_visivel(usuario, pk):
    vinculo = painel.vinculos_do_usuario(usuario).filter(pk=pk).first()
    if vinculo is None:
        raise Http404
    return vinculo


@require_GET
@perfil_requerido(*ACOMPANHAMENTO)
def lista(request):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    vaga_id = _int(request.GET.get("vaga"))
    situacao = request.GET.get("situacao", "abertos")
    q = request.GET.get("q", "").strip()
    vence = mes_valido(request.GET.get("vence", ""))

    base = painel.vinculos_do_usuario(request.user, vaga_id)
    if situacao == "abertos":
        qs = base.ativos()
    elif situacao == "todos":
        qs = base
    else:
        qs = base.da_situacao(situacao, hoje, config.dias_alerta)
    if q:
        qs = qs.filter(candidato__nome__icontains=q)
    if vence:
        qs = qs.filter(data_fim__year=vence.year, data_fim__month=vence.month)

    contagens = {
        "abertos": base.ativos().count(), "ativo": base.ocupando(hoje).count(),
        "vencendo": base.vencendo(hoje, config.dias_alerta).count(), "vencido": base.vencidos(hoje).count(),
        "concluido": base.filter(status="concluido").count(), "encerrado": base.filter(status="encerrado").count(),
        "todos": base.count(),
    }
    ordem = "-data_fim" if situacao in ("concluido", "encerrado", "todos") else "data_fim"
    pagina = Paginator(qs.prefetch_related("checkins").order_by(ordem, "candidato__nome"), 25).get_page(request.GET.get("page"))
    vinculos = apresentacao.preparar(pagina.object_list, config, hoje)
    return render(request, "vinculos/lista.html", {
        "page_obj": pagina, "vinculos": vinculos, "situacao": situacao, "q": q, "vaga_id": vaga_id, "vence": vence,
        "abas": [(chave, rotulo, contagens[chave]) for chave, rotulo in SITUACOES],
        "vagas": painel.vagas_do_usuario(request.user).order_by("nome"),
    })


@require_GET
@perfil_requerido(*ACOMPANHAMENTO)
def detalhe(request, pk):
    config = Configuracao.atual()
    vinculo = _vinculo_visivel(request.user, pk)
    vinculo = Vinculo.objects.select_related("candidato", "vaga", "criado_por", "encerrado_por").prefetch_related("checkins__registrado_por").get(pk=vinculo.pk)
    [v] = apresentacao.preparar([vinculo], config)
    pessoa = v.candidato
    painel.anotar_distancia([pessoa], config)
    return render(request, "vinculos/detalhe.html", {
        "v": v, "pessoa": pessoa, "motivos": Vinculo.Motivo.choices,
        "responsaveis": v.vaga.responsaveis.all(), "form_encerrar": EncerrarForm(),
        "chips_detalhe": [{**c, "responsavel": c["checkin"].registrado_por if c["checkin"] else None} for c in v.chips],
    })


@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def chamar(request):
    config = Configuracao.atual()
    if request.method == "POST":
        form = ChamarForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                vinculo = services.chamar_para_vaga(d["candidato"], d["vaga"], request.user, d["data_chamada"], d["observacoes"], request)
            except RegraDeNegocio as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, f"{vinculo.candidato.nome} foi chamado(a) para {vinculo.vaga.nome}. Prazo até {vinculo.data_fim:%d/%m/%Y}. Avise pelo WhatsApp:")
                return redirect(f"{reverse('mensagens:compor')}?candidato={vinculo.candidato_id}&vinculo={vinculo.pk}&categoria=convocacao")
    else:
        inicial = {}
        if _int(request.GET.get("candidato")):
            inicial["candidato"] = _int(request.GET.get("candidato"))
        if _int(request.GET.get("vaga")):
            inicial["vaga"] = _int(request.GET.get("vaga"))
        form = ChamarForm(initial=inicial)
    hoje = timezone.localdate()
    livres = {l["vaga"].pk: l["livres"] for l in painel.montar_quadro(request.user, config, hoje)[0]}
    form.fields["vaga"].label_from_instance = lambda v: f"{v.nome} — {livres.get(v.pk, 0)} {'livre' if livres.get(v.pk, 0) == 1 else 'livres'} de {v.quantidade}"
    return render(request, "vinculos/chamar.html", {"form": form, "config": config})


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def previa_prazo(request):
    """Mostra, antes de confirmar, até quando a pessoa poderá ficar na vaga."""
    data = parse_date(request.GET.get("data", ""))
    if data is None:
        return JsonResponse({"erro": "Data inválida."}, status=400)
    inicio, fim = services.periodo_para(data)
    meses = [rotulo_mes(m, longo=True) for m in meses_do_periodo(inicio, fim)]
    return JsonResponse({
        "inicio": inicio.strftime("%d/%m/%Y"), "fim": fim.strftime("%d/%m/%Y"), "meses": meses,
        "vencido": fim < timezone.localdate(),
    })


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def encerrar(request, pk):
    vinculo = get_object_or_404(Vinculo, pk=pk)
    form = EncerrarForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escolha o motivo do encerramento.")
        return redirect(vinculo)
    try:
        services.encerrar_vinculo(vinculo, request.user, form.cleaned_data["motivo"], form.cleaned_data["observacao"], request)
        messages.success(request, "Vínculo encerrado. A vaga foi liberada.")
    except RegraDeNegocio as erro:
        messages.error(request, str(erro))
    return redirect(vinculo)
