"""Provider SLD discovery and portable conversion. No QGIS/arcpy dependency.

This file is also packaged beside the ArcGIS Python toolbox. Network access is
injected by the host so its authentication and redirect policy remain in use.
"""
import copy
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.parse
import xml.etree.ElementTree as ET

SLD = "http://www.opengis.net/sld"
OGC = "http://www.opengis.net/ogc"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("sld", SLD)
ET.register_namespace("ogc", OGC)
ET.register_namespace("xlink", XLINK)
MAX_STYLE_BYTES = 8 * 1024 * 1024
TRAFICOM_WMS = tuple("https://julkinen.traficom.fi/inspirepalvelu/" + part + "/wms"
                     for part in ("avoin", "rajoitettu", "ilmaliikenne"))


class StyleError(ValueError):
    pass


def local(element):
    return element.tag.split("}")[-1]


def children(element, name):
    return [item for item in element if local(item) == name] if element is not None else []


def child(element, name):
    return next(iter(children(element, name)), None)


def value(element, name, default=""):
    found = child(element, name)
    return (found.text or "").strip() if found is not None else default


def query_url(endpoint, **params):
    parsed = urllib.parse.urlsplit(endpoint)
    # Preserve authentication parameters, replace protocol request parameters.
    replacing = {key.lower() for key in params}
    items = [(key, val) for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in replacing]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(items + list(params.items()))))


def style_endpoints(entry):
    """Only WFS collections have a corresponding downloadable SLD.

    OGC Features and MVT have different schemas: never apply an unrelated WMS
    style merely because a collection has a similar title.
    """
    if entry.get("kind") not in ("wfs", "oskari_wfs"):
        return []
    if entry.get("kind") == "oskari_wfs":
        return list(TRAFICOM_WMS)
    endpoint = entry.get("endpoint") or ""
    if not endpoint:
        return []
    parsed = urllib.parse.urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/wfs"):
        path = path[:-4] + "/wms"
    # Several MML INSPIRE endpoints only expose WFS. Do not request their WFS
    # capabilities as if they were a WMS service.
    if parsed.hostname == "inspire-wfs.maanmittauslaitos.fi" and not path.endswith("/ows"):
        return []
    return [urllib.parse.urlunsplit(parsed._replace(path=path))]


def parse_xml(data):
    if len(data) > MAX_STYLE_BYTES or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise StyleError("Tyylivastaus on liian suuri tai sisältää DTD-määrityksen")
    root = ET.fromstring(data)
    if local(root) != "StyledLayerDescriptor":
        raise StyleError("Palvelu ei palauttanut SLD-tyyliä")
    return root


def select_style(data, layer_id, style_name=""):
    root = parse_xml(data)
    layers = children(root, "NamedLayer")
    exact = [item for item in layers if value(item, "Name") == layer_id]
    matches = exact or [item for item in layers if value(item, "Name").split(":")[-1] == layer_id.split(":")[-1]]
    if len(matches) != 1:
        raise StyleError("SLD ei sisällä yksiselitteisesti valitun tason tyyliä")
    styles = children(matches[0], "UserStyle")
    if style_name:
        styles = [item for item in styles if value(item, "Name") == style_name
                  or value(item, "Name").split(":")[-1] == style_name.split(":")[-1]]
    else:
        defaults = [item for item in styles if value(item, "IsDefault").lower() in ("1", "true")]
        styles = defaults or styles[:1]
    if not styles or not any(local(item) == "Rule" for item in styles[0].iter()):
        raise StyleError("Palvelulla ei ole ladattavaa symboliikkaa tälle tasolle")
    # GetStyles can return every alternative style, including GeoServer generic.
    # Keep precisely the chosen/default UserStyle so QGIS cannot select another.
    selected = ET.Element("{" + SLD + "}StyledLayerDescriptor", {"version": root.get("version", "1.0.0")})
    layer = ET.SubElement(selected, "{" + SLD + "}NamedLayer")
    ET.SubElement(layer, "{" + SLD + "}Name").text = layer_id
    layer.append(copy.deepcopy(styles[0]))
    return selected


