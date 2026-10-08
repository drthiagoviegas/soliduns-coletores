"""
SOLIDUNS — LEILÃO DE IMÓVEIS — Reconhecimento das fontes EXTRAJUDICIAIS (bancos e leiloeiros) — v1.0 (08/10/2026)

Rotina MANUAL ("Leilões - teste dos bancos"). Não grava nada no banco de dados e não usa chave nenhuma.
Para cada instituição do SFH/SFI (Banco do Brasil, Poupex, Santander, Itaú, Bradesco, BRB, BMG e outras) e para os
leiloeiros que vendem imóveis de bancos:
  1. lê o robots.txt e RESPEITA o que ele proíbe (página proibida não é aberta);
  2. abre o endereço conhecido da área de imóveis e segue até 6 links do mesmo site com "imóve", "leil" ou "venda";
  3. anota pistas de como os dados chegam à página (lista em HTML, dados embutidos em JSON, endereço de API,
     sitemap, PDF de edital) e conta preços "R$", cidades/UF e menções a "lote", "edital", "matrícula";
  4. guarda uma AMOSTRA de cada página (pasta amostras/, artefato "amostras-bancos-leiloeiros") e o resumo.
Com as amostras reais, o leitor de cada fonte permitida é escrito na versão seguinte (mesmo método da Caixa).
"""

import csv
import os
import re
import sys
import time
import urllib.parse as up
import urllib.robotparser as rp

import requests

AGENTE = "SOLIDUNS-coletor/1.0 (+contato@soliduns.com.br)"
# (tipo, sigla, nome, [endereços para começar]) — endereço errado ou fora do ar só aparece como erro no resumo
FONTES = [
    ("banco", "bb", "Banco do Brasil", ["https://seuimovelbb.com.br/", "https://www.bb.com.br/"]),
    ("banco", "poupex", "Poupex", ["https://www.poupex.com.br/"]),
    ("banco", "santander", "Santander", ["https://www.santanderimoveis.com.br/", "https://www.santander.com.br/"]),
    ("banco", "itau", "Itaú Unibanco", ["https://www.itau.com.br/imoveis-itau/"]),
    ("banco", "bradesco", "Bradesco", ["https://www.bradesco.com.br/"]),
    ("banco", "brb", "BRB – Banco de Brasília", ["https://campanhas.brb.com.br/imoveisbrb/", "https://novo.brb.com.br/"]),
    ("banco", "bmg", "Banco BMG", ["https://www.bancobmg.com.br/"]),
    ("banco", "banrisul", "Banrisul", ["https://www.banrisul.com.br/"]),
    ("banco", "inter", "Banco Inter", ["https://www.bancointer.com.br/"]),
    ("banco", "safra", "Banco Safra", ["https://www.safra.com.br/"]),
    ("banco", "btg", "BTG Pactual", ["https://www.btgpactual.com/"]),
    ("banco", "sicredi", "Sicredi", ["https://www.sicredi.com.br/"]),
    ("banco", "sicoob", "Sicoob", ["https://www.sicoob.com.br/"]),
    ("banco", "emgea", "Emgea", ["https://www.emgea.gov.br/"]),
    ("plataforma", "resale", "Resale (vitrine de imóveis de bancos)", ["https://www.resale.com.br/"]),
    ("leiloeiro", "zuk", "Portal Zuk (Zukerman)", ["https://www.portalzuk.com.br/"]),
    ("leiloeiro", "sodre", "Sodré Santoro", ["https://www.sodresantoro.com.br/"]),
    ("leiloeiro", "mega", "Mega Leilões", ["https://www.megaleiloes.com.br/"]),
    ("leiloeiro", "superbid", "Superbid", ["https://www.superbid.net/"]),
    ("leiloeiro", "frazao", "Frazão Leilões", ["https://www.frazaoleiloes.com.br/"]),
    ("leiloeiro", "vip", "VIP Leilões", ["https://www.vipleiloes.com.br/"]),
    ("leiloeiro", "freitas", "Freitas Leiloeiro", ["https://www.freitasleiloeiro.com.br/"]),
    ("leiloeiro", "biasi", "Biasi Leilões", ["https://www.biasileiloes.com.br/"]),
    ("leiloeiro", "sato", "Sato Leilões", ["https://www.satoleiloes.com.br/"]),
    ("leiloeiro", "milan", "Milan Leilões", ["https://www.milanleiloes.com.br/"]),
]
PASTA = os.environ.get("AMOSTRAS") or "amostras"
RE_LINK_UTIL = re.compile(r"im[oó]ve|leil|venda|retomad|oportunidad", re.I)
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
            return None, "sem robots.txt", []
        linhas = resp.text.splitlines()
        r.parse(linhas)
        mapas = [l.split(":", 1)[1].strip() for l in linhas if l.lower().startswith("sitemap:")]
        return r, "robots.txt lido", mapas
    except requests.RequestException as e:
        return None, f"robots.txt não respondeu ({type(e).__name__})", []


