"""Percorre todas as telas com cada perfil: abre (200) onde pode e bloqueia (403/404) onde não pode."""
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from candidatos.models import Candidato
from vagas.models import Vaga
from vinculos.models import Vinculo

ADMIN, REC, GESTOR, VISUAL = "admin", "recrutadora", "gestor.farmacia", "visualizador"
TODOS = {ADMIN, REC, GESTOR, VISUAL}
EQUIPE = {ADMIN, REC}


@override_settings(RRPA_DEMO=True, RRPA_DEMO_SENHA="teste-demo-123", RRPA_WHATSAPP_API=True)
class TelasPorPerfilTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.vaga = Vaga.objects.get(nome="Farmácia")
        cls.candidato = Candidato.objects.filter(status="aprovado").first()
        cls.vinculo = Vinculo.objects.filter(vaga=cls.vaga, status="ativo").first()

    def acessos(self):
        v, c, vi = self.vaga.pk, self.candidato.pk, self.vinculo.pk
        g = "&gaveta=1"
        return [
            # (nome para exibir, url, perfis que devem abrir)
            ("painel", reverse("painel:inicio"), TODOS),
            ("painel TV", reverse("painel:inicio") + "?tv=1", TODOS),
            ("painel filtrado", reverse("painel:inicio") + f"?vaga={v}", TODOS),
            ("gaveta ocupantes", reverse("painel:detalhe") + f"?tipo=ocupantes&vaga={v}{g}", TODOS),
            ("gaveta vencendo", reverse("painel:detalhe") + f"?tipo=vencendo{g}", TODOS),
            ("gaveta vencidos", reverse("painel:detalhe") + f"?tipo=vencidos{g}", TODOS),
            ("gaveta disponiveis", reverse("painel:detalhe") + f"?tipo=disponiveis{g}", TODOS),
            ("gaveta disponiveis vaga", reverse("painel:detalhe") + f"?tipo=disponiveis&vaga={v}{g}", TODOS),
            ("gaveta checkins", reverse("painel:detalhe") + f"?tipo=checkins{g}", TODOS),
            ("gaveta mes", reverse("painel:detalhe") + f"?tipo=mes&mes=2026-12{g}", TODOS),
            ("gaveta status", reverse("painel:detalhe") + f"?tipo=status&status=aprovado{g}", TODOS),
            ("gaveta faixa", reverse("painel:detalhe") + f"?tipo=faixa&de=0&ate=5{g}", TODOS),
            ("gaveta territorio", reverse("painel:detalhe") + f"?tipo=territorio&bairro=Mooca&cidade=S%C3%A3o%20Paulo{g}", TODOS),
            ("gaveta como pagina", reverse("painel:detalhe") + f"?tipo=ocupantes&vaga={v}", TODOS),
            ("vagas", reverse("vagas:lista"), TODOS),
            ("vaga", reverse("vagas:detalhe", args=[v]), TODOS),
            ("vaga historico", reverse("vagas:detalhe", args=[v]) + "?aba=historico", TODOS),
            ("vaga candidatos", reverse("vagas:detalhe", args=[v]) + "?aba=candidatos", TODOS),
            ("vaga nova", reverse("vagas:nova"), {ADMIN}),
            ("vaga editar", reverse("vagas:editar", args=[v]), {ADMIN}),
            ("modelo xlsx", reverse("vagas:modelo", args=[v]), EQUIPE),
            ("exportar vaga", reverse("vagas:exportar", args=[v]), {ADMIN, REC, GESTOR}),
            ("candidatos", reverse("candidatos:lista"), EQUIPE),
            ("candidatos filtrados", reverse("candidatos:lista") + "?status=aprovado&q=a&ordem=nome", EQUIPE),
            ("candidato", reverse("candidatos:detalhe", args=[c]), EQUIPE),
            ("candidato novo", reverse("candidatos:novo"), EQUIPE),
            ("candidato editar", reverse("candidatos:editar", args=[c]), EQUIPE),
            ("importar", reverse("candidatos:importar"), EQUIPE),
            ("importar vaga", reverse("candidatos:importar_vaga", args=[v]), EQUIPE),
            ("importacoes", reverse("candidatos:importacoes"), EQUIPE),
            ("vinculos", reverse("vinculos:lista"), {ADMIN, REC, GESTOR}),
            ("vinculos vencendo", reverse("vinculos:lista") + "?situacao=vencendo", {ADMIN, REC, GESTOR}),
            ("vinculos vencidos", reverse("vinculos:lista") + "?situacao=vencido", {ADMIN, REC, GESTOR}),
            ("vinculos concluidos", reverse("vinculos:lista") + "?situacao=concluido", {ADMIN, REC, GESTOR}),
            ("vinculo", reverse("vinculos:detalhe", args=[vi]), {ADMIN, REC, GESTOR}),
            ("chamar", reverse("vinculos:chamar") + f"?candidato={c}&vaga={v}", EQUIPE),
            ("previa prazo", reverse("vinculos:previa_prazo") + "?data=2026-10-20", EQUIPE),
            ("checkins", reverse("checkins:lista"), {ADMIN, GESTOR}),
            ("checkins todos", reverse("checkins:lista") + "?aba=todos", {ADMIN, GESTOR}),
            ("mapa", reverse("mapa:mapa"), TODOS),
            ("mapa dados", reverse("mapa:dados") + "?status=aprovado,em_vaga&ate_km=10", TODOS),
            ("mensagens", reverse("mensagens:modelos"), EQUIPE),
            ("modelo novo", reverse("mensagens:modelo_novo"), EQUIPE),
            ("compor", reverse("mensagens:compor") + f"?candidato={c}&categoria=convocacao", EQUIPE),
            ("fila", reverse("mensagens:fila") + f"?ids={c}", EQUIPE),
            ("historico mensagens", reverse("mensagens:historico"), EQUIPE),
            ("disparos", reverse("mensagens:disparos"), EQUIPE),
            ("conexao whatsapp", reverse("whatsapp:conexao"), {ADMIN}),
            ("links", reverse("links:lista"), {ADMIN, REC, GESTOR}),
            ("convite novo", reverse("links:novo"), {ADMIN, REC, GESTOR}),
            ("cartaz", reverse("links:cartaz"), {ADMIN, REC, GESTOR}),
            ("configuracoes", reverse("core:configuracoes"), {ADMIN}),
            ("atividades", reverse("core:auditoria"), {ADMIN}),
            ("usuarios", reverse("contas:usuarios"), {ADMIN}),
            ("usuario novo", reverse("contas:usuario_novo"), {ADMIN}),
            ("minha senha", reverse("contas:minha_senha"), TODOS),
        ]

    def test_cada_perfil_abre_so_o_que_pode(self):
        senha = settings.RRPA_DEMO_SENHA
        for login in (ADMIN, REC, GESTOR, VISUAL):
            self.client.logout()
            self.assertTrue(self.client.login(username=login, password=senha), login)
            for nome, url, permitidos in self.acessos():
                resposta = self.client.get(url)
                if login in permitidos:
                    self.assertEqual(resposta.status_code, 200, f"{login} deveria abrir “{nome}” ({url}) mas recebeu {resposta.status_code}")
                else:
                    self.assertIn(resposta.status_code, (403, 404), f"{login} não deveria abrir “{nome}” ({url}) mas recebeu {resposta.status_code}")

    def test_sem_login_tudo_redireciona_para_a_entrada(self):
        for nome, url, _ in self.acessos():
            resposta = self.client.get(url)
            self.assertEqual(resposta.status_code, 302, f"{nome} abriu sem login")
            self.assertIn(reverse("contas:login"), resposta["Location"])

    def test_tela_de_login_e_paginas_publicas(self):
        self.assertEqual(self.client.get(reverse("contas:login")).status_code, 200)
        self.assertContains(self.client.get(reverse("contas:login")), "Modo demonstração")
        self.assertEqual(self.client.get(reverse("core:saude")).json(), {"status": "ok"})
        self.assertEqual(self.client.get("/cadastro/token-que-nao-existe/").status_code, 404)
        self.assertEqual(self.client.get("/webhook/whatsapp/token-que-nao-existe/").status_code, 404)  # público: não manda para o login
        self.assertEqual(self.client.get(reverse("candidatos:cadastro_concluido")).status_code, 200)
