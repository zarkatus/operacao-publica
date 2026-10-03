#!/usr/bin/env bash
# Ensaio da reserva do Supabase (fio fornecedores-resiliencia, S3b / T5.9, 03/10/2026).
# Sobe o Supabase auto-hospedado OFICIAL (compose do repo supabase/supabase) num runner efêmero, aplica a camada
# real da plataforma (schema + GRANTs + buckets, SEM dado: plataforma-camada.sql.gz.enc), copia as 100 EFs reais
# e prova com dado SINTÉTICO: Auth, REST com a RLS real da plataforma (fn_user_pode_acessar_projeto), Storage,
# Vault, pg_cron, pg_net -> EF e o boot das EFs. Mede o tempo até o serviço responder (parte do RTO).
#
# Log PÚBLICO e mudo por desenho: só contagens, tempos e veredito. Nomes de objeto e mensagens de erro vão para
# detalhe.txt, que sai CIFRADO (mesma chave do schema) como artefato.
# Trava de saída: todo host externo que o schema ou as EFs chamam aponta para 0.0.0.0 dentro dos containers
# (db e functions), para nenhum trigger/EF tocar produção. Só os CDNs de dependência (npm, esm.sh, deno) ficam abertos.
set -uo pipefail
T0=$(date +%s)
R="${RUNNER_TEMP:-/tmp}/ens"; mkdir -p "$R"; DET="$R/detalhe.txt"; : > "$DET"
AQUI="$(cd "$(dirname "$0")" && pwd)"
seg() { echo $(( $(date +%s) - T0 )); }
FALHAS=0
ok()   { echo "OK     $1"; }
nok()  { echo "FALHOU $1"; FALHAS=$((FALHAS+1)); }
conf() { if [ "$2" = "$3" ]; then ok "$1 (esperado=$3 obtido=$2)"; else nok "$1 (esperado=$3 obtido=$2)"; fi; }

# ---------- 1. camada da plataforma (decifra) ----------
[ -n "${ENSAIO_SCHEMA_KEY:-}" ] || { echo "sem ENSAIO_SCHEMA_KEY"; exit 1; }
printf '%s' "$ENSAIO_SCHEMA_KEY" > "$R/k"; chmod 600 "$R/k"; unset ENSAIO_SCHEMA_KEY
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$R/k" -in "$AQUI/plataforma-camada.sql.gz.enc" | gunzip > "$R/camada.sql" \
  || { echo "decifrar falhou"; exit 1; }
echo "camada_bytes=$(stat -c %s "$R/camada.sql")"

# ---------- 2. EFs reais (deploy key só leitura) ----------
python3 - "$R" <<'PY'
import json, os, sys, pathlib
k = json.loads(os.environ.get("DEPLOY_KEYS") or "{}").get("innovasphere-platform", "")
p = pathlib.Path(sys.argv[1]) / "dk"; p.write_text(k.strip() + "\n"); p.chmod(0o600)
PY
unset DEPLOY_KEYS
export GIT_SSH_COMMAND="ssh -i $R/dk -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$R/kh"
git clone -q --depth 1 --filter=blob:none --sparse git@github.com:zarkatus/innovasphere-platform.git "$R/plat" >/dev/null 2>&1 \
  && git -C "$R/plat" sparse-checkout set supabase/functions >/dev/null 2>&1 || { echo "clone das EFs falhou"; exit 1; }
rm -f "$R/dk"; unset GIT_SSH_COMMAND
echo "plataforma_commit=$(git -C "$R/plat" rev-parse --short HEAD)"

