"""Contrato entre os provedores de WhatsApp (este app) e os disparos (app mensagens).

Só dados: nada aqui acessa banco, rede ou request. Os status dos eventos usam os mesmos nomes de
`mensagens.models.MensagemDisparo.Status` ("enviada", "entregue", "lida", "falhou").
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChamadaTemplate:
    """Template aprovado pela Meta. `parametros` são os valores de {{1}}, {{2}}... na ordem."""

    nome: str
    idioma: str = "pt_BR"
    parametros: list[str] = field(default_factory=list)


@dataclass
class MensagemSaida:
    """Mensagem para UMA pessoa. Fora da janela de 24 h, a API oficial só aceita template aprovado."""

    para: str  # só dígitos, com DDI (5511987654321)
    texto: str
    template: ChamadaTemplate | None = None


@dataclass
class ResultadoEnvio:
    ok: bool
    id_mensagem: str = ""  # id no provedor; casa com os eventos do webhook
    erro: str = ""
    tentar_de_novo: bool = False  # limite de taxa, 5xx, timeout: vale outra tentativa mais tarde


@dataclass
class ResultadoTeste:
    ok: bool
    mensagem: str
    numero: str = ""  # número verificado no provedor, quando ele informa


@dataclass
class EventoWebhook:
    """Evento já normalizado. tipo="status": id_mensagem + status. tipo="recebida": telefone + texto."""

    tipo: str  # "status" | "recebida"
    id_mensagem: str = ""
    status: str = ""
    erro: str = ""
    telefone: str = ""  # só dígitos, com DDI
    texto: str = ""
    quando: datetime | None = None


@dataclass
class CampoProvedor:
    nome: str
    rotulo: str
    tipo: str = "text"  # "text" | "password"
    obrigatorio: bool = False
    segredo: bool = False  # segredos nunca voltam ao navegador
    ajuda: str = ""
    exemplo: str = ""


@dataclass
class DefinicaoProvedor:
    chave: str
    rotulo: str
    descricao: str
    oficial: bool
    passos: list[str]
    campos: list[CampoProvedor]
    exige_template: bool  # True: só envia template aprovado (mensagem iniciada pela empresa)
    criar: Callable[[dict], object]  # credenciais abertas -> objeto com enviar() e testar_conexao()
    interpretar_webhook: Callable[[bytes], list[EventoWebhook]]
    # Sem esta função o webhook aceita o corpo sem assinatura (só o Simulador faz isso, para testes locais).
    assinatura_valida: Callable[[bytes, str, dict], bool] | None = None
    # Resposta ao teste de URL do provedor (GET). Devolve (status, corpo) ou None se não for esse teste.
    responder_verificacao: Callable[[dict, str], tuple[int, str] | None] | None = None
