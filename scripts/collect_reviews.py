"""Normal-session, read-only review collector. No browser credentials copied or stored."""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

class CollectionError(Exception):
    def __init__(self, code, message): super().__init__(message); self.code = code

def fail(code, message): raise CollectionError(code, message)
def parse_item_url(raw):
    normalized=html.unescape(str(raw).strip()).replace('\\&','&')
    u=urlparse(normalized); ids=parse_qs(u.query).get('id',[])
    if u.scheme!='https' or u.hostname not in ('item.taobao.com','detail.tmall.com','detail.tmall.hk') or u.path!='/item.htm' or u.username or u.password:
        fail('INPUT','需要完整的淘宝/天猫HTTPS商品详情链接')
    if len(ids)!=1 or not re.fullmatch(r'\d+',ids[0]): fail('INPUT','商品ID缺失或重复')
    return {'id':ids[0],'url':normalized,'platform':'tmall' if 'tmall' in u.hostname else 'taobao'}

def parse_response(raw):
    if not isinstance(raw,str): fail('FORMAT','响应必须为原始文本')
    text=raw.strip()
    try: data=json.loads(text)
    except json.JSONDecodeError:
        match=re.fullmatch(r'(?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)?\s*\(([\s\S]*)\)\s*;?',text)
        if not match:
            if re.search(r'login|登录',text,re.I):fail('LOGIN','需要在正常浏览器登录')
            if re.search(r'verify|滑块|验证码|_____tmd',text,re.I):fail('VERIFY','需要人工完成页面验证')
            fail('FORMAT','响应不是JSON/合法JSONP；未执行任何样本代码')
        try:data=json.loads(match.group(1))
        except json.JSONDecodeError:fail('FORMAT','JSON包装内数据解析失败')
    if not isinstance(data,dict):fail('FORMAT','响应对象格式变化')
    challenge=data.get('url') or (data.get('data',{}).get('url') if isinstance(data.get('data'),dict) else None)
    if challenge:fail('LOGIN' if 'login.taobao' in str(challenge) else 'VERIFY','接口要求登录/手动验证；未自动绕过')
    if 'ret' in data:
        ret=data['ret']
        if not isinstance(ret,list) or not ret or not all(isinstance(x,str) for x in ret):fail('FORMAT','ret格式变化')
        errors=[x for x in ret if x.split('::')[0]!='SUCCESS']
        if errors:
            msg=';'.join(errors);fail('LOGIN' if re.search('LOGIN|SESSION',msg) else 'VERIFY' if re.search('VALIDATE|CREEP',msg) else 'API',msg)
    owner=data
    if not isinstance(owner.get('comments'),list) and isinstance(owner.get('data'),dict):owner=owner['data']
    if not isinstance(owner.get('comments'),list):fail('FORMAT','没有comments数组，不能认为已采完')
    total=owner.get('total'); total=total if isinstance(total,int) and not isinstance(total,bool) and total>=0 else None
    raw_more=owner.get('hasMore'); more=raw_more if isinstance(raw_more,bool) else len(owner['comments'])>=20
    return {'comments':owner['comments'],'total':total,'hasMore':more,'hasMoreBasis':'explicit' if isinstance(raw_more,bool) else 'page-length'}

def safe_url(raw):
    if not isinstance(raw,str) or not raw:return None
    value='https:'+raw if raw.startswith('//') else raw
    u=urlparse(value)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return None
    return value

def original_image_url(value):
    u=urlparse(value)
    if u.hostname=='alicdn.com' or (u.hostname or '').endswith('.alicdn.com'):
        path=re.sub(r'(\.(?:jpe?g|png|webp|heic|heif|avif))_\d+x\d+[a-z]*\.(?:jpe?g|png|webp)$',r'\1',u.path,flags=re.I)
        return urlunparse(u._replace(path=path))
    return value

def as_list(value,name):
    if value is None:return []
    if not isinstance(value,list):fail('FORMAT',name+'必须是数组')
    return value

def scalar_id(value,name):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or str(value).strip()=='':fail('FORMAT',name+'不是稳定ID')
    return str(value)

def photo_list(row):
    result=[]
    for p in as_list(row.get('photos',row.get('pics')),'photos'):
        raw=p if isinstance(p,str) else p.get('url',p.get('originSrc')) if isinstance(p,dict) else None
        url=safe_url(raw)
        if not url:fail('FORMAT','图片URL缺失或无效')
        thumb=safe_url(p.get('thumbnail',p.get('src'))) if isinstance(p,dict) else None
        result.append({'url':original_image_url(url),'thumbnail':thumb or url,'sourceUrl':url})
    return result

