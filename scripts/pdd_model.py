"""Pure PDD review parsing plus optional unauthenticated media downloads.

Adapted from the pinned PDD collectors' schema, specs and month windows only.
No business/sentiment filters, session capture, browser control or retry logic.
Windows use inclusive epoch seconds in UTC+08; absent page facts stay unknown.
Download manifests retain each review-to-media relationship. Images are checked
by Pillow; videos receive ISO-BMFF container checks, not codec/decode validation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from urllib import request
from urllib.error import HTTPError
from urllib.parse import urlsplit

PDD_TIMEZONE = timezone(timedelta(hours=8))
# yangkeduo appears in the source; pddpic is the explicitly supported PDD CDN.
MEDIA_HOSTS = ('pddpic.com', 'yangkeduo.com')


class PddError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _identity(value, name):
    if isinstance(value, str) and value.strip():
        return value
    if type(value) is int and value >= 0:
        return str(value)
    raise PddError('IDENTITY', f'{name} must be a nonempty string or nonnegative integer')


def _integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value):
        return int(value)
    return None


def parse_page(obj):
    if not isinstance(obj, dict) or type(obj.get('success')) is not bool:
        raise PddError('FORMAT', 'Missing boolean success flag')
    if not obj['success']:
        message = obj.get('errorMsg')
        message = message if isinstance(message, str) and message else 'PDD API failed'
        lowered = message.lower()
        code = ('LOGIN' if any(x in lowered for x in ('login', '登录', 'session', '未登陆')) else
                'VERIFY' if any(x in lowered for x in ('verify', '验证', 'captcha', '验证码', 'anti-content')) else
                'RATE_LIMIT' if any(x in lowered for x in ('429', 'too many', '频繁', '限流')) else 'API')
        raise PddError(code, message)
    result = obj.get('result')
    if not isinstance(result, dict) or not isinstance(result.get('data'), list):
        raise PddError('FORMAT', 'Missing result.data review array; completion is unknown')
    rows = result['data']
    if not all(isinstance(row, dict) for row in rows):
        raise PddError('FORMAT', 'Review array contains a non-object')
    total = _integer(result.get('totalNum'))
    more = result.get('hasMore')
    return {'rows': rows, 'total': total if total is not None and total >= 0 else None,
            'has_more': more if type(more) is bool else None}


def specs_text(value):
    source_string = value if isinstance(value, str) else ''
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if not isinstance(parsed, (dict, list)):
            return value
        value = parsed
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return ''
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key, val = item.get('spec_key'), item.get('spec_value')
        if isinstance(key, str) and isinstance(val, (str, int, float)) and not isinstance(val, bool):
            key, val = key.strip(), str(val).strip()
            if key and val:
                parts.append(f'{key}:{val}')
    return '；'.join(parts) if parts else source_string


def _media_url(value):
    # Nested URL objects are owned here, not reinterpreted by consumers.
    for _ in range(16):
        if isinstance(value, str):
            return value if value else None
        if not isinstance(value, dict):
            return None
        value = next((value[key] for key in ('url', 'videoUrl', 'playUrl', 'downloadUrl')
                      if value.get(key)), None)
    return None


def _media(row, include_videos):
    if type(include_videos) is not bool:
        raise PddError('INPUT', 'include_videos must be an explicit boolean')
    pictures = row.get('pictures')
    pictures = pictures if isinstance(pictures, list) else []
    images, videos = [], []
    for item in pictures:
        url = _media_url(item)
        if url and {'url': url} not in images:
            images.append({'url': url})
    if include_videos:
        candidates = [row.get('video')] + [item.get('livePhotoVideo') for item in pictures if isinstance(item, dict)]
        for item in candidates:
            url = _media_url(item)
            if url and {'url': url} not in videos:
                videos.append({'url': url})
    return images, videos


def normalize_review(row, mall_id: str, source_url: str, page: int, window: str, include_videos=False):
    if not isinstance(row, dict):
        raise PddError('FORMAT', 'Review must be an object')
    if not isinstance(mall_id, str) or not mall_id.strip():
        raise PddError('IDENTITY', 'Caller must provide an explicit mall_id string')
    review_id = _identity(row.get('reviewId'), 'reviewId')
    goods_id = _identity(row.get('goodsId'), 'goodsId')
    if type(page) is not int or page < 1 or not isinstance(window, str) or not isinstance(source_url, str):
        raise PddError('INPUT', 'Invalid source page/window/url')
    images, videos = _media(row, include_videos)
    timestamp = _integer(row.get('createTime'))
    date = row.get('createTime') if isinstance(row.get('createTime'), str) and timestamp is None else None
    if timestamp is not None:
        try:
            date = datetime.fromtimestamp(timestamp, PDD_TIMEZONE).isoformat()
        except (OverflowError, ValueError, OSError):
            date = None
    rating = _integer(row.get('descScore'))
    return {'id': review_id, 'parentId': None, 'itemId': goods_id, 'type': 'initial',
            'text': row.get('comment') if isinstance(row.get('comment'), str) else '',
            'date': date, 'sku': specs_text(row.get('specs')) or None,
            'images': images, 'videos': videos,
            'source': {'platform': 'pinduoduo', 'mallId': mall_id, 'goodsId': goods_id,
                       'reviewId': review_id, 'page': page, 'window': window, 'url': source_url},
            'rating': rating if rating is not None and 1 <= rating <= 5 else None}


def month_windows(months, now):
    if type(months) is not int or months < 1 or not isinstance(now, datetime) or now.utcoffset() is None:
        raise PddError('INPUT', 'Positive months and timezone-aware now required')
    now = now.astimezone(PDD_TIMEZONE)
    # Calendar range is a real datetime-format constraint, not a pagination cap.
    if months > (now.year - 1) * 12 + now.month:
        raise PddError('INPUT', 'Month range precedes supported calendar')
    year, month = now.year, now.month
    windows = []
    for _ in range(months):
        start = datetime(year, month, 1, tzinfo=PDD_TIMEZONE)
        if year == now.year and month == now.month:
            end = int(now.timestamp())
        else:
            next_start = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=PDD_TIMEZONE)
            end = int(next_start.timestamp()) - 1
        windows.append({'label': f'{year:04d}-{month:02d}', 'start': int(start.timestamp()), 'end': end})
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return windows


def candidate_selected(row, options=None):
    options = {} if options is None else options
    if not isinstance(row, dict) or not isinstance(options, dict):
        raise PddError('INPUT', 'Review and selection options must be objects')
    _identity(row.get('reviewId'), 'reviewId')
    goods = _identity(row.get('goodsId'), 'goodsId')
    if options.get('goods_id') is not None and goods != _identity(options['goods_id'], 'goods_id'):
        return False
    stars = options.get('stars')
    if stars is not None:
        if type(stars) is not int or not 1 <= stars <= 5:
            raise PddError('INPUT', 'stars must be an integer from 1 to 5')
        if _integer(row.get('descScore')) != stars:
            return False
    media = options.get('media', 'image')
    if media not in ('image', 'video', 'all'):
        raise PddError('INPUT', 'media must be image, video or all')
    images, videos = _media(row, options.get('include_videos', False))
    return bool(images) if media == 'image' else bool(videos) if media == 'video' else True


def _allowed_url(url):
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url):
        raise PddError('URL', 'Invalid media URL')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)
                or not any(host == domain or host.endswith('.' + domain) for domain in MEDIA_HOSTS)):
            raise PddError('URL', 'Media URL must use an approved HTTPS PDD/CDN host without credentials')
    except ValueError as exc:
        raise PddError('URL', 'Malformed media URL') from exc
    return url


class _PddRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_media(url, max_bytes):
    _allowed_url(url)
    opener = request.build_opener(_PddRedirect())
    with opener.open(request.Request(url), timeout=30) as response:
        _allowed_url(response.geturl())
        length = response.headers.get('Content-Length')
        if length is not None:
            try:
                if int(length) > max_bytes:
                    raise PddError('SIZE', 'Media exceeds max_bytes')
            except ValueError as exc:
                raise PddError('FORMAT', 'Invalid Content-Length') from exc
        data = bytearray()
        while True:
            chunk = response.read(min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise PddError('SIZE', 'Media exceeds max_bytes')
        return bytes(data)


def _media_suffix(data, kind):
    if kind == 'image':
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}.get(image.format)
            if suffix is None:
                raise ValueError('Unsupported image format')
            return suffix
        except Exception as exc:
            raise PddError('MEDIA', 'Image integrity/format verification failed') from exc
    # Require complete ISO-BMFF boxes, ftyp first, and nonempty media data.
    cursor, media = 0, False
    while cursor < len(data):
        if len(data) - cursor < 8:
            raise PddError('MEDIA', 'Truncated video container')
        size, tag = int.from_bytes(data[cursor:cursor + 4], 'big'), data[cursor + 4:cursor + 8]
        if cursor == 0 and (tag != b'ftyp' or size < 16):
            raise PddError('MEDIA', 'Expected ISO-BMFF video signature')
        if size < 8 or cursor + size > len(data):
            raise PddError('MEDIA', 'Invalid video box bounds')
        media = media or (tag == b'mdat' and size > 8)
        cursor += size
    if not media:
        raise PddError('MEDIA', 'Video has no media data')
    return '.mp4'


def _atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='.pdd-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def download_media(rows, output_dir, include_videos=False, workers=4, max_bytes=30_000_000, fetcher=None):
    """Return items/status/counts; cache URLs within this output directory only.

    Each item has source,rowId,url,type,status,path,sha256,reason. Content hashes
    deduplicate files across URLs without discarding review relationships. Cache
    hits verify the file hash and media integrity before reuse. No retries occur.
    Status: complete/partial/failed/empty; item status: saved/reused/error.
    """
    if type(include_videos) is not bool:
        raise PddError('INPUT', 'include_videos must be an explicit boolean')
    if type(workers) is not int or workers < 1 or type(max_bytes) is not int or max_bytes < 1:
        raise PddError('INPUT', 'workers and max_bytes must be positive integers')
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / 'media-index.json'
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if not isinstance(index, dict):
            index = {}
    except (OSError, ValueError):
        index = {}
    tasks, relationships = {}, []
    for row in rows:
        row_id = _identity(row.get('id'), 'row.id')
        for kind, field in [('image', 'images')] + ([('video', 'videos')] if include_videos else []):
            for media in row.get(field, []):
                url = media.get('url') if isinstance(media, dict) else None
                base = {'source': dict(row.get('source') or {}), 'rowId': row_id, 'url': url, 'type': kind}
                # Malformed URL objects never become dictionary keys or paths.
                key = (kind, url) if isinstance(url, str) else (kind, None)
                tasks.setdefault(key, None)
                relationships.append((key, base))
    fetch = fetcher or _fetch_media

    def task(key):
        kind, url = key
        try:
            _allowed_url(url)
            cache_key = hashlib.sha256((kind + '\n' + url).encode('utf-8')).hexdigest()
            cached = index.get(cache_key)
            if isinstance(cached, dict) and re.fullmatch(r'[0-9a-f]{64}', str(cached.get('sha256', ''))):
                digest, suffix = cached['sha256'], cached.get('suffix')
                if suffix in ('.jpg', '.png', '.webp', '.gif', '.mp4'):
                    path = folder / (digest + suffix)
                    if not path.is_symlink() and path.is_file() and path.stat().st_size <= max_bytes:
                        data = path.read_bytes()
                        if hashlib.sha256(data).hexdigest() == digest:
                            try:
                                if _media_suffix(data, kind) == suffix:
                                    return key, cache_key, {'status': 'reused', 'path': str(path), 'sha256': digest, 'reason': None}, suffix
                            except PddError:
                                pass
            data = fetch(url, max_bytes)
            if not isinstance(data, bytes):
                raise PddError('FORMAT', 'fetcher must return bytes')
            if len(data) > max_bytes:
                raise PddError('SIZE', 'Media exceeds max_bytes')
            suffix = _media_suffix(data, kind)
            digest = hashlib.sha256(data).hexdigest()
            return key, cache_key, {'status': 'saved', 'path': str(folder / (digest + suffix)), 'sha256': digest, 'reason': None, '_data': data}, suffix
        except Exception as exc:
            reason = f'HTTP_{exc.code}' if isinstance(exc, HTTPError) else exc.code if isinstance(exc, PddError) else type(exc).__name__
            return key, None, {'status': 'error', 'path': None, 'sha256': None, 'reason': reason}, None

    # Bound completed/in-flight payload retention to workers, not all URLs.
    # One owner serializes content and cache updates after each bounded batch.
    pending = iter(tasks)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while batch := list(islice(pending, workers)):
            for key, cache_key, result, suffix in pool.map(task, batch):
                if result['status'] != 'error':
                    data = result.pop('_data', None)
                    if data is not None:
                        path = Path(result['path'])
                        try:
                            if not path.is_symlink() and path.is_file() and path.stat().st_size <= max_bytes and hashlib.sha256(path.read_bytes()).hexdigest() == result['sha256']:
                                result['status'] = 'reused'
                            else:
                                _atomic_write(path, data)
                        except OSError as exc:
                            result = {'status': 'error', 'path': None, 'sha256': None, 'reason': type(exc).__name__}
                    if result['status'] != 'error':
                        index[cache_key] = {'sha256': result['sha256'], 'suffix': suffix}
                tasks[key] = result
    items = []
    seen = set()
    for key, base in relationships:
        value = dict(tasks[key])
        if key in seen and value['status'] == 'saved':
            value['status'] = 'reused'
        seen.add(key)
        items.append({**base, **value})
    if tasks:
        _atomic_write(index_path, json.dumps(index, ensure_ascii=False, sort_keys=True).encode('utf-8'))
    counts = {'total': len(items), 'saved': sum(x['status'] == 'saved' for x in items),
              'reused': sum(x['status'] == 'reused' for x in items),
              'failed': sum(x['status'] == 'error' for x in items)}
    status = 'empty' if not items else 'complete' if not counts['failed'] else 'failed' if counts['failed'] == len(items) else 'partial'
    return {'items': items, 'status': status, 'counts': counts}
