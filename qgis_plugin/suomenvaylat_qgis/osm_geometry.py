"""Overpass-elementtien GeoJSON-geometriat ilman QGIS-riippuvuuksia.

Sama logiikka on ArcGIS Pro -työkalussa (``Toolboxes/VaylaWFSDownloader.pyt``).
Muutokset pitää tehdä molempiin; ``tests/test_osm_geometry.py`` ajaa samat
tapaukset kummallekin toteutukselle.
"""

# Suljettu way tulkitaan alueeksi, jos sillä on jokin näistä avaimista eikä
# viiva-avainta. Luettelo noudattaa OSM-wikin "Area"-käytäntöä pääpiirteittäin.
AREA_KEYS = frozenset([
    "aeroway", "amenity", "boundary", "building", "building:part", "craft",
    "historic", "landcover", "landuse", "leisure", "man_made", "military",
    "natural", "office", "place", "power", "public_transport", "shop", "sport",
    "tourism", "water", "waterway", "wetland",
])
# Näillä avaimilla suljettu way on viiva, ellei siinä ole area=yes.
LINEAR_KEYS = frozenset(["highway", "barrier", "railway", "aerialway", "route"])
# Avaimet, joiden osa arvoista on viivoja ja osa alueita.
LINEAR_TAG_VALUES = {
    "waterway": None,  # kaikki paitsi AREA_WATERWAYS
    "power": frozenset(["line", "minor_line", "cable"]),
    "natural": frozenset(["coastline", "tree_row", "cliff", "ridge", "arete"]),
    "man_made": frozenset([
        "embankment", "breakwater", "pipeline", "cutline", "groyne", "dyke",
    ]),
    "leisure": frozenset(["track", "slipway"]),
    "aeroway": frozenset(["runway", "taxiway"]),
}
AREA_WATERWAYS = frozenset(["riverbank", "dock", "boatyard"])


def is_area_way(tags):
    """Palauta True, jos suljettu way kuvaa aluetta eikä viivaa."""
    tags = tags or {}
    area = str(tags.get("area", "")).lower()
    if area == "no":
        return False
    if area == "yes":
        return True
    if any(key in tags for key in LINEAR_KEYS):
        return False
    for key, values in LINEAR_TAG_VALUES.items():
        if key not in tags:
            continue
        value = str(tags.get(key))
        if key == "waterway":
            if value not in AREA_WATERWAYS:
                return False
        elif value in values:
            return False
    return any(key in tags for key in AREA_KEYS)


def _coords(points):
    out = []
    for point in points or []:
        if isinstance(point, dict) and point.get("lon") is not None and point.get("lat") is not None:
            out.append((point["lon"], point["lat"]))
    return out


def assemble_rings(segments):
    """Liitä relaation wayt päistään yhteen. Palauta (suljetut renkaat, avoimet osat)."""
    pending = [list(segment) for segment in segments if len(segment) >= 2]
    rings = []
    open_parts = []
    while pending:
        ring = pending.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for index, segment in enumerate(pending):
                if segment[0] == ring[-1]:
                    ring.extend(segment[1:])
                elif segment[-1] == ring[-1]:
                    ring.extend(reversed(segment[:-1]))
                elif segment[-1] == ring[0]:
                    ring[:0] = segment[:-1]
                elif segment[0] == ring[0]:
                    ring[:0] = list(reversed(segment[1:]))
                else:
                    continue
                pending.pop(index)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
        else:
            open_parts.append(ring)
    return rings, open_parts


def signed_area(ring):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


def point_in_ring(point, ring):
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (y - y1) * (x2 - x1) / float(y2 - y1)
            if x < cross_x:
                inside = not inside
    return inside


def _oriented(ring, counter_clockwise):
    ring = list(ring)
    if (signed_area(ring) > 0) != counter_clockwise:
        ring.reverse()
    return [list(point) for point in ring]


def relation_geometry(element):
    """Muodosta multipolygon-/boundary-relaatiosta GeoJSON-geometria.

    Overpassin ``out geom`` antaa relaatiolle jäsenten geometriat, ei
    ylätason geometriaa. Ulko- ja sisärenkaat kootaan jäsenwayden päistä.
    Jos suljettuja ulkorenkaita ei synny, jäsenet palautetaan viivoina, jotta
    aineisto ei katoa hiljaa.
    """
    outer_segments = []
    inner_segments = []
    for member in element.get("members") or []:
        if not isinstance(member, dict) or member.get("type") != "way":
            continue
        coords = _coords(member.get("geometry"))
        if len(coords) < 2:
            continue
        if str(member.get("role") or "outer").lower() == "inner":
            inner_segments.append(coords)
        else:
            outer_segments.append(coords)
    outer_rings, outer_open = assemble_rings(outer_segments)
    inner_rings, _ = assemble_rings(inner_segments)
    if outer_rings:
        polygons = [[_oriented(ring, True)] for ring in outer_rings]
        for inner in inner_rings:
            probe = inner[0]
            for polygon, outer in zip(polygons, outer_rings):
                if point_in_ring(probe, outer):
                    polygon.append(_oriented(inner, False))
                    break
        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        return {"type": "MultiPolygon", "coordinates": polygons}
    lines = [[list(point) for point in part] for part in outer_open + inner_segments if len(part) >= 2]
    if not lines:
        return None
    if len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    return {"type": "MultiLineString", "coordinates": lines}


def element_geometry(element, poi=False):
    """Palauta Overpass-elementin GeoJSON-geometria tai None."""
    if poi:
        point = element if element.get("type") == "node" else element.get("center") or {}
        if point.get("lon") is not None and point.get("lat") is not None:
            return {"type": "Point", "coordinates": [point["lon"], point["lat"]]}
        return None
    element_type = element.get("type")
    if element_type == "node":
        if element.get("lon") is None or element.get("lat") is None:
            return None
        return {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
    if element_type == "relation":
        return relation_geometry(element)
    coords = _coords(element.get("geometry"))
    if len(coords) < 2:
        return None
    if len(coords) >= 4 and coords[0] == coords[-1] and is_area_way(element.get("tags")):
        return {"type": "Polygon", "coordinates": [_oriented(coords, True)]}
    return {"type": "LineString", "coordinates": [list(point) for point in coords]}
