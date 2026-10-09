"""
SOLIDUNS — LEILÃO DE AUTOMÓVEIS — Coletor dos lotes — v1.1 (09/10/2026)

v1.1: a função de São Paulo do Supabase se chama "swift-api" (não "djen-relay"): na v1.0 todas as consultas ao
Diário de Justiça davam "não encontrada" e a rotina parava depois de ~18 min. O resumo agora é gravado também
quando a rotina falha (com o motivo).

FONTES
  djen       Diário de Justiça Eletrônico Nacional (editais de leilão judicial com veículos) — ATIVA.
             Mesmo caminho do Leilão de Imóveis: função djen-relay do Supabase em São Paulo
             (o DJEN bloqueia acessos de fora do Brasil), 35 tribunais.
  receita, detran, prf, banco, leiloeiro — EM IMPLANTAÇÃO: a rotina "Veículos - teste das fontes"
             (testar_fontes_veiculos.py) faz o reconhecimento de cada site; o leitor de cada uma
             entra numa versão seguinte, a partir das amostras reais (como foi feito com a Caixa).

LEITURA por REGRAS FIXAS (custo zero, sem IA) — veiculos_regras.py:
  tipo, marca e modelo (casados com a Tabela FIPE), ano fab./modelo, km, combustível, cor,
  final da placa, condição (circulação / recuperável / sucata), avaliação, lance inicial,
  data do leilão, cidade/UF (só se existir no IBGE), pátio. Nome de proprietário, CPF/CNPJ,
  Renavam, chassi e placa completa NÃO são gravados.

Grava em public.veiculos_leiloes (SQL 110) e encerra os lotes vencidos (veiculos_vencer).

Variáveis (Secrets do GitHub): SUPABASE_URL, SUPABASE_SERVICE_KEY, DJEN_TOKEN.
Opcional: VEICULOS_DIAS (padrão 3; na 1ª execução use 30). Testes: DJEN_RELAY_URL, LEILOES_TESTE.
"""

import datetime as dt
import html
import os
import re
import sys
import time

import requests

from veiculos_regras import (ESTADO_NOME, UFS27, VERSAO_REGRAS, Catalogo, Cidades, extrair_lotes,
                             parece_edital_de_veiculo, sa, texto_busca)

VERSAO = "1.1"
TRIBUNAIS = ["TJDFT", "TJGO", "TJSP", "TJMG", "TJBA", "TJCE", "TJPB", "TJRN",
             "TRF1", "TRF3", "TRF5", "TRF6", "TRT2", "TRT3", "TRT5", "TRT7", "TRT10", "TRT13", "TRT15", "TRT18", "TRT21",
             "TJRJ", "TJPR", "TJSC", "TJRS", "TJES", "TJPE", "TRF2", "TRF4", "TRT1", "TRT4", "TRT6", "TRT9", "TRT12", "TRT17"]
UF_DO_TRIBUNAL = {"TJDFT": "DF", "TJGO": "GO", "TJSP": "SP", "TJMG": "MG", "TJBA": "BA", "TJCE": "CE",
                  "TJPB": "PB", "TJRN": "RN", "TRT2": "SP", "TRT3": "MG", "TRT5": "BA", "TRT7": "CE",
                  "TRT10": "DF", "TRT13": "PB", "TRT15": "SP", "TRT18": "GO", "TRT21": "RN", "TRF6": "MG",
                  "TJRJ": "RJ", "TJPR": "PR", "TJSC": "SC", "TJRS": "RS", "TJES": "ES", "TJPE": "PE", "TRT1": "RJ", "TRT4": "RS",
                  "TRT6": "PE", "TRT9": "PR", "TRT12": "SC", "TRT17": "ES"}   # TRFs: vários estados (lido do texto)
DIAS = int(os.environ.get("VEICULOS_DIAS") or 3)

relatorio = []


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    relatorio.append(m)


def limpar(t):
    t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", t or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


# ------------------------------------------------------------ Supabase
def sb():
    url, ch = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not ch:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY.")
    cab = {"apikey": ch, "Content-Type": "application/json"}
    if not ch.startswith("sb_"):
        cab["Authorization"] = f"Bearer {ch}"
    return url.rstrip("/"), cab