def baixar(url):
    try:
        r = requests.get(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR"}, timeout=45)
        enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.status_code, r.url, r.content.decode(enc or "utf-8", "replace"), r.headers.get("content-type", "")
    except requests.RequestException as e:
        return None, url, f"ERRO {type(e).__name__}", ""


def pistas(texto):
    t = texto.lower()
    p = []
    if "__next_data__" in t:
        p.append("Next.js (JSON embutido)")
    if "window.__nuxt__" in t or "__nuxt" in t:
        p.append("Nuxt (JSON embutido)")
    if "application/ld+json" in t:
        p.append("JSON-LD")
    apis = sorted(set(re.findall(r"""["'](/?(?:api|graphql|wp-json)[/\w\-.?=&]*)["']""", texto, re.I)))[:5]
    if apis:
        p.append("API: " + ", ".join(apis))
    if re.search(r"<table", t):
        p.append("tabela HTML")
    if "captcha" in t or "recaptcha" in t:
        p.append("CAPTCHA")
    if len(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", t, flags=re.S).split()) < 80:
        p.append("pouco texto (página montada por JavaScript)")
    return p


def contar(texto):
    t = texto.lower()
    return {"precos": len(re.findall(r"r\$\s?\d", t)), "lotes": len(re.findall(r"\blote\b", t)),
            "editais": len(re.findall(r"edital", t)), "pdf": len(re.findall(r"\.pdf", t)),
            "uf": len(re.findall(r"[a-zà-ú]{3,}\s?[/-]\s?(?:df|go|sp|mg|rj|ba|pr|sc|rs|pe|ce|es|rn|pb)\b", t)),
            "matricula": len(re.findall(r"matr[ií]cula", t))}


def guardar(n, nome, final, st, txt):
    with open(os.path.join(PASTA, f"{n:03d}_{nome}.html"), "w", encoding="utf-8") as f:
        f.write(f"<!-- {final} | status {st} -->\n" + txt)


def main():
    os.makedirs(PASTA, exist_ok=True)
    resumo = []
    log("SOLIDUNS — Leilões extrajudiciais: reconhecimento de bancos e leiloeiros v1.0")
    log("")
    log("| tipo | instituição | página | situação | robots.txt | R$ | lotes | editais | PDFs | pistas |")
    log("|---|---|---|---|---|---|---|---|---|---|")
    n = 0
    for tipo, sigla, nome, urls in FONTES:
        vistos = set()
        for url in urls:
            regra, msg, mapas = robots(url)
            if regra and not regra.can_fetch(AGENTE, url):
                log(f"| {tipo} | {nome} | {url[:60]} | NÃO LIDO | proíbe | | | | | |")
                resumo.append([tipo, sigla, nome, url, "proibido pelo robots.txt", "", "", "", "", "", ""])
                continue
            st, final, txt, ctype = baixar(url)
            n += 1
            guardar(n, sigla, final, st, txt)
            c, pi = contar(txt), pistas(txt)
            log(f"| {tipo} | {nome} | {final[:60]} | {st} | {msg}{' + sitemap' if mapas else ''} | {c['precos']} | {c['lotes']} | {c['editais']} | {c['pdf']} | {'; '.join(pi)} |")
            resumo.append([tipo, sigla, nome, final, st, msg, c["precos"], c["lotes"], c["editais"], c["pdf"], "; ".join(pi)])
            if st != 200:
                continue
            base = up.urlparse(final).netloc
            links = []
            for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', txt, re.S | re.I):
                href, rot = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
                alvo = up.urljoin(final, href)
                if up.urlparse(alvo).netloc.endswith(base.replace("www.", "")) and RE_LINK_UTIL.search(href + " " + rot) \
                        and alvo not in links and alvo not in vistos and not alvo.lower().endswith(".pdf"):
                    links.append(alvo)
            for alvo in links[:6]:
                vistos.add(alvo)
                if regra and not regra.can_fetch(AGENTE, alvo):
                    log(f"|  | ↳ | {alvo[:60]} | NÃO LIDO | proíbe | | | | | |")
                    resumo.append([tipo, sigla, nome, alvo, "proibido pelo robots.txt", "", "", "", "", "", ""])
                    continue
                time.sleep(2)
                st2, final2, txt2, _ = baixar(alvo)
                n += 1
                guardar(n, sigla + "_sub", final2, st2, txt2)
                c2, p2 = contar(txt2), pistas(txt2)
                log(f"|  | ↳ | {final2[:60]} | {st2} | | {c2['precos']} | {c2['lotes']} | {c2['editais']} | {c2['pdf']} | {'; '.join(p2)} |")
                resumo.append([tipo, sigla, nome, final2, st2, "", c2["precos"], c2["lotes"], c2["editais"], c2["pdf"], "; ".join(p2)])
            for mapa in mapas[:2]:                       # o sitemap mostra se há páginas de imóvel individuais
                if regra and not regra.can_fetch(AGENTE, mapa):
                    continue
                st3, final3, txt3, _ = baixar(mapa)
                n += 1
                guardar(n, sigla + "_sitemap", final3, st3, txt3[:300000])
                urls_im = len(re.findall(r"<loc>[^<]*(im[oó]vel|imoveis|leilao|lote)[^<]*</loc>", txt3, re.I))
                log(f"|  | ↳ sitemap | {final3[:60]} | {st3} | | | | | | {urls_im} endereço(s) de imóvel/lote |")
            time.sleep(2)
    with open(os.path.join(PASTA, "resumo.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["tipo", "sigla", "instituicao", "pagina", "situacao", "robots", "precos_RS", "lotes", "editais", "pdfs", "pistas"])
        w.writerows(resumo)
    log("")
    log(f"{n} página(s) guardada(s). Baixe o artefato 'amostras-bancos-leiloeiros' no fim da página desta execução"
        " e envie o .zip na conversa de Leilões.")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Leilões extrajudiciais — reconhecimento de bancos e leiloeiros\n\n" + "\n".join(relatorio) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
