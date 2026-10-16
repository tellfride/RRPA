from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm

from core.forms import EstiloFormMixin
from core.utils import normalizar_telefone, validar_telefone

from .models import Usuario


class LoginForm(EstiloFormMixin, AuthenticationForm):
    username = forms.CharField(label="Usuário", widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}))
    password = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    error_messages = {
        "invalid_login": "Usuário ou senha incorretos. Confira e tente de novo.",
        "inactive": "Este acesso está desativado. Fale com o administrador.",
    }


class SenhaForm(EstiloFormMixin, PasswordChangeForm):
    pass


class RedefinirSenhaForm(EstiloFormMixin, SetPasswordForm):
    pass


class UsuarioForm(EstiloFormMixin, forms.ModelForm):
    senha = forms.CharField(
        label="Senha inicial", strip=False, required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Mínimo de 8 caracteres. Deixe em branco para manter a senha atual.",
    )

    # aceita colar "+55 (11) 91234-5678": o texto é normalizado em clean_telefone (o banco guarda só 10 ou 11 números)
    telefone = forms.CharField(
        label="Telefone", max_length=20, required=False,
        widget=forms.TextInput(attrs={"data-mascara": "telefone", "inputmode": "tel", "placeholder": "(11) 91234-5678"}),
    )

    class Meta:
        model = Usuario
        fields = ["first_name", "last_name", "username", "email", "telefone", "perfil", "is_active"]
        labels = {"first_name": "Nome", "last_name": "Sobrenome", "username": "Usuário (login)", "email": "E-mail",
                  "is_active": "Acesso ativo"}
        help_texts = {"username": "Sem espaços. Ex.: maria.souza"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        if not self.instance.pk:
            self.fields["senha"].required = True
            self.fields["senha"].help_text = "Mínimo de 8 caracteres. A pessoa pode trocá-la depois em “Alterar minha senha”."

    def clean_telefone(self):
        telefone = normalizar_telefone(self.cleaned_data.get("telefone"))
        if telefone and not validar_telefone(telefone):
            raise forms.ValidationError("Informe o telefone com DDD.")
        return telefone

    def clean_senha(self):
        senha = self.cleaned_data.get("senha")
        if senha:
            password_validation.validate_password(senha, self.instance)
        return senha

    def save(self, commit=True):
        usuario = super().save(commit=False)
        senha = self.cleaned_data.get("senha")
        if senha:
            usuario.set_password(senha)
        if commit:
            usuario.save()
        return usuario
