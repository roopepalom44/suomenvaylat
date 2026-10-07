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
import urllib.error
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

from .osm_geometry import element_geometry
from .service_styles import StyleClient, MAX_STYLE_BYTES, localize_graphics, save_sld, StyleError

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
SENSITIVE_HEADERS = frozenset(["authorization", "proxy-authorization", "cookie"])


def _url_origin(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    scheme = (parsed.scheme or "").lower()
    port = parsed.port or {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower(), port


def _redirect_headers(old_url, new_url, headers):
    """Pudota tunnisteet toiselle palvelimelle ohjattaessa ja estä HTTPS→HTTP."""
    old_origin = _url_origin(old_url)
    new_origin = _url_origin(new_url)
    if old_origin[0] == "https" and new_origin[0] != "https":
        raise urllib.error.URLError("uudelleenohjaus HTTPS:stä salaamattomaan osoitteeseen estettiin")
    if old_origin == new_origin:
        return dict(headers or {})
    return {key: value for key, value in (headers or {}).items()
            if str(key).lower() not in SENSITIVE_HEADERS}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is None:
            return None
        safe = _redirect_headers(req.full_url, newurl, dict(new_request.header_items()))
        new_request.headers = {}
        new_request.unredirected_hdrs = {}
        for key, value in safe.items():
            new_request.add_header(key, value)
        return new_request


_SAFE_URL_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _urlopen(request, timeout=45):
    """``urlopen`` ilman tunnisteiden vuotoa uudelleenohjauksessa."""
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
OVERPASS_USER_AGENT = "Suomenvaylat-QGIS (+https://github.com/roopepalom44/suomenvaylat)"
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
    with _urlopen(request, timeout=45) as response:
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
            with _urlopen(request, timeout=45) as response:
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
                with _urlopen(url, timeout=45) as response:
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
            with _urlopen(url, timeout=45) as response:
                root = ET.fromstring(response.read())
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
            with _urlopen(url, timeout=60) as response:
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


def download(entry, mask, mask_crs, destination, key="", progress=None, add_layer=None):
    """Stream intersecting features into a GeoPackage in EPSG:3067.

    ``add_layer`` lisää valmiin tason projektiin. Taustatehtävä antaa oman
    funktionsa, joka siirtää tason pääsäikeeseen lisättäväksi.
    """
    project_add = add_layer or _add_project_layer

    def add_layer(layer):
        if isinstance(layer, QgsVectorLayer):
            _apply_provider_style(layer, entry, destination)
        project_add(layer)
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
            return _download_vector_layer(entry, layer, mask, mask_crs, destination, progress,
                                          add_layer, clip=True)
        finally:
            if layer is not None:
                from qgis.PyQt import sip
                sip.delete(layer)
            gdal.Unlink(path)
    layer = _open_remote_layer(entry)
    return _download_vector_layer(entry, layer, mask, mask_crs, destination, progress, add_layer,
                                  clip=entry.get("source") not in UNCLIPPED_WFS_SOURCES)


def _style_fetch(url):
    with _urlopen(urllib.request.Request(url), timeout=15) as response:
        data = response.read(MAX_STYLE_BYTES + 1)
    if len(data) > MAX_STYLE_BYTES:
        raise StyleError("Tyylivastaus on liian suuri")
    return data


def _apply_provider_style(layer, entry, destination):
    """Style before handing a background-created layer to the main thread."""
    from qgis.core import QgsMessageLog, Qgis
    path = Path(destination).with_suffix(".sld")
    try:
        result = StyleClient(_style_fetch).get(entry)
        if result is None:
            return
        root, endpoint = result
        # Keep the provider definition even when its icon is a server-local
        # file or otherwise unavailable to a desktop client.
        save_sld(root, path)
        localize_graphics(root, endpoint, path, _style_fetch)
        save_sld(root, path)
        message, ok = layer.loadSldStyle(str(path))
        if not ok:
            raise StyleError("QGIS ei voinut ottaa SLD-symboliikkaa käyttöön")
        layer.setCustomProperty("suomenvaylat/style_status", "Rajapinnan symboliikka")
        # GeoPackage stores the native renderer and labels, so reopening the
        # downloaded dataset also restores the style without a network request.
        message = layer.saveStyleToDatabase("Suomenväylät", "Rajapinnan oletustyyli", True, "")
        layer.saveNamedStyle(str(Path(destination).with_suffix(".qml")))
        if message:
            QgsMessageLog.logMessage("Tyylin GeoPackage-tallennus: " + message, "Suomenväylät", Qgis.Warning)
    except Exception as exc:
        # Raw HTTP exception URLs can contain an Aino token. Persist only the
        # curated error, never a service response or authenticated request URL.
        message = str(exc) if isinstance(exc, StyleError) else "Rajapinnan symboliikan käyttöönotto epäonnistui"
        layer.setCustomProperty("suomenvaylat/style_status", message)
        QgsMessageLog.logMessage(layer.name() + ": " + message, "Suomenväylät", Qgis.Warning)


def _download_vector_layer(entry, layer, mask, mask_crs, destination, progress=None,
                           add_layer=None, clip=True):
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
            if clip:
                geometry = _clip_geometry(geometry, target_mask)
                if geometry.isEmpty():
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
    (add_layer or _add_project_layer)(output)
    return count


def _download_oskari_wms(entry, mask, mask_crs, destination, add_layer=None):
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
    with _urlopen(request, timeout=180) as response:
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
    (add_layer or _add_project_layer)(layer)
    return 1


def _download_oskari_wmts(entry, mask, mask_crs, destination, add_layer=None):
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
    (add_layer or _add_project_layer)(layer)
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


def _write_feature_groups(destination, base_name, title, fields, groups, target_crs, add_layer):
    """Kirjoita geometriatyypeittäin ryhmitellyt kohteet samaan GeoPackageen.

    Jos tyyppejä on useita, jokaisesta tulee oma GeoPackage-taso ja
    projektitaso (kuten ArcGIS Pro -versiossa).
    """
    project = QgsProject.instance()
    non_empty = [(family, suffix, groups[family]) for family, suffix in GEOMETRY_FAMILIES
                 if groups.get(family)]
    written = []
    for index, (_family, suffix, features) in enumerate(non_empty):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = base_name if len(non_empty) == 1 else f"{base_name}_{suffix}"[:63]
        if index:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        writer = QgsVectorFileWriter.create(str(destination), fields, QgsWkbTypes.Unknown,
                                            target_crs, project.transformContext(), options)
        if writer.hasError() != QgsVectorFileWriter.NoError:
            error = writer.errorMessage()
            del writer
            raise RuntimeError(error)
        try:
            for feature in features:
                if not writer.addFeature(feature):
                    raise RuntimeError(writer.errorMessage())
        finally:
            del writer
        layer_title = title if len(non_empty) == 1 else f"{title} ({suffix})"
        written.append((options.layerName, layer_title))
    for layer_name, layer_title in written:
        layer = QgsVectorLayer(f"{destination}|layername={layer_name}", layer_title, "ogr")
        if not layer.isValid():
            raise RuntimeError(f"Tallennettu taso ei avaudu: {layer_title}")
        add_layer(layer)
    return len(written)


def _download_osm(entry, mask, mask_crs, destination, progress=None, add_layer=None):
    add_layer = add_layer or _add_project_layer
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    target = QgsCoordinateReferenceSystem("EPSG:3067")
    to_target = QgsCoordinateTransform(wgs84, target, project)
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
    groups = {0: [], 1: [], 2: []}
    count, seen = 0, set()
    poi = entry["id"] == "osm_poi_points"
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
                raw_geometry = element_geometry(element, poi)
                if raw_geometry is None:
                    continue
                geometry = QgsJsonUtils.geometryFromGeoJson(json.dumps(raw_geometry))
                if geometry.isEmpty() or not geometry.intersects(wgs_mask):
                    continue
                geometry.transform(to_target)
                if not geometry.intersects(target_mask):
                    continue
                geometry = _clip_geometry(geometry, target_mask)
                if geometry.isEmpty():
                    continue
                family = _geometry_type_value(geometry.type())
                if family not in groups:
                    continue
                tags = element.get("tags") or {}
                classes = _poi_classes(tags) if poi else [(None, None)]
                for code, fclass in classes:
                    feature = QgsFeature(fields)
                    feature.setAttributes([str(element.get("id") or ""), str(element.get("type") or ""),
                                           json.dumps(tags, ensure_ascii=False), code, fclass])
                    feature.setGeometry(geometry)
                    groups[family].append(feature)
                    count += 1
            if progress:
                progress(count)
    if not count:
        raise RuntimeError("Rajauksesta ei löytynyt OSM-kohteita")
    base_name = re.sub(r"[^\w]+", "_", entry["id"])[:60]
    try:
        _write_feature_groups(destination, base_name, entry["title"], fields, groups, target, add_layer)
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise
    return count


def _download_kapsi(entry, mask, mask_crs, destination, progress=None, add_layer=None):
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
                                outputSRS="EPSG:3067",
                                creationOptions=["TILED=YES", "COMPRESS=JPEG", "PHOTOMETRIC=YCBCR"])
        if result is None:
            raise RuntimeError("Kapsi-GeoTIFFin kirjoitus epäonnistui")
        result = None
    layer = QgsRasterLayer(str(output_path), entry["title"])
    if not layer.isValid():
        raise RuntimeError("Kapsi-rasteria ei voitu avata")
    if layer.crs().authid() != "EPSG:3067":
        raise RuntimeError("Kapsi-rasterin koordinaatisto ei tallentunut oikein")
    (add_layer or _add_project_layer)(layer)
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
    """Download all OGC API Features pages using the service's next links.

    Kentät päätellään kaikkien sivujen kohteista, joten myöhemmillä sivuilla
    esiintyvät ominaisuudet eivät katoa. Kohteet puskuroidaan väliaikaiseen
    tiedostoon ennen GeoPackagen kirjoitusta.
    """
    add_layer = add_layer or _add_project_layer
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    target_crs = QgsCoordinateReferenceSystem("EPSG:3067")
    to_target = QgsCoordinateTransform(wgs84, target_crs, project)
    wgs_mask = QgsGeometry(mask)
    wgs_mask.transform(QgsCoordinateTransform(mask_crs, wgs84, project))
    target_mask = QgsGeometry(mask)
    target_mask.transform(QgsCoordinateTransform(mask_crs, target_crs, project))
    bounds = wgs_mask.boundingBox()
    bbox = ",".join(str(v) for v in (bounds.xMinimum(), bounds.yMinimum(), bounds.xMaximum(), bounds.yMaximum()))
    base = urllib.parse.urljoin(entry["endpoint"],
                                f"collections/{urllib.parse.quote(entry['id'], safe='')}/items")
    url = base + "?" + urllib.parse.urlencode({"bbox": bbox, "limit": 1000, "f": "json"})
    field_kinds = {}
    count, pages = 0, 0
    visited = set()
    destination = str(destination)
    with tempfile.TemporaryDirectory(prefix="suomenvaylat_ogc_") as temp:
        spool_path = Path(temp) / "features.jsonl"
        with spool_path.open("w", encoding="utf-8") as spool:
            while url:
                if url in visited or pages >= 10000:
                    raise RuntimeError("OGC-sivutus pysähtyi; aineistoa ei tallennettu vajaana")
                visited.add(url)
                data = _request_json(url, key)
                for raw in data.get("features", []):
                    geom_data = raw.get("geometry")
                    if not geom_data:
                        continue
                    geometry = QgsJsonUtils.geometryFromGeoJson(json.dumps(geom_data))
                    if geometry.isEmpty() or not geometry.intersects(wgs_mask):
                        continue
                    geometry.transform(to_target)
                    if not geometry.intersects(target_mask):
                        continue
                    geometry = _clip_geometry(geometry, target_mask)
                    if geometry.isEmpty():
                        continue
                    properties = raw.get("properties") or {}
                    for name, value in properties.items():
                        field_kinds.setdefault(str(name), set()).add(_ogc_field_kind(value))
                    spool.write(json.dumps({"wkt": geometry.asWkt(), "properties": properties},
                                           ensure_ascii=False) + "\n")
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
        if count == 0:
            raise RuntimeError("Rajauksesta ei löytynyt kohteita")
        fields = QgsFields()
        variants = {}
        for name, kinds in field_kinds.items():
            variants[name] = _ogc_variant(kinds)
            fields.append(QgsField(name, variants[name]))
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = re.sub(r"[^\w]+", "_", entry["id"])[:60] or "data"
        writer = QgsVectorFileWriter.create(destination, fields, QgsWkbTypes.Unknown,
                                            target_crs, project.transformContext(), options)
        if writer.hasError() != QgsVectorFileWriter.NoError:
            error = writer.errorMessage()
            del writer
            raise RuntimeError(error)
        completed = False
        try:
            with spool_path.open("r", encoding="utf-8") as spool:
                for line in spool:
                    record = json.loads(line)
                    properties = record["properties"]
                    output = QgsFeature(fields)
                    output.setAttributes([
                        _ogc_value(properties.get(field.name()), variants[field.name()])
                        for field in fields
                    ])
                    output.setGeometry(QgsGeometry.fromWkt(record["wkt"]))
                    if not writer.addFeature(output):
                        raise RuntimeError(writer.errorMessage())
            completed = True
        finally:
            del writer
            if not completed:
                Path(destination).unlink(missing_ok=True)
    output = QgsVectorLayer(f"{destination}|layername={options.layerName}", entry["title"], "ogr")
    if not output.isValid():
        raise RuntimeError("Tallennettu taso ei avaudu")
    add_layer(output)
    return count
