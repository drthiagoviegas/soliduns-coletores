"""
SOLIDUNS — LEILÃO DE AUTOMÓVEIS — regras de leitura (custo zero, sem IA) — v1.0 (08/10/2026)

Usado por coletar_leiloes_veiculos.py e coletar_fipe.py. Não acessa a internet.

  - nome_marca(nome_fipe)         nome de exibição da marca da FIPE ("VW - VolksWagen" -> "Volkswagen")
  - familia_do_modelo(nome)       modelo para o filtro ("Gol 1.0 Mi Total Flex 8V 4p" -> "Gol")
  - Catalogo(marcas, modelos)     casa marca/modelo do edital com a tabela FIPE
  - extrair_lotes(texto, ...)     separa os lotes de VEÍCULO de um edital e lê cada campo
  - Cidades                       lista oficial do IBGE (dados/cidades_ibge.json, a mesma do site)

Princípio (lição do Leilão de Imóveis): nenhum nome lido do edital vira opção de filtro.
Marca e modelo só ficam preenchidos quando batem com a FIPE; cidade só quando existe no IBGE
daquela UF. O resto fica só no texto (busca livre).
"""

import datetime as dt
import json
import os
import re
import unicodedata

VERSAO_REGRAS = "veic-regras-v1"


def sa(t):
    """minúsculas sem acento"""
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower().strip()


def chave(t):
    """só letras e números, sem acento (HB 20 == HB20 == hb-20)"""
    return re.sub(r"[^a-z0-9]", "", sa(t))


# ------------------------------------------------------------------ marcas
MARCA_EXIBICAO = {                       # grafias da FIPE que não são o nome usual da marca
    "volkswagen": "Volkswagen", "chevrolet": "Chevrolet", "kia motors": "Kia", "mercedes-benz": "Mercedes-Benz",
    "citroen": "Citroën", "harley-davidson": "Harley-Davidson", "land rover": "Land Rover", "mclaren": "McLaren",
    "caoa chery": "CAOA Chery", "caoa chery/chery": "CAOA Chery", "mini": "Mini", "gwm": "GWM",
}
SIGLAS = {"BMW", "BYD", "JAC", "GWM", "KTM", "MV", "BRP", "DAF", "MAN", "CAOA", "JPX", "TAC", "AGB", "BRM", "GM", "VW",
          "MG", "RAM", "SSANGYONG", "EFFA", "SHC", "JTZ", "AMW", "BRAVAX", "HAOJUE", "IVECO", "XCMG", "SANY", "JCB"}


def _titulo(p):
    if p.upper() in SIGLAS and len(p) <= 4:
        return p.upper()
    return "-".join(x[:1].upper() + x[1:].lower() if x else x for x in p.split("-"))


def nome_marca(nome_fipe):
    n = re.sub(r"\s+", " ", (nome_fipe or "").strip())
    if " - " in n:                       # "VW - VolksWagen", "GM - Chevrolet", "LR - Land Rover"
        n = n.split(" - ", 1)[1].strip()
    if sa(n) in MARCA_EXIBICAO:
        return MARCA_EXIBICAO[sa(n)]
    letras = re.sub(r"[^A-Za-z]", "", n)
    if letras and (letras.isupper() or letras.islower()):
        n = " ".join(_titulo(p) for p in n.split(" "))
    return n


