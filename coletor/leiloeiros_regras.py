"""
SOLIDUNS — LEILÕES DE BANCOS E LEILOEIROS — leitura das páginas (sem internet, sem IA) — v1.0 (09/10/2026)

Usado por coletar_leiloeiros.py. Cada leitor recebe o HTML de UMA página e devolve uma lista de dicts.
Formatos conferidos nas amostras reais de 08/10/2026 (rotina "Leilões - teste dos bancos"):
  - Portal Zuk   (portalzuk.com.br): cartões "card-property" — tipo, cidade/UF, bairro, endereço, comitente
                 no título ("... - Banco Bradesco S/A | Z37546"), valor de cada praça, % de desconto, data.
  - Mega Leilões (megaleiloes.com.br): cartões "card" — banco pelo ícone (leilao-banco-santander.png),
                 título "Casa 160 m² - Bairro - Cidade - UF", Extrajudicial/Judicial, 1ª e 2ª praça (data e valor).
  - Superbid     (superbid.net): dados da página em JSON (__NEXT_DATA__ -> offersList.offers): vendedor,
                 preço, lance inicial, cidade, categoria, descrição.
"""

import datetime as dt
import html as htmlmod
import json
import re
import unicodedata

VERSAO = "leiloeiros-v1"


def sa(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower().strip()


def texto(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h or "", flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def num_br(v):
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d{1,3}(?:\.\d{3})+|\d+)", v or "")
    if not m:
        return None
    s = m.group(1)
    s = s.replace(".", "").replace(",", ".") if "," in s else s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def data_br(v):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", v or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------ bancos e financeiras (comitentes)
# chave = a mesma de SELO_BANCOS / logos/index.json do site
BANCOS = [
    ("caixa", "Caixa Econômica Federal", r"caixa econ|\bcef\b"),
    ("bb", "Banco do Brasil", r"banco do brasil|\bbb\b"),
    ("bradesco", "Bradesco", r"bradesco"),
    ("itau", "Itaú Unibanco", r"ita[uú]"),
    ("santander", "Santander", r"santander"),
    ("inter", "Banco Inter", r"\binter\b|inter&co|inter ?& ?co"),
    ("sicoob", "Sicoob", r"sicoob|bancoob"),
    ("sicredi", "Sicredi", r"sicredi"),
    ("brb", "BRB – Banco de Brasília", r"\bbrb\b|banco de bras[ií]lia"),
    ("banrisul", "Banrisul", r"banrisul|banco do estado do rio grande do sul"),
    ("btg", "BTG Pactual", r"\bbtg\b"),
    ("safra", "Banco Safra", r"safra"),
    ("pan", "Banco Pan", r"banco pan\b|\bpan s\.?a"),
    ("bmg", "Banco BMG", r"\bbmg\b"),
    ("poupex", "Poupex", r"poupex"),
    ("emgea", "Emgea", r"emgea"),
    ("daycoval", "Banco Daycoval", r"daycoval"),
    ("c6", "C6 Bank", r"\bc6\b|c6 ?bank"),
    ("bv", "Banco BV", r"banco bv\b|votorantim"),
    ("mercantil", "Banco Mercantil", r"mercantil do brasil"),
    ("banestes", "Banestes", r"banestes"),
    ("banco_nordeste", "Banco do Nordeste", r"banco do nordeste|\bbnb\b"),
    ("banpara", "Banpará", r"banpar[aá]"),
    ("cresol", "Cresol", r"cresol"),
    ("unicred", "Unicred", r"unicred"),
    ("tribanco", "Tribanco", r"tribanco|banco tri[aâ]ngulo"),
    ("rendimento", "Banco Rendimento", r"banco rendimento"),
    ("creditas", "Creditas", r"creditas"),
]
RE_FINANCEIRA = re.compile(r"\bbanco\b|financeira|cr[eé]dito imobili|securitizadora|cooperativa de cr[eé]dito|"
                           r"\bs\.?a\.? *-? *cr[eé]dito|caixa de previd|fundo de investimento|\bfidc\b", re.I)


def banco(nome):
    """devolve (chave, nome bonito) se o comitente for banco/financeira; senão (None, None)"""
    n = sa(nome)
    for chave, rot, padrao in BANCOS:
        if re.search(padrao, n):
            return chave, rot
    if RE_FINANCEIRA.search(nome or ""):
        return "banco", (nome or "").strip()[:80]
    return None, None


# ------------------------------------------------------------ tipo do imóvel -> código do Radar
TIPOS = [
    (r"cobertura", "cobertura"), (r"kitnet|kitinete|studio|est[uú]dio|flat|loft", "kitnet"),
    (r"apartamento|\bapto\b|\bap\b", "apartamento"), (r"sobrado", "sobrado"),
    (r"casa.*condom[ií]nio|condom[ií]nio.*casa", "casa_condominio"), (r"\bcasa", "casa"),
    (r"lote.*condom[ií]nio|condom[ií]nio.*lote", "lote_condominio"),
    (r"ponto comercial", "ponto_comercial"),
    (r"sala|conjunto comercial|escrit[oó]rio|consult[oó]rio", "sala_comercial"), (r"\bloja", "loja"),
    (r"galp[aã]o|barrac[aã]o|armaz[eé]m|dep[oó]sito|industrial|ind[uú]stria", "galpao"),
    (r"pr[eé]dio|edif[ií]cio", "predio_comercial"), (r"hotel|pousada", "hotel_pousada"), (r"\bharas\b", "haras"),
    (r"ch[aá]cara", "chacara"), (r"s[ií]tio", "sitio"), (r"fazenda", "fazenda"),
    (r"[aá]rea rural|im[oó]vel rural|\brural\b", "area_rural"), (r"gleba", "gleba"),
    (r"terreno|\blote\b|[aá]rea\b", "terreno_urbano"),
]


def tipo_imovel(t):
    s = sa(t)
    for padrao, cod in TIPOS:
        if re.search(padrao, s):
            return cod
    return "outros"


def area_m2(t):
    m = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?)\s*(m²|m2|ha)\b", t or "", re.I)
    if not m:
        return None
    v = float(m.group(1).replace(".", "").replace(",", "."))
    return round(v * 10000 if m.group(2).lower() == "ha" else v, 2)


