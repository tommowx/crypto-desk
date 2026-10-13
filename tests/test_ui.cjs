// Run with Node.js; optional development test, not required to run the app.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../index.html','utf8').split('<script>')[1].split('</script>')[0].replace('refresh();setInterval(refresh,2500);','');
const elements={};const context={document:{getElementById:id=>elements[id]??=( {textContent:'',innerHTML:'',disabled:false,className:''}),querySelectorAll:()=>[]},console:{error(){}},AbortController,setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(source,context);
const sample={portfolio:{positions:{},trades:[],halted:false},equity:1000,stale:false,quotes_at:1,running:false,busy:false,stage:'Ciclo completato',unpriced:[],roles:{},market:[],analyses:{},logs:[],token:'test',warnings:[]};
async function refresh(){await vm.runInContext('refresh()',context)}
(async()=>{
 context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();
 assert.equal(elements.deepStatus.textContent,'Pronto');assert.equal(elements.startAll.disabled,false);assert.equal(elements.exportAll.disabled,false);
 sample.deep={status:'running',done:0,total:1000};context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();assert.match(elements.profitChart.innerHTML,/Grafico basato su rilevazioni effettive/);assert.match(elements.profitChart.innerHTML,/polyline/);assert.equal(elements.chartMode.textContent,'TEST COMPLETATI');sample.deep={status:'idle'};sample.running=true;sample.equity=1003;context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();assert.equal(elements.chartMode.textContent,'SALDO SIMULATO');assert.match(elements.profitChart.innerHTML,/polyline/);sample.equity=1005;await refresh();assert.match(elements.replayLabel.textContent,/1005/);sample.running=false;
 context.fetch=async()=>{throw Error('offline')};await refresh();assert.match(elements.status.textContent,/Motore non raggiungibile/);assert.equal(elements.startAll.disabled,true);
 context.fetch=async()=>({ok:false,status:503});await refresh();assert.match(elements.status.textContent,/HTTP 503/);
 context.fetch=async()=>({ok:true,json:async()=>({})});await refresh();assert.match(elements.status.textContent,/Risposta non valida/);
 context.fetch=async()=>({ok:true,json:async()=>({...sample,portfolio:{}})});await refresh();assert.match(elements.status.textContent,/Motore raggiungibile, errore interfaccia/);
 context.fetch=async()=>({ok:true,json:async()=>structuredClone(sample)});await refresh();assert.equal(elements.startAll.disabled,false);
 console.log('6 UI scenarios passed (DOM simulated; no browser rendering test).');
})().catch(e=>{console.error(e);process.exitCode=1});
