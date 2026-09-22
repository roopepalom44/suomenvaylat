import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import shutil
import tempfile
import time
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fake_http


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "Toolboxes" / "VaylaWFSDownloader.pyt"


def load_toolbox_module():
    old_arcpy = sys.modules.get("arcpy")
    sys.modules["arcpy"] = types.ModuleType("arcpy")
    try:
        loader = importlib.machinery.SourceFileLoader("vayla_toolbox_test", str(TOOLBOX))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        if old_arcpy is None:
            sys.modules.pop("arcpy", None)
        else:
            sys.modules["arcpy"] = old_arcpy


MODULE = load_toolbox_module()


class ToolboxHelperTests(unittest.TestCase):
    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._wfs_geometry_field_cache = {}
        self.tool._wfs_sort_candidate_cache = {}
        self.tool._wfs_sort_field_cache = {}
        self.tool._verbose_diagnostics = False
        self.tool._runtime_aino_token = ""

    def test_sanitized_url_drops_credentials_query_and_fragment(self):
        value = self.tool._sanitize_url(
            "https://user:password@example.test:8443/wfs?api_key=secret#token"
        )
        self.assertEqual("https://example.test:8443/wfs", value)
        self.assertNotIn("secret", value)
        self.assertNotIn("password", value)

    def test_runtime_secrets_are_redacted_from_log_text(self):
        self.tool._runtime_mml_api_key = "key-value"
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_aino_token = "aino-value"
        self.tool._runtime_karttakuva_user = "user-value"
        self.tool._runtime_karttakuva_pass = "pass-value"
        value = self.tool._redact_secrets(
            "key-value aino-value user-value pass-value"
        )
        self.assertEqual(
            "[PIILOTETTU] [PIILOTETTU] [PIILOTETTU] [PIILOTETTU]",
            value,
        )

    def test_aino_registry_uses_clean_endpoint_and_requires_runtime_token(self):
        registry = MODULE.WFSSourceRegistry()
        endpoint = registry.get_endpoint("Aino")
        self.assertEqual("https://aino.sitowise.com/ows", endpoint)
        self.assertNotIn("token", endpoint)
        self.assertIn("Aino", registry.get_sources_list())

        self.tool.wfs_registry = registry
        self.tool._runtime_aino_token = "CaseSensitive+Aino/Token"
        secured = self.tool._source_endpoint_with_credentials("Aino", endpoint)
        query = MODULE.urllib.parse.parse_qs(
            MODULE.urllib.parse.urlsplit(secured).query
        )
        self.assertEqual(["CaseSensitive+Aino/Token"], query["token"])
        self.assertEqual("https://aino.sitowise.com/ows", self.tool._sanitize_url(secured))
        self.assertNotIn(
            "CaseSensitive%2BAino%2FToken",
            self.tool._redact_secrets(secured),
        )
        self.assertIn("[PIILOTETTU]", self.tool._redact_secrets(secured))

    def test_aino_layer_discovery_combines_wfs_and_wms_without_storing_token(self):
        self.tool.wfs_registry = MODULE.WFSSourceRegistry()
        self.tool._runtime_aino_token = "aino-secret"
        captured = {}

        def fake_capabilities(endpoint, headers=None):
            captured["wfs_endpoint"] = endpoint
            return [{"id": "aluejaot:test", "title": "Testitaso", "kind": "wfs"}]

        def fake_wms_capabilities(endpoint, headers=None):
            captured["wms_endpoint"] = endpoint
            return [{
                "id": "taustakartat:test",
                "title": "Testitausta",
                "kind": "aino_wms",
                "wms_title": "Testitausta",
                "is_background": True,
            }]

        self.tool._fetch_wfs_capabilities_with_headers = fake_capabilities
        self.tool._fetch_wms_capabilities_with_headers = fake_wms_capabilities
        layers = self.tool._get_aino_layers()
        self.assertEqual("aluejaot:test", layers[0]["id"])
        self.assertEqual("Testitaso (WFS)", layers[0]["title"])
        self.assertEqual("taustakartat:test", layers[1]["id"])
        self.assertEqual("Testitausta (WMS)", layers[1]["title"])
        self.assertEqual("https://aino.sitowise.com/ows", layers[1]["endpoint"])
        for key in ("wfs_endpoint", "wms_endpoint"):
            self.assertEqual(
                ["aino-secret"],
                MODULE.urllib.parse.parse_qs(
                    MODULE.urllib.parse.urlsplit(captured[key]).query
                )["token"],
            )
        self.assertNotIn("aino-secret", repr(layers))

    def test_aino_wms_capabilities_returns_every_named_layer_and_background_flag(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            @staticmethod
            def read():
                return '''<WMS_Capabilities xmlns="http://www.opengis.net/wms">
                  <Capability><Layer><Title>Service</Title>
                    <Layer><Name>taustakartat:tausta</Name><Title>Taustakartta</Title></Layer>
                    <Layer><Title>Ryhmä</Title>
                      <Layer><Name>aineisto:kohteet</Name><Title>Kohteet</Title></Layer>
                    </Layer>
                  </Layer></Capability>
                </WMS_Capabilities>'''.encode("utf-8")

        calls = []
        original_open = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=60: (
            calls.append(request.full_url) or Response()
        )
        try:
            layers = self.tool._fetch_wms_capabilities_with_headers(
                "https://aino.sitowise.com/ows?token=secret"
            )
        finally:
            MODULE.urllib.request.urlopen = original_open

        self.assertEqual(
            ["taustakartat:tausta", "aineisto:kohteet"],
            [item["id"] for item in layers],
        )
        self.assertTrue(layers[0]["is_background"])
        self.assertFalse(layers[1]["is_background"])
        query = MODULE.urllib.parse.parse_qs(
            MODULE.urllib.parse.urlsplit(calls[0]).query
        )
        self.assertEqual(["WMS"], query["service"])
        self.assertEqual(["GetCapabilities"], query["request"])
        self.assertEqual(["1.3.0"], query["version"])

    def test_aino_wms_parser_has_no_layer_limit_at_175(self):
        named_layers = "".join(
            "<Layer><Name>aineisto:taso_{}</Name><Title>Taso {}</Title></Layer>".format(
                index, index
            )
            for index in range(175)
        )
        xml = (
            '<WMS_Capabilities xmlns="http://www.opengis.net/wms">'
            "<Capability><Layer><Title>Service</Title>{}</Layer></Capability>"
            "</WMS_Capabilities>"
        ).format(named_layers).encode("utf-8")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return xml

        original_open = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=60: Response()
        try:
            layers = self.tool._fetch_wms_capabilities_with_headers(
                "https://aino.sitowise.com/ows?token=secret"
            )
        finally:
            MODULE.urllib.request.urlopen = original_open
        self.assertEqual(175, len(layers))
        self.assertEqual(175, len({item["id"] for item in layers}))

    def test_aino_token_is_hidden_parameter_appended_after_existing_parameters(self):
        class Filter:
            def __init__(self):
                self.type = None
                self.list = []

        class Parameter:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
                self.value = None
                self.values = None
                self.enabled = True
                self.filter = Filter()
                self.filters = [Filter()]

        sentinel = object()
        original_parameter = getattr(MODULE.arcpy, "Parameter", sentinel)
        MODULE.arcpy.Parameter = Parameter
        tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        tool.wfs_registry = MODULE.WFSSourceRegistry()
        tool._get_saved_secret = lambda name: (
            "saved-aino-token" if name == "aino_token" else ""
        )
        try:
            parameters = tool.getParameterInfo()
        finally:
            if original_parameter is sentinel:
                delattr(MODULE.arcpy, "Parameter")
            else:
                MODULE.arcpy.Parameter = original_parameter

        self.assertEqual(13, len(parameters))
        self.assertEqual("refresh_layer_catalog", parameters[11].name)
        self.assertEqual("aino_token", parameters[12].name)
        self.assertEqual("GPStringHidden", parameters[12].datatype)
        self.assertEqual("saved-aino-token", parameters[12].value)
        self.assertFalse(parameters[12].enabled)

    def test_layer_label_removes_redundant_digiroad_parenthesis(self):
        self.assertEqual(
            "Esterakenne - DigiRoad",
            self.tool._format_layer_label("Esterakenne (Digiroad)", "DigiRoad"),
        )

    def test_layer_placeholder_is_not_a_real_selection(self):
        self.assertTrue(self.tool._is_layer_placeholder("(ei osumia – tyhjennä haku)"))
        self.assertTrue(self.tool._is_layer_placeholder("(no matches – clear search)"))
        self.assertFalse(self.tool._is_layer_placeholder("tieviiva - Karttapaikka"))

    def test_source_selection_survives_empty_validation_value(self):
        class Param:
            def __init__(self, value=None, values=None, datatype=None):
                self.value = value
                self.values = values
                self.datatype = datatype

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in self.values
                    )
                return self.value

        param = Param(values=[["Karttapaikka"]], datatype="GPValueTable")
        self.assertEqual(["Karttapaikka"], self.tool._source_values_from_param(param))

        param.values = []
        param.value = None
        self.assertEqual(["Karttapaikka"], self.tool._source_values_from_param(param))
        self.assertEqual([["Karttapaikka"]], param.values)

    def test_karttapaikka_uses_current_mml_endpoints(self):
        registry = MODULE.WFSSourceRegistry()
        endpoints = registry.get_endpoints("Karttapaikka")
        self.assertTrue(endpoints)
        self.assertTrue(all("inspire-wfs.maanmittauslaitos.fi" in url for url in endpoints))
        self.assertIn(
            "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
            registry.get_ogc_endpoint("Karttapaikka"),
        )

    def test_mml_uses_current_property_ogc_endpoint(self):
        registry = MODULE.WFSSourceRegistry()
        self.assertEqual(
            [MODULE.MML_PROPERTY_OGC_API_ENDPOINT],
            registry.get_endpoints("MML"),
        )
        self.assertEqual(
            MODULE.MML_PROPERTY_OGC_API_ENDPOINT,
            registry.get_ogc_endpoint("MML"),
        )
        self.assertEqual(
            MODULE.MML_PROPERTY_VECTOR_TILE_TILEJSON,
            registry.get_source("MML")["vector_tile_endpoint"],
        )

    def test_mml_auth_uses_basic_header_without_putting_key_in_url(self):
        headers = self.tool._mml_auth_headers("CaseSensitive-Key")
        self.assertIn("Authorization", headers)
        self.assertNotIn("CaseSensitive-Key", headers["Authorization"])

    def test_karttapaikka_auth_is_only_sent_to_ogc_api(self):
        self.tool._runtime_karttapaikka_api_key = "test-key"
        self.assertEqual(
            {},
            self.tool._build_source_auth_headers("Karttapaikka", layer_kind="wfs"),
        )
        headers = self.tool._build_source_auth_headers(
            "Karttapaikka",
            endpoint="https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
            layer_kind="mml_ogcapi",
        )
        self.assertIn("Authorization", headers)
        self.assertNotIn("test-key", headers["Authorization"])

    def test_mml_property_auth_is_only_sent_to_ogc_api(self):
        self.tool._runtime_mml_api_key = "property-key"
        self.assertEqual(
            {},
            self.tool._build_source_auth_headers("MML", layer_kind="wfs"),
        )
        headers = self.tool._build_source_auth_headers(
            "MML",
            endpoint=MODULE.MML_PROPERTY_OGC_API_ENDPOINT,
            layer_kind="mml_property_ogcapi",
        )
        self.assertIn("Authorization", headers)
        self.assertNotIn("property-key", headers["Authorization"])

    def test_mml_property_collections_are_discovered_dynamically(self):
        self.tool.wfs_registry = MODULE.WFSSourceRegistry()
        self.tool._runtime_mml_api_key = "property-key"
        captured = {}

        def fake_fetch(url, timeout=60, quiet=False, extra_headers=None, timings=None):
            captured["url"] = url
            captured["headers"] = extra_headers
            return (
                {
                    "collections": [
                        {
                            "id": "KiinteistorajanSijaintitiedot",
                            "title": "Kiinteistörajan sijaintitiedot",
                        },
                        {
                            "id": "PalstanSijaintitiedot",
                            "title": "Palstan sijaintitiedot",
                        },
                    ]
                },
                "{}",
                200,
                "application/json",
            )

        self.tool._fetch_json = fake_fetch
        layers = self.tool._get_mml_ogc_layers()

        self.assertEqual(
            MODULE.MML_PROPERTY_OGC_API_ENDPOINT + "collections",
            captured["url"],
        )
        self.assertIn("Authorization", captured["headers"])
        self.assertEqual(
            ["KiinteistorajanSijaintitiedot", "PalstanSijaintitiedot"],
            [layer["id"] for layer in layers],
        )
        self.assertEqual("Kiinteistojaotus", layers[0]["title"])
        self.assertEqual("mml_property_ogcapi", layers[0]["kind"])

    def test_secret_cache_key_preserves_api_key_case_without_exposing_it(self):
        lower = self.tool._secret_cache_key("AbC-123")
        upper = self.tool._secret_cache_key("aBc-123")
        self.assertNotEqual(lower, upper)
        self.assertNotIn("AbC-123", lower)

    def test_ogc_pages_follow_next_link_and_keep_projection_stats(self):
        pages = [
            (
                {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": None, "properties": {}}],
                    "links": [{"rel": "next", "href": "/next?page=2"}],
                },
                '{"features":[{}]}',
            ),
            (
                {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": None, "properties": {}}],
                    "links": [],
                },
                '{"features":[{}]}',
            ),
        ]
        calls = []

        def fake_fetch(url, timeout=60, quiet=False, extra_headers=None, timings=None):
            calls.append(url)
            data, raw = pages.pop(0)
            return data, raw, 200, "application/geo+json"

        conversion = MODULE.PhaseMetrics()
        conversion.set("JSONToFeatures", 0.2)
        conversion.set("projektointi", 0.3)
        self.tool._fetch_json = fake_fetch
        self.tool._json_to_temp_fc = lambda raw, project_to_epsg=None: ("fc{}".format(len(calls)), conversion)
        self.tool._warn = lambda message: None
        chunks, found, stats = self.tool._fetch_ogcapi_feature_chunks(
            "https://example.test/features/v1/",
            "tieviiva",
            "23,61,24,62",
            1,
        )
        self.assertEqual(["fc1", "fc2"], chunks)
        self.assertEqual(2, found)
        self.assertEqual(2, stats["pages"])
        self.assertIn("/next?page=2", calls[1])
        self.assertAlmostEqual(0.4, stats["json_to_features_s"])
        self.assertAlmostEqual(0.6, stats["projection_s"])

    def test_empty_layer_list_refetches_after_api_key_change(self):
        class Filter:
            def __init__(self):
                self.list = []

        class Param:
            def __init__(self, value=None, values=None):
                self.value = value
                self.values = values
                self.filter = Filter()
                self.enabled = True
                self.errors = []

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(str(item[0] if isinstance(item, (list, tuple)) else item)
                                    for item in self.values)
                return self.value

            def setErrorMessage(self, message):
                self.errors.append(message)

            def clearMessage(self):
                self.errors = []

        self.tool._all_wfs_layers_cache = {}
        self.tool._layer_mapping = {}
        self.tool._runtime_mml_api_key = ""
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = ""
        self.tool._runtime_karttakuva_pass = ""
        calls = []

        def fake_fetch_layer_list(sources, cache_key=None, allow_disk_cache=True):
            calls.append(self.tool._runtime_karttapaikka_api_key)
            return (
                ["tieviiva (Maastotiedot) - Karttapaikka"]
                if self.tool._runtime_karttapaikka_api_key else []
            )

        self.tool._fetch_layer_list = fake_fetch_layer_list
        self.tool._get_extent_choices = lambda extent_type: []
        self.tool._warn = lambda message: None
        parameters = [
            Param(values=[["Karttapaikka"]]),
            Param(""),
            Param(value="(ei osumia – tyhjennä haku)", values=["(ei osumia – tyhjennä haku)"]),
            Param(None), Param(None), Param(None), Param(None),
            Param(""), Param(""), Param(""), Param(""),
        ]

        self.tool.updateParameters(parameters)
        self.assertEqual([], parameters[2].filter.list)
        self.assertEqual([], parameters[2].values)

        parameters[8].value = "new-key"
        self.tool.updateParameters(parameters)
        self.assertEqual(
            ["tieviiva (Maastotiedot) - Karttapaikka"],
            parameters[2].filter.list,
        )
        self.assertEqual(["", "new-key"], calls)

    def test_layer_selection_survives_filter_refresh(self):
        class Filter:
            def __init__(self, owner=None):
                self.owner = owner
                self._list = []
                self.assignments = 0

            @property
            def list(self):
                return self._list

            @list.setter
            def list(self, value):
                self._list = list(value)
                self.assignments += 1
                if self.owner is not None:
                    self.owner.values = []
                    self.owner.value = None

        class Param:
            def __init__(self, value=None, values=None, datatype=None, clears_on_filter=False):
                self.value = value
                self.values = values
                self.datatype = datatype
                self.filter = Filter(self if clears_on_filter else None)
                self.enabled = True

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in self.values
                    )
                return self.value

        layer_name = "Tiestötiedot - Väylä"
        parameters = [
            Param(values=[["Väylä"]], datatype="GPValueTable"),
            Param(""),
            Param(values=[layer_name], clears_on_filter=True),
            Param("Koko Suomi"), Param(None), Param(None), Param("C:\\output.gdb"),
            Param(""), Param(""), Param(""), Param(""),
        ]
        self.tool._all_wfs_layers_cache = {}
        self.tool._layer_mapping = {}
        self.tool._runtime_mml_api_key = ""
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = ""
        self.tool._runtime_karttakuva_pass = ""
        self.tool._fetch_layer_list = (
            lambda sources, cache_key=None, allow_disk_cache=True: [layer_name]
        )
        self.tool._get_extent_choices = lambda extent_type: []
        self.tool._warn = lambda message: None
        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

        parameters[8].value = "new-key"
        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

    def test_valid_layer_selection_survives_first_source_key_update(self):
        class Filter:
            def __init__(self):
                self.list = []

        class Param:
            def __init__(self, value=None, values=None):
                self.value = value
                self.values = values
                self.filter = Filter()
                self.enabled = True

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in self.values
                    )
                return self.value

        layer = "Peruskartta (peruskartta) - Kapsi"
        self.tool._all_wfs_layers_cache = {}
        self.tool._layer_mapping = {}
        self.tool._runtime_mml_api_key = ""
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = ""
        self.tool._runtime_karttakuva_pass = ""
        self.tool._fetch_layer_list = (
            lambda sources, cache_key=None, allow_disk_cache=True: [layer]
        )
        self.tool._get_extent_choices = lambda extent_type: []
        self.tool._warn = lambda message: None
        parameters = [
            Param(values=[["Kapsi"]]), Param(""), Param(value=layer, values=[layer]),
            Param(None), Param(None), Param(None), Param(None),
            Param(""), Param(""), Param(""), Param(""),
        ]

        self.tool.updateParameters(parameters)

        self.assertEqual([layer], parameters[2].values)
        self.assertEqual([layer], parameters[2].filter.list)

    def test_wmts_matrix_parser_and_selection_use_epsg3067(self):
        xml = b'''<?xml version="1.0"?>
        <Capabilities xmlns="http://www.opengis.net/wmts/1.0"
                      xmlns:ows="http://www.opengis.net/ows/1.1">
          <Contents>
            <Layer>
              <ows:Identifier>taustakartta</ows:Identifier>
              <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style>
              <Format>image/png</Format>
              <TileMatrixSetLink><TileMatrixSet>ETRS-TM35FIN</TileMatrixSet></TileMatrixSetLink>
            </Layer>
            <TileMatrixSet>
              <ows:Identifier>ETRS-TM35FIN</ows:Identifier>
              <ows:SupportedCRS>urn:ogc:def:crs:EPSG::3067</ows:SupportedCRS>
              <TileMatrix>
                <ows:Identifier>0</ows:Identifier>
                <ScaleDenominator>3571428.5714285714</ScaleDenominator>
                <TopLeftCorner>-548576 8388608</TopLeftCorner>
                <TileWidth>256</TileWidth><TileHeight>256</TileHeight>
                <MatrixWidth>8</MatrixWidth><MatrixHeight>8</MatrixHeight>
              </TileMatrix>
            </TileMatrixSet>
          </Contents>
        </Capabilities>'''
        spec = self.tool._parse_mml_wmts_layer(xml, "taustakartta")
        ext = types.SimpleNamespace(XMin=-500000, YMin=8100000, XMax=-300000, YMax=8300000)
        matrix, tile_range = self.tool._choose_mml_wmts_matrix(spec["matrices"], ext)

        self.assertEqual("ETRS-TM35FIN", spec["matrix_set"])
        self.assertEqual("image/png", spec["format"])
        self.assertEqual("0", matrix["id"])
        self.assertIsNotNone(tile_range)

    def test_mml_basemap_uses_current_vector_tile_services(self):
        self.assertEqual(
            "https://avoin-karttakuva.maanmittauslaitos.fi/"
            "vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/"
            "v21/ETRS-TM35FIN/tilejson.json",
            MODULE.MML_TOPO_VECTOR_TILE_TILEJSON,
        )
        self.assertEqual(
            MODULE.MML_PROPERTY_VECTOR_TILE_TILEJSON,
            MODULE.MML_VECTOR_TILE_LAYER_IDS["Kiinteistojaotus"],
        )

        basemap = MODULE.MMLBasemapDownloader.__new__(MODULE.MMLBasemapDownloader)
        basemap._mml_layer_mapping = {}
        self.assertEqual(
            ["Taustakartta", "Maastokartta", "Kiinteistojaotus"],
            basemap._get_basemap_layers_cached("MML"),
        )

    def test_mml_vector_tile_is_added_with_custom_api_key(self):
        class Layer:
            def __init__(self, name, group=False):
                self.name = name
                self.isGroupLayer = group
                self.visible = False

        class Map:
            def __init__(self):
                self.group = Layer("Taustakartta", group=True)
                self.vector = Layer("vector")
                self.calls = []

            def listLayers(self):
                return [self.group]

            def addDataFromPath(self, path, data_type=None, custom_parameters=None):
                self.calls.append((path, data_type, custom_parameters))
                return self.vector

            def addLayerToGroup(self, group, layer, position):
                self.calls.append(("group", layer, position))

        self.tool._runtime_map_loaded = True
        self.tool._runtime_map = Map()
        self.tool._msg = lambda message: None
        result = self.tool._add_mml_vector_tile_layer(
            MODULE.MML_PROPERTY_VECTOR_TILE_TILEJSON,
            "Kiinteistojaotus",
            "Secret-Key",
        )

        self.assertEqual("MML vector tile – Kiinteistojaotus", result["vector_tile"].name)
        self.assertTrue(result["vector_tile"].visible)
        self.assertIn(
            (
                MODULE.MML_PROPERTY_VECTOR_TILE_TILEJSON,
                "VECTOR_TILE",
                {"api-key": "Secret-Key"},
            ),
            self.tool._runtime_map.calls,
        )

    def test_aino_wms_adds_clean_service_and_selects_technical_sublayer(self):
        class CimNode:
            def __init__(self, service_id, name, children=None):
                self.serviceLayerID = service_id
                self.name = name
                self.subLayers = list(children or [])
                self.visibility = True

        selected = CimNode("taustakartat:tausta", "Taustakartta")
        other = CimNode("aineisto:muu", "Muu taso")
        group_node = CimNode("", "Palvelu", [selected, other])
        definition = types.SimpleNamespace(subLayers=[group_node])

        class Layer:
            def __init__(self, name, group=False):
                self.name = name
                self.isGroupLayer = group
                self.visible = False
                self.definition_set = False

            def getDefinition(self, version):
                self.requested_version = version
                return definition

            def setDefinition(self, value):
                self.definition_set = value is definition

        class Map:
            def __init__(self):
                self.group = Layer("Taustakartta", group=True)
                self.wms = Layer("service")
                self.calls = []

            def listLayers(self):
                return [self.group]

            def addDataFromPath(self, path, data_type=None, custom_parameters=None):
                self.calls.append((path, data_type, custom_parameters))
                return self.wms

            def addLayerToGroup(self, group, layer, position):
                self.calls.append(("group", group.name, layer, position))

        self.tool._runtime_map_loaded = True
        self.tool._runtime_map = Map()
        self.tool._msg = lambda message: None
        result = self.tool._add_aino_wms_layer(
            "https://aino.sitowise.com/ows?token=must-not-leak",
            "taustakartat:tausta",
            "Taustakartta",
            "runtime-secret",
            is_background=True,
        )

        self.assertEqual("Taustakartta", result["group"])
        self.assertTrue(selected.visibility)
        self.assertFalse(other.visibility)
        self.assertTrue(group_node.visibility)
        self.assertTrue(self.tool._runtime_map.wms.definition_set)
        self.assertEqual("Aino WMS – Taustakartta", result["wms"].name)
        self.assertIn(
            (
                "https://aino.sitowise.com/ows",
                "WMS",
                {"token": "runtime-secret"},
            ),
            self.tool._runtime_map.calls,
        )

    def test_aino_wms_removes_service_if_requested_sublayer_is_missing(self):
        class Layer:
            name = "service"
            visible = True

            @staticmethod
            def listLayers():
                return [types.SimpleNamespace(name="Muu taso", visible=True)]

        class Map:
            def __init__(self):
                self.layer = Layer()
                self.removed = []

            @staticmethod
            def listLayers():
                return []

            def createGroupLayer(self, name):
                return types.SimpleNamespace(name=name, isGroupLayer=True, visible=True)

            def addDataFromPath(self, path, data_type=None, custom_parameters=None):
                return self.layer

            def removeLayer(self, layer):
                self.removed.append(layer)

        self.tool._runtime_map_loaded = True
        self.tool._runtime_map = Map()
        self.tool._msg = lambda message: None
        with self.assertRaisesRegex(Exception, "ei löytynyt"):
            self.tool._add_aino_wms_layer(
                "https://aino.sitowise.com/ows",
                "aineisto:puuttuu",
                "Puuttuu",
                "runtime-secret",
            )
        self.assertEqual([self.tool._runtime_map.layer], self.tool._runtime_map.removed)

    def test_aino_background_group_is_moved_below_operational_layers(self):
        background = types.SimpleNamespace(name="Taustakartta", longName="Taustakartta")
        roads = types.SimpleNamespace(name="Tiet", longName="Tiet")

        class Map:
            def __init__(self):
                self.calls = []

            @staticmethod
            def listLayers():
                return [background, roads]

            def moveLayer(self, reference_layer, move_layer, position):
                self.calls.append((reference_layer, move_layer, position))

        active_map = Map()
        self.tool._move_group_to_map_bottom(active_map, background)
        self.assertEqual([(roads, background, "AFTER")], active_map.calls)

    def test_fixed_mml_wmts_range_and_tile_url(self):
        span = MODULE.MML_WMTS_TILE_SIZE * (2 ** (13 - 9))
        ext = types.SimpleNamespace(
            XMin=MODULE.MML_WMTS_ORIGIN_X + (2 * span) + 1,
            XMax=MODULE.MML_WMTS_ORIGIN_X + (2 * span) + 100,
            YMin=MODULE.MML_WMTS_ORIGIN_Y - (4 * span) + 100,
            YMax=MODULE.MML_WMTS_ORIGIN_Y - (3 * span) - 100,
        )
        self.assertEqual(
            (3, 3, 2, 2),
            self.tool._mml_wmts_tile_range(ext, 9),
        )
        self.assertEqual(16.0, self.tool._mml_wmts_resolution(9))

        self.tool.mml_wmts_base = MODULE.MML_WMTS_SERVICE_URL
        url = self.tool._mml_wmts_tile_url("taustakartta", 9, 3, 2, "A/B")
        self.assertEqual(
            "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0/"
            "taustakartta/default/ETRS-TM35FIN/9/3/2.png?api-key=A%2FB",
            url,
        )
        auth = self.tool._mml_auth_headers("A/B")["Authorization"]
        self.assertEqual("Basic " + MODULE.base64.b64encode(b"A/B:").decode("ascii"), auth)

    def test_mml_background_adds_wms_top_and_hides_local_raster(self):
        class Layer:
            def __init__(self, name, group=False):
                self.name = name
                self.isGroupLayer = group
                self.visible = True

        class Map:
            def __init__(self):
                self.group = Layer("Taustakartta", group=True)
                self.local = Layer("local")
                self.wms = Layer("wms")
                self.calls = []

            def listLayers(self):
                return [self.group]

            def addDataFromPath(self, path, data_type=None):
                self.calls.append((path, data_type))
                return [self.wms if data_type == "WMS" else self.local]

            def addLayerToGroup(self, group, layer, position):
                self.calls.append(("group", layer, position))

        self.tool._runtime_map_loaded = True
        self.tool._runtime_map = Map()
        self.tool.mml_wms_services = dict(MODULE.MML_WMS_SERVICE_URLS)
        self.tool._msg = lambda message: None
        self.tool._warn = lambda message: None
        result = self.tool._add_mml_background_layers(
            "C:/tmp/MML_RGB", "taustakartta", "Taustakartta"
        )
        self.assertTrue(result["wms_added"])
        self.assertFalse(result["local"].visible)
        self.assertTrue(result["wms"].visible)
        self.assertEqual("MML WMS – Taustakartta", result["wms"].name)
        self.assertIn(
            (MODULE.MML_WMS_SERVICE_URLS["taustakartta"], "WMS"),
            self.tool._runtime_map.calls,
        )

    def test_kapsi_uses_selected_scale_dependent_layer(self):
        self.assertEqual(
            "taustakartta_800k",
            self.tool._kapsi_request_layer(
                "https://tiles.kartat.kapsi.fi/taustakartta",
                "taustakartta_800k",
            ),
        )

    def test_kapsi_target_gsd_uses_capabilities_scale_range(self):
        layer_ref = (
            "https://tiles.kartat.kapsi.fi/taustakartta|taustakartta_800k"
        )
        self.tool._kapsi_layer_scale_ranges = {
            layer_ref: (500000.0, 1200000.0)
        }
        gsd, exact_scale = self.tool._kapsi_target_gsd(
            layer_ref, "taustakartta_800k"
        )
        self.assertTrue(exact_scale)
        self.assertAlmostEqual(
            (500000.0 * 1200000.0) ** 0.5 * (0.0254 / 72.0),
            gsd,
        )

    def test_kapsi_parent_layer_keeps_automatic_resolution(self):
        layer_ref = "https://tiles.kartat.kapsi.fi/taustakartta|taustakartta"
        self.tool._kapsi_layer_scale_ranges = {
            layer_ref: (None, 7000.0)
        }
        self.assertEqual(
            (20.0, False),
            self.tool._kapsi_target_gsd(layer_ref, "taustakartta"),
        )

    def test_kapsi_exact_scale_splits_excessive_tile_count_into_batches(self):
        batches = self.tool._kapsi_tile_batches(7, 7)
        batch_sizes = [
            (row_end - row_start) * (col_end - col_start)
            for row_start, row_end, col_start, col_end in batches
        ]
        self.assertEqual(49, sum(batch_sizes))
        self.assertEqual([21, 21, 7], batch_sizes)
        self.assertTrue(all(size <= 25 for size in batch_sizes))

    def test_kapsi_exact_single_tile_is_padded_to_supported_scale(self):
        axis_min, axis_max = self.tool._kapsi_exact_tile_axis_bounds(
            300000.0, 310000.0, 14200.0, 0, 1
        )
        self.assertAlmostEqual(297900.0, axis_min)
        self.assertAlmostEqual(312100.0, axis_max)
        self.assertAlmostEqual(14200.0, axis_max - axis_min)

    def test_kapsi_exact_last_tile_keeps_full_supported_span(self):
        first = self.tool._kapsi_exact_tile_axis_bounds(
            300000.0, 325000.0, 14200.0, 0, 2
        )
        last = self.tool._kapsi_exact_tile_axis_bounds(
            300000.0, 325000.0, 14200.0, 1, 2
        )
        self.assertEqual((300000.0, 314200.0), first)
        self.assertEqual((310800.0, 325000.0), last)

    def test_kapsi_exact_grid_bounds_include_padded_narrow_axis(self):
        extent = types.SimpleNamespace(
            XMin=1000.0, YMin=2000.0, XMax=1500.0, YMax=11000.0
        )
        self.assertEqual(
            (-1300.0, 2000.0, 3800.0, 11000.0),
            self.tool._kapsi_exact_grid_bounds(
                extent, tile_span=5100.0, cols=1, rows=2
            ),
        )

    def test_kapsi_capabilities_do_not_list_same_layer_reference_twice(self):
        class Registry:
            def get_endpoints(self, source_name):
                self.assertEqual("Kapsi", source_name)
                return [
                    "https://example.test/ortokuva?SERVICE=WMS&REQUEST=GetCapabilities"
                ]

            def assertEqual(self, expected, actual):
                assert expected == actual

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"""<WMS_Capabilities>
                    <Capability><Layer>
                        <Name>ortokuva</Name>
                        <Title>Maanmittauslaitoksen ortokuvat</Title>
                        <Layer>
                            <Name>ortokuva</Name>
                            <Title>ortokuva</Title>
                        </Layer>
                        <Layer>
                            <Name>ortokuva_overlay</Name>
                            <Title>ortokuva</Title>
                        </Layer>
                    </Layer></Capability>
                </WMS_Capabilities>"""

        self.tool.wfs_registry = Registry()
        self.tool._kapsi_layer_mapping = {}
        self.tool._kapsi_layer_scale_ranges = {}
        self.tool._warn = lambda message: None
        original_open = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=60: Response()
        try:
            layers = self.tool._fetch_kapsi_layer_list()
        finally:
            MODULE.urllib.request.urlopen = original_open

        references = [self.tool._kapsi_layer_mapping[layer] for layer in layers]
        self.assertEqual(len(references), len(set(references)))
        self.assertEqual(2, len(layers))

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_dpapi_secret_round_trip_is_not_plaintext(self):
        encrypted = self.tool._protect_secret("test-secret")
        self.assertTrue(encrypted.startswith("dpapi:"))
        self.assertNotIn("test-secret", encrypted)
        self.assertEqual("test-secret", self.tool._unprotect_secret(encrypted))

    def test_phase_metrics_distinguish_skipped_from_zero(self):
        metrics = MODULE.PhaseMetrics()
        metrics.set("fast", 0.0)
        metrics.skip("unused", "ei tarpeen")
        self.assertEqual(0.0, metrics.get("fast"))
        self.assertIsNone(metrics.get("unused"))
        self.assertEqual("ei tarpeen", metrics.status["unused"])

    def test_existing_wkt_2d_conversion_removes_z_dimension(self):
        source = "POLYGON Z ((1 2 3, 4 5 6, 1 2 3))"
        self.assertEqual(
            "POLYGON ((1 2, 4 5, 1 2))",
            self.tool._wkt_force_2d(source),
        )

    def test_cql_get_url_keeps_intersects_and_encodes_spaces(self):
        cql = "INTERSECTS(geom, POLYGON ((1 2, 3 4, 1 2)))"
        url = self.tool._build_wfs_getfeature_url(
            "https://example.test/wfs", "digiroad:test", 5000, 0,
            "application/json", cql_filter=cql, geometry_only=False,
        )
        self.assertIn("CQL_FILTER=INTERSECTS", url)
        self.assertNotIn(" ", url)
        self.assertNotIn("bbox=", url)

    def test_aino_getfeature_uses_wfs_11_parameters_and_preserves_token(self):
        base = "https://aino.sitowise.com/ows?token=aino-secret"
        url = self.tool._build_wfs_getfeature_url(
            base, "aluejaot:test", 5000, 5000,
            "application/json", bbox_str="1,2,3,4", geometry_only=False,
        )
        query = MODULE.urllib.parse.parse_qs(
            MODULE.urllib.parse.urlsplit(url).query
        )
        self.assertEqual(["aino-secret"], query["token"])
        self.assertEqual(["1.1.0"], query["version"])
        self.assertEqual(["aluejaot:test"], query["typeName"])
        self.assertEqual(["5000"], query["maxFeatures"])
        self.assertEqual(["5000"], query["startIndex"])
        self.assertNotIn("typeNames", query)
        self.assertNotIn("count", query)

        form = self.tool._wfs_getfeature_form(
            "aluejaot:test", 5000, 0, "application/json",
            bbox_str="1,2,3,4", geometry_only=False, wfs_version="1.1.0",
        )
        self.assertEqual("aluejaot:test", form["typeName"])
        self.assertEqual("5000", form["maxFeatures"])
        self.assertNotIn("typeNames", form)
        self.assertNotIn("count", form)

    def test_remote_output_name_uses_run_id_without_exists_loop(self):
        self.tool._runtime_workspace = None
        self.tool._runtime_workspace_is_folder = None
        self.tool._run_scratch_gdb = None
        self.tool._run_id = "abcdef12"
        name = self.tool._unique_output_name(
            "Kaiteet", r"\\server\share\results.gdb"
        )
        self.assertEqual("Kaiteet_abcdef12", name)

    def test_removed_execution_checkboxes_are_not_in_toolbox(self):
        source = TOOLBOX.read_text(encoding="utf-8")
        for parameter_name in (
            "keep_scratch_on_error", "clip_cql_results", "benchmark_copy"
        ):
            self.assertNotIn(parameter_name, source)

    def test_osm_branch_defines_mode_for_common_staging_summary(self):
        source = TOOLBOX.read_text(encoding="utf-8")
        osm_branch = source.index('if layer_kind == "osm":')
        osm_branch_end = source.index('elif layer_kind == "mml_raster":', osm_branch)
        self.assertIn(
            'requested_mode = "OpenStreetMap Overpass API + paikallinen Clip"',
            source[osm_branch:osm_branch_end],
        )

    def test_osm_catalog_contains_geofabrik_poi_points_layer(self):
        registry = MODULE.WFSSourceRegistry()
        endpoints = registry.get_endpoints("OpenStreetMap")
        self.assertGreaterEqual(len(endpoints), 2)
        self.assertTrue(all(url.startswith("https://") for url in endpoints))

        layers = MODULE.OverpassAdapter.get_layers()
        poi = next(
            layer for layer in layers
            if layer["id"] == MODULE.GeofabrikPOIAdapter.LAYER_ID
        )
        self.assertEqual("POI-pisteet", poi["title"])
        self.assertEqual("OpenStreetMap", poi["source"])
        self.assertEqual("osm", poi["kind"])

    def test_overpass_connection_falls_back_to_second_endpoint(self):
        class Registry:
            @staticmethod
            def get_endpoints(source_name):
                assert source_name == "OpenStreetMap"
                return ["https://first.example/api", "https://second.example/api"]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            @staticmethod
            def read():
                return b'{"elements": [{"type": "node", "id": 1}]}'

        calls = []

        def fake_open(request, timeout=0):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise MODULE.urllib.error.URLError("primary unavailable")
            return Response()

        self.tool.wfs_registry = Registry()
        original_open = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = fake_open
        try:
            result = self.tool._fetch_overpass_json("[out:json];node(1);out;")
        finally:
            MODULE.urllib.request.urlopen = original_open

        self.assertEqual({"elements": [{"type": "node", "id": 1}]}, result)
        self.assertEqual(
            [
                ("https://first.example/api", 150),
                ("https://second.example/api", 150),
            ],
            calls,
        )

    def test_overpass_remark_is_an_error_and_uses_fallback_endpoint(self):
        class Registry:
            @staticmethod
            def get_endpoints(source_name):
                assert source_name == "OpenStreetMap"
                return ["https://first.example/api", "https://second.example/api"]

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return self.body

        responses = [
            b'{"remark": "runtime error: Query timed out", "elements": []}',
            b'{"elements": [{"type": "node", "id": 2}]}',
        ]

        def fake_open(request, timeout=0):
            return Response(responses.pop(0))

        self.tool.wfs_registry = Registry()
        original_open = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = fake_open
        try:
            result = self.tool._fetch_overpass_json("[out:json];node(1);out;")
        finally:
            MODULE.urllib.request.urlopen = original_open

        self.assertEqual([{"type": "node", "id": 2}], result["elements"])
        self.assertEqual([], responses)

    def test_poi_geojson_conversion_explicitly_uses_point_geometry(self):
        source = TOOLBOX.read_text(encoding="utf-8")
        self.assertIn(
            'arcpy.conversion.JSONToFeatures(temp_json_path, temp_fc, "POINT")',
            source,
        )
        self.assertIn("if converted_count == 0:", source)

    def test_geofabrik_poi_query_combines_point_and_area_osm_objects(self):
        query = MODULE.GeofabrikPOIAdapter.build_query("60,24,61,25")
        self.assertIn('[out:json][timeout:120]', query)
        self.assertIn('nwr["amenity"]', query)
        self.assertIn('nwr["shop"]', query)
        self.assertIn('nwr["tourism"]', query)
        self.assertIn('(60,24,61,25)', query)
        self.assertTrue(query.endswith("out center;"))

    def test_geofabrik_poi_classification_keeps_multiple_classes(self):
        classes = MODULE.GeofabrikPOIAdapter.classify({
            "amenity": "restaurant",
            "tourism": "hotel",
            "name": "Testikohde",
        })
        self.assertEqual(
            [(2301, "restaurant"), (2401, "hotel")],
            classes,
        )
        self.assertEqual(
            [(2031, "recycling_glass")],
            MODULE.GeofabrikPOIAdapter.classify({
                "amenity": "recycling", "recycling:glass": "yes"
            }),
        )

    def test_geofabrik_poi_catalog_has_all_mapped_feature_classes(self):
        classes = {
            (code, fclass)
            for _, _, code, fclass in MODULE.GeofabrikPOIAdapter.TAG_CLASSES
        }
        classes.update({
            (2017, "consulate"),
            (2030, "recycling"),
            (2031, "recycling_glass"),
            (2032, "recycling_paper"),
            (2033, "recycling_clothes"),
            (2034, "recycling_metal"),
            (2590, "vending_machine"),
            (2592, "vending_parking"),
            (2950, "tower"),
            (2951, "comms_tower"),
            (2953, "observation_tower"),
        })
        # Liitteen 141 luokkaa sekä kaksi määrittelyn mukaista luokkaa,
        # joilla ei ollut Suomen otoksessa yhtään pistettä.
        self.assertEqual(143, len(classes))
        self.assertEqual(
            [(2953, "observation_tower")],
            MODULE.GeofabrikPOIAdapter.classify({
                "man_made": "tower", "tower:type": "observation"
            }),
        )

    def test_geofabrik_poi_geojson_outputs_nodes_and_area_centres_as_points(self):
        result = MODULE.GeofabrikPOIAdapter.to_geojson({
            "elements": [
                {
                    "type": "node", "id": 10, "lat": 60.1, "lon": 24.1,
                    "tags": {"amenity": "cafe", "name": "Kahvila"},
                },
                {
                    "type": "way", "id": 20,
                    "center": {"lat": 60.2, "lon": 24.2},
                    "tags": {"amenity": "restaurant", "tourism": "attraction"},
                },
            ]
        })
        self.assertEqual(3, len(result["features"]))
        self.assertTrue(all(
            feature["geometry"]["type"] == "Point"
            for feature in result["features"]
        ))
        way_features = [
            feature for feature in result["features"]
            if feature["properties"]["osm_type"] == "way"
        ]
        self.assertEqual(
            {"restaurant", "attraction"},
            {feature["properties"]["fclass"] for feature in way_features},
        )
        self.assertEqual([24.2, 60.2], way_features[0]["geometry"]["coordinates"])

    def test_fetch_json_records_network_read_and_parse_separately(self):
        transport = fake_http.FakeTransport([
            fake_http.FakeResponse({"features": []}, read_seconds=0.01)
        ])
        self.tool._http_transport = transport
        timings = MODULE.PhaseMetrics()
        data, raw, status, content_type = self.tool._fetch_json(
            "https://example.test/wfs", timings=timings
        )

        self.assertEqual({"features": []}, data)
        self.assertEqual(200, status)
        self.assertIn("application/json", content_type)
        for phase in (
            "requestin muodostaminen", "verkkopyyntö",
            "vastauksen lukeminen", "JSON-jäsennys",
        ):
            self.assertIsInstance(timings.get(phase), float)
            self.assertGreaterEqual(timings.get(phase), 0.0)

    def test_cql_rejection_does_not_fall_back_when_caller_will_split(self):
        self.tool.heavy_chunk_sources = ["Väylä"]
        self.tool._warn = lambda message: None
        self.tool._msg = lambda message: None
        self.tool._fetch_wfs_page = lambda **kwargs: (
            None, "request rejected", 414, "text/html"
        )
        with self.assertRaises(MODULE.CQLRequestRejected) as raised:
            self.tool._fetch_bbox_feature_chunks(
                "https://example.test/wfs", "digiroad:test", "1,2,3,4",
                ["application/json"], 5000, boundary_wkt="POLYGON ((0 0, 1 0, 0 0))",
                source_name="Väylä", allow_bbox_fallback=False,
            )
        self.assertEqual("CQL", raised.exception.stats["mode"])

    def test_bbox_page_stats_are_kept_but_default_log_is_concise(self):
        self.tool.heavy_chunk_sources = ["Väylä"]
        self.tool._msg_lines = []
        self.tool._msg = self.tool._msg_lines.append
        self.tool._warn = lambda message: None
        self.tool._fetch_wfs_page = lambda **kwargs: (
            {"features": [{"type": "Feature", "geometry": None, "properties": {}}]},
            '{"features":[{}]}', 200, "application/json",
        )
        conversion = MODULE.PhaseMetrics()
        conversion.set("JSONToFeatures", 0.125)
        self.tool._json_to_temp_fc = lambda raw, project_to_epsg=None: (
            "temporary_fc", conversion
        )
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 5000, source_name="Liiteri",
        )
        self.assertEqual(["temporary_fc"], chunks)
        self.assertEqual(1, found)
        self.assertEqual(1, requests)
        self.assertFalse(cql_ok)
        self.assertAlmostEqual(0.125, stats["json_to_features_s"])
        self.assertFalse(any("sivu yhteensä" in line for line in self.tool._msg_lines))

    def test_describe_feature_type_finds_geometry_and_sort_candidate(self):
        schema = b'''<?xml version="1.0"?>
        <xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                    xmlns:gml="http://www.opengis.net/gml/3.2">
          <xsd:element name="objectid" type="xsd:int"/>
          <xsd:element name="geom" type="gml:MultiSurfacePropertyType"/>
        </xsd:schema>'''

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return schema

        original = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=30: Response()
        try:
            self.tool._discover_wfs_schema(
                "https://example.test/wfs", "test:polygon"
            )
        finally:
            MODULE.urllib.request.urlopen = original

        self.assertEqual("geom", self.tool._get_wfs_geometry_field("test:polygon"))
        self.assertEqual("objectid", self.tool._wfs_sort_candidate_cache["test:polygon"])

    def test_explicit_sort_is_added_for_wfs_without_primary_key(self):
        self.tool._wfs_sort_field_cache["liiteri:test"] = "objectid"
        url = self.tool._build_wfs_getfeature_url(
            "https://example.test/wfs", "liiteri:test", 5000, 5000,
            "application/json", bbox_str="1,2,3,4", geometry_only=False,
        )
        self.assertIn("sortBy=objectid", url)

    def test_kapsi_retries_incomplete_jpeg(self):
        class Response:
            headers = {"Content-Type": "image/jpeg"}

            def __init__(self, fail=False):
                self.fail = fail

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if self.fail:
                    raise MODULE.http.client.IncompleteRead(b"\xff\xd8partial")
                return b"\xff\xd8complete\xff\xd9"

        responses = iter([Response(True), Response(False)])
        original_open = MODULE.urllib.request.urlopen
        original_sleep = MODULE.time.sleep
        MODULE.urllib.request.urlopen = lambda request, timeout=180: next(responses)
        MODULE.time.sleep = lambda seconds: None
        try:
            raw = self.tool._download_kapsi_image_bytes("https://example.test/map")
        finally:
            MODULE.urllib.request.urlopen = original_open
            MODULE.time.sleep = original_sleep
        self.assertEqual(b"\xff\xd8complete\xff\xd9", raw)

    def test_osm_unknown_geojson_crs_is_defined_as_wgs84(self):
        original_describe = getattr(MODULE.arcpy, "Describe", None)
        original_management = getattr(MODULE.arcpy, "management", None)
        calls = []

        class UnknownSpatialReference:
            factoryCode = 0

        MODULE.arcpy.Describe = lambda path: types.SimpleNamespace(
            spatialReference=UnknownSpatialReference()
        )
        MODULE.arcpy.SpatialReference = lambda code: code
        MODULE.arcpy.management = types.SimpleNamespace(
            DefineProjection=lambda path, sr: calls.append((path, sr))
        )
        try:
            defined = self.tool._define_osm_source_projection("osm_fc")
        finally:
            if original_describe is None:
                del MODULE.arcpy.Describe
            else:
                MODULE.arcpy.Describe = original_describe
            if original_management is None:
                del MODULE.arcpy.management
            else:
                MODULE.arcpy.management = original_management

        self.assertTrue(defined)
        self.assertEqual([("osm_fc", 4326)], calls)


