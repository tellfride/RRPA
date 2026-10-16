"""Tela de conexão com o WhatsApp (somente administrador) e o webhook público que o provedor chama."""
import logging

from django.contrib import messages
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse
from django.http.request import RawPostDataException
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core import ratelimit
from core.models import LogAtividade
from core.permissoes import SOMENTE_ADMIN, perfil_requerido
from core.utils import base_local, url_absoluta
from mensagens.eventos import aplicar_eventos

from . import registro
from .cripto import ErroCredenciais
from .ativacao import exige_api
from .forms import ConexaoForm
from .models import ConexaoWhatsApp, novo_token_webhook
from .provedores.comum import iguais
from .tipos import ResultadoTeste

log = logging.getLogger(__name__)

MAX_CORPO_WEBHOOK = 1_000_000


def _testar(request, conexao):
    """Chama o provedor e guarda o resultado. O texto devolvido nunca contém credenciais."""
    chave = f"wa-teste:{request.user.pk}"
    if ratelimit.excedeu(chave, 10):
        messages.warning(request, "Muitos testes seguidos. Aguarde um minuto.")
        return
    ratelimit.registrar(chave, 60)
    try:
        resultado = registro.criar_provedor(conexao).testar_conexao()
    except ErroCredenciais:
        resultado = ResultadoTeste(ok=False, mensagem="As credenciais salvas não puderam ser lidas. Digite-as de novo e salve.")
    except Exception as exc:  # noqa: BLE001
        log.error("Falha ao testar a conexão do WhatsApp (%s)", type(exc).__name__)
        resultado = ResultadoTeste(ok=False, mensagem="Erro inesperado ao testar a conexão.")
    conexao.status = ConexaoWhatsApp.Status.CONECTADA if resultado.ok else ConexaoWhatsApp.Status.ERRO
    conexao.numero_exibido = resultado.numero[:30] if resultado.ok else ""
    conexao.ultimo_erro = "" if resultado.ok else resultado.mensagem[:300]
    conexao.testada_em = timezone.now()
    conexao.save()
    LogAtividade.registrar(
        request.user, "whatsapp.teste", f"Testou a conexão do WhatsApp ({conexao.get_provedor_display()}): {'conectada' if resultado.ok else 'com erro'}",
        conexao, request,
    )
    (messages.success if resultado.ok else messages.error)(request, resultado.mensagem)


@exige_api
@perfil_requerido(*SOMENTE_ADMIN)
def conexao(request):
    con = ConexaoWhatsApp.atual()
    if request.method == "POST":
        acao = request.POST.get("acao", "salvar")
        if acao == "novo_token":
            con.webhook_token = novo_token_webhook()
            con.save(update_fields=["webhook_token", "atualizado_em"])
            LogAtividade.registrar(request.user, "whatsapp.conexao", "Gerou um novo token para o webhook do WhatsApp", con, request)
            messages.success(request, "Novo token gerado. Cadastre a URL nova no painel da Meta; a antiga deixou de funcionar.")
            return redirect("whatsapp:conexao")
        form = ConexaoForm(request.POST, conexao=con)
        if form.is_valid():
            form.salvar()
            LogAtividade.registrar(request.user, "whatsapp.conexao", f"Salvou a conexão do WhatsApp ({con.get_provedor_display()})", con, request)
            if acao == "testar":
                _testar(request, con)
            else:
                messages.success(request, "Conexão salva. Clique em “Salvar e testar” para liberar os disparos.")
            return redirect("whatsapp:conexao")
    else:
        form = ConexaoForm(conexao=con)
    return render(request, "whatsapp/conexao.html", {
        "form": form, "con": con, "ilegivel": form.ilegivel, "grupos": form.grupos(),
        "url_webhook": url_absoluta(request, reverse("whatsapp:webhook", args=[con.webhook_token])), "aviso_local": base_local(request),
    })


# ------------------------------------------------------------------ webhook (público)
def _texto(corpo, status=200):
    return HttpResponse(corpo, status=status, content_type="text/plain; charset=utf-8")


@never_cache
@csrf_exempt
@exige_api
@require_http_methods(["GET", "POST"])
def webhook(request, token):
    """Quem chama é o provedor: sem login e sem CSRF. A conexão é achada pelo token da URL (secreto, aleatório) e o
    corpo é conferido pela assinatura do provedor antes de qualquer processamento. Não registra corpo nem telefones."""
    con = ConexaoWhatsApp.objects.filter(pk=1).first()
    definicao = registro.definicao(con.provedor) if con else None
    if con is None or definicao is None or not iguais(token, con.webhook_token):
        return _texto("Not found", 404)

    if request.method == "GET":
        if definicao.responder_verificacao is not None:
            resposta = definicao.responder_verificacao({k: request.GET.get(k, "") for k in request.GET}, con.webhook_token)
            if resposta is not None:
                return _texto(resposta[1], resposta[0])
        return _texto("ok")

    try:
        declarado = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declarado = 0
    if declarado > MAX_CORPO_WEBHOOK:
        return _texto("Payload too large", 413)
    try:
        corpo = request.body
    except (RequestDataTooBig, RawPostDataException):
        return _texto("Payload too large", 413)
    if len(corpo) > MAX_CORPO_WEBHOOK:
        return _texto("Payload too large", 413)

    if definicao.assinatura_valida is not None:
        try:
            credenciais = con.credenciais_abertas()
        except ErroCredenciais:  # sem como validar: melhor pedir nova tentativa do que aceitar sem conferir
            return _texto("Service unavailable", 503)
        if not definicao.assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256", ""), credenciais):
            return _texto("Invalid signature", 401)

    try:
        eventos = definicao.interpretar_webhook(corpo)
        if eventos:
            aplicar_eventos(eventos)
    except Exception as exc:  # noqa: BLE001 — responde erro para o provedor tentar de novo; o log não leva dados do corpo
        log.error("Falha ao processar o webhook do WhatsApp (%s)", type(exc).__name__)
        return _texto("Error", 500)
    return _texto("ok")
