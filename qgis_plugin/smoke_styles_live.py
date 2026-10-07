"""Resolve actual WFS-qualified names to provider SLDs; no credentials needed."""
import os
from pathlib import Path
import sys
import tempfile
from qgis.core import QgsApplication

profile = tempfile.mkdtemp(prefix="suomenvaylat_styles_live_")
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = profile
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis.services import catalog, _style_fetch  # noqa: E402
from suomenvaylat_qgis.service_styles import StyleClient, local, value  # noqa: E402

client = StyleClient(_style_fetch)
cases = {"Väylä": "tiestotiedot:aidat", "DigiRoad": "digiroad:dr_ajoneuvokoht_rajoitus",
         "Liiteri": "liiteri_etaisyysvyohykkeet:etaisyysvyoh_ala_asteet",
         "Syke": "inspire_ps:PS.ProtectedSitesAsetusValtionOmistamienRakennustenSuojelusta",
         "Tilastokeskus": "tilastointialueet:avi1000k", "Karttapaikka": "cp:CadastralBoundary"}
for source, requested in cases.items():
    entries, errors = catalog(source)
    entry = next(item for item in entries if item["id"] == requested or item["id"].split(":")[-1] == requested.split(":")[-1])
    root, _ = client.get(entry)
    assert any(local(item) == "Rule" for item in root.iter())
    print(source, entry["id"], "->", next(value(item, "Name") for item in root.iter() if local(item) == "UserStyle"), flush=True)
entries, _ = catalog("Traficom Oskari")
entry = next(item for item in entries if item["kind"] in ("wfs", "oskari_wfs") and item.get("layer_name") == "DepthArea_A")
root, _ = client.get(entry)
assert any(local(item) == "Rule" for item in root.iter())
print("Traficom Oskari", entry["id"], "SLD resolved", flush=True)
