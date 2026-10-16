"""O mapa mostra também quem se candidatou à vaga (e não só quem já está nela), com nome e vaga para a equipe."""
from django.test import TestCase
from django.urls import reverse

from candidatos.models import Candidato
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao
from vagas.models import Vaga


def candidato(nome, n, status, vaga=None, bairro="Mooca", lat=-23.55, lng=-46.63):
    pessoa = Candidato.objects.create(
        nome=nome, cpf=cpf_valido(800 + n), telefone=telefone_ficticio(n), cep="03103000", bairro=bairro, cidade="São Paulo", uf="SP",
        status=status, latitude=lat, longitude=lng, precisao_geo="ENDERECO", geo_status="ok",
    )
    if vaga is not None:
        pessoa.vagas_interesse.add(vaga)
    return pessoa


class MapaDeCandidatosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Configuracao.atual()
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")
        cls.gestor = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.farmacia = Vaga.objects.create(nome="Farmácia", quantidade=2)
        cls.rouparia = Vaga.objects.create(nome="Rouparia", quantidade=2)
        cls.farmacia.responsaveis.add(cls.gestor)
        cls.ana = candidato("Ana Lima", 1, "pendente", cls.farmacia)  # só se candidatou
        cls.bia = candidato("Bia Prado", 2, "aprovado", cls.farmacia, bairro="Santana", lat=-23.50, lng=-46.60)
        cls.caio = candidato("Caio Reis", 3, "aprovado", cls.rouparia)
        cls.davi = candidato("Davi Melo", 4, "desistente", cls.farmacia)  # fora da situação padrão

    def dados(self, **params):
        return self.client.get(reverse("mapa:dados"), params).json()

    def test_candidatos_a_vaga_aparecem_no_mapa_por_padrao(self):
        self.client.force_login(self.rec)
        carga = self.dados()
        self.assertEqual(carga["totals"]["candidatos"], 3)  # pendente + 2 aprovados (desistente fica de fora)
        self.assertEqual(carga["totals"]["with_geo"], 3)

    def test_filtro_por_vaga_traz_quem_se_candidatou_a_ela(self):
        self.client.force_login(self.rec)
        self.assertEqual(self.dados(vaga=self.farmacia.pk)["totals"]["candidatos"], 2)
        self.assertEqual(self.dados(vaga=self.rouparia.pk)["totals"]["candidatos"], 1)

    def test_equipe_busca_por_nome_e_por_vaga(self):
        self.client.force_login(self.rec)
        self.assertEqual(self.dados(q="Bia")["totals"]["candidatos"], 1)
        self.assertEqual(self.dados(q="farmácia")["totals"]["candidatos"], 2)
        self.assertEqual(self.dados(q="Mooca")["totals"]["candidatos"], 2)

    def test_lista_do_bairro_traz_nome_situacao_e_vaga(self):
        self.client.force_login(self.rec)
        r = self.client.get(reverse("mapa:candidatos"), {"bairro": "Mooca", "cidade": "São Paulo", "status": "pendente,aprovado"})
        corpo = r.json()
        self.assertEqual(corpo["total"], 2)
        ana = next(c for c in corpo["candidatos"] if c["nome"] == "Ana Lima")
        self.assertEqual((ana["situacao"], ana["vagas"], ana["na_vaga"]), ("Aguardando aprovação", ["Farmácia"], ""))
        self.assertEqual(ana["url"], reverse("candidatos:detalhe", args=[self.ana.pk]))
        self.assertNotIn("cpf", ana)

    def test_ponto_unico_e_parametros_obrigatorios(self):
        self.client.force_login(self.rec)
        um = self.client.get(reverse("mapa:candidatos"), {"id": self.bia.pk}).json()
        self.assertEqual([c["nome"] for c in um["candidatos"]], ["Bia Prado"])
        self.assertEqual(self.client.get(reverse("mapa:candidatos")).status_code, 400)

    def test_nomes_so_para_a_equipe_de_recrutamento(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("mapa:candidatos"), {"id": self.ana.pk}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("mapa:candidatos"), {"id": self.ana.pk}).status_code, 302)

    def test_gestor_ve_so_os_candidatos_as_vagas_dele_e_sem_buscar_por_nome(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.dados()["totals"]["candidatos"], 2)  # Ana e Bia (Farmácia); Caio quer Rouparia
        self.assertEqual(self.dados(q="Lima")["totals"]["candidatos"], 0)  # busca por nome é só da equipe
        self.assertTrue(all(p["id"] is None for p in self.dados()["points"]))

    def test_busca_de_telefone_na_lista_aceita_numero_colado_com_mais_55(self):
        self.client.force_login(self.rec)
        telefone = Candidato.objects.get(pk=self.ana.pk).telefone
        pagina = self.client.get(reverse("candidatos:lista"), {"q": f"+55 {telefone}"})
        self.assertContains(pagina, "Ana Lima")
