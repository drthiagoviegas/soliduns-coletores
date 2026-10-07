"""
SOLIDUNS — AGENTE DE LEILÕES — Coletor dos LEILÕES JUDICIAIS — v1.4 (05/10/2026)

v1.4: inclui o TRF6 (Justiça Federal de Minas Gerais, desde 2022) — exige a
função de São Paulo (djen-relay) v1.1, que aceita o TRF6.

v1.3 (2ª conferência do Thiago): estado dos TRFs também pelo código de
origem do nº CNJ quando o texto não diz (TRF1: 33=BA, 34=DF, 35=GO, 38=MG;
TRF3: 61=SP, 60=MS); "leiloeiro" que não é nome de pessoa (ex.: "EM CASO DE
ARREMATAÇÃO") é descartado.

v1.2 (correções da conferência do Thiago, 05/10): (1) cidade só vale se
existir na lista oficial de municípios do IBGE daquele estado; (2) nos TRFs
(vários estados) o estado vem da "Seção/Subseção Judiciária" — sem certeza,
o imóvel NÃO entra; (3) "cobertura" e "fazenda" só com sentido de imóvel;
(4) editais lidos pelas regras antigas são RELIDOS e corrigidos (ou saem do
Radar, se deixarem de ser imóvel nos 8 estados).

v1.1: DOIS MODOS DE LEITURA, escolhidos sozinhos:
  - SEM a chave ANTHROPIC_API_KEY -> lê por REGRAS FIXAS (custo ZERO;
    decisão do Thiago em 05/10: sem novos custos na fase de testes);
  - COM a chave -> lê por IA (Claude), como na v1.0.
  Ligar a IA no futuro = só cadastrar a chave no GitHub. Editais lidos por
  regras ficam marcados (modelo "regras-v1") e podem ser relidos pela IA.

Rotina "Leilões" (leiloes.yml v1.1), etapa 2, todo dia depois da Caixa.
Grava em public.leiloes_djen e public.leiloes_radar (SQL 62).

FLUXO (AGENTE):
  1. Busca no DJEN, pela função djen-relay do Supabase em São Paulo (o DJEN
     bloqueia acessos de fora do Brasil), as publicações com "leilão" dos
     20 tribunais dos 8 estados, nos últimos DIAS dias.
  2. REGRA (grátis): fica só o que parece EDITAL de leilão e cita imóvel.
     Publicação já vista (mesmo id) não é lida de novo.
  3. IA (Claude, paga por uso): lê cada edital e devolve os dados em JSON:
     processo, lotes (tipo, endereço, cidade/UF, áreas, matrícula,
     avaliação, lances, datas das praças, ocupação/ônus/débitos citados),
     leiloeiro e site. Teto: LEILOES_IA_MAX editais por execução.
  4. Cada lote de IMÓVEL num dos 8 estados vira item do Radar
     (id "djen-<id>-<n>", modalidade "judicial"). Sem dado pessoal.
  5. Tira do Radar o leilão cuja última praça passou e recalcula a
     pré-nota (funções do SQL 62 e 58).

Variáveis (Secrets do GitHub): SUPABASE_URL, SUPABASE_SERVICE_KEY,
DJEN_TOKEN, ANTHROPIC_API_KEY. Opcionais: LEILOES_IA_MODELO,
LEILOES_IA_RESERVA, LEILOES_IA_MAX (padrão 120), LEILOES_DIAS (padrão 3).
Testes: DJEN_RELAY_URL e ANTHROPIC_URL trocam os endereços.
"""

import datetime as dt
import html
import json
import os
import re
import sys
import time

import requests

VERSAO = "1.4"
TRIBUNAIS = ["TJDFT", "TJGO", "TJSP", "TJMG", "TJBA", "TJCE", "TJPB", "TJRN",
             "TRF1", "TRF3", "TRF5", "TRF6", "TRT2", "TRT3", "TRT5", "TRT7", "TRT10", "TRT13", "TRT15", "TRT18", "TRT21"]
UF_DO_TRIBUNAL = {"TJDFT": "DF", "TJGO": "GO", "TJSP": "SP", "TJMG": "MG", "TJBA": "BA", "TJCE": "CE",
                  "TJPB": "PB", "TJRN": "RN", "TRT2": "SP", "TRT3": "MG", "TRT5": "BA", "TRT7": "CE",
                  "TRT10": "DF", "TRT13": "PB", "TRT15": "SP", "TRT18": "GO", "TRT21": "RN", "TRF6": "MG"}  # TRFs: vários estados
