-- Despertador pg_cron do revezamento (30/10/2026): havendo rodada atrasada (nenhum executor pegou em 20 min), acorda o
-- executor GitLab pelo gatilho do projeto (ic_segredo 'revezamento_gitlab_gatilho' = "<projeto>:<token>"). Cada acordar
-- (de qualquer despertador que escreva aqui) fica em ops_despertar.
create table if not exists public.ops_despertar (id bigserial primary key, quem text not null, em timestamptz not null default now(),
  atrasadas int, detalhe text);
alter table public.ops_despertar enable row level security;
revoke all on public.ops_despertar from anon, authenticated;
create or replace function public.ops_despertar_pgcron(p_forcar boolean default false) returns int
language plpgsql security definer set search_path = public as $$
declare n int := ops_atrasadas_qtd(); g text := ic_segredo_valor('revezamento_gitlab_gatilho'); rid bigint;
begin
  if n = 0 and not p_forcar then return 0; end if;
  if g is null or position(':' in g) = 0 then
    insert into ops_despertar(quem, atrasadas, detalhe) values ('pgcron', n, 'sem gatilho em ic_segredo'); return -1;
  end if;
  select net.http_post(url := 'https://gitlab.com/api/v4/projects/' || split_part(g, ':', 1) || '/trigger/pipeline',
         body := jsonb_build_object('token', split_part(g, ':', 2), 'ref', 'main'), headers := '{"Content-Type":"application/json"}'::jsonb) into rid;
  insert into ops_despertar(quem, atrasadas, detalhe) values ('pgcron', n, 'pedido pg_net ' || rid || case when p_forcar then ' (forçado)' else '' end);
  return n;
end $$;
revoke all on function public.ops_despertar_pgcron(boolean) from public, anon, authenticated;
select cron.schedule('ops_despertar_pgcron', '7,27,47 * * * *', 'select public.ops_despertar_pgcron()');
