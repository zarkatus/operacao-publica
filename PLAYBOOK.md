# operacao-publica · PLAYBOOK

Rotinas de alta frequência do grupo rodando em repositório **público**, onde o GitHub Actions é grátis e ilimitado.
Código público; dado, conteúdo privado e segredo ficam fora (repos privados, GitHub Secrets, banco).

## Regra de origem (CVO, 03/10/2026)
"Tudo que tem essa frequência absurda, entenda primeiro se é necessário e se for, coloque em repositório público com
minutos ilimitados." / "Grátis ilimitados. Sempre assim. Custo zero imperativo pétreo."
A franquia de repo privado (2.000 min/mês, orçamento US$ 0 com Stop usage) esgota e para TODO workflow privado de uma vez.

## O que roda aqui
| workflow | o que faz | frequência |
|---|---|---|
| `publicar-sites.yml` | publica cada site de `sites.json` no Cloudflare Pages (e, na entrada `tipo: supabase-funcoes`, as Edge Functions da plataforma) quando o ramo do repo privado tem commit novo; no fim, o agendador em corrente (`scripts/agendar.py`) dispara as rotinas do dia e o próximo ciclo | ~5 min (corrente) + manual |
| `vigia-rodape.yml` | vigia do rodapé dos sites (código em `innconta-site/scripts/vigia_rodape`) via `scripts/rodar_rotina.py` | 11:30 UTC (corrente) + 14:30 `--rede` (cron do GitHub, só rede) |
| `ensaio-supabase.yml` | ensaio da reserva do Supabase (S3b/T5.9): self-host oficial + camada da plataforma cifrada (sem dado) + 99 EFs reais, só dado sintético; ~2 min | mensal pelo agendador (`intervalo_dias: 30`) + manual |
| `plat-*.yml`, `ic-*.yml` (11) | rotinas de `innovasphere-platform` (digest, audit, ia-worker, vigia-endpoints, comunicado, RDO, SINAPI) e `innconta-site` (sanções rápidas e lentas, 2 radares) via `scripts/rodar_rotina.py`; migradas em 17/10/2026 | agendador (`rotinas.json`: horário + `dias_semana`/`dias_mes`) + manual (`-f ensaio=true` = modo ensaio do script) |

## Como funciona o publicador (`scripts/publicar.py`)
- Lê o HEAD do ramo pelo `git ls-remote` com **deploy key só leitura** daquele repo (segredo único `DEPLOY_KEYS`, JSON repo→chave).
- Compara com o `commit_hash` da última publicação de produção **bem-sucedida** no Pages (a verdade mora lá; sem arquivo de estado).
- Se mudou: clone raso, roda as `etapas` de curadoria **do próprio repo** (lista de permissão, travas), `wrangler@3 pages deploy`.
- Opcionais por site: `historico: true` (clone com histórico e tags, sem blobs antigos: a curadoria da plataforma usa `git describe`) e `caminhos` (pathspecs do git: commit que não toca neles fica "sem mudança de app", lembrado em `estado/falhas.json` → `_sem_app`, e não republica nem troca a versão de quem está usando).
- Plataforma (`innovasphere-platform`, 03/10/2026): curadoria em `scripts/montar_publicar.sh` do próprio repo, provada idêntica ao `deploy.yml` antigo (313 arquivos, `diff -r` vazio); `caminhos` = complemento do que a curadoria exclui.
- Publicar agora, sem esperar o ciclo: `gh workflow run publicar-sites.yml -R zarkatus/operacao-publica -f site=<repo>`.

