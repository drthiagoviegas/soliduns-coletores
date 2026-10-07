"""
SOLIDUNS — AGENTE DE LEILÕES — Coletor do RADAR — v1.2 (05/10/2026)

v1.2: depois das UFs, recalcula a PRÉ-NOTA de todos os imóveis ativos
(public.leiloes_radar_pontuar(), SQL 58). Falha aqui só avisa.

v1.1: mais 5 estados a pedido do Thiago — RN, PB, CE, BA e MG (total: 8 UFs).

Fase 1 do "Projeto de automação com Agente IA" (documento de 04/10/2026).
Rotina "Leilões" (leiloes.yml), todo dia. Grava em public.leiloes_radar
(SQL 57). Nada aparece no site até a versão que lê o Radar (v63).

FONTE: lista oficial de imóveis da Caixa por estado (DF, GO, SP, RN, PB, CE, BA, MG),
  https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_<UF>.csv
  Formato REAL conferido no teste de 05/10/2026: separador ";",
  codificação Latin-1, 1ª linha com a "Data de geração", 2ª linha com o
  cabeçalho: Nº do imóvel; UF; Cidade; Bairro; Endereço; Preço; Valor de
  avaliação; Desconto; Financiamento; Descrição; Modalidade de venda; Link.
  Preço/avaliação no formato brasileiro (1.945.401,90); desconto com ponto
  (41.05). A descrição traz tipo, áreas, quartos e vagas em texto corrido.

O QUE FAZ, POR UF:
  1. confere o robots.txt e baixa a lista (identifica-se como robô);
  2. se a resposta não for a planilha (bloqueio, página de erro), avisa e
     NÃO mexe no que já está gravado;
  3. se a "Data de geração" for a mesma da última coleta, não regrava
     (a Caixa não atualiza todo dia) — use --forcar para regravar;
  4. grava/atualiza cada imóvel (situacao = 'ativo');
  5. marca 'saiu' o que não veio na lista de hoje — SÓ se a lista nova
     tiver pelo menos 50% das linhas da anterior (proteção contra lista
     incompleta);
  6. registra a coleta em public.leiloes_coletas.
Uma UF com problema nunca derruba as outras.

USO
  python coletor/coletar_leiloes.py                 (as 8 UFs da lista UFS_PADRAO)
  python coletor/coletar_leiloes.py --uf DF --forcar
  python coletor/coletar_leiloes.py --arquivo Lista_imoveis_DF.csv --uf DF
        (importa um arquivo baixado à mão — plano B se a Caixa bloquear)
  python coletor/coletar_leiloes.py --sem-gravar     (só lê e resume)
Variáveis: SUPABASE_URL e SUPABASE_SERVICE_KEY (as mesmas das outras rotinas).
"""

import argparse
import csv
import datetime as dt
import io
import os
import re
import sys
import time
import unicodedata
from urllib import robotparser

import requests

VERSAO = "1.2"
UA = "SOLIDUNS-coletor/1.0 (+https://soliduns.com.br; contato@soliduns.com.br)"
CAIXA_BASE = "https://venda-imoveis.caixa.gov.br"
CAIXA_URL = CAIXA_BASE + "/listaweb/Lista_imoveis_{uf}.csv"
UFS_PADRAO = ["DF", "GO", "SP", "RN", "PB", "CE", "BA", "MG"]
FONTE = "caixa"

relatorio = []


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    relatorio.append(msg)


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower().strip()


# ------------------------------------------------------------ leitura da lista
def numero(txt):
    """'1.945.401,90' -> 1945401.9 ; '41.05' -> 41.05 ; '' -> None"""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


TIPOS = [  # (começo do texto sem acento, código do site)
    ("apartamento", "apartamento"), ("cobertura", "cobertura"), ("kitnet", "kitnet"), ("kit", "kitnet"),
    ("flat", "flat"), ("loft", "loft"), ("sobrado", "sobrado"), ("casa", "casa"),
    ("sala", "sala_comercial"), ("loja", "loja"), ("ponto", "ponto_comercial"), ("galpao", "galpao"),
    ("barracao", "galpao"), ("predio", "predio_comercial"), ("edificio", "predio_comercial"),
    ("hotel", "hotel_pousada"), ("pousada", "hotel_pousada"),
    ("terreno", "terreno_urbano"), ("lote", "terreno_urbano"), ("gleba", "gleba"),
    ("fazenda", "fazenda"), ("sitio", "sitio"), ("chacara", "chacara"), ("haras", "haras"),
    ("imovel rural", "area_rural"), ("area rural", "area_rural"), ("rural", "area_rural"),
]


