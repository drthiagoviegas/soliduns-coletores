"""
SOLIDUNS — Coletor do MERCADO AMERICANO (NYSE e Nasdaq) — v1.10 (10/10/2026)

v1.10 (Busca avançada igual à brasileira — sem SQL novo, sem rotina nova):
a cobertura dos indicadores americanos estava bem abaixo da brasileira
(margem bruta 41% × 86%; P/Ativo circ. líq. 29% × 86%; CAGR do lucro 34% ×
63%). Quatro correções, só nas empresas dos EUA:
  1. LUCRO BRUTO POR DIFERENÇA: muitas empresas não informam "GrossProfit",
     mas informam a receita e o custo (CostOfRevenue, CostOfGoodsAndServicesSold
     ou CostOfGoodsSold). Faltando o lucro bruto, ele passa a ser
     receita − custo, no MESMO período dos 12 meses e só para empresa não
     financeira. Vale também para o histórico anual (ano a ano). E quando o
     GrossProfit informado é de um período mais VELHO que a receita (empresa
     que parou de informar a etiqueta — ex.: Honeywell desde 2020 — ficava
     com a margem bruta misturando períodos), vale o derivado do período
     atual. O mesmo para o EBIT velho, na correção 2.
  2. EBIT DE RESERVA: faltando "OperatingIncomeLoss", o EBIT passa a ser o
     lucro antes dos impostos + despesa de juros (aproximação usual), só
     para empresa não financeira (em banco, juro é a operação). Vale também
     para o histórico anual. Destrava margem EBIT, P/EBIT, EV/EBIT, ROIC e
     dívida líquida/EBIT.
  3. HISTÓRICO DE 5 ANOS POR DATA E COM AS ETIQUETAS SOMADAS: para o CAGR e
     para o histórico anual, os exercícios das VÁRIAS etiquetas de um mesmo
     conceito agora são juntados (empresa que trocou de etiqueta, ex.:
     SalesRevenueNet → RevenueFromContract..., não perde os anos antigos); e
     o exercício "de 5 anos atrás" é achado pela DATA (±45 dias), não pela
     posição na lista (ano faltando deslocava a conta). Os 12 meses (TTM)
     continuam numa etiqueta só, como antes.
  4. MESMA RÉGUA DO BRASIL: P/Capital de giro e Preço/Ativo circulante
     líquido passam a ser gravados também quando o denominador é negativo
     (a base brasileira guarda o valor com sinal); e o PEG passa a existir
     sempre que há P/L e CAGR do lucro (na base brasileira a conta é essa).
As colunas do banco não mudam; os conceitos novos (custo, lucro antes dos
impostos, juros) são usados no cálculo e descartados antes de gravar.

v1.9 (v68, SQL 63): depois do cálculo dos BDRs em lotes (v1.8, inalterado),
chama public.bdrs_calcular_etf() para os BDRs de ETF (final 39), que não têm
valor de mercado na brapi: ETF de origem pelo nome e pelo código, paridade
pelo preço do ETF lá fora × dólar PTAX, sem ambiguidade. Sem o SQL 63, só
avisa.

v1.8 (SQL 58): (1) o cálculo da paridade e do ágio dos BDRs é chamado em 8
lotes (por letra inicial do código), cada um bem abaixo do limite de tempo
do Supabase — na 1ª coleta real a chamada única passou de 8 s (erro 500);
(2) data do pregão dos BDRs: antes das 18h de Brasília vale o pregão
ANTERIOR (a 1ª coleta, à 0h27 de segunda, gravou os preços de sexta como
se fossem de segunda).

v1.7 (BDRs, SQL 57): em todas as execuções, lê a lista pública de BDRs da
brapi (sem chave; ~2 consultas de 500), grava public.bdrs e public.bdrs_hist
e chama public.bdrs_calcular(), que descobre sozinho a empresa de origem e a
paridade (valor de mercado da brapi ÷ preço × ações da SEC, conferido pelo
preço lá fora × dólar PTAX) e calcula o ágio. Depois public.bdrs_limpar().
Sem o SQL 57, só avisa; falha nos BDRs nunca derruba a coleta das ações.

v1.6 (ETFs, SQL 55): a consulta diária "grouped daily" da Massive já traz o
fechamento de TODOS os ETFs (antes descartados): agora os ETFs da lista são
guardados em public.etfs_us_hist (45 dias + último pregão de cada mês). No
modo semanal: lista de ETFs da Massive (nome e bolsa; ~5 consultas) em
public.etfs_us e, enquanto faltar, o fechamento de fim de mês dos últimos
13 meses (1 consulta por mês, só uma vez). Ao final chama
public.etfs_us_limpar(). Sem o SQL 55, só avisa; falha nos ETFs nunca
derruba a coleta das ações. REITs não precisam de nada novo (SIC 6798).

v1.5 (Agenda de resultados EUA, SQL 49 — sem coleta extra): do mesmo
arquivo semanal da SEC, guarda a data em que cada empresa entregou cada
10-Q e 10-K (o 1º arquivamento do balanço daquele período) em
public.resultados_entregas_us. Sem o SQL 49, só avisa.

v1.4 (gráficos do relatório, SQL 48 — sem coleta extra): guarda abertura,
máxima e mínima de cada pregão (candles) e o fechamento do ETF SPY (S&P
500) em public.indices_hist — os dois já vinham na consulta diária da
Massive e eram descartados; extrai do mesmo arquivo semanal da SEC o
histórico anual (public.fundamentos_historico_us); e, só no modo
semanal, busca os dividendos americanos novos desde a última data
gravada (public.proventos_us). Na 1ª execução semanal com o SQL 48,
rebusca os 22 últimos pregões uma vez (preenche candles e o SPY). Sem o
SQL 48, só avisa e segue como antes.

v1.3: o Supabase entrega no máximo 1.000 linhas por consulta e o coletor
esperava blocos de 10.000 — ao reler os fechamentos (e, no modo diário, os
balanços) ficava só com as 1.000 primeiras linhas: indicadores sem
variação do dia e com liquidez incompleta (Cotações de hoje vazias). Agora
lê em páginas de 1.000, em ordem fixa, até acabar.

v1.2: a 1ª coleta real gravou 5.164 ações (SEC) e recebeu 264.068
fechamentos (Massive), mas o banco recusou os preços: a Massive manda o
mesmo papel duas vezes no dia em grafias que viram o mesmo código (ex.:
BRK.B / BRK-B). Agora fica UM fechamento por ação e dia (o de maior
volume) e um registro por ticker em balanços e indicadores.

v1.1: cabe no Supabase gratuito — guarda fechamentos SÓ das ações com
balanço da SEC (a Massive manda ~10 mil códigos/dia: ETFs, warrants...) e
por 1 ano (370 dias); ao final chama public.cotacoes_us_limpar() (SQL 47),
que também apaga o excesso gravado pela v1. Sem o SQL 47, só avisa.

Fontes:
  - SEC (EDGAR, domínio público): lista oficial de tickers com a bolsa e o
    arquivo único "companyfacts.zip" com os dados XBRL dos 10-K/10-Q de
    todas as empresas; setor pela classificação SIC (submissions).
  - Massive (ex-Polygon.io): fechamento diário de TODAS as ações em uma
    consulta por dia ("grouped daily"). Plano gratuito: 5 consultas/min.

Modos:
  python coletar_eua.py              -> semanal: balanços (SEC) + preços + indicadores
  python coletar_eua.py --precos     -> diário: só preços (Massive) + indicadores
  python coletar_eua.py --fonte pasta/ --saida pasta/   -> testes (arquivos locais)

Grava (SQL 45): public.fundamentos_us, public.cotacoes_us, public.indicadores_us.

Variáveis de ambiente (Secrets do GitHub):
  SUPABASE_URL, SUPABASE_SERVICE_KEY   (as mesmas da Coleta CVM)
  MASSIVE_API_KEY                      chave gratuita da Massive (massive.com)
  SEC_USER_AGENT                       opcional; a SEC exige identificação com
                                       e-mail (padrão: SOLIDUNS + admin@...)
"""
import argparse, datetime as dt, io, json, math, os, re, sys, tempfile, time, zipfile
from collections import defaultdict

