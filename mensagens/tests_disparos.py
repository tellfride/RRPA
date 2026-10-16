"""Disparos automáticos de WhatsApp: criação, prévia, processamento (ritmo, tentativas, bloqueios) e regras de eventos.

Nenhum teste usa a rede: o Simulador não tem rede e a Meta é simulada em `requests.request` (veja whatsapp/testando.py).
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidatos import services as candidatos_services
from candidatos.models import Candidato
from contas.models import Usuario
from core.excecoes import RegraDeNegocio
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao, LogAtividade
from mensagens import disparos as servico
from mensagens import eventos, processador
from mensagens.models import Disparo, MensagemDisparo, MensagemRecebida, ModeloMensagem
from mensagens.padroes import criar_modelos_padrao
from vagas.models import Vaga
from whatsapp.models import ConexaoWhatsApp
from whatsapp.provedores import simulador
from whatsapp.testando import SemRede, conectar, resposta

T = MensagemDisparo.Status


def candidato(nome, n, ok=None, telefone=None, vaga=None):
    pessoa = Candidato.objects.create(
        nome=nome, cpf=cpf_valido(700 + n), telefone=telefone or telefone_ficticio(n), cep="01310100", status="aprovado", whatsapp_ok=ok,
    )
    if vaga is not None:
        pessoa.vagas_interesse.add(vaga)
    return pessoa


class Base(SemRede, TestCase):
    @classmethod
    def setUpTestData(cls):
        Configuracao.atual()
        criar_modelos_padrao()
        cls.rec = Usuario.objects.create_user("rec", password="x", first_name="Marina", last_name="Lopes", perfil="recrutador")
        cls.admin = Usuario.objects.create_user("adm", password="x", perfil="admin")
        cls.gestor = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.vaga = Vaga.objects.create(nome="Farmácia", quantidade=2)
        cls.modelo = ModeloMensagem.objects.get(categoria="aprovacao")  # usa primeiro_nome, vaga, recrutador e unidade
        cls.ana = candidato("Ana Lima", 1, ok=True, vaga=cls.vaga)
        cls.bia = candidato("Bia Prado", 2, ok=None, vaga=cls.vaga)  # cadastro da equipe, sem registro de autorização
        cls.caio = candidato("Caio Reis", 3, ok=False, vaga=cls.vaga)  # pediu para não receber
        cls.davi = candidato("Davi Melo", 4, ok=True, telefone="1234", vaga=cls.vaga)  # telefone inválido
        cls.eva = candidato("Eva Nunes", 5, ok=True)  # sem vaga de interesse: falta {{vaga}}
        cls.fabio = candidato("Fabio Cruz", 6, ok=True, telefone=cls.ana.telefone, vaga=cls.vaga)  # mesmo telefone da Ana
        cls.todos = [cls.ana, cls.bia, cls.caio, cls.davi, cls.eva, cls.fabio]

    def setUp(self):
        super().setUp()
        self.conexao = conectar("simulador")
        self.agora = timezone.now()

    def criar(self, pessoas=None, modelo=None, ritmo=20):
        pedido = RequestFactory().get("/")
        pedido.user = self.rec
        return servico.criar_disparo(self.rec, pessoas or self.todos, modelo or self.modelo, ritmo, pedido)

    def modelo_oficial(self, **extra):
        dados = dict(
            nome="Aprovação (oficial)", categoria="aprovacao", texto=self.modelo.texto, template_oficial_nome="aprovacao_rpa",
            template_oficial_variaveis=["primeiro_nome", "vaga"],
        )
        return ModeloMensagem.objects.create(**{**dados, **extra})

    def status_de(self, pessoa, disparo):
        return disparo.mensagens.get(candidato=pessoa)

    def ciclo(self, depois=0):
        return processador.ciclo(agora=self.agora + timedelta(seconds=depois))


class CriacaoTests(Base):
    def test_previa_mostra_quem_entra_e_quem_fica_de_fora(self):
        self.client.force_login(self.rec)
        ids = ",".join(str(p.pk) for p in self.todos)
        pagina = self.client.get(reverse("mensagens:fila"), {"ids": ids, "modelo": self.modelo.pk})
        self.assertContains(pagina, "Disparar para 2 pessoas")
        self.assertContains(pagina, "Entra no disparo")
        for motivo in ("Pediu para não receber mensagens", "Telefone inválido", "Faltam dados: vaga", "Telefone repetido neste disparo"):
            self.assertContains(pagina, f"Fora do disparo: {motivo}")
        self.assertContains(pagina, "Olá, Ana!")  # prévia com os dados daquela pessoa
        self.assertContains(pagina, "Abrir WhatsApp")  # o envio manual continua disponível

    def test_sem_conexao_pronta_a_previa_so_oferece_o_envio_manual(self):
        ConexaoWhatsApp.objects.update(status="pendente")
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:fila"), {"ids": str(self.ana.pk)})
        self.assertContains(pagina, "ainda não está pronta")
        self.assertNotContains(pagina, "Disparar para")
        self.assertContains(pagina, "Abrir WhatsApp")

    def test_cria_uma_mensagem_por_pessoa_com_status_e_motivo(self):
        self.client.force_login(self.rec)
        ids = ",".join(str(p.pk) for p in self.todos)
        resposta_post = self.client.post(reverse("mensagens:disparo_criar"), {"ids": ids, "modelo": self.modelo.pk, "ritmo": "999"})
        disparo = Disparo.objects.get()
        self.assertRedirects(resposta_post, reverse("mensagens:disparo", args=[disparo.pk]))
        self.assertEqual((disparo.status, disparo.criado_por, disparo.modelo, disparo.provedor), ("em_andamento", self.rec, self.modelo, "simulador"))
        self.assertEqual(disparo.ritmo_por_minuto, 20)  # nunca passa do limite da conexão
        self.assertEqual(disparo.mensagens.count(), 6)
        na_fila = {m.candidato: m for m in disparo.mensagens.filter(status=T.FILA)}
        self.assertEqual(set(na_fila), {self.ana, self.bia})
        self.assertEqual(na_fila[self.ana].texto.split("!")[0], "Olá, Ana")
        self.assertIn("Farmácia", na_fila[self.ana].texto)
        self.assertIn("Marina Lopes", na_fila[self.ana].texto)
        motivos = {m.candidato: m.erro for m in disparo.mensagens.filter(status=T.IGNORADA)}
        self.assertEqual(motivos, {
            self.caio: "Pediu para não receber mensagens", self.davi: "Telefone inválido",
            self.eva: "Faltam dados: vaga", self.fabio: "Telefone repetido neste disparo",
        })
        self.assertEqual(self.status_de(self.caio, disparo).texto, "")  # quem recusou nem tem texto preparado

    def test_atividades_sem_dados_pessoais(self):
        disparo = self.criar()
        self.ciclo()
        self.ciclo(30)
        acoes = set(LogAtividade.objects.values_list("acao", flat=True))
        self.assertTrue({"disparo.criado", "disparo.concluido"} <= acoes)
        for atividade in LogAtividade.objects.all():
            for pessoa in self.todos:
                for dado in (pessoa.nome, pessoa.nome.split()[0], pessoa.telefone, pessoa.cpf):
                    self.assertNotIn(dado, atividade.descricao)
            self.assertNotIn("Farmácia", atividade.descricao)
            self.assertNotIn("Olá", atividade.descricao)
        self.assertEqual(LogAtividade.objects.get(acao="disparo.criado").entidade_id, disparo.pk)

    def test_sem_conexao_pronta_nao_cria_nada(self):
        ConexaoWhatsApp.objects.update(status="erro")
        self.client.force_login(self.rec)
        resposta_post = self.client.post(reverse("mensagens:disparo_criar"), {"ids": str(self.ana.pk), "modelo": self.modelo.pk}, follow=True)
        self.assertContains(resposta_post, "ainda não está pronta")
        self.assertFalse(Disparo.objects.exists())

    def test_quem_nao_pode_receber_nao_gera_disparo(self):
        self.client.force_login(self.rec)
        resposta_post = self.client.post(
            reverse("mensagens:disparo_criar"), {"ids": f"{self.caio.pk},{self.davi.pk}", "modelo": self.modelo.pk}, follow=True,
        )
        self.assertContains(resposta_post, "Nenhuma das pessoas escolhidas pode receber")
        self.assertFalse(Disparo.objects.exists())

    def test_modelo_inativo_ou_ausente_e_recusado(self):
        ModeloMensagem.objects.filter(pk=self.modelo.pk).update(ativo=False)
        self.client.force_login(self.rec)
        self.client.post(reverse("mensagens:disparo_criar"), {"ids": str(self.ana.pk), "modelo": self.modelo.pk})
        self.client.post(reverse("mensagens:disparo_criar"), {"ids": str(self.ana.pk)})
        self.assertFalse(Disparo.objects.exists())

    def test_so_a_equipe_de_recrutamento_dispara(self):
        dados = {"ids": str(self.ana.pk), "modelo": self.modelo.pk}
        self.assertEqual(self.client.post(reverse("mensagens:disparo_criar"), dados).status_code, 302)  # sem login
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.post(reverse("mensagens:disparo_criar"), dados).status_code, 403)
        self.assertEqual(self.client.get(reverse("mensagens:disparos")).status_code, 403)
        self.assertEqual(self.client.get(reverse("mensagens:fila"), {"ids": str(self.ana.pk)}).status_code, 403)
        self.client.force_login(self.rec)
        self.assertEqual(self.client.get(reverse("mensagens:disparo_criar")).status_code, 405)
        self.assertFalse(Disparo.objects.exists())

    def test_exigir_autorizacao_deixa_de_fora_quem_nao_informou(self):
        ConexaoWhatsApp.objects.update(exigir_autorizacao=True)
        disparo = self.criar([self.ana, self.bia])
        self.assertEqual(self.status_de(self.ana, disparo).status, T.FILA)
        bia = self.status_de(self.bia, disparo)
        self.assertEqual((bia.status, bia.erro), (T.IGNORADA, "Sem autorização registrada para WhatsApp"))

    def test_limite_de_pessoas_por_disparo(self):
        with mock.patch.object(servico, "MAX_DESTINATARIOS", 1):
            with self.assertRaisesMessage(RegraDeNegocio, "até 1 pessoas"):
                self.criar([self.ana, self.bia])

    def test_api_oficial_exige_template_aprovado(self):
        conectar("meta")
        with self.assertRaisesMessage(RegraDeNegocio, "só envia template aprovado"):
            self.criar([self.ana])
        oficial = self.modelo_oficial()
        disparo = self.criar([self.ana, self.eva], oficial)
        self.assertEqual((disparo.template_nome, disparo.template_idioma, disparo.provedor), ("aprovacao_rpa", "pt_BR", "meta"))
        ana = self.status_de(self.ana, disparo)
        self.assertEqual((ana.status, ana.parametros), (T.FILA, ["Ana", "Farmácia"]))
        eva = self.status_de(self.eva, disparo)
        self.assertEqual((eva.status, eva.erro, eva.parametros), (T.IGNORADA, "Faltam dados: vaga", []))

    def test_previa_do_template_mostra_os_parametros(self):
        conectar("meta")
        oficial = self.modelo_oficial()
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:fila"), {"ids": str(self.ana.pk), "modelo": oficial.pk})
        self.assertContains(pagina, "Parâmetros do template")
        self.assertContains(pagina, "{{1}}</code> = Ana")
        sem = self.client.get(reverse("mensagens:fila"), {"ids": str(self.ana.pk), "modelo": self.modelo.pk})
        self.assertContains(sem, "só envia <strong>template aprovado</strong>")

    def test_ritmo_invalido_cai_no_padrao(self):
        self.assertEqual(self.criar([self.ana], ritmo="abc").ritmo_por_minuto, 20)
        self.assertEqual(self.criar([self.bia], ritmo=0).ritmo_por_minuto, 1)


class ProcessadorTests(Base):
    def test_envia_pelo_simulador_sem_tocar_na_rede(self):
        disparo = self.criar([self.ana, self.bia])
        relatorio = self.ciclo()
        self.assertEqual((relatorio["enviadas"], relatorio["falhas"]), (2, 0))
        for m in disparo.mensagens.all():
            self.assertEqual((m.status, m.tentativas, m.enviada_em), (T.ENVIADA, 1, self.agora))
            self.assertTrue(m.id_provedor.startswith("sbx_"))
        self.rede.assert_not_called()

    def test_entrega_e_leitura_simuladas_e_conclusao(self):
        disparo = self.criar([self.ana, self.bia])
        self.ciclo()
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "concluido")  # nada mais na fila (o acompanhamento de entrega continua)
        self.assertIsNotNone(disparo.finalizado_em)
        self.ciclo(30)
        for m in disparo.mensagens.all():
            self.assertIn(m.status, (T.ENTREGUE, T.LIDA))
            self.assertIsNotNone(m.entregue_em)
        self.assertEqual(servico.contagens(disparo)["entregues"], 2)

    def test_ritmo_da_conexao_e_do_disparo(self):
        pessoas = [candidato(f"Pessoa {i}", 100 + i, ok=True, vaga=self.vaga) for i in range(5)]
        conectar("simulador", limite=3)
        disparo = self.criar(pessoas, ritmo=3)
        self.assertEqual(self.ciclo()["enviadas"], 3)
        self.assertEqual(self.ciclo(10)["enviadas"], 0)  # a janela de 1 minuto ainda está cheia
        self.assertEqual(self.ciclo(59)["enviadas"], 0)
        self.assertEqual(self.ciclo(61)["enviadas"], 2)
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "concluido")

    def test_ritmo_menor_do_disparo_vale_mesmo_com_a_conexao_folgada(self):
        pessoas = [candidato(f"Pessoa {i}", 100 + i, ok=True, vaga=self.vaga) for i in range(3)]
        self.criar(pessoas, ritmo=1)
        self.assertEqual(self.ciclo()["enviadas"], 1)
        self.assertEqual(self.ciclo(30)["enviadas"], 0)
        self.assertEqual(self.ciclo(61)["enviadas"], 1)

    def test_limite_da_conexao_vale_para_todos_os_disparos_somados(self):
        conectar("simulador", limite=2)
        primeiro = self.criar([candidato("Pessoa A", 201, ok=True, vaga=self.vaga), candidato("Pessoa B", 202, ok=True, vaga=self.vaga)])
        segundo = self.criar([candidato("Pessoa C", 203, ok=True, vaga=self.vaga)])
        self.assertEqual(self.ciclo()["enviadas"], 2)
        self.assertEqual(primeiro.mensagens.filter(status=T.ENVIADA).count(), 2)
        self.assertEqual(segundo.mensagens.filter(status=T.FILA).count(), 1)
        self.assertEqual(self.ciclo(61)["enviadas"], 1)

    def test_maximo_por_ciclo(self):
        self.criar([self.ana, self.bia])
        self.assertEqual(processador.ciclo(agora=self.agora, maximo=1)["enviadas"], 1)

    def test_quem_recusou_depois_de_entrar_na_fila_nao_recebe(self):
        disparo = self.criar([self.ana, self.bia])
        Candidato.objects.filter(pk=self.ana.pk).update(whatsapp_ok=False)
        relatorio = self.ciclo()
        self.assertEqual((relatorio["enviadas"], relatorio["ignoradas"]), (1, 1))
        ana = self.status_de(self.ana, disparo)
        self.assertEqual((ana.status, ana.erro, ana.id_provedor), (T.IGNORADA, "Pediu para não receber mensagens", ""))
        self.assertEqual(self.status_de(self.bia, disparo).status, T.ENVIADA)

    def test_autorizacao_exigida_depois_e_telefone_alterado_tambem_bloqueiam(self):
        disparo = self.criar([self.ana, self.bia])
        ConexaoWhatsApp.objects.update(exigir_autorizacao=True)
        Candidato.objects.filter(pk=self.ana.pk).update(telefone=telefone_ficticio(9))
        self.ciclo()
        self.assertEqual(self.status_de(self.ana, disparo).erro, "Telefone do cadastro foi alterado")
        self.assertEqual(self.status_de(self.bia, disparo).erro, "Sem autorização registrada para WhatsApp")
        self.assertFalse(disparo.mensagens.filter(status=T.ENVIADA).exists())

    def test_cadastro_removido_nao_recebe(self):
        disparo = self.criar([self.ana])
        candidatos_services.anonimizar(self.ana, self.admin)
        self.ciclo()
        m = disparo.mensagens.get()
        self.assertEqual(m.status, T.IGNORADA)
        self.assertEqual((m.nome_destino, m.telefone, m.texto), ("Cadastro removido", "", ""))

    def test_falha_passageira_espera_e_tenta_de_novo(self):
        conectar("meta")
        disparo = self.criar([self.ana], self.modelo_oficial())
        limite = resposta(429, {"error": {"code": 130429, "message": "rate"}})
        self.responder(limite, limite, resposta(200, {"messages": [{"id": "wamid.OK"}]}))
        self.ciclo()
        m = disparo.mensagens.get()
        self.assertEqual((m.status, m.tentativas), (T.FILA, 1))
        self.assertEqual(m.proxima_tentativa_em, self.agora + timedelta(seconds=60))
        self.assertIn("Limite de vazão", m.erro)
        self.ciclo(30)  # ainda esperando
        self.assertEqual(self.rede.call_count, 1)
        self.ciclo(61)
        m.refresh_from_db()
        self.assertEqual((m.status, m.tentativas, self.rede.call_count), (T.FILA, 2, 2))
        self.assertEqual(m.proxima_tentativa_em, self.agora + timedelta(seconds=61 + 300))
        self.ciclo(61 + 301)
        m.refresh_from_db()
        self.assertEqual((m.status, m.tentativas, m.id_provedor, m.erro), (T.ENVIADA, 3, "wamid.OK", ""))

    def test_desiste_depois_de_tres_tentativas_e_a_equipe_pode_reenviar(self):
        conectar("meta")
        disparo = self.criar([self.ana], self.modelo_oficial())
        self.responder(resposta(503, None))
        for depois in (0, 61, 61 + 301):
            self.ciclo(depois)
        m = disparo.mensagens.get()
        self.assertEqual((m.status, m.tentativas, self.rede.call_count), (T.FALHOU, 3, 3))
        self.assertEqual(self.ciclo(2000)["enviadas"], 0)  # falhou de vez: o sistema não insiste sozinho
        self.responder(resposta(200, {"messages": [{"id": "wamid.DEPOIS"}]}))
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "concluido")
        servico.controlar(disparo, "reenviar", self.rec)
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "em_andamento")
        self.ciclo(3000)
        m.refresh_from_db()
        self.assertEqual((m.status, m.tentativas, m.id_provedor), (T.ENVIADA, 4, "wamid.DEPOIS"))

    def test_erro_definitivo_falha_na_hora(self):
        conectar("meta")
        disparo = self.criar([self.ana], self.modelo_oficial())
        self.responder(resposta(400, {"error": {"code": 132001, "message": "no template"}}))
        self.assertEqual(self.ciclo()["falhas"], 1)
        m = disparo.mensagens.get()
        self.assertEqual((m.status, m.tentativas), (T.FALHOU, 1))
        self.assertIn("Template inexistente", m.erro)

    def test_envio_pela_meta_leva_o_template_e_os_parametros_e_o_telefone_com_ddi(self):
        conectar("meta")
        self.criar([self.ana], self.modelo_oficial(template_oficial_idioma="pt_BR"))
        self.responder(resposta(200, {"messages": [{"id": "wamid.1"}]}))
        self.ciclo()
        corpo = self.rede.call_args.kwargs["json"]
        self.assertEqual(corpo["to"], "55" + self.ana.telefone)
        self.assertEqual(corpo["type"], "template")
        self.assertEqual(corpo["template"]["name"], "aprovacao_rpa")
        self.assertEqual([p["text"] for p in corpo["template"]["components"][0]["parameters"]], ["Ana", "Farmácia"])

    def test_excecao_do_provedor_vira_falha_passageira_sem_vazar_o_texto(self):
        self.criar([self.ana])
        with mock.patch.object(simulador.Simulador, "enviar", side_effect=RuntimeError("token-secreto-123")):
            self.ciclo()
        m = MensagemDisparo.objects.get(candidato=self.ana)
        self.assertEqual(m.status, T.FILA)
        self.assertIn("RuntimeError", m.erro)
        self.assertNotIn("token-secreto-123", m.erro)

    def test_falhas_seguidas_do_provedor_interrompem_o_ciclo(self):
        pessoas = [candidato(f"Pessoa {i}", 300 + i, ok=True, vaga=self.vaga) for i in range(6)]
        self.criar(pessoas)
        with mock.patch.object(simulador.Simulador, "enviar", side_effect=RuntimeError("x")) as enviar:
            self.ciclo()
        self.assertEqual(enviar.call_count, processador.FALHAS_SEGUIDAS_PARA_PARAR)

    def test_pausar_retomar_e_cancelar(self):
        disparo = self.criar([self.ana, self.bia])
        servico.controlar(disparo, "pausar", self.rec)
        self.assertEqual(self.ciclo()["enviadas"], 0)
        with self.assertRaises(RegraDeNegocio):
            servico.controlar(disparo, "pausar", self.rec)
        servico.controlar(disparo, "retomar", self.rec)
        servico.controlar(disparo, "cancelar", self.rec)
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "cancelado")
        self.assertEqual(self.ciclo()["enviadas"], 0)
        self.assertEqual(set(disparo.mensagens.values_list("status", "erro")), {(T.IGNORADA, "Disparo cancelado")})
        for acao in ("pausar", "retomar", "cancelar", "reenviar", "inventada"):
            with self.assertRaises(RegraDeNegocio):
                servico.controlar(disparo, acao, self.rec)
        self.assertEqual({"disparo.pausado", "disparo.retomado", "disparo.cancelado"}, set(LogAtividade.objects.values_list("acao", flat=True)) - {"disparo.criado"})

    def test_conexao_fora_pausa_o_disparo_e_so_retoma_com_ela_de_volta(self):
        disparo = self.criar([self.ana])
        ConexaoWhatsApp.objects.update(status="erro")
        self.assertEqual(self.ciclo()["enviadas"], 0)
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "pausado")
        self.assertIn("conexão", disparo.motivo_pausa)
        with self.assertRaisesMessage(RegraDeNegocio, "não está pronta"):
            servico.controlar(disparo, "retomar", self.rec)
        ConexaoWhatsApp.objects.update(status="conectada")
        servico.controlar(disparo, "retomar", self.rec)
        self.assertEqual(self.ciclo(5)["enviadas"], 1)

    def test_credenciais_ilegiveis_pausam_o_disparo(self):
        conectar("meta")
        disparo = self.criar([self.ana], self.modelo_oficial())
        with override_settings(SECRET_KEY="a-chave-mudou"):
            self.ciclo()
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "pausado")
        self.assertIn("credenciais", disparo.motivo_pausa)
        self.rede.assert_not_called()

    def test_disparo_sem_template_pausa_quando_a_conexao_passa_a_exigir(self):
        disparo = self.criar([self.ana])  # criado no Simulador, sem template
        conectar("meta")
        self.ciclo()
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "pausado")
        self.assertIn("template", disparo.motivo_pausa)
        self.rede.assert_not_called()

    def test_mensagem_presa_em_enviando_volta_para_a_fila(self):
        disparo = self.criar([self.ana, self.bia])
        antigo = self.agora - timedelta(minutes=10)
        MensagemDisparo.objects.filter(candidato=self.ana).update(status=T.ENVIANDO, tentativas=1, atualizado_em=antigo)
        MensagemDisparo.objects.filter(candidato=self.bia).update(status=T.ENVIANDO, tentativas=3, atualizado_em=antigo)
        self.ciclo()
        self.assertEqual(self.status_de(self.ana, disparo).status, T.ENVIADA)
        bia = self.status_de(self.bia, disparo)
        self.assertEqual((bia.status, bia.erro), (T.FALHOU, "Sem confirmação do provedor após várias tentativas."))

    def test_enviando_recente_nao_e_mexido(self):
        disparo = self.criar([self.ana])
        MensagemDisparo.objects.update(status=T.ENVIANDO, tentativas=1, atualizado_em=timezone.now())
        self.ciclo()
        self.assertEqual(disparo.mensagens.get().status, T.ENVIANDO)

    def test_reenviar_falhas_respeita_o_limite_manual(self):
        disparo = self.criar([self.ana, self.bia])
        self.ciclo()
        MensagemDisparo.objects.filter(candidato=self.ana).update(status=T.FALHOU, tentativas=3, erro="x")
        MensagemDisparo.objects.filter(candidato=self.bia).update(status=T.FALHOU, tentativas=5, erro="x")
        self.assertEqual(servico.contagens(disparo)["reenviaveis"], 1)
        servico.controlar(disparo, "reenviar", self.rec)
        self.assertEqual(self.status_de(self.ana, disparo).status, T.FILA)
        self.assertEqual(self.status_de(self.bia, disparo).status, T.FALHOU)
        with self.assertRaisesMessage(RegraDeNegocio, "Não há falhas"):
            MensagemDisparo.objects.filter(candidato=self.ana).update(status=T.ENVIADA)
            servico.controlar(disparo, "reenviar", self.rec)


class ComandoTests(Base):
    def test_uma_rodada_do_comando_disparar(self):
        disparo = self.criar([self.ana, self.bia])
        saida = StringIO()
        call_command("disparar", stdout=saida)
        self.assertIn("2 enviada(s)", saida.getvalue())
        self.assertEqual(disparo.mensagens.filter(status=T.ENVIADA).count(), 2)
        silencio = StringIO()
        call_command("disparar", stdout=silencio)  # nada a fazer: não escreve nada
        self.assertEqual(silencio.getvalue(), "")

    def test_com_localizar_o_worker_tambem_cuida_do_mapa(self):
        with mock.patch("core.management.commands.geocodificar.geo.aplicar_localizacao") as localizar:
            call_command("disparar", "--localizar", stdout=StringIO())
        self.assertTrue(localizar.called)  # candidatos criados com CEP ficam "aguardando localização"
        self.rede.assert_not_called()

    def test_o_servico_worker_roda_o_comando_certo(self):
        compose = (__import__("pathlib").Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"manage.py", "disparar", "--continuo", "--localizar"', compose)


class EventosRegrasTests(SimpleTestCase):
    def test_status_so_avancam(self):
        casos = [
            ("enviada", "entregue", "entregue"), ("enviada", "lida", "lida"), ("entregue", "lida", "lida"),
            ("lida", "entregue", None), ("entregue", "enviada", None), ("lida", "falhou", None), ("entregue", "falhou", None),
            ("enviada", "falhou", "falhou"), ("falhou", "entregue", "entregue"), ("falhou", "lida", "lida"), ("falhou", "enviada", None),
            ("falhou", "falhou", None), ("ignorada", "entregue", None), ("ignorada", "falhou", None), ("enviada", "enviada", None),
            ("enviada", "inventado", None),
        ]
        for atual, novo, esperado in casos:
            with self.subTest(atual=atual, novo=novo):
                self.assertEqual(eventos.proximo_status(atual, novo), esperado)

    def test_palavras_de_saida(self):
        for texto in ("SAIR", "sair", " Sair! ", "Sair.", "PARAR", "stop", "Descadastrar", "remover", "PARAR PROMOÇÕES", "Stop promotions"):
            self.assertTrue(eventos.pediu_saida(texto), texto)
        for texto in ("", "quero sair do grupo", "não quero sair", "sairei amanhã", "cancelar", "sim", None):
            self.assertFalse(eventos.pediu_saida(texto), texto)

    def test_variantes_do_telefone(self):
        self.assertEqual(eventos.variantes_do_telefone("5511988887777"), ["11988887777"])
        self.assertEqual(eventos.variantes_do_telefone("551188887777"), ["1188887777", "11988887777"])
        self.assertEqual(eventos.variantes_do_telefone("551133334444"), ["1133334444"])
        self.assertEqual(eventos.variantes_do_telefone("+55 (11) 98888-7777"), ["11988887777"])
        self.assertEqual(eventos.variantes_do_telefone("12345"), [])
        self.assertEqual(eventos.variantes_do_telefone(""), [])


class AnonimizacaoTests(Base):
    def test_remover_dados_pessoais_limpa_as_mensagens_automaticas(self):
        disparo = self.criar([self.ana, self.bia])
        self.ciclo(0)  # as duas já foram enviadas
        MensagemDisparo.objects.filter(candidato=self.ana).update(status=T.FILA)
        MensagemRecebida.objects.create(telefone=self.ana.telefone, texto="Oi, sou a Ana", candidato=self.ana)
        candidatos_services.anonimizar(self.ana, self.admin)
        ana = disparo.mensagens.get(candidato=self.ana)
        self.assertEqual((ana.nome_destino, ana.telefone, ana.texto, ana.parametros, ana.status), ("Cadastro removido", "", "", [], T.IGNORADA))
        recebida = MensagemRecebida.objects.get()
        self.assertEqual((recebida.telefone, recebida.texto), ("", ""))
        bia = disparo.mensagens.get(candidato=self.bia)
        self.assertEqual(bia.nome_destino, "Bia Prado")  # só a pessoa que pediu é afetada


class TelasDisparoTests(Base):
    def test_lista_e_detalhe(self):
        disparo = self.criar()
        self.ciclo()
        self.client.force_login(self.rec)
        lista = self.client.get(reverse("mensagens:disparos"))
        self.assertContains(lista, disparo.nome)
        self.assertContains(lista, "Concluído")
        detalhe = self.client.get(reverse("mensagens:disparo", args=[disparo.pk]))
        self.assertContains(detalhe, "Ana Lima")
        self.assertContains(detalhe, "Pediu para não receber mensagens")
        self.assertNotContains(detalhe, 'http-equiv="refresh"')  # concluído: não precisa atualizar sozinho
        filtrado = self.client.get(reverse("mensagens:disparo", args=[disparo.pk]), {"status": "ignorada"})
        self.assertNotContains(filtrado, "Ana Lima")
        self.assertContains(filtrado, "Caio Reis")
        self.assertEqual(self.client.get(reverse("mensagens:disparo", args=[9999])).status_code, 404)

    def test_disparo_em_andamento_se_atualiza_e_os_botoes_funcionam(self):
        disparo = self.criar([self.ana, self.bia])
        self.client.force_login(self.rec)
        detalhe = self.client.get(reverse("mensagens:disparo", args=[disparo.pk]))
        self.assertContains(detalhe, 'http-equiv="refresh"')
        self.assertContains(detalhe, "Pausar")
        controle = reverse("mensagens:disparo_controle", args=[disparo.pk])
        self.client.post(controle, {"acao": "pausar"})
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "pausado")
        self.assertContains(self.client.get(reverse("mensagens:disparo", args=[disparo.pk])), "Retomar")
        pagina = self.client.post(controle, {"acao": "pausar"}, follow=True)  # já pausado: avisa em vez de quebrar
        self.assertContains(pagina, "Só é possível pausar")
        self.client.post(controle, {"acao": "cancelar"})
        disparo.refresh_from_db()
        self.assertEqual(disparo.status, "cancelado")
        self.assertEqual(self.client.get(controle).status_code, 405)

    def test_gestor_nao_acessa_o_detalhe_nem_os_controles(self):
        disparo = self.criar([self.ana])
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("mensagens:disparo", args=[disparo.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("mensagens:disparo_controle", args=[disparo.pk]), {"acao": "cancelar"}).status_code, 403)


class ModeloOficialFormTests(Base):
    def dados(self, **extra):
        base = {"nome": "Oficial", "categoria": "geral", "texto": "Olá {{primeiro_nome}}", "template_oficial_nome": "", "template_oficial_idioma": "", "template_oficial_variaveis": "", "ativo": "on"}
        return {**base, **extra}

    def test_salva_o_template_e_a_ordem_dos_campos(self):
        self.client.force_login(self.rec)
        self.client.post(reverse("mensagens:modelo_novo"), self.dados(
            template_oficial_nome="convocacao_rpa", template_oficial_variaveis="primeiro_nome, {{vaga}} data_fim",
        ))
        modelo = ModeloMensagem.objects.get(nome="Oficial")
        self.assertEqual((modelo.template_oficial_nome, modelo.template_oficial_idioma), ("convocacao_rpa", "pt_BR"))
        self.assertEqual(modelo.template_oficial_variaveis, ["primeiro_nome", "vaga", "data_fim"])
        editar = self.client.get(reverse("mensagens:modelo_editar", args=[modelo.pk]))
        self.assertContains(editar, 'value="primeiro_nome, vaga, data_fim"')

    def test_validacoes(self):
        self.client.force_login(self.rec)
        for extra, trecho in [
            ({"template_oficial_nome": "Convocação RPA"}, "minúsculas, números e _"),
            ({"template_oficial_nome": "ok", "template_oficial_idioma": "portugues"}, "formato pt_BR"),
            ({"template_oficial_nome": "ok", "template_oficial_variaveis": "primeiro_nome, inventado"}, "desconhecido"),
            ({"template_oficial_variaveis": "vaga"}, "Informe o nome do template"),
            ({"template_oficial_nome": "ok", "template_oficial_variaveis": ",".join(["vaga"] * 11)}, "No máximo 10"),
        ]:
            with self.subTest(extra=extra):
                self.assertContains(self.client.post(reverse("mensagens:modelo_novo"), self.dados(**extra)), trecho)
        self.assertFalse(ModeloMensagem.objects.filter(nome="Oficial").exists())

    def test_modelo_sem_template_continua_valendo_como_antes(self):
        self.client.force_login(self.rec)
        self.client.post(reverse("mensagens:modelo_novo"), self.dados())
        modelo = ModeloMensagem.objects.get(nome="Oficial")
        self.assertEqual((modelo.template_oficial_nome, modelo.template_oficial_variaveis), ("", []))
