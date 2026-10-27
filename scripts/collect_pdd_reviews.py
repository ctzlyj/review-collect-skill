"""PDD merchant reviews through the current normal authenticated page, read only."""
from __future__ import annotations
import argparse,hashlib,json,time,sys,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from collect_reviews import WebCLI,atomic_json,CollectionError
from pdd_model import PddError,parse_page,normalize_review,month_windows,candidate_selected,download_media
PAGE_URL='https://mms.pinduoduo.com/goods/evaluation/index?msfrom=mms_sidenav'
ENDPOINT='https://mms.pinduoduo.com/saturn/reviews/list'
CAPTURE_KEY='__reviewCollectPddCapture'
PREPARE_JS=r'''(()=>{if(location.origin!=="https://mms.pinduoduo.com"||location.pathname.includes("login"))return JSON.stringify({error:"LOGIN",message:"请先正常登录PDD商家后台"});const key="__reviewCollectPddCapture";if(window[key]?.installed)return JSON.stringify({installed:true,ready:!!window[key].capture,id:window[key].capture?.id});const state={installed:true,capture:null};Object.defineProperty(window,key,{value:state,configurable:true});const save=(url,method,headers,body)=>{try{const u=new URL(url,location.href);if(u.origin!==location.origin||u.pathname!=="/saturn/reviews/list"||String(method).toUpperCase()!=="POST")return;const h=new Headers(headers);const data=typeof body==="string"?JSON.parse(body):body;if(!data||typeof data!=="object"||Array.isArray(data)||!h.get("anti-content"))return;state.capture={id:crypto.randomUUID(),body:data,headers:{"anti-content":h.get("anti-content"),"content-type":"application/json"},mallId:data.mallId??data.mall_id??null,at:Date.now()};}catch{}};const originalFetch=window.fetch;window.fetch=function(input,init){try{const request=input instanceof Request?input:null;const url=request?request.url:String(input);if(request&&!init?.body)request.clone().text().then(body=>save(url,init?.method??request.method,init?.headers??request.headers,body)).catch(()=>{});else save(url,init?.method??"GET",init?.headers,init?.body);}catch{}return originalFetch.apply(this,arguments);};state.originalFetch=originalFetch;const originalOpen=XMLHttpRequest.prototype.open,originalHeader=XMLHttpRequest.prototype.setRequestHeader,originalSend=XMLHttpRequest.prototype.send;const meta=new WeakMap();XMLHttpRequest.prototype.open=function(method,url){meta.set(this,{method,url,headers:{}});return originalOpen.apply(this,arguments)};XMLHttpRequest.prototype.setRequestHeader=function(name,value){const m=meta.get(this);if(m)m.headers[name]=value;return originalHeader.apply(this,arguments)};XMLHttpRequest.prototype.send=function(body){const m=meta.get(this);if(m)save(m.url,m.method,m.headers,body);return originalSend.apply(this,arguments)};return JSON.stringify({installed:true,ready:false,message:"在当前PDD评价页正常点击查询一次；会话参数仅保留在当前页面内存"});})()'''
INFO_JS=r'''(()=>{if(location.origin!=="https://mms.pinduoduo.com"||location.pathname.includes("login"))return JSON.stringify({error:"LOGIN",message:"需要正常登录"});const s=window.__reviewCollectPddCapture,c=s?.capture;return JSON.stringify({ready:!!c,captureId:c?.id,mallId:c?.mallId==null?null:String(c.mallId),capturedAt:c?.at,baseFilters:c?Object.fromEntries(["descScore","score","reviewType","hasPicture","hasVideo"].filter(k=>k in c.body).map(k=>[k,c.body[k]])):null});})()'''

