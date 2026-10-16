import json
import re
from datetime import timedelta
from unittest import mock

import requests
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidatos.models import Candidato
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import LogAtividade
from mensagens.models import Disparo, MensagemDisparo, MensagemRecebida
from whatsapp import cripto, registro
from whatsapp.models import ConexaoWhatsApp
from whatsapp.provedores import meta, simulador
from whatsapp.tipos import ChamadaTemplate, MensagemSaida

from .testando import CREDENCIAIS_META, SEGREDO_APP, TOKEN, SemRede, conectar, resposta

URL_ENVIO = "https://graph.facebook.com/v21.0/123456789012345/messages"


class CriptoTests(SimpleTestCase):
    def test_cifra_sem_deixar_o_texto_aparecer_e_decifra_de_volta(self):
        cifrado = cripto.cifrar_json({"access_token": TOKEN})
        self.assertTrue(cifrado.startswith("v1:"))
        self.assertNotIn(TOKEN, cifrado)
        self.assertNotIn("access_token", cifrado)
        self.assertEqual(cripto.decifrar_json(cifrado), {"access_token": TOKEN})

    def test_outra_secret_key_nao_decifra(self):
        cifrado = cripto.cifrar_json({"a": "b"})
        with override_settings(SECRET_KEY="outra-chave-completamente-diferente"):
            with self.assertRaises(cripto.ErroCredenciais):
                cripto.decifrar_json(cifrado)

    def test_chave_antiga_em_fallbacks_continua_valendo(self):
        with override_settings(SECRET_KEY="chave-antiga", SECRET_KEY_FALLBACKS=[]):
            cifrado = cripto.cifrar_json({"a": "b"})
        with override_settings(SECRET_KEY="chave-nova", SECRET_KEY_FALLBACKS=["chave-antiga"]):
            self.assertEqual(cripto.decifrar_json(cifrado), {"a": "b"})

    def test_dado_corrompido_ou_de_outro_formato_e_recusado(self):
        for ruim in ("v1:isto-nao-e-fernet", "v2:abc", "texto puro"):
            with self.assertRaises(cripto.ErroCredenciais):
                cripto.decifrar_json(ruim)

    def test_mascara_mostra_no_maximo_os_quatro_ultimos(self):
        self.assertEqual(cripto.mascarar(""), "")
        self.assertEqual(cripto.mascarar("curto"), "••••••••")
        mascarado = cripto.mascarar(TOKEN)
        self.assertTrue(mascarado.endswith(TOKEN[-4:]))
        self.assertNotIn(TOKEN[:-4], mascarado)


class ConexaoModeloTests(TestCase):
    def test_credenciais_ficam_criptografadas_no_banco(self):
        con = ConexaoWhatsApp.atual()
        con.guardar_credenciais(CREDENCIAIS_META)
        con.save()
        with connection.cursor() as cursor:
            cursor.execute("select credenciais from whatsapp_conexaowhatsapp")
            gravado = cursor.fetchone()[0]
        self.assertNotIn(TOKEN, gravado)
        self.assertNotIn(SEGREDO_APP, gravado)
        self.assertEqual(ConexaoWhatsApp.atual().credenciais_abertas()["access_token"], TOKEN)

    def test_e_um_registro_unico(self):
        ConexaoWhatsApp.atual()
        ConexaoWhatsApp(provedor="meta").save()
        self.assertEqual(ConexaoWhatsApp.objects.count(), 1)


