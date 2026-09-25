"""Service catalog and geographic selection for Suomenväylät QGIS."""

import base64
import concurrent.futures
import json
import math
import re
import tempfile
import time
import unicodedata
import uuid
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
KARTTAKUVA_WMS = "https://karttakuva.maanmittauslaitos.fi/maasto/wms"
OVERPASS_ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
OSKARI_ENDPOINT = "https://julkinen.traficom.fi/oskari/action"
OSKARI_WMTS_ENDPOINT = "https://julkinen.traficom.fi/rasteripalvelu/wmts"
TRAFICOM_WFS_ENDPOINTS = (
    "https://julkinen.traficom.fi/inspirepalvelu/avoin/wfs",
    "https://julkinen.traficom.fi/inspirepalvelu/rajoitettu/wfs",
    "https://julkinen.traficom.fi/inspirepalvelu/ilmaliikenne/wfs",
)
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


def catalog(source, api_key="", password=""):
    """Fetch live WFS/OGC API catalog. Entries contain source, id and endpoint."""
    entries, errors = [], []
    if source == "Traficom Oskari":
        return _oskari_catalog()
    if source == "MML Karttakuva":
        if not api_key or not password:
            raise ValueError("MML Karttakuva vaatii käyttäjätunnuksen ja salasanan")
        credentials = base64.b64encode(f"{api_key}:{password}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(KARTTAKUVA_WMS + "?SERVICE=WMS&REQUEST=GetCapabilities",
                                         headers={"Authorization": "Basic " + credentials})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                root = ET.fromstring(response.read())
        except Exception as exc:
            raise RuntimeError(f"MML Karttakuva -tasoluettelo ei avaudu: {str(exc).replace(api_key, '[PIILOTETTU]').replace(password, '[PIILOTETTU]')}") from None
        for element in root.iter():
            if element.tag.split("}")[-1] != "Layer":
                continue
            children = {child.tag.split("}")[-1]: (child.text or "").strip() for child in element}
            name = children.get("Name")
            if name:
                entries.append({"source": source, "kind": "karttakuva_wms", "id": name,
                                "title": children.get("Title") or name, "endpoint": KARTTAKUVA_WMS})
        if not entries:
            raise RuntimeError("MML Karttakuva ei palauttanut karttatasoja")
        return entries, []
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
    if source == "Aino":
        endpoint = "https://aino.sitowise.com/ows"
        url = endpoint + "?" + urllib.parse.urlencode({"token": api_key,
                                                         "SERVICE": "WMS", "REQUEST": "GetCapabilities"})
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                root = ET.fromstring(response.read())
            for element in root.iter():
                if element.tag.split("}")[-1] != "Layer":
                    continue
                children = {child.tag.split("}")[-1]: (child.text or "").strip() for child in element}
                name = children.get("Name")
                if name:
                    entries.append({"source": source, "kind": "aino_wms", "id": name,
                                    "title": children.get("Title") or name, "endpoint": endpoint})
        except Exception as exc:
            errors.append("Aino WMS: " + str(exc).replace(api_key, "[PIILOTETTU]"))
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