# ------------------------------------------------------------ Portal Zuk
RE_ZUK_CARD = re.compile(r'<div class="card-property card_lotes_div"')


def zuk(pagina, url_base="https://www.portalzuk.com.br"):
    """cartões de uma página de listagem do Zuk (imóveis ou veículos)"""
    out = []
    for c in RE_ZUK_CARD.split(pagina)[1:]:
        link = re.search(r'href="(https?://www\.portalzuk\.com\.br/(?:imovel|veiculo)/[^"]+)"', c)
        tit = re.search(r'title="([^"]+)"', c)
        if not link or not tit:
            continue
        titulo = htmlmod.unescape(tit.group(1)).strip()
        m = re.search(r"-\s*([^-|]+?)\s*\|\s*(Z\d+)\s*$", titulo)
        comitente = m.group(1).strip() if m else ""
        evento = m.group(2) if m else ""
        cod = re.search(r"/(\d+-\d+)/?$", link.group(1))
        status = texto((re.search(r'<span class="card-property-news">(.*?)</span>', c, re.S) or [None, ""])[1])
        tipo_txt = texto((re.search(r'card-property-price-lote">(.*?)</span>', c, re.S) or [None, ""])[1])
        end = re.search(r'<address class="card-property-address">(.*?)</address>', c, re.S)
        cidade = uf = bairro = endereco = None
        if end:
            loc = re.search(r">([^<>/]+?)\s*/\s*([A-Z]{2})</a>\s*(?:-\s*([^<]+))?</span>", end.group(1))
            if loc:
                cidade, uf = htmlmod.unescape(loc.group(1)).strip(), loc.group(2)
                bairro = htmlmod.unescape(loc.group(3) or "").strip() or None
            ends = re.findall(r'margin-left:2\.5rem;">([^<]+)</span>', end.group(1))
            endereco = htmlmod.unescape(ends[0]).strip() if ends else None
        if not cidade and endereco:                     # veículos: "Bebedouro / SP - Centro"
            m2 = re.match(r"^\s*([^/]{2,60}?)\s*/\s*([A-Z]{2})\b(?:\s*-\s*(.+))?", endereco)
            if m2:
                cidade, uf, bairro = m2.group(1).strip(), m2.group(2), (m2.group(3) or "").strip() or None
        infos = [texto(x) for x in re.findall(r'card-property-info-label">(.*?)</span>', c, re.S)]
        pracas = []
        for p in re.finditer(r'<li class="card-property-price"[^>]*data-pracas[^>]*>(.*?)</li>', c, re.S):
            bloco = p.group(1)
            rot = texto((re.search(r'card-property-price-label"\s*>(.*?)</span>', bloco, re.S) or [None, ""])[1])
            val = num_br(texto((re.search(r'card-property-price-value">(.*?)(?:<span class="card-property-price-percent"|</span>)', bloco, re.S) or [None, ""])[1]))
            pct = re.search(r'card-property-price-percent">.*?</i>\s*(\d{1,2})\s*<i', bloco, re.S)
            data = data_br(texto((re.search(r'card-property-price-data">(.*?)</span>', bloco, re.S) or [None, ""])[1]))
            pracas.append({"rotulo": rot, "valor": val, "desconto": int(pct.group(1)) if pct else None, "data": data})
        out.append({"codigo": cod.group(1) if cod else link.group(1).rsplit("/", 1)[-1], "link": htmlmod.unescape(link.group(1)).replace("'", "%27"),
                    "titulo": titulo, "comitente": comitente, "evento": evento, "status": status, "tipo_txt": tipo_txt,
                    "cidade": cidade, "uf": uf, "bairro": bairro, "endereco": endereco, "infos": infos, "pracas": pracas,
                    "descricao": texto(re.sub(r"^[^<]*?>", "", c, count=1))[:600], "veiculo": "/veiculo/" in link.group(1)})
    return out


