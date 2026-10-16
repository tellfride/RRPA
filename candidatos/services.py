"""Regras de negócio de candidatos e links de cadastro."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, ProtectedError
from django.utils import timezone

from core import geo
from core.constantes import VINCULO_ATIVO
from core.excecoes import RegraDeNegocio
from core.models import Configuracao, LogAtividade
from mensagens.disparos import apagar_dados_pessoais
from mensagens.models import EnvioMensagem

from .models import Candidato, LinkCadastro


def aprovar(candidato, usuario, request=None):
    if candidato.status == Candidato.Status.APROVADO:
        return candidato
    if candidato.status == Candidato.Status.EM_VAGA:
        raise RegraDeNegocio(f"{candidato.nome} já está em uma vaga.")
    candidato.status = Candidato.Status.APROVADO
    candidato.aprovado_por = usuario
    candidato.aprovado_em = timezone.now()
    candidato.save(update_fields=["status", "aprovado_por", "aprovado_em", "atualizado_em"])
    LogAtividade.registrar(usuario, "candidato.aprovado", f"Aprovou {candidato.nome}", candidato, request)
    return candidato


def reprovar(candidato, usuario, motivo="", request=None):
    if candidato.status == Candidato.Status.EM_VAGA:
        raise RegraDeNegocio(f"{candidato.nome} está em uma vaga. Encerre o vínculo antes.")
    candidato.status = Candidato.Status.REPROVADO
    if motivo.strip():
        candidato.observacoes = f"{candidato.observacoes}\n[Não aprovado em {timezone.localdate():%d/%m/%Y}] {motivo.strip()}".strip()
    candidato.save(update_fields=["status", "observacoes", "atualizado_em"])
    LogAtividade.registrar(usuario, "candidato.reprovado", f"Marcou {candidato.nome} como não aprovado", candidato, request)
    return candidato


def reabrir(candidato, usuario, request=None):
    if candidato.status not in (Candidato.Status.REPROVADO, Candidato.Status.DESISTENTE, Candidato.Status.CONCLUIDO):
        raise RegraDeNegocio("Só é possível reabrir cadastros encerrados.")
    candidato.status = Candidato.Status.PENDENTE
    candidato.save(update_fields=["status", "atualizado_em"])
    LogAtividade.registrar(usuario, "candidato.reaberto", f"Reabriu o cadastro de {candidato.nome}", candidato, request)
    return candidato


def anonimizar(candidato, usuario, request=None):
    """Atende pedido de exclusão de dados (LGPD) mantendo o histórico de vínculos sem identificar a pessoa."""
    if candidato.vinculos.filter(status=VINCULO_ATIVO).exists():
        raise RegraDeNegocio("Há um vínculo ativo. Encerre-o antes de remover os dados.")
    nome = candidato.nome
    candidato.nome = "Cadastro removido"
    candidato.cpf = f"9{candidato.pk:010d}"[-11:]
    candidato.telefone = ""
    candidato.email = ""
    candidato.cep = candidato.logradouro = candidato.numero = candidato.complemento = ""
    candidato.bairro = candidato.cidade = candidato.uf = ""
    candidato.observacoes = ""
    candidato.latitude = candidato.longitude = None
    candidato.precisao_geo = ""
    candidato.geo_status = Candidato.GeoStatus.SEM_CEP
    candidato.status = Candidato.Status.DESISTENTE
    candidato.consentimento_em = None
    candidato.consentimento_ip = None
    candidato.save(update_fields=[
        "nome", "cpf", "telefone", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
        "observacoes", "latitude", "longitude", "precisao_geo", "geo_status", "status", "consentimento_em",
        "consentimento_ip", "atualizado_em",
    ])
    candidato.vagas_interesse.clear()
    apagar_dados_pessoais(candidato)
    LogAtividade.registrar(usuario, "candidato.anonimizado", f"Removeu os dados pessoais de {nome} (LGPD)", candidato, request)
    return candidato


@transaction.atomic
def excluir(candidato, usuario, request=None):
    """Apaga o cadastro de vez: serve para duplicados, erros de digitação e testes.

    Se a pessoa já foi chamada para uma vaga, o vínculo e os check-ins dela são apagados junto e a vaga fica livre. Para
    atender a um pedido de exclusão de dados mantendo esse histórico, o caminho é anonimizar(). As mensagens e os convites
    ligados ao cadastro ficam sem nome, telefone e texto, e convites ainda abertos são revogados.
    """
    nome = candidato.nome
    historico = candidato.vinculos.aggregate(vinculos=Count("id", distinct=True), checkins=Count("checkins"))
    detalhe = f" e {historico['vinculos']} vínculo(s) com {historico['checkins']} check-in(s)" if historico["vinculos"] else ""
    LogAtividade.registrar(usuario, "candidato.excluido", f"Excluiu o cadastro de {nome}{detalhe}", candidato, request)
    apagar_dados_pessoais(candidato)
    EnvioMensagem.objects.filter(candidato=candidato).update(nome_destino="Cadastro removido", telefone="", texto="")
    convites = candidato.convites.all()
    convites.filter(usado_em__isnull=True, revogado_em__isnull=True).update(revogado_em=timezone.now())
    convites.update(nome_sugerido="", telefone_sugerido="")
    try:
        candidato.vinculos.all().delete()  # os check-ins vão junto (CASCADE)
        candidato.delete()
    except ProtectedError as erro:  # algo que não conhecemos ainda protege o cadastro: desfaz tudo acima
        raise RegraDeNegocio(f"{nome} não pode ser excluído(a): há registros que dependem dele(a).") from erro


def registrar_whatsapp(candidato, usuario, aceita, request=None):
    """Registra se a pessoa aceita (ou pediu para não receber) mensagens por WhatsApp."""
    candidato.whatsapp_ok = bool(aceita)
    candidato.whatsapp_ok_em = timezone.now()
    candidato.save(update_fields=["whatsapp_ok", "whatsapp_ok_em", "atualizado_em"])
    LogAtividade.registrar(
        usuario, "candidato.whatsapp",
        f"Registrou que {candidato.nome} {'aceita' if aceita else 'NÃO quer receber'} mensagens por WhatsApp", candidato, request,
    )
    return candidato


def criar_convite(usuario, nome, telefone, vaga=None, candidato=None, dias=None, request=None):
    """Convite individual de cadastro. A aprovação de quem forneceu o link fica registrada no próprio convite."""
    config = Configuracao.atual()
    agora = timezone.now()
    dias = dias or config.validade_convite_dias
    with transaction.atomic():
        if candidato is not None and candidato.status == Candidato.Status.PENDENTE:
            aprovar(candidato, usuario, request)
        link = LinkCadastro.objects.create(
            tipo=LinkCadastro.Tipo.CONVITE, criado_por=usuario, vaga=vaga, candidato_origem=candidato,
            nome_sugerido=nome, telefone_sugerido=telefone, aprovado_por=usuario, aprovado_em=agora,
            expira_em=agora + timedelta(days=dias),
        )
        LogAtividade.registrar(usuario, "link.convite", f"Gerou um convite de cadastro para {nome}", link, request)
    return link


def revogar_link(link, usuario, request=None):
    if link.revogado_em:
        return link
    link.revogado_em = timezone.now()
    link.save(update_fields=["revogado_em"])
    LogAtividade.registrar(usuario, "link.revogado", f"Desativou um link de cadastro ({link.get_tipo_display().lower()})", link, request)
    return link


@transaction.atomic
def registrar_candidato_publico(link, dados, ip=None, request=None):
    """Grava o cadastro feito pelo candidato. Sempre associa o candidato a quem forneceu o link."""
    agora = timezone.now()
    link = LinkCadastro.objects.select_for_update(of=("self",)).select_related("criado_por", "aprovado_por", "candidato_origem").get(pk=link.pk)
    if not link.utilizavel:
        raise RegraDeNegocio("Este link não está mais disponível.")

    vaga = dados.get("vaga") or link.vaga
    campos = {k: dados.get(k, "") or "" for k in
              ("nome", "cpf", "telefone", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")}
    # Duplicata: o formulário pode ter vindo de outra pessoa; não mexe em nada além do vínculo com a vaga e com o link.

    candidato = link.candidato_origem
    existente = Candidato.objects.filter(cpf=campos["cpf"]).first()
    if candidato is None and existente is not None:
        # Não altera dados de quem já existe (qualquer pessoa com o link poderia adulterá-los) e não revela o CPF ao visitante.
        if vaga:
            existente.vagas_interesse.add(vaga)
        if existente.indicado_por_id is None:
            existente.indicado_por, existente.link = link.criado_por, link
            existente.save(update_fields=["indicado_por", "link", "atualizado_em"])
        LogAtividade.registrar(
            None, "candidato.duplicado",
            f"Cadastro repetido de {existente.nome} recebido pelo link de {link.criado_por.nome_exibicao}", existente, request,
        )
        if link.tipo == LinkCadastro.Tipo.CONVITE:
            link.usado_em = agora
            link.save(update_fields=["usado_em"])
        return existente

    if candidato is None:
        candidato = Candidato(
            origem=Candidato.Origem.CONVITE if link.tipo == LinkCadastro.Tipo.CONVITE else Candidato.Origem.LINK_PESSOAL,
            status=Candidato.Status.APROVADO if link.aprovado_em else Candidato.Status.PENDENTE,
            aprovado_por=link.aprovado_por if link.aprovado_em else None, aprovado_em=link.aprovado_em,
        )
    for campo, valor in campos.items():
        setattr(candidato, campo, valor)
    candidato.link = link
    candidato.indicado_por = link.criado_por
    candidato.consentimento_em = agora
    candidato.consentimento_ip = ip
    candidato.whatsapp_ok = bool(dados.get("aceita_whatsapp"))
    candidato.whatsapp_ok_em = agora
    candidato.save()
    geo.aplicar_localizacao(candidato, somente_cache=True)
    if vaga:
        candidato.vagas_interesse.add(vaga)

    if link.tipo == LinkCadastro.Tipo.CONVITE:
        link.usado_em = agora
        link.save(update_fields=["usado_em"])
    LogAtividade.registrar(
        None, "candidato.cadastro_link",
        f"{candidato.nome} se cadastrou pelo link de {link.criado_por.nome_exibicao}", candidato, request,
    )
    return candidato