class NetworkPathTests(unittest.TestCase):
    """Sivutus, uudelleenyritys ja varareitit vale-HTTP-kerroksen päällä."""

    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._wfs_geometry_field_cache = {}
        self.tool._wfs_sort_candidate_cache = {}
        self.tool._wfs_sort_field_cache = {}
        self.tool._wfs_output_format_cache = {}
        self.tool._verbose_diagnostics = False
        self.tool._http_max_attempts = 3
        self.tool._page_workers = 1
        self.tool._json_batch_pages = 1
        self.messages = []
        self.warnings = []
        self.tool._msg = self.messages.append
        self.tool._warn = self.warnings.append
        self.tool.heavy_chunk_sources = []
        self.converted = []

        def fake_pages_to_fc(pages, project_to_epsg=None):
            merged = MODULE.VaylaWFSDownloader._merge_feature_pages(pages)
            if not merged or not merged.get("features"):
                return None, MODULE.PhaseMetrics()
            self.converted.append(len(merged["features"]))
            timings = MODULE.PhaseMetrics()
            timings.set("JSONToFeatures", 0.01)
            return "fc_{}".format(len(self.converted)), timings

        self.tool._pages_to_temp_fc = fake_pages_to_fc

    def _install(self, transport):
        self.tool._http_transport = transport
        return transport

    def test_sleeps_are_skipped(self):
        # Testit eivät saa nukkua oikeita backoff-viiveitä.
        self.assertTrue(hasattr(self.tool, "_retry_delay"))

    def test_retries_transient_network_error_then_succeeds(self):
        self.tool._retry_delay = lambda attempt, retry_after=None: 0.0
        transport = self._install(fake_http.FakeTransport([
            fake_http.FakeResponse(error=TimeoutError("timeout")),
            fake_http.FakeResponse({"features": []}),
        ]))
        data, raw, status, ctype = self.tool._fetch_json("https://example.test/wfs")
        self.assertEqual({"features": []}, data)
        self.assertEqual(2, transport.call_count)
        self.assertTrue(any("Verkkopyyntö epäonnistui" in w for w in self.warnings))

    def test_retries_server_error_status(self):
        self.tool._retry_delay = lambda attempt, retry_after=None: 0.0
        transport = self._install(fake_http.FakeTransport([
            fake_http.FakeResponse("gateway down", status=502, content_type="text/html"),
            fake_http.FakeResponse({"features": []}),
        ]))
        data, _, status, _ = self.tool._fetch_json("https://example.test/wfs")
        self.assertEqual({"features": []}, data)
        self.assertEqual(200, status)
        self.assertEqual(2, transport.call_count)

    def test_client_error_is_not_retried(self):
        self.tool._retry_delay = lambda attempt, retry_after=None: 0.0
        transport = self._install(fake_http.FakeTransport([
            fake_http.FakeResponse("request too long", status=414, content_type="text/html"),
        ]))
        data, _, status, _ = self.tool._fetch_json("https://example.test/wfs")
        self.assertIsNone(data)
        self.assertEqual(414, status)
        self.assertEqual(1, transport.call_count)

    def test_gives_up_after_max_attempts(self):
        self.tool._retry_delay = lambda attempt, retry_after=None: 0.0
        transport = self._install(fake_http.FakeTransport([
            fake_http.FakeResponse(error=TimeoutError("t1")),
            fake_http.FakeResponse(error=TimeoutError("t2")),
            fake_http.FakeResponse(error=TimeoutError("t3")),
        ]))
        data, raw, status, _ = self.tool._fetch_json("https://example.test/wfs")
        self.assertIsNone(data)
        self.assertEqual(3, transport.call_count)

    def test_serial_pagination_walks_start_index_to_the_end(self):
        transport = self._install(fake_http.FakeTransport(
            handler=fake_http.paged_handler(total_features=250, page_size=100)
        ))
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, source_name="Liiteri",
        )
        self.assertEqual(250, found)
        self.assertEqual(3, requests)
        self.assertEqual([0, 100, 200], transport.start_indexes())
        self.assertFalse(stats["truncated"])

    def test_parallel_prefetch_returns_same_features_as_serial(self):
        self.tool._page_workers = 4
        transport = self._install(fake_http.FakeTransport(
            handler=fake_http.paged_handler(total_features=250, page_size=100)
        ))
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, source_name="Liiteri",
        )
        self.assertEqual(250, found)
        # Kaikki sivut haettiin, eika yhtaan startIndexia haettu kahdesti.
        indexes = transport.start_indexes()
        self.assertEqual(len(indexes), len(set(indexes)))
        self.assertTrue({0, 100, 200}.issubset(set(indexes)))

    def test_prefetch_wave_is_capped_by_number_matched(self):
        self.tool._page_workers = 4

        def handler(url, method, headers, body):
            import urllib.parse as up
            query = dict(up.parse_qsl(up.urlsplit(url).query))
            start = int(query.get("startIndex", 0))
            count = min(100, max(0, 250 - start))
            payload = fake_http.feature_page(count, start)
            payload["numberMatched"] = 250
            return fake_http.FakeResponse(payload)

        transport = self._install(fake_http.FakeTransport(handler=handler))
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, source_name="Liiteri",
        )
        self.assertEqual(250, found)
        # numberMatched=250 -> hannasta ei haeta tyhjia sivuja.
        self.assertEqual([0, 100, 200], sorted(transport.start_indexes()))

    def test_batched_conversion_makes_one_call_per_batch(self):
        self.tool._json_batch_pages = 3
        self._install(fake_http.FakeTransport(
            handler=fake_http.paged_handler(total_features=500, page_size=100)
        ))
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, source_name="Liiteri",
        )
        self.assertEqual(500, found)
        # 5 taydellista sivua + 1 tyhja: erat 3 + 2 = kaksi muunnosta.
        self.assertEqual([300, 200], self.converted)
        self.assertEqual(2, len(chunks))

    def test_merge_feature_pages_keeps_first_page_members(self):
        merged = MODULE.VaylaWFSDownloader._merge_feature_pages([
            {"type": "FeatureCollection", "crs": "EPSG:3067",
             "geometry_name": "geom", "numberReturned": 2,
             "features": [{"id": 1}, {"id": 2}]},
            {"type": "FeatureCollection", "features": [{"id": 3}]},
        ])
        self.assertEqual("EPSG:3067", merged["crs"])
        self.assertEqual("geom", merged["geometry_name"])
        self.assertNotIn("numberReturned", merged)
        self.assertEqual([1, 2, 3], [f["id"] for f in merged["features"]])

    def test_truncation_is_reported_in_stats(self):
        self._install(fake_http.FakeTransport(
            handler=fake_http.paged_handler(total_features=10000, page_size=100)
        ))
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, max_requests=4, source_name="Liiteri",
        )
        self.assertTrue(stats["truncated"])
        self.assertEqual(400, found)



