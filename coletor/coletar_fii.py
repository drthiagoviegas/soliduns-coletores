"""
SOLIDUNS — Coletor de FUNDOS IMOBILIÁRIOS (FIIs) — v1.4 (05/10/2026)

v1.4 (ficha do FII, SQL 49): os informes mensais que já são lidos (dois
anos) passam a ser guardados mês a mês em public.fundos_fii_hist (DY do
mês, rentabilidade, valor patrimonial da cota, patrimônio, cotistas). Sem
o SQL 49, só avisa.

v1.3 (gráficos do relatório, SQL 48): a lista de fundos da brapi que já é
baixada traz o ETF BOVA11 (réplica do Ibovespa); o fechamento dele passa a
ser guardado em public.indices_hist (IBOV) — base do gráfico "desempenho
vs. Ibovespa". Sem o SQL 48, só avisa.

v1.1: a 1ª execução achou 761 FIIs, 4.115 rendimentos, mas 0 preços — a
lista diária de cotações da plataforma (cotacoes_eod) só traz AÇÕES. Agora
os preços dos fundos vêm da mesma lista pública da brapi, pedindo os
fundos (type=fund, paginada). Se ela falhar, a coleta segue sem preços.
Também: contagem certa dos motivos de falha no registro.

Fontes:
  - CVM (dados abertos, oficial): Informe Mensal Estruturado dos FIIs
    (inf_mensal_fii_AAAA.zip: arquivos "geral" e "complemento") — segmento,
    mandato, gestão, patrimônio, cotas, valor patrimonial da cota,
    cotistas, taxa de administração, DY e rentabilidade de cada mês.
  - Último fechamento já gravado pela plataforma (public.cotacoes_eod).
  - B3 (consulta pública de fundos, GetListedSupplementFunds): rendimentos
    (data-com, pagamento, valor por cota) -> public.proventos_b3 (SQL 44),
    os mesmos da Agenda de dividendos.

Grava (SQL 46): public.fundos_fii e public.proventos_b3.

Uso:
  python coletar_fii.py                         -> coleta e grava no Supabase
  python coletar_fii.py --fonte pasta/ --saida pasta/   -> testes (sem rede)
Variáveis: SUPABASE_URL e SUPABASE_SERVICE_KEY (as mesmas da Coleta CVM).
Reaproveita as funções do coletar_cvm.py (mesma pasta).
"""
import argparse, base64, datetime as dt, io, json, os, re, sys, time, zipfile
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coletar_cvm as cvm   # noqa: E402

URL_INF = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{ano}.zip"
URL_BRAPI_FUNDOS = "https://brapi.dev/api/quote/list?type=fund&limit=100&page={pagina}"
URL_B3_FUNDOS = ("https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/"
                 "GetListedSupplementFunds/{token}")