import requests

SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_BULK = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
MASSIVE_GROUPED = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{data}?adjusted=true&apiKey={chave}"
MASSIVE_DIVIDENDOS = ("https://api.polygon.io/v3/reference/dividends?ex_dividend_date.gte={desde}"
                      "&order=asc&sort=ex_dividend_date&limit=1000&apiKey={chave}")
MASSIVE_ETFS = "https://api.polygon.io/v3/reference/tickers?type=ETF&market=stocks&active=true&limit=1000&apiKey={chave}"   # v1.6
BOLSAS_ETF = {"XNYS": "NYSE", "ARCX": "NYSE Arca", "XNAS": "Nasdaq", "BATS": "Cboe BZX", "XASE": "NYSE American"}
MESES_ETF = 13
BRAPI_BDR = "https://brapi.dev/api/quote/list?type=bdr&limit=500&page={pag}&sortBy=volume&sortOrder=desc"   # v1.7
UA_SEC = os.environ.get("SEC_USER_AGENT") or "SOLIDUNS Consultoria admin@soliduns.com.br"
BOLSAS = {"NYSE": "NYSE", "Nasdaq": "Nasdaq", "NASDAQ": "Nasdaq"}
DIAS_PRECO_GUARDADOS = 370
DIAS_LIQUIDEZ = 20
PAUSA_MASSIVE = 13          # 5 consultas por minuto no plano gratuito
IR_EUA = 0.21               # alíquota federal (NOPAT do ROIC)


def log(*a):
    print(dt.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------------
# Acesso (rede ou pasta local de testes)
# ---------------------------------------------------------------------
FONTE = None


def obter(url, cab=None, stream_para=None, tentativas=3):
    """Baixa a URL (ou lê da pasta de testes). Devolve bytes, ou o caminho
    do arquivo quando stream_para é usado."""
    if FONTE:
        nome = re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("://", 1)[-1].split("?")[0])
        cam = os.path.join(FONTE, nome)
        if not os.path.exists(cam):
            return None
        return cam if stream_para else open(cam, "rb").read()
    for t in range(tentativas):
        try:
            r = requests.get(url, headers=cab or {}, timeout=300, stream=bool(stream_para))
            if r.status_code == 200:
                if stream_para:
                    with open(stream_para, "wb") as f:
                        for bloco in r.iter_content(1 << 20):
                            f.write(bloco)
                    return stream_para
                return r.content
            if r.status_code in (403, 404):
                log(f"  aviso: {r.status_code} em {url.split('?')[0]}")
                return None
            if r.status_code == 429:
                time.sleep(60)
                continue
        except requests.RequestException as e:
            log(f"  rede: {type(e).__name__} em {url.split('?')[0]}")
        time.sleep(5 * (t + 1))
    return None


def cab_sec():
    return {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"}


# ---------------------------------------------------------------------
# Setor pelo SIC (classificação de atividade da SEC), em português
# ---------------------------------------------------------------------
FAIXAS_SIC = [
    (100, 999, "Agropecuária"), (1000, 1099, "Mineração"), (1200, 1299, "Mineração"),
    (1300, 1399, "Petróleo e gás"), (1400, 1499, "Mineração"), (1500, 1799, "Construção"),
    (2000, 2199, "Alimentos, bebidas e fumo"), (2200, 2399, "Têxtil e vestuário"),
    (2400, 2599, "Madeira e móveis"), (2600, 2699, "Papel e celulose"), (2700, 2799, "Mídia e editoras"),
    (2830, 2836, "Farmacêutico e biotecnologia"), (2800, 2899, "Químico"),
    (2900, 2999, "Petróleo e gás"), (3000, 3299, "Materiais industriais"),
    (3300, 3399, "Siderurgia e metalurgia"), (3400, 3499, "Produtos de metal"),
    (3570, 3579, "Tecnologia (hardware)"), (3660, 3699, "Tecnologia (semicondutores e eletrônicos)"),
    (3500, 3599, "Máquinas e equipamentos"), (3600, 3659, "Equipamentos elétricos"),
    (3710, 3716, "Automóveis e autopeças"), (3720, 3729, "Aeroespacial e defesa"),
    (3700, 3799, "Equipamentos de transporte"), (3840, 3851, "Equipamentos médicos"),
    (3800, 3899, "Instrumentos de precisão"), (3900, 3999, "Indústria diversa"),
    (4000, 4799, "Transporte e logística"), (4800, 4899, "Telecomunicações e mídia"),
    (4900, 4999, "Utilidade pública (energia, gás, água)"), (5000, 5199, "Atacado e distribuição"),
    (5200, 5999, "Varejo"), (6000, 6199, "Bancos e crédito"), (6200, 6299, "Corretoras e mercado de capitais"),
    (6300, 6499, "Seguros"), (6798, 6798, "Fundos imobiliários (REITs)"), (6500, 6599, "Imobiliário"),
    (6700, 6799, "Holdings e veículos de investimento"), (7000, 7099, "Hotelaria e turismo"),
    (7370, 7379, "Software e serviços de tecnologia"), (7200, 7399, "Serviços"),
    (7800, 7999, "Entretenimento e lazer"), (8000, 8099, "Serviços de saúde"),
    (8200, 8299, "Educação"), (8700, 8799, "Engenharia e consultoria"), (7400, 8999, "Serviços"),
    (9100, 9999, "Outros"),
]


def setor_do_sic(sic):
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return "Sem setor informado"
    melhor = None
    for ini, fim, nome in FAIXAS_SIC:          # a faixa mais estreita vence
        if ini <= n <= fim and (melhor is None or (fim - ini) < (melhor[1] - melhor[0])):
            melhor = (ini, fim, nome)
    return melhor[2] if melhor else "Outros"


def eh_financeira(sic):
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return False
    return 6000 <= n <= 6499 or (6700 <= n <= 6799 and n != 6798)


# ---------------------------------------------------------------------
# Tickers oficiais (SEC): NYSE e Nasdaq
# ---------------------------------------------------------------------
def normalizar_ticker(t):
    return str(t or "").strip().upper().replace("-", ".")


def ler_tickers():
    b = obter(SEC_TICKERS, cab_sec())
    if not b:
        raise RuntimeError("Não foi possível baixar a lista de tickers da SEC.")
    j = json.loads(b)
    campos = j.get("fields") or ["cik", "name", "ticker", "exchange"]
    idx = {c: campos.index(c) for c in ("cik", "name", "ticker", "exchange")}
    saida = {}
    for linha in j.get("data", []):
        bolsa = BOLSAS.get(str(linha[idx["exchange"]] or ""))
        if not bolsa:
            continue
        t = normalizar_ticker(linha[idx["ticker"]])
        if not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z]{1,2})?", t):
            continue                                   # descarta formatos estranhos
        saida[t] = {"cik": str(int(linha[idx["cik"]])).zfill(10), "empresa": str(linha[idx["name"]] or "").strip(), "bolsa": bolsa}
    log(f"SEC: {len(saida)} tickers da NYSE e Nasdaq ({len({v['cik'] for v in saida.values()})} empresas)")
    return saida


