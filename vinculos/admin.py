from django.contrib import admin

from .models import Checkin, Vinculo


class CheckinInline(admin.TabularInline):
    model = Checkin
    extra = 0


@admin.register(Vinculo)
class VinculoAdmin(admin.ModelAdmin):
    list_display = ("candidato", "vaga", "data_chamada", "data_fim", "status")
    list_filter = ("status", "vaga")
    search_fields = ("candidato__nome",)
    raw_id_fields = ("candidato",)
    inlines = [CheckinInline]