class MetaTests(SemRede, SimpleTestCase):
    def provedor(self):
        return meta.Meta(dict(CREDENCIAIS_META))

    def test_envia_texto(self):
        self.responder(resposta(200, {"messages": [{"id": "wamid.ABC"}]}))
        resultado = self.provedor().enviar(MensagemSaida(para="5511988887777", texto="Olá, Maria!"))
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.id_mensagem, "wamid.ABC")
        args, kwargs = self.rede.call_args
        self.assertEqual(args, ("POST", URL_ENVIO))
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["json"], {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": "5511988887777",
            "type": "text", "text": {"body": "Olá, Maria!", "preview_url": True},
        })

    def test_envia_template_aprovado_com_parametros_na_ordem(self):
        self.responder(resposta(200, {"messages": [{"id": "wamid.T"}]}))
        template = ChamadaTemplate(nome="convocacao_rpa", idioma="pt_BR", parametros=["Maria", "Farmácia\nTurno da manhã", ""])
        resultado = self.provedor().enviar(MensagemSaida(para="5511988887777", texto="não vai", template=template))
        self.assertTrue(resultado.ok)
        corpo = self.rede.call_args.kwargs["json"]
        self.assertEqual(corpo["type"], "template")
        self.assertNotIn("text", corpo)
        self.assertEqual(corpo["template"]["name"], "convocacao_rpa")
        self.assertEqual(corpo["template"]["language"], {"code": "pt_BR"})
        self.assertEqual(
            corpo["template"]["components"],
            [{"type": "body", "parameters": [
                {"type": "text", "text": "Maria"},
                {"type": "text", "text": "Farmácia Turno da manhã"},  # quebra de linha não é aceita pela Meta
                {"type": "text", "text": "-"},  # parâmetro vazio também não
            ]}],
        )

    def test_template_sem_parametros_nao_leva_componentes(self):
        self.responder(resposta(200, {"messages": [{"id": "wamid.T"}]}))
        self.provedor().enviar(MensagemSaida(para="5511988887777", texto="", template=ChamadaTemplate(nome="aviso_geral")))
        self.assertNotIn("components", self.rede.call_args.kwargs["json"]["template"])

    def test_erros_da_api_viram_mensagem_em_portugues_e_dizem_se_vale_tentar_de_novo(self):
        casos = [
            (401, {"error": {"code": 190, "message": "Invalid OAuth access token"}}, "Token de acesso inválido", False),
            (400, {"error": {"code": 132001, "message": "Template name does not exist"}}, "Template inexistente", False),
            (400, {"error": {"code": 131026, "message": "Message undeliverable"}}, "não entregável", False),
            (429, {"error": {"code": 130429, "message": "Rate limit"}}, "Limite de vazão", True),
            (400, {"error": {"code": 131056, "message": "pair rate limit"}}, "Muitas mensagens", True),
            (500, {"error": {"message": "Internal"}}, "Internal", True),
            (503, None, "Erro HTTP 503", True),
        ]
        for status, dados, trecho, repetir in casos:
            with self.subTest(status=status, trecho=trecho):
                self.responder(resposta(status, dados))
                resultado = self.provedor().enviar(MensagemSaida(para="5511988887777", texto="oi"))
                self.assertFalse(resultado.ok)
                self.assertIn(trecho, resultado.erro)
                self.assertEqual(resultado.tentar_de_novo, repetir)
                self.assertNotIn(TOKEN, resultado.erro)

    def test_falha_de_rede_e_tempo_esgotado_podem_ser_repetidos_sem_vazar_detalhes(self):
        for erro in (requests.ConnectionError(f"falha em {URL_ENVIO} com {TOKEN}"), requests.Timeout("lento")):
            with self.subTest(erro=type(erro).__name__):
                self.rede.side_effect = erro
                resultado = self.provedor().enviar(MensagemSaida(para="5511988887777", texto="oi"))
                self.assertFalse(resultado.ok)
                self.assertTrue(resultado.tentar_de_novo)
                self.assertNotIn(TOKEN, resultado.erro)
                self.assertNotIn("graph.facebook.com", resultado.erro)

    def test_credenciais_incompletas_e_destino_invalido_nem_chamam_a_api(self):
        self.assertFalse(meta.Meta({}).enviar(MensagemSaida(para="5511988887777", texto="oi")).ok)
        self.assertFalse(self.provedor().enviar(MensagemSaida(para="123", texto="oi")).ok)
        self.rede.assert_not_called()

    def test_resposta_de_sucesso_sem_id_nao_conta_como_enviada(self):
        self.responder(resposta(200, {"messages": []}))
        resultado = self.provedor().enviar(MensagemSaida(para="5511988887777", texto="oi"))
        self.assertFalse(resultado.ok)
        self.assertFalse(resultado.tentar_de_novo)

    def test_teste_de_conexao_ok(self):
        self.responder(resposta(200, {"display_phone_number": "+55 11 3000-0000", "verified_name": "Hospital Exemplo", "quality_rating": "GREEN"}))
        resultado = self.provedor().testar_conexao()
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.numero, "+55 11 3000-0000")
        self.assertIn("Hospital Exemplo", resultado.mensagem)
        self.assertIn("alta", resultado.mensagem)
        args, kwargs = self.rede.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].startswith("https://graph.facebook.com/v21.0/123456789012345?fields="))

    def test_teste_de_conexao_com_token_invalido_e_com_rede_fora(self):
        self.responder(resposta(401, {"error": {"code": 190, "message": "bad"}}))
        resultado = self.provedor().testar_conexao()
        self.assertFalse(resultado.ok)
        self.assertIn("Token de acesso inválido", resultado.mensagem)
        self.rede.side_effect = requests.ConnectionError("x")
        self.assertFalse(self.provedor().testar_conexao().ok)
        self.assertFalse(meta.Meta({}).testar_conexao().ok)

    def test_versao_da_api_nao_deixa_trocar_o_endereco(self):
        self.assertEqual(meta.url_base("v22.0"), "https://graph.facebook.com/v22.0")
        self.assertEqual(meta.url_base(""), "https://graph.facebook.com/v21.0")
        self.assertEqual(meta.url_base("v1/../../evil"), "https://graph.facebook.com/v1%2F..%2F..%2Fevil")


