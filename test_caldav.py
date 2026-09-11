import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "bin"))

import _caldav as caldav  # noqa: E402


PRINCIPAL_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/</d:href>
    <d:propstat>
      <d:prop><d:current-user-principal><d:href>/dav/principals/ed/</d:href></d:current-user-principal></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

HOMESET_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/principals/ed/</d:href>
    <d:propstat>
      <d:prop><c:calendar-home-set><d:href>/dav/calendars/ed/</d:href></c:calendar-home-set></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

COLLECTIONS_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:ic="http://apple.com/ns/ical/">
  <d:response>
    <d:href>/dav/calendars/ed/personal/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
        <d:displayname>Personal</d:displayname>
        <ic:calendar-color>#3FB98FFF</ic:calendar-color>
        <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/ed/inbox/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><c:calendar/><c:schedule-inbox/></d:resourcetype>
        <d:displayname>Inbox</d:displayname>
        <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/ed/tasks/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
        <d:displayname>Tasks</d:displayname>
        <c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

REPORT_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/calendars/ed/personal/one.ics</d:href>
    <d:propstat>
      <d:prop><c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART:20260101T090000Z
DTEND:20260101T100000Z
SUMMARY:One
END:VEVENT
END:VCALENDAR
]]></c:calendar-data></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


class OriginPinningTest(unittest.TestCase):
    def test_relative_href_resolves_against_account_url(self):
        resolved = caldav.resolve_discovered_url(
            "https://dav.example.test/base/", "principals/ed/")
        self.assertEqual(resolved, "https://dav.example.test/base/principals/ed/")

    def test_absolute_path_href_resolves_on_same_origin(self):
        resolved = caldav.resolve_discovered_url(
            "https://dav.example.test/base/", "/dav/calendars/ed/")
        self.assertEqual(resolved, "https://dav.example.test/dav/calendars/ed/")

    def test_cross_origin_href_is_rejected(self):
        # A compromised or misconfigured server must not be able to hand this
        # account's credentials to a different host than the one the user
        # actually typed in.
        resolved = caldav.resolve_discovered_url(
            "https://dav.example.test/base/", "https://evil.test/steal-me/")
        self.assertEqual(resolved, "")

    def test_well_known_redirect_to_other_origin_is_rejected(self):
        headers = "HTTP/1.1 301 Moved\r\nLocation: https://evil.test/dav/\r\n"
        resolved = caldav.discovered_well_known_url(301, headers, "https://dav.example.test/")
        self.assertEqual(resolved, "")

    def test_well_known_redirect_to_same_origin_is_followed(self):
        headers = "HTTP/1.1 301 Moved\r\nLocation: /dav/\r\n"
        resolved = caldav.discovered_well_known_url(301, headers, "https://dav.example.test/")
        self.assertEqual(resolved, "https://dav.example.test/dav/")

    def test_non_redirect_status_yields_no_well_known_url(self):
        self.assertEqual(
            caldav.discovered_well_known_url(404, "HTTP/1.1 404 Not Found\r\n",
                                              "https://dav.example.test/"), "")


class DiscoveryParsingTest(unittest.TestCase):
    def test_discovers_principal_url(self):
        self.assertEqual(
            caldav.discovered_principal_url(PRINCIPAL_XML, "https://dav.example.test/dav/"),
            "https://dav.example.test/dav/principals/ed/")

    def test_discovers_homeset_url(self):
        self.assertEqual(
            caldav.discovered_homeset_url(HOMESET_XML, "https://dav.example.test/dav/principals/ed/"),
            "https://dav.example.test/dav/calendars/ed/")

    def test_collections_excludes_schedule_inbox_and_task_lists(self):
        calendars = caldav.discovered_calendars(
            COLLECTIONS_XML, "https://dav.example.test/dav/calendars/ed/")
        self.assertEqual(len(calendars), 1)
        self.assertEqual(calendars[0]["name"], "Personal")
        self.assertEqual(calendars[0]["url"],
                          "https://dav.example.test/dav/calendars/ed/personal/")
        self.assertEqual(calendars[0]["color"], "#3fb98f")

    def test_calendar_data_survives_cdata_wrapping(self):
        blobs = caldav.caldav_event_blobs(REPORT_XML)
        self.assertEqual(len(blobs), 1)
        self.assertIn("BEGIN:VEVENT", blobs[0])
        self.assertIn("UID:one", blobs[0])


class DiscoverCalendarsOrchestrationTest(unittest.TestCase):
    def test_full_chain_with_working_server(self):
        calls = []

        def fake_transport(url, method="GET", headers=None, body=None, auth=None):
            calls.append((url, method))
            if url.endswith("/.well-known/caldav"):
                return 404, "", "HTTP/1.1 404 Not Found\r\n", None
            if method == "PROPFIND" and "current-user-principal" in (body or ""):
                return 207, PRINCIPAL_XML, "HTTP/1.1 207 Multi-Status\r\n", None
            if method == "PROPFIND" and "calendar-home-set" in (body or ""):
                return 207, HOMESET_XML, "HTTP/1.1 207 Multi-Status\r\n", None
            if method == "PROPFIND" and "resourcetype" in (body or ""):
                return 207, COLLECTIONS_XML, "HTTP/1.1 207 Multi-Status\r\n", None
            return 500, "", "HTTP/1.1 500\r\n", None

        result = caldav.discover_calendars(
            "https://dav.example.test/dav/", "ed", "hunter2", transport=fake_transport)

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["calendars"]), 1)
        self.assertEqual(result["calendars"][0]["url"],
                          "https://dav.example.test/dav/calendars/ed/personal/")

    def test_rejects_non_https_url(self):
        result = caldav.discover_calendars("http://dav.example.test/", "ed", "pw")
        self.assertFalse(result["ok"])
        self.assertIn("HTTPS", result["error"])

    def test_requires_credentials(self):
        result = caldav.discover_calendars("https://dav.example.test/", "", "")
        self.assertFalse(result["ok"])

    def test_no_calendars_found_is_reported(self):
        def empty_transport(url, method="GET", headers=None, body=None, auth=None):
            return 207, "<d:multistatus xmlns:d=\"DAV:\"></d:multistatus>", "HTTP/1.1 207\r\n", None

        result = caldav.discover_calendars(
            "https://dav.example.test/", "ed", "pw", transport=empty_transport)
        self.assertFalse(result["ok"])
        self.assertIn("No calendars", result["error"])


if __name__ == "__main__":
    unittest.main()
