"""CalDAV calendar discovery and event REPORT parsing.

Ported from eddownes/OmaMailCalDav's calendar/Calendar.js discovery chain --
RFC 6764's well-known redirect, RFC 5397's current-user-principal, RFC 4791's
calendar-home-set, then a Depth:1 listing of what's inside it -- reworked
around Python's xml.etree instead of the regex-based XML reader that
project's QML JS runtime was stuck with. ElementTree already resolves
entities and CDATA sections into .text, so none of the manual decodeXml /
decodeXmlText machinery that regex approach needed is required here.

Every discovered address (principal, home-set, each calendar's href, and the
well-known redirect target) is re-pinned to the account URL's own origin
before use, on the same rule OmaMailCalDav's resolveDiscoveredUrl applies: a
compromised or merely misconfigured server must not be able to hand this
account's credentials to a different host than the one the user typed in.
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET

from _netsafe import https_request

DAV_HREF = "href"


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def url_origin(url):
    match = re.match(r"^(https)://([^/?#:]+)(?::(\d+))?", str(url or ""), re.IGNORECASE)
    if not match:
        return ""
    scheme, host, port = match.group(1).lower(), match.group(2).lower(), match.group(3)
    return f"{scheme}://{host}:{port or '443'}"


def _pin_to_origin(account_url, candidate_url):
    origin = url_origin(account_url)
    if not origin or url_origin(candidate_url) != origin:
        return ""
    return candidate_url


def resolve_discovered_url(account_url, href):
    """A PROPFIND/REPORT-discovered href, resolved against account_url and
    accepted only when it lands back on that URL's own origin."""
    href = str(href or "")
    if not href or re.search(r"\s", href):
        return ""
    try:
        joined = urllib.parse.urljoin(str(account_url or ""), href)
    except ValueError:
        return ""
    return _pin_to_origin(account_url, joined)


def discovered_well_known_url(status, headers_text, account_url):
    # RFC 6764: a client that only knows a bare server address tries
    # /.well-known/caldav there first and follows the redirect -- some
    # servers answer a request for the address alone with a plain 404 and
    # only the well-known path names where the real service lives.
    if status is None or status < 300 or status >= 400:
        return ""
    match = re.search(r"^location:\s*(\S+)", headers_text or "", re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    try:
        joined = urllib.parse.urljoin(str(account_url or ""), match.group(1).strip())
    except ValueError:
        return ""
    return _pin_to_origin(account_url, joined)


def _strip_doctype(xml_text):
    # A PROPFIND/REPORT reply is never legitimately DTD-bearing. Stripping any
    # DOCTYPE closes off internal-entity expansion ("billion laughs") against
    # expat, which -- unlike external entities -- it does not refuse by
    # default, without adding a defusedxml dependency for one guard.
    return re.sub(r"<!DOCTYPE[^>]*>", "", xml_text or "", flags=re.IGNORECASE | re.DOTALL)


def _parse(xml_text):
    try:
        return ET.fromstring(_strip_doctype(xml_text).encode("utf-8"))
    except ET.ParseError:
        return None


def _responses(xml_text):
    root = _parse(xml_text)
    if root is None:
        return []
    return [el for el in root.iter() if _local(el.tag) == "response"]


def _first_element(el, local_name):
    for child in el.iter():
        if child is not el and _local(child.tag) == local_name:
            return child
    return None


def _first_text(el, local_name):
    found = _first_element(el, local_name)
    return (found.text or "").strip() if found is not None else ""


def discovered_container_url(xml_text, account_url, container_local_name):
    for response in _responses(xml_text):
        container = _first_element(response, container_local_name)
        if container is None:
            continue
        href = _first_text(container, DAV_HREF)
        if not href:
            continue
        resolved = resolve_discovered_url(account_url, href)
        if resolved:
            return resolved
    return ""


def discovered_principal_url(xml_text, account_url):
    return discovered_container_url(xml_text, account_url, "current-user-principal")


def discovered_homeset_url(xml_text, account_url):
    return discovered_container_url(xml_text, account_url, "calendar-home-set")


def is_calendar_collection(resourcetype_el):
    # A resourcetype naming a real calendar, not the scheduling inbox or
    # outbox CalDAV Scheduling (RFC 6638) adds beside it: both also carry
    # {CALDAV:}calendar, but neither holds events a user meant to add here.
    if resourcetype_el is None:
        return False
    names = {_local(child.tag) for child in resourcetype_el.iter()}
    if "calendar" not in names:
        return False
    return "schedule-inbox" not in names and "schedule-outbox" not in names


def supports_vevent(component_set_el):
    # A collection that declares a component set without VEVENT in it is a
    # task list or a journal, not a calendar this feature reads events from;
    # one that declares no set at all has not said either way, so it's kept.
    if component_set_el is None:
        return True
    comps = [c for c in component_set_el.iter() if _local(c.tag) == "comp"]
    if not comps:
        return True
    return any(c.get("name", "").upper() == "VEVENT" for c in comps)


def discovered_calendar_color(value):
    match = re.match(r"^#([0-9a-fA-F]{6})", str(value or "").strip())
    return ("#" + match.group(1).lower()) if match else ""


def discovered_calendars(xml_text, home_set_url):
    out = []
    for response in _responses(xml_text):
        if not is_calendar_collection(_first_element(response, "resourcetype")):
            continue
        if not supports_vevent(_first_element(response, "supported-calendar-component-set")):
            continue
        url = resolve_discovered_url(home_set_url, _first_text(response, DAV_HREF))
        if not url:
            continue
        out.append({
            "url": url,
            "name": _first_text(response, "displayname") or "Calendar",
            "color": discovered_calendar_color(_first_text(response, "calendar-color")),
        })
    return out


def caldav_event_blobs(xml_text):
    return [text for text in (
        _first_text(response, "calendar-data") for response in _responses(xml_text)
    ) if text]


def propfind_principal_body():
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')


def propfind_homeset_body():
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')


def propfind_collections_body():
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
            'xmlns:ic="http://apple.com/ns/ical/">'
            '<d:prop><d:resourcetype/><d:displayname/><ic:calendar-color/>'
            '<c:supported-calendar-component-set/></d:prop></d:propfind>')


