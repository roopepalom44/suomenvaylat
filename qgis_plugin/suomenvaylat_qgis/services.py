"""Service catalog and geographic selection for Suomenväylät QGIS."""

import base64
import concurrent.futures
from contextvars import ContextVar
import pickle
import inspect
import sqlite3
import json
import math
import re
import tempfile
import time
import unicodedata
import uuid
from functools import lru_cache
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsField, QgsFields, QgsGeometry, QgsJsonUtils,
    QgsProject, QgsRasterLayer, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .osm_geometry import element_geometry
from .service_styles import StyleClient, MAX_STYLE_BYTES, localize_graphics, save_sld, StyleError
from . import service_network, wfs_pages

from .service_network import checked_xml
_WORK_CONTEXT = ContextVar("suomenvaylat_transform_context", default=None)


def _notify(progress, count, total=None, stage="Ladataan"):
    try:
        inspect.signature(progress).bind(count, total, stage)
    except (TypeError, ValueError):
        progress(count)
    else:
        progress(count, total, stage)


def _capability_results(urls):
    def fetch(url):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": SERVICE_USER_AGENT})
            return checked_xml(service_network.request_bytes(request, _urlopen,
                               limit=service_network.MAX_XML_BYTES)), None
        except Exception as exc:
            return None, exc
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as pool:
        return list(pool.map(fetch, urls))


def _transform_context():
    context = _WORK_CONTEXT.get()
    return context if context is not None else QgsProject.instance().transformContext()


def _transform(geometry, transform):
    result = geometry.transform(transform)
    if int(getattr(result, "value", result)) != 0:
        raise RuntimeError("Geometrian koordinaatistomuunnos epäonnistui")
    return geometry


def _geometry_from_geojson(data):
    if hasattr(QgsJsonUtils, "geometryFromGeoJson"):
        return QgsJsonUtils.geometryFromGeoJson(json.dumps(data))
    from osgeo import ogr
    raw = ogr.CreateGeometryFromJson(json.dumps(data))
    geometry = QgsGeometry()
    if raw is not None:
        geometry.fromWkb(bytes(raw.ExportToWkb()))
    return geometry


def _prepared(mask):
    engine = QgsGeometry.createGeometryEngine(mask.constGet())
    engine.prepareGeometry()
    return engine


def cleanup_output(destination):
    """Release output sidecars without replacing the original download error."""
    from qgis.core import QgsMessageLog, Qgis
    paths = [Path(destination)] + [Path(str(destination) + suffix) for suffix in ("-wal", "-shm", ".aux.xml")]
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            QgsMessageLog.logMessage(f"Tiedoston siivous epäonnistui: {path}", "Suomenväylät", Qgis.Warning)


def _output_descriptor(destination, title, layer_name=None):
    return {"path": str(destination), "title": title, "layer_name": layer_name}


def open_downloaded_layer(item, entry):
    """Called on the GUI thread, after every writer has closed."""
    path, name = item["path"], item.get("layer_name")
    layer = (QgsVectorLayer(f"{path}|layername={name}", item["title"], "ogr") if name
             else QgsRasterLayer(path, item["title"]))
    if not layer.isValid():
        raise RuntimeError(f"Tallennettu taso ei avaudu: {item['title']}")
    if name:
        _apply_provider_style(layer, entry, path, prepared=item.get("style_prepared", False))
    return layer


def download_to_files(entry, mask, mask_crs, destination, context, key="", progress=None):
    """Worker API: value objects in, file descriptors out. No QgsMapLayers."""
    token = _WORK_CONTEXT.set(context)
    outputs = []
    try:
        count = download(entry, mask, mask_crs, destination, key, progress,
                         add_layer=outputs.append, files_only=True)
        if entry.get("kind") == "wfs":
            _prepare_provider_style(entry, destination)
        for item in outputs:
            item["style_prepared"] = True
        return count, outputs
    except Exception:
        cleanup_output(destination)
        raise
    finally:
        _WORK_CONTEXT.reset(token)

