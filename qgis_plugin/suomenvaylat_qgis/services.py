"""Service catalog and geographic selection for Suomenväylät QGIS."""

import base64
import concurrent.futures
import json
import math
import re
import tempfile
import time
from functools import lru_cache
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsDataSourceUri, QgsFeatureRequest, QgsField, QgsFields, QgsGeometry, QgsJsonUtils,
    QgsProject, QgsRasterLayer, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

WFS_SOURCES = {
    "Väylä": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/ows"],
    "DigiRoad": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/digiroad/ows"],
    "Liiteri": [
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_asuinalueet/wfs",
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_etaisyysvyohykkeet/wfs",
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_taajamat/wfs",
    ],
    "Syke": ["https://paikkatiedot.ymparisto.fi/geoserver/inspire_ps/wfs"],
    "Karttapaikka": [
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/au/ows",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/bu_mtk_point",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/bu_mtk_polygon",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/cp/ows",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/gn",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/hy",
        "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/mu/ows",
    ],
    "Aino": ["https://aino.sitowise.com/ows"],
}
OGC_SOURCES = {
    "MML": "https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/simple-features/v3/",
    "Karttapaikka": "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
}
KAPSI_SERVICES = {
    "Peruskartta": "https://tiles.kartat.kapsi.fi/peruskartta",
    "Taustakartta": "https://tiles.kartat.kapsi.fi/taustakartta",
    "Ortokuva": "https://tiles.kartat.kapsi.fi/ortokuva",
}
OVERPASS_ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
ADMIN_LAYERS = {
    "Koko Suomi": "Valtakunta", "Elinvoimakeskus": "Elinvoimakeskus",
    "Hyvinvointialue": "Hyvinvointialue", "Maakunta": "Maakunta",
    "Kunta/Kaupunki": "Kunta",
}


