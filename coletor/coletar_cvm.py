"""
SOLIDUNS — Coletor de demonstrações financeiras da CVM (Fase 1: Brasil)
v7 (03/10/2026): também grava o HISTÓRICO ANUAL (uma linha por empresa e
ano, das DFP dos últimos 7 anos) em public.fundamentos_historico — usado
pelo gráfico "Evolução anual" do relatório de empresas (SQL 43). Se a
tabela ainda não existir, só avisa e a coleta segue normalmente.
v7.1 (03/10/2026): empresa com mais de uma DFP no MESMO ano (mudou a data
de fechamento do exercício ou reapresentou com outra data) gerava duas
linhas iguais de empresa+ano e o banco recusava o lote inteiro; agora
fica uma linha por empresa e ano (a de data mais recente).
v8 (03/10/2026): AGENDAS (SQL 44).
  - Entregas de resultados: lê o índice dos próprios arquivos de ITR/DFP já
    baixados (data da 1ª entrega de cada documento à CVM) e grava em
    public.resultados_entregas — base da Agenda de resultados.
  - Proventos: consulta pública da B3 (GetListedSupplementCompany, uma
    chamada por empresa, mesma consulta que o site da B3 faz) e grava
    dividendos, JCP e rendimentos (data-com, pagamento, valor por ação) em
    public.proventos_b3 — base da Agenda de dividendos.
  - Modo --so-proventos: só os proventos (lista de ações tirada do próprio
    banco), para rodar todo dia útil sem baixar a CVM.
  Se o SQL 44 ainda não tiver sido executado, só avisa: a coleta segue.
v8.1 (04/10/2026): na 1ª execução as 320 consultas à B3 falharam sem
  dizer o motivo. Agora: cabeçalhos iguais aos da página da B3 (navegador),
  certificado incompleto da B3 tratado (repete sem verificação — dados
  públicos, só leitura), MOTIVO de cada tipo de falha no registro (código
  HTTP, erro de rede/certificado, trecho da resposta) e parada antecipada
  se as 15 primeiras consultas falharem.

O que faz, em ordem:
  1. Baixa do Portal de Dados Abertos da CVM:
       - DFP (demonstrações anuais) dos últimos 7 anos;
       - ITR (trimestrais) do ano atual e do anterior;
       - FCA do ano atual (liga o CNPJ ao código de negociação, ex. PETR4);
       - cadastro das companhias abertas (setor de atividade).
  2. Separa só as contas necessárias (BP, DRE e DFC; consolidado quando
     existir, senão individual) e a composição do capital.
  3. Monta, para cada empresa, o último balanço e os resultados dos
     últimos 12 meses (TTM), além do crescimento de 5 anos e do histórico
     anual (receita, lucros, dividendos e balanço de cada DFP).
  4. Grava uma linha por ação em public.fundamentos_br, o histórico em
     public.fundamentos_historico (Supabase) e pede ao banco para
     recalcular os indicadores (public.indicadores_atualizar).

Uso:
  python coletar_cvm.py                  -> coleta e grava no Supabase
  python coletar_cvm.py --saida x.json   -> coleta e só salva em arquivo
                                            (o histórico vai em x.historico.json)
  python coletar_cvm.py --fonte pasta/   -> lê os .zip de uma pasta local
                                            (em vez de baixar da CVM)
  python coletar_cvm.py --so-proventos   -> só os proventos da B3 (diário)
  python coletar_cvm.py --sem-proventos  -> tudo, menos os proventos

Variáveis de ambiente para gravar (guardadas nos Secrets do GitHub):
  SUPABASE_URL          ex.: https://evcsniicnrlrtzpdmeza.supabase.co
  SUPABASE_SERVICE_KEY  chave "service_role" (NUNCA vai para o site)
"""
import argparse, datetime as dt, io, json, math, os, re, sys, time, unicodedata, zipfile
from collections import defaultdict

import pandas as pd
import requests

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
URL_DFP = BASE + "/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
URL_ITR = BASE + "/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
URL_FCA = BASE + "/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip"
URL_CAD = BASE + "/CAD/DADOS/cad_cia_aberta.csv"
# v8: consulta pública da B3 (a mesma que o site da B3 usa na página da empresa)
URL_B3_PROVENTOS = ("https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/"
                    "CompanyCall/GetListedSupplementCompany/{token}")

# Diagnóstico: para estes códigos o registro da execução mostra as parcelas
# do lucro e dos dividendos (acumulado atual, mesmo período do ano anterior,
# ano anterior cheio) e as linhas da DRE/DFC usadas. Não altera o resultado.
DIAG_TICKERS = {t.strip().upper() for t in
                os.environ.get("COLETOR_DIAG", "GOAU3,EQTL3,SANB11,ABCB4,NEXP3,WIZC3").split(",") if t.strip()}

SETORES_FINANCEIROS = ("banco", "intermedia", "segurad", "previdenc", "arrendamento",
                       "credito imobiliario", "securitiza", "capitaliza")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# v6: nomes com acento quebrado no cadastro da CVM (ex.: NEXP3,
# "PARTICIPAÃ...ES"). Tenta desfazer a dupla codificação; se não der,
# usa outro nome disponível (razão social ou o nome das demonstrações).
def nome_quebrado(s):
    s = str(s or "")
    if re.search(r"[\x80-\x9f\ufffd]", s):
        return True
    # "Ã" legítimo vem antes de O, E, S, espaço, pontuação ou fim (SÃO, MÃE, IRMÃS)
    if re.search(r"[Ãã](?![OEoeSs\s\-.,/)]|$)", s):
        return True
    # "Â" seguido de símbolo é resto de UTF-8 lido como latin-1 (ex.: "Âº")
    return bool(re.search(r"Â[\xa0-\xbf]", s))


def consertar_texto(s):
    s = str(s or "").strip()
    for _ in range(2):
        if not nome_quebrado(s):
            break
        novo = None
        for cod in ("latin-1", "cp1252"):
            try:
                novo = s.encode(cod).decode("utf-8")
                break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        if not novo or novo == s:
            break
        s = novo
    return s


