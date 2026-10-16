"""Controle de acesso por perfil."""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .constantes import PERFIL_ADMIN, PERFIL_GESTOR, PERFIL_RECRUTADOR, PERFIL_VISUALIZADOR

TODOS = (PERFIL_ADMIN, PERFIL_RECRUTADOR, PERFIL_GESTOR, PERFIL_VISUALIZADOR)
EQUIPE_RECRUTAMENTO = (PERFIL_ADMIN, PERFIL_RECRUTADOR)
EQUIPE_SETOR = (PERFIL_ADMIN, PERFIL_GESTOR)
SOMENTE_ADMIN = (PERFIL_ADMIN,)
GERADORES_DE_LINK = (PERFIL_ADMIN, PERFIL_RECRUTADOR, PERFIL_GESTOR)


class PerfilRequeridoMixin(AccessMixin):
    """Mixin para class-based views: só deixa passar os perfis em `perfis_permitidos`."""

    perfis_permitidos = TODOS

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.perfil not in self.perfis_permitidos:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def perfil_requerido(*perfis):
    """Decorador equivalente para function views."""

    def decorador(view):
        @wraps(view)
        def interna(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.perfil not in perfis:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return interna

    return decorador
