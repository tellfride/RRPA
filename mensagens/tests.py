from django.test import TestCase
from django.urls import reverse

from candidatos import services
from candidatos.models import Candidato, LinkCadastro
from contas.models import Usuario
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao
from mensagens import render as r
from mensagens.models import EnvioMensagem, ModeloMensagem
from mensagens.padroes import criar_modelos_padrao
from vagas.models import Vaga


class RenderTests(TestCase):
    def test_campos_sao_trocados_e_os_desconhecidos_ficam_como_estao(self):
        texto, faltando = r.renderizar("Olá, {{primeiro_nome}}! Vaga: {{ vaga }}. {{inventado}}", {"primeiro_nome": "Maria", "vaga": "Farmácia"})
        self.assertEqual(texto, "Olá, Maria! Vaga: Farmácia. {{inventado}}")
        self.assertEqual(faltando, [])
        self.assertEqual(r.campos_desconhecidos("{{nome}} {{inventado}}"), ["inventado"])

    def test_campo_valido_sem_valor_e_avisado(self):
        texto, faltando = r.renderizar("Link: {{link_cadastro}} até {{validade_link}}", {})
        self.assertEqual(faltando, ["link_cadastro", "validade_link"])
        self.assertIn("{{link_cadastro}}", texto)

    def test_nao_executa_formato_python_nem_acessa_atributos(self):
        texto, _ = r.renderizar("{0.__class__} {nome.__class__} {{nome.__class__}}", {"nome": "x"})
        self.assertEqual(texto, "{0.__class__} {nome.__class__} {{nome.__class__}}")


class EnvioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Configuracao.atual()
        criar_modelos_padrao()
        cls.rec = Usuario.objects.create_user("rec", password="x", first_name="Marina", last_name="Lopes", perfil="recrutador")
        cls.gestor = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.vaga = Vaga.objects.create(nome="Farmácia", quantidade=2)
        cls.c = Candidato.objects.create(nome="Pedro Alves", cpf=cpf_valido(900), telefone=telefone_ficticio(9), cep="01310100", status="aprovado")
        cls.c.vagas_interesse.add(cls.vaga)

    def test_modelos_padrao_foram_criados_com_os_campos_certos(self):
        self.assertEqual(ModeloMensagem.objects.count(), 6)
        for modelo in ModeloMensagem.objects.all():
            self.assertEqual(r.campos_desconhecidos(modelo.texto), [], modelo.nome)

    def test_mensagem_vem_pronta_e_usa_o_link_de_quem_envia(self):
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:compor"), {"candidato": self.c.pk, "categoria": "convite_cadastro"})
        link = LinkCadastro.pessoal_de(self.rec)
        self.assertContains(pagina, "Olá, Pedro!")
        self.assertContains(pagina, "Marina Lopes")
        self.assertContains(pagina, "Farmácia")
        self.assertContains(pagina, link.token)  # o cadastro fica ligado a quem forneceu o link

    def test_convite_especifico_usa_o_proprio_link(self):
        link = services.criar_convite(self.rec, self.c.nome, self.c.telefone, candidato=self.c)
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:compor"), {"candidato": self.c.pk, "link": link.pk, "categoria": "convite_cadastro"})
        self.assertContains(pagina, link.token)

    def test_quem_pediu_para_nao_receber_fica_bloqueado(self):
        Candidato.objects.filter(pk=self.c.pk).update(whatsapp_ok=False)
        self.client.force_login(self.rec)
        pagina = self.client.get(reverse("mensagens:compor"), {"candidato": self.c.pk})
        self.assertContains(pagina, "pediu para não receber")
        fila = self.client.get(reverse("mensagens:fila"), {"ids": str(self.c.pk)})
        self.assertContains(fila, "Pediu para não receber")
        Candidato.objects.filter(pk=self.c.pk).update(whatsapp_ok=None)

    def test_registrar_guarda_o_que_foi_aberto_no_whatsapp(self):
        link = services.criar_convite(self.rec, self.c.nome, self.c.telefone, candidato=self.c)
        modelo = ModeloMensagem.objects.first()
        self.client.force_login(self.rec)
        resposta = self.client.post(reverse("mensagens:registrar"), {
            "candidato": self.c.pk, "link": link.pk, "modelo": modelo.pk, "texto": "Olá, Pedro! Teste.",
        })
        self.assertEqual(resposta.status_code, 204)
        envio = EnvioMensagem.objects.get()
        self.assertEqual((envio.usuario, envio.candidato, envio.texto), (self.rec, self.c, "Olá, Pedro! Teste."))
        link.refresh_from_db()
        self.assertIsNotNone(link.enviado_em)
        self.assertEqual(self.client.post(reverse("mensagens:registrar"), {"candidato": self.c.pk, "texto": ""}).status_code, 400)

    def test_gestor_nao_envia_mensagens(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("mensagens:compor"), {"candidato": self.c.pk}).status_code, 403)
        self.assertEqual(self.client.post(reverse("mensagens:registrar"), {"candidato": self.c.pk, "texto": "x"}).status_code, 403)

    def test_modelo_com_campo_desconhecido_e_recusado(self):
        self.client.force_login(self.rec)
        resposta = self.client.post(reverse("mensagens:modelo_novo"), {
            "nome": "Teste", "categoria": "geral", "texto": "Olá {{primeiro_nome}} {{xyz}}", "ativo": "on",
        })
        self.assertContains(resposta, "desconhecido")
        self.assertFalse(ModeloMensagem.objects.filter(nome="Teste").exists())