def video_list(row):
    sources=([row['video']] if row.get('video') else [])+as_list(row.get('videoList'),'videoList')+as_list(row.get('videos'),'videos')
    result=[];seen=set()
    for v in sources:
        raw=v if isinstance(v,str) else v.get('cloudVideoUrl',v.get('videoUrl',v.get('url'))) if isinstance(v,dict) else None
        url=safe_url(raw);cover=safe_url(v.get('coverUrl',v.get('cover'))) if isinstance(v,dict) else None
        if url:
            if url not in seen:seen.add(url);result.append({'url':url,'cover':cover})
        elif isinstance(v,dict) and v.get('videoId') is not None:
            source=scalar_id(v['videoId'],'videoId');key='missing:'+source
            if key not in seen:seen.add(key);result.append({'url':None,'cover':cover,'sourceId':source,'unavailable':'源响应未提供播放URL'})
        else:fail('FORMAT','视频缺失URL与可保留身份')
    return result

def validate_page_identity(comments,item):
    for c in comments:
        auction=c.get('auction') if isinstance(c,dict) else None
        declared=auction.get('aucNumId') if isinstance(auction,dict) else None
        if str(declared or '')!=item['id']:fail('IDENTITY','评价条目aucNumId缺失或与请求商品不一致')

def normalize_comments(comments,item,page,url):
    result=[]
    for c in as_list(comments,'comments'):
        if not isinstance(c,dict):fail('FORMAT','评论条目无效')
        raw_id=scalar_id(c.get('rateId',c.get('id')),'rateId'); rid=item['id']+':'+raw_id
        auction=c.get('auction');sku=auction.get('sku') if isinstance(auction,dict) else c.get('auctionSku')
        if sku is not None and not isinstance(sku,str):fail('FORMAT','SKU不是文本')
        def row(x,identity,parent,kind):
            text=x.get('content',x.get('rateContent',''));date=x.get('date',x.get('rateDate'))
            if text is None:text=''
            if not isinstance(text,str) or date is not None and not isinstance(date,str):fail('FORMAT','内容/日期格式变化')
            return {'id':identity,'parentId':parent,'itemId':item['id'],'type':kind,'text':html.unescape(text),'sku':html.unescape(sku) if sku is not None else None,'date':date,'images':photo_list(x),'videos':video_list(x),'source':{'page':page,'url':url}}
        result.append(row(c,rid,None,'initial'));seen=set()
        additions=([c['append']] if c.get('append') else [])+as_list(c.get('appendList'),'appendList')
        for a in additions:
            if not isinstance(a,dict):fail('FORMAT','追评条目无效')
            aid=rid+':append:'+scalar_id(a.get('appendId',a.get('id')),'appendId')
            if aid in seen:continue
            seen.add(aid);result.append(row(a,aid,rid,'append'))
    return result

def merge_rows(previous,incoming):
    result={x['id']:x for x in previous}
    for r in incoming:
        if r['id'] not in result:result[r['id']]=r
    return list(result.values())

def projected_rows(rows,filter_name):
    return [r for r in rows if r.get('videos')] if filter_name=='video' else list(rows)

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)