def sb_get(caminho):
    url, cab = sb()
    saida, ini = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{caminho}", headers=dict(cab, Range=f"{ini}-{ini + 999}"), timeout=60)
        if r.status_code == 404:
            raise RuntimeError("Tabelas de veículos não existem: rode o SQL 110 no Supabase.")
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
    return f"{url}/functions/v1/swift-api"          # nome que o Supabase deu à função de São Paulo


def buscar_djen(tribunal, inicio, fim):
    token = os.environ.get("DJEN_TOKEN") or ""
    itens, pagina = [], 1
    while pagina <= 60:
        r = None
        for tentativa in range(3):
            try:
                r = requests.get(relay_url(), params={"forceFunctionRegion": "sa-east-1", "tribunal": tribunal,
                                                      "inicio": inicio, "fim": fim, "pagina": pagina},
                                 headers={"x-soliduns-token": token}, timeout=90)
                if r.status_code == 200:
                    break
                if r.status_code == 401:
                    raise RuntimeError("djen-relay recusou o token (confira DJEN_TOKEN no GitHub e no Supabase)")
                if r.status_code == 404:          # v1.1: endereço errado não espera 3 tentativas
                    raise RuntimeError("função de São Paulo não encontrada (confira DJEN_RELAY_URL na rotina)")
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


def uf_federal(t):
    tl = sa(t)
    m = re.search(r"se[cç][aã]o judici[aá]ria (?:do |da |de )?(?:estado (?:do |da |de )?)?([a-z ]{4,40})", tl)
    if m:
        nome = m.group(1).strip()
        for k in sorted(ESTADO_NOME, key=len, reverse=True):
            if nome.startswith(k):
                return ESTADO_NOME[k]
    m = re.search(r"subse[cç][aã]o judici[aá]ria de [a-z ]{3,40}?[\-/]\s?([a-z]{2})\b", tl)
    if m and m.group(1).upper() in UFS27:
        return m.group(1).upper()
    m = re.search(r"\bsj([a-z]{2})\b", tl)
    return m.group(1).upper() if m and m.group(1).upper() in UFS27 else None


def titulo(x):
    partes = [x.get("marca"), x.get("familia") or (x.get("modelo_texto") or "").split("/")[-1][:30] or None]
    ano = x.get("ano_modelo") or x.get("ano_fabricacao")
    t = " ".join(p for p in partes if p) or {"carro": "Veículo", "moto": "Motocicleta", "caminhao": "Caminhão/ônibus",
                                               "maquina": "Máquina"}.get(x["tipo"], "Veículo")
    return (t + (f" {ano}" if ano else ""))[:90]


def linhas_do_edital(pub, catalogo, cidades, hoje):
    texto = pub["texto"]
    trib = pub["tribunal"]
    uf_padrao = UF_DO_TRIBUNAL.get(trib) or uf_federal(texto)
    edital, lotes = extrair_lotes(texto, catalogo, cidades, uf_padrao, hoje)
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    out = []
    for x in lotes:
        if x.get("data_leilao") and x["data_leilao"] < (hoje - dt.timedelta(days=1)).isoformat() and not x.get("data_fim"):
            continue                                        # leilão já passou
        linha = {k: x.get(k) for k in ("tipo", "marca_codigo", "marca", "modelo_codigo", "familia", "modelo_texto",
                                        "ano_fabricacao", "ano_modelo", "combustivel", "km", "cor", "placa_final",
                                        "condicao", "uf", "cidade", "patio", "lance_inicial", "avaliacao",
                                        "data_leilao", "data_fim", "lote", "descricao")}
        linha.update(id=f"djen-{pub['id']}-{x['lote']}", fonte="djen", origem_nome=trib, anunciante_sigla=trib.lower(),
                     processo=edital.get("processo") or pub.get("processo"), leiloeiro=edital.get("leiloeiro"),
                     url=pub.get("link"), titulo=titulo(x), situacao="ativo", atualizado_em=agora)
        linha["texto_busca"] = texto_busca(linha["titulo"], linha.get("marca"), linha.get("familia"), linha.get("modelo_texto"),
                                           linha.get("cidade"), linha.get("patio"), linha.get("descricao"), trib)
        out.append(linha)
    return out


