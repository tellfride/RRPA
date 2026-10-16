"""Localização por CEP: o ponto de cada CEP precisa cair no bairro dele.

Caso que motivou os testes: a BrasilAPI devolve o mesmo ponto (o Centro do Rio) para todos os CEPs do Rio de Janeiro,
e Guadalupe aparecia no centro da cidade, a ~20 km de onde fica.
"""
import importlib
from unittest import mock

import requests
from django.apps import apps
from django.test import TestCase

from candidatos.models import Candidato
from core import geo
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import GeoArea, GeoCache

CENTRO_DO_RIO = ("-22.90642", "-43.18223")  # o que a BrasilAPI manda para quase todo CEP do Rio
CAIXA_GUADALUPE = (-22.8507666, -22.8248894, -43.3879, -43.3604)  # sul, norte, oeste, leste (OpenStreetMap)
CAIXA_CENTRO_DO_RIO = (-22.9150, -22.8950, -43.2000, -43.1650)


def brasilapi(cep="21660000", rua="Avenida Brasil", bairro="Guadalupe", cidade="Rio de Janeiro", uf="RJ"):
    return {"cep": cep, "street": rua, "neighborhood": bairro, "city": cidade, "state": uf,
            "location": {"type": "Point", "coordinates": {"latitude": CENTRO_DO_RIO[0], "longitude": CENTRO_DO_RIO[1]}}}


def lugar_osm(nome="Guadalupe", cidade="Rio de Janeiro", uf="RJ", caixa=CAIXA_GUADALUPE, centro=(-22.8376208, -43.3751608),
              tipo="suburb"):
    sul, norte, oeste, leste = caixa
    endereco = {"city": cidade, "ISO3166-2-lvl4": f"BR-{uf}"}
    if tipo != "city":
        endereco[tipo] = nome
    return {"name": nome, "addresstype": tipo, "lat": str(centro[0]), "lon": str(centro[1]),
            "boundingbox": [str(sul), str(norte), str(oeste), str(leste)], "address": endereco}


CIDADE_DO_RIO_OSM = lugar_osm("Rio de Janeiro", caixa=(-23.0827051, -22.7460878, -43.7962520, -43.0990811),
                              centro=(-22.9110137, -43.2093727), tipo="city")


def resposta(status, json):
    r = mock.Mock(status_code=status)
    r.json.return_value = json
    return r


class RedeFalsa:
    """Substitui requests.get: responde por serviço (BrasilAPI, AwesomeAPI, Nominatim) e guarda as chamadas feitas."""

    def __init__(self, cep=None, awesome=None, areas=(), ruas=(), osm_fora=False, awesome_fora=False):
        self.cep, self.awesome, self.areas, self.ruas = cep, awesome, areas, ruas
        self.osm_fora, self.awesome_fora = osm_fora, awesome_fora
        self.chamadas = []

    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.chamadas.append((url, params))
        if "brasilapi" in url:
            return resposta(200, self.cep) if self.cep else resposta(404, None)
        if "viacep" in url:
            return resposta(404, None)
        if "awesomeapi" in url:
            if self.awesome_fora:
                raise requests.ConnectionError("AwesomeAPI fora do ar")
            return resposta(200, self.awesome) if self.awesome else resposta(404, None)
        if "nominatim" in url:
            if self.osm_fora:
                raise requests.ConnectionError("Nominatim fora do ar")
            if "street" in params:
                return resposta(200, list(self.ruas(params) if callable(self.ruas) else self.ruas))
            return resposta(200, list(self.areas(params["q"]) if callable(self.areas) else self.areas))
        raise AssertionError(f"chamada inesperada: {url}")

    def buscas_de_rua(self):
        return [p for url, p in self.chamadas if "nominatim" in url and "street" in p]

    def buscas_de_area(self):
        return [p for url, p in self.chamadas if "nominatim" in url and "street" not in p]


def consultar(cep, rede):
    with mock.patch("core.geo.requests.get", side_effect=rede), mock.patch("core.geo.time.sleep"):
        return geo.consultar_cep(cep)


