"""Dados fictícios para demonstrar o sistema. Tudo aqui é inventado: nomes, CPFs, telefones e endereços."""
import random
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from candidatos.models import Candidato, Importacao, LinkCadastro
from contas.models import Usuario
from core.constantes import PERFIL_ADMIN, PERFIL_GESTOR, PERFIL_RECRUTADOR, PERFIL_VISUALIZADOR
from core.fabrica import cpf_valido, telefone_ficticio
from core.models import Configuracao, GeoCache, LogAtividade
from core.utils import calcular_periodo, meses_do_periodo, somar_meses, ultimo_dia_do_mes
from mensagens.models import EnvioMensagem, ModeloMensagem
from vagas.models import Vaga
from vinculos.models import Checkin, Vinculo
from whatsapp.models import ConexaoWhatsApp

USUARIOS = [
    ("admin", "Helena", "Duarte", PERFIL_ADMIN),
    ("recrutadora", "Marina", "Lopes", PERFIL_RECRUTADOR),
    ("recrutador.rafael", "Rafael", "Teixeira", PERFIL_RECRUTADOR),
    ("gestor.farmacia", "Paulo", "Ribeiro", PERFIL_GESTOR),
    ("gestor.rouparia", "Sandra", "Nogueira", PERFIL_GESTOR),
    ("gestor.administrativo", "Carlos", "Mendes", PERFIL_GESTOR),
    ("visualizador", "Visitante", "Convidado", PERFIL_VISUALIZADOR),
]

# nome, setor, vagas, ocupadas agora, responsável
VAGAS = [
    ("Auxiliar administrativo", "Administrativo", 10, 8, "gestor.administrativo"),
    ("Rouparia", "Apoio hospitalar", 6, 4, "gestor.rouparia"),
    ("Almoxarifado", "Suprimentos", 5, 3, "gestor.farmacia"),
    ("Farmácia", "Assistência farmacêutica", 8, 6, "gestor.farmacia"),
    ("NAF", "NAF", 6, 3, "gestor.administrativo"),
    ("ASG", "Serviços gerais", 12, 10, "gestor.rouparia"),
    ("Secretárias", "Secretaria", 5, 3, "gestor.administrativo"),
    ("PCD", "Inclusão", 4, 2, "gestor.administrativo"),
]

# bairro, cidade, UF, latitude, longitude, prefixo do CEP, peso (mais perto da unidade = mais candidatos)
TERRITORIOS = [
    ("Bela Vista", "São Paulo", "SP", -23.5547, -46.6469, "01311", 10),
    ("Consolação", "São Paulo", "SP", -23.5551, -46.6602, "01302", 8),
    ("Liberdade", "São Paulo", "SP", -23.5586, -46.6359, "01503", 8),
    ("Vila Mariana", "São Paulo", "SP", -23.5890, -46.6345, "04101", 8),
    ("Pinheiros", "São Paulo", "SP", -23.5670, -46.6921, "05422", 6),
    ("Mooca", "São Paulo", "SP", -23.5578, -46.5985, "03103", 7),
    ("Tatuapé", "São Paulo", "SP", -23.5405, -46.5764, "03071", 6),
    ("Santana", "São Paulo", "SP", -23.5006, -46.6258, "02012", 5),
    ("Lapa", "São Paulo", "SP", -23.5231, -46.7019, "05072", 5),
    ("Penha", "São Paulo", "SP", -23.5256, -46.5434, "03633", 4),
    ("Itaquera", "São Paulo", "SP", -23.5452, -46.4571, "08220", 4),
    ("Santo Amaro", "São Paulo", "SP", -23.6527, -46.7099, "04743", 3),
    ("Jabaquara", "São Paulo", "SP", -23.6619, -46.6409, "04329", 4),
    ("Campo Limpo", "São Paulo", "SP", -23.6310, -46.7587, "05788", 3),
    ("Freguesia do Ó", "São Paulo", "SP", -23.4986, -46.6925, "02910", 3),
    ("São Miguel Paulista", "São Paulo", "SP", -23.4942, -46.4440, "08010", 2),
    ("Centro", "Guarulhos", "SP", -23.4629, -46.5333, "07010", 3),
    ("Centro", "Osasco", "SP", -23.5329, -46.7917, "06016", 2),
    ("Centro", "São Caetano do Sul", "SP", -23.6229, -46.5548, "09510", 2),
    ("Centro", "Santo André", "SP", -23.6639, -46.5383, "09010", 2),
]
RUAS = ["Rua das Acácias", "Avenida Brasil", "Rua Voluntários da Pátria", "Rua São Joaquim", "Avenida Central", "Rua Sete de Setembro",
        "Rua das Palmeiras", "Rua XV de Novembro", "Avenida dos Estados", "Rua Dom Pedro II", "Rua Santa Clara", "Rua do Comércio"]
