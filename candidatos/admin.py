from django.contrib import admin

from .models import Candidato, Importacao, LinkCadastro


@admin.register(Candidato)
class CandidatoAdmin(admin.ModelAdmin):
    list_display = ("nome", "status", "origem", "bairro", "cidade", "indicado_por", "criado_em")
    list_filter = ("status", "origem", "geo_status")
    search_fields = ("nome", "cpf", "telefone")
    raw_id_fields = ("indicado_por", "criado_por", "aprovado_por", "link")


@admin.register(LinkCadastro)
class LinkCadastroAdmin(admin.ModelAdmin):
    list_display = ("tipo", "criado_por", "nome_sugerido", "criado_em", "usado_em", "revogado_em")
    list_filter = ("tipo",)
    readonly_fields = ("token",)


@admin.register(Importacao)
class ImportacaoAdmin(admin.ModelAdmin):
    list_display = ("nome_arquivo", "vaga", "usuario", "status", "novas", "duplicadas", "erros", "criado_em")
    exclude = ("dados",)
