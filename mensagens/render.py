"""Preenche os campos {{nome}}, {{vaga}}, {{link_cadastro}}... dos modelos de mensagem.

Não usa str.format: o texto dos modelos é digitado por usuários, e só os campos desta lista são aceitos.
"""
import re

from django.utils import timezone

from core.utils import primeiro_nome, texto_prazo, url_absoluta

# (campo, descrição, exemplo)
CAMPOS = [
    ("nome", "Nome completo", "Maria Souza Lima"),
    ("primeiro_nome", "Primeiro nome", "Maria"),
    ("vaga", "Nome da vaga", "Farmácia"),
    ("setor", "Setor da vaga", "Assistência farmacêutica"),
    ("unidade", "Nome da unidade", "Hospital Exemplo"),
    ("recrutador", "Quem está enviando", "Marina Lopes"),
    ("responsavel_setor", "Responsável do setor", "Paulo Ribeiro"),
    ("data_chamada", "Data da chamada", "15/10/2026"),
    ("data_fim", "Fim da permanência na vaga", "31/12/2026"),
    ("prazo_restante", "Quanto falta para o fim", "faltam 12 dias"),
    ("prazo_meses", "Prazo total na vaga", "3 meses"),
    ("link_cadastro", "Link de cadastro", "https://rrpa.exemplo.org/cadastro/abc123"),
    ("validade_link", "Até quando o link vale", "22/10/2026"),
]
NOMES_VALIDOS = {c[0] for c in CAMPOS}
EXEMPLOS = {c[0]: c[2] for c in CAMPOS}
PADRAO = re.compile(r"\{\{\s*(\w+)\s*\}\}")  # mesmo formato do E-Find: {{nome}}


def campos_do_texto(texto):
    return PADRAO.findall(texto or "")


def campos_desconhecidos(texto):
    return sorted({c for c in campos_do_texto(texto) if c not in NOMES_VALIDOS})


def montar_contexto(usuario, config, request=None, candidato=None, vinculo=None, link=None, vaga=None, nome=None):
    """Valores para os campos. Campos sem valor ficam fora do dicionário (e são avisados na tela)."""
    from candidatos.models import LinkCadastro  # evita importação circular

    ctx = {"unidade": config.nome_unidade, "prazo_meses": config.prazo_texto}
    if usuario is not None:
        ctx["recrutador"] = usuario.nome_exibicao
    nome_pessoa = nome or (candidato.nome if candidato else "") or (link.nome_sugerido if link else "")
    if nome_pessoa:
        ctx["nome"], ctx["primeiro_nome"] = nome_pessoa, primeiro_nome(nome_pessoa)
    if vinculo is not None:
        vaga = vaga or vinculo.vaga
        hoje = timezone.localdate()
        ctx.update(data_chamada=f"{vinculo.data_chamada:%d/%m/%Y}", data_fim=f"{vinculo.data_fim:%d/%m/%Y}",
                   prazo_restante=texto_prazo((vinculo.data_fim - hoje).days))
    if vaga is None and link is not None:
        vaga = link.vaga
    if vaga is None and candidato is not None:
        vaga = candidato.vagas_interesse.first()
    if vaga is not None:
        ctx["vaga"] = vaga.nome
        if vaga.setor:
            ctx["setor"] = vaga.setor
        responsavel = vaga.responsaveis.first()
        if responsavel:
            ctx["responsavel_setor"] = responsavel.nome_exibicao
    if request is not None:
        # Sem convite específico, usa o link pessoal de quem envia: o cadastro fica associado a quem forneceu o link.
        alvo = link or (LinkCadastro.pessoal_de(usuario) if usuario is not None and usuario.pode_gerar_link else None)
        if alvo is not None:
            ctx["link_cadastro"] = url_absoluta(request, alvo.caminho)
            if alvo.expira_em:
                ctx["validade_link"] = f"{timezone.localtime(alvo.expira_em):%d/%m/%Y}"
    return ctx


def renderizar(texto, contexto):
    """Devolve (texto pronto, campos usados que ficaram sem valor)."""
    faltando = []

    def trocar(m):
        campo = m.group(1)
        if campo in contexto:
            return contexto[campo]
        if campo in NOMES_VALIDOS and campo not in faltando:
            faltando.append(campo)
        return m.group(0)

    return PADRAO.sub(trocar, texto or ""), faltando
