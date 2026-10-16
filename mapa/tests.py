from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from candidatos.models import Candidato
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao
from mapa.dados import agregar, amostrar, coordenadas_validas
from mapa.escala import RAMPA, construir_escala, cor_para, raio_para
from vagas.models import Vaga


class EscalaTests(SimpleTestCase):
    def test_faixas_sao_continuas_e_cobrem_de_1_ate_o_maximo(self):
        for maximo in (1, 2, 5, 14, 100, 1000):
            faixas = construir_escala(maximo)
            self.assertEqual(faixas[0]["min"], 1)
            self.assertEqual(faixas[-1]["max"], maximo)
            for anterior, atual in zip(faixas, faixas[1:]):
                self.assertEqual(atual["min"], anterior["max"] + 1)
            self.assertLessEqual(len(faixas), len(RAMPA))
        self.assertEqual(construir_escala(0), [])
        self.assertEqual(construir_escala(None), [])

    def test_cor_e_raio(self):
        faixas = construir_escala(50)
        self.assertEqual(cor_para(1, faixas), RAMPA[0])
        self.assertEqual(cor_para(50, faixas), RAMPA[-1])
        self.assertLess(raio_para(1, 50), raio_para(50, 50))
        self.assertEqual(raio_para(0, 50), raio_para(1, 1000))  # sempre há um piso clicável


class AgregarTests(SimpleTestCase):
    def linhas(self):
        base = {"precisao": "ENDERECO", "uf": "SP", "status": "aprovado", "whatsapp_ok": True, "indicado_por_id": 1}
        return [
            {**base, "id": 1, "latitude": -23.55, "longitude": -46.63, "bairro": "Mooca", "cidade": "São Paulo"},
            {**base, "id": 2, "latitude": -23.56, "longitude": -46.64, "bairro": " Mooca ", "cidade": "São Paulo", "whatsapp_ok": False},
            {**base, "id": 3, "latitude": -23.50, "longitude": -46.60, "bairro": "Santana", "cidade": "São Paulo", "precisao": "BAIRRO", "status": "em_vaga"},
            {**base, "id": 4, "latitude": None, "longitude": None, "bairro": "Penha", "cidade": "São Paulo"},
        ]

    def test_totais_bairros_e_privacidade(self):
        dados = agregar(self.linhas(), {1: "Marina Lopes"}, pode_ver_candidatos=False)
        self.assertEqual(dados["totals"], {"candidatos": 4, "with_geo": 3, "without_geo": 1, "approximate": 1})
        mooca = dados["neighborhoods"][0]
        self.assertEqual((mooca["name"], mooca["count"], mooca["whatsapp"]), ("Mooca", 2, 1))
        self.assertEqual(mooca["top_indicadores"], [{"name": "Marina Lopes", "count": 2}])
        self.assertEqual([b["name"] for b in dados["neighborhoods_without_geo"]], ["Penha"])
        self.assertTrue(all(p["id"] is None for p in dados["points"]))  # sem permissão: nenhum id de candidato
        self.assertNotIn("nome", str(dados["points"]))
        self.assertEqual(dados["bounds"], [[-23.56, -46.64], [-23.5, -46.6]])

    def test_quem_pode_ver_recebe_os_ids(self):
        dados = agregar(self.linhas(), {}, pode_ver_candidatos=True)
        self.assertEqual(sorted(p["id"] for p in dados["points"]), [1, 2, 3])

    def test_amostra_e_coordenadas(self):
        self.assertEqual(len(amostrar(list(range(100)), 10)), 10)
        self.assertEqual(amostrar([1, 2], 10), [1, 2])
        self.assertFalse(coordenadas_validas(0, 0))
        self.assertFalse(coordenadas_validas(None, 1))
        self.assertFalse(coordenadas_validas(91, 0))
        self.assertTrue(coordenadas_validas("-23.5", "-46.6"))


class MapaViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        config = Configuracao.atual()
        config.latitude, config.longitude = -23.5614, -46.6559
        config.save()
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")
        cls.vis = Usuario.objects.create_user("vis", password="x", perfil="visualizador")
        cls.ges = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.vaga = Vaga.objects.create(nome="Farmácia", quantidade=3)
        cls.vaga.responsaveis.add(cls.ges)
        for n, (bairro, lat, lng) in enumerate([("Bela Vista", -23.555, -46.647), ("Bela Vista", -23.556, -46.648), ("Itaquera", -23.545, -46.457)]):
            c = Candidato.objects.create(nome=f"Pessoa Numero{n}", cpf=cpf_valido(n + 1), telefone=telefone_ficticio(n), cep="01310100",
                                         bairro=bairro, cidade="São Paulo", uf="SP", latitude=lat, longitude=lng,
                                         precisao_geo="ENDERECO", geo_status="ok", status="aprovado")
            c.vagas_interesse.add(cls.vaga)
        Candidato.objects.create(nome="Sem Localizacao", cpf=cpf_valido(50), telefone=telefone_ficticio(50), cep="01310100", status="pendente")

    def dados(self, usuario, **params):
        self.client.force_login(usuario)
        return self.client.get(reverse("mapa:dados"), params).json()

    def test_recrutador_ve_totais_e_pode_abrir_cadastros(self):
        d = self.dados(self.rec)
        self.assertEqual((d["totals"]["candidatos"], d["totals"]["with_geo"], d["totals"]["without_geo"]), (4, 3, 1))
        self.assertTrue(d["can_view_candidates"])
        self.assertEqual(d["neighborhoods"][0]["name"], "Bela Vista")
        self.assertEqual(d["unidade"]["lat"], -23.5614)

    def test_visualizador_nao_recebe_ids_nem_nomes(self):
        d = self.dados(self.vis)
        self.assertFalse(d["can_view_candidates"])
        self.assertTrue(all(p["id"] is None for p in d["points"]))
        self.assertNotIn("Pessoa", str(d))

    def test_responsavel_do_setor_so_ve_quem_esta_na_sua_vaga_ou_se_candidatou_a_ela(self):
        # os aprovados do cenário se candidataram à vaga que ele acompanha; candidatos de outras vagas continuam invisíveis
        self.assertEqual(self.dados(self.ges, status="aprovado")["totals"]["candidatos"], 3)

    def test_filtros_de_distancia_e_busca(self):
        self.assertEqual(self.dados(self.rec, ate_km="5")["totals"]["with_geo"], 2)  # Itaquera fica a ~20 km
        self.assertEqual(self.dados(self.rec, q="itaq")["totals"]["with_geo"], 1)
        self.assertEqual(self.dados(self.rec, vaga=self.vaga.pk, status="aprovado")["totals"]["candidatos"], 3)

    def test_so_a_equipe_localiza_e_so_o_admin_relocaliza(self):
        self.client.force_login(self.vis)
        self.assertEqual(self.client.post(reverse("mapa:geocodificar")).status_code, 403)
        self.assertEqual(self.client.post(reverse("mapa:relocalizar")).status_code, 403)
        self.client.force_login(self.rec)
        self.assertEqual(self.client.post(reverse("mapa:relocalizar")).status_code, 403)