# ---------------------------------------------------------------------
# Balanços (companyfacts XBRL)
# ---------------------------------------------------------------------
CONCEITOS = {
    "receita": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating"],
    "lucro_bruto": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    # v1.10 — só para derivar (descartados antes de gravar): custo total da receita
    # (etiquetas de custo TOTAL; as parciais CostOfServices/CostOfGoods separadas
    # ficam de fora para não superestimar o lucro bruto), lucro antes dos impostos
    # e despesa de juros.
    "custo": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "lai": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "juros": ["InterestExpense", "InterestExpenseNonoperating"],
    "lucro_liq": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "dividendos": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "ativo_total": ["Assets"],
    "ativo_circ": ["AssetsCurrent"],
    "passivo_total": ["Liabilities"],
    "passivo_circ": ["LiabilitiesCurrent"],
    "patrimonio_liq": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "caixa": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "divida_lp": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "divida_lp_circ": ["LongTermDebtCurrent"],
    "divida_cp": ["ShortTermBorrowings", "CommercialPaper"],
}
FLUXOS = {"receita", "lucro_bruto", "ebit", "lucro_liq", "dividendos", "custo", "lai", "juros"}
AUXILIARES = ("custo", "lai", "juros")      # v1.10: entram na derivação e são descartados antes de gravar


