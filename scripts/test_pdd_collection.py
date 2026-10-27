import unittest,tempfile,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from collect_pdd_reviews import collect,read_checkpoint,PddError,validate_capture_info,request_body
class PddFlowTests(unittest.TestCase):
 def test_capture_boundary_never_relabels_observed_mall(self):
  with self.assertRaises(PddError):validate_capture_info({'ready':True,'mallId':'20','captureId':'a'},'10')
  with self.assertRaises(PddError):validate_capture_info({'ready':False},'10')
  self.assertEqual(validate_capture_info({'ready':True,'mallId':None,'captureId':'a'},'10')['mallIdentity'],'caller-selected-not-observed')
 def test_request_body_scoped_without_cookie_or_unrelated_filters(self):
  b=request_body({'label':'m','start':1,'end':2},3,'10');self.assertEqual(b,{'startTime':1,'endTime':2,'pageNo':3,'pageSize':10,'goodsId':'10'})
 def test_two_pages_and_receipt_not_fake_limit_complete(self):
  calls=[]
  def fetch(w,p,g):
   calls.append(p);return {'success':True,'result':{'data':[{'reviewId':str(p),'goodsId':'9','comment':'差评也保留','descScore':1,'pictures':[{'url':'https://review.pddpic.com/a.jpg'}]}],'totalNum':2}}
  with tempfile.TemporaryDirectory() as d:
   r=collect(fetch,Path(d),{'mallId':'1','captureId':'a'},[{'label':'m','start':1,'end':2}],limit=1,stars=None,max_pages=3,delay=0)
   self.assertEqual(r['rows'][0]['text'],'差评也保留');self.assertEqual(r['coverage']['endReason'],'requested-limit');self.assertFalse(r['coverage']['complete']);self.assertEqual(calls,[1])
 def test_auth_failure_stops_without_next_window_and_preserves_partial(self):
  calls=[]
  def fetch(w,p,g):calls.append(w['label']);return {'success':False,'errorMsg':'请登录'}
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(PddError):collect(fetch,Path(d),{'mallId':'1','captureId':'a'},[{'label':'a','start':1,'end':2},{'label':'b','start':3,'end':4}],limit=20,delay=0)
   self.assertEqual(calls,['a']);self.assertTrue((Path(d)/'error.json').exists())
 def test_same_capture_success_pages_reuse_and_wrong_scope_refused(self):
  def fetch(w,p,g):return {'success':True,'result':{'data':[],'totalNum':0}}
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);identity={'mallId':'1','captureId':'a'};wins=[{'label':'m','start':1,'end':2}]
   r=collect(fetch,root,identity,wins,delay=0);self.assertTrue(r['coverage']['complete'])
   r=collect(lambda *x:self.fail('valid page re-fetched'),root,identity,wins,delay=0);self.assertTrue(r['coverage']['complete'])
   with self.assertRaises(PddError):collect(fetch,root,{'mallId':'2','captureId':'a'},wins,delay=0)
if __name__=='__main__':unittest.main()

class BridgeRecoveryTests(unittest.TestCase):
 def test_js_bridge_transports_raw_without_integer_loss(self):
  import subprocess
  from collect_pdd_reviews import request_js,decode_browser_page
  text='{"success":true,"result":{"data":[{"reviewId":900719925474099312345,"goodsId":"9","comment":"a","pictures":[]},{"reviewId":900719925474099312346,"goodsId":"9","comment":"b","pictures":[]}],"totalNum":2}}'
  js=request_js({'captureId':'c','mallId':'1'},{'label':'m','start':1,'end':2},1,None)
  code='const text='+json.dumps(text)+';global.location={origin:"https://mms.pinduoduo.com",pathname:"/goods/evaluation/index"};global.window={__reviewCollectPddCapture:{capture:{id:"c",mallId:"1",body:{},headers:{}},originalFetch:async()=>({ok:true,text:async()=>text})}};('+js+').then(x=>console.log(x));'
  r=subprocess.run(['node','-e',code],capture_output=True,text=True,check=True)
  parsed=decode_browser_page(json.loads(r.stdout));self.assertNotEqual(parsed['result']['data'][0]['reviewId'],parsed['result']['data'][1]['reviewId'])
  self.assertEqual(parsed['result']['data'][0]['reviewId'],900719925474099312345)
 def test_new_explicit_invocation_can_refetch_known_limit_rejection_once(self):
  def rejected(*x):return {'success':False,'errorMsg':'too many requests'}
  with tempfile.TemporaryDirectory() as d:
   wins=[{'label':'m','start':1,'end':2}];identity={'mallId':'1','captureId':'c'}
   with self.assertRaises(PddError):collect(rejected,Path(d),identity,wins,delay=0)
   calls=[]
   def resumed(*x):calls.append(1);return {'success':True,'result':{'data':[],'totalNum':0}}
   r=collect(resumed,Path(d),identity,wins,delay=0);self.assertTrue(r['coverage']['complete']);self.assertEqual(calls,[1]);self.assertTrue(list((Path(d)/'raw/failed-attempts').glob('*.json')))