class PontoDoCepTests(TestCase):
    def test_ponto_generico_da_brasilapi_nao_vira_a_posicao_do_cep(self):
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[lugar_osm()])
        cache = consultar("21660000", rede)
        self.assertAlmostEqual(cache.latitude, -22.8444177)  # e não o -22.90642 do Centro do Rio
        self.assertAlmostEqual(cache.longitude, -43.3758278)
        self.assertEqual((cache.fonte, cache.precisao), ("awesomeapi", "ENDERECO"))
        self.assertLess(geo.haversine_km(cache.latitude, cache.longitude, -22.8376, -43.3751), 3)

    def test_ponto_no_centro_do_rio_e_recusado_e_a_rua_vem_do_osm_dentro_do_bairro(self):
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": CENTRO_DO_RIO[0], "lng": CENTRO_DO_RIO[1]}, areas=[lugar_osm()],
                         ruas=[{"lat": "-22.8427273", "lon": "-43.3730178"}])
        cache = consultar("21660000", rede)
        self.assertEqual((cache.fonte, cache.precisao), ("nominatim", "ENDERECO"))
        self.assertAlmostEqual(cache.latitude, -22.8427273)
        (busca,) = rede.buscas_de_rua()
        self.assertEqual(busca["street"], "Avenida Brasil")
        self.assertEqual(busca["bounded"], 1)  # a avenida existe em dezenas de bairros: a busca fica presa a este
        oeste, norte, leste, sul = (float(x) for x in busca["viewbox"].split(","))
        self.assertTrue(sul < -22.8376 < norte and oeste < -43.3751 < leste)

    def test_rua_que_o_osm_nao_conhece_fica_no_centro_do_bairro(self):
        rede = RedeFalsa(cep=brasilapi(), areas=[lugar_osm()], ruas=[])
        cache = consultar("21660000", rede)
        self.assertEqual((cache.fonte, cache.precisao), ("nominatim", "BAIRRO"))
        self.assertAlmostEqual(cache.latitude, -22.8376208)
        self.assertAlmostEqual(cache.longitude, -43.3751608)

    def test_rua_e_procurada_primeiro_dentro_do_bairro_e_so_depois_com_folga(self):
        """Na borda, a mesma avenida passa para o bairro vizinho: o trecho certo é o de dentro dos limites."""
        rede = RedeFalsa(cep=brasilapi(), areas=[lugar_osm()], ruas=[])
        consultar("21660000", rede)
        estrita, com_folga = (tuple(float(x) for x in b["viewbox"].split(",")) for b in rede.buscas_de_rua())
        self.assertEqual(estrita, (CAIXA_GUADALUPE[2], CAIXA_GUADALUPE[1], CAIXA_GUADALUPE[3], CAIXA_GUADALUPE[0]))
        self.assertLess(com_folga[0], estrita[0])  # oeste mais a oeste
        self.assertGreater(com_folga[1], estrita[1])  # norte mais ao norte
        self.assertGreater(com_folga[2], estrita[2])  # leste mais a leste
        self.assertLess(com_folga[3], estrita[3])  # sul mais ao sul

    def test_rua_logo_alem_do_limite_do_bairro_ainda_e_aceita(self):
        sul, norte, oeste, leste = CAIXA_GUADALUPE

        def ruas(params):  # só aparece na busca com folga, 300 m ao sul do limite
            return [] if params["viewbox"].startswith(f"{oeste},") else [{"lat": str(sul - 0.003), "lon": "-43.37"}]

        cache = consultar("21660000", RedeFalsa(cep=brasilapi(), areas=[lugar_osm()], ruas=ruas))
        self.assertEqual((cache.fonte, cache.precisao), ("nominatim", "ENDERECO"))
        self.assertAlmostEqual(cache.latitude, sul - 0.003)

    def test_rua_longe_do_bairro_nao_e_aceita(self):
        """Mesmo que o Nominatim devolva algo fora da caixa (busca sem 'bounded' efetivo), o ponto é conferido."""
        cache = consultar("21660000", RedeFalsa(cep=brasilapi(), areas=[lugar_osm()], ruas=[{"lat": CENTRO_DO_RIO[0], "lon": CENTRO_DO_RIO[1]}]))
        self.assertEqual(cache.precisao, "BAIRRO")  # caiu para o centro do bairro, e não para o Centro do Rio
        self.assertAlmostEqual(cache.latitude, -22.8376208)

    def test_cep_sem_rua_nao_tem_precisao_de_rua(self):
        rede = RedeFalsa(cep=brasilapi(rua=""), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[lugar_osm()])
        cache = consultar("21660000", rede)
        self.assertEqual(cache.precisao, "BAIRRO")
        self.assertEqual(rede.buscas_de_rua(), [])

    def test_bairro_homonimo_de_outra_cidade_e_ignorado(self):
        """'Centro' existe em quase toda cidade: o do Rio não pode ser trocado pelo de Nova Friburgo."""
        nova_friburgo = lugar_osm("Centro", "Nova Friburgo", caixa=(-22.30, -22.26, -42.55, -42.50), centro=(-22.28, -42.53))
        centro_rio = lugar_osm("Centro", caixa=CAIXA_CENTRO_DO_RIO, centro=(-22.905, -43.18))
        rede = RedeFalsa(cep=brasilapi("20030020", "Avenida Presidente Wilson", "Centro"),
                         awesome={"lat": "-22.9114872", "lng": "-43.1733924"}, areas=[nova_friburgo, centro_rio])
        cache = consultar("20030020", rede)
        self.assertEqual(cache.fonte, "awesomeapi")  # só passa na conferência se a caixa escolhida foi a do Rio
        area = GeoArea.objects.get(nome="Centro")
        self.assertEqual((area.cidade, area.latitude), ("Rio de Janeiro", -22.905))

    def test_homonimo_de_outro_estado_e_ignorado(self):
        outro_estado = lugar_osm("Guadalupe", "Rio de Janeiro", uf="PI", caixa=(-6.9, -6.7, -43.7, -43.5), centro=(-6.8, -43.6))
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[outro_estado])
        consultar("21660000", rede)
        self.assertFalse(GeoArea.objects.get(nome="Guadalupe").encontrado)

    def test_sem_o_bairro_no_osm_vale_a_caixa_da_cidade(self):
        def areas(q):
            return [] if q.startswith("Guadalupe") else [CIDADE_DO_RIO_OSM]

        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=areas)
        cache = consultar("21660000", rede)
        self.assertEqual(cache.fonte, "awesomeapi")
        self.assertFalse(GeoArea.objects.get(tipo="BAIRRO").encontrado)
        self.assertTrue(GeoArea.objects.get(tipo="CIDADE").encontrado)

    def test_ponto_fora_da_cidade_e_recusado_mesmo_sem_o_bairro(self):
        def areas(q):
            return [] if q.startswith("Guadalupe") else [CIDADE_DO_RIO_OSM]

        sao_paulo = {"lat": "-23.5617698", "lng": "-46.6553299"}  # coordenada errada de outra cidade
        rede = RedeFalsa(cep=brasilapi(), awesome=sao_paulo, areas=areas, ruas=[{"lat": "-22.84", "lon": "-43.37"}])
        cache = consultar("21660000", rede)
        self.assertEqual((cache.fonte, round(cache.latitude, 2)), ("nominatim", -22.84))


