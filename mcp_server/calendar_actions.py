"""Read-only Mail Calendar CalDAV access using the standard library."""

import base64
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HOST = "calendar.mail.ru"
DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
NS = {"d": DAV, "c": CAL}
MAX_RESPONSE = 2_000_000


def _credentials():
    user = os.environ.get("CALDAV_USER")
    password = os.environ.get("CALDAV_PASSWORD")
    if not user or not password:
        raise RuntimeError("Calendar credentials are not configured (CALDAV_USER/CALDAV_PASSWORD)")
    return user, password


def _url(href, base=None):
    url = urllib.parse.urljoin(base or f"https://{HOST}/", href)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443) or parsed.username:
        raise ValueError("Unexpected CalDAV URL")
    return url


def _request(method, url, body=b"", depth=None):
    user, password = _credentials()
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
        "Content-Type": "application/xml; charset=utf-8",
        "Accept": "application/xml",
    }
    if depth is not None:
        headers["Depth"] = str(depth)
    for _ in range(4):
        req = urllib.request.Request(_url(url), data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                if response.status not in (200, 207):
                    raise RuntimeError(f"CalDAV returned HTTP {response.status}")
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise RuntimeError("Calendar response is too large")
                return data
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 307, 308) and error.headers.get("Location"):
                url = _url(error.headers["Location"], url)
                continue
            if error.code in (401, 403):
                raise RuntimeError("Calendar credentials or access denied") from None
            raise RuntimeError(f"CalDAV returned HTTP {error.code}") from None
    raise RuntimeError("Too many CalDAV redirects")


def _propfind(url, properties, depth=0):
    props = "".join(f"<d:{p}/>" if ":" not in p else f"<c:{p.split(':', 1)[1]}/>" for p in properties)
    query = f'<d:propfind xmlns:d="DAV:" xmlns:c="{CAL}"><d:prop>{props}</d:prop></d:propfind>'
    return _responses(_request("PROPFIND", url, query.encode(), depth))


def _responses(data):
    root = ET.fromstring(data)
    if root.tag != f"{{{DAV}}}multistatus":
        raise RuntimeError("Unexpected CalDAV response")
    result = []
    for item in root.findall("d:response", NS):
        props = {}
        for status in item.findall("d:propstat", NS):
            if " 200 " not in (status.findtext("d:status", "", NS)):
                continue
            prop = status.find("d:prop", NS)
            if prop is not None:
                for child in prop:
                    props[child.tag] = child
        result.append((item.findtext("d:href", "", NS), props))
    return result


def _nested_href(props, key):
    element = props.get(key)
    if element is not None:
        return element.findtext("d:href", "", NS)
    return ""


def _calendars():
    origin = _url(os.environ.get("CALDAV_URL", f"https://{HOST}/"))
    try:
        principal = _propfind(origin, ["current-user-principal"])
    except RuntimeError as error:
        if origin != f"https://{HOST}/" or "HTTP 404" not in str(error):
            raise
        origin = _url("/.well-known/caldav")
        principal = _propfind(origin, ["current-user-principal"])
    principal_href = next((_nested_href(p, f"{{{DAV}}}current-user-principal") for _, p in principal if p), "")
    if not principal_href:
        raise RuntimeError("CalDAV principal was not found")
    principal_url = _url(principal_href, origin)
    homes = _propfind(principal_url, ["c:calendar-home-set"])
    home_href = next((_nested_href(p, f"{{{CAL}}}calendar-home-set") for _, p in homes if p), "")
    if not home_href:
        raise RuntimeError("CalDAV calendar home was not found")
    home_url = _url(home_href, principal_url)
    collections = _propfind(home_url, ["resourcetype", "displayname"], depth=1)
    calendars = []
    for href, props in collections:
        kind = props.get(f"{{{DAV}}}resourcetype")
        if kind is None or kind.find("c:calendar", NS) is None:
            continue
        url = _url(href, home_url)
        name = props.get(f"{{{DAV}}}displayname")
        calendars.append({"id": url, "name": (name.text or "").strip() if name is not None else ""})
    return calendars


