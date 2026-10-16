"""Simulador: não usa a rede nem envia nada. Gera ids "sbx_…", falha na taxa configurada e, com o tempo, o
worker aplica "entregue" e "lida" como se viessem do webhook (veja `eventos_pendentes`)."""
import random
import secrets
from datetime import timedelta

from whatsapp.tipos import (
    CampoProvedor, DefinicaoProvedor, EventoWebhook, MensagemSaida, ResultadoEnvio, ResultadoTeste,
)

from . import comum
from .meta import interpretar_webhook as interpretar_webhook_da_meta

PREFIXO = "sbx_"
TAXA_PADRAO = 0.05
TAXA_MAXIMA = 0.5
# Entrega e leitura simuladas acontecem em até ~10 s; passado 1 min não há mais o que aplicar.
JANELA_SEGUNDOS = 60


def ler_taxa(valor):
    """"0,1" | "0.1" | "" -> número entre 0 e 0,5 (padrão 0,05)."""
    bruto = str(valor or "").strip().replace(",", ".")
    if not bruto:
        return TAXA_PADRAO
    try:
        taxa = float(bruto)
    except ValueError:
        return TAXA_PADRAO
    if taxa != taxa:  # NaN
        return TAXA_PADRAO
    return min(TAXA_MAXIMA, max(0.0, taxa))


def plano(id_mensagem):
    """(segundos até a entrega, segundos até a leitura ou None). Decidido só pelo id, então é repetível."""
    sorteio = random.Random(int(id_mensagem[len(PREFIXO):] or "0", 16))
    entregue = round(1.0 + sorteio.random() * 3.0, 3)
    lida = max(entregue + 0.5, round(3.0 + sorteio.random() * 7.0, 3)) if sorteio.random() < 0.6 else None
    return entregue, lida


def eventos_pendentes(id_mensagem, enviada_em, agora):
    """Eventos de entrega/leitura que já deveriam ter acontecido (aplicá-los de novo é inofensivo)."""
    if not id_mensagem.startswith(PREFIXO) or enviada_em is None:
        return []
    entregue, lida = plano(id_mensagem)
    eventos = []
    for status, segundos in (("entregue", entregue), ("lida", lida)):
        if segundos is not None and agora >= enviada_em + timedelta(seconds=segundos):
            eventos.append(EventoWebhook(tipo="status", id_mensagem=id_mensagem, status=status, quando=enviada_em + timedelta(seconds=segundos)))
    return eventos


class Simulador:
    chave = "simulador"

    def __init__(self, credenciais, sorteio=random.random):
        self.taxa = ler_taxa((credenciais or {}).get("taxa_falhas"))
        self.sorteio = sorteio

    def enviar(self, mensagem: MensagemSaida):
        if not comum.destino_valido(mensagem.para):
            return ResultadoEnvio(ok=False, erro="Número de destino inválido.")
        if not (mensagem.texto or "").strip() and mensagem.template is None:
            return ResultadoEnvio(ok=False, erro="Mensagem vazia.")
        if self.sorteio() < self.taxa:
            return ResultadoEnvio(ok=False, erro="Falha simulada pelo Simulador (nenhuma mensagem real foi enviada).")
        return ResultadoEnvio(ok=True, id_mensagem=PREFIXO + secrets.token_hex(12))

    def testar_conexao(self):
        return ResultadoTeste(ok=True, mensagem="Simulador pronto: nenhuma mensagem real é enviada.", numero="Simulador")


SIMULADOR = DefinicaoProvedor(
    chave="simulador",
    rotulo="Simulador (testes)",
    descricao="Finge os envios, entregas e leituras sem mandar nenhuma mensagem real. Serve para treinar a equipe e testar um disparo.",
    oficial=False,
    passos=[
        "Não precisa de credenciais: depois de salvar e testar, o simulador já funciona.",
        "Os disparos aparecem como enviados e, poucos segundos depois, como entregues e lidos, sem nenhuma mensagem real.",
        "Use a taxa de falhas para ver como o sistema trata os erros de envio.",
        "Antes de contatar candidatos de verdade, troque o provedor para a Meta e teste de novo.",
    ],
    campos=[
        CampoProvedor("taxa_falhas", "Taxa de falhas simuladas", exemplo="0,05",
                      ajuda="Proporção de envios que falham de propósito, de 0 a 0,5 (0,05 = 5%)."),
    ],
    exige_template=False,
    criar=lambda credenciais: Simulador(credenciais),
    # Aceita o formato da Cloud API sem assinatura, para testar o webhook localmente com curl.
    interpretar_webhook=interpretar_webhook_da_meta,
)