UFS = {"DF", "GO", "SP", "RN", "PB", "CE", "BA", "MG"}
TIPOS = {"apartamento", "cobertura", "kitnet", "casa", "sobrado", "casa_condominio", "terreno_urbano",
         "lote_condominio", "sala_comercial", "loja", "galpao", "predio_comercial", "hotel_pousada",
         "chacara", "sitio", "fazenda", "area_rural", "gleba", "outros"}
MODELO = os.environ.get("LEILOES_IA_MODELO") or "claude-haiku-4-5-20251001"
RESERVA = os.environ.get("LEILOES_IA_RESERVA") or "claude-sonnet-5-5"
IA_MAX = int(os.environ.get("LEILOES_IA_MAX") or 120)
DIAS = int(os.environ.get("LEILOES_DIAS") or 3)
ANTHROPIC_URL = os.environ.get("ANTHROPIC_URL") or "https://api.anthropic.com/v1/messages"

relatorio = []


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    relatorio.append(m)


# ------------------------------------------------------------ texto e regra
def limpar(t):
    t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", t or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


RE_EDITAL = re.compile(r"edital\s+(de\s+)?(leil|hasta|pra[cç]a|aliena)|ser[aá]\(?[aã]?o?\)?\s+levad[oa]\(?s?\)?\s+a\s+(leil|p[uú]blico)"
                       r"|primeir[oa]\s+(leil|pra[cç]|hasta)|1[ºo°]\s*(leil|pra[cç])|datas?\s+(e\s+hor[aá]rios?\s+)?dos\s+leil", re.I)
RE_IMOVEL = re.compile(r"im[oó]ve(l|is)|matr[ií]cula|apartamento|terreno|\blote\s+\d|\bcasa\b|sala\s+comercial|gleba|fazenda|"
                       r"ch[aá]cara|s[ií]tio|galp[aã]o|pr[eé]dio|unidade\s+aut[oô]noma", re.I)


def parece_edital_de_imovel(texto):
    return bool(RE_EDITAL.search(texto)) and bool(RE_IMOVEL.search(texto))


# ------------------------------------------------------------ Supabase
def sb():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY.")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url.rstrip("/"), cab


def sb_get(caminho):
    url, cab = sb()
    r = requests.get(f"{url}/rest/v1/{caminho}", headers=cab, timeout=60)
    if r.status_code == 404:
        raise RuntimeError("Tabelas dos judiciais não existem: rode o SQL 62.")
    r.raise_for_status()
    return r.json()


def sb_upsert(tabela, linhas, conflito="id"):
    if not linhas:
        return
    url, cab = sb()
    for i in range(0, len(linhas), 300):
        r = requests.post(f"{url}/rest/v1/{tabela}?on_conflict={conflito}", json=linhas[i:i + 300],
                          headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"gravação em {tabela} recusada: {r.status_code} {r.text[:200]}")


def sb_rpc(nome):
    url, cab = sb()
    r = requests.post(f"{url}/rest/v1/rpc/{nome}", json={}, headers=cab, timeout=300)
    return r.json() if r.status_code < 300 else f"erro {r.status_code}"


# ------------------------------------------------------------ DJEN (via São Paulo)
def relay_url():
    base = os.environ.get("DJEN_RELAY_URL")
    if base:
        return base
    url, _ = sb()
    return f"{url}/functions/v1/djen-relay"


def buscar_djen(tribunal, inicio, fim):
    token = os.environ.get("DJEN_TOKEN") or ""
    itens, pagina = [], 1
    while pagina <= 60:
        for tentativa in range(3):
            try:
                r = requests.get(relay_url(), params={"forceFunctionRegion": "sa-east-1", "tribunal": tribunal,
                                                      "inicio": inicio, "fim": fim, "pagina": pagina},
                                 headers={"x-soliduns-token": token}, timeout=90)
                if r.status_code == 200:
                    break
                if r.status_code == 401:
                    raise RuntimeError("djen-relay recusou o token (confira DJEN_TOKEN no GitHub e no Supabase)")
            except requests.RequestException:
                r = None
            time.sleep(0 if os.environ.get("LEILOES_TESTE") else 5 * (tentativa + 1))
        if r is None or r.status_code != 200:
            raise RuntimeError(f"DJEN indisponível ({r.status_code if r is not None else 'sem conexão'})")
        lote = r.json().get("itens") or []
        itens += lote
        if len(lote) < 100:
            break
        pagina += 1
        time.sleep(0 if os.environ.get("LEILOES_TESTE") else 1)
    return itens


