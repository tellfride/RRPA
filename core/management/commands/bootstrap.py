import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.constantes import PERFIL_ADMIN
from core.models import Configuracao
from mensagens.padroes import criar_modelos_padrao
from vagas.models import Vaga


class Command(BaseCommand):
    help = "Prepara o sistema na primeira execução (pode rodar sempre: não repete o que já existe)."

    def handle(self, *args, **opcoes):
        config = Configuracao.atual()
        nome = os.environ.get("RRPA_NOME_UNIDADE", "").strip()
        if nome and config.nome_unidade == "Minha unidade":
            config.nome_unidade = nome
            config.save(update_fields=["nome_unidade"])

        self._administrador()
        criados = criar_modelos_padrao()
        if criados:
            self.stdout.write(f"{criados} modelos de mensagem criados.")
        if settings.RRPA_DEMO and not Vaga.objects.exists():
            self.stdout.write("Modo demonstração: carregando dados fictícios...")
            call_command("seed_demo")

    def _administrador(self):
        Usuario = get_user_model()
        if Usuario.objects.filter(perfil=PERFIL_ADMIN, is_active=True).exists():
            return
        usuario = os.environ.get("RRPA_ADMIN_USUARIO", "").strip() or "admin"
        senha = os.environ.get("RRPA_ADMIN_SENHA", "")
        gerada = not senha
        if gerada:
            senha = secrets.token_urlsafe(12)
        Usuario.objects.create_user(
            username=usuario, password=senha, perfil=PERFIL_ADMIN, first_name="Administrador",
            email=os.environ.get("RRPA_ADMIN_EMAIL", ""),
        )
        self.stdout.write(self.style.SUCCESS(f"Administrador criado: {usuario}"))
        if gerada:
            self.stdout.write(self.style.WARNING(
                f"Senha gerada para '{usuario}': {senha}  (anote e troque em “Alterar minha senha”; esta mensagem não se repete)"
            ))