## Onde ele morde
- **Log público é mudo por desenho.** A curadoria imprime nomes de arquivos privados quando reprova; por isso toda saída de etapa e do wrangler vai a arquivo, e o log mostra só "publicado/em dia/FALHOU, etapa N". O detalhe vai à central técnica (ntfy, AOP-01). Nunca trocar isso por `print` da saída.
- **As etapas não recebem os segredos do publicador** (o script tira todos do ambiente ao iniciar; segredos entram por nome, nunca `toJSON(secrets)`, que o GitHub marca como malicioso e segura a run).
- Commit que reprovou fica em `estado/falhas.json` (cache do Actions): não é retentado nem reavisado a cada 5 min; volta com commit novo ou `forcar=true`.
- **O cron do GitHub NÃO é confiável** (02/10: o vigia "das 11:30" rodou 16:43; 03/10 não rodou; o `*/5` deste repo levou >40 min sem 1º disparo). Agendamento real = corrente: cada ciclo dispara o próximo com o `GITHUB_TOKEN` (workflow_dispatch é o evento que esse token pode gerar). Rotina nova com horário: `horarios_utc` em `rotinas.json`. Sem PAT, sem pg_cron.
- Rotinas de repo privado (`scripts/rodar_rotina.py`): mesmo log mudo; segredos da rotina entram por nome no workflow dela; os do operador saem do ambiente antes do código privado rodar.
- Cron de repo público **é desligado pelo GitHub após 60 dias sem atividade**: o último passo reativa o próprio workflow a cada rodada agendada. Rede de fora: ping do Healthchecks (`HC_PING_PUBLICAR_SITES`), que avisa a central se o ciclo parar.
- Ao migrar um site para cá, **desligar o `deploy.yml` do repo privado** (`gh workflow disable deploy.yml -R zarkatus/<repo>`): ele continua lá como reserva manual.
- Gatilho em `pull_request` é proibido neste repo (forks); só `schedule` e `workflow_dispatch`.

## Segredos (nomes; valores no cofre, índice em INVENTARIO.md)
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (cofre `de-documentos-12set/innovasphere-platform/.env.local`, fonte única desde a rotação de 13/09), `NTFY_TOPICO` (`ntfy-central-tecnica.txt`), `HC_PING_PUBLICAR_SITES`, `DEPLOY_KEYS` (JSON montado de `_ces-secrets/deploy-keys-operacao-publica/<repo>`). `ESPELHO_CHAVES` (`espelho-gitlab-publico-chaves.json`, regravado pelo `chaves_espelho_publico.py`), `GITLAB_MIRROR_PAT` (`gitlab-mirror-pat.txt`), `HC_PING_ESPELHO_GITLAB` (`pulso-check-espelho-gitlab-publico.txt`).

## Histórico
- 03/10/2026 (sessão fio fornecedores-resiliencia): criado; piloto `schifino-site`.


## Ensaio da reserva do Supabase (03/10/2026, fio fornecedores-resiliencia)
- `ensaio-supabase/ensaio.sh`; resultado e tempos em `OUTPUTS/fornecedores-resiliencia/pesquisa-alternativas-supabase/F-ensaio-selfhost-03out2026.md` (workspace).
- **Onde morde:** o `.env` oficial fixa `COMPOSE_FILE`: o `docker-compose.override.yml` (trava de saída) só carrega porque o script o acrescenta ali; sem isso o banco do ensaio alcança a produção (run 37134777423). `docker exec -i` dentro de `while read` engole o stdin do laço (usar `< /dev/null`). Passo com `tee` precisa de `shell: bash` (pipefail), senão falha vira success. O `hello` oficial só aceita as chaves novas; o teste usa `ensaio-eco`. Editar o script por Python com `\1` virou byte de controle `^A`: conferir com `cat -A`.
- **Camada (04/10/2026, T5.10 p3):** não mora mais aqui. A VM do Google a gera todo dia às 07:20 UTC do dump diário (`innovasphere-platform/infra/vm-gcp-diversificacao/opt-camada-ensaio/`, provada byte a byte contra a manual de 03/10) e o ensaio baixa por `ssh pullcamada@<host de CAMADA_SSH_KH>` (comando forçado: só devolve o arquivo cifrado). Camada com mais de 50 h = FALHOU. Falha do ensaio avisa a central técnica por ntfy; `simular_falha=true` prova o aviso sem subir nada (e o agendador não conta esse run).
- **Rotina mensal:** `rotinas.json` aceita `intervalo_dias`; o agendador dispara se não houve run REAL nos últimos N dias, então um dia com a corrente quebrada não perde o mês. Run manual também conta e empurra o próximo para 30 dias depois.

