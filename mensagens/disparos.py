"""Regras dos disparos de WhatsApp: quem pode receber, a mensagem de cada pessoa, criação e controle.

Nada aqui grava nome, telefone ou texto em LogAtividade: só números de disparo e contagens.
"""
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from core.excecoes import RegraDeNegocio
from core.models import Configuracao, LogAtividade
from core.utils import validar_telefone
from whatsapp import registro
from whatsapp.models import ConexaoWhatsApp

from . import render as mensagem
from .models import Disparo, MensagemDisparo

MAX_TENTATIVAS = 3  # tentativas automáticas por mensagem (só para falhas passageiras)
MAX_TENTATIVAS_MANUAIS = 5  # limite total, contando o "Reenviar falhas"
MAX_DESTINATARIOS = 1000
RITMO_PADRAO = 20


@dataclass
class Destinatario:
    candidato: object
    texto: str = ""
    parametros: list = field(default_factory=list)
    faltando: list = field(default_factory=list)  # campos do modelo sem valor para esta pessoa
    motivo: str = ""  # vazio = vai receber; senão, por que não vai


def variaveis_do_modelo(modelo):
    """Campos que preenchem {{1}}, {{2}}... do template aprovado, na ordem (só nomes de campo conhecidos)."""
    bruto = modelo.template_oficial_variaveis if modelo is not None else []
    return [v for v in bruto if isinstance(v, str) and v in mensagem.NOMES_VALIDOS][:20] if isinstance(bruto, list) else []


def motivo_bloqueio(candidato, conexao):
    """Por que esta pessoa NÃO pode receber mensagem automática (ou "" se pode). Vale também na hora do envio."""
    if candidato is None:
        return "Cadastro removido"
    if candidato.whatsapp_ok is False:
        return "Pediu para não receber mensagens"
    if conexao is not None and conexao.exigir_autorizacao and candidato.whatsapp_ok is not True:
        return "Sem autorização registrada para WhatsApp"
    if not validar_telefone(candidato.telefone):
        return "Telefone inválido"
    return ""


def preparar(candidatos, modelo, usuario, request, conexao, exige_template):
    """Mensagem de cada pessoa, pronta para a prévia e para o disparo. A ordem da lista é mantida."""
    config = Configuracao.atual()
    variaveis = variaveis_do_modelo(modelo)
    usa_template = bool(modelo is not None and modelo.template_oficial_nome)
    telefones_vistos = set()
    saida = []
    for candidato in candidatos:
        item = Destinatario(candidato=candidato, motivo=motivo_bloqueio(candidato, conexao))
        if modelo is not None:
            vinculo = candidato.vinculos.filter(status="ativo").select_related("vaga").first()
            contexto = mensagem.montar_contexto(usuario, config, request, candidato=candidato, vinculo=vinculo)
            item.texto, item.faltando = mensagem.renderizar(modelo.texto, contexto)
            if usa_template:
                item.parametros = [contexto.get(v, "") for v in variaveis]
            if exige_template:  # só o template vai: o texto livre não é enviado
                item.faltando = [v for v in variaveis if v not in contexto]
        if not item.motivo:
            if item.faltando:
                item.motivo = "Faltam dados: " + ", ".join(item.faltando)
            elif candidato.telefone in telefones_vistos:
                item.motivo = "Telefone repetido neste disparo"
            else:
                telefones_vistos.add(candidato.telefone)
        saida.append(item)
    return saida


def _ritmo(valor, conexao):
    try:
        ritmo = int(valor)
    except (TypeError, ValueError):
        ritmo = RITMO_PADRAO
    return max(1, min(ritmo, conexao.limite_por_minuto))


@transaction.atomic
def criar_disparo(usuario, candidatos, modelo, ritmo=None, request=None):
    conexao = ConexaoWhatsApp.atual()
    if not conexao.pode_enviar:
        raise RegraDeNegocio("A conexão com o WhatsApp ainda não está pronta. O administrador precisa configurá-la e testá-la em Configurações.")
    if modelo is None or not modelo.ativo:
        raise RegraDeNegocio("Escolha um modelo de mensagem ativo.")
    exige_template = registro.definicao(conexao.provedor).exige_template
    if exige_template and not modelo.template_oficial_nome:
        raise RegraDeNegocio(
            f"A API oficial só envia template aprovado. Edite o modelo “{modelo.nome}” e informe o nome do template aprovado na Meta."
        )
    if len(candidatos) > MAX_DESTINATARIOS:
        raise RegraDeNegocio(f"Um disparo aceita até {MAX_DESTINATARIOS} pessoas. Divida a lista em partes.")
    destinatarios = preparar(candidatos, modelo, usuario, request, conexao, exige_template)
    if not any(not d.motivo for d in destinatarios):
        raise RegraDeNegocio("Nenhuma das pessoas escolhidas pode receber a mensagem. Veja o motivo de cada uma na lista.")

    agora = timezone.now()
    disparo = Disparo.objects.create(
        nome=f"{modelo.nome} · {timezone.localtime(agora):%d/%m %H:%M}"[:120], modelo=modelo, criado_por=usuario,
        ritmo_por_minuto=_ritmo(ritmo, conexao), provedor=conexao.provedor,
        template_nome=modelo.template_oficial_nome if exige_template else "",
        template_idioma=modelo.template_oficial_idioma or "pt_BR",
    )
    MensagemDisparo.objects.bulk_create([
        MensagemDisparo(
            disparo=disparo, candidato=d.candidato, nome_destino=d.candidato.nome, telefone=d.candidato.telefone,
            texto="" if d.motivo else d.texto, parametros=[] if d.motivo else d.parametros,
            status=MensagemDisparo.Status.IGNORADA if d.motivo else MensagemDisparo.Status.FILA, erro=d.motivo,
        )
        for d in destinatarios
    ])
    total = contagens(disparo)
    LogAtividade.registrar(
        usuario, "disparo.criado",
        f"Criou o disparo nº {disparo.pk}: {total['fila']} mensagem(ns) na fila e {total['ignoradas']} não enviada(s)", disparo, request,
    )
    return disparo