def validate_capture_info(info,mall_id):
    if not isinstance(info,dict) or info.get('error'):raise PddError('LOGIN','正常PDD后台页面不可用')
    if not info.get('ready') or not isinstance(info.get('captureId'),str):raise PddError('SESSION_NOT_READY','请先--prepare，再在正常评价页点击查询一次；不生成或破解anti-content')
    observed=info.get('mallId')
    if observed is not None and str(observed)!=mall_id:raise PddError('IDENTITY','请求中店铺身份与指定mall-id不同')
    return {'mallId':mall_id,'captureId':info['captureId'],'mallIdentity':'observed-request-body' if observed is not None else 'caller-selected-not-observed','baseFilters':info.get('baseFilters')}

def request_body(window,page,goods_id=None):
    result={'startTime':window['start'],'endTime':window['end'],'pageNo':page,'pageSize':10}
    if goods_id is not None:result['goodsId']=goods_id
    return result

def request_js(identity,window,page,goods_id):
    args=json.dumps({'captureId':identity['captureId'],'mallId':identity['mallId'],'updates':request_body(window,page,goods_id)},ensure_ascii=False)
    return '(async()=>{const a='+args+r''';if(location.origin!=="https://mms.pinduoduo.com"||location.pathname.includes("login"))return JSON.stringify({error:"LOGIN",message:"店铺页面需要登录"});const s=window.__reviewCollectPddCapture,c=s?.capture;if(!c||c.id!==a.captureId)return JSON.stringify({error:"IDENTITY",message:"评价页或查询条件已变更，停止旧任务"});if(c.mallId!=null&&String(c.mallId)!==a.mallId)return JSON.stringify({error:"IDENTITY",message:"店铺身份不匹配"});try{const body={...c.body,...a.updates};const r=await s.originalFetch("/saturn/reviews/list",{method:"POST",credentials:"include",headers:c.headers,body:JSON.stringify(body),signal:AbortSignal.timeout(12000)});if(!r.ok)return JSON.stringify({error:"HTTP",message:"HTTP "+r.status});const text=await r.text();if(text.length>8*1024*1024)return JSON.stringify({error:"CAPACITY",message:"单页响应过大"});return JSON.stringify({rawText:text});}catch(e){return JSON.stringify({error:"NETWORK",message:e.message});}})()'''

def decode_browser_page(result):
    if not isinstance(result,dict):raise PddError('FORMAT','浏览器响应结构无效')
    if result.get('error'):raise PddError(result['error'],result.get('message','浏览器读取失败'))
    text=result.get('rawText')
    if not isinstance(text,str):raise PddError('FORMAT','缺少原始响应文本')
    try:return json.loads(text)
    except json.JSONDecodeError as e:raise PddError('FORMAT','后台响应不是JSON，不执行HTML或脚本') from e

def selection_key(identity,windows,goods_id,stars,media,include_videos,limit):
    # Captured anti-content, body and ambient credentials never enter the local record.
    return {'mallId':identity['mallId'],'captureId':identity['captureId'],'mallIdentity':identity.get('mallIdentity','not-observed'),'baseFilters':identity.get('baseFilters'),'windows':windows,'goodsId':goods_id,'stars':stars,'media':media,'includeVideos':include_videos,'limit':limit}

def read_checkpoint(path,key,window,page):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('selection')!=key or data.get('window')!=window or data.get('page')!=page:raise PddError('IDENTITY','检查点店铺/筛选/窗口不同')
    payload=data.get('response');encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(encoded).hexdigest()!=data.get('sha256'):raise PddError('CHECKPOINT','检查点摘要不匹配')
    return payload

