"""
SOLIDUNS — LEILÕES DE BANCOS E LEILOEIROS — v1.1 (10/10/2026)

v1.1 (veículos): cada lote leva a MODALIDADE (SQL 114) — judicial (tribunal/vara/praça judicial), venda direta
(tomada de preço / venda direta) ou extrajudicial — e o endereço da FOTO do lote no site do leiloeiro (SQL 116:
só o link; a imagem não é copiada). Título sem ano repetido ("John Deere 350G 2020"); "Mini Trailer" não é a
marca Mini; reboque/trailer vai para Caminhões.
v1.1 (VIP Leilões): carros RECUPERADOS DE FINANCIAMENTO (alienação fiduciária) da VIP Leilões — a página
/pesquisa/recuperadofinanciamento e a lista que ela mesma pede ao site (?handler=pesquisar, 12 por página) + a ficha de
cada lote NOVO (comitente, km, combustível, cidade). Comitente banco/financeira/consórcio -> fonte 'banco'
(Origem "Bancos (retomados)", selo do banco); os demais (seguradora, particular) -> fonte 'leiloeiro'. Modalidade
extrajudicial. Lance = o valor atual do lote (já com os lances dados), nunca abaixo do valor inicial.

Rotina "Leilões - bancos e leiloeiros" (leiloes-leiloeiros.yml), todo dia. Custo zero (sem IA paga).
Lê as páginas PÚBLICAS de três leiloeiros oficiais que vendem imóveis e veículos de bancos:
  - Portal Zuk    (portalzuk.com.br)    — imóveis por comitente (Bradesco, Itaú, Santander, Sicoob...) e veículos
  - Mega Leilões  (megaleiloes.com.br)  — imóveis (banco pelo ícone do cartão) e veículos
  - Superbid      (exchange.superbid.net) — imóveis (Banco Inter, Daycoval...) e veículos
Regras de boa vizinhança: respeita o robots.txt de cada site (página proibida não é aberta), identifica o robô
(SOLIDUNS-coletor), espera 2 s entre páginas, não passa por CAPTCHA nem por login.

Grava:
  - IMÓVEIS de bancos/financeiras (menos a Caixa, que tem lista própria) e só os EXTRAJUDICIAIS em
    public.leiloes_radar (fonte 'zuk' | 'mega' | 'superbid'; orgao = banco vendedor; leiloeiro = o site).
    Os judiciais desses sites ficam de fora: o Radar já os lê do Diário de Justiça.
  - VEÍCULOS em public.veiculos_leiloes (fonte 'leiloeiro'), marca/modelo casados com a Tabela FIPE.
Saída da lista: imóvel não visto há 3 dias (com a leitura daquele site completa) ou com a data do leilão passada.

Repositório PÚBLICO: nenhuma chave aqui — SUPABASE_URL e SUPABASE_SERVICE_KEY vêm dos Secrets do GitHub.
"""

import datetime as dt
import os
import re
import sys
import time
import urllib.parse as up
import urllib.robotparser as rp

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leiloeiros_regras as R  # noqa: E402
from veiculos_regras import Catalogo, Cidades, ler_lote, limpar_privado, texto_busca, tipo_pelo_texto  # noqa: E402
from veiculos_regras import VERSAO_REGRAS as VERSAO_VEI  # noqa: E402

VERSAO = "1.1"
UA = "SOLIDUNS-coletor/1.0 (+contato@soliduns.com.br)"
TESTE = bool(os.environ.get("LEILOES_TESTE"))
PAUSA = float(os.environ.get("LEILOEIROS_PAUSA") or (0 if TESTE else 2))
TEMPO_MAX = int(os.environ.get("LEILOEIROS_TEMPO_MAX") or 2400)          # segundos (a rotina tem 55 min)
MAX_PAG = int(os.environ.get("LEILOEIROS_MAX_PAGINAS") or 120)          # por site
ZUK_CIDADES = int(os.environ.get("LEILOEIROS_ZUK_CIDADES") or 60)     # páginas de cidade (extras) no Zuk
VEI_PAG = int(os.environ.get("LEILOEIROS_VEICULOS_PAGINAS") or 15)     # por categoria de veículo
DIAS_SEM_VER = int(os.environ.get("LEILOEIROS_DIAS_SEM_VER") or 3)
FONTES = [f.strip() for f in (os.environ.get("LEILOEIROS_FONTES") or "zuk,mega,superbid,vip").split(",") if f.strip()]
VIP_PAG = int(os.environ.get("LEILOEIROS_VIP_PAGINAS") or 25)            # páginas da lista (12 lotes cada)
VIP_FICHAS = int(os.environ.get("LEILOEIROS_VIP_FICHAS") or 260)         # fichas de lotes novos por execução
BASES = {
    "zuk": (os.environ.get("ZUK_BASE") or "https://www.portalzuk.com.br").rstrip("/"),
    "mega": (os.environ.get("MEGA_BASE") or "https://www.megaleiloes.com.br").rstrip("/"),
    "superbid": (os.environ.get("SUPERBID_BASE") or "https://exchange.superbid.net").rstrip("/"),
    "vip": (os.environ.get("VIP_BASE") or "https://www.vipleiloes.com.br").rstrip("/"),
}
NOMES = {"zuk": "Portal Zuk", "mega": "Mega Leilões", "superbid": "Superbid", "vip": "VIP Leilões"}
SITES = {"zuk": "https://www.portalzuk.com.br", "mega": "https://www.megaleiloes.com.br", "superbid": "https://www.superbid.net",
         "vip": "https://www.vipleiloes.com.br"}
UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
       "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
COLS_RADAR = ("id", "fonte", "numero", "uf", "cidade", "bairro", "endereco", "tipo", "tipo_original", "preco", "avaliacao",
              "desconto", "financiamento", "area_total", "area_privativa", "area_terreno", "quartos", "vagas", "descricao",
              "modalidade_venda", "link", "data_lista", "modalidade", "orgao", "leiloeiro", "site_leiloeiro", "praca1",
              "praca2", "lance1", "lance2", "ocupacao_edital", "situacao", "visto_em", "saiu_em", "coletado_em")

relatorio = []
inicio_relogio = time.time()


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    relatorio.append(m)


def sem_tempo():
    return time.time() - inicio_relogio > TEMPO_MAX


