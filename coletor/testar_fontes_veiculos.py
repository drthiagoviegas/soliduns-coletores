"""
SOLIDUNS — LEILÃO DE AUTOMÓVEIS — Reconhecimento das fontes — v1.0 (08/10/2026)

Rotina MANUAL ("Veículos - teste das fontes"). Não grava nada no banco e não usa chave nenhuma.
Para cada fonte (Receita Federal, Detrans, PRF, leiloeiros que vendem veículos de bancos):
  1. lê o robots.txt do site e respeita o que ele proíbe;
  2. abre a página inicial/de leilões e segue até 5 links com "leil" no mesmo site;
  3. guarda uma AMOSTRA de cada página (pasta amostras/, baixada como artefato da rotina)
     e um resumo: responde? quantos editais/lotes/PDFs aparecem? o robots.txt permite?
Com as amostras reais, o leitor de cada fonte é escrito na versão seguinte (mesmo método usado
com as matrículas da Caixa). Fonte cujo robots.txt proíbe a leitura automática fica de fora.
"""

import os
import re
import sys
import time
import urllib.parse as up
import urllib.robotparser as rp

import requests

AGENTE = "SOLIDUNS-coletor/1.0 (+contato@soliduns.com.br)"
FONTES = [
    ("receita", "Receita Federal (SLE)", ["https://www25.receita.fazenda.gov.br/sle-sociedade/portal",
                                          "https://www.gov.br/receitafederal/pt-br/assuntos/leiloes"]),
    ("prf", "PRF", ["https://www.gov.br/prf/pt-br/assuntos/leiloes",
                    "https://www.gov.br/prf/pt-br/assuntos/leiloes-prf-ate-2023"]),
    ("detran", "Detran-GO", ["https://goias.gov.br/detran/leiloes/"]),
    ("detran", "Detran-SP", ["https://www.detran.sp.gov.br/"]),
    ("detran", "Detran-MG", ["https://transito.mg.gov.br/"]),
    ("detran", "Detran-DF", ["https://www.detran.df.gov.br/"]),
    ("detran", "Detran-RJ", ["https://www.detran.rj.gov.br/"]),
    ("detran", "Detran-PR", ["https://www.detran.pr.gov.br/"]),
    ("detran", "Detran-BA", ["https://www.detran.ba.gov.br/"]),
    ("detran", "Detran-RS", ["https://www.detran.rs.gov.br/"]),
    ("detran", "Detran-SC", ["https://www.detran.sc.gov.br/"]),
    ("detran", "Detran-PE", ["https://www.detran.pe.gov.br/"]),
    ("detran", "Detran-CE", ["https://www.detran.ce.gov.br/"]),
    ("detran", "Detran-ES", ["https://detran.es.gov.br/"]),
    ("leiloeiro", "Copart", ["https://www.copart.com.br/"]),
    ("leiloeiro", "Sodré Santoro", ["https://www.sodresantoro.com.br/"]),
    ("leiloeiro", "Superbid", ["https://www.superbid.net/"]),
    ("leiloeiro", "Freitas Leiloeiro", ["https://www.freitasleiloeiro.com.br/"]),
    ("leiloeiro", "VIP Leilões", ["https://www.vipleiloes.com.br/"]),
    ("leiloeiro", "Mega Leilões", ["https://www.megaleiloes.com.br/"]),
]
PASTA = os.environ.get("AMOSTRAS") or "amostras"
relatorio = []


def log(m=""):
    print(m, flush=True)
    relatorio.append(m)


def robots(url):
    p = up.urlparse(url)
    r = rp.RobotFileParser()
    try:
        resp = requests.get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": AGENTE}, timeout=30)
        if resp.status_code >= 400:
            return None, f"sem robots.txt ({resp.status_code})"
        r.parse(resp.text.splitlines())
        return r, "robots.txt lido"
    except requests.RequestException as e:
        return None, f"robots.txt não respondeu ({type(e).__name__})"


def baixar(url):
    try:
        r = requests.get(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR"}, timeout=45)
        enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.status_code, r.url, r.content.decode(enc or "utf-8", "replace")
    except requests.RequestException as e:
        return None, url, f"ERRO {type(e).__name__}"


def contar(texto):
    t = texto.lower()
    return {"editais": len(re.findall(r"edital", t)), "lotes": len(re.findall(r"\blote\b", t)),
            "pdf": len(re.findall(r"\.pdf", t)), "veiculo": len(re.findall(r"ve[ií]culo|autom[oó]vel|motocicleta", t))}


def main():
    os.makedirs(PASTA, exist_ok=True)
    log("SOLIDUNS — Veículos: reconhecimento das fontes v1.0")
    log("")
    log("| fonte | site | situação | robots.txt | editais | lotes | PDFs | 'veículo' |")
    log("|---|---|---|---|---|---|---|---|")
    n = 0
    for fonte, nome, urls in FONTES:
        for url in urls:
            regra, msg = robots(url)
            if regra and not regra.can_fetch(AGENTE, url):
                log(f"| {fonte} | {nome} | NÃO LIDO | proíbe ({msg}) | | | | |")
                continue
            st, final, txt = baixar(url)
            c = contar(txt)
            n += 1
            nome_arq = re.sub(r"[^a-z0-9]+", "_", f"{fonte}_{nome}".lower())[:50]
            with open(os.path.join(PASTA, f"{n:02d}_{nome_arq}.html"), "w", encoding="utf-8") as f:
                f.write(f"<!-- {final} | status {st} -->\n" + txt)
            log(f"| {fonte} | {nome} ({final[:60]}) | {st} | {msg} | {c['editais']} | {c['lotes']} | {c['pdf']} | {c['veiculo']} |")
            if st != 200:
                continue
            base = up.urlparse(final).netloc
            links = []
            for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', txt, re.S | re.I):
                href, rot = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
                alvo = up.urljoin(final, href)
                if up.urlparse(alvo).netloc == base and re.search(r"leil", href + " " + rot, re.I) and alvo not in links:
                    links.append(alvo)
            for alvo in links[:5]:
                if regra and not regra.can_fetch(AGENTE, alvo):
                    log(f"|  | ↳ {alvo[:70]} | NÃO LIDO | proíbe | | | | |")
                    continue
                time.sleep(2)
                st2, final2, txt2 = baixar(alvo)
                c2 = contar(txt2)
                n += 1
                with open(os.path.join(PASTA, f"{n:02d}_{nome_arq}_sub.html"), "w", encoding="utf-8") as f:
                    f.write(f"<!-- {final2} | status {st2} -->\n" + txt2)
                log(f"|  | ↳ {final2[:70]} | {st2} | | {c2['editais']} | {c2['lotes']} | {c2['pdf']} | {c2['veiculo']} |")
            time.sleep(2)
    log("")
    log(f"{n} página(s) guardada(s) em '{PASTA}' — baixe o artefato 'amostras-fontes-veiculos' no fim da página da rotina.")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Veículos — reconhecimento das fontes\n\n" + "\n".join(relatorio) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
