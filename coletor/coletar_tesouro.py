"""
SOLIDUNS — Coletor do TESOURO DIRETO — v1.0 (04/10/2026)

Fonte (oficial e gratuita): Tesouro Transparente — "Taxas dos Títulos
Ofertados pelo Tesouro Direto" (precotaxatesourodireto.csv, ~15 MB, todo
o histórico desde 2002; o Tesouro atualiza o arquivo todo dia).

Grava (SQL 50): public.tesouro_precos — taxa e preço unitário de compra e
de venda (resgate) de cada título e dia.
  - 1ª execução (tabela vazia): histórico inteiro dos títulos que ainda não
    venceram (~71 mil linhas).
  - Demais: só os últimos 10 dias do arquivo (cobre feriados, rotinas que
    não rodaram e correções do Tesouro).
  - No fim apaga os títulos já vencidos (não aparecem mais no site).

Uso:
  python coletar_tesouro.py                        -> coleta e grava no Supabase
  python coletar_tesouro.py --fonte arquivo.csv --saida pasta/   -> testes (sem rede)
Variáveis: SUPABASE_URL e SUPABASE_SERVICE_KEY (as mesmas das outras rotinas).
"""
import argparse, csv, datetime as dt, io, json, os, sys, time

import requests

URL_CSV = ("https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/"
           "resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv")
URL_PACOTE = ("https://www.tesourotransparente.gov.br/ckan/api/3/action/package_show"
              "?id=taxas-dos-titulos-ofertados-pelo-tesouro-direto")
JANELA_DIAS = 10


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def baixar(url):
    for tentativa in range(4):
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "SOLIDUNS coletor Tesouro"})
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log(f"  falha ao baixar ({e}); nova tentativa em {15 * (tentativa + 1)}s")
            time.sleep(15 * (tentativa + 1))
    return None


def obter_csv():
    """Bytes do CSV. Se o endereço fixo falhar, procura o atual no catálogo do Tesouro."""
    r = baixar(URL_CSV)
    if r is None or b"Tipo Titulo" not in r.content[:200]:
        log("  endereço fixo falhou; procurando o arquivo no catálogo do Tesouro Transparente")
        p = baixar(URL_PACOTE)
        url = None
        if p is not None:
            for rec in p.json().get("result", {}).get("resources", []):
                if str(rec.get("url", "")).lower().endswith(".csv"):
                    url = rec["url"]
        r = baixar(url) if url else None
    if r is None:
        raise RuntimeError("Não foi possível baixar o arquivo do Tesouro Transparente.")
    return r.content


def data_br(s):
    return dt.datetime.strptime(s.strip(), "%d/%m/%Y").date()


def num(s):
    s = (s or "").strip().replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def ler(conteudo):
    try:
        texto = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    cab = [c.strip().lower() for c in next(leitor)]

    def pos(*trechos):
        for i, c in enumerate(cab):
            if all(t in c for t in trechos):
                return i
        raise RuntimeError(f"Coluna não encontrada: {trechos} (cabeçalho: {cab})")
    i_tipo, i_venc, i_data = pos("tipo"), pos("vencimento"), pos("data base")
    i_tc, i_tv = pos("taxa", "compra"), pos("taxa", "venda")
    i_pc, i_pv, i_pb = pos("pu", "compra"), pos("pu", "venda"), pos("pu", "base")
    linhas = {}
    for r in leitor:
        if len(r) <= max(i_pb, i_pv, i_pc):
            continue
        try:
            chave = (r[i_tipo].strip(), data_br(r[i_venc]), data_br(r[i_data]))
        except ValueError:
            continue
        linhas[chave] = {"tipo": chave[0], "vencimento": chave[1].isoformat(), "data": chave[2].isoformat(),
                         "taxa_compra": num(r[i_tc]), "taxa_venda": num(r[i_tv]),
                         "pu_compra": num(r[i_pc]), "pu_venda": num(r[i_pv]), "pu_base": num(r[i_pb])}
    return linhas


