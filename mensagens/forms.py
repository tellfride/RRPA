import re

from django import forms

from core.forms import EstiloFormMixin
from whatsapp.ativacao import api_ligada

from .models import ModeloMensagem
from .render import NOMES_VALIDOS, campos_desconhecidos

NOME_TEMPLATE = re.compile(r"^[a-z0-9_]{1,120}$")
IDIOMA_TEMPLATE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
MAX_VARIAVEIS = 10


class ModeloMensagemForm(EstiloFormMixin, forms.ModelForm):
    # Na tela é uma lista separada por vírgulas ("primeiro_nome, vaga"); no banco, uma lista JSON.
    template_oficial_variaveis = forms.CharField(
        label="Campos de {{1}}, {{2}}… (na ordem)", required=False,
        help_text="Quais campos do R-RPA preenchem os parâmetros do template, separados por vírgula. Ex.: primeiro_nome, vaga",
    )

    class Meta:
        model = ModeloMensagem
        fields = ["nome", "categoria", "texto", "template_oficial_nome", "template_oficial_idioma", "template_oficial_variaveis", "ativo"]
        labels = {"ativo": "Disponível para uso"}
        widgets = {"texto": forms.Textarea(attrs={"rows": 7, "data-previa-mensagem": "1"})}
        help_texts = {
            "template_oficial_nome": "Exatamente como foi aprovado no WhatsApp Manager (minúsculas, números e _). Só é usado na API oficial da Meta.",
            "template_oficial_idioma": "Código do idioma do template. Padrão: pt_BR.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not api_ligada():  # tela sem esses campos: salvar o modelo não pode apagar um template já cadastrado
            for nome in ("template_oficial_nome", "template_oficial_idioma", "template_oficial_variaveis"):
                del self.fields[nome]
        else:
            self.initial["template_oficial_variaveis"] = ", ".join(self.instance.template_oficial_variaveis or [])

    def clean_texto(self):
        texto = self.cleaned_data["texto"].strip()
        desconhecidos = campos_desconhecidos(texto)
        if desconhecidos:
            raise forms.ValidationError(
                "Campo(s) desconhecido(s): " + ", ".join("{{" + c + "}}" for c in desconhecidos) + ". Use os botões abaixo para inserir campos válidos."
            )
        return texto

    def clean_template_oficial_nome(self):
        nome = self.cleaned_data["template_oficial_nome"].strip()
        if nome and not NOME_TEMPLATE.match(nome):
            raise forms.ValidationError("Use só letras minúsculas, números e _ (sem espaços), como aparece no WhatsApp Manager.")
        return nome

    def clean_template_oficial_idioma(self):
        idioma = self.cleaned_data["template_oficial_idioma"].strip() or "pt_BR"
        if not IDIOMA_TEMPLATE.match(idioma):
            raise forms.ValidationError("Use o formato pt_BR.")
        return idioma

    def clean_template_oficial_variaveis(self):
        campos = [c.strip().strip("{} ") for c in re.split(r"[,;\s]+", self.cleaned_data["template_oficial_variaveis"]) if c.strip("{} ")]
        invalidos = [c for c in campos if c not in NOMES_VALIDOS]
        if invalidos:
            raise forms.ValidationError("Campo(s) desconhecido(s): " + ", ".join(invalidos) + ". Use os nomes dos botões de campos.")
        if len(campos) > MAX_VARIAVEIS:
            raise forms.ValidationError(f"No máximo {MAX_VARIAVEIS} campos.")
        return campos

    def clean(self):
        dados = super().clean()
        if dados.get("template_oficial_variaveis") and not dados.get("template_oficial_nome"):
            self.add_error("template_oficial_nome", "Informe o nome do template aprovado para usar os campos de {{1}}, {{2}}…")
        return dados