def escolher_nome(*candidatos):
    limpos = [consertar_texto(c) for c in candidatos if str(c or "").strip() not in ("", "nan")]
    for c in limpos:
        if not nome_quebrado(c):
            return c
    return limpos[0] if limpos else ""


def sem_acento(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------- download
def obter(url, fonte_local=None):
    """Bytes do arquivo (da pasta local, se informada), ou None se não existir."""
    nome = url.rsplit("/", 1)[-1]
    if fonte_local:
        caminho = os.path.join(fonte_local, nome)
        return open(caminho, "rb").read() if os.path.exists(caminho) else None
    for tentativa in range(4):
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "SOLIDUNS coletor CVM"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            log(f"  falha ao baixar {nome} ({e}); nova tentativa em {10 * (tentativa + 1)}s")
            time.sleep(10 * (tentativa + 1))
    raise RuntimeError(f"Não foi possível baixar {url}")


def ler_csv_zip(conteudo_zip, padrao):
    """Lê o primeiro CSV do zip cujo nome casa com o padrão (regex)."""
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as z:
        for nome in z.namelist():
            if re.search(padrao, nome, re.I):
                with z.open(nome) as f:
                    return pd.read_csv(f, sep=";", encoding="latin-1", dtype=str, low_memory=False,
                                       keep_default_na=False)
    return None


def col(df, *trechos):
    """Nome da coluna que contém todos os trechos (sem distinguir maiúsculas)."""
    for c in df.columns:
        n = sem_acento(c)
        if all(t in n for t in trechos):
            return c
    return None


# ---------------------------------------------------- demonstrações (DFP/ITR)
DEMOS = {"BPA": r"_BPA_(con|ind)_", "BPP": r"_BPP_(con|ind)_", "DRE": r"_DRE_(con|ind)_",
         "DFC_MI": r"_DFC_MI_(con|ind)_", "DFC_MD": r"_DFC_MD_(con|ind)_"}


def ler_demonstracoes(conteudo_zip, documento):
    """DataFrame único com as linhas úteis de todas as demonstrações do zip."""
    partes = []
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as z:
        nomes = z.namelist()
    for demo, padrao in DEMOS.items():
        for tipo in ("con", "ind"):
            pad = padrao.replace("(con|ind)", tipo)
            df = ler_csv_zip(conteudo_zip, pad + r"\d{4}\.csv$")
            if df is None or df.empty:
                continue
            df = df.rename(columns=str.upper)
            cd = df["CD_CONTA"].astype(str)
            # Só as contas necessárias: níveis 1 a 3 de BP e DRE; 6.01 e 6.03.* no DFC.
            nivel = cd.str.count(r"\.") + 1
            if demo.startswith("DFC"):
                manter = cd.eq("6.01") | cd.str.startswith("6.03")
            else:
                manter = nivel <= 3
            df = df[manter].copy()
            df["DEMO"] = "DFC" if demo.startswith("DFC") else demo
            df["TIPO"] = tipo
            df["DOCUMENTO"] = documento
            partes.append(df)
    if not partes:
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True)
    escala = df.get("ESCALA_MOEDA", pd.Series(["UNIDADE"] * len(df))).fillna("UNIDADE").str.upper()
    df["VALOR"] = pd.to_numeric(df["VL_CONTA"].str.replace(",", ".", regex=False), errors="coerce") \
        * escala.map(lambda e: 1000.0 if e.startswith("MIL") else 1.0)
    df["ORDEM"] = df["ORDEM_EXERC"].map(sem_acento)
    df["DS"] = df["DS_CONTA"].map(sem_acento)
    df["VERSAO"] = pd.to_numeric(df["VERSAO"], errors="coerce").fillna(1)
    return df


def ler_entregas(conteudo_zip, documento):
    """v8: data da 1ª entrega de cada ITR/DFP (índice do próprio zip da CVM:
    itr_cia_aberta_AAAA.csv / dfp_cia_aberta_AAAA.csv, coluna DT_RECEB)."""
    pad = r"(?:^|/)" + documento.lower() + r"_cia_aberta_\d{4}\.csv$"
    df = ler_csv_zip(conteudo_zip, pad)
    if df is None or df.empty:
        return []
    c_cnpj, c_ref, c_rec = col(df, "cnpj_cia"), col(df, "dt_refer"), col(df, "dt_receb")
    if not (c_cnpj and c_ref and c_rec):
        log(f"  aviso: índice {documento} sem as colunas esperadas: {list(df.columns)[:10]}")
        return []
    df = df[[c_cnpj, c_ref, c_rec]].dropna()
    df.columns = ["cnpj", "dt_refer", "dt_receb"]
    df["dt_refer"] = pd.to_datetime(df["dt_refer"], errors="coerce").dt.date
    df["dt_receb"] = pd.to_datetime(df["dt_receb"], errors="coerce").dt.date
    df = df.dropna()
    df = df.groupby(["cnpj", "dt_refer"], as_index=False)["dt_receb"].min()   # 1ª entrega
    return [{"cnpj": r.cnpj, "documento": documento, "dt_refer": r.dt_refer.isoformat(),
             "dt_receb": r.dt_receb.isoformat()} for r in df.itertuples()]


def ultima_versao(df, chaves):
    mx = df.groupby(chaves)["VERSAO"].transform("max")
    return df[df["VERSAO"] == mx]


# ------------------------------------------------ extração das contas-chave
def conta_por_codigo(linhas, codigo, ds_contem=None):
    s = linhas[linhas["CD_CONTA"] == codigo]
    if ds_contem:
        s = s[s["DS"].str.contains(ds_contem, regex=False)]
    return None if s.empty else float(s["VALOR"].iloc[0])


def conta_por_descricao(linhas, prefixo, nivel, contem, ultimo=False):
    s = linhas[linhas["CD_CONTA"].str.startswith(prefixo)
               & (linhas["CD_CONTA"].str.count(r"\.") + 1 == nivel)
               & linhas["DS"].str.contains(contem, regex=True)]
    if s.empty:
        return None
    return float(s["VALOR"].iloc[-1] if ultimo else s["VALOR"].iloc[0])


