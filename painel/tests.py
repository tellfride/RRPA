from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from candidatos.models import Candidato
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao
from core.utils import somar_meses
from painel import services
from vagas.models import Vaga
from vinculos import services as vs
from vinculos.models import Vinculo


class PainelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.config = Configuracao.atual()
        cls.hoje = timezone.localdate()
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")
        cls.ges = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.ges2 = Usuario.objects.create_user("ges2", password="x", perfil="gestor")
        cls.vis = Usuario.objects.create_user("vis", password="x", perfil="visualizador")
        cls.farmacia = Vaga.objects.create(nome="Farmácia", quantidade=4)
        cls.farmacia.responsaveis.add(cls.ges)
        cls.rouparia = Vaga.objects.create(nome="Rouparia", quantidade=2)
        cls.rouparia.responsaveis.add(cls.ges2)
        cls.contador = 0

    def pessoa(self, nome, status="aprovado"):
        PainelTests.contador += 1
        return Candidato.objects.create(nome=nome, cpf=cpf_valido(PainelTests.contador), telefone=telefone_ficticio(PainelTests.contador),
                                        cep="01310100", status=status)

    def cenario(self):
        """Farmácia: 2 no prazo (um vencendo), 1 vencido. Rouparia: 1 no prazo."""
        a = vs.chamar_para_vaga(self.pessoa("Ana Primeira"), self.farmacia, self.rec)
        b = vs.chamar_para_vaga(self.pessoa("Bia Segunda"), self.farmacia, self.rec)
        Vinculo.objects.filter(pk=a.pk).update(data_fim=self.hoje + timedelta(days=5))  # vencendo
        c = Vinculo.objects.create(candidato=self.pessoa("Caio Terceiro", "em_vaga"), vaga=self.farmacia, data_chamada=self.hoje - timedelta(days=120),
                                   inicio_periodo=self.hoje - timedelta(days=120), data_fim=self.hoje - timedelta(days=3))
        vs.chamar_para_vaga(self.pessoa("Davi Quarto"), self.rouparia, self.rec)
        return a, b, c

    def test_numeros_do_quadro(self):
        self.cenario()
        dados = services.painel_completo(self.rec, self.config, self.hoje)
        t = dados["totais"]
        self.assertEqual((t["quantidade"], t["ocupadas"], t["livres"], t["vencendo"], t["vencidos"]), (6, 3, 3, 1, 1))
        farmacia = next(l for l in dados["quadro"] if l["vaga"] == self.farmacia)
        self.assertEqual([a["tipo"] for a in farmacia["assentos"]], ["ocupada", "vencendo", "livre", "livre", "separador", "vencido"])
        self.assertEqual((farmacia["n_ocupadas"], farmacia["livres"], farmacia["pct"]), (2, 2, 50))

    def test_vaga_inativa_some_do_painel_mas_aparece_na_lista_do_admin(self):
        Vaga.objects.filter(pk=self.rouparia.pk).update(ativa=False)
        self.assertEqual(len(services.painel_completo(self.rec, self.config, self.hoje)["quadro"]), 1)
        self.assertEqual(len(services.montar_quadro(self.rec, self.config, self.hoje, incluir_inativas=True)[0]), 2)

    def test_prazos_por_mes_e_proximos_vencimentos(self):
        self.cenario()
        dados = services.painel_completo(self.rec, self.config, self.hoje)
        self.assertEqual(dados["meses"][0]["rotulo"], "Vencidos")
        self.assertEqual(dados["meses"][0]["total"], 1)
        self.assertEqual(sum(m["total"] for m in dados["meses"][1:]), 3)
        self.assertEqual(dados["proximos"][0].candidato.nome, "Caio Terceiro")  # o vencido vem primeiro
        self.assertEqual(len(dados["meses"]), 1 + self.config.prazo_meses + 1)

    def test_gestor_so_ve_as_suas_vagas(self):
        self.cenario()
        dados = services.painel_completo(self.ges, self.config, self.hoje)
        self.assertEqual([l["vaga"] for l in dados["quadro"]], [self.farmacia])
        self.assertIsNone(dados["funil"])  # gestor não vê o funil de candidatos
        outro = services.painel_completo(self.ges2, self.config, self.hoje)
        self.assertEqual(outro["totais"]["ocupadas"], 1)

    def test_pendencias_de_checkin_aparecem_no_resumo(self):
        self.cenario()
        dados = services.painel_completo(self.rec, self.config, self.hoje)
        self.assertGreater(dados["pendencias"]["total"], 0)

    def test_funil_conta_candidatos_por_situacao(self):
        self.pessoa("Eva Pendente", "pendente")
        self.pessoa("Fabio Aprovado", "aprovado")
        funil, fora = services.funil_candidatos()
        por_status = {e["status"]: e["total"] for e in funil}
        self.assertEqual((por_status["pendente"], por_status["aprovado"], fora), (1, 1, 0))

    def test_gaveta_lista_quem_ocupa_e_quem_pode_ser_chamado(self):
        self.cenario()
        extra = self.pessoa("Gil Aprovado")
        extra.vagas_interesse.add(self.farmacia)
        ocupantes = services.lista_detalhe(self.rec, self.config, {"tipo": "ocupantes", "vaga": self.farmacia.pk})
        self.assertEqual(sorted(v.candidato.nome for v in ocupantes["itens"]), ["Ana Primeira", "Bia Segunda"])
        livres = services.lista_detalhe(self.rec, self.config, {"tipo": "disponiveis", "vaga": self.farmacia.pk})
        self.assertEqual([c.nome for c in livres["itens"]], ["Gil Aprovado"])
        self.assertEqual(livres["vaga_chamar"], self.farmacia)
        vencidos = services.lista_detalhe(self.rec, self.config, {"tipo": "vencidos"})
        self.assertEqual([v.candidato.nome for v in vencidos["itens"]], ["Caio Terceiro"])

    def test_visualizador_ve_nomes_abreviados(self):
        self.cenario()
        self.client.force_login(self.vis)
        pagina = self.client.get(reverse("painel:detalhe"), {"tipo": "ocupantes", "vaga": self.farmacia.pk, "gaveta": "1"})
        self.assertContains(pagina, "Ana P.")
        self.assertNotContains(pagina, "Ana Primeira")
        quadro = self.client.get(reverse("painel:inicio"))
        self.assertNotContains(quadro, "Davi Quarto")

    def test_gestor_nao_abre_gaveta_de_candidatos(self):
        self.client.force_login(self.ges)
        pagina = self.client.get(reverse("painel:detalhe"), {"tipo": "status", "status": "aprovado", "gaveta": "1"})
        self.assertNotContains(pagina, "Aprovado")  # cai no estado vazio padrão

    def test_painel_compartilhavel_mostra_numeros_sem_nomes(self):
        self.cenario()
        self.assertEqual(self.client.get(reverse("painel:publico", args=["qualquer"])).status_code, 404)  # desativado por padrão
        Configuracao.objects.filter(pk=1).update(painel_publico_token="token-de-teste")
        pagina = self.client.get(reverse("painel:publico", args=["token-de-teste"]))
        self.assertContains(pagina, "Farmácia")
        for nome in ("Ana Primeira", "Bia Segunda", "Caio Terceiro", "Davi Quarto"):
            self.assertNotContains(pagina, nome)
        self.assertEqual(self.client.get(reverse("painel:publico", args=["errado"])).status_code, 404)

    def test_modo_data_exata_muda_o_prazo_das_proximas_chamadas(self):
        Configuracao.objects.filter(pk=1).update(contagem_prazo="data_exata")
        v = vs.chamar_para_vaga(self.pessoa("Hugo Exato"), self.farmacia, self.rec)
        self.assertEqual(v.inicio_periodo, self.hoje)
        self.assertEqual(v.data_fim, somar_meses(self.hoje, 3) - timedelta(days=1) if self.hoje.day <= somar_meses(self.hoje, 3).day else somar_meses(self.hoje, 3))
