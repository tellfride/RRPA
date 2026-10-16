"""Funções utilitárias: CPF, telefone, CEP, prazo de permanência e textos."""
import calendar
import ipaddress
import re
import secrets
import unicodedata
from datetime import date, timedelta
from urllib.parse import urlparse

from django.conf import settings

MES_CALENDARIO = "mes_calendario"
DATA_EXATA = "data_exata"

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


# ------------------------------------------------------------------ números
def so_digitos(valor):
    return re.sub(r"[^0-9]", "", "" if valor is None else str(valor))


def normalizar_cpf(valor):
    """Só dígitos; completa com zeros à esquerda (planilhas costumam perdê-los)."""
    digitos = so_digitos(valor)
    return digitos.zfill(11) if 8 <= len(digitos) < 11 else digitos


def validar_cpf(valor):
    cpf = so_digitos(valor)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for posicao in (9, 10):
        soma = sum(int(cpf[i]) * (posicao + 1 - i) for i in range(posicao))
        if (soma * 10 % 11) % 10 != int(cpf[posicao]):
            return False
    return True


def formatar_cpf(valor):
    cpf = so_digitos(valor)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def mascarar_cpf(valor):
    cpf = so_digitos(valor)
    if len(cpf) != 11:
        return "***"
    return f"***.{cpf[3:6]}.{cpf[6:9]}-**"


def normalizar_telefone(valor):
    """Devolve DDD + número (10 ou 11 dígitos), sem o 55 do país."""
    digitos = so_digitos(valor)
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) > 11:
        digitos = digitos.lstrip("0")
    return digitos


def validar_telefone(valor):
    tel = so_digitos(valor)
    if len(tel) not in (10, 11) or not 11 <= int(tel[:2]) <= 99:
        return False
    return tel[2] == "9" if len(tel) == 11 else tel[2] in "2345"


def formatar_telefone(valor):
    tel = so_digitos(valor)
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel


def telefone_whatsapp(valor):
    """Número no formato internacional usado pelo wa.me (55 + DDD + número)."""
    tel = normalizar_telefone(valor)
    return f"55{tel}" if tel else ""


def normalizar_cep(valor):
    """Só dígitos. Recupera o zero inicial perdido pela planilha (1310100 -> 01310100), mas só nesse caso."""
    digitos = so_digitos(valor)
    return digitos.zfill(8) if len(digitos) == 7 else digitos


def validar_cep(valor):
    cep = so_digitos(valor)
    return len(cep) == 8 and cep != "00000000"


def formatar_cep(valor):
    cep = so_digitos(valor)
    return f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else cep


# ------------------------------------------------------------------ nomes
def sem_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def limpar_nome(nome):
    """Remove espaços extras; nomes TODOS EM MAIÚSCULAS ou minúsculas viram 'Nome Próprio'."""
    nome = re.sub(r"\s+", " ", (nome or "").strip())
    if nome and (nome.isupper() or nome.islower()):
        partes = nome.lower().split(" ")
        nome = " ".join(p if (i and p in PARTICULAS) else p.capitalize() for i, p in enumerate(partes))
    return nome


def primeiro_nome(nome):
    partes = (nome or "").split()
    return partes[0].capitalize() if partes else ""


def iniciais(nome):
    partes = [p for p in (nome or "").split() if p]
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def abreviar_nome(nome):
    """'Maria Souza Lima' -> 'Maria L.' (usado para quem só pode ver dados resumidos)."""
    partes = [p for p in (nome or "").split() if p]
    if len(partes) <= 1:
        return nome or ""
    return f"{partes[0]} {partes[-1][0].upper()}."


# ------------------------------------------------------------------ datas e prazo
def ultimo_dia_do_mes(ano, mes):
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def somar_meses(data, meses):
    ano, mes = divmod(data.year * 12 + (data.month - 1) + meses, 12)
    mes += 1
    return date(ano, mes, min(data.day, calendar.monthrange(ano, mes)[1]))


def calcular_periodo(data_chamada, meses=3, modo=MES_CALENDARIO):
    """Devolve (início do período, data final) da permanência na vaga.

    * mes_calendario: o mês da chamada conta como o 1º mês. Chamado em 20/10 com
      3 meses -> outubro, novembro e dezembro: o prazo termina em 31/12.
    * data_exata: chamada + N meses. Chamado em 20/10 -> termina em 19/01.
    """
    if modo == DATA_EXATA:
        alvo = somar_meses(data_chamada, meses)
        fim = alvo if data_chamada.day > alvo.day else alvo - timedelta(days=1)
        return data_chamada, fim
    inicio = data_chamada.replace(day=1)
    ultimo = somar_meses(inicio, meses - 1)
    return inicio, ultimo_dia_do_mes(ultimo.year, ultimo.month)


def meses_do_periodo(inicio, fim):
    """Primeiro dia de cada mês entre inicio e fim (inclusive) — as 'competências' do check-in."""
    atual = inicio.replace(day=1)
    meses = []
    while atual <= fim:
        meses.append(atual)
        atual = somar_meses(atual, 1)
    return meses


def rotulo_mes(data, longo=False):
    if longo:
        return f"{MESES[data.month - 1]} de {data.year}"
    return f"{MESES_ABREV[data.month - 1]}/{str(data.year)[2:]}"


def texto_prazo(dias):
    """'termina hoje', 'faltam 12 dias', 'terminou há 3 dias'."""
    if dias == 0:
        return "termina hoje"
    if dias == 1:
        return "falta 1 dia"
    if dias > 1:
        return f"faltam {dias} dias"
    if dias == -1:
        return "terminou ontem"
    return f"terminou há {-dias} dias"


# ------------------------------------------------------------------ rede e links
def destino_seguro(proximo, padrao):
    """Endereço de retorno vindo de um formulário: só aceita caminhos do próprio sistema (nada de //outro-site)."""
    proximo = (proximo or "").strip()
    if proximo.startswith("/") and not proximo.startswith(("//", "/\\")) and "\n" not in proximo and "\r" not in proximo:
        return proximo
    return padrao


def texto_de_planilha(valor):
    """Impede que um texto digitado por terceiros vire fórmula ao abrir a planilha (=, +, -, @ no início)."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return valor


def gerar_token(tamanho=18):
    return secrets.token_urlsafe(tamanho)


def url_absoluta(request, caminho):
    """Link completo para enviar a terceiros. Usa RRPA_BASE_URL quando definido."""
    base = getattr(settings, "RRPA_BASE_URL", "")
    return f"{base}{caminho}" if base else request.build_absolute_uri(caminho)


def base_local(request):
    """True se os links gerados apontam para localhost/rede interna e não abririam no celular de quem recebe."""
    base = getattr(settings, "RRPA_BASE_URL", "")
    host = urlparse(base).hostname if base else request.get_host().split(":")[0].strip("[]")
    if not host or host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def ip_do_cliente(request):
    if getattr(settings, "RRPA_CONFIAR_PROXY", False):
        encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if encaminhado:
            return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None