# ------------------------------------------------------------ IA
INSTRUCOES = """Você extrai dados de EDITAIS DE LEILÃO JUDICIAL brasileiros publicados no Diário de Justiça.
Responda SOMENTE com um objeto JSON válido, sem texto antes ou depois, neste formato:
{"e_edital_de_leilao": true|false,
 "processo": "número CNJ ou null", "vara": "texto ou null", "comarca": "texto ou null",
 "leiloeiro": "nome do leiloeiro oficial ou null", "site_leiloeiro": "endereço do site ou null",
 "lotes": [{"e_imovel": true|false,
   "tipo": "apartamento|cobertura|kitnet|casa|sobrado|casa_condominio|terreno_urbano|lote_condominio|sala_comercial|loja|galpao|predio_comercial|hotel_pousada|chacara|sitio|fazenda|area_rural|gleba|outros",
   "descricao_curta": "até 120 caracteres, só o imóvel",
   "endereco": "logradouro e número, ou null", "bairro": "ou null", "cidade": "ou null", "uf": "sigla de 2 letras ou null",
   "area_m2": número ou null, "area_terreno_m2": número ou null, "quartos": inteiro ou null, "vagas": inteiro ou null,
   "matricula": "número da matrícula ou null", "cartorio": "ou null",
   "avaliacao": número em reais ou null, "lance_minimo_1": número em reais ou null, "lance_minimo_2": número em reais ou null,
   "data_1": "AAAA-MM-DD ou null", "data_2": "AAAA-MM-DD ou null",
   "ocupacao": "ocupado|desocupado|nao_informado",
   "onus": "resumo curto das penhoras, hipotecas e indisponibilidades citadas, ou null",
   "debitos": "resumo curto de quem paga IPTU/condomínio segundo o edital, ou null"}]}
Regras: NÃO invente; use null quando o edital não informar. Valores em reais como número (1234567.89).
Se o lance mínimo vier só em percentual, calcule a partir da avaliação. Áreas em m² (1 hectare = 10000 m²).
Veículos, máquinas e outros bens não imóveis: "e_imovel": false. NUNCA escreva nomes de pessoas, CPF ou CNPJ
em nenhum campo (nem de partes, nem de depositários). Se não for edital de leilão, "e_edital_de_leilao": false e "lotes": []."""


def ler_com_ia(texto):
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if not chave:
        raise RuntimeError("Defina ANTHROPIC_API_KEY (Secrets do GitHub).")
    modelos = [MODELO] + ([RESERVA] if RESERVA and RESERVA != MODELO else [])
    ultimo = None
    for modelo in modelos:
        for tentativa in range(3):
            r = requests.post(ANTHROPIC_URL, timeout=120, headers={
                "x-api-key": chave, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": modelo, "max_tokens": 2500, "system": INSTRUCOES,
                      "messages": [{"role": "user", "content": "EDITAL:\n\n" + texto[:40000]}]})
            if r.status_code == 200:
                j = r.json()
                bruto = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
                bruto = re.sub(r"^```(?:json)?|```$", "", bruto.strip(), flags=re.M).strip()
                ini, fim = bruto.find("{"), bruto.rfind("}")
                dados = json.loads(bruto[ini:fim + 1])
                uso = j.get("usage", {})
                return dados, modelo, uso.get("input_tokens", 0), uso.get("output_tokens", 0)
            ultimo = f"{r.status_code} {r.text[:160]}"
            if r.status_code in (400, 404) and ("model" in r.text.lower()):
                break                          # modelo inexistente/aposentado: tenta a reserva
            if r.status_code in (401, 403):
                raise RuntimeError(f"Chave da Anthropic recusada ({r.status_code}).")
            time.sleep(10 * (tentativa + 1))   # 429/5xx: espera e tenta de novo
    raise RuntimeError(f"IA indisponível: {ultimo}")


# ------------------------------------------------------------ leitura por REGRAS (custo zero)
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA_NUM = re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](20\d{2})")
RE_DATA_EXT = re.compile(r"(\d{1,2})º?\s+de\s+([a-zçãé]+)\s+de\s+(20\d{2})", re.I)
RE_REAIS = re.compile(r"R\$\s*([\d.]{1,15},\d{2})")
RE_CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
TIPOS_PALAVRA = [(r"apartamento|\bapto\b|unidade aut[oô]noma", "apartamento"), (r"apartamento\s+(tipo\s+)?cobertura|cobertura\s+(duplex|triplex|linear)|unidade\s+cobertura", "cobertura"),
                 (r"\bsobrado\b", "sobrado"), (r"\bcasa\b|resid[eê]ncia", "casa"), (r"sala comercial|conjunto comercial|\bsala\s+n[ºo°]", "sala_comercial"),
                 (r"\bloja\b", "loja"), (r"galp[aã]o|barrac[aã]o", "galpao"), (r"pr[eé]dio|edif[ií]cio", "predio_comercial"),
                 (r"fazenda", "fazenda"), (r"s[ií]tio", "sitio"), (r"ch[aá]cara", "chacara"), (r"gleba", "gleba"),
                 (r"im[oó]vel rural|[aá]rea rural|terras de cultura|hectares", "area_rural"),
                 (r"terreno|\blote\b", "terreno_urbano")]


