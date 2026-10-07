"""
SOLIDUNS — AGENTE DE LEILÕES — Acompanhamento dos PROCESSOS dos leilões judiciais — v1.1 (07/10/2026)

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

VERSAO = "1.1"
UA = "SOLIDUNS-coletor/1.0 (+https://soliduns.com.br; contato@soliduns.com.br)"
DATAJUD = os.environ.get("DATAJUD_URL_TESTE") or "https://api-publica.datajud.cnj.jus.br/api_publica_{trib}/_search"  # a variável só existe nos testes
PAUSA = float(os.environ.get("DATAJUD_PAUSA_TESTE") or 0.7)                      # segundos entre consultas (gentil com o serviço público)
MAX_PROCESSOS = 1500             # teto de segurança por execução
TEMPO_MAX = float(os.environ.get("DATAJUD_TEMPO_MAX") or 20 * 60)   # segundos por execução
ESPERA = float(os.environ.get("DATAJUD_ESPERA_TESTE") or 20)   # segundos máximos por consulta
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
        for m in (s.get("movimentos") or []):
            if m.get("dataHora"):
                movs.append(m)
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
    """Processos dos leilões judiciais ativos: {digitos: (tribunal, data de referência)}."""
    out, off = {}, 0
    while True:
        lote = sb("GET", "leiloes_radar?select=processo,tribunal,data_lista,primeira_vez&fonte=eq.djen&situacao=eq.ativo"
                         "&order=id.asc&limit=1000&offset=" + str(off))
        for r in lote:
            d = so_digitos(r.get("processo"))
            if not d or not r.get("tribunal"):
                continue
            ref = (r.get("data_lista") or (r.get("primeira_vez") or "")[:10] or dt.date.today().isoformat())[:10]
            ant = out.get(d)
            if not ant or ref < ant[1]:
                out[d] = (r["tribunal"], ref)
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
    ja = {p["processo"]: p for p in sb("GET", "leiloes_processos?select=processo,consultado_em&limit=5000")}
    fila = [(d, t, ref) for d, (t, ref) in sorted(procs.items())
            if not (ja.get(d) and (ja[d]["consultado_em"] or "")[:10] == hoje)][:MAX_PROCESSOS]
    print(f"  processos de leilões judiciais ativos: {len(procs)} | a consultar hoje: {len(fila)}")
    ses = requests.Session()
    ses.headers.update({"Authorization": "APIKey " + chave, "Content-Type": "application/json", "User-Agent": UA})
    token = (os.environ.get("DJEN_TOKEN") or "").strip()
    modo, falhas_seguidas, t0 = "direto", 0, time.time()

    def consultar(d, trib):
        """Devolve (status_http, json_ou_None). Lança exceção em falha de rede/tempo."""
        if modo == "direto":
            r = ses.post(DATAJUD.format(trib=trib.lower()), data=json.dumps({"query": {"match": {"numeroProcesso": d}}, "size": 10}),
                         timeout=ESPERA)
        else:
            r = requests.get(RELAY, params={"forceFunctionRegion": "sa-east-1", "datajud": "1", "tribunal": trib, "processo": d},
                             headers={"x-soliduns-token": token, "x-datajud-key": chave, "User-Agent": UA}, timeout=ESPERA + 15)
            if r.status_code == 401 and "nao_autorizado" in r.text:
                raise PermissionError("a função de São Paulo recusou o DJEN_TOKEN")
            if r.status_code in (502, 504):
                raise TimeoutError("o Datajud não respondeu nem pela função de São Paulo")
        return r.status_code, (r.json() if r.status_code == 200 else None)

    cont = {"consultados": 0, "encontrados": 0, "nao_encontrados": 0, "erros": 0}
    por_alerta, linhas, parar, refazer = {}, [], False, []
    for i, (d, trib, ref) in enumerate(fila):          # (a lista pode crescer: os que falharam antes da troca de rota)
        if time.time() - t0 > TEMPO_MAX:
            print(f"  tempo máximo de {TEMPO_MAX / 60:.0f} min atingido — {len(fila) - i} processo(s) ficam para a próxima execução")
            break
        desde = (dt.date.fromisoformat(ref) - dt.timedelta(days=JANELA_ANTES_DO_EDITAL)).isoformat()
        reg = {"processo": d, "tribunal": trib, "consultado_em": agora.isoformat(), "encontrado": False, "classe": None,
               "orgao": None, "ultimo_mov_data": None, "ultimo_mov_nome": None, "alertas": [], "alerta_principal": None,
               "alerta_mov": None, "alerta_data": None, "movimentos": [], "erro": None}   # todas as chaves: o envio em lote exige
        for tentativa in range(2):
            try:
                status, dados = consultar(d, trib)
                if status in (401, 403):
                    if modo == "direto" and status == 403 and token:
                        raise ConnectionError("acesso direto recusado (403)")
                    print(f"  ERRO: o Datajud recusou a chave (HTTP {status}). Confira a chave pública vigente na wiki do CNJ "
                          "e atualize o segredo DATAJUD_API_KEY. Interrompido.")
                    cont["erros"] += 1
                    parar = True
                elif status == 404:
                    reg.update(encontrado=False, erro="tribunal sem índice no Datajud")
                    cont["nao_encontrados"] += 1
                elif status != 200:
                    raise ConnectionError(f"HTTP {status}")
                else:
                    falhas_seguidas = 0
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
                break
            except PermissionError as e:
                print(f"  ERRO: {e}. Confira o segredo DJEN_TOKEN (o mesmo da etapa dos judiciais). Interrompido.")
                cont["erros"] += 1
                parar = True
                break
            except Exception as e:                               # noqa: BLE001 — rede/tempo: conta e decide
                falhas_seguidas += 1
                reg.update(erro=str(e)[:200])
                if modo == "direto" and falhas_seguidas >= 3:
                    if token:
                        modo, falhas_seguidas = "relay", 0
                        print("  o Datajud não respondeu ao acesso direto (3 falhas seguidas) — passando a consultar pela função de São Paulo")
                        fila.extend(refazer)                     # os que falharam antes voltam para o fim da fila
                        refazer = []
                        reg["erro"] = None
                        continue                                 # repete este processo pela função
                    print("  ERRO: o Datajud não responde a partir do GitHub e não há DJEN_TOKEN para usar a função de São Paulo. Interrompido.")
                    parar = True
                elif modo == "direto":
                    refazer.append((d, trib, ref))
                    reg["_refazer"] = True
                elif modo == "relay" and falhas_seguidas >= 5:
                    print("  ERRO: o Datajud não respondeu nem pela função de São Paulo (5 falhas seguidas). Interrompido; tenta de novo amanhã.")
                    parar = True
                cont["erros"] += 1
                break
        if parar:
            break
        if reg.pop("_refazer", False):
            time.sleep(PAUSA)
            continue                                             # não grava: será refeito pela função de São Paulo
        linhas.append(reg)
        if len(linhas) >= 50:
            sb("POST", "leiloes_processos?on_conflict=processo", data=json.dumps(linhas),
               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            linhas = []
        time.sleep(PAUSA)
    print(f"  modo de acesso: {'direto (GitHub)' if modo == 'direto' else 'pela função de São Paulo'}")
    if linhas:
        sb("POST", "leiloes_processos?on_conflict=processo", data=json.dumps(linhas),
           headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    print(f"  consultados: {cont['consultados']} | encontrados: {cont['encontrados']} | não encontrados: {cont['nao_encontrados']} | erros: {cont['erros']}")
    if por_alerta:
        print("  ALERTAS (movimentação depois do edital): " + ", ".join(f"{ROTULO[k]} {v}" for k, v in
              sorted(por_alerta.items(), key=lambda kv: [t for t, _ in ALERTAS].index(kv[0]))))
    else:
        print("  ALERTAS: nenhum")
    return 0


if __name__ == "__main__":
    sys.exit(main())
