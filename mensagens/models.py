from django.conf import settings
from django.db import models


class ModeloMensagem(models.Model):
    """Texto-padrão de WhatsApp com campos que o sistema preenche ({primeiro_nome}, {vaga}, {link_cadastro}...)."""

    class Categoria(models.TextChoices):
        CONVITE_CADASTRO = "convite_cadastro", "Convite para cadastro"
        APROVACAO = "aprovacao", "Aprovação"
        CONVOCACAO = "convocacao", "Convocação para a vaga"
        LEMBRETE = "lembrete", "Lembrete de vencimento"
        RETORNO = "retorno", "Retorno ao candidato"
        GERAL = "geral", "Geral"

    nome = models.CharField("Nome do modelo", max_length=80)
    categoria = models.CharField("Quando usar", max_length=20, choices=Categoria.choices, default=Categoria.GERAL)
    texto = models.TextField("Mensagem", max_length=1200)
    # API oficial da Meta: fora da janela de 24 h só vai template aprovado. Os campos abaixo dizem qual é e o que
    # preenche {{1}}, {{2}}... (lista de nomes de campo, na ordem). O disparo pelo Simulador usa só o texto acima.
    template_oficial_nome = models.CharField("Nome do template aprovado na Meta", max_length=120, blank=True)
    template_oficial_idioma = models.CharField("Idioma do template", max_length=10, blank=True, default="pt_BR")
    template_oficial_variaveis = models.JSONField("Campos de {{1}}, {{2}}…", default=list, blank=True)
    ativo = models.BooleanField(default=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "modelo de mensagem"
        verbose_name_plural = "modelos de mensagem"
        ordering = ["categoria", "nome"]

    def __str__(self):
        return self.nome


class EnvioMensagem(models.Model):
    """Registro de cada mensagem aberta no WhatsApp (o envio em si é feito pela pessoa, no aplicativo)."""

    candidato = models.ForeignKey("candidatos.Candidato", null=True, blank=True, on_delete=models.SET_NULL, related_name="mensagens")
    link = models.ForeignKey("candidatos.LinkCadastro", null=True, blank=True, on_delete=models.SET_NULL, related_name="mensagens")
    modelo = models.ForeignKey(ModeloMensagem, null=True, blank=True, on_delete=models.SET_NULL, related_name="envios")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="mensagens_enviadas")
    nome_destino = models.CharField(max_length=120)
    telefone = models.CharField(max_length=15)
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "mensagem aberta no WhatsApp"
        verbose_name_plural = "mensagens abertas no WhatsApp"
        ordering = ["-criado_em", "-id"]

    def __str__(self):
        return f"{self.nome_destino}: {self.texto[:40]}"


class Disparo(models.Model):
    """Envio automático de um modelo para vários candidatos pela API do WhatsApp (processado pelo worker)."""

    class Status(models.TextChoices):
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        PAUSADO = "pausado", "Pausado"
        CONCLUIDO = "concluido", "Concluído"
        CANCELADO = "cancelado", "Cancelado"

    TONS = {"em_andamento": "info", "pausado": "warn", "concluido": "good", "cancelado": "neutral"}

    nome = models.CharField(max_length=120)
    modelo = models.ForeignKey(ModeloMensagem, null=True, blank=True, on_delete=models.SET_NULL, related_name="disparos")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="disparos")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.EM_ANDAMENTO, db_index=True)
    motivo_pausa = models.CharField(max_length=200, blank=True)
    ritmo_por_minuto = models.PositiveSmallIntegerField(default=20)
    provedor = models.CharField(max_length=12, blank=True)
    # Cópia do template no momento do disparo: mexer no modelo depois não muda o que está em andamento.
    template_nome = models.CharField(max_length=120, blank=True)
    template_idioma = models.CharField(max_length=10, blank=True, default="pt_BR")
    criado_em = models.DateTimeField(auto_now_add=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "disparo de WhatsApp"
        verbose_name_plural = "disparos de WhatsApp"
        ordering = ["-criado_em", "-id"]

    def __str__(self):
        return self.nome

    @property
    def tom(self):
        return self.TONS.get(self.status, "neutral")

    @property
    def ativo(self):
        return self.status in (self.Status.EM_ANDAMENTO, self.Status.PAUSADO)

    @property
    def provedor_rotulo(self):
        return {"meta": "API oficial (Meta)", "simulador": "Simulador"}.get(self.provedor, self.provedor or "—")


class MensagemDisparo(models.Model):
    """Uma mensagem de um disparo, com o texto já preenchido para aquela pessoa e o andamento do envio."""

    class Status(models.TextChoices):
        FILA = "fila", "Na fila"
        ENVIANDO = "enviando", "Enviando"
        ENVIADA = "enviada", "Enviada"
        ENTREGUE = "entregue", "Entregue"
        LIDA = "lida", "Lida"
        FALHOU = "falhou", "Falhou"
        IGNORADA = "ignorada", "Não enviada"

    TONS = {"fila": "neutral", "enviando": "info", "enviada": "info", "entregue": "good", "lida": "accent", "falhou": "bad", "ignorada": "warn"}

    disparo = models.ForeignKey(Disparo, on_delete=models.CASCADE, related_name="mensagens")
    candidato = models.ForeignKey("candidatos.Candidato", null=True, blank=True, on_delete=models.SET_NULL, related_name="mensagens_disparo")
    nome_destino = models.CharField(max_length=120)
    telefone = models.CharField(max_length=15)
    texto = models.TextField(blank=True)
    parametros = models.JSONField(default=list, blank=True)  # valores de {{1}}, {{2}}... do template aprovado
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FILA)
    id_provedor = models.CharField(max_length=200, blank=True, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    tentativa_em = models.DateTimeField(null=True, blank=True, db_index=True)  # última chamada ao provedor (controla o ritmo)
    proxima_tentativa_em = models.DateTimeField(null=True, blank=True)  # espera entre tentativas após falha passageira
    enviada_em = models.DateTimeField(null=True, blank=True)
    entregue_em = models.DateTimeField(null=True, blank=True)
    lida_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "mensagem do disparo"
        verbose_name_plural = "mensagens dos disparos"
        ordering = ["id"]
        indexes = [models.Index(fields=["disparo", "status"])]

    def __str__(self):
        return f"{self.nome_destino}: {self.get_status_display()}"

    @property
    def tom(self):
        return self.TONS.get(self.status, "neutral")


class MensagemRecebida(models.Model):
    """Resposta de um candidato recebida pelo webhook. Guarda o suficiente para saber por que alguém saiu da lista."""

    telefone = models.CharField(max_length=20)
    texto = models.CharField(max_length=500, blank=True)
    candidato = models.ForeignKey("candidatos.Candidato", null=True, blank=True, on_delete=models.SET_NULL, related_name="respostas_whatsapp")
    pediu_saida = models.BooleanField(default=False)
    recebida_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "resposta recebida no WhatsApp"
        verbose_name_plural = "respostas recebidas no WhatsApp"
        ordering = ["-recebida_em", "-id"]

    def __str__(self):
        return f"{'SAIR' if self.pediu_saida else 'Resposta'} em {self.recebida_em:%d/%m/%Y}"
