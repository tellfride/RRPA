from django.contrib import admin

from .models import ConexaoWhatsApp


@admin.register(ConexaoWhatsApp)
class ConexaoWhatsAppAdmin(admin.ModelAdmin):
    """Só leitura: as credenciais nunca aparecem aqui. Edite em Configurações › WhatsApp."""

    list_display = ("provedor", "status", "numero_exibido", "limite_por_minuto", "testada_em")
    exclude = ("credenciais", "webhook_token")
    readonly_fields = [f.name for f in ConexaoWhatsApp._meta.fields if f.name not in ("credenciais", "webhook_token")]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