def extrair_balanco(bp):
    """bp: linhas BPA+BPP de um documento (já filtradas em ÚLTIMO)."""
    at = conta_por_codigo(bp, "1")
    ac = conta_por_descricao(bp, "1.", 2, r"^ativo circulante")
    pc = conta_por_descricao(bp, "2.", 2, r"^passivo circulante")
    pl = conta_por_descricao(bp, "2.", 2, r"patrimonio liquido")
    cx = sum(v for v in [conta_por_descricao(bp, "1.01.", 3, r"^caixa e equivalentes"),
                         conta_por_descricao(bp, "1.01.", 3, r"^aplicacoes financeiras")] if v)
    div = sum(v for v in [conta_por_descricao(bp, "2.01.", 3, r"^emprestimos e financiamentos"),
                          conta_por_descricao(bp, "2.02.", 3, r"^emprestimos e financiamentos")] if v)
    return {"ativo_total": at, "ativo_circ": ac, "passivo_circ": pc,
            "patrimonio_liq": pl, "caixa": cx, "divida_bruta": div}


# v5: aceita as abreviações "juros s/ o capital próprio", "JSCP" e "JCP"
# (o Banco ABC lança assim na DFP 2025 e ficava sem dividendos).
PADRAO_DIVIDENDOS = r"dividend|juros\s*s(?:obre|/)\s*(?:o\s*)?capital|\bj\.?s?\.?c\.?p\b"


def extrair_dividendos(dfc):
    """Dividendos/JCP PAGOS no financiamento (6.03): (valor, linhas usadas)."""
    # Linhas de "recebidos" ficam de fora: com o valor absoluto, entravam
    # somadas como se fossem pagamentos.
    # v3: também ficam de fora os pagos a NÃO CONTROLADORES (minoritários das
    # controladas), quando a companhia separa essa linha.
    div = dfc[dfc["CD_CONTA"].str.startswith("6.03")
              & dfc["DS"].str.contains(PADRAO_DIVIDENDOS, regex=True)
              & ~dfc["DS"].str.contains("recebid", regex=False)
              & ~dfc["DS"].str.contains(r"nao[ -]?controlador|minoritari", regex=True)]
    valor = float(div["VALOR"].abs().sum()) if not div.empty else 0.0
    det = [(c, d[:60], v) for c, d, v in zip(div["CD_CONTA"], div["DS"], div["VALOR"])]
    return valor, det


def extrair_resultado(dre, dfc):
    """Contas de resultado de um período (DRE + DFC)."""
    r = {
        "receita": conta_por_codigo(dre, "3.01"),
        "lucro_bruto": conta_por_codigo(dre, "3.03"),
        "ebit": conta_por_codigo(dre, "3.05", "antes do resultado financeiro"),
        "lair": conta_por_descricao(dre, "3.", 2, r"antes dos tributos sobre o lucro"),
        "ir": conta_por_descricao(dre, "3.", 2, r"imposto de renda e contribuicao social"),
    }
    # Lucro líquido: última linha de nível 2 da DRE que é o lucro do período;
    # se houver a abertura "atribuído aos controladores", usa ela.
    ll = conta_por_descricao(dre, "3.", 2, r"lucro.*(?:periodo|exercicio)", ultimo=True)
    s = dre[dre["DS"].str.contains("controlador", regex=False)
            & ~dre["DS"].str.contains("nao controlador", regex=False)
            & (dre["CD_CONTA"].str.count(r"\.") + 1 == 3)]
    # Algumas companhias entregam a abertura "controladores" zerada e só
    # preenchem o total; nesse caso vale o lucro do período (nível 2).
    if not s.empty and float(s["VALOR"].iloc[0]) != 0:
        ll = float(s["VALOR"].iloc[0])
    r["lucro_liq"] = ll
    r["dividendos"], r["_det_div"] = extrair_dividendos(dfc)
    r["_fonte_div"] = "consolidado"
    f = dfc[dfc["CD_CONTA"].str.startswith("6.03")]
    r["_det_603"] = [(c, d[:70], v) for c, d, v in zip(f["CD_CONTA"], f["DS"], f["VALOR"])]
    lin = dre[dre["DS"].str.contains(r"lucro|prejuizo|controlador", regex=True)
              & (dre["CD_CONTA"].str.count(r"\.") + 1 <= 3)]
    r["_det_lucro"] = [(c, d[:60], v) for c, d, v in zip(lin["CD_CONTA"], lin["DS"], lin["VALOR"])]
    return r


def combinar_ttm(ytd, ytd_ant, anual_ant):
    """TTM = acumulado do ano + ano anterior cheio − mesmo período do ano anterior."""
    out = {}
    for k in ytd:
        if k.startswith("_"):
            continue
        a, b, c = ytd.get(k), (ytd_ant or {}).get(k), (anual_ant or {}).get(k)
        if a is None:
            out[k] = None
        elif b is None or c is None:
            out[k] = None if k != "dividendos" else a
        else:
            out[k] = a + c - b
    # Dividendos pagos não podem ser negativos. Quando a conta dá negativo, o
    # DFC anual e o trimestral da companhia não são comparáveis (caso visto em
    # bancos). Usa-se então o ano anterior cheio (DFP auditada) como estimativa
    # dos 12 meses; sem ele, o acumulado do ano.
    # Se a DFP do ano anterior não traz a linha de dividendos (zero) mas o
    # mesmo período do ano anterior traz pagamentos, o ano cheio está
    # incompleto: sem base confiável, o TTM fica vazio (caso Santander 2025).
    b = (ytd_ant or {}).get("dividendos")
    c = (anual_ant or {}).get("dividendos")
    if "dividendos" in out and anual_ant is not None and not c and b and b > 0:
        out["dividendos"] = None
    d = out.get("dividendos")
    if d is not None and d < 0:
        out["dividendos"] = c if c and c > 0 else None
    return out