def selecionar(linhas, ultima_no_banco):
    """Títulos do último dia do arquivo; histórico inteiro ou só a janela recente."""
    ult = max(k[2] for k in linhas)
    atuais = {(k[0], k[1]) for k in linhas if k[2] == ult}
    corte = None if ultima_no_banco is None else ultima_no_banco - dt.timedelta(days=JANELA_DIAS)
    sel = [v for k, v in linhas.items()
           if (k[0], k[1]) in atuais and k[1] > ult and (corte is None or k[2] >= corte)]
    sel.sort(key=lambda v: (v["data"], v["tipo"], v["vencimento"]))
    return ult, atuais, sel


def cab_supabase():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url, cab


def ultima_data_banco():
    url, cab = cab_supabase()
    r = requests.get(f"{url}/rest/v1/tesouro_precos?select=data&order=data.desc&limit=1", headers=cab, timeout=60)
    if r.status_code == 404:
        raise RuntimeError("A tabela tesouro_precos não existe: rode o SQL 50 no Supabase.")
    r.raise_for_status()
    j = r.json()
    return dt.date.fromisoformat(j[0]["data"]) if j else None


def gravar(sel, lote_tam=1000):
    url, cab = cab_supabase()
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(0, len(sel), lote_tam):
        lote = [dict(l, coletado_em=agora) for l in sel[i:i + lote_tam]]
        for tentativa in range(3):
            r = requests.post(f"{url}/rest/v1/tesouro_precos?on_conflict=tipo,vencimento,data", json=lote,
                              headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
            if r.status_code < 300:
                break
            log(f"  lote {i // lote_tam + 1}: {r.status_code} {r.text[:200]}; nova tentativa")
            time.sleep(10)
        else:
            raise RuntimeError("Gravação recusada pelo Supabase (ver a linha acima).")
    return len(sel)


def apagar_vencidos(ult):
    url, cab = cab_supabase()
    r = requests.delete(f"{url}/rest/v1/tesouro_precos?vencimento=lte.{ult.isoformat()}",
                        headers=dict(cab, Prefer="return=minimal,count=exact"), timeout=120)
    if r.status_code >= 300:
        log(f"  aviso: limpeza dos vencidos não feita ({r.status_code} {r.text[:150]})")
        return None
    faixa = r.headers.get("Content-Range", "")
    return faixa.rsplit("/", 1)[-1] if "/" in faixa else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte")
    ap.add_argument("--saida")
    ap.add_argument("--ultima", help="testes: simula a última data já gravada (AAAA-MM-DD)")
    a = ap.parse_args()

    conteudo = open(a.fonte, "rb").read() if a.fonte else obter_csv()
    log(f"Arquivo do Tesouro: {len(conteudo) / 1e6:.1f} MB")
    linhas = ler(conteudo)
    log(f"Registros no arquivo: {len(linhas)}")
    if not linhas:
        raise RuntimeError("Arquivo do Tesouro sem registros.")

    if a.saida:
        ultima = dt.date.fromisoformat(a.ultima) if a.ultima else None
    else:
        ultima = ultima_data_banco()
    ult, atuais, sel = selecionar(linhas, ultima)
    log(f"Data base mais recente: {ult.strftime('%d/%m/%Y')} — {len(atuais)} títulos")
    for tipo in sorted({t for t, _ in atuais}):
        log(f"   {tipo}: {sum(1 for t, _ in atuais if t == tipo)}")
    log("Modo: " + ("HISTÓRICO COMPLETO (1ª execução)" if ultima is None
                    else f"últimos dias (banco até {ultima.strftime('%d/%m/%Y')})"))

    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
        json.dump(sel, open(os.path.join(a.saida, "tesouro_precos.json"), "w"), ensure_ascii=False)
        log(f"tesouro_precos: {len(sel)} linha(s) (teste, sem gravar)")
        return
    n = gravar(sel)
    log(f"tesouro_precos: {n} linha(s) gravada(s)")
    apagados = apagar_vencidos(ult)
    if apagados is not None:
        log(f"Títulos vencidos apagados: {apagados} linha(s)")
    log("FIM — Tesouro Direto atualizado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"ERRO: {e}")
        sys.exit(1)
