"""
SOLIDUNS — LEILÃO DE AUTOMÓVEIS — Tabela FIPE — v1.0 (08/10/2026)

Duas tarefas, escolhidas sozinhas a cada execução:

  1. CATÁLOGO (marcas e modelos oficiais da FIPE) — quando a tabela está vazia ou tem mais de
     FIPE_DIAS_CATALOGO dias (padrão 30). São ~250 consultas. É dele que saem as opções dos
     filtros "Marca" e "Modelo" do site (nenhum nome digitado à mão).
  2. PREÇOS — para cada lote ATIVO com marca + modelo + ano casados e sem preço FIPE, consulta
     o valor (com cache em veiculos_fipe_precos). Teto: FIPE_PRECOS_MAX consultas por execução
     (padrão 150), para respeitar o limite gratuito da API.

Fonte: API pública e gratuita da Tabela FIPE (parallelum.com.br), que repete os dados oficiais da
Fundação Instituto de Pesquisas Econômicas. Sem custo. Se a API responder "limite atingido" (429),
o robô para educadamente e continua no dia seguinte.

Variáveis (Secrets do GitHub): SUPABASE_URL, SUPABASE_SERVICE_KEY. Opcional: FIPE_TOKEN (chave
gratuita da parallelum, aumenta o limite diário). Testes: FIPE_BASE troca o endereço.
Uso: python coletar_fipe.py [catalogo|precos|tudo]   (padrão: tudo). FIPE_FORCAR=1 refaz o catálogo mesmo em dia.
"""

import datetime as dt
import os
import re
import sys
import time
from collections import defaultdict

import requests

from veiculos_regras import chave, familia_do_modelo, familia_exibicao, nome_marca

VERSAO = "1.0"
BASE = (os.environ.get("FIPE_BASE") or "https://parallelum.com.br/fipe/api/v1").rstrip("/")
TIPOS = {"carro": "carros", "moto": "motos", "caminhao": "caminhoes"}
DIAS_CATALOGO = int(os.environ.get("FIPE_DIAS_CATALOGO") or 30)
PRECOS_MAX = int(os.environ.get("FIPE_PRECOS_MAX") or 150)
PAUSA = float(os.environ.get("FIPE_PAUSA") or 0.7)

relatorio = []
consultas = 0


class Limite(Exception):
    pass


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    relatorio.append(m)


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


def sb_upsert(tabela, linhas, conflito):
    if not linhas:
        return
    url, cab = sb()
    for i in range(0, len(linhas), 500):
        r = requests.post(f"{url}/rest/v1/{tabela}?on_conflict={conflito}", json=linhas[i:i + 500],
                          headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"gravação em {tabela} recusada: {r.status_code} {r.text[:200]}")


def sb_patch(caminho, dados):
    url, cab = sb()
    r = requests.patch(f"{url}/rest/v1/{caminho}", json=dados, headers=dict(cab, Prefer="return=minimal"), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"atualização recusada: {r.status_code} {r.text[:200]}")


# ------------------------------------------------------------ FIPE
def fipe(caminho):
    global consultas
    cab = {"Accept": "application/json", "User-Agent": "SOLIDUNS-coletor/1.0"}
    if os.environ.get("FIPE_TOKEN"):
        cab["X-Subscription-Token"] = os.environ["FIPE_TOKEN"]
    for tentativa in range(3):
        consultas += 1
        try:
            r = requests.get(f"{BASE}/{caminho}", headers=cab, timeout=40)
        except requests.RequestException:
            time.sleep(3 * (tentativa + 1))
            continue
        if r.status_code == 429:
            raise Limite()
        if r.status_code == 200:
            time.sleep(PAUSA)
            return r.json()
        if r.status_code == 404:
            return None
        time.sleep(3 * (tentativa + 1))
    raise RuntimeError(f"FIPE não respondeu ({caminho})")


