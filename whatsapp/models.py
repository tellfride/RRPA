from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.utils import gerar_token

from . import cripto


def novo_token_webhook():
    return gerar_token(32)


class ConexaoWhatsApp(models.Model):
    """Conexão do sistema com o WhatsApp (sempre o registro de id 1). As credenciais ficam criptografadas."""

    class Provedor(models.TextChoices):
        SIMULADOR = "simulador", "Simulador (testes, não envia nada)"
        META = "meta", "WhatsApp Cloud API (Meta)"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Ainda não testada"
        CONECTADA = "conectada", "Conectada"
        ERRO = "erro", "Com erro"

    provedor = models.CharField("Provedor", max_length=12, choices=Provedor.choices, default=Provedor.SIMULADOR)
    credenciais = models.TextField(blank=True)  # JSON criptografado (whatsapp.cripto); nunca vai para o navegador
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    numero_exibido = models.CharField(max_length=30, blank=True)
    limite_por_minuto = models.PositiveSmallIntegerField(
        "Limite de mensagens por minuto", default=20, validators=[MinValueValidator(1), MaxValueValidator(300)],
    )
    exigir_autorizacao = models.BooleanField("Enviar só a quem autorizou", default=False)
    webhook_token = models.CharField(max_length=64, default=novo_token_webhook, editable=False)
    testada_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.CharField(max_length=300, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "conexão com o WhatsApp"
        verbose_name_plural = "conexão com o WhatsApp"

    def __str__(self):
        return f"WhatsApp: {self.get_provedor_display()} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def atual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def pode_enviar(self):
        return self.status == self.Status.CONECTADA

    @property
    def tom(self):
        return {"pendente": "neutral", "conectada": "good", "erro": "bad"}[self.status]

    def credenciais_abertas(self):
        """Credenciais descriptografadas. Só para uso no servidor; lança ErroCredenciais se não der para ler."""
        if not self.credenciais:
            return {}
        dados = cripto.decifrar_json(self.credenciais)
        if not isinstance(dados, dict):
            raise cripto.ErroCredenciais("Credenciais em formato inválido.")
        return {str(k): "" if v is None else str(v) for k, v in dados.items()}

    def guardar_credenciais(self, dados):
        self.credenciais = cripto.cifrar_json(dados) if dados else ""
