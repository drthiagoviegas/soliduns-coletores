"""
SOLIDUNS — AGENTE DE LEILÕES — Acompanhamento dos PROCESSOS dos leilões judiciais — v1.4 (07/10/2026)

v1.4: "Baixa Definitiva" só vira alerta de EXTINÇÃO quando acontece na 1ª instância (vara: grau G1/JE). Vinda do
tribunal (G2) ou de tribunal superior, é só o RETORNO do recurso à vara e não ameaça o leilão (caso real do TRT17,
São Mateus/ES, 07/10). "Extinção" e "arquivamento definitivo" continuam alertando em qualquer instância.

v1.3 (1ª execução da v1.2: ~40 s por processo no Datajud; só 28 de 204 em 20 min): (1) pede ao Datajud SÓ os
campos usados (classe, órgão e movimentações) — resposta menor; (2) PRIORIDADE e RODÍZIO: entram na fila os
nunca consultados / com erro e os com PRAÇA nos próximos 15 dias (todo dia); os demais, a cada 3 dias — os de
praça mais próxima primeiro; (3) roda numa ROTINA PRÓPRIA (.github/workflows/leiloes-processos.yml), 4 vezes
por dia (10h, 14h, 18h, 22h de Brasília), até 50 min cada — custo zero (repositório público).

v1.2 (diagnóstico pelos erros gravados em 07/10): o Datajud NÃO bloqueia o GitHub — o acesso direto funcionou
(TJSP, TJCE, TRT10/12/15/18 ok). As falhas eram LENTIDÃO ("Read timed out" nos tribunais grandes) e LIMITE DE
VELOCIDADE ("429 Too Many Requests", agravado por duas execuções simultâneas). Agora: espera até 60 s por consulta;
2 s entre consultas; no 429 aguarda (Retry-After ou 30/60/90 s) e tenta de novo; processo que falhar NÃO para o
robô — fica para a repetição no fim da execução e, se ainda falhar, para a próxima; a função de São Paulo só é
usada em BLOQUEIO de verdade (403 ou conexão recusada), não em lentidão; reconsulta no mesmo dia os que ficaram
com erro; para só se o serviço cair (12 falhas seguidas) ou no teto de 20 min.

v1.1: a 1ª execução real (07/10) travou: o Datajud não respondia a partir do GitHub (EUA) e o robô esperava cada
processo até o limite, estourando a hora da rotina. Agora: (1) espera no máximo 20 s por consulta; (2) depois de 3
falhas seguidas no acesso direto, passa a consultar PELA FUNÇÃO DE SÃO PAULO (swift-api v1.3, rota ?datajud=1,
protegida pelo DJEN_TOKEN — a mesma do DJEN); (3) teto de 20 minutos por execução (o que faltar fica para o dia
seguinte); (4) sem acesso nem pela função: avisa e termina sem travar.

Todo dia, depois dos coletores, consulta na API PÚBLICA e GRATUITA do Datajud (CNJ) os processos dos
leilões judiciais ATIVOS no Radar e grava as últimas movimentações em public.leiloes_processos (SQL 105).
Marca ALERTA quando, DEPOIS da publicação do edital, aparece movimentação que ameaça o leilão:
cancelamento/sustação, suspensão, acordo, remição, extinção, embargos, adjudicação ou arrematação.
Custo zero, sem IA (regras fixas sobre os nomes oficiais das movimentações — Tabela Processual Unificada).

Segredos (GitHub > Settings > Secrets, repositório PÚBLICO soliduns-coletores — NUNCA no código):
  SUPABASE_URL, SUPABASE_SERVICE_KEY  (já existem)
  DATAJUD_API_KEY  — a CHAVE PÚBLICA divulgada pelo CNJ em https://datajud-wiki.cnj.jus.br/api-publica/acesso
                     (só o texto depois de "APIKey "). O CNJ pode trocá-la: se o robô avisar "chave recusada",
                     copie a vigente da wiki e atualize o segredo.
Sem DATAJUD_API_KEY o robô só avisa e termina sem erro.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata

import requests

VERSAO = "1.4"
UA = "SOLIDUNS-coletor/1.0 (+https://soliduns.com.br; contato@soliduns.com.br)"
DATAJUD = os.environ.get("DATAJUD_URL_TESTE") or "https://api-publica.datajud.cnj.jus.br/api_publica_{trib}/_search"  # a variável só existe nos testes
PAUSA = float(os.environ.get("DATAJUD_PAUSA_TESTE") or 2.0)                      # segundos entre consultas (gentil com o serviço público)
MAX_PROCESSOS = 1500             # teto de segurança por execução
TEMPO_MAX = float(os.environ.get("DATAJUD_TEMPO_MAX") or 50 * 60)   # segundos por execução (rotina própria, 4x/dia)
DIAS_PRACA_PERTO = 15            # praça até 15 dias à frente: reconsulta todo dia
DIAS_RODIZIO = 3                 # demais: reconsulta a cada 3 dias
CAMPOS = ["grau", "classe.nome", "orgaoJulgador.nome", "movimentos.nome", "movimentos.dataHora",
          "movimentos.complementosTabelados.nome", "movimentos.complementosTabelados.descricao"]   # só o que o robô usa
ESPERA = float(os.environ.get("DATAJUD_ESPERA_TESTE") or 60)   # segundos máximos por consulta (o Datajud é lento nos grandes)
ESPERA_429 = [float(x) for x in (os.environ.get("DATAJUD_429_TESTE") or "30,60,90").split(",")]   # espera após "devagar"
RELAY = os.environ.get("DJEN_RELAY_URL") or "https://evcsniicnrlrtzpdmeza.supabase.co/functions/v1/swift-api"
JANELA_ANTES_DO_EDITAL = 10      # dias: movimentação até 10 dias antes da publicação ainda conta

# Ordem = gravidade (o primeiro achado vira o alerta principal)
ALERTAS = [
    ("cancelamento", r"leil[aã]o (cancelad|sustad|suspens|adiad)|hasta (publica )?(cancelad|sustad|suspens|adiad)|"
                     r"cancelamento (do |da )?(leil|hasta|praca)|sustacao|susta[cç][aã]o"),
    ("suspensao",    r"suspens|sobrest"),
    ("acordo",       r"\bacordo\b|transa[cç][aã]o|concilia[cç][aã]o realizada|homologa[cç][aã]o de (acordo|transa)"),
    ("remicao",      r"remi[cç][aã]o|remi[cç][aã]o da execu"),
    ("extincao",     r"extin[cç][aã]o|arquivamento definitivo|baixa definitiva"),
    ("embargos",     r"embargos"),
    ("adjudicacao",  r"adjudica"),
    ("arrematacao",  r"arremata"),
]
ROTULO = {"cancelamento": "leilão cancelado/sustado", "suspensao": "suspensão", "acordo": "acordo",
          "remicao": "remição", "extincao": "extinção/arquivamento", "embargos": "embargos",
          "adjudicacao": "adjudicação", "arrematacao": "arrematação"}


def sa(t):
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def so_digitos(p):
    d = re.sub(r"\D", "", p or "")
    return d if len(d) == 20 else None


def texto_mov(m):
    partes = [m.get("nome") or ""]
    for c in (m.get("complementosTabelados") or []):
        partes.append(str(c.get("nome") or ""))
        partes.append(str(c.get("descricao") or ""))
    return sa(" ".join(partes))


def ler_resposta(dados):
    """Junta as movimentações de todos os registros (graus) do processo, da mais nova à mais antiga."""
    hits = ((dados or {}).get("hits") or {}).get("hits") or []
    if not hits:
        return None
    movs, classe, orgao = [], None, None
    for h in hits:
        s = h.get("_source") or {}
        classe = classe or (s.get("classe") or {}).get("nome")
        orgao = orgao or (s.get("orgaoJulgador") or {}).get("nome")
        grau = (s.get("grau") or "").upper()
        for m in (s.get("movimentos") or []):
            if m.get("dataHora"):
                movs.append(dict(m, _grau=grau))
    vistos, unicos = set(), []
    for m in sorted(movs, key=lambda x: x.get("dataHora") or "", reverse=True):
        k = (m.get("dataHora"), m.get("nome"))
        if k in vistos:
            continue
        vistos.add(k)
        unicos.append(m)
    return {"classe": classe, "orgao": orgao, "movimentos": unicos}


def classificar(movs, desde):
    """Alertas das movimentações a partir de 'desde' (data ISO). Retorna (lista, principal, mov, data)."""
    achados = {}
    for m in movs:
        if (m.get("dataHora") or "")[:10] < desde:
            continue
        t = texto_mov(m)
        for tipo, rx in ALERTAS:
            if re.search(rx, t) and tipo not in achados:
                if (tipo == "extincao" and "baixa definitiva" in t and not re.search(r"extin|arquivamento definitivo", t)
                        and m.get("_grau") not in ("G1", "JE", "")):
                    continue                                    # baixa vinda do tribunal = retorno do recurso à vara
                achados[tipo] = m
    if not achados:
        return [], None, None, None
    tipos = [t for t, _ in ALERTAS if t in achados]
    p = tipos[0]
    return tipos, p, achados[p].get("nome"), achados[p].get("dataHora")


def sb(metodo, caminho, **kw):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + caminho
    k = os.environ["SUPABASE_SERVICE_KEY"]
    h = {"apikey": k, "Authorization": "Bearer " + k, "Content-Type": "application/json"}
    h.update(kw.pop("headers", {}))
    r = requests.request(metodo, url, headers=h, timeout=60, **kw)
    r.raise_for_status()
    return r.json() if r.text else None


def processos_do_radar():
    """Processos dos leilões judiciais ativos: {digitos: (tribunal, data de referência, praça mais próxima)}."""
    out, off = {}, 0
    while True:
        lote = sb("GET", "leiloes_radar?select=processo,tribunal,data_lista,primeira_vez,praca1,praca2&fonte=eq.djen&situacao=eq.ativo"
                         "&order=id.asc&limit=1000&offset=" + str(off))
        for r in lote:
            d = so_digitos(r.get("processo"))
            if not d or not r.get("tribunal"):
                continue
            ref = (r.get("data_lista") or (r.get("primeira_vez") or "")[:10] or dt.date.today().isoformat())[:10]
            hoje = dt.date.today().isoformat()
            pracas = sorted(p[:10] for p in (r.get("praca1"), r.get("praca2")) if p and p[:10] >= hoje)
            praca = pracas[0] if pracas else None
            ant = out.get(d)
            if not ant:
                out[d] = (r["tribunal"], ref, praca)
            else:
                out[d] = (ant[0], min(ant[1], ref), min([x for x in (ant[2], praca) if x], default=None))
        if len(lote) < 1000:
            return out
        off += 1000


def main():
    agora = dt.datetime.now(dt.timezone.utc)
    print(f"SOLIDUNS — Acompanhamento dos processos (Datajud/CNJ) v{VERSAO} — {agora:%d/%m/%Y %H:%M} (UTC do servidor)")
    chave = (os.environ.get("DATAJUD_API_KEY") or "").strip()
    if chave.lower().startswith("apikey "):
        chave = chave[7:].strip()
    if not chave:
        print("  AVISO: segredo DATAJUD_API_KEY não cadastrado — nada consultado. "
              "Copie a chave pública vigente em https://datajud-wiki.cnj.jus.br/api-publica/acesso")
        return 0
    procs = processos_do_radar()
    hoje = agora.date().isoformat()
    ja = {p["processo"]: p for p in sb("GET", "leiloes_processos?select=processo,consultado_em,erro,encontrado&limit=5000")}
    perto = (agora.date() + dt.timedelta(days=DIAS_PRACA_PERTO)).isoformat()
    rodizio = (agora.date() - dt.timedelta(days=DIAS_RODIZIO - 1)).isoformat()
    fila = []
    for d, (t, ref, praca) in procs.items():
        j = ja.get(d)
        ult = ((j or {}).get("consultado_em") or "")[:10]
        if j and not j.get("erro") and ult == hoje:
            continue                                            # já deu certo hoje
        nunca_ou_erro = (not j) or bool(j.get("erro"))
        praca_perto = bool(praca and praca <= perto)
        if not (nunca_ou_erro or praca_perto or ult < rodizio):
            continue                                            # fica para o rodízio
        prioridade = 0 if nunca_ou_erro else (1 if praca_perto else 2)
        fila.append((prioridade, praca or "9999-12-31", d, t, ref))
    fila = [(d, t, ref) for _, _, d, t, ref in sorted(fila)][:MAX_PROCESSOS]
    print(f"  processos de leilões judiciais ativos: {len(procs)} | a consultar hoje: {len(fila)}")
    ses = requests.Session()
    ses.headers.update({"Authorization": "APIKey " + chave, "Content-Type": "application/json", "User-Agent": UA})
    token = (os.environ.get("DJEN_TOKEN") or "").strip()
    estado = {"modo": "direto", "falhas_seguidas": 0, "bloqueios": 0}
    t0 = time.time()

    def consultar(d, trib):
        """(status, json|None). Rede: lança exceção. 429: espera e repete (até 3 vezes)."""
        for n429 in range(len(ESPERA_429) + 1):
            if estado["modo"] == "direto":
                r = ses.post(DATAJUD.format(trib=trib.lower()), data=json.dumps({"query": {"match": {"numeroProcesso": d}}, "size": 10, "_source": CAMPOS}),
                             timeout=(15, ESPERA))
            else:
                r = requests.get(RELAY, params={"forceFunctionRegion": "sa-east-1", "datajud": "1", "tribunal": trib, "processo": d},
                                 headers={"x-soliduns-token": token, "x-datajud-key": chave, "User-Agent": UA}, timeout=(15, ESPERA + 15))
                if r.status_code == 401 and "nao_autorizado" in r.text:
                    raise PermissionError("a função de São Paulo recusou o DJEN_TOKEN")
                if r.status_code in (502, 504):
                    raise TimeoutError("o Datajud não respondeu a tempo (pela função de São Paulo)")
            if r.status_code == 429 and n429 < len(ESPERA_429):
                espera = ESPERA_429[n429]
                try:
                    espera = max(espera, float(r.headers.get("Retry-After") or 0))
                except ValueError:
                    pass
                print(f"  o Datajud pediu para ir mais devagar (429) — aguardando {espera:.0f} s")
                time.sleep(espera)
                continue
            return r.status_code, (r.json() if r.status_code == 200 else None)
        return 429, None

    cont = {"consultados": 0, "encontrados": 0, "nao_encontrados": 0, "erros": 0, "repetidos": 0}
    por_alerta, linhas = {}, []
    falhou_1a_vez, ja_repetidos = [], set()

    def gravar(lote):
        if lote:
            sb("POST", "leiloes_processos?on_conflict=processo", data=json.dumps(lote),
               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    i = 0
    while i < len(fila) or falhou_1a_vez:
        if i >= len(fila):                                  # fim da fila: repete UMA vez os que falharam
            fila.extend(falhou_1a_vez)
            ja_repetidos.update(d for d, _, _ in falhou_1a_vez)
            cont["repetidos"] += len(falhou_1a_vez)
            falhou_1a_vez = []
            continue
        d, trib, ref = fila[i]
        i += 1
        if time.time() - t0 > TEMPO_MAX:
            print(f"  tempo máximo de {TEMPO_MAX / 60:.0f} min atingido — o restante fica para a próxima execução")
            break
        desde = (dt.date.fromisoformat(ref) - dt.timedelta(days=JANELA_ANTES_DO_EDITAL)).isoformat()
        reg = {"processo": d, "tribunal": trib, "consultado_em": agora.isoformat(), "encontrado": False, "classe": None,
               "orgao": None, "ultimo_mov_data": None, "ultimo_mov_nome": None, "alertas": [], "alerta_principal": None,
               "alerta_mov": None, "alerta_data": None, "movimentos": [], "erro": None}   # todas as chaves: o envio em lote exige
        try:
            status, dados = consultar(d, trib)
            if status in (401,):
                print("  ERRO: o Datajud recusou a chave (HTTP 401). Confira a chave pública vigente na wiki do CNJ "
                      "e atualize o segredo DATAJUD_API_KEY. Interrompido.")
                cont["erros"] += 1
                break
            if status == 403 and estado["modo"] == "direto":
                raise ConnectionRefusedError("acesso direto recusado (403)")
            if status == 404:
                reg.update(erro="tribunal sem índice no Datajud")
                cont["nao_encontrados"] += 1
            elif status != 200:
                raise RuntimeError(f"HTTP {status}")
            else:
                estado["falhas_seguidas"] = 0
                res = ler_resposta(dados)
                cont["consultados"] += 1
                if not res:
                    cont["nao_encontrados"] += 1
                else:
                    cont["encontrados"] += 1
                    tipos, p, pm, pd = classificar(res["movimentos"], desde)
                    ult = res["movimentos"][0] if res["movimentos"] else {}
                    reg.update(encontrado=True, classe=res["classe"], orgao=res["orgao"],
                               ultimo_mov_data=ult.get("dataHora"), ultimo_mov_nome=ult.get("nome"),
                               alertas=tipos, alerta_principal=p, alerta_mov=pm, alerta_data=pd,
                               movimentos=[{"data": (m.get("dataHora") or "")[:10], "nome": m.get("nome")} for m in res["movimentos"][:15]])
                    if p:
                        por_alerta[p] = por_alerta.get(p, 0) + 1
            linhas.append(reg)
        except PermissionError as e:
            print(f"  ERRO: {e}. Confira o segredo DJEN_TOKEN (o mesmo da etapa dos judiciais). Interrompido.")
            cont["erros"] += 1
            break
        except (requests.exceptions.ConnectionError, ConnectionRefusedError):
            # BLOQUEIO (conexão recusada / 403) — não confundir com lentidão (ReadTimeout é tratado abaixo)
            estado["bloqueios"] += 1
            estado["falhas_seguidas"] += 1
            if estado["modo"] == "direto" and estado["bloqueios"] >= 3 and token:
                estado["modo"], estado["falhas_seguidas"] = "relay", 0
                print("  o acesso direto ao Datajud está BLOQUEADO — passando a consultar pela função de São Paulo")
            if d not in ja_repetidos:
                falhou_1a_vez.append((d, trib, ref))
            cont["erros"] += 1
        except Exception as e:                               # noqa: BLE001 — lentidão/outros: segue para o próximo
            estado["falhas_seguidas"] += 1
            cont["erros"] += 1
            if d not in ja_repetidos:
                falhou_1a_vez.append((d, trib, ref))
            else:
                reg.update(erro=str(e)[:200])                # falhou 2 vezes: grava o erro (reconsultado na próxima execução)
                linhas.append(reg)
        if estado["falhas_seguidas"] >= 12:
            print("  ERRO: 12 falhas seguidas — o Datajud parece fora do ar. Interrompido; tenta de novo na próxima execução.")
            break
        if len(linhas) >= 25:
            gravar(linhas)
            linhas = []
        time.sleep(PAUSA)
    gravar(linhas)
    linhas = []
    modo = estado["modo"]
    print(f"  modo de acesso: {'direto (GitHub)' if modo == 'direto' else 'pela função de São Paulo'}")
    print(f"  consultados: {cont['consultados']} | encontrados: {cont['encontrados']} | não encontrados: {cont['nao_encontrados']} | "
          f"falhas: {cont['erros']} (repetidos no fim: {cont['repetidos']})")
    if por_alerta:
        print("  ALERTAS (movimentação depois do edital): " + ", ".join(f"{ROTULO[k]} {v}" for k, v in
              sorted(por_alerta.items(), key=lambda kv: [t for t, _ in ALERTAS].index(kv[0]))))
    else:
        print("  ALERTAS: nenhum")
    return 0


if __name__ == "__main__":
    sys.exit(main())
