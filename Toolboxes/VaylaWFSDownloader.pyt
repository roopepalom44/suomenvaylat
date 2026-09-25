# -*- coding: utf-8 -*-
import arcpy
import urllib.request
import urllib.parse
import urllib.error
import http.client
import json
import os
import uuid
import base64
import time
import xml.etree.ElementTree as ET
import unicodedata
import re
import hashlib
import math
import shutil
import tempfile
import socket
import threading
import concurrent.futures
import ctypes
from ctypes import wintypes


# MML:n nykyiset avoimet rajapinnat. Kiinteistöaineistot haetaan OGC API
# Features -palvelusta ja karttatasot lisätään ArcGIS Prohon TileJSON-vektori-
# tiilinä. Vanha WMTS-polku on säilytetty alempana vain yhteensopivuus-/
# varareittejä varten; sitä ei käytetä MML:n normaalissa tasolistauksessa.
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
    # MML julkaisee taustakartan ja maastokartan nykyisessä avoimessa
    # vektoritiilipalvelussa saman TileJSON-lähteen kautta. Eri karttatyylit
    # voidaan vaihtaa ArcGIS Pron vektoritiilikerroksen tyylieditorilla.
    "Maastokartta": MML_TOPO_VECTOR_TILE_TILEJSON,
    "Kiinteistojaotus": MML_PROPERTY_VECTOR_TILE_TILEJSON,
}

# Vanha WMTS-osoite ja julkiset WMS-osoitteet pidetään erillään. Näitä
# käytetään vain vanhoissa/varareiteissä, ei nykyisessä MML-vector tile -
# toteutuksessa. Kapsin live-WMS ei saa koskaan periä MML:n API-avainta.
MML_WMTS_SERVICE_URL = (
    "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0"
)
MML_WMTS_LAYER_IDS = {
    "Taustakartta": "taustakartta",
    "Maastokartta": "maastokartta",
}
MML_WMS_SERVICE_URLS = {
    "taustakartta": "https://tiles.kartat.kapsi.fi/taustakartta?",
    "maastokartta": "https://tiles.kartat.kapsi.fi/peruskartta?",
}
MML_WMTS_MATRIX_SET = "ETRS-TM35FIN"
MML_WMTS_EPSG = 3067
MML_WMTS_TILE_SIZE = 256
MML_WMTS_ORIGIN_X = -548576.0
MML_WMTS_ORIGIN_Y = 8388608.0
MML_WMTS_MIN_LEVEL = 0
MML_WMTS_MAX_LEVEL = 13
MML_WMTS_DEFAULT_LEVEL = 9
MML_WMTS_MAX_TILES = 256

TRAFICOM_OPEN_WFS_ENDPOINT = "https://julkinen.traficom.fi/inspirepalvelu/avoin/wfs"
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

    def measured_sum(self, excluded=None):
        excluded = set(excluded or [])
        return sum(
            value for name, value in self.seconds.items()
            if name not in excluded and isinstance(value, (int, float))
        )


class CQLRequestRejected(Exception):
    """CQL GET ja POST epäonnistuivat; kutsuja voi kokeilla pienempiä CQL-osia."""


# Uudelleenyritettävät HTTP-tilakoodit. 4xx-virheitä ei yritetä uudelleen:
# esimerkiksi 400 ja 414 ovat kutsujalle merkitseviä signaaleja (liian pitkä
# CQL_FILTER), joiden varareitit hoidetaan ylempänä.
RETRYABLE_HTTP_STATUS = frozenset([408, 425, 429, 500, 502, 503, 504])
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
        for _ in range(self.MAX_REDIRECTS + 1):
            status, resp_headers, payload, location, read_s = self._single_request(
                current_url, method, headers, body, timeout
            )
            if status in (301, 302, 303, 307, 308) and location:
                current_url = urllib.parse.urljoin(current_url, location)
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

        send_headers = {"Connection": "keep-alive", "Accept-Encoding": "identity"}
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
                payload = response.read()
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
                if attempt == 0:
                    continue
                raise
            except Exception:
                self._drop(key)
                raise
        raise last_error if last_error else urllib.error.URLError("tuntematon verkkovirhe")


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
        for endpoint in endpoints:
            try:
                sep = "&" if "?" in endpoint else "?"
                endpoint_layers = self._parse_capabilities("{}{}service=WFS&request=GetCapabilities".format(endpoint, sep))
            except Exception:
                endpoint_layers = []
            for lyr in endpoint_layers:
                lid = lyr.get("id")
                if lid and lid not in seen:
                    seen.add(lid)
                    layers.append(lyr)
        self._capabilities_cache[source_name] = layers
        return layers

    def _parse_capabilities(self, url):
        layers = []
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as response:
            xml_bytes = response.read()
        root = ET.fromstring(xml_bytes)

        ns = {'wfs': 'http://www.wfs.opengis.net/wfs/2.0',
              'wfs11': 'http://www.opengis.net/wfs',
              'wfs20': 'http://www.opengis.net/wfs/2.0'}
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
        etype = element.get("type")
        if etype == "node":
            lon = element.get("lon")
            lat = element.get("lat")
            if lon is None or lat is None:
                return None
            return {"type": "Point", "coordinates": [lon, lat]}

        geometry = element.get("geometry") or []
        if not geometry:
            return None
        coords = [[pt.get("lon"), pt.get("lat")] for pt in geometry if "lon" in pt and "lat" in pt]
        if len(coords) < 2:
            return None

        is_closed = coords[0] == coords[-1]
        if is_closed and len(coords) >= 4:
            return {"type": "Polygon", "coordinates": [coords]}
        return {"type": "LineString", "coordinates": coords}


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
    TAG_CLASSES = [
        # public / education / health
        ("amenity", "police", 2001, "police"),
        ("amenity", "fire_station", 2002, "fire_station"),
        ("amenity", "post_box", 2004, "post_box"),
        ("amenity", "post_office", 2005, "post_office"),
        ("amenity", "telephone", 2006, "telephone"),
        ("amenity", "library", 2007, "library"),
        ("amenity", "townhall", 2008, "town_hall"),
        ("amenity", "courthouse", 2009, "courthouse"),
        ("amenity", "prison", 2010, "prison"),
        ("amenity", "embassy", 2011, "embassy"),
        ("amenity", "community_centre", 2012, "community_centre"),
        ("amenity", "nursing_home", 2013, "nursing_home"),
        ("amenity", "arts_centre", 2014, "arts_centre"),
        ("amenity", "grave_yard", 2015, "graveyard"),
        ("amenity", "marketplace", 2016, "marketplace"),
        ("amenity", "university", 2081, "university"),
        ("amenity", "school", 2082, "school"),
        ("amenity", "kindergarten", 2083, "kindergarten"),
        ("amenity", "college", 2084, "college"),
        ("amenity", "public_building", 2099, "public_building"),
        ("amenity", "pharmacy", 2101, "pharmacy"),
        ("amenity", "hospital", 2110, "hospital"),
        ("amenity", "clinic", 2111, "clinic"),
        ("amenity", "doctors", 2120, "doctors"),
        ("amenity", "dentist", 2121, "dentist"),
        ("amenity", "veterinary", 2129, "veterinary"),

        # leisure / sports
        ("amenity", "theatre", 2201, "theatre"),
        ("amenity", "nightclub", 2202, "nightclub"),
        ("amenity", "cinema", 2203, "cinema"),
        ("leisure", "park", 2204, "park"),
        ("leisure", "playground", 2205, "playground"),
        ("leisure", "dog_park", 2206, "dog_park"),
        ("leisure", "sports_centre", 2251, "sports_centre"),
        ("leisure", "pitch", 2252, "pitch"),
        ("amenity", "swimming_pool", 2253, "swimming_pool"),
        ("leisure", "swimming_pool", 2253, "swimming_pool"),
        ("leisure", "water_park", 2253, "swimming_pool"),
        ("sport", "swimming", 2253, "swimming_pool"),
        ("leisure", "golf_course", 2255, "golf_course"),
        ("leisure", "stadium", 2256, "stadium"),
        ("leisure", "ice_rink", 2257, "ice_rink"),
        ("leisure", "track", 2258, "track"),
        ("leisure", "fitness_centre", 2259, "fitness_centre"),
        ("leisure", "sports_hall", 2261, "sports_hall"),

        # catering / accommodation
        ("amenity", "restaurant", 2301, "restaurant"),
        ("amenity", "fast_food", 2302, "fast_food"),
        ("amenity", "cafe", 2303, "cafe"),
        ("amenity", "pub", 2304, "pub"),
        ("amenity", "bar", 2305, "bar"),
        ("amenity", "food_court", 2306, "food_court"),
        ("amenity", "biergarten", 2307, "biergarten"),
        ("tourism", "hotel", 2401, "hotel"),
        ("tourism", "motel", 2402, "motel"),
        ("tourism", "bed_and_breakfast", 2403, "bed_and_breakfast"),
        ("tourism", "guest_house", 2404, "guesthouse"),
        ("tourism", "hostel", 2405, "hostel"),
        ("tourism", "chalet", 2406, "chalet"),
        ("amenity", "shelter", 2421, "shelter"),
        ("tourism", "camp_site", 2422, "camp_site"),
        ("tourism", "alpine_hut", 2423, "alpine_hut"),
        ("tourism", "caravan_site", 2424, "caravan_site"),
        ("tourism", "wilderness_hut", 2425, "wilderness_hut"),

        # shopping and services
        ("shop", "supermarket", 2501, "supermarket"),
        ("shop", "bakery", 2502, "bakery"),
        ("shop", "kiosk", 2503, "kiosk"),
        ("shop", "mall", 2504, "mall"),
        ("shop", "department_store", 2505, "department_store"),
        ("shop", "general", 2510, "general"),
        ("shop", "convenience", 2511, "convenience"),
        ("shop", "clothes", 2512, "clothes"),
        ("shop", "florist", 2513, "florist"),
        ("shop", "chemist", 2514, "chemist"),
        ("shop", "books", 2515, "bookshop"),
        ("shop", "butcher", 2516, "butcher"),
        ("shop", "shoes", 2517, "shoe_shop"),
        ("shop", "alcohol", 2518, "beverages"),
        ("shop", "beverages", 2518, "beverages"),
        ("shop", "optician", 2519, "optician"),
        ("shop", "jewelry", 2520, "jeweller"),
        ("shop", "gift", 2521, "gift_shop"),
        ("shop", "sports", 2522, "sports_shop"),
        ("shop", "stationery", 2523, "stationery"),
        ("shop", "outdoor", 2524, "outdoor_shop"),
        ("shop", "mobile_phone", 2525, "mobile_phone_shop"),
        ("shop", "toys", 2526, "toy_shop"),
        ("shop", "newsagent", 2527, "newsagent"),
        ("shop", "greengrocer", 2528, "greengrocer"),
        ("shop", "beauty", 2529, "beauty_shop"),
        ("shop", "video", 2530, "video_shop"),
        ("shop", "car", 2541, "car_dealership"),
        ("shop", "bicycle", 2542, "bicycle_shop"),
        ("shop", "doityourself", 2543, "doityourself"),
        ("shop", "hardware", 2543, "doityourself"),
        ("shop", "furniture", 2544, "furniture_shop"),
        ("shop", "computer", 2546, "computer_shop"),
        ("shop", "garden_centre", 2547, "garden_centre"),
        ("shop", "hairdresser", 2561, "hairdresser"),
        ("shop", "car_repair", 2562, "car_repair"),
        ("amenity", "car_rental", 2563, "car_rental"),
        ("amenity", "car_wash", 2564, "car_wash"),
        ("amenity", "car_sharing", 2565, "car_sharing"),
        ("amenity", "bicycle_rental", 2566, "bicycle_rental"),
        ("shop", "travel_agency", 2567, "travel_agent"),
        ("shop", "laundry", 2568, "laundry"),
        ("shop", "dry_cleaning", 2568, "laundry"),
        ("amenity", "bank", 2601, "bank"),
        ("amenity", "atm", 2602, "atm"),

        # tourism / historic
        ("tourism", "information", 2701, "tourist_info"),
        ("tourism", "attraction", 2721, "attraction"),
        ("tourism", "museum", 2722, "museum"),
        ("historic", "monument", 2723, "monument"),
        ("historic", "memorial", 2724, "memorial"),
        ("tourism", "artwork", 2725, "artwork"),
        ("historic", "castle", 2731, "castle"),
        ("historic", "ruins", 2732, "ruins"),
        ("historic", "archaeological_site", 2733, "archaeological"),
        ("historic", "wayside_cross", 2734, "wayside_cross"),
        ("historic", "wayside_shrine", 2735, "wayside_shrine"),
        ("historic", "battlefield", 2736, "battlefield"),
        ("historic", "fort", 2737, "fort"),
        ("tourism", "picnic_site", 2741, "picnic_site"),
        ("tourism", "viewpoint", 2742, "viewpoint"),
        ("tourism", "zoo", 2743, "zoo"),
        ("tourism", "theme_park", 2744, "theme_park"),

        # miscellaneous POIs
        ("amenity", "toilets", 2901, "toilet"),
        ("amenity", "bench", 2902, "bench"),
        ("amenity", "drinking_water", 2903, "drinking_water"),
        ("amenity", "fountain", 2904, "fountain"),
        ("amenity", "hunting_stand", 2905, "hunting_stand"),
        ("amenity", "waste_basket", 2906, "waste_basket"),
        ("man_made", "surveillance", 2907, "camera_surveillance"),
        ("man_made", "water_tower", 2952, "water_tower"),
        ("man_made", "windmill", 2954, "windmill"),
        ("man_made", "lighthouse", 2955, "lighthouse"),
        ("man_made", "wastewater_plant", 2961, "wastewater_plant"),
        ("man_made", "water_well", 2962, "water_well"),
        ("man_made", "watermill", 2963, "water_mill"),
        ("man_made", "water_works", 2964, "water_works"),
    ]

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
        for field_name, safe_type in unsupported.items():
            temp_name = "{}_TXT".format(field_name[:20])
            arcpy.management.AddField(temp_fc, temp_name, safe_type, field_length=255 if safe_type == "TEXT" else None)
            with arcpy.da.UpdateCursor(temp_fc, [field_name, temp_name]) as cursor:
                for row in cursor:
                    row[1] = None if row[0] is None else str(row[0])[:254] if safe_type == "TEXT" else row[0]
                    cursor.updateRow(row)
            arcpy.management.DeleteField(temp_fc, field_name)
            arcpy.management.AlterField(temp_fc, temp_name, field_name[:10], field_name[:10])
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
            batch_size = max(100, int(self.max_batch_size / max(1, grid_size * 2)))
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


class Toolbox(object):
    def __init__(self):
        self.label = "Väylävirasto WFS Lataustyökalu"
        self.alias = "vayla_wfs_lataus"
        self.tools = [VaylaWFSDownloader, MMLBasemapDownloader]


