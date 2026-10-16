"""Processador da fila de disparos: roda no serviço `worker` (manage.py disparar --continuo).

Cada ciclo: devolve à fila o que ficou preso em "enviando", envia as mensagens dos disparos em andamento
respeitando o ritmo, aplica entrega/leitura do Simulador e conclui os disparos sem pendências.

Ritmo: no máximo `limite_por_minuto` chamadas ao provedor por minuto na conexão toda e `ritmo_por_minuto` por disparo.
Falha passageira (limite da API, 5xx, rede) volta para a fila e espera um tempo crescente; depois de
MAX_TENTATIVAS a mensagem fica como "falhou" (a equipe pode mandar reenviar).
Nada aqui escreve nome, telefone ou texto em log: só ids e contagens.
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import LogAtividade
from core.utils import telefone_whatsapp
from whatsapp import registro
from whatsapp.cripto import ErroCredenciais
from whatsapp.models import ConexaoWhatsApp
from whatsapp.provedores import simulador
from whatsapp.tipos import ChamadaTemplate, MensagemSaida, ResultadoEnvio

from . import eventos
from .disparos import MAX_TENTATIVAS, contagens, motivo_bloqueio
from .models import Disparo, MensagemDisparo

log = logging.getLogger(__name__)

S = MensagemDisparo.Status
MAXIMO_POR_CICLO = 50
JANELA_RITMO = timedelta(seconds=60)
PRESA_APOS = timedelta(minutes=5)  # "enviando" há mais que isso: o processo foi interrompido no meio
ESPERAS = (60, 300)  # segundos de espera depois da 1ª e da 2ª falha passageira (a 3ª encerra)
FALHAS_SEGUIDAS_PARA_PARAR = 3  # provedor instável: deixa o resto para o próximo ciclo
CONFERIR_A_CADA = 10  # confere se o disparo continua em andamento (pausa/cancelamento durante o envio)


def _marcar(pk, de, **campos):
    """Troca condicional: só altera a mensagem se ela ainda estiver em `de` (outro processo pode ter mexido)."""
    return MensagemDisparo.objects.filter(pk=pk, status=de).update(atualizado_em=timezone.now(), **campos)


def _pausar(disparo, motivo):
    mudou = Disparo.objects.filter(pk=disparo.pk, status=Disparo.Status.EM_ANDAMENTO).update(
        status=Disparo.Status.PAUSADO, motivo_pausa=motivo[:200],
    )
    if mudou:
        LogAtividade.registrar(None, "disparo.pausado", f"Disparo nº {disparo.pk} pausado automaticamente: {motivo}", disparo)


def _recuperar_presas(agora, relatorio):
    presas = MensagemDisparo.objects.filter(status=S.ENVIANDO, atualizado_em__lt=agora - PRESA_APOS)
    esgotadas = presas.filter(tentativas__gte=MAX_TENTATIVAS).update(
        status=S.FALHOU, erro="Sem confirmação do provedor após várias tentativas.", atualizado_em=agora,
    )
    voltaram = presas.update(status=S.FILA, atualizado_em=agora)
    relatorio["falhas"] += esgotadas
    relatorio["reagendadas"] += voltaram


def _motivo_para_nao_enviar(m, conexao):
    motivo = motivo_bloqueio(m.candidato, conexao)
    if not motivo and m.candidato.telefone != m.telefone:
        motivo = "Telefone do cadastro foi alterado"
    return motivo


def _montar(m, disparo, exige_template):
    template = None
    if exige_template and disparo.template_nome:
        template = ChamadaTemplate(nome=disparo.template_nome, idioma=disparo.template_idioma or "pt_BR", parametros=list(m.parametros or []))
    return MensagemSaida(para=telefone_whatsapp(m.telefone), texto=m.texto, template=template)


def _enviar_um(m, disparo, conexao, provedor, exige_template, agora, relatorio):
    """Envia uma mensagem já reservada. Devolve True se o provedor falhou de forma passageira."""
    motivo = _motivo_para_nao_enviar(m, conexao)
    if motivo:  # o cadastro pode ter mudado (ou a pessoa respondido SAIR) depois que o disparo foi criado
        _marcar(m.pk, S.ENVIANDO, status=S.IGNORADA, erro=motivo)
        relatorio["ignoradas"] += 1
        return False
    try:
        resultado = provedor.enviar(_montar(m, disparo, exige_template))
    except Exception as exc:  # noqa: BLE001 — qualquer erro do provedor vira falha passageira, sem o texto da exceção
        resultado = ResultadoEnvio(ok=False, erro=f"Erro inesperado ({type(exc).__name__}).", tentar_de_novo=True)

    if resultado.ok:
        campos = {"enviada_em": agora, "id_provedor": (resultado.id_mensagem or "")[:200], "erro": ""}
        if not _marcar(m.pk, S.ENVIANDO, status=S.ENVIADA, **campos):
            # Um aviso do provedor já fez a mensagem avançar: só completa os dados do envio.
            MensagemDisparo.objects.filter(pk=m.pk, enviada_em__isnull=True).update(**campos)
        relatorio["enviadas"] += 1
        return False

    repetir = resultado.tentar_de_novo and m.tentativas < MAX_TENTATIVAS
    erro = (resultado.erro or "Falha no envio.")[:300]
    if repetir:
        espera = ESPERAS[min(m.tentativas - 1, len(ESPERAS) - 1)]
        _marcar(m.pk, S.ENVIANDO, status=S.FILA, erro=erro, proxima_tentativa_em=agora + timedelta(seconds=espera))
        relatorio["reagendadas"] += 1
    else:
        _marcar(m.pk, S.ENVIANDO, status=S.FALHOU, erro=erro)
        relatorio["falhas"] += 1
    return resultado.tentar_de_novo


def _enviar(agora, maximo, relatorio):
    disparos = list(Disparo.objects.filter(status=Disparo.Status.EM_ANDAMENTO).order_by("criado_em", "id"))
    if not disparos:
        return
    conexao = ConexaoWhatsApp.objects.filter(pk=1).first()
    provedor, motivo = None, ""
    if conexao is None or not conexao.pode_enviar:
        motivo = "A conexão com o WhatsApp não está pronta (veja Configurações › WhatsApp)."
    else:
        try:
            provedor = registro.criar_provedor(conexao)
        except (ErroCredenciais, ValueError):
            motivo = "Não foi possível ler as credenciais do WhatsApp. Salve-as de novo em Configurações."
    if motivo:
        for disparo in disparos:
            _pausar(disparo, motivo)
            relatorio["pausados"] += 1
        return

    exige_template = registro.definicao(conexao.provedor).exige_template
    janela = agora - JANELA_RITMO
    usadas_na_conexao = MensagemDisparo.objects.filter(tentativa_em__gte=janela).count()
    restante = maximo
    for disparo in disparos:
        if restante <= 0:
            break
        if exige_template and not disparo.template_nome:
            _pausar(disparo, "A conexão atual só envia template aprovado e este disparo não tem um.")
            relatorio["pausados"] += 1
            continue
        proprias = MensagemDisparo.objects.filter(disparo=disparo, tentativa_em__gte=janela).count()
        orcamento = min(
            conexao.limite_por_minuto - usadas_na_conexao,
            min(disparo.ritmo_por_minuto, conexao.limite_por_minuto) - proprias,
            restante,
        )
        if orcamento <= 0:
            continue
        fila = list(
            MensagemDisparo.objects.filter(disparo=disparo, status=S.FILA)
            .filter(Q(proxima_tentativa_em__isnull=True) | Q(proxima_tentativa_em__lte=agora))
            .select_related("candidato").order_by("id")[:orcamento]
        )
        falhas_seguidas = 0
        for posicao, m in enumerate(fila):
            if posicao and posicao % CONFERIR_A_CADA == 0 and not Disparo.objects.filter(pk=disparo.pk, status=Disparo.Status.EM_ANDAMENTO).exists():
                break
            reservada = MensagemDisparo.objects.filter(pk=m.pk, status=S.FILA).update(
                status=S.ENVIANDO, tentativas=m.tentativas + 1, tentativa_em=agora, atualizado_em=timezone.now(),
            )
            if not reservada:  # outro processo pegou esta mensagem
                continue
            m.tentativas += 1
            usadas_na_conexao += 1
            restante -= 1
            falhas_seguidas = falhas_seguidas + 1 if _enviar_um(m, disparo, conexao, provedor, exige_template, agora, relatorio) else 0
            if falhas_seguidas >= FALHAS_SEGUIDAS_PARA_PARAR:
                break


def _avancar_simulados(agora, relatorio):
    """Entrega e leitura do Simulador, no lugar do webhook que ele não tem."""
    recentes = agora - timedelta(seconds=simulador.JANELA_SEGUNDOS)
    candidatas = (
        MensagemDisparo.objects.filter(id_provedor__startswith=simulador.PREFIXO)
        .filter(Q(status=S.ENVIADA) | Q(status=S.ENTREGUE, enviada_em__gte=recentes))
        .order_by("id")[:200]
    )
    pendentes = []
    for m in candidatas:
        pendentes += simulador.eventos_pendentes(m.id_provedor, m.enviada_em, agora)
    if pendentes:
        relatorio["simuladas"] += eventos.aplicar_eventos(pendentes).atualizadas


def _concluir(agora, relatorio):
    for disparo in Disparo.objects.filter(status=Disparo.Status.EM_ANDAMENTO):
        if disparo.mensagens.filter(status__in=(S.FILA, S.ENVIANDO)).exists():
            continue
        if Disparo.objects.filter(pk=disparo.pk, status=Disparo.Status.EM_ANDAMENTO).update(status=Disparo.Status.CONCLUIDO, finalizado_em=agora):
            total = contagens(disparo)
            LogAtividade.registrar(
                None, "disparo.concluido",
                f"Disparo nº {disparo.pk} concluído: {total['enviadas']} enviada(s), {total['falhas']} falha(s), {total['ignoradas']} não enviada(s)",
                disparo,
            )
            relatorio["concluidos"] += 1


def ciclo(agora=None, maximo=MAXIMO_POR_CICLO):
    """Um ciclo da fila. Devolve as contagens do que foi feito."""
    agora = agora or timezone.now()
    relatorio = dict.fromkeys(("enviadas", "falhas", "ignoradas", "reagendadas", "pausados", "simuladas", "concluidos"), 0)
    _recuperar_presas(agora, relatorio)
    _enviar(agora, max(0, int(maximo)), relatorio)
    _avancar_simulados(agora, relatorio)
    _concluir(agora, relatorio)
    return relatorio


def houve_atividade(relatorio):
    return any(relatorio.values())