class FalhasDeServicoTests(TestCase):
    def test_osm_fora_do_ar_devolve_o_endereco_mas_nao_grava_o_cep(self):
        cache = consultar("21660000", RedeFalsa(cep=brasilapi(), osm_fora=True))
        self.assertTrue(cache.provisorio)
        self.assertEqual((cache.bairro, cache.cidade, cache.uf), ("Guadalupe", "Rio de Janeiro", "RJ"))
        self.assertIsNone(cache.latitude)
        self.assertTrue(cache.encontrado)
        self.assertFalse(GeoCache.objects.exists())  # tentará de novo na próxima consulta

    def test_candidato_continua_pendente_enquanto_o_osm_esta_fora_do_ar(self):
        c = Candidato.objects.create(nome="Maria Souza", cpf=cpf_valido(1), telefone=telefone_ficticio(1), cep="21660000")
        with mock.patch("core.geo.requests.get", side_effect=RedeFalsa(cep=brasilapi(), osm_fora=True)), mock.patch("core.geo.time.sleep"):
            self.assertFalse(geo.aplicar_localizacao(c))
        c.refresh_from_db()
        self.assertEqual(c.geo_status, "pendente")
        self.assertIsNone(c.latitude)

    def test_awesomeapi_fora_do_ar_nao_impede_a_localizacao(self):
        rede = RedeFalsa(cep=brasilapi(), areas=[lugar_osm()], ruas=[{"lat": "-22.8427273", "lon": "-43.3730178"}], awesome_fora=True)
        cache = consultar("21660000", rede)
        self.assertEqual((cache.fonte, cache.precisao), ("nominatim", "ENDERECO"))

    def test_awesomeapi_com_ponto_absurdo_e_ignorada(self):
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "0", "lng": "0"}, areas=[lugar_osm()],
                         ruas=[{"lat": "-22.8427273", "lon": "-43.3730178"}])
        self.assertEqual(consultar("21660000", rede).fonte, "nominatim")


