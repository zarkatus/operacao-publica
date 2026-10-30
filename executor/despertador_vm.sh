#!/bin/sh
# Despertador do revezamento numa VM (30/10/2026, fio prospeccao-parceiros-email-qualidade): rodada atrasada no livro
# (ops_atrasadas_qtd, só contagem, chave anônima pública) => acorda o executor GitLab pelo gatilho. FORCAR=1 acorda sempre (prova).
. /etc/revezamento-despertador/conf   # SB_ANON, GATILHO="<projeto>:<token>", QUEM
n=$(curl -fsS -m 30 -X POST "https://gprkopmqhocsslrdmebm.supabase.co/rest/v1/rpc/ops_atrasadas_qtd" -H "apikey: $SB_ANON" \
    -H "Authorization: Bearer $SB_ANON" -H "Content-Type: application/json" -d '{}') || { echo "livro inacessível"; exit 1; }
[ "$n" -gt 0 ] 2>/dev/null || [ "${FORCAR:-0}" = 1 ] || { echo "ok: 0 atrasadas"; exit 0; }
p=${GATILHO%%:*}; t=${GATILHO#*:}
c=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -X POST "https://gitlab.com/api/v4/projects/$p/trigger/pipeline" -F "token=$t" -F ref=main)
echo "$QUEM: $n atrasada(s), GitLab acordado http=$c"; [ "$c" = 201 ]
