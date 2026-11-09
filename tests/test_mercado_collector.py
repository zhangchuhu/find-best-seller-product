from __future__ import annotations
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from scripts.platforms import Platform
from scripts.workflow import validate_evidence
from tests.test_workflow import prepare_only

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'chrome-extension' / 'mercado-evidence-collector'

class MercadoCollectorTests(unittest.TestCase):
    def test_manifest_is_limited_to_mercado_hosts(self):
        manifest = json.loads((EXTENSION / 'manifest.json').read_text())
        hosts = ['https://www.mercadolibre.com.mx/*', 'https://listado.mercadolibre.com.mx/*', 'https://articulo.mercadolibre.com.mx/*']
        self.assertEqual(hosts, manifest['host_permissions'])
        self.assertEqual(hosts, manifest['content_scripts'][0]['matches'])
        self.assertEqual({'activeTab', 'downloads'}, set(manifest['permissions']))
        source = ''.join((EXTENSION / p).read_text() for p in ('collector.js', 'content.js', 'collector.html'))
        for forbidden in ('fetch(', 'XMLHttpRequest', 'document.cookie', 'chrome.cookies', 'localStorage', 'captureVisibleTab'):
            self.assertNotIn(forbidden, source)

    def test_export_passes_existing_mercado_evidence_validator(self):
        harness = r'''
const fs=require('fs'), vm=require('vm'), api={};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {URL, globalThis:{__MERCADO_COLLECTOR_TEST__:api}});
const queries=['vestido','vestido manga abullonada','vestido cóctel'];
const s=api.createSession({taskRecordId:'rec_source',platform:'mercado-libre-mx',queries,cardLimit:30,resultLimit:1});
queries.forEach((query,q)=>api.addSearchObservations(s,{query,cards:Array.from({length:30},(_,i)=>{
 const id='MLM'+(i<2?100001+i:300000+q*100+i);
 return {query,rank:i+1,is_ad:false,title:'Vestido',url:`https://articulo.mercadolibre.com.mx/${id}-vestido`,product_id:id,thumbnail_url:null};
})}));
api.addDetailOutcome(s,{identity:'mercado-libre-mx:MLM100001',status:'qualified',reason:null,detail_url:'https://articulo.mercadolibre.com.mx/MLM100001-vestido',product_id:'MLM100001',title:'Vestido',category:null,visual_category_code:'dress',sold_display:'2 mil vendidos',reviews_display:null,rating_display:null,match_level:'类似竞品',visual_features:['manga larga']});
process.stdout.write(api.serializeEvidence(s));
'''
        result = subprocess.run(['node', '-e', harness, str(EXTENSION / 'collector.js')], check=True, text=True, capture_output=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_only(root, Platform.MERCADO_MX, limit=1)
            evidence = root / 'evidence.json'
            evidence.write_text(result.stdout)
            summary = validate_evidence(root / 'rec_source', evidence)
        self.assertIn('2 recurring', summary)
        self.assertIn('1 details', summary)
