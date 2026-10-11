"""Shared, host-independent HTTP and OGC response handling."""
import gzip
import io
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from email.utils import parsedate_to_datetime

MAX_XML_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
RETRYABLE_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))
SENSITIVE_HEADERS = frozenset(("authorization", "proxy-authorization", "cookie"))
OVERPASS_ENDPOINTS = ("https://overpass-api.de/api/interpreter",
                      "https://overpass.kumi.systems/api/interpreter")


def url_origin(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    scheme = (parsed.scheme or "").lower()
    return scheme, (parsed.hostname or "").lower(), parsed.port or {"http": 80, "https": 443}.get(scheme)


def redirect_headers(old_url, new_url, headers):
    old, new = url_origin(old_url), url_origin(new_url)
    if old[0] == "https" and new[0] != "https":
        raise urllib.error.URLError("uudelleenohjaus HTTPS:stä salaamattomaan osoitteeseen estettiin")
    return {key: value for key, value in (headers or {}).items()
            if old == new or str(key).lower() not in SENSITIVE_HEADERS}


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            safe = redirect_headers(req.full_url, newurl, dict(result.header_items()))
            result.headers = {}
            result.unredirected_hdrs = {}
            for key, value in safe.items():
                result.add_header(key, value)
        return result


def decode_body(data, encoding, limit=MAX_RESPONSE_BYTES):
    encoding = (encoding or "identity").strip().lower()
    if encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(limit + 1)
    elif encoding == "deflate":
        try:
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, limit + 1)
        except zlib.error:
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
            data = decoder.decompress(data, limit + 1)
        if not decoder.eof and len(data) <= limit:
            raise ValueError("Katkennut deflate-vastaus")
    elif encoding not in ("", "identity"):
        raise ValueError("Tuntematon HTTP-pakkaus: " + encoding)
    if len(data) > limit:
        raise ValueError("Palvelun vastaus ylittää kokorajan")
    return data


def parse_xml(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    # Remove NULs for the DTD check to cover UTF-16/32 documents too.
    declarations = data.replace(b"\x00", b"").upper()
    if len(data) > MAX_XML_BYTES or b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("XML-vastaus on liian suuri tai sisältää DTD/ENTITY-määrityksen")
    return ET.fromstring(data)


def ogc_exception(data, max_len=500):
    try:
        root = parse_xml(data)
    except (ValueError, ET.ParseError):
        return ""
    messages = [(element.text or "").strip() for element in root.iter()
                if element.tag.split("}")[-1] in ("ExceptionText", "ServiceException")]
    return "; ".join(message for message in messages if message)[:max_len]


def checked_xml(data):
    root = parse_xml(data)
    error = ogc_exception(data)
    if error:
        raise RuntimeError("OGC-virhe: " + error)
    return root


def retry_delay(attempt, retry_after=None):
    if retry_after:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            try:
                return min(60.0, max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time()))
            except (ValueError, TypeError, OverflowError):
                pass
    return min(8.0, 0.5 * 2 ** attempt)


def request_bytes(request, opener, timeout=45, limit=MAX_RESPONSE_BYTES, checkpoint=None):
    for attempt in range(3):
        if checkpoint:
            checkpoint()
        try:
            request.add_header("Accept-Encoding", "gzip, deflate")
            with opener(request, timeout=timeout) as response:
                data = response.read(limit + 1)
                return decode_body(data, response.headers.get("Content-Encoding"), limit)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUSES or attempt == 2:
                error = ogc_exception(exc.read(MAX_XML_BYTES + 1))
                if error:
                    raise RuntimeError("OGC-virhe: " + error) from None
                raise
            delay = retry_delay(attempt, exc.headers.get("Retry-After"))
            exc.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, EOFError,
                http.client.IncompleteRead, http.client.RemoteDisconnected, http.client.BadStatusLine):
            if attempt == 2:
                raise
            delay = retry_delay(attempt)
        # Small waits let cancellation interrupt a throttled download.
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if checkpoint:
                checkpoint()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    raise RuntimeError("Palvelupyyntö epäonnistui")
