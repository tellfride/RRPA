from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.constantes import VINCULO_ATIVO, VINCULO_CONCLUIDO, VINCULO_ENCERRADO
from core.utils import meses_do_periodo, texto_prazo


class VinculoQuerySet(models.QuerySet):
    def ativos(self):
        """Todos os vínculos em aberto (inclui os que já passaram do prazo e aguardam baixa)."""
        return self.filter(status=VINCULO_ATIVO)

    def ocupando(self, hoje=None):
        """Quem ocupa uma vaga agora: ativo e dentro do prazo."""
        return self.filter(status=VINCULO_ATIVO, data_fim__gte=hoje or timezone.localdate())

    def vencendo(self, hoje, dias):
        return self.filter(status=VINCULO_ATIVO, data_fim__gte=hoje, data_fim__lte=hoje + timedelta(days=dias))

    def vencidos(self, hoje=None):
        return self.filter(status=VINCULO_ATIVO, data_fim__lt=hoje or timezone.localdate())

    def da_situacao(self, situacao, hoje, dias_alerta):
        """Filtro usado nas listas: ativo (dentro do prazo), vencendo, vencido, concluido, encerrado."""
        if situacao == "ativo":
            return self.ocupando(hoje)
        if situacao == "vencendo":
            return self.vencendo(hoje, dias_alerta)
        if situacao == "vencido":
            return self.vencidos(hoje)
        if situacao in (VINCULO_CONCLUIDO, VINCULO_ENCERRADO):
            return self.filter(status=situacao)
        return self


class Vinculo(models.Model):
    """Colaborador ocupando uma vaga. A permanência é limitada ao prazo (3 meses por padrão)."""

    class Status(models.TextChoices):
        ATIVO = VINCULO_ATIVO, "Ativo"
        CONCLUIDO = VINCULO_CONCLUIDO, "Concluído"
        ENCERRADO = VINCULO_ENCERRADO, "Encerrado antes do prazo"

    class Motivo(models.TextChoices):
        PRAZO_CUMPRIDO = "prazo_cumprido", "Prazo cumprido"
        DESISTENCIA = "desistencia", "Desistência do colaborador"
        DESLIGAMENTO = "desligamento", "Desligamento pela unidade"
        DESCUMPRIMENTO = "descumprimento", "Não cumpriu o RPA"
        OUTRO = "outro", "Outro motivo"

    candidato = models.ForeignKey("candidatos.Candidato", on_delete=models.PROTECT, related_name="vinculos")
    vaga = models.ForeignKey("vagas.Vaga", on_delete=models.PROTECT, related_name="vinculos")
    data_chamada = models.DateField("Chamado em")
    inicio_periodo = models.DateField("Início do período")
    data_fim = models.DateField("Permanência até")
    status = models.CharField(max_length=12, choices=Status.choices, default=VINCULO_ATIVO, db_index=True)
    motivo_encerramento = models.CharField(max_length=16, choices=Motivo.choices, blank=True)
    observacao_encerramento = models.TextField(blank=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)
    encerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vinculos_criados")
    observacoes = models.TextField("Observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = VinculoQuerySet.as_manager()

    class Meta:
        verbose_name = "vínculo"
        verbose_name_plural = "vínculos"
        ordering = ["data_fim", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["candidato"], condition=Q(status=VINCULO_ATIVO), name="um_vinculo_ativo_por_candidato",
            ),
        ]

    def __str__(self):
        return f"{self.candidato} · {self.vaga}"

    def get_absolute_url(self):
        return reverse("vinculos:detalhe", args=[self.pk])

    # -------- prazo
    def dias_restantes(self, hoje=None):
        return (self.data_fim - (hoje or timezone.localdate())).days

    def vencido(self, hoje=None):
        return self.status == VINCULO_ATIVO and self.data_fim < (hoje or timezone.localdate())

    def situacao(self, hoje=None, dias_alerta=30):
        """ativo | vencendo | vencido | concluido | encerrado"""
        if self.status != VINCULO_ATIVO:
            return self.status
        dias = self.dias_restantes(hoje)
        if dias < 0:
            return "vencido"
        return "vencendo" if dias <= dias_alerta else "ativo"

    def progresso(self, hoje=None):
        """Quanto do período já passou (0 a 100)."""
        hoje = hoje or timezone.localdate()
        total = (self.data_fim - self.inicio_periodo).days + 1
        if total <= 0:
            return 100
        return max(0, min(100, round(100 * ((hoje - self.inicio_periodo).days + 1) / total)))

    @property
    def texto_prazo(self):
        return texto_prazo(self.dias_restantes())

    # -------- check-in
    def competencias(self):
        """Todos os meses do período (primeiro dia de cada mês)."""
        return meses_do_periodo(self.inicio_periodo, self.data_fim)

    def competencias_exigidas(self):
        """Meses que o setor precisa validar: até o fim do prazo ou até o encerramento, o que vier antes."""
        limite = self.data_fim
        if self.status != VINCULO_ATIVO and self.encerrado_em:
            limite = min(limite, timezone.localtime(self.encerrado_em).date())
        return meses_do_periodo(self.inicio_periodo, limite)


class Checkin(models.Model):
    """Validação mensal do responsável do setor: o colaborador cumpriu o RPA contratado?"""

    class Resultado(models.TextChoices):
        CUMPRIU = "cumpriu", "Cumpriu o RPA"
        PARCIAL = "parcial", "Cumpriu em parte"
        NAO_CUMPRIU = "nao_cumpriu", "Não cumpriu"

    TONS = {Resultado.CUMPRIU: "good", Resultado.PARCIAL: "warn", Resultado.NAO_CUMPRIU: "bad"}

    vinculo = models.ForeignKey(Vinculo, on_delete=models.CASCADE, related_name="checkins")
    competencia = models.DateField("Mês validado")
    resultado = models.CharField(max_length=12, choices=Resultado.choices)
    observacao = models.TextField("Observação", blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="checkins")
    registrado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "check-in"
        verbose_name_plural = "check-ins"
        ordering = ["competencia"]
        constraints = [
            models.UniqueConstraint(fields=["vinculo", "competencia"], name="um_checkin_por_mes"),
        ]

    def __str__(self):
        return f"{self.vinculo} · {self.competencia:%m/%Y} · {self.get_resultado_display()}"

    @property
    def tom(self):
        return self.TONS.get(self.resultado, "neutral")
