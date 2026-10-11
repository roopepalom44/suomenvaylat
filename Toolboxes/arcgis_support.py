# -*- coding: utf-8 -*-
import arcpy
import urllib.request
import urllib.parse
import urllib.error
import http.client
import json
import os
import uuid
import time
import re
import socket
import ssl
import threading
import importlib.util


def _load_shared_module(name):
    # Installed add-in: beside this toolbox. Source checkout: canonical module
    # in the QGIS package. Both packages contain the very same implementation.
    here = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(here, name + ".py")
    if not os.path.isfile(module_path):
        module_path = os.path.join(os.path.dirname(here), "qgis_plugin", "suomenvaylat_qgis", name + ".py")
    spec = importlib.util.spec_from_file_location("suomenvaylat_" + name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service_styles = _load_shared_module("service_styles")
service_network = _load_shared_module("service_network")
osm_geometry = _load_shared_module("osm_geometry")


# MML:n nykyiset avoimet rajapinnat. Kiinteistöaineistot haetaan OGC API
# Features -palvelusta ja karttatasot lisätään ArcGIS Prohon TileJSON-vektori-
# tiilinä.
MML_PROPERTY_OGC_API_ENDPOINT = (
    "https://avoin-paikkatieto.maanmittauslaitos.fi/"
    "kiinteisto-avoin/simple-features/v3/"
)
MML_PROPERTY_VECTOR_TILE_TILEJSON = (
    "https://avoin-karttakuva.maanmittauslaitos.fi/"
    "kiinteisto-avoin/v3/kiinteistojaotus/ETRS-TM35FIN/tilejson.json"
)
MML_TOPO_VECTOR_TILE_TILEJSON = (
    "https://avoin-karttakuva.maanmittauslaitos.fi/"
    "vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/"
    "v21/ETRS-TM35FIN/tilejson.json"
)
MML_PROPERTY_COLLECTION_LABELS = {
    # Säilytä vanhan työkalun tuttu nimi, vaikka palvelun virallinen kokoelma
    # on nimetty KiinteistorajanSijaintitiedot.
    "KiinteistorajanSijaintitiedot": "Kiinteistojaotus",
}
MML_VECTOR_TILE_LAYER_IDS = {
    "Taustakartta": MML_TOPO_VECTOR_TILE_TILEJSON,
    # Sama tiiliaineisto, mutta valittu esitystyyli haetaan stylejson-palvelusta.
    "Maastokartta": MML_TOPO_VECTOR_TILE_TILEJSON,
    "Kiinteistojaotus": MML_PROPERTY_VECTOR_TILE_TILEJSON,
}

# ETRS-TM35FIN, johon kaikki paikalliset rajaukset ja tulokset projisoidaan.
EPSG_TM35FIN = 3067

TRAFICOM_OPEN_WFS_ENDPOINT = "https://julkinen.traficom.fi/inspirepalvelu/avoin/wfs"
TRAFICOM_RAJOITETTU_WFS_ENDPOINT = "https://julkinen.traficom.fi/inspirepalvelu/rajoitettu/wfs"
TRAFICOM_ILMALIIKENNE_WFS_ENDPOINT = "https://julkinen.traficom.fi/inspirepalvelu/ilmaliikenne/wfs"
TRAFICOM_WMTS_ENDPOINT = "https://julkinen.traficom.fi/rasteripalvelu/wmts?service=WMTS&request=GetCapabilities"


class PhaseMetrics(object):
    """Monotoniseen kelloon perustuva vaihekirjanpito.

    None tarkoittaa, ettei vaihetta suoritettu. Näin loki ei sekoita
    käyttämätöntä vaihetta aidosti hyvin nopeaan (0,0 s) vaiheeseen.
    """

    def __init__(self):
        self.seconds = {}
        self.status = {}

    def add(self, name, elapsed):
        self.seconds[name] = self.seconds.get(name, 0.0) + max(0.0, float(elapsed))
        self.status.pop(name, None)

    def set(self, name, elapsed):
        self.seconds[name] = max(0.0, float(elapsed))
        self.status.pop(name, None)

    def skip(self, name, reason="ei käytetty"):
        if name not in self.seconds:
            self.seconds[name] = None
            self.status[name] = reason

    def get(self, name, default=None):
        return self.seconds.get(name, default)

class CQLRequestRejected(Exception):
    """CQL-pyyntö epäonnistui; kutsuja voi kokeilla pienempiä CQL-osia."""


# Uudelleenyritettävät HTTP-tilakoodit. 4xx-virheitä ei yritetä uudelleen:
# esimerkiksi 400 ja 414 ovat kutsujalle merkitseviä signaaleja (liian pitkä
# CQL_FILTER), joiden varareitit hoidetaan ylempänä.
RETRYABLE_HTTP_STATUS = frozenset([408, 425, 429, 500, 502, 503, 504])
WFS_PAGE_TIMEOUT_S = 300
# Jätä tilaa palvelimen URL-rajaan: tarkista koko koodattu GET-osoite.
WFS_CQL_GET_MAX_URL_LENGTH = 6500
FORMAT_INDEPENDENT_HTTP_STATUS = frozenset([401, 403, 414])
RETRYABLE_NETWORK_ERRORS = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.CannotSendRequest,
    http.client.ResponseNotReady,
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    socket.timeout,
    OSError,
)