# apelidos usados em editais, Detran e Renavam (MARCA/MODELO) -> nome de exibição
APELIDOS_MARCA = {
    "vw": "Volkswagen", "volks": "Volkswagen", "volkswagen": "Volkswagen", "vw caminhoes": "Volkswagen",
    "gm": "Chevrolet", "chev": "Chevrolet", "chevrolet": "Chevrolet", "gm chevrolet": "Chevrolet",
    "fiat": "Fiat", "ford": "Ford", "honda": "Honda", "yamaha": "Yamaha", "toyota": "Toyota", "renault": "Renault",
    "ren": "Renault", "peugeot": "Peugeot", "peug": "Peugeot", "citroen": "Citroën", "hyundai": "Hyundai",
    "kia": "Kia", "kia motors": "Kia", "nissan": "Nissan", "mitsubishi": "Mitsubishi", "mmc": "Mitsubishi",
    "jeep": "Jeep", "volvo": "Volvo", "scania": "Scania", "iveco": "Iveco", "daf": "DAF", "man": "MAN",
    "agrale": "Agrale", "suzuki": "Suzuki", "dafra": "Dafra", "shineray": "Shineray", "kawasaki": "Kawasaki",
    "triumph": "Triumph", "ktm": "KTM", "bmw": "BMW", "audi": "Audi", "land rover": "Land Rover", "lr": "Land Rover",
    "chery": "CAOA Chery", "caoa chery": "CAOA Chery", "jac": "JAC", "byd": "BYD", "gwm": "GWM", "troller": "Troller",
    "harley davidson": "Harley-Davidson", "h davidson": "Harley-Davidson", "harley": "Harley-Davidson",
    "mercedes": "Mercedes-Benz", "mercedes benz": "Mercedes-Benz", "m benz": "Mercedes-Benz", "mbenz": "Mercedes-Benz",
    "mb": "Mercedes-Benz", "subaru": "Subaru", "porsche": "Porsche", "lexus": "Lexus", "chrysler": "Chrysler",
    "dodge": "Dodge", "ram": "RAM", "mini": "Mini", "smart": "Smart", "ssangyong": "SsangYong", "lifan": "Lifan",
    "haojue": "Haojue", "kasinski": "Kasinski", "traxx": "Traxx", "sundown": "Sundown", "royal enfield": "Royal Enfield",
    "ducati": "Ducati", "marcopolo": "Marcopolo", "caio": "Caio", "comil": "Comil", "neobus": "Neobus",
    "john deere": "John Deere", "massey ferguson": "Massey Ferguson", "new holland": "New Holland", "case": "Case",
    "case ih": "Case IH", "caterpillar": "Caterpillar", "cat": "Caterpillar", "valtra": "Valtra", "komatsu": "Komatsu",
    "jcb": "JCB", "xcmg": "XCMG", "sany": "SANY", "liugong": "LiuGong", "doosan": "Doosan", "kubota": "Kubota",
    "yanmar": "Yanmar", "stara": "Stara", "jacto": "Jacto", "fendt": "Fendt", "bobcat": "Bobcat",
}


# ------------------------------------------------------------------ modelos
DUPLA_SE = {  # 1ª palavra -> 2ª palavra só entra quando é uma destas (Palio Weekend, Uno Mille, Range Rover...)
    "grand": None, "range": None, "new": None, "nova": None, "novo": None, "land": None, "santa": None,
    "space": None, "classe": None, "serie": None, "town": None, "pt": None,
}


def familia_do_modelo(nome):
    """'Gol 1.0 Mi Total Flex 8V 4p' -> 'Gol'; 'Grand Siena ATTRAC. 1.4' -> 'Grand Siena'; 'HB20 Comfort' -> 'HB20'"""
    p = [x for x in re.split(r"\s+", re.sub(r"[()]", " ", (nome or "").strip())) if x]
    if not p:
        return ""
    f = p[0].strip(".,;:/")
    minimo = 1 if sa(f) in ("classe", "serie") else 2
    if sa(f) in DUPLA_SE and len(p) > 1 and re.match(r"^[A-Za-zÀ-ú]{%d,}$" % minimo, p[1]):
        f = f + " " + p[1].strip(".,;:/")
    return f


def familia_exibicao(variantes):
    """várias grafias da mesma família (ONIX / Onix) -> uma só: prefere a de maiúsc./minúsc. misturadas"""
    def nota(v):
        letras = re.sub(r"[^A-Za-zÀ-ú]", "", v)
        misto = letras and not letras.isupper() and not letras.islower()
        return (1 if misto else 0, variantes.count(v))
    melhor = max(set(variantes), key=nota)
    letras = re.sub(r"[^A-Za-zÀ-ú]", "", melhor)
    if letras.isupper() and len(letras) >= 5 and not re.search(r"\d", melhor):
        melhor = " ".join(w[:1] + w[1:].lower() for w in melhor.split(" "))
    return melhor