class VaylaWFSDownloader(object):
    def __init__(self):
        self.label = "Suomenväylät.fi"
        self.description = "Lataa WFS- ja OGC API Features -aineistot osissa, hakee aluerajaukset paikallisesta geopackagesta ja leikkaa aineistot."
        self.canRunInBackground = False

        self.wfs_registry = WFSSourceRegistry()

        # Väylä (WFS)
        self.wfs_vayla = "https://avoinapi.vaylapilvi.fi/vaylatiedot/ows"
        # Digiroad (WFS)
        self.wfs_digiroad = "https://avoinapi.vaylapilvi.fi/vaylatiedot/digiroad/ows"

        # Paikallinen hallinnolliset aluejaot -aineisto (sama kuin muissa työkaluissa)
        self.admin_gpkg_name = "hallinnolliset_aluejaot.gpkg"
        self.admin_layer_names = {
            "Koko Suomi": "Valtakunta",
            "Elinvoimakeskus": "Elinvoimakeskus",
            "Hyvinvointialue": "Hyvinvointialue",
            "Maakunta": "Maakunta",
            "Kunta/Kaupunki": "Kunta"
        }

        self._all_wfs_layers_cache = {}
        self._layer_mapping_cache = {}
        self._kunnat_cache = None
        self._layer_mapping = {}  # Yhdistää käyttöliittymänimen ja teknisen WFS-nimen

        self._admin_choices_cache = {}
        self._admin_namefield_cache = {}
        self._admin_fc_cache = {}

        # RASKAAT TASOT (helppo laajentaa)
        self.heavy_layer_prefixes = ["liikennemaar"]
        self.heavy_layer_exact = []

        # Lähteet joiden raskaat tasot pilkotaan kunnittain
        self.heavy_chunk_sources = ["Väylä", "DigiRoad"]

        # MML:n OGC API Features- ja vector tile -palvelut
        self._all_mml_layers_cache = {}
        self._mml_layer_mapping = {}
        self._mml_layer_mapping_cache = {}
        self._runtime_mml_api_key = ""
        self._runtime_karttapaikka_api_key = ""
        self._runtime_aino_token = ""
        self._aino_catalog_errors = {}
        self._aino_catalog_counts = {}
        self._credentials_cache = None
        self._wfs_output_format_cache = {}
        self._wfs_geometry_field_cache = {}
        self._wfs_sort_candidate_cache = {}
        self._wfs_sort_field_cache = {}
        self._runtime_workspace = None
        self._runtime_workspace_is_folder = None
        self._runtime_workspace_validated = False
        self._runtime_project = None
        self._runtime_map = None
        self._runtime_map_loaded = False
        # Vanha WMTS-polku jätetään luokkaan yhteensopivuutta varten. MML:n
        # normaali tasolistaus käyttää nykyistä kiinteistöjen OGC API Features
        # -palvelua ja taustakarttatyökalu käyttää TileJSON-vektoritiiliä.
        self.mml_wmts_capabilities = MML_WMTS_SERVICE_URL + "/WMTSCapabilities.xml"
        self.mml_wmts_base = MML_WMTS_SERVICE_URL
        self.mml_wms_services = dict(MML_WMS_SERVICE_URLS)
        self.mml_karttakuva_wmts = "https://karttakuva.maanmittauslaitos.fi/maasto/wmts/1.0.0/WMTSCapabilities.xml"
        self._all_mml_karttakuva_layers_cache = {}
        self._mml_karttakuva_layer_mapping = {}
        self._runtime_karttakuva_user = ""
        self._runtime_karttakuva_pass = ""
        self._last_source_values = ["Väylä"]
        self._all_kapsi_layers_cache = None
        self._kapsi_layer_mapping = {
            "Ortokuva": "https://tiles.kartat.kapsi.fi/ortokuva|ortokuva"
        }
        self._kapsi_layer_scale_ranges = {}
        self.kapsi_wms_base = "https://tiles.kartat.kapsi.fi/ortokuva"
        self._run_scratch_folder = None
        self._run_scratch_gdb = None
        self._tool_metrics = None
        self._run_id = None
        self._verbose_diagnostics = False
        self._run_had_layer_failures = False
        # Verkkokerros: pysyvät yhteydet ja uudelleenyritys ohimenevissä virheissä.
        self._http_transport = HttpTransport()
        self._http_max_attempts = 3
        # Rinnakkaisten sivuhakujen määrä. 1 = vanha sarjallinen toiminta.
        self._page_workers = 4
        # Montako sivua kootaan yhteen JSONToFeatures-kutsuun.
        self._json_batch_pages = 8
        # Tasolistauksen levyvälimuistin elinikä sekunteina (0 = pois).
        self._layer_cache_ttl_s = 86400
        self._layer_refresh_consumed = False
        # Rinnakkaiset rasterilaattojen lataukset (Kapsi/WMTS).
        self._tile_workers = 5
        self._resources_dir_cache = "__unset__"
        self._admin_gpkg_cache = "__unset__"
        self._workspace_kind_cache = {}

    # ---------------------------
    # LOGGING
    # ---------------------------
    def _msg(self, s: str):
        arcpy.AddMessage(self._redact_secrets(s))

    def _warn(self, s: str):
        arcpy.AddWarning(self._redact_secrets(s))

    def _error(self, s: str):
        arcpy.AddError(self._redact_secrets(s))

    def _scratch_folder(self):
        return self._run_scratch_folder or arcpy.env.scratchFolder

    def _scratch_gdb(self):
        return self._run_scratch_gdb or arcpy.env.scratchGDB

    def _sanitize_url(self, raw_url):
        """Palauta lokitettava palveluosoite ilman tunnisteita tai query-arvoja."""
        if not raw_url:
            return "(ei käytössä)"
        try:
            parsed = urllib.parse.urlsplit(str(raw_url))
            host = parsed.hostname or ""
            if parsed.port:
                host = "{}:{}".format(host, parsed.port)
            return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except Exception:
            return "(osoite piilotettu)"

    def _redact_secrets(self, value):
        text = str(value or "")
        for secret in (
            getattr(self, "_runtime_mml_api_key", ""),
            getattr(self, "_runtime_karttapaikka_api_key", ""),
            getattr(self, "_runtime_aino_token", ""),
            getattr(self, "_runtime_karttakuva_user", ""),
            getattr(self, "_runtime_karttakuva_pass", ""),
        ):
            if secret:
                for candidate in {
                    secret,
                    urllib.parse.quote(secret, safe=""),
                    urllib.parse.quote_plus(secret, safe=""),
                }:
                    if candidate:
                        text = text.replace(candidate, "[PIILOTETTU]")
        return text

    @staticmethod
    def _secret_cache_key(value):
        """Palauta tunnisteelle case-sensitive, lokiin sopimaton välimuistiavain."""
        text = str(value or "")
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_aino_token(value):
        """Korjaa yleiset Aino-tokenin kopiointimuodot.

        Joissakin sähköposti-/HTML-lähteissä URL:n yhtäsuuruusmerkki näkyy
        quoted-printable-muodossa ``=3D``. Tällöin käyttäjälle voi päätyä
        varsinaisen 46-merkkisen tokenin eteen teksti ``3D``. Aino hylkää
        sellaisen arvon HTTP 401:llä.
        """
        token = str(value or "").strip().strip("'\"")
        if not token:
            return ""
        try:
            parsed = urllib.parse.urlsplit(token)
            if parsed.scheme and parsed.netloc:
                for key, item_value in urllib.parse.parse_qsl(
                    parsed.query, keep_blank_values=True
                ):
                    if key.casefold() == "token":
                        token = item_value.strip()
                        break
        except Exception:
            pass
        token = urllib.parse.unquote(token).strip()
        if token.casefold().startswith("token="):
            token = token.split("=", 1)[1].strip()
        if token.startswith("=3D"):
            token = token[3:]
        # Ainon nykyinen tunniste on 46 aakkosnumeerista merkkiä. Rajattu
        # muototarkistus estää aidosti "3D":llä alkavan muun pituisen tokenin
        # muuttamisen.
        if re.fullmatch(r"3D[A-Za-z0-9]{46}", token):
            token = token[2:]
        return token

    def _format_phase(self, metrics, name):
        value = metrics.get(name)
        if isinstance(value, (int, float)):
            return "{:.3f} s".format(value)
        return metrics.status.get(name, "ei käytetty")

    def _log_phase_summary(self, title, metrics, ordered_names, total_s):
        if not getattr(self, "_verbose_diagnostics", False):
            return
        measured = sum(
            metrics.get(name) for name in ordered_names
            if isinstance(metrics.get(name), (int, float))
        )
        other_s = max(0.0, float(total_s) - measured)
        self._msg("{}".format(title))
        for name in ordered_names:
            self._msg("    - {}: {}".format(name, self._format_phase(metrics, name)))
        self._msg("    - Vaiheiden summa: {:.3f} s".format(measured))
        self._msg("    - Muu-aika: {:.3f} s".format(other_s))
        self._msg("    - Kokonaisaika: {:.3f} s".format(float(total_s)))

    def _create_run_scratch(self):
        create_start = time.perf_counter()
        self._run_id = uuid.uuid4().hex[:8]
        run_folder = tempfile.mkdtemp(prefix="suomenvaylat_")
        try:
            arcpy.management.CreateFileGDB(run_folder, "scratch.gdb")
            run_gdb = os.path.join(run_folder, "scratch.gdb")
        except Exception:
            shutil.rmtree(run_folder, ignore_errors=True)
            raise
        self._run_scratch_folder = run_folder
        self._run_scratch_gdb = run_gdb
        return time.perf_counter() - create_start

    def _cleanup_run_scratch(self, preserve=False):
        cleanup_start = time.perf_counter()
        folder = self._run_scratch_folder
        if preserve:
            self._warn("[VAROITUS] Virheajon scratch-aineisto säilytettiin: {}".format(folder))
            return time.perf_counter() - cleanup_start, None
        cleanup_error = None
        try:
            try:
                arcpy.management.ClearWorkspaceCache(self._run_scratch_gdb)
            except Exception:
                pass
            if folder and os.path.isdir(folder):
                shutil.rmtree(folder)
        except Exception as ex:
            cleanup_error = ex
        finally:
            self._run_scratch_folder = None
            self._run_scratch_gdb = None
        return time.perf_counter() - cleanup_start, cleanup_error

    # ---------------------------
    # UTILS
    # ---------------------------
    def _norm(self, s: str) -> str:
        if s is None:
            return ""
        s = str(s).strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return s

    def _sanitize_table_name(self, raw_name: str) -> str:
        if not raw_name:
            raw_name = "output"
        n = unicodedata.normalize("NFKD", raw_name)
        n = "".join(c for c in n if not unicodedata.combining(c))
        n = n.replace(" ", "_").replace("-", "_")
        n = re.sub(r"[^0-9A-Za-z_+]", "_", n)
        if n and n[0].isdigit():
            n = "_" + n
        return n

    def _validated_name(self, raw_name: str, workspace: str) -> str:
        n = self._sanitize_table_name(raw_name)
        if not self._is_filesystem_workspace(workspace):
            try:
                # File GDB -nimisäännöt ovat samat paikallisessa ja verkko-GDB:ssä.
                # Validointi paikallista scratch-GDB:tä vasten välttää hitaan
                # verkko-GDB:n avaamisen pelkkää nimitarkistusta varten.
                validation_workspace = (
                    self._scratch_gdb() if self._is_remote_workspace(workspace) else workspace
                )
                n = arcpy.ValidateTableName(n, validation_workspace)
            except Exception:
                pass
        return n

    def _is_remote_workspace(self, workspace: str) -> bool:
        path = os.path.abspath(str(workspace or ""))
        if path.startswith("\\\\"):
            return True
        drive, _ = os.path.splitdrive(path)
        if not drive or os.name != "nt":
            return False
        try:
            # DRIVE_REMOTE = 4. Mapped drives (esim. V:) tunnistuvat tällä.
            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4
        except Exception:
            return False

    def _unique_output_name(self, raw_name: str, workspace: str, max_len: int = 60) -> str:
        """Validoi nimi ja varmista ettei se törmää jo olemassa olevaan
        tulokseen samassa workspacessa (estää hiljaisen ylikirjoituksen)."""
        base = self._validated_name(raw_name, workspace)[:max_len]
        if self._is_remote_workspace(workspace):
            # Verkko-GDB:n jokainen Exists-kutsu voi kestää useita sekunteja.
            # Ajokohtainen tunniste antaa käytännössä yksilöllisen nimen ilman
            # yhtäkään verkkokyselyä.
            suffix = "_{}".format(self._run_id or uuid.uuid4().hex[:8])
            return base[:max_len - len(suffix)] + suffix
        candidate = base
        i = 1
        while arcpy.Exists(self._dataset_output_path(workspace, candidate)):
            suffix = "_{}".format(i)
            candidate = base[:max_len - len(suffix)] + suffix
            i += 1
        return candidate

    def _add_to_map(self, dataset_path: str):
        try:
            if not self._runtime_map_loaded:
                self._runtime_project = arcpy.mp.ArcGISProject("CURRENT")
                self._runtime_map = self._runtime_project.activeMap
                self._runtime_map_loaded = True
            m = self._runtime_map
            if m:
                m.addDataFromPath(dataset_path)
                return True, None
            return False, "aktiivista karttaa ei ole"
        except Exception as ex:
            return False, str(ex)

    def _active_map_for_background(self):
        if not self._runtime_map_loaded:
            self._runtime_project = arcpy.mp.ArcGISProject("CURRENT")
            self._runtime_map = self._runtime_project.activeMap
            self._runtime_map_loaded = True
        if not self._runtime_map:
            raise Exception("Aktiivista karttaa ei löydy.")
        return self._runtime_map

    def _find_or_create_group_layer(self, active_map, group_name):
        for layer in active_map.listLayers():
            if getattr(layer, "isGroupLayer", False) and getattr(layer, "name", "") == group_name:
                return layer
        create_group = getattr(active_map, "createGroupLayer", None)
        if callable(create_group):
            return create_group(group_name)
        # ArcGIS Prossa createGroupLayer on saatavilla, mutta pidetään
        # testaus-/vanhan projektin varapolku ilman että karttataso katoaa.
        return None

    @staticmethod
    def _move_group_to_map_bottom(active_map, group_layer):
        """Siirrä taustakarttaryhmä kartan pinon alimmaiseksi, jos mahdollista."""
        if group_layer is None:
            return
        move_layer = getattr(active_map, "moveLayer", None)
        if not callable(move_layer):
            return
        try:
            top_level = []
            for layer in active_map.listLayers():
                if layer is group_layer:
                    continue
                long_name = str(getattr(layer, "longName", "") or "")
                # ArcPy käyttää kenoviivaa ryhmäpolun erottimena. Tyhjä
                # longName tulkitaan ylintason tasoksi vanhojen Pro-versioiden
                # yhteensopivuuden vuoksi.
                if not long_name or "\\" not in long_name:
                    top_level.append(layer)
            if top_level:
                move_layer(top_level[-1], group_layer, "AFTER")
        except Exception:
            # Piirtojärjestys ei saa estää palvelutason lisäämistä.
            pass

    @staticmethod
    def _as_layer_list(value):
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def _put_layer_in_group(self, active_map, group_layer, layer):
        if group_layer is None:
            return
        add_to_group = getattr(active_map, "addLayerToGroup", None)
        if callable(add_to_group):
            add_to_group(group_layer, layer, "TOP")
            return
        group_add = getattr(group_layer, "addLayer", None)
        if callable(group_add):
            group_add(layer)
            return
            raise Exception("Taustakartta-ryhmään ei voitu lisätä tasoa.")

    def _configure_group_layers(self, active_map, group_layer, layers, layer_name,
                                visible=True):
        layers = self._as_layer_list(layers)
        if not layers:
            raise Exception("ArcGIS Pro ei palauttanut lisättyä tasoa.")
        for layer in layers:
            self._put_layer_in_group(active_map, group_layer, layer)
        top_layer = layers[0]
        try:
            top_layer.name = layer_name
        except Exception:
            pass
        try:
            top_layer.visible = bool(visible)
        except Exception:
            pass
        return top_layer

    def _add_path_to_group(self, active_map, group_layer, path, layer_name, visible=True,
                           data_type=None):
        if data_type:
            added = active_map.addDataFromPath(path, data_type)
        else:
            added = active_map.addDataFromPath(path)
        if not self._as_layer_list(added):
            raise Exception("ArcGIS Pro ei palauttanut lisättyä tasoa polusta '{}'.".format(path))
        # addDataFromPath voi palauttaa palvelutasolle useamman alitason.
        # Kaikki palautetut tasot siirretään samaan ryhmään; päällimmäisenä
        # näkyvä taso saa käyttäjälle selkeän nimen.
        return self._configure_group_layers(
            active_map, group_layer, added, layer_name, visible
        )

    def _add_mml_background_layers(self, local_raster_path, layer_id, display_name):
        """Lisää RGB-varatason ja julkisen WMS:n Taustakartta-ryhmään."""
        active_map = self._active_map_for_background()
        group_layer = self._find_or_create_group_layer(active_map, "Taustakartta")
        if group_layer is not None:
            try:
                group_layer.visible = True
            except Exception:
                pass
        local_layer = None
        try:
            local_layer = self._add_path_to_group(
                active_map,
                group_layer,
                local_raster_path,
                "MML RGB – {}".format(display_name),
                visible=False,
            )
        except Exception as ex:
            self._warn("[VAROITUS] Paikallista MML RGB-rasteria ei lisätty kartalle: {}".format(ex))

        service_url = self.mml_wms_services.get((layer_id or "").strip())
        if not service_url:
            if local_layer is not None:
                local_layer.visible = True
            raise Exception("MML WMS -palveluosoitetta ei ole tasolle '{}'.".format(layer_id))

        self._msg("[INFO] live-WMS:n lisäys alkaa: {}".format(display_name))
        try:
            # Mahdolliset layoutit ja legendat käsitellään ennen tätä kohtaa.
            # Tässä lisäosassa ei ole erillistä PAGX-tuontia, joten WMS lisätään
            # vasta kun paikallinen RGB-rasteri on valmis ja ryhmä luotu.
            layers = active_map.addDataFromPath(service_url, "WMS")
            wms_layer = self._configure_group_layers(
                active_map,
                group_layer,
                layers,
                "MML WMS – {}".format(display_name),
                visible=True,
            )
            if local_layer is not None:
                local_layer.visible = False
            self._msg("[INFO] live-WMS lisätty: {}".format(display_name))
            return {"local": local_layer, "wms": wms_layer, "wms_added": True}
        except Exception as ex:
            if local_layer is not None:
                try:
                    local_layer.visible = True
                except Exception:
                    pass
                self._warn(
                    "[VAROITUS] live-WMS:n lisäys epäonnistui; paikallinen RGB-rasteri "
                    "otettiin käyttöön varatasona: {}".format(ex)
                )
                self._msg("[INFO] Paikallinen rasteri otettu käyttöön varatasona.")
                return {"local": local_layer, "wms": None, "wms_added": False}
            raise

    def _add_mml_vector_tile_layer(self, tilejson_url, display_name, api_key):
        """Lisää MML:n nykyisen TileJSON-vektoritiilipalvelun kartalle.

        API-avain annetaan ArcGIS Pron custom request parameter -sanakirjassa,
        joten se ei päädy palveluosoitteeseen tai tavalliseen lokitulosteeseen.
        """
        key = (api_key or "").strip()
        if not key:
            raise Exception("MML:n vector tile -palvelu vaatii API-avaimen.")
        tilejson_url = str(tilejson_url or "").strip()
        if not tilejson_url:
            raise Exception("MML:n vector tile -palveluosoite puuttuu.")

        active_map = self._active_map_for_background()
        group_layer = self._find_or_create_group_layer(active_map, "Taustakartta")
        if group_layer is not None:
            try:
                group_layer.visible = True
            except Exception:
                pass

        self._msg(
            "[INFO] MML vector tile -tason lisäys alkaa: {} ({})".format(
                display_name, self._sanitize_url(tilejson_url)
            )
        )
        # ArcGIS Pro tukee TileJSON-osoitetta VECTOR_TILE-tyyppinä ja välittää
        # custom_parameters-sanakirjan tiilipyyntöihin.
        layers = active_map.addDataFromPath(
            tilejson_url,
            "VECTOR_TILE",
            {"api-key": key},
        )
        vector_layer = self._configure_group_layers(
            active_map,
            group_layer,
            layers,
            "MML vector tile – {}".format(display_name),
            visible=True,
        )
        self._msg("[INFO] MML vector tile -taso lisätty: {}".format(display_name))
        return {"vector_tile": vector_layer}

    @staticmethod
    def _wms_match_key(value):
        """Normalisoi WMS:n tekninen nimi tai otsikko vertailua varten."""
        return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()

    def _select_wms_sublayer_in_cim(self, service_layer, layer_name, layer_title):
        """Valitse yksi WMS-alitaso CIM:n ServiceLayerID:n perusteella.

        WMS-palvelun tekninen ``Name`` päätyy ArcGIS Pron CIM-mallissa yleensä
        ``serviceLayerID``-kenttään, kun taas Contents-paneelissa näkyvä nimi on
        palvelun ``Title``. Tekninen nimi on siksi aina ensisijainen.
        """
        get_definition = getattr(service_layer, "getDefinition", None)
        set_definition = getattr(service_layer, "setDefinition", None)
        if not callable(get_definition) or not callable(set_definition):
            return False
        try:
            definition = get_definition("V3")
        except Exception:
            return False

        records = []

        def visit(nodes, ancestors):
            for node in list(nodes or []):
                records.append((node, tuple(ancestors)))
                visit(getattr(node, "subLayers", None), ancestors + [node])

        visit(getattr(definition, "subLayers", None), [])
        if not records:
            return False

        technical_key = self._wms_match_key(layer_name)
        local_key = self._wms_match_key(str(layer_name or "").split(":")[-1])
        title_key = self._wms_match_key(layer_title)
        selected_record = None
        for record in records:
            service_id = self._wms_match_key(
                getattr(record[0], "serviceLayerID", None)
            )
            if service_id and service_id == technical_key:
                selected_record = record
                break
        if selected_record is None:
            # Joissakin Pro-versioissa nimiavaruus jää ServiceLayerID:stä pois.
            for record in records:
                service_id = self._wms_match_key(
                    getattr(record[0], "serviceLayerID", None)
                )
                if service_id and service_id == local_key:
                    selected_record = record
                    break
        if selected_record is None:
            for record in records:
                node_name = self._wms_match_key(getattr(record[0], "name", None))
                if node_name in (technical_key, local_key):
                    selected_record = record
                    break
        if selected_record is None:
            for record in records:
                if self._wms_match_key(getattr(record[0], "name", None)) == title_key:
                    selected_record = record
                    break
        if selected_record is None:
            return False

        selected_node, ancestors = selected_record
        enabled_nodes = set([id(selected_node)] + [id(node) for node in ancestors])

        def add_descendants(node):
            for child in list(getattr(node, "subLayers", None) or []):
                enabled_nodes.add(id(child))
                add_descendants(child)

        add_descendants(selected_node)
        for node, _ in records:
            try:
                node.visibility = id(node) in enabled_nodes
            except Exception:
                pass

        # Pelkkä visibility=False jättäisi kaikki palvelun 175 alitasoa
        # Contents-paneeliin. Säilytä CIM-puussa vain valittu alitaso,
        # mahdolliset sen lapset sekä valintaan johtava välttämätön yläpolku.
        # Näin palveluyhteys säilyy oikeana WMS:nä, mutta käyttäjä ei saa
        # karttaan koko Ainon tasopuuta.
        def pruned_path(nodes):
            kept = []
            for node in list(nodes or []):
                if node is selected_node:
                    kept.append(node)
                    continue
                children = list(getattr(node, "subLayers", None) or [])
                kept_children = pruned_path(children)
                if kept_children:
                    try:
                        node.subLayers = kept_children
                    except Exception:
                        pass
                    try:
                        node.visibility = True
                    except Exception:
                        pass
                    kept.append(node)
            return kept

        try:
            definition.subLayers = pruned_path(
                getattr(definition, "subLayers", None)
            )
        except Exception:
            return False
        try:
            set_definition(definition)
            return True
        except Exception:
            return False

    def _select_wms_sublayer_in_layer_tree(self, service_layer, layer_name,
                                           layer_title):
        """ArcPy-varareitti WMS-alitason valintaan komposiittitason puusta."""
        list_layers = getattr(service_layer, "listLayers", None)
        if not callable(list_layers):
            return False
        try:
            sublayers = list(list_layers() or [])
        except Exception:
            return False
        if not sublayers:
            return False

        keys = {
            self._wms_match_key(layer_name),
            self._wms_match_key(str(layer_name or "").split(":")[-1]),
            self._wms_match_key(layer_title),
        }
        selected = None
        for sublayer in sublayers:
            names = {
                self._wms_match_key(getattr(sublayer, "name", None)),
                self._wms_match_key(getattr(sublayer, "longName", None)),
            }
            if keys.intersection(names):
                selected = sublayer
                break
        if selected is None:
            return False
        selected_long_name = self._wms_match_key(
            getattr(selected, "longName", None)
        )
        selected_is_group = bool(getattr(selected, "isGroupLayer", False))
        for sublayer in sublayers:
            try:
                sublayer_long_name = self._wms_match_key(
                    getattr(sublayer, "longName", None)
                )
                is_descendant = bool(
                    selected_is_group
                    and selected_long_name
                    and sublayer_long_name.startswith(selected_long_name + "\\")
                )
                sublayer.visible = bool(
                    sublayer is selected
                    or is_descendant
                    or getattr(sublayer, "isGroupLayer", False)
                )
            except Exception:
                pass
        return True

    def _add_aino_wms_layer(self, endpoint, layer_name, layer_title, token,
                            is_background=False):
        """Lisää yksi Ainon nimetty taso aktiiviseen karttaan live-WMS:nä."""
        token = (token or "").strip()
        if not token:
            raise Exception("Aino WMS vaatii tokenin.")
        endpoint = self._sanitize_url(endpoint)
        if not endpoint:
            raise Exception("Aino WMS -palveluosoite puuttuu.")
        if not (layer_name or "").strip():
            raise Exception("Aino WMS -tason tekninen nimi puuttuu.")

        active_map = self._active_map_for_background()
        group_name = "Taustakartta" if is_background else "Aino WMS"
        group_layer = self._find_or_create_group_layer(active_map, group_name)
        if is_background:
            self._move_group_to_map_bottom(active_map, group_layer)
        if group_layer is not None:
            try:
                group_layer.visible = True
            except Exception:
                pass

        # custom_parameters välittyy GetCapabilities-, GetMap- ja
        # GetFeatureInfo-pyyntöihin. Token ei näin päädy URL:iin tai lokiin.
        added = active_map.addDataFromPath(endpoint, "WMS", {"token": token})
        roots = self._as_layer_list(added)
        if not roots:
            raise Exception("ArcGIS Pro ei palauttanut lisättyä Aino WMS -tasoa.")

        selected = False
        for root in roots:
            if self._select_wms_sublayer_in_cim(root, layer_name, layer_title):
                selected = True
                break
            if self._select_wms_sublayer_in_layer_tree(root, layer_name, layer_title):
                selected = True
                break
        if not selected:
            remove_layer = getattr(active_map, "removeLayer", None)
            if callable(remove_layer):
                for root in roots:
                    try:
                        remove_layer(root)
                    except Exception:
                        pass
            raise Exception(
                "ArcGIS Pro lisäsi WMS-palvelun, mutta valittua alitasoa '{}' "
                "ei löytynyt palvelutasosta.".format(layer_name)
            )

        display_name = "Aino WMS – {}".format(layer_title or layer_name)
        top_layer = roots[0]
        for root in roots:
            try:
                root.visible = True
            except Exception:
                pass
        try:
            top_layer.name = display_name
        except Exception:
            pass

        if group_layer is not None:
            # addLayerToGroup kopioi jo kartassa olevan tason ryhmään. Nimeä
            # ja rajaa taso ennen kopiointia, hae uusi ryhmäkopio ja poista
            # alkuperäinen, jotta karttaan ei jää kuvan kaltaisia tuplatasoja.
            for root in roots:
                self._put_layer_in_group(active_map, group_layer, root)
            list_group_layers = getattr(group_layer, "listLayers", None)
            if callable(list_group_layers):
                try:
                    candidates = [
                        candidate for candidate in list_group_layers()
                        if getattr(candidate, "name", "") == display_name
                    ]
                    if candidates:
                        top_layer = candidates[0]
                except Exception:
                    pass
            remove_layer = getattr(active_map, "removeLayer", None)
            if callable(remove_layer):
                for root in roots:
                    try:
                        remove_layer(root)
                    except Exception:
                        pass
        self._msg("[INFO] Aino live-WMS lisätty: {}".format(layer_title or layer_name))
        return {"wms": top_layer, "group": group_name}

    def _parse_source_values(self, value_as_text):
        values = self._parse_multivalue(value_as_text)
        return values if values else ["Väylä"]

    def _parse_multivalue_param(self, param):
        try:
            raw_values = getattr(param, "values", None)
            if raw_values:
                if isinstance(raw_values, str):
                    raw_values = [raw_values]
                out = []
                seen = set()
                for v in raw_values:
                    if isinstance(v, (list, tuple)):
                        txt = str(v[0]).strip().strip("'").strip('"') if len(v) > 0 else ""
                    else:
                        txt = str(v).strip().strip("'").strip('"')
                    if not txt:
                        continue
                    key = self._norm(txt)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(txt)
                if out:
                    return out
        except Exception:
            pass
        return self._parse_multivalue(param.valueAsText)

    def _source_values_from_param(self, param, restore_empty=True):
        """Lue lähteet ja säilytä viimeinen valinta ArcGISin validointikierrosten yli."""
        source_values = self._parse_multivalue_param(param)
        if source_values:
            self._last_source_values = list(source_values)
            return source_values

        source_values = list(getattr(self, "_last_source_values", None) or ["Väylä"])
        if restore_empty:
            restored_values = (
                [[value] for value in source_values]
                if getattr(param, "datatype", None) == "GPValueTable"
                else list(source_values)
            )
            try:
                param.values = restored_values
            except Exception:
                try:
                    param.value = ";".join(source_values)
                except Exception:
                    pass
        return source_values

    @staticmethod
    def _is_layer_placeholder(value):
        """Tunnista UI:n tyhjän hakutuloksen viesti, ei oikea tasovalinta."""
        text = str(value or "").strip().lower()
        return text.startswith("(ei osumia") or text.startswith("(no matches")

    def _clear_multivalue_param(self, param):
        """Tyhjennä vanha tasovalinta, kun lähde/haku vaihtuu.

        ArcGIS Pro -versioiden Parameter-toteutuksissa joko ``values`` tai
        ``value`` on kirjoitettavissa, joten kokeillaan molempia turvallisesti.
        """
        cleared = False
        try:
            param.values = []
            cleared = True
        except Exception:
            pass
        try:
            param.value = None
            cleared = True
        except Exception:
            pass
        return cleared

    @staticmethod
    def _set_multivalue_param(param, values):
        """Aseta moniarvovalinta takaisin ArcGIS-version tukemalla tavalla."""
        clean_values = [str(value) for value in (values or []) if str(value or "").strip()]
        try:
            param.values = clean_values
            return True
        except Exception:
            pass
        try:
            param.value = ";".join(clean_values) if clean_values else None
            return True
        except Exception:
            return False

    def _format_layer_label(self, title, source_name):
        clean_title = re.sub(r"\s*\(\s*digiroad\s*\)", "", str(title or ""), flags=re.IGNORECASE)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()
        return "{} - {}".format(clean_title, source_name)

    def _dataset_output_path(self, workspace, out_name):
        final_name = out_name
        if self._is_filesystem_workspace(workspace) and not final_name.lower().endswith(".shp"):
            final_name = final_name + ".shp"
        return os.path.join(workspace, final_name)

    def _copy_features_compatible(self, source_fc, workspace, out_name, metrics=None,
                                  output_known_absent=False):
        metrics = metrics if metrics is not None else PhaseMetrics()
        out_path = self._dataset_output_path(workspace, out_name)
        if output_known_absent:
            output_exists = False
            metrics.skip(
                "olemassa olevan tulosaineiston tarkistus",
                "ohitettu (ajokohtainen yksilöllinen nimi)"
            )
        else:
            exists_start = time.perf_counter()
            output_exists = arcpy.Exists(out_path)
            metrics.add("olemassa olevan tulosaineiston tarkistus", time.perf_counter() - exists_start)
        if output_exists:
            delete_start = time.perf_counter()
            self._safe_delete(out_path)
            metrics.add("olemassa olevan tulosaineiston poistaminen", time.perf_counter() - delete_start)
        else:
            metrics.skip("olemassa olevan tulosaineiston poistaminen", "ei tarpeen")
        workspace_is_folder = self._is_filesystem_workspace(workspace)
        fields_start = time.perf_counter()
        copy_source, conversion_count = ShapefileFieldConverter.convert_feature_class(source_fc, workspace_is_folder)
        metrics.add("kenttien käsittely", time.perf_counter() - fields_start)
        if conversion_count > 0:
            self._msg("[INFO] Muunnettiin {} kenttää shapefile-yhteensopivaksi: {}".format(conversion_count, out_name))
        copy_start = time.perf_counter()
        arcpy.management.CopyFeatures(copy_source, out_path)
        metrics.add("lopullinen CopyFeatures", time.perf_counter() - copy_start)
        if copy_source != source_fc:
            self._safe_delete(copy_source)
        return out_path

    def _copy_raster_to_workspace(self, source_raster, workspace):
        raster_dir = self._raster_folder(workspace)
        base_name, extension = os.path.splitext(os.path.basename(source_raster))
        extension = extension or ".tif"
        base_name = self._validated_name(base_name, raster_dir)
        output_path = os.path.join(raster_dir, base_name + extension)
        suffix = 1
        while os.path.exists(output_path):
            output_path = os.path.join(raster_dir, "{}_{}{}".format(base_name, suffix, extension))
            suffix += 1
        arcpy.management.CopyRaster(source_raster, output_path)
        return output_path

    def _copy_raster_bundle_to_workspace(self, source_raster, workspace):
        raster_dir = self._raster_folder(workspace)
        base_name = self._validated_name(
            os.path.splitext(os.path.basename(source_raster))[0], raster_dir
        )
        output_path = os.path.join(raster_dir, base_name + ".jpg")
        suffix = 1
        while os.path.exists(output_path):
            output_path = os.path.join(raster_dir, "{}_{}.jpg".format(base_name, suffix))
            suffix += 1

        source_root = os.path.splitext(source_raster)[0]
        output_root = os.path.splitext(output_path)[0]
        shutil.copy2(source_raster, output_path)
        for extension in [".jgw", ".prj"]:
            sidecar = source_root + extension
            if os.path.exists(sidecar):
                shutil.copy2(sidecar, output_root + extension)
        return output_path

    def _remove_local_output(self, path):
        if not path:
            return
        try:
            if arcpy.Exists(path):
                self._safe_delete(path)
                return
        except Exception:
            pass
        if os.path.isdir(path):
            self._safe_delete(path)
            return
        if os.path.isfile(path):
            try:
                os.remove(path)
            except Exception:
                pass
        root, _ = os.path.splitext(path)
        for extension in [".jgw", ".prj"]:
            sidecar = root + extension
            if os.path.isfile(sidecar):
                try:
                    os.remove(sidecar)
                except Exception:
                    pass

    def _assert_has_selection(self, source_fc, where_clause, error_message=None):
        msg = error_message or "Valinnalla ei löytynyt geometriaa."
        with arcpy.da.SearchCursor(source_fc, ["OID@"], where_clause) as cur:
            if not cur.next():
                raise Exception(msg)

    def _delete_identical_downloads(self, feature_class):
        fields = ["Shape"]
        try:
            for field in arcpy.ListFields(feature_class):
                if field.type not in ("OID", "Geometry", "Blob", "Raster"):
                    fields.append(field.name)
        except Exception:
            pass
        arcpy.management.DeleteIdentical(feature_class, fields)

    def _export_features_compat(self, source_fc, workspace, out_name, where_clause=None):
        """Vie feature class työtilaan nykyisellä GP-työkalulla.

        ``FeatureClassToFeatureClass`` on deprecated ArcGIS Pro 3.x:ssä ja
        ``ExportFeatures`` on sen nopeampi seuraaja. Vanha työkalu jää
        varareitiksi, jotta laajennus toimii myös vanhemmassa Prossa.
        """
        out_path = os.path.join(workspace, out_name)
        export_features = getattr(arcpy.conversion, "ExportFeatures", None)
        if export_features is not None:
            try:
                export_features(source_fc, out_path, where_clause)
                return out_path
            except AttributeError:
                pass
        arcpy.conversion.FeatureClassToFeatureClass(
            source_fc, workspace, out_name, where_clause
        )
        return out_path

    def _feature_class_to_workspace(self, source_fc, workspace, out_name, where_clause=None):
        if self._is_filesystem_workspace(workspace):
            temp_name = "sel_{}".format(uuid.uuid4().hex[:8])
            temp_fc = os.path.join(self._scratch_gdb(), temp_name)
            try:
                self._export_features_compat(
                    source_fc, self._scratch_gdb(), temp_name, where_clause
                )
                return self._copy_features_compatible(temp_fc, workspace, out_name)
            finally:
                self._safe_delete(temp_fc)

        out_path = os.path.join(workspace, out_name)
        self._safe_delete(out_path)
        self._export_features_compat(
            source_fc, workspace, out_name, where_clause
        )
        return out_path

    def _choose_wfs_endpoint(self, layer_name: str, source_name: str = None) -> str:
        layer_lower = (layer_name or "").lower()
        if source_name:
            endpoints = self.wfs_registry.get_endpoints(source_name)
            if endpoints:
                if len(endpoints) == 1:
                    return endpoints[0]
                prefix = layer_lower.split(":")[0] if ":" in layer_lower else layer_lower
                for ep in endpoints:
                    if prefix and prefix in ep.lower():
                        return ep
                return endpoints[0]
        if layer_lower.startswith("digiroad:"):
            return self.wfs_digiroad
        return self.wfs_vayla

    @staticmethod
    def _set_url_query_parameter(raw_url, name, value):
        """Lisää tai korvaa yksi query-parametri rikkomatta muuta osoitetta."""
        parsed = urllib.parse.urlsplit(str(raw_url or ""))
        pairs = [
            (key, item_value)
            for key, item_value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if key.lower() != str(name).lower()
        ]
        pairs.append((str(name), str(value)))
        return urllib.parse.urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path,
            urllib.parse.urlencode(pairs), parsed.fragment,
        ))

    def _source_endpoint_with_credentials(self, source_name, endpoint):
        """Palauta ajonaikainen palveluosoite lähteen tunnisteilla."""
        if source_name != "Aino":
            return endpoint
        token = self._normalize_aino_token(
            getattr(self, "_runtime_aino_token", "")
        )
        if not token:
            return endpoint
        return self._set_url_query_parameter(endpoint, "token", token)

    @staticmethod
    def _wfs_version_for_endpoint(endpoint):
        """Aino on WFS 1.1; muut nykyiset lähteet käyttävät WFS 2.0:aa."""
        try:
            host = (urllib.parse.urlsplit(str(endpoint or "")).hostname or "").lower()
        except Exception:
            host = ""
        return "1.1.0" if host == "aino.sitowise.com" else "2.0.0"

    def _build_source_auth_headers(self, source_name: str, endpoint=None,
                                   layer_kind=None):
        """Muodosta tunnisteheaderit palvelutyypin mukaan.

        Karttapaikan vanha WFS-osoite hyväksyi joissakin versioissa Basic-
        tunnistautumista, mutta nykyiset INSPIRE WFS -osoitteet ovat avoimia.
        Basic-headerin lähettäminen niihin aiheuttaa 401-vastauksen. Nykyiset
        MML:n OGC API Features -rajapinnat käyttävät API-avainta Basic-
        tunnistautumisessa, mutta header kuuluu vain OGC-pyyntöihin.
        """
        headers = {}
        if source_name == "MML":
            key = (getattr(self, "_runtime_mml_api_key", "") or "").strip()
            if key and layer_kind == "mml_property_ogcapi":
                return self._mml_auth_headers(
                    key, user_agent="ArcGISPro-MMLPropertyOGC/1.0"
                )
            return headers

        if source_name != "Karttapaikka":
            return headers

        key = (getattr(self, "_runtime_karttapaikka_api_key", "") or "").strip()
        endpoint_text = str(endpoint or "").lower()
        is_ogc = (
            layer_kind == "mml_ogcapi"
            or "/maastotiedot/features/" in endpoint_text
            or endpoint_text.endswith("/features/v1")
            or endpoint_text.endswith("/features/v1/")
        )
        if key and is_ogc:
            # MML:n API-avainohjeen mukainen käyttäjätunnus=avain,
            # salasana tyhjä. Avainta ei lisätä URL:iin eikä lokiin.
            token = base64.b64encode(f"{key}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _fetch_wfs_capabilities_with_headers(self, endpoint, headers=None):
        url = endpoint
        for key, value in (
            ("service", "WFS"),
            ("request", "GetCapabilities"),
            ("version", self._wfs_version_for_endpoint(endpoint)),
        ):
            url = self._set_url_query_parameter(url, key, value)
        req_headers = {"User-Agent": "ArcGISPro-Arcpy-WFSDownloader/1.4"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            xml_bytes = response.read()
        root = ET.fromstring(xml_bytes)
        layers = []
        for elem in root.iter():
            if not elem.tag.endswith("FeatureType"):
                continue
            name_text = None
            title_text = None
            for child in elem.iter():
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

    def _fetch_wms_capabilities_with_headers(self, endpoint, headers=None):
        """Hae WMS 1.3.0 -tasot ja säilytä palvelun tekninen nimi."""
        url = endpoint
        for key, value in (
            ("service", "WMS"),
            ("request", "GetCapabilities"),
            ("version", "1.3.0"),
        ):
            url = self._set_url_query_parameter(url, key, value)
        req_headers = {"User-Agent": "ArcGISPro-Arcpy-WFSDownloader/1.4"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            xml_bytes = response.read()
        root = ET.fromstring(xml_bytes)
        layers = []
        seen = set()
        for elem in root.iter():
            if not elem.tag.endswith("Layer"):
                continue
            name_text = None
            title_text = None
            for child in list(elem):
                if child.tag.endswith("Name") and child.text and not name_text:
                    name_text = child.text.strip()
                elif child.tag.endswith("Title") and child.text and not title_text:
                    title_text = child.text.strip()
            if not name_text or name_text in seen:
                continue
            seen.add(name_text)
            namespace = name_text.split(":", 1)[0] if ":" in name_text else ""
            normalized_name = self._wms_match_key(name_text)
            normalized_title = self._wms_match_key(title_text)
            layers.append({
                "id": name_text,
                "title": title_text or name_text.split(":")[-1],
                "source": "Aino",
                "kind": "aino_wms",
                "wms_title": title_text or name_text.split(":")[-1],
                "is_background": (
                    namespace.casefold() == "taustakartat"
                    or "taustakartta" in normalized_name
                    or "taustakartta" in normalized_title
                ),
            })
        return layers

    def _get_aino_layers(self):
        """Hae Ainon WFS- ja kaikki nimetyt WMS-tasot ajonaikaisella tokenilla."""
        token = self._normalize_aino_token(
            getattr(self, "_runtime_aino_token", "")
        )
        if not token:
            return []
        self._runtime_aino_token = token
        endpoint = self.wfs_registry.get_endpoint("Aino")
        if not endpoint:
            return []
        request_endpoint = self._source_endpoint_with_credentials("Aino", endpoint)
        wfs_layers = []
        wms_layers = []
        errors = {}
        try:
            wfs_layers = self._fetch_wfs_capabilities_with_headers(request_endpoint)
        except Exception as ex:
            errors["WFS"] = ex
        try:
            wms_layers = self._fetch_wms_capabilities_with_headers(request_endpoint)
        except Exception as ex:
            errors["WMS"] = ex
        self._aino_catalog_errors = {
            service: self._aino_catalog_error_text(error)
            for service, error in errors.items()
        }
        self._aino_catalog_counts = {
            "WFS": len(wfs_layers),
            "WMS": len(wms_layers),
        }
        if not wfs_layers and not wms_layers:
            if not self._aino_catalog_errors:
                self._aino_catalog_errors = {
                    "OWS": "palvelu ei palauttanut yhtään tasoa"
                }
            details = "; ".join(
                "{}: {}".format(service, self._aino_catalog_errors[service])
                for service in sorted(self._aino_catalog_errors)
            ) or "palvelu ei palauttanut tasoja"
            raise Exception("Aino-tasoluetteloa ei saatu. {}".format(details))
        for service, error_text in sorted(self._aino_catalog_errors.items()):
            self._warn(
                "[VAROITUS] Ainon {}-tasoluettelo epäonnistui; toinen "
                "palvelutyyppi pidetään käytettävissä: {}".format(
                    service, error_text
                )
            )
        result = []
        for entry in wfs_layers:
            item = dict(entry)
            item["title"] = "{} (WFS)".format(
                item.get("title") or item.get("id", "")
            )
            item["endpoint"] = endpoint
            result.append(item)
        for entry in wms_layers:
            item = dict(entry)
            item["wms_title"] = item.get("wms_title") or item.get("title")
            item["title"] = "{} (WMS)".format(
                item.get("title") or item.get("id", "")
            )
            item["endpoint"] = endpoint
            result.append(item)
        return result

    @staticmethod
    def _aino_catalog_error_text(error):
        """Muodosta käyttäjälle turvallinen Aino-luettelovirhe ilman URL:ia."""
        if isinstance(error, urllib.error.HTTPError):
            if error.code in (401, 403):
                return "token hylättiin (HTTP {})".format(error.code)
            return "palvelin vastasi HTTP {}".format(error.code)
        if isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout)):
            return "yhteys epäonnistui"
        return "{}".format(type(error).__name__)

    def _get_karttapaikka_layers(self):
        layers = []
        seen = set()
        # Nykyiset INSPIRE WFS -palvelut ovat avoimia. Älä lähetä niihin
        # vanhaa Basic-headeria, koska palvelin vastaa silloin 401:llä.
        headers = self._build_source_auth_headers("Karttapaikka", layer_kind="wfs")
        for endpoint in self.wfs_registry.get_endpoints("Karttapaikka"):
            try:
                endpoint_layers = self._fetch_wfs_capabilities_with_headers(endpoint, headers=headers)
            except urllib.error.HTTPError as ex:
                self._warn("[VAROITUS] Karttapaikka GetCapabilities epäonnistui '{}': HTTP {}".format(endpoint, ex.code))
                continue
            except Exception as ex:
                self._warn("[VAROITUS] Karttapaikka GetCapabilities epäonnistui '{}': {}".format(endpoint, ex))
                continue

            for lyr in endpoint_layers:
                lid = lyr.get("id")
                # Rakennukset julkaistaan piste- ja polygonipalvelussa samalla
                # typeName-arvolla. Endpoint kuuluu dedup-avaimeen, jotta
                # käyttäjä voi valita molemmat geometriaesitykset.
                seen_key = (endpoint, lid)
                if lid and seen_key not in seen:
                    seen.add(seen_key)
                    layer_entry = dict(lyr)
                    layer_entry["endpoint"] = endpoint
                    endpoint_lower = endpoint.lower()
                    if "bu_mtk_point" in endpoint_lower:
                        layer_entry["title"] = "{} (piste)".format(
                            layer_entry.get("title") or lid
                        )
                    elif "bu_mtk_polygon" in endpoint_lower:
                        layer_entry["title"] = "{} (polygoni)".format(
                            layer_entry.get("title") or lid
                        )
                    layers.append(layer_entry)

        # Nykyinen Maastotietokannan OGC API Features -palvelu on API-avaimen
        # takana ja täydentää INSPIRE WFS -listaa mm. liikenneverkon,
        # rakennusten ja rakenteiden kohdeluokilla. Jos avainta ei ole, WFS-
        # tasot voidaan silti näyttää normaalisti ilman varoitusspämmiä.
        if (getattr(self, "_runtime_karttapaikka_api_key", "") or "").strip():
            try:
                ogc_layers = self._get_karttapaikka_ogc_layers()
            except Exception as ex:
                self._warn(
                    "[VAROITUS] Karttapaikan Maastotiedot OGC API -tasojen "
                    "listaus epäonnistui: {}".format(self._redact_secrets(ex))
                )
                ogc_layers = []
            for lyr in ogc_layers:
                # WFS- ja OGC-kokoelmat voivat käyttää samaa ihmislukemaa,
                # mutta tekninen id on eri palvelussa. Pidä molemmat valittavina.
                layers.append(lyr)
        return layers

    def _get_karttapaikka_ogc_layers(self):
        """Hae nykyisen Maastotiedot OGC API Features -palvelun kokoelmat."""
        endpoint = self.wfs_registry.get_ogc_endpoint("Karttapaikka")
        if not endpoint:
            return []
        base = endpoint.rstrip("/") + "/"
        request_url = base + "collections"
        headers = self._build_source_auth_headers(
            "Karttapaikka", endpoint=endpoint, layer_kind="mml_ogcapi"
        )
        data, raw_text, status, content_type = self._fetch_json(
            request_url,
            timeout=60,
            quiet=True,
            extra_headers=headers,
        )
        if not isinstance(data, dict):
            reason = self._wfs_error_snippet(raw_text, 300) or content_type or "ei vastaussisältöä"
            if status == 401 or status == 403:
                reason = "API-avain hylättiin (HTTP {})".format(status)
            raise Exception(
                "Maastotiedot OGC API -kokoelmia ei voitu hakea (HTTP {}): {}".format(
                    status, reason
                )
            )

        layers = []
        for collection in data.get("collections", []) or []:
            if not isinstance(collection, dict):
                continue
            collection_id = str(collection.get("id") or "").strip()
            if not collection_id:
                continue
            title = str(collection.get("title") or collection_id).strip()
            layers.append({
                "id": collection_id,
                "title": "{} (Maastotiedot)".format(title),
                "source": "Karttapaikka",
                "kind": "mml_ogcapi",
                "endpoint": endpoint,
                "geometry_field": "geometry",
            })
        return layers

    def _get_mml_ogc_layers(self):
        """Hae MML:n kiinteistöaineistojen nykyiset OGC API -kokoelmat.

        Kokoelmalista luetaan palvelusta joka kerta, kun MML:n API-avain
        vaihtuu. Näin uudet kiinteistöaineistot tulevat ArcGIS Pron valikkoon
        ilman että niitä tarvitsee lisätä tähän toolboxiin käsin.
        """
        endpoint = self.wfs_registry.get_ogc_endpoint("MML")
        if not endpoint:
            return []
        key = (getattr(self, "_runtime_mml_api_key", "") or "").strip()
        if not key:
            return []

        base = endpoint.rstrip("/") + "/"
        request_url = base + "collections"
        headers = self._build_source_auth_headers(
            "MML", endpoint=endpoint, layer_kind="mml_property_ogcapi"
        )
        data, raw_text, status, content_type = self._fetch_json(
            request_url,
            timeout=60,
            quiet=True,
            extra_headers=headers,
        )
        if not isinstance(data, dict):
            reason = self._wfs_error_snippet(raw_text, 300) or content_type or "ei vastaussisältöä"
            if status == 401 or status == 403:
                reason = "API-avain hylättiin (HTTP {})".format(status)
            raise Exception(
                "MML:n kiinteistöjen OGC API -kokoelmia ei voitu hakea "
                "(HTTP {}): {}".format(status, reason)
            )

        layers = []
        for collection in data.get("collections", []) or []:
            if not isinstance(collection, dict):
                continue
            collection_id = str(collection.get("id") or "").strip()
            if not collection_id:
                continue
            title = str(collection.get("title") or collection_id).strip()
            title = MML_PROPERTY_COLLECTION_LABELS.get(collection_id, title)
            layers.append({
                "id": collection_id,
                "title": title,
                "source": "MML",
                "kind": "mml_property_ogcapi",
                "endpoint": endpoint,
                "geometry_field": "geometry",
            })
        return layers

    def _get_traficom_oskari_layers(self):
        """Hae Oskarin koko tasoluettelo ja valitse kullekin toimiva lataustapa."""
        endpoint = self.wfs_registry.get_endpoint("Traficom Oskari")
        if not endpoint:
            return []
        query = urllib.parse.urlencode({
            "action_route": "GetHierarchicalMapLayerGroups",
            "srs": "EPSG:3067",
            "lang": "fi",
        })
        request_url = "{}?{}".format(endpoint, query)
        data, raw_text, status, content_type = self._fetch_json(
            request_url, timeout=60, quiet=True
        )
        if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
            reason = self._wfs_error_snippet(raw_text, 300) or content_type or "virheellinen JSON"
            raise Exception(
                "Traficomin Oskari-tasoluettelo epäonnistui (HTTP {}): {}".format(
                    status, reason
                )
            )

        wfs_by_name = {}
        wfs_error = None
        wfs_features = None
        for attempt in range(1, 4):
            try:
                wfs_features = self._fetch_wfs_capabilities_with_headers(
                    TRAFICOM_OPEN_WFS_ENDPOINT
                )
                wfs_error = None
                break
            except Exception as ex:
                wfs_error = ex
                if attempt < 3:
                    time.sleep(0.5 * attempt)
        if wfs_error is None:
            for feature_type in wfs_features or []:
                feature_id = str(feature_type.get("id") or "").strip()
                if feature_id:
                    wfs_by_name[self._wms_match_key(feature_id.split(":")[-1])] = feature_id
        else:
            self._warn(
                "[VAROITUS] Traficomin avoimen WFS:n tasoluetteloa ei saatu: {}. "
                "Oskarin karttatasot jäävät silti käytettäviksi karttakuvina.".format(
                    self._redact_secrets(wfs_error)
                )
            )

        layers = []
        for item in data.get("layers", []):
            if not isinstance(item, dict):
                continue
            layer_id = str(item.get("id") or "").strip()
            if not layer_id:
                continue
            title = str(item.get("name") or item.get("layerName") or layer_id).strip()
            catalog_type = str(item.get("type") or "").strip().lower()
            layer_name = str(item.get("layerName") or "").strip()
            organization = str(item.get("orgName") or "").strip()
            attributes = item.get("attributes")
            geometry_field = (
                attributes.get("geometry")
                if isinstance(attributes, dict) else None
            )

            if catalog_type == "wfslayer":
                kind = "oskari_wfs"
                request_id = layer_id
                layer_endpoint = endpoint
            elif catalog_type == "wmslayer":
                direct_wfs_id = wfs_by_name.get(
                    self._wms_match_key(layer_name.split(":")[-1])
                )
                if direct_wfs_id:
                    kind = "wfs"
                    request_id = direct_wfs_id
                    layer_endpoint = TRAFICOM_OPEN_WFS_ENDPOINT
                else:
                    kind = "oskari_wms"
                    request_id = layer_id
                    layer_endpoint = endpoint
            elif catalog_type == "wmtslayer":
                kind = "oskari_wmts"
                request_id = layer_id
                layer_endpoint = TRAFICOM_WMTS_ENDPOINT
            else:
                # Säilytä tuntemattomat Oskari-katalogityypit valittavina.
                # Niitä ei kuitenkaan nimetä WFS-, WMS- tai WMTS-tasoksi.
                kind = "oskari_wms"
                request_id = layer_id
                layer_endpoint = endpoint

            layers.append({
                "id": request_id,
                "title": title,
                "source": "Traficom Oskari",
                "kind": kind,
                "endpoint": layer_endpoint,
                "geometry_field": geometry_field,
                "layer_name": layer_name,
                "version": item.get("version"),
                "catalog_type": catalog_type,
                "catalog_id": layer_id,
                "organization": organization,
                "style": item.get("style"),
            })
        return layers

    def _get_layer_entries_for_sources(self, source_names):
        entries = []
        mapping = {}
        for source_name in source_names:
            source_type = self.wfs_registry.get_type(source_name)
            if source_type == "overpass":
                source_entries = OverpassAdapter.get_layers()
            elif source_type == "oskari":
                try:
                    source_entries = self._get_traficom_oskari_layers()
                except Exception as ex:
                    self._warn(
                        "[VAROITUS] Traficomin Oskari-tasoluettelo epäonnistui: {}".format(
                            self._redact_secrets(ex)
                        )
                    )
                    source_entries = []
            elif source_type == "mml_raster":
                source_entries = []
                try:
                    for disp in self._get_mml_layers_cached(api_key=self._runtime_mml_api_key):
                        source_entries.append({
                            "id": self._mml_layer_mapping.get(disp, disp),
                            "title": disp,
                            "kind": "mml_raster"
                        })
                except Exception as ex:
                    self._warn("[VAROITUS] MML karttatasojen listaus epäonnistui: {}".format(ex))
            elif source_type == "mml_ogcapi":
                source_entries = []
                try:
                    source_entries = self._get_mml_ogc_layers()
                except Exception as ex:
                    self._warn(
                        "[VAROITUS] MML:n kiinteistöjen OGC API -tasojen "
                        "listaus epäonnistui: {}".format(
                            self._redact_secrets(ex)
                        )
                    )
            elif source_type == "mml_karttakuva":
                source_entries = []
                try:
                    for disp in self._get_mml_karttakuva_layers_cached(
                        user=self._runtime_karttakuva_user, password=self._runtime_karttakuva_pass
                    ):
                        source_entries.append({
                            "id": self._mml_karttakuva_layer_mapping.get(disp, disp),
                            "title": disp,
                            "kind": "mml_karttakuva"
                        })
                except Exception as ex:
                    self._warn("[VAROITUS] MML Karttakuva -tasojen listaus ep\u00e4onnistui: {}".format(ex))
            elif source_type == "kapsi_wms":
                source_entries = []
                try:
                    for disp in self._get_kapsi_layers_cached():
                        source_entries.append({
                            "id": self._kapsi_layer_mapping.get(disp, disp),
                            "title": disp,
                            "kind": "kapsi_wms"
                        })
                except Exception as ex:
                    self._warn("[VAROITUS] Kapsi karttatasojen listaus ep\u00e4onnistui: {}".format(ex))
            elif source_name == "Karttapaikka":
                try:
                    source_entries = self._get_karttapaikka_layers()
                except Exception as ex:
                    self._warn("[VAROITUS] Karttapaikka-tasojen listaus epäonnistui: {}".format(ex))
                    source_entries = []
            elif source_name == "Aino":
                try:
                    source_entries = self._get_aino_layers()
                except Exception as ex:
                    self._warn(
                        "[VAROITUS] Aino-tasojen listaus epäonnistui: {}".format(
                            self._redact_secrets(ex)
                        )
                    )
                    source_entries = []
            else:
                try:
                    source_entries = self.wfs_registry.get_capabilities(source_name)
                except Exception as ex:
                    self._warn("[VAROITUS] GetCapabilities epäonnistui lähteelle '{}': {}".format(source_name, ex))
                    source_entries = []

            for entry in source_entries:
                title = entry.get("title") or entry.get("id", "")
                label = self._format_layer_label(title, source_name)
                unique_label = label
                counter = 2
                while unique_label in mapping:
                    unique_label = "{} ({})".format(label, counter)
                    counter += 1
                mapping[unique_label] = {
                    "source": source_name,
                    "id": entry.get("id"),
                    "kind": entry.get("kind", source_type),
                    "title": title,
                    "endpoint": entry.get("endpoint"),
                    "geometry_field": entry.get("geometry_field"),
                    "wms_title": entry.get("wms_title"),
                    "is_background": bool(entry.get("is_background", False)),
                    "layer_name": entry.get("layer_name"),
                    "version": entry.get("version"),
                    "catalog_type": entry.get("catalog_type"),
                    "catalog_id": entry.get("catalog_id"),
                    "organization": entry.get("organization"),
                    "style": entry.get("style"),
                }
                entries.append(unique_label)

        entries = sorted(list(set(entries)), key=lambda x: self._norm(x.split(" - ")[0]))
        self._layer_mapping = mapping
        return entries

    def _extent_bbox_4326(self, boundary_fc):
        tmp = os.path.join(self._scratch_gdb(), "bnd_wgs84_{}".format(uuid.uuid4().hex[:8]))
        arcpy.management.Project(boundary_fc, tmp, arcpy.SpatialReference(4326))
        ext = arcpy.Describe(tmp).extent
        self._safe_delete(tmp)
        return "{},{},{},{}".format(ext.YMin, ext.XMin, ext.YMax, ext.XMax)

    def _define_osm_source_projection(self, feature_class):
        """Varmista Overpass-GeoJSON-väliaineiston WGS84-lähde-CRS."""
        try:
            source_sr = arcpy.Describe(feature_class).spatialReference
            source_code = getattr(source_sr, "factoryCode", 0) if source_sr else 0
        except Exception:
            source_code = 0
        if not source_code:
            arcpy.management.DefineProjection(feature_class, arcpy.SpatialReference(4326))
            return True
        return False

    def _fetch_overpass_json(self, query):
        """Lähetä Overpass-kysely ja vaihda tarvittaessa varapalveluun."""
        overpass_urls = self.wfs_registry.get_endpoints("OpenStreetMap")
        if not overpass_urls:
            raise Exception("OpenStreetMap Overpass -palveluosoite puuttuu.")
        post_body = urllib.parse.urlencode({"data": query}).encode("utf-8")
        endpoint_errors = []
        for overpass_url in overpass_urls:
            try:
                req = urllib.request.Request(
                    overpass_url, data=post_body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "ArcGISPro-Suomenvaylat-OSM/1.1",
                    }
                )
                with urllib.request.urlopen(req, timeout=150) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise Exception("Overpass palautti odottamattoman JSON-rakenteen.")

                # Overpass voi vastata HTTP 200:lla, vaikka kysely olisi
                # aikakatkaistu tai resurssiraja olisi ylittynyt. Tällöin
                # virhe on payloadin remark-kentässä ja elements voi olla
                # tyhjä. Älä hyväksy sitä oikeaksi nollatulokseksi, vaan
                # kokeile seuraavaa palvelinta ja lopulta pienempää ruudukkoa.
                remark = str(payload.get("remark") or "").strip()
                if remark:
                    raise Exception("Overpass palautti virheen: {}".format(remark[:500]))
                if not isinstance(payload.get("elements"), list):
                    raise Exception("Overpass-vastauksesta puuttuu elements-taulukko.")
                return payload
            except Exception as ex:
                endpoint_errors.append("{}: {}".format(
                    self._sanitize_url(overpass_url), ex
                ))
        raise Exception(
            "Kaikki OpenStreetMap Overpass -yhteydet epäonnistuivat: {}".format(
                " | ".join(endpoint_errors)
            )
        )

    def _fetch_osm_feature_chunks(self, layer_id, boundary_fc, max_grid=4):
        bbox_wgs84 = self._extent_bbox_4326(boundary_fc)
        boundary_sr = arcpy.Describe(boundary_fc).spatialReference

        def _fetch_one(osm_bbox, batch_size):
            query = OverpassAdapter.build_query(layer_id, osm_bbox)
            response_data = self._fetch_overpass_json(query)

            if layer_id == GeofabrikPOIAdapter.LAYER_ID:
                geojson = GeofabrikPOIAdapter.to_geojson(response_data)
            else:
                geojson = OverpassAdapter.to_geojson(response_data)
            if not geojson.get("features"):
                return [], 0
            temp_json_path = os.path.join(self._scratch_folder(), "osm_{}.geojson".format(uuid.uuid4().hex))
            with open(temp_json_path, "w", encoding="utf-8") as handle:
                json.dump(geojson, handle, ensure_ascii=False)
            temp_fc = os.path.join(self._scratch_gdb(), "osm_fc_{}".format(uuid.uuid4().hex[:10]))
            try:
                # Esrin JSON To Features vaatii GeoJSONille geometriatyypin.
                # POI-adapteri tuottaa aina pisteitä; ilman POINT-parametria
                # ArcGIS voi luoda tyhjän feature classin täysin kelvollisesta
                # GeoJSONista (havaittu Oulun kuntahaussa).
                if layer_id == GeofabrikPOIAdapter.LAYER_ID:
                    arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc, "POINT")
                else:
                    arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc)
            finally:
                try:
                    os.remove(temp_json_path)
                except Exception:
                    pass

            converted_count = int(arcpy.management.GetCount(temp_fc)[0])
            if converted_count == 0:
                self._safe_delete(temp_fc)
                raise Exception(
                    "ArcGIS ei muuntanut Overpass-vastauksen {} kohdetta paikkatietokohteiksi."
                    .format(len(geojson.get("features", [])))
                )
            # Overpass palauttaa koordinaatit aina WGS84-longitude/latitude-
            # muodossa, mutta GeoJSON-väliaineistoon ei välttämättä tallennu
            # CRS-metadataa. Määritä lähde-CRS ennen Projectia, muuten ArcGIS
            # antaa virheen 000517 (koordinaattijärjestelmää ei ole määritetty).
            self._define_osm_source_projection(temp_fc)
            projected_fc = os.path.join(self._scratch_gdb(), "osm_prj_{}".format(uuid.uuid4().hex[:10]))
            arcpy.management.Project(temp_fc, projected_fc, boundary_sr)
            self._safe_delete(temp_fc)
            return [projected_fc], len(geojson.get("features", []))

        # POI-haku voi olla tavallista OSM-tasoa tiheämpi, joten sille on
        # yksi lisääntynyt ruudutustaso ennen lopullista virhettä.
        if layer_id == GeofabrikPOIAdapter.LAYER_ID:
            max_grid = max(8, max_grid)
            grid_levels = [1, 2, 4, max_grid]
        else:
            grid_levels = [1, 2, max_grid]
        resilience = ResilienceStrategy(max_batch_size=1, grid_levels=grid_levels)
        return resilience.execute_with_fallback(_fetch_one, bbox_wgs84)

    def _is_heavy_layer(self, layer_clean: str) -> bool:
        last = (layer_clean.split(":")[-1] if layer_clean else "")
        last_n = self._norm(last)
        for p in self.heavy_layer_prefixes:
            if last_n.startswith(self._norm(p)):
                return True
        for e in self.heavy_layer_exact:
            if last_n == self._norm(e):
                return True
        return False

    def _get_kunta_name_field(self, fc: str):
        fields = [f.name for f in arcpy.ListFields(fc)]
        fields_lower = {f.lower(): f for f in fields}
        candidates = ["nimi", "name", "namn", "kunta_nimi", "kunta", "municipality", "kommun"]
        for c in candidates:
            if c in fields_lower:
                return fields_lower[c]
        return None

    def _iter_kunnat(self, fc: str, name_field: str = None):
        oid_field = arcpy.Describe(fc).OIDFieldName
        if name_field:
            with arcpy.da.SearchCursor(fc, [oid_field, name_field, "SHAPE@"]) as cur:
                for oid, nm, geom in cur:
                    yield oid, (nm or f"OID_{oid}"), geom
        else:
            with arcpy.da.SearchCursor(fc, [oid_field, "SHAPE@"]) as cur:
                for oid, geom in cur:
                    yield oid, f"OID_{oid}", geom

    def _parse_multivalue(self, value_as_text: str):
        if not value_as_text:
            return []
        parts = [p.strip().strip("'").strip('"') for p in value_as_text.split(";") if p.strip()]
        seen = set()
        out = []
        for p in parts:
            key = self._norm(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _safe_delete(self, path_or_layer):
        try:
            if path_or_layer:
                arcpy.management.Delete(path_or_layer)
        except Exception:
            pass

    def _is_filesystem_workspace(self, workspace: str) -> bool:
        if (
            self._runtime_workspace
            and workspace == self._runtime_workspace
            and self._runtime_workspace_is_folder is not None
        ):
            return self._runtime_workspace_is_folder
        # Rasterilaattojen nimivalidointi kutsuu tätä kerran laattaa kohti.
        # Describe samalle kansiolle sadasti ajossa on puhdasta hukkaa.
        cache = getattr(self, "_workspace_kind_cache", None)
        if cache is None:
            cache = {}
            self._workspace_kind_cache = cache
        key = str(workspace)
        if key in cache:
            return cache[key]
        try:
            d = arcpy.Describe(workspace)
            result = (getattr(d, "workspaceType", "") or "").lower() == "filesystem"
        except Exception:
            result = False
        cache[key] = result
        return result

    def _init_workspace_cache(self, workspace: str):
        self._runtime_workspace = workspace
        self._runtime_workspace_is_folder = None
        self._runtime_workspace_validated = False
        try:
            d = arcpy.Describe(workspace)
            self._runtime_workspace_is_folder = (getattr(d, "workspaceType", "") or "").lower() == "filesystem"
            self._runtime_workspace_validated = True
        except Exception:
            self._runtime_workspace_is_folder = False

    def _wfs_supports_cql(self, source_name) -> bool:
        return source_name in self.heavy_chunk_sources

    def _boundary_extent_from_features(self, boundary_fc):
        """Compute extent from actual features, respecting definition queries on feature layers."""
        xmin = ymin = float('inf')
        xmax = ymax = float('-inf')
        with arcpy.da.SearchCursor(boundary_fc, ["SHAPE@"]) as cur:
            for row in cur:
                if row[0]:
                    e = row[0].extent
                    xmin = min(xmin, e.XMin)
                    ymin = min(ymin, e.YMin)
                    xmax = max(xmax, e.XMax)
                    ymax = max(ymax, e.YMax)
        if xmin == float('inf'):
            return arcpy.Describe(boundary_fc).extent
        return arcpy.Extent(xmin, ymin, xmax, ymax)

    def _geometry_to_2d(self, geometry):
        shape_type = (getattr(geometry, "type", "") or "").lower()
        spatial_reference = getattr(geometry, "spatialReference", None)

        if shape_type == "polygon":
            parts = arcpy.Array()
            for part in geometry:
                ring = arcpy.Array()
                has_points = False
                for point in part:
                    if point is None:
                        if has_points:
                            parts.add(ring)
                            ring = arcpy.Array()
                            has_points = False
                    else:
                        ring.add(arcpy.Point(point.X, point.Y))
                        has_points = True
                if has_points:
                    parts.add(ring)
            return arcpy.Polygon(parts, spatial_reference, False, False)

        if shape_type == "polyline":
            parts = arcpy.Array()
            for part in geometry:
                path = arcpy.Array()
                has_points = False
                for point in part:
                    if point is not None:
                        path.add(arcpy.Point(point.X, point.Y))
                        has_points = True
                if has_points:
                    parts.add(path)
            return arcpy.Polyline(parts, spatial_reference, False, False)

        if shape_type == "point":
            point = geometry.firstPoint
            return arcpy.PointGeometry(arcpy.Point(point.X, point.Y), spatial_reference, False, False)

        raise ValueError("CQL-rajaus tukee vain Polygon-, Polyline- tai Point-geometriaa.")

    def _wkt_force_2d(self, wkt):
        """GeoServerin CQL-jäsennin ei hyväksy 'POLYGON Z'-tyyppisiä WKT-merkkijonoja."""
        if not wkt:
            return wkt
        wkt = re.sub(r"^\s*([A-Za-z]+)\s+(?:ZM|Z|M)\s*\(", r"\1 (", wkt)
        return re.sub(
            r"-?\d[\d.eE+-]*(?:\s+-?\d[\d.eE+-]*){2,}",
            lambda m: " ".join(m.group(0).split()[:2]),
            wkt,
        )

    def _boundary_wkt_3067(self, boundary_fc, for_cql=False):
        geoms = []
        with arcpy.da.SearchCursor(boundary_fc, ["SHAPE@"]) as cur:
            for row in cur:
                if row[0]:
                    geoms.append(row[0])
        if not geoms:
            return None
        if len(geoms) == 1:
            merged = geoms[0]
        else:
            try:
                merged = geoms[0]
                for geom in geoms[1:]:
                    merged = merged.union(geom)
            except Exception:
                merged = geoms[0]
        if for_cql:
            try:
                merged = merged.generalize(50)
            except Exception:
                pass
            merged = self._geometry_to_2d(merged)
            return self._wkt_force_2d(merged.WKT)
        return merged.WKT

    def _boundary_geometry_metrics(self, geometry):
        if not geometry:
            return {"parts": 0, "points": 0}
        return {
            "parts": int(getattr(geometry, "partCount", 0) or 0),
            "points": int(getattr(geometry, "pointCount", 0) or 0),
        }

    def _merged_boundary_geometry(self, boundary_fc):
        geoms = []
        with arcpy.da.SearchCursor(boundary_fc, ["SHAPE@"]) as cur:
            for row in cur:
                if row and row[0]:
                    geoms.append(row[0])
        if not geoms:
            return None, 0
        merged = geoms[0]
        try:
            for geom in geoms[1:]:
                merged = merged.union(geom)
        except Exception:
            # Sama turvallinen varakäytös kuin nykyisessä WKT-toteutuksessa.
            merged = geoms[0]
        return merged, len(geoms)

    def _prepare_cql_wkts(self, boundary_fc, full_wkt, tolerance=50.0,
                          max_encoded_chars=6500, max_grid_size=8):
        """Muodosta tarvittaessa pienemmät CQL-geometriat.

        Tarkkaa ``boundary_fc``-aineistoa ei muuteta. Pilkotut geometriat ovat
        vain palvelimelle lähetettävää INTERSECTS-suodatinta varten. Nykyinen
        2D-muunnos ja Z-arvojen poistaminen säilyvät samoina.
        """
        prep_start = time.perf_counter()
        merged, feature_count = self._merged_boundary_geometry(boundary_fc)
        if merged is None or not full_wkt:
            return [], {
                "feature_count": feature_count, "original": {"parts": 0, "points": 0},
                "simplified": {"parts": 0, "points": 0}, "tolerance": tolerance,
                "unit": "tuntematon", "elapsed_s": time.perf_counter() - prep_start,
            }

        original_metrics = self._boundary_geometry_metrics(merged)
        simplified = merged
        try:
            simplified = merged.generalize(tolerance)
        except Exception:
            pass
        simplified = self._geometry_to_2d(simplified)
        simplified_metrics = self._boundary_geometry_metrics(simplified)
        sr = getattr(merged, "spatialReference", None)
        unit = getattr(sr, "linearUnitName", None) or "tuntematon"
        encoded_len = len(urllib.parse.quote(
            self._build_cql_intersects("geometry", full_wkt), safe="(),'="
        ))
        info = {
            "feature_count": feature_count,
            "original": original_metrics,
            "simplified": simplified_metrics,
            "tolerance": tolerance,
            "unit": unit,
            "encoded_chars": encoded_len,
        }
        if encoded_len <= max_encoded_chars or (getattr(merged, "type", "") or "").lower() != "polygon":
            info["elapsed_s"] = time.perf_counter() - prep_start
            return [full_wkt], info

        extent = merged.extent
        best_chunks = [full_wkt]
        for grid_size in (2, 4, 8):
            if grid_size > max_grid_size:
                break
            dx = (extent.XMax - extent.XMin) / float(grid_size)
            dy = (extent.YMax - extent.YMin) / float(grid_size)
            chunks = []
            longest = 0
            for ix in range(grid_size):
                for iy in range(grid_size):
                    xmin = extent.XMin + ix * dx
                    ymin = extent.YMin + iy * dy
                    xmax = extent.XMax if ix == grid_size - 1 else xmin + dx
                    ymax = extent.YMax if iy == grid_size - 1 else ymin + dy
                    ring = arcpy.Array([
                        arcpy.Point(xmin, ymin), arcpy.Point(xmax, ymin),
                        arcpy.Point(xmax, ymax), arcpy.Point(xmin, ymax),
                        arcpy.Point(xmin, ymin),
                    ])
                    tile = arcpy.Polygon(ring, sr, False, False)
                    try:
                        piece = merged.intersect(tile, 4)
                    except Exception:
                        piece = None
                    if not piece or getattr(piece, "isEmpty", True):
                        continue
                    try:
                        piece = piece.generalize(tolerance)
                    except Exception:
                        pass
                    piece = self._geometry_to_2d(piece)
                    piece_wkt = self._wkt_force_2d(piece.WKT)
                    if not piece_wkt:
                        continue
                    chunks.append(piece_wkt)
                    longest = max(longest, len(urllib.parse.quote(
                        self._build_cql_intersects("geometry", piece_wkt), safe="(),'="
                    )))
            if chunks:
                best_chunks = chunks
                info["grid_size"] = grid_size
                info["longest_encoded_chunk"] = longest
            if chunks and longest <= max_encoded_chars:
                break

        info["elapsed_s"] = time.perf_counter() - prep_start
        return best_chunks, info

    def _export_geometry_only(self, source_fc: str, workspace: str, out_name: str):
        desc = arcpy.Describe(source_fc)
        shape_type = desc.shapeType if hasattr(desc, "shapeType") else "POLYGON"
        sr = desc.spatialReference if hasattr(desc, "spatialReference") else None

        final_name = out_name
        if self._is_filesystem_workspace(workspace) and not final_name.lower().endswith(".shp"):
            final_name = final_name + ".shp"

        out_fc = os.path.join(workspace, final_name)
        self._safe_delete(out_fc)
        arcpy.management.CreateFeatureclass(workspace, final_name, shape_type, spatial_reference=sr)
        with arcpy.da.SearchCursor(source_fc, ["SHAPE@"]) as s_cur:
            with arcpy.da.InsertCursor(out_fc, ["SHAPE@"]) as i_cur:
                for row in s_cur:
                    if row and row[0]:
                        geometry = row[0]
                        try:
                            geometry = self._geometry_to_2d(geometry)
                        except (AttributeError, ValueError):
                            pass
                        i_cur.insertRow([geometry])
        return out_fc

    def _sql_quote(self, value):
        return "'{}'".format(str(value).replace("'", "''"))

    def _find_resources_dir(self):
        # Polku ei muutu ajon aikana, mutta updateMessages kutsuu tätä joka
        # validaatiokierroksella. Ilman välimuistia dialogi tekee turhan
        # levyhaun jokaisen näppäilyn jälkeen.
        cached = getattr(self, "_resources_dir_cache", "__unset__")
        if cached != "__unset__":
            return cached
        result = self._find_resources_dir_uncached()
        self._resources_dir_cache = result
        return result

    def _find_resources_dir_uncached(self):
        base = os.path.dirname(os.path.abspath(__file__))
        candidates = []
        for up in range(0, 7):
            here = os.path.abspath(os.path.join(base, *([".."] * up)))
            candidates.append(os.path.join(here, "Resources"))
            candidates.append(os.path.join(here, "Toolboxes", "Resources"))

        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return None

    def _find_admin_gpkg(self, resources_dir=None):
        if resources_dir is None:
            cached = getattr(self, "_admin_gpkg_cache", "__unset__")
            if cached != "__unset__":
                return cached
            resources_dir = self._find_resources_dir()
            result = self._admin_gpkg_from_dir(resources_dir)
            self._admin_gpkg_cache = result
            return result
        return self._admin_gpkg_from_dir(resources_dir)

    def _admin_gpkg_from_dir(self, resources_dir):
        if not resources_dir:
            return None
        gpkg_path = os.path.join(resources_dir, self.admin_gpkg_name)
        if os.path.exists(gpkg_path):
            return gpkg_path
        return None

    def _credentials_file_path(self):
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            cred_dir = os.path.join(appdata, "Suomenvaylat")
            return os.path.join(cred_dir, "service_credentials.json")
        resources_dir = self._find_resources_dir()
        if resources_dir:
            return os.path.join(resources_dir, "service_credentials.json")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_credentials.json")

    def _load_credentials_store(self):
        if self._credentials_cache is not None:
            return self._credentials_cache

        path = self._credentials_file_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                self._credentials_cache = data if isinstance(data, dict) else {}
            else:
                self._credentials_cache = {}
        except Exception:
            self._credentials_cache = {}
        return self._credentials_cache

    def _save_credentials_store(self):
        path = self._credentials_file_path()
        data = self._credentials_cache if isinstance(self._credentials_cache, dict) else {}
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

    def _dpapi_transform(self, payload, protect=True):
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        if os.name != "nt":
            raise RuntimeError("DPAPI on käytettävissä vain Windowsissa")
        raw = payload if isinstance(payload, bytes) else bytes(payload)
        raw_buffer = ctypes.create_string_buffer(raw, len(raw))
        in_blob = DATA_BLOB(
            len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte))
        )
        out_blob = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(in_blob), "Suomenvaylat", None, None, None,
                flags, ctypes.byref(out_blob),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None,
                flags, ctypes.byref(out_blob),
            )
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            local_handle = wintypes.HLOCAL(
                ctypes.cast(out_blob.pbData, ctypes.c_void_p).value
            )
            kernel32.LocalFree(local_handle)

    def _protect_secret(self, value):
        encrypted = self._dpapi_transform(value.encode("utf-8"), protect=True)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")

    def _unprotect_secret(self, value):
        encrypted = base64.b64decode(value[len("dpapi:"):].encode("ascii"))
        return self._dpapi_transform(encrypted, protect=False).decode("utf-8")

    def _get_saved_secret(self, key_name):
        store = self._load_credentials_store()
        val = store.get(key_name, "")
        if not isinstance(val, str) or not val:
            return ""
        if val.startswith("dpapi:"):
            try:
                return self._unprotect_secret(val)
            except Exception as ex:
                self._warn("[VAROITUS] Tallennetun tunnisteen avaaminen epäonnistui: {}".format(ex))
                return ""
        # Migroi aiemman version selväkielinen arvo heti DPAPI-salaukseen.
        try:
            store[key_name] = self._protect_secret(val)
            self._credentials_cache = store
            self._save_credentials_store()
        except Exception as ex:
            self._warn(
                "[VAROITUS] Vanhan selväkielisen tunnisteen turvallinen migraatio epäonnistui; "
                "arvoa ei kirjoitettu uudelleen: {}".format(ex)
            )
        return val

    def _set_saved_secret(self, key_name, value):
        if not (value or "").strip():
            return
        store = self._load_credentials_store()
        try:
            store[key_name] = self._protect_secret(value.strip())
        except Exception as ex:
            self._warn(
                "[VAROITUS] Tunnistetta ei tallennettu, koska käyttäjäkohtainen salaus epäonnistui: {}".format(ex)
            )
            return
        self._credentials_cache = store
        try:
            self._save_credentials_store()
        except Exception as ex:
            self._warn("[VAROITUS] API-avaimen tallennus epäonnistui: {}".format(ex))

    def _list_gpkg_featureclasses(self, gpkg_path):
        fc_paths = []
        try:
            for dirpath, dirnames, filenames in arcpy.da.Walk(gpkg_path, datatype="FeatureClass"):
                for fc in filenames:
                    fc_paths.append(os.path.join(dirpath, fc))
        except Exception:
            old_ws = arcpy.env.workspace
            try:
                arcpy.env.workspace = gpkg_path
                for fc in (arcpy.ListFeatureClasses() or []):
                    fc_paths.append(os.path.join(gpkg_path, fc))
            finally:
                arcpy.env.workspace = old_ws
        return fc_paths

    def _find_fc_in_gpkg(self, gpkg_path, layer_name):
        cache_key = (gpkg_path, layer_name)
        if cache_key in self._admin_fc_cache:
            return self._admin_fc_cache[cache_key]

        target = layer_name.lower()
        all_fcs = self._list_gpkg_featureclasses(gpkg_path)
        for fc in all_fcs:
            bn = os.path.basename(fc).lower()
            if bn == target or bn == ("main." + target) or bn == ("main_" + target):
                self._admin_fc_cache[cache_key] = fc
                return fc

        for fc in all_fcs:
            bn = os.path.basename(fc).lower()
            if target in bn:
                self._admin_fc_cache[cache_key] = fc
                return fc

        return None

    def _get_string_fields(self, fc):
        fields = []
        for f in arcpy.ListFields(fc):
            if f.type == "String" and f.name.upper() not in ["SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "OBJECTID", "FID"]:
                fields.append(f.name)
        return fields

    def _score_text_field(self, fc, field_name):
        score = 0
        vals = []
        try:
            with arcpy.da.SearchCursor(fc, [field_name]) as cur:
                for row in cur:
                    v = row[0]
                    if v is None:
                        continue
                    txt = str(v).strip()
                    if txt:
                        vals.append(txt)
                    if len(vals) >= 25:
                        break
        except Exception:
            return -9999

        if not vals:
            return -9999

        uniq = len(set(vals))
        avg_len = sum(len(v) for v in vals) / float(len(vals))
        alpha_count = sum(1 for v in vals if any(ch.isalpha() for ch in v))
        digit_only_count = sum(1 for v in vals if v.isdigit())

        score += uniq * 5
        score += avg_len
        score += alpha_count * 2
        score -= digit_only_count * 10

        lname = field_name.lower()
        if "nimi" in lname:
            score += 100
        if lname in ["name", "label", "teksti", "text"]:
            score += 50
        if "koodi" in lname or "code" in lname or lname.endswith("id") or lname == "id":
            score -= 50

        return score

    def _pick_name_field(self, fc, extent_type):
        cache_key = (fc, extent_type)
        if cache_key in self._admin_namefield_cache:
            return self._admin_namefield_cache[cache_key]

        fields = self._get_string_fields(fc)
        if not fields:
            raise Exception("Tasolta {} ei löytynyt yhtään tekstikenttää nimeä varten.".format(fc))

        candidates = []
        if extent_type == "Kunta/Kaupunki":
            candidates = [
                "nimi", "NIMI", "kuntanimi", "KUNTANIMI", "kunta_nimi", "KUNTA_NIMI",
                "nimi_suomi", "NIMI_SUOMI", "namefin", "NAMEFIN", "name", "NAME"
            ]
        elif extent_type == "Maakunta":
            candidates = [
                "nimi", "NIMI", "maakunta_nimi", "MAAKUNTA_NIMI", "nimi_suomi",
                "NIMI_SUOMI", "namefin", "NAMEFIN", "name", "NAME"
            ]
        elif extent_type == "Elinvoimakeskus":
            candidates = [
                "nimi", "NIMI", "elinvoimakeskus_nimi", "ELINVOIMAKESKUS_NIMI",
                "nimi_suomi", "NIMI_SUOMI", "namefin", "NAMEFIN", "name", "NAME"
            ]
        elif extent_type == "Hyvinvointialue":
            candidates = [
                "nimi", "NIMI", "hyvinvointialue_nimi", "HYVINVOINTIALUE_NIMI",
                "nimi_suomi", "NIMI_SUOMI", "namefin", "NAMEFIN", "name", "NAME"
            ]
        elif extent_type == "Koko Suomi":
            candidates = ["nimi", "NIMI", "name", "NAME"]

        field_lookup = {f.lower(): f for f in fields}
        for cand in candidates:
            if cand.lower() in field_lookup:
                chosen = field_lookup[cand.lower()]
                self._admin_namefield_cache[cache_key] = chosen
                return chosen

        scored = sorted([(self._score_text_field(fc, f), f) for f in fields], reverse=True)
        chosen = scored[0][1]
        self._admin_namefield_cache[cache_key] = chosen
        return chosen

    def _read_distinct_values(self, fc, field_name):
        vals = set()
        with arcpy.da.SearchCursor(fc, [field_name]) as cur:
            for row in cur:
                v = row[0]
                if v is None:
                    continue
                txt = str(v).strip()
                if txt:
                    vals.add(txt)
        return sorted(vals)

    def _get_extent_fc_and_namefield(self, extent_type, resources_dir=None):
        if extent_type not in self.admin_layer_names:
            raise Exception("Tuntematon paikallisen aluerajauksen tyyppi: {}".format(extent_type))

        gpkg = self._find_admin_gpkg(resources_dir)
        if not gpkg:
            raise Exception("Paikallista hallinnolliset_aluejaot.gpkg-aineistoa ei löydy Resources-kansiosta.")

        layer_name = self.admin_layer_names[extent_type]
        fc = self._find_fc_in_gpkg(gpkg, layer_name)
        if not fc:
            raise Exception("Tasoa '{}' ei löydy geopackagesta: {}".format(layer_name, gpkg))

        name_field = None
        if extent_type != "Koko Suomi":
            name_field = self._pick_name_field(fc, extent_type)

        return fc, name_field

    def _get_extent_choices(self, extent_type, resources_dir=None):
        cache_key = (extent_type, resources_dir or "")
        if cache_key in self._admin_choices_cache:
            return self._admin_choices_cache[cache_key]

        fc, name_field = self._get_extent_fc_and_namefield(extent_type, resources_dir)
        if not name_field:
            self._admin_choices_cache[cache_key] = []
            return []

        values = self._read_distinct_values(fc, name_field)
        self._admin_choices_cache[cache_key] = values
        return values

    # ---------------------------
    # HTTP / WFS
    # ---------------------------
    def _transport(self):
        """Palauta jaettu HTTP-yhteyspooli (luodaan tarvittaessa)."""
        transport = getattr(self, "_http_transport", None)
        if transport is None:
            transport = HttpTransport()
            self._http_transport = transport
        return transport

    def _log_retry(self, request_url, attempt, max_attempts, reason, delay, quiet=False):
        """Kerro uudelleenyrityksestä ilman, että tunnisteet päätyvät lokiin.

        Uudelleenyritys koskee vain ohimeneviä verkkovirheitä ja 5xx/429-
        vastauksia. CQL:n varareittien 400/414 eivät koskaan päädy tänne, joten
        tämä viesti kertoo aina aidosta häiriöstä ja lokitetaan aina.
        """
        try:
            self._warn(
                "[VAROITUS] Verkkopyyntö epäonnistui ({}) palvelussa {}. "
                "Yritys {}/{}, uusi yritys {:.1f} s kuluttua.".format(
                    reason, self._sanitize_url(request_url), attempt,
                    max_attempts, delay,
                )
            )
        except Exception:
            pass

    def _retry_delay(self, attempt, retry_after=None):
        """Eksponentiaalinen viive; palvelimen Retry-After voittaa."""
        if retry_after:
            try:
                return max(0.0, min(30.0, float(str(retry_after).strip())))
            except Exception:
                pass
        return min(8.0, 0.5 * (2 ** max(0, attempt - 1)))

    def _fetch_json(self, request_url: str, timeout: int = 60, quiet: bool = False,
                    extra_headers=None, post_data=None, timings=None, max_attempts=None):
        timings = timings if timings is not None else PhaseMetrics()
        build_start = time.perf_counter()
        headers = {
            "Accept": "application/json, application/geo+json, text/plain, */*",
            "User-Agent": "ArcGISPro-Arcpy-WFSDownloader/1.4"
        }
        if post_data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if extra_headers:
            headers.update(extra_headers)
        body = urllib.parse.urlencode(post_data).encode("utf-8") if post_data is not None else None
        method = "POST" if post_data is not None else "GET"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        timings.add("requestin muodostaminen", time.perf_counter() - build_start)
        status = None
        ctype = ""
        raw_text = ""

        if max_attempts is None:
            max_attempts = getattr(self, "_http_max_attempts", 3)
        max_attempts = max(1, int(max_attempts))
        transport = self._transport()
        raw_bytes = None
        last_error = None

        for attempt in range(1, max_attempts + 1):
            network_start = time.perf_counter()
            try:
                status, resp_headers, raw_bytes, read_s = transport.request(
                    request_url, method=method, headers=headers,
                    body=body, timeout=timeout,
                )
                elapsed = time.perf_counter() - network_start
                timings.add("verkkopyyntö", max(0.0, elapsed - read_s))
                timings.add("vastauksen lukeminen", read_s)
                try:
                    ctype = resp_headers.get("Content-Type", "") or ""
                except Exception:
                    ctype = ""
                if status in RETRYABLE_HTTP_STATUS and attempt < max_attempts:
                    retry_after = None
                    try:
                        retry_after = resp_headers.get("Retry-After")
                    except Exception:
                        retry_after = None
                    delay = self._retry_delay(attempt, retry_after)
                    self._log_retry(request_url, attempt, max_attempts,
                                    "HTTP {}".format(status), delay, quiet)
                    time.sleep(delay)
                    continue
                last_error = None
                break
            except RETRYABLE_NETWORK_ERRORS as ex:
                timings.add("verkkopyyntö", time.perf_counter() - network_start)
                last_error = ex
                raw_bytes = None
                if attempt < max_attempts:
                    delay = self._retry_delay(attempt)
                    self._log_retry(request_url, attempt, max_attempts,
                                    type(ex).__name__, delay, quiet)
                    time.sleep(delay)
                    continue
                break
            except Exception as ex:
                timings.add("verkkopyyntö", time.perf_counter() - network_start)
                last_error = ex
                raw_bytes = None
                break

        if raw_bytes is None:
            return None, "", status, ctype

        decode_start = time.perf_counter()
        raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
        timings.add("vastauksen dekoodaus", time.perf_counter() - decode_start)

        if not raw_text:
            return None, raw_text, status, ctype

        if raw_text.lstrip()[:1] == "<":
            return None, raw_text, status, ctype

        try:
            parse_start = time.perf_counter()
            data = json.loads(raw_text)
            timings.add("JSON-jäsennys", time.perf_counter() - parse_start)
            if isinstance(data, dict):
                if "exceptions" in data or data.get("type") == "ExceptionReport" or "error" in data:
                    return None, raw_text, status, ctype
            return data, raw_text, status, ctype
        except Exception:
            if "parse_start" in locals():
                timings.add("JSON-jäsennys", time.perf_counter() - parse_start)
            return None, raw_text, status, ctype

    def _build_wfs_getfeature_url(self, base_wfs: str, layer_clean: str, max_features: int,
                                  start_index: int, output_format: str, bbox_str: str = None,
                                  cql_filter: str = None, geometry_only: bool = True) -> str:
        wfs_version = self._wfs_version_for_endpoint(base_wfs)
        is_wfs_11 = wfs_version == "1.1.0"
        type_names_q = urllib.parse.quote(layer_clean, safe=":")
        outfmt_q = urllib.parse.quote(output_format, safe=";/,+=")
        separator = "&" if urllib.parse.urlsplit(base_wfs).query else "?"
        type_name_parameter = "typeName" if is_wfs_11 else "typeNames"
        limit_parameter = "maxFeatures" if is_wfs_11 else "count"
        exception_format = (
            "application/vnd.ogc.se_xml" if is_wfs_11 else "application/json"
        )
        url = (
            f"{base_wfs}{separator}service=WFS&version={wfs_version}"
            f"&request=GetFeature&{type_name_parameter}={type_names_q}"
            f"&outputFormat={outfmt_q}"
            f"&exceptions={urllib.parse.quote(exception_format, safe='/')}"
            f"&srsName=EPSG:3067"
            f"&{limit_parameter}={max_features}&startIndex={start_index}"
        )
        sort_field = getattr(self, "_wfs_sort_field_cache", {}).get(layer_clean)
        if sort_field:
            url += "&sortBy=" + urllib.parse.quote(sort_field, safe="")
        if geometry_only:
            url += "&propertyName=" + self._get_wfs_geometry_field(layer_clean)
        if cql_filter:
            # Encode all chars including spaces so GeoServer parses WKT correctly
            url += "&CQL_FILTER=" + urllib.parse.quote(cql_filter, safe="(),'=")
        elif bbox_str:
            url += f"&bbox={bbox_str},EPSG:3067"
        return url

    def _wfs_getfeature_form(self, layer_clean, max_features, start_index, output_format,
                             bbox_str=None, cql_filter=None, geometry_only=True,
                             wfs_version="2.0.0"):
        is_wfs_11 = wfs_version == "1.1.0"
        form = {
            "service": "WFS",
            "version": wfs_version,
            "request": "GetFeature",
            "typeName" if is_wfs_11 else "typeNames": layer_clean,
            "outputFormat": output_format,
            "exceptions": (
                "application/vnd.ogc.se_xml" if is_wfs_11
                else "application/json"
            ),
            "srsName": "EPSG:3067",
            "maxFeatures" if is_wfs_11 else "count": str(max_features),
            "startIndex": str(start_index),
        }
        sort_field = getattr(self, "_wfs_sort_field_cache", {}).get(layer_clean)
        if sort_field:
            form["sortBy"] = sort_field
        if geometry_only:
            form["propertyName"] = self._get_wfs_geometry_field(layer_clean)
        if cql_filter:
            form["CQL_FILTER"] = cql_filter
        elif bbox_str:
            form["bbox"] = "{},EPSG:3067".format(bbox_str)
        return form

    def _wfs_error_snippet(self, raw_text, max_len=200):
        if not raw_text:
            return ""
        snippet = self._redact_secrets(raw_text.strip().replace("\n", " "))[:max_len]
        if raw_text.lstrip()[:1] == "<" and "blocked" in raw_text.lower():
            return "HTML/WAF-vastaus (mahdollinen CQL_FILTER-esto)"
        return snippet

    def _remember_wfs_geometry_field(self, layer_clean, json_data):
        if not isinstance(json_data, dict):
            return
        geom_name = json_data.get("geometry_name")
        if geom_name:
            self._wfs_geometry_field_cache[layer_clean] = geom_name

    def _get_wfs_geometry_field(self, layer_clean):
        default_field = "geometry"
        l_lower = (layer_clean or "").lower()
        if any(l_lower.startswith(prefix) for prefix in ["digiroad:", "liiteri_", "inspire_ps:"]):
            default_field = "geom"
        return self._wfs_geometry_field_cache.get(layer_clean, default_field)

    def _discover_wfs_schema(self, base_wfs, layer_clean, extra_headers=None):
        """Lue geometriakenttä ja vakaa sivutuskenttä palvelun omasta skeemasta."""
        if not hasattr(self, "_wfs_sort_candidate_cache"):
            self._wfs_sort_candidate_cache = {}
        if not hasattr(self, "_wfs_sort_field_cache"):
            self._wfs_sort_field_cache = {}
        if layer_clean in self._wfs_geometry_field_cache and layer_clean in self._wfs_sort_candidate_cache:
            return
        wfs_version = self._wfs_version_for_endpoint(base_wfs)
        query = urllib.parse.urlencode({
            "service": "WFS",
            "version": wfs_version,
            "request": "DescribeFeatureType",
            "typeName" if wfs_version == "1.1.0" else "typeNames": layer_clean,
        })
        request_url = "{}{}{}".format(base_wfs, "&" if "?" in base_wfs else "?", query)
        headers = {"User-Agent": "ArcGISPro-Suomenvaylat/1.0"}
        headers.update(extra_headers or {})
        try:
            req = urllib.request.Request(request_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                root = ET.fromstring(response.read())
        except Exception:
            return

        geometry_name = None
        scalar_fields = []
        geometry_markers = (
            "geometrypropertytype", "pointpropertytype", "curvepropertytype",
            "linestringpropertytype", "surfacepropertytype", "polygonpropertytype",
            "multigeometrypropertytype", "multipointpropertytype",
            "multicurvepropertytype", "multilinestringpropertytype",
            "multisurfacepropertytype", "multipolygonpropertytype",
        )
        for element in root.iter():
            if not element.tag.lower().endswith("element"):
                continue
            name = (element.attrib.get("name") or "").strip()
            field_type = (element.attrib.get("type") or "").lower()
            if not name or not field_type:
                continue
            if "gml:" in field_type and any(marker in field_type for marker in geometry_markers):
                geometry_name = geometry_name or name
            elif field_type.startswith(("xsd:", "xs:")):
                scalar_fields.append(name)

        if geometry_name:
            self._wfs_geometry_field_cache[layer_clean] = geometry_name
        preferred = None
        for candidate in ("objectid", "id", "fid", "ogc_fid"):
            preferred = next((field for field in scalar_fields if field.lower() == candidate), None)
            if preferred:
                break
        if not preferred and scalar_fields:
            preferred = scalar_fields[0]
        self._wfs_sort_candidate_cache[layer_clean] = preferred

    def _activate_wfs_stable_sort(self, base_wfs, layer_clean, extra_headers=None):
        self._discover_wfs_schema(base_wfs, layer_clean, extra_headers)
        sort_field = self._wfs_sort_candidate_cache.get(layer_clean)
        if sort_field:
            self._wfs_sort_field_cache[layer_clean] = sort_field
        return sort_field

    @staticmethod
    def _wfs_needs_explicit_sort(raw_text):
        return "cannot do natural order without a primary key" in (raw_text or "").lower()

    def _build_cql_intersects(self, geometry_field, boundary_wkt):
        return "INTERSECTS({}, {})".format(geometry_field, boundary_wkt)


    def _fetch_wfs_page(self, base_wfs, layer_clean, bbox_str, max_features, start_index,
                        output_formats, extra_headers=None, cql_filter=None,
                        geometry_only=True, prefer_post=False, timings=None):
        timings = timings if timings is not None else PhaseMetrics()
        cached_fmt = self._wfs_output_format_cache.get(base_wfs)
        if cached_fmt:
            formats_to_try = [cached_fmt] + [f for f in output_formats if f != cached_fmt]
        else:
            formats_to_try = output_formats

        raw_text = ""
        status = None
        ctype = ""

        def _try_request(fmt, use_post):
            compose_start = time.perf_counter()
            if use_post:
                wfs_version = self._wfs_version_for_endpoint(base_wfs)
                form = self._wfs_getfeature_form(
                    layer_clean, max_features, start_index, fmt,
                    bbox_str=bbox_str, cql_filter=cql_filter, geometry_only=geometry_only,
                    wfs_version=wfs_version,
                )
                timings.add("requestin muodostaminen", time.perf_counter() - compose_start)
                return self._fetch_json(
                    base_wfs, post_data=form, extra_headers=extra_headers, timings=timings
                )
            request_url = self._build_wfs_getfeature_url(
                base_wfs=base_wfs,
                layer_clean=layer_clean,
                max_features=max_features,
                start_index=start_index,
                output_format=fmt,
                bbox_str=bbox_str,
                cql_filter=cql_filter,
                geometry_only=geometry_only,
            )
            timings.add("requestin muodostaminen", time.perf_counter() - compose_start)
            return self._fetch_json(
                request_url, timeout=90, quiet=True, extra_headers=extra_headers,
                timings=timings,
            )

        for fmt in formats_to_try:
            attempts = [True] if prefer_post else ([False] if cql_filter else [False, True])
            for use_post in attempts:
                json_data, raw_text, status, ctype = _try_request(fmt, use_post)
                if json_data is None and self._wfs_needs_explicit_sort(raw_text):
                    if self._activate_wfs_stable_sort(base_wfs, layer_clean, extra_headers):
                        json_data, raw_text, status, ctype = _try_request(fmt, use_post)
                if json_data is not None:
                    if cached_fmt != fmt:
                        self._wfs_output_format_cache[base_wfs] = fmt
                    self._remember_wfs_geometry_field(layer_clean, json_data)
                    return json_data, raw_text, status, ctype
        return None, raw_text, status, ctype


    def _is_in_memory_layer(self, dataset):
        if not dataset:
            return False
        if os.path.sep in str(dataset) or "/" in str(dataset) or "\\" in str(dataset):
            return False
        try:
            desc = arcpy.Describe(dataset)
            return getattr(desc, "dataType", "") in ("FeatureLayer", "Layer")
        except Exception:
            return False


    @staticmethod
    def _merge_feature_pages(page_payloads):
        """Yhdistä useamman sivun GeoJSON yhdeksi FeatureCollectioniksi.

        Ensimmäinen sivu toimii pohjana, joten palvelun omat ylätason jäsenet
        (esim. ``crs`` ja ``geometry_name``) säilyvät ennallaan. Vain
        ``features`` kootaan yhteen.
        """
        merged = None
        features = []
        for payload in page_payloads:
            if not isinstance(payload, dict):
                continue
            if merged is None:
                merged = {
                    key: value for key, value in payload.items()
                    if key != "features"
                }
            page_features = payload.get("features") or []
            if page_features:
                features.extend(page_features)
        if merged is None:
            return None
        merged["type"] = merged.get("type") or "FeatureCollection"
        merged["features"] = features
        # Sivukohtaiset laskurit eivät päde yhdistetylle aineistolle.
        for stale_key in ("numberReturned", "numberMatched", "links"):
            merged.pop(stale_key, None)
        return merged

    def _pages_to_temp_fc(self, page_payloads, project_to_epsg=None):
        """Muunna monta sivua yhdellä JSONToFeatures-kutsulla.

        Yksi GP-kutsu sivua kohti oli mittausten mukaan merkittävä osa ison
        tason latausajasta. Sivujen kokoaminen eräksi vähentää sekä
        GP-käynnistyksiä että myöhemmän Mergen syötteiden määrää.
        """
        merged = self._merge_feature_pages(page_payloads)
        if merged is None or not merged.get("features"):
            return None, PhaseMetrics()
        serialize_start = time.perf_counter()
        raw_text = json.dumps(merged)
        serialize_s = time.perf_counter() - serialize_start
        temp_fc, timings = self._json_to_temp_fc(
            raw_text, project_to_epsg=project_to_epsg
        )
        timings.add("sivujen yhdistäminen", serialize_s)
        return temp_fc, timings

    def _json_to_temp_fc(self, raw_text: str, project_to_epsg=None):
        timings = PhaseMetrics()
        temp_json_path = os.path.join(self._scratch_folder(), f"temp_{uuid.uuid4().hex}.json")
        write_start = time.perf_counter()
        with open(temp_json_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        timings.set("väliaikaisen JSON-tiedoston kirjoittaminen", time.perf_counter() - write_start)
        temp_fc = os.path.join(self._scratch_gdb(), f"temp_fc_{uuid.uuid4().hex}")
        gp_start = time.perf_counter()
        arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc)
        timings.set("JSONToFeatures", time.perf_counter() - gp_start)
        if project_to_epsg:
            # OGC API Features palauttaa oletuksena CRS84-GeoJSONin ilman
            # erillistä crs-jäsenkenttää. Määritä lähde-CRS ennen Projectia
            # samalla turvallisella tavalla kuin Overpass-GeoJSONille.
            self._define_osm_source_projection(temp_fc)
            projected_fc = os.path.join(
                self._scratch_gdb(), f"projected_{uuid.uuid4().hex}"
            )
            project_start = time.perf_counter()
            arcpy.management.Project(
                temp_fc, projected_fc, arcpy.SpatialReference(project_to_epsg)
            )
            timings.set("projektointi", time.perf_counter() - project_start)
            self._safe_delete(temp_fc)
            temp_fc = projected_fc
        delete_start = time.perf_counter()
        try:
            os.remove(temp_json_path)
        except Exception:
            pass
        timings.set("väliaikaisen JSON-tiedoston poistaminen", time.perf_counter() - delete_start)
        return temp_fc, timings

    def _boundary_bbox_wgs84(self, boundary_fc):
        """Palauta OGC API -haun CRS84-bbox 3067-rajausaineistosta."""
        merged, _ = self._merged_boundary_geometry(boundary_fc)
        if merged is None:
            return None
        projected = merged.projectAs(arcpy.SpatialReference(4326))
        extent = projected.extent
        return "{:.12f},{:.12f},{:.12f},{:.12f}".format(
            extent.XMin, extent.YMin, extent.XMax, extent.YMax
        )

    def _fetch_ogcapi_feature_chunks(self, endpoint, collection_id,
                                     bbox_wgs84, max_features,
                                     extra_headers=None, max_requests=250,
                                     service_label="OGC API Features"):
        """Hae OGC API Features -kokoelma sivuina paikalliseen scratch-GDB:hen.

        OGC API:n oletus-GeoJSON on CRS84. Se muunnetaan heti JSONToFeaturesin
        jälkeen EPSG:3067:ään, jotta nykyinen tarkka paikallinen Clip ja muu
        WFS-käsittely voivat jatkua muuttumattomina.
        """
        fetch_start = time.perf_counter()
        stats = {
            "request_build_s": 0.0, "network_s": 0.0, "response_read_s": 0.0,
            "decode_s": 0.0, "json_parse_s": 0.0, "json_write_s": 0.0,
            "json_to_features_s": 0.0, "projection_s": 0.0,
            "json_temp_delete_s": 0.0, "pages": 0, "mode": "OGC_API",
            "fetch_total_s": 0.0, "truncated": False,
        }
        page_fcs = []
        pending_pages = []
        json_batch_pages = max(1, int(getattr(self, "_json_batch_pages", 1) or 1))
        total_features = 0
        current_url = None
        visited = set()
        base = str(endpoint or "").rstrip("/")
        collection_q = urllib.parse.quote(str(collection_id), safe="")

        def _accumulate_conversion_stats(conversion_timing, target_timing):
            for timing_name, timing_value in conversion_timing.seconds.items():
                if not isinstance(timing_value, (int, float)):
                    continue
                if target_timing is not None:
                    target_timing.add(timing_name, timing_value)
                if timing_name == "projektointi":
                    stats["projection_s"] += timing_value
                elif timing_name == "JSONToFeatures":
                    stats["json_to_features_s"] += timing_value
                elif timing_name == "väliaikaisen JSON-tiedoston kirjoittaminen":
                    stats["json_write_s"] += timing_value
                elif timing_name == "väliaikaisen JSON-tiedoston poistaminen":
                    stats["json_temp_delete_s"] += timing_value

        while len(visited) < max_requests:
            page_start = time.perf_counter()
            page_timing = PhaseMetrics()
            request_build_start = time.perf_counter()
            if current_url is None:
                query = urllib.parse.urlencode({
                    "limit": str(max_features),
                    "bbox": bbox_wgs84,
                })
                current_url = "{}/collections/{}/items?{}".format(
                    base, collection_q, query
                )
            page_timing.add(
                "requestin muodostaminen",
                time.perf_counter() - request_build_start,
            )
            visited.add(current_url)
            json_data, raw_text, status, content_type = self._fetch_json(
                current_url,
                timeout=120,
                quiet=True,
                extra_headers=extra_headers,
                timings=page_timing,
            )
            stats["pages"] += 1
            if json_data is None:
                reason = self._wfs_error_snippet(raw_text, 350) or content_type or "ei vastaussisältöä"
                raise Exception(
                    "{}-pyyntö epäonnistui (HTTP {}): {}".format(
                        service_label, status, reason
                    )
                )

            features = json_data.get("features", []) if isinstance(json_data, dict) else []
            if not features:
                for phase_name, stat_name in (
                    ("requestin muodostaminen", "request_build_s"),
                    ("verkkopyyntö", "network_s"),
                    ("vastauksen lukeminen", "response_read_s"),
                    ("vastauksen dekoodaus", "decode_s"),
                    ("JSON-jäsennys", "json_parse_s"),
                ):
                    stats[stat_name] += page_timing.get(phase_name, 0.0) or 0.0
                if self._verbose_diagnostics:
                    self._msg(
                        "    [EDISTYMINEN] OGC-sivu {}: +0 kohdetta (yhteensä {}), "
                        "sivu yhteensä {:.3f} s".format(
                            stats["pages"], total_features,
                            time.perf_counter() - page_start,
                        )
                    )
                break

            # OGC API:n sivutus seuraa palvelun next-linkkiä, joten sivuja ei
            # voi hakea rinnakkain. JSONToFeatures ajetaan silti erissä, jotta
            # GP-kutsujen määrä ei kasva sivumäärän mukana.
            pending_pages.append(json_data)
            if len(pending_pages) >= json_batch_pages:
                page_fc, conversion_timing = self._pages_to_temp_fc(
                    pending_pages, project_to_epsg=3067
                )
                pending_pages = []
                _accumulate_conversion_stats(conversion_timing, page_timing)
                if page_fc:
                    page_fcs.append(page_fc)
            got = len(features)
            total_features += got
            for phase_name, stat_name in (
                ("requestin muodostaminen", "request_build_s"),
                ("verkkopyyntö", "network_s"),
                ("vastauksen lukeminen", "response_read_s"),
                ("vastauksen dekoodaus", "decode_s"),
                ("JSON-jäsennys", "json_parse_s"),
            ):
                stats[stat_name] += page_timing.get(phase_name, 0.0) or 0.0

            if self._verbose_diagnostics:
                self._msg(
                    "    [EDISTYMINEN] OGC-sivu {}: +{} kohdetta (yhteensä {}), "
                    "request {:.3f} s, verkko {:.3f} s, luku {:.3f} s, "
                    "JSON-jäsennys {:.3f} s, JSONToFeatures {:.3f} s, "
                    "sivu yhteensä {:.3f} s".format(
                        stats["pages"], got, total_features,
                        page_timing.get("requestin muodostaminen", 0.0) or 0.0,
                        page_timing.get("verkkopyyntö", 0.0) or 0.0,
                        page_timing.get("vastauksen lukeminen", 0.0) or 0.0,
                        page_timing.get("JSON-jäsennys", 0.0) or 0.0,
                        page_timing.get("JSONToFeatures", 0.0) or 0.0,
                        time.perf_counter() - page_start,
                    )
                )

            next_url = None
            for link in (json_data.get("links", []) if isinstance(json_data, dict) else []) or []:
                if isinstance(link, dict) and str(link.get("rel", "")).lower() == "next":
                    next_url = link.get("href")
                    break
            if not next_url or next_url in visited:
                break
            current_url = urllib.parse.urljoin(current_url, str(next_url))

        if pending_pages:
            page_fc, conversion_timing = self._pages_to_temp_fc(
                pending_pages, project_to_epsg=3067
            )
            pending_pages = []
            _accumulate_conversion_stats(conversion_timing, None)
            if page_fc:
                page_fcs.append(page_fc)

        stats["truncated"] = len(visited) >= max_requests
        if stats["truncated"]:
            self._warn(
                "[VAROITUS] Maksimipyyntömäärä saavutettu OGC API -kokoelmassa "
                "'{}' (max_requests={}).".format(collection_id, max_requests)
            )
        stats["fetch_total_s"] = time.perf_counter() - fetch_start
        return page_fcs, total_features, stats

    def _fetch_oskari_feature_chunks(self, endpoint, layer_id, bbox_3067,
                                     service_label="Traficomin Oskari"):
        """Hae yksi rajaus Oskarin GetWFSFeatures-rajapinnasta GeoJSONina."""
        fetch_start = time.perf_counter()
        stats = {
            "request_build_s": 0.0, "network_s": 0.0, "response_read_s": 0.0,
            "decode_s": 0.0, "json_parse_s": 0.0, "json_write_s": 0.0,
            "json_to_features_s": 0.0, "projection_s": 0.0,
            "json_temp_delete_s": 0.0, "pages": 0, "mode": "OSKARI_WFS",
            "fetch_total_s": 0.0, "truncated": False,
        }
        query = urllib.parse.urlencode({
            "action_route": "GetWFSFeatures",
            "id": str(layer_id),
            "srs": "EPSG:3067",
            "bbox": bbox_3067,
        })
        request_url = "{}?{}".format(endpoint, query)
        request_timing = PhaseMetrics()
        data, raw_text, status, content_type = self._fetch_json(
            request_url, timeout=120, quiet=True, timings=request_timing
        )
        stats["pages"] = 1
        phase_to_stat = {
            "requestin muodostaminen": "request_build_s",
            "verkkopyyntö": "network_s",
            "vastauksen lukeminen": "response_read_s",
            "vastauksen dekoodaus": "decode_s",
            "JSON-jäsennys": "json_parse_s",
        }
        for phase_name, stat_name in phase_to_stat.items():
            stats[stat_name] += request_timing.get(phase_name, 0.0) or 0.0

        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            reason = self._wfs_error_snippet(raw_text, 350) or content_type or "virheellinen GeoJSON"
            raise Exception(
                "{} GetWFSFeatures -pyyntö epäonnistui (HTTP {}): {}".format(
                    service_label, status, reason
                )
            )

        features = data.get("features") or []
        if not features:
            stats["fetch_total_s"] = time.perf_counter() - fetch_start
            return [], 0, stats

        # Oskari palauttaa pyydetyn EPSG:3067:n eksplisiittisesti GeoJSONin
        # crs-jäsenessä. Lisää se tarvittaessa uudelleen ArcGISin muunnosta
        # varten ja hylkää eri koordinaatistossa palautettu aineisto.
        crs = data.get("crs")
        crs_properties = crs.get("properties") if isinstance(crs, dict) else None
        crs_name = crs_properties.get("name") if isinstance(crs_properties, dict) else None
        if crs_name and "3067" not in str(crs_name):
            raise Exception(
                "{} palautti koordinaatiston '{}'; odotettiin EPSG:3067.".format(
                    service_label, crs_name
                )
            )
        if not crs_name:
            data["crs"] = {
                "type": "name",
                "properties": {"name": "EPSG:3067"},
            }

        feature_class, conversion_timing = self._pages_to_temp_fc([data])
        if not feature_class:
            raise Exception(
                "{} palautti {} kohdetta, mutta ArcGIS Pro ei muodostanut niistä aineistoa.".format(
                    service_label, len(features)
                )
            )

        # JSONToFeaturesin tulos tarkistetaan tässä, jotta mahdollinen ArcGISin
        # GeoJSON-CRS-tulkinta ei siirrä TM35FIN-koordinaatteja väärään CRS:ään.
        try:
            sr = getattr(arcpy.Describe(feature_class), "spatialReference", None)
            sr_code = int(getattr(sr, "factoryCode", 0) or 0) if sr else 0
            if sr_code != 3067:
                define_start = time.perf_counter()
                arcpy.management.DefineProjection(
                    feature_class, arcpy.SpatialReference(3067)
                )
                stats["projection_s"] += time.perf_counter() - define_start
            converted_count = int(arcpy.management.GetCount(feature_class)[0])
        except Exception:
            self._safe_delete(feature_class)
            raise
        if converted_count != len(features):
            self._safe_delete(feature_class)
            raise Exception(
                "ArcGIS Pro muodosti Oskari-vastauksesta {} kohdetta, vaikka GeoJSONissa oli {}.".format(
                    converted_count, len(features)
                )
            )

        for timing_name, stat_name in (
            ("JSONToFeatures", "json_to_features_s"),
            ("väliaikaisen JSON-tiedoston kirjoittaminen", "json_write_s"),
            ("väliaikaisen JSON-tiedoston poistaminen", "json_temp_delete_s"),
        ):
            stats[stat_name] += conversion_timing.get(timing_name, 0.0) or 0.0
        stats["fetch_total_s"] = time.perf_counter() - fetch_start
        return [feature_class], len(features), stats

    def _fetch_bbox_feature_chunks(self, base_wfs: str, layer_clean: str, bbox_str: str,
                                   output_formats, max_features: int, max_requests: int = 200,
                                   extra_headers=None, boundary_wkt: str = None,
                                   source_name: str = None, allow_bbox_fallback=True):
        fetch_start = time.perf_counter()
        stats = {
            "request_build_s": 0.0, "network_s": 0.0, "response_read_s": 0.0,
            "decode_s": 0.0, "json_parse_s": 0.0, "json_write_s": 0.0,
            "json_to_features_s": 0.0, "json_temp_delete_s": 0.0,
            "http_s": 0.0, "gp_s": 0.0, "gp_json_s": 0.0,
            "pages": 0, "mode": "BBOX", "fetch_total_s": 0.0,
            "truncated": False,
        }

        def _accumulate_page_timing(page_timing):
            mapping = {
                "requestin muodostaminen": "request_build_s",
                "verkkopyyntö": "network_s",
                "vastauksen lukeminen": "response_read_s",
                "vastauksen dekoodaus": "decode_s",
                "JSON-jäsennys": "json_parse_s",
                "väliaikaisen JSON-tiedoston kirjoittaminen": "json_write_s",
                "JSONToFeatures": "json_to_features_s",
                "väliaikaisen JSON-tiedoston poistaminen": "json_temp_delete_s",
            }
            for phase_name, stat_name in mapping.items():
                stats[stat_name] += page_timing.get(phase_name, 0.0) or 0.0
            stats["http_s"] = stats["network_s"]
            stats["gp_json_s"] = stats["json_to_features_s"]
            stats["gp_s"] = stats["json_to_features_s"]
        use_cql = bool(boundary_wkt and self._wfs_supports_cql(source_name))
        if use_cql:
            self._discover_wfs_schema(base_wfs, layer_clean, extra_headers)
        geom_field = self._get_wfs_geometry_field(layer_clean)
        cql_filter = self._build_cql_intersects(geom_field, boundary_wkt) if use_cql else None
        if use_cql:
            stats["mode"] = "CQL"

        page_fcs = []
        pending_pages = []
        json_batch_pages = max(1, int(getattr(self, "_json_batch_pages", 1) or 1))
        request_count = 0
        total_features = 0
        start_index = 0
        repeated_guard = 0
        prev_hash = None
        cql_disabled = False

        # Rinnakkainen esihaku. Ensimmäinen sivu haetaan aina sarjallisesti,
        # jotta outputFormat-, geometriakenttä- ja sortBy-päättely sekä
        # CQL:n varareitit tapahtuvat täsmälleen kuten ennenkin. Vasta kun
        # sivutus on todistetusti käynnissä, seuraavat sivut haetaan
        # rinnakkain — WFS:n startIndex tekee niistä toisistaan riippumattomia.
        prefetch_buffer = []
        prefetch_post = False
        reported_total = None

        def _fetch_page_at(index, use_post):
            page_metrics = PhaseMetrics()
            result = self._fetch_wfs_page(
                base_wfs=base_wfs,
                layer_clean=layer_clean,
                bbox_str=bbox_str,
                max_features=max_features,
                start_index=index,
                output_formats=output_formats,
                extra_headers=extra_headers,
                cql_filter=cql_filter if (use_cql and not cql_disabled) else None,
                prefer_post=use_post,
                geometry_only=False,
                timings=page_metrics,
            )
            return (index,) + tuple(result) + (page_metrics,)

        def _fill_prefetch(from_index, budget, use_post, known_total=None):
            workers = max(1, int(getattr(self, "_page_workers", 1) or 1))
            wave = min(workers, max(0, budget))
            if known_total is not None:
                # Palvelu kertoi kokonaismäärän: älä hae hännästä tyhjiä sivuja.
                remaining = max(0, known_total - from_index)
                pages_left = int(math.ceil(remaining / float(max_features)))
                wave = min(wave, pages_left)
            if workers <= 1 or wave <= 0:
                return []
            indexes = [from_index + step * max_features for step in range(wave)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=wave) as pool:
                return list(pool.map(lambda i: _fetch_page_at(i, use_post), indexes))

        def _number_matched(payload):
            if not isinstance(payload, dict):
                return None
            for key in ("numberMatched", "totalFeatures"):
                value = payload.get(key)
                if isinstance(value, int) and value >= 0:
                    return value
                try:
                    if isinstance(value, str) and value.isdigit():
                        return int(value)
                except Exception:
                    continue
            return None

        while request_count < max_requests:
            page_start = time.perf_counter()
            active_cql = cql_filter if (use_cql and not cql_disabled) else None
            cql_post_tried = False
            json_data = None
            raw_text = ""
            status = None
            ctype = ""
            page_timing = None

            if prefetch_buffer:
                page_index, json_data, raw_text, status, ctype, page_timing = \
                    prefetch_buffer.pop(0)
                start_index = page_index
                if json_data is None:
                    # Esihaettu sivu epäonnistui: tyhjennä jono ja hae sama sivu
                    # sarjallisesti, jotta virhe- ja varareittilogiikka toimii
                    # täsmälleen kuten ilman esihakua.
                    prefetch_buffer = []
                    page_timing = None

            if page_timing is None:
                page_timing = PhaseMetrics()
                json_data, raw_text, status, ctype = self._fetch_wfs_page(
                    base_wfs=base_wfs,
                    layer_clean=layer_clean,
                    bbox_str=bbox_str,
                    max_features=max_features,
                    start_index=start_index,
                    output_formats=output_formats,
                    extra_headers=extra_headers,
                    cql_filter=active_cql,
                    geometry_only=False,
                    timings=page_timing,
                )

            request_count += 1

            if json_data is None and active_cql and not cql_disabled and not cql_post_tried:
                if status == 414:
                    self._msg("  [INFO] CQL GET oli liian pitkä; jatketaan POST-pyynnöllä.")
                elif getattr(self, "_verbose_diagnostics", False):
                    snippet = self._wfs_error_snippet(raw_text)
                    self._warn(
                        "[VAROITUS] CQL_FILTER GET hylätty (HTTP {}, {}). Yritetään POST-pyyntöä.".format(
                            status, snippet or ctype
                        )
                    )
                cql_post_tried = True
                json_data, raw_text, status, ctype = self._fetch_wfs_page(
                    base_wfs=base_wfs,
                    layer_clean=layer_clean,
                    bbox_str=bbox_str,
                    max_features=max_features,
                    start_index=start_index,
                    output_formats=output_formats,
                    extra_headers=extra_headers,
                    cql_filter=active_cql,
                    prefer_post=True,
                    geometry_only=False,
                    timings=page_timing,
                )
                if json_data is not None:
                    stats["mode"] = "CQL_POST"
                    # Esihaku käyttää jatkossa samaa POST-muotoa.
                    prefetch_post = True

            if json_data is None:
                if active_cql and not cql_disabled:
                    snippet = self._wfs_error_snippet(raw_text)
                    if not allow_bbox_fallback:
                        self._warn(
                            "[VAROITUS] CQL_FILTER POST hylätty (HTTP {}, {}). "
                            "Yritetään tarvittaessa pienempiä CQL-geometrioita ennen BBOX-varamenetelmää.".format(
                                status, snippet or ctype
                            )
                        )
                        for fc in page_fcs:
                            self._safe_delete(fc)
                        _accumulate_page_timing(page_timing)
                        stats["fetch_total_s"] = time.perf_counter() - fetch_start
                        rejected = CQLRequestRejected("CQL GET ja POST hylättiin")
                        rejected.stats = stats
                        raise rejected
                    self._warn(
                        "[VAROITUS] CQL_FILTER POST hylätty (HTTP {}, {}). Käytetään BBOX-hakua.".format(
                            status, snippet or ctype
                        )
                    )
                    cql_disabled = True
                    stats["mode"] = "BBOX"
                    for fc in page_fcs:
                        self._safe_delete(fc)
                    page_fcs = []
                    pending_pages = []
                    prefetch_buffer = []
                    prefetch_post = False
                    reported_total = None
                    total_features = 0
                    start_index = 0
                    request_count = 0
                    repeated_guard = 0
                    prev_hash = None
                    continue

                dump_path = os.path.join(self._scratch_folder(), f"wfs_error_{uuid.uuid4().hex}.txt")
                try:
                    with open(dump_path, "w", encoding="utf-8") as f:
                        f.write(self._redact_secrets(raw_text or ""))
                except Exception:
                    pass
                reason = self._wfs_error_snippet(raw_text, 350) or ctype or "ei vastaussisältöä"
                raise Exception(
                    "WFS-pyyntö epäonnistui (HTTP {}): {}. Tarkempi vastaus: {}".format(
                        status, reason, dump_path
                    )
                )

            features = json_data.get("features", []) if isinstance(json_data, dict) else []
            if not features:
                _accumulate_page_timing(page_timing)
                stats["pages"] += 1
                if getattr(self, "_verbose_diagnostics", False):
                    self._msg(
                        "    [EDISTYMINEN] Sivu {}: +0 kohdetta (yhteensä {}), "
                        "request {:.3f} s, verkko {:.3f} s, luku {:.3f} s, "
                        "JSON-jäsennys {:.3f} s, JSONToFeatures ei tarpeen, "
                        "sivu yhteensä {:.3f} s".format(
                            request_count, total_features,
                            page_timing.get("requestin muodostaminen", 0.0) or 0.0,
                            page_timing.get("verkkopyyntö", 0.0) or 0.0,
                            page_timing.get("vastauksen lukeminen", 0.0) or 0.0,
                            page_timing.get("JSON-jäsennys", 0.0) or 0.0,
                            time.perf_counter() - page_start,
                        )
                    )
                break

            text_hash = hashlib.md5(raw_text[:8000].encode('utf-8', errors='replace')).hexdigest()
            if prev_hash == text_hash:
                repeated_guard += 1
            else:
                repeated_guard = 0
            prev_hash = text_hash
            if repeated_guard >= 2:
                _accumulate_page_timing(page_timing)
                stats["pages"] += 1
                self._warn(
                    "[VAROITUS] WFS sivutus toistaa samaa sisältöä tasolla '{}'. Keskeytetään sivutus turvallisesti.".format(
                        layer_clean
                    )
                )
                if getattr(self, "_verbose_diagnostics", False):
                    self._msg(
                        "    [EDISTYMINEN] Sivu {}: vastaus toisti aiemman sivun; "
                        "verkko {:.3f} s, luku {:.3f} s, sivu yhteensä {:.3f} s.".format(
                            request_count,
                            page_timing.get("verkkopyyntö", 0.0) or 0.0,
                            page_timing.get("vastauksen lukeminen", 0.0) or 0.0,
                            time.perf_counter() - page_start,
                        )
                    )
                break

            # Sivut kootaan eräksi, jotta JSONToFeatures ajetaan kerran usean
            # sivun yli yhden GP-kutsun sijaan sivua kohti.
            pending_pages.append(json_data)
            if len(pending_pages) >= json_batch_pages:
                page_fc, conversion_timing = self._pages_to_temp_fc(pending_pages)
                pending_pages = []
                for timing_name, timing_value in conversion_timing.seconds.items():
                    if isinstance(timing_value, (int, float)):
                        page_timing.add(timing_name, timing_value)
                if page_fc:
                    page_fcs.append(page_fc)
            stats["pages"] += 1

            got = len(features)
            total_features += got
            start_index += got

            # Per-page progress logging. Sivun kokonaisaikaa ei kutsuta HTTP-ajaksi.
            request_build_s = page_timing.get("requestin muodostaminen", 0.0) or 0.0
            network_s = page_timing.get("verkkopyyntö", 0.0) or 0.0
            response_read_s = page_timing.get("vastauksen lukeminen", 0.0) or 0.0
            decode_s = page_timing.get("vastauksen dekoodaus", 0.0) or 0.0
            json_parse_s = page_timing.get("JSON-jäsennys", 0.0) or 0.0
            json_write_s = page_timing.get("väliaikaisen JSON-tiedoston kirjoittaminen", 0.0) or 0.0
            json_to_features_s = page_timing.get("JSONToFeatures", 0.0) or 0.0
            json_temp_delete_s = page_timing.get("väliaikaisen JSON-tiedoston poistaminen", 0.0) or 0.0
            _accumulate_page_timing(page_timing)
            page_elapsed = time.perf_counter() - page_start
            if getattr(self, "_verbose_diagnostics", False):
                self._msg(
                    "    [EDISTYMINEN] Sivu {}: +{} kohdetta (yhteensä {}), "
                    "request {:.3f} s, verkko {:.3f} s, luku {:.3f} s, "
                    "JSON-jäsennys {:.3f} s, JSON-kirjoitus {:.3f} s, "
                    "JSONToFeatures {:.3f} s, sivu yhteensä {:.3f} s".format(
                        request_count, got, total_features, request_build_s, network_s,
                        response_read_s, json_parse_s, json_write_s,
                        json_to_features_s, page_elapsed,
                    )
                )

            if got < max_features:
                break

            # Sivu oli täysi, joten sivutus jatkuu. Täytä esihakujono, jotta
            # verkko ja geoprosessointi eivät odota vuorotellen toisiaan.
            if reported_total is None:
                reported_total = _number_matched(json_data)
            if not prefetch_buffer:
                remaining_budget = max_requests - request_count
                prefetch_buffer = _fill_prefetch(
                    start_index, remaining_budget, prefetch_post, reported_total
                )

        # Viimeinen vajaa erä on muunnettava myös.
        if pending_pages:
            page_fc, conversion_timing = self._pages_to_temp_fc(pending_pages)
            pending_pages = []
            tail_timing = PhaseMetrics()
            for timing_name, timing_value in conversion_timing.seconds.items():
                if isinstance(timing_value, (int, float)):
                    tail_timing.add(timing_name, timing_value)
            _accumulate_page_timing(tail_timing)
            if page_fc:
                page_fcs.append(page_fc)

        truncated = request_count >= max_requests
        stats["truncated"] = truncated
        if truncated:
            self._warn(
                "[VAROITUS] Maksimipyyntömäärä saavutettu tasolla '{}' (max_requests={}).".format(
                    layer_clean, max_requests
                )
            )

        cql_effective = use_cql and not cql_disabled
        stats["fetch_total_s"] = time.perf_counter() - fetch_start
        return page_fcs, total_features, request_count, stats, cql_effective

    # ---------------------------
    # KUNNAT (lista + polygonit) paikallisesta geopackagesta
    # ---------------------------
    def _fetch_all_kunnat(self):
        if self._kunnat_cache is not None:
            return self._kunnat_cache
        self._kunnat_cache = self._get_extent_choices("Kunta/Kaupunki")
        return self._kunnat_cache

    def _fetch_all_kunnat_fc(self):
        gpkg = self._find_admin_gpkg()
        if not gpkg:
            return None
        source_fc = self._find_fc_in_gpkg(gpkg, self.admin_layer_names["Kunta/Kaupunki"])
        if not source_fc:
            return None
        out_fc = os.path.join(self._scratch_gdb(), f"kunnat_all_fc_{uuid.uuid4().hex}")
        arcpy.management.CopyFeatures(source_fc, out_fc)
        return out_fc

    def _select_kunnat_center_in(self, kunnat_fc: str, boundary_fc: str):
        scratch_gdb = self._scratch_gdb()
        lyr = f"kunnat_lyr_{uuid.uuid4().hex[:8]}"
        arcpy.management.MakeFeatureLayer(kunnat_fc, lyr)
        arcpy.management.SelectLayerByLocation(lyr, "HAVE_THEIR_CENTER_IN", boundary_fc)
        out_fc = os.path.join(scratch_gdb, f"kunnat_sel_{uuid.uuid4().hex}")
        arcpy.management.CopyFeatures(lyr, out_fc)
        arcpy.management.Delete(lyr)
        return out_fc

    def _copy_single_feature(self, fc: str, oid: int):
        scratch_gdb = self._scratch_gdb()
        oid_field = arcpy.Describe(fc).OIDFieldName
        lyr = f"one_lyr_{uuid.uuid4().hex[:8]}"
        arcpy.management.MakeFeatureLayer(fc, lyr, f"{oid_field} = {int(oid)}")
        out_fc = os.path.join(scratch_gdb, f"kunta_{uuid.uuid4().hex}")
        arcpy.management.CopyFeatures(lyr, out_fc)
        arcpy.management.Delete(lyr)
        return out_fc

    @staticmethod
    def _direct_xml_text(parent, local_name):
        for child in list(parent):
            if child.tag.split("}")[-1] == local_name and child.text:
                return child.text.strip()
        return None

    @staticmethod
    def _mml_auth_headers(api_key, user_agent="ArcGISPro-MMLBasemapTool/1.1"):
        key = (api_key or "").strip()
        headers = {"User-Agent": user_agent}
        if key:
            token = base64.b64encode("{}:".format(key).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic {}".format(token)
        return headers

    def _fetch_mml_capabilities(self, api_key=""):
        key = (api_key or "").strip()
        if not key:
            raise Exception("MML WMTS vaatii API-avaimen.")
        req = urllib.request.Request(
            self.mml_wmts_capabilities,
            headers=self._mml_auth_headers(key),
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                raise Exception("MML WMTS hylkäsi API-avaimen (HTTP {}).".format(ex.code))
            raise

    def _fetch_mml_layer_list(self, api_key=""):
        out = []
        mapping = {}
        xml_bytes = self._fetch_mml_capabilities(api_key)
        root = ET.fromstring(xml_bytes)
        for elem in root.iter():
            if not elem.tag.endswith("Layer"):
                continue
            layer_id = None
            title = None
            for child in elem.iter():
                if child.tag.endswith("Identifier") and child.text and not layer_id:
                    layer_id = child.text.strip()
                elif child.tag.endswith("Title") and child.text and not title:
                    title = child.text.strip()
            if layer_id:
                display = title if title else layer_id
                if display in mapping and mapping[display] != layer_id:
                    display = f"{display} ({layer_id})"
                mapping[display] = layer_id
                out.append(display)
        out_sorted = sorted(list(set(out)))
        if not out_sorted:
            raise Exception("Yhtään MML-karttatasoa ei löytynyt WMTS capabilities -vastauksesta.")
        self._mml_layer_mapping = mapping
        return out_sorted

    def _get_mml_layers_cached(self, api_key=""):
        cache_key = "auth:{}".format(self._secret_cache_key(api_key)) if (api_key or "").strip() else "noauth"
        if cache_key not in self._all_mml_layers_cache:
            self._all_mml_layers_cache[cache_key] = self._fetch_mml_layer_list(api_key=api_key)
            self._mml_layer_mapping_cache[cache_key] = dict(self._mml_layer_mapping)
        else:
            self._mml_layer_mapping = dict(self._mml_layer_mapping_cache.get(cache_key, {}))
        return self._all_mml_layers_cache[cache_key]

    def _fetch_mml_karttakuva_layer_list(self, user="", password=""):
        out = []
        self._mml_karttakuva_layer_mapping.clear()
        headers = {"User-Agent": "ArcGISPro-MMLKarttakuva/1.0"}
        u = (user or "").strip()
        p = (password or "").strip()
        if u:
            token = base64.b64encode("{}:{}".format(u, p).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic {}".format(token)
        req = urllib.request.Request(self.mml_karttakuva_wmts, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                xml_bytes = response.read()
        except urllib.error.HTTPError as ex:
            if ex.code == 401:
                raise Exception("MML Karttakuva vaatii tunnukset (401 Unauthorized).")
            raise
        root = ET.fromstring(xml_bytes)
        for elem in root.iter():
            if not elem.tag.endswith("Layer"):
                continue
            layer_id = None
            title = None
            for child in elem.iter():
                if child.tag.endswith("Identifier") and child.text and not layer_id:
                    layer_id = child.text.strip()
                elif child.tag.endswith("Title") and child.text and not title:
                    title = child.text.strip()
            if layer_id:
                display = title if title else layer_id
                if display in self._mml_karttakuva_layer_mapping and self._mml_karttakuva_layer_mapping[display] != layer_id:
                    display = "{} ({})".format(display, layer_id)
                self._mml_karttakuva_layer_mapping[display] = layer_id
                out.append(display)
        out_sorted = sorted(list(set(out)))
        if not out_sorted:
            raise Exception("Yhtään MML Karttakuva -tasoa ei löytynyt WMTS-vastauksesta.")
        return out_sorted

    def _get_mml_karttakuva_layers_cached(self, user="", password=""):
        cache_key = "{}:{}".format(self._norm(user), self._norm(password))
        if cache_key not in self._all_mml_karttakuva_layers_cache:
            self._all_mml_karttakuva_layers_cache[cache_key] = self._fetch_mml_karttakuva_layer_list(user=user, password=password)
        return self._all_mml_karttakuva_layers_cache[cache_key]

    def _add_karttakuva_wmts_layer(self, layer_id, user, password):
        """Adds a live WMTS/WMS layer to the current map via addDataFromPath."""
        parsed = urllib.parse.urlparse(self.mml_karttakuva_wmts)
        u_enc = urllib.parse.quote(user, safe="")
        p_enc = urllib.parse.quote(password, safe="")
        netloc_with_creds = "{}:{}@{}".format(u_enc, p_enc, parsed.netloc)
        wmts_url = urllib.parse.urlunparse(parsed._replace(netloc=netloc_with_creds))
        wms_url = "https://{}:{}@karttakuva.maanmittauslaitos.fi/maasto/wms".format(u_enc, p_enc)
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        m = aprx.activeMap
        if not m:
            raise Exception("Aktiivista karttaa ei löydy.")
        errors = []
        for url, s_type in [(wmts_url, "AUTOMATIC"), (wmts_url, "WMS"), (wms_url, "WMS"), (wmts_url, "ARCGIS_SERVER_WEB")]:
            try:
                m.addDataFromPath(url, s_type)
                return
            except Exception as e:
                errors.append(str(e))
        raise Exception("; ".join(errors[:2]))

    def _kapsi_service_name_from_caps(self, caps_url):
        base = caps_url.split("?", 1)[0].rstrip("/")
        return base.split("/")[-1] if "/" in base else "kapsi"

    def _fetch_kapsi_layer_list(self):
        out = []
        seen_layer_refs = set()
        self._kapsi_layer_mapping.clear()
        self._kapsi_layer_scale_ranges.clear()
        caps_urls = self.wfs_registry.get_endpoints("Kapsi")
        if not caps_urls:
            raise Exception("Kapsi GetCapabilities-osoite puuttuu.")

        for caps_url in caps_urls:
            try:
                req = urllib.request.Request(caps_url, headers={"User-Agent": "ArcGISPro-KapsiBasemapTool/1.0"})
                with urllib.request.urlopen(req, timeout=60) as response:
                    xml_bytes = response.read()
                root = ET.fromstring(xml_bytes)
            except Exception as ex:
                self._warn("[VAROITUS] Kapsi GetCapabilities epäonnistui osoitteelle '{}': {}".format(caps_url, ex))
                continue

            service_name = self._kapsi_service_name_from_caps(caps_url)
            service_base = caps_url.split("?", 1)[0]

            for elem in root.iter():
                if not elem.tag.endswith("Layer"):
                    continue
                layer_name = None
                layer_title = None
                min_scale = None
                max_scale = None
                for child in elem:
                    if child.tag.endswith("Name") and child.text and not layer_name:
                        layer_name = child.text.strip()
                    elif child.tag.endswith("Title") and child.text and not layer_title:
                        layer_title = child.text.strip()
                    elif child.tag.endswith("MinScaleDenominator") and child.text:
                        try:
                            min_scale = float(child.text)
                        except ValueError:
                            self._warn(
                                "[VAROITUS] Kapsi-tason minimimittakaava ei ole numero: {}".format(
                                    child.text
                                )
                            )
                    elif child.tag.endswith("MaxScaleDenominator") and child.text:
                        try:
                            max_scale = float(child.text)
                        except ValueError:
                            self._warn(
                                "[VAROITUS] Kapsi-tason maksimimittakaava ei ole numero: {}".format(
                                    child.text
                                )
                            )

                if layer_name:
                    display_core = layer_title if layer_title else layer_name
                    display = "{} ({})".format(display_core, service_name)
                    layer_ref = "{}|{}".format(service_base, layer_name)
                    if layer_ref in seen_layer_refs:
                        if (
                            layer_ref not in self._kapsi_layer_scale_ranges
                            and (min_scale is not None or max_scale is not None)
                        ):
                            self._kapsi_layer_scale_ranges[layer_ref] = (
                                min_scale,
                                max_scale,
                            )
                        continue
                    seen_layer_refs.add(layer_ref)
                    unique_display = display
                    counter = 2
                    while unique_display in self._kapsi_layer_mapping and self._kapsi_layer_mapping[unique_display] != layer_ref:
                        unique_display = "{} ({})".format(display, counter)
                        counter += 1

                    self._kapsi_layer_mapping[unique_display] = layer_ref
                    if min_scale is not None or max_scale is not None:
                        self._kapsi_layer_scale_ranges[layer_ref] = (min_scale, max_scale)
                    out.append(unique_display)

        if not out:
            self._kapsi_layer_mapping["Ortokuva"] = "https://tiles.kartat.kapsi.fi/ortokuva|ortokuva"
            out = ["Ortokuva"]

        return sorted(list(set(out)), key=lambda x: self._norm(x))

    def _get_kapsi_layers_cached(self):
        if self._all_kapsi_layers_cache is None:
            try:
                self._all_kapsi_layers_cache = self._fetch_kapsi_layer_list()
            except Exception:
                if not self._kapsi_layer_mapping:
                    self._kapsi_layer_mapping["Ortokuva"] = "https://tiles.kartat.kapsi.fi/ortokuva|ortokuva"
                self._all_kapsi_layers_cache = sorted(list(self._kapsi_layer_mapping.keys()))
        return self._all_kapsi_layers_cache

    def _get_basemap_provider(self, download_type):
        if download_type == "Kapsi taustakartat":
            return "Kapsi"
        return "MML"

    def _get_basemap_layers_cached(self, provider):
        if provider == "Kapsi":
            return self._get_kapsi_layers_cached()
        return self._get_mml_layers_cached(api_key=self._runtime_mml_api_key)

    def _get_basemap_layer_id(self, provider, display_name):
        if provider == "Kapsi":
            return self._kapsi_layer_mapping.get(display_name, display_name)
        return self._mml_layer_mapping.get(display_name, display_name)

    def _get_basemap_mode_options(self, provider):
        if provider == "Kapsi":
            return ["Raster (JPEG WMS EPSG:3067)"]
        return ["Live vector tile"]

    def _write_world_file(self, raster_path, ext, width, height):
        root, extension = os.path.splitext(raster_path)
        world_extension_map = {
            ".jpg": ".jgw",
            ".jpeg": ".jgw",
            ".png": ".pgw",
            ".tif": ".tfw"
        }
        world_path = root + world_extension_map.get(extension.lower(), ".wld")
        pixel_x = (ext.XMax - ext.XMin) / float(width)
        pixel_y = -((ext.YMax - ext.YMin) / float(height))
        top_left_x = ext.XMin + (pixel_x / 2.0)
        top_left_y = ext.YMax + (pixel_y / 2.0)
        with open(world_path, "w", encoding="ascii") as handle:
            handle.write("{}\n0.0\n0.0\n{}\n{}\n{}\n".format(pixel_x, pixel_y, top_left_x, top_left_y))

        prj_path = root + ".prj"
        with open(prj_path, "w", encoding="utf-8") as handle:
            handle.write(arcpy.SpatialReference(3067).exportToString())

    def _download_oskari_wms_geotiff(
        self, layer_id, layer_name, layer_title, style, boundary_fc, workspace,
        endpoint=None,
    ):
        """Hae Oskarin WMS-karttataso aluerajauksen georeferoituna kuvana."""
        endpoint = endpoint or self.wfs_registry.get_endpoint("Traficom Oskari")
        if not endpoint:
            raise Exception("Oskarin WMS-palveluosoite puuttuu.")
        layer_name = (layer_name or "").strip()
        if not layer_name:
            raise Exception("Oskarin WMS-tason tekninen nimi puuttuu.")

        ext = self._boundary_extent_3067(boundary_fc)
        extent_w = float(ext.XMax - ext.XMin)
        extent_h = float(ext.YMax - ext.YMin)
        if extent_w <= 0 or extent_h <= 0:
            raise Exception("Rajauksen laajuus ei riitä Oskari WMS -kuvan muodostamiseen.")
        max_dimension = 2048
        scale = max_dimension / max(extent_w, extent_h)
        width = max(1, min(max_dimension, int(round(extent_w * scale))))
        height = max(1, min(max_dimension, int(round(extent_h * scale))))

        if isinstance(style, (list, tuple)):
            style = ",".join(str(value) for value in style if value is not None)
        elif isinstance(style, dict):
            style = style.get("name") or style.get("id") or ""
        params = {
            "action_route": "GetLayerTile",
            "id": str(layer_id),
            "SERVICE": "WMS",
            "REQUEST": "GetMap",
            "VERSION": "1.1.1",
            "LAYERS": layer_name,
            "STYLES": str(style or ""),
            "SRS": "EPSG:3067",
            "BBOX": "{},{},{},{}".format(
                ext.XMin, ext.YMin, ext.XMax, ext.YMax
            ),
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
        }
        parsed = urllib.parse.urlsplit(endpoint)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.extend(params.items())
        request_url = urllib.parse.urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path,
            urllib.parse.urlencode(query), parsed.fragment,
        ))
        request = urllib.request.Request(request_url, headers={
            "User-Agent": "ArcGISPro-Suomenvaylat-Oskari/1.0",
            "Accept": "image/png",
        })
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            content_type = (response.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("image/") or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            detail = raw[:250].decode("utf-8", errors="replace")
            raise Exception(
                "Oskari WMS ei palauttanut PNG-kuvaa ({}): {}".format(
                    content_type or "tuntematon sisältötyyppi", detail
                )
            )

        os.makedirs(workspace, exist_ok=True)
        stem = "oskari_wms_{}_{}".format(layer_id, uuid.uuid4().hex[:8])
        png_path = os.path.join(workspace, stem + ".png")
        tif_path = os.path.join(workspace, stem + ".tif")
        with open(png_path, "wb") as handle:
            handle.write(raw)
        try:
            self._write_world_file(png_path, ext, width, height)
            arcpy.management.CopyRaster(png_path, tif_path)
            try:
                source_code = int(arcpy.Describe(tif_path).spatialReference.factoryCode or 0)
            except Exception:
                source_code = 0
            if source_code != 3067:
                arcpy.management.DefineProjection(tif_path, arcpy.SpatialReference(3067))
        except Exception:
            self._remove_local_output(tif_path)
            raise
        finally:
            for sidecar in (os.path.splitext(png_path)[0] + ".pgw",
                            os.path.splitext(png_path)[0] + ".prj"):
                try:
                    if os.path.exists(sidecar):
                        os.remove(sidecar)
                except Exception:
                    pass
            try:
                if os.path.exists(png_path):
                    os.remove(png_path)
            except Exception:
                pass
        self._msg(
            "[INFO] Oskari WMS -karttakuva ladattu: {} ({} × {}, EPSG:3067).".format(
                layer_title or layer_name, width, height
            )
        )
        return tif_path

    def _download_oskari_wmts_tile(self, request_url, attempts=3):
        """Lataa yksi julkinen Traficom WMTS PNG -laatta."""
        last_error = None
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(request_url, headers={
                "User-Agent": "ArcGISPro-Suomenvaylat-Oskari/1.0",
                "Accept": "image/png",
                "Accept-Encoding": "identity",
            })
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read()
                    content_type = (response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("image/") or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    detail = raw[:250].decode("utf-8", errors="replace")
                    raise Exception(
                        "Traficom WMTS palautti PNG:n sijaan {}: {}".format(
                            content_type or "tuntematon sisältötyyppi", detail
                        )
                    )
                return raw
            except (urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead) as ex:
                last_error = ex
                if attempt < attempts:
                    time.sleep(0.5 * attempt)
                    continue
            except Exception as ex:
                last_error = ex
                break
        raise Exception(
            "Traficom WMTS -laatan lataus epäonnistui: {}".format(last_error)
        )

    def _traficom_wmts_layer_metadata(self, layer_name):
        """Lue valitun Oskari-tason WMTS-matriisit ja sallitut tiilirajat."""
        endpoint = TRAFICOM_WMTS_ENDPOINT
        request = urllib.request.Request(endpoint, headers={
            "User-Agent": "ArcGISPro-Suomenvaylat-Oskari/1.0",
            "Accept": "application/xml,text/xml",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            root = ET.fromstring(response.read())

        selected_layer = None
        for elem in root.iter():
            if not elem.tag.endswith("Layer"):
                continue
            identifier = next((
                (child.text or "").strip() for child in list(elem)
                if child.tag.endswith("Identifier") and child.text
            ), "")
            if identifier == layer_name:
                selected_layer = elem
                break
        if selected_layer is None:
            raise Exception("WMTS GetCapabilities ei sisällä tasoa '{}'.".format(layer_name))

        format_name = next((
            (child.text or "").strip() for child in list(selected_layer)
            if child.tag.endswith("Format") and child.text
        ), "image/png")
        if format_name.casefold() != "image/png":
            raise Exception("Oskari-WMTS-tasolle '{}' ei ole PNG-kuvaformaattia.".format(layer_name))
        style_id = ""
        for style in selected_layer.iter():
            if not style.tag.endswith("Style"):
                continue
            identifier = next((
                (child.text or "").strip() for child in list(style)
                if child.tag.endswith("Identifier") and child.text
            ), "")
            if style.get("isDefault", "false").casefold() == "true":
                style_id = identifier
                break

        matrix_set_links = {}
        for link in selected_layer.iter():
            if not link.tag.endswith("TileMatrixSetLink"):
                continue
            set_name = next((
                (child.text or "").strip() for child in list(link)
                if child.tag.endswith("TileMatrixSet") and child.text
            ), "")
            limits = {}
            for limit in link.iter():
                if not limit.tag.endswith("TileMatrixLimits"):
                    continue
                values = {}
                for child in list(limit):
                    if child.text:
                        values[child.tag.split("}")[-1]] = child.text.strip()
                matrix_id = values.get("TileMatrix")
                if matrix_id:
                    limits[matrix_id] = {
                        "min_row": int(values.get("MinTileRow", 0)),
                        "max_row": int(values.get("MaxTileRow", 0)),
                        "min_col": int(values.get("MinTileCol", 0)),
                        "max_col": int(values.get("MaxTileCol", 0)),
                    }
            if set_name:
                matrix_set_links[set_name] = limits

        matrix_sets = {}
        for elem in root.iter():
            if not elem.tag.endswith("TileMatrixSet"):
                continue
            children = list(elem)
            set_id = next((
                (child.text or "").strip() for child in children
                if child.tag.endswith("Identifier") and child.text
            ), "")
            if not set_id or set_id not in matrix_set_links:
                continue
            supported_crs = next((
                (child.text or "").strip() for child in children
                if child.tag.endswith("SupportedCRS") and child.text
            ), "")
            if "3067" not in supported_crs:
                continue
            matrices = []
            for matrix in children:
                if not matrix.tag.endswith("TileMatrix"):
                    continue
                values = {}
                for child in list(matrix):
                    if child.text:
                        values[child.tag.split("}")[-1]] = child.text.strip()
                identifier = values.get("Identifier")
                corner = values.get("TopLeftCorner", "").split()
                if not identifier or len(corner) < 2:
                    continue
                matrices.append({
                    "id": identifier,
                    "scale": float(values["ScaleDenominator"]),
                    "origin_x": float(corner[0]),
                    "origin_y": float(corner[1]),
                    "tile_width": int(values.get("TileWidth", 256)),
                    "tile_height": int(values.get("TileHeight", 256)),
                    "matrix_width": int(values["MatrixWidth"]),
                    "matrix_height": int(values["MatrixHeight"]),
                    "limits": matrix_set_links[set_id].get(identifier),
                })
            if matrices:
                matrix_sets[set_id] = matrices

        if not matrix_sets:
            raise Exception("Traficom WMTS -tasolta puuttuu EPSG:3067-tiiliruudukko.")
        # Palvelussa on tavallisesti yksi 3067-matriisijoukko. Jos palvelu
        # tarjoaa useita, valitse suurimman käyttökelpoisen tarkkuuden joukko.
        set_id, matrices = next(iter(matrix_sets.items()))
        return {
            "endpoint": endpoint.split("?", 1)[0],
            "layer": layer_name,
            "style": style_id,
            "format": format_name,
            "matrix_set": set_id,
            "matrices": sorted(matrices, key=lambda item: item["scale"], reverse=True),
        }

    def _download_oskari_wmts_geotiff(self, layer_name, layer_title, boundary_fc, workspace):
        """Lataa Oskarin WMTS-tason leikkausalue ja mosaiikoi sen GeoTIFFiksi."""
        metadata = self._traficom_wmts_layer_metadata(layer_name)
        ext = self._boundary_extent_3067(boundary_fc)
        tile_size_m = 0.00028
        max_tiles = 256
        selected = None
        # Matrix list is coarse-to-fine; use the finest resolution that stays
        # within the request cap instead of immediately choosing the overview.
        for matrix in reversed(metadata["matrices"]):
            resolution = matrix["scale"] * tile_size_m
            tile_w = matrix["tile_width"] * resolution
            tile_h = matrix["tile_height"] * resolution
            first_col = int(math.floor((ext.XMin - matrix["origin_x"]) / tile_w))
            last_col = int(math.floor((ext.XMax - matrix["origin_x"] - 1e-8) / tile_w))
            first_row = int(math.floor((matrix["origin_y"] - ext.YMax) / tile_h))
            last_row = int(math.floor((matrix["origin_y"] - ext.YMin - 1e-8) / tile_h))
            limits = matrix.get("limits") or {
                "min_row": 0, "max_row": matrix["matrix_height"] - 1,
                "min_col": 0, "max_col": matrix["matrix_width"] - 1,
            }
            first_col = max(first_col, limits["min_col"], 0)
            last_col = min(last_col, limits["max_col"], matrix["matrix_width"] - 1)
            first_row = max(first_row, limits["min_row"], 0)
            last_row = min(last_row, limits["max_row"], matrix["matrix_height"] - 1)
            if last_col < first_col or last_row < first_row:
                continue
            tile_count = (last_col - first_col + 1) * (last_row - first_row + 1)
            if tile_count <= max_tiles:
                selected = {
                    "matrix": matrix,
                    "resolution": resolution,
                    "first_col": first_col,
                    "last_col": last_col,
                    "first_row": first_row,
                    "last_row": last_row,
                    "tile_count": tile_count,
                }
                break
        if selected is None:
            raise Exception(
                "Valitulle alueelle tarvittaisiin WMTS:stä yli {} tiiltä.".format(max_tiles)
            )

        matrix = selected["matrix"]
        tile_width = matrix["tile_width"]
        tile_height = matrix["tile_height"]
        tile_w = tile_width * selected["resolution"]
        tile_h = tile_height * selected["resolution"]
        work_dir = tempfile.mkdtemp(prefix="oskari_wmts_", dir=workspace)
        png_paths = []
        rgb_paths = []
        try:
            tile_plan = []
            for row in range(selected["first_row"], selected["last_row"] + 1):
                for col in range(selected["first_col"], selected["last_col"] + 1):
                    params = {
                        "SERVICE": "WMTS",
                        "REQUEST": "GetTile",
                        "VERSION": "1.0.0",
                        "LAYER": metadata["layer"],
                        "STYLE": metadata["style"],
                        "FORMAT": metadata["format"],
                        "TILEMATRIXSET": metadata["matrix_set"],
                        "TILEMATRIX": matrix["id"],
                        "TILEROW": str(row),
                        "TILECOL": str(col),
                    }
                    tile_plan.append({
                        "url": metadata["endpoint"] + "?" + urllib.parse.urlencode(params),
                        "png_path": os.path.join(work_dir, "tile_{}_{}.png".format(row, col)),
                        "row": row,
                        "col": col,
                    })
            workers = min(max(1, int(getattr(self, "_tile_workers", 4) or 1)), 8, len(tile_plan))
            if workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    payloads = list(pool.map(
                        lambda item: self._download_oskari_wmts_tile(item["url"]),
                        tile_plan,
                    ))
            else:
                payloads = [self._download_oskari_wmts_tile(item["url"]) for item in tile_plan]

            for item, raw in zip(tile_plan, payloads):
                with open(item["png_path"], "wb") as handle:
                    handle.write(raw)
                class _TileExtent:
                    pass
                tile_ext = _TileExtent()
                tile_ext.XMin = matrix["origin_x"] + item["col"] * tile_w
                tile_ext.XMax = tile_ext.XMin + tile_w
                tile_ext.YMax = matrix["origin_y"] - item["row"] * tile_h
                tile_ext.YMin = tile_ext.YMax - tile_h
                self._write_world_file(
                    item["png_path"], tile_ext, tile_width, tile_height
                )
                png_paths.append((item["png_path"], tile_ext))

            for png_path, tile_ext in png_paths:
                rgb_path = os.path.splitext(png_path)[0] + "_rgb.tif"
                try:
                    self._colormap_to_rgb(png_path, rgb_path)
                except Exception:
                    # Traficomin WMTS palvelee sekä PNG8- että RGB-PNG-laattoja.
                    # ColormapToRGB käsittelee vain palettikuvat; tavallinen
                    # RGB-PNG kopioidaan suoraan GeoTIFFiksi.
                    arcpy.management.CopyRaster(
                        png_path, rgb_path, format="TIFF"
                    )
                self._write_world_file(rgb_path, tile_ext, tile_width, tile_height)
                rgb_paths.append(rgb_path)

            if not rgb_paths:
                raise Exception("Traficom WMTS ei palauttanut yhtään tiiltä.")
            band_count = int(arcpy.Describe(rgb_paths[0]).bandCount or 3)
            output_path = os.path.join(work_dir, "oskari_wmts_mosaic.tif")
            arcpy.management.MosaicToNewRaster(
                rgb_paths,
                work_dir,
                os.path.basename(output_path),
                coordinate_system_for_the_raster=arcpy.SpatialReference(3067),
                pixel_type="8_BIT_UNSIGNED",
                number_of_bands=band_count,
                cellsize=selected["resolution"],
                mosaic_method="FIRST",
                mosaic_colormap_mode="REJECT",
            )
            final_path = os.path.join(
                workspace, "oskari_wmts_{}_{}.tif".format(
                    self._sanitize_table_name(layer_name.split(":")[-1]),
                    uuid.uuid4().hex[:8],
                )
            )
            arcpy.management.CopyRaster(output_path, final_path)
            self._msg(
                "[INFO] Oskari WMTS -mosaiikki valmis: {} ({}/{} laattaa, "
                "matriisi {}).".format(
                    layer_title or layer_name, len(rgb_paths), selected["tile_count"], matrix["id"]
                )
            )
            return final_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _download_kapsi_image_bytes(self, request_url, attempts=3):
        """Lataa kokonainen JPEG ja yritä katkennutta chunked-vastausta uudelleen."""
        last_error = None
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(request_url, headers={
                "User-Agent": "ArcGISPro-KapsiBasemapTool/1.0",
                "Accept": "image/jpeg",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read()
                    ctype = (resp.headers.get("Content-Type", "") or "").lower()
                    content_length = resp.headers.get("Content-Length")
                if "xml" in ctype or "html" in ctype or not ctype.startswith("image/"):
                    raise Exception("palvelu palautti kuvan sijaan sisältötyypin '{}'".format(ctype or "tuntematon"))
                if content_length and len(raw) != int(content_length):
                    raise http.client.IncompleteRead(raw, int(content_length) - len(raw))
                if len(raw) < 4 or not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
                    raise http.client.IncompleteRead(raw)
                return raw
            except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, OSError) as ex:
                last_error = ex
                if attempt < attempts:
                    time.sleep(0.5 * attempt)
                    continue
            except Exception as ex:
                last_error = ex
                break
        raise Exception(
            "Kapsi WMS -kuvan lataus katkesi {} yrityksen jälkeen: {}".format(
                attempts, last_error or "tuntematon verkkovirhe"
            )
        )

    @staticmethod
    def _kapsi_request_layer(service_base, selected_layer):
        return selected_layer

    def _kapsi_target_gsd(self, layer_ref, selected_layer):
        """Palauta valitun mittakaavatason WMS-mittakaavaan sopiva pikselikoko."""
        if not re.search(r"_\d+(?:k|m)$", selected_layer or "", re.IGNORECASE):
            return 20.0, False

        scale_range = getattr(self, "_kapsi_layer_scale_ranges", {}).get(layer_ref)
        if not scale_range:
            return 20.0, False

        min_scale, max_scale = scale_range
        if min_scale is not None and max_scale is not None:
            scale_denominator = math.sqrt(min_scale * max_scale)
        elif max_scale is not None:
            scale_denominator = max_scale * 0.75
        else:
            scale_denominator = min_scale * 1.25
        # Kapsin MapServer käyttää oletuksena 72 DPI:tä mittakaavan laskentaan.
        mapserver_pixel_size_m = 0.0254 / 72.0
        return max(0.1, scale_denominator * mapserver_pixel_size_m), True

    @staticmethod
    def _kapsi_exact_tile_axis_bounds(axis_min, axis_max, tile_span, index, count):
        """Pidä myös reunalaatta täysikokoisena, ettei WMS-taso katoa mittakaavarajalla."""
        if count == 1:
            center = (axis_min + axis_max) / 2.0
            return center - (tile_span / 2.0), center + (tile_span / 2.0)
        tile_min = min(axis_min + (index * tile_span), axis_max - tile_span)
        return tile_min, tile_min + tile_span

    def _kapsi_exact_grid_bounds(self, ext, tile_span, cols, rows):
        x_min, _ = self._kapsi_exact_tile_axis_bounds(
            ext.XMin, ext.XMax, tile_span, 0, cols
        )
        _, x_max = self._kapsi_exact_tile_axis_bounds(
            ext.XMin, ext.XMax, tile_span, cols - 1, cols
        )
        y_min, _ = self._kapsi_exact_tile_axis_bounds(
            ext.YMin, ext.YMax, tile_span, 0, rows
        )
        _, y_max = self._kapsi_exact_tile_axis_bounds(
            ext.YMin, ext.YMax, tile_span, rows - 1, rows
        )
        return x_min, y_min, x_max, y_max

    @staticmethod
    def _kapsi_tile_batches(cols, rows, batch_size=25):
        """Jaa laattaruutu peräkkäisiin eriin, joissa on enintään batch_size laattaa."""
        if cols < 1 or rows < 1 or batch_size < 1:
            return []
        cols_per_batch = min(cols, batch_size)
        rows_per_batch = max(1, batch_size // cols_per_batch)
        batches = []
        for row_start in range(0, rows, rows_per_batch):
            row_end = min(rows, row_start + rows_per_batch)
            for col_start in range(0, cols, cols_per_batch):
                col_end = min(cols, col_start + cols_per_batch)
                batches.append((row_start, row_end, col_start, col_end))
        return batches

    def _download_kapsi_wms_jpeg(self, layer_id: str, boundary_fc: str, workspace: str):
        service_base = self.kapsi_wms_base
        service_layer = layer_id
        if "|" in (layer_id or ""):
            parts = layer_id.split("|", 1)
            service_base = parts[0].strip() or self.kapsi_wms_base
            service_layer = parts[1].strip() or layer_id

        layer_ref = "{}|{}".format(service_base, service_layer)
        request_layer = self._kapsi_request_layer(service_base, service_layer)

        ext = self._boundary_extent_3067(boundary_fc)

        # Determine tiling: for large areas, split into a grid so each tile
        # has a resolution suitable for the selected Kapsi map series.
        extent_w = ext.XMax - ext.XMin
        extent_h = ext.YMax - ext.YMin
        tile_px = 4096
        target_gsd, exact_scale = self._kapsi_target_gsd(layer_ref, service_layer)
        tile_span = tile_px * target_gsd
        cols = max(1, int(math.ceil(extent_w / tile_span)))
        rows = max(1, int(math.ceil(extent_h / tile_span)))
        required_tiles = cols * rows
        if required_tiles > 25:
            if exact_scale:
                self._msg(
                    "[INFO] Kapsi-taso '{}' jaetaan {} laatan ruudukoksi useaan "
                    "enintään 25 laatan latauserään.".format(
                        service_layer, required_tiles
                    )
                )
            else:
                scale = math.sqrt(25.0 / (cols * rows))
                cols = max(1, int(cols * scale))
                rows = max(1, int(rows * scale))

        tile_w = extent_w / cols
        tile_h = extent_h / rows
        output_ext = ext
        if exact_scale:
            output_bounds = self._kapsi_exact_grid_bounds(
                ext, tile_span, cols, rows
            )

            class _OutputExt:
                pass

            output_ext = _OutputExt()
            (
                output_ext.XMin,
                output_ext.YMin,
                output_ext.XMax,
                output_ext.YMax,
            ) = output_bounds

        raster_dir = self._raster_folder(workspace)
        tile_paths = []
        tile_batches = self._kapsi_tile_batches(cols, rows)

        for batch_index, (row_start, row_end, col_start, col_end) in enumerate(
            tile_batches, 1
        ):
            if len(tile_batches) > 1:
                batch_tiles = (row_end - row_start) * (col_end - col_start)
                self._msg(
                    "[INFO] Kapsi-latauserä {}/{} ({} laattaa).".format(
                        batch_index, len(tile_batches), batch_tiles
                    )
                )
            # Laattojen bbox-laskenta on puhdasta aritmetiikkaa, joten koko erä
            # voidaan suunnitella kerralla ja ladata rinnakkain. Lataus on
            # verkkosidonnaista, joten tämä on erän suurin yksittäinen säästö.
            batch_plan = []
            for row_i in range(row_start, row_end):
                for col_i in range(col_start, col_end):
                    if exact_scale:
                        t_xmin, t_xmax = self._kapsi_exact_tile_axis_bounds(
                            ext.XMin, ext.XMax, tile_span, col_i, cols
                        )
                        t_ymin, t_ymax = self._kapsi_exact_tile_axis_bounds(
                            ext.YMin, ext.YMax, tile_span, row_i, rows
                        )
                    else:
                        t_xmin = ext.XMin + col_i * tile_w
                        t_ymin = ext.YMin + row_i * tile_h
                        t_xmax = t_xmin + tile_w
                        t_ymax = t_ymin + tile_h
                    params = {
                        "FORMAT": "image/jpeg",
                        "VERSION": "1.1.1",
                        "SERVICE": "WMS",
                        "REQUEST": "GetMap",
                        "LAYERS": request_layer,
                        "STYLES": "",
                        "SRS": "EPSG:3067",
                        "WIDTH": str(tile_px),
                        "HEIGHT": str(tile_px),
                        "BBOX": "{},{},{},{}".format(t_xmin, t_ymin, t_xmax, t_ymax),
                    }
                    request_url = "{}?{}".format(service_base, urllib.parse.urlencode(params))
                    # Nimi varataan pääsäikeessä, jotta rinnakkaiset lataukset
                    # eivät voi päätyä samaan tiedostonimeen.
                    tile_name = self._validated_name(
                        "Kapsi_{}_tile_{}_{}".format(service_layer, row_i, col_i), raster_dir
                    ) + ".jpg"
                    batch_plan.append({
                        "url": request_url,
                        "path": os.path.join(raster_dir, tile_name),
                        "bounds": (t_xmin, t_ymin, t_xmax, t_ymax),
                    })

            workers = max(1, int(getattr(self, "_tile_workers", 1) or 1))
            workers = min(workers, len(batch_plan)) or 1
            if workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    payloads = list(pool.map(
                        lambda item: self._download_kapsi_image_bytes(item["url"]),
                        batch_plan,
                    ))
            else:
                payloads = [
                    self._download_kapsi_image_bytes(item["url"])
                    for item in batch_plan
                ]

            for item, raw in zip(batch_plan, payloads):
                tile_path = item["path"]
                with open(tile_path, "wb") as handle:
                    handle.write(raw)

                # Create a simple Extent-like object for the world file
                class _TileExt:
                    pass
                te = _TileExt()
                te.XMin, te.YMin, te.XMax, te.YMax = item["bounds"]
                self._write_world_file(tile_path, te, tile_px, tile_px)
                tile_paths.append(tile_path)

        if len(tile_paths) == 1:
            # Single tile – rename to final output name
            final_name = self._validated_name("Kapsi_{}_raster".format(service_layer), raster_dir) + ".jpg"
            final_path = os.path.join(raster_dir, final_name)
            if tile_paths[0] != final_path:
                # Rename jpg + world + prj files
                for src_ext, dst_ext in [(".jpg", ".jpg"), (".jgw", ".jgw"), (".prj", ".prj")]:
                    src = os.path.splitext(tile_paths[0])[0] + src_ext
                    dst = os.path.splitext(final_path)[0] + dst_ext
                    if os.path.exists(src):
                        try:
                            os.rename(src, dst)
                        except Exception:
                            pass
            return final_path
        else:
            # Multiple tiles – mosaic them into a single raster
            out_name = self._validated_name("Kapsi_{}_raster".format(service_layer), raster_dir) + ".jpg"
            out_path = os.path.join(raster_dir, out_name)
            try:
                arcpy.management.MosaicToNewRaster(
                    tile_paths, raster_dir, os.path.basename(out_path),
                    coordinate_system_for_the_raster=arcpy.SpatialReference(3067),
                    pixel_type="8_BIT_UNSIGNED",
                    number_of_bands=3,
                    mosaic_method="LAST",
                )
                # JPEG ei kanna geotransformaatiota luotettavasti kaikissa
                # ArcGIS-versioissa. Kirjoita mosaiikille aina eksplisiittinen
                # world file ja EPSG:3067-prj.
                try:
                    mosaic_desc = arcpy.Describe(out_path)
                    mosaic_width = int(getattr(mosaic_desc, "width", tile_px * cols))
                    mosaic_height = int(getattr(mosaic_desc, "height", tile_px * rows))
                except Exception:
                    mosaic_width, mosaic_height = tile_px * cols, tile_px * rows
                self._write_world_file(
                    out_path, output_ext, mosaic_width, mosaic_height
                )
                # Clean up tiles
                for tp in tile_paths:
                    for ext_s in [".jpg", ".jgw", ".prj"]:
                        p = os.path.splitext(tp)[0] + ext_s
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass
            except Exception as ex:
                self._warn("[VAROITUS] Tiilien yhdistäminen mosaiikiksi epäonnistui: {}. Palautetaan ensimmäinen tiili.".format(ex))
                return tile_paths[0]
            return out_path

    def _raster_folder(self, workspace: str) -> str:
        """Return a plain folder for raster output — GDBs cannot hold loose image files."""
        if not self._is_filesystem_workspace(workspace):
            parent = os.path.dirname(os.path.abspath(workspace))
            return parent if os.path.isdir(parent) else self._scratch_folder()
        return workspace

    def _boundary_extent_3067(self, boundary_fc):
        desc = arcpy.Describe(boundary_fc)
        sr = getattr(desc, "spatialReference", None)
        if sr and int(getattr(sr, "factoryCode", 0) or 0) == MML_WMTS_EPSG:
            # WMTS-ruudukon laskenta tehdään nimenomaan latausrajauksen
            # Describe().extentistä, ei oletetusta projektinäkymästä.
            return desc.extent
        if not sr:
            raise Exception("Latausrajauksen koordinaatistoa ei voitu tunnistaa.")
        tmp = os.path.join(self._scratch_gdb(), f"bnd_3067_{uuid.uuid4().hex[:8]}")
        arcpy.management.Project(boundary_fc, tmp, arcpy.SpatialReference(MML_WMTS_EPSG))
        ext = arcpy.Describe(tmp).extent
        self._safe_delete(tmp)
        return ext

    def _prepare_custom_boundary(self, custom_layer, metrics=None):
        """Normalisoi käyttäjän oma rajausaineisto leikkauskelpoiseksi:
        projisoi EPSG:3067:ään ja muuntaa viivan/pisteen polygoniksi, jotta
        bbox-haku ja arcpy.analysis.Clip toimivat oikein."""
        metrics = metrics if metrics is not None else PhaseMetrics()
        describe_start = time.perf_counter()
        desc = arcpy.Describe(custom_layer)
        metrics.add("lähtöaineiston kuvaustietojen lukeminen", time.perf_counter() - describe_start)
        sr = desc.spatialReference
        shape_type = getattr(desc, "shapeType", "Polygon")

        local_source_name = "custom_source_{}".format(uuid.uuid4().hex[:8])
        source_start = time.perf_counter()
        local_source = self._export_geometry_only(custom_layer, self._scratch_gdb(), local_source_name)
        metrics.add("rajauksen kopiointi", time.perf_counter() - source_start)
        self._msg("[INFO] Oma rajaus kopioitu paikalliseen scratch-GDB:hen ({:.1f} s).".format(
            time.perf_counter() - source_start
        ))

        src_fc = local_source
        if not sr or sr.factoryCode != 3067:
            projected = os.path.join(self._scratch_gdb(), "custom_3067_{}".format(uuid.uuid4().hex[:8]))
            project_start = time.perf_counter()
            arcpy.management.Project(local_source, projected, arcpy.SpatialReference(3067))
            self._safe_delete(local_source)
            src_fc = projected
            self._msg("[INFO] Oma rajaus projisoitu EPSG:3067:ään ({:.1f} s).".format(
                time.perf_counter() - project_start
            ))
            metrics.add("rajauksen projektointi", time.perf_counter() - project_start)
        else:
            metrics.skip("rajauksen projektointi", "ei tarpeen (aineisto on jo EPSG:3067)")

        if shape_type == "Polygon":
            metrics.skip("geometrian tarkistus tai korjaus", "ei tarpeen")
            return src_fc

        # Viiva/piste -> polygoni (konveksi peite kaikista kohteista)
        poly_fc = os.path.join(self._scratch_gdb(), f"custom_hull_{uuid.uuid4().hex[:8]}")
        hull_start = time.perf_counter()
        arcpy.management.MinimumBoundingGeometry(
            src_fc, poly_fc, "CONVEX_HULL", "ALL"
        )
        self._msg("[INFO] Oma viiva/piste muunnettu polygonirajaukseksi ({:.1f} s).".format(
            time.perf_counter() - hull_start
        ))
        metrics.add("geometrian tarkistus tai korjaus", time.perf_counter() - hull_start)
        self._safe_delete(src_fc)
        return poly_fc

    def _parse_mml_wmts_layer(self, xml_bytes, layer_id):
        """Palauta EPSG:3067 WMTS-tason tyyli, formaatti ja matriisit."""
        root = ET.fromstring(xml_bytes)
        contents = next((elem for elem in root.iter() if elem.tag.split("}")[-1] == "Contents"), None)
        if contents is None:
            raise Exception("MML WMTS -vastauksesta puuttuu Contents.")

        selected_layer = None
        for elem in list(contents):
            if elem.tag.split("}")[-1] != "Layer":
                continue
            if self._direct_xml_text(elem, "Identifier") == layer_id:
                selected_layer = elem
                break
        if selected_layer is None:
            raise Exception("MML WMTS -tasoa '{}' ei löytynyt.".format(layer_id))

        formats = [
            (child.text or "").strip()
            for child in list(selected_layer)
            if child.tag.split("}")[-1] == "Format" and child.text
        ]
        image_format = next((fmt for fmt in formats if fmt.lower() == "image/png"), None)
        image_format = image_format or next((fmt for fmt in formats if fmt.lower() in ("image/jpeg", "image/jpg")), None)
        if not image_format:
            raise Exception("MML WMTS -tasolla ei ole tuettua PNG/JPEG-kuvaformaattia.")

        style_id = "default"
        styles = [child for child in list(selected_layer) if child.tag.split("}")[-1] == "Style"]
        preferred_style = next(
            (style for style in styles if str(style.attrib.get("isDefault", "")).lower() == "true"),
            styles[0] if styles else None,
        )
        if preferred_style is not None:
            style_id = self._direct_xml_text(preferred_style, "Identifier") or style_id

        linked_sets = []
        for child in list(selected_layer):
            if child.tag.split("}")[-1] == "TileMatrixSetLink":
                matrix_set_id = self._direct_xml_text(child, "TileMatrixSet")
                if matrix_set_id:
                    linked_sets.append(matrix_set_id)

        matrix_sets = {}
        for elem in list(contents):
            if elem.tag.split("}")[-1] != "TileMatrixSet":
                continue
            matrix_set_id = self._direct_xml_text(elem, "Identifier")
            if matrix_set_id:
                matrix_sets[matrix_set_id] = elem

        selected_set_id = None
        selected_set = None
        for matrix_set_id in linked_sets:
            elem = matrix_sets.get(matrix_set_id)
            if elem is None:
                continue
            crs = self._direct_xml_text(elem, "SupportedCRS") or ""
            if "3067" in crs or "tm35" in matrix_set_id.lower():
                selected_set_id, selected_set = matrix_set_id, elem
                break
        if selected_set is None:
            raise Exception("MML WMTS -tasolta puuttuu EPSG:3067 TileMatrixSet.")

        matrices = []
        for elem in list(selected_set):
            if elem.tag.split("}")[-1] != "TileMatrix":
                continue
            try:
                matrix_id = self._direct_xml_text(elem, "Identifier")
                scale = float(self._direct_xml_text(elem, "ScaleDenominator"))
                origin_parts = re.split(r"[\s,]+", self._direct_xml_text(elem, "TopLeftCorner") or "")
                origin_values = [float(value) for value in origin_parts if value]
                if len(origin_values) != 2:
                    continue
                origin_x, origin_y = origin_values
                # Osa palveluista noudattaa EPSG-akselijärjestystä (N,E),
                # vaikka WMTS-laskenta tarvitsee arvot järjestyksessä (E,N).
                if abs(origin_x) > 2000000 and abs(origin_y) < 2000000:
                    origin_x, origin_y = origin_y, origin_x
                matrices.append({
                    "id": matrix_id,
                    "resolution": scale * 0.00028,
                    "origin_x": origin_x,
                    "origin_y": origin_y,
                    "tile_width": int(self._direct_xml_text(elem, "TileWidth")),
                    "tile_height": int(self._direct_xml_text(elem, "TileHeight")),
                    "matrix_width": int(self._direct_xml_text(elem, "MatrixWidth")),
                    "matrix_height": int(self._direct_xml_text(elem, "MatrixHeight")),
                })
            except (TypeError, ValueError):
                continue
        if not matrices:
            raise Exception("MML WMTS -palvelusta ei löytynyt käyttökelpoisia tiilimatriiseja.")
        return {
            "style": style_id,
            "format": image_format,
            "matrix_set": selected_set_id,
            "matrices": matrices,
        }

    @staticmethod
    def _wmts_tile_range(matrix, ext):
        span_x = matrix["tile_width"] * matrix["resolution"]
        span_y = matrix["tile_height"] * matrix["resolution"]
        epsilon_x = max(span_x * 1e-10, 1e-8)
        epsilon_y = max(span_y * 1e-10, 1e-8)
        col_min = int(math.floor((ext.XMin - matrix["origin_x"]) / span_x))
        col_max = int(math.floor((ext.XMax - epsilon_x - matrix["origin_x"]) / span_x))
        row_min = int(math.floor((matrix["origin_y"] - ext.YMax) / span_y))
        row_max = int(math.floor((matrix["origin_y"] - ext.YMin - epsilon_y) / span_y))
        col_min = max(0, col_min)
        row_min = max(0, row_min)
        col_max = min(matrix["matrix_width"] - 1, col_max)
        row_max = min(matrix["matrix_height"] - 1, row_max)
        if col_max < col_min or row_max < row_min:
            return None
        return col_min, col_max, row_min, row_max

    def _choose_mml_wmts_matrix(self, matrices, ext, max_tiles=25):
        candidates = []
        for matrix in matrices:
            tile_range = self._wmts_tile_range(matrix, ext)
            if tile_range is None:
                continue
            col_min, col_max, row_min, row_max = tile_range
            count = (col_max - col_min + 1) * (row_max - row_min + 1)
            candidates.append((matrix["resolution"], count, matrix, tile_range))
        if not candidates:
            raise Exception("Valittu alue ei osu MML WMTS -palvelun tiiliruudukkoon.")
        candidates.sort(key=lambda item: item[0])
        for candidate in candidates:
            if candidate[1] <= max_tiles:
                return candidate[2], candidate[3]
        # Karkeinkin taso on turvallisin vaihtoehto hyvin suurelle alueelle.
        return candidates[-1][2], candidates[-1][3]

    @staticmethod
    def _mml_wmts_resolution(level):
        level = int(level)
        if level < MML_WMTS_MIN_LEVEL or level > MML_WMTS_MAX_LEVEL:
            raise ValueError("MML WMTS -tason pitää olla välillä 0–13.")
        return float(2 ** (MML_WMTS_MAX_LEVEL - level))

    @staticmethod
    def _mml_wmts_tile_range(ext, level):
        """Laske MML:n kiinteän ETRS-TM35FIN-ruudukon kattavat tiilet."""
        resolution = VaylaWFSDownloader._mml_wmts_resolution(level)
        tile_span = MML_WMTS_TILE_SIZE * resolution
        # Kun rajaus päättyy täsmälleen tiilen reunaan, viimeistä viereistä
        # tiiltä ei tarvita. Pieni epsilon estää liukulukujen vuoksi syntyvän
        # ylimääräisen rivi-/sarakepyynnön.
        epsilon = max(resolution * 1e-10, 1e-8)
        first_col = int(math.floor((ext.XMin - MML_WMTS_ORIGIN_X) / tile_span))
        last_col = int(math.floor(
            (ext.XMax - epsilon - MML_WMTS_ORIGIN_X) / tile_span
        ))
        first_row = int(math.floor((MML_WMTS_ORIGIN_Y - ext.YMax) / tile_span))
        last_row = int(math.floor(
            (MML_WMTS_ORIGIN_Y - ext.YMin - epsilon) / tile_span
        ))
        first_col = max(0, first_col)
        first_row = max(0, first_row)
        if last_col < first_col or last_row < first_row:
            return None
        return first_row, last_row, first_col, last_col

    @classmethod
    def _choose_mml_fixed_wmts_level(cls, ext):
        """Valitse oletustaso 9, mutta harvenna tasoa yli 256 tiilen alueella."""
        for level in range(MML_WMTS_DEFAULT_LEVEL, MML_WMTS_MIN_LEVEL - 1, -1):
            tile_range = cls._mml_wmts_tile_range(ext, level)
            if tile_range is None:
                continue
            first_row, last_row, first_col, last_col = tile_range
            count = (last_row - first_row + 1) * (last_col - first_col + 1)
            if count <= MML_WMTS_MAX_TILES:
                return level, tile_range, count
        raise Exception(
            "MML WMTS -rajaukselle tarvittaisiin yli 256 tiiltä myös tasolla 0."
        )

    def _mml_wmts_tile_url(self, layer_id, level, row, column, api_key):
        key = (api_key or "").strip()
        if not key:
            raise Exception("MML WMTS vaatii API-avaimen.")
        service_url = self.mml_wmts_base.rstrip("/")
        encoded_key = urllib.parse.quote(key, safe="")
        return "{}/{}/default/{}/{}/{}/{}.png?api-key={}".format(
            service_url,
            layer_id,
            MML_WMTS_MATRIX_SET,
            int(level),
            int(row),
            int(column),
            encoded_key,
        )

    def _download_mml_wmts_tile(self, request_url, api_key):
        """Lataa yksi PNG8-tiili ja vaadi aidon PNG-tiedoston allekirjoitus."""
        req = urllib.request.Request(
            request_url,
            headers=dict(self._mml_auth_headers(api_key), Accept="image/png"),
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                raw = response.read()
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                raise Exception(
                    "MML WMTS hylkäsi API-avaimen tiiltä ladattaessa (HTTP {}).".format(
                        ex.code
                    )
                )
            raise
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Exception("MML WMTS palautti kuvan sijaan muuta sisältöä kuin PNG:n.")
        return raw

    @staticmethod
    def _path_exists(path):
        try:
            if arcpy.Exists(path):
                return True
        except Exception:
            pass
        return os.path.exists(path)

    def _colormap_to_rgb(self, png_path, rgb_path):
        """Muunna yksi PNG8-tiili RGB-TIFFiksi ennen mosaiikkia."""
        converters = []
        image_analysis = getattr(arcpy, "ia", None)
        spatial_analysis = getattr(arcpy, "sa", None)
        primary = getattr(image_analysis, "ColormapToRGB", None)
        fallback = getattr(spatial_analysis, "ColormapToRGB", None)
        if callable(primary):
            converters.append(("arcpy.ia.ColormapToRGB", primary))
        if callable(fallback) and fallback is not primary:
            converters.append(("arcpy.sa.ColormapToRGB", fallback))
        if not converters:
            raise Exception(
                "ArcGISin ColormapToRGB-toimintoa ei ole saatavilla (arcpy.ia/arcpy.sa)."
            )

        errors = []
        for converter_name, converter in converters:
            try:
                try:
                    result = converter(png_path, rgb_path)
                except TypeError:
                    # Spatial Analystin joissakin Pro-versioissa funktio
                    # ottaa vain syöterasterin ja palauttaa Raster-olion.
                    result = converter(png_path)
                # Image Analystin ja Spatial Analystin versiot palauttavat eri
                # tavoin Raster-olion tai kirjoittavat annetun polun suoraan.
                if result is not None and not self._path_exists(rgb_path):
                    save = getattr(result, "save", None)
                    if callable(save):
                        save(rgb_path)
                if self._path_exists(rgb_path):
                    return rgb_path
                raise Exception("muunnos ei tuottanut RGB-TIFFiä")
            except Exception as ex:
                errors.append("{}: {}".format(converter_name, ex))
                try:
                    if self._path_exists(rgb_path):
                        self._safe_delete(rgb_path)
                except Exception:
                    pass
        raise Exception("PNG8-tiilen RGB-muunnos epäonnistui: {}".format("; ".join(errors)))

    def _ensure_raster_file_gdb(self, workspace):
        """Palauta File GDB rasteritulokselle, luo sellainen tarvittaessa."""
        if not workspace:
            try:
                workspace = arcpy.mp.ArcGISProject("CURRENT").defaultGeodatabase
            except Exception:
                workspace = self._scratch_gdb()
        workspace = os.path.abspath(str(workspace))
        if workspace.lower().endswith(".gdb"):
            if not self._path_exists(workspace):
                raise Exception("Rasterin File Geodatabasea ei löydy: {}".format(workspace))
            return workspace

        if not os.path.isdir(workspace):
            raise Exception("Rasterin tallennuskohde ei ole kansio tai File GDB: {}".format(workspace))
        gdb_name = self._sanitize_table_name("Suomenvaylat_MML") + ".gdb"
        gdb_path = os.path.join(workspace, gdb_name)
        if not self._path_exists(gdb_path):
            arcpy.management.CreateFileGDB(workspace, gdb_name)
        return gdb_path

    def _download_mml_wmts_geotiff(
        self, layer_id: str, boundary_fc: str, workspace: str, api_key: str,
        output_gdb: str = None,
    ):
        """Lataa MML:n PNG8-tiilet, muunna ne yksitellen RGB:ksi ja mosaiikoi.

        ``output_gdb`` annetaan MML-taustakartalle, jolloin lopputulos syntyy
        suoraan File Geodatabaseen. Yleisen MML-rasterilähteen vanha kutsu voi
        edelleen käyttää väliaikaista kansiota ilman että karttatyönkulku
        muuttuu.
        """
        key = (api_key or "").strip()
        if not key:
            raise Exception("MML WMTS vaatii API-avaimen.")
        layer_id = (layer_id or "").strip()
        if not layer_id:
            raise Exception("MML WMTS -tason tunniste puuttuu.")

        # Rajaus otetaan Describe().extentistä. Jos aineisto ei ole
        # EPSG:3067:ssä, _boundary_extent_3067 projisoi sen ensin väliaikaisesti.
        ext = self._boundary_extent_3067(boundary_fc)
        level, tile_range, tile_count = self._choose_mml_fixed_wmts_level(ext)
        first_row, last_row, first_col, last_col = tile_range
        resolution = self._mml_wmts_resolution(level)
        self._msg(
            "[INFO] MML-tason lataus alkaa: {} (taso {}, {} tiiltä).".format(
                layer_id, level, tile_count
            )
        )

        raster_dir = self._raster_folder(workspace)
        if not os.path.isdir(raster_dir):
            os.makedirs(raster_dir, exist_ok=True)
        temporary_dir = tempfile.mkdtemp(prefix="mml_wmts_tiles_", dir=raster_dir)
        png_paths = []
        rgb_paths = []
        tile_span = MML_WMTS_TILE_SIZE * resolution

        try:
            # Sama rinnakkaistus kuin Kapsin laatoissa: verkkosidonnainen työ
            # tehdään säikeissä, tiedostojen kirjoitus pääsäikeessä.
            tile_plan = []
            for row in range(first_row, last_row + 1):
                for column in range(first_col, last_col + 1):
                    tile_stem = self._sanitize_table_name(
                        "MML_{}_L{}_R{}_C{}".format(layer_id, level, row, column)
                    )
                    tile_plan.append({
                        "url": self._mml_wmts_tile_url(layer_id, level, row, column, key),
                        "png_path": os.path.join(temporary_dir, tile_stem + ".png"),
                        "row": row,
                        "column": column,
                    })

            workers = max(1, int(getattr(self, "_tile_workers", 1) or 1))
            workers = min(workers, len(tile_plan)) or 1
            if workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    payloads = list(pool.map(
                        lambda item: self._download_mml_wmts_tile(item["url"], key),
                        tile_plan,
                    ))
            else:
                payloads = [
                    self._download_mml_wmts_tile(item["url"], key)
                    for item in tile_plan
                ]

            downloaded = 0
            for item, raw in zip(tile_plan, payloads):
                png_path = item["png_path"]
                with open(png_path, "wb") as handle:
                    handle.write(raw)

                class _TileExtent:
                    pass

                tile_ext = _TileExtent()
                tile_ext.XMin = MML_WMTS_ORIGIN_X + item["column"] * tile_span
                tile_ext.XMax = tile_ext.XMin + tile_span
                tile_ext.YMax = MML_WMTS_ORIGIN_Y - item["row"] * tile_span
                tile_ext.YMin = tile_ext.YMax - tile_span
                self._write_world_file(
                    png_path, tile_ext, MML_WMTS_TILE_SIZE, MML_WMTS_TILE_SIZE
                )
                png_paths.append((png_path, tile_ext))
                downloaded += 1
            self._msg(
                "[EDISTYMINEN] Ladatut tiilet {}/{}".format(downloaded, tile_count)
            )

            self._msg(
                "[INFO] Kaikkien tiilien lataus valmis: {}/{}".format(
                    downloaded, tile_count
                )
            )
            self._msg("[INFO] RGB-muunnos alkaa: {} PNG8-tiiltä.".format(len(png_paths)))
            for png_path, tile_ext in png_paths:
                rgb_path = os.path.splitext(png_path)[0] + "_RGB.tif"
                self._colormap_to_rgb(png_path, rgb_path)
                # Varmista world/prj myös ColormapToRGB:n versiosta riippumatta.
                self._write_world_file(
                    rgb_path, tile_ext, MML_WMTS_TILE_SIZE, MML_WMTS_TILE_SIZE
                )
                rgb_paths.append(rgb_path)

            if not rgb_paths:
                raise Exception("MML WMTS ei palauttanut yhtään tiiltä.")

            output_workspace = output_gdb or raster_dir
            is_gdb = str(output_workspace).lower().endswith(".gdb")
            output_stem = "MML_{}_RGB".format(layer_id)
            if is_gdb:
                output_base = self._unique_output_name(output_stem, output_workspace)
            else:
                output_base = self._validated_name(output_stem, output_workspace)
                suffix = 1
                while os.path.exists(os.path.join(output_workspace, output_base + ".tif")):
                    output_base = "{}_{}".format(
                        self._validated_name(output_stem, output_workspace), suffix
                    )
                    suffix += 1
            output_name = output_base if is_gdb else output_base + ".tif"
            final_path = os.path.join(output_workspace, output_name)
            self._msg("[INFO] RGB-mosaiikki alkaa: {} RGB-TIFFiä.".format(len(rgb_paths)))

            # Tärkeää: MosaicToNewRaster saa vain RGB-TIFFit. PNG8-kuvia ei
            # koskaan yhdistetä suoraan, koska niiden väripaletit voivat erota.
            arcpy.management.MosaicToNewRaster(
                rgb_paths,
                output_workspace,
                output_name,
                coordinate_system_for_the_raster=arcpy.SpatialReference(MML_WMTS_EPSG),
                pixel_type="8_BIT_UNSIGNED",
                number_of_bands=3,
                cellsize=resolution,
                mosaic_method="FIRST",
                mosaic_colormap_mode="REJECT",
            )
            self._msg("[INFO] RGB-mosaiikki valmis: {}".format(final_path))
            if is_gdb:
                self._msg("[INFO] Rasteri tallennettu geodatabaseen: {}".format(final_path))
            return final_path
        finally:
            # PNG:t, world/prj-tiedostot ja väliaikaiset RGB-TIFFit ovat vain
            # ajon välivaiheita; lopullinen rasteri jää output_workspaceen.
            try:
                shutil.rmtree(temporary_dir, ignore_errors=True)
            except Exception:
                pass

    # ---------------------------
    # UI / PARAMETERS
    # ---------------------------
    def getParameterInfo(self):
        p_wfs_sources = arcpy.Parameter(
            displayName="Valitse rajapinnat",
            name="wfs_sources",
            datatype="GPValueTable",
            parameterType="Required",
            direction="Input"
        )
        p_wfs_sources.columns = [["GPString", "Rajapinta"]]
        p_wfs_sources.filters[0].type = "ValueList"
        p_wfs_sources.filters[0].list = self.wfs_registry.get_sources_list()
        p_wfs_sources.values = [["Väylä"]]

        p_layer_search = arcpy.Parameter(
            displayName="Suodata tasoja kirjoittamalla",
            name="layer_search",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_layer_search.value = ""

        p_layers = arcpy.Parameter(
            displayName="Valitse ladattavat aineistot",
            name="layers",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
            multiValue=True
        )
        p_layers.filter.type = "ValueList"
        # Lista täytetään laiskasti updateParameters-kutsussa, jotta työkalu-
        # ikkuna avautuu välittömästi ilman synkronista GetCapabilities-kutsua.
        p_layers.filter.list = []

        p_extent_type = arcpy.Parameter(
            displayName="Aluerajauksen taso",
            name="extent_type",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_extent_type.filter.list = [
            "Koko Suomi",
            "Elinvoimakeskus",
            "Hyvinvointialue",
            "Maakunta",
            "Kunta/Kaupunki",
            "Oma aineisto (Polygon/Polyline)"
        ]

        p_extent_value = arcpy.Parameter(
            displayName="Valitse alue",
            name="extent_value",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True
        )
        p_extent_value.enabled = False
        p_extent_value.filter.type = "ValueList"
        p_extent_value.filter.list = []

        p_custom_layer = arcpy.Parameter(
            displayName="Oma rajausaineisto",
            name="custom_layer",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        p_custom_layer.filter.list = ["Polygon", "Polyline"]
        p_custom_layer.enabled = False

        p_workspace = arcpy.Parameter(
            displayName="Tallennuskohde (GDB tai kansio)",
            name="workspace",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input"
        )
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            p_workspace.value = aprx.defaultGeodatabase
        except Exception:
            pass

        p_mml_api_key = arcpy.Parameter(
            displayName="MML API-avain (OGC API/vector tile -tasoille)",
            name="mml_api_key",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_mml_api_key.value = self._get_saved_secret("mml_api_key")

        p_karttapaikka_api_key = arcpy.Parameter(
            displayName="Karttapaikka API-avain (Maastotiedot OGC API -tasoille)",
            name="karttapaikka_api_key",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_karttapaikka_api_key.value = self._get_saved_secret("karttapaikka_api_key")

        p_karttakuva_user = arcpy.Parameter(
            displayName="MML Karttakuva käyttäjätunnus",
            name="karttakuva_user",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_karttakuva_user.value = self._get_saved_secret("karttakuva_user")
        p_karttakuva_user.enabled = False

        p_karttakuva_pass = arcpy.Parameter(
            displayName="MML Karttakuva salasana",
            name="karttakuva_pass",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_karttakuva_pass.value = self._get_saved_secret("karttakuva_pass")
        p_karttakuva_pass.enabled = False

        # Uudet parametrit lisätään aina listan loppuun, jotta aiemmat
        # indeksit (parameters[0]..[10]) pysyvät voimassa.
        p_refresh_layers = arcpy.Parameter(
            displayName="Päivitä tasolistaus palvelusta (ohita välimuisti)",
            name="refresh_layer_catalog",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        p_refresh_layers.value = False

        p_aino_token = arcpy.Parameter(
            displayName="Aino-token",
            name="aino_token",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_aino_token.value = self._normalize_aino_token(
            self._get_saved_secret("aino_token")
        )
        p_aino_token.enabled = False

        return [
            p_wfs_sources,
            p_layer_search, p_layers,
            p_extent_type, p_extent_value, p_custom_layer, p_workspace,
            p_mml_api_key,
            p_karttapaikka_api_key,
            p_karttakuva_user,
            p_karttakuva_pass,
            p_refresh_layers,
            p_aino_token
        ]

    def updateParameters(self, parameters):
        try:
            if not isinstance(getattr(self, "_layer_mapping_cache", None), dict):
                self._layer_mapping_cache = {}
            selected_before = self._parse_multivalue_param(parameters[2])
            source_values = self._source_values_from_param(parameters[0])
            layer_search = parameters[1].valueAsText or ""
            extent_type = parameters[3].valueAsText
            mml_api_key = (parameters[7].valueAsText or "").strip()
            karttapaikka_api_key = (parameters[8].valueAsText or "").strip()
            karttakuva_user = (parameters[9].valueAsText or "").strip()
            karttakuva_pass = (parameters[10].valueAsText or "").strip()
            raw_aino_token = (
                (parameters[12].valueAsText or "").strip()
                if len(parameters) > 12 else ""
            )
            aino_token = self._normalize_aino_token(raw_aino_token)
            if len(parameters) > 12 and aino_token != raw_aino_token:
                # Korjaa myös piilotetun kentän arvo, jotta execute-vaihe ja
                # DPAPI-tallennus käyttävät samaa palvelun hyväksymää tokenia.
                parameters[12].value = aino_token
            self._runtime_mml_api_key = mml_api_key
            self._runtime_karttapaikka_api_key = karttapaikka_api_key
            self._runtime_karttakuva_user = karttakuva_user
            self._runtime_karttakuva_pass = karttakuva_pass
            self._runtime_aino_token = aino_token

            parameters[0].enabled = True
            parameters[1].enabled = True
            parameters[2].enabled = True

            # Näytä projektin oletusgeodatabase myös silloin, kun ArcGIS Pro
            # kutsuu updateParameters-metodia ennen kuin p_workspace.value on
            # ehtinyt siirtyä käyttöliittymään.
            if not (parameters[6].valueAsText or "").strip():
                try:
                    project = arcpy.mp.ArcGISProject("CURRENT")
                    if project.defaultGeodatabase:
                        parameters[6].value = project.defaultGeodatabase
                except Exception:
                    pass

            uses_mml = "MML" in source_values
            uses_karttapaikka = "Karttapaikka" in source_values
            uses_karttakuva = "MML Karttakuva" in source_values
            uses_aino = "Aino" in source_values
            parameters[7].enabled = uses_mml
            parameters[8].enabled = uses_karttapaikka
            parameters[9].enabled = uses_karttakuva
            parameters[10].enabled = uses_karttakuva
            if len(parameters) > 12:
                parameters[12].enabled = uses_aino

            # Versioi avain, jotta vanhan 113 WFS -tason Aino-välimuisti ei
            # peitä uuden version 175 WMS -valintaa päivityksen jälkeen.
            source_key = "catalog-v2|{}|mml:{}|kartta:{}|kk:{}:{}|aino:{}".format(
                "|".join(source_values), self._secret_cache_key(mml_api_key),
                self._secret_cache_key(karttapaikka_api_key),
                self._secret_cache_key(karttakuva_user),
                self._secret_cache_key(karttakuva_pass),
                self._secret_cache_key(aino_token)
            )
            self._last_layer_source_key = source_key
            refresh_requested = bool(
                parameters[11].value if len(parameters) > 11 else False
            )
            if refresh_requested and not getattr(self, "_layer_refresh_consumed", False):
                # Kertaluonteinen ohitus: tyhjennä muisti- ja levyvälimuisti,
                # hae kerran palvelusta ja palauta valinta pois päältä.
                self._layer_refresh_consumed = True
                self._all_wfs_layers_cache.pop(source_key, None)
                self._layer_mapping_cache.pop(source_key, None)
                try:
                    parameters[11].value = False
                except Exception:
                    pass
            elif not refresh_requested:
                self._layer_refresh_consumed = False

            if source_key not in self._all_wfs_layers_cache:
                fetch_error = None
                if uses_aino:
                    # Älä näytä edellisen tokenin virhettä uuden tokenin tai
                    # onnistuneen levyvälimuistiosuman yhteydessä.
                    self._aino_catalog_errors = {}
                    self._aino_catalog_counts = {}
                try:
                    self._all_wfs_layers_cache[source_key] = self._fetch_layer_list(
                        source_values, cache_key=source_key,
                        allow_disk_cache=not getattr(self, "_layer_refresh_consumed", False),
                    )
                except Exception as fe:
                    fetch_error = fe
                    self._all_wfs_layers_cache[source_key] = []
                if fetch_error:
                    self._layer_mapping = {}
                    self._warn("[VAROITUS] Tasojen haku epäonnistui ({}): {}".format(
                        ", ".join(source_values), fetch_error))
                self._layer_mapping_cache[source_key] = dict(self._layer_mapping)
            else:
                self._layer_mapping = dict(self._layer_mapping_cache.get(source_key, {}))

            filtered_layers = self._all_wfs_layers_cache.get(source_key, [])
            if layer_search.strip():
                q = self._norm(layer_search)
                filtered_layers = [x for x in filtered_layers if q in self._norm(x)]

            # Tyhjää tulosta ei saa lisätä oikeana GPString-valintana. Aiempi
            # placeholder päätyi muuten execute-vaiheessa ladattavaksi
            # tasoksi ja aiheutti turhan määritystä ei löytynyt -virheen.
            valid_layers = {self._norm(value) for value in filtered_layers}
            valid_selection = [
                value for value in selected_before
                if not self._is_layer_placeholder(value)
                and self._norm(value) in valid_layers
            ]
            try:
                current_filter = list(parameters[2].filter.list or [])
            except Exception:
                current_filter = None
            filter_changed = current_filter != filtered_layers
            if filter_changed:
                parameters[2].filter.list = filtered_layers

            if selected_before:
                if valid_selection:
                    # ArcGIS Pro voi tyhjentää GPString-monivalinnan, kun sen
                    # ValueList päivitetään. Palauta edelleen kelvolliset arvot.
                    if filter_changed or valid_selection != selected_before:
                        self._set_multivalue_param(parameters[2], valid_selection)
                else:
                    self._clear_multivalue_param(parameters[2])

            parameters[4].enabled = False
            parameters[5].enabled = False
            parameters[4].filter.list = []

            if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"]:
                parameters[4].enabled = True
                try:
                    parameters[4].filter.list = self._get_extent_choices(extent_type)
                except Exception:
                    parameters[4].filter.list = [f"({extent_type.lower()}-listan haku epäonnistui)"]
            elif extent_type == "Oma aineisto (Polygon/Polyline)":
                parameters[5].enabled = True
                parameters[4].enabled = False
                parameters[4].value = None
            elif extent_type == "Koko Suomi":
                parameters[4].enabled = False
                parameters[4].value = None

        except Exception as e:
            self._warn(f"updateParameters epäonnistui: {e}")

    def updateMessages(self, parameters):
        extent_type = parameters[3].valueAsText
        extent_value_text = parameters[4].valueAsText
        custom_layer = parameters[5].valueAsText
        mml_api_key = parameters[7].valueAsText or ""
        karttapaikka_api_key = parameters[8].valueAsText or ""
        karttakuva_user = (parameters[9].valueAsText or "").strip()
        karttakuva_pass = (parameters[10].valueAsText or "").strip()
        aino_token = self._normalize_aino_token(
            (parameters[12].valueAsText or "").strip()
            if len(parameters) > 12 else ""
        )
        source_values = self._source_values_from_param(parameters[0], restore_empty=False)
        selected_layers = [
            value for value in self._parse_multivalue_param(parameters[2])
            if not self._is_layer_placeholder(value)
        ]

        vals = self._parse_multivalue(extent_value_text)

        if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"] and not vals:
            parameters[4].setErrorMessage("Valitse alue on pakollinen tälle aluerajauksen tasolle.")
        else:
            parameters[4].clearMessage()

        if extent_type == "Oma aineisto (Polygon/Polyline)":
            if not custom_layer or str(custom_layer).strip() == "":
                parameters[5].setErrorMessage("Valitse oma rajausaineisto.")
            else:
                parameters[5].clearMessage()
        else:
            parameters[5].clearMessage()

        layers_text = parameters[2].valueAsText
        if not layers_text or not selected_layers:
            parameters[2].setErrorMessage("Valitse vähintään yksi ladattava taso.")
        else:
            parameters[2].clearMessage()

        needs_mml_key = "MML" in source_values
        if selected_layers:
            for lbl in selected_layers:
                info = self._layer_mapping.get(lbl)
                if info and info.get("kind") in ("mml_raster", "mml_property_ogcapi"):
                    needs_mml_key = True
                    break
        if needs_mml_key and not mml_api_key.strip():
            parameters[7].setErrorMessage(
                "MML:n OGC API/vector tile -tasot vaativat API-avaimen."
            )
        else:
            parameters[7].clearMessage()

        needs_karttapaikka_key = False
        if selected_layers:
            for lbl in selected_layers:
                info = self._layer_mapping.get(lbl)
                if info and info.get("kind") == "mml_ogcapi":
                    needs_karttapaikka_key = True
                    break

        if needs_karttapaikka_key and not karttapaikka_api_key.strip():
            parameters[8].setErrorMessage(
                "Karttapaikan Maastotiedot-tasot vaativat API-avaimen."
            )
        else:
            parameters[8].clearMessage()

        uses_karttakuva = "MML Karttakuva" in source_values or any(
            (self._layer_mapping.get(lbl) or {}).get("kind") == "mml_karttakuva"
            for lbl in (selected_layers or [])
        )
        if uses_karttakuva and not karttakuva_user:
            parameters[9].setErrorMessage("MML Karttakuva vaatii käyttäjätunnuksen.")
        else:
            parameters[9].clearMessage()
        if uses_karttakuva and not karttakuva_pass:
            parameters[10].setErrorMessage("MML Karttakuva vaatii salasanan.")
        else:
            parameters[10].clearMessage()

        uses_aino = "Aino" in source_values or any(
            (self._layer_mapping.get(lbl) or {}).get("source") == "Aino"
            for lbl in (selected_layers or [])
        )
        if len(parameters) > 12:
            if uses_aino and not aino_token:
                parameters[12].setErrorMessage("Aino-rajapinta vaatii tokenin.")
            elif uses_aino and getattr(self, "_aino_catalog_errors", None):
                counts = getattr(self, "_aino_catalog_counts", {}) or {}
                details = "; ".join(
                    "{}: {}".format(service, error_text)
                    for service, error_text in sorted(
                        self._aino_catalog_errors.items()
                    )
                )
                message = "Aino-tasoluettelo: {}".format(details)
                if sum(int(value or 0) for value in counts.values()) <= 0:
                    parameters[12].setErrorMessage(message)
                else:
                    set_warning = getattr(parameters[12], "setWarningMessage", None)
                    if callable(set_warning):
                        set_warning(message)
            else:
                parameters[12].clearMessage()

        if extent_type in ["Koko Suomi", "Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"]:
            gpkg = self._find_admin_gpkg()
            if not gpkg:
                parameters[3].setErrorMessage("Resources-kansiosta puuttuu tiedosto hallinnolliset_aluejaot.gpkg.")
    def _lookup_layer_info(self, layer_ui_name):
        clean_name = (layer_ui_name or "").strip().strip("'").strip('"')
        info = self._layer_mapping.get(clean_name)
        if info:
            return info
        norm_name = self._norm(clean_name)
        for k, val in self._layer_mapping.items():
            if self._norm(k) == norm_name:
                return val
        parts = clean_name.split(" - ")
        if len(parts) >= 2:
            base_name = self._norm(parts[0])
            suffix = parts[-1].strip()
            for k, val in self._layer_mapping.items():
                k_parts = k.split(" - ")
                if len(k_parts) >= 2 and self._norm(k_parts[0]) == base_name and k_parts[-1].strip() == suffix:
                    return val
        return None

    # ---------------------------
    # EXECUTION
    # ---------------------------
    def execute(self, parameters, messages):
        self._tool_metrics = PhaseMetrics()
        self._run_had_layer_failures = False
        self._tool_run_start = time.perf_counter()
        original_overwrite = arcpy.env.overwriteOutput
        try:
            original_scratch_workspace = arcpy.env.scratchWorkspace
        except Exception:
            original_scratch_workspace = None
        success = False
        try:
            scratch_elapsed = self._create_run_scratch()
            self._runtime_project = None
            self._runtime_map = None
            self._runtime_map_loaded = False
            self._tool_metrics.set("työkalun varsinainen käynnistys", scratch_elapsed)
            arcpy.env.overwriteOutput = True
            try:
                arcpy.env.scratchWorkspace = self._run_scratch_gdb
            except Exception:
                pass
            if self._verbose_diagnostics:
                self._msg("[INFO] Ajokohtainen paikallinen scratch-GDB: {}".format(
                    self._run_scratch_gdb
                ))
            result = self._execute_impl(parameters, messages)
            success = True
            return result
        finally:
            try:
                arcpy.env.scratchWorkspace = original_scratch_workspace
            except Exception:
                pass
            cleanup_elapsed, cleanup_error = self._cleanup_run_scratch(
                preserve=(not success or self._run_had_layer_failures)
            )
            self._tool_metrics.set("väliaineistojen siivous", cleanup_elapsed)
            if cleanup_error:
                self._warn(
                    "[VAROITUS] Scratch-aineiston siivous epäonnistui eikä peitä ajon varsinaista tulosta: {}".format(
                        cleanup_error
                    )
                )
            arcpy.env.overwriteOutput = original_overwrite
            total_s = time.perf_counter() - self._tool_run_start
            tool_phases = [
                "työkalun varsinainen käynnistys",
                "parametrien lukeminen ja validointi",
                "kohdetyötilan validointi",
                "aluerajauksen valmistelu",
                "tasomääritysten muodostaminen",
                "kaikkien tasojen käsittely",
                "lopputulosten kopiointi",
                "tulosten kartalle lisääminen",
                "väliaineistojen siivous",
            ]
            for phase in tool_phases:
                if phase not in self._tool_metrics.seconds:
                    self._tool_metrics.skip(phase, "ohitettu")
            self._log_phase_summary(
                "[INFO] Työkalun vaiheajat:", self._tool_metrics, tool_phases, total_s
            )

    def _execute_impl(self, parameters, messages):
        self._msg("=== Työkalu käynnistyy ===")

        parameter_start = time.perf_counter()
        source_names = self._source_values_from_param(parameters[0], restore_empty=False)
        layers = [
            value for value in self._parse_multivalue_param(parameters[2])
            if not self._is_layer_placeholder(value)
        ]
        extent_type = parameters[3].valueAsText
        extent_value_text = parameters[4].valueAsText
        custom_layer = parameters[5].valueAsText
        workspace = parameters[6].valueAsText
        mml_api_key = parameters[7].valueAsText or ""
        karttapaikka_api_key = parameters[8].valueAsText or ""
        karttakuva_user = (parameters[9].valueAsText or "").strip()
        karttakuva_pass = (parameters[10].valueAsText or "").strip()
        aino_token = self._normalize_aino_token(
            (parameters[12].valueAsText or "").strip()
            if len(parameters) > 12 else ""
        )
        self._runtime_mml_api_key = mml_api_key.strip()
        self._runtime_karttapaikka_api_key = karttapaikka_api_key.strip()
        self._runtime_karttakuva_user = karttakuva_user
        self._runtime_karttakuva_pass = karttakuva_pass
        self._runtime_aino_token = aino_token
        sel_vals = self._parse_multivalue(extent_value_text)

        if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"] and not sel_vals:
            self._error(f"[VIRHE] Aluerajauksen taso on '{extent_type}', mutta 'Valitse alue' on tyhjä.")
            raise arcpy.ExecuteError

        if extent_type == "Oma aineisto (Polygon/Polyline)" and (not custom_layer or str(custom_layer).strip() == ""):
            self._error("[VIRHE] Valitsit 'Oma aineisto', mutta rajausaineisto puuttuu.")
            raise arcpy.ExecuteError

        if "Aino" in source_names and not aino_token:
            self._error("[VIRHE] Aino-rajapinta vaatii tokenin.")
            raise arcpy.ExecuteError

        self._tool_metrics.set(
            "parametrien lukeminen ja validointi", time.perf_counter() - parameter_start
        )

        if not workspace or workspace.strip() == "":
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                workspace = aprx.defaultGeodatabase
            except Exception:
                workspace = self._scratch_gdb()

        scratch_gdb = self._scratch_gdb()
        workspace_validation_start = time.perf_counter()
        self._init_workspace_cache(workspace)
        self._tool_metrics.set(
            "kohdetyötilan validointi",
            time.perf_counter() - workspace_validation_start,
        )

        if extent_type == "Koko Suomi":
            area_label = "Koko_Suomi"
        else:
            area_label = "+".join(sel_vals)

        custom_boundary_tmp = None
        admin_boundary_pending = None
        admin_boundary_is_layer = False
        admin_boundary_name = None
        admin_boundary_existing = False
        if extent_type != "Oma aineisto (Polygon/Polyline)":
            admin_boundary_name = self._validated_name(area_label, workspace)
            try:
                admin_boundary_existing = bool(arcpy.Exists(
                    self._dataset_output_path(workspace, admin_boundary_name)
                ))
            except Exception:
                admin_boundary_existing = False

        self._msg(f"[INFO] Noudetaan aluerajaus ({extent_type}: {area_label})...")
        boundary_start = time.perf_counter()
        boundary_metrics = PhaseMetrics()

        if extent_type == "Oma aineisto (Polygon/Polyline)":
            boundary_fc = self._prepare_custom_boundary(custom_layer, boundary_metrics)
            custom_boundary_tmp = boundary_fc
        else:
            boundary_fc = self._process_administrative_boundary(
                extent_type, sel_vals, scratch_gdb, boundary_metrics
            )
            admin_boundary_pending = boundary_fc
            admin_boundary_is_layer = self._is_in_memory_layer(boundary_fc)

        if not layers:
            self._error("[VIRHE] Yhtään tasoa ei ole valittu ladattavaksi.")
            raise arcpy.ExecuteError

        extent_start = time.perf_counter()
        ext = self._boundary_extent_from_features(boundary_fc)
        boundary_metrics.add(
            "valittujen kohteiden tai määrittelykyselyn käsittely",
            time.perf_counter() - extent_start,
        )
        buffer_m = 1000
        bbox_str = f"{ext.XMin - buffer_m},{ext.YMin - buffer_m},{ext.XMax + buffer_m},{ext.YMax + buffer_m}"

        max_features = 5000  # Vähennetty 10000:sta tehokkaampia HTTP-pyyntöjä varten
        scratch_folder = self._scratch_folder()
        output_formats = ["application/json", "application/geo+json", "application/json;subtype=geojson", "json"]
        wkt_start = time.perf_counter()
        boundary_wkt = self._boundary_wkt_3067(boundary_fc, for_cql=True)
        boundary_metrics.set("WKT:n muodostaminen", time.perf_counter() - wkt_start)
        cql_wkts, cql_geometry_info = self._prepare_cql_wkts(boundary_fc, boundary_wkt)
        boundary_metrics.set(
            "CQL-geometrian yksinkertaistaminen", cql_geometry_info.get("elapsed_s", 0.0)
        )
        boundary_metrics.skip("geometrioiden yhdistäminen", "sisältyy WKT:n muodostamiseen")
        original_info = cql_geometry_info.get("original", {})
        simplified_info = cql_geometry_info.get("simplified", {})
        self._msg(
            "[INFO] CQL-geometria: alkuperäinen {} osaa / {} pistettä, "
            "yksinkertaistettu {} osaa / {} pistettä, toleranssi {} {}, "
            "WKT-osia {} (URL-koodattu pituus {} merkkiä).".format(
                original_info.get("parts", 0), original_info.get("points", 0),
                simplified_info.get("parts", 0), simplified_info.get("points", 0),
                cql_geometry_info.get("tolerance", 50.0), cql_geometry_info.get("unit", "tuntematon"),
                len(cql_wkts), cql_geometry_info.get("encoded_chars", 0),
            )
        )
        if len(cql_wkts) > 1:
            self._msg(
                "[INFO] Pitkä CQL-geometria voidaan tarvittaessa hakea {} pienempänä "
                "INTERSECTS-pyyntönä ({}x{} ruudukko); tarkka Clip-rajaus säilyy erillisenä.".format(
                    len(cql_wkts), cql_geometry_info.get("grid_size", "?"),
                    cql_geometry_info.get("grid_size", "?"),
                )
            )

        boundary_total = time.perf_counter() - boundary_start
        boundary_phase_names = [
            "lähtöaineiston kuvaustietojen lukeminen",
            "valittujen kohteiden tai määrittelykyselyn käsittely",
            "rajauksen kopiointi",
            "rajauksen projektointi",
            "geometrioiden yhdistäminen",
            "geometrian tarkistus tai korjaus",
            "CQL-geometrian yksinkertaistaminen",
            "WKT:n muodostaminen",
        ]
        for phase in boundary_phase_names:
            if phase not in boundary_metrics.seconds:
                boundary_metrics.skip(phase, "ei käytetty")
        self._log_phase_summary(
            "[INFO] Aluerajauksen vaiheajat:", boundary_metrics,
            boundary_phase_names, boundary_total,
        )
        self._tool_metrics.set("aluerajauksen valmistelu", boundary_total)

        staged_outputs = []
        layer_failures = []

        def _record_layer_failure(label, error):
            self._run_had_layer_failures = True
            reason = self._redact_secrets(str(error or "tuntematon virhe")).strip()
            if len(reason) > 600:
                reason = reason[:600] + "..."
            layer_failures.append((label, reason))
            self._warn(
                "[VAROITUS] Taso '{}' epäonnistui: {} Jatketaan seuraavaan tasoon.".format(
                    label, reason
                )
            )

        # Always rebuild mapping from cache so it matches the current source selection
        definitions_start = time.perf_counter()
        all_available_sources = self.wfs_registry.get_sources_list()
        needed_sources = set(source_names or [])
        for layer_str in layers:
            l_clean = layer_str.strip().strip("'").strip('"')
            for src in all_available_sources:
                if l_clean.endswith(" - " + src) or (" - " + src) in l_clean:
                    needed_sources.add(src)
        self._fetch_layer_list(
            list(needed_sources),
            cache_key=getattr(self, "_last_layer_source_key", None),
        )
        self._tool_metrics.set(
            "tasomääritysten muodostaminen", time.perf_counter() - definitions_start
        )

        needs_kunta_chunks = False
        if extent_type in ["Maakunta", "Elinvoimakeskus", "Hyvinvointialue", "Koko Suomi"]:
            for layer in layers:
                layer_ui_name = layer.strip().strip("'").strip('"')
                layer_info = self._lookup_layer_info(layer_ui_name) or {}
                if (
                    layer_info.get("kind") == "wfs"
                    and layer_info.get("source") in self.heavy_chunk_sources
                    and self._is_heavy_layer(layer_info.get("id"))
                ):
                    needs_kunta_chunks = True
                    break

        # Kuntajoukko raskaille tasoille
        kunnat_sel_fc = None
        kunnat_all_fc = None
        if needs_kunta_chunks:
            self._msg("[INFO] Valittu taso vaatii kuntakohtaisen pilkkomisen. Haetaan kuntarajaukset...")
            kunnat_start = time.perf_counter()
            kunnat_all_fc = self._fetch_all_kunnat_fc()
            if kunnat_all_fc and arcpy.Exists(kunnat_all_fc):
                if extent_type == "Koko Suomi":
                    kunnat_sel_fc = kunnat_all_fc
                else:
                    kunnat_sel_fc = self._select_kunnat_center_in(kunnat_all_fc, boundary_fc)
            self._msg(f"[INFO] Kuntarajaukset valmiina ({time.perf_counter() - kunnat_start:.1f} s).")

        if any(
            (self._lookup_layer_info(lbl.strip().strip("'").strip('"')) or {}).get("kind")
            == "mml_property_ogcapi"
            for lbl in layers
        ) and not mml_api_key.strip():
            self._error("[VIRHE] MML:n kiinteistöjen OGC API -tasot vaativat API-avaimen.")
            raise arcpy.ExecuteError

        if any(
            (self._lookup_layer_info(lbl.strip().strip("'").strip('"')) or {}).get("kind")
            == "mml_ogcapi"
            for lbl in layers
        ) and not karttapaikka_api_key.strip():
            self._error("[VIRHE] Karttapaikan Maastotiedot-tasot vaativat API-avaimen.")
            raise arcpy.ExecuteError

        all_layers_start = time.perf_counter()
        total_layers = len(layers)
        ogc_bbox_wgs84 = None
        for layer_index, layer in enumerate(layers, 1):
            layer_ui_name = layer.strip().strip("'").strip('"')
            self._msg("[INFO] Käsitellään taso [{}/{}]: {}".format(
                layer_index, total_layers, layer_ui_name
            ))
            layer_info = self._lookup_layer_info(layer_ui_name)
            if not layer_info:
                _record_layer_failure(layer_ui_name, "tason määritystä ei löytynyt")
                continue

            layer_clean = layer_info.get("id")
            source_name = layer_info.get("source")
            layer_kind = layer_info.get("kind")

            base_wfs = (
                layer_info.get("endpoint")
                or self._choose_wfs_endpoint(layer_clean, source_name)
            )
            base_wfs = self._source_endpoint_with_credentials(
                source_name, base_wfs
            )
            auth_headers = self._build_source_auth_headers(
                source_name, endpoint=base_wfs, layer_kind=layer_kind
            )
            if layer_kind == "wfs":
                self._discover_wfs_schema(base_wfs, layer_clean, auth_headers)
            is_heavy = (layer_kind == "wfs" and source_name in self.heavy_chunk_sources and self._is_heavy_layer(layer_clean))
            temp_feature_classes = []
            used_kunta_chunks = False
            used_overpass_grid = 1
            layer_start = time.perf_counter()
            layer_metrics = PhaseMetrics()
            layer_http_s = 0.0
            layer_gp_json_s = 0.0
            layer_gp_clip_s = 0.0
            layer_gp_copy_s = 0.0
            layer_gp_merge_s = 0.0
            layer_gp_stage_s = 0.0
            skip_clip = False
            cql_split_effective = False
            stage_name = "stage_{}_{}".format(layer_index, uuid.uuid4().hex[:8])
            staged_fc = os.path.join(scratch_gdb, stage_name)
            output_name_start = time.perf_counter()
            proposed_output_name = self._unique_output_name(
                layer_ui_name.rsplit(" - ", 1)[0], workspace
            )
            layer_metrics.add(
                "tulosnimen validointi", time.perf_counter() - output_name_start
            )
            proposed_output_path = self._dataset_output_path(workspace, proposed_output_name)
            # Kaikki ominaisuudet, jotka päätyvät yhteiseen staged_outputs-
            # yhteenvetoon, tarvitsevat hakutavan. OSM ei kulje WFS/OGC-haaran
            # kautta, joten alusta arvo ennen lähdekohtaista käsittelyä.
            requested_mode = "paikallinen aineistohaku"
            if layer_kind == "wfs":
                if is_heavy:
                    requested_mode = "kuntakohtainen BBOX + paikallinen Clip"
                elif boundary_wkt and self._wfs_supports_cql(source_name):
                    requested_mode = "CQL INTERSECTS (GET, POST, pilkottu CQL; BBOX vain varalla)"
                else:
                    requested_mode = "BBOX + paikallinen Clip"
                self._msg("  [TASO] Näyttönimi: {}".format(layer_ui_name))
                self._msg("  [TASO] Lähde: {}".format(source_name or "(tuntematon)"))
                self._msg("  [TASO] WFS-palvelu: {}".format(self._sanitize_url(base_wfs)))
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))
                self._msg("  [TASO] Sivukoko: {}".format(max_features))
                if self._verbose_diagnostics:
                    self._msg("  [TASO] typeName: {}".format(layer_clean))
                    self._msg("  [TASO] Geometriakenttä: {}".format(
                        self._get_wfs_geometry_field(layer_clean)
                    ))
                    self._msg("  [TASO] Paikallinen välitulos: {}".format(staged_fc))
                    self._msg("  [TASO] Lopputulos: {}".format(proposed_output_path))
            elif layer_kind in ("mml_ogcapi", "mml_property_ogcapi"):
                if layer_kind == "mml_property_ogcapi":
                    requested_mode = "MML:n kiinteistötietojen OGC API Features + paikallinen Clip"
                else:
                    requested_mode = "Maastotiedot OGC API Features + paikallinen Clip"
                self._msg("  [TASO] Näyttönimi: {}".format(layer_ui_name))
                self._msg("  [TASO] Lähde: {}".format(source_name or "(tuntematon)"))
                self._msg("  [TASO] OGC API -palvelu: {}".format(
                    self._sanitize_url(base_wfs)
                ))
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))
                self._msg("  [TASO] Sivukoko: {}".format(max_features))
                if self._verbose_diagnostics:
                    self._msg("  [TASO] collection: {}".format(layer_clean))
                    self._msg("  [TASO] Geometriakenttä: geometry")
                    self._msg("  [TASO] Paikallinen välitulos: {}".format(staged_fc))
                    self._msg("  [TASO] Lopputulos: {}".format(proposed_output_path))

            elif layer_kind == "oskari_wfs":
                requested_mode = "Traficomin Oskari GetWFSFeatures (EPSG:3067) + paikallinen Clip"
                self._msg("  [TASO] Näyttönimi: {}".format(layer_ui_name))
                self._msg("  [TASO] Lähde: Traficom Oskari")
                self._msg("  [TASO] Oskari API: {}".format(self._sanitize_url(base_wfs)))
                self._msg("  [TASO] Oskari tasotunnus: {}".format(layer_clean))
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))

            elif layer_kind == "aino_wms":
                requested_mode = "Aino WMS 1.3.0 live-karttataso"
                self._msg("  [TASO] Näyttönimi: {}".format(layer_ui_name))
                self._msg("  [TASO] Lähde: Aino")
                self._msg("  [TASO] WMS-palvelu: {}".format(
                    self._sanitize_url(layer_info.get("endpoint") or base_wfs)
                ))
                self._msg("  [TASO] WMS-alitaso: {}".format(layer_clean))
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))
            elif layer_kind in ("oskari_wms", "oskari_wmts"):
                if layer_kind == "oskari_wms":
                    requested_mode = "Oskari GetLayerTile WMS + georeferoitu rasterikuva"
                    service_label = "WMS"
                else:
                    requested_mode = "Traficom WMTS GetTile + paikallinen GeoTIFF-mosaiikki"
                    service_label = "WMTS"
                self._msg("  [TASO] Näyttönimi: {}".format(layer_ui_name))
                self._msg("  [TASO] Lähde: Traficom Oskari ({})".format(
                    layer_info.get("organization") or "Oskari-karttapalvelu"
                ))
                self._msg("  [TASO] {}-tason nimi: {}".format(
                    service_label, layer_info.get("layer_name") or layer_clean
                ))
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))

            if layer_kind == "aino_wms":
                try:
                    self._add_aino_wms_layer(
                        layer_info.get("endpoint") or base_wfs,
                        layer_clean,
                        layer_info.get("wms_title") or layer_info.get("title"),
                        aino_token,
                        is_background=bool(layer_info.get("is_background")),
                    )
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, self._redact_secrets(ex))
                    continue
                self._msg("  [INFO] Taso valmis.")
                continue
            elif layer_kind == "osm":
                if layer_clean == GeofabrikPOIAdapter.LAYER_ID:
                    requested_mode = (
                        "OpenStreetMap Overpass API + Geofabrik POI-luokitus + "
                        "aluekohteiden keskipisteet + paikallinen Clip"
                    )
                else:
                    requested_mode = "OpenStreetMap Overpass API + paikallinen Clip"
                self._msg("  [TASO] Hakutapa: {}".format(requested_mode))
                self._msg("  [INFO] Haetaan OpenStreetMap-aineistoa: {}".format(layer_ui_name))
                try:
                    chunks, total_found, used_grid = self._fetch_osm_feature_chunks(layer_clean, boundary_fc)
                    used_overpass_grid = used_grid
                    temp_feature_classes.extend(chunks)
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
            elif layer_kind == "oskari_wms":
                download_start = time.perf_counter()
                try:
                    out_tif = self._download_oskari_wms_geotiff(
                        layer_id=layer_info.get("catalog_id") or layer_clean,
                        layer_name=layer_info.get("layer_name"),
                        layer_title=layer_info.get("title") or layer_ui_name,
                        style=layer_info.get("style"),
                        boundary_fc=boundary_fc,
                        workspace=self._scratch_folder(),
                        endpoint=layer_info.get("endpoint") or base_wfs,
                    )
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
                staged_outputs.append({
                    "path": out_tif,
                    "output_name": os.path.splitext(os.path.basename(out_tif))[0],
                    "output_type": "raster",
                    "label": layer_ui_name,
                    "layer_start": layer_start,
                    "download_s": time.perf_counter() - download_start,
                })
                continue
            elif layer_kind == "oskari_wmts":
                download_start = time.perf_counter()
                try:
                    out_tif = self._download_oskari_wmts_geotiff(
                        layer_name=layer_info.get("layer_name"),
                        layer_title=layer_info.get("title") or layer_ui_name,
                        boundary_fc=boundary_fc,
                        workspace=self._scratch_folder(),
                    )
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
                staged_outputs.append({
                    "path": out_tif,
                    "output_name": os.path.splitext(os.path.basename(out_tif))[0],
                    "output_type": "raster",
                    "label": layer_ui_name,
                    "layer_start": layer_start,
                    "download_s": time.perf_counter() - download_start,
                })
                continue
            elif layer_kind == "mml_raster":
                if not mml_api_key.strip():
                    _record_layer_failure(layer_ui_name, "MML-rasteritaso vaatii API-avaimen")
                    continue
                download_start = time.perf_counter()
                try:
                    out_tif = self._download_mml_wmts_geotiff(
                        layer_clean, boundary_fc, self._scratch_folder(), mml_api_key.strip()
                    )
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
                staged_outputs.append({
                    "path": out_tif,
                    "output_name": os.path.splitext(os.path.basename(out_tif))[0],
                    "output_type": "raster",
                    "label": layer_ui_name,
                    "layer_start": layer_start,
                    "download_s": time.perf_counter() - download_start,
                })
                continue
            elif layer_kind == "kapsi_wms":
                download_start = time.perf_counter()
                try:
                    out_jpg = self._download_kapsi_wms_jpeg(
                        layer_clean, boundary_fc, self._scratch_folder()
                    )
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
                staged_outputs.append({
                    "path": out_jpg,
                    "output_name": os.path.splitext(os.path.basename(out_jpg))[0],
                    "output_type": "raster_bundle",
                    "label": layer_ui_name,
                    "layer_start": layer_start,
                    "download_s": time.perf_counter() - download_start,
                })
                continue
            elif layer_kind == "mml_karttakuva":
                if not karttakuva_user or not karttakuva_pass:
                    _record_layer_failure(
                        layer_ui_name, "MML Karttakuva vaatii käyttäjätunnuksen ja salasanan"
                    )
                    continue
                self._msg("  [INFO] Lisätään MML Karttakuva -WMTS-taso: {}".format(layer_ui_name))
                try:
                    self._add_karttakuva_wmts_layer(layer_clean, karttakuva_user, karttakuva_pass)
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
                self._msg("  [INFO] Taso valmis.")
                continue
            elif layer_kind in ("mml_ogcapi", "mml_property_ogcapi"):
                is_property_ogc = layer_kind == "mml_property_ogcapi"
                api_key = mml_api_key.strip() if is_property_ogc else karttapaikka_api_key.strip()
                if is_property_ogc:
                    service_label = "MML:n kiinteistötiedot"
                    missing_key_message = "MML:n kiinteistötaso vaatii API-avaimen"
                else:
                    service_label = "Karttapaikan Maastotiedot"
                    missing_key_message = "Karttapaikan Maastotiedot-taso vaatii API-avaimen"
                self._msg(
                    "  [INFO] Haetaan {} -aineistoa (OGC API Features)...".format(
                        service_label
                    )
                )
                if not api_key:
                    _record_layer_failure(
                        layer_ui_name,
                        missing_key_message,
                    )
                    continue
                try:
                    if ogc_bbox_wgs84 is None:
                        bbox_projection_start = time.perf_counter()
                        ogc_bbox_wgs84 = self._boundary_bbox_wgs84(boundary_fc)
                        layer_metrics.add(
                            "projektointi",
                            time.perf_counter() - bbox_projection_start,
                        )
                    if not ogc_bbox_wgs84:
                        raise Exception("Rajauksesta ei voitu muodostaa OGC API -bboxia.")
                    chunks, total_found, ogc_stats = self._fetch_ogcapi_feature_chunks(
                        endpoint=base_wfs,
                        collection_id=layer_clean,
                        bbox_wgs84=ogc_bbox_wgs84,
                        max_features=max_features,
                        extra_headers=auth_headers,
                        service_label=service_label,
                    )
                    temp_feature_classes.extend(chunks)
                    if ogc_stats.get("truncated"):
                        raise Exception(
                            "OGC API -sivutuksen maksimipyyntömäärä täyttyi, joten taso "
                            "jäisi vaillinaiseksi. Rajaa alue pienemmäksi."
                        )
                    layer_http_s += ogc_stats.get("network_s", 0.0)
                    layer_gp_json_s += ogc_stats.get("json_to_features_s", 0.0)
                    stat_to_phase = {
                        "request_build_s": "requestin muodostaminen",
                        "network_s": "verkkopyyntö",
                        "response_read_s": "vastauksen lukeminen",
                        "decode_s": "vastauksen dekoodaus",
                        "json_parse_s": "JSON-jäsennys",
                        "json_write_s": "väliaikaisen JSON-tiedoston kirjoittaminen",
                        "json_to_features_s": "JSONToFeatures",
                        "projection_s": "projektointi",
                        "json_temp_delete_s": "väliaikaisen JSON-tiedoston poistaminen",
                    }
                    for stat_name, phase_name in stat_to_phase.items():
                        value = ogc_stats.get(stat_name)
                        if isinstance(value, (int, float)) and value > 0:
                            layer_metrics.add(phase_name, value)
                    self._msg(
                        "  [INFO] OGC-yhteenveto: {} sivua ladattu ({} kohdetta).".format(
                            ogc_stats.get("pages", 0), total_found
                        )
                    )
                except Exception as ex:
                    for temp_fc in temp_feature_classes:
                        self._safe_delete(temp_fc)
                    temp_feature_classes = []
                    _record_layer_failure(layer_ui_name, ex)
                    continue
            elif layer_kind == "oskari_wfs":
                try:
                    bbox_projection_start = time.perf_counter()
                    extent_3067 = self._boundary_extent_3067(boundary_fc)
                    layer_metrics.add(
                        "projektointi",
                        time.perf_counter() - bbox_projection_start,
                    )
                    bbox_3067 = "{},{},{},{}".format(
                        extent_3067.XMin, extent_3067.YMin,
                        extent_3067.XMax, extent_3067.YMax,
                    )
                    chunks, total_found, oskari_stats = self._fetch_oskari_feature_chunks(
                        endpoint=base_wfs,
                        layer_id=layer_clean,
                        bbox_3067=bbox_3067,
                    )
                    temp_feature_classes.extend(chunks)
                    layer_http_s += oskari_stats.get("network_s", 0.0)
                    layer_gp_json_s += oskari_stats.get("json_to_features_s", 0.0)
                    stat_to_phase = {
                        "request_build_s": "requestin muodostaminen",
                        "network_s": "verkkopyyntö",
                        "response_read_s": "vastauksen lukeminen",
                        "decode_s": "vastauksen dekoodaus",
                        "json_parse_s": "JSON-jäsennys",
                        "json_write_s": "väliaikaisen JSON-tiedoston kirjoittaminen",
                        "json_to_features_s": "JSONToFeatures",
                        "projection_s": "projektointi",
                        "json_temp_delete_s": "väliaikaisen JSON-tiedoston poistaminen",
                    }
                    for stat_name, phase_name in stat_to_phase.items():
                        value = oskari_stats.get(stat_name)
                        if isinstance(value, (int, float)) and value > 0:
                            layer_metrics.add(phase_name, value)
                    self._msg(
                        "  [INFO] Oskari-yhteenveto: {} kohdetta GeoJSONissa; "
                        "EPSG:3067, paikallinen Clip.".format(total_found)
                    )
                except Exception as ex:
                    for temp_fc in temp_feature_classes:
                        self._safe_delete(temp_fc)
                    temp_feature_classes = []
                    _record_layer_failure(layer_ui_name, ex)
                    continue
            elif is_heavy and extent_type in ["Maakunta", "Elinvoimakeskus", "Hyvinvointialue", "Koko Suomi"] and kunnat_sel_fc and arcpy.Exists(kunnat_sel_fc):
                used_kunta_chunks = True
                self._msg(f"[INFO] Raskas taso havaittu -> haetaan kunta kerrallaan ({layer_clean})")

                name_field = self._get_kunta_name_field(kunnat_sel_fc)
                try:
                    total_kunnat = int(arcpy.management.GetCount(kunnat_sel_fc)[0])
                except Exception:
                    total_kunnat = 0

                i_kunta = 0
                for oid, kunta_name, geom in self._iter_kunnat(kunnat_sel_fc, name_field):
                    i_kunta += 1
                    one_kunta_fc = None
                    try:
                        one_kunta_fc = self._copy_single_feature(kunnat_sel_fc, oid)
                        kext = arcpy.Describe(one_kunta_fc).extent
                        kbbox = f"{kext.XMin},{kext.YMin},{kext.XMax},{kext.YMax}"

                        start_index = 0
                        has_more_data = True
                        request_count_kunta = 0
                        max_requests_kunta = 50
                        prev_hash_kunta = None
                        repeated_guard_kunta = 0
                        while has_more_data and request_count_kunta < max_requests_kunta:
                            page_start = time.perf_counter()
                            page_timing = PhaseMetrics()
                            request_count_kunta += 1
                            if self._verbose_diagnostics:
                                if total_kunnat > 0:
                                    self._msg(f"  [INFO] Haetaan kohteet {kunta_name} [{i_kunta}/{total_kunnat}] {start_index} - {start_index + max_features - 1}...")
                                else:
                                    self._msg(f"  [INFO] Haetaan kohteet {kunta_name} {start_index} - {start_index + max_features - 1}...")

                            json_data = None
                            raw_text = ""
                            status = None
                            ctype = ""

                            for fmt in output_formats:
                                request_build_start = time.perf_counter()
                                request_url = self._build_wfs_getfeature_url(
                                    base_wfs=base_wfs,
                                    layer_clean=layer_clean,
                                    max_features=max_features,
                                    start_index=start_index,
                                    output_format=fmt,
                                    bbox_str=kbbox,
                                    geometry_only=False,
                                )
                                page_timing.add(
                                    "requestin muodostaminen",
                                    time.perf_counter() - request_build_start,
                                )
                                json_data, raw_text, status, ctype = self._fetch_json(
                                    request_url, timeout=120, quiet=True,
                                    extra_headers=auth_headers, timings=page_timing,
                                )
                                if json_data is not None:
                                    break
                            layer_http_s += page_timing.get("verkkopyyntö", 0.0) or 0.0

                            if json_data is None:
                                # Siivoa temp-tiedostot ennen virhettä
                                for t in temp_feature_classes:
                                    self._safe_delete(t)
                                dump_path = os.path.join(scratch_folder, f"wfs_error_{uuid.uuid4().hex}.txt")
                                try:
                                    with open(dump_path, "w", encoding="utf-8") as f:
                                        f.write(self._redact_secrets(raw_text or ""))
                                except Exception:
                                    pass
                                self._error(f"[VIRHE] WFS-pyyntö epäonnistui (HTTP {status}, Content-Type: {ctype}). Virhevastaus: {dump_path}")
                                raise arcpy.ExecuteError

                            features = json_data.get("features", []) if isinstance(json_data, dict) else []
                            if features and len(features) > 0:
                                text_hash_kunta = hashlib.md5(raw_text[:8000].encode('utf-8', errors='replace')).hexdigest() if raw_text else None
                                if prev_hash_kunta == text_hash_kunta:
                                    repeated_guard_kunta += 1
                                else:
                                    repeated_guard_kunta = 0
                                prev_hash_kunta = text_hash_kunta
                                if repeated_guard_kunta >= 2:
                                    self._warn(f"[VAROITUS] WFS sivutus toistaa samaa sisältöä kunnassa '{kunta_name}'. Keskeytetään.")
                                    break

                                temp_json_path = os.path.join(scratch_folder, f"temp_{uuid.uuid4().hex}.json")
                                json_write_start = time.perf_counter()
                                with open(temp_json_path, "w", encoding="utf-8") as f:
                                    f.write(raw_text)
                                page_timing.add(
                                    "väliaikaisen JSON-tiedoston kirjoittaminen",
                                    time.perf_counter() - json_write_start,
                                )

                                temp_fc = os.path.join(self._scratch_gdb(), f"temp_fc_{uuid.uuid4().hex}")
                                json_start = time.perf_counter()
                                arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc)
                                json_elapsed = time.perf_counter() - json_start
                                layer_gp_json_s += json_elapsed
                                page_timing.add("JSONToFeatures", json_elapsed)
                                temp_feature_classes.append(temp_fc)

                                try:
                                    os.remove(temp_json_path)
                                except Exception:
                                    pass

                                for phase_name, phase_value in page_timing.seconds.items():
                                    if isinstance(phase_value, (int, float)):
                                        layer_metrics.add(phase_name, phase_value)

                                got = len(features)
                                if self._verbose_diagnostics:
                                    self._msg(
                                        "    [EDISTYMINEN] Kunta {} / sivu {}: +{} kohdetta, "
                                        "request {:.3f} s, verkko {:.3f} s, luku {:.3f} s, "
                                        "JSON-jäsennys {:.3f} s, JSON-kirjoitus {:.3f} s, "
                                        "JSONToFeatures {:.3f} s, sivu yhteensä {:.3f} s".format(
                                            kunta_name, request_count_kunta, got,
                                            page_timing.get("requestin muodostaminen", 0.0) or 0.0,
                                            page_timing.get("verkkopyyntö", 0.0) or 0.0,
                                            page_timing.get("vastauksen lukeminen", 0.0) or 0.0,
                                            page_timing.get("JSON-jäsennys", 0.0) or 0.0,
                                            page_timing.get("väliaikaisen JSON-tiedoston kirjoittaminen", 0.0) or 0.0,
                                            page_timing.get("JSONToFeatures", 0.0) or 0.0,
                                            time.perf_counter() - page_start,
                                        )
                                    )

                                start_index += len(features)

                                if len(features) < max_features:
                                    has_more_data = False
                            else:
                                has_more_data = False

                        if request_count_kunta >= max_requests_kunta:
                            self._warn(f"[VAROITUS] Kunnan '{kunta_name}' maksimipyyntömäärä ({max_requests_kunta}) saavutettu.")

                    finally:
                        try:
                            if one_kunta_fc and arcpy.Exists(one_kunta_fc):
                                arcpy.management.Delete(one_kunta_fc)
                        except Exception:
                            pass

            else:
                if boundary_wkt and self._wfs_supports_cql(source_name):
                    self._msg("  [INFO] Haetaan kohteet (CQL INTERSECTS / BBOX fallback)...")
                else:
                    self._msg("  [INFO] Haetaan kohteet perus-BBOXilla...")
                try:
                    cql_state = {"effective": False, "split": False}
                    truncation_state = {"hit": False}

                    def _record_wfs_stats(stats):
                        nonlocal layer_http_s, layer_gp_json_s
                        if stats.get("truncated"):
                            truncation_state["hit"] = True
                        layer_http_s += stats.get("network_s", stats.get("http_s", 0.0))
                        layer_gp_json_s += stats.get("json_to_features_s", stats.get("gp_json_s", 0.0))
                        stat_to_phase = {
                            "request_build_s": "requestin muodostaminen",
                            "network_s": "verkkopyyntö",
                            "response_read_s": "vastauksen lukeminen",
                            "decode_s": "vastauksen dekoodaus",
                            "json_parse_s": "JSON-jäsennys",
                            "json_write_s": "väliaikaisen JSON-tiedoston kirjoittaminen",
                            "json_to_features_s": "JSONToFeatures",
                            "json_temp_delete_s": "väliaikaisen JSON-tiedoston poistaminen",
                        }
                        for stat_name, phase_name in stat_to_phase.items():
                            value = stats.get(stat_name)
                            if isinstance(value, (int, float)) and value > 0:
                                layer_metrics.add(phase_name, value)

                    def _fetch_bbox_once(tile_bbox, batch_size):
                        tile_wkt = boundary_wkt if tile_bbox == bbox_str else None
                        common_args = dict(
                            base_wfs=base_wfs, layer_clean=layer_clean,
                            bbox_str=tile_bbox, output_formats=output_formats,
                            max_features=batch_size,
                            max_requests=250 if tile_bbox == bbox_str else 40,
                            extra_headers=auth_headers, source_name=source_name,
                        )
                        if tile_wkt:
                            try:
                                chunks, found, _, stats, cql_ok = self._fetch_bbox_feature_chunks(
                                    boundary_wkt=tile_wkt, allow_bbox_fallback=False, **common_args
                                )
                                _record_wfs_stats(stats)
                            except CQLRequestRejected as rejected:
                                stats = getattr(rejected, "stats", {})
                                _record_wfs_stats(stats)
                                chunks = []
                                found = 0
                                cql_ok = False
                                split_failed = False
                                if len(cql_wkts) > 1:
                                    self._msg(
                                        "  [INFO] Kokeillaan pitkän CQL-suodattimen sijaan {} pienempää INTERSECTS-pyyntöä.".format(
                                            len(cql_wkts)
                                        )
                                    )
                                    for cql_index, cql_piece in enumerate(cql_wkts, 1):
                                        if self._verbose_diagnostics:
                                            self._msg("  [INFO] CQL-osa {}/{}...".format(cql_index, len(cql_wkts)))
                                        try:
                                            part_chunks, part_found, _, part_stats, part_ok = self._fetch_bbox_feature_chunks(
                                                boundary_wkt=cql_piece,
                                                allow_bbox_fallback=False,
                                                **common_args
                                            )
                                            stats = part_stats
                                            _record_wfs_stats(part_stats)
                                            if not part_ok:
                                                split_failed = True
                                                break
                                            chunks.extend(part_chunks)
                                            found += part_found
                                        except CQLRequestRejected as part_rejected:
                                            _record_wfs_stats(getattr(part_rejected, "stats", {}))
                                            split_failed = True
                                            break
                                    if not split_failed:
                                        cql_ok = True
                                        cql_state["split"] = True
                                if not cql_ok:
                                    for partial_fc in chunks:
                                        self._safe_delete(partial_fc)
                                    self._warn(
                                        "[VAROITUS] Sekä yhtenäinen että pilkottu CQL GET/POST epäonnistuivat. "
                                        "Käytetään vasta nyt BBOX-varamenetelmää ja paikallista Clip-vaihetta."
                                    )
                                    chunks, found, _, stats, cql_ok = self._fetch_bbox_feature_chunks(
                                        boundary_wkt=None, allow_bbox_fallback=True, **common_args
                                    )
                                    _record_wfs_stats(stats)
                        else:
                            chunks, found, _, stats, cql_ok = self._fetch_bbox_feature_chunks(
                                boundary_wkt=None, allow_bbox_fallback=True, **common_args
                            )
                            _record_wfs_stats(stats)
                        if tile_bbox == bbox_str and cql_ok:
                            cql_state["effective"] = True
                        if stats.get("pages"):
                            self._msg(
                                "  [INFO] WFS-yhteenveto: {} sivua ladattu ({} kohdetta).".format(
                                    stats.get("pages"), found
                                )
                            )
                        return chunks, found

                    resilience = ResilienceStrategy(
                        max_batch_size=max_features,
                        progress_callback=self._msg if self._verbose_diagnostics else None,
                        cleanup_callback=self._safe_delete,
                    )
                    chunks, total_found, used_grid = resilience.execute_with_fallback(_fetch_bbox_once, bbox_str)
                    temp_feature_classes.extend(chunks)
                    if truncation_state["hit"]:
                        # Vaillinainen aineisto on vaarallisempi kuin puuttuva:
                        # se näyttää kartalla täydeltä. Kaadetaan taso, jolloin
                        # ajon yhteenveto kertoo asiasta selkeästi.
                        raise Exception(
                            "Sivutuksen maksimipyyntömäärä täyttyi, joten taso jäisi "
                            "vaillinaiseksi. Rajaa alue pienemmäksi tai nosta rajaa."
                        )
                    cql_split_effective = cql_state["split"]
                    skip_clip = cql_state["effective"] and used_grid == 1
                except Exception as ex:
                    for temp_fc in temp_feature_classes:
                        self._safe_delete(temp_fc)
                    _record_layer_failure(layer_ui_name, ex)
                    continue

            if not temp_feature_classes:
                self._msg("  [INFO] Tasolta ei löytynyt kohteita annetulla rajauksella; vienti ohitettiin.")
                continue

            if len(temp_feature_classes) == 1:
                merged_fc = temp_feature_classes[0]
                created_merged = False
            else:
                merged_fc = os.path.join(self._scratch_gdb(), f"merged_{uuid.uuid4().hex[:10]}")
                merge_start = time.perf_counter()
                arcpy.management.Merge(temp_feature_classes, merged_fc)
                layer_gp_merge_s += time.perf_counter() - merge_start
                layer_metrics.add("Merge", time.perf_counter() - merge_start)
                created_merged = True

            # Kuntakohtaisessa ja Overpass-ruutuhaussa sama kohde voi tulla
            # mukaan useasta bboxista. Kaikki attribuutit kuuluvat vertailuun,
            # joten saman geometrian eri POI-fclass-rivit säilyvät.
            # Poistetaan geometrialtaan identtiset duplikaatit ennen leikkausta.
            if (
                used_kunta_chunks or cql_split_effective or used_overpass_grid > 1
            ) and created_merged:
                try:
                    duplicate_start = time.perf_counter()
                    self._delete_identical_downloads(merged_fc)
                    layer_metrics.add("duplikaattien poisto", time.perf_counter() - duplicate_start)
                except Exception as ex:
                    self._warn(f"[VAROITUS] Duplikaattien poisto epäonnistui tasolla '{layer_clean}': {ex}")

            if skip_clip:
                stage_start = time.perf_counter()
                arcpy.management.CopyFeatures(merged_fc, staged_fc)
                layer_gp_stage_s += time.perf_counter() - stage_start
                layer_metrics.add("staging", time.perf_counter() - stage_start)
                layer_metrics.skip(
                    "Clip",
                    "ohitettu (CQL palauttaa kokonaiset leikkaavat geometriat)"
                )
            else:
                clipped_fc = staged_fc
                clip_start = time.perf_counter()
                arcpy.analysis.Clip(merged_fc, boundary_fc, clipped_fc)
                layer_gp_clip_s += time.perf_counter() - clip_start
                layer_metrics.add("Clip", time.perf_counter() - clip_start)
                layer_metrics.skip("staging", "ei käytetty erillisenä vaiheena")

            temp_cleanup_start = time.perf_counter()
            for t in temp_feature_classes:
                if t != merged_fc:
                    try:
                        arcpy.management.Delete(t)
                    except Exception:
                        pass

            if created_merged and merged_fc and arcpy.Exists(merged_fc):
                try:
                    arcpy.management.Delete(merged_fc)
                except Exception:
                    pass
            layer_metrics.add(
                "väliaineistojen poistaminen", time.perf_counter() - temp_cleanup_start
            )

            count_start = time.perf_counter()
            staged_feature_count = int(arcpy.management.GetCount(staged_fc)[0])
            layer_metrics.add("kohdemäärän laskenta", time.perf_counter() - count_start)

            layer_processing_total = time.perf_counter() - layer_start

            staged_outputs.append({
                "path": staged_fc,
                "output_name": proposed_output_name,
                "output_name_is_final": True,
                "output_type": "feature",
                "label": layer_ui_name,
                "kind": "wfs",
                "layer_start": layer_start,
                "http_s": layer_http_s,
                "json_s": layer_gp_json_s,
                "merge_s": layer_gp_merge_s,
                "stage_s": layer_gp_stage_s,
                "clip_s": layer_gp_clip_s,
                "metrics": layer_metrics,
                "processing_total_s": layer_processing_total,
                "requested_mode": requested_mode,
                "feature_count": staged_feature_count,
            })

        if layer_failures:
            self._warn(
                "[VAROITUS] {} / {} valitusta tasosta epäonnistui: {}. "
                "Onnistuneet tasot viimeistellään normaalisti.".format(
                    len(layer_failures), total_layers,
                    ", ".join(label for label, _ in layer_failures),
                )
            )

        self._tool_metrics.set(
            "kaikkien tasojen käsittely", time.perf_counter() - all_layers_start
        )

        # siivous
        try:
            if kunnat_sel_fc and arcpy.Exists(kunnat_sel_fc) and kunnat_sel_fc != kunnat_all_fc:
                arcpy.management.Delete(kunnat_sel_fc)
        except Exception:
            pass
        try:
            if kunnat_all_fc and arcpy.Exists(kunnat_all_fc):
                arcpy.management.Delete(kunnat_all_fc)
        except Exception:
            pass
        if custom_boundary_tmp:
            self._safe_delete(custom_boundary_tmp)

        if admin_boundary_pending:
            boundary_output_start = time.perf_counter()
            local_boundary = admin_boundary_pending
            if admin_boundary_is_layer and admin_boundary_name:
                local_boundary = self._copy_features_compatible(
                    admin_boundary_pending, scratch_gdb, admin_boundary_name
                )
                self._safe_delete(admin_boundary_pending)
            staged_outputs.insert(0, {
                "path": local_boundary,
                "output_name": admin_boundary_name or "Aluerajaus",
                "output_type": "feature",
                "label": "Aluerajaus ({})".format(area_label),
                "kind": "boundary",
                "skip_map": admin_boundary_existing,
                "layer_start": boundary_output_start,
                "http_s": 0.0,
                "json_s": 0.0,
                "merge_s": 0.0,
                "stage_s": 0.0,
                "clip_s": 0.0,
            })

        staged_source_counts = {}
        for output in staged_outputs:
            source_path = output.get("path")
            if source_path:
                source_key = os.path.normcase(os.path.abspath(str(source_path)))
                staged_source_counts[source_key] = (
                    staged_source_counts.get(source_key, 0) + 1
                )

        to_add = []
        if staged_outputs:
            self._msg("[INFO] Kaikki käsittely on valmis. Kopioidaan tulokset kohteeseen vasta nyt...")
        outputs_copy_start = time.perf_counter()
        for output in staged_outputs:
            copy_start = time.perf_counter()
            if output["output_type"] == "feature":
                copy_metrics = output.get("metrics") or PhaseMetrics()
                if self._runtime_workspace_validated:
                    copy_metrics.skip(
                        "kohde-GDB:n olemassaolon tarkistus",
                        "ohitettu (kohde validoitiin ajon alussa)"
                    )
                else:
                    target_check_start = time.perf_counter()
                    if not (arcpy.Exists(workspace) or os.path.exists(workspace)):
                        raise Exception("Tallennuskohdetta ei löydy: {}".format(workspace))
                    copy_metrics.add(
                        "kohde-GDB:n olemassaolon tarkistus",
                        time.perf_counter() - target_check_start,
                    )
                    self._runtime_workspace_validated = True
                if output.get("output_name_is_final"):
                    output_name = output["output_name"]
                else:
                    name_start = time.perf_counter()
                    output_name = self._unique_output_name(output["output_name"], workspace)
                    copy_metrics.add("tulosnimen validointi", time.perf_counter() - name_start)
                final_path = self._copy_features_compatible(
                    output["path"], workspace, output_name, copy_metrics,
                    output_known_absent=True,
                )
                if "feature_count" not in output:
                    count_start = time.perf_counter()
                    output["feature_count"] = int(arcpy.management.GetCount(output["path"])[0])
                    copy_metrics.add("kohdemäärän laskenta", time.perf_counter() - count_start)
                copy_metrics.skip("indeksien luonti", "ei tarpeen")
                copy_metrics.skip("metatietojen käsittely", "ei käytetty")
                output["metrics"] = copy_metrics
            elif output["output_type"] == "raster":
                final_path = self._copy_raster_to_workspace(output["path"], workspace)
            else:
                final_path = self._copy_raster_bundle_to_workspace(output["path"], workspace)
            output["copy_s"] = time.perf_counter() - copy_start
            output["final_path"] = final_path
            to_add.append(output)
            local_delete_start = time.perf_counter()
            source_key = os.path.normcase(os.path.abspath(str(output["path"])))
            staged_source_counts[source_key] -= 1
            if staged_source_counts[source_key] <= 0:
                self._remove_local_output(output["path"])
            if output.get("metrics"):
                output["metrics"].add(
                    "väliaineistojen poistaminen", time.perf_counter() - local_delete_start
                )
            output["copy_and_local_cleanup_s"] = time.perf_counter() - copy_start
        self._tool_metrics.set(
            "lopputulosten kopiointi", time.perf_counter() - outputs_copy_start
        )

        if mml_api_key.strip():
            self._set_saved_secret("mml_api_key", mml_api_key)
        if karttapaikka_api_key.strip():
            self._set_saved_secret("karttapaikka_api_key", karttapaikka_api_key)
        if karttakuva_user and karttakuva_pass:
            self._set_saved_secret("karttakuva_user", karttakuva_user)
            self._set_saved_secret("karttakuva_pass", karttakuva_pass)
        if aino_token:
            self._set_saved_secret("aino_token", aino_token)

        self._msg("[INFO] Lisätään aineistoa kartalle.")
        map_all_start = time.perf_counter()
        for output in to_add:
            p = output.get("final_path")
            if output.get("skip_map"):
                output["map_s"] = None
                if output.get("metrics"):
                    output["metrics"].skip(
                        "kartalle lisääminen",
                        "ohitettu (sama latausalue on jo työtilassa)",
                    )
                self._msg(
                    "[INFO] Latausalueen rajaus on jo työtilassa; sitä ei lisätty kartalle."
                )
                continue
            map_start = time.perf_counter()
            if p:
                added, add_error = self._add_to_map(p)
                if added:
                    output["map_s"] = time.perf_counter() - map_start
                else:
                    output["map_s"] = None
                    self._warn(
                        "[VAROITUS] Aineistoa '{}' ei lisätty kartalle: {}".format(
                            p, add_error or "tuntematon syy"
                        )
                    )
            else:
                output["map_s"] = None
            if output.get("metrics"):
                if output["map_s"] is None:
                    output["metrics"].skip("kartalle lisääminen", "ohitettu")
                else:
                    output["metrics"].add("kartalle lisääminen", output["map_s"])
        self._tool_metrics.set(
            "tulosten kartalle lisääminen", time.perf_counter() - map_all_start
        )

        layer_phase_names = [
            "requestin muodostaminen", "verkkopyyntö", "vastauksen lukeminen",
            "vastauksen dekoodaus", "JSON-jäsennys",
            "väliaikaisen JSON-tiedoston kirjoittaminen", "JSONToFeatures",
            "projektointi", "geometrian tarkistus tai korjaus", "Merge",
            "duplikaattien poisto", "Clip", "staging",
            "kohde-GDB:n olemassaolon tarkistus", "tulosnimen validointi",
            "olemassa olevan tulosaineiston tarkistus",
            "olemassa olevan tulosaineiston poistaminen", "kenttien käsittely",
            "lopullinen CopyFeatures",
            "kohdemäärän laskenta", "indeksien luonti", "metatietojen käsittely",
            "kartalle lisääminen", "väliaineistojen poistaminen",
        ]
        for output in to_add:
            if output.get("kind") != "wfs" or not output.get("metrics"):
                continue
            metrics = output["metrics"]
            for phase in layer_phase_names:
                if phase not in metrics.seconds:
                    metrics.skip(phase, "ei käytetty")
            layer_total = (
                output.get("processing_total_s", 0.0)
                + output.get("copy_and_local_cleanup_s", 0.0)
                + (output.get("map_s") or 0.0)
            )
            self._msg(
                "  [INFO] Taso valmis: {} ({} kohdetta, hakutapa {}).".format(
                    output["label"], output.get("feature_count", "?"),
                    output.get("requested_mode", "tuntematon"),
                )
            )
            self._log_phase_summary(
                "  [INFO] Tason vaiheajat:", metrics, layer_phase_names, layer_total
            )

        self._msg("[INFO] Lataus valmis.")

        if layer_failures:
            self._msg("\n=== Ajo suoritettu osittain: onnistuneet tasot tallennettiin ===")
        else:
            self._msg("\n=== Ajo suoritettu onnistuneesti ===")
        return

    # ---------------------------
    # BOUNDARIES (paikallinen geopackage)
    # ---------------------------
    def _fetch_finland_boundary(self):
        source_fc, _ = self._get_extent_fc_and_namefield("Koko Suomi")
        out_fc = os.path.join(self._scratch_gdb(), f"finland_{uuid.uuid4().hex}")
        try:
            arcpy.management.CopyFeatures(source_fc, out_fc)
        except Exception:
            lyr = f"fin_lyr_{uuid.uuid4().hex[:8]}"
            dissolved_fc = os.path.join(self._scratch_gdb(), f"fin_diss_{uuid.uuid4().hex}")
            try:
                arcpy.management.MakeFeatureLayer(source_fc, lyr)
                arcpy.management.Dissolve(lyr, dissolved_fc, multi_part="MULTI_PART")
                arcpy.management.CopyFeatures(dissolved_fc, out_fc)
            finally:
                self._safe_delete(lyr)
                self._safe_delete(dissolved_fc)
        return out_fc

    def _process_administrative_boundary(self, extent_type, extent_values, workspace, metrics=None):
        metrics = metrics if metrics is not None else PhaseMetrics()
        metrics.skip("rajauksen projektointi", "ei tarpeen (paikallinen aineisto on EPSG:3067)")
        metrics.skip("geometrian tarkistus tai korjaus", "ei tarpeen")
        if extent_type == "Koko Suomi":
            base_name = "Koko_Suomi"
            out_name = self._validated_name(base_name, workspace)
            describe_start = time.perf_counter()
            source_fc, _ = self._get_extent_fc_and_namefield("Koko Suomi")
            metrics.add("lähtöaineiston kuvaustietojen lukeminen", time.perf_counter() - describe_start)
            try:
                copy_start = time.perf_counter()
                out_fc = self._feature_class_to_workspace(source_fc, workspace, out_name)
                metrics.add("rajauksen kopiointi", time.perf_counter() - copy_start)
                self._assert_has_selection(out_fc, None, "Tulosaineisto jäi tyhjäksi.")
            except Exception as direct_ex:
                fin_fc = self._fetch_finland_boundary()
                try:
                    out_fc = self._copy_features_compatible(fin_fc, workspace, out_name)
                    self._assert_has_selection(out_fc, None, "Tulosaineisto jäi tyhjäksi.")
                except Exception as ex:
                    raise Exception(
                        "Koko Suomi -rajauksen muodostus epäonnistui. extent_type={}, source={}, workspace={}, syy={}".format(
                            extent_type, source_fc, workspace, ex
                        )
                    ) from direct_ex
                finally:
                    self._safe_delete(fin_fc)
            return out_fc

        if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"]:
            vals = extent_values or []
            if not vals:
                raise Exception(f"{extent_type} valittu, mutta lista tyhjä.")

            base_name = "+".join(vals)
            out_name = self._validated_name(base_name, workspace)

            describe_start = time.perf_counter()
            source_fc, name_field = self._get_extent_fc_and_namefield(extent_type)
            metrics.add("lähtöaineiston kuvaustietojen lukeminen", time.perf_counter() - describe_start)
            selection_start = time.perf_counter()
            fld = arcpy.AddFieldDelimiters(source_fc, name_field)
            sql_values = ", ".join([self._sql_quote(v) for v in vals])
            where_clause = "{} IN ({})".format(fld, sql_values)
            empty_msg = "{}-rajauksen haku epäonnistui: yhtään geometriaa ei saatu.".format(extent_type)

            self._assert_has_selection(source_fc, where_clause, empty_msg)
            metrics.add(
                "valittujen kohteiden tai määrittelykyselyn käsittely",
                time.perf_counter() - selection_start,
            )
            # Kopioi vain valitut kohteet ajokohtaiseen paikalliseen scratch-GDB:hen.
            # Geometrioita ei yhdistetä tässä: tarkka monikohteinen aineisto säilyy
            # Clip-vaihetta varten, ja CQL-WKT yhdistetään vasta sitä muodostettaessa.
            copy_start = time.perf_counter()
            out_fc = self._feature_class_to_workspace(
                source_fc, workspace, out_name, where_clause
            )
            metrics.add("rajauksen kopiointi", time.perf_counter() - copy_start)
            self._assert_has_selection(out_fc, None, empty_msg)
            return out_fc

        raise Exception(f"Tuntematon extent_type: {extent_type}")

    # ---------------------------
    # LAYER LIST
    # ---------------------------
    def _layer_cache_file(self):
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
        if appdata:
            return os.path.join(appdata, "Suomenvaylat", "layer_catalog_cache.json")
        return os.path.join(tempfile.gettempdir(), "suomenvaylat_layer_catalog_cache.json")

    def _read_layer_disk_cache(self, cache_key):
        """Lue tasolistaus levyltä, jos merkintä on tuore.

        Tasolistaus haetaan GetCapabilities-pyynnöillä, jotka kestävät
        sekunteja. Ilman levyvälimuistia jokainen työkalun avaus maksaa saman
        odotuksen uudelleen, koska muistivälimuisti elää vain instanssin ajan.
        """
        ttl = max(0, int(getattr(self, "_layer_cache_ttl_s", 86400)))
        if ttl <= 0:
            return None
        path = self._layer_cache_file()
        try:
            if not os.path.isfile(path):
                return None
            if (time.time() - os.path.getmtime(path)) > ttl:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                store = json.load(handle)
        except Exception:
            return None
        if not isinstance(store, dict):
            return None
        entry = store.get(cache_key)
        if not isinstance(entry, dict):
            return None
        layers = entry.get("layers")
        mapping = entry.get("mapping")
        if not isinstance(layers, list) or not isinstance(mapping, dict):
            return None
        return layers, mapping

    def _write_layer_disk_cache(self, cache_key, layers, mapping):
        if max(0, int(getattr(self, "_layer_cache_ttl_s", 86400))) <= 0:
            return
        path = self._layer_cache_file()
        store = {}
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    store = loaded
        except Exception:
            store = {}
        store[cache_key] = {"layers": list(layers), "mapping": dict(mapping)}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Kirjoita väliaikaistiedoston kautta, jottei rinnakkainen ArcGIS
            # Pro -istunto näe puolikasta JSONia.
            temp_path = "{}.{}".format(path, uuid.uuid4().hex[:8])
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle, ensure_ascii=False)
            os.replace(temp_path, path)
        except Exception:
            pass

    def _fetch_layer_list(self, source_names=None, cache_key=None, allow_disk_cache=True):
        selected_sources = source_names or ["Väylä", "DigiRoad"]
        if allow_disk_cache and cache_key:
            cached = self._read_layer_disk_cache(cache_key)
            if cached is not None:
                layers, mapping = cached
                self._layer_mapping = dict(mapping)
                return list(layers)
        layer_list = self._get_layer_entries_for_sources(selected_sources)
        if cache_key and layer_list:
            self._write_layer_disk_cache(
                cache_key, layer_list, dict(self._layer_mapping)
            )
        return layer_list


class MMLBasemapDownloader(VaylaWFSDownloader):
    def __init__(self):
        super().__init__()
        self.label = "Taustakartat (MML/Kapsi)"
        self.description = "Lisää MML:n nykyiset vector tile -taustakartat tai lataa Kapsin rasteritaustan."
        self.canRunInBackground = False
        # Kaikki MML-/WMS-attribuutit ja apumetodit peritään emoluokasta
        # (VaylaWFSDownloader); vain UI ja execute eroavat.

    def _get_basemap_layers_cached(self, provider):
        if provider == "MML":
            # Nykyinen MML:n avoin karttakuvapalvelu julkaisee TileJSONin
            # kautta sekä maastotiedot että kiinteistöjaotuksen.
            self._mml_layer_mapping = dict(MML_VECTOR_TILE_LAYER_IDS)
            return list(MML_VECTOR_TILE_LAYER_IDS.keys())
        return super()._get_basemap_layers_cached(provider)

    def _get_basemap_mode_options(self, provider):
        if provider == "MML":
            return ["Live vector tile"]
        return super()._get_basemap_mode_options(provider)

    def getParameterInfo(self):
        p_provider = arcpy.Parameter(
            displayName="Taustakarttapalvelu",
            name="provider",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_provider.filter.list = ["MML", "Kapsi"]
        p_provider.value = "MML"

        p_map_search = arcpy.Parameter(
            displayName="Suodata taustakarttoja kirjoittamalla",
            name="map_search",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_map_search.value = ""

        p_map = arcpy.Parameter(
            displayName="Valitse taustakartta",
            name="mml_map",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_map.filter.type = "ValueList"
        try:
            p_map.filter.list = self._get_basemap_layers_cached("MML")
        except Exception:
            p_map.filter.list = []

        p_mode = arcpy.Parameter(
            displayName="Toimitustapa",
            name="delivery_mode",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_mode.filter.list = ["Live vector tile"]
        p_mode.value = "Live vector tile"

        p_extent_type = arcpy.Parameter(
            displayName="Aluerajauksen taso",
            name="extent_type",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_extent_type.filter.list = [
            "Koko Suomi",
            "Elinvoimakeskus",
            "Hyvinvointialue",
            "Maakunta",
            "Kunta/Kaupunki",
            "Oma aineisto (Polygon/Polyline)"
        ]
        p_extent_type.value = "Koko Suomi"

        p_extent_value = arcpy.Parameter(
            displayName="Valitse alue",
            name="extent_value",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True
        )
        p_extent_value.enabled = False
        p_extent_value.filter.type = "ValueList"
        p_extent_value.filter.list = []

        p_custom_layer = arcpy.Parameter(
            displayName="Oma rajausaineisto",
            name="custom_layer",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        p_custom_layer.filter.list = ["Polygon", "Polyline"]
        p_custom_layer.enabled = False

        p_workspace = arcpy.Parameter(
            displayName="Tallennuskohde (File GDB tai kansio)",
            name="workspace",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input"
        )
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            p_workspace.value = aprx.defaultGeodatabase
        except Exception:
            pass

        p_api_key = arcpy.Parameter(
            displayName="MML API-avain",
            name="api_key",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_api_key.value = self._get_saved_secret("mml_api_key")

        return [p_provider, p_map_search, p_map, p_mode, p_extent_type, p_extent_value, p_custom_layer, p_workspace, p_api_key]

    def updateParameters(self, parameters):
        try:
            provider = parameters[0].valueAsText or "MML"
            map_search = parameters[1].valueAsText or ""
            selected_map = parameters[2].valueAsText
            extent_type = parameters[4].valueAsText
            api_key = (parameters[8].valueAsText or "").strip()
            self._runtime_mml_api_key = api_key
            mode_options = self._get_basemap_mode_options(provider)
            mode = parameters[3].valueAsText or mode_options[0]

            all_maps = self._get_basemap_layers_cached(provider)
            filtered = all_maps
            if map_search.strip():
                q = self._norm(map_search)
                filtered = [x for x in all_maps if q in self._norm(x)]
            parameters[2].filter.list = filtered if filtered else ["(ei osumia – tyhjennä haku)"]
            if selected_map and selected_map in filtered:
                # filter.list-päivitys voi muuten nollata valinnan ArcGIS Prossa.
                parameters[2].value = selected_map
            elif selected_map and selected_map not in filtered:
                parameters[2].value = None
            parameters[3].filter.list = mode_options
            if mode not in mode_options:
                parameters[3].value = mode_options[0]

            parameters[5].enabled = False
            parameters[6].enabled = False
            parameters[5].filter.list = []
            parameters[7].enabled = (provider == "Kapsi")
            # MML:n vector tile -palvelu on live-palvelu: se piirtää kartan
            # kulloisenkin karttanäkymän mukaan eikä tee rajauskohtaisesti
            # tiedostoon tallennettavaa rasteria. Rajausparametrit koskevat
            # siksi vain Kapsin rasterilatausta.
            parameters[4].enabled = (provider == "Kapsi")
            if provider == "Kapsi" and extent_type in [
                "Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"
            ]:
                parameters[5].enabled = True
                parameters[5].filter.list = self._get_extent_choices(extent_type)
            elif provider == "Kapsi" and extent_type == "Oma aineisto (Polygon/Polyline)":
                parameters[6].enabled = True
                parameters[5].value = None

            parameters[8].enabled = (provider == "MML")
        except Exception as e:
            self._warn(f"updateParameters epäonnistui: {e}")

    def updateMessages(self, parameters):
        provider = parameters[0].valueAsText or "MML"
        extent_type = parameters[4].valueAsText
        extent_value_text = parameters[5].valueAsText
        custom_layer = parameters[6].valueAsText
        mode = parameters[3].valueAsText or "Live vector tile"
        api_key = parameters[8].valueAsText or ""

        vals = self._parse_multivalue(extent_value_text)
        if provider == "Kapsi" and extent_type in [
            "Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"
        ] and not vals:
            parameters[5].setErrorMessage("Valitse alue on pakollinen tälle aluerajauksen tasolle.")
        else:
            parameters[5].clearMessage()

        if provider == "Kapsi" and extent_type == "Oma aineisto (Polygon/Polyline)" and (not custom_layer or str(custom_layer).strip() == ""):
            parameters[6].setErrorMessage("Valitse oma rajausaineisto.")
        else:
            parameters[6].clearMessage()

        if provider == "MML" and not api_key.strip():
            parameters[8].setErrorMessage(
                "MML:n OGC API/vector tile -tasojen listaus ja lisäys vaativat API-avaimen."
            )
        else:
            parameters[8].clearMessage()

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        self._msg("=== Taustakarttatyökalu käynnistyy ===")

        provider = parameters[0].valueAsText or "MML"
        map_display = parameters[2].valueAsText
        mode = parameters[3].valueAsText or "Live vector tile"
        extent_type = parameters[4].valueAsText
        extent_vals = self._parse_multivalue(parameters[5].valueAsText)
        custom_layer = parameters[6].valueAsText
        workspace = parameters[7].valueAsText
        api_key = parameters[8].valueAsText or ""
        self._runtime_mml_api_key = api_key.strip()

        if not workspace or workspace.strip() == "":
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                workspace = aprx.defaultGeodatabase
            except Exception:
                workspace = self._scratch_gdb()

        if not map_display or self._is_layer_placeholder(map_display):
            raise Exception("Valitse taustakartta.")
        layer_id = self._get_basemap_layer_id(provider, map_display)
        boundary_fc = None
        if provider == "Kapsi":
            if extent_type == "Oma aineisto (Polygon/Polyline)":
                boundary_fc = self._prepare_custom_boundary(custom_layer)
            else:
                boundary_fc = self._process_administrative_boundary(
                    extent_type, extent_vals, self._scratch_gdb()
                )

        try:
            if provider == "MML":
                if not api_key.strip():
                    raise Exception("MML:n vector tile -palvelu vaatii API-avaimen.")
                self._add_mml_vector_tile_layer(
                    layer_id, map_display, api_key.strip()
                )
            else:
                out_jpg = self._download_kapsi_wms_jpeg(
                    layer_id, boundary_fc, self._scratch_folder()
                )
                final_jpg = self._copy_raster_bundle_to_workspace(out_jpg, workspace)
                self._add_to_map(final_jpg)
                self._remove_local_output(out_jpg)
        finally:
            if boundary_fc:
                self._safe_delete(boundary_fc)

        if api_key.strip():
            self._set_saved_secret("mml_api_key", api_key)

        self._msg("[INFO] Taustakartan tuonti valmis.")