def list_calendars():
    """Return accessible calendar collections; no events are stored locally."""
    return _calendars()


def _bound(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Dates must be YYYY-MM-DD") from None


def _unescape(value):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m[1].lower() == "n" else m[1], value)


def _event_lines(ical):
    lines = []
    for line in re.split(r"\r\n|\n|\r", ical):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            yield key, value


def _time(key, value):
    if "VALUE=DATE" in key or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date().isoformat(), True
    try:
        instant = datetime.strptime(value, "%Y%m%dT%H%M%S" + ("Z" if value.endswith("Z") else ""))
        if value.endswith("Z"):
            return instant.replace(tzinfo=timezone.utc).isoformat(), False
        tzid = re.search(r"(?:^|;)TZID=([^;:]+)", key)
        if tzid:
            instant = instant.replace(tzinfo=ZoneInfo(tzid[1].strip('"')))
        return instant.isoformat(), False
    except (ValueError, ZoneInfoNotFoundError):
        return value[:80], False


def _parse_events(ical, calendar, max_events):
    events = []
    current = None
    for key, value in _event_lines(ical):
        if key.upper() == "BEGIN" and value == "VEVENT":
            current = {"calendar": calendar, "all_day": False}
        elif key.upper() == "END" and value == "VEVENT" and current is not None:
            events.append(current)
            current = None
            if len(events) >= max_events:
                break
        elif current is not None:
            field = key.split(";", 1)[0].upper()
            if field in ("DTSTART", "DTEND"):
                current["start" if field == "DTSTART" else "end"], all_day = _time(key, value)
                current["all_day"] = current["all_day"] or all_day
            elif field in ("SUMMARY", "LOCATION", "DESCRIPTION", "UID", "STATUS", "RRULE", "RECURRENCE-ID"):
                labels = {"SUMMARY": "title", "LOCATION": "location", "DESCRIPTION": "description",
                          "UID": "uid", "STATUS": "status", "RRULE": "recurrence_rule",
                          "RECURRENCE-ID": "recurrence_id"}
                current[labels[field]] = _unescape(value)[:4000]
    return events


def list_events(start_date, end_date, calendar_id="", limit=100):
    """List events in a date interval [start, end); recurring masters are not expanded."""
    start, end = _bound(start_date), _bound(end_date)
    if not start < end <= start + timedelta(days=62):
        raise ValueError("Select a nonempty range of up to 62 days")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be 1-200")
    calendars = _calendars()
    selected = [c for c in calendars if not calendar_id or c["id"] == calendar_id]
    if calendar_id and not selected:
        raise ValueError("Unknown calendar_id: choose one returned by list_mail_calendars")
    zone = ZoneInfo(os.environ.get("CALDAV_TIMEZONE", "Europe/Moscow"))
    start_utc = datetime.combine(start, time.min, zone).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_utc = datetime.combine(end, time.min, zone).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    query = (f'<c:calendar-query xmlns:d="DAV:" xmlns:c="{CAL}"><d:prop><c:calendar-data/></d:prop>'
             f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
             f'<c:time-range start="{start_utc}" end="{end_utc}"/>'
             '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>').encode()
    events = []
    for cal in selected:
        for _, props in _responses(_request("REPORT", cal["id"], query, 1)):
            data = props.get(f"{{{CAL}}}calendar-data")
            if data is not None and data.text:
                events.extend(_parse_events(data.text, cal["name"], limit - len(events)))
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break
    events.sort(key=lambda event: event.get("start", ""))
    return {"events": events, "range_start": start.isoformat(), "range_end_exclusive": end.isoformat(),
            "range_timezone": str(zone),
            "limit_reached": len(events) >= limit, "recurrences_expanded": False}