class StyleClient:
    """Small per-run cache; no credentials or capability payloads on disk."""
    def __init__(self, fetch):
        self.fetch = fetch
        self.cache = {}
        self.catalogs = {}

    def _wms_layer_name(self, endpoint, layer_id):
        if endpoint not in self.catalogs:
            try:
                data = self.fetch(query_url(endpoint, SERVICE="WMS", REQUEST="GetCapabilities", VERSION="1.3.0"))
                if len(data) > MAX_STYLE_BYTES:
                    raise StyleError("Tasoluettelo on liian suuri")
                root = ET.fromstring(data)
                names = {value(item, "Name") for item in root.iter() if local(item) == "Layer" and value(item, "Name")}
                self.catalogs[endpoint] = names
            except Exception:
                self.catalogs[endpoint] = set()
        names = self.catalogs[endpoint]
        if layer_id in names:
            return layer_id
        if urllib.parse.urlsplit(endpoint).hostname == "inspire-wfs.maanmittauslaitos.fi" and ":" in layer_id:
            prefix, name = layer_id.split(":", 1)
            inspire_name = prefix.upper() + "." + name
            if inspire_name in names:
                return inspire_name
        # A workspace-scoped WMS may advertise an unqualified name while WFS
        # advertises workspace:name (notably DigiRoad and Liiteri). Only accept
        # a unique advertised name; never guess a similarly titled collection.
        matches = [name for name in names if name.split(":")[-1] == layer_id.split(":")[-1]
                   and (":" not in name or ":" not in layer_id)]
        return matches[0] if len(matches) == 1 else None

    def get(self, entry):
        layer_id = str(entry.get("layer_name") or entry.get("id") or "")
        endpoints = style_endpoints(entry)
        if not endpoints:
            return None
        for endpoint in endpoints:
            url = query_url(endpoint, SERVICE="WMS", REQUEST="GetStyles", VERSION="1.1.1", LAYERS=layer_id)
            key = (url, str(entry.get("style") or ""))
            if key not in self.cache:
                try:
                    self.cache[key] = select_style(self.fetch(url), layer_id, entry.get("style") or "")
                except Exception:
                    # Never include an authenticated URL or raw service exception.
                    self.cache[key] = None
            if self.cache[key] is not None:
                return copy.deepcopy(self.cache[key]), endpoint
            alternate = self._wms_layer_name(endpoint, layer_id)
            if alternate and alternate != layer_id:
                alternate_url = query_url(endpoint, SERVICE="WMS", REQUEST="GetStyles", VERSION="1.1.1", LAYERS=alternate)
                if (alternate_url, key[1]) not in self.cache:
                    try:
                        self.cache[alternate_url, key[1]] = select_style(self.fetch(alternate_url), alternate, entry.get("style") or "")
                    except Exception:
                        self.cache[alternate_url, key[1]] = None
                if self.cache[alternate_url, key[1]] is not None:
                    return copy.deepcopy(self.cache[alternate_url, key[1]]), endpoint
        raise StyleError("Rajapinnan symboliikkaa ei saatu ladattua; käytetään oletussymbolia")


def save_sld(root, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = copy.deepcopy(root)
    for element in clean.iter():
        for attr in list(element.attrib):
            if attr.endswith("}href"):
                url = urllib.parse.urlsplit(element.get(attr))
                if url.scheme in ("http", "https"):
                    pairs = [(key, val) for key, val in urllib.parse.parse_qsl(url.query)
                             if key.lower() not in ("token", "api-key", "api_key", "apikey", "password", "access_token")]
                    host = url.netloc.rsplit("@", 1)[-1]
                    element.set(attr, urllib.parse.urlunsplit(url._replace(netloc=host, query=urllib.parse.urlencode(pairs))))
    ET.ElementTree(clean).write(str(path), encoding="utf-8", xml_declaration=True)
    return str(path)


def localize_graphics(root, endpoint, path, fetch):
    """Keep icons with the output; drop credentials from persisted SLD links."""
    path = Path(path)
    assets = path.with_suffix("").with_name(path.stem + "_symbols")
    for element in root.iter():
        if local(element) != "OnlineResource":
            continue
        href = element.get("{" + XLINK + "}href", "")
        url = urllib.parse.urljoin(endpoint, href)
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise StyleError("Symbolin verkko-osoitetta ei tueta")
        content = fetch(url)
        if len(content) > MAX_STYLE_BYTES:
            raise StyleError("Symbolitiedosto on liian suuri")
        extension = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        if extension not in (".png", ".svg", ".gif", ".jpg", ".jpeg"):
            extension = ".png"
        assets.mkdir(parents=True, exist_ok=True)
        target = assets / (hashlib.sha256(content).hexdigest()[:20] + extension)
        target.write_bytes(content)
        element.set("{" + XLINK + "}href", str(target.resolve()))


def mml_tile_style(name, matrix="WGS84_Pseudo-Mercator"):
    if name in ("Kiinteistöjaotus", "Kiinteistojaotus"):
        return query_url("https://avoin-karttakuva.maanmittauslaitos.fi/kiinteisto-avoin/styles/v3/kiinteistojaotus_pelkistetty.json",
                         TileMatrixSet=matrix)
    style = "backgroundmap" if name == "Maastokartta" else "taustakartta"
    return query_url("https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/stylejson/v21/" + style + ".json",
                     TileMatrixSet=matrix)


def number(text, default=0):
    try:
        result = float(text)
        if not math.isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError):
        if text in (None, ""):
            return default
        raise StyleError("Tyylissä on laskennallinen tai virheellinen numero")


