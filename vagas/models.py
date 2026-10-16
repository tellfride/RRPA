from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from core.constantes import VINCULO_ATIVO


class VagaQuerySet(models.QuerySet):
    def ativas(self):
        return self.filter(ativa=True)

    def com_ocupacao(self, hoje=None, dias_alerta=30):
        """Anota n_ocupadas, n_vencendo e n_vencidos (vínculos ativos) em cada vaga."""
        hoje = hoje or timezone.localdate()
        limite = hoje + timedelta(days=dias_alerta)
        ativo = Q(vinculos__status=VINCULO_ATIVO)
        return self.annotate(
            n_ocupadas=Count("vinculos", filter=ativo & Q(vinculos__data_fim__gte=hoje)),
            n_vencendo=Count("vinculos", filter=ativo & Q(vinculos__data_fim__gte=hoje, vinculos__data_fim__lte=limite)),
            n_vencidos=Count("vinculos", filter=ativo & Q(vinculos__data_fim__lt=hoje)),
        )


class Vaga(models.Model):
    """Um cargo com número de vagas (ex.: Farmácia, 8 vagas). Só o administrador cadastra."""

    nome = models.CharField("Cargo / vaga", max_length=100, unique=True)
    setor = models.CharField("Setor", max_length=100, blank=True)
    descricao = models.TextField("Descrição e requisitos", blank=True)
    quantidade = models.PositiveSmallIntegerField(
        "Quantidade de vagas", default=1, validators=[MinValueValidator(1)],
        help_text="Quantas pessoas podem ocupar este cargo ao mesmo tempo.",
    )
    responsaveis = models.ManyToManyField(
        settings.AUTH_USER_MODEL, verbose_name="Responsáveis pelo check-in", blank=True,
        related_name="vagas_sob_responsabilidade",
        help_text="Quem valida, mês a mês, que o colaborador cumpriu o RPA.",
    )
    ativa = models.BooleanField("Vaga ativa", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    objects = VagaQuerySet.as_manager()

    class Meta:
        verbose_name = "vaga"
        verbose_name_plural = "vagas"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def ocupadas(self, hoje=None):
        hoje = hoje or timezone.localdate()
        return self.vinculos.filter(status=VINCULO_ATIVO, data_fim__gte=hoje).count()

    def disponiveis(self, hoje=None):
        return max(self.quantidade - self.ocupadas(hoje), 0)
