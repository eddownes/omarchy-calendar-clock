import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).parent / "bin" / "calendars-sync"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_loader(
    "calendars_sync", SourceFileLoader("calendars_sync", str(SCRIPT))
)
CALENDARS_SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CALENDARS_SYNC)


class FeedOperationTest(unittest.TestCase):
    def test_add_feed_reads_private_url_from_stdin(self):
        self.assertNotIn("CALENDARS_FEED_URL", SCRIPT.read_text())

        with tempfile.TemporaryDirectory() as directory:
            feeds = Path(directory) / "feeds.json"
            cache = Path(directory) / "events.json"
            request = {"action": "add", "url": "https://example.test/private-token", "name": "Private"}
            payload = {"ok": True, "configured": True, "feeds": [], "events": []}

            with (
                patch.object(sys, "argv", [str(SCRIPT), "--feeds", str(feeds), "--feed-operation-stdin"]),
                patch.object(sys, "stdin", io.StringIO(json.dumps(request) + "\n")),
                patch.object(sys, "stdout", io.StringIO()),
                patch.object(CALENDARS_SYNC, "sync", return_value=payload),
                patch.object(CALENDARS_SYNC, "result_path", return_value=str(cache)),
            ):
                self.assertEqual(CALENDARS_SYNC.main(), 0)

            saved = json.loads(feeds.read_text())
            self.assertEqual(saved[0]["url"], request["url"])

    def test_feed_storage_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "feeds.json"
            CALENDARS_SYNC.save_feeds(path, [{
                "name": "Private",
                "url": "https://example.test/token",
                "color": "#8b7ff5",
                "enabled": True,
            }])

            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class CaldavFeedTest(unittest.TestCase):
    def test_caldav_feed_round_trips_type_and_username(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.json"
            CALENDARS_SYNC.save_feeds(path, [{
                "name": "Work", "url": "https://dav.example.test/calendars/work/",
                "color": "#8b7ff5", "enabled": True, "type": "caldav", "username": "ed",
            }])
            feeds, error = CALENDARS_SYNC.load_feeds(path)

        self.assertEqual(error, "")
        self.assertEqual(feeds[0]["type"], "caldav")
        self.assertEqual(feeds[0]["username"], "ed")

    def test_ics_feed_omits_type_and_username_on_disk(self):
        # An untouched, ICS-only feeds.json should round-trip byte-for-byte
        # rather than growing a "type": "ics" key on every entry.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.json"
            CALENDARS_SYNC.save_feeds(path, [{
                "name": "Personal", "url": "https://example.test/personal.ics",
                "color": "#8b7ff5", "enabled": True, "type": "ics", "username": "",
            }])
            saved = json.loads(path.read_text())

        self.assertNotIn("type", saved[0])
        self.assertNotIn("username", saved[0])

    def test_add_caldav_stores_password_in_keyring_not_feeds_json(self):
        with tempfile.TemporaryDirectory() as directory:
            feeds = Path(directory) / "feeds.json"
            cache = Path(directory) / "events.json"
            request = {
                "action": "add-caldav", "username": "ed", "password": "hunter2",
                "calendars": [{"url": "https://dav.example.test/calendars/work/", "name": "Work"}],
            }
            payload = {"ok": True, "configured": True, "feeds": [], "events": []}

            with (
                patch.object(sys, "argv", [str(SCRIPT), "--feeds", str(feeds), "--feed-operation-stdin"]),
                patch.object(sys, "stdin", io.StringIO(json.dumps(request) + "\n")),
                patch.object(sys, "stdout", io.StringIO()),
                patch.object(CALENDARS_SYNC, "sync", return_value=payload),
                patch.object(CALENDARS_SYNC, "result_path", return_value=str(cache)),
                patch.object(CALENDARS_SYNC, "store_caldav_password") as store_password,
            ):
                self.assertEqual(CALENDARS_SYNC.main(), 0)

            saved = json.loads(feeds.read_text())
            self.assertEqual(saved[0]["type"], "caldav")
            self.assertNotIn("password", saved[0])
            store_password.assert_called_once_with(
                CALENDARS_SYNC.caldav_feed_id("https://dav.example.test/calendars/work/"), "hunter2")

    def test_add_caldav_keyring_failure_keeps_existing_feeds_visible(self):
        # A bad password (or no secret-tool at all) must not blank the panel's
        # calendar list -- it reports through the payload's top-level error
        # and falls through to a normal sync of whatever is already there.
        with tempfile.TemporaryDirectory() as directory:
            feeds = Path(directory) / "feeds.json"
            cache = Path(directory) / "events.json"
            request = {
                "action": "add-caldav", "username": "ed", "password": "hunter2",
                "calendars": [{"url": "https://dav.example.test/calendars/work/", "name": "Work"}],
            }
            existing_payload = {"ok": True, "configured": True,
                                 "feeds": [{"name": "Personal", "url": "x", "color": "#000"}],
                                 "events": [{"uid": "1"}]}

            with (
                patch.object(sys, "argv", [str(SCRIPT), "--feeds", str(feeds), "--feed-operation-stdin"]),
                patch.object(sys, "stdin", io.StringIO(json.dumps(request) + "\n")),
                patch.object(sys, "stdout", io.StringIO()) as fake_stdout,
                patch.object(CALENDARS_SYNC, "sync", return_value=dict(existing_payload)),
                patch.object(CALENDARS_SYNC, "result_path", return_value=str(cache)),
                patch.object(CALENDARS_SYNC, "store_caldav_password",
                              side_effect=FileNotFoundError("secret-tool")),
            ):
                self.assertEqual(CALENDARS_SYNC.main(), 0)

            output = json.loads(fake_stdout.getvalue())

        self.assertFalse(output["ok"])
        self.assertIn("could not save the password", output["error"])
        self.assertEqual(output["feeds"], existing_payload["feeds"])
        self.assertEqual(output["events"], existing_payload["events"])
        self.assertFalse(feeds.exists())


    def test_caldav_report_events_flow_through_the_ics_expansion_pipeline(self):
        # The multistatus response's per-event blobs get concatenated and
        # handed to the same expand()/parse_calendar_lenient() path a plain
        # ICS feed already uses -- so a CalDAV calendar's events should come
        # out the door identically to one pasted as a feed URL.
        report_xml = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/calendars/ed/work/standup.ics</d:href>
    <d:propstat>
      <d:prop><c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:standup
DTSTART:20260115T090000Z
DTEND:20260115T091500Z
SUMMARY:Standup
END:VEVENT
END:VCALENDAR
</c:calendar-data></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""
        feed = {"name": "Work", "url": "https://dav.example.test/calendars/ed/work/",
                "color": "#8b7ff5", "enabled": True, "type": "caldav", "username": "ed"}

        with (
            patch.object(CALENDARS_SYNC, "lookup_caldav_password", return_value=("hunter2", None)),
            patch("_netsafe.https_request", return_value=(207, report_xml, "", None)) as transport,
        ):
            text, error, from_cache = CALENDARS_SYNC.fetch_caldav(
                feed, datetime(2026, 1, 1), datetime(2026, 2, 1))

        self.assertEqual(error, "")
        self.assertFalse(from_cache)
        transport.assert_called_once()
        self.assertEqual(transport.call_args.kwargs["auth"], ("ed", "hunter2"))
        self.assertEqual(transport.call_args.kwargs["headers"]["Depth"], "1")

        events, name, _truncated, _skipped = CALENDARS_SYNC.expand(
            text, feed, datetime(2026, 1, 1), datetime(2026, 2, 1), ZoneInfo("UTC"))

        self.assertEqual(name, "Work")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Standup")
        self.assertEqual(events[0]["uid"], "standup")


class NetworkSecurityTest(unittest.TestCase):
    def test_loopback_address_is_rejected(self):
        answer = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(CALENDARS_SYNC.socket, "getaddrinfo", return_value=answer):
            address, error = CALENDARS_SYNC.resolve_public_ip("example.test", 443)

        self.assertIsNone(address)
        self.assertEqual(error, "resolves only to a private/internal address")

    def test_redirect_target_is_revalidated(self):
        private = "http://127.0.0.1/calendar"
        with patch.object(
            CALENDARS_SYNC,
            "fetch_one_hop",
            side_effect=[(None, private, None), (None, None, "private address rejected")],
        ) as fetch_one_hop:
            with patch.object(CALENDARS_SYNC, "read_cache", return_value=("", "blocked", False)):
                self.assertEqual(CALENDARS_SYNC.fetch("https://example.test/calendar"), ("", "blocked", False))

        self.assertEqual(fetch_one_hop.call_args_list[1].args[0], private)


class CalendarExpansionTest(unittest.TestCase):
    def test_weekly_recurrence_keeps_local_time_across_dst(self):
        calendar = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:dst-test
DTSTART;TZID=Europe/Monaco:20260323T090000
DTEND;TZID=Europe/Monaco:20260323T100000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:DST test
END:VEVENT
END:VCALENDAR
"""
        timezone = ZoneInfo("Europe/Monaco")
        events, _, _, _ = CALENDARS_SYNC.expand(
            calendar,
            {"name": "Test", "color": "#8b7ff5"},
            datetime(2026, 3, 20, tzinfo=timezone),
            datetime(2026, 4, 10, tzinfo=timezone),
            timezone,
        )

        self.assertEqual(
            [event["start"] for event in events],
            [
                "2026-03-23T09:00:00+01:00",
                "2026-03-30T09:00:00+02:00",
                "2026-04-06T09:00:00+02:00",
            ],
        )


if __name__ == "__main__":
    unittest.main()
