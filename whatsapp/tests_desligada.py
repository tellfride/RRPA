"""Com RRPA_WHATSAPP_API=0 (o padrão) o envio é só o do WhatsApp aberto com a mensagem pronta (wa.me):
nada da API aparece, nada do webhook responde e nenhuma linha de conexão é criada."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from candidatos.models import Candidato
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from mensagens.models import Disparo, EnvioMensagem, MensagemDisparo, ModeloMensagem
from mensagens.padroes import criar_modelos_padrao
from vagas.models import Vaga
from whatsapp.models import ConexaoWhatsApp


@override_settings(RRPA_WHATSAPP_API=False)
class ApiDesligadaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        criar_modelos_padrao()
        cls.admin = Usuario.objects.create_user("adm", password="x", perfil="admin")
        cls.rec = Usuario.objects.create_user("rec", password="x", first_name="Marina", perfil="recrutador")
        cls.vaga = Vaga.objects.create(nome="Farmácia", quantidade=2)
        cls.ana = Candidato.objects.create(nome="Ana Lima", cpf=cpf_valido(1), telefone=telefone_ficticio(1), cep="01310100", status="aprovado", whatsapp_ok=True)
        cls.ana.vagas_interesse.add(cls.vaga)
        cls.caio = Candidato.objects.create(nome="Caio Reis", cpf=cpf_valido(2), telefone=telefone_ficticio(2), cep="01310100", status="aprovado", whatsapp_ok=False)
        cls.modelo = ModeloMensagem.objects.get(categoria="aprovacao")

    def test_a_chave_vem_desligada_por_padrao(self):
        import os

        from rrpa import settings as projeto

        if "RRPA_WHATSAPP_API" not in os.environ:
            self.assertFalse(projeto.RRPA_WHATSAPP_API)

    def test_telas_da_api_nao_existem(self):
        self.client.force_login(self.admin)
        for url in (reverse("whatsapp:conexao"), reverse("mensagens:disparos"), reverse("mensagens:disparo", args=[1])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in (reverse("mensagens:disparo_criar"), reverse("mensagens:disparo_controle", args=[1])):
            self.assertEqual(self.client.post(url, {"ids": str(self.ana.pk), "modelo": self.modelo.pk, "acao": "pausar"}).status_code, 404, url)
        self.assertFalse(Disparo.objects.exists())

    def test_webhook_nao_responde_nem_com_o_token_certo(self):
        token = ConexaoWhatsApp.atual().webhook_token
        url = reverse("whatsapp:webhook", args=[token])
        self.assertEqual(self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1"}).status_code, 404)
        corpo = '{"entry":[{"changes":[{"value":{"messages":[{"from":"5511988887777","type":"text","text":{"body":"SAIR"}}]}}]}]}'
        self.assertEqual(self.client.post(url, data=corpo, content_type="application/json").status_code, 404)
        self.assertEqual(self.client.put(url, data=corpo, content_type="application/json").status_code, 404)

    def test_envio_em_lote_continua_so_com_o_botao_do_whatsapp(self):
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:fila"), {"ids": f"{self.ana.pk},{self.caio.pk}", "modelo": self.modelo.pk})
        self.assertContains(pagina, "Abrir WhatsApp")
        self.assertContains(pagina, "Pediu para não receber")
        self.assertContains(pagina, "Olá, Ana!")
        for rastro in ("Disparo automático", "Disparar para", "Entra no disparo", "Fora do disparo", "Parâmetros do template", "disparos/novo"):
            self.assertNotContains(pagina, rastro)

    def test_registrar_o_envio_manual_segue_funcionando(self):
        self.client.force_login(self.rec)
        resposta = self.client.post(reverse("mensagens:registrar"), {"candidato": self.ana.pk, "modelo": self.modelo.pk, "texto": "Olá, Ana!"})
        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(EnvioMensagem.objects.count(), 1)

    def test_nenhuma_tela_menciona_a_api_nem_cria_conexao(self):
        self.client.force_login(self.admin)
        for url in (reverse("core:configuracoes"), reverse("mensagens:modelos"), reverse("mensagens:modelo_novo"),
                    reverse("mensagens:modelo_editar", args=[self.modelo.pk]), reverse("core:auditoria"), reverse("painel:inicio"),
                    reverse("mensagens:fila") + f"?ids={self.ana.pk}", reverse("candidatos:lista")):
            pagina = self.client.get(url)
            self.assertEqual(pagina.status_code, 200, url)
            for rastro in ("WhatsApp automático", "Configurar o WhatsApp", "API oficial da Meta", "mensagens/disparos", "Disparos de WhatsApp", "configuracoes/whatsapp"):
                self.assertNotContains(pagina, rastro, msg_prefix=url)
        self.assertFalse(ConexaoWhatsApp.objects.exists())  # nem abrir as telas cria o registro da conexão

    def test_salvar_um_modelo_nao_apaga_o_template_ja_cadastrado(self):
        ModeloMensagem.objects.filter(pk=self.modelo.pk).update(template_oficial_nome="aprovacao_rpa", template_oficial_variaveis=["primeiro_nome"])
        self.client.force_login(self.rec)
        self.client.post(reverse("mensagens:modelo_editar", args=[self.modelo.pk]), {
            "nome": "Aprovação na triagem", "categoria": "aprovacao", "texto": "Olá, {{primeiro_nome}}!", "ativo": "on",
        })
        self.modelo.refresh_from_db()
        self.assertEqual(self.modelo.texto, "Olá, {{primeiro_nome}}!")
        self.assertEqual((self.modelo.template_oficial_nome, self.modelo.template_oficial_variaveis), ("aprovacao_rpa", ["primeiro_nome"]))

    def test_comando_disparar_nao_envia_nada(self):
        disparo = Disparo.objects.create(nome="Antigo", criado_por=self.rec, provedor="simulador")
        mensagem = MensagemDisparo.objects.create(disparo=disparo, candidato=self.ana, nome_destino=self.ana.nome, telefone=self.ana.telefone, status="fila")
        saida = StringIO()
        call_command("disparar", stdout=saida)
        self.assertIn("desligado", saida.getvalue())
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, "fila")

    def test_com_localizar_o_worker_continua_cuidando_do_mapa(self):
        with mock.patch("core.management.commands.geocodificar.geo.aplicar_localizacao") as localizar:
            call_command("disparar", "--localizar", stdout=StringIO())
        self.assertTrue(localizar.called)

    def test_demonstracao_nao_conecta_o_simulador(self):
        with override_settings(RRPA_DEMO=True, RRPA_DEMO_SENHA="teste-demo-123"):
            call_command("seed_demo", "--forcar", verbosity=0)
        self.assertFalse(ConexaoWhatsApp.objects.exists())
