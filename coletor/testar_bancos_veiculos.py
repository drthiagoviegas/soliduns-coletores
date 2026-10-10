"""
SOLIDUNS — LEILÃO DE AUTOMÓVEIS — Reconhecimento: VEÍCULOS RETOMADOS DE BANCOS — v1.0 (10/10/2026)

Rotina MANUAL ("Veículos - teste VIP e Sodré"). Não grava nada no banco e não usa chave nenhuma.
Objetivo: descobrir COMO as listas de lotes de dois leiloeiros que vendem carros retomados por financiamento
(alienação fiduciária) chegam ao navegador, para escrever o leitor na versão seguinte:
  - VIP Leilões (vipleiloes.com.br): "Recuperados de financiamento" (Banco PAN, BV, Omni, Creditas...).
    A lista vem de um formulário (#formPost) que a própria página envia; o teste repete o que o navegador faz
    (abre a página, lê o formulário e o envia uma vez, com o mesmo cookie) e guarda as respostas.
  - Sodré Santoro (sodresantoro.com.br): veículos (Bradesco, Santander...). A lista vem da API do próprio site
    (prd-api.sodresantoro.com.br); o teste lê os scripts públicos da página para achar os endereços usados e
    abre só os de LEITURA (GET) que aparecerem, respeitando o robots.txt do endereço.
Regras: respeita o robots.txt; robô identificado; 2 s entre pedidos; sem login, sem CAPTCHA; no máximo ~40 pedidos.
Saída: pasta amostras_bancos_veiculos/ (artefato da rotina) + resumo no Summary.
"""

import json
import os
import re
import time
import urllib.parse as up
import urllib.robotparser as rp

import requests

AGENTE = "SOLIDUNS-coletor/1.0 (+contato@soliduns.com.br)"
PASTA = os.environ.get("AMOSTRAS") or "amostras_bancos_veiculos"
ESPERA = 0 if os.environ.get("LEILOES_TESTE") else 2
relatorio = []
_robots = {}
_n = [0]


def log(m=""):
    print(m, flush=True)
    relatorio.append(m)


def permitido(url):
    p = up.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        r = rp.RobotFileParser()
        try:
            resp = requests.get(base + "/robots.txt", headers={"User-Agent": AGENTE}, timeout=30)
            r.parse(resp.text.splitlines() if resp.status_code < 400 else [])
            log(f"  robots.txt de {p.netloc}: {resp.status_code}")
        except requests.RequestException as e:
            r.parse([])
            log(f"  robots.txt de {p.netloc}: não respondeu ({type(e).__name__})")
        _robots[base] = r
    return _robots[base].can_fetch(AGENTE, url)


def guardar(nome, url, status, texto):
    os.makedirs(PASTA, exist_ok=True)
    with open(os.path.join(PASTA, nome), "w", encoding="utf-8") as f:
        f.write(f"<!-- {url} | status {status} -->\n{texto}")


def pedir(sess, metodo, url, nome, **kw):
    if _n[0] >= 40:
        log("  teto de 40 pedidos atingido")
        return None
    if not permitido(url):
        log(f"  robots.txt proíbe {url} — não aberto")
        return None
    _n[0] += 1
    time.sleep(ESPERA)
    try:
        r = sess.request(metodo, url, timeout=45, **kw)
    except requests.RequestException as e:
        log(f"  {metodo} {url}: ERRO {type(e).__name__}")
        return None
    texto = r.text
    guardar(nome, url, r.status_code, texto)
    log(f"  {metodo} {url} -> {r.status_code}, {len(texto)} letras, tipo {r.headers.get('Content-Type', '')[:40]} -> {nome}")
    return r