class HttpTransport(object):
    """Säiekohtainen HTTP-yhteyspooli, joka käyttää yhteyksiä uudelleen.

    urllib avaa jokaiselle pyynnölle uuden TCP+TLS-yhteyden. Sivutetussa
    WFS-haussa se tarkoittaa kymmeniä turhia kättelyitä. Tämä luokka pitää
    yhteyden auki hostia kohti ja palaa urllibiin, jos jokin menee pieleen.

    Pooli on ``threading.local``, joten rinnakkaiset sivuhaut eivät jaa
    samaa socketia.
    """

    MAX_REDIRECTS = 5

    def __init__(self):
        self._local = threading.local()

    def _pool(self):
        pool = getattr(self._local, "pool", None)
        if pool is None:
            pool = {}
            self._local.pool = pool
        return pool

    def _connection(self, scheme, host, port, timeout):
        key = (scheme, host, port, timeout)
        pool = self._pool()
        conn = pool.get(key)
        if conn is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=timeout)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
            pool[key] = conn
        return key, conn

    def _drop(self, key):
        conn = self._pool().pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        pool = self._pool()
        for key in list(pool.keys()):
            self._drop(key)

    def request(self, url, method="GET", headers=None, body=None, timeout=60):
        """Palauta (status, headers, body_bytes, read_seconds).

        Nostaa poikkeuksen verkkovirheessä. ``read_seconds`` erottelee
        vastauksen lukemisen verkkopyynnön kokonaisajasta, jotta työkalun
        vaihekohtainen loki säilyy yhtä tarkkana kuin urllib-toteutuksessa.
        """
        current_url = url
        headers = dict(headers or {})
        for _ in range(self.MAX_REDIRECTS + 1):
            status, resp_headers, payload, location, read_s = self._single_request(
                current_url, method, headers, body, timeout
            )
            if status in (301, 302, 303, 307, 308) and location:
                next_url = urllib.parse.urljoin(current_url, location)
                headers = _redirect_headers(current_url, next_url, headers)
                current_url = next_url
                if status in (301, 302, 303) and method == "POST":
                    # 303 (ja käytännössä 301/302) muuttaa POSTin GETiksi.
                    method = "GET"
                    body = None
                continue
            return status, resp_headers, payload, read_s
        raise urllib.error.URLError("liian monta uudelleenohjausta: {}".format(url))

    def _single_request(self, url, method, headers, body, timeout):
        parsed = urllib.parse.urlsplit(url)
        scheme = (parsed.scheme or "https").lower()
        if scheme not in ("http", "https"):
            raise urllib.error.URLError("tuntematon protokolla: {}".format(scheme))
        host = parsed.hostname or ""
        port = parsed.port
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))

        send_headers = {"Connection": "keep-alive", "Accept-Encoding": "gzip, deflate"}
        send_headers.update(headers or {})
        send_headers.setdefault("Host", parsed.netloc.split("@")[-1])

        # Vanhentunut keep-alive-socket ei ole virhe vaan normaali tilanne:
        # ensimmäinen yritys uusitaan aina kerran tuoreella yhteydellä.
        last_error = None
        for attempt in range(2):
            key, conn = self._connection(scheme, host, port, timeout)
            try:
                conn.request(method, target, body=body, headers=send_headers)
                response = conn.getresponse()
                read_start = time.perf_counter()
                payload = service_network.decode_body(response.read(service_network.MAX_RESPONSE_BYTES + 1),
                                                      response.headers.get("Content-Encoding"))
                read_s = time.perf_counter() - read_start
                status = response.status
                resp_headers = response.headers
                location = response.headers.get("Location")
                if response.will_close or _header_says_close(response.headers):
                    self._drop(key)
                return status, resp_headers, payload, location, read_s
            except RETRYABLE_NETWORK_ERRORS as ex:
                last_error = ex
                self._drop(key)
                if attempt == 0 and _is_retryable_error(ex):
                    continue
                raise
            except Exception:
                self._drop(key)
                raise
        raise last_error if last_error else urllib.error.URLError("tuntematon verkkovirhe")