class Catalogo:
    """marcas e modelos da FIPE (como estão no Supabase) para casar com o texto dos editais"""

    def __init__(self, marcas, modelos):
        self.marcas = marcas                    # [{tipo, codigo, nome}]
        self.por_nome = {}                      # (tipo, chave(nome)) -> marca
        for m in marcas:
            self.por_nome[(m["tipo"], chave(m["nome"]))] = m
        self.familias = {}                      # (tipo, marca_codigo) -> {chave(familia): (familia, [modelos])}
        for x in modelos:
            d = self.familias.setdefault((x["tipo"], x["marca_codigo"]), {})
            d.setdefault(chave(x["familia"]), (x["familia"], []))[1].append(x)

    def marca(self, texto_marca, tipo_preferido=None):
        """devolve (tipo, marca) ou (None, None)"""
        k = sa(re.sub(r"[./\-_]", " ", texto_marca or "")).strip()
        k = re.sub(r"^i\s+", "", k)              # "I/TOYOTA" = importado
        k = re.sub(r"\s+", " ", k)
        nome = APELIDOS_MARCA.get(k) or APELIDOS_MARCA.get(k.replace(" ", ""))
        alvo = chave(nome or k)
        ordem = [tipo_preferido] if tipo_preferido else []
        ordem += [t for t in ("carro", "moto", "caminhao", "maquina") if t not in ordem]
        for t in ordem:
            m = self.por_nome.get((t, alvo))
            if m:
                return t, m
        return None, None

    def tipos_com_marca(self, texto_marca):
        out = []
        for t in ("carro", "moto", "caminhao", "maquina"):
            tt, m = self.marca(texto_marca, t)
            if m and tt == t:
                out.append(t)
        return out

    def modelo(self, tipo, marca_codigo, texto_modelo):
        """devolve (familia, modelo_codigo) — modelo_codigo só com versão bem casada"""
        fams = self.familias.get((tipo, marca_codigo)) or {}
        if not fams or not texto_modelo:
            return None, None
        palavras = [w for w in re.split(r"[\s/]+", texto_modelo.strip()) if w]
        fam = None
        for n in (3, 2, 1):                      # "GRAND SIENA" antes de "GRAND"
            if len(palavras) >= n:
                k = chave(" ".join(palavras[:n]))
                if k in fams:
                    fam = fams[k]
                    break
        if not fam:                              # "ONIX1.0" / "HB20S" colados
            k1 = chave(palavras[0])
            for kk in sorted(fams, key=len, reverse=True):
                if len(kk) >= 3 and k1.startswith(kk):
                    fam = fams[kk]
                    break
        if not fam:
            return None, None
        familia, modelos = fam
        alvo = set(_tokens(texto_modelo))
        melhor, nota_m, empate = None, 0, False
        for x in modelos:
            n = len(alvo & set(_tokens(x["nome"])))
            if n > nota_m:
                melhor, nota_m, empate = x, n, False
            elif n == nota_m and n:
                empate = True
        cod = melhor["codigo"] if melhor and not empate and (nota_m >= 3 or nota_m == len(alvo) >= 2) else None
        return familia, cod


def _tokens(t):
    t = sa(t).replace(",", ".")
    return [w for w in re.split(r"[\s/()\-]+", t) if w and w not in {"de", "c", "p", "mec", "aut"}]


# ------------------------------------------------------------------ cidades (IBGE)
class Cidades:
    def __init__(self, caminho=None):
        caminho = caminho or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "cidades_ibge.json")
        with open(caminho, encoding="utf-8") as f:
            bruto = json.load(f)
        self.por_uf = {uf: {sa(c): c for c in lista} for uf, lista in bruto.items()}

    def oficial(self, trecho, uf):
        lista = self.por_uf.get(uf or "") or {}
        if not lista or not trecho:
            return None
        palavras = re.split(r"\s+", re.sub(r"[/\-–,.;:()]", " ", trecho).strip())
        for n in range(min(6, len(palavras)), 0, -1):
            cand = sa(" ".join(palavras[:n]))
            if cand in lista:
                return lista[cand]
        return None


