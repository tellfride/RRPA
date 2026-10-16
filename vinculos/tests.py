from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from candidatos.models import Candidato
from contas.models import Usuario
from core.excecoes import RegraDeNegocio
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao
from core.utils import somar_meses, ultimo_dia_do_mes
from vagas.models import Vaga
from vinculos import apresentacao, services
from vinculos.models import Checkin, Vinculo


def novo_candidato(n, status=Candidato.Status.APROVADO):
    return Candidato.objects.create(
        nome=f"Pessoa {n}", cpf=cpf_valido(n), telefone=telefone_ficticio(n), cep="01310100", status=status,
    )


class BaseVinculoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Configuracao.atual()
        cls.rec = Usuario.objects.create_user("rec", password="x", perfil="recrutador")
        cls.gestor = Usuario.objects.create_user("ges", password="x", perfil="gestor")
        cls.outro_gestor = Usuario.objects.create_user("ges2", password="x", perfil="gestor")
        cls.vaga = Vaga.objects.create(nome="Farmácia", quantidade=2)
        cls.vaga.responsaveis.add(cls.gestor)
        cls.hoje = timezone.localdate()
        cls.mes = cls.hoje.replace(day=1)


class ChamarParaVagaTests(BaseVinculoTests):
    def test_chamar_define_prazo_de_tres_meses_e_muda_a_situacao(self):
        candidato = novo_candidato(1)
        vinculo = services.chamar_para_vaga(candidato, self.vaga, self.rec)
        self.assertEqual(vinculo.data_chamada, self.hoje)
        self.assertEqual(vinculo.inicio_periodo, self.mes)
        terceiro_mes = somar_meses(self.mes, 2)
        self.assertEqual(vinculo.data_fim, ultimo_dia_do_mes(terceiro_mes.year, terceiro_mes.month))
        self.assertEqual(len(vinculo.competencias()), 3)
        self.assertGreaterEqual(vinculo.data_fim, self.hoje)
        candidato.refresh_from_db()
        self.assertEqual(candidato.status, Candidato.Status.EM_VAGA)

    def test_nao_ultrapassa_a_quantidade_de_vagas(self):
        for n in (1, 2):
            services.chamar_para_vaga(novo_candidato(n), self.vaga, self.rec)
        with self.assertRaisesMessage(RegraDeNegocio, "Não há vaga disponível"):
            services.chamar_para_vaga(novo_candidato(3), self.vaga, self.rec)

    def test_vinculo_vencido_nao_ocupa_vaga(self):
        for n in (1, 2):
            Vinculo.objects.create(
                candidato=novo_candidato(n, Candidato.Status.EM_VAGA), vaga=self.vaga,
                data_chamada=self.hoje - timedelta(days=100), inicio_periodo=self.hoje - timedelta(days=100),
                data_fim=self.hoje - timedelta(days=1),
            )
        self.assertEqual(self.vaga.ocupadas(), 0)
        self.assertEqual(self.vaga.disponiveis(), 2)
        services.chamar_para_vaga(novo_candidato(3), self.vaga, self.rec)
        self.assertEqual(self.vaga.ocupadas(), 1)

    def test_so_chama_candidato_aprovado(self):
        with self.assertRaisesMessage(RegraDeNegocio, "aprovados"):
            services.chamar_para_vaga(novo_candidato(1, Candidato.Status.PENDENTE), self.vaga, self.rec)

    def test_candidato_nao_ocupa_duas_vagas(self):
        outra = Vaga.objects.create(nome="Rouparia", quantidade=3)
        candidato = novo_candidato(1)
        services.chamar_para_vaga(candidato, self.vaga, self.rec)
        candidato.status = Candidato.Status.APROVADO
        candidato.save()
        with self.assertRaisesMessage(RegraDeNegocio, "já ocupa uma vaga"):
            services.chamar_para_vaga(candidato, outra, self.rec)

    def test_nao_aceita_chamada_que_ja_nasce_vencida(self):
        antiga = somar_meses(self.hoje, -6)
        with self.assertRaisesMessage(RegraDeNegocio, "vencido"):
            services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec, data_chamada=antiga)

    def test_vaga_inativa_nao_recebe_chamada(self):
        self.vaga.ativa = False
        self.vaga.save()
        with self.assertRaisesMessage(RegraDeNegocio, "inativa"):
            services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)

    def test_contagem_anotada_na_vaga(self):
        services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        Vinculo.objects.create(
            candidato=novo_candidato(2, Candidato.Status.EM_VAGA), vaga=self.vaga,
            data_chamada=self.hoje - timedelta(days=100), inicio_periodo=self.hoje - timedelta(days=100),
            data_fim=self.hoje - timedelta(days=2),
        )
        vaga = Vaga.objects.com_ocupacao(self.hoje, 30).get(pk=self.vaga.pk)
        self.assertEqual((vaga.n_ocupadas, vaga.n_vencidos), (1, 1))


