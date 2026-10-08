"""
SOLIDUNS — AGENTE DE LEILÕES — PRÉ-ANÁLISE DA MATRÍCULA — v1.0 (07/10/2026)

ETAPA 2 da pré-análise (aprovada pelo Thiago em 07/10; CUSTO ZERO, sem IA paga).
Rotina "Leilões - pré-análise" (leiloes-preanalise.yml), todo dia. Grava em
public.leiloes_preanalise (SQL 108). Só o administrador vê (tela Analisar).

PARA CADA IMÓVEL DA CAIXA NA FILA DE TRIAGEM (pendente ou em análise; primeiro
os "em análise", depois a maior pré-nota):
  1. abre a página do imóvel na Caixa: nº da matrícula/cartório, regras de quem
     paga condomínio e tributos, ocupação (se a página disser) e o endereço do
     PDF da matrícula;
  2. baixa o PDF da matrícula (pela página ou pelo caminho direto
     /editais/matricula/<UF>/<nº>.pdf);
  3. lê o PDF (texto; se for imagem escaneada, Tesseract em português) e aplica
     as regras de ler_matricula.py: atos, ônus ativos/cancelados/consolidados;
  4. grava a pré-análise + uma SUGESTÃO para o checklist (o administrador
     confere e confirma; nada é publicado sozinho).
Releitura: a cada 30 dias; com erro, tenta de novo no dia seguinte.
Limites: até LIMITE imóveis por execução (padrão 80) e TEMPO_MAX segundos.
Regras do projeto: identifica-se como robô, respeita o robots.txt, sem captcha,
sem login, pausa entre consultas. Nenhuma chave no código (só Secrets).

USO
  python coletor/preanalisar_leiloes.py                (rotina)
  python coletor/preanalisar_leiloes.py --id caixa-8787714199234 --sem-gravar
Variáveis: SUPABASE_URL e SUPABASE_SERVICE_KEY (as mesmas das outras rotinas).
"""

import argparse
import datetime as dt
import html as htmlmod
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
CAIXA = os.environ.get("CAIXA_BASE_TESTE") or "https://venda-imoveis.caixa.gov.br"
PAUSA = float(os.environ.get("PAUSA_CAIXA", "2"))
LIMITE = int(os.environ.get("PREANALISE_LIMITE", "80"))
TEMPO_MAX = int(os.environ.get("PREANALISE_TEMPO_MAX", "2400"))
RELER_DIAS = 30
RE_PDF = re.compile(r"""["'(]\s*((?:https?://[^"'()\s]+)?/?[^"'()\s]*?\.pdf)\s*["')]""", re.I)

relatorio = []
s = requests.Session()
s.headers.update({"User-Agent": UA})
_rp = None


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    relatorio.append(msg)


# ------------------------------------------------------------ Supabase
def sb():
    url, chave = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not chave:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_KEY (Secrets do GitHub).")
    cab = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        cab["Authorization"] = f"Bearer {chave}"
    return url.rstrip("/"), cab


def sb_get(caminho):
    url, cab = sb()
    r = requests.get(f"{url}/rest/v1/{caminho}", headers=cab, timeout=90)
    if r.status_code == 404 or (r.status_code == 400 and "leiloes_preanalise" in r.text):
        raise RuntimeError("A tabela leiloes_preanalise não existe: rode o SQL 108.")
    r.raise_for_status()
    return r.json()


def sb_upsert(linha):
    url, cab = sb()
    r = requests.post(f"{url}/rest/v1/leiloes_preanalise?on_conflict=id", json=[linha],
                      headers=dict(cab, Prefer="resolution=merge-duplicates,return=minimal"), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"gravação recusada: {r.status_code} {r.text[:200]}")


