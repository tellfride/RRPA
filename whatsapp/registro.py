"""Lista de provedores disponíveis e fábrica do provedor da conexão salva."""
from .provedores.meta import META
from .provedores.simulador import SIMULADOR

PROVEDORES = [META, SIMULADOR]


def definicao(chave):
    return next((p for p in PROVEDORES if p.chave == chave), None)


def criar_provedor(conexao):
    """Provedor pronto para enviar. Lança ErroCredenciais se as credenciais salvas não puderem ser lidas."""
    definicao_do_provedor = definicao(conexao.provedor)
    if definicao_do_provedor is None:
        raise ValueError(f"Provedor de WhatsApp desconhecido: {conexao.provedor}")
    return definicao_do_provedor.criar(conexao.credenciais_abertas())