# ------------------------------------------------------------ internet (robots.txt + pausa)
class Leitor:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
        self.regras = {}
        self.paginas = 0
        self.ultima = 0.0

    def permitido(self, url):
        p = up.urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self.regras:
            regra = rp.RobotFileParser()
            try:
                r = self.s.get(host + "/robots.txt", timeout=30)
                regra.parse(r.text.splitlines() if r.status_code < 400 else [])   # 4xx: tudo permitido (RFC 9309)
            except requests.RequestException:
                regra.parse([])
            self.regras[host] = regra
        return self.regras[host].can_fetch(UA, url)

    def pagina(self, url, dados=None, cabecalhos=None):
        """(status, texto); status 'robots' quando o robots.txt proíbe. Com 'dados': envia o formulário (POST),
           como o navegador faz na página (mesma sessão/cookie)."""
        if not self.permitido(url):
            return "robots", ""
        espera = PAUSA - (time.time() - self.ultima)
        if espera > 0:
            time.sleep(espera)
        for tentativa in range(2):
            try:
                r = (self.s.post(url, data=dados, headers=cabecalhos or {}, timeout=60) if dados is not None
                     else self.s.get(url, timeout=60))
                self.ultima = time.time()
                self.paginas += 1
                if r.status_code == 429:
                    return 429, ""
                enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
                return r.status_code, r.content.decode(enc or "utf-8", "replace")
            except requests.RequestException as e:
                self.ultima = time.time()
                erro = type(e).__name__
                time.sleep(0 if TESTE else 5)
        return erro, ""


def na_base(fonte, url):
    """endereço lido na página (sempre o domínio real) -> mesmo caminho na base configurada (teste usa servidor local)"""
    p = up.urlparse(url)
    return BASES[fonte] + p.path + (("?" + p.query) if p.query else "")


# ------------------------------------------------------------ Supabase
def sb():
    url, ch = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not ch:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": ch, "Content-Type": "application/json"}
    if not ch.startswith("sb_"):
        cab["Authorization"] = f"Bearer {ch}"
    return url.rstrip("/"), cab


def sb_get(caminho):
    url, cab = sb()
    saida, ini = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{caminho}", headers=dict(cab, Range=f"{ini}-{ini + 999}"), timeout=60)
        r.raise_for_status()
        lote = r.json()
        saida += lote
        if len(lote) < 1000:
            return saida
        ini += 1000


