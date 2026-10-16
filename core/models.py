from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .utils import DATA_EXATA, MES_CALENDARIO, ip_do_cliente, so_digitos


class Configuracao(models.Model):
    """Configuração única do sistema (sempre o registro de id 1)."""

    class ContagemPrazo(models.TextChoices):
        MES_CALENDARIO = MES_CALENDARIO, "Meses de calendário, contando o mês da chamada"
        DATA_EXATA = DATA_EXATA, "Data exata da chamada + meses"

    nome_unidade = models.CharField("Nome da unidade", max_length=120, default="Minha unidade")
    cep_unidade = models.CharField("CEP da unidade", max_length=8, blank=True)
    numero_unidade = models.CharField("Número", max_length=15, blank=True)
    endereco_unidade = models.CharField("Endereço da unidade", max_length=200, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    prazo_meses = models.PositiveSmallIntegerField(
        "Permanência máxima na vaga (meses)", default=3,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    contagem_prazo = models.CharField(
        "Como contar o prazo", max_length=20, choices=ContagemPrazo.choices, default=ContagemPrazo.MES_CALENDARIO,
    )
    dias_alerta = models.PositiveSmallIntegerField(
        "Avisar o vencimento com (dias)", default=30,
        validators=[MinValueValidator(1), MaxValueValidator(180)],
    )
    validade_convite_dias = models.PositiveSmallIntegerField(
        "Validade do convite de cadastro (dias)", default=7,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    painel_publico_token = models.CharField(max_length=40, blank=True)

    class Meta:
        verbose_name = "configuração"
        verbose_name_plural = "configurações"

    def __str__(self):
        return f"Configuração de {self.nome_unidade}"

    def save(self, *args, **kwargs):
        self.pk = 1
        self.cep_unidade = so_digitos(self.cep_unidade)
        self.numero_unidade = (self.numero_unidade or "").strip()
        super().save(*args, **kwargs)

    @classmethod
    def atual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def tem_coordenadas(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def prazo_texto(self):
        return f"{self.prazo_meses} {'mês' if self.prazo_meses == 1 else 'meses'}"


class GeoCache(models.Model):
    """Resultado da consulta de um CEP (endereço + coordenadas), para não repetir chamadas externas."""

    class Precisao(models.TextChoices):
        ENDERECO = "ENDERECO", "Rua / CEP"
        BAIRRO = "BAIRRO", "Bairro"
        CIDADE = "CIDADE", "Cidade"
        NENHUMA = "NENHUMA", "Sem coordenadas"

    cep = models.CharField(max_length=8, primary_key=True)
    logradouro = models.CharField(max_length=200, blank=True)
    bairro = models.CharField(max_length=100, blank=True)
    cidade = models.CharField(max_length=100, blank=True)
    uf = models.CharField(max_length=2, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    precisao = models.CharField(max_length=10, choices=Precisao.choices, default=Precisao.NENHUMA)
    fonte = models.CharField(max_length=30, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cache de CEP"
        verbose_name_plural = "cache de CEPs"

    def __str__(self):
        return self.cep

    @property
    def encontrado(self):
        """False para CEP que não existe nas bases consultadas."""
        return self.precisao != self.Precisao.NENHUMA or bool(self.cidade or self.logradouro)

    # Não é campo do banco: fica True só no resultado de uma consulta que não foi gravada (endereço obtido, coordenadas ainda não).
    provisorio = False


class GeoArea(models.Model):
    """Limites de um bairro (ou de uma cidade) no OpenStreetMap.

    Serve para conferir se o ponto achado para um CEP cai mesmo no bairro dele: uma fonte que devolve o centro da cidade
    para todos os CEPs é detectada porque o ponto fica fora da caixa do bairro.
    """

    class Tipo(models.TextChoices):
        BAIRRO = "BAIRRO", "Bairro"
        CIDADE = "CIDADE", "Cidade"

    chave = models.CharField(max_length=260, primary_key=True)  # UF|cidade|bairro, sem acentos nem maiúsculas
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    nome = models.CharField("Bairro", max_length=120, blank=True)
    cidade = models.CharField(max_length=100)
    uf = models.CharField(max_length=2, blank=True)
    sul = models.FloatField(null=True, blank=True)
    norte = models.FloatField(null=True, blank=True)
    oeste = models.FloatField(null=True, blank=True)
    leste = models.FloatField(null=True, blank=True)
    latitude = models.FloatField("Latitude do centro", null=True, blank=True)
    longitude = models.FloatField("Longitude do centro", null=True, blank=True)
    encontrado = models.BooleanField(default=False)  # False = o OpenStreetMap não conhece o lugar (não perguntar de novo já)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "limite de bairro/cidade"
        verbose_name_plural = "limites de bairros/cidades"

    def __str__(self):
        return ", ".join(p for p in (self.nome, self.cidade, self.uf) if p)


class GeoEndereco(models.Model):
    """Ponto de um endereço com número ("rua, 329" de um CEP) no OpenStreetMap, para não repetir a consulta."""

    chave = models.CharField(max_length=60, primary_key=True)  # CEP|número, sem acentos nem maiúsculas
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    encontrado = models.BooleanField(default=False)  # False = o OpenStreetMap não tem esse número (não perguntar de novo já)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ponto de endereço com número"
        verbose_name_plural = "pontos de endereços com número"

    def __str__(self):
        return self.chave


class LogAtividade(models.Model):
    """Trilha de auditoria: quem fez o quê e quando. Alimenta a 'Atividade recente' do painel."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="atividades",
    )
    acao = models.CharField(max_length=40, db_index=True)
    descricao = models.CharField(max_length=300)
    entidade = models.CharField(max_length=30, blank=True)
    entidade_id = models.PositiveBigIntegerField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "atividade"
        verbose_name_plural = "atividades"
        ordering = ["-criado_em", "-id"]

    def __str__(self):
        return f"{self.acao}: {self.descricao}"

    @classmethod
    def registrar(cls, usuario, acao, descricao, obj=None, request=None):
        return cls.objects.create(
            usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
            acao=acao,
            descricao=descricao[:300],
            entidade=obj._meta.model_name if obj is not None else "",
            entidade_id=obj.pk if obj is not None else None,
            ip=ip_do_cliente(request) if request is not None else None,
        )