log = cvm.log
HIST = []          # v1.4: um registro por FII e mês


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if s in ("", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct(v):
    """O informe publica percentuais como fração (0,0085 = 0,85%)."""
    x = _num(v)
    if x is None:
        return None
    return round(x * 100, 4) if abs(x) < 0.5 else round(x, 4)


def ticker_fii(isin):
    isin = str(isin or "").strip().upper()
    if len(isin) == 12 and isin.startswith("BR") and isin[6:9] in ("CTF", "CDA"):
        return isin[2:6] + "11"
    return None


def ler_informes(fonte, hoje):
    geral, comp = [], []
    for ano in (hoje.year - 1, hoje.year):
        z = cvm.obter(URL_INF.format(ano=ano), fonte)
        if not z:
            continue
        g = cvm.ler_csv_zip(z, r"inf_mensal_fii_geral_\d{4}\.csv$")
        c = cvm.ler_csv_zip(z, r"inf_mensal_fii_complemento_\d{4}\.csv$")
        if g is not None and not g.empty:
            geral.append(g)
        if c is not None and not c.empty:
            comp.append(c)
        log(f"Informe mensal FII {ano}: lido")
    if not geral or not comp:
        raise RuntimeError("Não foi possível ler o Informe Mensal dos FIIs na CVM.")
    import pandas as pd
    return pd.concat(geral, ignore_index=True), pd.concat(comp, ignore_index=True)


def montar_fundos(geral, comp):
    """Uma linha por FII (o informe mais recente) + DY dos 12 últimos meses."""
    def colunas(df, **pedidos):
        out = {}
        for chave, trechos in pedidos.items():
            out[chave] = cvm.col(df, *trechos) if isinstance(trechos, tuple) else cvm.col(df, trechos)
        return out
    cg = colunas(geral, cnpj=("cnpj_fundo",), ref="data_referencia", ver="versao", nome=("nome_fundo",),
                 isin="codigo_isin", segmento="segmento_atuacao", mandato="mandato", gestao="tipo_gestao",
                 publico="publico_alvo", adm=("nome_administrador",), bolsa="mercado_negociacao_bolsa")
    cc = colunas(comp, cnpj=("cnpj_fundo",), ref="data_referencia", ver="versao", cotistas="total_numero_cotistas",
                 pl="patrimonio_liquido", cotas="cotas_emitidas", vp="valor_patrimonial_cotas",
                 taxa="percentual_despesas_taxa_administracao", dy="percentual_dividend_yield_mes",
                 rentab="percentual_rentabilidade_patrimonial_mes")
    for nome, c in (("geral", cg), ("complemento", cc)):
        faltam = [k for k in ("cnpj", "ref") if not c.get(k)]
        if faltam:
            raise RuntimeError(f"Informe FII ({nome}) sem as colunas {faltam}.")

    def ultima_versao(df, c):
        df = df.copy()
        df["_ref"] = df[c["ref"]].astype(str).str[:10]
        df["_ver"] = df[c["ver"]].apply(_num).fillna(1) if c.get("ver") else 1
        df = df.sort_values("_ver").drop_duplicates([c["cnpj"], "_ref"], keep="last")
        return df

    geral, comp = ultima_versao(geral, cg), ultima_versao(comp, cc)
    por_cnpj = defaultdict(list)
    for _, r in comp.iterrows():
        por_cnpj[r[cc["cnpj"]]].append(r)
    cad = {}
    for _, r in geral.sort_values("_ref").iterrows():          # o cadastro mais recente vence
        cad[r[cg["cnpj"]]] = r

    saida = []
    for cnpj, linhas in por_cnpj.items():
        g = cad.get(cnpj)
        if g is None:
            continue
        t = ticker_fii(g[cg["isin"]]) if cg.get("isin") else None
        if not t:
            continue
        if cg.get("bolsa") and str(g[cg["bolsa"]]).strip().upper() in ("N", "NAO", "NÃO", "FALSE"):
            continue
        linhas.sort(key=lambda r: r["_ref"])
        u = linhas[-1]
        for r in linhas:                               # v1.4: série mensal
            HIST.append({"ticker": t, "mes": r["_ref"],
                         "dy_mes": _pct(r[cc["dy"]]) if cc.get("dy") else None,
                         "rentab_patr_mes": _pct(r[cc["rentab"]]) if cc.get("rentab") else None,
                         "vp_cota": _num(r[cc["vp"]]) if cc.get("vp") else None,
                         "patrimonio_liq": _num(r[cc["pl"]]) if cc.get("pl") else None,
                         "cotistas": _num(r[cc["cotistas"]]) if cc.get("cotistas") else None})
        ref = dt.date.fromisoformat(u["_ref"])
        inicio = dt.date(ref.year - 1, ref.month, 1)
        dys = [_pct(r[cc["dy"]]) for r in linhas if cc.get("dy") and dt.date.fromisoformat(r["_ref"]) > inicio]
        dys = [x for x in dys if x is not None]
        txt = lambda c, k: (str(c[k]).strip() if k and str(c[k]).strip() not in ("", "nan") else None)
        saida.append({
            "ticker": t, "cnpj": cnpj, "nome": cvm.consertar_texto(txt(g, cg.get("nome")) or ""),
            "segmento": txt(g, cg.get("segmento")), "mandato": txt(g, cg.get("mandato")),
            "tipo_gestao": txt(g, cg.get("gestao")), "publico_alvo": txt(g, cg.get("publico")),
            "administrador": txt(g, cg.get("adm")), "dt_refer": ref.isoformat(),
            "patrimonio_liq": _num(u[cc["pl"]]) if cc.get("pl") else None,
            "cotas": _num(u[cc["cotas"]]) if cc.get("cotas") else None,
            "vp_cota": _num(u[cc["vp"]]) if cc.get("vp") else None,
            "cotistas": _num(u[cc["cotistas"]]) if cc.get("cotistas") else None,
            "taxa_adm_pct": _pct(u[cc["taxa"]]) if cc.get("taxa") else None,
            "dy_mes": _pct(u[cc["dy"]]) if cc.get("dy") else None,
            "dy_12m": round(sum(dys), 4) if dys else None,
            "rentab_patr_mes": _pct(u[cc["rentab"]]) if cc.get("rentab") else None,
        })
    unicos = {}
    for l in saida:                                   # dois CNPJs com o mesmo código: o mais recente
        if l["ticker"] not in unicos or l["dt_refer"] > unicos[l["ticker"]]["dt_refer"]:
            unicos[l["ticker"]] = l
    log(f"FIIs com código de negociação: {len(unicos)}")
    return list(unicos.values())


def ultimos_precos(fonte):
    """Último fechamento de cada ticker (cotacoes_eod, campo "todas")."""
    if fonte:
        cam = os.path.join(fonte, "cotacoes_eod.json")
        if not os.path.exists(cam):
            return {}, None
        linha = json.load(open(cam))
    else:
        url, cab = cvm.cabecalho_supabase()
        r = requests.get(f"{url}/rest/v1/cotacoes_eod?mercado=eq.br&select=capturado_em,todas&order=capturado_em.desc&limit=1",
                         headers=cab, timeout=60)
        if r.status_code >= 300 or not r.json():
            log(f"  aviso: não foi possível ler o último fechamento ({r.status_code}); FIIs sem preço")
            return {}, None
        linha = r.json()[0]
    precos = {}
    for c in linha.get("todas") or []:
        t = str(c.get("ticker") or c.get("stock") or "").upper()
        p = c.get("preco", c.get("close"))
        if t and p is not None:
            precos[t] = {"preco": float(p), "volume": c.get("volume") or c.get("volume_financeiro")}
    return precos, str(linha.get("capturado_em") or "")[:10] or None


def ultimo_pregao(hoje):
    d = hoje - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def precos_brapi_fundos(hoje, sessao=None):
    """v1.1: fechamento dos fundos (FIIs) na lista pública da brapi."""
    sessao = sessao or requests.Session()
    precos, pagina = {}, 1
    while pagina <= 40:
        try:
            r = sessao.get(URL_BRAPI_FUNDOS.format(pagina=pagina), timeout=60)
            if r.status_code != 200:
                log(f"  aviso: lista de fundos da brapi respondeu {r.status_code} (página {pagina})")
                break
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"  aviso: lista de fundos da brapi falhou ({type(e).__name__}) na página {pagina}")
            break
        itens = j.get("stocks") or []
        for c in itens:
            t = str(c.get("stock") or "").upper()
            p = c.get("close")
            if t and p is not None:
                vol = c.get("volume")
                precos[t] = {"preco": float(p),
                             "volume": round(float(vol) * float(p), 2) if vol not in (None, "") else None}
        if not itens or not j.get("hasNextPage", False):
            break
        pagina += 1
        time.sleep(0.5)
    return precos, ultimo_pregao(hoje)


