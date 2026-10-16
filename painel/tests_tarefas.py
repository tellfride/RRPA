from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(RRPA_DEMO=True, RRPA_DEMO_SENHA="teste-demo-123")
class ParaFazerAgoraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def abrir(self, login, **params):
        self.client.logout()
        self.assertTrue(self.client.login(username=login, password=settings.RRPA_DEMO_SENHA))
        return self.client.get(reverse("painel:inicio"), params)

    def test_admin_ve_as_tarefas_em_ordem_de_urgencia(self):
        pagina = self.abrir("admin")
        self.assertContains(pagina, "Para fazer agora")
        titulos = [t["titulo"] for t in pagina.context["tarefas"]]
        self.assertTrue(titulos[0].startswith("Prazo"), titulos)
        self.assertIn("Candidatos aguardando aprovação", titulos)

    def test_gestor_nao_recebe_tarefas_de_recrutamento(self):
        titulos = " ".join(t["titulo"] for t in self.abrir("gestor.farmacia").context["tarefas"])
        self.assertNotIn("Candidato", titulos)
        self.assertNotIn("aprovado", titulos)

    def test_visualizador_nao_recebe_checkin(self):
        titulos = " ".join(t["titulo"] for t in self.abrir("visualizador").context["tarefas"])
        self.assertNotIn("Check-in", titulos)

    def test_analises_ficam_na_outra_aba(self):
        self.assertNotContains(self.abrir("admin"), "Quem trouxe candidatos")
        analises = self.abrir("admin", aba="analises")
        self.assertContains(analises, "Quem trouxe candidatos")
        self.assertContains(analises, "Prazos por mês")

    def test_modo_apresentacao_nao_mostra_tarefas(self):
        self.assertNotContains(self.abrir("admin", tv="1"), "Para fazer agora")
