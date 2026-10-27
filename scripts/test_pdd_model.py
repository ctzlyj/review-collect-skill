"""Synthetic-only PDD model tests: no browser, credentials or network."""
import copy
import hashlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image
from pdd_model import (PddError, parse_page, normalize_review, specs_text,
                       month_windows, candidate_selected, download_media)

URL = 'https://img.pddpic.com/synthetic.png'
TZ = timezone(timedelta(hours=8))


def picture_bytes():
    stream = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(stream, format='PNG')
    return stream.getvalue()


def raw_row(**updates):
    return {'reviewId': 900719925474099312345, 'goodsId': 42,
            'comment': '差评也应保留，物流慢', 'descScore': 1,
            'pictures': [{'url': {'url': URL},
                          'livePhotoVideo': {'playUrl': 'https://video.pddpic.com/a.mp4'}}],
            'specs': [{'spec_key': '颜色', 'spec_value': '红色'}], **updates}


def normalized(**updates):
    return normalize_review(raw_row(**updates), '99', 'https://mms.pinduoduo.com/goods/evaluation/index', 1, '2026-10', True)


class ParsingTests(unittest.TestCase):
    def test_page_preserves_unknown_and_empty(self):
        rows = [raw_row()]
        parsed = parse_page({'success': True, 'result': {'data': rows, 'totalNum': 1, 'hasMore': False}})
        self.assertEqual(parsed, {'rows': rows, 'total': 1, 'has_more': False})
        self.assertIs(parsed['rows'], rows)
        self.assertEqual(parse_page({'success': True, 'result': {'data': []}}),
                         {'rows': [], 'total': None, 'has_more': None})
        self.assertIsNone(parse_page({'success': True, 'result': {'data': [], 'totalNum': True}})['total'])
        self.assertIsNone(parse_page({'success': True, 'result': {'data': [], 'hasMore': 1}})['has_more'])

    def test_malformed_is_not_empty_success(self):
        for value in (None, [], {}, {'success': 1, 'result': {'data': []}},
                      {'success': True, 'result': {}},
                      {'success': True, 'result': {'data': [None]}},
                      {'success': True, 'result': {'data': {}}}):
            with self.subTest(value=value), self.assertRaises(PddError):
                parse_page(value)

    def test_auth_errors_preserve_reason(self):
        for message, code in [('请登录', 'LOGIN'), ('需要验证', 'VERIFY'), ('too many requests', 'RATE_LIMIT'), ('unknown failure', 'API')]:
            with self.subTest(message=message), self.assertRaises(PddError) as raised:
                parse_page({'success': False, 'errorMsg': message})
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.message, message)

    def test_normalization_and_explicit_video(self):
        row = raw_row(createTime=0)
        saved = copy.deepcopy(row)
        value = normalize_review(row, '99', 'source-url', 2, '2026-10')
        self.assertEqual(value['id'], '900719925474099312345')
        self.assertEqual(value['itemId'], '42')
        self.assertEqual(value['text'], row['comment'])
        self.assertEqual(value['rating'], 1)
        self.assertEqual(value['sku'], '颜色:红色')
        self.assertEqual(value['date'], '1970-01-01T08:00:00+08:00')
        self.assertIsNone(value['parentId'])
        self.assertEqual(value['type'], 'initial')
        self.assertEqual(value['images'], [{'url': URL}])
        self.assertEqual(value['videos'], [])
        self.assertEqual(value['source'], {'platform': 'pinduoduo', 'mallId': '99', 'goodsId': '42',
            'reviewId': value['id'], 'page': 2, 'window': '2026-10', 'url': 'source-url'})
        self.assertEqual(len(normalized()['videos']), 1)
        self.assertEqual(row, saved)

    def test_id_boundary(self):
        for field in ('reviewId', 'goodsId'):
            for invalid in (True, False, {}, [], 1.0, None, ''):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(PddError):
                    normalize_review(raw_row(**{field: invalid}), '99', 'u', 1, 'w')
        for mall in (True, {}, None, 99):
            with self.subTest(mall=mall), self.assertRaises(PddError):
                normalize_review(raw_row(), mall, 'u', 1, 'w')
        self.assertIsNone(normalized(createTime=None, descScore=True)['date'])
        self.assertIsNone(normalized(descScore=True)['rating'])

    def test_specs_never_python_dictionary_repr(self):
        self.assertEqual(specs_text(' 原始规格 '), ' 原始规格 ')
        self.assertEqual(specs_text('{"unknown":"source"}'), '{"unknown":"source"}')
        self.assertEqual(specs_text('[{"spec_key":"尺码","spec_value":"M"}]'), '尺码:M')
        self.assertEqual(specs_text({'spec_key': '色', 'spec_value': '红'}), '色:红')
        self.assertEqual(specs_text({'unknown': 'value'}), '')
        self.assertEqual(specs_text([{'spec_key': {}, 'spec_value': []}]), '')

    def test_filter_has_no_hidden_sentiment_or_rating(self):
        self.assertTrue(candidate_selected(raw_row(), {}))
        self.assertFalse(candidate_selected(raw_row(), {'stars': 5}))
        self.assertTrue(candidate_selected(raw_row(descScore=5), {'stars': 5}))
        self.assertFalse(candidate_selected(raw_row(), {'goods_id': '43'}))
        self.assertTrue(candidate_selected(raw_row(), {'goods_id': '42'}))
        self.assertFalse(candidate_selected(raw_row(pictures=[], video=URL), {}))
        self.assertFalse(candidate_selected(raw_row(pictures=[], video=URL), {'media': 'video'}))
        self.assertTrue(candidate_selected(raw_row(pictures=[], video=URL), {'media': 'video', 'include_videos': True}))
        self.assertTrue(candidate_selected(raw_row(pictures=[]), {'media': 'all'}))

    def test_month_windows_year_boundary_and_timezone(self):
        now = datetime(2026, 1, 15, 8, 9, 10, tzinfo=TZ)
        windows = month_windows(3, now)
        self.assertEqual([w['label'] for w in windows], ['2026-01', '2025-12', '2025-11'])
        self.assertEqual(windows[0]['end'], int(now.timestamp()))
        self.assertEqual(windows[0]['start'], int(datetime(2026, 1, 1, tzinfo=TZ).timestamp()))
        for latest, older in zip(windows, windows[1:]):
            self.assertEqual(older['end'] + 1, latest['start'])
        self.assertEqual(month_windows(1, now.astimezone(timezone.utc)), windows[:1])
        for months, date in [(True, now), (0, now), (1, datetime(2026, 1, 1))]:
            with self.assertRaises(PddError):
                month_windows(months, date)