UFS27 = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
         "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
ESTADO_NOME = {"acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
               "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso do sul": "MS",
               "mato grosso": "MT", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
               "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
               "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO"}


# ------------------------------------------------------------------ leitura do edital
RE_VEICULO = re.compile(r"ve[ií]cul|autom[oó]ve|motocicl|motonet|ciclomotor|caminh[aã]o|caminhonete|camioneta|[oô]nibus|"
                        r"\btrator|colheitadeir|retroescavadeir|escavadeir|p[aá]\s+carregadeir|motoniveladr|motoniveladora|"
                        r"empilhadeir|semirreboque|semi-reboque|\breboque|renavam|\bchassi|\bplaca\b|marca\s*/\s*modelo|"
                        r"\b[A-Z]{2,8}\s*/\s*[A-Z0-9]{2,}|\bI\s*/\s*[A-Z]{2,}|\b(?:19|20)\d\d\s*/\s*(?:19|20)\d\d\b|"
                        r"\d\s*km\b|\bdiesel\b|\bflex\b", re.I)
RE_VEICULO_FORTE = re.compile(r"renavam|\bchassi|\bplaca\b|marca\s*/\s*modelo|ano\s*(de\s*)?fab|\bfab\.?\s*/\s*mod|"
                              r"motocicl|caminh[aã]o|autom[oó]vel|\btrator\b|colheitadeira|ve[ií]culo", re.I)
RE_IMOVEL_FORTE = re.compile(r"matr[ií]cula\s+n|registro\s+de\s+im[oó]veis|\bm²|metros\s+quadrados|hectare|unidade\s+aut[oô]noma|"
                             r"apartamento|\bterreno\b", re.I)
RE_EDITAL = re.compile(r"edital\s+(de\s+)?(leil|hasta|pra[cç]a|aliena)|ser[aá]\(?[aã]?o?\)?\s+levad[oa]\(?s?\)?\s+a\s+(leil|p[uú]blico)"
                       r"|primeir[oa]\s+(leil|pra[cç]|hasta)|1[ºo°]\s*(leil|pra[cç])|datas?\s+(e\s+hor[aá]rios?\s+)?dos\s+leil|leil[aã]o\s+p[uú]blico", re.I)

RE_LOTE = re.compile(r"\blote\s*(?:n[º°o.]*\s*)?(\d{1,4})\b\s*[-–:.)]?", re.I)
RE_MARCA_MODELO = re.compile(
    r"(?:marca\s*/\s*modelo\s*:?\s*|ve[ií]culo\s*:?\s*|\b)"
    r"((?:I\s*/\s*)?[A-Z][A-Z.]{1,14}(?:[ -][A-Z]{2,10})?)\s*/\s*([A-Z0-9][A-Za-z0-9 .,\-+]{1,45}?)"
    r"(?=\s*(?:[,;]|\bano\b|\bfab|\bcor\b|\bplaca|\brenavam|\bchassi|\bcomb|\b(19|20)\d\d\b|$))")
RE_IMPORTADO = re.compile(r"\bI\s*/\s*([A-Z][A-Z.\-]{1,14}(?:\s+[A-Z0-9][A-Za-z0-9.\-+]{0,20}){1,6})"
                          r"(?=\s*(?:[,;]|\bano\b|\bfab|\bcor\b|\bplaca|\bcomb|\b(19|20)\d\d\b|$))")
RE_MARCA_ROTULO = re.compile(r"\bmarca\s*:?\s*([A-ZÀ-Ú][\wÀ-ú.\-]{1,20}(?:\s+[A-ZÀ-Ú][\wÀ-ú.\-]{1,20})?)\s*[,;]?\s*modelo\s*:?\s*"
                             r"([A-Z0-9][\w.\-]{0,20}(?:\s+[A-Z0-9][\w.\-]{0,20}){0,3})", re.I)
