-- Vigia das sentinelas do revezamento (30/10/2026): 04:30 UTC, toda sentinela ops-prova-<executor> sem 'ok' na rodada de hoje
-- (03:10 UTC) = e-mail à central técnica (AOP-01), com o executor, o estado visto e a ação.
create or replace function public.ops_vigia_sentinelas() returns int language plpgsql security definer set search_path = public as $$
declare l text := ''; n int := 0; r record;
begin
  for r in select o.nome, e.estado, e.detalhe from ops_rotina o left join ops_execucao e on e.rotina = o.nome and e.slot = ops_slot_atual(o.nome)
           where o.ativa and o.nome like 'ops-prova-%' and coalesce(e.estado, '') <> 'ok' loop
    n := n + 1; l := l || '<li><code>' || r.nome || '</code>: ' || coalesce(r.estado || ' · ' || coalesce(r.detalhe, ''), 'não rodou') || '</li>';
  end loop;
  if n > 0 then
    perform ic_email('central@innconta.com.br', '[ALERTA] Revezamento: ' || n || ' executor(es) sem sinal de vida hoje',
      '<p><b>O que quebrou:</b> a sentinela diária (03:10 UTC) destes executores do revezamento de rotinas não fechou ok.</p><ul>' || l ||
      '</ul><p><b>Ação:</b> pc = tarefa Revezamento-PC e log em C:/Users/raphe/_ferramentas/revezamento/log; oracle = <code>journalctl -t revezamento</code> na evo-pessoal-01; ' ||
      'gitlab = pipelines de zarkatus-mirror/revezamento-executor (minutos do mês?); github = corrente do operacao-publica. As rotinas seguem pelos outros executores.</p>');
  end if;
  return n;
end $$;
revoke all on function public.ops_vigia_sentinelas() from public, anon, authenticated;
select cron.schedule('ops_vigia_sentinelas', '30 4 * * *', 'select public.ops_vigia_sentinelas()');