def tipo_site(tipo_original):
    t = sem_acento(tipo_original)
    for chave, cod in TIPOS:
        if t.startswith(chave):
            return cod
    return "outros"


def ler_descricao(desc):
    d = sem_acento(desc)
    def area(rotulo):
        m = re.search(r"([\d.,]+)\s*de area " + rotulo, d)
        v = numero(m.group(1).replace(",", ".")) if m else None
        return v if v else None          # 0.00 = não informado
    q = re.search(r"(\d+)\s*qto", d)
    v = re.search(r"(\d+)\s*vaga", d)
    return {
        "tipo_original": (desc or "").split(",")[0].strip().rstrip(".") or None,
        "area_total": area("total"),
        "area_privativa": area("privativa"),
        "area_terreno": area("do terreno"),
        "quartos": int(q.group(1)) if q else None,
        "vagas": int(v.group(1)) if v else None,
    }


COLUNAS = {  # pedaço do nome da coluna (sem acento) -> campo
    "do imovel": "numero", "uf": "uf", "cidade": "cidade", "bairro": "bairro", "endereco": "endereco",
    "preco": "preco", "avaliacao": "avaliacao", "desconto": "desconto", "financiamento": "financiamento",
    "descricao": "descricao", "modalidade": "modalidade_venda", "link": "link",
}


def ler_lista(conteudo_bytes, uf_esperada):
    """Devolve (data_lista, [imóveis]). Lança ValueError se não for a planilha."""
    try:
        texto = conteudo_bytes.decode("utf-8")
    except UnicodeDecodeError:
        texto = conteudo_bytes.decode("latin-1")
    linhas = texto.splitlines()
    data_lista = None
    i_cab = None
    for i, l in enumerate(linhas[:10]):
        if data_lista is None:
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", l)
            if m and "gera" in sem_acento(l):
                data_lista = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if "do imovel" in sem_acento(l) and ";" in l:
            i_cab = i
            break
    if i_cab is None:
        raise ValueError("cabeçalho da planilha não encontrado (a resposta não é a lista da Caixa)")

    cab = [sem_acento(c) for c in next(csv.reader([linhas[i_cab]], delimiter=";"))]
    mapa = {}
    for j, nome in enumerate(cab):
        for pedaco, campo in COLUNAS.items():
            if campo not in mapa.values() and (nome == pedaco or (len(pedaco) > 2 and pedaco in nome)):
                mapa[j] = campo
                break
    faltam = {"numero", "uf", "preco"} - set(mapa.values())
    if faltam:
        raise ValueError(f"colunas obrigatórias ausentes: {sorted(faltam)} (cabeçalho: {cab})")

    imoveis, descartados = [], 0
    for reg in csv.reader(io.StringIO("\n".join(linhas[i_cab + 1:])), delimiter=";"):
        if not reg or not any(c.strip() for c in reg):
            continue
        bruto = {campo: (reg[j].strip() if j < len(reg) else "") for j, campo in mapa.items()}
        num = re.sub(r"\D", "", bruto.get("numero", ""))
        uf = bruto.get("uf", "").upper()
        preco = numero(bruto.get("preco"))
        if not num or not re.fullmatch(r"[A-Z]{2}", uf) or preco is None:
            descartados += 1
            continue
        desc = bruto.get("descricao") or ""
        info = ler_descricao(desc)
        fin = sem_acento(bruto.get("financiamento", ""))
        imoveis.append({
            "id": f"{FONTE}-{num}",
            "fonte": FONTE,
            "numero": num,
            "uf": uf,
            "cidade": bruto.get("cidade") or None,
            "bairro": bruto.get("bairro") or None,
            "endereco": bruto.get("endereco") or None,
            "tipo": tipo_site(info["tipo_original"]),
            "preco": preco,
            "avaliacao": numero(bruto.get("avaliacao")),
            "desconto": numero(bruto.get("desconto")),
            "financiamento": True if fin.startswith("sim") else (False if fin.startswith("nao") else None),
            "descricao": desc or None,
            "modalidade_venda": bruto.get("modalidade_venda") or None,
            "link": bruto.get("link") or None,
            "data_lista": data_lista.isoformat() if data_lista else None,
            **info,
        })
    outras = sum(1 for x in imoveis if x["uf"] != uf_esperada)
    if outras:
        log(f"  aviso: {outras} linha(s) de outra UF na lista de {uf_esperada}")
    if descartados:
        log(f"  aviso: {descartados} linha(s) sem nº, UF ou preço foram ignoradas")
    # mesmo nº repetido: fica o último
    unicos = {x["id"]: x for x in imoveis}
    return data_lista, list(unicos.values())


