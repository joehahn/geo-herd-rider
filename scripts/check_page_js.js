// Execute a dashboard's inline JS against a Plotly/DOM stub and report which panels DREW.
// A page can be syntactically valid and still render half its plots: one uncaught throw inside
// draw() kills every panel after it, and the page shows blanks with no error visible to the reader.
// That failure has now shipped twice (the FBS brace bug, the BK temporal-dead-zone bug), and both
// times it was found by eye. This finds it in a second.
const fs = require('fs'), vm = require('vm');
const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');

// Only EMPTY divs are expected to be filled by Plotly. A panel can also be pre-rendered server
// side (the event storyboard is a static table), and those must not be reported as failures.
// Only divs that are EMPTY and have real height are expected to be filled by Plotly. A panel can
// carry pre-rendered server-side content instead (the event storyboard is a static table sitting
// beside a height:0 placeholder), and those must not be reported as failures.
const all = [...html.matchAll(/id="(c-[a-z0-9-]+|p-[a-z0-9-]+|s-[a-z0-9-]+)"([^>]*)>([\s\S]{0,40})/g)];
const isPlot = m => m[3].trimStart().startsWith('</') && !/height:\s*0(px)?\b/.test(m[2]);
const ids = all.filter(isPlot).map(m => m[1]);
const prefilled = all.filter(m => !isPlot(m)).map(m => m[1]);
const drawn = new Set(), errors = [];
const mkEl = id => ({ id, style:{}, dataset:{}, classList:{add(){},remove(){},contains:()=>false},
  appendChild(){}, addEventListener(){}, setAttribute(){}, getAttribute:()=>null,
  querySelector:()=>null, querySelectorAll:()=>[], children:[], innerHTML:'', textContent:'',
  offsetWidth:900, offsetHeight:400, getBoundingClientRect:()=>({width:900,height:400,top:0,left:0}) });

const sandbox = {
  console, setTimeout, clearTimeout, requestAnimationFrame: f => f(),
  Plotly: { react:(id)=>{ drawn.add(typeof id === 'string' ? id : id.id); return Promise.resolve(); },
            newPlot:(id)=>{ drawn.add(typeof id === 'string' ? id : id.id); return Promise.resolve(); },
            relayout(){}, restyle(){}, purge(){}, Plots:{resize(){}} },
  document: { getElementById: id => ids.includes(id) ? mkEl(id) : null,
              querySelector:()=>null, querySelectorAll:()=>[],
              addEventListener(){}, documentElement:mkEl('html'), body:mkEl('body'),
              createElement:()=>mkEl('tmp') },
  matchMedia: () => ({ matches:false, addEventListener(){}, addListener(){} }),
  location:{href:''}, navigator:{userAgent:'node'}, localStorage:{getItem:()=>null,setItem(){}},
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const ctx = vm.createContext(sandbox);
for (const [i, code] of blocks.entries()) {
  try { vm.runInContext(code, ctx, { timeout: 30000 }); }
  catch (e) { errors.push(`script block ${i}: ${e.constructor.name}: ${e.message}`); }
}
for (const fn of ['draw', 'render', 'main']) {
  if (typeof sandbox[fn] === 'function') {
    for (const theme of [undefined, 'dark']) {
      try { sandbox[fn](theme); } catch (e) { errors.push(`${fn}(${theme}): ${e.constructor.name}: ${e.message}`); }
    }
  }
}
const missing = ids.filter(id => !drawn.has(id));
console.log(`  ${file}`);
console.log(`    plot divs: ${ids.length}   drawn: ${drawn.size}   NOT DRAWN: ${missing.length}`
          + (prefilled.length ? `   (${prefilled.length} pre-rendered: ${prefilled.join(', ')})` : ''));
if (missing.length) console.log(`    missing: ${missing.join(', ')}`);
for (const e of errors) console.log(`    !! ${e}`);
process.exit(missing.length || errors.length ? 1 : 0);
