"""Utilidades HTTP e de texto dos provedores. Nunca registre token, telefone ou texto de mensagem em log."""
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from whatsapp.tipos import ResultadoEnvio

TIMEOUT = 15


class ErroDeRede(Exception):
    """Falha de rede ou tempo esgotado ao falar com o provedor (sempre vale tentar de novo)."""

    def __init__(self, mensagem, tempo_esgotado=False):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.tempo_esgotado = tempo_esgotado


@dataclass
class RespostaHttp:
    status: int
    ok: bool
    dados: Any = None  # corpo lido como JSON (None se vazio ou inválido)


def requisicao(metodo, url, headers=None, json_body=None):
    """Requisição com tempo limite de 15 s, sem seguir redirecionamentos e sem expor o texto do erro de rede."""
    try:
        resposta = requests.request(
            metodo, url, headers=dict(headers or {}), json=json_body, timeout=TIMEOUT, allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise ErroDeRede("Tempo esgotado ao contatar o provedor.", tempo_esgotado=True) from exc
    except requests.RequestException as exc:
        raise ErroDeRede("Falha de rede ao contatar o provedor.") from exc
    dados = None
    if resposta.content:
        try:
            dados = resposta.json()
        except ValueError:
            dados = None
    return RespostaHttp(status=resposta.status_code, ok=200 <= resposta.status_code < 300, dados=dados)


def falha_de_envio(exc):
    """Converte uma exceção de envio em resultado (rede e tempo esgotado podem ser repetidos)."""
    if isinstance(exc, ErroDeRede):
        return ResultadoEnvio(ok=False, erro=exc.mensagem, tentar_de_novo=True)
    return ResultadoEnvio(ok=False, erro="Erro inesperado ao enviar a mensagem.", tentar_de_novo=True)


def so_digitos(valor):
    return "".join(c for c in str(valor or "") if c.isdigit())


def destino_valido(valor):
    """Número aceitável pelos provedores: só dígitos, com DDI, de 10 a 15 dígitos."""
    return 10 <= len(so_digitos(valor)) <= 15


def truncar(texto, limite):
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def iguais(a, b):
    """Comparação de textos em tempo constante."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def unix_para_datetime(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