def periodos(linhas_doc):
    """Separa as linhas de fluxo de um documento em (acumulado atual, acumulado anterior)."""
    fl = linhas_doc[linhas_doc["DEMO"].isin(["DRE", "DFC"])].copy()
    if fl.empty:
        return None, None
    fl["DT_INI"] = pd.to_datetime(fl["DT_INI_EXERC"], errors="coerce")
    res = []
    for ordem in ("ultimo", "penultimo"):
        s = fl[fl["ORDEM"] == ordem]
        if s.empty:
            res.append(None)
            continue
        ini = s["DT_INI"].min()                       # início mais antigo = acumulado do ano
        s = s[s["DT_INI"] == ini]
        res.append(extrair_resultado(s[s["DEMO"] == "DRE"], s[s["DEMO"] == "DFC"]))
    return res[0], res[1]


def dividendos_individual(ind, ref, documento):
    """v4: dividendos do DFC INDIVIDUAL (só a controladora) de um documento:
    (acumulado atual, acumulado anterior), cada um (valor, linhas) ou None.
    O DFC consolidado soma o que as CONTROLADAS pagaram aos sócios
    minoritários delas (ex.: Metalúrgica Gerdau com a Gerdau S.A.); o
    individual mostra só o que a companhia pagou aos PRÓPRIOS acionistas."""
    if ind is None or ind.empty:
        return None, None
    s0 = ind[(ind["REF"] == ref) & (ind["DOCUMENTO"] == documento)].copy()
    if s0.empty:
        return None, None
    s0["DT_INI"] = pd.to_datetime(s0["DT_INI_EXERC"], errors="coerce")
    res = []
    for ordem in ("ultimo", "penultimo"):
        s = s0[s0["ORDEM"] == ordem]
        if s.empty:
            res.append(None)
            continue
        s = s[s["DT_INI"] == s["DT_INI"].min()]
        res.append(extrair_dividendos(s))
    return res[0], res[1]


def usar_individual(pares):
    """pares: [(período consolidado, dividendos individuais)]. Troca os
    dividendos pelos do individual só se ele existir para TODOS os períodos
    usados (sem misturar fontes numa mesma conta de 12 meses)."""
    usados = [(p, i) for p, i in pares if p is not None]
    if not usados or any(i is None for _, i in usados):
        return False
    for p, (valor, det) in usados:
        p["_div_con"] = p["dividendos"]
        p["dividendos"], p["_det_div"], p["_fonte_div"] = valor, det, "individual"
    return True


# -------------------------------------------------- composição do capital
def ler_capital(conteudo_zip):
    """Ações em circulação (integralizadas menos tesouraria) por CNPJ.
    Os nomes das colunas variam entre anos (ex.: QT_ACAO_ORDIN_... ou
    QT_ACAO_ORDINARIA_...); por isso a busca é por trechos do nome."""
    df = ler_csv_zip(conteudo_zip, r"composicao_capital_\d{4}\.csv$")
    if df is None or df.empty:
        return {}
    c_cnpj, c_ref, c_ver = col(df, "cnpj"), col(df, "dt_refer"), col(df, "versao")
    c_on, c_pn = col(df, "ordin", "integr"), col(df, "pref", "integr")
    c_tot = col(df, "total", "integr")
    c_on_t, c_pn_t, c_tot_t = col(df, "ordin", "tesour"), col(df, "pref", "tesour"), col(df, "total", "tesour")
    if not c_cnpj or not c_ref or not (c_on or c_tot):
        log("  aviso: composição do capital com colunas inesperadas, ignorada:", list(df.columns))
        return {}

    def num(c):
        if not c:
            return pd.Series(0.0, index=df.index)
        return pd.to_numeric(df[c].astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0.0)

    if c_on:
        on = (num(c_on) - num(c_on_t)).clip(lower=0)
        pn = (num(c_pn) - num(c_pn_t)).clip(lower=0)
    else:                                   # só o total: trata tudo como uma classe
        on = (num(c_tot) - num(c_tot_t)).clip(lower=0)
        pn = pd.Series(0.0, index=df.index)
    tab = pd.DataFrame({"cnpj": df[c_cnpj], "ref": pd.to_datetime(df[c_ref], errors="coerce"),
                        "ver": num(c_ver) if c_ver else 1.0, "on": on, "pn": pn})
    tab = tab.sort_values(["ref", "ver"])
    out = {}
    for r in tab.itertuples(index=False):   # a data mais recente prevalece
        out[r.cnpj] = (r.ref, float(r.on), float(r.pn))
    return out


