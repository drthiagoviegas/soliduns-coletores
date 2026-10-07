"""
SOLIDUNS — Coletor de FUNDOS DE INVESTIMENTO — v1.8 (05/10/2026)

v1.8 (correção, achada na 1ª execução diária real): o índice diário da SEC só
é publicado depois que o dia termina e, para arquivo que ainda não existe, a
SEC responde 403 (não 404). A v1.7 tratava isso como erro e abandonava toda a
atualização dos IPOs dos EUA. Agora: começa pelo dia ANTERIOR e trata 403/404
de índice diário como "ainda não publicado" (pula o arquivo).

v1.7 (v67, SQL 62): FIDC, FIP e FIAGRO — do MESMO cadastro de classes que a
rotina já baixa (registro_fundo_classe.zip; sem download novo): classes em
funcionamento com nome, CNPJ, situação, público-alvo, exclusivo,
condomínio, patrimônio líquido (e data), gestor e administrador. Para os
FIDCs, também o informe mensal mais recente da CVM (~3,5 MB): carteira de
direitos creditórios, inadimplência, rentabilidade do mês das cotas sênior e
subordinada e número de cotistas. Grava public.fundos_estrut. Falha aqui
nunca derruba os fundos.

v1.6 (v66, SQL 61): na mesma rotina, também os PRÓXIMOS IPOs DOS EUA pela SEC
(EDGAR, gratuito): descobre os pedidos de registro S-1 (empresas americanas) e
F-1 (estrangeiras) — na 1ª vez, pelos índices trimestrais dos últimos 12
meses (~190 MB, uma vez só); depois, pelos índices diários dos últimos 7 dias
— e acompanha cada empresa pela ficha dela na SEC (submissions): emendas,
registro efetivado (EFFECT), preço definido (424B4), retirada (RW) e ticker.
Empresa que já entregava balanços (10-K/10-Q/20-F/40-F) antes do pedido NÃO é
IPO e fica de fora. SPAC (SIC 6770) é marcado. Grava public.ipos_eua.
Falha nos IPOs nunca derruba os fundos.

v1.5 (v65, SQL 60): na mesma rotina, também as CRIPTOMOEDAS — fechamento
diário em US$ (e volume) das principais moedas na Kraken (consulta pública
gratuita, sem chave; 1 consulta por moeda, ~17 por dia), últimos 400 dias,
em public.cripto e public.cripto_hist; limpeza de 400 dias. O dia ainda em
andamento NÃO é gravado. Falha nas criptos nunca derruba os fundos.

v1.4 (v62): na mesma rotina, também (1) DÓLAR e EURO — PTAX de fechamento
do Banco Central (venda), últimos ~13 meses, gravados em public.indices_hist
('USD' e 'EUR'); (2) MANCHETES (título, data e link; nunca o texto) da
Agência Brasil (economia), do Federal Reserve e da SEC em public.noticias
(SQL 56), com limpeza de 30 dias. Cada parte é independente: falha em uma
nunca derruba os fundos (só um aviso no registro).

v1.3 (v57, "Próximos IPOs"): na mesma rotina, lê também as OFERTAS PÚBLICAS
DE AÇÕES registradas na CVM (Resolução CVM 160: IPOs e follow-ons; arquivo
oferta_distribuicao.zip, ~6 MB) e grava em public.ofertas_acoes (SQL 54).
Falha nas ofertas NUNCA derruba a coleta dos fundos (só um aviso no registro).

v1.2: o CDI é sempre buscado para os 37 meses, também no modo diário (que
lê só 2 meses de informes) — na v1.1 o modo diário pedia o CDI só desde o
mês anterior e ficava sem nenhum mês publicado.

v1.1: o GitHub (servidores nos EUA) não alcança a API do Banco Central nem o
Ipeadata (bloqueio de acesso de fora do Brasil; o Supabase também não).
CDI passa a vir, na falta do BC, da taxa interbancária mensal publicada
pela OCDE no FRED (IRSTCI01BRM156N = Selic over, % a.a.) menos 0,10 p.p.,
capitalizada pelos dias úteis do mês (calendário nacional) — confere com o
CDI mensal do Ipeadata/BC na 2ª casa decimal em todos os meses de 2023-2026.
Os meses ainda não publicados (até 2) são ESTIMADOS com a última taxa e
marcados em indices_hist (indice 'CDI_ESTIMADO'); a próxima coleta troca
pelo dado oficial.

Fontes (oficiais e gratuitas):
  - CVM, dados abertos:
      cadastro de fundos e classes (registro_fundo_classe.zip)
      Informe Diário (inf_diario_fi_AAAAMM.zip: cota, patrimônio,
        captação, resgates e cotistas de cada dia)
      extrato de informações (extrato_fi_AAAA.csv: taxa de administração,
        taxa de performance, aplicação mínima, prazos de resgate)
  - Banco Central (SGS, série 12 — CDI diário); se não responder, Ipeadata
    (CDI mensal, série BM12_TJCDI12).

Universo: classes de FIF (renda fixa, multimercado, ações, cambial) em
funcionamento, não exclusivas, para o público em geral ou investidores
qualificados.

Grava (SQL 51): public.fundos_inv (cadastro + última cota),
public.fundos_inv_mes (última cota de cada mês, 37 meses) e o CDI mensal em
public.indices_hist (indice 'CDI').
  - 1ª execução (sem meses gravados): baixa 38 meses de informes (~450 MB,
    alguns minutos).
  - Demais: só o mês atual e o anterior.
  - Apaga os fundos que saíram do universo e os meses com mais de 37 meses.

Uso:
  python coletar_fundos.py                              -> coleta e grava
  python coletar_fundos.py --fonte pasta/ --saida pasta/ [--meses N]  -> testes
Variáveis: SUPABASE_URL e SUPABASE_SERVICE_KEY (as mesmas das outras rotinas).
"""
import argparse, datetime as dt, email.utils, hashlib, io, json, math, os, re, sys, time, zipfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

URL_CAD = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
URL_FIDC = "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/inf_mensal_fidc_{am}.zip"   # v1.7
TIPOS_ESTRUT = {"FIDC": "FIDC", "FIP": "FIP", "FIAGRO": "FIAGRO"}
URL_INF = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{am}.zip"
URL_EXT = "https://dados.cvm.gov.br/dados/FI/DOC/EXTRATO/DADOS/extrato_fi_{ano}.csv"
URL_OFERTAS = "https://dados.cvm.gov.br/dados/OFERTA/DISTRIB/DADOS/oferta_distribuicao.zip"
URL_PTAX = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,"
            "dataFinalCotacao=@dataFinalCotacao)?@moeda='{m}'&@dataInicial='{ini}'&@dataFinalCotacao='{fim}'&$format=json"
            "&$select=cotacaoCompra,cotacaoVenda,dataHoraCotacao,tipoBoletim")   # v1.4
FEEDS = [("br", "Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml", "rss_agbr.xml"),
         ("us", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "rss_fed.xml"),
         ("us", "SEC", "https://www.sec.gov/news/pressreleases.rss", "rss_sec.xml")]