def candidatos(so_id=None):
    filtro = f"&id=eq.{so_id}" if so_id else "&status=in.(pendente,em_analise)"
    fila = []
    passo = 1000
    for ini in range(0, 20000, passo):
        bloco = sb_get("leiloes_triagem?select=id,status,pre_nota,leiloes_radar!inner(numero,uf,fonte,situacao,link)"
                       f"&leiloes_radar.fonte=eq.caixa&leiloes_radar.situacao=eq.ativo{filtro}"
                       f"&order=pre_nota.desc.nullslast,id&offset={ini}&limit={passo}")
        fila += bloco
        if len(bloco) < passo:
            break
    feitos = {}
    for ini in range(0, 50000, passo):
        bloco = sb_get(f"leiloes_preanalise?select=id,lido_em,erro&order=id&offset={ini}&limit={passo}")
        feitos.update({b["id"]: b for b in bloco})
        if len(bloco) < passo:
            break
    hoje = dt.datetime.now(dt.timezone.utc)
    pend = []
    for f in fila:
        ja = feitos.get(f["id"])
        if ja and not so_id:
            lido = dt.datetime.fromisoformat(ja["lido_em"].replace("Z", "+00:00"))
            if ja.get("erro"):
                if (hoje - lido).total_seconds() < 20 * 3600:
                    continue          # errou há menos de 20 h: tenta na execução de amanhã
            elif (hoje - lido).days < RELER_DIAS:
                continue
        pend.append(f)
    # "em análise" primeiro; depois a maior pré-nota (a consulta já veio ordenada por pré-nota)
    pend.sort(key=lambda f: 0 if f["status"] == "em_analise" else 1)
    return pend, len(fila)


# ------------------------------------------------------------ Caixa
def permitido(url):
    global _rp
    if _rp is None:
        _rp = robotparser.RobotFileParser()
        try:
            r = s.get(CAIXA + "/robots.txt", timeout=40)
            _rp.parse(r.text.splitlines() if r.status_code < 400 else [])
        except requests.RequestException:
            _rp.parse([])
    return _rp.can_fetch(UA, url)


def pegar(url):
    if not permitido(url):
        raise RuntimeError("robots.txt da Caixa não permite")
    time.sleep(PAUSA)
    for tentativa in range(2):
        try:
            return s.get(url, timeout=60)
        except requests.RequestException as e:
            if tentativa:
                raise RuntimeError(f"sem conexão ({type(e).__name__})")
            time.sleep(10)


def decodificar(r):
    """Texto da página com a codificação certa (o cabeçalho às vezes não diz)."""
    cs = re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""), re.I)
    if not cs:
        cs = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", r.content[:3000], re.I)
        cs = cs and cs.group(1).decode("ascii", "ignore")
    else:
        cs = cs.group(1)
    for cod in [c for c in (cs, "utf-8", "latin-1") if c]:
        try:
            return r.content.decode(cod)
        except (UnicodeDecodeError, LookupError):
            continue
    return r.content.decode("latin-1", "ignore")


def texto_html(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", t, flags=re.I)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\r\f\v]+", " ", t)