def parameters(element):
    result = {}
    if element is not None:
        for item in element:
            if local(item) in ("CssParameter", "SvgParameter"):
                if list(item):
                    raise StyleError("Laskennallista symbolin ominaisuutta ei tueta ArcGIS-muunnoksessa")
                result[item.get("name")] = (item.text or "").strip()
    return result


def color(text, opacity=1):
    text = text or "#808080"
    names = {"black": "#000000", "white": "#ffffff", "red": "#ff0000",
             "green": "#008000", "blue": "#0000ff", "gray": "#808080", "yellow": "#ffff00"}
    text = names.get(text.lower(), text)
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        text = "#" + "".join(char * 2 for char in text[1:])
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        raise StyleError("Värimuotoa ei tueta: " + text)
    return {"type": "CIMRGBColor", "values": [int(text[i:i + 2], 16) for i in (1, 3, 5)] + [number(opacity, 1) * 100]}


def stroke(element):
    if element is None:
        return None
    if child(element, "GraphicStroke") is not None or child(element, "GraphicFill") is not None:
        raise StyleError("ArcGIS-muunnos ei tue kuviollista viivaa; alkuperäinen SLD säilytetään")
    params = parameters(element)
    result = {"type": "CIMSolidStroke", "enable": True,
              "color": color(params.get("stroke", "#000000"), params.get("stroke-opacity", 1)),
              "width": number(params.get("stroke-width"), 1) * 0.75,
              "capStyle": {"butt": "Butt", "round": "Round", "square": "Square"}.get(params.get("stroke-linecap"), "Butt"),
              "joinStyle": {"mitre": "Miter", "miter": "Miter", "round": "Round", "bevel": "Bevel"}.get(params.get("stroke-linejoin"), "Miter")}
    dash = params.get("stroke-dasharray")
    if dash:
        result["effects"] = [{"type": "CIMGeometricEffectDashes", "dashTemplate": [number(x) * 0.75 for x in re.split(r"[ ,]+", dash)],
                              "lineDashEnding": "NoConstraint", "controlPointEnding": "NoConstraint"}]
    return result


def fill(element):
    if element is None:
        return None
    if child(element, "GraphicFill") is not None:
        raise StyleError("ArcGIS-muunnos ei tue kuviotäyttöä; alkuperäinen SLD säilytetään")
    params = parameters(element)
    return {"type": "CIMSolidFill", "enable": True,
            "color": color(params.get("fill", "#808080"), params.get("fill-opacity", 1))}


