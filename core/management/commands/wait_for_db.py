import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Espera o banco de dados aceitar conexões (usado na inicialização do container)."

    def add_arguments(self, parser):
        parser.add_argument("--tentativas", type=int, default=60)

    def handle(self, *args, **opcoes):
        for tentativa in range(1, opcoes["tentativas"] + 1):
            try:
                connections["default"].ensure_connection()
            except OperationalError:
                self.stdout.write(f"Banco de dados indisponível (tentativa {tentativa}); aguardando...")
                time.sleep(2)
            else:
                self.stdout.write(self.style.SUCCESS("Banco de dados disponível."))
                return
        raise SystemExit("Não foi possível conectar ao banco de dados.")