class DownloadTests(unittest.TestCase):
    def test_url_and_content_dedupe_preserves_every_review_and_existing_hash(self):
        a, b, c = normalized(), normalized(reviewId='other'), normalized(reviewId='third', pictures=[URL + '?same=1'])
        before = copy.deepcopy([a, b, c])
        calls = []
        def fetch(url, bound):
            calls.append(url)
            return picture_bytes()
        with tempfile.TemporaryDirectory() as folder:
            result = download_media([a, b, c], folder, fetcher=fetch)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(result['items']), 3)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len({item['path'] for item in result['items']}), 1)
            path = Path(result['items'][0]['path'])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(all(item['sha256'] == digest for item in result['items']))
            self.assertEqual([item['rowId'] for item in result['items']], [a['id'], b['id'], c['id']])
            download_media([a], folder, fetcher=lambda *_: self.fail('valid cached URL refetched'))
            path.write_bytes(b'corrupt')
            repaired = download_media([a], folder, fetcher=fetch)
            self.assertEqual(repaired['status'], 'complete')
            self.assertEqual(path.read_bytes(), picture_bytes())
        self.assertEqual([a, b, c], before)

    def test_failures_have_no_media_file_or_text_loss_and_no_retry(self):
        row = normalized()
        for payload in (b'<html>login verify</html>', picture_bytes()[:-8], b'x' * 101):
            with self.subTest(payload=payload[:10]), tempfile.TemporaryDirectory() as folder:
                result = download_media([row], folder, max_bytes=100, fetcher=lambda *_: payload)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['counts']['failed'], 1)
                self.assertIsNone(result['items'][0]['path'])
                self.assertTrue(result['items'][0]['reason'])
                self.assertFalse(list(Path(folder).glob('*.png')))
        for status in (403, 429):
            calls = []
            def failure(url, bound):
                calls.append(url)
                raise HTTPError(url, status, 'denied', {}, None)
            with tempfile.TemporaryDirectory() as folder:
                result = download_media([row, row], folder, fetcher=failure)
                self.assertEqual(result['counts']['failed'], 2)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['items'][0]['reason'], 'HTTP_' + str(status))
        self.assertEqual(row['text'], '差评也应保留，物流慢')

    def test_url_gate_and_opaque_id_paths(self):
        for url in ('http://img.pddpic.com/a', 'https://img.pddpic.com.evil.test/a', 'https://u:p@img.pddpic.com/a', 'file:///tmp/a'):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                row = normalized(reviewId='../../escape', pictures=[url])
                result = download_media([row], folder, fetcher=lambda *_: self.fail('unsafe URL fetched'))
                self.assertEqual(result['status'], 'failed')
        with tempfile.TemporaryDirectory() as folder:
            row = normalized(reviewId='../../escape')
            result = download_media([row], folder, fetcher=lambda *_: picture_bytes())
            self.assertEqual(Path(result['items'][0]['path']).parent, Path(folder).resolve())

    def test_video_explicit_and_partial(self):
        row = normalized()
        with tempfile.TemporaryDirectory() as folder:
            result = download_media([row], folder, include_videos=True, fetcher=lambda url, _: picture_bytes() if url == URL else b'<html>denied</html>')
            self.assertEqual(result['status'], 'partial')
            self.assertEqual([item['type'] for item in result['items']], ['image', 'video'])
            self.assertEqual(result['counts']['failed'], 1)

    def test_default_stream_is_bounded_without_real_network(self):
        class Response(io.BytesIO):
            headers = {'Content-Length': '1000'}
            status = 200
            def geturl(self): return URL
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.OpenerDirector.open', return_value=Response(b'x' * 1000)):
            result = download_media([normalized()], folder, max_bytes=100)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('SIZE', result['items'][0]['reason'])

