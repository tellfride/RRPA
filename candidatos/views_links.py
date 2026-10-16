"""Links de cadastro: o link pessoal de quem indica e os convites individuais feitos após a aprovação."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.forms import EstiloFormMixin
from core.qr import qr_data_url, qr_svg
from core.models import Configuracao
from core.permissoes import GERADORES_DE_LINK, perfil_requerido
from core.utils import base_local, normalizar_telefone, url_absoluta, validar_telefone
from vagas.models import Vaga

from . import services
from .models import Candidato, LinkCadastro


class ConviteForm(EstiloFormMixin, forms.Form):
    nome = forms.CharField(label="Nome da pessoa", max_length=120)
    telefone = forms.CharField(label="Telefone com WhatsApp", max_length=20,
                               widget=forms.TextInput(attrs={"data-mascara": "telefone", "inputmode": "tel", "placeholder": "(11) 91234-5678"}))
    vaga = forms.ModelChoiceField(label="Vaga", queryset=Vaga.objects.none(), required=False, empty_label="Deixar a pessoa escolher",
                                  help_text="Se escolher uma vaga, o formulário já abre com ela selecionada.")
    validade_dias = forms.IntegerField(label="Validade do link (dias)", min_value=1, max_value=60)
    aprovado = forms.BooleanField(
        label="Esta pessoa foi aprovada na triagem e pode receber o link de cadastro", required=True,
        error_messages={"required": "O link só é enviado depois da aprovação. Marque a confirmação para continuar."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vaga"].queryset = Vaga.objects.filter(ativa=True).order_by("nome")
        self.fields["validade_dias"].initial = Configuracao.atual().validade_convite_dias

    def clean_telefone(self):
        telefone = normalizar_telefone(self.cleaned_data["telefone"])
        if not validar_telefone(telefone):
            raise forms.ValidationError("Informe o telefone com DDD. Ex.: (11) 91234-5678.")
        return telefone

    def clean_nome(self):
        return " ".join(self.cleaned_data["nome"].split())


@perfil_requerido(*GERADORES_DE_LINK)
def lista(request):
    pessoal = LinkCadastro.pessoal_de(request.user)
    convites = LinkCadastro.objects.filter(tipo=LinkCadastro.Tipo.CONVITE).select_related("criado_por", "vaga", "candidato_origem", "aprovado_por")
    if not request.user.eh_admin:
        convites = convites.filter(criado_por=request.user)
    url_pessoal = url_absoluta(request, pessoal.caminho)

    # Tudo que entrou por links fornecidos por esta pessoa (link pessoal e convites)
    meus = Candidato.objects.filter(indicado_por=request.user)
    inicio_do_dia = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    estatisticas = {
        "hoje": meus.filter(criado_em__gte=inicio_do_dia).count(),
        "semana": meus.filter(criado_em__gte=inicio_do_dia - timedelta(days=6)).count(),
        "total": meus.count(),
        "avancaram": meus.exclude(status__in=[Candidato.Status.PENDENTE, Candidato.Status.REPROVADO, Candidato.Status.DESISTENTE]).count(),
    }
    recentes = list(meus.order_by("-criado_em")[:5]) if request.user.pode_recrutar else []
    return render(request, "candidatos/links.html", {
        "pessoal": pessoal, "url_pessoal": url_pessoal, "convites": convites[:60], "estatisticas": estatisticas, "recentes": recentes,
        "texto_compartilhar": f"Cadastre-se para as vagas de {Configuracao.atual().nome_unidade}: {url_pessoal}",
        "qr_url": qr_data_url(url_pessoal + "?src=qr", escala=10),
        "qr_arquivo": "qrcode-cadastro-" + (slugify(request.user.nome_exibicao) or "rrpa") + ".png",
        "aviso_local": base_local(request),
    })


@perfil_requerido(*GERADORES_DE_LINK)
def cartaz(request):
    """Folha A4 para imprimir e afixar: QR Code do link pessoal de quem imprime."""
    pessoal = LinkCadastro.pessoal_de(request.user)
    url = url_absoluta(request, pessoal.caminho)
    return render(request, "candidatos/cartaz.html", {
        "qr_svg": mark_safe(qr_svg(url + "?src=qr", escala=10)),  # noqa: S308 - SVG gerado pela biblioteca a partir de uma URL nossa
        "endereco": url.split("://", 1)[-1], "config": Configuracao.atual(), "dono": request.user,
    })


@perfil_requerido(*GERADORES_DE_LINK)
def novo(request):
    form = ConviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        link = services.criar_convite(request.user, d["nome"], d["telefone"], vaga=d["vaga"], dias=d["validade_dias"], request=request)
        messages.success(request, "Convite criado. Confira a mensagem e envie pelo WhatsApp.")
        if request.user.pode_recrutar:
            return redirect(f"{reverse('mensagens:compor')}?link={link.pk}&categoria=convite_cadastro")
        return redirect("links:lista")
    return render(request, "candidatos/convite_form.html", {"form": form})


@require_POST
@perfil_requerido(*GERADORES_DE_LINK)
def revogar(request, pk):
    link = get_object_or_404(LinkCadastro, pk=pk)
    if not request.user.eh_admin and link.criado_por_id != request.user.pk:
        messages.error(request, "Só quem criou o link pode desativá-lo.")
    elif link.tipo == LinkCadastro.Tipo.PESSOAL:
        messages.error(request, "O link pessoal não pode ser desativado por aqui.")
    else:
        services.revogar_link(link, request.user, request)
        messages.success(request, "Link desativado. Quem tentar abrir verá que não está mais disponível.")
    return redirect("links:lista")
