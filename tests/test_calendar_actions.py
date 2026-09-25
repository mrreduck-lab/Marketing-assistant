import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import calendar_actions as calendar


def multistatus(responses):
    return (f'<d:multistatus xmlns:d="DAV:" xmlns:c="{calendar.CAL}">'
            + responses + '</d:multistatus>').encode()


def response(href, properties):
    return (f'<d:response><d:href>{href}</d:href><d:propstat><d:prop>{properties}</d:prop>'
            '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')


class CalendarTests(unittest.TestCase):
    def test_discovery_and_read_only_report(self):
        calls = []
        ics = 'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:abc\nSUMMARY:Партнёрская встреча\nDTSTART;TZID=Europe/Moscow:20260925T100000\nDTEND;TZID=Europe/Moscow:20260925T110000\nLOCATION:Бутик\\, Москва\nEND:VEVENT\nEND:VCALENDAR'

        def request(method, url, body=b'', depth=None):
            calls.append((method, url, body))
            if method == 'REPORT':
                self.assertIn(b'20260924T210000Z', body)
                self.assertIn(b'20260925T210000Z', body)
                return multistatus(response('/me/main/1.ics', f'<c:calendar-data>{ics}</c:calendar-data>'))
            if url == 'https://calendar.mail.ru/':
                return multistatus(response('/', '<d:current-user-principal><d:href>/me/</d:href></d:current-user-principal>'))
            if url == 'https://calendar.mail.ru/me/':
                return multistatus(response('/me/', '<c:calendar-home-set><d:href>/me/home/</d:href></c:calendar-home-set>'))
            return multistatus(response('/me/home/main/', '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Рабочий</d:displayname>'))

        with patch.object(calendar, '_request', side_effect=request):
            data = calendar.list_events('2026-09-25', '2026-09-26')
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(data['events'][0]['title'], 'Партнёрская встреча')
        self.assertEqual(data['events'][0]['location'], 'Бутик, Москва')
        self.assertEqual(data['events'][0]['start'], '2026-09-25T10:00:00+03:00')
        self.assertEqual([item[0] for item in calls], ['PROPFIND', 'PROPFIND', 'PROPFIND', 'REPORT'])

    def test_reject_unknown_calendar_and_large_range(self):
        with self.assertRaises(ValueError):
            calendar.list_events('2026-09-01', '2027-01-01')
        with patch.object(calendar, '_calendars', return_value=[{'id': 'https://calendar.mail.ru/me/main/', 'name': 'Work'}]):
            with self.assertRaises(ValueError):
                calendar.list_events('2026-09-01', '2026-09-02', 'https://example.com/private')


    def test_preview_and_create_verified_event(self):
        cal = {'id': 'https://calendar.mail.ru/me/home/main/', 'name': 'Рабочий'}
        state = {'saved': b'', 'puts': 0}
        def request(method, url, body=b'', depth=None, content_type='', extra_headers=None):
            if method == 'GET':
                if not state['saved']:
                    raise RuntimeError('CalDAV returned HTTP 404')
                return state['saved']
            if method == 'PUT':
                self.assertEqual(extra_headers, {'If-None-Match': '*'})
                self.assertIn(b'BEGIN:VEVENT', body)
                self.assertIn(b'DTSTART:20260925T070000Z', body)
                state['saved'] = body
                state['puts'] += 1
                return b''
            raise AssertionError(method)
        with patch.object(calendar, '_calendars', return_value=[cal]), \
             patch.object(calendar, '_request', side_effect=request), \
             patch.dict(os.environ, {'CALDAV_USER': 'mail@example.com', 'CALDAV_PASSWORD': 'secret'}):
            preview = calendar.preview_calendar_event('Проверка', '2026-09-25T10:00:00+03:00',
                                                      '2026-09-25T10:30:00+03:00')
            self.assertEqual(preview['status'], 'preview_only')
            self.assertEqual(state['puts'], 0)
            with self.assertRaises(ValueError):
                calendar.create_calendar_event('Изменено', '2026-09-25T10:00:00+03:00',
                                               '2026-09-25T10:30:00+03:00', preview['approval'])
            first = calendar.create_calendar_event('Проверка', '2026-09-25T10:00:00+03:00',
                                                  '2026-09-25T10:30:00+03:00', preview['approval'])
            second = calendar.create_calendar_event('Проверка', '2026-09-25T10:00:00+03:00',
                                                   '2026-09-25T10:30:00+03:00', preview['approval'])
            self.assertEqual(first['status'], 'created')
            self.assertEqual(second['status'], 'already_exists')
            self.assertEqual(state['puts'], 1)

    def test_timezone_and_calendar_selection_are_required(self):
        with patch.object(calendar, '_calendars', return_value=[
            {'id': 'https://calendar.mail.ru/a/', 'name': 'A'},
            {'id': 'https://calendar.mail.ru/b/', 'name': 'B'}]):
            with self.assertRaises(ValueError):
                calendar.preview_calendar_event('Test', '2026-09-25T10:00:00+03:00',
                                                '2026-09-25T11:00:00+03:00')
            with self.assertRaises(ValueError):
                calendar.preview_calendar_event('Test', '2026-09-25T10:00:00',
                                                '2026-09-25T11:00:00')


    def test_diagnostic_identifies_failed_stage_without_event_content(self):
        with patch.object(calendar, '_propfind', side_effect=RuntimeError('CalDAV PROPFIND HTTP 400 at /')):
            result = calendar.diagnose_calendar()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['checks'][0]['stage'], 'principal')
        self.assertIn('HTTP 400', result['checks'][0]['error'])

    def test_get_has_no_empty_request_body(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b'ok'
        with patch.dict(os.environ, {'CALDAV_USER': 'user@example.com',
                                     'CALDAV_PASSWORD': 'test'}, clear=True), \
             patch.object(calendar.urllib.request, 'urlopen', return_value=Response()) as call:
            calendar._request('GET', 'https://calendar.mail.ru/')
            self.assertIsNone(call.call_args.args[0].data)

    def test_reject_external_url_and_missing_calendar_secret(self):
        with self.assertRaises(ValueError):
            calendar._url('https://example.com/steal')
        with patch.dict(os.environ, {'CALDAV_USER': '', 'CALDAV_PASSWORD': '', 'MAIL_USER': '', 'MAIL_PASSWORD': ''}):
            with self.assertRaises(RuntimeError):
                calendar._credentials()


if __name__ == '__main__':
    unittest.main()