class ResourceAndTransportTests(unittest.TestCase):
    def test_worker_batch_bounds_retained_media_payloads(self):
        from concurrent.futures import ThreadPoolExecutor
        owner = self
        class BoundedPool(ThreadPoolExecutor):
            def map(self, fn, iterable, *args, **kwargs):
                batch = list(iterable)
                owner.assertLessEqual(len(batch), self._max_workers)
                return super().map(fn, batch, *args, **kwargs)
        rows = [normalized(reviewId=str(i), pictures=[URL + '?n=' + str(i)]) for i in range(7)]
        with tempfile.TemporaryDirectory() as folder, patch('pdd_model.ThreadPoolExecutor', BoundedPool):
            self.assertEqual(download_media(rows, folder, workers=2, fetcher=lambda *_: picture_bytes())['status'], 'complete')

    def test_video_flags_require_explicit_boolean(self):
        with self.assertRaises(PddError):
            normalize_review(raw_row(), '99', 'u', 1, 'w', include_videos='false')
        with self.assertRaises(PddError):
            candidate_selected(raw_row(), {'include_videos': 'false'})
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(PddError):
            download_media([normalized()], folder, include_videos='false', fetcher=lambda *_: picture_bytes())

    def test_redirect_and_unknown_length_stream_without_network(self):
        from pdd_model import _PddRedirect, _fetch_media
        from urllib.request import Request
        with self.assertRaises(PddError):
            _PddRedirect().redirect_request(Request(URL), None, 302, '', {}, 'https://evil.test/a')
        class Response(io.BytesIO):
            headers = {}
            def geturl(self): return URL
        with patch('urllib.request.OpenerDirector.open', return_value=Response(b'x' * 1000)), self.assertRaises(PddError) as raised:
            _fetch_media(URL, 100)
        self.assertEqual(raised.exception.code, 'SIZE')

    def test_valid_video_container_and_empty_manifest(self):
        def box(tag, payload):
            return (len(payload) + 8).to_bytes(4, 'big') + tag + payload
        video = box(b'ftyp', b'isom\x00\x00\x00\x00isom') + box(b'mdat', b'synthetic')
        row = normalized(pictures=[], video='https://video.pddpic.com/fixture.mp4')
        with tempfile.TemporaryDirectory() as folder:
            result = download_media([row], folder, include_videos=True, fetcher=lambda *_: video)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(Path(result['items'][0]['path']).read_bytes(), video)
            self.assertEqual(download_media([], folder, fetcher=lambda *_: self.fail('fetch'))['status'], 'empty')


if __name__ == '__main__':
    unittest.main()