def contagens(disparo):
    por_status = dict(disparo.mensagens.values_list("status").annotate(n=Count("id")))

    def soma(*status):
        return sum(por_status.get(s, 0) for s in status)

    total = sum(por_status.values())
    fila = soma("fila", "enviando")
    return {
        "total": total, "fila": fila, "enviadas": soma("enviada", "entregue", "lida"), "entregues": soma("entregue", "lida"),
        "lidas": soma("lida"), "falhas": soma("falhou"), "ignoradas": soma("ignorada"),
        "resolvidas": total - fila, "pct": round(100 * (total - fila) / total) if total else 0,
        "reenviaveis": disparo.mensagens.filter(status="falhou", tentativas__lt=MAX_TENTATIVAS_MANUAIS).count(),
    }


def controlar(disparo, acao, usuario, request=None):
    """Pausar, retomar, cancelar ou reenviar as falhas. Devolve a frase de confirmação para a tela."""
    agora = timezone.now()
    S = Disparo.Status
    if acao == "pausar":
        mudou = Disparo.objects.filter(pk=disparo.pk, status=S.EM_ANDAMENTO).update(status=S.PAUSADO, motivo_pausa="Pausado pela equipe")
        if not mudou:
            raise RegraDeNegocio("Só é possível pausar um disparo em andamento.")
        LogAtividade.registrar(usuario, "disparo.pausado", f"Pausou o disparo nº {disparo.pk}", disparo, request)
        return "Disparo pausado. Nenhuma mensagem sai até você retomar."
    if acao == "retomar":
        if not ConexaoWhatsApp.atual().pode_enviar:
            raise RegraDeNegocio("A conexão com o WhatsApp não está pronta. Peça ao administrador para testá-la em Configurações.")
        mudou = Disparo.objects.filter(pk=disparo.pk, status=S.PAUSADO).update(status=S.EM_ANDAMENTO, motivo_pausa="")
        if not mudou:
            raise RegraDeNegocio("Só é possível retomar um disparo pausado.")
        LogAtividade.registrar(usuario, "disparo.retomado", f"Retomou o disparo nº {disparo.pk}", disparo, request)
        return "Disparo retomado."
    if acao == "cancelar":
        with transaction.atomic():
            mudou = Disparo.objects.filter(pk=disparo.pk, status__in=(S.EM_ANDAMENTO, S.PAUSADO)).update(
                status=S.CANCELADO, finalizado_em=agora, motivo_pausa="",
            )
            if not mudou:
                raise RegraDeNegocio("Este disparo já terminou.")
            MensagemDisparo.objects.filter(disparo=disparo, status=MensagemDisparo.Status.FILA).update(
                status=MensagemDisparo.Status.IGNORADA, erro="Disparo cancelado", atualizado_em=agora,
            )
        LogAtividade.registrar(usuario, "disparo.cancelado", f"Cancelou o disparo nº {disparo.pk}", disparo, request)
        return "Disparo cancelado. O que já foi enviado continua registrado."
    if acao == "reenviar":
        if disparo.status not in (S.EM_ANDAMENTO, S.CONCLUIDO):
            raise RegraDeNegocio("Só é possível reenviar as falhas de um disparo em andamento ou concluído.")
        if not ConexaoWhatsApp.atual().pode_enviar:
            raise RegraDeNegocio("A conexão com o WhatsApp não está pronta. Peça ao administrador para testá-la em Configurações.")
        with transaction.atomic():
            refeitas = MensagemDisparo.objects.filter(
                disparo=disparo, status=MensagemDisparo.Status.FALHOU, tentativas__lt=MAX_TENTATIVAS_MANUAIS,
            ).update(status=MensagemDisparo.Status.FILA, erro="", proxima_tentativa_em=None, atualizado_em=agora)
            if not refeitas:
                raise RegraDeNegocio("Não há falhas que possam ser reenviadas.")
            Disparo.objects.filter(pk=disparo.pk).update(status=S.EM_ANDAMENTO, finalizado_em=None)
        LogAtividade.registrar(usuario, "disparo.reenviado", f"Mandou reenviar {refeitas} falha(s) do disparo nº {disparo.pk}", disparo, request)
        return f"{refeitas} mensagem(ns) voltaram para a fila."
    raise RegraDeNegocio("Ação desconhecida.")


def apagar_dados_pessoais(candidato):
    """LGPD: remove nome, telefone e texto das mensagens de um candidato e cancela o que ainda estava na fila."""
    from .models import MensagemRecebida

    agora = timezone.now()
    MensagemDisparo.objects.filter(candidato=candidato, status__in=(MensagemDisparo.Status.FILA, MensagemDisparo.Status.ENVIANDO)).update(
        status=MensagemDisparo.Status.IGNORADA, erro="Cadastro removido", atualizado_em=agora,
    )
    MensagemDisparo.objects.filter(candidato=candidato).update(nome_destino="Cadastro removido", telefone="", texto="", parametros=[])
    MensagemRecebida.objects.filter(candidato=candidato).update(telefone="", texto="")
