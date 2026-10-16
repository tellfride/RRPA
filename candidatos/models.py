from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core import utils
from core.constantes import VINCULO_ATIVO


class Candidato(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Aguardando aprovação"
        APROVADO = "aprovado", "Aprovado"
        EM_VAGA = "em_vaga", "Em vaga"
        CONCLUIDO = "concluido", "Já atuou"
        REPROVADO = "reprovado", "Não aprovado"
        DESISTENTE = "desistente", "Desistiu"

    class Origem(models.TextChoices):
        MANUAL = "manual", "Cadastro manual"
        PLANILHA = "planilha", "Planilha"
        LINK_PESSOAL = "link_pessoal", "Link de indicação"
        CONVITE = "convite", "Convite individual"

    class GeoStatus(models.TextChoices):
        PENDENTE = "pendente", "Aguardando localização"
        OK = "ok", "Localizado"
        FALHOU = "falhou", "Não localizado"
        SEM_CEP = "sem_cep", "Sem CEP"

    TONS = {
        Status.PENDENTE: "neutral", Status.APROVADO: "info", Status.EM_VAGA: "good",
        Status.CONCLUIDO: "accent", Status.REPROVADO: "bad", Status.DESISTENTE: "neutral",
    }

    nome = models.CharField("Nome completo", max_length=120)
    cpf = models.CharField("CPF", max_length=11, unique=True)
    telefone = models.CharField("Telefone (WhatsApp)", max_length=15)
    email = models.EmailField("E-mail", blank=True)

    cep = models.CharField("CEP", max_length=8, blank=True, db_index=True)
    logradouro = models.CharField("Endereço", max_length=150, blank=True)
    numero = models.CharField("Número", max_length=15, blank=True)
    complemento = models.CharField("Complemento", max_length=60, blank=True)
    bairro = models.CharField("Bairro", max_length=80, blank=True, db_index=True)
    cidade = models.CharField("Cidade", max_length=80, blank=True, db_index=True)
    uf = models.CharField("UF", max_length=2, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    precisao_geo = models.CharField(max_length=10, blank=True)
    geo_status = models.CharField(max_length=10, choices=GeoStatus.choices, default=GeoStatus.PENDENTE, db_index=True)

    vagas_interesse = models.ManyToManyField("vagas.Vaga", verbose_name="Vagas de interesse", blank=True, related_name="candidatos")
    status = models.CharField("Situação", max_length=12, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    origem = models.CharField("Origem", max_length=14, choices=Origem.choices, default=Origem.MANUAL)

    link = models.ForeignKey("LinkCadastro", null=True, blank=True, on_delete=models.SET_NULL, related_name="candidatos")
    indicado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="indicacoes",
        verbose_name="Link fornecido por",
    )
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="candidatos_criados")
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="candidatos_aprovados")
    aprovado_em = models.DateTimeField(null=True, blank=True)

    observacoes = models.TextField("Observações", blank=True)
    consentimento_em = models.DateTimeField(null=True, blank=True)
    consentimento_ip = models.GenericIPAddressField(null=True, blank=True)
    # Autorização separada para mensagens por WhatsApp (como no E-Find). None = não informada (cadastro feito pela equipe).
    whatsapp_ok = models.BooleanField("Aceita mensagens por WhatsApp", null=True, blank=True)
    whatsapp_ok_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "candidato"
        verbose_name_plural = "candidatos"
        ordering = ["nome"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cep_inicial = self.cep

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        self.nome = utils.limpar_nome(self.nome)
        self.cpf = utils.normalizar_cpf(self.cpf)
        self.telefone = utils.normalizar_telefone(self.telefone)
        self.cep = utils.normalizar_cep(self.cep)
        self.uf = (self.uf or "").strip().upper()[:2]
        for campo in ("logradouro", "bairro", "cidade"):  # "rio de janeiro" / "GUADALUPE" viram "Rio de Janeiro" / "Guadalupe"
            setattr(self, campo, utils.limpar_nome(getattr(self, campo)))
        if kwargs.get("update_fields") is None:
            novo = self._state.adding
            mudou = not novo and self.cep != self._cep_inicial
            if mudou:  # endereço mudou: a localização antiga não vale mais
                self.latitude = self.longitude = None
                self.precisao_geo = ""
            if mudou or (novo and self.latitude is None):
                self.geo_status = self.GeoStatus.PENDENTE if utils.validar_cep(self.cep) else self.GeoStatus.SEM_CEP
        super().save(*args, **kwargs)
        self._cep_inicial = self.cep

    def get_absolute_url(self):
        return reverse("candidatos:detalhe", args=[self.pk])

    # -------- apresentação
    @property
    def tom(self):
        return self.TONS.get(self.status, "neutral")

    @property
    def cpf_formatado(self):
        return utils.formatar_cpf(self.cpf)

    @property
    def cpf_mascarado(self):
        return utils.mascarar_cpf(self.cpf)

    @property
    def telefone_formatado(self):
        return utils.formatar_telefone(self.telefone)

    @property
    def cep_formatado(self):
        return utils.formatar_cep(self.cep)

    @property
    def whatsapp(self):
        return utils.telefone_whatsapp(self.telefone)

    @property
    def territorio(self):
        partes = [p for p in (self.bairro, self.cidade) if p]
        return ", ".join(partes)

    @property
    def endereco_completo(self):
        rua = " ".join(p for p in (self.logradouro, self.numero) if p)
        if self.complemento:
            rua = f"{rua} — {self.complemento}" if rua else self.complemento
        local = ", ".join(p for p in (self.bairro, f"{self.cidade}/{self.uf}" if self.cidade and self.uf else self.cidade) if p)
        return " · ".join(p for p in (rua, local, self.cep_formatado) if p)

    @property
    def localizado(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def vinculo_ativo(self):
        return self.vinculos.filter(status=VINCULO_ATIVO).select_related("vaga").first()


class LinkCadastro(models.Model):
    """Link para o candidato se cadastrar. Sempre guarda quem forneceu o link (criado_por)."""

    class Tipo(models.TextChoices):
        PESSOAL = "pessoal", "Link pessoal de indicação"
        CONVITE = "convite", "Convite individual"

    token = models.CharField(max_length=40, unique=True, default=utils.gerar_token, editable=False)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="links", verbose_name="Fornecido por")
    vaga = models.ForeignKey("vagas.Vaga", null=True, blank=True, on_delete=models.SET_NULL, related_name="links")
    nome_sugerido = models.CharField(max_length=120, blank=True)
    telefone_sugerido = models.CharField(max_length=15, blank=True)
    candidato_origem = models.ForeignKey("Candidato", null=True, blank=True, on_delete=models.SET_NULL, related_name="convites")
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="convites_aprovados")
    aprovado_em = models.DateTimeField(null=True, blank=True)
    expira_em = models.DateTimeField(null=True, blank=True)
    enviado_em = models.DateTimeField(null=True, blank=True)
    usado_em = models.DateTimeField(null=True, blank=True)
    revogado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "link de cadastro"
        verbose_name_plural = "links de cadastro"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.criado_por}"

    @property
    def caminho(self):
        return reverse("candidatos:cadastro", args=[self.token])

    @property
    def estado(self):
        if self.revogado_em:
            return "revogado"
        if self.tipo == self.Tipo.CONVITE and self.usado_em:
            return "usado"
        if self.expira_em and timezone.now() > self.expira_em:
            return "expirado"
        return "ativo"

    @property
    def utilizavel(self):
        return self.estado == "ativo"

    @property
    def estado_rotulo(self):
        return {"ativo": "Ativo", "usado": "Usado", "expirado": "Expirado", "revogado": "Revogado"}[self.estado]

    @property
    def tom(self):
        return {"ativo": "info", "usado": "good", "expirado": "neutral", "revogado": "bad"}[self.estado]

    @classmethod
    def pessoal_de(cls, usuario):
        """Link permanente de indicação do usuário (criado na primeira vez que é pedido)."""
        link = cls.objects.filter(tipo=cls.Tipo.PESSOAL, criado_por=usuario, revogado_em__isnull=True).first()
        return link or cls.objects.create(tipo=cls.Tipo.PESSOAL, criado_por=usuario)


class Importacao(models.Model):
    """Uma planilha enviada: guarda a prévia validada e, depois da confirmação, o resultado."""

    class Status(models.TextChoices):
        PREVIA = "previa", "Aguardando confirmação"
        CONFIRMADA = "confirmada", "Importada"
        CANCELADA = "cancelada", "Cancelada"

    vaga = models.ForeignKey("vagas.Vaga", null=True, on_delete=models.SET_NULL, related_name="importacoes")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="importacoes")
    nome_arquivo = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREVIA)
    como_aprovados = models.BooleanField(default=False)
    dados = models.JSONField(default=list)
    total = models.PositiveIntegerField(default=0)
    novas = models.PositiveIntegerField(default=0)
    duplicadas = models.PositiveIntegerField(default=0)
    erros = models.PositiveIntegerField(default=0)
    criadas = models.PositiveIntegerField(default=0)
    vinculadas = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "importação"
        verbose_name_plural = "importações"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.nome_arquivo} ({self.get_status_display()})"