FEMININOS = ["Ana", "Beatriz", "Camila", "Daniela", "Eduarda", "Fernanda", "Gabriela", "Helena", "Isabela", "Juliana", "Karina", "Larissa",
             "Mariana", "Natália", "Patrícia", "Rafaela", "Sabrina", "Tatiane", "Vanessa", "Aline", "Bruna", "Cláudia", "Débora", "Elaine",
             "Franciele", "Giovana", "Jéssica", "Letícia", "Michele", "Priscila"]
MASCULINOS = ["Anderson", "Bruno", "Carlos", "Diego", "Eduardo", "Felipe", "Gustavo", "Henrique", "Igor", "João", "Kleber", "Leandro",
              "Marcos", "Nelson", "Otávio", "Paulo", "Rodrigo", "Sérgio", "Thiago", "Vinícius", "Wesley", "André", "Caio", "Davi", "Everton",
              "Fábio", "Guilherme", "Jonas", "Lucas", "Mateus"]
SOBRENOMES = ["Almeida", "Barbosa", "Cardoso", "Dias", "Esteves", "Ferreira", "Gomes", "Lima", "Machado", "Nascimento", "Oliveira", "Pereira",
              "Queiroz", "Ribeiro", "Santos", "Teixeira", "Vieira", "Moreira", "Rocha", "Araújo", "Batista", "Castro", "Duarte", "Freitas",
              "Gonçalves", "Lopes", "Martins", "Nogueira", "Pinto", "Ramos", "Silva", "Souza", "Costa", "Carvalho", "Azevedo", "Barros"]
INDICADORES = [("recrutadora", 40), ("recrutador.rafael", 22), ("admin", 8), ("gestor.farmacia", 16), ("gestor.rouparia", 10), ("gestor.administrativo", 4)]


def aware(data, hora=9, minuto=0):
    return timezone.make_aware(datetime.combine(data, time(hora, minuto)))


