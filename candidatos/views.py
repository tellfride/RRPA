from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, UpdateView

from contas.models import Usuario
from core import geo
from core.excecoes import RegraDeNegocio
from core.models import Configuracao, LogAtividade
from core.permissoes import EQUIPE_RECRUTAMENTO, SOMENTE_ADMIN, PerfilRequeridoMixin, perfil_requerido
from core.utils import destino_seguro, normalizar_telefone, so_digitos
from mensagens.models import EnvioMensagem
from painel import services as painel
from vagas.models import Vaga
from vinculos import apresentacao

from . import importacao as imp
from . import services
from .forms import CandidatoForm
from .models import Candidato, Importacao

POR_PAGINA = 25
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def lista(request):
    config = Configuracao.atual()
    qs = Candidato.objects.select_related("indicado_por").prefetch_related("vagas_interesse")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    vaga_id = _int(request.GET.get("vaga"))
    origem = request.GET.get("origem", "")
    indicado_por = _int(request.GET.get("indicado_por"))
    bairro = request.GET.get("bairro", "").strip()
    cidade = request.GET.get("cidade", "").strip()
    if q:
        digitos = so_digitos(q)
        filtro = Q(nome__icontains=q)
        if len(digitos) >= 3:
            filtro |= Q(cpf__contains=digitos) | Q(telefone__contains=digitos) | Q(cep__contains=digitos)
            nacional = normalizar_telefone(digitos)  # "+55 (11) 91234-5678" também acha o cadastro
            if nacional != digitos and len(nacional) >= 3:
                filtro |= Q(telefone__contains=nacional)
        qs = qs.filter(filtro)
    if status in Candidato.Status.values:
        qs = qs.filter(status=status)
    if vaga_id:
        qs = qs.filter(vagas_interesse=vaga_id)
    if origem in Candidato.Origem.values:
        qs = qs.filter(origem=origem)
    if indicado_por:
        qs = qs.filter(indicado_por_id=indicado_por)
    if bairro:
        qs = qs.filter(bairro=bairro)
    if cidade:
        qs = qs.filter(cidade=cidade)
    ordem = {"recentes": "-criado_em", "nome": "nome", "bairro": "bairro"}.get(request.GET.get("ordem", "recentes"), "-criado_em")
    pagina = Paginator(qs.order_by(ordem, "nome").distinct(), POR_PAGINA).get_page(request.GET.get("page"))
    painel.anotar_distancia(list(pagina.object_list), config)

    contagem = dict(Candidato.objects.values_list("status").annotate(n=Count("id")))
    abas = [("", "Todos", sum(contagem.values()))] + [(valor, rotulo, contagem.get(valor, 0)) for valor, rotulo in Candidato.Status.choices]
    return render(request, "candidatos/lista.html", {
        "page_obj": pagina, "candidatos": pagina.object_list, "abas": abas, "q": q, "status": status, "vaga_id": vaga_id,
        "origem": origem, "indicado_por": indicado_por, "bairro": bairro, "cidade": cidade, "ordem": request.GET.get("ordem", "recentes"),
        "vagas": Vaga.objects.order_by("nome"), "origens": Candidato.Origem.choices,
        "indicadores": Usuario.objects.filter(links__isnull=False).distinct().order_by("first_name"),
        "filtrado": any([q, status, vaga_id, origem, indicado_por, bairro, cidade]),
    })


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def acoes_em_massa(request):
    ids = [i for i in (_int(x) for x in request.POST.getlist("ids")) if i]
    acao = request.POST.get("acao", "")
    if not ids:
        messages.warning(request, "Marque ao menos um candidato.")
        return redirect(request.POST.get("proximo") or "candidatos:lista")
    if acao == "mensagem":
        return redirect(f"{reverse('mensagens:fila')}?ids={','.join(str(i) for i in ids)}")
    candidatos = list(Candidato.objects.filter(pk__in=ids))
    feitos, recusados = 0, []
    for candidato in candidatos:
        try:
            if acao == "aprovar":
                services.aprovar(candidato, request.user, request)
            elif acao == "reprovar":
                services.reprovar(candidato, request.user, request=request)
            else:
                raise RegraDeNegocio("Ação desconhecida.")
            feitos += 1
        except RegraDeNegocio as erro:
            recusados.append(str(erro))
    if feitos:
        messages.success(request, f"{feitos} candidato(s) {'aprovado(s)' if acao == 'aprovar' else 'marcado(s) como não aprovado(s)'}.")
    if recusados:
        messages.warning(request, "Não foi possível alterar alguns: " + " ".join(recusados[:3]))
    return redirect(destino_seguro(request.POST.get("proximo"), reverse("candidatos:lista")))