# ------------------------------------------------------------ download
sessao = requests.Session()
sessao.headers.update({"User-Agent": UA})


def robots_permite(url):
    try:
        r = sessao.get(CAIXA_BASE + "/robots.txt", timeout=40)
        if r.status_code >= 400:
            return True  # robots.txt indisponível (4xx): permitido (RFC 9309)
        rp = robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, url)
    except requests.RequestException:
        return True


def baixar(uf):
    url = CAIXA_URL.format(uf=uf)
    if not robots_permite(url):
        raise RuntimeError("o robots.txt da Caixa passou a proibir o download — usar a importação manual (--arquivo)")
    for tentativa in range(3):
        try:
            r = sessao.get(url, timeout=120)
            if r.status_code == 200:
                return r.content
            log(f"  {uf}: HTTP {r.status_code} (tentativa {tentativa + 1})")
        except requests.RequestException as e:
            log(f"  {uf}: {type(e).__name__} (tentativa {tentativa + 1})")
        time.sleep(15)
    raise RuntimeError("a Caixa não entregou a lista (ver as linhas acima)")


# ------------------------------------------------------------ Supabase
def cab_supabase():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url.rstrip("/"), cab


def ultima_coleta(uf):
    url, cab = cab_supabase()
    r = requests.get(f"{url}/rest/v1/leiloes_coletas?fonte=eq.{FONTE}&uf=eq.{uf}&select=data_lista,linhas",
                     headers=cab, timeout=60)
    if r.status_code == 404:
        raise RuntimeError("As tabelas do Radar não existem: rode o SQL 57 no Supabase.")
    r.raise_for_status()
    j = r.json()
    return (j[0]["data_lista"], j[0]["linhas"]) if j else (None, 0)


def gravar(tabela, linhas, conflito, lote_tam=500):
    url, cab = cab_supabase()
    for i in range(0, len(linhas), lote_tam):
        lote = linhas[i:i + lote_tam]
        for _ in range(3):
            r = requests.post(f"{url}/rest/v1/{tabela}?on_conflict={conflito}", json=lote,
                              headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=180)
            if r.status_code < 300:
                break
            log(f"  {tabela} lote {i // lote_tam + 1}: {r.status_code} {r.text[:200]}; nova tentativa")
            time.sleep(10)
        else:
            raise RuntimeError(f"Gravação de {tabela} recusada pelo Supabase (ver a linha acima).")


def marcar_saidas(uf, inicio_iso):
    url, cab = cab_supabase()
    r = requests.post(f"{url}/rest/v1/rpc/leiloes_radar_saidas",
                      json={"p_fonte": FONTE, "p_uf": uf, "p_inicio": inicio_iso}, headers=cab, timeout=120)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------ principal