class Command(BaseCommand):
    help = "Carrega dados fictícios de demonstração (só se o sistema ainda não tem candidatos)."

    def add_arguments(self, parser):
        parser.add_argument("--forcar", action="store_true", help="Cria mesmo que já existam candidatos.")

    @transaction.atomic
    def handle(self, *args, **opcoes):
        if Candidato.objects.exists() and not opcoes["forcar"]:
            self.stdout.write("Já existem candidatos; nada a fazer (use --forcar para criar mesmo assim).")
            return
        self.rng = random.Random(2026)
        self.hoje = timezone.localdate()
        self.mes = self.hoje.replace(day=1)
        self.config = Configuracao.atual()
        self.usados_cpf, self.contador = set(), 0

        self.configurar_unidade()
        self.conectar_simulador()
        self.criar_usuarios()
        self.criar_vagas()
        self.criar_links_pessoais()
        self.criar_candidatos_e_vinculos()
        self.criar_importacoes_e_mensagens()
        self.criar_atividades()
        self.stdout.write(self.style.SUCCESS(
            f"Demonstração pronta: {Candidato.objects.count()} candidatos, {Vinculo.objects.count()} vínculos, "
            f"{Checkin.objects.count()} check-ins. Senha dos acessos de teste: {settings.RRPA_DEMO_SENHA}"
        ))

    # ------------------------------------------------------------------ base
    def configurar_unidade(self):
        c = self.config
        c.nome_unidade = "Hospital Exemplo"
        c.cep_unidade, c.latitude, c.longitude = "01310100", -23.5614, -46.6559
        c.endereco_unidade = "Avenida Paulista, Bela Vista, São Paulo"
        c.prazo_meses, c.dias_alerta = 3, 30
        c.save()

    def conectar_simulador(self):
        """Deixa o WhatsApp automático pronto para experimentar: o Simulador não envia nada de verdade."""
        if not settings.RRPA_WHATSAPP_API:
            return
        conexao = ConexaoWhatsApp.atual()
        if conexao.credenciais or conexao.status != ConexaoWhatsApp.Status.PENDENTE:
            return
        conexao.provedor = ConexaoWhatsApp.Provedor.SIMULADOR
        conexao.guardar_credenciais({"taxa_falhas": "0,05"})
        conexao.status, conexao.numero_exibido, conexao.testada_em = ConexaoWhatsApp.Status.CONECTADA, "Simulador", timezone.now()
        conexao.save()

    def criar_usuarios(self):
        self.usuarios = {}
        for login, nome, sobrenome, perfil in USUARIOS:
            u, _ = Usuario.objects.get_or_create(username=login, defaults={"first_name": nome, "last_name": sobrenome, "perfil": perfil})
            u.first_name, u.last_name, u.perfil, u.is_active = nome, sobrenome, perfil, True
            u.set_password(settings.RRPA_DEMO_SENHA)
            u.telefone = telefone_ficticio(len(self.usuarios) + 9000)
            u.save()
            self.usuarios[login] = u

    def criar_vagas(self):
        self.vagas = {}
        for nome, setor, qtd, _, responsavel in VAGAS:
            vaga, _ = Vaga.objects.update_or_create(nome=nome, defaults={"setor": setor, "quantidade": qtd, "ativa": True})
            vaga.responsaveis.set([self.usuarios[responsavel]])
            self.vagas[nome] = vaga

    def criar_links_pessoais(self):
        for u in self.usuarios.values():
            if u.pode_gerar_link:
                LinkCadastro.pessoal_de(u)

    # ------------------------------------------------------------------ pessoas
    def novo_candidato(self, status, vaga_interesse=None, origem=None):
        rng = self.rng
        self.contador += 1
        feminino = rng.random() < 0.62
        nome = f"{rng.choice(FEMININOS if feminino else MASCULINOS)} {rng.choice(SOBRENOMES)} {rng.choice(SOBRENOMES)}"
        while True:
            cpf = cpf_valido(rng.randrange(10**8, 10**9))
            if cpf not in self.usados_cpf:
                self.usados_cpf.add(cpf)
                break
        bairro, cidade, uf, lat, lng, prefixo, _ = rng.choices(TERRITORIOS, weights=[t[6] for t in TERRITORIOS])[0]
        cep = f"{prefixo}{rng.randrange(0, 1000):03d}"
        rua = rng.choice(RUAS)
        lat_p, lng_p = lat + rng.gauss(0, 0.0045), lng + rng.gauss(0, 0.0055)
        GeoCache.objects.update_or_create(cep=cep, defaults={
            "logradouro": rua, "bairro": bairro, "cidade": cidade, "uf": uf, "latitude": lat_p, "longitude": lng_p,
            "precisao": GeoCache.Precisao.ENDERECO, "fonte": "demo",
        })
        origem = origem or rng.choices(
            [Candidato.Origem.PLANILHA, Candidato.Origem.LINK_PESSOAL, Candidato.Origem.CONVITE, Candidato.Origem.MANUAL],
            weights=[52, 24, 14, 10],
        )[0]
        indicado = None
        if origem in (Candidato.Origem.LINK_PESSOAL, Candidato.Origem.CONVITE):
            login = rng.choices([i[0] for i in INDICADORES], weights=[i[1] for i in INDICADORES])[0]
            indicado = self.usuarios[login]
        criado_em = aware(self.hoje - timedelta(days=rng.randrange(2, 75)), rng.randrange(8, 18), rng.randrange(60))
        candidato = Candidato(
            nome=nome, cpf=cpf, telefone=telefone_ficticio(self.contador), cep=cep, logradouro=rua, numero=str(rng.randrange(12, 2400)),
            bairro=bairro, cidade=cidade, uf=uf, latitude=lat_p, longitude=lng_p, precisao_geo="ENDERECO",
            geo_status=Candidato.GeoStatus.OK, status=status, origem=origem, indicado_por=indicado,
            criado_por=None if indicado else self.usuarios["recrutadora"],
            observacoes="Dado fictício (demonstração).",
            consentimento_em=criado_em if indicado else None,
        )
        if status in (Candidato.Status.APROVADO, Candidato.Status.EM_VAGA, Candidato.Status.CONCLUIDO):
            candidato.aprovado_por = indicado or self.usuarios["recrutadora"]
            candidato.aprovado_em = criado_em + timedelta(days=1)
        candidato.save()
        Candidato.objects.filter(pk=candidato.pk).update(criado_em=criado_em)
        if vaga_interesse is not None:
            candidato.vagas_interesse.add(vaga_interesse)
        if origem == Candidato.Origem.CONVITE and indicado is not None:
            link = LinkCadastro.objects.create(
                tipo=LinkCadastro.Tipo.CONVITE, criado_por=indicado, vaga=vaga_interesse, nome_sugerido=nome,
                telefone_sugerido=candidato.telefone, aprovado_por=indicado, aprovado_em=criado_em - timedelta(days=1),
                expira_em=criado_em + timedelta(days=6), usado_em=criado_em, enviado_em=criado_em - timedelta(hours=20),
            )
            LinkCadastro.objects.filter(pk=link.pk).update(criado_em=criado_em - timedelta(days=1))
            Candidato.objects.filter(pk=candidato.pk).update(link=link)
        elif origem == Candidato.Origem.LINK_PESSOAL and indicado is not None:
            Candidato.objects.filter(pk=candidato.pk).update(link=LinkCadastro.pessoal_de(indicado))
        return candidato

    def criar_vinculo(self, candidato, vaga, meses_atras, status=Vinculo.Status.ATIVO, motivo="", encerra_apos_dias=None):
        rng = self.rng
        mes_chamada = somar_meses(self.mes, -meses_atras)
        maior_dia = self.hoje.day if meses_atras == 0 else 28
        data_chamada = mes_chamada.replace(day=rng.randrange(1, max(maior_dia, 1) + 1))
        inicio, fim = calcular_periodo(data_chamada, self.config.prazo_meses, self.config.contagem_prazo)
        vinculo = Vinculo.objects.create(
            candidato=candidato, vaga=vaga, data_chamada=data_chamada, inicio_periodo=inicio, data_fim=fim, status=status,
            criado_por=self.usuarios["recrutadora"],
        )
        Vinculo.objects.filter(pk=vinculo.pk).update(criado_em=aware(data_chamada, 10))
        if status != Vinculo.Status.ATIVO:
            fim_real = min(fim, data_chamada + timedelta(days=encerra_apos_dias)) if encerra_apos_dias else fim
            vinculo.motivo_encerramento = motivo
            vinculo.encerrado_em = aware(min(fim_real + timedelta(days=2), self.hoje), 16)
            vinculo.encerrado_por = self.usuarios["recrutadora"]
            vinculo.save()
        return vinculo

    def criar_checkins(self, vinculo, deixar_atrasado=False, adiantar_mes_atual=False):
        rng = self.rng
        gestor = vinculo.vaga.responsaveis.first()
        competencias = vinculo.competencias_exigidas()
        for i, competencia in enumerate(competencias):
            if competencia > self.mes:
                continue
            if competencia == self.mes and not adiantar_mes_atual:
                continue
            if deixar_atrasado and i == 0 and competencia < self.mes:
                continue
            sorteio = rng.random()
            resultado, observacao = Checkin.Resultado.CUMPRIU, ""
            if sorteio > 0.94:
                resultado, observacao = Checkin.Resultado.NAO_CUMPRIU, "Faltou em vários dias sem aviso."
            elif sorteio > 0.85:
                resultado, observacao = Checkin.Resultado.PARCIAL, "Faltou dois dias; apresentou atestado."
            fim_mes = min(ultimo_dia_do_mes(competencia.year, competencia.month) + timedelta(days=rng.randrange(1, 4)), self.hoje)
            Checkin.objects.create(vinculo=vinculo, competencia=competencia, resultado=resultado, observacao=observacao,
                                   registrado_por=gestor, registrado_em=aware(fim_mes, 15))

    def criar_candidatos_e_vinculos(self):
        rng = self.rng
        # 1) ocupando vagas agora: coortes de meses atrás (0, 1 e 2); a de 2 meses vence neste mês
        for nome, _, _, ocupadas, _ in VAGAS:
            vaga = self.vagas[nome]
            for _ in range(ocupadas):
                meses_atras = rng.choices([2, 1, 0], weights=[22, 40, 38])[0]
                pessoa = self.novo_candidato(Candidato.Status.EM_VAGA, vaga)
                vinculo = self.criar_vinculo(pessoa, vaga, meses_atras)
                self.criar_checkins(vinculo, deixar_atrasado=(meses_atras == 2 and rng.random() < 0.3),
                                    adiantar_mes_atual=rng.random() < 0.25)
        # 2) prazo vencido, aguardando a baixa da equipe
        for nome in ("ASG", "Farmácia", "Auxiliar administrativo"):
            vaga = self.vagas[nome]
            pessoa = self.novo_candidato(Candidato.Status.EM_VAGA, vaga)
            self.criar_checkins(self.criar_vinculo(pessoa, vaga, 3))
        # 3) histórico: concluídos e encerrados antes do prazo
        for nome in rng.choices(list(self.vagas), k=14):
            vaga = self.vagas[nome]
            pessoa = self.novo_candidato(Candidato.Status.CONCLUIDO, vaga)
            vinculo = self.criar_vinculo(pessoa, vaga, rng.randrange(4, 8), Vinculo.Status.CONCLUIDO, Vinculo.Motivo.PRAZO_CUMPRIDO)
            self.criar_checkins(vinculo)
        for nome in rng.choices(list(self.vagas), k=4):
            vaga = self.vagas[nome]
            pessoa = self.novo_candidato(Candidato.Status.DESISTENTE, vaga)
            vinculo = self.criar_vinculo(pessoa, vaga, rng.randrange(4, 6), Vinculo.Status.ENCERRADO, Vinculo.Motivo.DESISTENCIA, encerra_apos_dias=40)
            self.criar_checkins(vinculo)
        # 4) banco de candidatos: aprovados esperando vaga, aguardando aprovação, não aprovados e desistentes
        nomes = list(self.vagas)
        pesos = [4 if v[2] > v[3] else 1 for v in VAGAS]
        for _ in range(24):
            self.novo_candidato(Candidato.Status.APROVADO, self.vagas[rng.choices(nomes, weights=pesos)[0]])
        for _ in range(28):
            self.novo_candidato(Candidato.Status.PENDENTE, self.vagas[rng.choice(nomes)])
        for _ in range(6):
            self.novo_candidato(Candidato.Status.REPROVADO, self.vagas[rng.choice(nomes)])
        for _ in range(3):
            self.novo_candidato(Candidato.Status.DESISTENTE, self.vagas[rng.choice(nomes)])

        # convites ainda sem cadastro: um ativo, um vencido e um desativado
        agora = timezone.now()
        recrutadora = self.usuarios["recrutadora"]
        for nome, tel, vaga, expira, revogado in [
            ("Luciana Prado Reis", 7001, "Farmácia", agora + timedelta(days=5), None),
            ("Roberto Chaves Lima", 7002, "ASG", agora + timedelta(days=2), None),
            ("Simone Faria Dias", 7003, "Rouparia", agora - timedelta(days=3), None),
            ("Tiago Borges Melo", 7004, "Almoxarifado", agora + timedelta(days=4), agora - timedelta(days=1)),
        ]:
            link = LinkCadastro.objects.create(
                tipo=LinkCadastro.Tipo.CONVITE, criado_por=recrutadora, vaga=self.vagas[vaga], nome_sugerido=nome,
                telefone_sugerido=telefone_ficticio(tel), aprovado_por=recrutadora, aprovado_em=agora - timedelta(days=2),
                expira_em=expira, revogado_em=revogado, enviado_em=agora - timedelta(days=1),
            )
            LinkCadastro.objects.filter(pk=link.pk).update(criado_em=agora - timedelta(days=2))

    # ------------------------------------------------------------------ rastro de uso
    def criar_importacoes_e_mensagens(self):
        recrutadora = self.usuarios["recrutadora"]
        for nome_vaga, arquivo, total, dias in [("ASG", "candidatos-asg.xlsx", 31, 41), ("Farmácia", "candidatos-farmacia.xlsx", 18, 23)]:
            imp = Importacao.objects.create(
                vaga=self.vagas[nome_vaga], usuario=recrutadora, nome_arquivo=arquivo, status=Importacao.Status.CONFIRMADA, dados=[],
                total=total, novas=total - 3, duplicadas=2, erros=1, criadas=total - 3, vinculadas=2,
                confirmada_em=aware(self.hoje - timedelta(days=dias), 11),
            )
            Importacao.objects.filter(pk=imp.pk).update(criado_em=aware(self.hoje - timedelta(days=dias), 10))
        modelos = list(ModeloMensagem.objects.all())
        if not modelos:
            return
        alvo = list(Candidato.objects.filter(status__in=[Candidato.Status.EM_VAGA, Candidato.Status.APROVADO])[:10])
        for i, pessoa in enumerate(alvo):
            envio = EnvioMensagem.objects.create(
                candidato=pessoa, modelo=modelos[i % len(modelos)], usuario=recrutadora, nome_destino=pessoa.nome, telefone=pessoa.telefone,
                texto=f"Olá, {pessoa.nome.split()[0]}! (mensagem de demonstração)",
            )
            EnvioMensagem.objects.filter(pk=envio.pk).update(criado_em=timezone.now() - timedelta(hours=3 * (i + 1)))

    def criar_atividades(self):
        agora = timezone.now()
        pessoas = list(Candidato.objects.filter(status=Candidato.Status.EM_VAGA).order_by("-id")[:5])
        aprovados = list(Candidato.objects.filter(status=Candidato.Status.APROVADO).order_by("-id")[:3])
        eventos = []
        for i, p in enumerate(pessoas):
            v = p.vinculos.first()
            eventos.append(("vinculo.criado", f"Chamou {p.nome} para {v.vaga.nome} (permanência até {v.data_fim:%d/%m/%Y})", "recrutadora", 2 + 7 * i))
        for i, p in enumerate(aprovados):
            eventos.append(("candidato.aprovado", f"Aprovou {p.nome}", "recrutador.rafael", 20 + 11 * i))
        eventos += [
            ("checkin.registrado", "Check-in do mês anterior registrado para um colaborador de Farmácia", "gestor.farmacia", 30),
            ("candidato.importado", "Importou 28 candidatos para ASG da planilha candidatos-asg.xlsx", "recrutadora", 55),
            ("link.convite", "Gerou um convite de cadastro para Luciana Prado Reis", "recrutadora", 75),
        ]
        for acao, texto, login, minutos in eventos:
            registro = LogAtividade.objects.create(usuario=self.usuarios[login], acao=acao, descricao=texto[:300])
            LogAtividade.objects.filter(pk=registro.pk).update(criado_em=agora - timedelta(minutes=minutos))
