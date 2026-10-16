"""Aplica os eventos do webhook: andamento das mensagens dos disparos e respostas dos candidatos.

Quem responde SAIR (ou PARAR, STOP...) passa a ter Candidato.whatsapp_ok = False e perde o que estava na fila.
Sem dados pessoais em LogAtividade: só o número interno do cadastro.
"""
import re
import unicodedata
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from candidatos.models import Candidato
from core.models import LogAtividade
from core.utils import so_digitos

from .models import MensagemDisparo, MensagemRecebida

S = MensagemDisparo.Status
ORDEM = {"enviada": 1, "entregue": 2, "lida": 3}
# Resposta inteira igual a uma destas (sem acento, pontuação ou diferença de maiúsculas) é pedido de saída.
PALAVRAS_DE_SAIDA = frozenset({"SAIR", "PARAR", "STOP", "DESCADASTRAR", "REMOVER", "PARAR PROMOCOES", "STOP PROMOTIONS"})
MOTIVO_SAIDA = "Pediu para não receber mensagens"


@dataclass
class Resultado:
    correspondidas: int = 0  # eventos de status cuja mensagem foi encontrada
    atualizadas: int = 0  # mensagens cujo status mudou
    recebidas: int = 0  # respostas registradas
    saidas: int = 0  # candidatos que passaram a "não aceita WhatsApp" nesta chamada


def normalizar_resposta(texto):
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn").upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9\s]+", "", sem_acento)).strip()


def pediu_saida(texto):
    return normalizar_resposta(texto) in PALAVRAS_DE_SAIDA


def proximo_status(atual, novo):
    """Próximo status da mensagem diante de um evento, ou None se nada muda.

    O status só avança (enviada < entregue < lida); "falhou" não vale depois de entregue/lida; entrega ou leitura
    confirmadas prevalecem sobre uma falha anterior; "não enviada" nunca muda.
    """
    if atual == S.IGNORADA:
        return None
    if novo == "falhou":
        return None if atual in (S.ENTREGUE, S.LIDA, S.FALHOU) else "falhou"
    if novo not in ORDEM:
        return None
    if atual == S.FALHOU:
        return None if novo == "enviada" else novo
    return novo if ORDEM[novo] > ORDEM.get(atual, 0) else None


def _aplicar_status(evento):
    """(achou a mensagem, mudou o status)."""
    id_provedor = (evento.id_mensagem or "").strip()
    if not id_provedor:  # nunca casa com mensagens que ainda não têm id no provedor
        return False, False
    for _ in range(2):  # a troca é condicional ao status lido: se outro aviso chegou junto, lê de novo
        linha = MensagemDisparo.objects.filter(id_provedor=id_provedor).values("pk", "status", "enviada_em", "entregue_em", "lida_em").first()
        if linha is None:
            return False, False
        novo = proximo_status(linha["status"], evento.status)
        if novo is None:
            return True, False
        quando = evento.quando or timezone.now()
        dados = {"status": novo, "atualizado_em": timezone.now()}
        if novo == "falhou":
            dados["erro"] = (evento.erro or "Falha na entrega informada pelo provedor.")[:300]
        else:
            if not linha["enviada_em"]:
                dados["enviada_em"] = quando
            if novo in ("entregue", "lida") and not linha["entregue_em"]:
                dados["entregue_em"] = quando
            if novo == "lida" and not linha["lida_em"]:
                dados["lida_em"] = quando
            if linha["status"] == S.FALHOU:
                dados["erro"] = ""
        if MensagemDisparo.objects.filter(pk=linha["pk"], status=linha["status"]).update(**dados):
            return True, True
    return True, False


def variantes_do_telefone(bruto):
    """Telefones de cadastro possíveis para quem escreveu: sem o 55 e, em celular antigo, com o nono dígito."""
    digitos = so_digitos(bruto)
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return []
    variantes = [digitos]
    if len(digitos) == 10 and digitos[2] in "6789":  # o WhatsApp informa muitos celulares sem o 9
        variantes.append(f"{digitos[:2]}9{digitos[2:]}")
    return variantes


def _aplicar_recebida(evento):
    """(registrou a resposta, quantos candidatos saíram da lista agora)."""
    digitos = so_digitos(evento.telefone)
    if not digitos:
        return False, 0
    candidatos = list(Candidato.objects.filter(telefone__in=variantes_do_telefone(digitos))) if variantes_do_telefone(digitos) else []
    saida = pediu_saida(evento.texto)
    novos = []
    with transaction.atomic():
        MensagemRecebida.objects.create(
            telefone=digitos[:20], texto=(evento.texto or "")[:500], candidato=candidatos[0] if candidatos else None, pediu_saida=saida,
        )
        if saida and candidatos:
            agora = timezone.now()
            # Quem divide o número (família, por exemplo) também deixa de receber: o pedido vem do dono da linha.
            novos = [c for c in candidatos if c.whatsapp_ok is not False]
            Candidato.objects.filter(pk__in=[c.pk for c in candidatos]).update(whatsapp_ok=False, whatsapp_ok_em=agora, atualizado_em=agora)
            MensagemDisparo.objects.filter(candidato__in=candidatos, status=S.FILA).update(status=S.IGNORADA, erro=MOTIVO_SAIDA, atualizado_em=agora)
    for candidato in novos:
        LogAtividade.registrar(None, "whatsapp.sair", "Pediu para não receber mensagens por WhatsApp (resposta SAIR)", candidato)
    return True, len(novos)


def aplicar_eventos(eventos):
    resultado = Resultado()
    # Dentro do mesmo aviso, aplica em ordem de progresso (enviada, entregue, lida) e deixa as falhas por último.
    ordem = lambda e: 5 if e.status == "falhou" else ORDEM.get(e.status, 0)  # noqa: E731
    for evento in sorted((e for e in eventos if e.tipo == "status"), key=ordem):
        achou, mudou = _aplicar_status(evento)
        resultado.correspondidas += int(achou)
        resultado.atualizadas += int(mudou)
    for evento in (e for e in eventos if e.tipo == "recebida"):
        registrou, saidas = _aplicar_recebida(evento)
        resultado.recebidas += int(registrou)
        resultado.saidas += saidas
    return resultado
