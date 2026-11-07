// CPP-01 (CVO 07/11/2026) · verificador do pacto 95% no deploy. Copia IDENTICA em todo repo do grupo (scripts/pacto95-selo.mjs);
// fonte canonica: painel-controle/pacto95-selo.mjs. Roda no CI/Vercel/hook SEM a escrituracao (nenhum limite vai ao repo).
// Uso: node scripts/pacto95-selo.mjs [raiz]   -> 0 liberado; 2 RECUSADO (falha fechada).
// Exige: (1) pacto95.json na raiz; (2) pacto95.selo com o sha256 do manifesto (so o pacto95_gate.py --selar grava, apos validar
// contra a escrituracao); (3) toda superficie de consumo derivada do CODIGO consta no manifesto pelo campo "fonte":
//   supabase/functions/<nome>/ (Edge Function)  e  .github/workflows/<arq>.yml com schedule: (cron do GitHub Actions).
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto'; import { execSync } from 'node:child_process';
const raiz = path.resolve(process.argv[2] || '.');
const recusa = (m) => { console.error('RECUSADO (CPP-01 pacto95): ' + m + '\n  Corrija: escriture o consumidor em painel-controle/dados/cotas_95_manual.json, ponha-o em pacto95.json e rode no PC: python pacto95_gate.py <repo>/pacto95.json --selar'); process.exit(2); };
const ler = (p) => { try { return fs.readFileSync(path.join(raiz, p), 'utf8'); } catch { return null; } };
const bruto = ler('pacto95.json'); if (bruto == null) recusa('pacto95.json ausente na raiz: produto sem manifesto nao publica.');
let man; try { man = JSON.parse(bruto); } catch (e) { recusa('pacto95.json ilegivel: ' + e.message); }
const hash = crypto.createHash('sha256').update(bruto.replace(/\r/g, '')).digest('hex');
let selo; try { selo = JSON.parse(ler('pacto95.selo') || 'null'); } catch { selo = null; }
if (!selo || selo.sha256 !== hash) recusa('pacto95.selo ' + (selo ? 'nao confere com o manifesto atual (manifesto mudou sem passar pelo portao)' : 'ausente') + '.');
let arquivos = null;
try { arquivos = execSync('git ls-files -- supabase/functions .github/workflows', { cwd: raiz, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split('\n').filter(Boolean); } catch { }
if (!arquivos || !arquivos.length) { arquivos = []; const anda = (d) => { let es = []; try { es = fs.readdirSync(path.join(raiz, d), { withFileTypes: true }); } catch { return; } for (const e of es) { const r = d + '/' + e.name; e.isDirectory() ? anda(r) : arquivos.push(r); } }; anda('supabase/functions'); anda('.github/workflows'); }
const sup = new Set();
for (const a of arquivos) {
  const m = a.match(/^supabase\/functions\/([^/]+)\//); if (m && !/^[_.]/.test(m[1])) sup.add('supabase/functions/' + m[1]);
  if (/^\.github\/workflows\/[^/]+\.ya?ml$/.test(a)) { let t = ler(a); if (t == null) { try { t = execSync('git show HEAD:' + a, { cwd: raiz, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch { t = ''; } } if (/^\s*schedule\s*:/m.test(t)) sup.add(a); }
}
const declarados = new Set((man.consumidores || []).map((c) => c.fonte).filter(Boolean));
const faltam = [...sup].filter((f) => !declarados.has(f)).sort();
if (faltam.length) recusa(faltam.length + ' consumidor(es) no codigo sem manifesto:\n  - ' + faltam.join('\n  - '));
const sobra = [...declarados].filter((f) => !sup.has(f)); if (sobra.length) console.log('pacto95: aviso, ' + sobra.length + ' fonte(s) no manifesto ja nao existem no codigo (pode limpar): ' + sobra.slice(0, 5).join(', '));
console.log('pacto95: liberado · ' + (man.produto || '?') + ' · ' + (man.consumidores || []).length + ' consumidor(es) · superficie ' + sup.size + ' · selo ' + hash.slice(0, 12) + ' de ' + (selo.selado_em || '?'));
