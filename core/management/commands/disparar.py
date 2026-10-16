import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.management.commands.geocodificar import Command as Geocodificar
from mensagens import processador


class Command(BaseCommand):
    help = "Envia as mensagens dos disparos de WhatsApp que estão na fila, respeitando o ritmo da conexão (só com RRPA_WHATSAPP_API=1)."

    def add_arguments(self, parser):
        parser.add_argument("--continuo", action="store_true", help="Não encerra: repete a verificação de tempos em tempos.")
        parser.add_argument("--intervalo", type=int, default=5, help="Segundos de espera quando não há nada a fazer.")
        parser.add_argument("--lote", type=int, default=processador.MAXIMO_POR_CICLO, help="Máximo de mensagens por rodada.")
        parser.add_argument("--localizar", action="store_true", help="Também localiza no mapa, pelo CEP, os candidatos pendentes (como o 'geocodificar').")

    def handle(self, *args, **opcoes):
        if not settings.RRPA_WHATSAPP_API:
            self.stdout.write(
                "Disparo automático desligado (RRPA_WHATSAPP_API=0): nada é enviado." + ("" if opcoes["localizar"] else " Nada a fazer.")
            )
        parar = threading.Event()

        def pedir_parada(signum, frame):  # o Docker avisa com SIGTERM: termina a rodada em curso e sai
            parar.set()

        anteriores = {}
        if opcoes["continuo"]:
            for sinal in (signal.SIGINT, signal.SIGTERM):
                try:
                    anteriores[sinal] = signal.signal(sinal, pedir_parada)
                except ValueError:  # fora da thread principal
                    break
        localizador = Geocodificar(stdout=self.stdout, stderr=self.stderr)
        try:
            while not parar.is_set():
                close_old_connections()
                houve = self.rodada(opcoes["lote"])
                if opcoes["localizar"]:
                    houve = bool(localizador.rodada(5)) or houve
                if not opcoes["continuo"]:
                    return
                parar.wait(1 if houve else opcoes["intervalo"])
        finally:
            for sinal, antigo in anteriores.items():
                signal.signal(sinal, antigo)

    def rodada(self, lote):
        if not settings.RRPA_WHATSAPP_API:
            return False
        relatorio = processador.ciclo(maximo=lote)
        if processador.houve_atividade(relatorio):
            self.stdout.write(
                f"Disparos: {relatorio['enviadas']} enviada(s), {relatorio['falhas']} falha(s), {relatorio['ignoradas']} não enviada(s), "
                f"{relatorio['reagendadas']} para tentar de novo, {relatorio['concluidos']} disparo(s) concluído(s)."
            )
        return processador.houve_atividade(relatorio)
