# SOLIDUNS — Coletor CVM

Coleta semanal das demonstrações financeiras das companhias abertas
(Portal de Dados Abertos da CVM), cálculo dos fundamentos e gravação no
Supabase da plataforma SOLIDUNS.

## Como funciona
1. Toda segunda e quinta-feira às 10h (Brasília), o GitHub executa `coletor/coletar_cvm.py`.
2. O programa baixa DFP, ITR, FCA e o cadastro da CVM, monta o último
   balanço e os resultados dos últimos 12 meses de cada empresa e grava
   uma linha por ação na tabela `fundamentos_br`.
3. Em seguida pede ao banco para recalcular `indicadores_acoes`, que é o
   que a Busca Avançada lê. Os indicadores que dependem de preço também
   são recalculados todo dia útil às 19h10, após o fechamento da B3.

## Segredos necessários (Settings -> Secrets and variables -> Actions)
- `SUPABASE_URL` — endereço do projeto Supabase.
- `SUPABASE_SERVICE_KEY` — chave `service_role` do Supabase.
  Ela tem acesso total ao banco: fica só aqui, nunca no site.

## Rodar à mão
Aba **Actions** -> **Coleta CVM** -> **Run workflow**.

## Fonte e licença dos dados
Dados públicos da CVM (dados.cvm.gov.br), Licença Aberta para Bases de
Dados (ODbL). Indicadores calculados pela SOLIDUNS; não constituem
recomendação de investimento.
