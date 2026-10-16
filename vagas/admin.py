from django.contrib import admin

from .models import Vaga


@admin.register(Vaga)
class VagaAdmin(admin.ModelAdmin):
    list_display = ("nome", "setor", "quantidade", "ativa")
    list_filter = ("ativa",)
    search_fields = ("nome", "setor")
    filter_horizontal = ("responsaveis",)
