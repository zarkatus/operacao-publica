#!/usr/bin/env python3
"""Agendador em corrente: roda no fim de cada ciclo do publicar-sites.

Por que (03/10/2026): o cron do GitHub atrasa horas ou pula (o vigia do rodapé "das 11:30" rodou 16:43 em 02/10 e
não rodou em 03/10). Em vez de depender dele, cada ciclo:
  1. dispara as rotinas de rotinas.json cujo horário UTC já passou hoje e que ainda não têm run hoje depois dele
     (com "intervalo_dias": N, nenhum run real nos últimos N dias);
  2. espera até completar ~CICLO_S desde o início do job;
  3. dispara o próximo ciclo do publicar-sites (workflow_dispatch com o GITHUB_TOKEN, o único evento que esse
     token pode gerar e que cria run nova).
O cron */5 do workflow fica só como reinício da corrente se ela quebrar; o Healthchecks avisa se tudo parar.
Repo público: minuto não custa nada. Usa só `gh` (GH_TOKEN = github.token) e biblioteca padrão.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "zarkatus/operacao-publica")
CICLO_S = int(os.environ.get("CICLO_S", "290"))
INICIO = float(os.environ.get("INICIO_JOB") or time.time())


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)


def rotinas_devidas(agora):
    rot = json.loads((RAIZ / "rotinas.json").read_text(encoding="utf-8"))["rotinas"]
    for nome, r in rot.items():
        # rotina de outro executor (revezamento 30/10: "executores" pc/oracle/gitlab, sem "workflow"): quem dispara é o
        # executor dela, não o Actions. Sem este pulo o KeyError derrubou a corrente às 19:20 UTC de 30/10 (nenhum site publicou).
        if not r.get("workflow"):
            continue
        # inicio (AAAA-MM-DD), dias_semana (0=seg..6=dom) e dias_mes (17/10/2026, rotinas migradas dos repos privados):
        # filtros de calendário antes do horário; ausentes = todo dia.
        if r.get("inicio") and agora.date().isoformat() < r["inicio"]:
            continue
        if "dias_semana" in r and agora.weekday() not in r["dias_semana"]:
            continue
        if "dias_mes" in r and agora.day not in r["dias_mes"]:
            continue
        # a_cada_min (25/10/2026, ronda externa a cada 30 min): devida se não houve run nos últimos N minutos; uma
        # consulta por ciclo, em vez de dezenas de horarios_utc (cada horário vencido custa uma chamada à API).
        if r.get("a_cada_min"):
            desde = agora - dt.timedelta(minutes=r["a_cada_min"])
            q = gh("api", f"repos/{REPO}/actions/workflows/{r['workflow']}/runs?created=%3E%3D{desde.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=1",
                   "-q", ".total_count")
            if q.returncode:
                print(f"agendador: não consegui ler runs de {nome} (rc={q.returncode}); tento no próximo ciclo")
            elif int(q.stdout.strip() or 0) == 0:
                d = gh("workflow", "run", r["workflow"], "-R", REPO)
                print(f"agendador: {nome} (a cada {r['a_cada_min']} min) disparada" if d.returncode == 0 else
                      f"agendador: disparo de {nome} falhou rc={d.returncode}")
            continue
        for h in r.get("horarios_utc", []):
            hh, mm = map(int, h.split(":"))
            alvo = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if agora < alvo:
                continue
            # intervalo_dias (04/10/2026, ensaio mensal): devida se não houve run REAL nos últimos N dias (run de
            # simulação/ensaio não conta); assim um dia em que a corrente quebrou não faz perder o mês, ela recupera no seguinte.
            desde = alvo - dt.timedelta(days=r["intervalo_dias"]) if r.get("intervalo_dias") else alvo
            q = gh("api", f"repos/{REPO}/actions/workflows/{r['workflow']}/runs?created=%3E%3D{desde.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=30",
                   "-q", '[.workflow_runs[] | select(.display_title | test("simula|ensaio") | not)] | length')
            if q.returncode:
                print(f"agendador: não consegui ler runs de {nome} (rc={q.returncode}); tento no próximo ciclo")
                continue
            if int(q.stdout.strip() or 0) == 0:
                d = gh("workflow", "run", r["workflow"], "-R", REPO)
                print(f"agendador: {nome} ({h} UTC) disparada" if d.returncode == 0 else
                      f"agendador: disparo de {nome} falhou rc={d.returncode}")


def main():
    agora = dt.datetime.now(dt.timezone.utc)
    try:
        rotinas_devidas(agora)
    except Exception as e:  # rotina malformada nunca derruba a corrente de publicação
        print(f"agendador: ERRO nas rotinas ({type(e).__name__}: {e}); sigo com a corrente")
    falta = CICLO_S - (time.time() - INICIO)
    if falta > 0:
        time.sleep(falta)
    d = gh("workflow", "run", "publicar-sites.yml", "-R", REPO)
    print("corrente: próximo ciclo disparado" if d.returncode == 0 else f"corrente: disparo falhou rc={d.returncode}")
    return 0  # nunca derruba o ciclo; o cron de reinício e o Healthchecks cobrem a quebra


if __name__ == "__main__":
    raise SystemExit(main())
