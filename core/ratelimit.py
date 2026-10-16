"""Limite simples de tentativas por chave (IP, usuário...), compartilhado entre processos via cache em banco."""
from django.core.cache import cache


def _nome(chave):
    return f"rl:{chave}"


def registrar(chave, janela_segundos):
    """Conta mais uma tentativa; a contagem expira `janela_segundos` após a primeira."""
    nome = _nome(chave)
    if cache.add(nome, 1, janela_segundos):
        return 1
    try:
        return cache.incr(nome)
    except ValueError:  # expirou entre o add e o incr
        cache.set(nome, 1, janela_segundos)
        return 1


def excedeu(chave, limite):
    return (cache.get(_nome(chave)) or 0) >= limite


def zerar(chave):
    cache.delete(_nome(chave))