## Rotinas migradas dos repos privados (17/10/2026, fio actions-redundancia)
- **Por quê:** o Actions de repo PRIVADO da conta zarkatus parou de vez ("recent account payments have failed"); repo público segue grátis e não é afetado. Código continua no privado; aqui só roda.
- **Segredos com prefixo `PLAT_`** = projeto Supabase da plataforma (`dqknyhfr`); `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` sem prefixo = projeto do **InnConta** (outro projeto, sem tabela `ic_*` na plataforma). Trocar um pelo outro grava no banco errado.
- **Service role da plataforma = `sb_secret` `github_actions_plataforma`** (a JWT legada morreu em 02/10; sb_secret vale em `Authorization`+`apikey`, inclusive EF com `verify_jwt`).
- **Calendário no `rotinas.json`:** `inicio` (AAAA-MM-DD), `dias_semana` (0=seg..6=dom), `dias_mes`. Run com "ensaio" ou "simula" no título não conta como a do dia.
- **Ensaio por rotina:** digest `--dry-run`, comunicado/RDO `NO_SEND=1` (comunicado também não zera a aprovação), radares `--so-ler`; as demais rodam de verdade (leitura ou carga idempotente).
- **Privado desligado** (`gh workflow disable`) depois do ensaio verde, para não rodar em dobro quando a cobrança voltar; religar = `gh workflow enable` lá e remover a entrada aqui.
- **Rotinas que comitam de volta** (`plat-snapshot-funcoes`, `ic-snapshot-funcoes`, `ic-pgfn`, `ic-cnpj-recorte`, `ic-acompanhamento-semanal`; 17/10/2026): `"escrita": true` no `rotinas.json` usa a deploy key de **escrita** daquele repo (`DEPLOY_KEYS_ESCRITA`, cofre `deploy-keys-operacao-publica-escrita/`, hoje só `innconta-site` e `innovasphere-platform`), referenciada SÓ nos workflows dessas rotinas; o git dos comandos herda o ssh dela. Todo commit faz `pull --rebase` antes do push (a falha de 12/10 do acompanhamento era push rejeitado por corrida).
- `GITHUB_OUTPUT` dos comandos é um arquivo do tmp da rotina: script que grava `nada_novo=true`/`com_segredo=N` é lido pelos comandos seguintes (`grep -qx "nada_novo=true" "$GITHUB_OUTPUT"`).
- PGFN não publica mais por conta própria: comita `dados/pgfn-radar-*.json` e o `publicar-sites` (entrada `innconta-site`) publica pela curadoria.
- Acompanhamento: a federação com o Google é do provedor `github-innconta`; se ele recusar este repo, o passo é tolerante e o relatório marca o Search Console como pendente. `BING_API_KEY` não está no cofre (só no secret do privado; GitHub não devolve valor): sem ela o Bing fica pendente.

## Deploys por push dos privados (17/10/2026, fio actions-redundancia)
- **Regra (CVO 17/10):** "não vou regularizar, é custo zero e pronto". Nenhum deploy depende do Actions privado; tudo que publicava por `push` lá entrou em `sites.json`: `innconta-site` (reserva do hook pre-push local), `innc-site`, `crm-picarras-hills`, `app-picarrashills`, `fazendapremium-site`, `estanciaferrari-site`, `pwa-clientes-picarrashills`, `carteira-inncorporate`, e, por `id`, `ces-reuniao`, `plat-cliente-portal`, `plat-portal-fiscalizacao`, `plat-edge-functions`.
- **`id`**: o estado (`estado/falhas.json`) e o filtro `SITE=` usam `id` (ou `repo`); entrada extra de um repo que já publica outro projeto precisa de `id`, senão uma pisa no estado da outra.
- **Projeto que é subpasta do repo** (`reuniao/`, `cliente-portal/`): `historico: true` + `caminhos` com a pasta, senão todo commit do repo republica o projeto.
- **Edge Functions (`tipo: supabase-funcoes`)**: o Supabase não guarda commit; o último publicado vive no cache (`_funcoes`), e sem cache vale `base_sha`. Publica só as funções tocadas pelo diff (`CHANGED_FILES`) com o `scripts/ci-deploy-functions.mjs` do próprio repo, que já trata `_shared`, `verify_jwt` e 429. O PAT entra só no publicador (`PLAT_SUPABASE_MANAGEMENT_PAT`) e é passado ao script desta entrada, nunca às etapas de curadoria. `forcar=true` nesta entrada = TODAS as funções.
- `crm-redirect` da plataforma **não** migrou: parado desde 05/2026 e publicaria no mesmo projeto `crm-picarras-hills` que o repo do CRM.

