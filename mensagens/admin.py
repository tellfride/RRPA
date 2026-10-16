from django.contrib import admin

from .models import Disparo, EnvioMensagem, MensagemDisparo, MensagemRecebida, ModeloMensagem


@admin.register(ModeloMensagem)
class ModeloMensagemAdmin(admin.ModelAdmin):
    list_display = ("nome", "categoria", "ativo")
    list_filter = ("categoria", "ativo")


@admin.register(EnvioMensagem)
class EnvioMensagemAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "nome_destino", "usuario", "modelo")
    search_fields = ("nome_destino", "texto")


@admin.register(Disparo)
class DisparoAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "nome", "status", "criado_por", "provedor")
    list_filter = ("status", "provedor")


@admin.register(MensagemDisparo)
class MensagemDisparoAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "disparo", "nome_destino", "status", "tentativas")
    list_filter = ("status",)
    search_fields = ("nome_destino",)


@admin.register(MensagemRecebida)
class MensagemRecebidaAdmin(admin.ModelAdmin):
    list_display = ("recebida_em", "candidato", "pediu_saida")
    list_filter = ("pediu_saida",)