class EncerrarVinculoTests(BaseVinculoTests):
    def test_prazo_cumprido_conclui_e_libera_a_vaga(self):
        candidato = novo_candidato(1)
        vinculo = services.chamar_para_vaga(candidato, self.vaga, self.rec)
        services.encerrar_vinculo(vinculo, self.rec, Vinculo.Motivo.PRAZO_CUMPRIDO)
        vinculo.refresh_from_db()
        candidato.refresh_from_db()
        self.assertEqual(vinculo.status, "concluido")
        self.assertEqual(candidato.status, Candidato.Status.CONCLUIDO)
        self.assertEqual(self.vaga.ocupadas(), 0)
        with self.assertRaisesMessage(RegraDeNegocio, "já foi encerrado"):
            services.encerrar_vinculo(vinculo, self.rec, Vinculo.Motivo.OUTRO)

    def test_desistencia_encerra_antes_do_prazo(self):
        candidato = novo_candidato(1)
        vinculo = services.chamar_para_vaga(candidato, self.vaga, self.rec)
        services.encerrar_vinculo(vinculo, self.rec, Vinculo.Motivo.DESISTENCIA, "Mudou de cidade")
        vinculo.refresh_from_db()
        candidato.refresh_from_db()
        self.assertEqual(vinculo.status, "encerrado")
        self.assertEqual(candidato.status, Candidato.Status.DESISTENTE)


class CheckinTests(BaseVinculoTests):
    def vinculo_ha_dois_meses(self):
        return services.chamar_para_vaga(
            novo_candidato(1), self.vaga, self.rec, data_chamada=somar_meses(self.hoje, -2),
        )

    def test_so_o_responsavel_do_setor_valida(self):
        vinculo = services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        for intruso in (self.rec, self.outro_gestor):
            with self.assertRaisesMessage(RegraDeNegocio, "responsável"):
                services.registrar_checkin(vinculo, self.mes, "cumpriu", intruso)
        checkin, criado = services.registrar_checkin(vinculo, self.mes, "cumpriu", self.gestor)
        self.assertTrue(criado)
        self.assertEqual(checkin.registrado_por, self.gestor)

    def test_corrigir_o_check_in_nao_duplica(self):
        vinculo = services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        services.registrar_checkin(vinculo, self.mes, "cumpriu", self.gestor)
        _, criado = services.registrar_checkin(vinculo, self.mes, "parcial", self.gestor, "Faltou dois dias")
        self.assertFalse(criado)
        self.assertEqual(Checkin.objects.get().resultado, "parcial")

    def test_administrador_valida_qualquer_vaga(self):
        admin = Usuario.objects.create_user("adm", password="x", perfil="admin")
        vinculo = services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        _, criado = services.registrar_checkin(vinculo, self.mes, "cumpriu", admin)
        self.assertTrue(criado)

    def test_exige_observacao_quando_nao_cumpriu(self):
        vinculo = services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        with self.assertRaisesMessage(RegraDeNegocio, "Explique"):
            services.registrar_checkin(vinculo, self.mes, "nao_cumpriu", self.gestor)

    def test_nao_registra_mes_que_ainda_nao_comecou(self):
        vinculo = services.chamar_para_vaga(novo_candidato(1), self.vaga, self.rec)
        with self.assertRaisesMessage(RegraDeNegocio, "ainda não começou"):
            services.registrar_checkin(vinculo, somar_meses(self.mes, 1), "cumpriu", self.gestor)
        with self.assertRaisesMessage(RegraDeNegocio, "não faz parte"):
            services.registrar_checkin(vinculo, somar_meses(self.mes, -5), "cumpriu", self.gestor)

    def test_pendencias_distinguem_atrasado_de_em_andamento(self):
        self.vinculo_ha_dois_meses()
        pendencias = services.pendencias_checkin(self.gestor)
        self.assertEqual(
            [(p["competencia"], p["atrasado"]) for p in pendencias],
            [(somar_meses(self.mes, -2), True), (somar_meses(self.mes, -1), True), (self.mes, False)],
        )
        self.assertEqual(services.contar_pendencias_checkin(self.gestor), 3)

    def test_pendencias_so_aparecem_para_quem_pode_validar_aquela_vaga(self):
        self.vinculo_ha_dois_meses()
        self.assertEqual(services.contar_pendencias_checkin(self.outro_gestor), 0)
        self.assertEqual(services.contar_pendencias_checkin(self.rec), 0)

    def test_check_in_registrado_sai_da_lista_de_pendencias(self):
        vinculo = self.vinculo_ha_dois_meses()
        services.registrar_checkin(vinculo, somar_meses(self.mes, -2), "cumpriu", self.gestor)
        self.assertEqual(services.contar_pendencias_checkin(self.gestor), 2)

    def test_chips_mostram_estado_de_cada_mes(self):
        vinculo = self.vinculo_ha_dois_meses()
        services.registrar_checkin(vinculo, somar_meses(self.mes, -2), "cumpriu", self.gestor)
        vinculo = Vinculo.objects.prefetch_related("checkins").get(pk=vinculo.pk)
        estados = [c["estado"] for c in apresentacao.chips_checkin(vinculo)]
        self.assertEqual(estados, ["cumpriu", "atrasado", "pendente"])
