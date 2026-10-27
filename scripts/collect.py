"""Platform dispatcher: only implemented collectors are selectable."""
import argparse,sys
from urllib.parse import urlparse

def platform_for(url,explicit=None):
    if explicit:
        if explicit not in ('taobao','pdd'):raise ValueError('该平台尚未实现；当前支持淘宝/天猫、PDD商家后台')
        return explicit
    host=urlparse(url or '').hostname
    if host in ('item.taobao.com','detail.tmall.com','detail.tmall.hk'):return 'taobao'
    if host=='mms.pinduoduo.com':return 'pdd'
    raise ValueError('需要已支持平台的链接或--platform taobao/pdd')

def main():
    args=sys.argv[1:];parser=argparse.ArgumentParser(description=__doc__,add_help=False);parser.add_argument('--platform');parser.add_argument('--url');known,rest=parser.parse_known_args(args)
    if not args or args==['--help']:
        print('评论采集\nTaobao/Tmall: collect.py --url HTTPS_ITEM_URL --output-dir DIR\nPDD merchant: collect.py --platform pdd --session SESSION --tab TARGET --prepare\nPDD collect: --mall-id CURRENT_MALL --output-dir DIR [--months 1 --limit 100 --include-videos --download-media]\nJD/1688/Douyin are not implemented yet.');return
    try:platform=platform_for(known.url,known.platform)
    except ValueError as e:parser.error(str(e))
    sys.argv=[sys.argv[0]]+rest+(['--url',known.url] if platform=='taobao' and known.url else [])
    if platform=='taobao':from collect_reviews import main as run
    else:from collect_pdd_reviews import main as run
    run()
if __name__=='__main__':main()