RE_ANOS = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\s*/\s*(19[5-9]\d|20[0-4]\d)\b")
RE_ANO_FAB = re.compile(r"ano\s*(?:de\s*)?fabrica[cç][aã]o\s*:?\s*(19[5-9]\d|20[0-4]\d)", re.I)
RE_ANO_MOD = re.compile(r"ano\s*(?:do\s*)?modelo\s*:?\s*(19[5-9]\d|20[0-4]\d)|\bano\s*:?\s*(19[5-9]\d|20[0-4]\d)\b", re.I)
RE_KM = re.compile(r"(?:quilometragem|hod[oô]metro|km\s*rodados?)\s*:?\s*(\d{1,3}(?:\.\d{3})+|\d{1,7})|\b(\d{1,3}(?:\.\d{3})+|\d{1,7})\s*km\b", re.I)
RE_PLACA = re.compile(r"\b([A-Z]{3})[\s-]?(\d[A-Z0-9]\d{2})\b")
RE_PLACA_MASC = re.compile(r"placa\s*(?:final|n[º°o.]*)?\s*:?\s*[*xX\-\s]{2,8}\d?(\d)\b", re.I)
RE_PLACA_FINAL = re.compile(r"placa\s+final\s*:?\s*(\d)\b", re.I)
RE_REAIS = r"R\$\s*([\d.]+,\d{2}|[\d.]+)"
RE_AVAL = re.compile(r"(?:avalia[cç][aã]o|avaliad[oa]s?\s+em|valor\s+de\s+avalia[cç][aã]o)\s*(?:total\s*)?(?:de\s*)?:?\s*" + RE_REAIS, re.I)
RE_LANCE = re.compile(r"(?:lance\s+(?:m[ií]nimo|inicial)|valor\s+m[ií]nimo|pre[cç]o\s+m[ií]nimo|valor\s+inicial|"
                      r"1[ºª°o]?\s*(?:leil[aã]o|pra[cç]a)[^R$]{0,40})\s*(?:de\s*)?:?\s*" + RE_REAIS, re.I)
RE_PCT = re.compile(r"(\d{2})\s*%\s*(?:\([^)]*\)\s*)?(?:do\s+valor\s+)?d[ao]\s+(?:valor\s+de\s+)?avalia", re.I)
RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
RE_DATA_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b")
RE_DATA_EXT = re.compile(r"\b(\d{1,2})[ºo°]?\s+de\s+([a-zç]+)\s+de\s+(20\d{2})\b", re.I)
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
CORES = {"branc": "Branca", "pret": "Preta", "prata": "Prata", "cinz": "Cinza", "vermelh": "Vermelha", "azul": "Azul",
         "verde": "Verde", "amarel": "Amarela", "bege": "Bege", "marrom": "Marrom", "dourad": "Dourada",
         "laranja": "Laranja", "vinho": "Vinho", "rosa": "Rosa", "rox": "Roxa", "grafite": "Grafite", "fantasia": "Fantasia"}
COMBUSTIVEIS = [(r"\bflex\b|[aá]lcool\s*/\s*gasolina|gasolina\s*/\s*[aá]lcool", "Flex"), (r"\bdiesel\b", "Diesel"),
                (r"h[ií]brid", "Híbrido"),
                (r"combust[ií]vel\s*:?\s*el[eé]tric|\b(?:ve[ií]culo|carro|autom[oó]vel|motor|caminh[aã]o|[oô]nibus|moto|motocicleta)\s+"
                 r"(?:100\s*%\s+)?el[eé]tric|\b100\s*%\s+el[eé]tric", "Elétrico"), (r"\bgnv\b", "GNV"),
                (r"\b[aá]lcool\b|\betanol\b", "Álcool"), (r"\bgasolina\b", "Gasolina")]
