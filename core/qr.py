"""QR Code dos links de cadastro (biblioteca segno: Python puro, sem dependências nativas)."""
import io

import segno

TINTA = "#10272b"


def qr_data_url(texto, escala=10):
    """PNG embutido (data URL) para mostrar na tela e baixar."""
    return segno.make(texto, error="m").png_data_uri(scale=escala, border=2, dark=TINTA, light="#ffffff")


def qr_svg(texto, escala=10):
    """SVG inline, nítido em qualquer tamanho (cartaz impresso)."""
    saida = io.BytesIO()
    segno.make(texto, error="m").save(saida, kind="svg", scale=escala, border=2, dark=TINTA, light="#ffffff", xmldecl=False, svgns=True)
    return saida.getvalue().decode("utf-8")