WFS_SOURCES = {
    "Väylä": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/ows"],
    "DigiRoad": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/digiroad/ows"],
    "Liiteri": [
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_asuinalueet/wfs",
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_etaisyysvyohykkeet/wfs",
        "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_taajamat/wfs",
    ],
    "Syke": ["https://paikkatiedot.ymparisto.fi/geoserver/inspire_ps/wfs"],
    # Globaali GeoServer-WFS: kaikki Tilastokeskuksen työtilat yhdellä
    # GetCapabilitiesilla (INSPIRE OGC API oli samoille alueille 10-40x hitaampi).
    "Tilastokeskus": ["https://geo.stat.fi/geoserver/wfs"],
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


# Näille lähteille ArcGIS Pro -versio käyttää CQL INTERSECTS -hakua, joka
# palauttaa kokonaiset rajaukseen osuvat geometriat ilman leikkausta. Muut
# vektorilähteet leikataan rajaukseen kuten ArcGIS Pron Clip-vaiheessa.
# Tilastokeskuksen tilastoarvot koskevat koko aluetta tai ruutua, joten
# leikattu pala antaisi harhaanjohtavan tuloksen.
UNCLIPPED_WFS_SOURCES = frozenset(["Väylä", "DigiRoad", "Tilastokeskus"])
# Palvelun testityötilat, joita ei näytetä tasoluettelossa.
EXCLUDED_WFS_PREFIXES = {"Tilastokeskus": ("testi:",)}
SERVICE_USER_AGENT = "Suomenvaylat-QGIS (+https://github.com/roopepalom44/suomenvaylat)"
SENSITIVE_HEADERS = service_network.SENSITIVE_HEADERS
_url_origin = service_network.url_origin
_redirect_headers = service_network.redirect_headers
_SafeRedirectHandler = service_network.SafeRedirectHandler
_SAFE_URL_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _urlopen(request, timeout=45):
    """``urlopen`` ilman tunnisteiden vuotoa uudelleenohjauksessa."""
    url = request if isinstance(request, str) else request.full_url
    if (urllib.parse.urlsplit(url).hostname or "").casefold() == "aino.sitowise.com":
        # Aino's Cloudflare rejects Python-urllib with HTTP 403 / error 1010
        # before checking the token. Identify the plugin on every Aino request,
        # including capabilities and style requests built as Request objects.
        if isinstance(request, str):
            request = urllib.request.Request(request)
        if not request.has_header("User-agent"):
            request.add_header("User-Agent", SERVICE_USER_AGENT)
    return _SAFE_URL_OPENER.open(request, timeout=timeout)


def _add_project_layer(layer):
    """Enable on-the-fly reprojection before adding georeferenced data."""
    if not layer.crs().isValid():
        raise RuntimeError(f"Tason koordinaatistoa ei tunnistettu: {layer.name()}")
    project = QgsProject.instance()
    if not project.crs().isValid():
        project.setCrs(layer.crs())
    project.addMapLayer(layer)


KARTTAKUVA_WMS = "https://karttakuva.maanmittauslaitos.fi/maasto/wms"
KARTTAKUVA_WMTS = "https://karttakuva.maanmittauslaitos.fi/maasto/wmts/1.0.0/WMTSCapabilities.xml"
OVERPASS_USER_AGENT = SERVICE_USER_AGENT
OVERPASS_ENDPOINTS = service_network.OVERPASS_ENDPOINTS
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
    headers = {"Accept": "application/geo+json, application/json", "User-Agent": SERVICE_USER_AGENT}
    if key:
        headers["Authorization"] = "Basic " + base64.b64encode((key + ":").encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    raw = service_network.request_bytes(request, _urlopen)
    error = service_network.ogc_exception(raw)
    if error:
        raise RuntimeError("OGC-virhe: " + error)
    return json.loads(raw)


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
            with _urlopen(request, timeout=45) as response:
                root = checked_xml(response.read(service_network.MAX_XML_BYTES + 1))
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
        # Offer native cached tiles first, retaining WMS for untiled layers.
        try:
            request = urllib.request.Request(KARTTAKUVA_WMTS, headers={"Authorization": "Basic " + credentials})
            root = checked_xml(service_network.request_bytes(request, _urlopen, limit=service_network.MAX_XML_BYTES))
            tiled = []
            for node in root.iter():
                if node.tag.split("}")[-1] != "Layer":
                    continue
                children = {child.tag.split("}")[-1]: child for child in node}
                identifier = children.get("Identifier")
                if identifier is None or not identifier.text:
                    continue
                matrices = [(item.text or "").strip() for item in node.iter()
                            if item.tag.split("}")[-1] == "TileMatrixSet"]
                matrix = next((value for value in matrices if "3067" in value or "TM35" in value), None)
                if not matrix:
                    continue
                title = children.get("Title")
                styles = [item for item in node if item.tag.split("}")[-1] == "Style"]
                style = next((item for item in styles if item.get("isDefault") == "true"), styles[0] if styles else None)
                style_name = next(((item.text or "") for item in style if item.tag.split("}")[-1] == "Identifier"), "default") if style is not None else "default"
                fmt = next(((item.text or "image/png") for item in node if item.tag.split("}")[-1] == "Format"), "image/png")
                tiled.append({"source": source, "kind": "karttakuva_wmts", "id": identifier.text.strip(),
                              "title": ((title.text or identifier.text) if title is not None else identifier.text) + " — WMTS",
                              "endpoint": KARTTAKUVA_WMTS, "matrix": matrix, "style": style_name, "format": fmt})
            entries = tiled + entries
        except Exception:
            errors.append("MML Karttakuva WMTS ei avaudu; WMS-tasot ovat käytettävissä")
        return entries, errors
    if source == "Kapsi":
        by_key = {}
        items = list(KAPSI_SERVICES.items())
        urls = [endpoint + "?SERVICE=WMS&REQUEST=GetCapabilities" for _, endpoint in items]
        for (service, endpoint), (root, error) in zip(items, _capability_results(urls)):
            try:
                if error is not None:
                    raise error
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
    endpoints = [endpoint + "?" + urllib.parse.urlencode({"token": api_key}) if source == "Aino" else endpoint
                 for endpoint in WFS_SOURCES.get(source, [])]
    urls = [endpoint + ("&" if "?" in endpoint else "?") + "SERVICE=WFS&REQUEST=GetCapabilities"
            for endpoint in endpoints]
    for endpoint, (root, error) in zip(endpoints, _capability_results(urls)):
        try:
            if error is not None:
                raise error
            for element in root.iter():
                if element.tag.split("}")[-1] != "FeatureType":
                    continue
                children = {child.tag.split("}")[-1]: (child.text or "").strip() for child in element}
                name = children.get("Name", "")
                if name and name.lower().startswith(EXCLUDED_WFS_PREFIXES.get(source, ())):
                    continue
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
            with _urlopen(url, timeout=45) as response:
                root = checked_xml(response.read(service_network.MAX_XML_BYTES + 1))
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
    urls = [endpoint + "?SERVICE=WFS&REQUEST=GetCapabilities" for endpoint in TRAFICOM_WFS_ENDPOINTS]
    for endpoint, (root, error) in zip(TRAFICOM_WFS_ENDPOINTS, _capability_results(urls)):
        try:
            if error is not None:
                raise error
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
    """Paketissa GeoPackage on lisäosan resources-kansiossa.

    Lähdekoodista ajettaessa käytetään ArcGIS Pro -työkalun samaa aineistoa,
    jotta 36 Mt:n tiedostoa ei tarvitse pitää repossa kahdesti.
    """
    packaged = Path(__file__).parent / "resources" / "hallinnolliset_aluejaot.gpkg"
    if packaged.exists():
        return packaged
    repository_copy = (Path(__file__).resolve().parents[2] / "Toolboxes" / "Resources"
                       / "hallinnolliset_aluejaot.gpkg")
    return repository_copy if repository_copy.exists() else packaged


@lru_cache(maxsize=16)
def _area_choices(area_type, path, modified):
    name = ADMIN_LAYERS.get(area_type)
    if name is None:
        return []
    # Only attribute names are needed; avoid reading geometries or GUI objects.
    with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as database:
        rows = database.execute(f'SELECT DISTINCT namefin FROM "{name}" WHERE namefin IS NOT NULL')
        return tuple(sorted(str(row[0]) for row in rows if row[0]))


def area_choices(area_type):
    path = admin_path()
    return list(_area_choices(area_type, str(path), path.stat().st_mtime_ns))


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
    geometry_type = _geometry_type_value(layer.geometryType())
    if geometry_type not in (1, 2):
        raise ValueError("Rajausaineiston tulee olla viiva tai polygon")
    chosen = []
    names = set(names)
    if area_type == "Oma aineisto" and layer.selectedFeatureCount() > 0:
        # Kuten ArcGIS Prossa: tason valinta rajaa käytettävät kohteet.
        features = layer.getSelectedFeatures()
    else:
        features = layer.getFeatures()
    for feature in features:
        if not names or area_type == "Oma aineisto" or str(feature["namefin"]) in names:
            geom = QgsGeometry(feature.geometry())
            if not geom.isEmpty():
                chosen.append(geom)
    if not chosen:
        raise ValueError("Valitulta alueelta ei löytynyt geometriaa")
    mask = QgsGeometry.unaryUnion(chosen)
    if geometry_type == 1:
        # Kuten ArcGIS Pron MinimumBoundingGeometry(CONVEX_HULL, ALL):
        # viivarajaus muutetaan kaikkien kohteiden konveksiksi peitteeksi.
        mask = mask.convexHull()
        if mask.isEmpty() or mask.area() <= 0:
            raise ValueError("Viivarajauksesta ei voitu muodostaa aluetta (viivat ovat samalla suoralla)")
    return mask, layer.crs()


def _geometry_type_value(value):
    """Palauta Qgis.GeometryType kokonaislukuna (0 piste, 1 viiva, 2 alue)."""
    return int(getattr(value, "value", value))


def _clip_geometry(geometry, mask):
    """Leikkaa geometria rajaukseen säilyttäen sen geometriatyypin.

    GEOS palauttaa rajaa sivuavista osista GeometryCollectionin (esim. alue
    + viiva). QGIS ei tunnista sellaisesta GPKG-tasosta koordinaatistoa,
    joten leikkauksesta säilytetään vain lähdegeometrian tyyppiset osat.
    """
    family = _geometry_type_value(geometry.type())
    clipped = geometry.intersection(mask)
    if clipped.isEmpty() or family not in (0, 1, 2):
        # Lähde on jo itse sekakokoelma: säilytä leikkaus sellaisenaan.
        return clipped
    is_collection = QgsWkbTypes.flatType(clipped.wkbType()) == QgsWkbTypes.GeometryCollection
    if not is_collection and _geometry_type_value(clipped.type()) == family:
        return clipped
    parts = [part for part in clipped.asGeometryCollection()
             if _geometry_type_value(part.type()) == family]
    return QgsGeometry.collectGeometry(parts) if parts else QgsGeometry()


def download(entry, mask, mask_crs, destination, key="", progress=None, add_layer=None, files_only=False):
    """Stream intersecting features into a GeoPackage in EPSG:3067.

    The worker passes files_only=True and receives plain output descriptors.
    """
    project_add = add_layer or _add_project_layer

    def add_layer(item):
        project_add(item if files_only else open_downloaded_layer(item, entry))
    if entry["kind"] == "kapsi_wms":
        return _download_kapsi(entry, mask, mask_crs, destination, progress, add_layer)
    if entry["kind"] == "osm":
        return _download_osm(entry, mask, mask_crs, destination, progress, add_layer)
    if entry["kind"] == "ogc":
        return _download_ogc(entry, mask, mask_crs, destination, key, progress, add_layer)
    if entry["kind"] == "oskari_wms":
        return _download_oskari_wms(entry, mask, mask_crs, destination, add_layer)
    if entry["kind"] == "oskari_wmts":
        return _download_oskari_wmts(entry, mask, mask_crs, destination, add_layer)
    if entry["kind"] == "oskari_wfs":
        project = _transform_context()
        target = QgsCoordinateReferenceSystem("EPSG:3067")
        selected = QgsGeometry(mask)
        _transform(selected, QgsCoordinateTransform(mask_crs, target, project))
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
        total = wfs_pages.matched_count(data)
        if total is not None and total != len(data["features"]):
            raise RuntimeError("Oskari palautti vaillinaisen aineiston")
        return _write_pages(entry, iter([data["features"]]), target, mask, mask_crs,
                            destination, progress, add_layer, clip=True)
    return _download_wfs(entry, mask, mask_crs, destination, progress, add_layer)


def _style_fetch(url):
    with _urlopen(urllib.request.Request(url), timeout=15) as response:
        data = response.read(MAX_STYLE_BYTES + 1)
    if len(data) > MAX_STYLE_BYTES:
        raise StyleError("Tyylivastaus on liian suuri")
    return data


def _gml_features(raw):
    """Decode a single GML page with OGR; no QObject or project is involved."""
    from osgeo import gdal, ogr
    path = "/vsimem/suomenvaylat_" + uuid.uuid4().hex + ".gml"
    gdal.FileFromMemBuffer(path, raw)
    dataset = None
    try:
        dataset = ogr.Open(path)
        if dataset is None:
            raise RuntimeError("Palvelun GML-vastaus ei avaudu")
        layer = dataset.GetLayer(0)
        features = []
        for feature in layer:
            geometry = feature.GetGeometryRef()
            features.append({"type": "Feature", "id": feature.GetFID(),
                             "geometry": json.loads(geometry.ExportToJson()) if geometry else None,
                             "properties": feature.items()})
        return {"features": features}
    finally:
        dataset = None
        gdal.Unlink(path)
        gdal.Unlink(path + ".gfs")


def _download_wfs(entry, mask, mask_crs, destination, progress, add_layer):
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    _transform(selected, QgsCoordinateTransform(mask_crs, target, _transform_context()))
    bounds = selected.boundingBox()
    # Same retrieval margin as ArcGIS. The exact mask is applied locally.
    bbox = ",".join(map(str, (bounds.xMinimum() - 1000, bounds.yMinimum() - 1000,
                              bounds.xMaximum() + 1000, bounds.yMaximum() + 1000)))
    cql_wkt = selected.asWkt() if entry.get("source") in UNCLIPPED_WFS_SOURCES else None

    def checkpoint():
        if progress:
            progress(0)

    def fetch(url, body):
        headers = {"User-Agent": SERVICE_USER_AGENT}
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return service_network.request_bytes(urllib.request.Request(url, data=body, headers=headers),
                                             _urlopen, timeout=300, checkpoint=checkpoint)

    def feature_pages():
        for features, expected, received in wfs_pages.pages(entry, bbox, cql_wkt, fetch,
                                                            _gml_features, checkpoint):
            if progress:
                _notify(progress, received, expected)
            yield features

    return _write_pages(entry, feature_pages(), target, mask, mask_crs, destination, progress,
                        add_layer, clip=entry.get("source") not in UNCLIPPED_WFS_SOURCES)


def _write_pages(entry, pages, source_crs, mask, mask_crs, destination, progress, add_layer, clip=True):
    """Bounded-memory schema inference and WKB spool, then atomic layer writes."""
    context = _transform_context()
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    source_mask, target_mask = QgsGeometry(mask), QgsGeometry(mask)
    _transform(source_mask, QgsCoordinateTransform(mask_crs, source_crs, context))
    _transform(target_mask, QgsCoordinateTransform(mask_crs, target, context))
    source_engine, target_engine = _prepared(source_mask), _prepared(target_mask)
    to_target = QgsCoordinateTransform(source_crs, target, context)
    field_kinds, families, geometry_types = {}, set(), {}
    count = 0
    base_name = re.sub(r"[^\w]+", "_", entry["id"].split(":")[-1])[:60] or "data"
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="suomenvaylat_features_") as temp:
            spool_path = Path(temp) / "features.bin"
            with spool_path.open("wb") as spool:
                for page in pages:
                    if progress:
                        progress(count)
                    for index, raw in enumerate(page):
                        if progress and index % 100 == 0:
                            progress(count)
                        data = raw.get("geometry")
                        if not data:
                            continue
                        geometry = _geometry_from_geojson(data)
                        if geometry.isEmpty() or not source_engine.intersects(geometry.constGet()):
                            continue
                        _transform(geometry, to_target)
                        if not target_engine.intersects(geometry.constGet()):
                            continue
                        if clip:
                            geometry = _clip_geometry(geometry, target_mask)
                        if geometry.isEmpty():
                            continue
                        family = _geometry_type_value(geometry.type())
                        if family not in (0, 1, 2):
                            raise RuntimeError("Palvelu palautti tukemattoman geometriakokoelman")
                        kind = geometry.wkbType()
                        current = geometry_types.get(family, kind)
                        if QgsWkbTypes.isMultiType(kind):
                            current = QgsWkbTypes.multiType(current)
                        if QgsWkbTypes.hasZ(kind):
                            current = QgsWkbTypes.addZ(current)
                        if QgsWkbTypes.hasM(kind):
                            current = QgsWkbTypes.addM(current)
                        geometry_types[family] = current
                        properties = dict(raw.get("properties") or {})
                        if raw.get("id") is not None and "id" not in properties:
                            properties["id"] = str(raw["id"])
                        for name, value in properties.items():
                            field_kinds.setdefault(str(name), set()).add(_ogc_field_kind(value))
                        # Only this process writes/reads this private temporary file.
                        pickle.dump((family, bytes(geometry.asWkb()), properties), spool, protocol=4)
                        families.add(family)
                        count += 1
            if not count:
                raise RuntimeError("Rajauksesta ei löytynyt kohteita")
            fields, variants = QgsFields(), {}
            for name, kinds in field_kinds.items():
                variants[name] = _ogc_variant(kinds)
                fields.append(QgsField(name, variants[name]))
            outputs = []
            for family, suffix in ((0, "pisteet"), (1, "viivat"), (2, "alueet")):
                if family not in families:
                    continue
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                options.layerName = base_name if len(families) == 1 else f"{base_name}_{suffix}"
                options.layerOptions = ["FID=__sv_fid"]
                if outputs:
                    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
                writer = QgsVectorFileWriter.create(str(destination), fields, geometry_types[family],
                                                    target, context, options)
                if writer.hasError() != QgsVectorFileWriter.NoError:
                    error = writer.errorMessage()
                    del writer
                    raise RuntimeError(error)
                title = entry["title"] if len(families) == 1 else f"{entry['title']} ({suffix})"
                outputs.append(_output_descriptor(destination, title, options.layerName))
                try:
                    with spool_path.open("rb") as spool:
                        written = 0
                        while True:
                            try:
                                record_family, wkb, properties = pickle.load(spool)
                            except EOFError:
                                break
                            if progress and written % 100 == 0:
                                _notify(progress, written, count, "Tallennetaan")
                            if record_family != family:
                                continue
                            geometry = QgsGeometry()
                            geometry.fromWkb(wkb)
                            feature = QgsFeature(fields)
                            feature.setAttributes([_ogc_value(properties.get(f.name()), variants[f.name()]) for f in fields])
                            feature.setGeometry(geometry)
                            if not writer.addFeature(feature):
                                raise RuntimeError("Kohteen tallennus epäonnistui: " + writer.errorMessage())
                            written += 1
                finally:
                    del writer
            if progress:
                _notify(progress, count, count, "Valmis")
            for item in outputs:
                add_layer(item)
        return count
    except Exception:
        cleanup_output(destination)
        raise


def _prepare_provider_style(entry, destination):
    path = Path(destination).with_suffix(".sld")
    try:
        result = StyleClient(_style_fetch).get(entry)
        if result is not None:
            root, endpoint = result
            save_sld(root, path)
            localize_graphics(root, endpoint, path, _style_fetch)
            save_sld(root, path)
    except Exception:
        pass  # Styling must not invalidate a complete dataset.


def _apply_provider_style(layer, entry, destination, prepared=False):
    """Apply and persist a downloaded style on the GUI thread."""
    from qgis.core import QgsMessageLog, Qgis
    path = Path(destination).with_suffix(".sld")
    try:
        if not prepared:
            _prepare_provider_style(entry, destination)
        if not path.exists():
            return
        message, ok = layer.loadSldStyle(str(path))
        if not ok:
            raise StyleError("QGIS ei voinut ottaa SLD-symboliikkaa käyttöön")
        layer.setCustomProperty("suomenvaylat/style_status", "Rajapinnan symboliikka")
        # GeoPackage stores the native renderer and labels, so reopening the
        # downloaded dataset also restores the style without a network request.
        message = layer.saveStyleToDatabase("Suomenväylät", "Rajapinnan oletustyyli", True, "")
        # The separate style connection changes SQLite's schema. Reopen the
        # OGR reader before it executes cached feature/count statements.
        layer.dataProvider().reloadData()
        layer.saveNamedStyle(str(Path(destination).with_suffix(".qml")))
        if message:
            QgsMessageLog.logMessage("Tyylin GeoPackage-tallennus: " + message, "Suomenväylät", Qgis.Warning)
    except Exception as exc:
        # Raw HTTP exception URLs can contain an Aino token. Persist only the
        # curated error, never a service response or authenticated request URL.
        message = str(exc) if isinstance(exc, StyleError) else "Rajapinnan symboliikan käyttöönotto epäonnistui"
        layer.setCustomProperty("suomenvaylat/style_status", message)
        QgsMessageLog.logMessage(layer.name() + ": " + message, "Suomenväylät", Qgis.Warning)


def _download_oskari_wms(entry, mask, mask_crs, destination, add_layer=None):
    """Save an Oskari map image as a georeferenced GeoTIFF."""
    from osgeo import gdal
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    _transform(selected, QgsCoordinateTransform(mask_crs, target, _transform_context()))
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
    with _urlopen(request, timeout=180) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/") or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Oskari WMS ei palauttanut PNG-kuvaa")
    destination = Path(destination).with_suffix(".tif")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="suomenvaylat_oskari_wms_") as temp:
        png = Path(temp) / "image.png"
        png.write_bytes(raw)
        result = gdal.Translate(str(destination), str(png), outputSRS="EPSG:3067",
                                outputBounds=[ext.xMinimum(), ext.yMaximum(),
                                              ext.xMaximum(), ext.yMinimum()],
                                creationOptions=["TILED=YES", "COMPRESS=DEFLATE"])
        if result is None:
            raise RuntimeError("Oskari-karttakuvan GeoTIFF-tallennus epäonnistui")
        result = None
    (add_layer or _add_project_layer)(_output_descriptor(destination, entry["title"]))
    return 1