def rendimentos_b3(fundos, pausa=0.4, sessao=None):
    """Rendimentos dos FIIs na consulta pública de fundos da B3."""
    if sessao is None:
        sessao = requests.Session()
        sessao.headers.update(dict(cvm.CABECALHOS_B3,
                                   Referer="https://sistemaswebb3-listados.b3.com.br/fundsListedPage/"))
    linhas, falhas, motivos = [], 0, {}
    for n, f in enumerate(fundos, 1):
        emissor = f["ticker"][:4]
        texto = None
        for corpo in ({"cnpj": re.sub(r"\D", "", f["cnpj"]), "identifierFund": emissor, "typeFund": 7},
                      {"cnpj": "0", "identifierFund": emissor, "typeFund": 7}):
            token = base64.b64encode(json.dumps(corpo, separators=(",", ":")).encode()).decode()
            try:
                r = sessao.get(URL_B3_FUNDOS.format(token=token), timeout=25)
                if r.status_code == 200 and r.text.strip():
                    texto = r.text
                    break
                chave = f"HTTP {r.status_code}" + (" (vazio)" if r.status_code == 200 else "")
                motivos[chave] = motivos.get(chave, 0) + 1
            except requests.RequestException as e:
                motivos[f"rede: {type(e).__name__}"] = motivos.get(f"rede: {type(e).__name__}", 0) + 1
        if texto is None:
            falhas += 1
        else:
            try:
                novas = cvm.ler_proventos_resposta(texto, emissor, {f["ticker"]})
                for l in novas:
                    l["ticker"] = l["ticker"] or f["ticker"]
                linhas.extend(novas)
            except (ValueError, AttributeError, TypeError) as e:
                falhas += 1
                motivos[f"resposta ilegível ({type(e).__name__}): {texto[:120]!r}"] = 1
        if n == 15 and falhas == 15:
            log("  rendimentos: as 15 primeiras consultas falharam — parando para não insistir.")
            break
        if n % 100 == 0:
            log(f"  rendimentos: {n}/{len(fundos)} FIIs consultados")
        time.sleep(pausa)
    unicos = {l["id"]: l for l in linhas}
    log(f"Rendimentos B3: {len(unicos)} registros de {min(n, len(fundos)) if fundos else 0} FIIs ({falhas} falha(s))")
    for m, q in sorted(motivos.items(), key=lambda x: -x[1]):
        log(f"  motivo: {m} — {q}x")
    return list(unicos.values())