class SimuladorTests(SemRede, SimpleTestCase):
    def test_nunca_usa_a_rede(self):
        envio = simulador.Simulador({"taxa_falhas": "0"})
        resultado = envio.enviar(MensagemSaida(para="5511988887777", texto="oi"))
        self.assertTrue(resultado.ok)
        self.assertTrue(resultado.id_mensagem.startswith("sbx_"))
        self.assertTrue(envio.testar_conexao().ok)
        self.rede.assert_not_called()

    def test_falha_conforme_a_taxa(self):
        sempre = simulador.Simulador({"taxa_falhas": "0,5"}, sorteio=lambda: 0.0)
        self.assertFalse(sempre.enviar(MensagemSaida(para="5511988887777", texto="oi")).ok)
        nunca = simulador.Simulador({"taxa_falhas": "0,5"}, sorteio=lambda: 0.99)
        self.assertTrue(nunca.enviar(MensagemSaida(para="5511988887777", texto="oi")).ok)

    def test_taxa_e_limitada_e_tem_padrao(self):
        self.assertEqual(simulador.ler_taxa(""), 0.05)
        self.assertEqual(simulador.ler_taxa("lixo"), 0.05)
        self.assertEqual(simulador.ler_taxa("0,1"), 0.1)
        self.assertEqual(simulador.ler_taxa("9"), 0.5)
        self.assertEqual(simulador.ler_taxa("-1"), 0.0)

    def test_numero_ou_mensagem_invalidos_falham(self):
        envio = simulador.Simulador({"taxa_falhas": "0"})
        self.assertFalse(envio.enviar(MensagemSaida(para="12", texto="oi")).ok)
        self.assertFalse(envio.enviar(MensagemSaida(para="5511988887777", texto="  ")).ok)

    def test_entrega_e_leitura_seguem_o_tempo_e_sao_repetiveis(self):
        id_mensagem = "sbx_" + "ab12" * 6
        enviada = timezone.now()
        self.assertEqual(simulador.eventos_pendentes(id_mensagem, enviada, enviada), [])
        depois = simulador.eventos_pendentes(id_mensagem, enviada, enviada + timedelta(seconds=30))
        self.assertIn("entregue", [e.status for e in depois])
        self.assertEqual(depois, simulador.eventos_pendentes(id_mensagem, enviada, enviada + timedelta(seconds=30)))
        self.assertEqual(simulador.eventos_pendentes("wamid.X", enviada, enviada + timedelta(days=1)), [])