RE_SUCATA = re.compile(r"sucata|inserv[ií]ve|irrecuper[aá]ve|fins?\s+de\s+desmontagem|desmanche|baixa\s+definitiva|"
                       r"aproveitamento\s+de\s+pe[cç]as|apenas\s+pe[cç]as|sem\s+direito\s+a\s+documenta", re.I)
RE_RECUPERAVEL = re.compile(r"recuper[aá]ve|sinistrad|avariad|danificad|batid|m[eé]dia\s+monta|grande\s+monta|"
                            r"sem\s+motor|n[aã]o\s+(liga|funciona)|incendiad", re.I)
RE_CIRCULACAO = re.compile(r"circula[cç][aã]o|documenta[vç][eãa]|apto\s+a\s+circular|em\s+condi[cç][oõ]es\s+de\s+uso|"
                           r"bom\s+estado|conservad|em\s+funcionamento|funcionando|rodando", re.I)
RE_MAQUINA = re.compile(r"\btrator|colheitadeir|colhedeir|retroescavadeir|escavadeir|p[aá]\s+carregadeir|motoniveladora|"
                        r"empilhadeir|rolo\s+compactador|plantadeir|pulverizador|guindaste|\bm[aá]quina\s+agr", re.I)
RE_CAMINHAO = re.compile(r"caminh[aã]o|[oô]nibus|micro-?\s?[oô]nibus|cavalo\s+mec[aâ]nico|semirreboque|semi-reboque|"
                         r"\breboque\b|\bcarreta\b|\btruck\b|basculante|\bba[uú]\b", re.I)
RE_MOTO = re.compile(r"motocicl|motonet|ciclomotor|\bmoto\b|scooter|triciclo|quadriciclo", re.I)
RE_PROPRIETARIO = re.compile(r"(de\s+propriedade|pertencente|em\s+nome)\s+d[eoa]s?\s+[^,;.]{2,80}", re.I)
RE_DOC = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b|"
                    r"renavam\s*(?:n[º°o.]*)?\s*:?\s*\d{9,11}|chassi\s*(?:n[º°o.]*)?\s*:?\s*[A-HJ-NPR-Z0-9*]{8,17}", re.I)


def num_br(v):
    try:
        s = v.strip().rstrip(".")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")
        return round(float(s), 2)
    except (ValueError, AttributeError):
        return None


def datas(trecho, hoje=None):
    hoje = hoje or dt.date.today()
    out = []
    for m in RE_DATA_NUM.finditer(trecho):
        out.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in RE_DATA_EXT.finditer(trecho):
        mth = MESES.get(sa(m.group(2)))
        if mth:
            out.append((m.start(), int(m.group(1)), mth, int(m.group(3))))
    res = []
    for _, d, mth, a in sorted(out):
        try:
            x = dt.date(a, mth, d)
        except ValueError:
            continue
        if hoje - dt.timedelta(days=60) <= x <= hoje + dt.timedelta(days=400):
            res.append(x)
    return res


def condicao(t):
    if RE_SUCATA.search(t):
        return "sucata"
    if RE_RECUPERAVEL.search(t):
        return "recuperavel"
    if RE_CIRCULACAO.search(t):
        return "circulacao"
    return "nao_informado"


def tipo_pelo_texto(t):
    if RE_MAQUINA.search(t):
        return "maquina"
    if RE_CAMINHAO.search(t):
        return "caminhao"
    if RE_MOTO.search(t):
        return "moto"
    return None


def limpar_privado(t):
    """tira nome de proprietário, CPF/CNPJ, Renavam, chassi e placa completa"""
    t = RE_PROPRIETARIO.sub(r"\1 [omitido]", t)
    t = RE_DOC.sub("[omitido]", t)
    t = RE_PLACA.sub(lambda m: "placa final " + m.group(2)[-1], t)
    return re.sub(r"\s+", " ", t).strip()


def parece_edital_de_veiculo(texto):
    return bool(RE_EDITAL.search(texto)) and bool(RE_VEICULO_FORTE.search(texto))