REGRAS_VERSAO = "regras-v3"
RA_DF = ["Plano Piloto", "Asa Sul", "Asa Norte", "Lago Sul", "Lago Norte", "Taguatinga", "Ceilândia", "Águas Claras", "Gama",
         "Sobradinho", "Planaltina", "Samambaia", "Guará", "Brazlândia", "Recanto das Emas", "Riacho Fundo", "Santa Maria",
         "São Sebastião", "Paranoá", "Núcleo Bandeirante", "Cruzeiro", "Sudoeste", "Park Way", "Vicente Pires", "Itapoã",
         "Jardim Botânico", "Estrutural", "Varjão", "Fercal", "Sol Nascente", "Arniqueira", "Candangolândia", "Noroeste"]
ESTADO_NOME = {"distrito federal": "DF", "goias": "GO", "sao paulo": "SP", "minas gerais": "MG", "bahia": "BA", "ceara": "CE",
               "paraiba": "PB", "rio grande do norte": "RN", "para": "PA", "maranhao": "MA", "piaui": "PI", "pernambuco": "PE",
               "alagoas": "AL", "sergipe": "SE", "mato grosso do sul": "MS", "mato grosso": "MT", "tocantins": "TO",
               "amazonas": "AM", "acre": "AC", "rondonia": "RO", "roraima": "RR", "amapa": "AP"}
_MUNICIPIOS = {}


