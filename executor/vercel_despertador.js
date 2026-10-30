// Despertador Vercel do revezamento de rotinas (30/10/2026, fio prospeccao-parceiros-email-qualidade): cron diário (teto do plano Hobby)
// lê no livro do InnConta só a CONTAGEM de rodadas atrasadas (chave anônima pública); havendo, acorda o executor GitLab.
// Cron do Vercel chama com Authorization: Bearer CRON_SECRET. GET ?forcar=1 com x-prova = PROVA acorda sempre (prova manual).
// Segredos (env): SB_ANON, GATILHO ("<projeto>:<token>"), PROVA, CRON_SECRET.
async function despertar(forcar) {
  const e = process.env;
  const r = await fetch("https://gprkopmqhocsslrdmebm.supabase.co/rest/v1/rpc/ops_atrasadas_qtd", { method: "POST",
    headers: { apikey: e.SB_ANON, Authorization: "Bearer " + e.SB_ANON, "Content-Type": "application/json" }, body: "{}" });
  if (!r.ok) return "livro inacessível " + r.status;
  const n = await r.json();
  if (!(n > 0) && !forcar) return "ok: 0 atrasadas";
  const [p, t] = [e.GATILHO.split(":")[0], e.GATILHO.split(":").slice(1).join(":")];
  const f = new FormData(); f.append("token", t); f.append("ref", "main");
  const g = await fetch(`https://gitlab.com/api/v4/projects/${p}/trigger/pipeline`, { method: "POST", body: f });
  return `vercel: ${n} atrasada(s), GitLab acordado http=${g.status}`;
}
export default async function handler(req, res) {
  const forcar = req.query.forcar === "1" && req.headers["x-prova"] === process.env.PROVA;
  const cron = req.headers["authorization"] === "Bearer " + process.env.CRON_SECRET;
  if (!forcar && !cron) return res.status(200).send("ok");
  const out = await despertar(forcar); console.log(out); res.status(200).send(out);
}