def completar(fundos, precos, dt_preco, rendimentos, hoje):
    por_tk = defaultdict(list)
    for r in rendimentos:
        por_tk[r["ticker"]].append(r)
    corte = (hoje - dt.timedelta(days=365)).isoformat()
    for f in fundos:
        p = precos.get(f["ticker"])
        f["preco"] = p["preco"] if p else None
        f["dt_preco"] = dt_preco if p else None
        f["pvp"] = round(f["preco"] / f["vp_cota"], 4) if f["preco"] and f.get("vp_cota") else None
        r12 = sum(r["valor"] for r in por_tk.get(f["ticker"], []) if r.get("data_com") and r["data_com"] >= corte)
        f["rendimento_12m"] = round(r12, 6) if r12 else None
        f["dy_12m_preco"] = round(100 * r12 / f["preco"], 4) if r12 and f["preco"] else None
        vol = p.get("volume") if p else None
        f["liq_diaria"] = float(vol) if vol not in (None, "") else None
    return fundos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte"); ap.add_argument("--saida"); ap.add_argument("--hoje")
    ap.add_argument("--sem-rendimentos", action="store_true")
    a = ap.parse_args()
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.date.today()
    geral, comp = ler_informes(a.fonte, hoje)
    fundos = montar_fundos(geral, comp)
    precos, dt_preco = ultimos_precos(a.fonte)
    if sum(1 for f in fundos if f["ticker"] in precos) < len(fundos) / 2:     # v1.1
        extra, dt_extra = ({}, None) if a.fonte else precos_brapi_fundos(hoje)
        if a.fonte and os.path.exists(os.path.join(a.fonte, "brapi_fundos.json")):
            extra = {k: v for k, v in json.load(open(os.path.join(a.fonte, "brapi_fundos.json"))).items()}
            dt_extra = ultimo_pregao(hoje)
        if extra:
            log(f"Preços dos fundos (brapi): {len(extra)} recebidos")
            precos = dict(precos, **extra)
            dt_preco = dt_extra
    log(f"Preços: {sum(1 for f in fundos if f['ticker'] in precos)} de {len(fundos)} FIIs com fechamento ({dt_preco})")
    rend = [] if (a.sem_rendimentos or a.fonte) else rendimentos_b3(fundos)
    if a.fonte and os.path.exists(os.path.join(a.fonte, "rendimentos.json")):
        rend = json.load(open(os.path.join(a.fonte, "rendimentos.json")))
    fundos = completar(fundos, precos, dt_preco, rend, hoje)
    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
        json.dump(fundos, open(os.path.join(a.saida, "fundos_fii.json"), "w"), ensure_ascii=False, indent=1)
        json.dump(list({(h["ticker"], h["mes"]): h for h in HIST}.values()), open(os.path.join(a.saida, "fundos_fii_hist.json"), "w"), ensure_ascii=False, indent=1)
        log("Arquivo salvo em", a.saida)
        return
    cvm.gravar_tabela_opcional("fundos_fii", fundos, "ticker")
    validos = {f["ticker"] for f in fundos}                # v1.4: histórico mensal
    hist = list({(h["ticker"], h["mes"]): h for h in HIST if h["ticker"] in validos}.values())
    cvm.gravar_tabela_opcional("fundos_fii_hist", hist, "ticker,mes")
    if "BOVA11" in precos and dt_preco:                    # v1.3: Ibovespa (via BOVA11)
        cvm.gravar_tabela_opcional("indices_hist", [{"indice": "IBOV", "data": dt_preco,
                                                     "valor": precos["BOVA11"]["preco"]}], "indice,data")
    if rend:
        cvm.gravar_tabela_opcional("proventos_b3", rend, "id")


if __name__ == "__main__":
    main()