def _depois(rotulo, t, n=220):
    m = re.search(rotulo + r"\s*:?\s*(.{3,%d}?)(?:\n|$)" % n, t, re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def ler_pagina(link):
    """Devolve dict com matricula_num, condominio, tributos, ocupacao, pdfs[] (ou bloqueio)."""
    info = {"matricula_num": None, "condominio": None, "tributos": None, "ocupacao": "nao_informado",
            "pdfs": [], "bloqueio": False}
    if not link:
        return info
    r = pegar(link)
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return info
    pagina = decodificar(r)
    if re.search(r"captcha|radware|are you a robot|access denied", pagina[:5000], re.I):
        info["bloqueio"] = True
        return info
    t = texto_html(pagina)
    info["matricula_num"] = _depois(r"Matr[íi]cula(?:\(s\))?\s*:", t, 160)
    info["condominio"] = _depois(r"Condom[íi]nio", t)
    info["tributos"] = _depois(r"Tributos", t)
    tl = ler_matricula._sa(t)
    if re.search(r"\bdesocupad", tl):
        info["ocupacao"] = "desocupado"
    elif re.search(r"imovel\s+ocupad|\bocupado\b", tl):
        info["ocupacao"] = "ocupado"
    info["pdfs"] = [urljoin(link, a) for a in sorted({m.group(1) for m in RE_PDF.finditer(pagina)})
                    if "matric" in a.lower()]
    return info


def sugestao(res, pag):
    sug = {}
    if res["atos"] >= 2 and res["qualidade"] >= 0.15 and not [o for o in res["onus"] if o["situacao"] == "ativo"]:
        sug["onus"] = "nenhum"
    regras = ler_matricula._sa(" ".join(x for x in (pag.get("condominio"), pag.get("tributos")) if x))
    if "comprador" in regras or "arrematante" in regras or "adquirente" in regras:
        sug["debitos"] = "comprador"
    elif regras and ("vendedor" in regras or "caixa" in regras):
        sug["debitos"] = "vendedor"
    if pag.get("ocupacao") in ("ocupado", "desocupado"):
        sug["ocupacao"] = pag["ocupacao"]
    return sug


def preanalisar(item):
    rad = item["leiloes_radar"]
    uf, num = rad["uf"], rad["numero"]
    linha = {"id": item["id"], "origem": "matricula", "doc_url": None, "metodo": None, "paginas": None,
             "qualidade": None, "matricula_num": None, "atos": [], "onus": [], "onus_ativos": 0,
             "consolidacao": None, "condominio": None, "tributos": None, "ocupacao": "nao_informado",
             "resumo": None, "formato": [], "sugestao": {}, "versao": f"{VERSAO}/{ler_matricula.VERSAO}",
             "lido_em": dt.datetime.now(dt.timezone.utc).isoformat(), "erro": None}
    pag = ler_pagina(rad.get("link"))
    for k in ("matricula_num", "condominio", "tributos", "ocupacao"):
        linha[k] = pag[k]
    urls = pag["pdfs"] + [u for u in [f"{CAIXA}/editais/matricula/{uf}/{num}.pdf"] if u not in pag["pdfs"]]
    ultimo_erro = "página bloqueada por proteção contra robôs" if pag["bloqueio"] else "matrícula não encontrada"
    for url in urls[:3]:
        r = pegar(url)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            ultimo_erro = f"matrícula não disponível (HTTP {r.status_code})"
            continue
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(r.content)
            cam = f.name
        try:
            texto, metodo, pags = ler_matricula.texto_do_pdf(cam)
        finally:
            os.unlink(cam)
        res = ler_matricula.analisar(texto)
        linha.update({"doc_url": url, "metodo": metodo, "paginas": pags, "qualidade": res["qualidade"],
                      "atos": res["lista_atos"], "onus": res["onus"],
                      "onus_ativos": sum(1 for o in res["onus"] if o["situacao"] == "ativo"),
                      "consolidacao": res["consolidacao"], "resumo": res["resumo"], "sugestao": sugestao(res, pag),
                      "formato": ler_matricula.formato_mascarado(texto, 15) if res["atos"] < 3 else []})
        return linha
    linha["erro"] = ultimo_erro
    linha["sugestao"] = sugestao({"atos": 0, "qualidade": 0, "onus": []}, pag)
    return linha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--sem-gravar", action="store_true")
    a = ap.parse_args()
    inicio = time.time()
    log(f"SOLIDUNS — pré-análise de matrículas v{VERSAO} (leitor v{ler_matricula.VERSAO}) — {dt.date.today():%d/%m/%Y}")
    pend, total = candidatos(a.id)
    log(f"Fila da Caixa (pendente/em análise): {total} | a ler agora: {min(len(pend), LIMITE)} de {len(pend)}")
    c = {"lidos": 0, "com_onus": 0, "erros": 0, "ocr": 0, "bloqueios": 0}
    seguidos = 0
    for item in pend[:LIMITE]:
        if time.time() - inicio > TEMPO_MAX:
            log(f"Teto de tempo ({TEMPO_MAX // 60} min) — o restante fica para a próxima execução.")
            break
        try:
            linha = preanalisar(item)
        except Exception as e:
            linha = None
            log(f"  {item['id']}: erro {type(e).__name__}: {str(e)[:120]}")
            c["erros"] += 1
            seguidos += 1
        if linha:
            if linha["erro"]:
                c["erros"] += 1
                seguidos += 1
                c["bloqueios"] += 1 if "proteção" in linha["erro"] else 0
                log(f"  {item['id']}: {linha['erro']}")
            else:
                seguidos = 0
                c["lidos"] += 1
                c["ocr"] += 1 if linha["metodo"] == "ocr" else 0
                c["com_onus"] += 1 if linha["onus_ativos"] else 0
                log(f"  {item['id']}: {linha['paginas']} pág. ({linha['metodo']}) | {len(linha['atos'])} atos | "
                    f"{linha['onus_ativos']} ônus ativo(s)")
            if not a.sem_gravar:
                sb_upsert(linha)
        if seguidos >= 10:
            log("10 falhas seguidas — a Caixa pode estar fora do ar ou bloqueando. Parando por hoje.")
            break
    log(f"Resultado: {c['lidos']} matrícula(s) lida(s) ({c['ocr']} por imagem), {c['com_onus']} com ônus ativo, "
        f"{c['erros']} sem matrícula/erro, {c['bloqueios']} bloqueio(s) | {time.time() - inicio:.0f} s")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Pré-análise de matrículas\n\n```\n" + "\n".join(relatorio[-60:]) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
