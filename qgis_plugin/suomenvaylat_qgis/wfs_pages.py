"""WFS paging without application objects. Fetching is injected by the host."""
import hashlib
import json
import urllib.parse

try:
    from .service_network import checked_xml, ogc_exception
    from .service_styles import query_url
except ImportError:  # ArcGIS standalone toolbox package
    from service_network import checked_xml, ogc_exception
    from service_styles import query_url


def matched_count(data):
    for name in ("numberMatched", "totalFeatures", "numberOfFeatures"):
        try:
            value = int(data.get(name))
            if value >= 0:
                return value
        except (ValueError, TypeError):
            pass
    return None


def request_parameters(endpoint, params):
    url = query_url(endpoint, **params)
    if len(url) <= 6500:
        return url, None
    # KVP POST preserves credentials already present in the endpoint.
    parsed = urllib.parse.urlsplit(endpoint)
    form = urllib.parse.urlencode(list(params.items())).encode("utf-8")
    return urllib.parse.urlunsplit(parsed), form


def metadata(entry, fetch):
    raw = fetch(query_url(entry["endpoint"], service="WFS", request="GetCapabilities"), None)
    root = checked_xml(raw)
    version = root.get("version", "2.0.0")
    formats = []
    operation = next((node for node in root.iter()
                      if (node.tag.split("}")[-1] == "Operation" and node.get("name") == "GetFeature")
                      or node.tag.split("}")[-1] == "GetFeature"), root)
    for node in operation.iter():
        if node.tag.split("}")[-1] == "Parameter" and node.get("name", "").lower() == "outputformat":
            formats.extend((child.text or "").strip() for child in node.iter()
                           if child.tag.split("}")[-1] == "Value")
        if node.tag.split("}")[-1] == "ResultFormat":
            formats.extend(child.tag.split("}")[-1] for child in node)
    # SYKE advertises application/geo+json first, but rejects it for GetFeature
    # on e.g. inspire_ge. Prefer the working canonical application/json format.
    output = next((value for preferred in ("application/json", "json", "application/geo+json")
                   for value in formats if value.lower() == preferred), None)
    output = output or next((value for value in formats if "json" in value.lower()), None)
    schema_url = query_url(entry["endpoint"], service="WFS", version=version,
                           request="DescribeFeatureType", typeName=entry["id"])
    schema = checked_xml(fetch(schema_url, None))
    geometry_field, sort_field = None, None
    for element in schema.iter():
        name, kind = element.get("name"), element.get("type", "")
        if name and kind.split(":", 1)[0].lower().startswith("gml") and any(
                value in kind for value in ("Geometry", "Point", "Line", "Curve", "Surface", "Polygon", "Solid")):
            geometry_field = name
        if name and name.casefold() in ("id", "fid", "gid", "objectid"):
            sort_field = name
    return version, output, geometry_field, sort_field


def pages(entry, bbox, cql_wkt, fetch, decode_gml, checkpoint=None):
    version, output, geometry_field, sort_field = metadata(entry, fetch)
    params = {"service": "WFS", "version": version, "request": "GetFeature",
              "typeNames" if version.startswith("2") else "typeName": entry["id"],
              "srsName": "EPSG:3067"}
    if cql_wkt and geometry_field:
        params["CQL_FILTER"] = f"INTERSECTS({geometry_field}, {cql_wkt})"
    else:
        params["bbox"] = bbox + ",EPSG:3067"
    hits = dict(params, resultType="hits")
    expected = None
    # A failed hits request must not turn a successful empty response into data.
    # Some WFS implementations do not support hits. Actual pages still use
    # explicit HTTP reads, so network failure cannot masquerade as EOF.
    import urllib.error
    try:
        url, body = request_parameters(entry["endpoint"], hits)
        expected = matched_count(checked_xml(fetch(url, body)).attrib)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        exc.close()
    if output:
        params["outputFormat"] = output
    if sort_field:
        params["sortBy"] = sort_field + " A"
    params["count" if version.startswith("2") else "maxFeatures"] = 1000
    received, seen_pages = 0, set()
    for _ in range(10000):
        if checkpoint:
            checkpoint()
        params["startIndex"] = received
        url, body = request_parameters(entry["endpoint"], params)
        raw = fetch(url, body)
        if raw.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"{"):
            data = json.loads(raw)
        else:
            error = ogc_exception(raw)
            if error:
                raise RuntimeError("OGC-virhe: " + error)
            root = checked_xml(raw)
            data = decode_gml(raw)
            data.update(root.attrib)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise RuntimeError("WFS ei palauttanut kohdekokoelmaa")
        total = matched_count(data)
        if total is not None:
            if expected is not None and total != expected:
                raise RuntimeError("WFS-aineiston kohdemäärä muuttui latauksen aikana")
            expected = total
        features = data["features"]
        if not features:
            if expected is not None and received != expected:
                raise RuntimeError(f"WFS-lataus katkesi: saatiin {received}/{expected} kohdetta")
            return
        signature = hashlib.sha256(json.dumps(features, sort_keys=True).encode()).digest()
        if signature in seen_pages:
            raise RuntimeError("WFS palautti saman sivun uudelleen; aineistoa ei tallennettu vajaana")
        seen_pages.add(signature)
        received += len(features)
        yield features, expected, received
        if expected is not None and received >= expected:
            if received != expected:
                raise RuntimeError("WFS palautti odotettua enemmän kohteita")
            return
    raise RuntimeError("WFS-sivutus ylitti turvarajan; aineistoa ei tallennettu vajaana")
