"""
SOLIDUNS — AGENTE DE LEILÕES — TESTE: MATRÍCULAS DA CAIXA — v1.0 (07/10/2026)

ETAPA 1 da "pré-análise da matrícula" (aprovada pelo Thiago em 07/10; custo zero).
Pergunta que este teste responde: o robô, rodando no GitHub, consegue
  (a) abrir a página de detalhe de um imóvel da Caixa,
  (b) achar e baixar o PDF da matrícula,
  (c) ler o PDF (texto ou imagem escaneada, com o Tesseract gratuito) e
  (d) separar os atos e os ônus com as regras de ler_matricula.py?
E mede a máquina do GitHub (para o passo opcional de IA aberta no futuro).

NÃO grava nada no banco, NÃO usa chave/segredo, NÃO guarda os PDFs.
PRIVACIDADE (repositório público = registro público): o relatório mostra só
números (páginas, letras, atos) e os TIPOS de ato — nunca nomes, CPF ou trechos.
Regras do projeto: identifica-se como robô, respeita o robots.txt, sem captcha,
sem login, poucas consultas, com pausa.

USO: python coletor/testar_matriculas_leiloes.py [--ufs DF,SP] [--por-uf 3]
"""

import argparse
import csv
import io
import os
import re
import sys
import tempfile
import time
from urllib import robotparser
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ler_matricula  # noqa: E402

VERSAO = "1.0"
UA = "SOLIDUNS-coletor/1.0 (+https://soliduns.com.br; contato@soliduns.com.br)"
BASE = os.environ.get("CAIXA_BASE_TESTE") or "https://venda-imoveis.caixa.gov.br"
PROTECAO = ("server", "via", "cf-ray", "x-iinfo", "x-cdn", "x-akamai-transformed", "x-sucuri-id", "set-cookie",
            "content-type", "retry-after")
RE_PDF = re.compile(r"""["'(]\s*((?:https?://[^"'()\s]+)?/?[^"'()\s]*?\.pdf)\s*["')]""", re.I)

relatorio = []
s = requests.Session()
s.headers.update({"User-Agent": UA})
rp = None


def log(msg=""):
    print(msg, flush=True)
    relatorio.append(msg)


def permitido(url):
    return rp is None or rp.can_fetch(UA, url)


def pegar(url, rotulo):
    if not permitido(url):
        log(f"    {rotulo}: o robots.txt da Caixa NÃO permite -> não acessado")
        return None
    time.sleep(float(os.environ.get("PAUSA_TESTE", "3")))
    try:
        r = s.get(url, timeout=60, allow_redirects=True)
    except requests.RequestException as e:
        log(f"    {rotulo}: SEM CONEXÃO ({type(e).__name__})")
        return None
    tipo = r.headers.get("content-type", "")
    log(f"    {rotulo}: HTTP {r.status_code} | {len(r.content)} bytes | {tipo[:40]}")
    if r.status_code != 200:
        for k in PROTECAO:
            if r.headers.get(k):
                log(f"      {k}: {r.headers[k][:120]}")
    if "html" in tipo:
        titulo = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
        log(f"      título da página: {(titulo.group(1).strip() if titulo else '-')[:90]}")
        if re.search(r"captcha|radware|are you a robot|access denied|bloquead", r.text, re.I):
            log("      >>> a página parece ser de PROTEÇÃO contra robôs (captcha/bloqueio)")
    return r


