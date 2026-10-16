import time

from django.core.management.base import BaseCommand

from candidatos.models import Candidato
from core import geo


class Command(BaseCommand):
    help = "Localiza no mapa, pelo CEP, os candidatos que ainda não têm posição (e mantém o ponto da unidade em dia)."

    INTERVALO_UNIDADE = 60  # segundos entre as conferências do ponto da unidade
    _unidade_conferida_em = float("-inf")

    def add_arguments(self, parser):
        parser.add_argument("--continuo", action="store_true", help="Não encerra: repete a busca de tempos em tempos.")
        parser.add_argument("--intervalo", type=int, default=30, help="Segundos de espera quando não há progresso.")
        parser.add_argument("--lote", type=int, default=20, help="Candidatos por rodada.")

    def handle(self, *args, **opcoes):
        while True:
            resolvidos = self.rodada(opcoes["lote"])
            if not opcoes["continuo"]:
                return
            time.sleep(2 if resolvidos else opcoes["intervalo"])

    def acertar_unidade(self):
        """Mantém o ponto da unidade igual ao do CEP dela: depois de uma atualização, o ponto antigo se corrige sozinho."""
        agora = time.monotonic()
        if agora - self._unidade_conferida_em < self.INTERVALO_UNIDADE:
            return
        self._unidade_conferida_em = agora
        geo.localizar_unidade()

    def rodada(self, lote):
        self.acertar_unidade()
        pendentes = list(Candidato.objects.filter(geo_status=Candidato.GeoStatus.PENDENTE).order_by("id")[:lote])
        resolvidos = 0
        for candidato in pendentes:
            geo.aplicar_localizacao(candidato)
            candidato.refresh_from_db(fields=["geo_status"])
            resolvidos += candidato.geo_status != Candidato.GeoStatus.PENDENTE
        if pendentes:
            self.stdout.write(f"Localização: {resolvidos} de {len(pendentes)} candidatos resolvidos.")
        return resolvidos