# ---------------------------------------------------------- CNPJ -> tickers
def ler_tickers(conteudo_zip):
    df = ler_csv_zip(conteudo_zip, r"valor_mobiliario_\d{4}\.csv$")
    if df is None or df.empty:
        return {}
    c_cnpj, c_cod = col(df, "cnpj"), col(df, "codigo_negociacao")
    c_merc, c_fim = col(df, "mercado"), col(df, "data_fim_negociacao")
    if not c_cnpj or not c_cod:
        log("  aviso: FCA com colunas inesperadas:", list(df.columns))
        return {}
    mapa = defaultdict(set)
    for _, r in df.iterrows():
        cod = str(r[c_cod] or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{4}(3|4|5|6|7|8|11)", cod):
            continue
        if c_merc and "bolsa" not in sem_acento(r[c_merc] or ""):
            continue
        if c_fim and str(r[c_fim] or "").strip() not in ("", "nan"):
            continue
        mapa[r[c_cnpj]].add(cod)
    return mapa


def classe_do_ticker(t):
    return "UNT" if t.endswith("11") else ("ON" if t.endswith("3") else "PN")


def ler_cadastro(conteudo):
    """Setor e nome por CNPJ. O cadastro tem uma linha por registro da
    companhia (inclusive registros cancelados); o registro ATIVO prevalece."""
    df = pd.read_csv(io.BytesIO(conteudo), sep=";", encoding="latin-1", dtype=str, keep_default_na=False)
    c_cnpj, c_setor = col(df, "cnpj"), col(df, "setor")
    if not c_cnpj:
        log("  aviso: cadastro com colunas inesperadas:", list(df.columns))
        return {}
    c_nome = col(df, "denom_comerc") or col(df, "denom_social")
    c_social = col(df, "denom_social")
    c_sit = col(df, "sit")
    if c_sit:
        df = df.assign(_ativo=df[c_sit].map(lambda v: sem_acento(v).startswith("ativo")))
        df = df.sort_values("_ativo")          # ativos por último: sobrescrevem os demais
    txt = lambda v: str(v).strip() if v is not None else ""
    out = {}
    for _, r in df.iterrows():
        out[r[c_cnpj]] = {"setor": txt(r[c_setor]) if c_setor else "",
                          "nome": txt(r[c_nome]) if c_nome else "",
                          "social": txt(r[c_social]) if c_social else ""}
    return out


# ------------------------------------------------------------------ núcleo
def cagr(v_fim, v_ini, anos=5):
    if not v_fim or not v_ini or v_fim <= 0 or v_ini <= 0:
        return None
    return round(((v_fim / v_ini) ** (1 / anos) - 1) * 100, 2)


def montar(fonte=None, hoje=None, historico=None, entregas=None, tickers_saida=None):
    """Lista de linhas para fundamentos_br. Se `historico` for uma lista,
    recebe também as linhas anuais (v7) para fundamentos_historico; se
    `entregas` for uma lista, recebe as datas de entrega (v8)."""
    hoje = hoje or dt.date.today()
    ano = hoje.year
    docs, capital = [], {}
    for a in range(ano - 7, ano + 1):
        z = obter(URL_DFP.format(ano=a), fonte)
        if z:
            log(f"DFP {a}: lido")
            docs.append(ler_demonstracoes(z, "DFP"))
            capital.update(ler_capital(z))
            if entregas is not None and a >= ano - 2:
                entregas.extend(ler_entregas(z, "DFP"))
    for a in (ano - 1, ano):
        z = obter(URL_ITR.format(ano=a), fonte)
        if z:
            log(f"ITR {a}: lido")
            docs.append(ler_demonstracoes(z, "ITR"))
            if entregas is not None:
                entregas.extend(ler_entregas(z, "ITR"))
            for k, v in ler_capital(z).items():
                if k not in capital or v[0] >= capital[k][0]:
                    capital[k] = v
    docs = [d for d in docs if not d.empty]
    if not docs:
        raise RuntimeError("Nenhuma demonstração encontrada.")
    df = pd.concat(docs, ignore_index=True)
    df["REF"] = pd.to_datetime(df["DT_REFER"], errors="coerce")
    df = ultima_versao(df, ["CNPJ_CIA", "DT_REFER", "DOCUMENTO", "DEMO"])
    # v4: o DFC individual é guardado à parte (dividendos da controladora).
    dfc_ind = df[(df["DEMO"] == "DFC") & (df["TIPO"] == "ind")]
    ind_por_cnpj = {k: v for k, v in dfc_ind.groupby("CNPJ_CIA")}
    # Consolidado quando existir; senão, individual.
    tem_con = df[df["TIPO"] == "con"].groupby(["CNPJ_CIA", "DT_REFER", "DOCUMENTO"]).size()
    chave = list(zip(df["CNPJ_CIA"], df["DT_REFER"], df["DOCUMENTO"]))
    usa_con = pd.Series([k in tem_con.index for k in chave], index=df.index)
    df = df[(df["TIPO"] == "con") == usa_con]

    tickers = {}
    for a in (ano, ano - 1):
        z = obter(URL_FCA.format(ano=a), fonte)
        if z:
            tickers = ler_tickers(z)
            if tickers:
                log(f"FCA {a}: {len(tickers)} empresas com código de negociação")
                break
    if not tickers:
        raise RuntimeError("Não foi possível ligar CNPJ a códigos de negociação (arquivo FCA).")
    if entregas is not None:          # v8: só empresas com ação negociada
        filtradas = [e for e in entregas if e["cnpj"] in tickers]
        entregas[:] = filtradas
    if tickers_saida is not None:
        tickers_saida.update(tickers)
    cad = {}
    c = obter(URL_CAD, fonte)
    if c:
        cad = ler_cadastro(c)

    saida, falhas = [], []
    for cnpj, g in df.groupby("CNPJ_CIA"):
        if cnpj not in tickers:
            continue
        try:
            saida.extend(montar_empresa(cnpj, g, tickers, cad, capital, ind_por_cnpj.get(cnpj), historico))
        except Exception as e:                       # registra e segue com as demais
            falhas.append((cnpj, f"{type(e).__name__}: {e}"))
    if falhas:
        log(f"  aviso: {len(falhas)} empresa(s) ignorada(s) por dados inesperados; exemplos:")
        for cnpj, erro in falhas[:10]:
            log(f"    {cnpj}: {erro}")
    if historico is not None:
        limpar_tipos(historico)
    return limpar_tipos(saida)


def historico_anual(cnpj, g, ind=None):
    """v7: uma linha por ano de DFP da empresa (resultado do ano e balanço
    do fim do ano). Mesmas regras do TTM: consolidado quando existir e
    dividendos da controladora (DFC individual) quando houver.
    v7.1: se houver mais de uma DFP no mesmo ano, vale a de data mais
    recente (as datas vêm em ordem crescente e a última sobrescreve)."""
    por_ano = {}
    for ref, gd in g[g["DOCUMENTO"] == "DFP"].groupby("REF"):
        r = periodos(gd)[0]
        if not r:
            continue
        i_ano = dividendos_individual(ind, ref, "DFP")[0]
        usar_individual([(r, i_ano)])
        bp = gd[gd["DEMO"].isin(["BPA", "BPP"]) & (gd["ORDEM"] == "ultimo")]
        bal = extrair_balanco(bp) if not bp.empty else {}
        por_ano[int(ref.year)] = {
            "cnpj": cnpj, "ano": int(ref.year), "dt_refer": ref.date().isoformat(),
            "receita": r.get("receita"), "lucro_bruto": r.get("lucro_bruto"), "ebit": r.get("ebit"),
            "lucro_liq": r.get("lucro_liq"), "dividendos": r.get("dividendos"),
            "ativo_total": bal.get("ativo_total"), "patrimonio_liq": bal.get("patrimonio_liq"),
            "caixa": bal.get("caixa"), "divida_bruta": bal.get("divida_bruta"),
        }
    return [por_ano[a] for a in sorted(por_ano)]


def montar_empresa(cnpj, g, tickers, cad, capital, ind=None, historico=None):
    saida = []
    if True:
        ult_ref = g["REF"].max()
        doc_ult = g[g["REF"] == ult_ref]
        documento = "DFP" if (doc_ult["DOCUMENTO"] == "DFP").any() else "ITR"
        doc_ult = doc_ult[doc_ult["DOCUMENTO"] == documento]

        bp = doc_ult[doc_ult["DEMO"].isin(["BPA", "BPP"]) & (doc_ult["ORDEM"] == "ultimo")]
        bal = extrair_balanco(bp)
        if not bal["ativo_total"] or bal["patrimonio_liq"] is None:
            return []

        ytd, ytd_ant = periodos(doc_ult)
        if ytd is None:
            return []
        i_ytd, i_ant = dividendos_individual(ind, ult_ref, documento)
        if documento == "DFP":
            usar_individual([(ytd, i_ytd)])
            ttm = ytd
        else:
            dfp_ant = g[(g["DOCUMENTO"] == "DFP") & (g["REF"].dt.year == ult_ref.year - 1)]
            anual_ant = periodos(dfp_ant)[0] if not dfp_ant.empty else None
            i_anual = (dividendos_individual(ind, dfp_ant["REF"].max(), "DFP")[0]
                       if not dfp_ant.empty else None)
            usar_individual([(ytd, i_ytd), (ytd_ant, i_ant), (anual_ant, i_anual)])
            ttm = combinar_ttm(ytd, ytd_ant, anual_ant)
        if DIAG_TICKERS & set(tickers[cnpj]):
            log(f"  DIAG {'/'.join(sorted(tickers[cnpj]))} ({documento} {ult_ref.date()}):")
            partes = [("acumulado atual", ytd), ("mesmo periodo ano anterior", ytd_ant)]
            if documento != "DFP":
                partes.append(("ano anterior cheio (DFP)", anual_ant))
            for rot, p in partes:
                if not p:
                    log(f"    {rot}: (sem dados)")
                    continue
                log(f"    {rot}: lucro_liq={p.get('lucro_liq')} dividendos={p.get('dividendos')}"
                    f" (DFC {p.get('_fonte_div')}; consolidado={p.get('_div_con', p.get('dividendos'))})")
                for c_, d_, v_ in p.get("_det_lucro", []):
                    log(f"      DRE {c_} {d_} = {v_}")
                for c_, d_, v_ in p.get("_det_div", []):
                    log(f"      DFC {c_} {d_} = {v_}")
                if rot.startswith("ano anterior"):
                    for c_, d_, v_ in p.get("_det_603", []):
                        log(f"      DFC-6.03 {c_} {d_} = {v_}")
            log(f"    TTM: lucro_liq={ttm.get('lucro_liq')} dividendos={ttm.get('dividendos')}")

        # Crescimento 5 anos, a partir das DFP
        anuais = {}
        for ref, gd in g[g["DOCUMENTO"] == "DFP"].groupby("REF"):
            r = periodos(gd)[0]
            if r:
                anuais[ref.year] = r
        cg_r = cg_l = None
        if anuais:
            ult_ano = max(anuais)
            if ult_ano - 5 in anuais:
                cg_r = cagr(anuais[ult_ano]["receita"], anuais[ult_ano - 5]["receita"])
                cg_l = cagr(anuais[ult_ano]["lucro_liq"], anuais[ult_ano - 5]["lucro_liq"])

        # v7: histórico anual (uma vez por empresa, não por ticker)
        if historico is not None:
            try:
                historico.extend(historico_anual(cnpj, g, ind))
            except Exception as e:                   # nunca derruba a empresa
                log(f"  aviso: histórico de {cnpj} ignorado ({type(e).__name__}: {e})")

        aliq = 0.34
        if ttm.get("lair") and ttm.get("ir") is not None and ttm["lair"] > 0:
            aliq = min(max(-ttm["ir"] / ttm["lair"], 0.0), 0.34)

        info = cad.get(cnpj, {})
        setor = info.get("setor") or ""
        financeira = any(t in sem_acento(setor) for t in SETORES_FINANCEIROS) or bal["ativo_circ"] is None
        _, acoes_on, acoes_pn = capital.get(cnpj, (None, None, None))
        nome = escolher_nome(info.get("nome"), info.get("social"), str(g["DENOM_CIA"].iloc[0]))
        if DIAG_TICKERS & set(tickers[cnpj]):
            log(f"    NOME: cadastro={info.get('nome')!r} social={info.get('social')!r}"
                f" demonstracoes={str(g['DENOM_CIA'].iloc[0])!r} -> gravado={nome!r}")

        for tk in sorted(tickers[cnpj]):
            saida.append({
                "ticker": tk, "cnpj": cnpj, "empresa": nome, "setor": setor or None,
                "financeira": bool(financeira), "classe": classe_do_ticker(tk),
                "dt_refer": ult_ref.date().isoformat(), "documento": documento,
                "acoes_on": acoes_on, "acoes_pn": acoes_pn,
                **bal,
                "receita_ttm": ttm.get("receita"), "lucro_bruto_ttm": ttm.get("lucro_bruto"),
                "ebit_ttm": ttm.get("ebit"), "lucro_liq_ttm": ttm.get("lucro_liq"),
                "aliquota_ir": round(aliq, 4), "dividendos_ttm": ttm.get("dividendos"),
                "cagr_receitas": cg_r, "cagr_lucros": cg_l,
            })
    return saida


# ---------------------------------------------------------------------
# v8 — PROVENTOS (consulta pública da B3)
# ---------------------------------------------------------------------
SUFIXO_POR_ESPECIE = {"OR": "3", "PR": "4", "PA": "5", "PB": "6", "PC": "7", "PD": "8"}


def ticker_do_isin(isin, conhecidos):
    """BRPETRACNPR6 -> PETR4 (ação PN); BRTAEECDAM12-> TAEE11 (unit).
    Só aceita se o código existir entre os tickers conhecidos do emissor."""
    isin = (isin or "").strip().upper()
    if len(isin) != 12 or not isin.startswith("BR"):
        return None
    emissor, tipo, esp = isin[2:6], isin[6:9], isin[9:11]
    cand = None
    if tipo == "ACN" and esp in SUFIXO_POR_ESPECIE:
        cand = emissor + SUFIXO_POR_ESPECIE[esp]
    elif tipo in ("UNT", "CDA"):
        cand = emissor + "11"
    if cand and (not conhecidos or cand in conhecidos):
        return cand
    return None


def data_br(v):
    v = str(v or "").strip()
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", v)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def numero_br(v):
    v = str(v or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def ler_proventos_resposta(texto, emissor, conhecidos):
    """Converte a resposta da B3 em linhas de public.proventos_b3."""
    texto = (texto or "").strip()
    if not texto:
        return []                                   # emissor que a B3 não reconhece
    dados = json.loads(texto)
    if isinstance(dados, list):
        dados = dados[0] if dados else {}
    lista = dados.get("cashDividends") or dados.get("CashDividends") or []
    linhas = []
    for d in lista:
        isin = (d.get("isinCode") or d.get("assetIssued") or "").strip().upper()
        tipo = re.sub(r"\s+", " ", str(d.get("label") or "").strip().upper())
        data_com = data_br(d.get("lastDatePrior"))
        aprov = data_br(d.get("approvedOn"))
        valor = numero_br(d.get("rate"))
        if not data_com or valor is None:
            continue
        ident = "|".join([isin or emissor, tipo, data_com, aprov or "", f"{valor:.10f}"])
        linhas.append({
            "id": ident, "emissor": emissor, "ticker": ticker_do_isin(isin, conhecidos),
            "isin": isin or None, "tipo": tipo or None, "aprovado_em": aprov, "data_com": data_com,
            "pagamento": data_br(d.get("paymentDate")), "valor": valor,
            "referente": (str(d.get("relatedTo") or "").strip() or None),
            "observacao": (str(d.get("remarks") or "").strip()[:300] or None),
        })
    return linhas


CABECALHOS_B3 = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://sistemaswebb3-listados.b3.com.br/listedCompaniesPage/",
    "Origin": "https://sistemaswebb3-listados.b3.com.br",
}


def coletar_proventos(tickers_por_emissor, pausa=0.4, sessao=None):
    """Uma consulta por empresa (4 letras do código). Devolve (linhas, falhas)."""
    import base64
    if sessao is None:
        sessao = requests.Session()
        sessao.headers.update(CABECALHOS_B3)
    linhas, falhas, vazios, consultas = [], 0, 0, 0
    motivos = {}                        # v8.1: motivo -> (quantas, exemplo)
    verificar_ssl = True

    def anotar(motivo, exemplo=""):
        q, ex = motivos.get(motivo, (0, exemplo))
        motivos[motivo] = (q + 1, ex or exemplo)

    for n, (emissor, conhecidos) in enumerate(sorted(tickers_por_emissor.items()), 1):
        token = base64.b64encode(json.dumps({"issuingCompany": emissor, "language": "pt-br"},
                                            separators=(",", ":")).encode()).decode()
        url = URL_B3_PROVENTOS.format(token=token)
        texto = None
        consultas += 1
        for tentativa in range(3):
            try:
                r = sessao.get(url, timeout=25, verify=verificar_ssl)
                if r.status_code == 200:
                    texto = r.text
                    break
                anotar(f"HTTP {r.status_code}", f"{emissor}: {r.text[:160]!r}")
                if r.status_code in (401, 403, 404):
                    break                               # não adianta repetir
            except requests.exceptions.SSLError as e:
                if verificar_ssl:
                    anotar("certificado da B3 recusado pelo Python (passa a consultar sem verificação)", str(e)[:160])
                    verificar_ssl = False
                    try:
                        import urllib3
                        urllib3.disable_warnings()
                    except Exception:
                        pass
                    continue
                anotar("erro de certificado", str(e)[:160])
            except requests.RequestException as e:
                anotar(f"rede: {type(e).__name__}", str(e)[:160])
            time.sleep(2 * (tentativa + 1))
        if texto is None:
            falhas += 1
        else:
            try:
                novas = ler_proventos_resposta(texto, emissor, conhecidos)
                if not novas:
                    vazios += 1
                linhas.extend(novas)
            except (ValueError, AttributeError, TypeError) as e:
                falhas += 1
                anotar(f"resposta ilegível ({type(e).__name__})", f"{emissor}: {texto[:160]!r}")
        if n == 15 and falhas == 15:
            log("  proventos: as 15 primeiras consultas falharam — parando para não insistir.")
            break
        if n % 50 == 0:
            log(f"  proventos: {n}/{len(tickers_por_emissor)} empresas consultadas")
        time.sleep(pausa)
    unicos = {l["id"]: l for l in linhas}
    log(f"Proventos B3: {len(unicos)} registros de {consultas} empresas consultadas "
        f"({vazios} sem proventos recentes, {falhas} falha(s) de consulta)")
    for motivo, (q, ex) in sorted(motivos.items(), key=lambda x: -x[1][0]):
        log(f"  motivo: {motivo} — {q}x; exemplo: {ex}")
    if not verificar_ssl:
        log("  obs.: consultas feitas sem verificar o certificado da B3 (dados públicos, só leitura).")
    return list(unicos.values()), falhas


def emissores_de(tickers):
    """{cnpj: {PETR3, PETR4}} -> {PETR: {PETR3, PETR4}}"""
    por = defaultdict(set)
    for cods in tickers.values():
        for t in cods:
            por[t[:4]].add(t)
    return por


def cabecalho_supabase():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url, cab


def gravar_tabela_opcional(nome, linhas, conflito, lote_tam=500):
    """Grava numa tabela do SQL 44. Falha aqui NÃO derruba a coleta."""
    if not linhas:
        log(f"  {nome}: nada para gravar")
        return 0
    url, cab = cabecalho_supabase()
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    gravados = 0
    try:
        for i in range(0, len(linhas), lote_tam):
            lote = [dict(l, coletado_em=agora) for l in linhas[i:i + lote_tam]]
            r = requests.post(f"{url}/rest/v1/{nome}?on_conflict={conflito}", json=lote,
                              headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
            if r.status_code >= 300:
                dica = " Rode o SQL 44 no Supabase." if r.status_code == 404 else ""
                log(f"  aviso: {nome} não gravado ({r.status_code} {r.text[:200]}).{dica} O resto da coleta continua valendo.")
                return gravados
            gravados += len(lote)
    except requests.RequestException as e:
        log(f"  aviso: {nome} não gravado ({e}); o resto da coleta continua valendo.")
        return gravados
    log(f"{nome}: {gravados} linha(s) gravada(s)")
    return gravados


def tickers_do_banco():
    """Modo --so-proventos: lista de ações já gravada em fundamentos_br."""
    url, cab = cabecalho_supabase()
    r = requests.get(f"{url}/rest/v1/fundamentos_br?select=ticker,cnpj", headers=cab, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Não foi possível ler a lista de ações: {r.status_code} {r.text[:200]}")
    por = defaultdict(set)
    for l in r.json():
        if l.get("ticker"):
            por[l.get("cnpj") or l["ticker"]].add(l["ticker"].upper())
    return por


def limpar_tipos(saida):
    # JSON só aceita tipos nativos, e não aceita NaN/infinito
    for linha in saida:
        for k, v in linha.items():
            if hasattr(v, "item"):          # números do numpy/pandas
                v = v.item()
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = None
            linha[k] = v
    return saida


def gravar_supabase(linhas, historico=None, entregas=None, proventos=None):
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    # Chaves no formato antigo (JWT "service_role") também vão no Authorization.
    # As chaves novas do Supabase ("sb_secret_...") não são JWT: só no apikey.
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(0, len(linhas), 200):
        lote = [dict(l, coletado_em=agora) for l in linhas[i:i + 200]]
        r = requests.post(f"{url}/rest/v1/fundamentos_br?on_conflict=ticker", json=lote,
                          headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase recusou a gravação: {r.status_code} {r.text[:300]}")
    # v7: histórico anual. Falha aqui NÃO derruba a coleta (ex.: SQL 43 ainda
    # não executado) — só avisa no registro.
    if historico:
        # v7.1: garantia final — uma linha por empresa e ano (vale a de data
        # mais recente), para o banco nunca recusar o lote por repetição.
        unicos = {}
        for l in historico:
            k = (l["cnpj"], l["ano"])
            if k not in unicos or str(l.get("dt_refer") or "") >= str(unicos[k].get("dt_refer") or ""):
                unicos[k] = l
        if len(unicos) < len(historico):
            log(f"  histórico anual: {len(historico) - len(unicos)} linha(s) repetida(s) de empresa+ano descartada(s)")
        historico = list(unicos.values())
        try:
            gravados = 0
            for i in range(0, len(historico), 500):
                lote = [dict(l, coletado_em=agora) for l in historico[i:i + 500]]
                r = requests.post(f"{url}/rest/v1/fundamentos_historico?on_conflict=cnpj,ano", json=lote,
                                  headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
                if r.status_code >= 300:
                    dica = (" Rode o SQL 43 no Supabase." if r.status_code == 404 else "")
                    log(f"  aviso: histórico anual não gravado ({r.status_code} {r.text[:200]})."
                        f"{dica} O resto da coleta continua valendo.")
                    break
                gravados += len(lote)
            else:
                log(f"Histórico anual: {gravados} linhas (empresa x ano) gravadas")
        except requests.RequestException as e:
            log(f"  aviso: histórico anual não gravado ({e}); o resto da coleta continua valendo.")
    # v8: agendas (SQL 44) — opcionais, nunca derrubam a coleta
    if entregas:
        unicas = {(e["cnpj"], e["documento"], e["dt_refer"]): e for e in entregas}
        gravar_tabela_opcional("resultados_entregas", list(unicas.values()), "cnpj,documento,dt_refer")
    if proventos:
        gravar_tabela_opcional("proventos_b3", proventos, "id")
    r = requests.post(f"{url}/rest/v1/rpc/indicadores_atualizar", json={}, headers=cab, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"Falha ao recalcular indicadores: {r.status_code} {r.text[:300]}")
    log("Supabase:", r.text.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", help="salva o resultado neste arquivo JSON em vez de gravar no Supabase")
    ap.add_argument("--fonte", help="pasta com os .zip/.csv da CVM já baixados")
    ap.add_argument("--hoje", help="data de referência AAAA-MM-DD (testes)")
    ap.add_argument("--so-proventos", action="store_true", help="só os proventos da B3 (rotina diária)")
    ap.add_argument("--sem-proventos", action="store_true", help="não consulta a B3")
    a = ap.parse_args()
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else None
    if a.so_proventos:                        # v8: rotina diária
        proventos, falhas = coletar_proventos(emissores_de(tickers_do_banco()))
        gravar_tabela_opcional("proventos_b3", proventos, "id")
        return
    historico, entregas, tickers = [], [], {}
    linhas = montar(a.fonte, hoje, historico, entregas, tickers)
    log(f"{len(linhas)} ações com fundamentos montados; {len(historico)} linhas de histórico anual; "
        f"{len(entregas)} entregas de ITR/DFP")
    if not linhas:
        sys.exit("Nada para gravar: verifique os arquivos da CVM.")
    proventos = []
    if not a.sem_proventos and not a.fonte:
        proventos, _ = coletar_proventos(emissores_de(tickers))
    if a.saida:
        json.dump(linhas, open(a.saida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        base, _ = os.path.splitext(a.saida)
        json.dump(historico, open(base + ".historico.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(entregas, open(base + ".entregas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(proventos, open(base + ".proventos.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log("Arquivos salvos:", a.saida, "(+ .historico, .entregas e .proventos)")
    else:
        gravar_supabase(linhas, historico, entregas, proventos)


if __name__ == "__main__":
    main()
