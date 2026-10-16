from unittest import mock

from django.test import TestCase

from candidatos.models import Candidato
from core import geo
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import GeoCache


def candidato(**extra):
    base = dict(nome="Maria Souza", cpf=cpf_valido(1), telefone=telefone_ficticio(1), cep="02166000")
    return Candidato.objects.create(**{**base, **extra})


class EnderecoCoerenteTests(TestCase):
    def cache(self):
        return GeoCache(cep="02166000", logradouro="Rua Exemplo", bairro="Vila Guilherme", cidade="São Paulo", uf="SP",
                        latitude=-23.51, longitude=-46.60, precisao="ENDERECO", fonte="teste")

    def test_endereco_digitado_em_caixa_baixa_ou_alta_vira_nome_proprio(self):
        c = candidato(bairro="guadalupe", cidade="RIO DE JANEIRO", logradouro="avenida paulista")
        self.assertEqual((c.bairro, c.cidade, c.logradouro), ("Guadalupe", "Rio de Janeiro", "Avenida Paulista"))
        self.assertEqual(candidato(cpf=cpf_valido(2), cidade="São Paulo").cidade, "São Paulo")

    def test_cidade_diferente_da_do_cep_passa_a_ser_a_do_cep(self):
        c = candidato(bairro="Guadalupe", cidade="Rio de Janeiro", uf="RJ")
        with mock.patch("core.geo.consultar_cep", return_value=self.cache()):
            self.assertTrue(geo.aplicar_localizacao(c))
        c.refresh_from_db()
        self.assertEqual((c.bairro, c.cidade, c.uf), ("Vila Guilherme", "São Paulo", "SP"))
        self.assertEqual((c.latitude, c.geo_status), (-23.51, "ok"))

    def test_mesma_cidade_com_outra_grafia_nao_e_conflito(self):
        c = candidato(bairro="Vila Gui", cidade="sao paulo", uf="SP")
        with mock.patch("core.geo.consultar_cep", return_value=self.cache()):
            geo.aplicar_localizacao(c)
        c.refresh_from_db()
        self.assertEqual(c.bairro, "Vila Gui")  # o que a pessoa digitou fica

    def test_campos_vazios_continuam_sendo_preenchidos(self):
        c = candidato()
        with mock.patch("core.geo.consultar_cep", return_value=self.cache()):
            geo.aplicar_localizacao(c)
        c.refresh_from_db()
        self.assertEqual((c.logradouro, c.bairro, c.cidade, c.uf), ("Rua Exemplo", "Vila Guilherme", "São Paulo", "SP"))
