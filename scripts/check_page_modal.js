// Exercise the ticker click-through modal for EVERY funded name and report which ones actually
// draw a FUNDED overlay. The modal located its spans with an exact PX.d.indexOf() on dates that
// are BOOK dates, not trading days, so a boundary falling on a non-trading day dropped the whole
// overlay in silence -- the modal then read "watchlisted, never funded" about a funded position.
// Found by eye on AEM (+$1,886). 3 of 33 names on CBS and 3 of 90 on CBT were affected.
const fs=require('fs'), vm=require('vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const ids=[...html.matchAll(/id="([a-z0-9-]+)"/g)].map(m=>m[1]);
const el=id=>({id,style:{},dataset:{},classList:{add(){},remove(){},contains:()=>false},appendChild(){},
 addEventListener(){},setAttribute(){},getAttribute:()=>null,querySelector:()=>null,querySelectorAll:()=>[],
 children:[],innerHTML:'',textContent:'',offsetWidth:900,offsetHeight:400,
 getBoundingClientRect:()=>({width:900,height:400,top:0,left:0})});
let last=null;
const sb={console,setTimeout,clearTimeout,requestAnimationFrame:f=>f(),
 Plotly:{react:(id,d,l)=>{if(id==='tkplot')last={traces:d,layout:l};return Promise.resolve();},
  newPlot(){return Promise.resolve();},relayout(){},restyle(){},purge(){},Plots:{resize(){}}},
 document:{getElementById:id=>el(id),querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},
  documentElement:el('html'),body:el('body'),createElement:()=>el('t')},
 matchMedia:()=>({matches:false,addEventListener(){},addListener(){}}),
 location:{href:''},navigator:{userAgent:'node'},localStorage:{getItem:()=>null,setItem(){}}};
sb.window=sb; sb.globalThis=sb;
const ctx=vm.createContext(sb);
for(const m of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)){
  try{vm.runInContext(m[1],ctx,{timeout:30000});}catch(e){console.log('ERR',e.message);}
}
try{sb.draw();}catch(e){console.log('draw ERR',e.message);}
const DATA=vm.runInContext('DATA', ctx), funded=[...new Set(DATA.book.wcomp.funded.map(s=>s.t))].sort();
let ok=0, bad=[];
for(const tk of funded){
  last=null;
  try{ sb._showTk(tk); }catch(e){ bad.push(tk+' (throw '+e.message+')'); continue; }
  if(!last){ bad.push(tk+' (no plot)'); continue; }
  const hasFunded = last.traces.some(t=>t.name==='funded');
  if(hasFunded) ok++; else bad.push(tk);
}
console.log(`  ${process.argv[2]}: ${funded.length} funded tickers -> ${ok} draw a FUNDED overlay`);
console.log(bad.length ? `    MISSING: ${bad.join(', ')}` : '    none missing');
process.exit(0);