def _download_oskari_wmts(entry, mask, mask_crs, destination, add_layer=None):
    """Read the service's EPSG:3067 tile matrix through GDAL and save a GeoTIFF."""
    from osgeo import gdal
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    _transform(selected, QgsCoordinateTransform(mask_crs, target, _transform_context()))
    ext = selected.boundingBox()
    if ext.width() <= 0 or ext.height() <= 0:
        raise ValueError("Rajauksen laajuus ei riitä Oskari-WMTS:n lataukseen")
    capabilities = entry["endpoint"] + "?service=WMTS&request=GetCapabilities"
    container = source = result = None
    try:
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
        (add_layer or _add_project_layer)(_output_descriptor(destination, entry["title"]))
        return 1

    finally:
        result = source = container = None


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
                # overpass-api.de hylkää Pythonin oletus-User-Agentin (HTTP 406).
                request = urllib.request.Request(endpoint, data=payload, headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "User-Agent": OVERPASS_USER_AGENT,
                })
                with _urlopen(request, timeout=160) as response:
                    data = json.load(response)
                if data.get("remark") and "error" in data["remark"].lower():
                    raise RuntimeError(data["remark"])
                return data.get("elements", [])
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
    raise RuntimeError(f"Overpass-haku epäonnistui: {last_error}")


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