def symbol_layers(element, geometry):
    kind = local(element)
    uom = element.get("uom", "")
    if uom and not uom.endswith("pixel"):
        raise StyleError("ArcGIS-muunnos tukee symbolin pikseliyksiköitä")
    geom = child(element, "Geometry")
    if geom is not None and (len(geom) != 1 or local(geom[0]) != "PropertyName"):
        raise StyleError("ArcGIS-muunnos ei tue symbolin geometriamuunnosta")
    if geom is not None and (geom[0].text or "").split("/")[-1].casefold() not in ("geom", "geometry", "the_geom", "wkb_geometry"):
        raise StyleError("SLD käyttää vaihtoehtoista geometriakenttää, jota paikallinen taso ei sisällä")
    if kind in ("PolygonSymbolizer", "LineSymbolizer"):
        if kind == "PolygonSymbolizer" and geometry != "Polygon":
            return []
        if kind == "LineSymbolizer" and geometry not in ("Polyline", "Polygon"):
            return []
        result = [stroke(child(element, "Stroke"))]
        offset = number(value(element, "PerpendicularOffset")) * 0.75
        if offset and result[0]:
            result[0].setdefault("effects", []).insert(0, {"type": "CIMGeometricEffectOffset", "offset": offset,
                                                           "method": "Mitered", "option": "Accurate"})
        if kind == "PolygonSymbolizer":
            result.append(fill(child(element, "Fill")))
        return [item for item in result if item]
    if kind != "PointSymbolizer":
        return []
    graphic = child(element, "Graphic")
    mark = child(graphic, "Mark")
    placement = {}
    if geometry == "Polyline":
        placement = {"markerPlacement": {"type": "CIMMarkerPlacementOnLine", "relativeTo": "LineMiddle", "angleToLine": False, "placePerPart": False}}
    elif geometry == "Polygon":
        placement = {"markerPlacement": {"type": "CIMMarkerPlacementPolygonCenter", "method": "CenterOfGravity", "placePerPart": False}}
    displacement = child(graphic, "Displacement")
    base = {"enable": True, "size": number(value(graphic, "Size", "6")) * 0.75,
            "rotation": -number(value(graphic, "Rotation", "0")),
            "offsetX": number(value(displacement, "DisplacementX")) * 0.75,
            "offsetY": number(value(displacement, "DisplacementY")) * 0.75, **placement}
    external = child(graphic, "ExternalGraphic")
    if external is not None:
        resource = child(external, "OnlineResource")
        path = Path(resource.get("{" + XLINK + "}href", "")) if resource is not None else Path("")
        if not path.is_file() or path.suffix.lower() == ".svg":
            raise StyleError("ArcGIS-muunnos tarvitsee paikallisen PNG/JPEG/GIF-pistesymbolin")
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(path.suffix.lower(), "image/png")
        return [{"type": "CIMPictureMarker", **base, "scaleX": 1,
                 "tintColor": color("#ffffff", value(graphic, "Opacity", "1")),
                 "uRL": "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii")}]
    shape = value(mark, "WellKnownName", "square").lower()
    polygons = {
        "square": [[-5,-5],[-5,5],[5,5],[5,-5],[-5,-5]],
        "triangle": [[-5,-4],[0,5],[5,-4],[-5,-4]],
        "diamond": [[0,5],[5,0],[0,-5],[-5,0],[0,5]],
        "cross": [[-5,-1],[-1,-1],[-1,-5],[1,-5],[1,-1],[5,-1],[5,1],[1,1],[1,5],[-1,5],[-1,1],[-5,1],[-5,-1]],
    }
    if shape == "circle":
        points = [[5 * math.cos(i * math.pi / 24), 5 * math.sin(i * math.pi / 24)] for i in range(49)]
    elif shape == "star":
        points = [[(5 if i % 2 == 0 else 2) * math.cos(math.pi / 2 + i * math.pi / 5),
                   (5 if i % 2 == 0 else 2) * math.sin(math.pi / 2 + i * math.pi / 5)] for i in range(11)]
    elif shape in polygons or shape == "x":
        points = polygons["cross" if shape == "x" else shape]
    else:
        raise StyleError("ArcGIS-muunnos ei tue pistesymbolia: " + shape)
    layers = [item for item in (stroke(child(mark, "Stroke")), fill(child(mark, "Fill"))) if item]
    size = number(value(graphic, "Size", "6")) * 0.75
    # Marker strokes use frame units; scaleToFit scales the 10-unit frame.
    for item in layers:
        if item["type"] == "CIMSolidStroke" and size:
            item["width"] *= 10 / size
    opacity = number(value(graphic, "Opacity", "1"))
    for item in layers:
        item["color"]["values"][3] *= opacity
    return [{"type": "CIMVectorMarker", **base,
             "rotation": base["rotation"] + (45 if shape == "x" else 0),
             "frame": {"xmin": -5, "ymin": -5, "xmax": 5, "ymax": 5}, "scaleSymbolsProportionally": True,
             "respectFrame": True,
             "markerGraphics": [{"type": "CIMMarkerGraphic", "geometry": {"rings": [points]},
                                 "symbol": {"type": "CIMPolygonSymbol", "symbolLayers": layers}}]}]


def field_name(text):
    return text.split("/")[-1].split(":")[-1]