def _segmentos(texto):
    marcas = list(RE_LOTE.finditer(texto))
    if not marcas:
        return [("1", texto)]
    seg = []
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        seg.append((m.group(1).lstrip("0") or "0", texto[m.start():fim]))
    return seg


def _candidatos_marca(t):
    """pares (marca, modelo): "VW/GOL 1.0" e o importado "I/TOYOTA HILUX CD" (marca de 1 ou 2 palavras)"""
    out = []
    for m in RE_MARCA_MODELO.finditer(t):
        out.append((m.group(1), m.group(2).strip(" .,-")))
    for m in RE_MARCA_ROTULO.finditer(t):
        out.append((m.group(1).strip(), m.group(2).strip(" .,-")))
    for m in RE_IMPORTADO.finditer(t):
        p = m.group(1).split()
        if len(p) >= 3:
            out.append((" ".join(p[:2]), " ".join(p[2:])))
        if len(p) >= 2:
            out.append((p[0], " ".join(p[1:])))
    return out


def ler_lote(seg, catalogo, cidades, uf_padrao=None, hoje=None):
    t = re.sub(r"\s+", " ", seg)
    if not RE_VEICULO.search(t):
        return None
    if RE_IMOVEL_FORTE.search(t) and not RE_VEICULO_FORTE.search(t):
        return None
    tipo_txt = tipo_pelo_texto(t)
    r = {"tipo": tipo_txt, "marca_codigo": None, "marca": None, "familia": None, "modelo_codigo": None, "modelo_texto": None}

    # marca / modelo (padrão MARCA/MODELO do Renavam)
    for bruto_marca, bruto_modelo in _candidatos_marca(t):
        if re.match(r"^(R\$|CPF|CNPJ|UF|RS|SP|LTDA|S\.?A)$", bruto_marca.strip(), re.I):
            continue
        if catalogo:
            tipo_m, marca = catalogo.marca(bruto_marca, tipo_txt)
            if marca:
                r["tipo"] = tipo_txt or tipo_m
                if tipo_txt and tipo_m != tipo_txt:        # ex.: Volvo caminhão x Volvo carro
                    tt, mm = catalogo.marca(bruto_marca, tipo_txt)
                    if mm and tt == tipo_txt:
                        marca, tipo_m = mm, tt
                if tipo_m == r["tipo"]:
                    r["marca_codigo"], r["marca"] = marca["codigo"], marca["nome"]
                    fam, cod = catalogo.modelo(tipo_m, marca["codigo"], bruto_modelo)
                    r["familia"], r["modelo_codigo"] = fam, cod
                    r["modelo_texto"] = (bruto_marca.strip() + "/" + bruto_modelo)[:80]
                    break
        if not r["modelo_texto"]:
            r["modelo_texto"] = (bruto_marca.strip() + "/" + bruto_modelo)[:80]
    r["tipo"] = r["tipo"] or "carro"

    # anos
    fab = mod = None
    m = RE_ANOS.search(t)
    if m and 0 <= int(m.group(2)) - int(m.group(1)) <= 1:
        fab, mod = int(m.group(1)), int(m.group(2))
    else:
        mf, mm = RE_ANO_FAB.search(t), RE_ANO_MOD.search(t)
        fab = int(mf.group(1)) if mf else None
        mod = int((mm.group(1) or mm.group(2))) if mm else None
    ano_max = (hoje or dt.date.today()).year + 1
    r["ano_fabricacao"] = fab if fab and fab <= ano_max else None
    r["ano_modelo"] = mod if mod and mod <= ano_max else None

    m = RE_KM.search(t)
    if m:
        k = int((m.group(1) or m.group(2)).replace(".", ""))
        r["km"] = k if k < 2_000_000 else None
    r["combustivel"] = next((nome for padrao, nome in COMBUSTIVEIS if re.search(padrao, t, re.I)), None)
    m = re.search(r"\bcor\s*:?\s*([a-zç]+)", t, re.I)
    r["cor"] = next((v for k, v in CORES.items() if m and sa(m.group(1)).startswith(k)), None)
    m = RE_PLACA_FINAL.search(t) or RE_PLACA_MASC.search(t)
    if m:
        r["placa_final"] = m.group(1)
    else:
        m = RE_PLACA.search(t)
        r["placa_final"] = m.group(2)[-1] if m and m.group(2)[-1].isdigit() else None
    r["condicao"] = condicao(t)

    m = RE_AVAL.search(t)
    r["avaliacao"] = num_br(m.group(1)) if m else None
    m = RE_LANCE.search(t)
    r["lance_inicial"] = num_br(m.group(1)) if m else None
    if not r["lance_inicial"] and r["avaliacao"]:
        m = RE_PCT.search(t)
        r["lance_inicial"] = round(r["avaliacao"] * int(m.group(1)) / 100, 2) if m else r["avaliacao"]
    for k in ("avaliacao", "lance_inicial"):
        if r[k] is not None and not (50 <= r[k] <= 50_000_000):
            r[k] = None

    # local: "pátio ... em Cidade/UF", "Cidade-UF", "Comarca de Cidade"
    uf, cid = None, None
    for mm in re.finditer(r"\b(?:em|de|na\s+cidade\s+de|munic[ií]pio\s+de|comarca\s+de|p[aá]tio[^,;]{0,40}?em)\s+"
                          r"([A-ZÀ-Ú][\wÀ-ú' ]{2,40}?)\s*[/\-–]\s*([A-Z]{2})\b", t):
        if mm.group(2) in UFS27 and cidades:
            c = cidades.oficial(mm.group(1), mm.group(2))
            if c:
                uf, cid = mm.group(2), c
                break
    if not uf and uf_padrao and cidades:
        for mm in re.finditer(r"(?:comarca|munic[ií]pio|cidade)\s+de\s+([A-ZÀ-Ú][\wÀ-ú' ]{2,40})", t, re.I):
            c = cidades.oficial(mm.group(1), uf_padrao)
            if c:
                cid = c
                break
        uf = uf_padrao
    r["uf"], r["cidade"] = uf, cid
    m = re.search(r"p[aá]tio\s+(?:d[oa]\s+)?([^,;.]{3,60})", t, re.I)
    r["patio"] = limpar_privado(m.group(1)).strip()[:60] if m and not re.match(r"(em|no|na)\s", m.group(1), re.I) else None

    ds = datas(t, hoje)
    r["data_leilao"] = ds[0].isoformat() if ds else None
    r["data_fim"] = ds[-1].isoformat() if len(ds) > 1 else None
    r["descricao"] = limpar_privado(t)[:600]
    return r


