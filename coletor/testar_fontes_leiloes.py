"""
SOLIDUNS — AGENTE DE LEILÕES — TESTE DE ACESSO ÀS FONTES — v1.1 (05/10/2026)

v1.1: DIAGNÓSTICO DO BLOQUEIO DO DJEN. Mostra TUDO no próprio registro da
execução (não precisa baixar pacote): de onde o GitHub está saindo (país),
e, para cada endereço do CNJ, o código de resposta, os cabeçalhos que
identificam a proteção (Cloudflare, AWS, Akamai...) e o começo da página de
recusa. Testa também o Datajud (outra API pública do CNJ), que pode servir
de caminho alternativo.

NÃO grava nada no banco, NÃO usa chave/segredo. Só lê e relata.
Regras do projeto: identifica-se como robô (sem fingir navegador), sem
captcha, sem login, uma consulta por endereço, com pausa.
"""

import os
import re
import sys
import time
from datetime import date, timedelta

import requests

VERSAO = "1.1"
UA = "SOLIDUNS-coletor/1.0 (+https://soliduns.com.br; contato@soliduns.com.br)"
TIMEOUT = 40
CABECALHOS_INTERESSE = ("server", "via", "cf-ray", "cf-mitigated", "x-amz-cf-id", "x-amz-cf-pop", "x-cache",
                        "x-akamai-transformed", "akamai-grn", "x-iinfo", "x-cdn", "content-type", "retry-after",
                        "x-azure-ref", "x-sucuri-id")

relatorio = []


def log(msg=""):
    print(msg, flush=True)
    relatorio.append(msg)


def texto_limpo(html, n=500):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n]


def sondar(rotulo, url, params=None):
    log("")
    log(f"--- {rotulo}")
    log(f"    {url}")
    time.sleep(1.5)
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json, text/html, */*"},
                         timeout=TIMEOUT, allow_redirects=True)
    except Exception as e:
        log(f"    SEM CONEXÃO: {type(e).__name__}: {str(e)[:200]}")
        return None
    log(f"    HTTP {r.status_code} | {len(r.content)} bytes")
    for k in CABECALHOS_INTERESSE:
        v = r.headers.get(k)
        if v:
            log(f"    {k}: {v[:160]}")
    if r.status_code != 200 or "html" in r.headers.get("content-type", ""):
        log(f"    página: {texto_limpo(r.text)}")
    else:
        log(f"    conteúdo: {r.text[:300]}")
    return r


def main():
    log(f"SOLIDUNS — diagnóstico de fontes de leilões v{VERSAO} — {date.today():%d/%m/%Y}")

    log("")
    log("== 1. DE ONDE O GITHUB ESTÁ SAINDO ==")
    try:
        j = requests.get("https://ipinfo.io/json", headers={"User-Agent": UA}, timeout=20).json()
        log(f"    país: {j.get('country')} | cidade: {j.get('city')} | rede: {j.get('org')}")
    except Exception as e:
        log(f"    não foi possível saber ({type(e).__name__})")

    log("")
    log("== 2. DJEN (CNJ) ==")
    fim = date.today()
    ini = fim - timedelta(days=2)
    sondar("API de comunicações (a que o robô usaria)",
           "https://comunicaapi.pje.jus.br/api/v1/comunicacao",
           {"siglaTribunal": "TJDFT", "dataDisponibilizacaoInicio": ini.isoformat(),
            "dataDisponibilizacaoFim": fim.isoformat(), "pagina": 1, "itensPorPagina": 5})
    sondar("raiz da API", "https://comunicaapi.pje.jus.br/")
    sondar("portal público do DJEN (página para pessoas)", "https://comunica.pje.jus.br/")

    log("")
    log("== 3. DATAJUD (API pública do CNJ — caminho alternativo) ==")
    chave = None
    r = sondar("página oficial com a chave pública", "https://datajud-wiki.cnj.jus.br/api-publica/acesso")
    if r is not None and r.status_code == 200:
        m = re.search(r"APIKey\s+([A-Za-z0-9+/=_\-]{20,})", r.text)
        chave = m.group(1) if m else None
        log(f"    chave pública encontrada na página: {'sim' if chave else 'não'}")
    if chave:
        time.sleep(1.5)
        try:
            rr = requests.post("https://api-publica.datajud.cnj.jus.br/api_publica_tjdft/_search",
                               json={"size": 1, "query": {"match_all": {}}},
                               headers={"Authorization": f"APIKey {chave}", "Content-Type": "application/json",
                                        "User-Agent": UA}, timeout=TIMEOUT)
            log("")
            log("--- Datajud TJDFT (1 processo de exemplo)")
            log(f"    HTTP {rr.status_code} | {len(rr.content)} bytes")
            log(f"    conteúdo: {texto_limpo(rr.text, 300)}")
        except Exception as e:
            log(f"    Datajud SEM CONEXÃO: {type(e).__name__}")

    log("")
    log("== FIM — copie este registro e cole na conversa ==")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Diagnóstico de fontes de leilões\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