def processar(uf, args):
    log(f"[{uf}]")
    if args.arquivo:
        with open(args.arquivo, "rb") as f:
            conteudo = f.read()
        log(f"  arquivo local: {args.arquivo} ({len(conteudo)} bytes)")
    else:
        conteudo = baixar(uf)
        log(f"  baixado: {len(conteudo)} bytes")
    data_lista, imoveis = ler_lista(conteudo, uf)
    imoveis = [x for x in imoveis if x["uf"] == uf]
    data_txt = data_lista.strftime("%d/%m/%Y") if data_lista else "sem data"
    tipos = {}
    for x in imoveis:
        tipos[x["tipo"]] = tipos.get(x["tipo"], 0) + 1
    log(f"  lista de {data_txt}: {len(imoveis)} imóveis | tipos: "
        + ", ".join(f"{k} {v}" for k, v in sorted(tipos.items(), key=lambda kv: -kv[1])))
    if not imoveis:
        raise RuntimeError("lista sem nenhum imóvel válido — nada gravado")
    if args.sem_gravar:
        return f"{len(imoveis)} lidos (sem gravar)"

    data_ant, linhas_ant = ultima_coleta(uf)
    if data_lista and data_ant == data_lista.isoformat() and not args.forcar:
        log(f"  lista igual à da última coleta ({data_txt}) — nada a regravar")
        return f"sem mudança (lista de {data_txt})"

    inicio = dt.datetime.now(dt.timezone.utc).isoformat()
    for x in imoveis:
        x.update(situacao="ativo", visto_em=inicio, saiu_em=None, coletado_em=inicio)
    gravar("leiloes_radar", imoveis, "id")
    log(f"  leiloes_radar: {len(imoveis)} gravado(s)/atualizado(s)")

    if linhas_ant and len(imoveis) < 0.5 * linhas_ant:
        log(f"  ATENÇÃO: lista com {len(imoveis)} imóveis contra {linhas_ant} da anterior — saídas NÃO marcadas")
        saidas = "saídas não marcadas (lista menor que 50% da anterior)"
        referencia = linhas_ant          # mantém a referência antiga
    else:
        referencia = len(imoveis)
        saidas = marcar_saidas(uf, inicio)
        log(f"  {saidas}")
    gravar("leiloes_coletas", [{"fonte": FONTE, "uf": uf, "data_lista": data_lista.isoformat() if data_lista else None,
                                "linhas": referencia, "executado_em": inicio}], "fonte,uf")
    return f"{len(imoveis)} imóveis (lista de {data_txt}); {saidas}"


def main():
    ap = argparse.ArgumentParser(description="SOLIDUNS — coletor do Radar de leilões (Caixa)")
    ap.add_argument("--uf", default=",".join(UFS_PADRAO), help="UFs separadas por vírgula (padrão: as 8 da lista UFS_PADRAO)")
    ap.add_argument("--forcar", action="store_true", help="regrava mesmo se a lista não mudou")
    ap.add_argument("--arquivo", help="importar um arquivo CSV baixado à mão (use com UMA UF)")
    ap.add_argument("--sem-gravar", action="store_true", help="só lê e resume")
    args = ap.parse_args()
    ufs = [u.strip().upper() for u in args.uf.split(",") if u.strip()]
    if args.arquivo and len(ufs) != 1:
        ap.error("--arquivo exige exatamente uma UF (--uf DF, por exemplo)")

    log(f"SOLIDUNS — Leilões (Radar Caixa) v{VERSAO} — {dt.datetime.now():%d/%m/%Y %H:%M} (UTC do servidor)")
    resultado, falhas = {}, 0
    for uf in ufs:
        try:
            resultado[uf] = processar(uf, args)
        except Exception as e:  # noqa: BLE001 — uma UF nunca derruba as outras
            falhas += 1
            resultado[uf] = f"FALHOU: {str(e)[:200]}"
            log(f"  {uf} FALHOU: {e}")
    if not args.sem_gravar and falhas < len(ufs):
        try:
            url, cab = cab_supabase()
            r = requests.post(f"{url}/rest/v1/rpc/leiloes_radar_pontuar", json={}, headers=cab, timeout=300)
            msg = r.json() if r.status_code < 300 else f"não calculada ({r.status_code}{' — rode o SQL 58' if r.status_code == 404 else ''})"
            resultado["PRÉ-NOTA"] = msg
            log(f"Pré-nota: {msg}")
        except Exception as e:  # noqa: BLE001
            resultado["PRÉ-NOTA"] = f"aviso: não calculada ({str(e)[:120]})"
    log("")
    log("== RESUMO ==")
    for uf, txt in resultado.items():
        log(f"  {uf}: {txt}")

    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Leilões — Radar Caixa\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 1 if falhas == len(ufs) else 0


if __name__ == "__main__":
    sys.exit(main())