# ---------- 3. compose oficial + chaves descartáveis ----------
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/supabase/supabase "$R/sb" && git -C "$R/sb" sparse-checkout set docker
echo "supabase_commit=$(git -C "$R/sb" rev-parse --short HEAD)"
cd "$R/sb/docker"
cp .env.example .env
sh utils/generate-keys.sh --update-env >/dev/null 2>&1 || { echo "generate-keys falhou"; exit 1; }
# liga a autoconfirmação e grava as 3 chaves do ensaio em arquivo (nunca no terminal)
python3 - "$R" <<'PY'
import sys, pathlib
r = pathlib.Path(sys.argv[1]); env = pathlib.Path(".env")
linhas = env.read_text().splitlines(); vals = {}
for i, l in enumerate(linhas):
    if "=" in l and not l.startswith("#"):
        k, v = l.split("=", 1); vals[k] = v
        if k == "ENABLE_EMAIL_AUTOCONFIRM": linhas[i] = "ENABLE_EMAIL_AUTOCONFIRM=true"
env.write_text("\n".join(linhas) + "\n")
for k, f in (("ANON_KEY", "anon"), ("SERVICE_ROLE_KEY", "srv"), ("POSTGRES_PASSWORD", "pgpass")):
    (r / f).write_text(vals[k])
PY
ANON=$(<"$R/anon"); SRV=$(<"$R/srv"); PGPASS=$(<"$R/pgpass"); rm -f "$R/anon" "$R/srv" "$R/pgpass"
echo "::add-mask::$ANON"; echo "::add-mask::$SRV"; echo "::add-mask::$PGPASS"

