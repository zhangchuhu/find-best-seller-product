import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const base = new URL('../../chrome-extension/mercado-evidence-collector/', import.meta.url);
function load(file) {
  const api = {};
  vm.runInNewContext(readFileSync(new URL(file, base), 'utf8'), {URL, globalThis: {__MERCADO_COLLECTOR_TEST__: api}});
  return api;
}
test('Mercado identities and ad labels are bounded and normalized', () => {
  const api = load('content.js');
  assert.equal(api.canonicalProductId('https://articulo.mercadolibre.com.mx/MLM-1234-vestido'), 'MLM1234');
  assert.equal(api.canonicalProductId('https://mercadolibre.com.mx.evil.test/MLM-1234'), null);
  assert.equal(api.classifyVisibleAd({visibleLabels:['Patrocinado']}), true);
  assert.equal(api.classifyVisibleAd({visibleLabels:[], visiblyOrganic:true}), false);
  assert.throws(() => api.classifyVisibleAd({visibleLabels:['unknown']}), /ambiguous/);
});
test('product sales are extracted only from unambiguous displayed detail text', () => {
  const api = load('content.js');
  const entry = (text) => ({kind:'sold', text, bbox:[0,0,1,1]});
  assert.equal(api.extractVisibleMetrics([entry('Nuevo | +2 mil vendidos')]).sold_display, '+2 mil vendidos');
  assert.equal(api.extractVisibleMetrics([entry('Vendedor: 2 mil vendidos')]).sold_display, null);
  assert.equal(api.extractVisibleMetrics([entry('20 vendidos'), entry('100 vendidos')]).sold_display, null);
});
test('collector requires recurring products and Mercado sales, independent of reviews', () => {
  const api = load('collector.js');
  const queries = ['vestido', 'vestido manga abullonada', 'vestido cóctel'];
  const session = api.createSession({taskRecordId:'rec_source', platform:'mercado-libre-mx', queries, cardLimit:30, resultLimit:1});
  queries.forEach((query, q) => api.addSearchObservations(session, {query, cards:Array.from({length:30}, (_, i) => {
    const id = 'MLM' + (i < 2 ? 100001 + i : 300000 + q * 100 + i);
    return {query, rank:i+1, is_ad:false, title:'Vestido', url:`https://articulo.mercadolibre.com.mx/${id}-vestido`, product_id:id, thumbnail_url:null};
  })}));
  const detail = {identity:'mercado-libre-mx:MLM100001', status:'qualified', reason:null, detail_url:'https://articulo.mercadolibre.com.mx/MLM100001-vestido', product_id:'MLM100001', title:'Vestido', category:null, visual_category_code:'dress', sold_display:null, reviews_display:null, rating_display:null, match_level:'类似竞品', visual_features:['manga larga']};
  assert.throws(() => api.addDetailOutcome(session, detail), /metrics/);
  detail.sold_display = '2 mil vendidos';
  api.addDetailOutcome(session, detail);
  assert.equal(JSON.parse(api.serializeEvidence(session)).details[0].sold_display, '2 mil vendidos');
});

test('partially visible card waits for title link without skipping its rank', () => {
  const api = {};
  const rect = (top, bottom) => ({top,bottom,left:0,right:100,width:100,height:bottom-top});
  const image = {getBoundingClientRect:()=>rect(10,100), currentSrc:'https://img.example/item.jpg'};
  const link = {getBoundingClientRect:()=>rect(900,930), href:'https://articulo.mercadolibre.com.mx/MLM-1234-vestido'};
  const card = {className:'ui-search-layout__item', getBoundingClientRect:()=>rect(0,1000), contains:()=>false,
    querySelectorAll:(selector)=>selector.includes('a[href') ? [link] : selector === 'img' ? [image] : [],
    querySelector:(selector)=>selector === 'img' ? image : link, innerText:'Vestido'};
  const input = {getBoundingClientRect:()=>rect(0,20), value:'vestido corto'};
  const document = {querySelectorAll:(selector)=>selector.includes('.ui-search-layout') ? [card] : [], querySelector:()=>input};
  vm.runInNewContext(readFileSync(new URL('content.js', base),'utf8'), {URL,document,location:{href:'https://listado.mercadolibre.com.mx/vestido-corto'}, innerHeight:800,innerWidth:1000,devicePixelRatio:1,getComputedStyle:()=>({visibility:'visible',display:'block',opacity:'1'}),globalThis:{__MERCADO_COLLECTOR_TEST__:api}});
  const result = api.extractSearch('vestido corto', [], 30);
  assert.equal(result.error, undefined);
  assert.equal(result.observations.length, 0);
});

test('search context remains bound when scrolling hides the search input', () => {
  const api=load('collector.js');
  const url='https://listado.mercadolibre.com.mx/vestido-corto';
  assert.equal(api.checkSearchContext({page_url:url,search_query:'vestido corto'},'vestido corto',null),url);
  assert.equal(api.checkSearchContext({page_url:url,search_query:null},'vestido corto',url),url);
  assert.throws(()=>api.checkSearchContext({page_url:url,search_query:null},'vestido corto',null),/exact Codex query/);
  assert.throws(()=>api.checkSearchContext({page_url:url,search_query:'other'},'vestido corto',url),/exact Codex query/);
  assert.throws(()=>api.checkSearchContext({page_url:url+'-other',search_query:null},'vestido corto',url),/exact Codex query/);
});

test('scrolling past an already collected card does not revalidate offscreen parts', () => {
  const api={}, rect=(top,bottom)=>({top,bottom,left:0,right:100,width:100,height:bottom-top});
  const link={getBoundingClientRect:()=>rect(-200,-180),href:'https://articulo.mercadolibre.com.mx/MLM-1234-vestido'};
  const image={getBoundingClientRect:()=>rect(-500,-300)};
  const card={className:'ui-search-layout__item', getBoundingClientRect:()=>rect(-500,100), contains:()=>false, innerText:'Vestido', querySelectorAll:s=>s.includes('a[href')?[link]:s==='img'?[image]:[], querySelector:s=>s==='img'?image:link};
  const document={querySelectorAll:s=>s.includes('.ui-search-layout')?[card]:[],querySelector:()=>null};
  vm.runInNewContext(readFileSync(new URL('content.js',base),'utf8'),{URL,document,location:{href:'https://listado.mercadolibre.com.mx/vestido'},innerHeight:800,innerWidth:1000,devicePixelRatio:1,getComputedStyle:()=>({visibility:'visible',display:'block',opacity:'1'}),globalThis:{__MERCADO_COLLECTOR_TEST__:api}});
  const prior=[{query:'vestido',rank:1,is_ad:false,title:'Vestido',url:link.href,product_id:'MLM1234',thumbnail_url:null}];
  const result=api.extractSearch('vestido',prior,30);
  assert.equal(result.error,undefined);
  assert.equal(result.observations.length,1);
});

test('Mercado catalog IDs are accepted without collapsing them into listing IDs', () => {
  const api=load('content.js');
  assert.equal(api.canonicalProductId('https://www.mercadolibre.com.mx/vestido/up/MLMU3819941570'),'MLMU3819941570');
  assert.notEqual(api.canonicalProductId('https://www.mercadolibre.com.mx/vestido/up/MLMU3819941570'),api.canonicalProductId('https://articulo.mercadolibre.com.mx/MLM-3819941570-vestido'));
});
