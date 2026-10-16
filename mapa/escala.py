"""Escalas visuais do mapa (lógica pura).

Cor: uma só cor (azul), do claro ao escuro conforme o número de candidatos do bairro — a rampa sequencial da paleta de dados.
Raio: proporcional à raiz quadrada do total, para que a ÁREA do círculo seja proporcional à quantidade.
"""
import math

# Etapas 200, 300, 400, 550 e 700 da rampa azul (claro -> escuro)
RAMPA = ("#9ec5f4", "#6da7ec", "#3987e5", "#1c5cab", "#0d366b")
RAIO_MIN = 7.0
RAIO_MAX = 32.0


def _arredondar(valor):
    return int(math.floor(valor + 0.5))  # arredondamento "comercial" (o round() do Python vai ao par mais próximo)


def _milhar(n):
    return f"{n:,}".replace(",", ".")


def _cor_da_rampa(indice, classes):
    if classes <= 1:
        return RAMPA[int(math.floor((len(RAMPA) - 1) * 0.75))]
    return RAMPA[_arredondar(indice * (len(RAMPA) - 1) / (classes - 1))]


def construir_escala(maximo):
    """Faixas contíguas de 1 até `maximo` (no máximo uma por cor). As primeiras são mais estreitas:
    poucos bairros concentram muitos candidatos."""
    try:
        maximo = int(math.floor(maximo))
    except (TypeError, ValueError, OverflowError):
        return []
    if maximo < 1:
        return []
    classes = min(len(RAMPA), maximo)
    faixas, minimo = [], 1
    for i in range(classes):
        restantes = classes - i - 1
        ideal = maximo if i == classes - 1 else _arredondar(maximo * ((i + 1) / classes) ** 2)
        limite = min(maximo - restantes, max(minimo, ideal))
        rotulo = _milhar(minimo) if minimo == limite else f"{_milhar(minimo)}–{_milhar(limite)}"
        faixas.append({"min": minimo, "max": limite, "color": _cor_da_rampa(i, classes), "label": rotulo})
        minimo = limite + 1
    return faixas


def cor_para(total, faixas):
    for faixa in faixas:
        if total <= faixa["max"]:
            return faixa["color"]
    return faixas[-1]["color"] if faixas else _cor_da_rampa(0, 1)


def raio_para(total, maximo):
    """Raio em pixels, com piso para o círculo continuar clicável no celular."""
    if not (maximo > 0) or not (total > 0):
        return RAIO_MIN
    return max(RAIO_MIN, _arredondar(RAIO_MAX * math.sqrt(min(total, maximo) / maximo) * 10) / 10)
