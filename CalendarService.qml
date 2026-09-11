import QtQuick
import Quickshell
import Quickshell.Io
import "CalendarModel.js" as Model

Item {
  id: root

  property var settings: ({})

  property var events: []
  property var buckets: ({})
  property var feeds: []
  property bool configured: false
  property bool stale: false
  property string error: ""
  property var generatedAt: null
  property bool syncing: syncProcess.running || feedProcess.running
  property bool everLoaded: false

  // CalDAV discovery, ported from eddownes/OmaMailCalDav's calendar/Calendar.js
  // discovery chain: a "Discover calendars..." flow so a user with a CalDAV
  // account does not have to hunt down each calendar's own address by hand.
  // Kept as its own process rather than folded into syncProcess/feedProcess:
  // discovery can fail or be cancelled mid-flow without ever touching
  // feeds.json, and its password never needs to reach any process but this
  // one's stdin.
  property var discoveryResults: []
  property bool discovering: discoverProcess.running
  property string discoveryError: ""

  readonly property string feedError: Model.firstFeedError(feeds)

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 900, 60, 86400)
  readonly property int dayStartHour: intSetting("dayStartHour", 7, 0, 23)
  readonly property int dayEndHour: Math.max(dayStartHour + 1, intSetting("dayEndHour", 22, 1, 24))
  readonly property string feedsFile: stringSetting("feedsFile", "")

  SystemClock {
    id: clock
    precision: SystemClock.Minutes
  }
  readonly property date now: clock.date
  readonly property string todayKey: Model.keyForDate(now)

  readonly property var nextEvent: Model.nextUpcoming(events, now)
  readonly property string nextRelative: Model.relativeLabel(nextEvent, now)
  readonly property var todayEvents: Model.eventsOn(buckets, todayKey)

  function scriptPath(name) {
    return String(Qt.resolvedUrl("bin/" + name)).replace(/^file:\/\//, "")
  }

  function intSetting(name, fallback, min, max) {
    var v = parseInt(settings ? settings[name] : undefined, 10)
    if (isNaN(v)) return fallback
    return Math.max(min, Math.min(max, v))
  }

  function stringSetting(name, fallback) {
    var v = settings ? settings[name] : undefined
    if (v === undefined || v === null) return fallback
    return String(v)
  }

  function syncArgs(extra) {
    var args = ["python3", scriptPath("calendars-sync")]
    if (feedsFile !== "") args = args.concat(["--feeds", feedsFile])
    return extra ? args.concat(extra) : args
  }

  function apply(text) {
    var state = Model.readPayload(text)
    root.events = state.events
    root.buckets = Model.bucketByDay(state.events)
    root.feeds = state.feeds
    root.configured = state.configured
    root.stale = state.stale
    root.error = state.error
    root.generatedAt = state.generatedAt
    root.everLoaded = true
  }

  function refresh() {
    if (syncProcess.running) return
    syncProcess.command = syncArgs(null)
    syncProcess.running = true
  }

  function ensureFresh() {
    if (!everLoaded) { loadCached(); return }
    if (!generatedAt) { refresh(); return }
    var age = (now.getTime() - generatedAt.getTime()) / 1000
    if (age >= refreshIntervalSec) refresh()
  }

  function loadCached() {
    if (cacheProcess.running) return
    cacheProcess.command = syncArgs(["--cached"])
    cacheProcess.running = true
  }

  function addFeed(url, name) {
    var trimmed = String(url || "").replace(/^\s+|\s+$/g, "")
    if (trimmed === "" || feedProcess.running) return
    runFeedOperation({ "action": "add", "url": trimmed, "name": String(name || "") })
  }

  function removeFeed(url) {
    if (feedProcess.running) return
    runFeedOperation({ "action": "remove", "url": String(url) })
  }

  function setFeedColor(url, color) {
    if (feedProcess.running) return
    runFeedOperation({ "action": "color", "url": String(url), "color": String(color) })
  }

  function moveFeed(url, index) {
    if (feedProcess.running) return
    runFeedOperation({ "action": "move", "url": String(url), "index": Number(index) })
  }

  function runFeedOperation(operation) {
    feedProcess.input = JSON.stringify(operation) + "\n"
    feedProcess.command = syncArgs(["--feed-operation-stdin"])
    feedProcess.running = true
  }

  // Finds every calendar collection under one CalDAV account: RFC 6764's
  // well-known redirect, RFC 5397's current-user-principal, RFC 4791's
  // calendar-home-set, then a Depth:1 listing of what's inside it. The
  // credentials go over this process's stdin, never its argv, the same way
  // addFeed's URL already does.
  function discoverCalendars(url, username, password) {
    if (discoverProcess.running) return
    root.discoveryResults = []
    root.discoveryError = ""
    discoverProcess.input = JSON.stringify({
      "url": String(url || ""), "username": String(username || ""), "password": String(password || "")
    }) + "\n"
    discoverProcess.command = ["python3", scriptPath("calendars-discover")]
    discoverProcess.running = true
  }

  // Adds the calendars the caller picked from a discoverCalendars() result.
  // One password: CalDAV discovery only ever runs against one set of
  // credentials, so every calendar it found shares one login, stored in the
  // system keyring by calendars-sync rather than written into feeds.json.
  function addCaldavCalendars(calendars, username, password) {
    if (feedProcess.running) return
    runFeedOperation({
      "action": "add-caldav",
      "username": String(username || ""),
      "password": String(password || ""),
      "calendars": calendars || [],
    })
  }

  Process {
    id: syncProcess
    command: []
    stdout: StdioCollector { id: syncStdout; waitForEnd: true }
    onExited: function (exitCode) {
      if (exitCode === 0) root.apply(String(syncStdout.text || ""))
      else root.error = "sync failed (exit " + exitCode + ")"
    }
  }

  Process {
    id: cacheProcess
    command: []
    stdout: StdioCollector { id: cacheStdout; waitForEnd: true }
    onExited: function (exitCode) {
      if (exitCode === 0) root.apply(String(cacheStdout.text || ""))
      root.everLoaded = true
    }
  }

  Process {
    id: feedProcess
    property string input: ""
    command: []
    stdinEnabled: true
    stdout: StdioCollector { id: feedStdout; waitForEnd: true }
    onStarted: write(input)
    onExited: function (exitCode) {
      input = ""
      if (exitCode === 0) root.apply(String(feedStdout.text || ""))
      else root.error = "calendar update failed (exit " + exitCode + ")"
    }
  }

  Process {
    id: discoverProcess
    property string input: ""
    command: []
    stdinEnabled: true
    stdout: StdioCollector { id: discoverStdout; waitForEnd: true }
    onStarted: { write(input); input = "" }
    onExited: function (exitCode) {
      var payload = null
      try { payload = JSON.parse(String(discoverStdout.text || "")) } catch (e) {}
      if (exitCode === 0 && payload && payload.ok) {
        root.discoveryResults = payload.calendars || []
        root.discoveryError = ""
      } else {
        root.discoveryResults = []
        root.discoveryError = (payload && payload.error) || "Calendar discovery failed"
      }
    }
  }

  Timer {
    id: refreshTimer
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Component.onCompleted: {
    loadCached()
    firstSync.start()
  }

  Timer {
    id: firstSync
    interval: 1200
    repeat: false
    onTriggered: root.refresh()
  }
}