def zuk_links_cidades(pagina):
    return sorted(set(re.findall(r"https://www\.portalzuk\.com\.br/leilao-de-imoveis/c/todos-imoveis/[a-z]{2}/regiao/[a-z0-9-]+", pagina)))


# ------------------------------------------------------------ Mega Leilões
RE_MEGA_CARD = re.compile(r'<div class="card(?: [a-z-]+)*">\s*(?=\s*<a class="card-image)')


def mega(pagina):
    out = []
    partes = re.split(r'<div class="card (?:open|closed|[a-z-]+)">', pagina)
    for c in partes[1:]:
        if 'class="card-title"' not in c:
            continue
        link = re.search(r'<a class="card-title" href="([^"?]+)', c)
        tit = texto((re.search(r'<a class="card-title"[^>]*>(.*?)</a>', c, re.S) or [None, ""])[1])
        cod = texto((re.search(r'<div class="card-number[^"]*">(.*?)</div>', c, re.S) or [None, ""])[1])
        loc = re.search(r'class="card-locality"[^>]*title="([^",]+),\s*([A-Z]{2})"', c)
        icone = re.search(r'bank_icons/([a-z0-9-]+)\.png', c)
        modal = texto((re.search(r'<div class="card-instance-title">\s*<a[^>]*>(.*?)</a>', c, re.S) or [None, ""])[1])
        status = texto((re.search(r'<div class="card-status">(.*?)</div>', c, re.S) or [None, ""])[1])
        pracas = []
        for p in re.finditer(r'<div class="instance[^"]*">(.*?)</div>', c, re.S):
            b = p.group(1)
            pracas.append({"rotulo": texto((re.search(r"<b>(.*?)</b>", b) or [None, ""])[1]),
                           "data": data_br(texto(b)), "valor": num_br(texto((re.search(r'card-instance-value">(.*?)</span>', b, re.S) or [None, ""])[1])),
                           "ativa": "active" in p.group(0)})
        if not link or not cod:
            continue
        partes_tit = [x.strip() for x in tit.split(" - ")]
        out.append({"codigo": cod, "link": link.group(1), "titulo": tit, "icone": icone.group(1) if icone else "",
                    "modalidade": "judicial" if sa(modal).startswith("judicial") else "extrajudicial",
                    "cidade": loc.group(1).strip() if loc else (partes_tit[-2] if len(partes_tit) >= 3 else None),
                    "uf": loc.group(2) if loc else (partes_tit[-1] if len(partes_tit) >= 3 and re.match(r"^[A-Z]{2}$", partes_tit[-1]) else None),
                    "bairro": partes_tit[1] if len(partes_tit) >= 4 else None,
                    "status": status, "pracas": pracas, "descricao": texto(c)[:600]})
    return out


def mega_banco(icone):
    """'leilao-banco-santander' -> nome para o casamento com BANCOS"""
    return re.sub(r"^leilao-|-", " ", icone or "").strip()