def campos_formulario(html, ident="formPost"):
    m = re.search(r'<form[^>]*id="' + ident + r'"[^>]*>(.*?)</form>', html, re.S | re.I)
    if not m:
        return None, None, {}
    tag = re.search(r'<form[^>]*id="' + ident + r'"[^>]*>', html, re.I).group(0)
    acao = (re.search(r'action="([^"]*)"', tag) or [None, ""])[1]
    met = (re.search(r'method="([^"]*)"', tag) or [None, "post"])[1]
    dados = {}
    for inp in re.finditer(r"<input[^>]*>", m.group(1), re.I):
        t = inp.group(0)
        nome = re.search(r'name="([^"]+)"', t)
        if not nome:
            continue
        tipo = (re.search(r'type="([^"]+)"', t) or [None, "text"])[1].lower()
        valor = (re.search(r'value="([^"]*)"', t) or [None, ""])[1]
        if tipo in ("checkbox", "radio") and "checked" not in t.lower():
            continue
        dados[nome.group(1)] = valor
    for sel in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', m.group(1), re.S | re.I):
        op = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"|<option[^>]*value="([^"]*)"[^>]*selected', sel.group(2), re.I)
        dados[sel.group(1)] = (op.group(1) or op.group(2)) if op else ""
    return acao, met.lower(), dados


def vip():
    log("== VIP Leilões")
    s = requests.Session()
    s.headers.update({"User-Agent": AGENTE, "Accept-Language": "pt-BR"})
    base = "https://www.vipleiloes.com.br"
    r = pedir(s, "GET", base + "/pesquisa/recuperadofinanciamento", "vip_01_pesquisa_recuperado.html")
    if r is None:
        return
    acao, met, dados = campos_formulario(r.text)
    log(f"  formulário #formPost: ação '{acao}', método {met}, {len(dados)} campo(s): {sorted(dados)[:25]}")
    guardar("vip_02_formulario.json", "formPost", 200, json.dumps({"acao": acao, "metodo": met, "campos": dados}, ensure_ascii=False, indent=1))
    if acao is not None:
        destino = up.urljoin(r.url, acao or r.url)
        if met == "get":
            pedir(s, "GET", destino, "vip_03_resposta_form.html", params=dados)
        else:
            pedir(s, "POST", destino, "vip_03_resposta_form.html", data=dados,
                  headers={"Referer": r.url, "X-Requested-With": "XMLHttpRequest"})
            pedir(s, "POST", destino, "vip_04_resposta_form_sem_ajax.html", data=dados, headers={"Referer": r.url})
    for i, js in enumerate(sorted(set(re.findall(r'<script[^>]+src="(/js/[^"]+)"', r.text)))[:8]):
        rj = pedir(s, "GET", base + js, f"vip_js_{i:02d}.js")
        if rj is not None:
            achados = sorted(set(re.findall(r'["\'](/[A-Za-z][\w/-]{3,80}(?:\?[^"\']{0,80})?)["\']', rj.text)))
            log(f"    rotas no script {js.split('?')[0]}: {achados[:30]}")
    pedir(s, "GET", base + "/pesquisa?classificacao=Usados", "vip_05_pesquisa_usados.html")


def sodre():
    log("== Sodré Santoro")
    s = requests.Session()
    s.headers.update({"User-Agent": AGENTE, "Accept-Language": "pt-BR"})
    base = "https://www.sodresantoro.com.br"
    r = pedir(s, "GET", base + "/veiculos/lotes?sort=auction_date_init_asc", "sodre_01_veiculos_lotes.html")
    if r is None:
        return
    rotas = set()
    scripts = sorted(set(re.findall(r'(/_nuxt/[A-Za-z0-9_.-]+\.js)', r.text)))
    log(f"  {len(scripts)} script(s) na página")
    trechos = []
    for i, js in enumerate(scripts[:24]):
        rj = pedir(s, "GET", base + js, f"sodre_js_{i:02d}.js")
        if rj is None:
            continue
        for m in re.finditer(r'(?:prd-api\.sodresantoro\.com\.br)?(/api/[\w/\-{}$.]+|/v\d/[\w/\-{}$.]+|/lots?[\w/\-{}$.]*|/search[\w/\-{}$.]*)', rj.text):
            rotas.add(m.group(1))
        for m in re.finditer(r'.{0,160}prd-api.{0,240}|.{0,120}(?:/lots|/search)\b.{0,200}', rj.text):
            trechos.append(js + ": " + m.group(0))
    log(f"  rotas achadas nos scripts: {sorted(rotas)[:60]}")
    guardar("sodre_02_trechos_api.txt", "scripts", 200, "\n\n".join(trechos[:300]))
    api = "https://prd-api.sodresantoro.com.br"
    tentados = 0
    for rota in sorted(rotas):
        if tentados >= 6 or "{" in rota or "$" in rota:
            continue
        if not re.search(r"lot|search|vehic|veicul", rota, re.I):
            continue
        tentados += 1
        pedir(s, "GET", api + rota, f"sodre_api_{tentados:02d}.json", headers={"Accept": "application/json", "Origin": base, "Referer": r.url})


def main():
    log("SOLIDUNS — teste VIP Leilões e Sodré Santoro (veículos retomados de bancos) v1.0")
    for f in (vip, sodre):
        try:
            f()
        except Exception as e:  # noqa: BLE001 — um site nunca derruba o outro
            log(f"  ERRO {type(e).__name__}: {str(e)[:200]}")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Teste VIP e Sodré (veículos de bancos)\n\n```\n" + "\n".join(relatorio) + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