def _sa(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower().strip()


def municipios(uf):
    """Lista oficial do IBGE (uma consulta por UF por execução)."""
    if uf not in _MUNICIPIOS:
        try:
            r = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios", timeout=60)
            _MUNICIPIOS[uf] = {_sa(m["nome"]): m["nome"] for m in r.json()} if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            _MUNICIPIOS[uf] = {}
    return _MUNICIPIOS[uf]


def cidade_oficial(trecho, uf):
    """Maior começo do trecho que seja um município do IBGE naquela UF."""
    lista = municipios(uf) if uf else {}
    if not lista or not trecho:
        return None
    palavras = re.split(r"\s+", re.sub(r"[/\-–,.;:()]", " ", trecho).strip())
    for n in range(min(6, len(palavras)), 0, -1):
        cand = _sa(" ".join(palavras[:n]))
        if cand in lista:
            return lista[cand]
    return None


def uf_federal(t):
    """Estado de um processo da Justiça Federal pelo texto (Seção/Subseção Judiciária)."""
    tl = _sa(t)
    m = re.search(r"se[cç][aã]o judici[aá]ria (?:do |da |de )?(?:estado (?:do |da |de )?)?([a-z ]{4,25}?)(?=[\s,.;/\-]|$)", tl)
    if m:
        nome = m.group(1).strip()
        for k in sorted(ESTADO_NOME, key=len, reverse=True):
            if nome.startswith(k):
                return ESTADO_NOME[k]
    m = re.search(r"subse[cç][aã]o judici[aá]ria de [a-z ]{3,40}?[\-/]\s?([a-z]{2})\b", tl)
    if m and m.group(1).upper() in UFS | {"PA", "MA", "PI", "PE", "AL", "SE", "MS", "MT", "TO", "AM", "AC", "RO", "RR", "AP"}:
        return m.group(1).upper()
    m = re.search(r"\bsj([a-z]{2})\b", tl)                     # ex.: "4ª Vara Federal de Execução Fiscal da SJMA"
    return m.group(1).upper() if m else None


def _num_br(v):
    try:
        return round(float(v.replace(".", "").replace(",", ".")), 2)
    except (ValueError, AttributeError):
        return None


def _primeira_data(trecho):
    cand = []
    for m in RE_DATA_NUM.finditer(trecho):
        d, mth, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        cand.append((m.start(), d, mth, a))
    for m in RE_DATA_EXT.finditer(trecho):
        mth = MESES.get(sem_acento_simples(m.group(2)))
        if mth:
            cand.append((m.start(), int(m.group(1)), mth, int(m.group(3))))
    for _, d, mth, a in sorted(cand):
        try:
            return dt.date(a, mth, d).isoformat()
        except ValueError:
            continue
    return None


def sem_acento_simples(t):
    return (t or "").lower().replace("ç", "c").replace("ã", "a").replace("é", "e")


def _depois(texto, padrao, n=320):
    m = re.search(padrao, texto, re.I)
    return texto[m.end():m.end() + n] if m else ""


RUIDO = re.compile(r"(secretaria|procuradoria|minist[eé]rio)\s+(da|de)\s+fazenda|fazenda\s+(p[uú]blica|nacional|estadual|municipal|do\s+estado|do\s+munic|do\s+distrito)|s[ií]tio\s+(eletr[oô]nico|da\s+internet|oficial)"
                   r"|casa\s+banc[aá]ria|sala\s+(de\s+audi[eê]ncia|virtual|passiva)", re.I)
RE_IMOVEL_FORTE = re.compile(r"m²|m2\b|metros\s+quadrados|hectare|matr[ií]cula|registro\s+de\s+im[oó]veis|\bcri\b|apartamento|"
                             r"\bcasa\b|\blote\s+(n[ºo°]|\d)|terreno|ch[aá]cara|s[ií]tio|fazenda|gleba|galp[aã]o|unidade\s+aut[oô]noma", re.I)


def ler_com_regras(texto, pub):
    t = re.sub(r"\s+", " ", texto)
    tl = RUIDO.sub(" ", t.lower())
    edital = bool(RE_EDITAL.search(t))
    mb = re.search(r"(?:seguintes\s+bens|bens?\s*:|descri[çc][aã]o\s+do\s+bem|do\s+bem\s*:|lote\s+(?:0?1|[úu]nico)\s*[:\-–])(.{0,700})", tl)
    trecho_bem = mb.group(1) if mb else tl          # o "BENS:" do edital pesa mais que o texto padrão
    imovel_forte = bool(RE_IMOVEL_FORTE.search(trecho_bem))
    veiculo = bool(re.search(r"renavam|chassi|placas?\s+(de\s+)?[a-z]{3}|motocicleta|ve[ií]culo", trecho_bem)) and not imovel_forte
    if not edital:
        return {"e_edital_de_leilao": False, "lotes": []}
    # datas das praças (1º e 2º leilão/praça/hasta)
    d1 = _primeira_data(_depois(t, r"(1[ºo°ª]|primeir[oa])\s*(leil[aã]o|pra[cç]a|hasta|preg[aã]o)"))
    d2 = _primeira_data(_depois(t, r"(2[ºo°ª]|segund[oa])\s*(leil[aã]o|pra[cç]a|hasta|preg[aã]o)"))
    if not d1:
        d1 = _primeira_data(_depois(t, r"(no|nos)\s+dias?\s+(?=\d)", 40)) or _primeira_data(_depois(t, r"leil[aã]o", 400))
    # valores
    av = None
    m = re.search(r"avalia[çc][aã]o[^R]{0,120}?R\$\s*([\d.]{1,15},\d{2})|avaliad[oa]s?\s+em\s+R\$\s*([\d.]{1,15},\d{2})", t, re.I)
    if m:
        av = _num_br(m.group(1) or m.group(2))
    trecho2 = _depois(t, r"(2[ºo°ª]|segund[oa])\s*(leil[aã]o|pra[cç]a|hasta|preg[aã]o)", 500)
    l2 = None
    m2 = RE_REAIS.search(trecho2[:300])
    if m2:
        l2 = _num_br(m2.group(1))
    else:
        mp = re.search(r"(\d{2})\s*%", trecho2) or re.search(r"lance m[ií]nimo[^%]{0,40}?(\d{2})\s*%", t, re.I)
        if mp and av:
            l2 = round(av * int(mp.group(1)) / 100, 2)
    if not av:
        valores = [_num_br(v) for v in RE_REAIS.findall(t)]
        valores = [v for v in valores if v and v > 1000]
        av = max(valores) if valores else None
    if l2 is not None and (l2 <= 0 or (av and l2 > av)):
        l2 = None
    # tipo, área, matrícula, local, ocupação
    tipo = "outros"
    for pad, cod in TIPOS_PALAVRA:
        if re.search(pad, tl):
            tipo = cod
            break
    area = None
    ma = re.search(r"([\d.]{1,9}(?:,\d{1,2})?)\s*(m²|m2|metros quadrados)", t, re.I)
    if ma:
        area = _num_br(ma.group(1) if "," in ma.group(1) else ma.group(1).replace(".", "") + ",00")
    else:
        mh = re.search(r"([\d.]{1,7}(?:,\d{1,4})?)\s*(hectares|ha\b)", t, re.I)
        if mh and _num_br(mh.group(1)):
            area = round(_num_br(mh.group(1)) * 10000, 2)
    mm = re.search(r"matr[ií]cula\s*(?:n[ºo°.]*\s*|sob\s+o\s+n[ºo°.]*\s*)?([\d][\d.]{0,10})", t, re.I)
    matricula = mm.group(1).rstrip(".") if mm else None
    uf = UF_DO_TRIBUNAL.get(pub["tribunal"]) or uf_federal(t)
    if not uf and pub["tribunal"] in ("TRF1", "TRF3"):          # código de origem do nº CNJ (últimos 4 dígitos)
        cnj0 = RE_CNJ.search(t) or RE_CNJ.search(pub.get("processo") or "")
        if cnj0:
            orig = cnj0.group(0)[-4:-2]
            uf = {"TRF1": {"33": "BA", "34": "DF", "35": "GO", "38": "MG"}, "TRF3": {"61": "SP", "60": "MS"}}[pub["tribunal"]].get(orig)
    mc = re.search(r"(?i:munic[ií]pio(?:\s+e\s+comarca)?|comarca|cidade|vara\s+do\s+trabalho)\s+(?i:de|do|da)\s+([A-ZÀ-Ú][A-Za-zÀ-ú' ]{2,40}?)"
                   r"(?=\s*[/\-–,.(]|\s+(?:SECRETARIA|Secretaria|\d|VARA|Vara|ESTADO|Estado|EXECU|Execu))", t)
    cidade = None
    for mcand in re.finditer(r"(?i:munic[ií]pio(?:\s+e\s+comarca)?|comarca|cidade|vara\s+do\s+trabalho|subse[cç][aã]o\s+judici[aá]ria|foro)\s+(?i:de|do|da)\s+(.{3,60})", t):
        cidade = cidade_oficial(mcand.group(1), uf)
        if cidade:
            break
    mc = None
    bairro_df = None
    if uf == "DF":                                  # no DF o IBGE só tem "Brasília"; a região vira bairro
        cidade = "Brasília"
        tsa = _sa(t)
        achadas = [ra for ra in RA_DF if re.search(r"\b" + re.escape(_sa(ra)) + r"\b", tsa)]
        bairro_df = max(achadas, key=lambda ra: tsa.count(_sa(ra))) if achadas else None
    if cidade:
        cidade = re.sub(r"\s+(Atord|Atsum|Cartprecciv|Exfis|Cumsen|Etciv|Acum|Conpag|Hte|Ccom|Cumprsen|Prioridade|Secretaria|Rua|Avenida|Av|Pra[cç]a|F[oó]rum)\b.*$",
                        "", cidade, flags=re.I).strip() or None
    if cidade:
        cidade = re.sub(r"\b(Da|De|Do|Das|Dos|E)\b", lambda m: m.group(1).lower(), cidade)
    if cidade and re.search(r"(?i)direito|justi[cç]a|trabalho", cidade):
        cidade = None
    ocup = "desocupado" if re.search(r"desocupad", tl) else ("ocupado" if re.search(r"im[oó]vel\s+ocupad|encontra-se\s+ocupad|\bocupad[oa]\b", tl) else "nao_informado")
    citados = [n for n, pad in (("hipoteca", r"hipotec"), ("penhora", r"penhora"), ("indisponibilidade", r"indisponib"),
                                ("alienação fiduciária", r"aliena[çc][aã]o fiduci")) if re.search(pad, tl)]
    mdeb = re.search(r"(d[eé]bitos?|iptu|condom[ií]nio)[^.]{0,160}(arrematante|adquirente|sub-?rog|pre[çc]o)", t, re.I)
    ml = re.search(r"(?i:leiloeir[oa]\(?a?\)?\s*(?:p[uú]blic[oa]\s+)?(?:oficial\s*)?(?:nomead[oa]\s+|designad[oa]\s+|credenciad[oa]\s+)?[,:]?\s*(?:o\s+|a\s+|sr\.?\s*|sra\.?\s*)?)"
                   r"([A-ZÀ-Ú][A-Za-zÀ-ú.]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][A-Za-zÀ-ú.]+){1,5})", t)
    ms = re.search(r"\b((?:https?://)?www\.[a-z0-9\-]+(?:\.[a-z]{2,4}){1,2})(?![a-z])", tl) or \
         re.search(r"\b((?:https?://)?www\.[a-z0-9\-]+(?:\.[a-z]{2,4}){1,2})", tl)
    site = ms.group(1) if ms and "jus.br" not in ms.group(1) else None
    cnj = RE_CNJ.search(t)
    return {"e_edital_de_leilao": True, "processo": cnj.group(0) if cnj else None,
            "leiloeiro": (ml.group(1).strip() if ml and not re.search(r"(?i)oficial|credenciad|p[uú]blic|nomead|designad|\bcaso\b|arremata|lance|leil[aã]o|edital|prazo|pagamento|valor|comiss", ml.group(1)) else None),
            "site_leiloeiro": site,
            "lotes": [{"e_imovel": imovel_forte and not veiculo, "tipo": tipo,
                       "descricao_curta": f"{tipo.replace('_', ' ').capitalize()} em leilão judicial"[:120],
                       "endereco": None, "bairro": bairro_df, "cidade": cidade, "uf": uf,
                       "area_m2": area, "area_terreno_m2": None, "quartos": None, "vagas": None,
                       "matricula": matricula, "cartorio": None, "avaliacao": av, "lance_minimo_1": av,
                       "lance_minimo_2": l2, "data_1": d1, "data_2": d2, "ocupacao": ocup,
                       "onus": ("O edital cita: " + ", ".join(citados)) if citados else None,
                       "debitos": "O edital trata de débitos (IPTU/condomínio); confira as regras" if mdeb else None}]}


# ------------------------------------------------------------ montagem do Radar
def num(v):
    try:
        x = float(v)
        return round(x, 2) if x > 0 else None
    except (TypeError, ValueError):
        return None


def data_ok(v):
    return v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None


def lotes_para_radar(pub, ia):
    """pub = linha de leiloes_djen; ia = JSON da IA. Devolve (itens, motivo)."""
    if not ia.get("e_edital_de_leilao"):
        return [], "nao_imovel"
    itens, fora = [], 0
    for n, l in enumerate(ia.get("lotes") or [], start=1):
        if not l.get("e_imovel"):
            continue
        uf = (l.get("uf") or UF_DO_TRIBUNAL.get(pub["tribunal"]) or "").upper().strip()
        if uf not in UFS:
            fora += 1
            continue
        avaliacao, l1, l2 = num(l.get("avaliacao")), num(l.get("lance_minimo_1")), num(l.get("lance_minimo_2"))
        preco = l2 or l1 or avaliacao
        if not preco:
            continue
        desconto = round((1 - preco / avaliacao) * 100, 2) if avaliacao and preco < avaliacao else None
        tipo = l.get("tipo") if l.get("tipo") in TIPOS else "outros"
        processo = ia.get("processo") or pub.get("processo")
        chave = re.sub(r"\D", "", processo or "") or pub["id"]      # republicação do mesmo processo não duplica
        itens.append({
            "id": f"djen-{chave}-{n}", "fonte": "djen", "numero": (processo or pub["id"])[:60],
            "uf": uf, "cidade": (l.get("cidade") or None), "bairro": (l.get("bairro") or None),
            "endereco": (l.get("endereco") or None), "tipo": tipo, "tipo_original": (l.get("descricao_curta") or "")[:120] or None,
            "preco": preco, "avaliacao": avaliacao, "desconto": desconto, "financiamento": None,
            "area_total": num(l.get("area_m2")), "area_privativa": None, "area_terreno": num(l.get("area_terreno_m2")),
            "quartos": l.get("quartos") if isinstance(l.get("quartos"), int) else None,
            "vagas": l.get("vagas") if isinstance(l.get("vagas"), int) else None,
            "descricao": (l.get("descricao_curta") or None), "modalidade_venda": "Leilão judicial",
            "link": (ia.get("site_leiloeiro") or None), "data_lista": pub.get("data"),
            "modalidade": "judicial", "processo": processo, "tribunal": pub["tribunal"], "orgao": pub.get("orgao"),
            "praca1": data_ok(l.get("data_1")), "praca2": data_ok(l.get("data_2")), "lance1": l1, "lance2": l2,
            "leiloeiro": ia.get("leiloeiro"), "site_leiloeiro": ia.get("site_leiloeiro"),
            "matricula": l.get("matricula"), "ocupacao_edital": l.get("ocupacao") or "nao_informado",
            "onus_edital": l.get("onus"), "debitos_edital": l.get("debitos"), "link_publicacao": pub.get("link"),
        })
    if itens:
        return itens, "ia_ok"
    return [], ("fora_uf" if fora else "nao_imovel")


def retirar_do_radar(processo, pub_id):
    chave = re.sub(r"\D", "", processo or "") or pub_id
    url, cab = sb()
    requests.patch(f"{url}/rest/v1/leiloes_radar?fonte=eq.djen&situacao=eq.ativo&id=like.djen-{chave}-*",
                   json={"situacao": "saiu", "saiu_em": dt.datetime.now(dt.timezone.utc).isoformat()},
                   headers=dict(cab, Prefer="return=minimal"), timeout=60)


# ------------------------------------------------------------ principal
def main():
    log(f"SOLIDUNS — Leilões judiciais (DJEN + IA) v{VERSAO} — {dt.datetime.now():%d/%m/%Y %H:%M} (UTC do servidor)")
    hoje = dt.date.today()
    inicio, fim = (hoje - dt.timedelta(days=DIAS)).isoformat(), hoje.isoformat()
    vistos = {x["id"] for x in sb_get(f"leiloes_djen?select=id&data=gte.{inicio}")}

    # 1-2. busca e regra
    novos, falhas = [], 0
    for t in TRIBUNAIS:
        try:
            itens = buscar_djen(t, inicio, fim)
        except Exception as e:  # noqa: BLE001 — um tribunal nunca derruba os outros
            falhas += 1
            log(f"  {t:6s}: FALHOU — {str(e)[:160]}")
            continue
        cand = 0
        for i in itens:
            if i["id"] in vistos:
                continue
            texto = limpar(i.get("texto"))
            if not parece_edital_de_imovel(texto):
                continue
            cand += 1
            vistos.add(i["id"])
            novos.append({"id": i["id"], "tribunal": t, "data": (i.get("data") or "")[:10] or None,
                          "processo": i.get("processo"), "tipo": i.get("tipo"), "orgao": i.get("orgao"),
                          "classe": i.get("classe"), "link": i.get("link"), "texto": texto, "status": "novo"})
        log(f"  {t:6s}: {len(itens):5d} publicações com 'leilão' | {cand:4d} editais de imóvel novos")
    sb_upsert("leiloes_djen", novos)
    if falhas == len(TRIBUNAIS):
        raise SystemExit("Nenhum tribunal respondeu (confira a função djen-relay e o DJEN_TOKEN).")

    # 3-4. IA (novos + pendentes de execuções anteriores), com teto
    usar_ia = bool(os.environ.get("ANTHROPIC_API_KEY"))
    log(f"Modo de leitura: {'IA (' + MODELO + ')' if usar_ia else 'REGRAS FIXAS (custo zero)'}")
    fila = sb_get("leiloes_djen?select=id,tribunal,data,processo,orgao,link,texto,tentativas"
                  "&or=(status.eq.novo,and(status.eq.erro,tentativas.lt.3)" + ("" if usar_ia else ",modelo.eq.regras-v1,modelo.eq.regras-v2") + ")"
                  "&order=data.desc&limit="
                  + str(IA_MAX if usar_ia else 2000))
    lidos = radar = tin = tout = 0
    contagem = {"ia_ok": 0, "nao_imovel": 0, "fora_uf": 0, "erro": 0}
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    for pub in fila:
        try:
            if usar_ia:
                ia, modelo, a, b = ler_com_ia(pub["texto"])
            else:
                ia, modelo, a, b = ler_com_regras(pub["texto"], pub), REGRAS_VERSAO, 0, 0
            itens, status = lotes_para_radar(pub, ia)
            for x in itens:
                x.update(situacao="ativo", visto_em=agora, saiu_em=None, coletado_em=agora)
            sb_upsert("leiloes_radar", itens)
            if status != "ia_ok":                   # releitura: deixou de ser imóvel nos 8 estados -> sai do Radar
                retirar_do_radar(ia.get("processo") or pub.get("processo"), pub["id"])
            radar += len(itens); tin += a; tout += b; lidos += 1
            contagem[status] += 1
            sb_upsert("leiloes_djen", [{"id": pub["id"], "tribunal": pub["tribunal"], "status": status, "ia": ia,
                                        "modelo": modelo, "tokens_in": a, "tokens_out": b, "erro": None,
                                        "processado_em": agora, "tentativas": (pub.get("tentativas") or 0) + 1}])
        except Exception as e:  # noqa: BLE001
            contagem["erro"] += 1
            sb_upsert("leiloes_djen", [{"id": pub["id"], "tribunal": pub["tribunal"], "status": "erro",
                                        "erro": str(e)[:300], "tentativas": (pub.get("tentativas") or 0) + 1}])
            log(f"  IA falhou em {pub['id']}: {str(e)[:160]}")
            if "recusada" in str(e):
                break
    custo = tin / 1e6 * 1 + tout / 1e6 * 5
    log("")
    log(f"{'IA' if usar_ia else 'Regras'}: {lidos} edital(is) lido(s) | {contagem['ia_ok']} com imóvel nos 8 estados -> {radar} item(ns) no Radar | "
        f"{contagem['nao_imovel']} sem imóvel | {contagem['fora_uf']} fora dos 8 estados | {contagem['erro']} erro(s)")
    if usar_ia:
        log(f"IA: {tin} + {tout} tokens (estimativa ~US$ {custo:.2f} no preço do Haiku 4.5)")

    # 5. vencidos e pré-nota
    log(f"Vencimento: {sb_rpc('leiloes_judiciais_vencer')}")
    log(f"Pré-nota: {sb_rpc('leiloes_radar_pontuar')}")

    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Leilões judiciais (DJEN + IA)\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
