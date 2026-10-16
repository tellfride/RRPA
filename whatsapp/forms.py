import re

from django import forms

from core.forms import EstiloFormMixin

from . import registro
from .cripto import ErroCredenciais, mascarar
from .models import ConexaoWhatsApp
from .provedores.simulador import TAXA_MAXIMA


def _id_numerico(valor):
    if not re.fullmatch(r"\d{5,25}", valor):
        raise forms.ValidationError("Informe só os números do ID.")
    return valor


def _sem_espacos(rotulo, minimo):
    def regra(valor):
        if re.search(r"\s", valor):
            raise forms.ValidationError(f"{rotulo} não pode ter espaços.")
        if len(valor) < minimo:
            raise forms.ValidationError(f"{rotulo} parece incompleto.")
        return valor

    return regra


def _versao(valor):
    normal = valor.lower() if valor.lower().startswith("v") else f"v{valor.lower()}"
    if not re.fullmatch(r"v\d{1,2}\.\d", normal):
        raise forms.ValidationError("Use o formato v21.0.")
    return normal


def _taxa(valor):
    try:
        n = float(valor.replace(",", "."))
    except ValueError:
        n = -1.0
    if not 0 <= n <= TAXA_MAXIMA:
        raise forms.ValidationError("Informe um valor entre 0 e 0,5.")
    return format(n, "g")


REGRAS = {
    "phone_number_id": _id_numerico,
    "business_account_id": _id_numerico,
    "access_token": _sem_espacos("O token", 20),
    "app_secret": _sem_espacos("A chave secreta", 16),
    "api_version": _versao,
    "taxa_falhas": _taxa,
}


class ConexaoForm(EstiloFormMixin, forms.Form):
    """Cadastro da conexão. Segredos nunca voltam ao navegador: em branco = manter o que já está salvo."""

    provedor = forms.ChoiceField(label="Provedor", choices=ConexaoWhatsApp.Provedor.choices)
    limite_por_minuto = forms.IntegerField(
        label="Limite de mensagens por minuto", min_value=1, max_value=300,
        help_text="O sistema nunca passa disso, somando todos os disparos. Comece baixo e suba aos poucos.",
    )
    exigir_autorizacao = forms.BooleanField(
        label="Enviar só a quem autorizou mensagens por WhatsApp", required=False,
        help_text="Desligado: recebe quem não pediu para sair (cadastros da equipe sem registro de autorização). Ligado: só quem marcou que aceita.",
    )

    def __init__(self, *args, conexao, **kwargs):
        self.conexao = conexao
        try:
            self.guardadas = conexao.credenciais_abertas()
            self.ilegivel = False
        except ErroCredenciais:
            self.guardadas, self.ilegivel = {}, True
        super().__init__(*args, **kwargs)
        self.fields["provedor"].widget.attrs["data-provedor-select"] = "1"  # app.js mostra só os campos do provedor escolhido
        self.initial.update(
            provedor=conexao.provedor, limite_por_minuto=conexao.limite_por_minuto, exigir_autorizacao=conexao.exigir_autorizacao,
        )
        for definicao in registro.PROVEDORES:
            for campo in definicao.campos:
                if not campo.segredo:
                    self.initial[campo.nome] = self.guardadas.get(campo.nome, "")
                elif self.guardadas.get(campo.nome):
                    self.fields[campo.nome].help_text = (
                        f"{campo.ajuda} Salvo: {mascarar(self.guardadas[campo.nome])}. Deixe em branco para manter."
                    )

    def grupos(self):
        """[(definição do provedor, [campos do formulário])] — a tela mostra só o grupo do provedor escolhido."""
        return [(d, [self[c.nome] for c in d.campos]) for d in registro.PROVEDORES]

    def clean(self):
        dados = super().clean()
        definicao = registro.definicao(dados.get("provedor"))
        if definicao is None:
            return dados
        novas = {}
        for campo in definicao.campos:
            valor = (dados.get(campo.nome) or "").strip()
            if not valor and campo.segredo:
                valor = self.guardadas.get(campo.nome, "")
            if not valor:
                if campo.obrigatorio:
                    self.add_error(campo.nome, "Obrigatório para este provedor.")
                continue
            regra = REGRAS.get(campo.nome)
            try:
                novas[campo.nome] = regra(valor) if regra else valor
            except forms.ValidationError as erro:
                self.add_error(campo.nome, erro)
        # Credenciais do outro provedor ficam como estavam; as do escolhido são substituídas pelo que foi digitado.
        proprios = {c.nome for c in definicao.campos}
        dados["credenciais"] = {**{k: v for k, v in self.guardadas.items() if k not in proprios}, **novas}
        return dados

    def salvar(self):
        """Grava a conexão. Devolve True se mudou algo que exige testar de novo (provedor ou credenciais)."""
        dados, con = self.cleaned_data, self.conexao
        mudou = con.provedor != dados["provedor"] or dados["credenciais"] != self.guardadas
        con.provedor = dados["provedor"]
        con.limite_por_minuto = dados["limite_por_minuto"]
        con.exigir_autorizacao = dados["exigir_autorizacao"]
        con.guardar_credenciais(dados["credenciais"])
        if mudou:
            con.status, con.numero_exibido, con.ultimo_erro, con.testada_em = ConexaoWhatsApp.Status.PENDENTE, "", "", None
        con.save()
        return mudou


def _campos_dos_provedores():
    """Um campo de formulário para cada credencial de cada provedor (a tela mostra só os do provedor escolhido)."""
    campos = {}
    for definicao in registro.PROVEDORES:
        for campo in definicao.campos:
            if campo.segredo:
                widget = forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"})
            else:
                widget = forms.TextInput(attrs={"placeholder": campo.exemplo})
            campos[campo.nome] = forms.CharField(label=campo.rotulo, required=False, max_length=4096, widget=widget, help_text=campo.ajuda)
    return campos


ConexaoForm.base_fields.update(_campos_dos_provedores())
