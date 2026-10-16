"""Criptografia das credenciais do WhatsApp em repouso (Fernet, chave derivada da SECRET_KEY).

Quem tiver só o banco de dados não lê o token: falta a SECRET_KEY, que fica em DJANGO_SECRET_KEY ou no
volume de dados (arquivo `secret_key`). Se a SECRET_KEY mudar, as credenciais salvas ficam ilegíveis e é
preciso digitá-las de novo (ou listar a chave antiga em SECRET_KEY_FALLBACKS, que também é aceita aqui).
"""
import base64
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

VERSAO = "v1:"
_CONTEXTO = b"rrpa.whatsapp.credenciais"


class ErroCredenciais(Exception):
    """Credenciais salvas que não podem ser lidas (SECRET_KEY trocada ou dado corrompido)."""


def _chave_derivada(segredo):
    bruta = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_CONTEXTO).derive(str(segredo).encode("utf-8"))
    return base64.urlsafe_b64encode(bruta)


def _fernet():
    segredos = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]
    return MultiFernet([Fernet(_chave_derivada(s)) for s in segredos])


def cifrar_json(valor):
    texto = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    return VERSAO + _fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def decifrar_json(cifrado):
    if not cifrado.startswith(VERSAO):
        raise ErroCredenciais("Formato de credenciais desconhecido.")
    try:
        return json.loads(_fernet().decrypt(cifrado[len(VERSAO):].encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError) as exc:
        raise ErroCredenciais("As credenciais salvas não puderam ser lidas.") from exc


def mascarar(valor):
    """Versão exibível de um segredo: só os 4 últimos caracteres (e só se o segredo for longo)."""
    if not valor:
        return ""
    return "••••••••" + valor[-4:] if len(valor) >= 16 else "••••••••"