def sb_upsert(tabela, linhas, conflito="id"):
    if not linhas:
        return
    url, cab = sb()
    for i in range(0, len(linhas), 300):
        lote = linhas[i:i + 300]
        for tentativa in range(3):
            r = requests.post(f"{url}/rest/v1/{tabela}?on_conflict={conflito}", json=lote,
                              headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
            if r.status_code < 300:
                break
            if tentativa == 2:
                raise RuntimeError(f"gravação em {tabela} recusada: {r.status_code} {r.text[:200]}")
            time.sleep(0 if TESTE else 10)


def sb_patch(tabela, filtros, dados):
    """PATCH com filtros (dict do PostgREST); devolve quantas linhas mudaram"""
    url, cab = sb()
    r = requests.patch(f"{url}/rest/v1/{tabela}", params=filtros, json=dados,
                       headers=dict(cab, Prefer="return=representation"), timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"atualização de {tabela} recusada: {r.status_code} {r.text[:200]}")
    return len(r.json())


def sb_rpc(nome):
    url, cab = sb()
    r = requests.post(f"{url}/rest/v1/rpc/{nome}", json={}, headers=cab, timeout=300)
    return r.json() if r.status_code < 300 else f"erro {r.status_code}"


# ------------------------------------------------------------ imóvel -> linha do Radar
def cidade_oficial(cidades, nome, uf):
    if not nome or uf not in UFS:
        return None
    return cidades.oficial(nome, uf) or nome.strip()[:80]


def linha_radar(fonte, codigo, uf, comitente, agora, hoje, **d):
    x = {k: None for k in COLS_RADAR}
    x.update(d)
    x.update(id=f"{fonte}-{codigo}", fonte=fonte, numero=str(codigo)[:40], uf=uf, orgao=comitente[:120],
             leiloeiro=NOMES[fonte], site_leiloeiro=SITES[fonte], modalidade="extrajudicial", data_lista=str(hoje),
             situacao="ativo", visto_em=agora, saiu_em=None, coletado_em=agora)
    p, a = x.get("preco"), x.get("avaliacao")
    if a and p and a > 0:
        x["desconto"] = round(100 * (1 - p / a), 2)
        if not (-100 < x["desconto"] < 100):
            x["avaliacao"] = x["desconto"] = None
    if x.get("descricao"):
        x["descricao"] = limpar_privado(x["descricao"])[:1500]
    if not x.get("tipo"):
        x["tipo"] = "outros"
    return x


def pracas_zuk(pracas):
    p1 = p2 = l1 = l2 = None
    for p in pracas:
        rot = R.sa(p.get("rotulo"))
        if rot.startswith("2"):
            p2, l2 = p.get("data"), p.get("valor")
        else:
            p1, l1 = p1 or p.get("data"), l1 or p.get("valor")
    return p1, p2, l1, l2


def imovel_zuk(x, cidades, agora, hoje):
    if R.sa(x.get("status")) in ("vendido", "encerrado", "suspenso", "cancelado") or x.get("veiculo"):
        return None
    chave, _ = R.banco(x.get("comitente"))
    if not chave or chave == "caixa" or x.get("uf") not in UFS:
        return None
    ativa = [p for p in x["pracas"] if p.get("valor")]
    if not ativa:
        return None
    atual = ativa[-1]
    preco = atual["valor"]
    aval = None
    if atual.get("desconto"):
        aval = round(preco / (1 - atual["desconto"] / 100), 2)
    elif R.sa(atual.get("rotulo")).startswith("1"):
        aval = preco                                     # 1º leilão (Lei 9.514): lance mínimo = valor do imóvel
    p1, p2, l1, l2 = pracas_zuk(x["pracas"])
    rot = R.sa(atual.get("rotulo"))
    venda = "2º leilão" if rot.startswith("2") else "1º leilão" if rot.startswith("1") else "Leilão online"
    tot, priv, ter = R.areas(" | ".join(x.get("infos") or []))
    tit = re.sub(r"^\s*em leil[aã]o\s*-\s*", "", x.get("titulo") or "", flags=re.I)
    desc = " · ".join(filter(None, [tit, x.get("tipo_txt"), ", ".join(x.get("infos") or [])]))
    return linha_radar("zuk", x["codigo"], x["uf"], x["comitente"], agora, hoje,
                       cidade=cidade_oficial(cidades, x.get("cidade"), x["uf"]), bairro=x.get("bairro"),
                       endereco=x.get("endereco"), tipo=R.tipo_imovel(x.get("tipo_txt")), tipo_original=(x.get("tipo_txt") or "")[:60],
                       preco=preco, avaliacao=aval, financiamento=R.financiamento(x.get("descricao")),
                       area_total=tot, area_privativa=priv, area_terreno=ter, quartos=R.quartos(x.get("descricao")),
                       vagas=R.vagas(x.get("descricao")), descricao=desc, modalidade_venda=venda, link=x["link"],
                       praca1=p1, praca2=p2, lance1=l1, lance2=l2,
                       ocupacao_edital=R.ocupacao((x.get("status") or "") + " " + (x.get("descricao") or "")))


def imovel_mega(x, cidades, agora, hoje):
    if x.get("modalidade") == "judicial" or "/imoveis/" not in x.get("link", ""):
        return None
    if R.sa(x.get("status")).startswith(("encerrad", "vendid", "suspens", "cancelad")):
        return None
    chave, nome = R.banco(R.mega_banco(x.get("icone")))
    if not chave or chave == "caixa" or x.get("uf") not in UFS:
        return None
    pr = [p for p in x["pracas"] if p.get("valor")]
    if not pr:
        return None
    ativas = [p for p in pr if p.get("ativa")]
    atual = (ativas or pr)[0]
    p1 = p2 = l1 = l2 = None
    for p in pr:
        if R.sa(p.get("rotulo")).startswith("2"):
            p2, l2 = p.get("data"), p.get("valor")
        else:
            p1, l1 = p.get("data"), p.get("valor")
    aval = l1 if (l2 and l1) else None                  # 1ª praça (Lei 9.514) = valor do imóvel
    venda = "2º leilão" if R.sa(atual.get("rotulo")).startswith("2") else "1º leilão" if R.sa(atual.get("rotulo")).startswith("1") else "Leilão online"
    tot, priv, ter = R.areas(x.get("titulo"))
    return linha_radar("mega", x["codigo"], x["uf"], nome, agora, hoje,
                       cidade=cidade_oficial(cidades, x.get("cidade"), x["uf"]), bairro=x.get("bairro"),
                       endereco=None, tipo=R.tipo_imovel(x.get("titulo")), tipo_original=(x.get("titulo") or "").split(" - ")[0][:60],
                       preco=atual["valor"], avaliacao=aval, financiamento=R.financiamento(x.get("descricao")),
                       area_total=tot, area_privativa=priv, area_terreno=ter, quartos=R.quartos(x.get("titulo")),
                       vagas=R.vagas(x.get("titulo")), descricao=x.get("titulo"), modalidade_venda=venda, link=x["link"],
                       praca1=p1, praca2=p2, lance1=l1, lance2=l2, ocupacao_edital=R.ocupacao(x.get("descricao")))


def imovel_superbid(x, cidades, agora, hoje):
    if not x.get("aberto"):
        return None
    chave, _ = R.banco(x.get("vendedor"))
    if not chave or chave == "caixa" or x.get("uf") not in UFS:
        return None
    preco = x.get("lance_inicial") or x.get("preco")
    if not preco:
        return None
    tipo = R.tipo_imovel(x.get("categoria"))
    if tipo in ("outros", "terreno_urbano", "casa") or "condom" in R.sa(x.get("titulo")):
        t2 = R.tipo_imovel(x.get("titulo"))
        if t2 != "outros" and (tipo == "outros" or t2 in ("casa_condominio", "lote_condominio", "sobrado")):
            tipo = t2
    tot, priv, ter = R.areas(x.get("titulo"))
    texto = (x.get("titulo") or "") + " " + (x.get("descricao") or "")
    return linha_radar("superbid", x["codigo"], x["uf"], x["vendedor"], agora, hoje,
                       cidade=cidade_oficial(cidades, x.get("cidade"), x["uf"]), bairro=None, endereco=None,
                       tipo=tipo, tipo_original=(x.get("categoria") or "")[:60], preco=float(preco),
                       avaliacao=R.avaliado(x.get("descricao")), financiamento=R.financiamento(texto),
                       area_total=tot, area_privativa=priv, area_terreno=ter, quartos=R.quartos(texto), vagas=R.vagas(texto),
                       descricao=texto.strip(), modalidade_venda=x.get("modalidade_txt") or "Venda online", link=x["link"],
                       praca1=x.get("fim"), praca2=None, lance1=float(preco), lance2=None, ocupacao_edital=R.ocupacao(texto))


# ------------------------------------------------------------ veículo -> linha de veiculos_leiloes
RE_FIPE_INFORMADA = re.compile(r"pre[cç]o\s+fipe\s*:?\s*R\$\s*([\d.]+(?:,\d{2})?)", re.I)
PALAVRAS_NAO_MARCA = {"carro", "moto", "motocicleta", "caminhao", "onibus", "direitos", "sobre", "veiculo", "trator", "com",
                      "cavalo", "mecanico", "veiculos", "outros", "em", "leilao", "utilitario", "camioneta", "caminhonete", "pickup", "van", "furgao", "sucata"}
# "Mini Trailer", "Mini Carregadeira", "Mini Escavadeira": "Mini" aqui não é a marca
RE_MINI_NAO_MARCA = re.compile(r"^mini\s+(?:trailer|reboque|carregadeira|escavadeira|trator|van|onibus|ônibus|buggy|moto|caminh)", re.I)
RE_JUDICIAL = re.compile(r"tribunal|\bvara\b|justi[cç]a|ju[ií]zo|\btj[a-z]{2,3}\b|\btr[ft]\s?\d|fal[eê]ncia|recupera[cç][aã]o\s+judicial|"
                         r"leil[aã]o\s+judicial|pra[cç]a\s+judicial|execu[cç][aã]o\s+fiscal", re.I)
RE_VENDA_DIRETA = re.compile(r"tomada\s+de\s+pre[cç]o|venda\s+direta|compra\s+direta|mercado\s+balc[aã]o|compre\s+j[aá]", re.I)


def modalidade_lote(fonte, x):
    """judicial | venda_direta | extrajudicial (o judicial vale mesmo quando o tribunal vende por proposta)"""
    if fonte == "mega":
        return x.get("modalidade") or "extrajudicial"
    if fonte == "superbid":
        if x.get("judicial") or RE_JUDICIAL.search(" ".join((x.get("evento") or "", x.get("vendedor") or ""))):
            return "judicial"
        return "venda_direta" if RE_VENDA_DIRETA.search(x.get("modalidade_txt") or "") else "extrajudicial"
    if RE_JUDICIAL.search(x.get("comitente") or ""):                    # zuk
        return "judicial"
    return "venda_direta" if RE_VENDA_DIRETA.search(" ".join((x.get("tipo_txt") or "", x.get("status") or ""))) else "extrajudicial"


def foto_ok(u):
    return u if isinstance(u, str) and re.match(r"^https://[\w.-]+/[^\s\"'<>]{4,400}$", u) else None


RE_MM_MISTO = re.compile(r"\b([A-Za-zÀ-ú][A-Za-zÀ-ú.\-]{1,15}(?:\s[A-Za-z]{2,10})?)\s*/\s*([A-Za-z0-9][\w .\-]{1,30})")


def marca_no_texto(titulo, descricao, catalogo, tipo):
    """marca/modelo quando o padrão do Renavam (MAIÚSCULAS) não aparece:
       'Carro, Volkswagen/Fox 1.6' (Zuk) ou 'Carro Volkswagen Passat LS - 1982' (Mega).
       Só aceita o nome exato de uma marca da Tabela FIPE (ou apelido conhecido). -> (tipo, marca, modelo)"""
    for m in RE_MM_MISTO.finditer((descricao or "")[:400]):
        if RE_MINI_NAO_MARCA.match(m.group(0)):
            continue
        t, mm = catalogo.marca(m.group(1), tipo)
        if mm and (not tipo or t == tipo):
            return t, mm, m.group(2).strip()
    for seg in (titulo or "").split(" - ")[:2] + [(descricao or "")[:250]]:
        p = [w for w in re.split(r"[\s,]+", seg) if w]
        for i in range(len(p)):
            for n in (2, 1):
                cand = " ".join(p[i:i + n])
                if len(cand) < 2 or R.sa(cand) in PALAVRAS_NAO_MARCA or RE_MINI_NAO_MARCA.match(" ".join(p[i:i + 2])):
                    continue
                t, mm = catalogo.marca(cand, tipo)
                if mm and (not tipo or t == tipo):
                    resto = re.split(r"(?i)\b(?:ano|cor|placa|chassi|renavam|com|em)\b", " ".join(p[i + n:i + n + 4]))[0].strip(" ,.-")
                    return t, mm, resto
    return None, None, ""


def sem_ano_final(t):
    return re.sub(r"(?:[\s\-/]*(?:19|20)\d\d)+\s*$", "", t or "").strip(" -/") or None


def titulo_veiculo(linha, original):
    """'Volkswagen Fox 2014' (como no DJEN); sem marca, o título do site sem o endereço"""
    ano = linha.get("ano_modelo") or linha.get("ano_fabricacao")
    if linha.get("marca"):
        mod = linha.get("familia") or (linha.get("modelo_texto") or "").split("/", 1)[-1][:30]
        mod = sem_ano_final(mod) if mod and mod != linha.get("modelo_texto") else None
        if mod and mod.isupper() and len(mod) > 3:              # "PUNTO TURBO T-JET" -> "Punto Turbo T-Jet"
            mod = " ".join(w if re.search(r"\d", w) and len(w) <= 6 else w.title() for w in mod.split())
        t = " ".join(x for x in (linha["marca"], mod) if x)
        return (t + (f" {ano}" if ano else ""))[:90]
    t = re.sub(r"\s+em leil[aã]o\s+-.*$", "", original or "", flags=re.I)
    t = re.sub(r"^ve[ií]culos?\s*-\s*", "", t, flags=re.I).strip()
    t = re.sub(r"\b((?:19|20)\d\d)(?:\s*/?\s*\1\b)+", r"\1", t)     # "2020 2020" -> "2020"
    if not t or R.sa(t) in ("outros", "veiculo", "veiculos"):
        t = {"carro": "Veículo", "moto": "Motocicleta", "caminhao": "Caminhão/ônibus", "maquina": "Máquina"}.get(linha["tipo"], "Veículo")
        t += f" {ano}" if ano else ""
    return t[:90]


def tipo_categoria(txt):
    s = R.sa(txt)
    if re.search(r"aeronave|aviao|helicoptero|barco|embarca|lancha|jet ?ski|navio", s):
        return "fora"
    if re.search(r"trator|maquina|agricol|colheitadeira|escavadeira|retroescavadeira|carregadeira|empilhadeira", s):
        return "maquina"
    if re.search(r"caminh|onibus|cavalo mec|carreta|reboque|semirreboque|micro-?onibus|trailer", s):
        return "caminhao"
    if re.search(r"\bmoto|motocicl|motoneta|scooter|quadriciclo|triciclo", s):
        return "moto"
    if re.search(r"carro|automove|utilitar|picape|pickup|caminhonete|camioneta|sedan|hatch|suv\b|\bvan\b", s):
        return "carro"
    return None


def linha_veiculo(fonte, x, texto, tipo_cat, catalogo, cidades, agora, hoje, uf, cidade, lance, data_leilao, data_fim):
    r = ler_lote("LOTE 1 - veículo " + texto, catalogo, cidades, uf, hoje) or {}
    tipo = tipo_cat or r.get("tipo") or tipo_pelo_texto(texto) or "carro"
    if r.get("tipo") and r["tipo"] != tipo:
        r.update(marca_codigo=None, marca=None, familia=None, modelo_codigo=None)
    if not r.get("marca_codigo") and catalogo:
        t, m, resto = marca_no_texto(x.get("titulo"), texto, catalogo, tipo)
        if m:
            fam, cod = catalogo.modelo(t, m["codigo"], resto)
            r.update(marca_codigo=m["codigo"], marca=m["nome"], familia=fam, modelo_codigo=cod,
                     modelo_texto=(m["nome"] + "/" + resto)[:80] if resto else r.get("modelo_texto"))
    linha = {k: r.get(k) for k in ("marca_codigo", "marca", "modelo_codigo", "familia", "modelo_texto", "ano_fabricacao",
                                    "ano_modelo", "combustivel", "km", "cor", "placa_final", "patio", "avaliacao")}
    linha.update(id=f"{fonte}-{x['codigo']}", fonte="leiloeiro", origem_nome=NOMES[fonte], anunciante_sigla=fonte, tipo=tipo,
                 condicao=r.get("condicao") or "nao_informado", uf=uf if uf in UFS else None,
                 cidade=cidade_oficial(cidades, cidade, uf) if uf in UFS else None,
                 lance_inicial=lance if lance and 50 <= lance <= 50_000_000 else None,
                 data_leilao=data_leilao, data_fim=data_fim, lote=str(x["codigo"])[:40], processo=None,
                 leiloeiro=NOMES[fonte], url=x["link"],
                 descricao=limpar_privado(texto)[:600], situacao="ativo", atualizado_em=agora,
                 modalidade=modalidade_lote(fonte, x), foto_url=foto_ok(x.get("foto")))
    if not linha.get("ano_modelo") and not linha.get("ano_fabricacao"):    # "Carro Volkswagen Passat LS - 1982", "... - 2013/2014"
        m = re.search(r"\b(19[5-9]\d|20[0-4]\d)(?:\s*/\s*(19[5-9]\d|20[0-4]\d))?\s*$", x.get("titulo") or "")
        if m:
            fab = int(m.group(1)); mod = int(m.group(2)) if m.group(2) else fab
            if fab <= hoje.year + 1 and 0 <= mod - fab <= 1:
                linha.update(ano_fabricacao=fab, ano_modelo=mod)
    linha["titulo"] = titulo_veiculo(linha, x.get("titulo"))
    if linha.get("avaliacao") is not None and not (50 <= linha["avaliacao"] <= 50_000_000):
        linha["avaliacao"] = None
    m = RE_FIPE_INFORMADA.search(texto)
    if m and not linha.get("modelo_codigo"):              # FIPE informada pelo leiloeiro (nosso casamento não achou a versão)
        v = R.num_br(m.group(1))
        if v and v >= 1000:
            linha.update(fipe_valor=v, fipe_codigo=None, fipe_referencia="informada pelo leiloeiro")
    linha["texto_busca"] = texto_busca(linha["titulo"], linha.get("marca"), linha.get("familia"), linha.get("modelo_texto"),
                                       linha.get("cidade"), linha.get("patio"), linha["descricao"], NOMES[fonte])
    return linha


def datas_leilao(datas, hoje):
    """(data do próximo leilão, última data) — com 1ª e 2ª praça, a 1ª que já passou não é a do card"""
    ds = sorted(d for d in datas if d)
    if not ds:
        return None, None
    h = str(hoje)
    prox = next((d for d in ds if d >= h), ds[-1])
    return prox, (ds[-1] if ds[-1] != prox else None)


def veiculo_zuk(x, catalogo, cidades, agora, hoje):
    if not x.get("veiculo") or R.sa(x.get("status")) in ("vendido", "encerrado", "suspenso", "cancelado"):
        return None
    pr = [p for p in x["pracas"] if p.get("valor")]
    tipo = tipo_categoria(x.get("descricao")[:120] + " " + (x.get("titulo") or ""))
    if tipo == "fora":
        return None
    d1, d2 = datas_leilao([p.get("data") for p in x["pracas"]], hoje)
    texto = (x.get("titulo") or "") + ". " + (x.get("descricao") or "")
    return linha_veiculo("zuk", x, texto, tipo, catalogo, cidades, agora, hoje, x.get("uf"), x.get("cidade"),
                         pr[-1]["valor"] if pr else None, d1, d2)


def veiculo_mega(x, catalogo, cidades, agora, hoje):
    if "/veiculos/" not in x.get("link", "") or R.sa(x.get("status")).startswith(("encerrad", "vendid", "suspens", "cancelad")):
        return None
    cat = x["link"].split("/veiculos/", 1)[1].split("/", 1)[0]
    tipo = tipo_categoria(cat + " " + (x.get("titulo") or ""))
    if tipo == "fora":
        return None
    if tipo == "caminhao" and tipo_categoria(x.get("titulo")) == "maquina":
        tipo = "maquina"
    pr = [p for p in x["pracas"] if p.get("valor")]
    ativas = [p for p in pr if p.get("ativa")]
    atual = (ativas or pr or [{}])[0]
    d1, d2 = datas_leilao([p.get("data") for p in x["pracas"]], hoje)
    v = linha_veiculo("mega", x, (x.get("titulo") or "") + ". " + (x.get("descricao") or ""), tipo, catalogo, cidades,
                      agora, hoje, x.get("uf"), x.get("cidade"), atual.get("valor"), d1, d2)
    if len(pr) >= 2 and not v.get("avaliacao") and pr[0].get("valor") and 50 <= pr[0]["valor"] <= 50_000_000:
        v["avaliacao"] = pr[0]["valor"]                    # 1ª praça = valor de avaliação (2ª = metade, no judicial)
    return v


def veiculo_superbid(x, catalogo, cidades, agora, hoje):
    if not x.get("aberto"):
        return None
    tipo = tipo_categoria((x.get("grupo") or "") + " " + (x.get("categoria") or "")) or tipo_categoria(x.get("titulo"))
    if tipo == "fora":
        return None
    lance = x.get("lance_inicial") or x.get("preco")
    texto = (x.get("titulo") or "") + ". " + (x.get("categoria") or "") + ". " + (x.get("descricao") or "")
    return linha_veiculo("superbid", x, texto, tipo, catalogo, cidades, agora, hoje, x.get("uf"), x.get("cidade"),
                         float(lance) if lance else None, x.get("fim"), None)


# ------------------------------------------------------------ leitura de cada site
class Resultado:
    def __init__(self, fonte):
        self.fonte, self.imoveis, self.veiculos = fonte, {}, {}
        self.completo = {"imoveis": True, "veiculos": True}          # leitura inteira? (só então quem sumiu sai)
        self.paginas, self.lidos, self.avisos = 0, 0, []
        self.vendidos = set()

    def aviso(self, m):
        self.avisos.append(m)
        log(f"    aviso: {m}")


def abrir(leitor, res, url, parte="imoveis", essencial=True, dados=None, cabecalhos=None):
    """texto da página; '' se não deu para ler; None = parar este site (tempo/teto)"""
    if sem_tempo():
        res.completo = {"imoveis": False, "veiculos": False}
        res.aviso("tempo máximo da rotina atingido — o resto fica para amanhã")
        return None
    if res.paginas >= MAX_PAG:
        if essencial:
            res.completo[parte] = False
        res.aviso(f"teto de {MAX_PAG} páginas atingido")
        return None
    st, txt = leitor.pagina(url, dados, cabecalhos)
    res.paginas += 1
    if st == "robots":
        res.aviso(f"robots.txt proíbe {up.urlparse(url).path} — página não lida")
        return ""
    if st == 429:
        res.completo = {"imoveis": False, "veiculos": False}
        res.aviso("o site pediu para diminuir o ritmo (HTTP 429) — leitura deste site encerrada por hoje")
        raise StopIteration
    if st != 200:
        if essencial:
            res.completo[parte] = False
        res.aviso(f"{up.urlparse(url).path}: HTTP {st}")
        return ""
    return txt


def ler_zuk(leitor, catalogo, cidades, agora, hoje):
    """1ª página -> páginas de cada banco comitente + 4 tipos + veículos (essenciais); depois páginas de cidade
    onde os bancos têm imóvel (extras: cada página do Zuk mostra só 30 cartões, as cidades ampliam a cobertura)"""
    res, b = Resultado("zuk"), BASES["zuk"]
    inicial = b + "/leilao-de-imoveis"
    filas = [(inicial, "imoveis", True)]
    vistos, comitentes, cidades_links = set(), [], []

    def processar(txt):
        cards = R.zuk(txt)
        res.lidos += len(cards)
        for x in cards:
            if R.sa(x.get("status")) == "vendido":
                res.vendidos.add(f"zuk-{x['codigo']}")
                continue
            if x.get("veiculo"):
                v = veiculo_zuk(x, catalogo, cidades, agora, hoje)
                if v:
                    res.veiculos[v["id"]] = v
                continue
            i = imovel_zuk(x, cidades, agora, hoje)
            if i:
                res.imoveis[i["id"]] = i

    try:
        while filas:
            url, parte, essencial = filas.pop(0)
            if url in vistos:
                continue
            vistos.add(url)
            txt = abrir(leitor, res, url, parte, essencial)
            if txt is None:
                break
            processar(txt)
            if url == inicial:
                if not txt:
                    break
                for u in R.zuk_links_comitentes(txt):
                    chave, _ = R.banco(u.rsplit("/", 1)[-1].replace("-", " "))
                    if chave and chave != "caixa":
                        comitentes.append(na_base("zuk", u))
                if not comitentes:
                    res.completo["imoveis"] = False
                    res.aviso("nenhum banco comitente achado na página inicial (formato mudou?)")
                filas += [(u, "imoveis", True) for u in comitentes]
                filas += [(b + "/leilao-de-imoveis/t/todos-imoveis/" + t, "imoveis", True)
                          for t in ("residenciais", "comerciais", "rurais", "terrenos")]
                filas.append((b + "/leilao-de-veiculos", "veiculos", True))
            if "/leilao-de-imoveis/v/" in url:
                for u in R.zuk_links_cidades(txt):
                    u = na_base("zuk", u)
                    if u not in cidades_links and len(cidades_links) < ZUK_CIDADES:
                        cidades_links.append(u)
            if not filas and cidades_links:
                filas, cidades_links = [(u, "imoveis", False) for u in cidades_links], []
        log(f"    bancos comitentes no Zuk: {len(comitentes)}")
    except StopIteration:
        pass
    return res


def ler_mega(leitor, catalogo, cidades, agora, hoje):
    res, b = Resultado("mega"), BASES["mega"]
    try:
        for secao, teto in (("imoveis", MAX_PAG), ("veiculos", VEI_PAG)):
            ultima, n = 1, 1
            while n <= min(ultima, teto):
                txt = abrir(leitor, res, f"{b}/{secao}" + (f"?pagina={n}" if n > 1 else ""), secao)
                if txt is None:
                    break
                if n == 1:
                    ultima = R.mega_ultima_pagina(txt)
                cards = R.mega(txt)
                res.lidos += len(cards)
                if not cards:
                    break
                for x in cards:
                    if secao == "imoveis":
                        i = imovel_mega(x, cidades, agora, hoje)
                        if i:
                            res.imoveis[i["id"]] = i
                    else:
                        v = veiculo_mega(x, catalogo, cidades, agora, hoje)
                        if v:
                            res.veiculos[v["id"]] = v
                n += 1
            if ultima > teto:
                res.completo[secao] = False
                res.aviso(f"imóveis: {ultima} páginas, lidas {teto}")
    except StopIteration:
        pass
    return res


SUPERBID_VEICULOS = ["carros-motos", "caminhoes-onibus", "maquinas-pesadas-agricolas"]


def ler_superbid(leitor, catalogo, cidades, agora, hoje):
    res, b = Resultado("superbid"), BASES["superbid"]
    try:
        for cat, teto in [("imoveis", MAX_PAG)] + [(c, VEI_PAG) for c in SUPERBID_VEICULOS]:
            n, paginas = 1, 1
            while n <= min(paginas, teto):
                parte = "imoveis" if cat == "imoveis" else "veiculos"
                txt = abrir(leitor, res, f"{b}/categorias/{cat}?pageNumber={n}&pageSize=30&orderBy=endDate:asc", parte)
                if txt is None:
                    break
                ofertas, total = R.superbid(txt)
                if n == 1 and not ofertas and txt:
                    res.completo[parte] = False
                    res.aviso(f"{cat}: página sem a lista de ofertas (formato mudou ou acesso negado) — nada lido")
                    break
                paginas = max(1, -(-total // 30))
                res.lidos += len(ofertas)
                for x in ofertas:
                    if cat == "imoveis":
                        i = imovel_superbid(x, cidades, agora, hoje)
                        if i:
                            res.imoveis[i["id"]] = i
                    else:
                        v = veiculo_superbid(x, catalogo, cidades, agora, hoje)
                        if v:
                            res.veiculos[v["id"]] = v
                if not ofertas:
                    break
                n += 1
            if paginas > teto:
                res.completo["imoveis" if cat == "imoveis" else "veiculos"] = False
                res.aviso(f"{cat}: {paginas} páginas, lidas {teto}")
    except StopIteration:
        pass
    return res


def veiculo_vip(x, ficha, catalogo, cidades, agora, hoje):
    """cartão + ficha da VIP -> linha de veiculos_leiloes"""
    if re.search(r"vendid|encerrad|cancelad|retirad|suspens|condicional", R.sa(x.get("situacao") or "") + " " + R.sa(x.get("estado") or "")):
        return None
    marca = (x.get("marca") or "").strip()
    titulo = (x.get("titulo") or "").strip()
    modelo, _, anos = titulo.rpartition(" - ") if re.search(r" - \d{4}", titulo) else (titulo, "", "")
    anos = anos or (ficha.get("ano") or "").replace(" ", "")
    km = ficha.get("km")
    texto = (f"{marca.upper()}/{modelo.upper()} {anos}. " + (f"KM {km} km. " if km else "")
             + (f"Combustível {ficha['combustivel']}. " if ficha.get("combustivel") else "")
             + (f"Cor: {ficha['cor']}. " if ficha.get("cor") else "")
             + (f"Câmbio {ficha['cambio']}. " if ficha.get("cambio") else "")
             + (f"Procedência: {ficha['procedencia']}. " if ficha.get("procedencia") else "Procedência: recuperado de financiamento. ")
             + (f"Comitente: {ficha['comitente']}. " if ficha.get("comitente") else "")
             + (f"Placa final {x['placa_final']}. " if x.get("placa_final") else ""))
    cidade, uf = None, x.get("uf")
    m = re.search(r",\s*([^,]{2,60}?),\s*([A-Z]{2})\s*-\s*CEP", ficha.get("localizacao") or "")
    if m:
        cidade, uf = m.group(1).strip().title(), m.group(2)
    atual, inicial = x.get("valor_atual") or 0, x.get("valor_inicial") or 0
    lance = max(atual, inicial) or None
    item = {"codigo": x["codigo"], "link": x["link"], "titulo": f"{marca.title()} {modelo.title()} - {anos}".strip(" -"),
            "comitente": ficha.get("comitente") or "", "foto": x.get("foto")}
    tipo = tipo_categoria(modelo) if tipo_categoria(modelo) in ("maquina", "caminhao", "moto") else None
    fim = ficha.get("encerramento") if (ficha.get("encerramento") or "") > (x.get("inicio") or "") else None
    v = linha_veiculo("vip", item, texto, tipo, catalogo, cidades, agora, hoje, uf, cidade, lance, x.get("inicio"), fim)
    if re.search(r"sinistr", R.sa(ficha.get("procedencia") or "")) and v.get("condicao") in (None, "nao_informado", "circulacao"):
        v["condicao"] = "recuperavel"
    if x.get("placa_final") and not v.get("placa_final"):
        v["placa_final"] = x["placa_final"][:1]
    ch, nome = R.vip_banco(ficha.get("comitente"))
    v["modalidade"] = "judicial" if RE_JUDICIAL.search(ficha.get("comitente") or "") else "extrajudicial"
    if ch:
        v.update(fonte="banco", anunciante_sigla=ch, origem_nome=nome)
    elif ficha.get("comitente"):
        v["origem_nome"] = "VIP Leilões · " + ficha["comitente"].strip().title()[:60]
    v["texto_busca"] = texto_busca(v["titulo"], v.get("marca"), v.get("familia"), v.get("modelo_texto"), v.get("cidade"),
                                   v["descricao"], "VIP Leilões", ficha.get("comitente"), "recuperado financiamento banco")
    return v


VIP_CACHE = {}          # fichas já lidas em execuções anteriores (id -> campos gravados), preenchido em main()


def ler_vip(leitor, catalogo, cidades, agora, hoje):
    res, b = Resultado("vip"), BASES["vip"]
    res.completo["imoveis"] = False                       # a VIP aqui é só veículos
    fichas, conhecidas = 0, len(VIP_CACHE)
    try:
        pag = abrir(leitor, res, b + "/pesquisa/recuperadofinanciamento", "veiculos")
        if not pag:
            res.completo["veiculos"] = False
            return res
        alvo, dados = R.vip_formulario(pag)
        if not alvo or dados.get("Filtro.Procedencia") in (None, ""):
            res.completo["veiculos"] = False
            res.aviso("página sem o formulário de pesquisa (formato mudou) — nada lido")
            return res
        cab = {"Referer": SITES["vip"] + "/pesquisa/recuperadofinanciamento", "X-Requested-With": "XMLHttpRequest"}
        fila, vistos_pag, n = [b + alvo], set(), 0
        while fila and n < VIP_PAG:
            url = fila.pop(0)
            if url in vistos_pag:
                continue
            vistos_pag.add(url)
            n += 1
            txt = abrir(leitor, res, url, "veiculos", dados=dados, cabecalhos=cab)
            if txt is None:
                break
            cartoes, total, outras = R.vip_lotes(txt)
            if n == 1 and not cartoes:
                res.completo["veiculos"] = False
                res.aviso("lista de lotes vazia ou em outro formato — nada lido")
                break
            res.lidos += len(cartoes)
            for u in outras:
                cheio = b + u if u.startswith("/") else u
                if cheio not in vistos_pag and cheio not in fila:
                    fila.append(cheio)
            for x in cartoes:
                vid = "vip-" + x["codigo"]
                ficha = VIP_CACHE.get(vid)
                if ficha is None and fichas < VIP_FICHAS:
                    t2 = abrir(leitor, res, na_base("vip", x["link"]), "veiculos", essencial=False)
                    if t2 is None:
                        break
                    ficha = R.vip_detalhe(t2) if t2 else {}
                    fichas += 1
                    if ficha:
                        VIP_CACHE[vid] = ficha                     # o mesmo lote em outra página não reabre
                v = veiculo_vip(x, ficha or {}, catalogo, cidades, agora, hoje)
                if v:
                    res.veiculos[v["id"]] = v
            if n == 1:
                paginas = max(1, -(-total // 12))
                if paginas > VIP_PAG:
                    res.completo["veiculos"] = False
                    res.aviso(f"{paginas} páginas na lista, lidas {VIP_PAG}")
        log(f"    VIP: {fichas} ficha(s) de lote novo lidas; {conhecidas} já conhecidas de execuções anteriores")
    except StopIteration:
        pass
    return res


LEITORES = {"zuk": ler_zuk, "mega": ler_mega, "superbid": ler_superbid, "vip": ler_vip}


# ------------------------------------------------------------ principal
def gravar_veiculos(linhas):
    sem = [x for x in linhas if "fipe_valor" not in x]
    com = [x for x in linhas if "fipe_valor" in x]
    sb_upsert("veiculos_leiloes", sem)                   # mesmas colunas em cada envio (PostgREST usa as do 1º item)
    sb_upsert("veiculos_leiloes", com)


def main():
    log(f"SOLIDUNS — Leilões de bancos e leiloeiros v{VERSAO} ({R.VERSAO}, {VERSAO_VEI}) — {dt.datetime.now():%d/%m/%Y %H:%M} (UTC do servidor)")
    hoje = dt.date.today()
    agora_dt = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    agora = agora_dt.isoformat().replace("+00:00", "Z")
    sem_gravar = "--sem-gravar" in sys.argv
    cidades = Cidades()
    catalogo = None
    if not sem_gravar:
        marcas = sb_get("veiculos_fipe_marcas?select=tipo,codigo,nome")
        modelos = sb_get("veiculos_fipe_modelos?select=tipo,marca_codigo,codigo,nome,familia")
        catalogo = Catalogo(marcas, modelos)
        if not marcas:
            log("AVISO: catálogo FIPE vazio — marca/modelo dos veículos ficam sem casar nesta execução.")
    leitor = Leitor()
    if "vip" in FONTES and not sem_gravar:                # fichas já lidas: não abre de novo (menos pedidos ao site)
        for y in sb_get("veiculos_leiloes?select=id,origem_nome,anunciante_sigla,descricao,cidade,uf&id=like.vip-*"):
            d = y.get("descricao") or ""
            ficha = {k: m.group(1).strip() for k, m in (
                ("comitente", re.search(r"Comitente: ([^.]+)\.", d)), ("km", re.search(r"KM ([\d.]+) km", d)),
                ("combustivel", re.search(r"Combustível ([^.]+)\.", d)), ("cor", re.search(r"Cor: ([^.]+)\.", d)),
                ("cambio", re.search(r"Câmbio ([^.]+)\.", d)), ("procedencia", re.search(r"Procedência: ([^.]+)\.", d))) if m}
            if ficha.get("comitente"):
                if y.get("cidade") and y.get("uf"):
                    ficha["localizacao"] = f"x, {y['cidade']}, {y['uf']} - CEP"
                VIP_CACHE[y["id"]] = ficha
    resumo, falhas, tot_im, tot_vei = {}, 0, 0, 0
    for f in FONTES:
        if f not in LEITORES:
            log(f"  {f}: fonte desconhecida (use zuk, mega, superbid)")
            continue
        log(f"[{NOMES[f]}]")
        try:
            res = LEITORES[f](leitor, catalogo, cidades, agora, hoje)
        except Exception as e:  # noqa: BLE001 — um site nunca derruba os outros
            falhas += 1
            resumo[f] = f"FALHOU: {type(e).__name__}: {str(e)[:160]}"
            log(f"    FALHOU: {e}")
            continue
        imoveis, veiculos = list(res.imoveis.values()), list(res.veiculos.values())
        bancos = {}
        for x in imoveis:
            bancos[x["orgao"]] = bancos.get(x["orgao"], 0) + 1
        log(f"    {res.paginas} página(s), {res.lidos} cartão(ões) lidos -> {len(imoveis)} imóvel(is) de banco | "
            f"{len(veiculos)} veículo(s)")
        if bancos:
            log("    bancos: " + ", ".join(f"{k} {v}" for k, v in sorted(bancos.items(), key=lambda kv: -kv[1])[:12]))
        tot_im += len(imoveis)
        tot_vei += len(veiculos)
        if sem_gravar:
            resumo[f] = f"{len(imoveis)} imóveis, {len(veiculos)} veículos (sem gravar)"
            continue
        sb_upsert("leiloes_radar", imoveis)
        gravar_veiculos(veiculos)
        msg = f"{len(imoveis)} imóveis de banco e {len(veiculos)} veículos"
        if res.vendidos:
            ids = sorted(res.vendidos)
            n = 0
            for i in range(0, len(ids), 100):
                n += sb_patch("leiloes_radar", {"id": "in.(" + ",".join(ids[i:i + 100]) + ")", "situacao": "eq.ativo"},
                              {"situacao": "saiu", "saiu_em": agora})
            if n:
                msg += f"; {n} vendido(s) saíram"
        limite = (agora_dt - dt.timedelta(days=DIAS_SEM_VER)).isoformat().replace("+00:00", "Z")
        if f == "vip":
            msg += f" (bancos/financeiras: {sum(1 for v in veiculos if v.get('fonte') == 'banco')})"
            if veiculos:
                sb_upsert("veiculos_fontes", [{"fonte": "banco", "nome": "Bancos (retomados)", "situacao": "ativa",
                                               "ultima_execucao": agora,
                                               "mensagem": "VIP Leilões — recuperados de financiamento (página pública, todo dia)"}], "fonte")
        elif res.completo["imoveis"] and imoveis:
            n = sb_patch("leiloes_radar", {"fonte": f"eq.{f}", "situacao": "eq.ativo", "visto_em": f"lt.{limite}"},
                         {"situacao": "saiu", "saiu_em": agora})
            msg += f"; imóveis não vistos há {DIAS_SEM_VER} dias: {n} saíram"
        else:
            msg += "; leitura dos imóveis incompleta — nenhum sai por não ter sido visto"
        if res.completo["veiculos"] and veiculos:
            filtro = ({"id": "like.vip-*"} if f == "vip" else {"fonte": "eq.leiloeiro", "anunciante_sigla": f"eq.{f}"})
            nv = sb_patch("veiculos_leiloes", dict(filtro, situacao="eq.ativo", atualizado_em=f"lt.{limite}"),
                          {"situacao": "encerrado", "atualizado_em": agora})
            msg += f"; veículos não vistos: {nv} encerrados"
        resumo[f] = msg

    if not sem_gravar:
        ontem = (hoje - dt.timedelta(days=1)).isoformat()
        n = sb_patch("leiloes_radar", {"fonte": "in.(zuk,mega,superbid)", "situacao": "eq.ativo",
                                       "or": f"(and(praca2.not.is.null,praca2.lt.{ontem}),and(praca2.is.null,praca1.lt.{ontem}))"},
                     {"situacao": "saiu", "saiu_em": agora})
        resumo["DATA PASSOU"] = f"{n} imóvel(is) com o leilão já realizado saíram da lista"
        if tot_im:
            resumo["PRÉ-NOTA"] = sb_rpc("leiloes_radar_pontuar")
        resumo["VEÍCULOS ENCERRADOS"] = sb_rpc("veiculos_vencer")
        sb_upsert("veiculos_fontes", [{"fonte": "leiloeiro", "nome": "Leiloeiros oficiais",
                                       "situacao": "ativa" if falhas < len(FONTES) else "em_implantacao",
                                       "ultima_execucao": agora,
                                       "mensagem": "Portal Zuk, Mega Leilões e Superbid (páginas públicas, todo dia)"}], "fonte")
    log("")
    log("== RESUMO ==")
    for k, v in resumo.items():
        log(f"  {k}: {v}")
    gravar_resumo()
    if falhas and falhas == len([f for f in FONTES if f in LEITORES]):
        raise SystemExit("Nenhum site respondeu (ver as linhas acima).")
    return 0


def gravar_resumo(extra=""):
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Leilões de bancos e leiloeiros\n\n```\n" + "\n".join(relatorio + ([extra] if extra else [])) + "\n```\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as e:
        if e.code not in (0, None):
            gravar_resumo(f"PAROU: {e.code}")
        raise
    except Exception as e:  # noqa: BLE001
        gravar_resumo(f"ERRO: {type(e).__name__}: {str(e)[:300]}")
        raise
