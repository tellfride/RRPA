from datetime import date
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core import geo
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import GeoCache
from core.utils import (
    DATA_EXATA, abreviar_nome, calcular_periodo, formatar_cep, formatar_cpf, formatar_telefone, iniciais,
    limpar_nome, mascarar_cpf, meses_do_periodo, normalizar_cep, normalizar_cpf, normalizar_telefone,
    somar_meses, telefone_whatsapp, texto_prazo, validar_cep, validar_cpf, validar_telefone,
)


class DocumentosTests(SimpleTestCase):
    def test_cpf_valido_e_invalido(self):
        self.assertTrue(validar_cpf("529.982.247-25"))
        self.assertTrue(validar_cpf("52998224725"))
        self.assertFalse(validar_cpf("529.982.247-24"))
        self.assertFalse(validar_cpf("111.111.111-11"))
        self.assertFalse(validar_cpf("1234"))
        self.assertFalse(validar_cpf(""))

    def test_cpf_gerado_para_testes_e_valido(self):
        for n in (0, 1, 7, 123456789, 999999999, 4242):
            self.assertTrue(validar_cpf(cpf_valido(n)), n)

    def test_cpf_recupera_zeros_perdidos_pela_planilha(self):
        self.assertEqual(normalizar_cpf("1234567890"), "01234567890")
        self.assertEqual(formatar_cpf("52998224725"), "529.982.247-25")
        self.assertEqual(mascarar_cpf("52998224725"), "***.982.247-**")

    def test_telefone(self):
        self.assertEqual(normalizar_telefone("+55 (11) 98888-7777"), "11988887777")
        self.assertEqual(normalizar_telefone("011988887777"), "11988887777")
        self.assertTrue(validar_telefone("11988887777"))
        self.assertTrue(validar_telefone("1133334444"))
        self.assertFalse(validar_telefone("11888887777"))  # celular precisa começar com 9
        self.assertFalse(validar_telefone("0988887777"))
        self.assertFalse(validar_telefone("123"))
        self.assertEqual(formatar_telefone("11988887777"), "(11) 98888-7777")
        self.assertEqual(telefone_whatsapp("(11) 98888-7777"), "5511988887777")
        self.assertTrue(validar_telefone(telefone_ficticio(5)))

    def test_cep(self):
        self.assertEqual(normalizar_cep("1310100"), "01310100")
        self.assertEqual(formatar_cep("01310100"), "01310-100")
        self.assertTrue(validar_cep("01310-100"))
        self.assertFalse(validar_cep("00000-000"))
        self.assertFalse(validar_cep("123"))

    def test_nomes(self):
        self.assertEqual(limpar_nome("  MARIA   DA SILVA "), "Maria da Silva")
        self.assertEqual(limpar_nome("joão dos santos"), "João dos Santos")
        self.assertEqual(limpar_nome("Ana de Souza"), "Ana de Souza")
        self.assertEqual(iniciais("Maria Souza Lima"), "ML")
        self.assertEqual(abreviar_nome("Maria Souza Lima"), "Maria L.")


class SegurancaTests(SimpleTestCase):
    def test_destino_de_retorno_so_aceita_caminhos_do_proprio_sistema(self):
        from core.utils import destino_seguro

        self.assertEqual(destino_seguro("/candidatos/?q=a", "/x/"), "/candidatos/?q=a")
        for perigoso in ("//outro-site.com", "/\\outro-site.com", "https://outro-site.com", "javascript:alert(1)", "", None, "/ok\r\nSet-Cookie: a=b"):
            self.assertEqual(destino_seguro(perigoso, "/x/"), "/x/", perigoso)

    def test_texto_digitado_por_terceiros_nao_vira_formula_na_planilha(self):
        from core.utils import texto_de_planilha

        self.assertEqual(texto_de_planilha('=HYPERLINK("http://x")'), "'=HYPERLINK(\"http://x\")")
        self.assertEqual(texto_de_planilha("@SUM(1)"), "'@SUM(1)")
        self.assertEqual(texto_de_planilha("Maria"), "Maria")
        self.assertEqual(texto_de_planilha(12), 12)


class PrazoTests(SimpleTestCase):
    def test_mes_calendario_conta_o_mes_da_chamada(self):
        self.assertEqual(calcular_periodo(date(2026, 10, 20), 3), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(calcular_periodo(date(2026, 10, 1), 3), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(calcular_periodo(date(2026, 11, 30), 3), (date(2026, 11, 1), date(2027, 1, 31)))
        self.assertEqual(calcular_periodo(date(2026, 12, 5), 3), (date(2026, 12, 1), date(2027, 2, 28)))

    def test_data_exata(self):
        self.assertEqual(calcular_periodo(date(2026, 10, 20), 3, DATA_EXATA), (date(2026, 10, 20), date(2027, 1, 19)))
        self.assertEqual(calcular_periodo(date(2026, 11, 30), 3, DATA_EXATA)[1], date(2027, 2, 28))
        self.assertEqual(calcular_periodo(date(2026, 1, 31), 3, DATA_EXATA)[1], date(2026, 4, 30))

    def test_prazo_configuravel(self):
        self.assertEqual(calcular_periodo(date(2026, 10, 20), 1)[1], date(2026, 10, 31))
        self.assertEqual(calcular_periodo(date(2026, 10, 20), 6)[1], date(2027, 3, 31))

    def test_meses_do_periodo(self):
        self.assertEqual(
            meses_do_periodo(date(2026, 11, 1), date(2027, 1, 31)),
            [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)],
        )
        self.assertEqual(somar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))

    def test_texto_prazo(self):
        self.assertEqual(texto_prazo(12), "faltam 12 dias")
        self.assertEqual(texto_prazo(1), "falta 1 dia")
        self.assertEqual(texto_prazo(0), "termina hoje")
        self.assertEqual(texto_prazo(-3), "terminou há 3 dias")


class GeoTests(TestCase):
    def test_distancia(self):
        # Av. Paulista -> Praça da Sé, cerca de 2,5 km em linha reta
        km = geo.haversine_km(-23.5614, -46.6559, -23.5505, -46.6333)
        self.assertAlmostEqual(km, 2.5, delta=0.4)
        self.assertEqual(geo.haversine_km(-23.5, -46.6, -23.5, -46.6), 0)

    def _resposta(self, status, json):
        resposta = mock.Mock(status_code=status)
        resposta.json.return_value = json
        return resposta

    def test_cep_inexistente_fica_registrado_como_nao_encontrado(self):
        with mock.patch("core.geo.requests.get", return_value=self._resposta(404, None)):
            cache = geo.consultar_cep("99999999")
        self.assertFalse(cache.encontrado)

    def test_servico_fora_do_ar_nao_grava_cep_como_inexistente(self):
        import requests

        with mock.patch("core.geo.requests.get", side_effect=requests.ConnectionError("sem rede")):
            self.assertIsNone(geo.consultar_cep("01310100"))
        self.assertFalse(GeoCache.objects.filter(pk="01310100").exists())

    def test_somente_cache_nunca_acessa_a_rede(self):
        with mock.patch("core.geo.requests.get") as get:
            self.assertIsNone(geo.consultar_cep("01310100", somente_cache=True))
        get.assert_not_called()
