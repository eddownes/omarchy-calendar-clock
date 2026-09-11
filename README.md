# Calendar Clock for Omarchy (CalDAV fork)

The Omarchy bar clock, upgraded: date and time on the bar, and a popup with
a month calendar, year/life progress bars, and your upcoming events from
any iCalendar (.ics) feed or CalDAV account.

This exists because I liked the design of Omarchy's default clock and its
calendar popup — the hero date, the quiet month grid, the progress rails —
and wanted that exact look to also show my real events. So this is the
stock design, kept as-is, wired to live calendars.

This fork of [matteodevenuto/omarchy-calendar-clock](https://github.com/matteodevenuto/omarchy-calendar-clock)
adds a **"Discover calendars…"** flow: give it a CalDAV server address,
username and password, and it walks the standard discovery chain (RFC 6764
well-known redirect, RFC 5397 principal, RFC 4791 calendar-home-set, then a
listing of what's inside it) to find every calendar on that account, so you
don't have to dig a CalDAV collection URL out of your provider's settings by
hand. The discovery logic is ported from
[eddownes/OmaMailCalDav](https://github.com/eddownes/OmaMailCalDav)'s
`calendar/Calendar.js`, reworked in Python around `xml.etree` instead of that
project's regex-based XML reader.

![Preview](preview.png)

## Features

- **Bar** — date/time label (formats configurable), vertical-bar mode for
  thin setups
- **Popup**
  - Big hero date; click it or the month header to jump back to today
  - Month grid with ISO week numbers and event dots per day
  - Year progress ("2026 · 64%") and life progress (editable birth year /
    expectancy, double-click the year row)
  - Upcoming list and selected-day events from your calendars
- **Calendars** — paste any shared iCalendar link (Proton, Google,
  Nextcloud, Fastmail, self-hosted…), or discover a whole CalDAV account's
  calendars at once. Reorder feeds with the arrows, recolor them by
  clicking a feed's dot. Recurring events expand correctly across DST
  changes; one broken event in a feed can't sink the rest.
- **CalDAV discovery** — click "Discover calendars…", enter a server
  address, username and password, and pick which of the account's
  calendars to add. Every discovered address is checked against the
  server's own origin before use, so a compromised or misconfigured server
  answer can't redirect your credentials elsewhere.
- **Private by default** — feed URLs and CalDAV requests never appear in
  process arguments or logs; feeds and caches are owner-only. CalDAV
  passwords are never written to disk — they're stored in the system
  keyring (via `secret-tool`).

## Requirements

```bash
sudo pacman -S --needed python-icalendar python-recurring-ical-events libsecret
```

`libsecret` (providing `secret-tool`) is only needed for CalDAV discovery —
it's where discovered calendars' passwords are stored. Plain iCalendar feeds
work without it, exactly as before.

## Install

```bash
omarchy plugin add https://github.com/eddownes/omarchy-calendar-clock --enable
```

It replaces the stock clock widget in place. Click the clock to open the
panel; Escape closes it.

## Keyboard

| Key | Action |
|---|---|
| `←`/`→` | Previous / next month |
| `↑`/`↓` | Previous / next year |
| `t` / `enter` | Back to today |
| `s` | Show/hide calendars |
| `u` | Toggle upcoming list |
| `w` | Toggle week start |
| `esc` | Close |

## Settings

| Key | Type | Default | Meaning |
|---|---|---|---|
| `format` | string | locale | Bar date/time format (Qt format string) |
| `weekStartDay` | string | locale | First day of week |
| `birthYear` | integer | — | Life-progress start year |
| `lifeExpectancy` | integer | 90 | Life-progress span |
| `refreshIntervalSec` | integer | 900 | Calendar feed poll interval |
| `feedsFile` | path | — | Defaults to `~/.config/omarchy/calendars/feeds.json` |

## Credits

Built on [matteodevenuto/omarchy-calendar-clock](https://github.com/matteodevenuto/omarchy-calendar-clock)
(MIT), which is itself built on the stock [Omarchy](https://omarchy.org/)
clock plugin (MIT, by the Omarchy authors) and
[Proton Calendar for Omarchy](https://github.com/itsmoorgrove/omarchy-protoncalendar)
by [itsmoorgrove](https://github.com/itsmoorgrove) (MIT). CalDAV discovery
is ported from [eddownes/OmaMailCalDav](https://github.com/eddownes/OmaMailCalDav).
See [LICENSE](LICENSE).

## Removal

```bash
omarchy plugin remove eddownes.calendar-clock
rm -rf ~/.config/omarchy/calendars ~/.cache/omarchy/calendars
secret-tool clear service omarchy-calendar-clock
```

Removing the clone restores the stock clock.
