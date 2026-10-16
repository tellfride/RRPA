from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from core.constantes import PERFIL_ADMIN, PERFIL_GESTOR, PERFIL_RECRUTADOR, PERFIL_VISUALIZADOR


class UsuarioManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("perfil", PERFIL_ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    """Quem acessa o sistema. O perfil define o que cada pessoa vê e pode fazer."""

    class Perfil(models.TextChoices):
        ADMIN = PERFIL_ADMIN, "Administrador(a)"
        RECRUTADOR = PERFIL_RECRUTADOR, "Recrutador(a)"
        GESTOR = PERFIL_GESTOR, "Responsável do setor"
        VISUALIZADOR = PERFIL_VISUALIZADOR, "Visualizador(a)"

    perfil = models.CharField("Perfil", max_length=20, choices=Perfil.choices, default=PERFIL_VISUALIZADOR)
    telefone = models.CharField("Telefone", max_length=15, blank=True)

    objects = UsuarioManager()

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        ordering = ["first_name", "username"]

    def save(self, *args, **kwargs):
        # O perfil é a única fonte de verdade: só administradores acessam o /admin do Django.
        self.is_staff = self.is_superuser = self.perfil == PERFIL_ADMIN
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nome_exibicao

    @property
    def nome_exibicao(self):
        return self.get_full_name() or self.username

    @property
    def eh_admin(self):
        return self.perfil == PERFIL_ADMIN

    @property
    def eh_gestor(self):
        return self.perfil == PERFIL_GESTOR

    @property
    def eh_visualizador(self):
        return self.perfil == PERFIL_VISUALIZADOR

    @property
    def pode_recrutar(self):
        """Cadastra/aprova candidatos, chama para a vaga, envia mensagens."""
        return self.perfil in (PERFIL_ADMIN, PERFIL_RECRUTADOR)

    @property
    def pode_checkin(self):
        """Valida o cumprimento do RPA (responsável do setor)."""
        return self.perfil in (PERFIL_ADMIN, PERFIL_GESTOR)

    @property
    def pode_gerar_link(self):
        return self.perfil in (PERFIL_ADMIN, PERFIL_RECRUTADOR, PERFIL_GESTOR)
