"""Prepara vínculos para exibição: situação, prazo e 'chips' de check-in de cada mês."""
from django.utils import timezone

from core.utils import rotulo_mes

ROTULOS_SITUACAO = {
    "ativo": ("Em vaga", "info"),
    "vencendo": ("Vencendo", "warn"),
    "vencido": ("Vencido", "bad"),
    "concluido": ("Concluído", "good"),
    "encerrado": ("Encerrado", "neutral"),
}

ROTULOS_CHIP = {
    "cumpriu": "Cumpriu",
    "parcial": "Cumpriu em parte",
    "nao_cumpriu": "Não cumpriu",
    "pendente": "Pendente (mês em andamento)",
    "atrasado": "Pendente (mês já terminou)",
    "futuro": "Ainda não começou",
}


def chips_checkin(vinculo, hoje=None):
    """Um chip por mês do período: resultado registrado, pendente, atrasado ou futuro."""
    hoje = hoje or timezone.localdate()
    mes_atual = hoje.replace(day=1)
    feitos = {c.competencia: c for c in vinculo.checkins.all()}
    exigidas = set(vinculo.competencias_exigidas())
    chips = []
    for competencia in vinculo.competencias():
        feito = feitos.get(competencia)
        if feito:
            estado = feito.resultado
        elif competencia not in exigidas:
            estado = "dispensado"
        elif competencia > mes_atual:
            estado = "futuro"
        elif competencia == mes_atual:
            estado = "pendente"
        else:
            estado = "atrasado"
        chips.append({
            "competencia": competencia,
            "rotulo": rotulo_mes(competencia),
            "estado": estado,
            "descricao": ROTULOS_CHIP.get(estado, "Não exigido"),
            "checkin": feito,
        })
    return chips


def preparar(vinculos, config, hoje=None, com_chips=True):
    """Anota cada vínculo com .dias, .sit, .sit_rotulo, .sit_tom, .pct e .chips. Devolve uma lista."""
    hoje = hoje or timezone.localdate()
    lista = list(vinculos)
    for v in lista:
        v.dias = v.dias_restantes(hoje)
        v.sit = v.situacao(hoje, config.dias_alerta)
        v.sit_rotulo, v.sit_tom = ROTULOS_SITUACAO[v.sit]
        v.pct = v.progresso(hoje)
        if com_chips:
            v.chips = chips_checkin(v, hoje)
            v.pendentes = sum(1 for c in v.chips if c["estado"] in ("pendente", "atrasado"))
            v.atrasados = sum(1 for c in v.chips if c["estado"] == "atrasado")
    return lista