class LayerCatalogCacheTests(unittest.TestCase):
    """Tasolistauksen levyvälimuisti."""

    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._layer_mapping = {}
        self.tool._layer_cache_ttl_s = 86400
        self.tmpdir = tempfile.mkdtemp()
        self.cache_path = os.path.join(self.tmpdir, "catalog.json")
        self.tool._layer_cache_file = lambda: self.cache_path
        self.fetch_calls = []

        def fake_entries(sources):
            self.fetch_calls.append(list(sources))
            self.tool._layer_mapping = {"Taso - Vayla": {"id": "vayla:taso"}}
            return ["Taso - Vayla"]

        self.tool._get_layer_entries_for_sources = fake_entries

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_second_call_is_served_from_disk_without_network(self):
        first = self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        self.assertEqual(["Taso - Vayla"], first)
        self.assertEqual(1, len(self.fetch_calls))

        # Uusi instanssi = uusi ArcGIS Pro -dialogin avaus.
        fresh = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        fresh._layer_mapping = {}
        fresh._layer_cache_ttl_s = 86400
        fresh._layer_cache_file = lambda: self.cache_path
        fresh._get_layer_entries_for_sources = lambda sources: self.fail(
            "levyvalimuistin pitaisi estaa verkkohaku"
        )
        second = fresh._fetch_layer_list(["Vayla"], cache_key="k1")
        self.assertEqual(["Taso - Vayla"], second)
        self.assertEqual({"Taso - Vayla": {"id": "vayla:taso"}}, fresh._layer_mapping)

    def test_expired_cache_is_refetched(self):
        self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        old = time.time() - 10
        os.utime(self.cache_path, (old, old))
        self.tool._layer_cache_ttl_s = 1
        self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        self.assertEqual(2, len(self.fetch_calls))

    def test_refresh_bypass_skips_disk_cache(self):
        self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        self.tool._fetch_layer_list(["Vayla"], cache_key="k1", allow_disk_cache=False)
        self.assertEqual(2, len(self.fetch_calls))

    def test_different_key_does_not_collide(self):
        self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        self.tool._fetch_layer_list(["Digiroad"], cache_key="k2")
        self.assertEqual(2, len(self.fetch_calls))
        self.assertEqual(
            ["Taso - Vayla"], self.tool._fetch_layer_list(["Vayla"], cache_key="k1")
        )
        self.assertEqual(2, len(self.fetch_calls))


if __name__ == "__main__":
    unittest.main()