GEOMETRY_FAMILIES = ((0, "pisteet"), (1, "viivat"), (2, "alueet"))


def _download_osm(entry, mask, mask_crs, destination, progress=None, add_layer=None):
    add_layer = add_layer or _add_project_layer
    project = _transform_context()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    wgs_mask = QgsGeometry(mask)
    _transform(wgs_mask, QgsCoordinateTransform(mask_crs, wgs84, project))
    bounds = wgs_mask.boundingBox()
    # Split large extents to keep Overpass requests below common public limits.
    cols = max(1, math.ceil(bounds.width() / .5))
    rows = max(1, math.ceil(bounds.height() / .5))
    if cols * rows > 1000:
        raise ValueError("OSM-rajaus on liian suuri yhdelle ajolle; valitse pienempi alue")
    poi = entry["id"] == "osm_poi_points"
    seen = set()

    def pages():
        for row in range(rows):
            for col in range(cols):
                if progress:
                    progress(len(seen))
                west = bounds.xMinimum() + col * bounds.width() / cols
                east = bounds.xMinimum() + (col + 1) * bounds.width() / cols
                south = bounds.yMinimum() + row * bounds.height() / rows
                north = bounds.yMinimum() + (row + 1) * bounds.height() / rows
                bbox = f"{south},{west},{north},{east}"
                page = []
                for element in _overpass_fetch(_osm_query(entry, bbox)):
                    identity = (element.get("type"), element.get("id"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    geometry = element_geometry(element, poi)
                    if geometry is None:
                        continue
                    tags = element.get("tags") or {}
                    classes = _poi_classes(tags) if poi else [(None, None)]
                    for code, fclass in classes:
                        page.append({"geometry": geometry, "properties": {
                            "osm_id": str(element.get("id") or ""),
                            "osm_type": str(element.get("type") or ""),
                            "tags": json.dumps(tags, ensure_ascii=False), "code": code, "fclass": fclass}})
                yield page

    return _write_pages(entry, pages(), wgs84, mask, mask_crs, destination, progress, add_layer)


def _download_kapsi(entry, mask, mask_crs, destination, progress=None, add_layer=None):
    """Fetch Kapsi WMS in parallel tiles, mosaic to georeferenced GeoTIFF."""
    from osgeo import gdal, osr
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    selected = QgsGeometry(mask)
    _transform(selected, QgsCoordinateTransform(mask_crs, target, _transform_context()))
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
                with _urlopen(urllib.request.Request(url, headers={"Accept": "image/jpeg"}), timeout=180) as response:
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
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="suomenvaylat_kapsi_") as temp:
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
                                outputSRS="EPSG:3067",
                                creationOptions=["TILED=YES", "COMPRESS=JPEG", "PHOTOMETRIC=YCBCR"])
        if result is None:
            raise RuntimeError("Kapsi-GeoTIFFin kirjoitus epäonnistui")
        result = None
    (add_layer or _add_project_layer)(_output_descriptor(output_path, entry["title"]))
    return 1


