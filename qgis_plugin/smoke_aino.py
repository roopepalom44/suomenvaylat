"""Offline regression for Aino's Cloudflare User-Agent requirement."""

import io
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


requests = []


def aino_server(request, timeout):
    """Reject Python's default signature as the real service does."""
    assert isinstance(request, urllib.request.Request)
    assert request.get_header("User-agent") == services.SERVICE_USER_AGENT
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert query["token"] == ["test-token"]
    requests.append(request)
    if query.get("REQUEST") == ["GetCapabilities"]:
        if query["SERVICE"] == ["WFS"]:
            return Response(b'<WFS_Capabilities><FeatureType><Name>aino:data</Name></FeatureType></WFS_Capabilities>')
        return Response(b'<WMS_Capabilities><Layer><Name>aino:map</Name></Layer></WMS_Capabilities>')
    if query.get("request") == ["GetStyles"]:
        return Response(b'<StyledLayerDescriptor/>')
    return Response(b'{"features": []}')


with patch.object(services._SAFE_URL_OPENER, "open", side_effect=aino_server):
    entries, errors = services.catalog("Aino", "test-token")
    assert not errors and {entry["kind"] for entry in entries} == {"wfs", "aino_wms"}
    assert services._style_fetch("https://aino.sitowise.com/ows?token=test-token&request=GetStyles")
    assert services._request_json("https://aino.sitowise.com/ows?token=test-token") == {"features": []}
assert len(requests) == 4
print("Aino WFS/WMS catalog, styles and JSON identify the plugin", flush=True)

with patch.object(services._SAFE_URL_OPENER, "open", return_value=Response(b"ok")) as opener:
    explicit = urllib.request.Request("https://aino.sitowise.com/ows", headers={"User-Agent": "ExplicitAgent/1.0"})
    services._urlopen(explicit)
    assert explicit.get_header("User-agent") == "ExplicitAgent/1.0"
    other = "https://example.test/ows"
    services._urlopen(other)
    assert opener.call_args.args[0] == other
print("Explicit User-Agent and other services stay unchanged", flush=True)

with patch.object(services._SAFE_URL_OPENER, "open", side_effect=urllib.error.HTTPError(
        "https://aino.sitowise.com/ows?token=test-token", 401, "Unauthorized", {}, None)):
    try:
        services.catalog("Aino", "test-token")
    except RuntimeError as error:
        assert "401" in str(error) and "test-token" not in str(error)
    else:
        raise AssertionError("Invalid token must fail without exposing the credential")
print("Invalid-token errors remain visible and redact the token", flush=True)
