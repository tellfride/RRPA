from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from core import ratelimit
from core.models import LogAtividade
from core.permissoes import SOMENTE_ADMIN, PerfilRequeridoMixin, perfil_requerido
from core.utils import ip_do_cliente

from .forms import LoginForm, RedefinirSenhaForm, SenhaForm, UsuarioForm
from .models import Usuario

TENTATIVAS_LOGIN = 8
JANELA_LOGIN = 10 * 60


class EntrarView(LoginView):
    template_name = "contas/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def _chave(self, usuario=""):
        return f"login:{ip_do_cliente(self.request) or '?'}:{usuario.lower()}"

    def post(self, request, *args, **kwargs):
        if ratelimit.excedeu(self._chave(request.POST.get("username", "")), TENTATIVAS_LOGIN):
            form = self.get_form()
            form.add_error(None, "Muitas tentativas seguidas. Aguarde alguns minutos e tente de novo.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ratelimit.registrar(self._chave(self.request.POST.get("username", "")), JANELA_LOGIN)
        return super().form_invalid(form)

    def form_valid(self, form):
        ratelimit.zerar(self._chave(self.request.POST.get("username", "")))
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "usuario.entrou", f"{self.request.user.nome_exibicao} entrou no sistema", request=self.request)
        return resposta

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.conf import settings

        if settings.RRPA_DEMO:
            ctx["demo_contas"] = list(
                Usuario.objects.filter(username__in=["admin", "recrutadora", "gestor.farmacia", "visualizador"])
                .order_by("perfil").values("username", "first_name", "perfil")
            )
            perfis = dict(Usuario.Perfil.choices)
            for conta in ctx["demo_contas"]:
                conta["perfil_rotulo"] = perfis.get(conta["perfil"], conta["perfil"])
            ctx["demo_senha"] = settings.RRPA_DEMO_SENHA
        return ctx


@require_POST
def sair(request):
    logout(request)
    return redirect("contas:login")


class MinhaSenhaView(PasswordChangeView):
    template_name = "contas/minha_senha.html"
    form_class = SenhaForm
    success_url = reverse_lazy("painel:inicio")

    def form_valid(self, form):
        messages.success(self.request, "Senha alterada.")
        LogAtividade.registrar(self.request.user, "usuario.senha", "Alterou a própria senha", request=self.request)
        return super().form_valid(form)


class UsuariosView(PerfilRequeridoMixin, ListView):
    perfis_permitidos = SOMENTE_ADMIN
    template_name = "contas/usuarios.html"
    context_object_name = "usuarios"

    def get_queryset(self):
        qs = Usuario.objects.all()
        q = self.request.GET.get("q", "").strip()
        perfil = self.request.GET.get("perfil", "")
        if q:
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q) | Q(email__icontains=q))
        if perfil:
            qs = qs.filter(perfil=perfil)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(q=self.request.GET.get("q", ""), perfil=self.request.GET.get("perfil", ""), perfis=Usuario.Perfil.choices)
        return ctx


class UsuarioCriarView(PerfilRequeridoMixin, CreateView):
    perfis_permitidos = SOMENTE_ADMIN
    model = Usuario
    form_class = UsuarioForm
    template_name = "contas/usuario_form.html"
    success_url = reverse_lazy("contas:usuarios")

    def form_valid(self, form):
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "usuario.criado", f"Criou o acesso de {self.object.nome_exibicao} ({self.object.get_perfil_display()})", self.object, self.request)
        messages.success(self.request, f"Acesso de {self.object.nome_exibicao} criado.")
        return resposta


class UsuarioEditarView(PerfilRequeridoMixin, UpdateView):
    perfis_permitidos = SOMENTE_ADMIN
    model = Usuario
    form_class = UsuarioForm
    template_name = "contas/usuario_form.html"
    success_url = reverse_lazy("contas:usuarios")

    def form_valid(self, form):
        if self.object.pk == self.request.user.pk and (not form.cleaned_data["is_active"] or form.cleaned_data["perfil"] != "admin"):
            form.add_error(None, "Você não pode desativar nem rebaixar o seu próprio acesso.")
            return self.form_invalid(form)
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "usuario.editado", f"Atualizou o acesso de {self.object.nome_exibicao}", self.object, self.request)
        messages.success(self.request, "Dados salvos.")
        return resposta


@perfil_requerido(*SOMENTE_ADMIN)
def redefinir_senha(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    form = RedefinirSenhaForm(usuario, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        LogAtividade.registrar(request.user, "usuario.senha", f"Redefiniu a senha de {usuario.nome_exibicao}", usuario, request)
        messages.success(request, f"Nova senha definida para {usuario.nome_exibicao}.")
        return redirect("contas:usuarios")
    return render(request, "contas/redefinir_senha.html", {"form": form, "usuario_alvo": usuario})
