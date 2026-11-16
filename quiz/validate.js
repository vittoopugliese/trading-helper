// Valida el banco de preguntas: formato, opciones duplicadas, preguntas repetidas
// y que index.html cargue todos los archivos de bank/.  Uso: node quiz/validate.js
const fs = require("fs"), path = require("path");
const dir = path.join(__dirname, "bank");
const items = [], errs = [];
global.QB = { add: (src, list) => list.forEach((q, i) => items.push({ src, i, q })) };
const files = fs.readdirSync(dir).filter(f => f.endsWith(".js")).sort();
for (const f of files) {
  try { eval(fs.readFileSync(path.join(dir, f), "utf8")); } catch (e) { errs.push(`${f}: ${e.message}`); }
}
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
for (const f of files) if (!html.includes(`bank/${f}`)) errs.push(`index.html no carga bank/${f}`);
const seen = new Map(), per = {};
for (const { src, i, q } of items) {
  per[src] = (per[src] || 0) + 1;
  const id = `${src}#${i}`;
  if (!Array.isArray(q) || typeof q[0] !== "string" || !q[0].trim()) { errs.push(`${id}: formato inválido`); continue; }
  if (q.length === 2) { if (typeof q[1] !== "boolean") errs.push(`${id}: V/F sin booleano`); }
  else if (q.length === 5) {
    const o = q.slice(1);
    if (o.some(x => typeof x !== "string" || !x.trim())) errs.push(`${id}: opción vacía`);
    else if (new Set(o.map(s => s.trim().toLowerCase())).size !== 4) errs.push(`${id}: opciones repetidas — ${q[0]}`);
  } else errs.push(`${id}: largo ${q.length} — ${q[0]}`);
  const k = q[0].trim().toLowerCase();
  if (seen.has(k)) errs.push(`${id}: pregunta repetida (= ${seen.get(k)}) — ${q[0]}`); else seen.set(k, id);
}
const tf = items.filter(x => x.q.length === 2).length;
console.table(per);
console.log(`Total: ${items.length} (${items.length - tf} de 4 opciones, ${tf} V/F)`);
if (errs.length) { console.error(errs.join("\n")); process.exit(1); }
console.log("OK");
