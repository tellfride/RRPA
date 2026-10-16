from django import forms

from .models import Configuracao
from .utils import so_digitos, validar_cep


class EstiloFormMixin:
    """Aplica as classes CSS do sistema a todos os campos do formulário."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            widget = campo.widget
            if isinstance(widget, forms.CheckboxInput):
                classe = "check"
            elif isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                classe = "opcoes"
            elif isinstance(widget, forms.Select):  # inclui SelectMultiple
                classe = "select"
            elif isinstance(widget, forms.Textarea):
                classe = "textarea"
            elif isinstance(widget, forms.FileInput):
                classe = "arquivo"
            else:
                classe = "input"
            widget.attrs["class"] = f'{widget.attrs.get("class", "")} {classe}'.strip()


class ConfiguracaoForm(EstiloFormMixin, forms.ModelForm):
    cep_unidade = forms.CharField(label="CEP da unidade", max_length=9, required=False,
                                  help_text="Usado para calcular a distância dos candidatos no mapa.")

    class Meta:
        model = Configuracao
        fields = ["nome_unidade", "cep_unidade", "prazo_meses", "contagem_prazo", "dias_alerta", "validade_convite_dias"]

    def clean_cep_unidade(self):
        cep = so_digitos(self.cleaned_data.get("cep_unidade"))
        if cep and not validar_cep(cep):
            raise forms.ValidationError("Informe um CEP com 8 números.")
        return cep