class CacheTests(TestCase):
    def test_resultado_fica_em_cache_e_nao_repete_consulta(self):
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[lugar_osm()])
        primeiro = consultar("21660-000", rede)
        antes = len(rede.chamadas)
        segundo = consultar("21660000", rede)
        self.assertEqual(len(rede.chamadas), antes)
        self.assertEqual((primeiro.latitude, primeiro.longitude), (segundo.latitude, segundo.longitude))

    def test_limites_do_bairro_sao_consultados_uma_vez_so(self):
        consultar("21660000", RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[lugar_osm()]))
        outro_cep = RedeFalsa(cep=brasilapi("21670900"), awesome={"lat": "-22.841209", "lng": "-43.3690732"}, areas=[lugar_osm()])
        cache = consultar("21670900", outro_cep)
        self.assertEqual(outro_cep.buscas_de_area(), [])  # o bairro já estava em GeoArea
        self.assertAlmostEqual(cache.latitude, -22.841209)

    def test_bairro_desconhecido_nao_e_perguntado_de_novo_a_cada_cep(self):
        def areas(q):
            return [] if q.startswith("Guadalupe") else [CIDADE_DO_RIO_OSM]

        consultar("21660000", RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=areas))
        outro_cep = RedeFalsa(cep=brasilapi("21670900"), awesome={"lat": "-22.841209", "lng": "-43.3690732"}, areas=areas)
        consultar("21670900", outro_cep)
        self.assertEqual(outro_cep.buscas_de_area(), [])


class LocalizarCandidatoTests(TestCase):
    def test_candidato_de_guadalupe_nao_vai_para_o_centro_do_rio(self):
        c = Candidato.objects.create(nome="Tell1 guadalupe", cpf=cpf_valido(1), telefone=telefone_ficticio(1), cep="21660000")
        rede = RedeFalsa(cep=brasilapi(), awesome={"lat": "-22.8444177", "lng": "-43.3758278"}, areas=[lugar_osm()])
        with mock.patch("core.geo.requests.get", side_effect=rede), mock.patch("core.geo.time.sleep"):
            self.assertTrue(geo.aplicar_localizacao(c))
        c.refresh_from_db()
        self.assertEqual((c.bairro, c.geo_status, c.precisao_geo), ("Guadalupe", "ok", "ENDERECO"))
        self.assertGreater(geo.haversine_km(c.latitude, c.longitude, float(CENTRO_DO_RIO[0]), float(CENTRO_DO_RIO[1])), 15)


class MigracaoDosPontosAntigosTests(TestCase):
    def test_cache_antigo_e_descartado_e_o_candidato_volta_para_a_fila(self):
        migracao = importlib.import_module("core.migrations.0002_geoarea")
        ponto = dict(latitude=float(CENTRO_DO_RIO[0]), longitude=float(CENTRO_DO_RIO[1]))
        GeoCache.objects.create(cep="21660000", bairro="Guadalupe", cidade="Rio de Janeiro", uf="RJ", fonte="brasilapi",
                                precisao="ENDERECO", **ponto)
        GeoCache.objects.create(cep="02166000", bairro="Vila Guilherme", cidade="São Paulo", uf="SP", fonte="demo",
                                precisao="ENDERECO", **ponto)
        GeoCache.objects.create(cep="01310100", bairro="Bela Vista", cidade="São Paulo", uf="SP", fonte="awesomeapi",
                                precisao="ENDERECO", **ponto)
        gravado = dict(geo_status="ok", precisao_geo="ENDERECO", **ponto)
        antigo = Candidato.objects.create(nome="Ana", cpf=cpf_valido(1), telefone=telefone_ficticio(1), cep="21660000", **gravado)
        demo = Candidato.objects.create(nome="Bia", cpf=cpf_valido(2), telefone=telefone_ficticio(2), cep="02166000", **gravado)
        novo = Candidato.objects.create(nome="Cris", cpf=cpf_valido(3), telefone=telefone_ficticio(3), cep="01310100", **gravado)

        migracao.refazer_localizacoes_antigas(apps, None)

        self.assertEqual(sorted(GeoCache.objects.values_list("cep", flat=True)), ["01310100", "02166000"])
        for candidato, esperado in ((antigo, "pendente"), (demo, "ok"), (novo, "ok")):
            candidato.refresh_from_db()
            self.assertEqual(candidato.geo_status, esperado, candidato.nome)