def amostra(uf, n):
    r = pegar(f"{BASE}/listaweb/Lista_imoveis_{uf}.csv", f"lista {uf}")
    if r is None or r.status_code != 200:
        return []
    try:
        texto = r.content.decode("utf-8")
    except UnicodeDecodeError:
        texto = r.content.decode("latin-1")
    linhas = texto.splitlines()
    i = next((k for k, l in enumerate(linhas[:10]) if ";" in l and "link" in l.lower()), None)
    if i is None:
        log("      lista sem cabeçalho reconhecível")
        return []
    cab = [c.strip().lower() for c in next(csv.reader([linhas[i]], delimiter=";"))]
    j_link = next(k for k, c in enumerate(cab) if "link" in c)
    j_num = 0
    itens = []
    for reg in csv.reader(io.StringIO("\n".join(linhas[i + 1:])), delimiter=";"):
        if len(reg) > j_link and reg[j_link].strip():
            itens.append((re.sub(r"\D", "", reg[j_num]), reg[j_link].strip()))
    log(f"      {len(itens)} imóveis com link na lista; testando {min(n, len(itens))}")
    passo = max(1, len(itens) // max(n, 1))            # espalha a amostra pela lista
    return itens[::passo][:n]


def testar_imovel(uf, numero, link):
    log("")
    log(f"--- imóvel {uf} nº ...{numero[-4:]}")
    r = pegar(link, "página de detalhe")
    candidatos = []
    if r is not None and r.status_code == 200 and "html" in r.headers.get("content-type", ""):
        achados = sorted({m.group(1) for m in RE_PDF.finditer(r.text)})
        mats = [a for a in achados if "matric" in a.lower()]
        log(f"      PDFs citados na página: {len(achados)} (de matrícula: {len(mats)})")
        for a in achados[:6]:
            # mostra só o "formato" do caminho (números trocados por #)
            log(f"        formato: {re.sub(r'[0-9]', '#', a)[:100]}")
        candidatos += [urljoin(link, a) for a in mats]
    # caminho direto (formato usado pelo site da Caixa), caso a página não abra
    for c in (f"{BASE}/editais/matricula/{uf}/{numero}.pdf",):
        if c not in candidatos:
            candidatos.append(c)
    for url in candidatos[:3]:
        rr = pegar(url, "PDF da matrícula (" + re.sub(r"[0-9]", "#", url.replace(BASE, ""))[:60] + ")")
        if rr is None or rr.status_code != 200 or not rr.content.startswith(b"%PDF"):
            if rr is not None and rr.status_code == 200:
                log("      a resposta NÃO é um PDF")
            continue
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(rr.content)
            caminho = f.name
        try:
            t0 = time.time()
            texto, metodo, pags = ler_matricula.texto_do_pdf(caminho)
            res = ler_matricula.analisar(texto)
            log(f"      LIDO: {pags} página(s) | método {metodo} | {len(texto)} letras | "
                f"qualidade {res['qualidade']} | {time.time() - t0:.0f} s")
            log(f"      atos separados: {res['atos']} -> " +
                (", ".join(f"{a['ato']}:{a['tipo']}" for a in res["lista_atos"][:25]) or "-"))
            log(f"      ônus: " + (", ".join(f"{o['ato']} {o['tipo']} ({o['situacao']})" for o in res["onus"]) or "nenhum"))
            log(f"      consolidação da propriedade: {'sim' if res['consolidacao'] else 'não achada'}")
            log(f"      RESUMO: {res['resumo'][:200]}")
            return True
        except Exception as e:
            log(f"      erro ao ler o PDF: {type(e).__name__}: {str(e)[:150]}")
        finally:
            os.unlink(caminho)
    return False


def maquina():
    log("== 0. MÁQUINA DO GITHUB ==")
    try:
        mem = open("/proc/meminfo").read()
        tot = int(re.search(r"MemTotal:\s+(\d+)", mem).group(1)) // 1024
        log(f"    processadores: {os.cpu_count()} | memória: {tot} MB")
    except Exception:
        log(f"    processadores: {os.cpu_count()}")
    st = os.statvfs("/")
    log(f"    disco livre: {st.f_bavail * st.f_frsize // 2**30} GB")


def main():
    global rp
    ap = argparse.ArgumentParser()
    ap.add_argument("--ufs", default="DF,SP,GO")
    ap.add_argument("--por-uf", type=int, default=3)
    a = ap.parse_args()

    log(f"SOLIDUNS — teste de matrículas da Caixa v{VERSAO} (leitor v{ler_matricula.VERSAO})")
    maquina()
    log("")
    log("== 1. ROBOTS.TXT DA CAIXA ==")
    try:
        r = s.get(BASE + "/robots.txt", timeout=40)
        log(f"    HTTP {r.status_code}")
        if r.status_code < 400:
            rp = robotparser.RobotFileParser()
            rp.parse(r.text.splitlines())
            for l in r.text.splitlines()[:15]:
                log(f"    | {l[:100]}")
    except requests.RequestException as e:
        log(f"    sem robots.txt ({type(e).__name__})")

    log("")
    log("== 2. IMÓVEIS DE AMOSTRA ==")
    total = lidos = 0
    for uf in [u.strip().upper() for u in a.ufs.split(",") if u.strip()]:
        log("")
        log(f"[{uf}]")
        for numero, link in amostra(uf, a.por_uf):
            total += 1
            lidos += 1 if testar_imovel(uf, numero, link) else 0

    log("")
    log(f"== RESULTADO: matrícula baixada e lida em {lidos} de {total} imóveis ==")
    log("== FIM — copie este registro e cole na conversa ==")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Teste de matrículas da Caixa\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
