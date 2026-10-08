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

VERSAO = "1.2"


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
    # PDF com texto de verdade: pelo menos ~200 letras por página lida E os atos aparecem no texto.
    # Muitas matrículas da Caixa são imagem escaneada com só um carimbo/rodapé em texto: aí lê por imagem
    # e fica com a leitura que separar mais atos.
    letras = len(re.findall(r"[A-Za-zÀ-ú]", texto))
    lidas = min(paginas, max_paginas) if paginas else 0
    if lidas and letras >= 200 * lidas and (letras >= 1200 * lidas or len(_marcas(normalizar(texto))) >= 2):
        return texto, "texto", paginas
    ocr = ocr_pdf(caminho, max_paginas, dpi)
    if len(_marcas(normalizar(ocr))) >= len(_marcas(normalizar(texto))):
        return ocr, "ocr", paginas
    return texto, "texto", paginas


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
# cabeçalho no COMEÇO DA LINHA, em qualquer formato: "R.1 - Prot.", "AV-01:", "R 3 Em 10/01/2020",
# "REGISTRO Nº 4 -", "AVERBAÇÃO 5 -", "Av.6/M-12.345". Citações no meio do texto não contam.
RE_ATO_LINHA = re.compile(r"(?m)^[ \t\-–—•*]*(r|av|reg(?:istro)?|averb(?:acao)?)\s*(?:n\s*[oº°\.]+\s*)?[\.\-]?\s*0*(\d{1,3})(?!\d)"
                          r"(?=\s*(?:[\/\-–—:\.\)(,]|em\b|de\b|prot|data|matr|m\b|$))")
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


def _cod(letra, num):
    return ("R" if letra.startswith("r") else "AV") + "-" + str(int(num))


def _marcas(t):
    """Usa os cabeçalhos no começo da linha; se achar poucos (texto sem quebras), usa o formato com nº da matrícula."""
    linha = list(RE_ATO_LINHA.finditer(t))
    meio = list(RE_ATO.finditer(t))
    return linha if len({_cod(m.group(1), m.group(2)) for m in linha}) >= max(2, len({_cod(m.group(1), m.group(2)) for m in meio}) // 2) else meio


def analisar(texto):
    t = normalizar(texto)
    marcas = _marcas(t)
    atos = []
    vistos = set()
    for i, m in enumerate(marcas):
        cod = _cod(m.group(1), m.group(2))
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(t)
        trecho = t[m.end():fim][:1500]
        if cod in vistos:          # a mesma marca citada de novo dentro do texto de outro ato
            continue
        vistos.add(cod)
        tipo, rotulo, onus = tipo_do_ato(trecho[:400])
        cita = set()
        if tipo == "cancelamento":
            for c in re.finditer(r"(?<![a-z0-9])(r|av|registro|averbacao)\s*(?:n\s*[oº°\.]+\s*)?[\.\-]?\s*0*(\d{1,3})(?!\d)", trecho[:600]):
                cita.add(_cod(c.group(1), c.group(2)))
            cita.discard(cod)
            if not cita:   # "cancelamento da hipoteca" sem citar o nº: cancela o último ônus desse tipo ainda ativo
                for tp, rot, eh, pad in TIPOS:
                    if eh and re.search(pad, trecho[:300]):
                        ant = [a for a in atos if a["tipo"] == tp and a["ato"] not in {x for b in atos for x in b["cancela"]}]
                        if ant:
                            cita.add(ant[-1]["ato"])
                        break
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


PALAVRAS_OK = set("""r av reg registro averbacao matricula m mat prot protocolo protocolado em de do da dos das e a o
no na nos nas n nº data titulo compra venda alienacao fiduciaria hipoteca penhora cancelamento cancelada
consolidacao propriedade dominio construcao habite-se incorporacao condominio convencao instituicao retificacao
indisponibilidade arresto usufruto doacao inscricao municipal imovel livro ficha folha fls cartorio oficio
registro geral cnm codigo nacional emolumentos selo valor transmitente adquirente devedor credor caixa economica
federal banco escritura instrumento particular contrato lei art termos conforme objeto presente averba-se registra-se
fica procede se ato atos onus averba registra habite""".split())


def formato_mascarado(texto, n=20):
    """Linhas que parecem cabeçalho de ato, com nomes e números escondidos (para o registro público do teste)."""
    saida = []
    for linha in normalizar(texto).splitlines():
        l = linha.strip()
        if not re.match(r"[\-–—•*]*\s*(r|av|reg|averb)\w*\s*[\.\-]?\s*(n\s*[oº°\.]+\s*)?\d", l):
            continue
        l = re.sub(r"\d", "#", l[:70])
        l = re.sub(r"[a-z]+", lambda m: m.group(0) if m.group(0) in PALAVRAS_OK else "x", l)
        saida.append(l)
        if len(saida) >= n:
            break
    return saida


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