def _oskari_catalog():
    query = urllib.parse.urlencode({"action_route": "GetHierarchicalMapLayerGroups",
                                     "srs": "EPSG:3067", "lang": "fi"})
    data = _request_json(OSKARI_ENDPOINT + "?" + query)
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        raise RuntimeError("Traficomin Oskari ei palauttanut tasoluetteloa")
    wfs_by_name = {}
    errors = []
    for endpoint in TRAFICOM_WFS_ENDPOINTS:
        try:
            url = endpoint + "?SERVICE=WFS&REQUEST=GetCapabilities"
            with urllib.request.urlopen(url, timeout=60) as response:
                root = ET.fromstring(response.read())
            for feature_type in root.iter():
                if feature_type.tag.split("}")[-1] != "FeatureType":
                    continue
                name = next(((child.text or "").strip() for child in feature_type
                             if child.tag.split("}")[-1] == "Name"), "")
                if name:
                    key = unicodedata.normalize("NFKC", name.split(":")[-1]).strip().casefold()
                    wfs_by_name.setdefault(key, (name, endpoint))
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    entries = []
    for item in data["layers"]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        layer_id = str(item["id"])
        layer_name = str(item.get("layerName") or "").strip()
        catalog_type = str(item.get("type") or "").strip().lower()
        title = str(item.get("name") or layer_name or layer_id).strip()
        if catalog_type == "wfslayer":
            kind, request_id, endpoint = "oskari_wfs", layer_id, OSKARI_ENDPOINT
        elif catalog_type == "wmslayer":
            key = unicodedata.normalize("NFKC", layer_name.split(":")[-1]).strip().casefold()
            match = wfs_by_name.get(key)
            kind, request_id, endpoint = ("wfs", *match) if match else ("oskari_wms", layer_id, OSKARI_ENDPOINT)
        elif catalog_type == "wmtslayer":
            kind, request_id, endpoint = "oskari_wmts", layer_id, OSKARI_WMTS_ENDPOINT
        else:
            kind, request_id, endpoint = "oskari_wms", layer_id, OSKARI_ENDPOINT
        entries.append({"source": "Traficom Oskari", "kind": kind, "id": request_id,
                        "title": title, "endpoint": endpoint, "layer_name": layer_name,
                        "catalog_id": layer_id, "catalog_type": catalog_type,
                        "style": item.get("style")})
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
    if entry["kind"] == "oskari_wms":
        return _download_oskari_wms(entry, mask, mask_crs, destination)
    if entry["kind"] == "oskari_wmts":
        return _download_oskari_wmts(entry, mask, mask_crs, destination)
    if entry["kind"] == "oskari_wfs":
        project = QgsProject.instance()
        target = QgsCoordinateReferenceSystem("EPSG:3067")
        selected = QgsGeometry(mask)
        selected.transform(QgsCoordinateTransform(mask_crs, target, project))
        bounds = selected.boundingBox()
        bbox = ",".join(str(value) for value in (bounds.xMinimum(), bounds.yMinimum(),
                                                 bounds.xMaximum(), bounds.yMaximum()))
        url = entry["endpoint"] + "?" + urllib.parse.urlencode({
            "action_route": "GetWFSFeatures", "id": entry["id"],
            "srs": "EPSG:3067", "bbox": bbox})
        data = _request_json(url)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise RuntimeError("Oskari ei palauttanut GeoJSON-kohteita")
        crs = data.get("crs") or {}
        crs_name = (crs.get("properties") or {}).get("name") if isinstance(crs, dict) else None
        if crs_name and "3067" not in str(crs_name):
            raise RuntimeError(f"Oskari palautti väärän koordinaatiston: {crs_name}")
        if not data["features"]:
            raise RuntimeError("Rajauksesta ei löytynyt kohteita")
        from osgeo import gdal
        path = "/vsimem/suomenvaylat_oskari_" + uuid.uuid4().hex + ".geojson"
        # GDAL exposes a top-level feature id as an ID field. Oskari also
        # has an ID property, so retain the property and avoid collision.
        for feature in data["features"]:
            feature.pop("id", None)
        data["crs"] = {"type": "name", "properties": {"name": "EPSG:3067"}}
        gdal.FileFromMemBuffer(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        layer = None
        try:
            layer = QgsVectorLayer(path, entry["title"], "ogr")
            if not layer.isValid() or layer.featureCount() != len(data["features"]):
                raise RuntimeError("QGIS ei avannut kaikkia Oskarin vektorikohteita")
            layer.setCrs(target)
            return _download_vector_layer(entry, layer, mask, mask_crs, destination, progress)
        finally:
            if layer is not None:
                from qgis.PyQt import sip
                sip.delete(layer)
            gdal.Unlink(path)
    layer = _open_remote_layer(entry)
    return _download_vector_layer(entry, layer, mask, mask_crs, destination, progress)


def _download_vector_layer(entry, layer, mask, mask_crs, destination, progress=None):
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


def _download_oskari_wms(entry, mask, mask_crs, destination):
    """Save an Oskari map image as a georeferenced GeoTIFF."""
    from osgeo import gdal
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    selected.transform(QgsCoordinateTransform(mask_crs, target, QgsProject.instance()))
    ext = selected.boundingBox()
    if ext.width() <= 0 or ext.height() <= 0:
        raise ValueError("Rajauksen laajuus ei riitä Oskari-karttakuvan lataukseen")
    layer_name = entry.get("layer_name") or ""
    if not layer_name:
        raise ValueError("Oskarin WMS-tasolta puuttuu tekninen nimi")
    style = entry.get("style") or ""
    if isinstance(style, (list, tuple)):
        style = ",".join(map(str, style))
    elif isinstance(style, dict):
        style = style.get("name") or style.get("id") or ""
    scale = 2048 / max(ext.width(), ext.height())
    width = max(1, min(2048, round(ext.width() * scale)))
    height = max(1, min(2048, round(ext.height() * scale)))
    params = {"action_route": "GetLayerTile", "id": entry["catalog_id"],
              "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1",
              "LAYERS": layer_name, "STYLES": style, "SRS": "EPSG:3067",
              "BBOX": ",".join(map(str, (ext.xMinimum(), ext.yMinimum(),
                                     ext.xMaximum(), ext.yMaximum()))),
              "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png",
              "TRANSPARENT": "TRUE"}
    request = urllib.request.Request(entry["endpoint"] + "?" + urllib.parse.urlencode(params),
                                     headers={"Accept": "image/png"})
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/") or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Oskari WMS ei palauttanut PNG-kuvaa")
    destination = Path(destination).with_suffix(".tif")
    with tempfile.TemporaryDirectory(prefix="suomenvaylat_oskari_wms_") as temp:
        png = Path(temp) / "image.png"
        png.write_bytes(raw)
        result = gdal.Translate(str(destination), str(png), outputSRS="EPSG:3067",
                                outputBounds=[ext.xMinimum(), ext.yMaximum(),
                                              ext.xMaximum(), ext.yMinimum()],
                                creationOptions=["TILED=YES", "COMPRESS=DEFLATE"])
        if result is None:
            raise RuntimeError("Oskari-karttakuvan GeoTIFF-tallennus epäonnistui")
        result = None
    layer = QgsRasterLayer(str(destination), entry["title"])
    if not layer.isValid():
        destination.unlink(missing_ok=True)
        raise RuntimeError("Tallennettu Oskari-karttakuva ei avaudu")
    QgsProject.instance().addMapLayer(layer)
    return 1