SENSITIVE_HEADERS = service_network.SENSITIVE_HEADERS
_url_origin = service_network.url_origin
_redirect_headers = service_network.redirect_headers
_SafeRedirectHandler = service_network.SafeRedirectHandler

_SAFE_URL_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _urlopen(request, timeout=60):
    """``urllib.request.urlopen`` turvallisella uudelleenohjauksella."""
    return _SAFE_URL_OPENER.open(request, timeout=timeout)


def _is_retryable_error(ex):
    """Uudelleenyritä vain ohimeneviä verkkovirheitä.

    Varmennevirhe tai HTTP 4xx ei korjaannu uudella yrityksellä.
    """
    if isinstance(ex, urllib.error.HTTPError):
        return ex.code in RETRYABLE_HTTP_STATUS
    if isinstance(ex, ssl.SSLCertVerificationError):
        return False
    if isinstance(ex, urllib.error.URLError) and isinstance(
        getattr(ex, "reason", None), ssl.SSLCertVerificationError
    ):
        return False
    return isinstance(ex, RETRYABLE_NETWORK_ERRORS)


def _header_says_close(headers):
    try:
        return "close" in (headers.get("Connection", "") or "").lower()
    except Exception:
        return False


# =================================
# HELPER CLASSES
# =================================

class WFSSourceRegistry(object):
    """Registry for supported WFS-like sources."""

    def __init__(self):
        self.sources = {
            "Väylä": {
                "type": "wfs",
                "endpoints": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/ows"],
                "description": "Väyläviraston WFS-aineistot"
            },
            "DigiRoad": {
                "type": "wfs",
                "endpoints": ["https://avoinapi.vaylapilvi.fi/vaylatiedot/digiroad/ows"],
                "description": "Digiroad-tasot"
            },
            "Traficom Oskari": {
                "type": "oskari",
                "endpoints": ["https://julkinen.traficom.fi/oskari/action"],
                "description": "Oskarin WFS-, WMS- ja WMTS-karttatasot"
            },
            "Liiteri": {
                "type": "wfs",
                "endpoints": [
                    "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_asuinalueet/wfs",
                    "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_etaisyysvyohykkeet/wfs",
                    "https://paikkatiedot.ymparisto.fi/geoserver/liiteri_taajamat/wfs"
                ],
                "description": "Liiteri-aineistot (SYKE / Ympäristö)"
            },
            "Syke": {
                "type": "wfs",
                "endpoints": [
                    "https://paikkatiedot.ymparisto.fi/geoserver/inspire_ps/wfs"
                ],
                "description": "SYKE INSPIRE Protected Sites"
            },
            "Tilastokeskus": {
                "type": "wfs",
                # Globaali GeoServer-WFS kattaa kaikki työtilat yhdellä
                # GetCapabilitiesilla: tilastointialueet, postinumeroalueet,
                # väestöalueet ja -ruudut, tieliikenneonnettomuudet ja
                # oppilaitokset. Mitattuna 5000 kohteen GeoJSON-sivu 0,1-0,9 s;
                # INSPIRE OGC API (/inspire/ogc/api/su) oli samoille alueille
                # 10-40x hitaampi, joten sitä ei käytetä.
                "endpoints": ["https://geo.stat.fi/geoserver/wfs"],
                "exclude_prefixes": ["testi:"],
                "description": "Tilastokeskuksen paikkatietoaineistot (WFS)"
            },
            "Aino": {
                "type": "aino",
                # Sitowise Aino julkaisee samasta osoitteesta WFS 1.1.0- ja
                # WMS 1.3.0 -rajapinnat. Käyttäjän token lisätään vain
                # ajonaikaisiin pyyntöihin eikä sitä tallenneta lähdekoodiin
                # tai tasoluettelon levyvälimuistiin.
                "endpoints": ["https://aino.sitowise.com/ows"],
                "description": "Sitowise Aino WFS + WMS"
            },
            "Karttapaikka": {
                "type": "mml_combined",
                "endpoints": [
                    # Maanmittauslaitoksen nykyiset INSPIRE WFS -palvelut.
                    # Aiemmat avoin-karttakuva.fi- ja geoserver/maastotiedot/wfs-
                    # osoitteet palauttavat nykyisin 404:n.
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/au/ows",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/bu_mtk_point",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/bu_mtk_polygon",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/cp/ows",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/gn",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/hy",
                    "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/mu/ows"
                ],
                # Maastotietokannan uusi OGC API Features -rajapinta sisältää
                # myös liikenneverkkoja, rakennuksia ja rakenteita. Se tarvitsee
                # käyttäjän API-avaimen ja lisätään listaan vain avaimen ollessa
                # käytettävissä.
                "ogc_api_endpoint": "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
                "description": "MML INSPIRE WFS + Maastotiedot OGC API Features"
            },
            "MML": {
                "type": "mml_ogcapi",
                "endpoints": [MML_PROPERTY_OGC_API_ENDPOINT],
                "ogc_api_endpoint": MML_PROPERTY_OGC_API_ENDPOINT,
                "vector_tile_endpoint": MML_PROPERTY_VECTOR_TILE_TILEJSON,
                "description": "MML kiinteistöaineistot (OGC API Features + vector tiles)"
            },
            "Kapsi": {
                "type": "kapsi_wms",
                "endpoints": [
                    "https://tiles.kartat.kapsi.fi/peruskartta?SERVICE=WMS&REQUEST=GetCapabilities",
                    "https://tiles.kartat.kapsi.fi/taustakartta?SERVICE=WMS&REQUEST=GetCapabilities",
                    "https://tiles.kartat.kapsi.fi/ortokuva?SERVICE=WMS&REQUEST=GetCapabilities"
                ],
                "description": "Kapsi WMS taustakartat"
            },
            "OpenStreetMap": {
                "type": "overpass",
                # Julkiset Overpass-palvelut voivat olla hetkellisesti
                # ruuhkautuneita. Kutsu yrittää osoitteita järjestyksessä
                # ja vaihtaa automaattisesti varapalveluun.
                "endpoints": [
                    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                    "https://overpass-api.de/api/interpreter"
                ],
                "description": "OpenStreetMap Overpass API"
            },
            "MML Karttakuva": {
                "type": "mml_karttakuva",
                "endpoints": ["https://karttakuva.maanmittauslaitos.fi/maasto/wmts/1.0.0/WMTSCapabilities.xml"],
                "description": "MML Karttakuva WMTS (maksullinen, vaatii tunnukset)"
            }
        }
        self._capabilities_cache = {}

    def get_sources_list(self):
        return [
            "Väylä",
            "DigiRoad",
            "Traficom Oskari",
            "MML",
            "MML Karttakuva",
            "Kapsi",
            "Liiteri",
            "Syke",
            "Tilastokeskus",
            "Aino",
            "Karttapaikka",
            "OpenStreetMap"
        ]

    def get_source(self, source_name):
        if source_name not in self.sources:
            raise KeyError("Unknown source: {}".format(source_name))
        return self.sources[source_name]

    def get_endpoint(self, source_name):
        endpoints = self.get_source(source_name).get("endpoints") or []
        return endpoints[0] if endpoints else None

    def get_endpoints(self, source_name):
        return list(self.get_source(source_name).get("endpoints") or [])

    def get_ogc_endpoint(self, source_name):
        """Palauta lähteen mahdollinen OGC API Features -juuri."""
        return self.get_source(source_name).get("ogc_api_endpoint")

    def get_type(self, source_name):
        return self.get_source(source_name).get("type", "wfs")

    def get_capabilities(self, source_name):
        if source_name in self._capabilities_cache:
            return self._capabilities_cache[source_name]

        source = self.get_source(source_name)
        endpoints = source.get("endpoints") or []
        if not endpoints or source.get("type") != "wfs":
            self._capabilities_cache[source_name] = []
            return []

        layers = []
        seen = set()
        excluded = tuple(prefix.lower() for prefix in source.get("exclude_prefixes") or [])
        for endpoint in endpoints:
            try:
                sep = "&" if "?" in endpoint else "?"
                endpoint_layers = self._parse_capabilities("{}{}service=WFS&request=GetCapabilities".format(endpoint, sep))
            except Exception:
                endpoint_layers = []
            for lyr in endpoint_layers:
                lid = lyr.get("id")
                if lid and excluded and lid.lower().startswith(excluded):
                    continue
                if lid and lid not in seen:
                    seen.add(lid)
                    layers.append(lyr)
        self._capabilities_cache[source_name] = layers
        return layers

    def _parse_capabilities(self, url):
        layers = []
        req = urllib.request.Request(url)
        with _urlopen(req, timeout=60) as response:
            xml_bytes = response.read(service_network.MAX_XML_BYTES + 1)
        root = service_network.checked_xml(xml_bytes)

        feature_types = [e for e in root.iter() if e.tag.endswith('FeatureType')]
        for elem in feature_types:
            name_text = None
            title_text = None
            for child in elem:
                if child.tag.endswith("Name") and child.text and not name_text:
                    name_text = child.text.strip()
                elif child.tag.endswith("Title") and child.text and not title_text:
                    title_text = child.text.strip()
            if name_text:
                layers.append({
                    "id": name_text,
                    "title": title_text or name_text.split(":")[-1],
                    "source": None,
                    "kind": "wfs"
                })
        return layers