# EFs da plataforma ao lado de main/ e hello/ do compose
for d in "$R/plat/supabase/functions"/*/; do n=$(basename "$d"); { [ "$n" = main ] || [ "$n" = hello ]; } && continue; cp -r "$d" volumes/functions/; done
NEF=$(find "$R/plat/supabase/functions" -mindepth 1 -maxdepth 1 -type d ! -name '_*' | wc -l); echo "efs_copiadas=$NEF"

# trava de saída: hosts do schema + das EFs + fixos -> 0.0.0.0
{ cat "$R/camada.sql"; find "$R/plat/supabase/functions" -name '*.ts' -exec cat {} +; } \
  | grep -oE 'https?://[a-zA-Z0-9.-]+\.[a-z]{2,}' | sed -E 's#https?://##' | sort -u \
  | grep -vE '^(esm\.sh|deno\.land|jsr\.io|cdn\.jsdelivr\.net|unpkg\.com|registry\.npmjs\.org|example\.com|www\.w3\.org|localhost)$' > "$R/bloq.txt"
printf '%s\n' evo2.innovasphere.ia.br hc-ping.com api.anthropic.com api.groq.com generativelanguage.googleapis.com \
  graph.facebook.com api.resend.com ntfy.sh mail.innovasphere.ia.br mail.picarrashills.com.br >> "$R/bloq.txt"
sort -u -o "$R/bloq.txt" "$R/bloq.txt"
python3 - "$R/bloq.txt" <<'PY'
import sys
h = [l.strip() for l in open(sys.argv[1]) if l.strip()]
with open("docker-compose.override.yml", "w") as f:
    f.write("services:\n")
    for s in ("db", "functions"):
        f.write(f"  {s}:\n    extra_hosts:\n" + "".join(f'      - "{x}:0.0.0.0"\n' for x in h))
PY
echo "hosts_bloqueados=$(wc -l < "$R/bloq.txt")"

# ---------- 4. sobe ----------
docker compose pull -q >/dev/null 2>&1; T_PULL=$(seg); echo "t_pull=${T_PULL}s"
docker compose up -d >"$R/up.log" 2>&1 || { echo "compose up com erro"; tail -c 3000 "$R/up.log" >> "$DET"; }
H="apikey: $ANON"
espera() { for i in $(seq 1 90); do c=$(curl -s -o /dev/null -w '%{http_code}' -H "$H" "$1"); [ "$c" = 200 ] && return 0; sleep 2; done; return 1; }
espera http://localhost:8000/auth/v1/health && ok "auth no ar" || nok "auth no ar"
espera http://localhost:8000/rest/v1/ && ok "rest no ar" || nok "rest no ar"
espera http://localhost:8000/storage/v1/status && ok "storage no ar" || nok "storage no ar"
T_UP=$(seg); echo "t_servico_vazio=${T_UP}s (desde o pull: $((T_UP-T_PULL))s)"
docker compose ps --format '{{.Service}}={{.State}}' | sort | tr '\n' ' '; echo

# trava provada: o db não alcança produção
B=$(docker exec supabase-db getent hosts dqknyhfrrmbqvkdshjfu.supabase.co | awk '{print $1}')
conf "trava de saída no db (prod resolve para)" "$B" "0.0.0.0"

# ---------- 5. aplica a camada ----------
SQL() { docker exec -e PGPASSWORD="$PGPASS" -i supabase-db psql -h localhost -U supabase_admin -d postgres -X -qtA "$@"; }
SQL -v ON_ERROR_STOP=0 < "$R/camada.sql" >/dev/null 2>"$R/restore.err"
T_REST=$(seg); echo "t_camada_aplicada=${T_REST}s (camada em $((T_REST-T_UP))s)"
NERR=$(grep -c 'ERROR:' "$R/restore.err"); echo "erros_restore=$NERR"
echo "categorias de erro (nomes trocados por <x>):"
grep -oE 'ERROR: .*' "$R/restore.err" | sed -E 's/"[^"]*"/<x>/g; s/[0-9]+/N/g' | sort | uniq -c | sort -rn | head -10
{ echo "== erros do restore"; grep -A1 'ERROR:' "$R/restore.err" | head -400; } >> "$DET"
SQL -c "NOTIFY pgrst, 'reload schema';" >/dev/null
read -r NT NRLS NPOL NFN <<<"$(SQL -F' ' -c "select (select count(*) from pg_tables where schemaname='public'), (select count(*) from pg_tables where schemaname='public' and rowsecurity), (select count(*) from pg_policies where schemaname in ('public','storage')), (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public')")"
echo "public: tabelas=$NT com_rls=$NRLS policies(public+storage)=$NPOL funcoes=$NFN"
[ "${NT:-0}" -ge 580 ] && ok "tabelas da plataforma ($NT >= 580)" || nok "tabelas da plataforma (${NT:-0})"
[ "${NT:-0}" = "${NRLS:-x}" ] && ok "RLS ligado em todas" || nok "RLS: ${NRLS:-0} de ${NT:-0}"
echo "extensoes: $(SQL -c "select string_agg(extname,' ' order by extname) from pg_extension")"
sleep 3

# ---------- 6. Auth (usuários sintéticos; os 4 triggers da plataforma em auth.users rodam) ----------
SENHA="Ens-$(openssl rand -hex 12)"; echo "::add-mask::$SENHA"
token() { python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null; }
cadastra() { curl -s -X POST http://localhost:8000/auth/v1/signup -H "$H" -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$SENHA\"}"; }
TA=$(cadastra ensaio-a@example.com | token); TB=$(cadastra ensaio-b@example.com | token)
[ -n "$TA" ] && echo "::add-mask::$TA"; [ -n "$TB" ] && echo "::add-mask::$TB"
[ -n "$TA" ] && [ -n "$TB" ] && ok "signup de 2 usuários com os triggers da plataforma" || nok "signup"
conf "handle_new_user criou profiles" "$(SQL -c "select count(*) from public.profiles where id in (select id from auth.users where email like 'ensaio-%@example.com')")" "2"
TL=$(curl -s -X POST 'http://localhost:8000/auth/v1/token?grant_type=password' -H "$H" -H 'Content-Type: application/json' -d "{\"email\":\"ensaio-a@example.com\",\"password\":\"$SENHA\"}" | token)
[ ${#TL} -gt 100 ] && ok "login por senha devolve JWT" || nok "login por senha"
CW=$(curl -s -o /dev/null -w '%{http_code}' -X POST 'http://localhost:8000/auth/v1/token?grant_type=password' -H "$H" -H 'Content-Type: application/json' -d '{"email":"ensaio-a@example.com","password":"errada-123456"}')
conf "senha errada recusada" "$CW" "400"

# ---------- 7. RLS real da plataforma: plat_agent_runs × fn_user_pode_acessar_projeto ----------
SQL -v ON_ERROR_STOP=1 >/dev/null 2>>"$DET" <<'EOSQL'
delete from public.innovasphere_data where key in ('viewer_profiles','project_index');
insert into public.innovasphere_data(key,value) values
 ('viewer_profiles','[{"email":"ensaio-a@example.com","projId":"ensaio_a","ativo":true,"cadeira":"engenheiro"}]'),
 ('project_index','[{"id":"ensaio_a","categoria_negocio":"administracao_obras"},{"id":"ensaio_b","categoria_negocio":"administracao_obras"}]');
insert into public.plat_agent_runs(agent_name,project_id) values ('ensaio','ensaio_a'),('ensaio','ensaio_b');
EOSQL
conta() { curl -s -H "$H" -H "Authorization: Bearer $1" 'http://localhost:8000/rest/v1/plat_agent_runs?select=project_id&agent_name=eq.ensaio' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else "erro")' 2>/dev/null; }
conf "usuário A (acesso só a ensaio_a) vê"  "$(conta "$TA")"  "1"
conf "usuário B (sem perfil) vê"            "$(conta "$TB")"  "0"
conf "anônimo vê"                           "$(conta "$ANON")" "0"
conf "service_role vê"                      "$(conta "$SRV")" "2"
rpc() { curl -s -X POST -H "$H" -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d "{\"p_key\":\"$1\"}" http://localhost:8000/rest/v1/rpc/fn_user_pode_acessar_projeto; }
conf "rpc fn_user_pode_acessar_projeto(ensaio_a) como A" "$(rpc ensaio_a)" "true"
conf "rpc fn_user_pode_acessar_projeto(ensaio_b) como A" "$(rpc ensaio_b)" "false"
# controle negativo: o teste tem de enxergar a mudança de permissão
SQL -c "update public.innovasphere_data set value='[{\"email\":\"ensaio-a@example.com\",\"_canAccessAll\":true,\"ativo\":true}]' where key='viewer_profiles'" >/dev/null
conf "controle negativo: A com _canAccessAll vê" "$(conta "$TA")" "2"
SQL -c "update public.innovasphere_data set value='[{\"email\":\"ensaio-a@example.com\",\"projId\":\"ensaio_a\",\"ativo\":false}]' where key='viewer_profiles'" >/dev/null
conf "perfil desativado não vê nada" "$(conta "$TA")" "0"

# ---------- 8. Storage (buckets reais, objeto sintético) ----------
NB=$(SQL -c "select count(*) from storage.buckets"); echo "buckets=$NB"
head -c 1048576 /dev/urandom > "$R/obj.bin"; S0=$(sha256sum "$R/obj.bin" | cut -c1-64)
CU=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$H" -H "Authorization: Bearer $SRV" -H 'Content-Type: application/octet-stream' --data-binary @"$R/obj.bin" http://localhost:8000/storage/v1/object/documentos/ensaio/obj.bin)
conf "upload 1 MiB no bucket privado documentos" "$CU" "200"
S1=$(curl -s -H "$H" -H "Authorization: Bearer $SRV" http://localhost:8000/storage/v1/object/documentos/ensaio/obj.bin | sha256sum | cut -c1-64)
conf "download devolve o mesmo sha256" "$S1" "$S0"
CA=$(curl -s -o /dev/null -w '%{http_code}' -H "$H" -H "Authorization: Bearer $ANON" http://localhost:8000/storage/v1/object/documentos/ensaio/obj.bin)
[ "$CA" != 200 ] && ok "anônimo não baixa do privado (http $CA)" || nok "anônimo baixou do privado"
CB=$(curl -s -o /dev/null -w '%{http_code}' -H "$H" -H "Authorization: Bearer $TB" http://localhost:8000/storage/v1/object/documentos/ensaio/obj.bin)
[ "$CB" != 200 ] && ok "usuário sem acesso não baixa do privado (http $CB)" || nok "usuário B baixou do privado"

# ---------- 9. Vault, pg_cron, pg_net -> EF ----------
SQL -c "select vault.create_secret('valor-ensaio-123','ensaio_s3b')" >/dev/null 2>>"$DET"
conf "vault cifra e decifra" "$(SQL -c "select decrypted_secret from vault.decrypted_secrets where name='ensaio_s3b'")" "valor-ensaio-123"
J=$(SQL -c "select cron.schedule('ensaio_s3b','* * * * *','select 1')" 2>>"$DET"); [ -n "$J" ] && ok "pg_cron agenda (job $J)" || nok "pg_cron"
SQL -c "select cron.unschedule('ensaio_s3b')" >/dev/null 2>&1
RID=$(SQL -c "select net.http_post(url:='http://kong:8000/functions/v1/hello', headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer $SRV'), body:='{\"name\":\"ensaio\"}'::jsonb)" 2>>"$DET")
ST=""; for i in $(seq 1 30); do ST=$(SQL -c "select status_code from net._http_response where id=${RID:-0}"); [ -n "$ST" ] && break; sleep 2; done
conf "pg_net chama EF local (caminho dos crons)" "${ST:-sem_resposta}" "200"

# ---------- 10. boot das EFs reais (só as que tratam OPTIONS antes de qualquer lógica) ----------
BOOT_OK=0; BOOT_FALHA=0; NAO_INV=0
for d in "$R/plat/supabase/functions"/*/; do
  n=$(basename "$d"); [[ $n == _* ]] && continue
  f=$(ls "$d"index.ts "$d"index.js 2>/dev/null | head -1)
  if [ -z "$f" ] || ! grep -qE "OPTIONS" "$f"; then NAO_INV=$((NAO_INV+1)); echo "nao_invocada $n" >> "$DET"; continue; fi
  CORPO=$(curl -s -m 90 -X OPTIONS -w '\n%{http_code}' -H "$H" -H "Authorization: Bearer $SRV" -H 'Origin: http://localhost' "http://localhost:8000/functions/v1/$n")
  C=$(echo "$CORPO" | tail -1)
  if echo "$CORPO" | grep -qiE 'boot|InvalidWorkerCreation|worker failed|failed to create the worker|Module not found|TypeError: |SyntaxError'; then
    BOOT_FALHA=$((BOOT_FALHA+1)); { echo "boot_falha $n http=$C"; echo "$CORPO" | head -c 600; echo; } >> "$DET"
  elif [ "$C" = 000 ]; then BOOT_FALHA=$((BOOT_FALHA+1)); echo "boot_timeout $n" >> "$DET"
  else BOOT_OK=$((BOOT_OK+1)); fi
done
echo "efs_boot_ok=$BOOT_OK efs_boot_falha=$BOOT_FALHA efs_nao_invocadas=$NAO_INV"
[ "$BOOT_FALHA" = 0 ] && ok "todas as EFs invocadas sobem" || nok "$BOOT_FALHA EFs não sobem no self-host"
docker logs supabase-edge-functions 2>&1 | tail -200 >> "$DET"

# ---------- 11. veredito ----------
T_FIM=$(seg)
echo "t_total=${T_FIM}s"
echo "RESUMO: serviço vazio em ${T_UP}s; camada da plataforma aplicada em $((T_REST-T_UP))s; falhas=$FALHAS"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$R/k" -in "$DET" -out "${GITHUB_WORKSPACE:-.}/detalhe.txt.enc"
rm -f "$R/k"
[ "$FALHAS" = 0 ]