def _download_oskari_wmts(entry, mask, mask_crs, destination):
    """Read the service's EPSG:3067 tile matrix through GDAL and save a GeoTIFF."""
    from osgeo import gdal
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    selected.transform(QgsCoordinateTransform(mask_crs, target, QgsProject.instance()))
    ext = selected.boundingBox()
    if ext.width() <= 0 or ext.height() <= 0:
        raise ValueError("Rajauksen laajuus ei riitä Oskari-WMTS:n lataukseen")
    capabilities = entry["endpoint"] + "?service=WMTS&request=GetCapabilities"
    container = gdal.Open("WMTS:" + capabilities)
    if container is None:
        raise RuntimeError("Traficomin WMTS-palvelua ei voitu avata")
    layer_name = entry.get("layer_name") or ""
    source_name = next((name for name, _description in container.GetSubDatasets()
                        if f'layer="{layer_name}"' in name and
                        "tilematrixset=ETRS89_TM35-FIN" in name), None)
    if not source_name:
        raise RuntimeError(f"WMTS-palvelusta puuttuu EPSG:3067-taso: {layer_name}")
    source = gdal.Open(source_name)
    if source is None:
        raise RuntimeError(f"WMTS-tasoa ei voitu avata: {layer_name}")
    native_res = abs(source.GetGeoTransform()[1])
    # Limit a single request to roughly 256 256-pixel tiles, as in ArcGIS Pro.
    resolution = native_res
    while math.ceil(ext.width() / (256 * resolution)) * math.ceil(ext.height() / (256 * resolution)) > 256:
        resolution *= 2
    destination = Path(destination).with_suffix(".tif")
    result = gdal.Translate(str(destination), source,
                            projWin=[ext.xMinimum(), ext.yMaximum(),
                                     ext.xMaximum(), ext.yMinimum()],
                            xRes=resolution, yRes=resolution,
                            creationOptions=["TILED=YES", "COMPRESS=DEFLATE"])
    if result is None:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Oskari-WMTS:n GeoTIFF-tallennus epäonnistui")
    result = None
    layer = QgsRasterLayer(str(destination), entry["title"])
    if not layer.isValid():
        destination.unlink(missing_ok=True)
        raise RuntimeError("Tallennettu Oskari-WMTS ei avaudu")
    QgsProject.instance().addMapLayer(layer)
    return 1


def _osm_query(entry, bbox):
    if entry["id"] != "osm_poi_points":
        return "[out:json][timeout:120];" + entry["query"].format(bbox=bbox)
    classes = _poi_table()
    values = {}
    for key, value, _, _ in classes:
        values.setdefault(key, set()).add(value)
    for key, extras in {"amenity": {"recycling", "vending_machine"},
                        "office": {"diplomatic"}, "landuse": {"cemetery"},
                        "man_made": {"tower"}}.items():
        values.setdefault(key, set()).update(extras)
    selectors = []
    for key, choices in sorted(values.items()):
        if key in {"amenity", "historic", "leisure", "shop", "tourism"}:
            selectors.append(f'nwr["{key}"]({bbox});')
        elif len(choices) == 1:
            selectors.append(f'nwr["{key}"="{next(iter(choices))}"]({bbox});')
        else:
            pattern = "^(" + "|".join(sorted(re.escape(choice) for choice in choices)) + ")$"
            selectors.append(f'nwr["{key}"~"{pattern}"]({bbox});')
    selectors = "".join(selectors)
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
    def add(code, name):
        if (code, name) not in result:
            result.append((code, name))
    if tags.get("office") == "diplomatic":
        if tags.get("diplomatic") == "consulate":
            add(2017, "consulate")
        elif tags.get("diplomatic") == "embassy":
            add(2011, "embassy")
    if tags.get("landuse") == "cemetery":
        add(2015, "graveyard")
    if tags.get("amenity") == "recycling":
        for field, code, name in (("recycling:glass", 2031, "recycling_glass"),
                                  ("recycling:glass_bottles", 2031, "recycling_glass"),
                                  ("recycling:paper", 2032, "recycling_paper"),
                                  ("recycling:clothes", 2033, "recycling_clothes"),
                                  ("recycling:scrap_metal", 2034, "recycling_metal")):
            if tags.get(field) == "yes":
                add(code, name)
                break
        else:
            add(2030, "recycling")
    if tags.get("amenity") == "vending_machine":
        add(2592, "vending_parking") if tags.get("vending") == "parking_tickets" else add(2590, "vending_machine")
    if tags.get("man_made") == "tower":
        tower = tags.get("tower:type")
        if tower == "communication":
            add(2951, "comms_tower")
        elif tower == "observation":
            add(2953, "observation_tower")
        else:
            add(2950, "tower")
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
