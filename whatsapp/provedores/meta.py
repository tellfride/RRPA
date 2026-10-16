"""WhatsApp Cloud API — API oficial da Meta (WhatsApp Business Platform).

Envio de texto e de template aprovado (parâmetros {{1}}, {{2}}...), teste de conexão e webhook
(status de entrega/leitura e mensagens recebidas), com validação da assinatura X-Hub-Signature-256.
"""
import hashlib
import hmac
import json
import re
from urllib.parse import quote

from whatsapp.tipos import (
    CampoProvedor, DefinicaoProvedor, EventoWebhook, MensagemSaida, ResultadoEnvio, ResultadoTeste,
)

from . import comum

VERSAO_PADRAO = "v21.0"

# Erros que indicam limite de taxa ou falha passageira: vale tentar de novo mais tarde.
CODIGOS_REPETIVEIS = frozenset({1, 2, 4, 130429, 131048, 131056, 80007})

ERROS = {
    0: "Falha de autenticação na API da Meta",
    4: "Limite de chamadas do app atingido",
    100: "Parâmetro inválido na requisição",
    190: "Token de acesso inválido ou expirado",
    200: "Permissão insuficiente no token de acesso",
    368: "Conta temporariamente bloqueada por violação de políticas",
    130429: "Limite de vazão da API atingido",
    131026: "Mensagem não entregável (o número não usa WhatsApp ou não aceitou os termos)",
    131030: "Número do destinatário não está na lista de números de teste permitidos",
    131031: "Conta do WhatsApp Business bloqueada ou restrita",
    131042: "Problema de pagamento na conta do WhatsApp Business",
    131047: "Passaram mais de 24 h desde a última mensagem da pessoa: é preciso usar um template aprovado",
    131048: "Limite de envio atingido por denúncias de spam (qualidade do número)",
    131049: "A Meta não entregou esta mensagem de marketing para preservar a experiência da pessoa",
    131050: "A pessoa optou por não receber mensagens de marketing",
    131051: "Tipo de mensagem não suportado",
    131056: "Muitas mensagens para a mesma pessoa em pouco tempo",
    132000: "Quantidade de parâmetros não confere com o template aprovado",
    132001: "Template inexistente ou não aprovado neste idioma",
    132005: "Texto do template ficou longo demais depois de preencher os parâmetros",
    132007: "O conteúdo viola a política de formatação do template",
    132012: "Formato dos parâmetros não confere com o template aprovado",
    132015: "Template pausado por baixa qualidade",
    132016: "Template desativado por baixa qualidade",
    133010: "Número de telefone não registrado na Cloud API",
    80007: "Limite de taxa da conta do WhatsApp Business atingido",
}
QUALIDADE = {"GREEN": "alta", "YELLOW": "média", "RED": "baixa", "UNKNOWN": "ainda não avaliada"}
STATUS_DA_META = {"sent": "enviada", "delivered": "entregue", "read": "lida", "failed": "falhou"}

LIMITE_PARAMETRO = 1000  # a Meta aceita até 1024 caracteres por parâmetro


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def descrever_erro(erro):
    """Erro da Cloud API em português: texto conhecido (ou o da Meta) + código. Nunca devolve o corpo todo."""
    if not isinstance(erro, dict):
        return None
    codigo = _inteiro(erro.get("code"))
    dados = erro.get("error_data") if isinstance(erro.get("error_data"), dict) else {}
    bruto = ERROS.get(codigo) or erro.get("title") or dados.get("details") or erro.get("message")
    if not bruto:
        return f"Erro {codigo} do provedor" if codigo is not None else None
    return comum.truncar(f"{bruto} (código {codigo})" if codigo is not None else str(bruto), 300)


def parametro_de_template(valor):
    """A Meta rejeita parâmetros vazios, com quebra de linha, tabulação ou 4+ espaços seguidos."""
    texto = re.sub(r"[\r\n\t]+", " ", valor or "")
    texto = re.sub(r" {4,}", "   ", texto).strip()
    return comum.truncar(texto, LIMITE_PARAMETRO) or "-"


def montar_corpo(mensagem: MensagemSaida):
    """Corpo JSON de POST /{id-do-número}/messages."""
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": comum.so_digitos(mensagem.para)}
    if mensagem.template is not None:
        template = {"name": mensagem.template.nome, "language": {"code": mensagem.template.idioma}}
        if mensagem.template.parametros:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": parametro_de_template(p)} for p in mensagem.template.parametros],
            }]
        return {**base, "type": "template", "template": template}
    return {**base, "type": "text", "text": {"body": mensagem.texto, "preview_url": True}}