## Espelho GitHub -> GitLab sem VM (24/10/2026, fio diversificacao-vms, sessão a56bf4a7)
- **O que é:** `scripts/espelho_gitlab.py` + `.github/workflows/espelho-gitlab.yml`, disparado pelo agendador (`rotinas.json`, `espelho-gitlab`, 00:20/06:20/12:20/18:20 UTC). Substitui o espelho da gcp-01 (`/opt/espelho-gitlab`, cache de 4,6 GB num disco de 29 GB). Não usa `rodar_rotina.py`: não há código de repo privado a rodar.
- **Compara antes de clonar:** `ls-remote --heads --tags --refs` nos dois lados; igual = pula. Rodada típica: ~2,5 min para 43 repos, nada em disco. Mudou = `clone --mirror` num diretório temporário + `push --prune` de heads e tags.
- **Log mudo de nomes (repo público):** só `repo #NN` (índice na ordem alfabética das chaves) e contagens; os nomes só vão no ntfy da central. Provado em 24/10: 0 nomes no log do run 40138905799.
- **Lista de repos = chaves do segredo `ESPELHO_CHAVES`.** Repo novo ganha chave (deploy key só leitura, título `espelho-gitlab-publico`) pelo `chaves_espelho_publico.py` do notebook (`PROJECTS/espelho-sites-repos/`), que a tarefa Espelho-GitLab chama a cada ciclo; ele regrava o segredo a partir do cofre.
- **Prova 24/10:** commit novo chegou ao GitLab (`e785307`); ramo de teste criado → espelhado → apagado no GitHub → sumiu do GitLab na rodada seguinte (resíduo 0 nos dois lados). Pulso `espelho-gitlab-publico` em `up`.
- **Dead-man:** Pulso (`HC_PING_ESPELHO_GITLAB`, 6 h + 3 h); `/fail` quando algum repo falha nas 2 passadas. PAT recusado (401/403) é tratado como credencial, nunca como queda (AOP-01c).

## Ronda externa a cada 30 min e `a_cada_min` no agendador (25/10/2026, fio diversificacao-vms, sessão e86b204a)
- **O quê:** rotina `ronda-externa` (`rotinas.json`, workflow `ronda-externa.yml`) roda `infra/pandastack/ronda-externa/handler.py` do `innovasphere-platform` e confere de fora da Oracle e do Google as superfícies públicas da Valida (`alvos.json` de lá; alvo novo = linha nova lá). Saída 1 = algum alvo falhou → `rodar_rotina.py` avisa a central (ntfy). Substitui o PandaStack como camada principal (crédito Free esgotado até 01/11); o PandaStack segue de reserva de hora em hora.
- **`a_cada_min: N`** (novo em `scripts/agendar.py`): devida se não houve run do workflow nos últimos N minutos; **uma** consulta por ciclo. Não usar dezenas de `horarios_utc` para frequência alta: cada horário vencido custa uma chamada à API em CADA ciclo de 5 min (48 horários chegariam a ~570 chamadas/h, perto do teto de 1000/h do `GITHUB_TOKEN`).
- **Prova (25/10):** run manual verde ("comando 1: ok"); ciclo do agendador com o código novo verde e sem disparar a ronda antes dos 30 min (controle negativo); controle negativo local do handler com alvo falso → saída 1 e nome do alvo.