def report_calendar_query_body(window_start, window_end):
    def stamp(value):
        return value.strftime("%Y%m%dT%H%M%SZ")
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{stamp(window_start)}" end="{stamp(window_end)}"/>'
            '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')


def discover_calendars(account_url, username, password, transport=https_request):
    """Finds every calendar collection under one CalDAV account rather than
    asking the user for each calendar's own address by hand. A server that
    doesn't answer one of the first two PROPFIND steps still gets a chance at
    the last one against whatever address was given -- many servers accept a
    home-set or even a calendar collection URL directly."""
    url = str(account_url or "").strip()
    if not re.match(r"^https://", url, re.IGNORECASE):
        return {"ok": False, "error": "Use an HTTPS CalDAV server address", "calendars": []}
    username = str(username or "").strip()
    password = str(password or "")
    if not username:
        return {"ok": False, "error": "Add the account username", "calendars": []}
    if not password:
        return {"ok": False, "error": "Add the account password", "calendars": []}

    auth = (username, password)
    origin = url_origin(url)
    well_known_url = origin + "/.well-known/caldav" if origin else url

    _status, _body, wk_headers, wk_error = transport(
        well_known_url, method="PROPFIND", headers={"Depth": "0"},
        body=propfind_principal_body(), auth=auth)
    principal_request_url = url
    if not wk_error:
        principal_request_url = discovered_well_known_url(_status, wk_headers, url) or url

    status, body, _headers, error = transport(
        principal_request_url, method="PROPFIND", headers={"Depth": "0"},
        body=propfind_principal_body(), auth=auth)
    principal_url = principal_request_url
    if not error and status in (200, 207):
        principal_url = discovered_principal_url(body, principal_request_url) or principal_request_url

    status, body, _headers, error = transport(
        principal_url, method="PROPFIND", headers={"Depth": "0"},
        body=propfind_homeset_body(), auth=auth)
    home_set_url = principal_url
    if not error and status in (200, 207):
        home_set_url = discovered_homeset_url(body, principal_url) or principal_url

    status, body, _headers, error = transport(
        home_set_url, method="PROPFIND", headers={"Depth": "1"},
        body=propfind_collections_body(), auth=auth)
    if error or status not in (200, 207):
        return {"ok": False, "error": "Could not read calendars from that address", "calendars": []}

    calendars = discovered_calendars(body, home_set_url)
    if not calendars:
        return {"ok": False, "error": "No calendars were found at that address", "calendars": []}
    return {"ok": True, "error": "", "calendars": calendars}