def _ogc_field_kind(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _ogc_variant(kinds):
    kinds = {kind for kind in kinds if kind}
    if not kinds or "string" in kinds:
        return QVariant.String
    if kinds == {"bool"}:
        return QVariant.Bool
    if kinds == {"int"}:
        return QVariant.LongLong
    if kinds <= {"int", "float"}:
        return QVariant.Double
    return QVariant.String


def _ogc_value(value, variant):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if variant == QVariant.String:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if variant == QVariant.Double:
        return float(value)
    return value


def _download_ogc(entry, mask, mask_crs, destination, key="", progress=None, add_layer=None):
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    selected = QgsGeometry(mask)
    _transform(selected, QgsCoordinateTransform(mask_crs, wgs84, _transform_context()))
    bounds = selected.boundingBox()
    bbox = ",".join(str(v) for v in (bounds.xMinimum(), bounds.yMinimum(), bounds.xMaximum(), bounds.yMaximum()))
    base = urllib.parse.urljoin(entry["endpoint"],
                                f"collections/{urllib.parse.quote(entry['id'], safe='')}/items")
    first_url = base + "?" + urllib.parse.urlencode({"bbox": bbox, "limit": 1000, "f": "json"})

    def pages():
        url, visited, received, expected = first_url, set(), 0, None
        while url:
            if url in visited or len(visited) >= 10000:
                raise RuntimeError("OGC-sivutus pysähtyi; aineistoa ei tallennettu vajaana")
            if service_network.url_origin(url) != service_network.url_origin(first_url):
                raise RuntimeError("OGC:n sivulinkki osoittaa toiseen palveluun")
            visited.add(url)
            if progress:
                progress(received)
            data = _request_json(url, key)
            if not isinstance(data, dict) or not isinstance(data.get("features"), list):
                raise RuntimeError("OGC API ei palauttanut kohdekokoelmaa")
            total = wfs_pages.matched_count(data)
            if total is not None:
                if expected is not None and total != expected:
                    raise RuntimeError("OGC-aineiston kohdemäärä muuttui latauksen aikana")
                expected = total
            received += len(data["features"])
            yield data["features"]
            url = next((urllib.parse.urljoin(url, item["href"]) for item in data.get("links", [])
                        if item.get("rel") == "next" and item.get("href")), None)
        if expected is not None and received != expected:
            raise RuntimeError(f"OGC-lataus katkesi: saatiin {received}/{expected} kohdetta")

    return _write_pages(entry, pages(), wgs84, mask, mask_crs, destination, progress,
                        add_layer or _add_project_layer)
