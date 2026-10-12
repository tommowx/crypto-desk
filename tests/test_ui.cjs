// Run with Node.js; optional development test, not required to run the app.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../index.html','utf8').split('<script>')[1].split('</script>')[0].replace('refresh();setInterval(refresh,2500);','');
const elements={};const context={document:{getElementById:id=>elements[id]??=( {textContent:'',innerHTML:'',disabled:false,className:''}),querySelectorAll:()=>[]},console:{error(){}},AbortController,setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(source,context);
const sample={portfolio:{positions:{},trades:[],halted:false},equity:1000,stale:false,quotes_at:1,running:false,busy:false,stage:'Ciclo completato',unpriced:[],roles:{},market:[],analyses:{},logs:[],token:'test',warnings:[]};
async function refresh(){await vm.runInContext('refresh()',context)}
(async()=>{
 context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();
 assert.match(elements.status.textContent,/Premi Avvia ricerca automatica/);assert.equal(elements.start.disabled,false);
 context.fetch=async()=>{throw Error('offline')};await refresh();assert.match(elements.status.textContent,/Motore non raggiungibile/);assert.equal(elements.start.disabled,true);
 context.fetch=async()=>({ok:false,status:503});await refresh();assert.match(elements.status.textContent,/HTTP 503/);
 context.fetch=async()=>({ok:true,json:async()=>({})});await refresh();assert.match(elements.status.textContent,/Risposta non valida/);
 context.fetch=async()=>({ok:true,json:async()=>({...sample,portfolio:{}})});await refresh();assert.match(elements.status.textContent,/Motore raggiungibile, errore interfaccia/);
 context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();assert.equal(elements.start.disabled,false);
 console.log('6 UI scenarios passed (DOM simulated; no browser rendering test).');
})().catch(e=>{console.error(e);process.exitCode=1});