def _request_json(url, key=""):
    headers = {"Accept": "application/geo+json, application/json"}
    if key:
        headers["Authorization"] = "Basic " + base64.b64encode((key + ":").encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def catalog(source, api_key=""):
    """Fetch live WFS/OGC API catalog. Entries contain source, id and endpoint."""
    entries, errors = [], []
    if source == "Kapsi":
        by_key = {}
        for service, endpoint in KAPSI_SERVICES.items():
            try:
                url = endpoint + "?SERVICE=WMS&REQUEST=GetCapabilities"
                with urllib.request.urlopen(url, timeout=45) as response:
                    root = ET.fromstring(response.read())
                for element in root.iter():
                    if element.tag.split("}")[-1] != "Layer":
                        continue
                    children = {child.tag.split("}")[-1]: (child.text or "").strip() for child in element}
                    name = children.get("Name")
                    if not name:
                        continue
                    record = {"source": source, "kind": "kapsi_wms", "id": name,
                              "title": f"{children.get('Title') or name} — {service}",
                              "endpoint": endpoint,
                              "min_scale": children.get("MinScaleDenominator"),
                              "max_scale": children.get("MaxScaleDenominator")}
                    key = (endpoint, name)
                    previous = by_key.get(key)
                    if previous is None or (record["min_scale"] or record["max_scale"]):
                        by_key[key] = record
            except Exception as exc:
                errors.append(f"{service}: {exc}")
        entries = list(by_key.values())
        if not entries and errors:
            raise RuntimeError("Kapsin tasoluettelon haku epäonnistui: " + "; ".join(errors))
        return entries, errors
    if source == "OpenStreetMap":
        layers = json.loads((Path(__file__).parent / "osm_layers.json").read_text(encoding="utf-8"))
        return [{"source": source, "kind": "osm", "id": item["id"],
                 "title": item["title"], "query": item["query"]} for item in layers], []
    if source == "Aino" and not api_key:
        raise ValueError("Aino-token tarvitaan tasoluettelon hakuun")
    for endpoint in WFS_SOURCES.get(source, []):
        if source == "Aino":
            endpoint = endpoint + "?" + urllib.parse.urlencode({"token": api_key})
        url = endpoint + ("&" if "?" in endpoint else "?") + "SERVICE=WFS&REQUEST=GetCapabilities"
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                root = ET.fromstring(response.read())
            for element in root.iter():
                if element.tag.split("}")[-1] != "FeatureType":
                    continue
                children = {child.tag.split("}")[-1]: (child.text or "").strip() for child in element}
                name = children.get("Name", "")
                if name:
                    entries.append({"source": source, "kind": "wfs", "id": name,
                                    "title": children.get("Title") or name, "endpoint": endpoint})
        except Exception as exc:
            errors.append(f"{source}: {str(exc).replace(api_key, '[PIILOTETTU]') if api_key else exc}")
    if source in OGC_SOURCES and (source != "Karttapaikka" or api_key):
        endpoint = OGC_SOURCES[source]
        try:
            data = _request_json(urllib.parse.urljoin(endpoint, "collections"), api_key)
            for item in data.get("collections", []):
                if item.get("id"):
                    entries.append({"source": source, "kind": "ogc", "id": item["id"],
                                    "title": item.get("title") or item["id"], "endpoint": endpoint})
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    if not entries and errors:
        raise RuntimeError("Tasoluettelon haku epäonnistui: " + "; ".join(errors))
    return entries, errors


def admin_path():
    return Path(__file__).parent / "resources" / "hallinnolliset_aluejaot.gpkg"


def area_choices(area_type):
    name = ADMIN_LAYERS.get(area_type)
    if name is None:
        return []
    layer = QgsVectorLayer(f"{admin_path()}|layername={name}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError("Hallinnollisten alueiden GeoPackage puuttuu tai on virheellinen")
    field = "namefin"
    return sorted({str(feature[field]) for feature in layer.getFeatures() if feature[field]})


def selection_geometry(area_type, names=(), custom_layer=None):
    if area_type == "Oma aineisto":
        layer = custom_layer
    else:
        name = ADMIN_LAYERS.get(area_type)
        if not name:
            raise ValueError(f"Tuntematon aluevalinta: {area_type}")
        layer = QgsVectorLayer(f"{admin_path()}|layername={name}", name, "ogr")
    if layer is None or not layer.isValid():
        raise ValueError("Rajausaineistoa ei voitu avata")
    if layer.geometryType() not in (1, 2):
        raise ValueError("Rajausaineiston tulee olla viiva tai polygon")
    chosen = []
    names = set(names)
    for feature in layer.getFeatures():
        if not names or area_type == "Oma aineisto" or str(feature["namefin"]) in names:
            geom = QgsGeometry(feature.geometry())
            if not geom.isEmpty():
                chosen.append(geom)
    if not chosen:
        raise ValueError("Valitulta alueelta ei löytynyt geometriaa")
    mask = QgsGeometry.unaryUnion(chosen)
    if layer.geometryType() == 1:
        mask = mask.buffer(1, 8)
    return mask, layer.crs()


def _open_remote_layer(entry):
    if entry["kind"] == "wfs":
        uri = QgsDataSourceUri()
        for name, value in {"url": entry["endpoint"], "typename": entry["id"], "version": "auto",
                            "pagingEnabled": "true", "restrictToRequestBBOX": "1",
                            "srsname": "EPSG:3067"}.items():
            uri.setParam(name, value)
        layer = QgsVectorLayer(uri.uri(), entry["title"], "WFS")
    else:
        raise ValueError("OGC API Features ladataan sivutettuna HTTP-rajapinnasta")
    if not layer.isValid():
        raise RuntimeError(f"Palvelutasoa ei voitu avata: {entry['title']}")
    return layer


def download(entry, mask, mask_crs, destination, key="", progress=None):
    """Stream intersecting features into a GeoPackage in EPSG:3067."""
    if entry["kind"] == "kapsi_wms":
        return _download_kapsi(entry, mask, mask_crs, destination, progress)
    if entry["kind"] == "osm":
        return _download_osm(entry, mask, mask_crs, destination, progress)
    if entry["kind"] == "ogc":
        return _download_ogc(entry, mask, mask_crs, destination, key, progress)
    layer = _open_remote_layer(entry)
    project = QgsProject.instance()
    to_source = QgsCoordinateTransform(mask_crs, layer.crs(), project)
    source_mask = QgsGeometry(mask)
    source_mask.transform(to_source)
    request = QgsFeatureRequest().setFilterRect(source_mask.boundingBox())
    target_crs = QgsCoordinateReferenceSystem("EPSG:3067")
    to_target = QgsCoordinateTransform(layer.crs(), target_crs, project)
    target_mask = QgsGeometry(mask)
    target_mask.transform(QgsCoordinateTransform(mask_crs, target_crs, project))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = re.sub(r"[^\w]+", "_", entry["id"].split(":")[-1])[:60] or "data"
    destination = str(destination)
    writer = QgsVectorFileWriter.create(destination, layer.fields(), QgsWkbTypes.Unknown, target_crs,
                                        project.transformContext(), options)
    if writer.hasError() != QgsVectorFileWriter.NoError:
        error = writer.errorMessage()
        del writer
        raise RuntimeError(error)
    count = 0
    try:
        for feature in layer.getFeatures(request):
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty() or not geometry.intersects(source_mask):
                continue
            geometry.transform(to_target)
            if not geometry.intersects(target_mask):
                continue
            output = QgsFeature(layer.fields())
            output.setAttributes(feature.attributes())
            output.setGeometry(geometry)
            if not writer.addFeature(output):
                raise RuntimeError(writer.errorMessage())
            count += 1
            if progress and count % 100 == 0:
                progress(count)
    finally:
        del writer
    if count == 0:
        Path(destination).unlink(missing_ok=True)
        raise RuntimeError("Rajauksesta ei löytynyt kohteita")
    output = QgsVectorLayer(f"{destination}|layername={options.layerName}", entry["title"], "ogr")
    if not output.isValid():
        raise RuntimeError("Tallennettu taso ei avaudu")
    project.addMapLayer(output)
    return count


def _osm_query(entry, bbox):
    if entry["id"] != "osm_poi_points":
        return "[out:json][timeout:120];" + entry["query"].format(bbox=bbox)
    classes = _poi_table()
    keys = sorted({row[0] for row in classes})
    selectors = "".join(f'nwr["{key}"]({bbox});' for key in keys)
    return f"[out:json][timeout:120];({selectors});out center;"


def _overpass_fetch(query):
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                request = urllib.request.Request(endpoint, data=payload,
                                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(request, timeout=160) as response:
                    data = json.load(response)
                if data.get("remark") and "error" in data["remark"].lower():
                    raise RuntimeError(data["remark"])
                return data.get("elements", [])
            except Exception as exc:
                last_error = exc
                time.sleep(attempt + 1)
    raise RuntimeError(f"Overpass-haku epäonnistui: {last_error}")


def _osm_geometry(element, poi=False):
    if poi:
        point = element if element.get("type") == "node" else element.get("center") or {}
        if "lon" in point and "lat" in point:
            return {"type": "Point", "coordinates": [point["lon"], point["lat"]]}
        return None
    if element.get("type") == "node":
        if "lon" in element and "lat" in element:
            return {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
        return None
    points = [(point["lon"], point["lat"]) for point in element.get("geometry", [])
              if "lon" in point and "lat" in point]
    if len(points) < 2:
        return None
    if len(points) >= 4 and points[0] == points[-1]:
        return {"type": "Polygon", "coordinates": [points]}
    return {"type": "LineString", "coordinates": points}


def _poi_classes(tags):
    classes = _poi_table()
    result = []
    for key, value, code, name in classes:
        if str(tags.get(key, "")) == value and (code, name) not in result:
            result.append((code, name))
    return result


@lru_cache(maxsize=1)
def _poi_table():
    return json.loads((Path(__file__).parent / "osm_poi_classes.json").read_text(encoding="utf-8"))


def _download_osm(entry, mask, mask_crs, destination, progress=None):
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    wgs_mask = QgsGeometry(mask)
    wgs_mask.transform(QgsCoordinateTransform(mask_crs, wgs84, project))
    target_mask = QgsGeometry(mask)
    target_mask.transform(QgsCoordinateTransform(mask_crs, target, project))
    bounds = wgs_mask.boundingBox()
    # Split large extents to keep Overpass requests below common public limits.
    cols = max(1, math.ceil(bounds.width() / .5))
    rows = max(1, math.ceil(bounds.height() / .5))
    if cols * rows > 1000:
        raise ValueError("OSM-rajaus on liian suuri yhdelle ajolle; valitse pienempi alue")
    fields = QgsFields()
    for name, kind in (("osm_id", QVariant.String), ("osm_type", QVariant.String),
                       ("tags", QVariant.String), ("code", QVariant.Int), ("fclass", QVariant.String)):
        fields.append(QgsField(name, kind))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = re.sub(r"[^\w]+", "_", entry["id"])[:60]
    writer = QgsVectorFileWriter.create(str(destination), fields, QgsWkbTypes.Unknown,
                                        target, project.transformContext(), options)
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError(writer.errorMessage())
    count, seen = 0, set()
    poi = entry["id"] == "osm_poi_points"
    try:
        for row in range(rows):
            for col in range(cols):
                west = bounds.xMinimum() + col * bounds.width() / cols
                east = bounds.xMinimum() + (col + 1) * bounds.width() / cols
                south = bounds.yMinimum() + row * bounds.height() / rows
                north = bounds.yMinimum() + (row + 1) * bounds.height() / rows
                bbox = f"{south},{west},{north},{east}"
                for element in _overpass_fetch(_osm_query(entry, bbox)):
                    key = (element.get("type"), element.get("id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    raw_geometry = _osm_geometry(element, poi)
                    if raw_geometry is None:
                        continue
                    geometry = QgsJsonUtils.geometryFromGeoJson(json.dumps(raw_geometry))
                    if geometry.isEmpty() or not geometry.intersects(wgs_mask):
                        continue
                    geometry.transform(QgsCoordinateTransform(wgs84, target, project))
                    if not geometry.intersects(target_mask):
                        continue
                    geometry = geometry.intersection(target_mask)
                    if geometry.isEmpty():
                        continue
                    tags = element.get("tags") or {}
                    classes = _poi_classes(tags) if poi else [(None, None)]
                    for code, fclass in classes:
                        feature = QgsFeature(fields)
                        feature.setAttributes([str(element.get("id") or ""), str(element.get("type") or ""),
                                               json.dumps(tags, ensure_ascii=False), code, fclass])
                        feature.setGeometry(geometry)
                        if not writer.addFeature(feature):
                            raise RuntimeError(writer.errorMessage())
                        count += 1
                if progress:
                    progress(count)
    finally:
        del writer
    if not count:
        Path(destination).unlink(missing_ok=True)
        raise RuntimeError("Rajauksesta ei löytynyt OSM-kohteita")
    layer = QgsVectorLayer(str(destination), entry["title"], "ogr")
    if not layer.isValid():
        raise RuntimeError("OSM-tulosta ei voitu avata")
    project.addMapLayer(layer)
    return count


def _download_kapsi(entry, mask, mask_crs, destination, progress=None):
    """Fetch Kapsi WMS in parallel tiles, mosaic to georeferenced GeoTIFF."""
    from osgeo import gdal, osr
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    selected.transform(QgsCoordinateTransform(mask_crs, target, QgsProject.instance()))
    ext = selected.boundingBox()
    width, height = ext.width(), ext.height()
    if width <= 0 or height <= 0:
        raise ValueError("Rajauksen leveys tai korkeus on nolla")
    min_scale = float(entry["min_scale"]) if entry.get("min_scale") else None
    max_scale = float(entry["max_scale"]) if entry.get("max_scale") else None
    exact = bool(re.search(r"_\d+(?:k|m)$", entry["id"], re.I) and (min_scale or max_scale))
    if exact:
        denominator = (math.sqrt(min_scale * max_scale) if min_scale and max_scale else
                       max_scale * .75 if max_scale else min_scale * 1.25)
        gsd = max(.1, denominator * .0254 / 72)
    else:
        gsd = 20.0
    tile_size = 4096
    cols = max(1, math.ceil(width / (tile_size * gsd)))
    rows = max(1, math.ceil(height / (tile_size * gsd)))
    if not exact and cols * rows > 25:
        gsd = max(gsd, math.sqrt(width * height / (25 * tile_size * tile_size)))
        cols = max(1, math.ceil(width / (tile_size * gsd)))
        rows = max(1, math.ceil(height / (tile_size * gsd)))
    # Exact-scale datasets are kept at their published resolution, even for
    # more than 25 tiles. Temporary files are released after the mosaic.
    def fetch(item):
        row, col, path, bounds = item
        params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1",
                  "LAYERS": entry["id"], "STYLES": "", "FORMAT": "image/jpeg",
                  "SRS": "EPSG:3067", "WIDTH": tile_size, "HEIGHT": tile_size,
                  "BBOX": ",".join(str(value) for value in bounds)}
        url = entry["endpoint"] + "?" + urllib.parse.urlencode(params)
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "image/jpeg"}), timeout=180) as response:
                    payload = response.read()
                    if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                        raise RuntimeError("Kapsi ei palauttanut kuvatiedostoa")
                if not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
                    raise RuntimeError("Kapsi palautti katkenneen JPEG-kuvan")
                path.write_bytes(payload)
                return item
            except Exception as exc:
                last_error = exc
                time.sleep(.5 * (attempt + 1))
        raise RuntimeError(f"Kapsi-laatta {row},{col}: {last_error}")
    with tempfile.TemporaryDirectory(prefix="suomenvaylat_kapsi_") as temp:
        plan = []
        for row in range(rows):
            for col in range(cols):
                xmin = ext.xMinimum() + col * width / cols
                xmax = ext.xMinimum() + (col + 1) * width / cols
                ymin = ext.yMinimum() + row * height / rows
                ymax = ext.yMinimum() + (row + 1) * height / rows
                plan.append((row, col, Path(temp) / f"tile_{row}_{col}.jpg", (xmin, ymin, xmax, ymax)))
        tile_paths = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(plan))) as pool:
            for number, item in enumerate(pool.map(fetch, plan), 1):
                row, col, path, bounds = item
                xmin, ymin, xmax, ymax = bounds
                pixel_x = (xmax - xmin) / tile_size
                pixel_y = -(ymax - ymin) / tile_size
                path.with_suffix(".jgw").write_text(
                    f"{pixel_x}\n0\n0\n{pixel_y}\n{xmin + pixel_x / 2}\n{ymax + pixel_y / 2}\n", encoding="ascii")
                reference = osr.SpatialReference()
                reference.ImportFromEPSG(3067)
                path.with_suffix(".prj").write_text(reference.ExportToWkt(), encoding="utf-8")
                tile_paths.append(str(path))
                if progress:
                    progress(number)
        vrt = gdal.BuildVRT(str(Path(temp) / "mosaic.vrt"), tile_paths)
        if vrt is None:
            raise RuntimeError("Kapsi-laattojen mosaiikki epäonnistui")
        vrt = None
        output_path = Path(destination).with_suffix(".tif")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = gdal.Translate(str(output_path), str(Path(temp) / "mosaic.vrt"),
                                creationOptions=["TILED=YES", "COMPRESS=JPEG", "PHOTOMETRIC=YCBCR"])
        if result is None:
            raise RuntimeError("Kapsi-GeoTIFFin kirjoitus epäonnistui")
        result = None
    layer = QgsRasterLayer(str(output_path), entry["title"])
    if not layer.isValid():
        raise RuntimeError("Kapsi-rasteria ei voitu avata")
    QgsProject.instance().addMapLayer(layer)
    return 1


