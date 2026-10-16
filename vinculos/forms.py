from datetime import date

from django import forms
from django.utils import timezone

from candidatos.models import Candidato
from core.forms import EstiloFormMixin
from vagas.models import Vaga

from .models import Vinculo


class ChamarForm(EstiloFormMixin, forms.Form):
    candidato = forms.ModelChoiceField(label="Candidato aprovado", queryset=Candidato.objects.none(), empty_label="Escolha um candidato")
    vaga = forms.ModelChoiceField(label="Vaga", queryset=Vaga.objects.none(), empty_label="Escolha a vaga")
    data_chamada = forms.DateField(
        label="Data da chamada", widget=forms.DateInput(attrs={"type": "date", "data-previa-prazo": "1"}, format="%Y-%m-%d"),
        help_text="O prazo conta a partir do mês desta data.",
    )
    observacoes = forms.CharField(label="Observações (opcional)", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["candidato"].queryset = Candidato.objects.filter(status=Candidato.Status.APROVADO).order_by("nome")
        self.fields["candidato"].label_from_instance = lambda c: f"{c.nome} — {c.territorio or 'sem endereço'}"
        self.fields["vaga"].queryset = Vaga.objects.filter(ativa=True).order_by("nome")
        self.fields["data_chamada"].initial = timezone.localdate()


class EncerrarForm(EstiloFormMixin, forms.Form):
    motivo = forms.ChoiceField(label="Motivo", choices=Vinculo.Motivo.choices, widget=forms.RadioSelect)
    observacao = forms.CharField(label="Observação (opcional)", required=False, widget=forms.Textarea(attrs={"rows": 3}))


def mes_valido(texto):
    """'2026-10' -> date(2026, 10, 1) ou None."""
    try:
        ano, mes = (int(p) for p in texto.split("-"))
        return date(ano, mes, 1)
    except (ValueError, AttributeError):
        return None