class CandidatoCriarView(PerfilRequeridoMixin, CreateView):
    perfis_permitidos = EQUIPE_RECRUTAMENTO
    model = Candidato
    form_class = CandidatoForm
    template_name = "candidatos/form.html"

    def get_initial(self):
        inicial = super().get_initial()
        vaga = _int(self.request.GET.get("vaga"))
        if vaga:
            inicial["vagas_interesse"] = [vaga]
        return inicial

    def form_valid(self, form):
        form.instance.origem = Candidato.Origem.MANUAL
        form.instance.criado_por = self.request.user
        resposta = super().form_valid(form)
        geo.aplicar_localizacao(self.object, somente_cache=True)
        LogAtividade.registrar(self.request.user, "candidato.criado", f"Cadastrou {self.object.nome}", self.object, self.request)
        messages.success(self.request, f"{self.object.nome} cadastrado(a). A localização no mapa é preenchida pelo CEP.")
        return resposta

    def get_success_url(self):
        return reverse("candidatos:detalhe", args=[self.object.pk])


class CandidatoEditarView(PerfilRequeridoMixin, UpdateView):
    perfis_permitidos = EQUIPE_RECRUTAMENTO
    model = Candidato
    form_class = CandidatoForm
    template_name = "candidatos/form.html"

    def form_valid(self, form):
        resposta = super().form_valid(form)
        geo.aplicar_localizacao(self.object, somente_cache=True)
        LogAtividade.registrar(self.request.user, "candidato.editado", f"Atualizou o cadastro de {self.object.nome}", self.object, self.request)
        messages.success(self.request, "Cadastro atualizado.")
        return resposta

    def get_success_url(self):
        return reverse("candidatos:detalhe", args=[self.object.pk])


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def detalhe(request, pk):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    candidato = get_object_or_404(Candidato.objects.select_related("indicado_por", "aprovado_por", "link__criado_por"), pk=pk)
    painel.anotar_distancia([candidato], config)
    vinculos = apresentacao.preparar(
        candidato.vinculos.select_related("vaga").prefetch_related("checkins").order_by("-data_chamada"), config, hoje,
    )
    envios = list(EnvioMensagem.objects.filter(candidato=candidato).select_related("usuario", "modelo")[:10])
    convites = list(candidato.convites.select_related("criado_por").order_by("-criado_em")[:5])

    linha_tempo = [{"quando": candidato.criado_em, "titulo": f"Cadastro ({candidato.get_origem_display().lower()})",
                    "detalhe": f"por {candidato.criado_por.nome_exibicao}" if candidato.criado_por_id else (f"pelo link de {candidato.indicado_por.nome_exibicao}" if candidato.indicado_por_id else "")}]
    if candidato.aprovado_em:
        linha_tempo.append({"quando": candidato.aprovado_em, "titulo": "Aprovado(a)", "detalhe": f"por {candidato.aprovado_por.nome_exibicao}" if candidato.aprovado_por_id else ""})
    for v in vinculos:
        linha_tempo.append({"quando": v.criado_em, "titulo": f"Chamado(a) para {v.vaga.nome}", "detalhe": f"prazo até {v.data_fim:%d/%m/%Y}"})
        if v.encerrado_em:
            linha_tempo.append({"quando": v.encerrado_em, "titulo": f"Vínculo encerrado em {v.vaga.nome}", "detalhe": v.get_motivo_encerramento_display()})
        for c in v.checkins.all():
            linha_tempo.append({"quando": c.registrado_em, "titulo": f"Check-in {c.competencia:%m/%Y}: {c.get_resultado_display().lower()}",
                                "detalhe": c.registrado_por.nome_exibicao if c.registrado_por_id else ""})
    for e in envios:
        linha_tempo.append({"quando": e.criado_em, "titulo": "Mensagem aberta no WhatsApp", "detalhe": e.modelo.nome if e.modelo_id else "texto livre"})
    linha_tempo.sort(key=lambda item: item["quando"], reverse=True)

    vagas_livres = [
        l for l in painel.montar_quadro(request.user, config, hoje)[0] if l["livres"]
    ] if candidato.status == Candidato.Status.APROVADO else []
    mini_mapa = None
    if candidato.localizado:
        mini_mapa = {
            "lat": candidato.latitude, "lng": candidato.longitude, "zoom": 15 if candidato.precisao_geo == "ENDERECO" else 13,
            "tiles": settings.RRPA_MAPA_TILES_URL, "atribuicao": settings.RRPA_MAPA_ATRIBUICAO,
            "unidade": {"lat": config.latitude, "lng": config.longitude, "nome": config.nome_unidade} if config.tem_coordenadas else None,
        }
    return render(request, "candidatos/detalhe.html", {
        "candidato": candidato, "vinculos": vinculos, "envios": envios, "convites": convites, "linha_tempo": linha_tempo,
        "vagas_livres": vagas_livres, "hoje": hoje, "vinculo_ativo": next((v for v in vinculos if v.status == "ativo"), None),
        "mini_mapa": mini_mapa,
    })