def _download_ogc(entry, mask, mask_crs, destination, key="", progress=None):
    """Download all OGC API Features pages using the service's next links."""
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    target_crs = QgsCoordinateReferenceSystem("EPSG:3067")
    wgs_mask = QgsGeometry(mask)
    wgs_mask.transform(QgsCoordinateTransform(mask_crs, wgs84, project))
    target_mask = QgsGeometry(mask)
    target_mask.transform(QgsCoordinateTransform(mask_crs, target_crs, project))
    bounds = wgs_mask.boundingBox()
    bbox = ",".join(str(v) for v in (bounds.xMinimum(), bounds.yMinimum(), bounds.xMaximum(), bounds.yMaximum()))
    base = urllib.parse.urljoin(entry["endpoint"],
                                f"collections/{urllib.parse.quote(entry['id'], safe='')}/items")
    url = base + "?" + urllib.parse.urlencode({"bbox": bbox, "limit": 1000, "f": "json"})
    fields = None
    writer = None
    count, pages = 0, 0
    visited = set()
    destination = str(destination)
    try:
        while url:
            if url in visited or pages >= 10000:
                raise RuntimeError("OGC-sivutus pysähtyi; aineistoa ei tallennettu vajaana")
            visited.add(url)
            data = _request_json(url, key)
            features = data.get("features", [])
            if fields is None:
                if not features:
                    break
                fields = QgsFields()
                properties = features[0].get("properties") or {}
                for name, value in properties.items():
                    variant = QVariant.String
                    if isinstance(value, bool):
                        variant = QVariant.Bool
                    elif isinstance(value, int):
                        variant = QVariant.LongLong
                    elif isinstance(value, float):
                        variant = QVariant.Double
                    fields.append(QgsField(str(name), variant))
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                options.layerName = re.sub(r"[^\w]+", "_", entry["id"])[:60] or "data"
                writer = QgsVectorFileWriter.create(destination, fields, QgsWkbTypes.Unknown,
                                                    target_crs, project.transformContext(), options)
                if writer.hasError() != QgsVectorFileWriter.NoError:
                    raise RuntimeError(writer.errorMessage())
            for raw in features:
                geom_data = raw.get("geometry")
                if not geom_data:
                    continue
                geometry = QgsJsonUtils.geometryFromGeoJson(json.dumps(geom_data))
                if geometry.isEmpty() or not geometry.intersects(wgs_mask):
                    continue
                geometry.transform(QgsCoordinateTransform(wgs84, target_crs, project))
                if not geometry.intersects(target_mask):
                    continue
                geometry = geometry.intersection(target_mask)
                if geometry.isEmpty():
                    continue
                output = QgsFeature(fields)
                properties = raw.get("properties") or {}
                values = []
                for field in fields:
                    value = properties.get(field.name())
                    values.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
                output.setAttributes(values)
                output.setGeometry(geometry)
                if not writer.addFeature(output):
                    raise RuntimeError(writer.errorMessage())
                count += 1
            pages += 1
            if progress:
                progress(count)
            next_url = None
            for link in data.get("links", []):
                if link.get("rel") == "next" and link.get("href"):
                    next_url = urllib.parse.urljoin(url, link["href"])
                    break
            url = next_url
    finally:
        if writer is not None:
            del writer
    if count == 0:
        Path(destination).unlink(missing_ok=True)
        raise RuntimeError("Rajauksesta ei löytynyt kohteita")
    output = QgsVectorLayer(destination, entry["title"], "ogr")
    if not output.isValid():
        raise RuntimeError("Tallennettu taso ei avaudu")
    project.addMapLayer(output)
    return count