# =================================
# OSM-GEOMETRIAT
# =================================
# Both packages load the canonical osm_geometry module.
osm_is_area_way = osm_geometry.is_area_way
osm_element_geometry = osm_geometry.element_geometry


class OverpassAdapter(object):
    """Static OSM layer catalog and Overpass query generation."""

    LAYERS = [
        {"id": "osm_fences", "title": "Aidat", "query": '(way["barrier"="fence"]({bbox}););out geom;'},
        {"id": "osm_addresses", "title": "Osoitteet", "query": '(node["addr:housenumber"]({bbox});way["addr:housenumber"]({bbox}););out geom;'},
        {"id": "osm_admin", "title": "Hallinnolliset alueet", "query": '(relation["boundary"="administrative"]({bbox}););out geom;'},
        {"id": "osm_buildings", "title": "Rakennukset", "query": '(way["building"]({bbox});relation["building"]({bbox}););out geom;'},
        {"id": "osm_bridges", "title": "Sillat", "query": '(way["bridge"]({bbox}););out geom;'},
        {"id": "osm_bus_stops", "title": "Bussipysakit", "query": '(node["highway"="bus_stop"]({bbox}););out geom;'},
        {"id": "osm_cycleways", "title": "Pyoratiet", "query": '(way["highway"="cycleway"]({bbox}););out geom;'},
        {"id": "osm_fire_stations", "title": "Paloasemat", "query": '(node["amenity"="fire_station"]({bbox});way["amenity"="fire_station"]({bbox}););out geom;'},
        {"id": "osm_forests", "title": "Metsat", "query": '(way["landuse"="forest"]({bbox});relation["landuse"="forest"]({bbox}););out geom;'},
        {"id": "osm_healthcare", "title": "Terveyspalvelut", "query": '(node["amenity"="hospital"]({bbox});node["amenity"="clinic"]({bbox});way["amenity"="hospital"]({bbox});way["amenity"="clinic"]({bbox}););out geom;'},
        {"id": "osm_roads", "title": "Tiet", "query": '(way["highway"]({bbox}););out geom;'},
        {"id": "osm_roads_major", "title": "Paavaylat", "query": '(way["highway"~"motorway|trunk|primary|secondary"]({bbox}););out geom;'},
        {"id": "osm_railways", "title": "Rautatiet", "query": '(way["railway"]({bbox}););out geom;'},
        {"id": "osm_landuse", "title": "Maankaytto", "query": '(way["landuse"]({bbox});relation["landuse"]({bbox}););out geom;'},
        {"id": "osm_nature", "title": "Luonnonsuojelu", "query": '(relation["boundary"="protected_area"]({bbox});way["boundary"="protected_area"]({bbox}););out geom;'},
        {"id": "osm_parking", "title": "Pysakointi", "query": '(way["amenity"="parking"]({bbox});node["amenity"="parking"]({bbox}););out geom;'},
        {"id": "osm_parks", "title": "Puistot", "query": '(way["leisure"="park"]({bbox});relation["leisure"="park"]({bbox}););out geom;'},
        {"id": "osm_poi_points", "title": "POI-pisteet", "query": None},
        {"id": "osm_poi", "title": "Palvelupisteet", "query": '(node["amenity"]({bbox});way["amenity"]({bbox}););out geom;'},
        {"id": "osm_power", "title": "Sahkoverkko", "query": '(way["power"]({bbox});node["power"]({bbox}););out geom;'},
        {"id": "osm_schools", "title": "Koulut", "query": '(node["amenity"="school"]({bbox});way["amenity"="school"]({bbox}););out geom;'},
        {"id": "osm_shops", "title": "Kaupat", "query": '(node["shop"]({bbox});way["shop"]({bbox}););out geom;'},
        {"id": "osm_sport", "title": "Urheilupaikat", "query": '(way["leisure"="sports_centre"]({bbox});way["leisure"="pitch"]({bbox});node["leisure"="sports_centre"]({bbox}););out geom;'},
        {"id": "osm_trails", "title": "Polut", "query": '(way["highway"="path"]({bbox});way["highway"="footway"]({bbox}););out geom;'},
        {"id": "osm_tourism", "title": "Matkailukohteet", "query": '(node["tourism"]({bbox});way["tourism"]({bbox}););out geom;'},
        {"id": "osm_water", "title": "Vesistot", "query": '(way["waterway"]({bbox});way["natural"="water"]({bbox});relation["natural"="water"]({bbox}););out geom;'},
        {"id": "osm_wood", "title": "Puumetsat", "query": '(way["natural"="wood"]({bbox});relation["natural"="wood"]({bbox}););out geom;'}
    ]

    @classmethod
    def get_layers(cls):
        out = []
        for item in cls.LAYERS:
            entry = dict(item)
            entry["source"] = "OpenStreetMap"
            entry["kind"] = "osm"
            out.append(entry)
        return out

    @classmethod
    def build_query(cls, layer_id, bbox_4326):
        if layer_id == GeofabrikPOIAdapter.LAYER_ID:
            return GeofabrikPOIAdapter.build_query(bbox_4326)
        for item in cls.LAYERS:
            if item["id"] == layer_id:
                return "[out:json][timeout:120];{}".format(item["query"].format(bbox=bbox_4326))
        raise KeyError("Unknown OSM layer id: {}".format(layer_id))

    @staticmethod
    def to_geojson(overpass_json):
        features = []
        for element in overpass_json.get("elements", []):
            geometry = OverpassAdapter._element_geometry(element)
            if geometry is None:
                continue
            props = dict(element.get("tags", {}))
            props["osm_id"] = element.get("id")
            props["osm_type"] = element.get("type")
            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": props
            })
        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def _element_geometry(element):
        return osm_element_geometry(element)


