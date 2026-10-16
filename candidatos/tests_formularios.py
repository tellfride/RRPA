"""Campos com máscara (CPF, telefone, CEP): o limite do campo e o do navegador precisam caber o texto já mascarado.

Defeito que isto impede: o cadastro da equipe herdava max_length=11 (CPF) e 8 (CEP) do modelo; a máscara escreve
"529.982.247-25" e "01310-100", então o navegador cortava a digitação em 9 e 7 números, a busca do endereço (que só
dispara com 8 números) nunca rodava e o servidor também recusaria o texto mascarado.
"""
import re

from django.test import TestCase
from django.urls import reverse

from candidatos.forms import CadastroPublicoForm, CandidatoForm
from candidatos.models import Candidato
from candidatos.views_links import ConviteForm
from contas.models import Usuario
from core.fabrica import cpf_valido
from core.models import Configuracao

# texto completo que a máscara do navegador produz (static/js/app.js)
MASCARADOS = {"cpf": "529.982.247-25", "telefone": "(11) 91234-5678", "cep": "01310-100"}


def maxlength_renderizado(form, campo):
    html = str(form[campo])
    achado = re.search(r'maxlength="(\d+)"', html)
    return int(achado.group(1)) if achado else None


class LimiteDosCamposTests(TestCase):
    def test_nenhum_campo_com_mascara_corta_o_texto_mascarado(self):
        formularios = {"cadastro da equipe": CandidatoForm(), "cadastro público": CadastroPublicoForm(), "convite": ConviteForm()}
        verificados = 0
        for nome_form, form in formularios.items():
            for campo, mascarado in MASCARADOS.items():
                if campo not in form.fields:
                    continue
                with self.subTest(formulario=nome_form, campo=campo):
                    self.assertIn("data-mascara", str(form[campo]))
                    limite_navegador = maxlength_renderizado(form, campo)
                    self.assertTrue(limite_navegador is None or limite_navegador >= len(mascarado), f"maxlength={limite_navegador}")
                    limite_campo = form.fields[campo].max_length
                    self.assertTrue(limite_campo is None or limite_campo >= len(mascarado), f"max_length={limite_campo}")
                    verificados += 1
        self.assertEqual(verificados, 3 + 3 + 1)  # equipe e público (CPF, telefone, CEP) e o convite (telefone)

    def test_o_servidor_aceita_o_texto_mascarado_e_guarda_so_os_numeros(self):
        form = CandidatoForm(data={"nome": "Maria Souza", **MASCARADOS, "cep": "01310-100"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            (form.cleaned_data["cpf"], form.cleaned_data["telefone"], form.cleaned_data["cep"]), ("52998224725", "11912345678", "01310100"),
        )

    def test_telefone_colado_com_codigo_do_pais_ou_zero_e_normalizado(self):
        for colado in ("+55 (11) 91234-5678", "5511912345678", "011912345678", "0055 11 91234-5678"):
            with self.subTest(colado=colado):
                form = CandidatoForm(data={"nome": "Maria Souza", "cpf": MASCARADOS["cpf"], "telefone": colado, "cep": "01310-100"})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["telefone"], "11912345678")

    def test_cpf_ou_cep_incompletos_continuam_sendo_recusados(self):
        form = CandidatoForm(data={"nome": "Maria Souza", "cpf": "529.982.247", "telefone": "(11) 91234-5678", "cep": "01310-1"})
        self.assertFalse(form.is_valid())
        self.assertIn("cpf", form.errors)
        self.assertIn("cep", form.errors)


class CadastroDaEquipeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Configuracao.atual()
        cls.rec = Usuario.objects.create_user("rec", password="x", first_name="Marina", last_name="Lopes", perfil="recrutador")

    def dados(self, **extra):
        base = {"nome": "Maria Souza", **MASCARADOS, "logradouro": "Avenida Paulista", "numero": "1000", "bairro": "Bela Vista", "cidade": "São Paulo", "uf": "SP"}
        base.update(extra)
        return base

    def test_pagina_do_cadastro_deixa_digitar_o_cpf_e_o_cep_inteiros(self):
        self.client.force_login(self.rec)
        html = self.client.get(reverse("candidatos:novo")).content.decode()
        self.assertRegex(html, r'name="cpf" maxlength="14"')
        self.assertRegex(html, r'name="cep" maxlength="9"')
        self.assertRegex(html, r'name="telefone" maxlength="20"')
        self.assertIn("data-cep-status", html)  # onde a busca do endereço avisa o que encontrou

    def test_cadastra_com_os_campos_mascarados(self):
        self.client.force_login(self.rec)
        resposta = self.client.post(reverse("candidatos:novo"), self.dados())
        candidato = Candidato.objects.get()
        self.assertRedirects(resposta, reverse("candidatos:detalhe", args=[candidato.pk]))
        self.assertEqual((candidato.cpf, candidato.telefone, candidato.cep), ("52998224725", "11912345678", "01310100"))

    def test_edita_com_os_campos_mascarados(self):
        candidato = Candidato.objects.create(nome="Maria Souza", cpf=cpf_valido(1), telefone="11912345678", cep="01310100")
        self.client.force_login(self.rec)
        resposta = self.client.post(reverse("candidatos:editar", args=[candidato.pk]), self.dados(cep="04101-300", cpf="529.982.247-25"))
        self.assertRedirects(resposta, reverse("candidatos:detalhe", args=[candidato.pk]))
        candidato.refresh_from_db()
        self.assertEqual((candidato.cpf, candidato.cep), ("52998224725", "04101300"))

    def test_cpf_repetido_e_reconhecido_mesmo_com_mascara(self):
        Candidato.objects.create(nome="Ana Lima", cpf="52998224725", telefone="11912345678", cep="01310100")
        self.client.force_login(self.rec)
        pagina = self.client.post(reverse("candidatos:novo"), self.dados())
        self.assertContains(pagina, "já está cadastrado para Ana Lima")
        self.assertEqual(Candidato.objects.count(), 1)

    def test_formulario_publico_tambem_deixa_digitar_tudo(self):
        html = str(CadastroPublicoForm()["cpf"]) + str(CadastroPublicoForm()["cep"])
        self.assertIn('maxlength="14"', html)
        self.assertIn('maxlength="9"', html)
