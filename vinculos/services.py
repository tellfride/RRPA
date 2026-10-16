"""Regras de negócio de vínculos e check-ins. As views só chamam estas funções."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from candidatos.models import Candidato
from core.constantes import VINCULO_ATIVO, VINCULO_CONCLUIDO, VINCULO_ENCERRADO
from core.excecoes import RegraDeNegocio
from core.models import Configuracao, LogAtividade
from core.utils import calcular_periodo, rotulo_mes
from vagas.models import Vaga

from .models import Checkin, Vinculo


def periodo_para(data_chamada, config=None):
    """(início do período, data final) para uma chamada em `data_chamada`, conforme a configuração."""
    config = config or Configuracao.atual()
    return calcular_periodo(data_chamada, config.prazo_meses, config.contagem_prazo)


@transaction.atomic
def chamar_para_vaga(candidato, vaga, usuario, data_chamada=None, observacoes="", request=None):
    """Vincula um candidato aprovado a uma vaga, respeitando quantidade disponível e prazo."""
    config = Configuracao.atual()
    hoje = timezone.localdate()
    data_chamada = data_chamada or hoje

    vaga = Vaga.objects.select_for_update().get(pk=vaga.pk)
    candidato = Candidato.objects.select_for_update().get(pk=candidato.pk)

    if not vaga.ativa:
        raise RegraDeNegocio(f"A vaga {vaga.nome} está inativa.")
    if candidato.status != Candidato.Status.APROVADO:
        raise RegraDeNegocio("Só é possível chamar candidatos aprovados.")
    if Vinculo.objects.filter(candidato=candidato, status=VINCULO_ATIVO).exists():
        raise RegraDeNegocio(f"{candidato.nome} já ocupa uma vaga. Encerre o vínculo atual antes de chamar para outra.")
    if data_chamada > hoje + timedelta(days=62):
        raise RegraDeNegocio("A data da chamada não pode estar tão distante no futuro.")

    inicio, fim = periodo_para(data_chamada, config)
    if fim < hoje:
        raise RegraDeNegocio(f"Com a chamada em {data_chamada:%d/%m/%Y} o prazo já estaria vencido ({fim:%d/%m/%Y}).")

    ocupadas = Vinculo.objects.filter(vaga=vaga, status=VINCULO_ATIVO, data_fim__gte=hoje).count()
    if ocupadas >= vaga.quantidade:
        raise RegraDeNegocio(f"Não há vaga disponível em {vaga.nome} ({ocupadas} de {vaga.quantidade} ocupadas).")

    vinculo = Vinculo.objects.create(
        candidato=candidato, vaga=vaga, data_chamada=data_chamada, inicio_periodo=inicio, data_fim=fim,
        criado_por=usuario, observacoes=observacoes,
    )
    candidato.status = Candidato.Status.EM_VAGA
    candidato.save(update_fields=["status", "atualizado_em"])
    LogAtividade.registrar(
        usuario, "vinculo.criado",
        f"Chamou {candidato.nome} para {vaga.nome} (permanência até {fim:%d/%m/%Y})", vinculo, request,
    )
    return vinculo


@transaction.atomic
def encerrar_vinculo(vinculo, usuario, motivo, observacao="", request=None):
    """Baixa o vínculo e libera a vaga. 'Prazo cumprido' conclui; os demais motivos encerram antes do prazo."""
    vinculo = Vinculo.objects.select_for_update(of=("self",)).select_related("candidato", "vaga").get(pk=vinculo.pk)
    if vinculo.status != VINCULO_ATIVO:
        raise RegraDeNegocio("Este vínculo já foi encerrado.")
    if motivo not in Vinculo.Motivo.values:
        raise RegraDeNegocio("Escolha o motivo do encerramento.")

    vinculo.status = VINCULO_CONCLUIDO if motivo == Vinculo.Motivo.PRAZO_CUMPRIDO else VINCULO_ENCERRADO
    vinculo.motivo_encerramento = motivo
    vinculo.observacao_encerramento = observacao
    vinculo.encerrado_em = timezone.now()
    vinculo.encerrado_por = usuario
    vinculo.save()

    candidato = vinculo.candidato
    candidato.status = (
        Candidato.Status.DESISTENTE if motivo == Vinculo.Motivo.DESISTENCIA else Candidato.Status.CONCLUIDO
    )
    candidato.save(update_fields=["status", "atualizado_em"])
    LogAtividade.registrar(
        usuario, "vinculo.encerrado",
        f"Encerrou o vínculo de {candidato.nome} em {vinculo.vaga.nome} ({vinculo.get_motivo_encerramento_display().lower()})",
        vinculo, request,
    )
    return vinculo


def pode_validar(usuario, vinculo):
    """Administradores validam qualquer vínculo; responsáveis do setor, só os das suas vagas."""
    if not usuario.is_authenticated or not usuario.pode_checkin:
        return False
    return usuario.eh_admin or vinculo.vaga.responsaveis.filter(pk=usuario.pk).exists()


@transaction.atomic
def registrar_checkin(vinculo, competencia, resultado, usuario, observacao="", request=None):
    """Registra (ou corrige) a validação de um mês. Devolve (checkin, criado)."""
    if not pode_validar(usuario, vinculo):
        raise RegraDeNegocio("Só o responsável pelo setor desta vaga pode registrar o check-in.")
    if resultado not in Checkin.Resultado.values:
        raise RegraDeNegocio("Escolha o resultado do check-in.")
    if competencia not in vinculo.competencias_exigidas():
        raise RegraDeNegocio("Este mês não faz parte do período do colaborador.")
    if competencia > timezone.localdate().replace(day=1):
        raise RegraDeNegocio("Esse mês ainda não começou.")
    if resultado != Checkin.Resultado.CUMPRIU and not observacao.strip():
        raise RegraDeNegocio("Explique em poucas palavras o que aconteceu quando o RPA não foi totalmente cumprido.")

    checkin, criado = Checkin.objects.update_or_create(
        vinculo=vinculo, competencia=competencia,
        defaults={"resultado": resultado, "observacao": observacao.strip(),
                  "registrado_por": usuario, "registrado_em": timezone.now()},
    )
    LogAtividade.registrar(
        usuario, "checkin.registrado",
        f"Check-in de {rotulo_mes(competencia)} de {vinculo.candidato.nome}: {checkin.get_resultado_display().lower()}",
        checkin, request,
    )
    return checkin, criado


def _vinculos_para_validar(usuario, hoje):
    """Vínculos que ainda podem precisar de check-in: ativos ou encerrados nos últimos 60 dias."""
    qs = Vinculo.objects.filter(Q(status=VINCULO_ATIVO) | Q(data_fim__gte=hoje - timedelta(days=60)))
    if usuario is not None and usuario.eh_gestor:
        qs = qs.filter(vaga__responsaveis=usuario)
    return qs.select_related("candidato", "vaga").prefetch_related("checkins")


def pendencias_checkin(usuario, hoje=None, vaga_id=None, todas=False):
    """Lista de check-ins que faltam: dicts com vinculo, competencia e atrasado (mês já terminou).

    Por padrão só devolve algo para quem pode validar (admin e responsáveis de setor, dentro das suas vagas).
    Com todas=True devolve a visão de acompanhamento para qualquer perfil.
    """
    if not todas and (usuario is None or not usuario.is_authenticated or not usuario.pode_checkin):
        return []
    hoje = hoje or timezone.localdate()
    mes_atual = hoje.replace(day=1)
    qs = _vinculos_para_validar(usuario, hoje)
    if vaga_id:
        qs = qs.filter(vaga_id=vaga_id)
    pendencias = []
    for vinculo in qs:
        feitos = {c.competencia for c in vinculo.checkins.all()}
        for competencia in vinculo.competencias_exigidas():
            if competencia <= mes_atual and competencia not in feitos:
                pendencias.append({"vinculo": vinculo, "competencia": competencia, "atrasado": competencia < mes_atual})
    pendencias.sort(key=lambda p: (not p["atrasado"], p["competencia"], p["vinculo"].candidato.nome))
    return pendencias


def contar_pendencias_checkin(usuario):
    return len(pendencias_checkin(usuario))