class WebhookRegrasTests(SimpleTestCase):
    def corpo(self, valor):
        return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": valor}]}]}).encode()

    def test_status_viram_eventos_com_os_nomes_do_sistema(self):
        eventos = meta.interpretar_webhook(self.corpo({"statuses": [
            {"id": "wamid.1", "status": "sent", "timestamp": "1760000000"},
            {"id": "wamid.1", "status": "delivered", "timestamp": "1760000005"},
            {"id": "wamid.1", "status": "read", "timestamp": "1760000009"},
            {"id": "wamid.2", "status": "failed", "errors": [{"code": 131026, "title": "Undeliverable"}]},
            {"id": "wamid.3", "status": "inventado"},
        ]}))
        self.assertEqual([(e.id_mensagem, e.status) for e in eventos], [("wamid.1", "enviada"), ("wamid.1", "entregue"), ("wamid.1", "lida"), ("wamid.2", "falhou")])
        self.assertIn("não entregável", eventos[3].erro)
        self.assertIsNotNone(eventos[0].quando)

    def test_mensagens_recebidas_viram_eventos(self):
        eventos = meta.interpretar_webhook(self.corpo({"messages": [
            {"from": "5511988887777", "type": "text", "text": {"body": "SAIR"}, "timestamp": "1760000000"},
            {"from": "5511977776666", "type": "button", "button": {"text": "Parar promoções"}},
            {"from": "5511966665555", "type": "image"},
        ]}))
        self.assertEqual([(e.tipo, e.telefone, e.texto) for e in eventos], [
            ("recebida", "5511988887777", "SAIR"), ("recebida", "5511977776666", "Parar promoções"), ("recebida", "5511966665555", "[imagem]"),
        ])

    def test_corpo_invalido_nao_derruba(self):
        for ruim in (b"", b"nao e json", b"[]", b'{"entry": "x"}', b'{"entry": [{"changes": [null]}]}'):
            self.assertEqual(meta.interpretar_webhook(ruim), [])

    def test_assinatura_so_vale_com_o_segredo_do_app(self):
        corpo = b'{"a": 1}'
        valida = meta.calcular_assinatura(SEGREDO_APP, corpo)
        cred = {"app_secret": SEGREDO_APP}
        self.assertTrue(meta.assinatura_valida(corpo, valida, cred))
        self.assertTrue(meta.assinatura_valida(corpo, valida.upper().replace("SHA256=", "sha256="), cred))
        self.assertFalse(meta.assinatura_valida(corpo, meta.calcular_assinatura("outro", corpo), cred))
        self.assertFalse(meta.assinatura_valida(b'{"a": 2}', valida, cred))
        self.assertFalse(meta.assinatura_valida(corpo, "", cred))
        self.assertFalse(meta.assinatura_valida(corpo, valida, {}))  # sem o segredo, recusa (não aceita às cegas)

    def test_verificacao_devolve_o_desafio_so_com_o_token_certo(self):
        consulta = {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "12345"}
        self.assertEqual(meta.responder_verificacao(consulta, "tok"), (200, "12345"))
        self.assertEqual(meta.responder_verificacao({**consulta, "hub.verify_token": "errado"}, "tok")[0], 403)
        self.assertEqual(meta.responder_verificacao({**consulta, "hub.challenge": "<script>"}, "tok")[0], 400)
        self.assertIsNone(meta.responder_verificacao({}, "tok"))


