// Despertador Cloudflare do revezamento de rotinas (30/10/2026, fio prospeccao-parceiros-email-qualidade): a cada 30 min
// lê no livro do InnConta só a CONTAGEM de rodadas atrasadas (chave anônima pública); havendo, acorda o executor GitLab.
// GET /?forcar=1 com o cabeçalho x-prova = PROVA acorda sempre (prova manual). Segredos: SB_ANON, GATILHO ("<projeto>:<token>"), PROVA.
async function despertar(env, forcar) {
  const r = await fetch("https://gprkopmqhocsslrdmebm.supabase.co/rest/v1/rpc/ops_atrasadas_qtd", { method: "POST",
    headers: { apikey: env.SB_ANON, Authorization: "Bearer " + env.SB_ANON, "Content-Type": "application/json" }, body: "{}" });
  if (!r.ok) return "livro inacessível " + r.status;
  const n = await r.json();
  if (!(n > 0) && !forcar) return "ok: 0 atrasadas";
  const [p, t] = [env.GATILHO.split(":")[0], env.GATILHO.split(":").slice(1).join(":")];
  const f = new FormData(); f.append("token", t); f.append("ref", "main");
  const g = await fetch(`https://gitlab.com/api/v4/projects/${p}/trigger/pipeline`, { method: "POST", body: f });
  return `cloudflare: ${n} atrasada(s), GitLab acordado http=${g.status}`;
}
export default {
  async scheduled(ev, env, ctx) { ctx.waitUntil(despertar(env, false).then(console.log)); },
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.searchParams.get("forcar") === "1" && req.headers.get("x-prova") === env.PROVA) return new Response(await despertar(env, true));
    return new Response("ok", { status: 200 });
  }
};
