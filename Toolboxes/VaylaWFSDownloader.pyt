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
import ctypes
from ctypes import wintypes


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
            "Karttapaikka": {
                "type": "wfs",
                "endpoints": [
                    "https://avoin-karttakuva.maanmittauslaitos.fi/inspire/wfs",
                    "https://avoin-paikkatieto.maanmittauslaitos.fi/geoserver/maastotiedot/wfs"
                ],
                "description": "MML INSPIRE WFS (voi vaatia tunnukset)"
            },
            "MML": {
                "type": "mml_raster",
                "endpoints": ["https://avoin-paikkatieto.maanmittauslaitos.fi/geoserver/gwc/service/wmts?SERVICE=WMTS&REQUEST=GetCapabilities"],
                "description": "MML taustakartat (rasteri)"
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
                "endpoints": ["https://overpass-api.de/api/interpreter"],
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
            "MML",
            "MML Karttakuva",
            "Kapsi",
            "Liiteri",
            "Syke",
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

    def __init__(self, max_batch_size=10000, grid_levels=None, progress_callback=None):
        self.max_batch_size = max_batch_size
        self.grid_levels = grid_levels or [1, 2, 4]
        self._progress = progress_callback  # callable(msg_str) or None

    def _log(self, msg):
        if self._progress:
            self._progress(msg)

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
        self.description = "Lataa WFS-aineistot osissa, hakee aluerajaukset paikallisesta geopackagesta ja leikkaa aineistot."
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

        # MML taustakartat (samassa UI:ssa)
        self._all_mml_layers_cache = {}
        self._mml_layer_mapping = {}
        self._runtime_mml_api_key = ""
        self._runtime_karttapaikka_api_key = ""
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
        self.mml_wmts_capabilities = "https://avoin-paikkatieto.maanmittauslaitos.fi/geoserver/gwc/service/wmts?SERVICE=WMTS&REQUEST=GetCapabilities"
        self.mml_wms_base = "https://avoin-paikkatieto.maanmittauslaitos.fi/geoserver/wms"
        self.mml_karttakuva_wmts = "https://karttakuva.maanmittauslaitos.fi/maasto/wmts/1.0.0/WMTSCapabilities.xml"
        self._all_mml_karttakuva_layers_cache = {}
        self._mml_karttakuva_layer_mapping = {}
        self._runtime_karttakuva_user = ""
        self._runtime_karttakuva_pass = ""
        self._all_kapsi_layers_cache = None
        self._kapsi_layer_mapping = {
            "Ortokuva": "https://tiles.kartat.kapsi.fi/ortokuva|ortokuva"
        }
        self.kapsi_wms_base = "https://tiles.kartat.kapsi.fi/ortokuva"
        self._run_scratch_folder = None
        self._run_scratch_gdb = None
        self._tool_metrics = None
        self._run_id = None
        self._verbose_diagnostics = False
        self._run_had_layer_failures = False

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
            getattr(self, "_runtime_karttakuva_user", ""),
            getattr(self, "_runtime_karttakuva_pass", ""),
        ):
            if secret:
                text = text.replace(secret, "[PIILOTETTU]")
        return text

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

    def _parse_source_values(self, value_as_text):
        values = self._parse_multivalue(value_as_text)
        return values if values else ["Väylä"]

    def _parse_multivalue_param(self, param):
        try:
            raw_values = getattr(param, "values", None)
            if raw_values:
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

    def _feature_class_to_workspace(self, source_fc, workspace, out_name, where_clause=None):
        if self._is_filesystem_workspace(workspace):
            temp_name = "sel_{}".format(uuid.uuid4().hex[:8])
            temp_fc = os.path.join(self._scratch_gdb(), temp_name)
            try:
                arcpy.conversion.FeatureClassToFeatureClass(
                    source_fc, self._scratch_gdb(), temp_name, where_clause
                )
                return self._copy_features_compatible(temp_fc, workspace, out_name)
            finally:
                self._safe_delete(temp_fc)

        out_path = os.path.join(workspace, out_name)
        self._safe_delete(out_path)
        arcpy.conversion.FeatureClassToFeatureClass(
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

    def _build_source_auth_headers(self, source_name: str):
        headers = {}
        if source_name == "Karttapaikka":
            key = (self._runtime_karttapaikka_api_key or "").strip()
            if key:
                token = base64.b64encode(f"{key}:".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
        return headers

    def _fetch_wfs_capabilities_with_headers(self, endpoint, headers=None):
        sep = "&" if "?" in endpoint else "?"
        url = "{}{}service=WFS&request=GetCapabilities".format(endpoint, sep)
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

    def _get_karttapaikka_layers(self):
        layers = []
        seen = set()
        headers = self._build_source_auth_headers("Karttapaikka")
        for endpoint in self.wfs_registry.get_endpoints("Karttapaikka"):
            try:
                endpoint_layers = self._fetch_wfs_capabilities_with_headers(endpoint, headers=headers)
            except urllib.error.HTTPError as ex:
                if ex.code == 401:
                    raise Exception("Karttapaikka vaatii API-avaimen (401 Unauthorized).")
                self._warn("[VAROITUS] Karttapaikka GetCapabilities epäonnistui '{}': HTTP {}".format(endpoint, ex.code))
                continue
            except Exception as ex:
                self._warn("[VAROITUS] Karttapaikka GetCapabilities epäonnistui '{}': {}".format(endpoint, ex))
                continue

            for lyr in endpoint_layers:
                lid = lyr.get("id")
                if lid and lid not in seen:
                    seen.add(lid)
                    layers.append(lyr)
        return layers

    def _get_layer_entries_for_sources(self, source_names):
        entries = []
        mapping = {}
        for source_name in source_names:
            source_type = self.wfs_registry.get_type(source_name)
            if source_type == "overpass":
                source_entries = OverpassAdapter.get_layers()
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
                    "title": title
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

    def _fetch_osm_feature_chunks(self, layer_id, boundary_fc, max_grid=4):
        bbox_wgs84 = self._extent_bbox_4326(boundary_fc)
        overpass_url = self.wfs_registry.get_endpoint("OpenStreetMap")
        boundary_sr = arcpy.Describe(boundary_fc).spatialReference
        temp_feature_classes = []

        def _fetch_one(osm_bbox, batch_size):
            query = OverpassAdapter.build_query(layer_id, osm_bbox)
            # Overpass API requires form-encoded POST: data=<urlencoded_query>
            post_body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            req = urllib.request.Request(
                overpass_url, data=post_body,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "ArcGISPro-OSMDownloader/1.0"}
            )
            with urllib.request.urlopen(req, timeout=180) as response:
                raw = response.read().decode("utf-8", errors="replace")
            geojson = OverpassAdapter.to_geojson(json.loads(raw))
            if not geojson.get("features"):
                return [], 0
            temp_json_path = os.path.join(self._scratch_folder(), "osm_{}.geojson".format(uuid.uuid4().hex))
            with open(temp_json_path, "w", encoding="utf-8") as handle:
                json.dump(geojson, handle, ensure_ascii=False)
            temp_fc = os.path.join(self._scratch_gdb(), "osm_fc_{}".format(uuid.uuid4().hex[:10]))
            arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc)
            try:
                os.remove(temp_json_path)
            except Exception:
                pass
            # Overpass palauttaa koordinaatit aina WGS84-longitude/latitude-
            # muodossa, mutta GeoJSON-väliaineistoon ei välttämättä tallennu
            # CRS-metadataa. Määritä lähde-CRS ennen Projectia, muuten ArcGIS
            # antaa virheen 000517 (koordinaattijärjestelmää ei ole määritetty).
            self._define_osm_source_projection(temp_fc)
            projected_fc = os.path.join(self._scratch_gdb(), "osm_prj_{}".format(uuid.uuid4().hex[:10]))
            arcpy.management.Project(temp_fc, projected_fc, boundary_sr)
            self._safe_delete(temp_fc)
            return [projected_fc], len(geojson.get("features", []))

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
        try:
            d = arcpy.Describe(workspace)
            return (getattr(d, "workspaceType", "") or "").lower() == "filesystem"
        except Exception:
            return False

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
            resources_dir = self._find_resources_dir()
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
    def _fetch_json(self, request_url: str, timeout: int = 60, quiet: bool = False,
                    extra_headers=None, post_data=None, timings=None):
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
        req = urllib.request.Request(request_url, data=body, headers=headers)
        timings.add("requestin muodostaminen", time.perf_counter() - build_start)
        status = None
        ctype = ""
        raw_text = ""

        try:
            network_start = time.perf_counter()
            response = urllib.request.urlopen(req, timeout=timeout)
            timings.add("verkkopyyntö", time.perf_counter() - network_start)
            with response:
                status = getattr(response, "status", None)
                ctype = response.headers.get("Content-Type", "") or ""
                read_start = time.perf_counter()
                raw_bytes = response.read()
                timings.add("vastauksen lukeminen", time.perf_counter() - read_start)
            decode_start = time.perf_counter()
            raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
            timings.add("vastauksen dekoodaus", time.perf_counter() - decode_start)
        except urllib.error.HTTPError as e:
            timings.add("verkkopyyntö", time.perf_counter() - network_start)
            status = getattr(e, "code", None)
            try:
                ctype = e.headers.get("Content-Type", "") or ""
            except Exception:
                ctype = ""
            try:
                read_start = time.perf_counter()
                raw_bytes = e.read()
                timings.add("vastauksen lukeminen", time.perf_counter() - read_start)
                decode_start = time.perf_counter()
                raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
                timings.add("vastauksen dekoodaus", time.perf_counter() - decode_start)
            except Exception:
                raw_text = ""
        except Exception:
            try:
                timings.add("verkkopyyntö", time.perf_counter() - network_start)
            except Exception:
                pass
            return None, "", status, ctype

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
        type_names_q = urllib.parse.quote(layer_clean, safe=":")
        outfmt_q = urllib.parse.quote(output_format, safe=";/,+=")
        url = (
            f"{base_wfs}?service=WFS&version=2.0.0"
            f"&request=GetFeature&typeNames={type_names_q}"
            f"&outputFormat={outfmt_q}"
            f"&exceptions=application/json"
            f"&srsName=EPSG:3067"
            f"&count={max_features}&startIndex={start_index}"
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
                             bbox_str=None, cql_filter=None, geometry_only=True):
        form = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer_clean,
            "outputFormat": output_format,
            "exceptions": "application/json",
            "srsName": "EPSG:3067",
            "count": str(max_features),
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
        query = urllib.parse.urlencode({
            "service": "WFS",
            "version": "2.0.0",
            "request": "DescribeFeatureType",
            "typeNames": layer_clean,
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
                form = self._wfs_getfeature_form(
                    layer_clean, max_features, start_index, fmt,
                    bbox_str=bbox_str, cql_filter=cql_filter, geometry_only=geometry_only,
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


    def _json_to_temp_fc(self, raw_text: str):
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
        delete_start = time.perf_counter()
        try:
            os.remove(temp_json_path)
        except Exception:
            pass
        timings.set("väliaikaisen JSON-tiedoston poistaminen", time.perf_counter() - delete_start)
        return temp_fc, timings

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
        request_count = 0
        total_features = 0
        start_index = 0
        repeated_guard = 0
        prev_hash = None
        cql_disabled = False

        while request_count < max_requests:
            page_start = time.perf_counter()
            page_timing = PhaseMetrics()
            request_count += 1
            active_cql = cql_filter if (use_cql and not cql_disabled) else None
            cql_post_tried = False

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

            page_fc, conversion_timing = self._json_to_temp_fc(raw_text)
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

        if request_count >= max_requests:
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

    def _fetch_mml_layer_list(self, api_key=""):
        out = []
        self._mml_layer_mapping.clear()
        headers = {"User-Agent": "ArcGISPro-MMLBasemapTool/1.0"}
        key = (api_key or "").strip()
        # MML accepts api-key as query param; also send Basic auth as fallback
        caps_url = self.mml_wmts_capabilities
        if key:
            sep = "&" if "?" in caps_url else "?"
            caps_url = caps_url + sep + "api-key=" + urllib.parse.quote(key, safe="")
            token = base64.b64encode(f"{key}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        req = urllib.request.Request(caps_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                xml_bytes = response.read()
        except urllib.error.HTTPError as ex:
            if ex.code == 401:
                raise Exception("MML WMTS vaatii API-avaimen (401 Unauthorized).")
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
                if display in self._mml_layer_mapping and self._mml_layer_mapping[display] != layer_id:
                    display = f"{display} ({layer_id})"
                self._mml_layer_mapping[display] = layer_id
                out.append(display)
        out_sorted = sorted(list(set(out)))
        if not out_sorted:
            raise Exception("Yhtään MML-karttatasoa ei löytynyt WMTS capabilities -vastauksesta.")
        return out_sorted

    def _get_mml_layers_cached(self, api_key=""):
        cache_key = "auth:{}".format(self._norm(api_key)) if (api_key or "").strip() else "noauth"
        if cache_key not in self._all_mml_layers_cache:
            self._all_mml_layers_cache[cache_key] = self._fetch_mml_layer_list(api_key=api_key)
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
        self._kapsi_layer_mapping.clear()
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
                for child in elem:
                    if child.tag.endswith("Name") and child.text and not layer_name:
                        layer_name = child.text.strip()
                    elif child.tag.endswith("Title") and child.text and not layer_title:
                        layer_title = child.text.strip()

                if layer_name:
                    display_core = layer_title if layer_title else layer_name
                    display = "{} ({})".format(display_core, service_name)
                    layer_ref = "{}|{}".format(service_base, layer_name)
                    unique_display = display
                    counter = 2
                    while unique_display in self._kapsi_layer_mapping and self._kapsi_layer_mapping[unique_display] != layer_ref:
                        unique_display = "{} ({})".format(display, counter)
                        counter += 1

                    self._kapsi_layer_mapping[unique_display] = layer_ref
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
        return ["Live WMTS", "Raster (GeoTIFF EPSG:3067)"]

    def _write_world_file(self, raster_path, ext, width, height):
        root, extension = os.path.splitext(raster_path)
        world_extension_map = {
            ".jpg": ".jgw",
            ".jpeg": ".jgw",
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
        service_root = os.path.basename(
            urllib.parse.urlsplit(service_base or "").path.rstrip("/")
        )
        return service_root or selected_layer

    def _download_kapsi_wms_jpeg(self, layer_id: str, boundary_fc: str, workspace: str):
        service_base = self.kapsi_wms_base
        service_layer = layer_id
        if "|" in (layer_id or ""):
            parts = layer_id.split("|", 1)
            service_base = parts[0].strip() or self.kapsi_wms_base
            service_layer = parts[1].strip() or layer_id

        # Kapsin Capabilities-listan asteikkotasot (esim. taustakartta_800k)
        # ovat mittakaavasidonnaisia. Niitä suoraan pyydettäessä MapServer voi
        # palauttaa tyhjän kuvan. Pyydä palvelun ylätaso, joka valitsee oikean
        # asteikkotason automaattisesti annetun BBOXin ja pikselikoon perusteella.
        request_layer = self._kapsi_request_layer(service_base, service_layer)

        ext = self._boundary_extent_3067(boundary_fc)

        # Determine tiling: for large areas, split into a grid so each tile
        # has a resolution suitable for the selected Kapsi map series.
        extent_w = ext.XMax - ext.XMin
        extent_h = ext.YMax - ext.YMin
        tile_px = 4096
        target_gsd_by_layer = {
            "taustakartta_4m": 4.0,
            "taustakartta_5k": 1.5,
            "taustakartta_8m": 8.0,
            "taustakartta_40k": 12.0,
            "taustakartta_80k": 24.0,
            "maastokartta_50k": 15.0,
            "maastokartta_100k": 30.0,
            "maastokartta_250k": 75.0,
            "maastokartta_500k": 150.0,
            "yleiskartta_1000k": 280.0,
            "yleiskartta_2000k": 560.0,
            "yleiskartta_4500k": 1260.0,
            "yleiskartta_8000k": 2240.0,
        }
        target_gsd = target_gsd_by_layer.get(service_layer.lower(), 20.0)
        cols = max(1, int(math.ceil(extent_w / (tile_px * target_gsd))))
        rows = max(1, int(math.ceil(extent_h / (tile_px * target_gsd))))
        # Cap tiles to avoid excessive requests
        if cols * rows > 25:
            scale = math.sqrt(25.0 / (cols * rows))
            cols = max(1, int(cols * scale))
            rows = max(1, int(rows * scale))

        tile_w = extent_w / cols
        tile_h = extent_h / rows

        raster_dir = self._raster_folder(workspace)
        tile_paths = []

        for row_i in range(rows):
            for col_i in range(cols):
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
                raw = self._download_kapsi_image_bytes(request_url)

                tile_name = self._validated_name(
                    "Kapsi_{}_tile_{}_{}".format(service_layer, row_i, col_i), raster_dir
                ) + ".jpg"
                tile_path = os.path.join(raster_dir, tile_name)
                with open(tile_path, "wb") as handle:
                    handle.write(raw)

                # Create a simple Extent-like object for the world file
                class _TileExt:
                    pass
                te = _TileExt()
                te.XMin, te.YMin, te.XMax, te.YMax = t_xmin, t_ymin, t_xmax, t_ymax
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
                self._write_world_file(out_path, ext, mosaic_width, mosaic_height)
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

    def _find_wmts_credentials_file(self):
        resources_dir = self._find_resources_dir()
        if not resources_dir:
            return None
        wmts_creds = os.path.join(resources_dir, "credentials.wmts")
        return wmts_creds if os.path.exists(wmts_creds) else None

    def _raster_folder(self, workspace: str) -> str:
        """Return a plain folder for raster output — GDBs cannot hold loose image files."""
        if not self._is_filesystem_workspace(workspace):
            parent = os.path.dirname(os.path.abspath(workspace))
            return parent if os.path.isdir(parent) else self._scratch_folder()
        return workspace

    def _boundary_extent_3067(self, boundary_fc):
        desc = arcpy.Describe(boundary_fc)
        sr = desc.spatialReference
        if sr and sr.factoryCode == 3067:
            return self._boundary_extent_from_features(boundary_fc)
        tmp = os.path.join(self._scratch_gdb(), f"bnd_3067_{uuid.uuid4().hex[:8]}")
        arcpy.management.Project(boundary_fc, tmp, arcpy.SpatialReference(3067))
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

    def _download_wms_geotiff(self, layer_id: str, boundary_fc: str, workspace: str, api_key: str):
        ext = self._boundary_extent_3067(boundary_fc)
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": layer_id,
            "STYLES": "",
            "CRS": "EPSG:3067",
            "BBOX": f"{ext.XMin},{ext.YMin},{ext.XMax},{ext.YMax}",
            "WIDTH": "4096",
            "HEIGHT": "4096",
            "FORMAT": "image/geotiff",
            "TRANSPARENT": "TRUE"
        }
        request_url = f"{self.mml_wms_base}?{urllib.parse.urlencode(params)}"
        token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            request_url,
            headers={"Authorization": f"Basic {token}", "User-Agent": "ArcGISPro-MMLBasemapTool/1.0"}
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
            ctype = (resp.headers.get("Content-Type", "") or "").lower()
        if "xml" in ctype or "html" in ctype:
            raise Exception("WMS palautti virhesisältöä XML/HTML-muodossa GeoTIFFin sijaan.")
        raster_dir = self._raster_folder(workspace)
        out_name = self._validated_name(f"MML_{layer_id}_raster", raster_dir) + ".tif"
        out_tif = os.path.join(raster_dir, out_name)
        with open(out_tif, "wb") as f:
            f.write(raw)
        return out_tif

    # ---------------------------
    # UI / PARAMETERS
    # ---------------------------
    def getParameterInfo(self):
        p_wfs_sources = arcpy.Parameter(
            displayName="Valitse rajapinnat",
            name="wfs_sources",
            datatype="GPValueTable",
            parameterType="Optional",
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
        p_layers.parameterDependencies = ["wfs_sources", "layer_search", "mml_api_key", "karttapaikka_api_key", "karttakuva_user", "karttakuva_pass"]
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
            displayName="MML API-avain (vain MML rasteritasoille)",
            name="mml_api_key",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        p_mml_api_key.value = self._get_saved_secret("mml_api_key")

        p_karttapaikka_api_key = arcpy.Parameter(
            displayName="Karttapaikka API-avain (vain Karttapaikka-lähteelle)",
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

        return [
            p_wfs_sources,
            p_layer_search, p_layers,
            p_extent_type, p_extent_value, p_custom_layer, p_workspace,
            p_mml_api_key,
            p_karttapaikka_api_key,
            p_karttakuva_user,
            p_karttakuva_pass
        ]

    def updateParameters(self, parameters):
        try:
            source_values = self._parse_multivalue_param(parameters[0])
            if not source_values:
                source_values = ["Väylä"]
            layer_search = parameters[1].valueAsText or ""
            extent_type = parameters[3].valueAsText
            mml_api_key = (parameters[7].valueAsText or "").strip()
            karttapaikka_api_key = (parameters[8].valueAsText or "").strip()
            karttakuva_user = (parameters[9].valueAsText or "").strip()
            karttakuva_pass = (parameters[10].valueAsText or "").strip()
            self._runtime_mml_api_key = mml_api_key
            self._runtime_karttapaikka_api_key = karttapaikka_api_key
            self._runtime_karttakuva_user = karttakuva_user
            self._runtime_karttakuva_pass = karttakuva_pass

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
            parameters[7].enabled = uses_mml
            parameters[8].enabled = uses_karttapaikka
            parameters[9].enabled = uses_karttakuva
            parameters[10].enabled = uses_karttakuva

            source_key = "{}|mml:{}|kartta:{}|kk:{}:{}".format(
                "|".join(source_values), self._norm(mml_api_key),
                self._norm(karttapaikka_api_key),
                self._norm(karttakuva_user), self._norm(karttakuva_pass)
            )
            if source_key not in self._all_wfs_layers_cache:
                fetch_error = None
                try:
                    self._all_wfs_layers_cache[source_key] = self._fetch_layer_list(source_values)
                except Exception as fe:
                    fetch_error = fe
                    self._all_wfs_layers_cache[source_key] = []
                if fetch_error:
                    self._warn("[VAROITUS] Tasojen haku epäonnistui ({}): {}".format(
                        ", ".join(source_values), fetch_error))

            filtered_layers = self._all_wfs_layers_cache.get(source_key, [])
            if layer_search.strip():
                q = self._norm(layer_search)
                filtered_layers = [x for x in filtered_layers if q in self._norm(x)]

            parameters[2].filter.list = filtered_layers if filtered_layers else ["(ei osumia – tyhjennä haku)"]

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
        source_values = self._parse_multivalue_param(parameters[0])
        if not source_values:
            source_values = ["Väylä"]
        selected_layers = self._parse_multivalue_param(parameters[2])

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
        if not layers_text:
            parameters[2].setErrorMessage("Valitse vähintään yksi ladattava taso.")
        else:
            parameters[2].clearMessage()

        needs_mml_key = False
        if selected_layers:
            for lbl in selected_layers:
                info = self._layer_mapping.get(lbl)
                if info and info.get("kind") == "mml_raster":
                    needs_mml_key = True
                    break
        if needs_mml_key and not mml_api_key.strip():
            parameters[7].setErrorMessage("MML rasteritasot vaativat API-avaimen.")
        else:
            parameters[7].clearMessage()

        needs_karttapaikka_key = False
        if selected_layers:
            for lbl in selected_layers:
                info = self._layer_mapping.get(lbl)
                if info and info.get("source") == "Karttapaikka":
                    needs_karttapaikka_key = True
                    break
        if not needs_karttapaikka_key and "Karttapaikka" in source_values:
            needs_karttapaikka_key = True

        if needs_karttapaikka_key and not karttapaikka_api_key.strip():
            parameters[8].setErrorMessage("Karttapaikka vaatii API-avaimen.")
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
        source_names = self._parse_multivalue_param(parameters[0])
        if not source_names:
            source_names = ["Väylä"]
        layers = (parameters[2].valueAsText.split(';') if parameters[2].valueAsText else [])
        extent_type = parameters[3].valueAsText
        extent_value_text = parameters[4].valueAsText
        custom_layer = parameters[5].valueAsText
        workspace = parameters[6].valueAsText
        mml_api_key = parameters[7].valueAsText or ""
        karttapaikka_api_key = parameters[8].valueAsText or ""
        karttakuva_user = (parameters[9].valueAsText or "").strip()
        karttakuva_pass = (parameters[10].valueAsText or "").strip()
        self._runtime_mml_api_key = mml_api_key.strip()
        self._runtime_karttapaikka_api_key = karttapaikka_api_key.strip()
        self._runtime_karttakuva_user = karttakuva_user
        self._runtime_karttakuva_pass = karttakuva_pass
        sel_vals = self._parse_multivalue(extent_value_text)

        if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"] and not sel_vals:
            self._error(f"[VIRHE] Aluerajauksen taso on '{extent_type}', mutta 'Valitse alue' on tyhjä.")
            raise arcpy.ExecuteError

        if extent_type == "Oma aineisto (Polygon/Polyline)" and (not custom_layer or str(custom_layer).strip() == ""):
            self._error("[VIRHE] Valitsit 'Oma aineisto', mutta rajausaineisto puuttuu.")
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
        self._fetch_layer_list(list(needed_sources))
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

        if any((self._lookup_layer_info(lbl.strip().strip("'").strip('"')) or {}).get("source") == "Karttapaikka" for lbl in layers):
            if not karttapaikka_api_key.strip():
                self._error("[VIRHE] Karttapaikka-lähde vaatii API-avaimen.")
                raise arcpy.ExecuteError

        all_layers_start = time.perf_counter()
        total_layers = len(layers)
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

            base_wfs = self._choose_wfs_endpoint(layer_clean, source_name)
            auth_headers = self._build_source_auth_headers(source_name)
            if layer_kind == "wfs":
                self._discover_wfs_schema(base_wfs, layer_clean, auth_headers)
            is_heavy = (layer_kind == "wfs" and source_name in self.heavy_chunk_sources and self._is_heavy_layer(layer_clean))
            temp_feature_classes = []
            used_kunta_chunks = False
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

            if layer_kind == "osm":
                self._msg("  [INFO] Haetaan OpenStreetMap-aineistoa: {}".format(layer_ui_name))
                try:
                    chunks, total_found, used_grid = self._fetch_osm_feature_chunks(layer_clean, boundary_fc)
                    temp_feature_classes.extend(chunks)
                except Exception as ex:
                    _record_layer_failure(layer_ui_name, ex)
                    continue
            elif layer_kind == "mml_raster":
                if not mml_api_key.strip():
                    _record_layer_failure(layer_ui_name, "MML-rasteritaso vaatii API-avaimen")
                    continue
                download_start = time.perf_counter()
                try:
                    out_tif = self._download_wms_geotiff(
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

                    def _record_wfs_stats(stats):
                        nonlocal layer_http_s, layer_gp_json_s
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
                    )
                    chunks, total_found, used_grid = resilience.execute_with_fallback(_fetch_bbox_once, bbox_str)
                    temp_feature_classes.extend(chunks)
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

            # Kuntakohtaisessa haussa vierekkäisten kuntien extent-bboxit menevät
            # päällekkäin, jolloin sama kohde voi tulla mukaan useasta ruudusta.
            # Poistetaan geometrialtaan identtiset duplikaatit ennen leikkausta.
            if (used_kunta_chunks or cql_split_effective) and created_merged:
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
                "requested_mode": requested_mode if layer_kind == "wfs" else layer_kind,
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
    def _fetch_layer_list(self, source_names=None):
        selected_sources = source_names or ["Väylä", "DigiRoad"]
        layer_list = self._get_layer_entries_for_sources(selected_sources)
        return layer_list


class MMLBasemapDownloader(VaylaWFSDownloader):
    def __init__(self):
        super().__init__()
        self.label = "Taustakartat (MML/Kapsi)"
        self.description = "Tuo MML- tai Kapsi-taustakarttoja live- tai rasterimuodossa."
        self.canRunInBackground = False
        # Kaikki MML-/WMS-attribuutit ja apumetodit peritään emoluokasta
        # (VaylaWFSDownloader); vain UI ja execute eroavat.

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
        p_mode.filter.list = ["Live WMTS", "Raster (GeoTIFF EPSG:3067)"]
        p_mode.value = "Live WMTS"

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

        p_api_key = arcpy.Parameter(
            displayName="MML API-avain (pakollinen vain MML rasterille)",
            name="api_key",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_api_key.value = self._get_saved_secret("mml_api_key")

        return [p_provider, p_map_search, p_map, p_mode, p_extent_type, p_extent_value, p_custom_layer, p_workspace, p_api_key]

    def updateParameters(self, parameters):
        try:
            provider = parameters[0].valueAsText or "MML"
            map_search = parameters[1].valueAsText or ""
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
            parameters[3].filter.list = mode_options
            if mode not in mode_options:
                parameters[3].value = mode_options[0]

            parameters[5].enabled = False
            parameters[6].enabled = False
            parameters[5].filter.list = []
            if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"]:
                parameters[5].enabled = True
                parameters[5].filter.list = self._get_extent_choices(extent_type)
            elif extent_type == "Oma aineisto (Polygon/Polyline)":
                parameters[6].enabled = True
                parameters[5].value = None

            parameters[8].enabled = (provider == "MML" and (parameters[3].valueAsText or mode_options[0]) == "Raster (GeoTIFF EPSG:3067)")
        except Exception as e:
            self._warn(f"updateParameters epäonnistui: {e}")

    def updateMessages(self, parameters):
        provider = parameters[0].valueAsText or "MML"
        extent_type = parameters[4].valueAsText
        extent_value_text = parameters[5].valueAsText
        custom_layer = parameters[6].valueAsText
        mode = parameters[3].valueAsText or "Live WMTS"
        api_key = parameters[8].valueAsText or ""

        vals = self._parse_multivalue(extent_value_text)
        if extent_type in ["Kunta/Kaupunki", "Maakunta", "Elinvoimakeskus", "Hyvinvointialue"] and not vals:
            parameters[5].setErrorMessage("Valitse alue on pakollinen tälle aluerajauksen tasolle.")
        else:
            parameters[5].clearMessage()

        if extent_type == "Oma aineisto (Polygon/Polyline)" and (not custom_layer or str(custom_layer).strip() == ""):
            parameters[6].setErrorMessage("Valitse oma rajausaineisto.")
        else:
            parameters[6].clearMessage()

        if provider == "MML" and mode == "Raster (GeoTIFF EPSG:3067)" and not api_key.strip():
            parameters[8].setErrorMessage("Rasterilataus vaatii MML API-avaimen.")
        else:
            parameters[8].clearMessage()

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        self._msg("=== Taustakarttatyökalu käynnistyy ===")

        provider = parameters[0].valueAsText or "MML"
        map_display = parameters[2].valueAsText
        mode = parameters[3].valueAsText or "Live WMTS"
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

        if not map_display:
            raise Exception("Valitse taustakartta.")
        layer_id = self._get_basemap_layer_id(provider, map_display)
        boundary_fc = None
        if mode != "Live WMTS":
            if extent_type == "Oma aineisto (Polygon/Polyline)":
                boundary_fc = self._prepare_custom_boundary(custom_layer)
            else:
                boundary_fc = self._process_administrative_boundary(
                    extent_type, extent_vals, self._scratch_gdb()
                )

        try:
            if provider == "MML" and mode == "Live WMTS":
                wmts_creds = self._find_wmts_credentials_file()
                secured = None
                try:
                    if wmts_creds:
                        secured = arcpy.ImportCredentials([wmts_creds])
                    out_layer = f"wmts_{uuid.uuid4().hex[:8]}"
                    arcpy.management.MakeWMTSLayer(self.mml_wmts_capabilities, layer_id, out_layer)
                    tmp_lyrx = os.path.join(self._scratch_folder(), f"{out_layer}.lyrx")
                    arcpy.management.SaveToLayerFile(out_layer, tmp_lyrx, "ABSOLUTE")
                    self._add_to_map(tmp_lyrx)
                    self._safe_delete(out_layer)
                finally:
                    if secured:
                        try:
                            arcpy.ClearCredentials(secured)
                        except Exception:
                            pass
            elif provider == "MML":
                if not api_key.strip():
                    raise Exception("Rasterilataus vaatii MML API-avaimen.")
                out_tif = self._download_wms_geotiff(
                    layer_id, boundary_fc, self._scratch_folder(), api_key.strip()
                )
                final_tif = self._copy_raster_to_workspace(out_tif, workspace)
                self._add_to_map(final_tif)
                self._remove_local_output(out_tif)
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
