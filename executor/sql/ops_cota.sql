-- Regime de bypass por cota (CVO 30/10/2026: "crie o regime de bypass sem permitir que cheguemos ao 100% de nenhum deles,
-- bypass em 79% de uso"). Cada fornecedor com franquia tem a sua medição aqui (gravada pelo medidor do PC, medir_cotas.py, de
-- hora em hora); executor cujo fornecedor está em >= 79% da janela (mês ou dia) perde a vez no livro e a rodada vai a outro.
-- Medição velha (mais de 26 h) NÃO bloqueia (falha aberta) mas o vigia acusa à central. Fio prospeccao-parceiros-email-qualidade.
create table if not exists public.ops_cota (
  fornecedor text primary key,                 -- github-privado, gitlab, cloudflare-workers, vercel...
  executores text[] not null default '{}',     -- executores do livro que gastam esta cota (vazio = só vigiada)
  uso numeric not null, teto numeric not null, unidade text not null,
  janela text not null check (janela in ('mes', 'dia')),
  pct numeric generated always as (round(100 * uso / nullif(teto, 0), 1)) stored,
  medido_em timestamptz not null default now(), fonte text, detalhe jsonb,
  bypass_desde timestamptz, avisado_em timestamptz);
alter table public.ops_cota enable row level security;
revoke all on public.ops_cota from anon, authenticated;

create or replace function public.ops_cota_limite() returns numeric language sql immutable as $$ select 79::numeric $$;

-- medição vale enquanto está na mesma janela (mês/dia UTC) e tem menos de 26 h
create or replace function public.ops_cota_vigente(c ops_cota) returns boolean language sql stable as $$
  select c.medido_em > now() - interval '26 hours'
     and date_trunc(case c.janela when 'mes' then 'month' else 'day' end, c.medido_em at time zone 'UTC')
       = date_trunc(case c.janela when 'mes' then 'month' else 'day' end, now() at time zone 'UTC');
$$;

-- motivo do bypass do executor (null = pode rodar)
create or replace function public.ops_bypass(p_executor text) returns text language sql stable security definer set search_path = public as $$
  select string_agg(format('bypass: %s em %s%% (%s de %s %s, limite %s%%)', c.fornecedor, c.pct, c.uso, c.teto, c.unidade, ops_cota_limite()), '; ')
  from ops_cota c where p_executor = any(c.executores) and ops_cota_vigente(c) and c.pct >= ops_cota_limite();
$$;

create or replace function public.ops_cota_registrar(p_fornecedor text, p_uso numeric, p_teto numeric, p_unidade text, p_janela text,
  p_executores text[] default '{}', p_fonte text default null, p_detalhe jsonb default null) returns jsonb
language plpgsql security definer set search_path = public as $$
declare c ops_cota;
begin
  insert into ops_cota (fornecedor, executores, uso, teto, unidade, janela, medido_em, fonte, detalhe)
  values (p_fornecedor, p_executores, p_uso, p_teto, p_unidade, p_janela, now(), p_fonte, p_detalhe)
  on conflict (fornecedor) do update set executores = excluded.executores, uso = excluded.uso, teto = excluded.teto, unidade = excluded.unidade,
    janela = excluded.janela, medido_em = now(), fonte = excluded.fonte, detalhe = excluded.detalhe
  returning * into c;
  if c.pct >= ops_cota_limite() and c.bypass_desde is null then update ops_cota set bypass_desde = now() where fornecedor = p_fornecedor;
  elsif c.pct < ops_cota_limite() and c.bypass_desde is not null then update ops_cota set bypass_desde = null, avisado_em = null where fornecedor = p_fornecedor;
  end if;
  return jsonb_build_object('fornecedor', c.fornecedor, 'pct', c.pct, 'bypass', c.pct >= ops_cota_limite());
end $$;

-- livro: executor em bypass não leva a rodada (os outros levam)
create or replace function public.ops_reivindicar(p_rotina text, p_executor text, p_lease_min int default 120) returns jsonb
language plpgsql security definer set search_path = public as $$
declare s timestamptz; e ops_execucao; b text;
begin
  s := ops_slot_atual(p_rotina);
  if s is null then return jsonb_build_object('ok', false, 'motivo', 'sem rodada devida'); end if;
  b := ops_bypass(p_executor);
  if b is not null then return jsonb_build_object('ok', false, 'slot', s, 'motivo', b, 'bypass', true); end if;
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

-- despertadores só acordam o GitLab: com o GitLab em bypass a contagem vira 0 (não gastam o que resta dele)
create or replace function public.ops_atrasadas_qtd() returns int language sql stable security definer set search_path = public as
$$ select case when ops_bypass('gitlab') is not null then 0 else (select count(*)::int from ops_atrasadas(20)) end $$;

-- vigia (de hora em hora, :55): fornecedor que entrou em bypass ou medição parada = e-mail à central técnica (AOP-01), uma vez por janela
create or replace function public.ops_vigia_cotas() returns int language plpgsql security definer set search_path = public as $$
declare l text := ''; n int := 0; c ops_cota;
begin
  for c in select * from ops_cota o where (o.bypass_desde is not null and o.avisado_em is null and ops_cota_vigente(o))
                                       or (not ops_cota_vigente(o) and (o.avisado_em is null or o.avisado_em < now() - interval '24 hours')) loop
    n := n + 1;
    if ops_cota_vigente(c) then
      l := l || format('<li><b>%s</b>: %s%% (%s de %s %s). Executores fora da vez: %s. %s</li>', c.fornecedor, c.pct, c.uso, c.teto, c.unidade,
        coalesce(nullif(array_to_string(c.executores, ', '), ''), 'nenhum (só vigiado)'), coalesce(c.detalhe->>'acao', ''));
    else
      l := l || format('<li><b>%s</b>: medição parada desde %s (o bypass dele não protege). Ação: tarefa Revezamento-Cotas no PC, log em C:/Users/raphe/_ferramentas/revezamento/log.</li>',
        c.fornecedor, to_char(c.medido_em at time zone 'America/Sao_Paulo', 'DD/MM HH24:MI'));
    end if;
    update ops_cota set avisado_em = now() where fornecedor = c.fornecedor;
  end loop;
  if n > 0 then
    perform ic_email('central@innconta.com.br', '[ALERTA] Cotas: ' || n || ' fornecedor(es) em bypass (>= ' || ops_cota_limite() || '%) ou sem medição',
      '<p><b>O que mudou:</b> o regime de bypass tirou da vez quem passou de ' || ops_cota_limite() || '% da franquia, para nenhum chegar a 100%.</p><ul>' || l ||
      '</ul><p><b>Ação:</b> nada a fazer se os outros executores estão rodando (ver <code>ops_execucao</code>); a vez volta sozinha na virada da janela. ' ||
      'Painel: <code>select fornecedor, pct, uso, teto, medido_em from ops_cota</code>.</p>');
  end if;
  return n;
end $$;

revoke all on function public.ops_cota_vigente(ops_cota), public.ops_bypass(text), public.ops_cota_registrar(text, numeric, numeric, text, text, text[], text, jsonb),
  public.ops_reivindicar(text, text, int), public.ops_atrasadas_qtd(), public.ops_vigia_cotas() from public, anon, authenticated;
grant execute on function public.ops_atrasadas_qtd() to anon;
select cron.schedule('ops_vigia_cotas', '55 * * * *', 'select public.ops_vigia_cotas()');