class GeofabrikPOIAdapter(object):
    """Geofabrikin POI-luokitusta vastaava yhdistetty OSM-pistetaso.

    Geofabrikin ``pois`` sisältää OSM:ssä pisteinä/viivoina kuvatut
    kohteet ja ``pois_a`` vastaavat alueet. Overpassin ``out center`` muuttaa
    way/relation-kohteet pisteiksi, joten yksi POI-pistetaso voi sisältää
    molemmat esitystavat.
    """

    LAYER_ID = "osm_poi_points"

    # (OSM-avain, OSM-arvo, Geofabrik code, fclass). Sama kohde voi osua
    # useaan luokkaan; silloin siitä tehdään yksi feature kutakin luokkaa
    # kohti, kuten Geofabrikin tutkitussa aineistossa.
    _poi_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osm_poi_classes.json")
    if not os.path.isfile(_poi_path):
        _poi_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "qgis_plugin", "suomenvaylat_qgis", "osm_poi_classes.json")
    with open(_poi_path, "r", encoding="utf-8") as _poi_file:
        TAG_CLASSES = [tuple(row) for row in json.load(_poi_file)]

    @classmethod
    def _query_values_by_key(cls):
        values = {}
        for key, value, _, _ in cls.TAG_CLASSES:
            values.setdefault(key, set()).add(value)
        extras = {
            "amenity": {"recycling", "vending_machine"},
            "office": {"diplomatic"},
            "landuse": {"cemetery"},
            "man_made": {"tower"},
        }
        for key, extra_values in extras.items():
            values.setdefault(key, set()).update(extra_values)
        return values

    @classmethod
    def build_query(cls, bbox_4326):
        selectors = []
        # Näiden yleisten avainten olemassaolohaku on Overpassissa selvästi
        # nopeampi kuin kymmenien vaihtoehtojen arvo-regex. Tuntemattomat
        # arvot suodatetaan pois classify-vaiheessa.
        broad_keys = {"amenity", "historic", "leisure", "shop", "tourism"}
        for key, values in sorted(cls._query_values_by_key().items()):
            if key in broad_keys:
                selectors.append('nwr["{}"]({});'.format(key, bbox_4326))
                continue
            if len(values) == 1:
                selectors.append('nwr["{}"="{}"]({});'.format(
                    key, next(iter(values)), bbox_4326
                ))
                continue
            # Overpass käyttää POSIX-regexiä, jossa Pythonin
            # non-capturing-ryhmä ``(?:...)`` ei ole sallittu.
            pattern = "^({})$".format("|".join(sorted(re.escape(v) for v in values)))
            selectors.append(
                'nwr["{}"~"{}"]({});'.format(key, pattern, bbox_4326)
            )
        return "[out:json][timeout:120];({});out center;".format("".join(selectors))

    @classmethod
    def classify(cls, tags):
        """Palauta kaikki Geofabrik-luokat ilman saman luokan duplikaatteja."""
        tags = tags or {}
        classes = []

        def add(value):
            if value and value not in classes:
                classes.append(value)

        for key, value, code, fclass in cls.TAG_CLASSES:
            if str(tags.get(key, "")) == value:
                add((code, fclass))

        if tags.get("office") == "diplomatic":
            if tags.get("diplomatic") == "consulate":
                add((2017, "consulate"))
            elif tags.get("diplomatic") == "embassy":
                add((2011, "embassy"))
        if tags.get("landuse") == "cemetery":
            add((2015, "graveyard"))

        if tags.get("amenity") == "recycling":
            recycling_classes = (
                ("recycling:glass", 2031, "recycling_glass"),
                ("recycling:glass_bottles", 2031, "recycling_glass"),
                ("recycling:paper", 2032, "recycling_paper"),
                ("recycling:clothes", 2033, "recycling_clothes"),
                ("recycling:scrap_metal", 2034, "recycling_metal"),
            )
            specific = None
            for tag_name, code, fclass in recycling_classes:
                if tags.get(tag_name) == "yes":
                    specific = (code, fclass)
                    break
            add(specific or (2030, "recycling"))

        if tags.get("amenity") == "vending_machine":
            if tags.get("vending") == "parking_tickets":
                add((2592, "vending_parking"))
            else:
                add((2590, "vending_machine"))

        if tags.get("man_made") == "tower":
            tower_type = tags.get("tower:type")
            if tower_type == "communication":
                add((2951, "comms_tower"))
            elif tower_type == "observation":
                add((2953, "observation_tower"))
            else:
                add((2950, "tower"))
        return classes

    @staticmethod
    def _point_geometry(element):
        if element.get("type") == "node":
            lon, lat = element.get("lon"), element.get("lat")
        else:
            center = element.get("center") or {}
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            return None
        return {"type": "Point", "coordinates": [lon, lat]}

    @classmethod
    def to_geojson(cls, overpass_json):
        features = []
        for element in overpass_json.get("elements", []) or []:
            geometry = cls._point_geometry(element)
            if geometry is None:
                continue
            tags = element.get("tags") or {}
            for code, fclass in cls.classify(tags):
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        "osm_id": str(element.get("id") or ""),
                        "osm_type": str(element.get("type") or ""),
                        "code": code,
                        "fclass": fclass,
                        "name": str(tags.get("name") or ""),
                    },
                })
        return {"type": "FeatureCollection", "features": features}