def extrair_lotes(texto, catalogo=None, cidades=None, uf_padrao=None, hoje=None):
    """lista de lotes de veículo (dicts) + dados do edital"""
    t = re.sub(r"<[^>]+>", " ", texto or "")
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    edital = {"processo": (RE_CNJ.search(t).group(0) if RE_CNJ.search(t) else None)}
    m = re.search(r"leiloeir[oa]\s+(?:p[uú]blic[oa]\s+)?(?:oficial\s+)?:?\s*([A-ZÀ-Ú][a-zà-ú]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][a-zà-ú]+){1,5})", t)
    edital["leiloeiro"] = m.group(1) if m else None
    ds_gerais = datas(t, hoje)
    lotes = []
    for n, seg in _segmentos(t):
        r = ler_lote(seg, catalogo, cidades, uf_padrao, hoje)
        if not r:
            continue
        if not r["data_leilao"] and ds_gerais:            # data do edital vale para os lotes sem data própria
            r["data_leilao"] = ds_gerais[0].isoformat()
            r["data_fim"] = ds_gerais[-1].isoformat() if len(ds_gerais) > 1 else None
        if not r["uf"] and uf_padrao:
            r["uf"] = uf_padrao
        r["lote"] = n
        lotes.append(r)
    return edital, lotes


def texto_busca(*partes):
    return re.sub(r"\s+", " ", sa(" ".join(p for p in partes if p)))[:2000]