def _acao(request, pk, funcao, sucesso, **extra):
    candidato = get_object_or_404(Candidato, pk=pk)
    try:
        funcao(candidato, request.user, request=request, **extra)
        messages.success(request, sucesso.format(nome=candidato.nome))
    except RegraDeNegocio as erro:
        messages.error(request, str(erro))
    return redirect(destino_seguro(request.POST.get("proximo"), candidato.get_absolute_url()))


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def aprovar(request, pk):
    return _acao(request, pk, services.aprovar, "{nome} aprovado(a). Já pode ser chamado(a) para uma vaga.")


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def reprovar(request, pk):
    return _acao(request, pk, services.reprovar, "{nome} marcado(a) como não aprovado(a).", motivo=request.POST.get("motivo", ""))


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def reabrir(request, pk):
    return _acao(request, pk, services.reabrir, "Cadastro de {nome} reaberto.")


@require_POST
@perfil_requerido(*SOMENTE_ADMIN)
def anonimizar(request, pk):
    return _acao(request, pk, services.anonimizar, "Os dados pessoais de {nome} foram removidos.")


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def whatsapp_consentimento(request, pk):
    """Registra que a pessoa aceitou — ou pediu para não receber — mensagens por WhatsApp."""
    candidato = get_object_or_404(Candidato, pk=pk)
    aceita = request.POST.get("valor") == "sim"
    services.registrar_whatsapp(candidato, request.user, aceita, request)
    messages.success(request, "Registrado: " + ("aceita mensagens por WhatsApp." if aceita else "não receberá mais mensagens por WhatsApp."))
    return redirect(candidato)


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def enviar_convite(request, pk):
    """Aprova (se ainda não estiver) e gera o link para o candidato completar o cadastro, ligado a quem o forneceu."""
    candidato = get_object_or_404(Candidato, pk=pk)
    if candidato.status in (Candidato.Status.REPROVADO, Candidato.Status.EM_VAGA):
        messages.error(request, "Este cadastro não pode receber um convite agora.")
        return redirect(candidato)
    link = services.criar_convite(
        request.user, candidato.nome, candidato.telefone, vaga=candidato.vagas_interesse.first(), candidato=candidato, request=request,
    )
    messages.success(request, "Convite criado. Confira a mensagem e envie pelo WhatsApp.")
    return redirect(f"{reverse('mensagens:compor')}?candidato={candidato.pk}&link={link.pk}&categoria=convite_cadastro")


# ------------------------------------------------------------------ importação de planilhas
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def importar(request, vaga_id=None):
    vagas = Vaga.objects.filter(ativa=True).order_by("nome")
    vaga_fixa = get_object_or_404(Vaga, pk=vaga_id) if vaga_id else None
    erro = ""
    if request.method == "POST":
        vaga = vaga_fixa or vagas.filter(pk=_int(request.POST.get("vaga"))).first()
        arquivo = request.FILES.get("arquivo")
        if vaga is None:
            erro = "Escolha a vaga para a qual os candidatos estão se candidatando."
        elif arquivo is None:
            erro = "Escolha o arquivo da planilha."
        else:
            try:
                previa = imp.criar_previa(arquivo, vaga, request.user, como_aprovados=bool(request.POST.get("como_aprovados")))
                return redirect("candidatos:previa", pk=previa.pk)
            except imp.ErroPlanilha as exc:
                erro = str(exc)
    return render(request, "candidatos/importar.html", {"vagas": vagas, "vaga_fixa": vaga_fixa, "erro": erro})


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def previa(request, pk):
    importacao = get_object_or_404(Importacao.objects.select_related("vaga", "usuario"), pk=pk)
    linhas = importacao.dados
    return render(request, "candidatos/importar_previa.html", {
        "importacao": importacao, "amostra": linhas[:60], "com_erro": [l for l in linhas if l["estado"] == "erro"][:200],
    })


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def confirmar_importacao(request, pk):
    importacao = get_object_or_404(Importacao, pk=pk)
    try:
        imp.confirmar(importacao, request.user, request)
    except imp.ErroPlanilha as erro:
        messages.error(request, str(erro))
        return redirect("candidatos:importacoes")
    messages.success(request, f"{importacao.criadas} candidatos cadastrados e {importacao.vinculadas} já existentes vinculados à vaga.")
    if importacao.vaga_id:
        return redirect(f"{reverse('vagas:detalhe', args=[importacao.vaga_id])}?aba=candidatos")
    return redirect("candidatos:lista")


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def cancelar_importacao(request, pk):
    importacao = get_object_or_404(Importacao, pk=pk, status=Importacao.Status.PREVIA)
    importacao.status = Importacao.Status.CANCELADA
    importacao.save(update_fields=["status"])
    messages.info(request, "Importação cancelada. Nenhum candidato foi cadastrado.")
    return redirect("candidatos:importacoes")


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def erros_xlsx(request, pk):
    importacao = get_object_or_404(Importacao, pk=pk)
    resposta = HttpResponse(imp.planilha_de_erros(importacao), content_type=XLSX)
    resposta["Content-Disposition"] = f'attachment; filename="linhas-com-problema-{importacao.pk}.xlsx"'
    return resposta


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def importacoes(request):
    pagina = Paginator(Importacao.objects.select_related("vaga", "usuario"), 20).get_page(request.GET.get("page"))
    return render(request, "candidatos/importacoes.html", {"page_obj": pagina, "importacoes": pagina.object_list})