class ShapefileFieldConverter(object):
    """Convert fields that are not safe for shapefile output."""

    @staticmethod
    def convert_feature_class(fc_path, workspace_is_folder=False):
        if not workspace_is_folder:
            return fc_path, 0

        unsupported = {}
        for fld in arcpy.ListFields(fc_path):
            if fld.required:
                continue
            safe_type = ShapefileFieldConverter._safe_type(fld.type)
            if safe_type != fld.type:
                unsupported[fld.name] = safe_type

        if not unsupported:
            return fc_path, 0

        temp_fc = os.path.join(arcpy.env.scratchGDB, "shp_safe_{}".format(uuid.uuid4().hex[:10]))
        arcpy.management.CopyFeatures(fc_path, temp_fc)
        used = {field.name[:10].casefold() for field in arcpy.ListFields(temp_fc) if field.name not in unsupported}
        mappings = []
        for index, (field_name, safe_type) in enumerate(unsupported.items()):
            temp_name = "sv_{}".format(uuid.uuid4().hex[:8])
            arcpy.management.AddField(temp_fc, temp_name, safe_type, field_length=255 if safe_type == "TEXT" else None)
            base, target, suffix = field_name[:10], field_name[:10], 1
            while target.casefold() in used:
                tail = "_{}".format(suffix)
                target = base[:10 - len(tail)] + tail
                suffix += 1
            used.add(target.casefold())
            mappings.append((field_name, temp_name, target, safe_type))
        names = [item[0] for item in mappings] + [item[1] for item in mappings]
        with arcpy.da.UpdateCursor(temp_fc, names) as cursor:
            for row in cursor:
                for index, (_, _, _, kind) in enumerate(mappings):
                    value = row[index]
                    row[index + len(mappings)] = None if value is None else str(value)[:254] if kind == "TEXT" else value
                cursor.updateRow(row)
        arcpy.management.DeleteField(temp_fc, [item[0] for item in mappings])
        for _, temp_name, target, _ in mappings:
            arcpy.management.AlterField(temp_fc, temp_name, target, target)
        return temp_fc, len(unsupported)

    @staticmethod
    def _safe_type(field_type):
        mapping = {
            "GUID": "TEXT",
            "GlobalID": "TEXT",
            "Blob": "TEXT",
            "Raster": "TEXT",
            "XML": "TEXT",
            "DateOnly": "TEXT",
            "TimeOnly": "TEXT",
            "TimestampOffset": "TEXT",
            "BigInteger": "DOUBLE"
        }
        return mapping.get(field_type, field_type)