def interpretar_resposta_envio(status, dados):
    corpo = dados if isinstance(dados, dict) else {}
    mensagens = corpo.get("messages")
    id_mensagem = mensagens[0].get("id") if isinstance(mensagens, list) and mensagens and isinstance(mensagens[0], dict) else None
    if 200 <= status < 300 and id_mensagem:
        return ResultadoEnvio(ok=True, id_mensagem=str(id_mensagem))
    erro_obj = corpo.get("error") if isinstance(corpo.get("error"), dict) else None
    codigo = _inteiro(erro_obj.get("code")) if erro_obj else None
    erro = descrever_erro(erro_obj)
    if not erro:
        erro = "Resposta do provedor sem o id da mensagem." if 200 <= status < 300 else f"Erro HTTP {status} do provedor."
    repetir = status == 429 or status >= 500 or (codigo is not None and codigo in CODIGOS_REPETIVEIS)
    return ResultadoEnvio(ok=False, erro=erro, tentar_de_novo=repetir)


def url_base(versao):
    return f"https://graph.facebook.com/{quote((versao or '').strip() or VERSAO_PADRAO, safe='')}"


class Meta:
    chave = "meta"

    def __init__(self, credenciais):
        self.cred = credenciais or {}

    def _completa(self):
        return bool(self.cred.get("phone_number_id") and self.cred.get("access_token"))

    def _cabecalhos(self, json_body=False):
        cabecalhos = {"Authorization": f"Bearer {self.cred.get('access_token', '')}"}
        if json_body:
            cabecalhos["Content-Type"] = "application/json"
        return cabecalhos

    def enviar(self, mensagem):
        if not self._completa():
            return ResultadoEnvio(ok=False, erro="Credenciais da Meta incompletas.")
        if not comum.destino_valido(mensagem.para):
            return ResultadoEnvio(ok=False, erro="Número de destino inválido.")
        url = f"{url_base(self.cred.get('api_version'))}/{quote(self.cred['phone_number_id'], safe='')}/messages"
        try:
            resposta = comum.requisicao("POST", url, self._cabecalhos(json_body=True), montar_corpo(mensagem))
        except Exception as exc:  # noqa: BLE001 — qualquer falha vira resultado, sem texto do erro
            return comum.falha_de_envio(exc)
        return interpretar_resposta_envio(resposta.status, resposta.dados)

    def testar_conexao(self):
        if not self._completa():
            return ResultadoTeste(ok=False, mensagem="Informe o ID do número e o token de acesso.")
        url = (f"{url_base(self.cred.get('api_version'))}/{quote(self.cred['phone_number_id'], safe='')}"
               "?fields=display_phone_number,verified_name,quality_rating")
        try:
            resposta = comum.requisicao("GET", url, self._cabecalhos())
        except comum.ErroDeRede as exc:
            return ResultadoTeste(ok=False, mensagem=exc.mensagem)
        except Exception:  # noqa: BLE001
            return ResultadoTeste(ok=False, mensagem="Erro inesperado ao testar a conexão.")
        dados = resposta.dados if isinstance(resposta.dados, dict) else None
        if not resposta.ok or dados is None or dados.get("error"):
            mensagem = descrever_erro(dados.get("error") if dados else None) or f"A Meta respondeu com erro HTTP {resposta.status}."
            return ResultadoTeste(ok=False, mensagem=mensagem)
        numero = str(dados.get("display_phone_number") or "")
        nome = str(dados.get("verified_name") or "número verificado")
        partes = [f"Conectado à Meta: {nome}{f' ({numero})' if numero else ''}"]
        qualidade = QUALIDADE.get(str(dados.get("quality_rating") or "").upper())
        if qualidade:
            partes.append(f"qualidade {qualidade}")
        return ResultadoTeste(ok=True, mensagem=" · ".join(partes) + ".", numero=numero)


# ------------------------------------------------------------------ webhook
_ROTULO_MIDIA = {
    "image": "[imagem]", "audio": "[áudio]", "voice": "[áudio]", "video": "[vídeo]", "document": "[documento]",
    "sticker": "[figurinha]", "location": "[localização]", "contacts": "[contato]", "reaction": "[reação]",
}


def _caminho(obj, *chaves):
    for chave in chaves:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(chave)
    return obj


def _texto_recebido(mensagem):
    texto = (_caminho(mensagem, "text", "body") or _caminho(mensagem, "button", "text")
             or _caminho(mensagem, "interactive", "button_reply", "title")
             or _caminho(mensagem, "interactive", "list_reply", "title"))
    if texto:
        return str(texto)
    tipo = mensagem.get("type")
    return _ROTULO_MIDIA.get(tipo, f"[{tipo}]" if tipo else "")


def interpretar_webhook(corpo):
    """Corpo do webhook -> eventos. Formato: {entry:[{changes:[{value:{statuses:[...], messages:[...]}}]}]}."""
    try:
        dados = json.loads(corpo)
    except (ValueError, TypeError):
        return []
    eventos = []
    entradas = dados.get("entry") if isinstance(dados, dict) else None
    for entrada in entradas if isinstance(entradas, list) else []:
        mudancas = entrada.get("changes") if isinstance(entrada, dict) else None
        for mudanca in mudancas if isinstance(mudancas, list) else []:
            valor = mudanca.get("value") if isinstance(mudanca, dict) else None
            if isinstance(valor, dict):
                _eventos_do_valor(valor, eventos)
    return eventos


