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
| `publicar-sites.yml` | publica cada site de `sites.json` no Cloudflare Pages quando o ramo do repo privado tem commit novo; no fim, o agendador em corrente (`scripts/agendar.py`) dispara as rotinas do dia e o próximo ciclo | ~5 min (corrente) + manual |
| `vigia-rodape.yml` | vigia do rodapé dos sites (código em `innconta-site/scripts/vigia_rodape`) via `scripts/rodar_rotina.py` | 11:30 UTC (corrente) + 14:30 `--rede` (cron do GitHub, só rede) |
| `ensaio-supabase.yml` | ensaio da reserva do Supabase (S3b/T5.9): self-host oficial + camada da plataforma cifrada (sem dado) + 99 EFs reais, só dado sintético; ~2 min | manual |

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
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (cofre `de-documentos-12set/innovasphere-platform/.env.local`, fonte única desde a rotação de 13/09), `NTFY_TOPICO` (`ntfy-central-tecnica.txt`), `HC_PING_PUBLICAR_SITES`, `DEPLOY_KEYS` (JSON montado de `_ces-secrets/deploy-keys-operacao-publica/<repo>`).

## Histórico
- 03/10/2026 (sessão fio fornecedores-resiliencia): criado; piloto `schifino-site`.


## Ensaio da reserva do Supabase (03/10/2026, fio fornecedores-resiliencia)
- `ensaio-supabase/ensaio.sh`; resultado e tempos em `OUTPUTS/fornecedores-resiliencia/pesquisa-alternativas-supabase/F-ensaio-selfhost-03out2026.md` (workspace).
- **Onde morde:** o `.env` oficial fixa `COMPOSE_FILE`: o `docker-compose.override.yml` (trava de saída) só carrega porque o script o acrescenta ali; sem isso o banco do ensaio alcança a produção (run 37134777423). `docker exec -i` dentro de `while read` engole o stdin do laço (usar `< /dev/null`). Passo com `tee` precisa de `shell: bash` (pipefail), senão falha vira success. O `hello` oficial só aceita as chaves novas; o teste usa `ensaio-eco`. Editar o script por Python com `\1` virou byte de controle `^A`: conferir com `cat -A`.
- Camada é retrato do schema de 03/10: recriar quando o schema mudar muito (roteiro no fio `fornecedores-resiliencia`).
