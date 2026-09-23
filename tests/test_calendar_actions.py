import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_reject_external_url_and_missing_calendar_secret(self):
        with self.assertRaises(ValueError):
            calendar._url('https://example.com/steal')
        with patch.dict(os.environ, {'CALDAV_USER': '', 'CALDAV_PASSWORD': ''}):
            with self.assertRaises(RuntimeError):
                calendar._credentials()


if __name__ == '__main__':
    unittest.main()