def _data(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fatos(gaap, nome):
    """Fatos em US$ de um conceito, só de 10-K/10-Q (e suas emendas)."""
    c = gaap.get(nome)
    if not c:
        return []
    unid = c.get("units", {})
    lista = unid.get("USD") or next(iter(unid.values()), [])
    out = []
    for f in lista:
        if not str(f.get("form", "")).startswith(("10-K", "10-Q")):
            continue
        fim = _data(f.get("end"))
        if not fim or f.get("val") is None:
            continue
        ini = _data(f.get("start")) if f.get("start") else None
        out.append({"ini": ini, "fim": fim, "val": float(f["val"]), "form": f.get("form"), "filed": str(f.get("filed") or "")})
    return out


def _escolher_conceito(gaap, nomes):
    """O conceito da lista com o dado mais recente."""
    melhor, fim_melhor = [], None
    for n in nomes:
        fs = _fatos(gaap, n)
        if fs:
            fim = max(f["fim"] for f in fs)
            if fim_melhor is None or fim > fim_melhor:
                melhor, fim_melhor = fs, fim
    return melhor


def _dedup(fs):
    """Mesmo período reapresentado: fica o arquivado por último."""
    por = {}
    for f in fs:
        k = (f["ini"], f["fim"])
        if k not in por or f["filed"] >= por[k]["filed"]:
            por[k] = f
    return list(por.values())


def _ttm(fs):
    """Últimos 12 meses de um fluxo: último anual + acumulado do ano −
    mesmo acumulado do ano anterior. Sem trimestres: o último anual."""
    fs = [f for f in _dedup(fs) if f["ini"]]
    if not fs:
        return None, None
    dur = lambda f: (f["fim"] - f["ini"]).days
    anuais = sorted([f for f in fs if 350 <= dur(f) <= 380], key=lambda f: f["fim"])
    ultimo = max(fs, key=lambda f: (f["fim"], dur(f)))
    if not anuais:
        return None, None
    ano = anuais[-1]
    if ultimo["fim"] <= ano["fim"]:
        return ano["val"], ano["fim"]
    # acumulado do ano corrente que começa logo depois do último anual
    ytd = [f for f in fs if f["fim"] == ultimo["fim"] and abs((f["ini"] - ano["fim"]).days - 1) <= 10 and dur(f) < 350]
    if not ytd:
        return ano["val"], ano["fim"]
    y = max(ytd, key=dur)
    alvo_fim = y["fim"] - dt.timedelta(days=365)
    ant = [f for f in fs if abs((f["fim"] - alvo_fim).days) <= 10 and abs(dur(f) - dur(y)) <= 15]
    if not ant:
        return ano["val"], ano["fim"]
    return ano["val"] + y["val"] - ant[0]["val"], y["fim"]


def _anuais_unidos(gaap, nomes):
    """v1.10: exercícios ANUAIS de um conceito juntando as etiquetas da lista.
    Empresa que trocou de etiqueta (ex.: SalesRevenueNet → RevenueFromContract...)
    não perde os anos antigos. Para um mesmo exercício vale a 1ª etiqueta da
    lista que o tiver (ordem = prioridade). Devolve [{fim, val}] por data."""
    por_fim = {}
    for n in nomes:
        for f in _dedup(_fatos(gaap, n)):
            if f["ini"] and 350 <= (f["fim"] - f["ini"]).days <= 380 and f["fim"] not in por_fim:
                por_fim[f["fim"]] = f["val"]
    return [{"fim": fim, "val": por_fim[fim]} for fim in sorted(por_fim)]


def _cagr_serie(anuais, anos=5, tolerancia=45):
    """v1.10: CAGR de `anos` anos achando o exercício inicial pela DATA
    (último exercício − `anos` anos, com `tolerancia` em dias para ano
    fiscal de 52/53 semanas), e não pela posição na lista — um ano
    faltando no meio não desloca mais a conta."""
    if not anuais:
        return None
    ult = anuais[-1]
    alvo = ult["fim"] - dt.timedelta(days=round(anos * 365.25))
    perto = [a for a in anuais if abs((a["fim"] - alvo).days) <= tolerancia]
    if not perto:
        return None
    ini = min(perto, key=lambda a: abs((a["fim"] - alvo).days))
    return _cagr(ult["val"], ini["val"], anos)


def _instantaneo(fs):
    fs = [f for f in _dedup(fs) if not f["ini"]] or _dedup(fs)
    if not fs:
        return None, None
    u = max(fs, key=lambda f: (f["fim"], f["filed"]))
    return u["val"], u["fim"]


def _cagr(fim, ini, anos=5):
    if fim is None or ini is None or fim <= 0 or ini <= 0:
        return None
    return round(((fim / ini) ** (1 / anos) - 1) * 100, 2)


def _acoes(fatos):
    """Ações em circulação: soma das classes na data mais recente (dei)."""
    dei = fatos.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    if dei:
        fim = max(str(f.get("end")) for f in dei)
        por_classe = {}
        for f in dei:
            if str(f.get("end")) == fim:
                por_classe[f.get("frame") or f.get("accn") or len(por_classe)] = float(f["val"])
        total = sum(por_classe.values())
        if total > 0:
            return total
    us = fatos.get("us-gaap", {}).get("CommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    if us:
        return float(max(us, key=lambda f: str(f.get("end")))["val"])
    return None


def extrair_empresa(fatos):
    gaap = fatos.get("facts", {}).get("us-gaap", {})
    if not gaap:
        return None
    r, ref = {}, None
    for chave, nomes in CONCEITOS.items():
        fs = _escolher_conceito(gaap, nomes)
        if chave in FLUXOS:
            v, fim = _ttm(fs)
            r[chave + "_ttm"] = v
            r["_fim_" + chave] = fim                 # v1.10: casar os períodos nas derivações
            if chave == "receita":
                r["cagr_receitas"] = _cagr_serie(_anuais_unidos(gaap, nomes))   # v1.10: por data, etiquetas somadas
            if chave == "lucro_liq":
                r["cagr_lucros"] = _cagr_serie(_anuais_unidos(gaap, nomes))
        else:
            v, fim = _instantaneo(fs)
            r[chave] = v
            if chave == "ativo_total" and fim:
                ref = fim
    if r.get("ativo_total") is None or r.get("receita_ttm") is None and r.get("lucro_liq_ttm") is None:
        return None
    if r.get("passivo_total") is None and r.get("patrimonio_liq") is not None:
        r["passivo_total"] = r["ativo_total"] - r["patrimonio_liq"]
    r["divida_bruta"] = sum(x for x in (r.pop("divida_lp"), r.pop("divida_lp_circ"), r.pop("divida_cp")) if x) or 0.0
    r["historico"] = _historico_anual(gaap)          # v1.4
    r["entregas"] = _entregas(gaap)                  # v1.5
    r["acoes"] = _acoes(fatos.get("facts", {}))
    r["dt_refer"] = ref.isoformat() if ref else None
    ult = max((f for n in CONCEITOS["ativo_total"] for f in _fatos(gaap, n)), key=lambda f: f["fim"], default=None)
    r["documento"] = (ult or {}).get("form", "")[:4] or None
    return r


def _historico_anual(gaap, anos=7):
    """v1.4: uma linha por exercício (fim do ano fiscal), últimos `anos`."""
    anual = {}
    for chave in ("receita", "lucro_bruto", "ebit", "lucro_liq", "dividendos") + AUXILIARES:
        for f in _anuais_unidos(gaap, CONCEITOS[chave]):             # v1.10: etiquetas somadas
            anual.setdefault(f["fim"], {})[chave] = f["val"]
    anual = {fim: a for fim, a in anual.items() if any(k in a for k in ("receita", "lucro_bruto", "ebit", "lucro_liq", "dividendos"))}
    if not anual:
        return []
    inst = {}
    for chave in ("ativo_total", "patrimonio_liq", "caixa", "divida_lp", "divida_lp_circ", "divida_cp"):
        por_data = {f["fim"]: f["val"] for f in sorted(_dedup(_escolher_conceito(gaap, CONCEITOS[chave])), key=lambda f: f["filed"])}
        for fim in anual:
            v = por_data.get(fim)
            if v is None:
                perto = [d for d in por_data if abs((d - fim).days) <= 5]
                v = por_data[perto[0]] if perto else None
            inst.setdefault(fim, {})[chave] = v
    linhas = {}
    for fim in sorted(anual)[-anos:]:
        a, i = anual[fim], inst.get(fim, {})
        div = [i.get(k) for k in ("divida_lp", "divida_lp_circ", "divida_cp") if i.get(k) is not None]
        der = {}                                     # v1.10: derivações do ano — só valem para empresa não financeira
        if a.get("lucro_bruto") is None and a.get("receita") is not None and a.get("custo") is not None:
            der["lucro_bruto"] = a["receita"] - a["custo"]
        if a.get("ebit") is None and a.get("lai") is not None:
            der["ebit"] = a["lai"] + (a.get("juros") or 0)
        linhas[fim.year] = {"ano": fim.year, "dt_refer": fim.isoformat(),
                            "receita": a.get("receita"), "lucro_bruto": a.get("lucro_bruto"), "ebit": a.get("ebit"),
                            "lucro_liq": a.get("lucro_liq"), "dividendos": a.get("dividendos"),
                            "ativo_total": i.get("ativo_total"), "patrimonio_liq": i.get("patrimonio_liq"),
                            "caixa": i.get("caixa"), "divida_bruta": sum(div) if div else None,
                            **({"_derivados": der} if der else {})}
    return list(linhas.values())


def derivar_fundamentos(l):
    """v1.10: derivações que dependem de saber se a empresa é financeira,
    feitas DEPOIS da marcação (main) e ANTES de gravar. Descarta os
    conceitos auxiliares (custo, lai, juros): as colunas do banco não mudam.
    - Lucro bruto por diferença: receita − custo, no mesmo período TTM.
    - EBIT de reserva: lucro antes dos impostos + juros.
    Em empresa financeira nada é derivado (juros são a operação)."""
    fin = bool(l.get("financeira"))
    custo, lai, juros = l.pop("custo_ttm", None), l.pop("lai_ttm", None), l.pop("juros_ttm", None)
    fins = {k: l.pop("_fim_" + k, None) for k in ("receita", "custo", "lucro_bruto", "ebit", "lai", "juros", "lucro_liq", "dividendos")}
    if not fin:
        fr = fins["receita"]
        # Lucro bruto: deriva quando falta E quando o informado é VELHO (empresa que parou de
        # informar GrossProfit ficava com a margem bruta misturando períodos — ex.: Honeywell).
        derivado = (l["receita_ttm"] - custo) if (l.get("receita_ttm") is not None and custo is not None and fr and fins["custo"] == fr) else None
        if derivado is not None and (l.get("lucro_bruto_ttm") is None or (fins["lucro_bruto"] and fins["lucro_bruto"] < fr)):
            l["lucro_bruto_ttm"] = derivado
        # EBIT de reserva: lucro antes dos impostos + juros, no período da receita.
        juros_ali = juros if (juros is not None and fins["juros"] == fins["lai"]) else 0
        reserva = (lai + juros_ali) if (lai is not None and (fr is None or fins["lai"] == fr)) else None
        if reserva is not None and (l.get("ebit_ttm") is None or (fins["ebit"] and fr and fins["ebit"] < fr)):
            l["ebit_ttm"] = reserva
    for h in l.get("historico") or []:
        for k, v in h.pop("_derivados", {}).items():
            if not fin and h.get(k) is None:
                h[k] = v
    return l


def _entregas(gaap, anos=3):
    """v1.5: 1º arquivamento (10-Q/10-K) de cada data de balanço (Assets)."""
    primeiro = {}
    for n in CONCEITOS["ativo_total"]:
        for f in _fatos(gaap, n):
            if f["ini"] or not f["filed"]:
                continue
            k = f["fim"]
            if k not in primeiro or f["filed"] < primeiro[k][0]:
                primeiro[k] = (f["filed"], f["form"])
    if not primeiro:
        return []
    corte = max(primeiro) - dt.timedelta(days=365 * anos)
    saida = []
    for fim, (filed, form) in primeiro.items():
        receb = _data(filed)
        if fim < corte or not receb or receb < fim:
            continue
        saida.append({"documento": "10-K" if str(form).startswith("10-K") else "10-Q",
                      "dt_refer": fim.isoformat(), "dt_receb": receb.isoformat()})
    return saida


def ler_balancos(tickers):
    ciks = {v["cik"] for v in tickers.values()}
    tmp = os.path.join(tempfile.gettempdir(), "companyfacts.zip")
    log("SEC: baixando o arquivo único de balanços (companyfacts.zip, ~1 GB)...")
    cam = obter(SEC_BULK, cab_sec(), stream_para=tmp)
    if not cam:
        raise RuntimeError("Não foi possível baixar o companyfacts.zip da SEC.")
    por_cik, falhas = {}, 0
    with zipfile.ZipFile(cam) as z:
        nomes = {n[3:13]: n for n in z.namelist() if n.startswith("CIK") and n.endswith(".json")}
        for cik in ciks:
            n = nomes.get(cik)
            if not n:
                continue
            try:
                e = extrair_empresa(json.loads(z.read(n)))
                if e:
                    por_cik[cik] = e
            except Exception:
                falhas += 1
    log(f"SEC: balanços extraídos de {len(por_cik)} de {len(ciks)} empresas ({falhas} com dados inesperados)")
    return por_cik


def ler_setores(ciks, conhecidos):
    """SIC de cada empresa (submissions). Só busca o que ainda não se sabe."""
    falta = [c for c in ciks if c not in conhecidos]
    if falta:
        log(f"SEC: buscando o setor (SIC) de {len(falta)} empresa(s) nova(s)...")
    sics = dict(conhecidos)
    for i, cik in enumerate(falta, 1):
        b = obter(SEC_SUBMISSIONS.format(cik=cik), cab_sec())
        if b:
            try:
                sics[cik] = str(json.loads(b).get("sic") or "")
            except ValueError:
                pass
        if not FONTE:
            time.sleep(0.12)                            # SEC: até 10 consultas/s
        if i % 500 == 0:
            log(f"  setores: {i}/{len(falta)}")
    return sics


# ---------------------------------------------------------------------
# Preços (Massive — grouped daily)
# ---------------------------------------------------------------------
def dias_uteis(ate, quantos):
    d, saida = ate, []
    while len(saida) < quantos:
        if d.weekday() < 5:
            saida.append(d)
        d -= dt.timedelta(days=1)
    return sorted(saida)


def precos_do_dia(data, chave):
    b = obter(MASSIVE_GROUPED.format(data=data.isoformat(), chave=chave))
    if not b:
        return None
    j = json.loads(b)
    if j.get("status") not in ("OK", "DELAYED") and not j.get("results"):
        return None
    linhas = []
    for r in j.get("results") or []:
        t = normalizar_ticker(r.get("T"))
        c, v = r.get("c"), r.get("v")
        if not t or c is None:
            continue
        vw = r.get("vw") or c
        num = lambda x: float(x) if x is not None else None
        linhas.append({"ticker": t, "data": data.isoformat(), "fechamento": float(c),
                       "abertura": num(r.get("o")), "maxima": num(r.get("h")), "minima": num(r.get("l")),   # v1.4
                       "volume": float(v or 0), "financeiro": round(float(v or 0) * float(vw), 2)})
    return linhas


def coletar_precos(chave, ja_tem, hoje=None, max_consultas=25):
    """Busca os dias úteis que ainda faltam (até `max_consultas` por execução)."""
    hoje = hoje or dt.date.today()
    alvo = [d for d in dias_uteis(hoje - dt.timedelta(days=1), DIAS_LIQUIDEZ + 2) if d.isoformat() not in ja_tem]
    alvo = alvo[-max_consultas:]
    todas, dias_ok = [], 0
    for i, d in enumerate(alvo):
        linhas = precos_do_dia(d, chave)
        if linhas:
            todas.extend(linhas)
            dias_ok += 1
        if i < len(alvo) - 1 and not FONTE:
            time.sleep(PAUSA_MASSIVE)
    unicos = {}
    for l in todas:                     # v1.2: um fechamento por ação e dia (o de maior volume)
        k = (l["ticker"], l["data"])
        if k not in unicos or (l.get("volume") or 0) > (unicos[k].get("volume") or 0):
            unicos[k] = l
    if len(unicos) < len(todas):
        log(f"  {len(todas) - len(unicos)} fechamento(s) repetido(s) descartado(s)")
    log(f"Massive: {dias_ok} pregão(ões) novo(s), {len(unicos)} fechamentos")
    return list(unicos.values())


# ---------------------------------------------------------------------
# Indicadores (mesmas definições da Busca avançada do Brasil)
# ---------------------------------------------------------------------
def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _r(v, casas=4):
    if v is None or isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return round(v, casas)


def calcular_indicadores(fund, precos):
    """fund: linhas de fundamentos_us; precos: {ticker: [(data, fech, financeiro)] ordenado}."""
    saida = []
    for f in fund:
        serie = precos.get(f["ticker"])
        if not serie:
            continue
        data, preco, _ = serie[-1]
        anterior = serie[-2][1] if len(serie) >= 2 else None
        acoes = f.get("acoes")
        vm = preco * acoes if acoes else None
        rec, lb, ebit, ll = f.get("receita_ttm"), f.get("lucro_bruto_ttm"), f.get("ebit_ttm"), f.get("lucro_liq_ttm")
        at, ac, pt, pc = f.get("ativo_total"), f.get("ativo_circ"), f.get("passivo_total"), f.get("passivo_circ")
        pl_eq, caixa, divida = f.get("patrimonio_liq"), f.get("caixa") or 0, f.get("divida_bruta") or 0
        div_liq = divida - caixa
        pos = lambda x: x if x is not None and x > 0 else None
        cap_giro = (ac - pc) if ac is not None and pc is not None else None
        acl = (ac - pt) if ac is not None and pt is not None else None
        ind = {
            "ticker": f["ticker"], "nome": f.get("empresa"), "setor": f.get("setor"), "bolsa": f.get("bolsa"),
            "financeira": bool(f.get("financeira")),
            "preco": _r(preco), "valor_mercado": _r(vm, 0),
            "dy": _r(_div((f.get("dividendos_ttm") or 0) * 100, vm)) if vm else None,
            "pl": _r(_div(vm, ll)), "pvp": _r(_div(vm, pos(pl_eq))), "p_ativos": _r(_div(vm, at)),
            "margem_bruta": _r(_div(lb * 100, rec) if lb is not None else None),
            "margem_ebit": _r(_div(ebit * 100, rec) if ebit is not None else None),
            "margem_liquida": _r(_div(ll * 100, rec) if ll is not None else None),
            "p_ebit": _r(_div(vm, ebit)), "ev_ebit": _r(_div(vm + div_liq, ebit) if vm else None),
            "div_liq_ebit": _r(_div(div_liq, ebit)), "div_liq_pl": _r(_div(div_liq, pos(pl_eq))),
            "psr": _r(_div(vm, rec)), "p_cap_giro": _r(_div(vm, cap_giro)), "p_acl": _r(_div(vm, acl)),   # v1.10: negativo fica, como no Brasil
            "roe": _r(_div(ll * 100, pos(pl_eq)) if ll is not None else None),
            "roic": _r(_div(ebit * (1 - IR_EUA) * 100, pos((pl_eq or 0) + div_liq)) if ebit is not None else None),
            "roa": _r(_div(ll * 100, at) if ll is not None else None),
            "liq_corrente": _r(_div(ac, pc)), "patr_ativos": _r(_div(pl_eq, at)), "passiv_ativos": _r(_div(pt, at)),
            "giro_ativo": _r(_div(rec, at)), "cagr_receitas": f.get("cagr_receitas"), "cagr_lucros": f.get("cagr_lucros"),
            "liq_diaria": _r(sum(x[2] for x in serie[-DIAS_LIQUIDEZ:]) / len(serie[-DIAS_LIQUIDEZ:]), 0),
            "vpa": _r(_div(pl_eq, acoes)), "lpa": _r(_div(ll, acoes)),
            "variacao_pct": _r((preco / anterior - 1) * 100, 2) if anterior else None,
            "dt_balanco": f.get("dt_refer"), "dt_preco": data,
        }
        pl, cl = ind["pl"], f.get("cagr_lucros")
        ind["peg"] = _r(pl / cl) if pl is not None and cl else None   # v1.10: mesma régua do Brasil (PEG sempre que há P/L e CAGR)
        saida.append(ind)
    return saida


# ---------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------
def supa():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url, cab


PAGINA = 1000          # limite de linhas por consulta do Supabase (v1.3)


def ler_tabela(nome, campos, filtro="", ordem="ticker.asc"):
    url, cab = supa()
    linhas, ini = [], 0
    if ordem and "order=" not in filtro:
        filtro = f"{filtro}&order={ordem}"
    while True:
        r = requests.get(f"{url}/rest/v1/{nome}?select={campos}{filtro}", timeout=120,
                         headers=dict(cab, Range=f"{ini}-{ini + PAGINA - 1}", **{"Range-Unit": "items"}))
        if r.status_code >= 300:
            dica = " Rode o SQL 45 no Supabase." if r.status_code == 404 else ""
            raise RuntimeError(f"Leitura de {nome} falhou: {r.status_code} {r.text[:200]}.{dica}")
        bloco = r.json()
        linhas.extend(bloco)
        if not bloco or len(bloco) < PAGINA and len(bloco) < 1000:
            return linhas
        ini += len(bloco)


def gravar(nome, linhas, conflito, lote=1000):
    if not linhas:
        log(f"  {nome}: nada para gravar")
        return
    url, cab = supa()
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    campo_data = "atualizado_em" if nome == "indicadores_us" else "coletado_em"
    for i in range(0, len(linhas), lote):
        r = requests.post(f"{url}/rest/v1/{nome}?on_conflict={conflito}",
                          json=[dict(l, **{campo_data: agora}) for l in linhas[i:i + lote]],
                          headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=300)
        if r.status_code >= 300:
            dica = " Rode o SQL 45 no Supabase." if r.status_code == 404 else ""
            raise RuntimeError(f"Gravação em {nome} recusada: {r.status_code} {r.text[:300]}.{dica}")
    log(f"{nome}: {len(linhas)} linha(s) gravada(s)")


def gravar_opcional(nome, linhas, conflito, lote=1000):
    """v1.4: tabelas do SQL 48 — falha aqui NÃO derruba a coleta."""
    if not linhas:
        log(f"  {nome}: nada para gravar")
        return
    try:
        gravar(nome, linhas, conflito, lote)
    except (RuntimeError, requests.RequestException) as e:
        log(f"  aviso: {nome} não gravado ({str(e)[:160]}). Rode o SQL 48 no Supabase; o resto da coleta continua valendo.")


def gravar_cotacoes(linhas):
    """v1.4: com abertura/máxima/mínima; sem o SQL 48, grava como antes."""
    try:
        gravar("cotacoes_us", linhas, "ticker,data")
    except RuntimeError as e:
        if "abertura" in str(e) or "maxima" in str(e) or "minima" in str(e) or "column" in str(e).lower():
            log("  aviso: sem as colunas de candles (rode o SQL 48) — gravando só o fechamento.")
            gravar("cotacoes_us", [{k: v for k, v in l.items() if k not in ("abertura", "maxima", "minima")} for l in linhas], "ticker,data")
        else:
            raise


def ultimo_valor(tabela, campo, filtro=""):
    url, cab = supa()
    try:
        r = requests.get(f"{url}/rest/v1/{tabela}?select={campo}{filtro}&order={campo}.desc&limit=1", headers=cab, timeout=60)
        if r.status_code >= 300:
            return "erro"
        j = r.json()
        return j[0][campo] if j else None
    except requests.RequestException:
        return "erro"


def coletar_dividendos(chave, desde, validos, max_paginas=40):
    """v1.4: dividendos com data-com a partir de `desde` (só os novos)."""
    url = MASSIVE_DIVIDENDOS.format(desde=desde, chave=chave)
    linhas, paginas = [], 0
    while url and paginas < max_paginas:
        b = obter(url)
        paginas += 1
        if not b:
            break
        j = json.loads(b)
        for d in j.get("results") or []:
            t = normalizar_ticker(d.get("ticker"))
            if validos and t not in validos:
                continue
            ident = str(d.get("id") or f"{t}|{d.get('ex_dividend_date')}|{d.get('cash_amount')}")
            linhas.append({"id": ident, "ticker": t, "tipo": d.get("dividend_type"),
                           "data_com": d.get("ex_dividend_date"), "pagamento": d.get("pay_date"),
                           "valor": d.get("cash_amount"), "frequencia": d.get("frequency")})
        prox = j.get("next_url")
        url = (prox + ("&" if "?" in prox else "?") + "apiKey=" + chave) if (prox and not FONTE) else None
        if url:
            time.sleep(PAUSA_MASSIVE)
    log(f"Massive: dividendos desde {desde}: {len(linhas)} registro(s) em {paginas} consulta(s)")
    return list({l["id"]: l for l in linhas}.values())


def limpar_precos():
    """v1.1: SQL 47 — apaga fechamentos sem balanço e com mais de 1 ano."""
    url, cab = supa()
    try:
        r = requests.post(f"{url}/rest/v1/rpc/cotacoes_us_limpar", json={"p_dias": DIAS_PRECO_GUARDADOS}, headers=cab, timeout=300)
    except requests.RequestException as e:
        log(f"  aviso: limpeza dos preços falhou ({type(e).__name__}); segue")
        return
    if r.status_code == 404:
        log("  aviso: limpeza dos preços indisponível — rode o SQL 47 no Supabase. A coleta continua valendo.")
        apagar("cotacoes_us", f"data=lt.{(dt.date.today() - dt.timedelta(days=DIAS_PRECO_GUARDADOS)).isoformat()}")
    elif r.status_code >= 300:
        log(f"  aviso: limpeza dos preços recusada ({r.status_code} {r.text[:150]}); segue")
    else:
        log(f"Limpeza dos preços: {r.text.strip()}")


def apagar(nome, filtro):
    url, cab = supa()
    r = requests.delete(f"{url}/rest/v1/{nome}?{filtro}", headers=cab, timeout=120)
    if r.status_code >= 300:
        log(f"  aviso: limpeza de {nome} falhou ({r.status_code})")


# ---------------------------------------------------------------------
# v1.6: ETFs
# ---------------------------------------------------------------------
def coletar_lista_etfs(chave, max_paginas=8):
    """Lista de ETFs ativos (ticker, nome, bolsa) — Massive, ~5 consultas."""
    url, linhas, paginas = MASSIVE_ETFS.format(chave=chave), {}, 0
    while url and paginas < max_paginas:
        b = obter(url)
        paginas += 1
        if not b:
            break
        j = json.loads(b)
        for r in j.get("results") or []:
            t = normalizar_ticker(r.get("ticker"))
            if t:
                linhas[t] = {"ticker": t, "nome": (r.get("name") or "").strip() or None,
                             "bolsa": BOLSAS_ETF.get(r.get("primary_exchange") or "", r.get("primary_exchange"))}
        prox = j.get("next_url")
        url = (prox + ("&" if "?" in prox else "?") + "apiKey=" + chave) if (prox and not FONTE) else None
        if url:
            time.sleep(PAUSA_MASSIVE)
    log(f"Massive: lista de ETFs: {len(linhas)} em {paginas} consulta(s)")
    return list(linhas.values())


def etfs_do_banco():
    try:
        return {l["ticker"] for l in ler_tabela("etfs_us", "ticker")}
    except (RuntimeError, requests.RequestException):
        return None


def meses_faltando(hoje):
    """Meses completos (até 13) sem o fechamento de fim de mês do SPY em etfs_us_hist."""
    try:
        tem = {l["data"][:7] for l in ler_tabela("etfs_us_hist", "data", "&ticker=eq.SPY", ordem="data.asc")}
    except (RuntimeError, requests.RequestException):
        return []
    meses, a, m = [], hoje.year, hoje.month
    for _ in range(MESES_ETF):
        m -= 1
        if m == 0:
            a, m = a - 1, 12
        if f"{a}-{m:02d}" not in tem:
            meses.append((a, m))
    return meses


def fins_de_mes(chave, meses, etfs):
    """Fechamento do último pregão de cada mês (1 consulta por mês; tenta até 4 dias)."""
    saida = []
    for a, m in meses:
        fim = (dt.date(a + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        dias = [d for d in dias_uteis(fim, 4)][::-1]
        for d in dias:
            if not FONTE:
                time.sleep(PAUSA_MASSIVE)
            linhas = precos_do_dia(d, chave)
            if linhas:
                saida += [l for l in linhas if l["ticker"] in etfs]
                log(f"  ETFs — fim de {m:02d}/{a}: pregão de {d.isoformat()}")
                break
    return saida


def hist_etf(linhas, etfs):
    unicos = {}
    for l in linhas:
        if l["ticker"] in etfs:
            k = (l["ticker"], l["data"])
            if k not in unicos or (l.get("volume") or 0) > (unicos[k].get("volume") or 0):
                unicos[k] = l
    return [{"ticker": l["ticker"], "data": l["data"], "fechamento": l["fechamento"], "financeiro": l.get("financeiro")} for l in unicos.values()]


def processar_etfs(chave, novos_todos, semanal, hoje):
    """v1.6 — nunca derruba a coleta das ações."""
    try:
        etfs = None
        if semanal:
            lista = coletar_lista_etfs(chave)
            if len(lista) >= 100:
                gravar_opcional("etfs_us", lista, "ticker")
                etfs = {l["ticker"] for l in lista}
        if etfs is None:
            etfs = etfs_do_banco()
        if etfs is None:
            log("  aviso: ETFs não gravados (rode o SQL 55 no Supabase); a coleta das ações continua valendo.")
            return
        if not etfs:
            log("  ETFs: lista vazia — a primeira coleta semanal busca a lista.")
            return
        linhas = hist_etf(novos_todos, etfs)
        if semanal:
            meses = meses_faltando(hoje)
            if meses:
                log(f"ETFs: buscando o fechamento de fim de mês de {len(meses)} mês(es) (só uma vez)")
                linhas += hist_etf(fins_de_mes(chave, meses, etfs), etfs)
        log(f"ETFs: {len(etfs)} na lista; {len(linhas)} fechamento(s) novo(s)")
        gravar_opcional("etfs_us_hist", linhas, "ticker,data")
        url, cab = supa()
        r = requests.post(f"{url}/rest/v1/rpc/etfs_us_limpar", json={}, headers=cab, timeout=300)
        log(f"Limpeza dos ETFs: {r.text.strip()}" if r.status_code < 300 else f"  aviso: limpeza dos ETFs não feita ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: ETFs não atualizados ({str(e)[:200]}); a coleta das ações continua valendo.")


# ---------------------------------------------------------------------
# v1.7: BDRs (preços da brapi; paridade e ágio calculados no banco)
# ---------------------------------------------------------------------
def ultimo_pregao_br(hoje=None):
    """Pregão a que se referem os preços da brapi: antes das 18h de Brasília, o anterior."""
    if hoje is None or not FONTE:
        agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
        d = agora.date() - (dt.timedelta(days=1) if agora.hour < 18 else dt.timedelta(0))
    else:
        d = hoje
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def lista_bdrs():
    linhas, pag = [], 1
    while pag <= 6:
        if FONTE:
            p = os.path.join(FONTE, f"brapi_bdr_p{pag}.json")
            if not os.path.exists(p):
                break
            j = json.load(open(p))
        else:
            r = requests.get(BRAPI_BDR.format(pag=pag), timeout=60)
            r.raise_for_status()
            j = r.json()
        linhas += [x for x in (j.get("stocks") or []) if x.get("type") == "bdr" or x.get("subType") == "bdr"]
        if not j.get("hasNextPage"):
            break
        pag += 1
        if not FONTE:
            time.sleep(2)
    return linhas


def processar_bdrs(hoje):
    """v1.7 — nunca derruba a coleta das ações."""
    try:
        brutos = lista_bdrs()
        data = ultimo_pregao_br(hoje).isoformat()
        cad, hist = {}, {}
        for x in brutos:
            t = normalizar_ticker(x.get("stock"))
            preco = x.get("close")
            if not t or not preco or float(preco) <= 0:
                continue
            nome = (x.get("name") or "").strip() or None
            cad[t] = {"ticker": t, "nome": nome if nome and nome != t else None, "setor": x.get("sector"),
                      "mcap": x.get("market_cap") if (x.get("market_cap") or 0) > 0 else None}
            hist[t] = {"ticker": t, "data": data, "preco": float(preco), "variacao": x.get("change"),
                       "volume": x.get("volume"), "mcap": cad[t]["mcap"]}
        log(f"BDRs (brapi): {len(cad)} com preço em {data}")
        if not cad:
            return
        gravar_opcional("bdrs", list(cad.values()), "ticker")
        gravar_opcional("bdrs_hist", list(hist.values()), "ticker,data")
        url, cab = supa()
        lotes = ["", "C", "F", "J", "M", "P", "S", "V", "~"]          # v1.8: 8 lotes por letra inicial
        conf = tot = 0
        for de, ate in zip(lotes, lotes[1:]):
            r = requests.post(f"{url}/rest/v1/rpc/bdrs_calcular", json={"p_de": de, "p_ate": ate}, headers=cab, timeout=120)
            if r.status_code >= 300:
                log(f"  aviso: paridade e ágio dos BDRs ({de or 'início'}–{ate}) não feita ({r.status_code}{' — rode o SQL 58' if r.status_code == 404 else ''})")
                continue
            m = re.match(r'"?(\d+) BDR\(s\): (\d+) confirmado', r.text.strip())
            if m:
                tot += int(m.group(1)); conf += int(m.group(2))
        log(f"Paridade e ágio dos BDRs: {conf} de {tot} com empresa e paridade confirmadas")
        r = requests.post(f"{url}/rest/v1/rpc/bdrs_calcular_etf", json={}, headers=cab, timeout=120)   # v1.9
        log(f"BDRs de ETF: {r.text.strip()}" if r.status_code < 300 else f"  aviso: BDRs de ETF não calculados ({r.status_code}{' — rode o SQL 63' if r.status_code == 404 else ''})")
        r = requests.post(f"{url}/rest/v1/rpc/bdrs_limpar", json={}, headers=cab, timeout=120)
        log(f"Limpeza dos BDRs: {r.text.strip()}" if r.status_code < 300 else f"  aviso: limpeza dos BDRs não feita ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: BDRs não atualizados ({str(e)[:200]}); a coleta das ações continua valendo.")


# ---------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------
def montar_fundamentos(tickers, balancos, sics):
    linhas = []
    for t, info in tickers.items():
        b = balancos.get(info["cik"])
        if not b:
            continue
        sic = sics.get(info["cik"], "")
        linhas.append(dict(b, ticker=t, cik=info["cik"], empresa=info["empresa"], bolsa=info["bolsa"],
                           sic=sic or None, setor=setor_do_sic(sic)))
    return linhas


def limpar(linhas):
    for l in linhas:
        for k, v in list(l.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                l[k] = None
    return linhas


def main():
    global FONTE
    ap = argparse.ArgumentParser()
    ap.add_argument("--precos", action="store_true", help="só preços + indicadores (rotina diária)")
    ap.add_argument("--fonte", help="pasta com arquivos de teste (sem rede)")
    ap.add_argument("--saida", help="pasta para salvar o resultado em JSON (sem gravar no banco)")
    ap.add_argument("--hoje", help="data de referência AAAA-MM-DD (testes)")
    a = ap.parse_args()
    FONTE = a.fonte
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.date.today()
    chave = os.environ.get("MASSIVE_API_KEY", "teste" if a.fonte else "")
    if not chave:
        sys.exit("Defina MASSIVE_API_KEY (chave gratuita da Massive, nos Secrets do GitHub).")
    local = bool(a.saida)

    # 1. Balanços (semanal)
    if a.precos:
        fund = [] if local else ler_tabela("fundamentos_us", "*")
        log(f"Balanços já gravados: {len(fund)} ações")
    else:
        tickers = ler_tickers()
        balancos = ler_balancos(tickers)
        conhecidos = {} if local else {l["cik"]: l["sic"] for l in ler_tabela("fundamentos_us", "cik,sic") if l.get("sic")}
        sics = ler_setores(sorted(balancos), conhecidos)
        fund = limpar(montar_fundamentos(tickers, balancos, sics))
        for l in fund:
            l["financeira"] = eh_financeira(l.get("sic"))
            derivar_fundamentos(l)                   # v1.10: lucro bruto por diferença e EBIT de reserva
        log(f"{len(fund)} ações com balanço montado")
        if not local:
            fund = list({l["ticker"]: l for l in fund}.values())   # v1.2: um por ticker
            gravar("fundamentos_us", [{k: v for k, v in l.items() if k not in ("financeira", "historico", "entregas")} for l in fund], "ticker")
            hist = {}                                              # v1.4: histórico anual por empresa
            for cik, b in balancos.items():
                for h in b.get("historico") or []:
                    hist[(cik, h["ano"])] = {k: v for k, v in dict(h, cik=cik).items() if not k.startswith("_")}
            gravar_opcional("fundamentos_historico_us", list(hist.values()), "cik,ano")
            entregas = {}                                          # v1.5: datas de entrega (10-Q/10-K)
            for cik, b in balancos.items():
                for e in b.get("entregas") or []:
                    entregas[(cik, e["documento"], e["dt_refer"])] = dict(e, cik=cik)
            gravar_opcional("resultados_entregas_us", list(entregas.values()), "cik,documento,dt_refer")
    if local and a.precos and os.path.exists(os.path.join(a.saida, "fundamentos_us.json")):
        fund = json.load(open(os.path.join(a.saida, "fundamentos_us.json")))

    # 2. Preços (diário)
    ja = set() if local else {l["data"] for l in ler_tabela("cotacoes_us", "data", "&ticker=eq.AAPL", ordem="data.asc")}
    if not local and not a.precos:                         # v1.4: 1ª vez com o SQL 48 -> rebusca os pregões
        try:
            n_spx = len(ler_tabela("indices_hist", "data", "&indice=eq.SPX", ordem="data.asc"))
            if n_spx < 15:
                log(f"Índice S&P 500 com {n_spx} pregão(ões): rebuscando os últimos pregões (candles e SPY).")
                ja = set()
        except RuntimeError:
            log("  aviso: tabela de índices ausente (rode o SQL 48); segue sem o S&P 500.")
    novos = coletar_precos(chave, ja, hoje)
    novos_todos = novos                                  # v1.6: inclui os ETFs (antes descartados)
    indices = [{"indice": "SPX", "data": l["data"], "valor": l["fechamento"]} for l in novos if l["ticker"] == "SPY"]
    com_balanco = {l["ticker"] for l in fund}
    if com_balanco:                                    # v1.1: só ações com balanço
        antes = len(novos)
        novos = [l for l in novos if l["ticker"] in com_balanco]
        log(f"Massive: {len(novos)} fechamentos de ações com balanço (de {antes} recebidos)")
    if not local:
        gravar_cotacoes(novos)
        gravar_opcional("indices_hist", indices, "indice,data")
        limpar_precos()
        corte = (hoje - dt.timedelta(days=45)).isoformat()
        hist = ler_tabela("cotacoes_us", "ticker,data,fechamento,financeiro", f"&data=gte.{corte}", ordem="data.asc,ticker.asc")
        log(f"Fechamentos relidos do banco (últimos 45 dias): {len(hist)}")
    else:
        hist = novos
    precos = defaultdict(list)
    for l in sorted(hist, key=lambda x: x["data"]):
        precos[l["ticker"]].append((l["data"], float(l["fechamento"]), float(l.get("financeiro") or 0)))

    # 2b. Dividendos (só no modo semanal, só os novos) — v1.4
    if not a.precos and not local and com_balanco:
        ult = ultimo_valor("proventos_us", "data_com")
        if ult == "erro":
            log("  aviso: tabela de dividendos ausente (rode o SQL 48); segue sem dividendos.")
        else:
            desde = (dt.date.fromisoformat(ult) - dt.timedelta(days=7)).isoformat() if ult else (hoje - dt.timedelta(days=400)).isoformat()
            gravar_opcional("proventos_us", coletar_dividendos(chave, desde, com_balanco), "id")
    if local and not a.precos:
        divs = coletar_dividendos(chave, (hoje - dt.timedelta(days=400)).isoformat(), {l["ticker"] for l in fund})
        os.makedirs(a.saida, exist_ok=True)
        json.dump(divs, open(os.path.join(a.saida, "proventos_us.json"), "w"), ensure_ascii=False, indent=1)
        json.dump(indices, open(os.path.join(a.saida, "indices_hist.json"), "w"), ensure_ascii=False, indent=1)

    # 2c. ETFs — v1.6 (sem o SQL 55, só avisa)
    if not local:
        processar_etfs(chave, novos_todos, not a.precos, hoje)

    # 3. Indicadores
    for l in fund:
        if "financeira" not in l:
            l["financeira"] = eh_financeira(l.get("sic"))
    ind = limpar(calcular_indicadores(fund, precos))
    ind = list({i["ticker"]: i for i in ind}.values())        # v1.2: um por ticker
    log(f"Indicadores: {len(ind)} ações com balanço e preço")
    if local:
        os.makedirs(a.saida, exist_ok=True)
        for nome, dados in (("fundamentos_us", fund), ("cotacoes_us", novos), ("indicadores_us", ind)):
            if nome == "fundamentos_us" and a.precos:
                continue
            json.dump(dados, open(os.path.join(a.saida, nome + ".json"), "w"), ensure_ascii=False, indent=1)
        log("Arquivos salvos em", a.saida)
        return
    if ind:
        gravar("indicadores_us", ind, "ticker")
        tickers_ok = {i["ticker"] for i in ind}
        velhos = [l["ticker"] for l in ler_tabela("indicadores_us", "ticker") if l["ticker"] not in tickers_ok]
        for i in range(0, len(velhos), 200):          # saiu da bolsa / sem preço recente
            apagar("indicadores_us", "ticker=in.(" + ",".join(f'"{t}"' for t in velhos[i:i + 200]) + ")")
        if velhos:
            log(f"  {len(velhos)} ação(ões) sem balanço ou preço recente removida(s) dos indicadores")
    processar_bdrs(hoje)   # v1.7: depois dos indicadores (o cálculo usa preços e ações atualizados)


if __name__ == "__main__":
    main()