# ------------------------------------------------------------ Superbid
def superbid(pagina):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
    if not m:
        return [], 0
    try:
        d = json.loads(m.group(1))
        ol = d["props"]["pageProps"]["offersList"]
    except (ValueError, KeyError, TypeError):
        return [], 0
    out = []
    for o in ol.get("offers") or []:
        try:
            p = o.get("product") or {}
            loc = p.get("location") or {}
            cid = (loc.get("city") or "").rsplit(" - ", 1)
            st = o.get("offerStatus") or {}
            od = o.get("offerDetail") or {}
            sub = (p.get("subCategory") or {})
            out.append({
                "codigo": str(o["id"]), "link": f"https://exchange.superbid.net/oferta/{o['id']}",
                "titulo": (p.get("shortDesc") or "").strip(), "vendedor": ((o.get("seller") or {}).get("name") or "").strip(),
                "evento": ((o.get("auction") or {}).get("desc") or "").strip(),
                "modalidade_txt": ((o.get("auction") or {}).get("modalityDesc") or "").strip(),
                "cidade": cid[0].strip() if cid and cid[0] else None, "uf": cid[1].strip() if len(cid) == 2 else None,
                "categoria": (sub.get("description") or ""), "grupo": ((sub.get("category") or {}).get("description") or ""),
                "preco": o.get("price"), "lance_inicial": od.get("initialBidValue") or od.get("currentMinBid"),
                "fim": (o.get("endDate") or "")[:10] or None, "aberto": bool(st.get("available")) and not st.get("sold") and not st.get("closed"),
                "descricao": texto((o.get("offerDescription") or {}).get("offerDescription") or "")[:1500]})
        except (KeyError, TypeError, AttributeError):
            continue
    return out, int(ol.get("total") or 0)


# ------------------------------------------------------------ comitentes do Zuk (páginas /leilao-de-imoveis/v/<nome>)
def zuk_links_comitentes(pagina):
    return sorted(set(re.findall(r"https://www\.portalzuk\.com\.br/leilao-de-imoveis/v/[a-z0-9-]+", pagina)))


def mega_ultima_pagina(pagina):
    ns = [int(n) for n in re.findall(r"[?&](?:amp;)?pagina=(\d{1,3})", pagina)]
    return max(ns) if ns else 1


# ------------------------------------------------------------ dados que vêm no texto
RE_QUARTOS = re.compile(r"(\d{1,2})\s*(?:quartos?|dormit[oó]rios?|dorms?\.?|su[ií]tes?)\b", re.I)
RE_VAGAS = re.compile(r"(\d{1,2})\s*vagas?\b", re.I)
RE_AVALIADO = re.compile(r"(?:avaliad[oa]|valor\s+de\s+avalia[cç][aã]o|avalia[cç][aã]o)\s*(?:em|:)?\s*R\$\s*([\d.]+(?:,\d{2})?)", re.I)
RE_FIN_SIM = re.compile(r"aceita\s+financiamento|financiamento\s+(?:banc[aá]rio|pr[oó]prio|dispon[ií]vel)|saldo\s+em\s+at[eé]|"
                        r"parcelamento|\bparcelad|entrada\s+de\s+\d", re.I)
RE_FIN_NAO = re.compile(r"n[aã]o\s+aceita\s+financiamento|somente\s+[àa]\s+vista|pagamento\s+[àa]\s+vista|apenas\s+[àa]\s+vista", re.I)


def quartos(t):
    m = RE_QUARTOS.search(t or "")
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 30 else None


def vagas(t):
    m = RE_VAGAS.search(t or "")
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 50 else None


def avaliado(t):
    m = RE_AVALIADO.search(t or "")
    v = num_br(m.group(1)) if m else None
    return v if v and v >= 1000 else None


def financiamento(t):
    if RE_FIN_NAO.search(t or ""):
        return False
    if RE_FIN_SIM.search(t or ""):
        return True
    return None


def _num_area(v):
    if "," in v:
        return float(v.replace(".", "").replace(",", "."))
    if re.match(r"^\d{1,3}(\.\d{3})+$", v):
        return float(v.replace(".", ""))
    return float(v)


def areas(t):
    """(total, construída/privativa, terreno) em m²: 'Casa 70 m² em Terreno de 588 m²', '90,64ha terreno', '54,00m² construída'"""
    tot = priv = ter = None
    t = t or ""
    for m in re.finditer(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d{1,2})?)\s*(m²|m2|ha)\b", t, re.I):
        try:
            v = _num_area(m.group(1))
        except ValueError:
            continue
        ha = m.group(2).lower() == "ha"
        v = round(v * 10000 if ha else v, 2)
        if v <= 0:
            continue
        antes, depois = sa(t[max(0, m.start() - 14):m.start()]), sa(t[m.end():m.end() + 14])
        if ha or "terreno" in antes or depois.strip().startswith(("terreno", "de terreno")):
            ter = ter or v
        elif re.search(r"constru|privativ|\butil", antes + " " + depois):
            priv = priv or v
        else:
            tot = tot or v
    return tot, priv, ter


def ocupacao(t):
    s = sa(t)
    if re.search(r"\bdesocupad", s):
        return "desocupado"
    if re.search(r"\bocupad", s):
        return "ocupado"
    return None
