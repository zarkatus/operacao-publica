#!/usr/bin/env python3
"""Agendador em corrente: roda no fim de cada ciclo do publicar-sites.

Por que (03/10/2026): o cron do GitHub atrasa horas ou pula (o vigia do rodapé "das 11:30" rodou 16:43 em 02/10 e
não rodou em 03/10). Em vez de depender dele, cada ciclo:
  1. dispara as rotinas de rotinas.json cujo horário UTC já passou hoje e que ainda não têm run hoje depois dele;
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
        for h in r.get("horarios_utc", []):
            hh, mm = map(int, h.split(":"))
            alvo = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if agora < alvo:
                continue
            q = gh("api", f"repos/{REPO}/actions/workflows/{r['workflow']}/runs?created=%3E%3D{alvo.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=1",
                   "-q", ".total_count")
            if q.returncode:
                print(f"agendador: não consegui ler runs de {nome} (rc={q.returncode}); tento no próximo ciclo")
                continue
            if int(q.stdout.strip() or 0) == 0:
                d = gh("workflow", "run", r["workflow"], "-R", REPO)
                print(f"agendador: {nome} ({h} UTC) disparada" if d.returncode == 0 else
                      f"agendador: disparo de {nome} falhou rc={d.returncode}")


def main():
    agora = dt.datetime.now(dt.timezone.utc)
    rotinas_devidas(agora)
    falta = CICLO_S - (time.time() - INICIO)
    if falta > 0:
        time.sleep(falta)
    d = gh("workflow", "run", "publicar-sites.yml", "-R", REPO)
    print("corrente: próximo ciclo disparado" if d.returncode == 0 else f"corrente: disparo falhou rc={d.returncode}")
    return 0  # nunca derruba o ciclo; o cron de reinício e o Healthchecks cobrem a quebra


if __name__ == "__main__":
    raise SystemExit(main())
