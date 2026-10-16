from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, UpdateView

from candidatos.models import Candidato
from core.constantes import VINCULO_ATIVO
from core.models import Configuracao, LogAtividade
from core.permissoes import EQUIPE_RECRUTAMENTO, SOMENTE_ADMIN, PerfilRequeridoMixin, perfil_requerido
from painel import services as painel
from vinculos import apresentacao
from vinculos.models import Vinculo
from vinculos.services import pendencias_checkin

from . import planilhas
from .forms import VagaForm
from .models import Vaga
from .sugeridas import VAGAS_SUGERIDAS

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _vaga_visivel(usuario, pk):
    vaga = painel.vagas_do_usuario(usuario).filter(pk=pk).first()
    if vaga is None:
        raise Http404
    return vaga


@require_GET
def lista(request):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    linhas, _, _ = painel.montar_quadro(request.user, config, hoje, incluir_inativas=request.user.eh_admin)
    return render(request, "vagas/lista.html", {"quadro": linhas, "links": True, "hoje": hoje})


@require_GET
def detalhe(request, pk):
    config = Configuracao.atual()
    hoje = timezone.localdate()
    vaga = _vaga_visivel(request.user, pk)
    linha = painel.montar_quadro(request.user, config, hoje, vaga_id=vaga.pk, incluir_inativas=True)[0][0]
    aba = request.GET.get("aba", "ocupantes")
    if aba == "candidatos" and not request.user.pode_recrutar:
        aba = "ocupantes"
    if aba not in ("ocupantes", "candidatos", "historico"):
        aba = "ocupantes"

    contexto = {"vaga": vaga, "linha": linha, "aba": aba, "hoje": hoje, "links": True}
    base = Vinculo.objects.filter(vaga=vaga).select_related("candidato", "vaga").prefetch_related("checkins")
    if aba == "ocupantes":
        contexto["vinculos"] = apresentacao.preparar(base.filter(status=VINCULO_ATIVO).order_by("data_fim", "candidato__nome"), config, hoje)
    elif aba == "historico":
        contexto["vinculos"] = apresentacao.preparar(base.exclude(status=VINCULO_ATIVO).order_by("-data_fim")[:200], config, hoje)
    else:
        candidatos = list(
            Candidato.objects.filter(vagas_interesse=vaga)
            .exclude(status__in=[Candidato.Status.EM_VAGA, Candidato.Status.REPROVADO, Candidato.Status.DESISTENTE]).distinct()
        )
        painel.anotar_distancia(candidatos, config)
        ordem = {Candidato.Status.APROVADO: 0, Candidato.Status.PENDENTE: 1, Candidato.Status.CONCLUIDO: 2}
        candidatos.sort(key=lambda c: (ordem.get(c.status, 9), c.distancia_km is None, c.distancia_km or 0, c.nome))
        contexto["candidatos"] = candidatos
    contexto["contagens"] = {
        "ocupantes": base.filter(status=VINCULO_ATIVO).count(),
        "candidatos": vaga.candidatos.exclude(status__in=[Candidato.Status.EM_VAGA, Candidato.Status.REPROVADO, Candidato.Status.DESISTENTE]).count() if request.user.pode_recrutar else 0,
        "historico": base.exclude(status=VINCULO_ATIVO).count(),
    }
    contexto["pendencias"] = len(pendencias_checkin(request.user, hoje, vaga_id=vaga.pk, todas=True))
    return render(request, "vagas/detalhe.html", contexto)


class VagaCriarView(PerfilRequeridoMixin, CreateView):
    perfis_permitidos = SOMENTE_ADMIN
    model = Vaga
    form_class = VagaForm
    template_name = "vagas/form.html"

    def form_valid(self, form):
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "vaga.criada", f"Cadastrou a vaga {self.object.nome} ({self.object.quantidade} vagas)", self.object, self.request)
        messages.success(self.request, f"Vaga {self.object.nome} cadastrada. Agora baixe o modelo de planilha e importe os candidatos.")
        return resposta

    def get_success_url(self):
        return reverse("vagas:detalhe", args=[self.object.pk])


class VagaEditarView(PerfilRequeridoMixin, UpdateView):
    perfis_permitidos = SOMENTE_ADMIN
    model = Vaga
    form_class = VagaForm
    template_name = "vagas/form.html"

    def form_valid(self, form):
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "vaga.editada", f"Atualizou a vaga {self.object.nome}", self.object, self.request)
        messages.success(self.request, "Vaga atualizada.")
        return resposta

    def get_success_url(self):
        return reverse("vagas:detalhe", args=[self.object.pk])


@require_POST
@perfil_requerido(*SOMENTE_ADMIN)
def criar_sugeridas(request):
    criadas = 0
    for nome, setor, descricao in VAGAS_SUGERIDAS:
        _, nova = Vaga.objects.get_or_create(nome=nome, defaults={"setor": setor, "descricao": descricao, "quantidade": 1})
        criadas += nova
    LogAtividade.registrar(request.user, "vaga.criada", f"Cadastrou {criadas} cargos sugeridos", request=request)
    messages.success(request, f"{criadas} cargos cadastrados com 1 vaga cada. Ajuste a quantidade de cada um.")
    return redirect("vagas:lista")


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def baixar_modelo(request, pk):
    vaga = get_object_or_404(Vaga, pk=pk)
    conteudo = planilhas.gerar_modelo(vaga, Configuracao.atual())
    nome = "modelo-" + "".join(c if c.isalnum() else "-" for c in vaga.nome.lower()).strip("-") + ".xlsx"
    resposta = HttpResponse(conteudo, content_type=XLSX)
    resposta["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resposta


@require_GET
def exportar(request, pk):
    if request.user.eh_visualizador:
        raise Http404
    config = Configuracao.atual()
    vaga = _vaga_visivel(request.user, pk)
    vinculos = apresentacao.preparar(
        Vinculo.objects.filter(vaga=vaga, status=VINCULO_ATIVO).select_related("candidato").prefetch_related("checkins").order_by("data_fim", "candidato__nome"),
        config,
    )
    conteudo = planilhas.exportar_vaga(vaga, vinculos, incluir_cpf=request.user.pode_recrutar)
    LogAtividade.registrar(request.user, "vaga.exportada", f"Exportou os colaboradores de {vaga.nome}", vaga, request)
    resposta = HttpResponse(conteudo, content_type=XLSX)
    resposta["Content-Disposition"] = f'attachment; filename="colaboradores-{vaga.pk}.xlsx"'
    return resposta
