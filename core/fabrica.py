"""Geradores de dados fictícios (CPF válido, telefone reservado) para testes e para a demonstração."""


def _digito_cpf(parcial):
    soma = sum(int(d) * peso for d, peso in zip(parcial, range(len(parcial) + 1, 1, -1)))
    return str((soma * 10 % 11) % 10)


def cpf_valido(numero):
    """CPF com dígitos verificadores corretos, derivado de um número qualquer (determinístico)."""
    base = f"{numero % 10**9:09d}"
    while len(set(base)) == 1:  # 111111111 e similares geram CPFs que o validador recusa
        numero += 1
        base = f"{numero % 10**9:09d}"
    d1 = _digito_cpf(base)
    return base + d1 + _digito_cpf(base + d1)


def telefone_ficticio(numero, ddd="11"):
    """Celular com o miolo '0000', faixa que a operadora não distribui: nunca chama uma pessoa real."""
    return f"{ddd}90000{numero % 10000:04d}"
