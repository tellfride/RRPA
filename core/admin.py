from django.contrib import admin

from .models import Configuracao, GeoCache, LogAtividade


@admin.register(Configuracao)
class ConfiguracaoAdmin(admin.ModelAdmin):
    list_display = ("nome_unidade", "cep_unidade", "prazo_meses", "contagem_prazo", "dias_alerta")


@admin.register(GeoCache)
class GeoCacheAdmin(admin.ModelAdmin):
    list_display = ("cep", "bairro", "cidade", "uf", "precisao", "fonte", "atualizado_em")
    search_fields = ("cep", "bairro", "cidade")
    list_filter = ("precisao", "fonte")


@admin.register(LogAtividade)
class LogAtividadeAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "usuario", "acao", "descricao")
    list_filter = ("acao",)
    search_fields = ("descricao",)
    readonly_fields = [f.name for f in LogAtividade._meta.fields]
