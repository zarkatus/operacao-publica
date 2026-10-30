-- Revezamento de executores das rotinas do grupo (CVO 30/10/2026: "2 fornecedores são muito pouco, vamos chegar ao menos a 8").
-- Qualquer executor (GitHub público, PC, Oracle, GitLab) pede a rodada; o primeiro leva, os outros pulam. Despertadores
-- (Google, pg_cron, Cloudflare, Healthchecks) leem ops_atrasadas_qtd() e acordam um executor. Agenda = rotinas.json do
-- operacao-publica, espelhada aqui por ops_rotina_sincronizar. Fio prospeccao-parceiros-email-qualidade.
create table if not exists public.ops_rotina (
  nome text primary key, horarios_utc text[] not null default '{}', dias_semana int[], dias_mes int[], inicio date,
  executores text[] not null default '{}', ativa boolean not null default true, atualizado timestamptz not null default now());
create table if not exists public.ops_execucao (
  rotina text not null, slot timestamptz not null, executor text not null,
  estado text not null check (estado in ('rodando','ok','falhou')), tentativas int not null default 1,
  iniciou timestamptz not null default now(), terminou timestamptz, lease_ate timestamptz, detalhe text,
  primary key (rotina, slot));
alter table public.ops_rotina enable row level security;
alter table public.ops_execucao enable row level security;
revoke all on public.ops_rotina, public.ops_execucao from anon, authenticated;

-- último horário devido <= p_agora (dias_semana 0=seg, como o weekday() do Python do agendador)
create or replace function public.ops_slot_atual(p_nome text, p_agora timestamptz default now()) returns timestamptz
language plpgsql stable set search_path = public as $$
declare r ops_rotina; d date; h text; ts timestamptz; melhor timestamptz;
begin
  select * into r from ops_rotina where nome = p_nome and ativa;
  if not found then return null; end if;
  for i in 0..35 loop
    d := (p_agora at time zone 'UTC')::date - i;
    if r.inicio is not null and d < r.inicio then exit; end if;
    if r.dias_semana is not null and (extract(isodow from d)::int - 1) <> all(r.dias_semana) then continue; end if;
    if r.dias_mes is not null and extract(day from d)::int <> all(r.dias_mes) then continue; end if;
    foreach h in array r.horarios_utc loop
      ts := (d + h::time) at time zone 'UTC';
      if ts <= p_agora and (melhor is null or ts > melhor) then melhor := ts; end if;
    end loop;
    if melhor is not null then return melhor; end if;
  end loop;
  return null;
end $$;

create or replace function public.ops_reivindicar(p_rotina text, p_executor text, p_lease_min int default 120) returns jsonb
language plpgsql security definer set search_path = public as $$
declare s timestamptz; e ops_execucao;
begin
  s := ops_slot_atual(p_rotina);
  if s is null then return jsonb_build_object('ok', false, 'motivo', 'sem rodada devida'); end if;
  insert into ops_execucao (rotina, slot, executor, estado, lease_ate) values (p_rotina, s, p_executor, 'rodando', now() + make_interval(mins => p_lease_min))
  on conflict (rotina, slot) do nothing;
  if found then return jsonb_build_object('ok', true, 'slot', s); end if;
  select * into e from ops_execucao where rotina = p_rotina and slot = s for update;
  if e.estado = 'ok' then return jsonb_build_object('ok', false, 'slot', s, 'motivo', 'já feita por ' || e.executor); end if;
  if e.estado = 'rodando' and e.lease_ate > now() then return jsonb_build_object('ok', false, 'slot', s, 'motivo', 'rodando em ' || e.executor); end if;
  if e.tentativas >= 3 then return jsonb_build_object('ok', false, 'slot', s, 'motivo', 'falhou 3 vezes; esperar a correção'); end if;
  update ops_execucao set executor = p_executor, estado = 'rodando', tentativas = tentativas + 1, iniciou = now(), terminou = null,
         lease_ate = now() + make_interval(mins => p_lease_min) where rotina = p_rotina and slot = s;
  return jsonb_build_object('ok', true, 'slot', s, 'retomada_de', e.executor);
end $$;

create or replace function public.ops_concluir(p_rotina text, p_slot timestamptz, p_executor text, p_ok boolean, p_detalhe text default null) returns void
language sql security definer set search_path = public as $$
  update ops_execucao set estado = case when p_ok then 'ok' else 'falhou' end, terminou = now(), lease_ate = null, detalhe = left(p_detalhe, 2000)
  where rotina = p_rotina and slot = p_slot and executor = p_executor;
$$;

create or replace function public.ops_rotina_sincronizar(p jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare k text; v jsonb; n int := 0;
begin
  for k, v in select * from jsonb_each(p) loop
    if not (v ? 'executores') then continue; end if;
    insert into ops_rotina (nome, horarios_utc, dias_semana, dias_mes, inicio, executores, ativa, atualizado)
    values (k, array(select jsonb_array_elements_text(coalesce(v->'horarios_utc','[]'))),
            case when v ? 'dias_semana' then array(select jsonb_array_elements_text(v->'dias_semana')::int) end,
            case when v ? 'dias_mes' then array(select jsonb_array_elements_text(v->'dias_mes')::int) end,
            (v->>'inicio')::date, array(select jsonb_array_elements_text(v->'executores')), true, now())
    on conflict (nome) do update set horarios_utc = excluded.horarios_utc, dias_semana = excluded.dias_semana, dias_mes = excluded.dias_mes,
      inicio = excluded.inicio, executores = excluded.executores, ativa = true, atualizado = now();
    n := n + 1;
  end loop;
  update ops_rotina set ativa = false where nome not in (select key from jsonb_each(p) where value ? 'executores');
  return n;
end $$;

-- rodadas devidas há > p_tol_min sem 'ok' e sem execução viva
create or replace function public.ops_atrasadas(p_tol_min int default 20) returns table (rotina text, slot timestamptz, estado text)
language sql stable security definer set search_path = public as $$
  select r.nome, s.slot, e.estado from ops_rotina r
  cross join lateral (select ops_slot_atual(r.nome, now() - make_interval(mins => p_tol_min)) slot) s
  left join ops_execucao e on e.rotina = r.nome and e.slot = s.slot
  where r.ativa and s.slot is not null
    and (e.rotina is null or (e.estado = 'falhou' and e.tentativas < 3) or (e.estado = 'rodando' and e.lease_ate < now()));
$$;
-- só a contagem, para despertador sem chave de serviço (Cloudflare, Healthchecks): não expõe nome nem dado
create or replace function public.ops_atrasadas_qtd() returns int language sql stable security definer set search_path = public as
$$ select count(*)::int from ops_atrasadas(20) $$;

revoke all on function public.ops_slot_atual(text, timestamptz), public.ops_reivindicar(text, text, int), public.ops_concluir(text, timestamptz, text, boolean, text),
  public.ops_rotina_sincronizar(jsonb), public.ops_atrasadas(int), public.ops_atrasadas_qtd() from public, anon, authenticated;
grant execute on function public.ops_atrasadas_qtd() to anon;