URL_KRAKEN = "https://api.kraken.com/0/public/OHLC?pair={par}&interval=1440"   # v1.5
CRIPTOS = [("BTC", "Bitcoin", "XBTUSD"), ("ETH", "Ethereum", "ETHUSD"), ("USDT", "Tether (dólar digital)", "USDTZUSD"),
           ("XRP", "XRP", "XRPUSD"), ("SOL", "Solana", "SOLUSD"), ("USDC", "USD Coin (dólar digital)", "USDCUSD"),
           ("DOGE", "Dogecoin", "XDGUSD"), ("ADA", "Cardano", "ADAUSD"), ("TRX", "Tron", "TRXUSD"),
           ("LINK", "Chainlink", "LINKUSD"), ("AVAX", "Avalanche", "AVAXUSD"), ("XLM", "Stellar", "XLMUSD"),
           ("BCH", "Bitcoin Cash", "BCHUSD"), ("LTC", "Litecoin", "LTCUSD"), ("DOT", "Polkadot", "DOTUSD"),
           ("UNI", "Uniswap", "UNIUSD"), ("ATOM", "Cosmos", "ATOMUSD")]
SEC_IDX_TRI = "https://www.sec.gov/Archives/edgar/full-index/{a}/QTR{q}/form.idx"   # v1.6
SEC_IDX_DIA = "https://www.sec.gov/Archives/edgar/daily-index/{a}/QTR{q}/form.{d}.idx"
SEC_SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
IPO_FORMS = ("S-1", "F-1")
JA_PUBLICA = ("10-K", "10-Q", "20-F", "40-F", "10-KT", "10-K405", "6-K")
UA_FEEDS = "SOLIDUNS (soliduns.com.br) contato contato@soliduns.com.br"
URL_CDI_BCB = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json&dataInicial={ini}&dataFinal={fim}"
URL_CDI_FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=IRSTCI01BRM156N"
URL_CDI_IPEA = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='BM12_TJCDI12')"
MESES_HIST = 37          # 36 meses de rentabilidade + o mês base
PUBLICOS = ("Público Geral", "Qualificado")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def baixar(url, fonte=None, obrigatorio=True):
    nome = url.rsplit("/", 1)[-1]
    if fonte:
        caminho = os.path.join(fonte, nome)
        return open(caminho, "rb").read() if os.path.exists(caminho) else None
    for tentativa in range(4):
        try:
            r = requests.get(url, timeout=240, headers={"User-Agent": "SOLIDUNS coletor Fundos"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            log(f"  falha ao baixar {nome} ({e}); nova tentativa em {15 * (tentativa + 1)}s")
            time.sleep(15 * (tentativa + 1))
    if obrigatorio:
        raise RuntimeError(f"Não foi possível baixar {url}")
    return None


def so_digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def txt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def num(v):
    s = txt(v)
    if s is None:
        return None
    try:
        x = float(s.replace(",", "."))
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def inteiro(v):
    x = num(v)
    return int(round(x)) if x is not None else None


# ------------------------------------------------------------ cadastro
def ler_cadastro(conteudo):
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    cls = pd.read_csv(z.open("registro_classe.csv"), sep=";", dtype=str, encoding="latin-1")
    fun = pd.read_csv(z.open("registro_fundo.csv"), sep=";", dtype=str, encoding="latin-1")
    cls = cls[(cls["Situacao"] == "Em Funcionamento Normal")
              & cls["Tipo_Classe"].fillna("").str.contains("FIF")
              & (cls["Exclusivo"].fillna("") != "S")
              & cls["Publico_Alvo"].isin(PUBLICOS)]
    fun = fun.drop_duplicates("ID_Registro_Fundo", keep="last").set_index("ID_Registro_Fundo")
    out = {}
    for _, r in cls.iterrows():
        cnpj = so_digitos(r["CNPJ_Classe"])
        if len(cnpj) != 14:
            continue
        f = fun.loc[r["ID_Registro_Fundo"]] if r["ID_Registro_Fundo"] in fun.index else None
        out[cnpj] = {
            "cnpj": cnpj, "nome": txt(r["Denominacao_Social"]) or cnpj,
            "classe": txt(r["Classificacao"]), "anbima": txt(r["Classificacao_Anbima"]),
            "publico": txt(r["Publico_Alvo"]), "condominio": txt(r["Forma_Condominio"]),
            "tributacao_lp": txt(r["Tributacao_Longo_Prazo"]), "benchmark": txt(r["Indicador_Desempenho"]),
            "gestor": txt(f["Gestor"]) if f is not None else None,
            "administrador": txt(f["Administrador"]) if f is not None else None,
            "inicio": (txt(f["Data_Constituicao"]) if f is not None else None) or txt(r["Data_Constituicao"]) or txt(r["Data_Inicio"]),
        }
    return out


# ------------------------------------------------------------ v1.7: FIDC, FIP e FIAGRO
def _tipo_estrut(t):
    t = (t or "").upper()
    if "FIAGRO" in t:
        return "FIAGRO"
    if "FIDC" in t:
        return "FIDC"
    if "FIP" in t:
        return "FIP"
    return None


def ler_estruturados(conteudo):
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    cls = pd.read_csv(z.open("registro_classe.csv"), sep=";", dtype=str, encoding="latin-1")
    fun = pd.read_csv(z.open("registro_fundo.csv"), sep=";", dtype=str, encoding="latin-1")
    cls = cls[cls["Situacao"].isin(["Em Funcionamento Normal", "Fase Pré-Operacional"])]
    fun = fun.drop_duplicates("ID_Registro_Fundo", keep="last").set_index("ID_Registro_Fundo")
    out, agora = {}, dt.datetime.now(dt.timezone.utc).isoformat()   # coletado_em renovado = a limpeza sabe quem saiu
    for _, r in cls.iterrows():
        tipo = _tipo_estrut(r.get("Tipo_Classe"))
        cnpj = so_digitos(r["CNPJ_Classe"])
        if not tipo or len(cnpj) != 14:
            continue
        f = fun.loc[r["ID_Registro_Fundo"]] if r["ID_Registro_Fundo"] in fun.index else None
        out[cnpj] = {"cnpj": cnpj, "tipo": tipo, "nome": txt(r["Denominacao_Social"]) or cnpj, "situacao": txt(r["Situacao"]),
                     "classificacao": txt(r.get("Classificacao")), "publico": txt(r.get("Publico_Alvo")),
                     "exclusivo": txt(r.get("Exclusivo")) == "S", "condominio": txt(r.get("Forma_Condominio")),
                     "pl": num(r.get("Patrimonio_Liquido")), "dt_pl": txt(r.get("Data_Patrimonio_Liquido")),
                     "registro": txt(r.get("Data_Registro")),
                     "gestor": txt(f["Gestor"]) if f is not None else None,
                     "administrador": txt(f["Administrador"]) if f is not None else None,
                     "carteira": None, "inad_pct": None, "rent_senior": None, "rent_sub": None, "cotistas": None, "dt_informe": None,
                     "coletado_em": agora}
    return out


def _fidc_informe(fonte, hoje):
    """Informe mensal de FIDC mais recente publicado (tenta os 3 últimos meses)."""
    for k in range(1, 4):
        a, m = hoje.year, hoje.month - k
        while m <= 0:
            a, m = a - 1, m + 12
        am = f"{a}{m:02d}"
        c = baixar(URL_FIDC.format(am=am), fonte, obrigatorio=False)
        if c:
            return am, zipfile.ZipFile(io.BytesIO(c))
    return None, None


def _ler_tab(z, nome):
    n = next((x for x in z.namelist() if x.lower().startswith(nome.lower() + "_")), None)
    return pd.read_csv(z.open(n), sep=";", dtype=str, encoding="latin-1") if n else None


def enriquecer_fidc(est, fonte, hoje):
    am, z = _fidc_informe(fonte, hoje)
    if not z:
        log("  aviso: informe mensal de FIDC indisponível; FIDCs só com o cadastro")
        return
    t1 = _ler_tab(z, "inf_mensal_fidc_tab_I")
    if t1 is not None:
        for _, r in t1.iterrows():
            x = est.get(so_digitos(r.get("CNPJ_CLASSE") or r.get("CNPJ_FUNDO_CLASSE")))
            if not x or x["tipo"] != "FIDC":
                continue
            cart, inad = num(r.get("TAB_I2A_VL_DIRCRED_RISCO")), num(r.get("TAB_I2A2_VL_CRED_VENC_INAD"))
            x["carteira"] = cart
            pct = round(100 * inad / cart, 2) if cart and cart > 0 and inad is not None else None
            x["inad_pct"] = pct if pct is not None and 0 <= pct <= 100 else None   # acima de 100% = informe preenchido errado
            x["dt_informe"] = txt(r.get("DT_COMPTC"))
    t4 = _ler_tab(z, "inf_mensal_fidc_tab_IV")
    if t4 is not None:
        for _, r in t4.iterrows():
            x = est.get(so_digitos(r.get("CNPJ_FUNDO_CLASSE")))
            if x and x["tipo"] == "FIDC" and num(r.get("TAB_IV_A_VL_PL")) is not None:
                x["pl"], x["dt_pl"] = num(r.get("TAB_IV_A_VL_PL")), txt(r.get("DT_COMPTC")) or x["dt_pl"]
    t3 = _ler_tab(z, "inf_mensal_fidc_tab_X_3")
    if t3 is not None:
        for _, r in t3.iterrows():
            x = est.get(so_digitos(r.get("CNPJ_FUNDO_CLASSE")))
            v = num(r.get("TAB_X_VL_RENTAB_MES"))
            if not x or v is None:
                continue
            serie = (txt(r.get("TAB_X_CLASSE_SERIE")) or "").upper()
            # limites de plausibilidade no mês: sênior ±30%, subordinada/mezanino ±100% (fora disso = erro de preenchimento)
            if ("SENIOR" in serie or "SÊNIOR" in serie) and x["rent_senior"] is None and abs(v) <= 30:
                x["rent_senior"] = v
            elif ("SUBORD" in serie or "MEZANINO" in serie) and x["rent_sub"] is None and abs(v) <= 100:
                x["rent_sub"] = v
    t11 = _ler_tab(z, "inf_mensal_fidc_tab_X_1_1")
    if t11 is not None:
        cols = [c for c in t11.columns if c.startswith("TAB_X_NR_COTST")]
        for _, r in t11.iterrows():
            x = est.get(so_digitos(r.get("CNPJ_FUNDO_CLASSE")))
            if x and x["tipo"] == "FIDC":
                x["cotistas"] = int(sum((num(r.get(c)) or 0) for c in cols))
    log(f"  FIDC: informe mensal de {am[4:]}/{am[:4]} aplicado")


def coletar_estruturados(conteudo_cad, fonte, saida, hoje):
    """v1.7 — nunca derruba os fundos."""
    try:
        est = ler_estruturados(conteudo_cad)
        enriquecer_fidc(est, fonte, hoje)
        linhas = list(est.values())
        cont = {}
        for l in linhas:
            cont[l["tipo"]] = cont.get(l["tipo"], 0) + 1
        log(f"FIDC, FIP e FIAGRO (cadastro CVM): {len(linhas)} classe(s) {cont}")
        if saida:
            json.dump(linhas, open(os.path.join(saida, "fundos_estrut.json"), "w"), ensure_ascii=False)
            return
        if linhas:
            gravar("fundos_estrut", linhas, "cnpj")
            vivos = {l["cnpj"] for l in linhas}
            url, cab = cab_supabase()
            r = requests.post(f"{url}/rest/v1/rpc/fundos_estrut_limpar", json={"p_horas": 30}, headers=cab, timeout=120)
            log(f"Limpeza FIDC/FIP/FIAGRO: {r.text.strip()}" if r.status_code < 300 else f"  aviso: limpeza não feita ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: FIDC/FIP/FIAGRO não atualizados ({str(e)[:150]}){' — rode o SQL 62' if 'fundos_estrut' in str(e) else ''}")


# ------------------------------------------------------------ extrato
def ler_extratos(anos, fonte):
    por = {}
    for ano in anos:
        c = baixar(URL_EXT.format(ano=ano), fonte, obrigatorio=False)
        if not c:
            log(f"  extrato {ano}: não encontrado")
            continue
        e = pd.read_csv(io.BytesIO(c), sep=";", dtype=str, encoding="latin-1",
                        usecols=lambda k: k in ("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO", "DT_COMPTC", "TAXA_ADM", "TAXA_PERFM",
                                                "APLIC_MIN", "QT_DIA_CONVERSAO_COTA", "QT_DIA_PAGTO_RESGATE"))
        col = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in e.columns else "CNPJ_FUNDO"
        for _, r in e.iterrows():
            cnpj = so_digitos(r[col])
            d = txt(r.get("DT_COMPTC"))
            if cnpj and (cnpj not in por or (d or "") >= (por[cnpj]["dt_extrato"] or "")):
                por[cnpj] = {"taxa_adm": num(r.get("TAXA_ADM")), "taxa_perf": num(r.get("TAXA_PERFM")),
                             "aplic_min": num(r.get("APLIC_MIN")), "dias_conversao": inteiro(r.get("QT_DIA_CONVERSAO_COTA")),
                             "dias_pagamento": inteiro(r.get("QT_DIA_PAGTO_RESGATE")), "dt_extrato": d}
        log(f"  extrato {ano}: lido")
    return por


# ------------------------------------------------------------ informe diário
def ler_informe(conteudo, universo):
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    d = pd.read_csv(z.open(z.namelist()[0]), sep=";", dtype=str)
    col = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in d.columns else "CNPJ_FUNDO"
    d["cnpj"] = d[col].str.replace(r"\D", "", regex=True)
    d = d[d["cnpj"].isin(universo)]
    if "ID_SUBCLASSE" not in d.columns:
        d["ID_SUBCLASSE"] = None
    for k in ("VL_QUOTA", "VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"):
        d[k] = pd.to_numeric(d[k], errors="coerce")
    d = d[d["VL_QUOTA"] > 0]
    d["sub"] = d["ID_SUBCLASSE"].fillna("")
    return d[["cnpj", "sub", "DT_COMPTC", "VL_QUOTA", "VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"]]


def por_dia(d):
    """Uma linha por fundo e dia. Classe sem linha própria (só subclasses):
    cota da subclasse de maior patrimônio; patrimônio e cotistas somados."""
    if d.empty:
        return d
    tem_classe = set(d.loc[d["sub"] == "", "cnpj"])
    a = d[d["sub"] == ""]
    b = d[(d["sub"] != "") & ~d["cnpj"].isin(tem_classe)]
    if not b.empty:
        soma = b.groupby(["cnpj", "DT_COMPTC"], as_index=False)[["VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"]].sum()
        maior = b.sort_values("VL_PATRIM_LIQ").drop_duplicates(["cnpj", "DT_COMPTC"], keep="last")[["cnpj", "DT_COMPTC", "VL_QUOTA"]]
        b = soma.merge(maior, on=["cnpj", "DT_COMPTC"])
    a = a.drop(columns=["sub"]).drop_duplicates(["cnpj", "DT_COMPTC"], keep="last")
    return pd.concat([a, b], ignore_index=True)


def resumo_mes(dd, am):
    if dd.empty:
        return []
    dd = dd.sort_values("DT_COMPTC")
    ult = dd.drop_duplicates("cnpj", keep="last").set_index("cnpj")
    capt = (dd["CAPTC_DIA"].fillna(0) - dd["RESG_DIA"].fillna(0)).groupby(dd["cnpj"]).sum()
    mes = f"{am[:4]}-{am[4:]}-01"
    out = []
    for cnpj, r in ult.iterrows():
        out.append({"cnpj": cnpj, "mes": mes, "dt": r["DT_COMPTC"], "cota": float(r["VL_QUOTA"]),
                    "pl": num(r["VL_PATRIM_LIQ"]), "cotistas": inteiro(r["NR_COTST"]),
                    "capt_liq": round(float(capt.get(cnpj, 0.0)), 2)})
    return out


def meses_ate(hoje, n):
    a, m = hoje.year, hoje.month
    out = []
    for _ in range(n):
        out.append(f"{a}{m:02d}")
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return out          # do mais recente para o mais antigo


# ------------------------------------------------------------ CDI
def pascoa(a):
    b, c, d = a % 19, a // 100, a % 100
    e, f, g = c // 4, c % 4, (8 * c + 13) // 25
    h = (19 * b + c - e - g + 15) % 30
    i, k = d // 4, d % 4
    l = (32 + 2 * f + 2 * i - h - k) % 7
    m = (b + 11 * h + 19 * l) // 433
    n = (h + l - 7 * m + 90) // 25
    return dt.date(a, n, (h + l - 7 * m + 33 * n + 19) % 32)


def feriados(a):
    p = pascoa(a)
    fs = {dt.date(a, 1, 1), dt.date(a, 4, 21), dt.date(a, 5, 1), dt.date(a, 9, 7), dt.date(a, 10, 12),
          dt.date(a, 11, 2), dt.date(a, 11, 15), dt.date(a, 12, 25),
          p - dt.timedelta(48), p - dt.timedelta(47), p - dt.timedelta(2), p + dt.timedelta(60)}
    if a >= 2024:
        fs.add(dt.date(a, 11, 20))       # Consciência Negra (feriado nacional desde 2024)
    return fs


def dias_uteis(a, m):
    d, n, fs = dt.date(a, m, 1), 0, feriados(a)
    while d.month == m:
        n += 1 if d.weekday() < 5 and d not in fs else 0
        d += dt.timedelta(days=1)
    return n


def mes_iso(a, m):
    return f"{a}-{m:02d}-01"


def cdi_mensal(meses, fonte, hoje):
    """% do CDI em cada mês completo -> ({'AAAA-MM-01': %}, {meses estimados})."""
    if fonte:
        p = os.path.join(fonte, "cdi.json")
        return (json.load(open(p)) if os.path.exists(p) else {}), set()
    ini = dt.date(int(meses[-1][:4]), int(meses[-1][4:]), 1)
    atual = mes_iso(hoje.year, hoje.month)
    res, origem = {}, None
    try:                                   # 1) Banco Central (SGS 12, diário)
        out, a = {}, ini
        while a <= hoje:
            b = min(dt.date(a.year + 1, a.month, 1) - dt.timedelta(days=1), hoje)
            r = requests.get(URL_CDI_BCB.format(ini=a.strftime("%d/%m/%Y"), fim=b.strftime("%d/%m/%Y")), timeout=30)
            r.raise_for_status()
            for x in r.json():
                d = dt.datetime.strptime(x["data"], "%d/%m/%Y").date()
                k = mes_iso(d.year, d.month)
                out[k] = out.get(k, 1.0) * (1 + float(x["valor"]) / 100)
            a = b + dt.timedelta(days=1)
        res = {k: round((v - 1) * 100, 6) for k, v in out.items() if k != atual}
        origem = "Banco Central, série 12"
    except Exception as e:  # noqa: BLE001
        log(f"  CDI pelo Banco Central indisponível ({str(e)[:90]}); usando a taxa interbancária da OCDE (FRED)")
    if len(res) < 12:                      # 2) OCDE/FRED: Selic over mensal (% a.a.) − 0,10 p.p.
        try:
            r = requests.get(URL_CDI_FRED, timeout=60)
            r.raise_for_status()
            res = {}
            for linha in r.text.splitlines()[1:]:
                d, _, v = linha.partition(",")
                if len(d) < 7 or v.strip() in ("", "."):
                    continue
                a, m = int(d[:4]), int(d[5:7])
                k = mes_iso(a, m)
                if k >= ini.isoformat() and k != atual:
                    taxa = float(v) - 0.10
                    res[k] = round(((1 + taxa / 100) ** (dias_uteis(a, m) / 252) - 1) * 100, 6)
            origem = "OCDE/FRED, Selic over − 0,10 p.p. por dia útil"
        except Exception as e:  # noqa: BLE001
            log(f"  CDI pela OCDE/FRED indisponível ({str(e)[:90]})")
    if len(res) < 12:                      # 3) Ipeadata (mensal, 2 casas)
        try:
            r = requests.get(URL_CDI_IPEA, timeout=60)
            r.raise_for_status()
            res = {x["VALDATA"][:10]: float(x["VALVALOR"]) for x in r.json()["value"]
                   if x.get("VALVALOR") is not None and ini.isoformat() <= x["VALDATA"][:10] != atual}
            origem = "Ipeadata, 2 casas decimais"
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: CDI indisponível ({str(e)[:90]}); a busca mostra os fundos sem a comparação com o CDI")
            return {}, set()
    # meses fechados ainda não publicados (até 2): estimados com a última taxa anual conhecida
    estimados = set()
    if res:
        ultimo = max(res)
        a, m = int(ultimo[:4]), int(ultimo[5:7])
        taxa_ano = ((1 + res[ultimo] / 100) ** (252 / dias_uteis(a, m)) - 1) * 100
        for _ in range(2):
            m += 1
            if m == 13:
                a, m = a + 1, 1
            k = mes_iso(a, m)
            if k >= atual:
                break
            res[k] = round(((1 + taxa_ano / 100) ** (dias_uteis(a, m) / 252) - 1) * 100, 6)
            estimados.add(k)
    log(f"CDI: {len(res)} meses ({origem})" + (f"; estimados: {', '.join(sorted(estimados))}" if estimados else ""))
    return res, estimados


# ------------------------------------------------------------ v1.3: ofertas de ações (IPOs e follow-ons)
def ler_ofertas(conteudo, hoje, anos=3):
    """Ofertas de AÇÕES (inclui units e certificados de depósito de ações) da
    Resolução CVM 160 com requerimento nos últimos `anos` anos."""
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        nome = next(n for n in z.namelist() if "resolucao_160" in n.lower())
        d = pd.read_csv(z.open(nome), sep=";", encoding="latin1", dtype=str)
    vm = d["Valor_Mobiliario"].fillna("")
    d = d[vm.str.contains(r"^(?:Ações|Units|Certificados? de Depósito de Ações)", regex=True)]
    corte = (hoje - dt.timedelta(days=365 * anos)).isoformat()
    d = d[d["Data_requerimento"].fillna("") >= corte]
    def data(v):
        s = txt(v)
        return s[:10] if s and re.match(r"^\d{4}-\d{2}-\d{2}", s) else None
    out = {}
    for _, r in d.iterrows():
        n = txt(r.get("Numero_Requerimento"))
        if not n:
            continue
        ini = txt(r.get("Oferta_inicial"))
        destin = txt(r.get("Destinacao_recursos"))
        out[n] = {
            "numero": n, "processo": txt(r.get("Numero_Processo")), "data_req": data(r.get("Data_requerimento")),
            "data_reg": data(r.get("Data_Registro")), "data_enc": data(r.get("Data_Encerramento")),
            "status": txt(r.get("Status_Requerimento")), "valor_mobiliario": txt(r.get("Valor_Mobiliario")),
            "emissor": txt(r.get("Nome_Emissor")), "cnpj": so_digitos(r.get("CNPJ_Emissor")) or None,
            "tipo": txt(r.get("Tipo_Oferta")), "inicial": (ini == "S") if ini in ("S", "N") else None,
            "qtde": num(r.get("Qtde_Total_Registrada")), "valor": num(r.get("Valor_Total_Registrado")),
            "lider": txt(r.get("Nome_Lider")), "publico": txt(r.get("Publico_alvo")),
            "mercado": txt(r.get("Mercado_negociacao")), "bookbuilding": txt(r.get("Bookbuilding")),
            "regime": txt(r.get("Regime_distribuicao")), "rito": txt(r.get("Rito_Requerimento")),
            "destinacao": destin[:800] if destin else None, "site": txt(r.get("Endereco_emissor_rede_mundial_computadores")),
        }
    return list(out.values())


def coletar_ofertas(fonte, saida, hoje):
    try:
        c = baixar(URL_OFERTAS, fonte, obrigatorio=False)
        if not c:
            log("Ofertas de ações: arquivo da CVM não encontrado (aviso; fundos seguem normais)")
            return
        ofs = ler_ofertas(c, hoje)
        abertas = sum(1 for o in ofs if not o["data_enc"] and o["status"] not in ("Oferta Encerrada", "Oferta Revogada", "Registro Caducado", "Requerimento Expirado"))
        log(f"Ofertas de ações (CVM 160, 3 anos): {len(ofs)}, das quais {sum(1 for o in ofs if o['inicial'])} IPO(s) e {abertas} em andamento")
        if saida:
            json.dump(ofs, open(os.path.join(saida, "ofertas_acoes.json"), "w"), ensure_ascii=False)
            return
        gravar("ofertas_acoes", ofs, "numero")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: ofertas de ações não atualizadas ({str(e)[:200]}){' — rode o SQL 54' if '404' in str(e) or 'ofertas_acoes' in str(e) else ''}")


# ------------------------------------------------------------ v1.4: dólar/euro (PTAX) e manchetes
def ptax(moeda, fonte, hoje, dias=400):
    """PTAX de fechamento (venda) por dia -> [{'indice','data','valor'}]."""
    if fonte:
        p = os.path.join(fonte, f"ptax_{moeda}.json")
        if not os.path.exists(p):
            return []
        dados = json.load(open(p))["value"]
    else:
        ini = (hoje - dt.timedelta(days=dias)).strftime("%m-%d-%Y")
        r = requests.get(URL_PTAX.format(m=moeda, ini=ini, fim=hoje.strftime("%m-%d-%Y")), timeout=60)
        r.raise_for_status()
        dados = r.json()["value"]
    por_dia = {}
    for x in dados:
        if x.get("tipoBoletim") == "Fechamento" and x.get("cotacaoVenda"):
            por_dia[x["dataHoraCotacao"][:10]] = float(x["cotacaoVenda"])
    return [{"indice": moeda, "data": d, "valor": v} for d, v in sorted(por_dia.items())]


def manchetes(fonte_dir):
    """Título, data e link de cada feed (nunca o texto)."""
    linhas = []
    for regiao, nome, url, arq in FEEDS:
        try:
            if fonte_dir:
                p = os.path.join(fonte_dir, arq)
                if not os.path.exists(p):
                    continue
                bruto = open(p, "rb").read()
            else:
                r = requests.get(url, headers={"User-Agent": UA_FEEDS}, timeout=30)
                r.raise_for_status()
                bruto = r.content
            raiz = ET.fromstring(bruto.lstrip(b"\xef\xbb\xbf").strip())
            n = 0
            for it in raiz.iter("item"):
                tit = " ".join((it.findtext("title") or "").split())
                link = (it.findtext("link") or "").strip()
                if not tit or not link.startswith("http"):
                    continue
                pub = None
                d = it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")
                if d:
                    try:
                        pub = email.utils.parsedate_to_datetime(d.strip()).astimezone(dt.timezone.utc).isoformat()
                    except (TypeError, ValueError):
                        try:
                            pub = dt.datetime.fromisoformat(d.strip().replace("Z", "+00:00")).isoformat()
                        except ValueError:
                            pub = None
                linhas.append({"id": hashlib.sha1(link.encode()).hexdigest()[:24], "regiao": regiao, "fonte": nome,
                               "titulo": tit[:400], "link": link[:1000], "publicado": pub})
                n += 1
                if n >= 60:
                    break
            log(f"  manchetes — {nome}: {n}")
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: manchetes de {nome} indisponíveis ({str(e)[:120]})")
    corte = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    linhas = [l for l in linhas if not l["publicado"] or l["publicado"] >= corte]   # só os últimos 30 dias
    return list({l["id"]: l for l in linhas}.values())


def coletar_cripto(fonte, saida, hoje):
    """v1.5 — fechamentos diários (US$) da Kraken; nunca derruba os fundos."""
    try:
        cad, hist = [], []
        hoje_utc = dt.datetime.now(dt.timezone.utc).date()
        corte = hoje - dt.timedelta(days=400)
        for ordem, (sim, nome, par) in enumerate(CRIPTOS, 1):
            try:
                if fonte:
                    p = os.path.join(fonte, f"kraken_{sim}.json")
                    if not os.path.exists(p):
                        continue
                    j = json.load(open(p))
                else:
                    r = requests.get(URL_KRAKEN.format(par=par), headers={"User-Agent": UA_FEEDS}, timeout=30)
                    r.raise_for_status()
                    j = r.json()
                    time.sleep(1.2)   # limite da consulta pública
                if j.get("error"):
                    log(f"  aviso: cripto {sim}: {j['error']}")
                    continue
                res = j.get("result") or {}
                velas = next((v for k, v in res.items() if k != "last" and isinstance(v, list)), [])
                n = 0
                for v in velas:
                    d = dt.datetime.fromtimestamp(int(v[0]), dt.timezone.utc).date()
                    if d >= hoje_utc or d < corte:
                        continue   # dia em andamento (incompleto) ou antigo demais
                    fech, vwap, vol = float(v[4]), float(v[5] or 0), float(v[6] or 0)
                    if fech <= 0:
                        continue
                    hist.append({"simbolo": sim, "data": d.isoformat(), "fechamento": fech, "volume_usd": round(vol * (vwap or fech), 2)})
                    n += 1
                if n:
                    cad.append({"simbolo": sim, "nome": nome, "par": par, "ordem": ordem})
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: cripto {sim} indisponível ({str(e)[:100]})")
        ult = max((h["data"] for h in hist), default=None)
        log(f"Criptomoedas (Kraken): {len(cad)} moeda(s), {len(hist)} fechamento(s)" + (f", último dia completo {ult}" if ult else ""))
        if saida:
            json.dump({"cripto": cad, "cripto_hist": hist}, open(os.path.join(saida, "cripto.json"), "w"))
            return
        if cad:
            gravar("cripto", cad, "simbolo")
            gravar("cripto_hist", hist, "simbolo,data")
            url, cab = cab_supabase()
            r = requests.post(f"{url}/rest/v1/rpc/cripto_limpar", json={}, headers=cab, timeout=120)
            log(f"Limpeza das criptomoedas: {r.text.strip()}" if r.status_code < 300 else f"  aviso: limpeza das criptomoedas não feita ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: criptomoedas não atualizadas ({str(e)[:150]}){' — rode o SQL 60' if 'cripto' in str(e) else ''}")


def _sec_get(url, fonte_dir, arq, ausente=(404,)):
    if fonte_dir:
        p = os.path.join(fonte_dir, arq)
        return open(p, "rb").read() if os.path.exists(p) else None
    for t in range(3):
        r = requests.get(url, headers={"User-Agent": UA_FEEDS, "Accept-Encoding": "gzip, deflate"}, timeout=120)
        if r.status_code in ausente:
            return None
        if r.status_code == 200:
            time.sleep(0.15)   # SEC: até 10 consultas por segundo
            return r.content
        time.sleep(3 * (t + 1))
    raise RuntimeError(f"SEC {r.status_code} em {url}")


def _idx_pedidos(conteudo, desde):
    """Linhas S-1/F-1 (pedido inicial) de um form.idx -> {cik: (data, forma)}."""
    out = {}
    for linha in conteudo.decode("latin-1").splitlines():
        forma = linha[:12].strip()
        if forma not in IPO_FORMS:
            continue
        m = re.search(r"\s(\d{1,10})\s+(\d{4}-\d{2}-\d{2})\s+edgar/", linha)
        if not m or m.group(2) < desde:
            continue
        cik = int(m.group(1))
        if cik not in out or m.group(2) < out[cik][0]:
            out[cik] = (m.group(2), forma)
    return out


def eh_fundo(sic, nome):
    """ETF/fundo/trust de ativos (ex.: ETFs de cripto) — usam o S-1, mas não são empresas abrindo capital."""
    n = (nome or "").upper()
    return str(sic or "") == "6221" or bool(re.search(r"\bETF\b|\bFUND\b|\bTRUST\b.*\b(BITCOIN|ETHER|CRYPTO|COIN|GOLD|SILVER)|\b(BITCOIN|ETHER|SOLANA|XRP|DOGE|CRYPTO)\b.*\bTRUST\b", n))


def _ipo_status(sub, primeiro, hoje):
    """Situação de um pedido a partir da ficha da SEC (submissions)."""
    r = (sub.get("filings") or {}).get("recent") or {}
    eventos = sorted(zip(r.get("form", []), r.get("filingDate", []), r.get("accessionNumber", [])), key=lambda x: x[1])
    if any(f in JA_PUBLICA and d < primeiro for f, d, _ in eventos):
        return None   # já entregava balanços: não é IPO
    regs = [(f, d, a) for f, d, a in eventos if f in IPO_FORMS and d >= primeiro]
    if not regs:
        return None
    f0, d0, a0 = regs[0]
    emendas = [d for f, d, _ in eventos if f in ("S-1/A", "F-1/A") and d >= d0]
    efetivado = next((d for f, d, _ in eventos if f == "EFFECT" and d >= d0), None)
    preco = next((d for f, d, _ in eventos if f == "424B4" and d >= d0), None)
    retirado = next((d for f, d, _ in reversed(eventos) if f == "RW" and d >= d0), None)
    ultima = max([d0] + emendas + [x for x in (efetivado, preco, retirado) if x])
    if preco:
        st = "estreou"
    elif retirado and retirado >= ultima:
        st = "retirado"
    elif efetivado:
        st = "efetivado"
    elif (hoje - dt.date.fromisoformat(ultima)).days > 180:
        st = "parado"
    else:
        st = "em_analise"
    tks = [t for t in (sub.get("tickers") or []) if t]
    exs = [e for e in (sub.get("exchanges") or []) if e]
    sic = str(sub.get("sic") or "")
    return {"cik": int(sub.get("cik") or 0), "empresa": (sub.get("name") or "").strip()[:200], "forma": f0,
            "primeiro_registro": d0, "emendas": len(emendas), "ultima_atividade": ultima, "efetivado": efetivado,
            "preco_definido": preco, "retirado": retirado, "situacao": st, "ticker": tks[0] if tks else None,
            "bolsa": exs[0] if exs else None, "sic": sic or None, "setor_sec": (sub.get("sicDescription") or None),
            "spac": sic == "6770", "fundo": eh_fundo(sic, sub.get("name")), "estado": (sub.get("stateOfIncorporation") or None),
            "pais": ((sub.get("addresses") or {}).get("business") or {}).get("stateOrCountryDescription"),
            "registro_link": f"https://www.sec.gov/Archives/edgar/data/{int(sub.get('cik') or 0)}/{a0.replace('-', '')}/"}


def coletar_ipos_eua(fonte, saida, hoje):
    """v1.6 — nunca derruba os fundos."""
    try:
        desde = (hoje - dt.timedelta(days=365)).isoformat()
        ja = {}
        if not saida and not fonte:
            try:
                url, cab = cab_supabase()
                ja, ini = {}, 0
                while True:   # lê a tabela em páginas de 1000
                    r = requests.get(f"{url}/rest/v1/ipos_eua?select=cik,situacao,primeiro_registro&order=cik.asc&limit=1000&offset={ini}", headers=cab, timeout=60)
                    if r.status_code >= 300:
                        raise RuntimeError(r.status_code)
                    lote = r.json()
                    ja.update({int(x["cik"]): x for x in lote})
                    if len(lote) < 1000:
                        break
                    ini += 1000
            except Exception:  # noqa: BLE001
                ja = None
            if ja is None:
                log("  aviso: IPOs dos EUA não atualizados (rode o SQL 61); fundos seguem normais")
                return
        pedidos = {}
        if not ja:   # 1ª vez: índices trimestrais dos últimos 12 meses
            a, q = hoje.year, (hoje.month - 1) // 3 + 1
            for _ in range(5):
                c = _sec_get(SEC_IDX_TRI.format(a=a, q=q), fonte, f"form_{a}Q{q}.idx")
                if c:
                    pedidos.update({k: v for k, v in _idx_pedidos(c, desde).items() if k not in pedidos or v[0] < pedidos[k][0]})
                q -= 1
                if q == 0:
                    a, q = a - 1, 4
            log(f"IPOs EUA: carga inicial pelos índices trimestrais — {len(pedidos)} empresa(s) com S-1/F-1 em 12 meses")
        else:        # depois: índices diários dos últimos 7 dias
            for k in range(1, 8):   # v1.8: o índice de hoje só sai depois que o dia termina
                d = hoje - dt.timedelta(days=k)
                if d.weekday() >= 5:
                    continue
                c = _sec_get(SEC_IDX_DIA.format(a=d.year, q=(d.month - 1) // 3 + 1, d=d.strftime("%Y%m%d")), fonte, f"form_{d:%Y%m%d}.idx",
                             ausente=(403, 404))   # v1.8: SEC responde 403 para índice ainda não publicado
                if c:
                    pedidos.update(_idx_pedidos(c, desde))
        # acompanha: novos + os que ainda podem mudar (em análise, efetivado, parado)
        alvo = dict(pedidos)
        for cik, x in (ja or {}).items():
            if x.get("situacao") in ("em_analise", "efetivado", "parado") and str(x.get("primeiro_registro")) >= desde:
                alvo.setdefault(cik, (str(x["primeiro_registro"]), None))
        linhas, ignoradas = [], 0
        for cik, (d0, _f) in alvo.items():
            try:
                c = _sec_get(SEC_SUB.format(cik=cik), fonte, f"sub_{cik}.json")
                if not c:
                    continue
                st = _ipo_status(json.loads(c), d0, hoje)
                if st:
                    linhas.append(st)
                else:
                    ignoradas += 1
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: ficha da SEC {cik} indisponível ({str(e)[:80]})")
        cont = {}
        for l in linhas:
            cont[l["situacao"]] = cont.get(l["situacao"], 0) + 1
        log(f"IPOs EUA (SEC): {len(linhas)} pedido(s) de IPO acompanhados {cont}; {ignoradas} de empresas que já tinham ações na bolsa (não são IPO)")
        if saida:
            json.dump(linhas, open(os.path.join(saida, "ipos_eua.json"), "w"), ensure_ascii=False)
            return
        if linhas:
            gravar("ipos_eua", linhas, "cik")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: IPOs dos EUA não atualizados ({str(e)[:150]}){' — rode o SQL 61' if 'ipos_eua' in str(e) else ''}")


def coletar_cambio_noticias(fonte, saida, hoje):
    try:
        linhas = ptax("USD", fonte, hoje) + ptax("EUR", fonte, hoje)
        usd = [l for l in linhas if l["indice"] == "USD"]
        log(f"Dólar e euro (PTAX, Banco Central): {len(linhas)} cotação(ões)" + (f"; dólar em {usd[-1]['data']}: R$ {usd[-1]['valor']:.4f}" if usd else ""))
        if saida:
            json.dump(linhas, open(os.path.join(saida, "cambio.json"), "w"))
        elif linhas:
            gravar("indices_hist", linhas, "indice,data")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: dólar e euro não atualizados ({str(e)[:150]})")
    try:
        ns = manchetes(fonte)
        log(f"Manchetes: {len(ns)} (Brasil {sum(1 for n in ns if n['regiao'] == 'br')}, EUA {sum(1 for n in ns if n['regiao'] == 'us')})")
        if saida:
            json.dump(ns, open(os.path.join(saida, "noticias.json"), "w"), ensure_ascii=False)
            return
        if ns:
            gravar("noticias", ns, "id")
            url, cab = cab_supabase()
            r = requests.post(f"{url}/rest/v1/rpc/noticias_limpar", json={}, headers=cab, timeout=120)
            log(f"Limpeza das manchetes: {r.text.strip()}" if r.status_code < 300 else f"  aviso: limpeza das manchetes não feita ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: manchetes não atualizadas ({str(e)[:150]}){' — rode o SQL 56' if 'noticias' in str(e) else ''}")


# ------------------------------------------------------------ Supabase
def cab_supabase():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url, cab


def meses_gravados():
    url, cab = cab_supabase()
    r = requests.get(f"{url}/rest/v1/fundos_inv_mes?select=mes&order=mes.asc&limit=1", headers=cab, timeout=60)
    if r.status_code == 404:
        raise RuntimeError("As tabelas de fundos não existem: rode o SQL 51 no Supabase.")
    r.raise_for_status()
    return bool(r.json())


def gravar(tabela, linhas, conflito, lote_tam=1000):
    if not linhas:
        log(f"{tabela}: nada para gravar")
        return 0
    url, cab = cab_supabase()
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(0, len(linhas), lote_tam):
        lote = [dict(l, coletado_em=agora) for l in linhas[i:i + lote_tam]]
        for _ in range(3):
            r = requests.post(f"{url}/rest/v1/{tabela}?on_conflict={conflito}", json=lote,
                              headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=180)
            if r.status_code < 300:
                break
            log(f"  {tabela} lote {i // lote_tam + 1}: {r.status_code} {r.text[:200]}; nova tentativa")
            time.sleep(10)
        else:
            raise RuntimeError(f"Gravação de {tabela} recusada pelo Supabase (ver a linha acima).")
    log(f"{tabela}: {len(linhas)} linha(s) gravada(s)")
    return len(linhas)


def apagar(tabela, filtro, rotulo):
    url, cab = cab_supabase()
    r = requests.delete(f"{url}/rest/v1/{tabela}?{filtro}", headers=dict(cab, Prefer="return=minimal,count=exact"), timeout=180)
    if r.status_code >= 300:
        log(f"  aviso: limpeza de {rotulo} não feita ({r.status_code} {r.text[:150]})")
        return
    faixa = r.headers.get("Content-Range", "")
    log(f"Limpeza — {rotulo}: {faixa.rsplit('/', 1)[-1] if '/' in faixa else '?'} linha(s)")


# ------------------------------------------------------------ principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte")
    ap.add_argument("--saida")
    ap.add_argument("--meses", type=int, help="testes: quantos meses de informe ler")
    ap.add_argument("--hoje", help="testes: data de referência AAAA-MM-DD")
    a = ap.parse_args()
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.date.today()

    conteudo_cad = baixar(URL_CAD, a.fonte)   # v1.7: guardado para FIDC/FIP/FIAGRO
    cad = ler_cadastro(conteudo_cad)
    log(f"Cadastro CVM: {len(cad)} classes de FIF abertas ao público em geral ou a qualificados (sem exclusivos)")
    ext = ler_extratos([hoje.year - 2, hoje.year - 1, hoje.year], a.fonte)
    com_ext = 0
    for cnpj, f in cad.items():
        e = ext.get(cnpj)
        f.update(e if e else {"taxa_adm": None, "taxa_perf": None, "aplic_min": None,
                              "dias_conversao": None, "dias_pagamento": None, "dt_extrato": None})
        com_ext += 1 if e else 0
    log(f"Extrato (taxas, aplicação mínima, prazos): {com_ext} de {len(cad)} fundos")

    if a.meses:
        n = a.meses
    elif a.saida:
        n = MESES_HIST + 1
    else:
        n = 2 if meses_gravados() else MESES_HIST + 1
    lista = meses_ate(hoje, n)
    log("Modo: " + ("HISTÓRICO COMPLETO" if n > 2 else "mês atual e anterior") + f" — {n} mês(es) de Informe Diário")

    universo = set(cad)
    hist, recentes = [], []
    for k, am in enumerate(lista):
        c = baixar(URL_INF.format(am=am), a.fonte, obrigatorio=(k == 0 and not a.fonte))
        if not c:
            log(f"  informe {am}: não encontrado")
            continue
        dd = por_dia(ler_informe(c, universo))
        hist += resumo_mes(dd, am)
        if k < 2:
            recentes.append(dd)
        log(f"  informe {am}: {dd['cnpj'].nunique() if not dd.empty else 0} fundos")

    # última cota e captação líquida de 30 dias
    rec = pd.concat(recentes, ignore_index=True) if recentes else pd.DataFrame()
    fundos = []
    if not rec.empty:
        rec = rec.sort_values("DT_COMPTC")
        ult = rec.drop_duplicates("cnpj", keep="last").set_index("cnpj")
        rec["d"] = pd.to_datetime(rec["DT_COMPTC"])
        lim = rec.groupby("cnpj")["d"].transform("max") - pd.Timedelta(days=30)
        jan = rec[rec["d"] > lim]
        capt = (jan["CAPTC_DIA"].fillna(0) - jan["RESG_DIA"].fillna(0)).groupby(jan["cnpj"]).sum()
        for cnpj, r in ult.iterrows():
            f = dict(cad[cnpj])
            f.update({"dt_cota": r["DT_COMPTC"], "cota": float(r["VL_QUOTA"]), "pl": num(r["VL_PATRIM_LIQ"]),
                      "cotistas": inteiro(r["NR_COTST"]), "capt_liq_30d": round(float(capt.get(cnpj, 0.0)), 2)})
            fundos.append(f)
    log(f"Fundos com cota nos 2 últimos meses: {len(fundos)} de {len(cad)}")
    if not fundos:
        raise RuntimeError("Nenhum fundo com cota recente — confira o Informe Diário da CVM.")
    datas = sorted(f["dt_cota"] for f in fundos)
    log(f"Cota mais recente: {datas[-1]}")
    cdi, estimados = cdi_mensal(meses_ate(hoje, MESES_HIST + 1), a.fonte, hoje)   # v1.2: sempre 37 meses
    linhas_cdi = [{"indice": "CDI", "data": k, "valor": v} for k, v in sorted(cdi.items())]
    linhas_est = [{"indice": "CDI_ESTIMADO", "data": k, "valor": 1} for k in sorted(estimados)]

    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
        for nome, obj in (("fundos_inv", fundos), ("fundos_inv_mes", hist), ("cdi", linhas_cdi)):
            json.dump(obj, open(os.path.join(a.saida, nome + ".json"), "w"), ensure_ascii=False)
        log(f"teste: fundos_inv {len(fundos)}, fundos_inv_mes {len(hist)}, CDI {len(linhas_cdi)} (sem gravar)")
        coletar_ofertas(a.fonte, a.saida, hoje)   # v1.3
        coletar_cambio_noticias(a.fonte, a.saida, hoje)   # v1.4
        coletar_cripto(a.fonte, a.saida, hoje)   # v1.5
        coletar_ipos_eua(a.fonte, a.saida, hoje)   # v1.6
        coletar_estruturados(conteudo_cad, a.fonte, a.saida, hoje)   # v1.7
        return

    gravar("fundos_inv", fundos, "cnpj")
    gravar("fundos_inv_mes", [h for h in hist if h["cnpj"] in {f['cnpj'] for f in fundos}], "cnpj,mes")
    if linhas_cdi:
        apagar("indices_hist", "indice=eq.CDI_ESTIMADO", "marcas de CDI estimado anteriores")
        gravar("indices_hist", linhas_cdi + linhas_est, "indice,data")
    agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)
    apagar("fundos_inv", f"coletado_em=lt.{agora.isoformat().replace('+00:00', 'Z')}", "fundos que saíram do universo")
    corte = meses_ate(hoje, MESES_HIST + 1)[-1]
    url, cab = cab_supabase()
    r = requests.post(f"{url}/rest/v1/rpc/fundos_limpar", json={"p_corte": f"{corte[:4]}-{corte[4:]}-01"}, headers=cab, timeout=180)
    if r.status_code < 300:
        log(f"Limpeza — meses antigos e de fundos encerrados: {r.text.strip()} linha(s)")
    else:
        log(f"  aviso: limpeza dos meses não feita ({r.status_code} {r.text[:150]})")
    coletar_ofertas(a.fonte, None, hoje)   # v1.3
    coletar_cambio_noticias(a.fonte, None, hoje)   # v1.4
    coletar_cripto(a.fonte, None, hoje)   # v1.5
    coletar_ipos_eua(a.fonte, None, hoje)   # v1.6
    coletar_estruturados(conteudo_cad, a.fonte, None, hoje)   # v1.7
    log("FIM — Fundos de investimento atualizados.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"ERRO: {e}")
        sys.exit(1)