def _eventos_do_valor(valor, saida):
    for s in valor.get("statuses") if isinstance(valor.get("statuses"), list) else []:
        if not isinstance(s, dict):
            continue
        status = STATUS_DA_META.get(str(s.get("status") or "").lower())
        if not s.get("id") or not status:
            continue
        erro = ""
        if status == "falhou":
            erros = s.get("errors")
            erro = descrever_erro(erros[0] if isinstance(erros, list) and erros else None) or "Falha na entrega informada pela Meta."
        saida.append(EventoWebhook(
            tipo="status", id_mensagem=str(s["id"]), status=status, erro=erro, quando=comum.unix_para_datetime(s.get("timestamp")),
        ))
    for m in valor.get("messages") if isinstance(valor.get("messages"), list) else []:
        if not isinstance(m, dict):
            continue
        remetente = comum.so_digitos(m.get("from"))
        if remetente:
            saida.append(EventoWebhook(
                tipo="recebida", telefone=remetente, texto=_texto_recebido(m), quando=comum.unix_para_datetime(m.get("timestamp")),
            ))


def calcular_assinatura(segredo, corpo):
    return "sha256=" + hmac.new(segredo.encode("utf-8"), corpo, hashlib.sha256).hexdigest()


def assinatura_valida(corpo, cabecalho, credenciais):
    """X-Hub-Signature-256. Sem a chave secreta do app não há como validar, então recusa (falha fechada)."""
    segredo = (credenciais or {}).get("app_secret")
    if not segredo or not cabecalho or not cabecalho.lower().startswith("sha256="):
        return False
    return comum.iguais("sha256=" + cabecalho[7:].strip().lower(), calcular_assinatura(segredo, corpo))


_DESAFIO = re.compile(r"^[A-Za-z0-9_.-]{1,512}$")


def responder_verificacao(consulta, token_do_webhook):
    """Teste de URL da Meta: GET ?hub.mode=subscribe&hub.verify_token=...&hub.challenge=... -> devolve o desafio."""
    modo = consulta.get("hub.mode")
    if not modo:
        return None
    token = consulta.get("hub.verify_token") or ""
    desafio = consulta.get("hub.challenge") or ""
    if modo != "subscribe" or not token_do_webhook or not comum.iguais(token, token_do_webhook):
        return 403, "Forbidden"
    if not _DESAFIO.match(desafio):
        return 400, "Bad request"
    return 200, desafio


META = DefinicaoProvedor(
    chave="meta",
    rotulo="WhatsApp Cloud API (Meta)",
    descricao="API oficial do WhatsApp Business, hospedada pela Meta. Exige um app no Meta for Developers e templates aprovados.",
    oficial=True,
    passos=[
        "No Meta for Developers (developers.facebook.com), crie um app do tipo Empresa e adicione o produto WhatsApp.",
        "Em WhatsApp › Configuração da API, adicione e verifique o número da unidade. Copie o ID do número de telefone.",
        "No Gerenciador de Negócios, crie um Usuário do Sistema, atribua o app e a conta do WhatsApp e gere um token permanente "
        "com as permissões whatsapp_business_messaging e whatsapp_business_management.",
        "Copie a Chave secreta do app (Configurações do app › Básico): ela valida a assinatura de cada aviso do webhook.",
        "Salve aqui e clique em “Salvar e testar”. Depois, em WhatsApp › Configuração, cadastre a URL do webhook e o token "
        "de verificação mostrados nesta tela e assine o campo “messages”.",
        "No WhatsApp Manager, crie e aguarde a aprovação dos templates. Em cada modelo de mensagem do R-RPA, informe o nome do "
        "template aprovado e a ordem dos campos que preenchem {{1}}, {{2}}…",
    ],
    campos=[
        CampoProvedor("phone_number_id", "ID do número de telefone", obrigatorio=True, exemplo="123456789012345",
                      ajuda="Phone number ID, em WhatsApp › Configuração da API."),
        CampoProvedor("business_account_id", "ID da conta do WhatsApp Business (WABA)", exemplo="109876543210987",
                      ajuda="Opcional. Só ajuda a identificar a conta."),
        CampoProvedor("access_token", "Token de acesso permanente", tipo="password", obrigatorio=True, segredo=True,
                      ajuda="Token de Usuário do Sistema. Fica criptografado e não volta a aparecer nesta tela."),
        CampoProvedor("app_secret", "Chave secreta do app (App Secret)", tipo="password", obrigatorio=True, segredo=True,
                      ajuda="Sem ela o sistema recusa os avisos do webhook, porque não consegue confirmar que vieram da Meta."),
        CampoProvedor("api_version", "Versão da Graph API", exemplo=VERSAO_PADRAO,
                      ajuda=f"Deixe em branco para usar {VERSAO_PADRAO}."),
    ],
    exige_template=True,
    criar=lambda credenciais: Meta(credenciais),
    interpretar_webhook=interpretar_webhook,
    assinatura_valida=assinatura_valida,
    responder_verificacao=responder_verificacao,
)