def predicate(element):
    """Compile the OGC filter to a JSON tree; fail closed on extensions."""
    if element is None:
        return ["true"]
    kind = local(element)
    if kind == "Filter":
        if len(element) != 1:
            raise StyleError("Virheellinen OGC-suodatin")
        return predicate(element[0])
    if kind in ("And", "Or", "Not"):
        return [kind] + [predicate(item) for item in element]
    if kind in ("PropertyIsEqualTo", "PropertyIsNotEqualTo", "PropertyIsGreaterThan", "PropertyIsGreaterThanOrEqualTo",
                "PropertyIsLessThan", "PropertyIsLessThanOrEqualTo", "PropertyIsNull"):
        prop = child(element, "PropertyName")
        if prop is None:
            raise StyleError("ArcGIS-muunnos ei tue laskennallista suodatinta")
        return [kind, field_name(prop.text or ""), value(element, "Literal"), element.get("matchCase", "true")]
    if kind == "PropertyIsBetween":
        prop = value(element, "PropertyName")
        return ["And", ["PropertyIsGreaterThanOrEqualTo", field_name(prop), value(child(element, "LowerBoundary"), "Literal"), "true"],
                ["PropertyIsLessThanOrEqualTo", field_name(prop), value(child(element, "UpperBoundary"), "Literal"), "true"]]
    raise StyleError("ArcGIS-muunnos ei tue OGC-suodatinta: " + kind)


def filter_fields(tree):
    if tree[0] in ("And", "Or", "Not"):
        return set().union(*(filter_fields(item) for item in tree[1:]))
    return {tree[1]} if tree[0].startswith("Property") else set()


def compare_value(raw, literal):
    if isinstance(raw, (int, float)):
        return raw, number(literal)
    return str(raw), literal


def matches(tree, row):
    kind = tree[0]
    if kind == "true":
        return True
    if kind == "And":
        return all(matches(item, row) for item in tree[1:])
    if kind == "Or":
        return any(matches(item, row) for item in tree[1:])
    if kind == "Not":
        return not matches(tree[1], row)
    raw = row.get(tree[1])
    if kind == "PropertyIsNull":
        return raw is None
    if raw is None:
        return False
    a, b = compare_value(raw, tree[2])
    if tree[3].lower() == "false" and isinstance(a, str):
        a, b = a.lower(), b.lower()
    return {"PropertyIsEqualTo": lambda: a == b, "PropertyIsNotEqualTo": lambda: a != b,
            "PropertyIsGreaterThan": lambda: a > b, "PropertyIsGreaterThanOrEqualTo": lambda: a >= b,
            "PropertyIsLessThan": lambda: a < b, "PropertyIsLessThanOrEqualTo": lambda: a <= b}[kind]()


def arcade(tree, fields):
    kind = tree[0]
    if kind == "true":
        return "true"
    if kind in ("And", "Or"):
        return "(" + (" && " if kind == "And" else " || ").join(arcade(item, fields) for item in tree[1:]) + ")"
    if kind == "Not":
        return "!(" + arcade(tree[1], fields) + ")"
    name, field_type = fields[tree[1]]
    lhs = "$feature[" + json.dumps(name, ensure_ascii=False) + "]"
    if kind == "PropertyIsNull":
        return "(" + lhs + " == null)"
    numeric = field_type not in ("String", "Guid", "GUID", "GlobalID")
    rhs = json.dumps(number(tree[2]) if numeric else tree[2], ensure_ascii=False)
    if tree[3].lower() == "false" and not numeric:
        lhs, rhs = "Lower(" + lhs + ")", "Lower(" + rhs + ")"
    operator = {"PropertyIsEqualTo": "==", "PropertyIsNotEqualTo": "!=", "PropertyIsGreaterThan": ">",
                "PropertyIsGreaterThanOrEqualTo": ">=", "PropertyIsLessThan": "<", "PropertyIsLessThanOrEqualTo": "<="}[kind]
    return "(" + lhs + " != null && " + lhs + " " + operator + " " + rhs + ")"


def arc_rules(root, geometry):
    rules, warnings = [], []
    for group, fts in enumerate(item for item in root.iter() if local(item) == "FeatureTypeStyle"):
        for element in children(fts, "Rule"):
            layers = []
            for sym in element:
                if local(sym).endswith("Symbolizer"):
                    if local(sym) == "TextSymbolizer":
                        warnings.append("SLD:n tekstisymbolia ei muunnettu ArcGIS-tekstitykseksi")
                    else:
                        layers = symbol_layers(sym, geometry) + layers
            if not layers:
                continue
            rules.append({"label": value(element, "Title") or value(element, "Name") or "Symboli",
                          "filter": predicate(child(element, "Filter")), "else": child(element, "ElseFilter") is not None,
                          "group": group, "min": number(value(element, "MinScaleDenominator")),
                          "max": number(value(element, "MaxScaleDenominator"), math.inf), "layers": layers})
    if not rules:
        raise StyleError("SLD ei sisällä tämän geometriatyypin tuettua symboliikkaa")
    return rules, sorted(set(warnings))