def write_checkpoint(path,item,page,filter_name,url,raw):
    record={'schema':'taobao-review-page','version':1,'itemId':item['id'],'sellerId':item.get('sellerId'),'page':page,'filter':filter_name,'requestUrl':url,'rawText':raw,'responseSha256':hashlib.sha256(raw.encode('utf-8')).hexdigest(),'capturedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    atomic_json(path,record);return record

def read_checkpoint(path,item,page,filter_name):
    try:r=json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as e:fail('CHECKPOINT','检查点不可读：'+str(e))
    if r.get('schema')!='taobao-review-page' or r.get('itemId')!=item['id'] or r.get('sellerId')!=item.get('sellerId') or r.get('page')!=page or r.get('filter')!=filter_name:fail('CHECKPOINT','检查点身份/筛选不匹配')
    raw=r.get('rawText')
    if not isinstance(raw,str) or hashlib.sha256(raw.encode('utf-8')).hexdigest()!=r.get('responseSha256'):fail('CHECKPOINT','检查点内容digest不匹配')
    return r

class WebCLI:
    def __init__(self,session,entry=None):
        self.session=session
        shim=shutil.which('webcli')
        candidate=Path(entry) if entry else Path(shim).parent/'node_modules/@jd/webcli/dist/src/main.js' if shim else None
        if not candidate or not candidate.is_file():fail('TOOL','WebCLI Node入口不可定位，请传--webcli-entry；不安装/修改工具')
        node=shutil.which('node')
        if not node:fail('TOOL','Node不可用')
        self.command=[node,str(candidate)]
    def call(self,args):
        env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
        try:r=subprocess.run(self.command+['browser',self.session]+args,capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=40,env=env)
        except subprocess.TimeoutExpired:fail('RESULT_UNKNOWN','WebCLI观察超时；未自动重放，检查既有浏览器会话再恢复')
        if r.returncode:fail('BROWSER',r.stderr.strip() or r.stdout.strip() or 'WebCLI失败，不认为空结果')
        try:value=json.loads(r.stdout.strip())
        except json.JSONDecodeError:fail('BROWSER','WebCLI响应不是JSON；保留会话，不猜成功')
        if isinstance(value,dict) and isinstance(value.get('error'),dict):fail('BROWSER',str(value['error']))
        return value
    def eval(self,tab,source):
        value=self.call(['eval','--tab',tab,source])
        # WebCLI normally presents JSON.stringify result as decoded JSON text.
        return json.loads(value) if isinstance(value,str) else value
    def new(self,url):
        r=self.call(['tab','new',url]);return r.get('page') or fail('BROWSER','新页缺少真实target ID')
    def close(self,tab):
        self.call(['tab','close',tab])

def context_source(item_id):
    return '(async()=>{const expected='+json.dumps(item_id)+r';const id=new URL(location.href).searchParams.get("id");if(id!==expected)return JSON.stringify({error:"IDENTITY",message:"商品页身份变更或需登录"});const b=window.g_bizdata?.itemPvItem;if(b&&String(b.itemId)===id&&b.sellerId)return JSON.stringify({itemId:id,sellerId:String(b.sellerId),title:document.title,url:location.href,source:"g_bizdata.itemPvItem"});if(!window.lib?.mtop?.request)return JSON.stringify({error:"LOADING",message:"正常页面SDK未就绪"});try{const r=await lib.mtop.request({api:"mtop.taobao.pcdetail.data.get",v:"1.0",data:{id,detail_v:"3.3.2",exParams:JSON.stringify({queryParams:location.search.slice(1),domain:location.origin,path_name:location.pathname,pcSource:"pcTaobaoMain"})},isSec:"0",ecode:"0",timeout:10000,jsonpIncPrefix:"pcdetail",ttid:"2022@taobao_litepc_9.17.0",AntiFlood:true,AntiCreep:true});if(!Array.isArray(r.ret)||r.ret.some(x=>String(x).split("::")[0]!=="SUCCESS"))return JSON.stringify({error:"API",message:JSON.stringify(r.ret)});const d=r.data;if(String(d?.item?.itemId)!==id||!d?.seller)return JSON.stringify({error:"IDENTITY",message:"接口没有匹配商品卖家信息"});return JSON.stringify({itemId:id,sellerId:d.seller.userId?String(d.seller.userId):null,shopId:d.seller.shopId?String(d.seller.shopId):null,title:d.item.title,url:location.href,source:"normal-page-mtop-pcdetail"});}catch(e){return JSON.stringify({error:"API",message:JSON.stringify(e.ret||e.message)});}})()'

def rate_url(item,page,filter_name):
    if item.get('sellerId') is not None and not re.fullmatch(r'\d+',str(item['sellerId'])):fail('IDENTITY','卖家ID格式错误')
    params={'auctionNumId':item['id'],'currentPageNum':page,'pageSize':20,'rateType':'3' if filter_name=='image' else '2' if filter_name=='append' else '', 'orderType':'sort_weight','attribute':'','sku':'','hasSku':'false','folded':0}
    if item.get('sellerId'):params['userNumId']=item['sellerId']
    return 'https://rate.taobao.com/feedRateList.htm?'+urlencode(params)

def page_source(url):
    return '(async()=>{const url='+json.dumps(url)+r';if(location.origin!=="https://rate.taobao.com")return JSON.stringify({error:"IDENTITY",message:"评价来源标签页已变更"});try{const r=await fetch(url,{credentials:"include",headers:{Accept:"application/json, text/javascript, */*; q=0.01","X-Requested-With":"XMLHttpRequest"},signal:AbortSignal.timeout(12000)});const rawText=await r.text();if(rawText.length>8*1024*1024)return JSON.stringify({error:"CAPACITY",message:"单页超过8MiB"});return JSON.stringify({status:r.status,rawText,responseUrl:r.url});}catch(e){return JSON.stringify({error:"NETWORK",message:e.message});}})()'

def collect_pages(item,filter_name,raw_dir,get_page,max_pages=500,delay=1.5,online=True):
    rows=[];raw_count=0;total=None;complete=False;reason='page-budget';basis=None;page=1
    for page in range(1,max_pages+1):
        cp=Path(raw_dir)/('page-%04d.json'%page)
        record=None
        if cp.exists():
            record=read_checkpoint(cp,item,page,filter_name)
            try:parse_response(record['rawText'])
            except CollectionError as error:
                if not online or error.code not in ('LOGIN','VERIFY','API'):raise
                attempts=cp.parent/'failed-attempts';attempts.mkdir(exist_ok=True)
                archive=attempts/(cp.stem+'-'+record['responseSha256'][:16]+'.json')
                if not archive.exists():atomic_json(archive,record)
                # A new user-requested online invocation retries a known rejected read once.
                # No in-call repeated auth retry or unknown transport replay.
                record=None
        if record is None:
            url=rate_url(item,page,filter_name);r=get_page(url)
            if r.get('error'):fail(r['error'],r.get('message','评价请求错误'))
            if r.get('status')!=200:fail('HTTP','评价HTTP '+str(r.get('status')))
            raw=r.get('rawText')
            if not isinstance(raw,str):fail('FORMAT','WebCLI未返回响应原文')
            # Keep received evidence, including a challenge response, before interpretation.
            record=write_checkpoint(cp,item,page,filter_name,url,raw)
            if delay:time.sleep(delay)
        parsed=parse_response(record['rawText']);validate_page_identity(parsed['comments'],item);new=normalize_comments(parsed['comments'],item,page,record['requestUrl']);merged=merge_rows(rows,new)
        if new and len(merged)==len(rows):fail('PAGING','重复分页，停止；不认为已采全')
        rows=merged;raw_count+=len(parsed['comments']);total=parsed['total'];basis=parsed['hasMoreBasis']
        print(json.dumps({'page':page,'raw_comments':len(parsed['comments']),'loaded_rows':len(rows),'reported_total':total,'checkpoint':str(cp)},ensure_ascii=False),flush=True)
        atomic_json(Path(raw_dir).parent/'progress.json',{'item':item,'filter':filter_name,'lastPage':page,'rawCount':raw_count,'loadedRows':len(rows),'reportedTotal':total,'status':'collecting','rows':rows})
        if len(rows)>20000:fail('CAPACITY','本地任务超过20000行，保留检查点请分段处理')
        if not parsed['hasMore']:complete=True;reason='explicit-end' if basis=='explicit' else 'short-page';break
    return {'item':item,'rows':projected_rows(rows,filter_name),'sourceRows':rows if filter_name=='video' else None,'coverage':{'filter':filter_name,'serverFilter':'all' if filter_name=='video' else filter_name,'localProjection':'video' if filter_name=='video' else None,'pages':page,'rawCount':raw_count,'loadedRows':len(rows),'reportedTotal':total,'complete':complete,'endReason':reason,'hasMoreBasis':basis,'limitations':['仅当前正常访问的筛选与分页范围；不保证平台隐藏/删除/受限评价。']}}

def run(args):
    output=Path(args.output_dir).resolve();output.mkdir(parents=True,exist_ok=True);manifest=output/'session.json';owned=[];bridge=None
    if args.from_raw:
        if not manifest.exists():fail('CHECKPOINT','离线重放需要session.json与原始页检查点')
        session=json.loads(manifest.read_text(encoding='utf-8'));item=session['item']
        if args.url and parse_item_url(args.url)['id']!=item['id']:fail('IDENTITY','输入商品不同于检查点')
        filter_name=args.filter or session['filter']
    else:
        if not args.url:fail('INPUT','需要--url')
        item=parse_item_url(args.url);filter_name=args.filter or 'image'
        bridge=WebCLI(args.session or 'review-'+uuid.uuid4().hex[:10],args.webcli_entry)
        tab=args.product_tab or bridge.new(item['url']);
        if not args.product_tab:owned.append(tab)
        atomic_json(output/'browser-attempt.json',{'schema':'taobao-review-browser-attempt','item':item,'filter':filter_name,'browserSession':bridge.session,'productTab':tab,'productTabOwned':not bool(args.product_tab),'status':'preparing','originalInputUrl':args.url})
        context=None
        for attempt in range(16):
            context=bridge.eval(tab,context_source(item['id']))
            if context.get('error')!='LOADING':break
            time.sleep(1)
        if context.get('error'):
            atomic_json(output/'error.json',{'code':context['error'],'message':context.get('message'),'status':'needs-user-or-browser-repair','browserAttempt':str(output/'browser-attempt.json')})
            fail(context['error'],context.get('message','页面上下文失败'))
        if context.get('itemId')!=item['id']:fail('IDENTITY','实际商品身份未匹配')
        item.update({'sellerId':context['sellerId'],'shopId':context.get('shopId'),'title':context.get('title'),'identitySource':context.get('source')})
        # Existing evidence is immutable for a different target/seller/filter.
        if manifest.exists():
            old=json.loads(manifest.read_text(encoding='utf-8'))
            if old['item']['id']!=item['id'] or old['item'].get('sellerId')!=item.get('sellerId') or old['filter']!=filter_name:fail('IDENTITY','输出目录属于不同商品/卖家/筛选；请使用新目录')
        atomic_json(manifest,{'schema':'taobao-review-session','version':1,'item':item,'filter':filter_name,'browserSession':bridge.session,'productTab':tab,'originalInputUrl':args.url})
        rate_tab=args.rate_tab or bridge.new(rate_url(item,1,filter_name))
        if not args.rate_tab:owned.append(rate_tab)
        session=json.loads(manifest.read_text(encoding='utf-8'));session['rateTab']=rate_tab;atomic_json(manifest,session)
    def get_page(url):
        if args.from_raw:fail('CHECKPOINT','离线检查点不完整，不能补成完成')
        return bridge.eval(rate_tab,page_source(url))
    try:
        result=collect_pages(item,filter_name,output/'raw',get_page,args.max_pages,args.delay,online=not args.from_raw)
        atomic_json(output/'reviews.json',result)
        from export_workbook import export_workbook
        receipt=export_workbook(output/'reviews.json',output/(args.filename or ('淘宝_'+item['id']+'_评价.xlsx')),Path(args.template) if args.template else None,exclude_videos=getattr(args,'exclude_videos',False))
        receipt.update({'coverage':result['coverage'],'rawDirectory':str(output/'raw'),'normalizedData':str(output/'reviews.json')});atomic_json(output/'receipt.json',receipt)
        print(json.dumps(receipt,ensure_ascii=False),flush=True)
        if not result['coverage']['complete']:fail('PARTIAL','已达到本地页数预算，结果已导出但未宣称采完')
        return receipt
    except (CollectionError,KeyboardInterrupt) as e:
        atomic_json(output/'error.json',{'code':getattr(e,'code','CANCELLED'),'message':str(e),'checkpointDirectory':str(output/'raw'),'status':'partial','instruction':'不要把登录/格式/超时当零评论；保留浏览器会话和本地页。人工处理后使用同目录恢复或先离线导出progress。'})
        raise
    finally:
        if bridge and not args.keep_tabs:
            for tab in reversed(owned):
                try:bridge.close(tab)
                except Exception:pass # Unknown lease cleanup is retained in session.json, never close unrelated tabs.

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url');p.add_argument('--output-dir',required=True);p.add_argument('--filter',choices=['image','all','video','append']);p.add_argument('--session');p.add_argument('--product-tab');p.add_argument('--rate-tab');p.add_argument('--webcli-entry');p.add_argument('--template');p.add_argument('--filename');p.add_argument('--exclude-videos',action='store_true',help='不导出视频链接，保留评论和原始证据');p.add_argument('--max-pages',type=int,default=500);p.add_argument('--delay',type=float,default=1.5);p.add_argument('--from-raw',action='store_true');p.add_argument('--keep-tabs',action='store_true');a=p.parse_args()
    if not 1<=a.max_pages<=500 or not 0<=a.delay<=60:p.error('max-pages须1..500、delay须0..60')
    try:run(a)
    except CollectionError as e:print(json.dumps({'ok':False,'code':e.code,'message':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(2)
    except KeyboardInterrupt:print('Cancelled; checkpoints preserved',file=sys.stderr);sys.exit(130)
if __name__=='__main__':main()
