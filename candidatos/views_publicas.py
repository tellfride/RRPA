"""Páginas abertas ao candidato (sem login): cadastro pelo link recebido."""
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core import ratelimit
from core.excecoes import RegraDeNegocio
from core.models import Configuracao
from core.utils import ip_do_cliente

from . import services
from .forms import CadastroPublicoForm
from .models import LinkCadastro

CADASTROS_POR_HORA = 12


@require_http_methods(["GET", "POST"])
def cadastro(request, token):
    link = LinkCadastro.objects.select_related("criado_por", "vaga", "candidato_origem").filter(token=token).first()
    if link is None:
        return render(request, "candidatos/publico_indisponivel.html", {"motivo": "nao_existe"}, status=404)
    if not link.utilizavel:
        return render(request, "candidatos/publico_indisponivel.html", {"motivo": link.estado, "link": link}, status=410)

    ip = ip_do_cliente(request) or "?"
    chave = f"cadastro:{ip}"
    if request.method == "POST":
        if ratelimit.excedeu(chave, CADASTROS_POR_HORA):
            return render(request, "candidatos/publico_indisponivel.html", {"motivo": "limite"}, status=429)
        form = CadastroPublicoForm(request.POST, link=link)
        if form.is_valid():
            if form.cleaned_data.get("website"):  # isca preenchida: robô. Finge que deu certo.
                return redirect("candidatos:cadastro_concluido")
            try:
                services.registrar_candidato_publico(link, form.cleaned_data, ip, request)
            except RegraDeNegocio:
                return render(request, "candidatos/publico_indisponivel.html", {"motivo": "usado", "link": link}, status=410)
            ratelimit.registrar(chave, 3600)
            return redirect("candidatos:cadastro_concluido")
    else:
        form = CadastroPublicoForm(link=link)
    return render(request, "candidatos/publico_cadastro.html", {
        "form": form, "link": link, "config": Configuracao.atual(),
        "atualizacao": link.candidato_origem_id is not None,
    })


def cadastro_concluido(request):
    return render(request, "candidatos/publico_obrigado.html", {"config": Configuracao.atual()})