def matching_rules(rules, row, scale):
    regular = [i for i, rule in enumerate(rules) if not rule["else"] and rule["min"] <= scale < rule["max"] and matches(rule["filter"], row)]
    # ElseFilter is scoped to its FeatureTypeStyle, not the complete SLD.
    groups = {rules[i]["group"] for i in regular}
    return tuple(i for i, rule in enumerate(rules) if i in regular or
                 (rule["else"] and rule["group"] not in groups and rule["min"] <= scale < rule["max"]))


def arc_renderer(rules, geometry, fields, rows):
    """Preserve overlapping rules, scale intervals and compound filters.

    Enumerate combinations occurring in downloaded attribute tuples at each
    scale interval. No attribute is added/changed in the user's dataset.
    """
    bounds = sorted({0.0} | {bound for rule in rules for bound in (rule["min"], rule["max"]) if math.isfinite(bound)})
    scales = [(a + b) / 2 for a, b in zip(bounds, bounds[1:])] + [bounds[-1] + 1]
    combinations = set()
    seen = set()
    field_keys = sorted(fields)
    for row in rows:
        signature = tuple(row.get(name) for name in field_keys)
        if signature in seen:
            continue
        seen.add(signature)
        for scale in scales:
            combinations.add(matching_rules(rules, row, scale))
        if len(combinations) > 512:
            raise StyleError("Tyylissä on yli 512 päällekkäistä symboliyhdistelmää")
    expression = ["var hits = [];"]
    for i, rule in enumerate(rules):
        test = arcade(rule["filter"], fields)
        if rule["min"]:
            test += " && $view.scale >= " + str(rule["min"])
        if math.isfinite(rule["max"]):
            test += " && $view.scale < " + str(rule["max"])
        expression.append("var r{} = {};".format(i, "false" if rule["else"] else test))
    for i, rule in enumerate(rules):
        if rule["else"]:
            others = ["r" + str(j) for j, other in enumerate(rules) if other["group"] == rule["group"] and not other["else"]]
            test = "!(" + (" || ".join(others) or "false") + ")"
            if rule["min"]:
                test += " && $view.scale >= " + str(rule["min"])
            if math.isfinite(rule["max"]):
                test += " && $view.scale < " + str(rule["max"])
            expression.append("r{} = {};".format(i, test))
        expression.append('if (r' + str(i) + ') { Push(hits, "' + str(i) + '"); }')
    expression.append('return Concatenate(hits, ",");')
    classes = []
    symbol_type = {"Point": "CIMPointSymbol", "Multipoint": "CIMPointSymbol", "Polyline": "CIMLineSymbol", "Polygon": "CIMPolygonSymbol"}[geometry]
    for combination in sorted(combinations):
        if not combination:
            continue
        layers = []
        for i in combination:
            layers = rules[i]["layers"] + layers
        classes.append({"type": "CIMUniqueValueClass", "visible": True,
                        "label": " / ".join(rules[i]["label"] for i in combination),
                        "values": [{"type": "CIMUniqueValue", "fieldValues": [",".join(map(str, combination))]}],
                        "symbol": {"type": "CIMSymbolReference", "symbol": {"type": symbol_type, "symbolLayers": layers}}})
    return {"type": "CIMUniqueValueRenderer", "useDefaultSymbol": False,
            "valueExpressionInfo": {"type": "CIMExpressionInfo", "title": "Rajapinnan SLD", "expression": "\n".join(expression), "returnType": "String"},
            "groups": [{"type": "CIMUniqueValueGroup", "heading": "Rajapinnan symboliikka", "classes": classes}]}


def cim_object(arcpy, data):
    """Construct typed CIM objects via the supported arcpy.cim API."""
    if isinstance(data, list):
        return [cim_object(arcpy, item) for item in data]
    if not isinstance(data, dict):
        return data
    if "type" not in data:
        return data
    result = arcpy.cim.CreateCIMObjectFromClassName(data["type"], "V3")
    for key, val in data.items():
        if key != "type":
            setattr(result, key, cim_object(arcpy, val))
    return result
