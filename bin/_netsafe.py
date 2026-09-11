"""SSRF-safe single-hop HTTPS transport, shared by calendars-discover and the
CalDAV path in calendars-sync.

Kept apart from calendars-sync's own ICS-feed fetch() (which stays http(s),
follows redirects itself, and is left untouched by this fork): CalDAV always
speaks HTTPS with a request body and Basic auth, never a bare GET, so it gets
its own transport rather than bending fetch_one_hop's shape to fit both.

The DNS-pinning trick is the same one calendars-sync already used for ICS
feeds: resolve the hostname once, reject anything that isn't a global address
unless CALENDARS_ALLOW_PRIVATE_HOSTS=1 is set, then hand curl the resolved IP
via --resolve so the TLS handshake and the request both go to the address
that was actually checked.
"""
import ipaddress
import os
import re
import socket
import subprocess
import tempfile
import urllib.parse

FETCH_TIMEOUT = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

STATUS_LINE_RE = re.compile(r"^HTTP/\S+\s+(\d\d\d)", re.MULTILINE)


def resolve_public_ip(hostname, port):
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return None, f"DNS resolution failed: {exc}"

    allow_private = os.environ.get("CALENDARS_ALLOW_PRIVATE_HOSTS") == "1"
    for _family, _type, _proto, _name, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if not allow_private and not ip.is_global:
            continue
        return sockaddr[0], None
    return None, "resolves only to a private/internal address"


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def https_request(url, method="GET", headers=None, body=None, auth=None,
                   timeout=FETCH_TIMEOUT, max_bytes=MAX_RESPONSE_BYTES):
    """One HTTPS request. No redirect is ever followed here -- the response
    headers are handed back so the caller can decide whether a 3xx's
    Location is worth one more hop on whatever origin-pinning rule it holds
    every other discovered address to (see _caldav.resolve_discovered_url).

    Credentials go to curl via a config file on stdin (`user = "..."`),
    never argv, the same way calendars-sync already keeps ICS feed URLs out
    of /proc/<pid>/cmdline.

    Returns (status:int|None, text:str, headers_text:str, error:str|None).
    """
    if any(c in url for c in "\r\n\x00"):
        return None, "", "", "malformed URL: contains a control character"
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        return None, "", "", f"malformed URL: {exc}"

    if parsed.scheme.lower() != "https":
        return None, "", "", "CalDAV requires HTTPS"
    hostname = parsed.hostname
    if not hostname:
        return None, "", "", "URL has no host"
    port = parsed.port or 443

    ip, dns_error = resolve_public_ip(hostname, port)
    if dns_error:
        return None, "", "", dns_error

    config_lines = [
        f'url = "{_escape(url)}"',
        'noproxy = "*"',
        f'request = "{method}"',
        'proto = "=https"',
        'proto-redir = "=https"',
    ]
    for name, value in (headers or {}).items():
        if any(c in f"{name}{value}" for c in "\r\n"):
            return None, "", "", "malformed header"
        config_lines.append(f'header = "{_escape(name)}: {_escape(value)}"')
    if body is not None:
        text_body = body if isinstance(body, str) else body.decode("utf-8")
        config_lines.append(f'data = "{_escape(text_body)}"')
    if auth is not None:
        username, password = auth
        config_lines.append(f'user = "{_escape(username)}:{_escape(password)}"')
    config = "\n".join(config_lines) + "\n"

    handle, header_path = tempfile.mkstemp(prefix=".hdr-")
    os.close(handle)
    try:
        result = subprocess.run(
            ["curl", "-s", "-K", "-", "--globoff", "--proto", "=https",
             "--resolve", f"{hostname}:{port}:{ip}",
             "--max-time", str(timeout), "--max-filesize", str(max_bytes),
             "-D", header_path],
            input=config.encode("utf-8"), capture_output=True,
            timeout=timeout + 10,
        )
        with open(header_path, encoding="utf-8", errors="replace") as fh:
            response_headers = fh.read()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", "", f"request failed: {exc}"
    finally:
        try:
            os.unlink(header_path)
        except OSError:
            pass

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        return None, "", response_headers, f"curl exit {result.returncode}{': ' + detail if detail else ''}"

    status_matches = STATUS_LINE_RE.findall(response_headers)
    status = int(status_matches[-1]) if status_matches else None
    return status, result.stdout.decode("utf-8", "replace"), response_headers, None
