"""
SOLIDUNS — AGENTE DE LEILÕES — LEITOR DE MATRÍCULA POR REGRAS — v1.0 (07/10/2026)

Custo zero: sem IA. Recebe o PDF da matrícula (ou o texto já extraído) e
devolve uma PRÉ-ANÁLISE para o administrador CONFERIR:
  - atos encontrados (R-1, AV-2 ...), com o tipo de cada um;
  - ônus (penhora, hipoteca, alienação fiduciária, indisponibilidade,
    arresto, sequestro, usufruto, ação/premonitória, bem de família...)
    marcados como "ativo" ou "cancelado" (quando um ato posterior cita o
    cancelamento daquele ato);
  - consolidação da propriedade (típica dos imóveis da Caixa).
NUNCA conclui "livre de ônus": a leitura por imagem (OCR) erra, e a
conferência humana do checklist continua obrigatória.

Leitura do PDF: se o PDF tem texto, usa o texto; se é imagem (escaneado),
usa o Tesseract (gratuito) em português, página por página.
Dependências: pypdf; programas tesseract (com idioma "por") e pdftoppm.
"""

import os
import re
import subprocess
import tempfile
import unicodedata

VERSAO = "1.0"


# ------------------------------------------------------------ texto do PDF
def texto_do_pdf(caminho, max_paginas=12, dpi=250):
    """Devolve (texto, metodo, paginas). metodo = 'texto' ou 'ocr'."""
    from pypdf import PdfReader
    try:
        leitor = PdfReader(caminho)
        paginas = len(leitor.pages)
        partes = []
        for p in leitor.pages[:max_paginas]:
            try:
                partes.append(p.extract_text() or "")
            except Exception:
                partes.append("")
        texto = "\n".join(partes)
    except Exception:
        paginas, texto = 0, ""
    # PDF com texto de verdade: pelo menos ~200 letras por página lida
    letras = len(re.findall(r"[A-Za-zÀ-ú]", texto))
    if paginas and letras >= 200 * min(paginas, max_paginas):
        return texto, "texto", paginas
    return ocr_pdf(caminho, max_paginas, dpi), "ocr", paginas


def ocr_pdf(caminho, max_paginas=12, dpi=250):
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-l", str(max_paginas), "-png", caminho, os.path.join(d, "p")],
                       check=True, capture_output=True, timeout=600)
        textos = []
        for nome in sorted(os.listdir(d)):
            if not nome.endswith(".png"):
                continue
            r = subprocess.run(["tesseract", os.path.join(d, nome), "stdout", "-l", "por", "--psm", "6"],
                               capture_output=True, timeout=300)
            textos.append(r.stdout.decode("utf-8", "ignore"))
        return "\n".join(textos)