class TelaConexaoTests(SemRede, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_user("adm", password="x", perfil="admin")
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")

    def dados_meta(self, **extra):
        dados = {
            "provedor": "meta", "limite_por_minuto": "30", "phone_number_id": "123456789012345", "access_token": TOKEN,
            "app_secret": SEGREDO_APP, "api_version": "v21.0", "acao": "salvar",
        }
        dados.update(extra)
        return dados

    def test_so_o_administrador_acessa(self):
        url = reverse("whatsapp:conexao")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.rec)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.dados_meta()).status_code, 403)
        self.client.force_login(self.admin)
        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertNotRegex(pagina.content.decode(), r"<script(?![^>]*\bsrc=)")  # CSP: sem script inline

    def test_salva_criptografado_e_nunca_devolve_o_segredo_ao_navegador(self):
        self.client.force_login(self.admin)
        resposta_post = self.client.post(reverse("whatsapp:conexao"), self.dados_meta(), follow=True)
        self.assertNotContains(resposta_post, TOKEN)
        self.assertNotContains(resposta_post, SEGREDO_APP)
        self.assertContains(resposta_post, "Salvo: ••••••••" + TOKEN[-4:])
        con = ConexaoWhatsApp.atual()
        self.assertEqual((con.provedor, con.limite_por_minuto, con.status), ("meta", 30, "pendente"))
        self.assertNotIn(TOKEN, con.credenciais)
        self.assertEqual(con.credenciais_abertas()["access_token"], TOKEN)
        # nem a tela, nem a trilha de atividades, nem o formulário reaberto com erro mostram o segredo
        self.assertNotContains(self.client.get(reverse("whatsapp:conexao")), TOKEN)
        invalido = self.client.post(reverse("whatsapp:conexao"), self.dados_meta(phone_number_id="abc", access_token="novo token com espaço"))
        self.assertNotContains(invalido, "novo token com espaço")
        self.assertNotContains(invalido, TOKEN)
        for atividade in LogAtividade.objects.all():
            self.assertNotIn(TOKEN, atividade.descricao)
            self.assertNotIn(SEGREDO_APP, atividade.descricao)

    def test_campo_secreto_em_branco_mantem_o_que_estava_salvo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("whatsapp:conexao"), self.dados_meta())
        self.client.post(reverse("whatsapp:conexao"), self.dados_meta(access_token="", app_secret="", limite_por_minuto="10"))
        con = ConexaoWhatsApp.atual()
        self.assertEqual(con.limite_por_minuto, 10)
        self.assertEqual(con.credenciais_abertas()["access_token"], TOKEN)
        self.assertEqual(con.credenciais_abertas()["app_secret"], SEGREDO_APP)

    def test_validacoes(self):
        self.client.force_login(self.admin)
        url = reverse("whatsapp:conexao")
        for campo, valor, trecho in [
            ("phone_number_id", "abc", "só os números"),
            ("phone_number_id", "", "Obrigatório"),
            ("access_token", "curto", "incompleto"),
            ("api_version", "21", "formato v21.0"),
            ("limite_por_minuto", "0", ""),
            ("limite_por_minuto", "999", ""),
        ]:
            with self.subTest(campo=campo, valor=valor):
                pagina = self.client.post(url, self.dados_meta(**{campo: valor}))
                self.assertEqual(pagina.status_code, 200)
                self.assertFalse(pagina.context["form"].is_valid())
                if trecho:
                    self.assertContains(pagina, trecho)
        self.assertEqual(ConexaoWhatsApp.atual().provedor, "simulador")  # nada foi salvo

    def test_primeiro_acesso_com_meta_exige_token_e_chave_do_app(self):
        self.client.force_login(self.admin)
        pagina = self.client.post(reverse("whatsapp:conexao"), self.dados_meta(access_token="", app_secret=""))
        self.assertEqual(len(pagina.context["form"].errors), 2)

    def test_salvar_e_testar_com_a_meta(self):
        self.client.force_login(self.admin)
        self.responder(resposta(200, {"display_phone_number": "+55 11 3000-0000", "verified_name": "Hospital Exemplo", "quality_rating": "GREEN"}))
        pagina = self.client.post(reverse("whatsapp:conexao"), self.dados_meta(acao="testar"), follow=True)
        con = ConexaoWhatsApp.atual()
        self.assertEqual((con.status, con.numero_exibido), ("conectada", "+55 11 3000-0000"))
        self.assertIsNotNone(con.testada_em)
        self.assertContains(pagina, "Conectado à Meta")
        self.assertEqual(self.rede.call_args.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertTrue(LogAtividade.objects.filter(acao="whatsapp.teste").exists())

    def test_teste_com_erro_marca_a_conexao_com_erro(self):
        self.client.force_login(self.admin)
        self.responder(resposta(401, {"error": {"code": 190, "message": "bad"}}))
        pagina = self.client.post(reverse("whatsapp:conexao"), self.dados_meta(acao="testar"), follow=True)
        con = ConexaoWhatsApp.atual()
        self.assertEqual(con.status, "erro")
        self.assertFalse(con.pode_enviar)
        self.assertIn("Token de acesso inválido", con.ultimo_erro)
        self.assertContains(pagina, "Token de acesso inválido")
        self.assertNotContains(pagina, TOKEN)

    def test_mudar_credenciais_ou_provedor_exige_testar_de_novo(self):
        conectar("meta")
        self.client.force_login(self.admin)
        self.client.post(reverse("whatsapp:conexao"), self.dados_meta(limite_por_minuto="5"))  # só o limite mudou
        self.assertEqual(ConexaoWhatsApp.atual().status, "conectada")
        self.client.post(reverse("whatsapp:conexao"), self.dados_meta(phone_number_id="999999999999999"))
        self.assertEqual(ConexaoWhatsApp.atual().status, "pendente")

    def test_simulador_nao_precisa_de_credenciais_nem_de_rede(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("whatsapp:conexao"), {"provedor": "simulador", "limite_por_minuto": "20", "taxa_falhas": "0,1", "acao": "testar"})
        con = ConexaoWhatsApp.atual()
        self.assertEqual((con.provedor, con.status), ("simulador", "conectada"))
        self.assertEqual(con.credenciais_abertas()["taxa_falhas"], "0.1")
        self.rede.assert_not_called()

    def test_trocar_para_o_simulador_nao_apaga_as_credenciais_da_meta(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("whatsapp:conexao"), self.dados_meta())
        self.client.post(reverse("whatsapp:conexao"), {"provedor": "simulador", "limite_por_minuto": "20", "taxa_falhas": "0", "acao": "salvar"})
        self.assertEqual(ConexaoWhatsApp.atual().credenciais_abertas()["access_token"], TOKEN)

    def test_credenciais_ilegiveis_avisam_e_deixam_digitar_de_novo(self):
        conectar("meta")
        url = reverse("whatsapp:conexao")
        with override_settings(SECRET_KEY="a-chave-mudou-depois-de-salvar"):
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(url), "não podem ser lidas")
            sem_digitar = self.client.post(url, self.dados_meta(access_token="", app_secret=""))
            self.assertEqual(len(sem_digitar.context["form"].errors), 2)  # não dá para "manter" o que não se lê
            self.responder(resposta(200, {"verified_name": "X"}))
            self.client.post(url, self.dados_meta(acao="testar"))
            self.assertEqual(ConexaoWhatsApp.atual().credenciais_abertas()["access_token"], TOKEN)
            self.assertEqual(ConexaoWhatsApp.atual().status, "conectada")

    def test_novo_token_do_webhook_invalida_a_url_antiga(self):
        conectar("meta")
        antigo = ConexaoWhatsApp.atual().webhook_token
        self.client.force_login(self.admin)
        self.client.post(reverse("whatsapp:conexao"), {"acao": "novo_token"})
        novo = ConexaoWhatsApp.atual().webhook_token
        self.assertNotEqual(antigo, novo)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("whatsapp:webhook", args=[antigo])).status_code, 404)
        self.assertEqual(self.client.get(reverse("whatsapp:webhook", args=[novo])).status_code, 200)

    def test_limite_de_testes_seguidos(self):
        self.client.force_login(self.admin)
        self.responder(resposta(200, {"verified_name": "X"}))
        for _ in range(12):
            self.client.post(reverse("whatsapp:conexao"), self.dados_meta(acao="testar"))
        self.assertEqual(self.rede.call_count, 10)


