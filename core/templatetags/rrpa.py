"""Tags e filtros de template do R-RPA (carregados automaticamente em todos os templates)."""
from urllib.parse import urlencode

from django import template
from django.templatetags.static import static
from django.utils.html import format_html

from core import utils

register = template.Library()


@register.simple_tag
def icone(nome, classe=""):
    """Ícone do sprite (Lucide). Uso: {% icone "users" %}"""
    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="{}#i-{}"></use></svg>',
        classe, static("icons/sprite.svg"), nome,
    )


@register.simple_tag(takes_context=True)
def qs(context, **novos):
    """Querystring atual com alguns parâmetros trocados/removidos. Uso: ?{% qs page=3 %}"""
    params = context["request"].GET.copy()
    for chave, valor in novos.items():
        if valor in (None, ""):
            params.pop(chave, None)
        else:
            params[chave] = valor
    return params.urlencode()


@register.simple_tag
def wa_url(telefone, texto=""):
    numero = utils.telefone_whatsapp(telefone)
    if not numero:
        return ""
    return f"https://wa.me/{numero}" + (f"?{urlencode({'text': texto})}" if texto else "")


@register.filter
def chave(nome):
    """'nome' -> '{{nome}}' (campo de modelo de mensagem)."""
    return "{{" + str(nome) + "}}"


@register.filter
def cpf(valor):
    return utils.formatar_cpf(valor)


@register.filter
def cpf_mascarado(valor):
    return utils.mascarar_cpf(valor)


@register.filter
def telefone(valor):
    return utils.formatar_telefone(valor)


@register.filter
def cep(valor):
    return utils.formatar_cep(valor)


@register.filter
def iniciais(nome):
    return utils.iniciais(nome)


@register.filter
def primeiro_nome(nome):
    return utils.primeiro_nome(nome)


@register.filter
def nome_para(nome, usuario):
    """Visualizadores veem 'Maria L.'; os demais perfis veem o nome completo."""
    if getattr(usuario, "eh_visualizador", False):
        return utils.abreviar_nome(nome)
    return nome


@register.filter
def dias_texto(dias):
    """12 -> 'em 12 dias'; 0 -> 'hoje'; -3 -> 'há 3 dias'."""
    if dias is None:
        return ""
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "amanhã"
    if dias > 1:
        return f"em {dias} dias"
    if dias == -1:
        return "ontem"
    return f"há {-dias} dias"


@register.filter
def mes_ano(data):
    return utils.rotulo_mes(data) if data else ""


@register.filter
def mes_longo(data):
    return utils.rotulo_mes(data, longo=True) if data else ""


@register.filter
def pct(valor, total):
    try:
        return round(100 * float(valor) / float(total)) if float(total) else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def get_item(dicionario, chave):
    try:
        return dicionario.get(chave)
    except AttributeError:
        return None
