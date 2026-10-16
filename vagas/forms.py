from django import forms

from contas.models import Usuario
from core.constantes import PERFIL_ADMIN, PERFIL_GESTOR
from core.forms import EstiloFormMixin

from .models import Vaga


class ResponsavelChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, usuario):
        return f"{usuario.nome_exibicao} — {usuario.get_perfil_display()}"


class VagaForm(EstiloFormMixin, forms.ModelForm):
    responsaveis = ResponsavelChoiceField(
        label="Responsáveis pelo check-in", required=False, queryset=Usuario.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text="Quem valida, mês a mês, que o colaborador cumpriu o RPA. Cadastre usuários com o perfil “Responsável do setor”.",
    )

    class Meta:
        model = Vaga
        fields = ["nome", "setor", "quantidade", "descricao", "responsaveis", "ativa"]
        labels = {"nome": "Nome do cargo", "descricao": "Descrição e requisitos", "ativa": "Vaga ativa (aceita novas chamadas)"}
        widgets = {"descricao": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsaveis"].queryset = Usuario.objects.filter(
            is_active=True, perfil__in=[PERFIL_GESTOR, PERFIL_ADMIN],
        ).order_by("perfil", "first_name")

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        existente = Vaga.objects.filter(nome__iexact=nome).exclude(pk=self.instance.pk)
        if existente.exists():
            raise forms.ValidationError("Já existe uma vaga com este nome.")
        return nome

    def clean_quantidade(self):
        quantidade = self.cleaned_data["quantidade"]
        if self.instance.pk:
            ocupadas = self.instance.ocupadas()
            if quantidade < ocupadas:
                raise forms.ValidationError(
                    f"{ocupadas} colaboradores ocupam esta vaga agora. A quantidade não pode ser menor que isso."
                )
        return quantidade