class WebhookHttpTests(SemRede, TestCase):
    """Fluxo completo pelo endereço público: handshake, assinatura, status e resposta SAIR."""

    @classmethod
    def setUpTestData(cls):
        cls.con = conectar("meta")
        cls.url = reverse("whatsapp:webhook", args=[cls.con.webhook_token])
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")
        cls.pessoa = Candidato.objects.create(nome="Maria Souza", cpf=cpf_valido(1), telefone="11988887777", cep="01310100", status="aprovado")
        cls.outra = Candidato.objects.create(nome="Pedro Alves", cpf=cpf_valido(2), telefone=telefone_ficticio(2), cep="01310100", status="aprovado")
        disparo = Disparo.objects.create(nome="Teste", criado_por=cls.rec, provedor="meta", template_nome="aviso")
        cls.msg = MensagemDisparo.objects.create(
            disparo=disparo, candidato=cls.pessoa, nome_destino=cls.pessoa.nome, telefone=cls.pessoa.telefone, status="enviada",
            id_provedor="wamid.A", enviada_em=timezone.now(),
        )
        cls.na_fila = MensagemDisparo.objects.create(disparo=disparo, candidato=cls.pessoa, nome_destino=cls.pessoa.nome, telefone=cls.pessoa.telefone, status="fila")

    def enviar(self, valor, assinar=True, segredo=SEGREDO_APP, cliente=None):
        corpo = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": valor}]}]}).encode()
        extra = {"HTTP_X_HUB_SIGNATURE_256": meta.calcular_assinatura(segredo, corpo)} if assinar else {}
        return (cliente or self.client).post(self.url, data=corpo, content_type="application/json", **extra)

    def status(self, id_, estado, ts=1760000000):
        return {"statuses": [{"id": id_, "status": estado, "timestamp": str(ts)}]}

    def test_handshake_da_meta(self):
        consulta = {"hub.mode": "subscribe", "hub.verify_token": self.con.webhook_token, "hub.challenge": "98765"}
        resposta_get = self.client.get(self.url, consulta)
        self.assertEqual((resposta_get.status_code, resposta_get.content), (200, b"98765"))
        self.assertEqual(self.client.get(self.url, {**consulta, "hub.verify_token": "errado"}).status_code, 403)

    def test_endereco_errado_ou_sem_conexao_e_404(self):
        self.assertEqual(self.client.get(reverse("whatsapp:webhook", args=["token-errado"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("whatsapp:webhook", args=["token-errado"]), data="{}", content_type="application/json").status_code, 404)
        ConexaoWhatsApp.objects.all().delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_publico_e_sem_csrf_mas_exige_assinatura(self):
        cliente = Client(enforce_csrf_checks=True)  # sem login e com a checagem de CSRF ligada
        self.assertEqual(self.enviar(self.status("wamid.A", "delivered"), cliente=cliente).status_code, 200)
        self.assertEqual(self.enviar(self.status("wamid.A", "read"), assinar=False, cliente=cliente).status_code, 401)
        self.assertEqual(self.enviar(self.status("wamid.A", "read"), segredo="errado", cliente=cliente).status_code, 401)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "entregue")  # o "lida" sem assinatura válida foi ignorado

    def test_sem_a_chave_do_app_salva_recusa_em_vez_de_aceitar_as_cegas(self):
        conectar("meta", credenciais={"phone_number_id": "123456789012345", "access_token": TOKEN})
        self.assertEqual(self.enviar(self.status("wamid.A", "read")).status_code, 401)

    def test_status_so_avancam(self):
        self.enviar(self.status("wamid.A", "read", 1760000009))
        self.enviar(self.status("wamid.A", "delivered", 1760000005))  # chegou atrasado
        self.enviar(self.status("wamid.A", "failed"))  # falha depois de lida não vale
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "lida")
        self.assertIsNotNone(self.msg.entregue_em)
        self.assertIsNotNone(self.msg.lida_em)
        self.assertEqual(self.msg.erro, "")

    def test_falha_informada_pelo_provedor(self):
        self.enviar({"statuses": [{"id": "wamid.A", "status": "failed", "errors": [{"code": 131026, "title": "x"}]}]})
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "falhou")
        self.assertIn("não entregável", self.msg.erro)

    def test_id_desconhecido_nao_mexe_em_nada(self):
        self.enviar(self.status("wamid.OUTRA", "read"))
        self.enviar(self.status("", "read"))
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "enviada")

    def test_resposta_sair_bloqueia_a_pessoa_e_tira_da_fila(self):
        self.enviar({"messages": [{"from": "5511988887777", "type": "text", "text": {"body": " Sair! "}, "timestamp": "1760000000"}]})
        self.pessoa.refresh_from_db()
        self.assertIs(self.pessoa.whatsapp_ok, False)
        self.assertIsNotNone(self.pessoa.whatsapp_ok_em)
        self.na_fila.refresh_from_db()
        self.assertEqual((self.na_fila.status, self.na_fila.erro), ("ignorada", "Pediu para não receber mensagens"))
        recebida = MensagemRecebida.objects.get()
        self.assertTrue(recebida.pediu_saida)
        self.assertEqual(recebida.candidato, self.pessoa)
        registro_log = LogAtividade.objects.get(acao="whatsapp.sair")
        self.assertEqual((registro_log.entidade, registro_log.entidade_id), ("candidato", self.pessoa.pk))
        for texto in (registro_log.descricao, registro_log.entidade):
            self.assertNotIn("Maria", texto)
            self.assertNotIn("11988887777", texto)
        self.assertNotIn("Souza", registro_log.descricao)

    def test_sair_de_celular_sem_o_nono_digito_tambem_acha_a_pessoa(self):
        self.enviar({"messages": [{"from": "551188887777", "type": "text", "text": {"body": "PARAR"}}]})
        self.pessoa.refresh_from_db()
        self.assertIs(self.pessoa.whatsapp_ok, False)

    def test_quem_divide_o_numero_tambem_deixa_de_receber(self):
        Candidato.objects.filter(pk=self.outra.pk).update(telefone="11988887777")
        self.enviar({"messages": [{"from": "5511988887777", "type": "text", "text": {"body": "sair"}}]})
        self.assertEqual(Candidato.objects.filter(whatsapp_ok=False).count(), 2)

    def test_outra_resposta_e_so_registrada(self):
        for texto in ("Quero sair do grupo de vagas?", "Sim, confirmo", "oi"):
            self.enviar({"messages": [{"from": "5511988887777", "type": "text", "text": {"body": texto}}]})
        self.pessoa.refresh_from_db()
        self.assertIsNone(self.pessoa.whatsapp_ok)
        self.assertEqual(MensagemRecebida.objects.filter(pediu_saida=False).count(), 3)
        self.assertFalse(LogAtividade.objects.filter(acao="whatsapp.sair").exists())

    def test_sair_de_numero_desconhecido_e_so_registrado(self):
        self.enviar({"messages": [{"from": "5521977770000", "type": "text", "text": {"body": "SAIR"}}]})
        self.assertTrue(MensagemRecebida.objects.get().pediu_saida)
        self.assertEqual(Candidato.objects.filter(whatsapp_ok=False).count(), 0)

    def test_corpo_grande_demais_e_recusado(self):
        corpo = b"x" * 1_100_000
        resposta_post = self.client.post(self.url, data=corpo, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=meta.calcular_assinatura(SEGREDO_APP, corpo))
        self.assertEqual(resposta_post.status_code, 413)

    def test_erro_interno_pede_nova_tentativa_ao_provedor(self):
        with mock.patch("whatsapp.views.aplicar_eventos", side_effect=RuntimeError("falha")):
            self.assertEqual(self.enviar(self.status("wamid.A", "read")).status_code, 500)

    def test_simulador_aceita_o_mesmo_formato_sem_assinatura(self):
        conectar("simulador")
        self.assertEqual(self.enviar(self.status("wamid.A", "read"), assinar=False).status_code, 200)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "lida")

    def test_so_aceita_get_e_post(self):
        self.assertEqual(self.client.put(self.url, data="{}", content_type="application/json").status_code, 405)

    def test_definicoes_do_registro(self):
        self.assertEqual([p.chave for p in registro.PROVEDORES], ["meta", "simulador"])
        self.assertTrue(registro.definicao("meta").exige_template)
        self.assertFalse(registro.definicao("simulador").exige_template)
        self.assertIsNone(registro.definicao("twilio"))
        self.assertTrue(re.fullmatch(r"[\w-]{40,}", self.con.webhook_token))