def collect(fetch_page,output,identity,windows,limit=100,goods_id=None,stars=None,media='image',include_videos=False,max_pages=200,delay=1.5,online=True):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);raw=output/'raw';raw.mkdir(exist_ok=True)
    key=selection_key(identity,windows,goods_id,stars,media,include_videos,limit);session=output/'pdd-session.json'
    if session.exists() and json.loads(session.read_text())['selection']!=key:raise PddError('IDENTITY','目录属于不同店铺/会话查询/筛选，不覆盖')
    atomic_json(session,{'schema':'pdd-review-session','selection':key,'identity':identity});rows=[];seen=set();scanned=0;pages=0;completed=[];end_reason='window-end';complete=True
    try:
        for window in windows:
            window_done=False
            for page in range(1,max_pages+1):
                cp=raw/(window['label']+'-%04d.json'%page);data=None
                if cp.exists():
                    data=read_checkpoint(cp,key,window,page)
                    try:parse_page(data)
                    except PddError as error:
                        if not online or error.code not in ('LOGIN','VERIFY','API','SESSION','RATE_LIMIT'):raise
                        attempts=raw/'failed-attempts';attempts.mkdir(exist_ok=True);saved=json.loads(cp.read_text());dest=attempts/(cp.stem+'-'+saved['sha256'][:16]+'.json')
                        if not dest.exists():atomic_json(dest,saved)
                        data=None
                if data is None:
                    if not online:raise PddError('CHECKPOINT','离线检查点缺页，不补网或伪造完成')
                    data=fetch_page(window,page,goods_id)
                    encoded=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();atomic_json(cp,{'selection':key,'window':window,'page':page,'response':data,'sha256':hashlib.sha256(encoded).hexdigest()})
                    if delay:time.sleep(delay)
                result=parse_page(data);batch=result['rows'];pages+=1;scanned+=len(batch);new_ids=0
                for row in batch:
                    review=normalize_review(row,identity['mallId'],ENDPOINT,page,window['label'],include_videos)
                    if review['id'] in seen:continue
                    seen.add(review['id']);new_ids+=1
                    if not candidate_selected(row,{'goods_id':goods_id,'stars':stars,'media':media,'include_videos':include_videos}):continue
                    rows.append(review)
                    if len(rows)>=limit:break
                print(json.dumps({'window':window['label'],'page':page,'rows':len(batch),'selected':len(rows),'total':result['total']},ensure_ascii=False),flush=True)
                atomic_json(output/'checkpoint.json',{'selection':key,'rows':rows,'scanned':scanned,'pages':pages,'completedWindows':completed,'currentWindow':window['label'],'currentPage':page,'status':'collecting'})
                if len(rows)>=limit:complete=False;end_reason='requested-limit';break
                if batch and not new_ids and page>1:raise PddError('PAGING','窗口分页重复，不认为结束')
                if result['has_more'] is False or not batch or (result['total'] is not None and page*10>=result['total']):window_done=True;completed.append(window['label']);break
            if end_reason=='requested-limit':break
            if not window_done:complete=False;end_reason='page-budget';break
        result={'item':{'id':'mall-'+identity['mallId'],'url':PAGE_URL,'platform':'pinduoduo','mallIdentity':identity.get('mallIdentity')},'rows':rows,'coverage':{'platform':'pinduoduo','selection':key,'pages':pages,'scanned':scanned,'selected':len(rows),'complete':complete,'endReason':end_reason,'completedWindows':completed,'limitations':['范围限指定店铺/月份及正常页面查询条件；不保证平台隐藏/受限评价。']}}
        atomic_json(output/'reviews.json',result);return result
    except (PddError,KeyboardInterrupt) as e:
        atomic_json(output/'error.json',{'status':'partial','code':getattr(e,'code','CANCELLED'),'message':str(e),'pages':pages,'rows':len(rows)});atomic_json(output/'partial-reviews.json',{'item':{'id':'mall-'+identity['mallId'],'url':PAGE_URL},'rows':rows,'coverage':{'complete':False,'reason':getattr(e,'code','CANCELLED'),'selection':key}});raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--session');p.add_argument('--tab');p.add_argument('--mall-id');p.add_argument('--prepare',action='store_true');p.add_argument('--output-dir');p.add_argument('--limit',type=int);p.add_argument('--months',type=int,default=1);p.add_argument('--goods-id');p.add_argument('--stars',type=int,choices=[1,2,3,4,5]);p.add_argument('--media',choices=['all','image','video'],default='image');p.add_argument('--include-videos',action='store_true');p.add_argument('--download-media',action='store_true');p.add_argument('--workers',type=int,default=4);p.add_argument('--max-pages',type=int,default=200);p.add_argument('--delay',type=float,default=1.5);p.add_argument('--from-raw',action='store_true');p.add_argument('--webcli-entry');a=p.parse_args()
    if not 1<=(a.limit or 100)<=20000 or not 1<=a.months<=24 or not 1<=a.max_pages<=200 or not 0<=a.delay<=60 or not 1<=a.workers<=8:p.error('limit/months/pages/workers超出本地资源预算')
    if a.goods_id and not a.goods_id.isdecimal():p.error('goods-id应为数字')
    if a.media=='video' and not a.include_videos:p.error('视频筛选需显式--include-videos')
    try:
        if a.from_raw:
            if not a.output_dir:p.error('需要output-dir')
            output=Path(a.output_dir);session=json.loads((output/'pdd-session.json').read_text());key=session['selection'];identity=session['identity'];windows=key['windows']
            if a.mall_id and a.mall_id!=identity['mallId']:raise PddError('IDENTITY','店铺不同')
            result=collect(None,output,identity,windows,limit=a.limit or key['limit'],goods_id=key['goodsId'],stars=key['stars'],media=key['media'],include_videos=key['includeVideos'],max_pages=a.max_pages,delay=0,online=False)
        else:
            if not a.session or not a.tab:p.error('传入实际PDD后台WebCLI session和tab；不猜店铺或复制Cookie')
            bridge=WebCLI(a.session,a.webcli_entry)
            if a.prepare:
                result=bridge.eval(a.tab,PREPARE_JS)
                if result.get('error'):raise PddError(result['error'],result.get('message','准备失败'))
                print(json.dumps(result,ensure_ascii=False));return
            if not a.output_dir or not a.mall_id or not a.mall_id.isdecimal():p.error('需output-dir和当前店铺mall-id')
            output=Path(a.output_dir);info=bridge.eval(a.tab,INFO_JS);identity=validate_capture_info(info,a.mall_id);
            existing=output/'pdd-session.json'
            windows=json.loads(existing.read_text())['selection']['windows'] if existing.exists() else month_windows(a.months,datetime.now(timezone(timedelta(hours=8))))
            def fetch_page(window,page,goods_id):
                r=bridge.eval(a.tab,request_js(identity,window,page,goods_id))
                if r.get('error'):raise PddError(r['error'],r.get('message','后台请求失败'))
                return decode_browser_page(r)
            result=collect(fetch_page,output,identity,windows,limit=a.limit or 100,goods_id=a.goods_id,stars=a.stars,media=a.media,include_videos=a.include_videos,max_pages=a.max_pages,delay=a.delay)
        from export_workbook import export_workbook
        receipt=export_workbook(output/'reviews.json',output/'拼多多_评价.xlsx')
        if a.download_media:
            receipts=download_media(result['rows'],output/'media',include_videos=result['coverage']['selection']['includeVideos'],workers=a.workers);atomic_json(output/'media-manifest.json',receipts);receipt['mediaStatus']=receipts['status']
        receipt['coverage']=result['coverage'];atomic_json(output/'summary.json',receipt);print(json.dumps(receipt,ensure_ascii=False))
        if result['coverage']['endReason']=='page-budget' or receipt.get('mediaStatus') in ('partial','failed'):sys.exit(2)
    except (PddError,CollectionError) as e:print(json.dumps({'ok':False,'code':e.code,'message':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(2)
    except KeyboardInterrupt:print('Cancelled; checkpoints preserved',file=sys.stderr);sys.exit(130)
if __name__=='__main__':main()