# ------------------------------------------------------------ regras
def _sa(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def normalizar(texto):
    t = _sa(texto)
    t = re.sub(r"-\s*\n\s*", "", t)          # palavra quebrada no fim da linha
    t = re.sub(r"[ \t]+", " ", t)
    return t


# cabeçalho de ato: "R-5/12.345", "R.5-12345", "AV-3/M-12.345", "AV.03 - 12345", "R 4/ 9.876"
RE_ATO = re.compile(r"(?<![a-z0-9])(r|av)\s*[\.\-]?\s*(\d{1,3})\s*[\/\-–—]\s*(?:m\s*[\.\-]?\s*)?\d[\d\.]{1,9}(?!\d)")

TIPOS = [  # (tipo, rótulo, é ônus?, padrão)
    ("cancelamento", "cancelamento", False, r"cancelad|cancelament|fica\s+sem\s+efeito|baixa\s+d[ao]"),
    ("consolidacao", "consolidação da propriedade", False, r"consolida\w*(?:-se)?\s+(?:d[eao]\s+|a\s+)?(?:propriedade|dominio)"),
    ("alienacao_fiduciaria", "alienação fiduciária", True, r"aliena\w*\s+fiduciari"),
    ("hipoteca", "hipoteca", True, r"hipotec"),
    ("penhora", "penhora", True, r"penhor[ae]|penhorad"),
    ("indisponibilidade", "indisponibilidade", True, r"indisponib"),
    ("arresto", "arresto", True, r"\barrest"),
    ("sequestro", "sequestro", True, r"sequestr"),
    ("usufruto", "usufruto", True, r"usufrut"),
    ("acao", "ação judicial / premonitória", True, r"premonitori|art\w*\.?\s*828|citacao|existencia\s+d[ae]\s+a[cç]ao|acao\s+(de\s+)?(execu|reivindica|anulat|usucapi)"),
    ("bem_familia", "bem de família", True, r"bem\s+de\s+familia"),
    ("clausula", "cláusula restritiva (inalienabilidade/impenhorabilidade)", True, r"inalienab|impenhorab|incomunicab"),
    ("compra_venda", "compra e venda / transmissão", False, r"compra\s+e\s+venda|transmit|arremata|adjudica|doacao|heranca|partilha|formal\s+de\s+partilha"),
    ("construcao", "construção / averbação de área", False, r"construc|habite-se|edifica"),
]


def tipo_do_ato(trecho):
    """O título do ato vem primeiro: vence o tipo citado MAIS CEDO no começo do ato
    (ex.: "COMPRA E VENDA ... livre de hipoteca" é compra e venda, não hipoteca).
    Cancelamento citado logo no início vence sempre."""
    m = re.search(TIPOS[0][3], trecho[:120])
    if m:
        return TIPOS[0][0], TIPOS[0][1], TIPOS[0][2]
    melhor = None
    for ordem, (tipo, rotulo, onus, padrao) in enumerate(TIPOS):
        m = re.search(padrao, trecho)
        if m and (melhor is None or (m.start(), ordem) < melhor[0]):
            melhor = ((m.start(), ordem), (tipo, rotulo, onus))
    return melhor[1] if melhor else ("outro", "outro", False)


def analisar(texto):
    t = normalizar(texto)
    marcas = list(RE_ATO.finditer(t))
    atos = []
    vistos = set()
    for i, m in enumerate(marcas):
        cod = ("R" if m.group(1) == "r" else "AV") + "-" + str(int(m.group(2)))
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(t)
        trecho = t[m.end():fim][:1500]
        if cod in vistos:          # a mesma marca citada de novo dentro do texto de outro ato
            continue
        vistos.add(cod)
        tipo, rotulo, onus = tipo_do_ato(trecho[:400])
        cita = set()
        if tipo == "cancelamento":
            for c in re.finditer(r"(?<![a-z0-9])(r|av)\s*[\.\-]?\s*(\d{1,3})(?!\d)", trecho[:600]):
                cita.add(("R" if c.group(1) == "r" else "AV") + "-" + str(int(c.group(2))))
        atos.append({"ato": cod, "tipo": tipo, "rotulo": rotulo, "onus": onus, "cancela": sorted(cita),
                     "trecho": trecho[:220].strip()})

    cancelados = set()
    for a in atos:
        cancelados.update(a["cancela"])
    consolidou = any(a["tipo"] == "consolidacao" for a in atos)

    onus = []
    for a in atos:
        if not a["onus"]:
            continue
        situacao = "cancelado" if a["ato"] in cancelados else "ativo"
        if a["tipo"] == "alienacao_fiduciaria" and consolidou and situacao == "ativo":
            situacao = "consolidado"   # o credor já ficou com o imóvel (Caixa)
        onus.append({"ato": a["ato"], "tipo": a["tipo"], "rotulo": a["rotulo"], "situacao": situacao})

    # termos soltos (quando o OCR não separou os atos direito)
    soltos = sorted({rot for tipo, rot, eh, pad in TIPOS if eh and re.search(pad, t)})
    ativos = [o for o in onus if o["situacao"] == "ativo"]
    if not atos:
        resumo = "Não foi possível separar os atos (leitura ruim?). Termos citados: " + (", ".join(soltos) or "nenhum")
    elif ativos:
        resumo = "ATENÇÃO — possíveis ônus sem cancelamento encontrado: " + \
                 "; ".join(f"{o['rotulo']} ({o['ato']})" for o in ativos)
    else:
        resumo = "Nenhum ônus ativo encontrado pelas regras (CONFERIR a matrícula)"
    return {"versao": VERSAO, "atos": len(atos), "lista_atos": atos, "onus": onus,
            "consolidacao": consolidou, "termos": soltos, "resumo": resumo,
            "qualidade": qualidade_texto(t)}


def qualidade_texto(t):
    """Proporção de palavras 'reconhecíveis' — indica se o OCR leu bem."""
    palavras = re.findall(r"[a-z]{2,}", t)
    if not palavras:
        return 0.0
    comuns = {"de", "do", "da", "dos", "das", "e", "a", "o", "em", "no", "na", "por", "com", "para", "que",
              "imovel", "matricula", "registro", "area", "lote", "rua", "apartamento", "proprietario", "cpf",
              "valor", "data", "oficial", "cartorio", "brasilia", "averbacao", "titulo", "escritura", "casa",
              "terreno", "quadra", "conjunto", "metros", "quadrados", "inscrito", "brasileiro", "casado",
              "solteiro", "residente", "domiciliado", "pelo", "pela", "sob", "nos", "nas", "ao", "aos", "se"}
    return round(sum(1 for p in palavras if p in comuns) / len(palavras), 3)


if __name__ == "__main__":
    import json
    import sys
    texto, metodo, pags = texto_do_pdf(sys.argv[1])
    r = analisar(texto)
    r["metodo"], r["paginas"] = metodo, pags
    print(json.dumps(r, ensure_ascii=False, indent=1))