def main():
    log(f"SOLIDUNS — Leilão de automóveis v{VERSAO} ({VERSAO_REGRAS}) — {dt.datetime.now():%d/%m/%Y %H:%M} (UTC do servidor)")
    hoje = dt.date.today()
    inicio, fim = (hoje - dt.timedelta(days=DIAS)).isoformat(), hoje.isoformat()
    marcas = sb_get("veiculos_fipe_marcas?select=tipo,codigo,nome")
    modelos = sb_get("veiculos_fipe_modelos?select=tipo,marca_codigo,codigo,nome,familia")
    if not [m for m in marcas if m["tipo"] != "maquina"]:
        log("AVISO: catálogo FIPE vazio — rode antes a etapa 'Tabela FIPE'. Marca/modelo ficam sem casar nesta execução.")
    catalogo, cidades = Catalogo(marcas, modelos), Cidades()
    existentes = {x["id"]: x for x in sb_get("veiculos_leiloes?select=id,modelo_codigo,ano_modelo&fonte=eq.djen&situacao=eq.ativo")}

    falhas, editais, linhas = 0, 0, []
    for t in TRIBUNAIS:
        try:
            itens = buscar_djen(t, inicio, fim)
        except Exception as e:  # noqa: BLE001 — um tribunal nunca derruba os outros
            falhas += 1
            log(f"  {t:6s}: FALHOU — {str(e)[:160]}")
            continue
        n_ed = n_lot = 0
        for i in itens:
            texto = limpar(i.get("texto"))
            if not parece_edital_de_veiculo(texto):
                continue
            n_ed += 1
            novas = linhas_do_edital({"id": i["id"], "tribunal": t, "texto": texto, "processo": i.get("processo"),
                                      "link": i.get("link")}, catalogo, cidades, hoje)
            n_lot += len(novas)
            linhas += novas
        editais += n_ed
        log(f"  {t:6s}: {len(itens):5d} publicações com 'leilão' | {n_ed:3d} edital(is) com veículo | {n_lot:4d} lote(s)")
    if falhas == len(TRIBUNAIS):
        raise SystemExit("Nenhum tribunal respondeu (confira a função djen-relay e o DJEN_TOKEN).")

    for x in linhas:                                        # versão/ano mudou na releitura -> preço FIPE refeito
        ant = existentes.get(x["id"])
        if ant and (ant.get("modelo_codigo") != x.get("modelo_codigo") or ant.get("ano_modelo") != x.get("ano_modelo")):
            x.update(fipe_valor=None, fipe_codigo=None, fipe_referencia=None)
    vistos = {}
    for x in linhas:
        vistos[x["id"]] = x
    linhas = list(vistos.values())
    # mesmas colunas em cada envio (o PostgREST usa as chaves do 1º item para o lote todo)
    sb_upsert("veiculos_leiloes", [x for x in linhas if "fipe_valor" not in x])
    sb_upsert("veiculos_leiloes", [x for x in linhas if "fipe_valor" in x])

    casados = sum(1 for x in linhas if x.get("marca_codigo"))
    com_mod = sum(1 for x in linhas if x.get("familia"))
    com_cid = sum(1 for x in linhas if x.get("cidade"))
    log("")
    log(f"DJEN: {editais} edital(is) com veículo -> {len(linhas)} lote(s) | marca FIPE em {casados} | modelo em {com_mod} | "
        f"cidade IBGE em {com_cid}")
    log(f"Encerrados (data passou): {sb_rpc('veiculos_vencer')}")
    sb_upsert("veiculos_fontes", [{"fonte": "djen", "nome": "Diário de Justiça (DJEN)", "situacao": "ativa",
                                   "ultima_execucao": dt.datetime.now(dt.timezone.utc).isoformat(),
                                   "mensagem": f"{editais} edital(is) com veículo nos últimos {DIAS} dia(s)"}], "fonte")
    gravar_resumo()
    return 0


def gravar_resumo(extra=""):
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Leilão de automóveis\n\n```\n" + "\n".join(relatorio + ([extra] if extra else [])) + "\n```\n")


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