class ResilienceStrategy(object):
    """Fallback runner: try full extent, then smaller batches and finer grids."""

    def __init__(self, max_batch_size=10000, grid_levels=None, progress_callback=None,
                 cleanup_callback=None):
        self.max_batch_size = max_batch_size
        self.grid_levels = grid_levels or [1, 2, 4]
        self._progress = progress_callback  # callable(msg_str) or None
        self._cleanup = cleanup_callback  # callable(dataset_path) or None

    def _log(self, msg):
        if self._progress:
            self._progress(msg)

    def _discard(self, chunks):
        """Poista keskeneräisen ruudukkotason väliaineistot."""
        for chunk in chunks or []:
            if self._cleanup is None:
                break
            try:
                self._cleanup(chunk)
            except Exception:
                pass

    def execute_with_fallback(self, fetch_func, initial_bbox):
        last_grid = self.grid_levels[-1]
        for grid_size in self.grid_levels:
            # Ensimmäinen yritys käyttää täyttä sivukokoa. Aiemmin jakaja
            # grid_size * 2 puolitti sivun myös koko alueen haussa, mikä
            # tuplasi raskaiden palvelujen (DigiRoad ~60-90 s/sivu) pyynnöt.
            divisor = 1 if grid_size <= 1 else grid_size * 2
            batch_size = max(100, int(self.max_batch_size / divisor))
            bboxes = [initial_bbox] if grid_size == 1 else list(self._split_bbox(initial_bbox, grid_size))
            total_tiles = len(bboxes)
            current_chunks = []
            current_found = 0
            try:
                for tile_idx, bbox in enumerate(bboxes, 1):
                    if total_tiles > 1:
                        self._log("  [INFO] Ruutu {}/{} (ruudukko {}x{}, batch_size={})...".format(
                            tile_idx, total_tiles, grid_size, grid_size, batch_size))
                    chunks, found = fetch_func(bbox, batch_size)
                    current_chunks.extend(chunks)
                    current_found += found
                    if total_tiles > 1:
                        self._log("  [INFO] Ruutu {}/{} valmis, kohteita tähän mennessä: {}".format(
                            tile_idx, total_tiles, current_found))
            except Exception:
                # Tämä ruudukkotaso epäonnistui (esim. liian iso pyyntö /
                # aikakatkaisu) -> kokeile hienompaa ruudukkoa, tai nosta
                # poikkeus jos tämä oli viimeinen taso.
                #
                # Keskeneräisen tason väliaineistot poistetaan aina: muuten ne
                # jäisivät scratch-GDB:hen paisuttamaan sitä koko ajon ajaksi.
                self._discard(current_chunks)
                if grid_size == last_grid:
                    raise
                self._log("  [INFO] Ruudukko {}x{} epäonnistui, kokeillaan hienompaa...".format(grid_size, grid_size))
                continue
            # Ruudukkotaso valmistui teknisesti onnistuneesti. Hyväksy tulos
            # myös kun kohteita ei löytynyt — tyhjä alue on validi vastaus, eikä
            # samaa aluetta kannata hakea uudelleen yhä hienommalla ruudukolla.
            return current_chunks, current_found, grid_size
        return [], 0, last_grid

    @staticmethod
    def _split_bbox(bbox_str, grid_size):
        xmin, ymin, xmax, ymax = [float(x) for x in bbox_str.split(",")[:4]]
        dx = (xmax - xmin) / float(grid_size)
        dy = (ymax - ymin) / float(grid_size)
        for ix in range(grid_size):
            for iy in range(grid_size):
                txmin = xmin + (ix * dx)
                tymin = ymin + (iy * dy)
                txmax = txmin + dx
                tymax = tymin + dy
                yield "{},{},{},{}".format(txmin, tymin, txmax, tymax)