def valor_br(v):
    m = re.search(r"([\d.]+,\d{2})", v or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


# ------------------------------------------------------------ 1. catálogo
def catalogo_velho():
    linhas = sb_get("veiculos_fipe_marcas?select=atualizado_em&origem=eq.fipe&order=atualizado_em.asc&limit=1")
    if not linhas:
        return True
    quando = dt.datetime.fromisoformat(linhas[0]["atualizado_em"].replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc) - quando > dt.timedelta(days=DIAS_CATALOGO)


def atualizar_catalogo():
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    tot_m = tot_mod = 0
    for tipo, cam in TIPOS.items():
        marcas = fipe(f"{cam}/marcas") or []
        linhas, vistos = [], defaultdict(list)
        for m in marcas:
            vistos[chave(nome_marca(m["nome"]))].append(m)
        for m in marcas:
            nome = nome_marca(m["nome"])
            if len(vistos[chave(nome)]) > 1:          # dois códigos com o mesmo nome -> mostra o da FIPE para não repetir
                nome = f'{nome} ({m["nome"].strip()})'
            linhas.append({"tipo": tipo, "codigo": str(m["codigo"]), "nome_fipe": m["nome"].strip(), "nome": nome,
                           "origem": "fipe", "atualizado_em": "2000-01-01T00:00:00+00:00"})
        linhas_marcas = linhas
        sb_upsert("veiculos_fipe_marcas", linhas, "tipo,codigo")   # data antiga até terminar os modelos (se parar no meio, refaz)
        tot_m += len(linhas)
        for m in marcas:
            j = fipe(f"{cam}/marcas/{m['codigo']}/modelos") or {}
            modelos = j.get("modelos") or []
            fam_bruta = [familia_do_modelo(x["nome"]) for x in modelos]
            grupos = defaultdict(list)
            for f in fam_bruta:
                if f:
                    grupos[chave(f)].append(f)
            exib = {k: familia_exibicao(v) for k, v in grupos.items()}
            linhas = [{"tipo": tipo, "marca_codigo": str(m["codigo"]), "codigo": str(x["codigo"]), "nome": x["nome"].strip(),
                       "familia": exib[chave(f)], "atualizado_em": agora}
                      for x, f in zip(modelos, fam_bruta) if f]
            sb_upsert("veiculos_fipe_modelos", linhas, "tipo,marca_codigo,codigo")
            tot_mod += len(linhas)
        sb_upsert("veiculos_fipe_marcas", [dict(x, atualizado_em=agora) for x in
                                           [{"tipo": tipo, "codigo": str(m["codigo"]), "nome_fipe": m["nome"].strip(),
                                             "nome": n["nome"], "origem": "fipe"} for m, n in zip(marcas, linhas_marcas)]],
                  "tipo,codigo")
        log(f"  {cam:9s}: {len(marcas)} marcas")
    log(f"Catálogo FIPE: {tot_m} marcas e {tot_mod} modelos gravados")


# ------------------------------------------------------------ 2. preços
def preencher_precos():
    lotes = sb_get("veiculos_leiloes?select=id,tipo,marca_codigo,modelo_codigo,ano_modelo,combustivel"
                   "&situacao=eq.ativo&fipe_valor=is.null&modelo_codigo=not.is.null&ano_modelo=not.is.null"
                   "&tipo=in.(carro,moto,caminhao)&condicao=neq.sucata&order=data_leilao.asc")
    cache = {}
    for p in sb_get("veiculos_fipe_precos?select=tipo,marca_codigo,modelo_codigo,ano_modelo,combustivel,codigo_fipe,valor,referencia"):
        cache.setdefault((p["tipo"], p["marca_codigo"], p["modelo_codigo"], p["ano_modelo"]), []).append(p)
    gastas_ini, feitos = consultas, 0
    for x in lotes:
        if consultas - gastas_ini >= PRECOS_MAX:
            log(f"  teto de {PRECOS_MAX} consultas atingido; o restante fica para amanhã")
            break
        k = (x["tipo"], x["marca_codigo"], x["modelo_codigo"], x["ano_modelo"])
        if k not in cache:
            cam = TIPOS[x["tipo"]]
            anos = fipe(f"{cam}/marcas/{x['marca_codigo']}/modelos/{x['modelo_codigo']}/anos") or []
            novos = []
            for a in anos:
                if not str(a["codigo"]).startswith(str(x["ano_modelo"]) + "-"):
                    continue
                d = fipe(f"{cam}/marcas/{x['marca_codigo']}/modelos/{x['modelo_codigo']}/anos/{a['codigo']}") or {}
                novos.append({"tipo": x["tipo"], "marca_codigo": x["marca_codigo"], "modelo_codigo": x["modelo_codigo"],
                              "ano_codigo": str(a["codigo"]), "ano_modelo": d.get("AnoModelo") or x["ano_modelo"],
                              "combustivel": d.get("Combustivel"), "codigo_fipe": d.get("CodigoFipe"),
                              "valor": valor_br(d.get("Valor")), "referencia": (d.get("MesReferencia") or "").strip() or None})
            sb_upsert("veiculos_fipe_precos", novos, "tipo,marca_codigo,modelo_codigo,ano_codigo")
            cache[k] = novos
        opcoes = [p for p in cache[k] if p.get("valor")]
        if x.get("combustivel"):
            mesmo = [p for p in opcoes if (p.get("combustivel") or "").lower().startswith(x["combustivel"].lower()[:4])]
            opcoes = mesmo or opcoes
        if len(opcoes) == 1 or (opcoes and len({p["valor"] for p in opcoes}) == 1):
            p = opcoes[0]
            sb_patch(f"veiculos_leiloes?id=eq.{requests.utils.quote(x['id'], safe='')}",
                     {"fipe_valor": p["valor"], "fipe_codigo": p["codigo_fipe"], "fipe_referencia": p["referencia"]})
            feitos += 1
    log(f"Preços FIPE: {feitos} lote(s) com preço novo | {len(lotes)} aguardavam | {consultas - gastas_ini} consulta(s)")


def main():
    modo = (sys.argv[1] if len(sys.argv) > 1 else "tudo").lower()
    log(f"SOLIDUNS — Tabela FIPE v{VERSAO} — {dt.datetime.now():%d/%m/%Y %H:%M} — modo {modo}")
    try:
        if modo in ("catalogo", "tudo") and (os.environ.get("FIPE_FORCAR") or catalogo_velho()):
            atualizar_catalogo()
        elif modo == "tudo":
            log("Catálogo FIPE em dia (atualiza a cada %d dias)" % DIAS_CATALOGO)
        if modo in ("precos", "tudo"):
            preencher_precos()
    except Limite:
        log("A API da FIPE informou LIMITE DIÁRIO atingido: o robô parou aqui e continua na próxima execução.")
    log(f"Consultas à FIPE nesta execução: {consultas}")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Tabela FIPE\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
