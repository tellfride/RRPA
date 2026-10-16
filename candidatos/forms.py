from django import forms

from core.forms import EstiloFormMixin
from core.utils import (
    limpar_nome, normalizar_cep, normalizar_cpf, normalizar_telefone, validar_cep, validar_cpf, validar_telefone,
)
from vagas.models import Vaga

from .models import Candidato

UFS = "AC,AL,AP,AM,BA,CE,DF,ES,GO,MA,MT,MS,MG,PA,PB,PR,PE,PI,RJ,RN,RS,RO,RR,SC,SP,SE,TO".split(",")

ATRIBUTOS = {
    "nome": {"autocomplete": "name"},
    "cpf": {"data-mascara": "cpf", "inputmode": "numeric", "autocomplete": "off", "placeholder": "000.000.000-00"},
    "telefone": {"data-mascara": "telefone", "inputmode": "tel", "autocomplete": "tel", "placeholder": "(11) 91234-5678"},
    "cep": {"data-mascara": "cep", "data-cep": "1", "inputmode": "numeric", "autocomplete": "postal-code", "placeholder": "00000-000"},
    "logradouro": {"data-cep-campo": "logradouro", "autocomplete": "address-line1"},
    "bairro": {"data-cep-campo": "bairro"},
    "cidade": {"data-cep-campo": "cidade", "autocomplete": "address-level2"},
    "uf": {"data-cep-campo": "uf", "maxlength": "2", "autocomplete": "address-level1", "style": "text-transform:uppercase"},
}


class CamposDeCadastroMixin:
    """Validações compartilhadas entre o cadastro da equipe e o cadastro público."""

    def clean_nome(self):
        nome = limpar_nome(self.cleaned_data["nome"])
        if len(nome.split()) < 2:
            raise forms.ValidationError("Informe nome e sobrenome.")
        return nome

    def clean_cpf(self):
        cpf = normalizar_cpf(self.cleaned_data["cpf"])
        if not validar_cpf(cpf):
            raise forms.ValidationError("CPF inválido. Confira os números.")
        return cpf

    def clean_telefone(self):
        telefone = normalizar_telefone(self.cleaned_data["telefone"])
        if not validar_telefone(telefone):
            raise forms.ValidationError("Informe o telefone com DDD. Ex.: (11) 91234-5678.")
        return telefone

    def clean_cep(self):
        cep = normalizar_cep(self.cleaned_data["cep"])
        if not validar_cep(cep):
            raise forms.ValidationError("CEP inválido. São 8 números.")
        return cep

    def clean_uf(self):
        uf = (self.cleaned_data.get("uf") or "").strip().upper()
        if uf and uf not in UFS:
            raise forms.ValidationError("Use a sigla do estado. Ex.: SP.")
        return uf


class CandidatoForm(CamposDeCadastroMixin, EstiloFormMixin, forms.ModelForm):
    """Cadastro feito pela equipe."""

    vagas_interesse = forms.ModelMultipleChoiceField(
        label="Vagas de interesse", required=False, queryset=Vaga.objects.none(), widget=forms.CheckboxSelectMultiple,
    )
    # No banco ficam só os números (CPF 11, CEP 8), mas o campo recebe o texto com máscara ("529.982.247-25",
    # "01310-100", "+55 (11) 91234-5678"). Os limites do campo e do navegador (maxlength) têm de caber a máscara inteira.
    cpf = forms.CharField(label="CPF", max_length=14)
    telefone = forms.CharField(label="Telefone (WhatsApp)", max_length=20)
    cep = forms.CharField(label="CEP", max_length=9)

    class Meta:
        model = Candidato
        fields = ["nome", "cpf", "telefone", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
                  "vagas_interesse", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vagas_interesse"].queryset = Vaga.objects.filter(ativa=True).order_by("nome")
        self.fields["cep"].required = True
        self.fields["cep"].help_text = "O endereço é preenchido sozinho a partir do CEP e o candidato aparece no mapa."
        for nome, attrs in ATRIBUTOS.items():
            self.fields[nome].widget.attrs.update(attrs)

    def clean_cpf(self):
        cpf = super().clean_cpf()
        igual = Candidato.objects.filter(cpf=cpf).exclude(pk=self.instance.pk).first()
        if igual:
            raise forms.ValidationError(f"Este CPF já está cadastrado para {igual.nome}.")
        return cpf


class CadastroPublicoForm(CamposDeCadastroMixin, EstiloFormMixin, forms.Form):
    """Formulário aberto pelo candidato a partir do link recebido."""

    nome = forms.CharField(label="Nome completo", max_length=120)
    cpf = forms.CharField(label="CPF", max_length=14)
    telefone = forms.CharField(label="Telefone com WhatsApp", max_length=20)
    email = forms.EmailField(label="E-mail (opcional)", required=False)
    cep = forms.CharField(label="CEP", max_length=9)
    logradouro = forms.CharField(label="Rua ou avenida", max_length=150, required=False)
    numero = forms.CharField(label="Número", max_length=15, required=False)
    complemento = forms.CharField(label="Complemento", max_length=60, required=False)
    bairro = forms.CharField(label="Bairro", max_length=80, required=False)
    cidade = forms.CharField(label="Cidade", max_length=80, required=False)
    uf = forms.CharField(label="UF", max_length=2, required=False)
    vaga = forms.ModelChoiceField(label="Vaga de interesse", queryset=Vaga.objects.none(), required=False, empty_label="Escolha uma vaga")
    aceite = forms.BooleanField(label="Autorizo o uso dos meus dados para recrutamento (LGPD)", required=True,
                                error_messages={"required": "Para continuar, é preciso autorizar o uso dos dados."})
    aceita_whatsapp = forms.BooleanField(label="Aceito receber mensagens por WhatsApp sobre vagas", required=False)
    website = forms.CharField(required=False, label="Não preencha este campo")  # isca para robôs

    def __init__(self, *args, link=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.link = link
        vagas = Vaga.objects.filter(ativa=True).order_by("nome")
        if link is not None and link.vaga_id:
            vagas = vagas.filter(pk=link.vaga_id)
            self.fields["vaga"].initial = link.vaga_id
            self.fields["vaga"].empty_label = None
        self.fields["vaga"].queryset = vagas
        self.fields["cep"].help_text = "Preenchemos o endereço para você."
        self.fields["website"].widget.attrs.update({"tabindex": "-1", "autocomplete": "off"})
        for nome, attrs in ATRIBUTOS.items():
            self.fields[nome].widget.attrs.update(attrs)
        if link is not None and not self.is_bound:
            self.initial.setdefault("nome", link.nome_sugerido)
            self.initial.setdefault("telefone", link.telefone_sugerido)
            origem = link.candidato_origem
            if origem is not None:
                for campo in ("nome", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"):
                    self.initial[campo] = getattr(origem, campo)
                self.initial["telefone"] = origem.telefone

    def clean(self):
        dados = super().clean()
        origem = self.link.candidato_origem if self.link is not None else None
        if origem is not None and dados.get("cpf") and dados["cpf"] != origem.cpf:
            self.add_error("cpf", "Este CPF não confere com o cadastro que recebeu este convite.")
        return dados
