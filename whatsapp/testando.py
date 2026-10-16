"""Apoio dos testes do WhatsApp. Nenhum teste pode chegar à rede: tudo passa por requests.request simulado."""
from unittest import mock

from django.test import override_settings

from .models import ConexaoWhatsApp

TOKEN = "EAAG" + "t0kenSecreto" * 4
SEGREDO_APP = "segredo-do-app-0123456789"
CREDENCIAIS_META = {"phone_number_id": "123456789012345", "access_token": TOKEN, "app_secret": SEGREDO_APP, "api_version": "v21.0"}


def resposta(status=200, dados=None):
    """Resposta falsa do `requests`."""
    falsa = mock.Mock(status_code=status)
    falsa.content = b"{}" if dados is not None else b""
    falsa.json.return_value = dados
    return falsa


class SemRede:
    """Mistura para os testes da API: liga RRPA_WHATSAPP_API e, sem simular, qualquer chamada de rede derruba o teste.

    `self.rede` é o requests.request simulado: combine a resposta com `self.responder(...)` (ou ponha uma exceção em
    `self.rede.side_effect`) e confira as chamadas em `self.rede.call_args`.
    """

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(RRPA_WHATSAPP_API=True))
        aviso = AssertionError("o teste tentou usar a rede sem simular a resposta")
        self.rede = self.enterContext(mock.patch("whatsapp.provedores.comum.requests.request", side_effect=aviso))
        self.enterContext(mock.patch("requests.Session.send", side_effect=aviso))  # rede de segurança para outros caminhos

    def responder(self, *respostas):
        """Uma resposta vale para todas as chamadas; várias valem uma por chamada, na ordem."""
        self.rede.side_effect = None
        if len(respostas) == 1:
            self.rede.return_value = respostas[0]
        else:
            self.rede.side_effect = list(respostas)


def conectar(provedor="simulador", credenciais=None, limite=20, **campos):
    """Conexão pronta para enviar (status "conectada"). O Simulador sem taxa de falhas nunca falha."""
    con = ConexaoWhatsApp.atual()
    con.provedor = provedor
    if credenciais is None:
        credenciais = {"taxa_falhas": "0"} if provedor == "simulador" else dict(CREDENCIAIS_META)
    con.guardar_credenciais(credenciais)
    con.status = ConexaoWhatsApp.Status.CONECTADA
    con.limite_por_minuto = limite
    for nome, valor in campos.items():
        setattr(con, nome, valor)
    con.save()
    return con
