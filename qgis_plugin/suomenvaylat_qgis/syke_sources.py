"""SYKE's published WFS services, shared by ArcGIS Pro and QGIS.

The server disables global WFS capabilities. Each published workspace must be
queried separately; inspire_ps only contains protected sites, not all SYKE data.
"""

import html
import re


DIRECTORY_URL = (
    "https://www.syke.fi/fi/ymparistotieto/avoimet-rajapinnat/paikkatietorajapinnat"
)
BASE_URL = "https://paikkatiedot.ymparisto.fi/geoserver/"
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024

# Snapshot of every WFS link in DIRECTORY_URL on 2026-10-20, including Liiteri.
# Used only when the official directory cannot be read. Layer names always come
# from live GetCapabilities responses, never from this fallback.
WFS_WORKSPACES = (
    "inspire_af", "inspire_am1", "inspire_am2", "inspire_am3", "inspire_br",
    "inspire_ef", "inspire_el", "inspire_ge", "inspire_hb", "inspire_hh",
    "inspire_hy", "inspire_lc", "inspire_nz", "inspire_pf", "inspire_ps",
    "inspire_sd1", "inspire_sd2", "inspire_us1",
    "liiteri_asemakaavoitettualue", "liiteri_asuinalueet",
    "liiteri_etaisyysvyohykkeet", "liiteri_harvatiheataajama",
    "liiteri_kaupunkimaaseutuluokitus", "liiteri_kaupunkiseudut",
    "liiteri_keskustakaupanalueet", "liiteri_kylatpienkylat", "liiteri_porot",
    "liiteri_saaristoluokitus", "liiteri_taajamat",
    "liiteri_yhdyskuntarakenteenvyohykkeet",
    "syke_arvokkaatmaisemaalueet", "syke_arvokkaatpienvedet1990",
    "syke_clcmuutos", "syke_euymparistomeludirektiivi2007",
    "syke_euymparistomeludirektiivi2012", "syke_euymparistomeludirektiivi2017",
    "syke_geologisetmuodostumat", "syke_hajajatevesienyslmukaisetrantaalueet",
    "syke_hiekkarantojenominaisuudet", "syke_kalkkikalliot",
    "syke_kaupunkipuistot", "syke_lahteikotesiintyminenmuuttuneisuus",
    "syke_lehtojensuojeluohjinvaineisto", "syke_luonnonsuojeluohjelma_alueet",
    "syke_luotauspisteet", "syke_maa_ainestenottoluvat", "syke_maatalousmaa",
    "syke_maisemanhoitoalueet", "syke_merenhoidonaluejako",
    "syke_metsataloudelleherkatvesistot", "syke_pintavesienekologinentila",
    "syke_potentiaalisettulvametsat", "syke_purohelmi",
    "syke_rakennussuojelukohteet", "syke_rannikonavoimetdyynit",
    "syke_rantaviiva", "syke_serpentiinikalliotkivikot",
    "syke_soidensuojeluntaydennysehdotus", "syke_turvetuotantoalueetjajalkikaytto",
    "syke_valumaaluejako1990", "syke_vesibio", "syke_vesikasvivyohyke",
    "syke_vhspohjavesi", "syke_vierasvayla", "syke_virtavesienlohikalakannat",
    "tulva_erikois_riskikohteet", "tulva_erikois_riskiruudut",
    "tulva_erikois_tiet", "tulva_erikoisskenaariot", "tulva_havaitut",
    "tulva_perus_peittama", "tulva_perus_riskikohteet",
    "tulva_perus_riskiruudut", "tulva_perus_tiet", "vemalaAvoin",
    "ylalapin_luontotyypit",
)
WFS_ENDPOINTS = tuple(BASE_URL + workspace + "/wfs" for workspace in WFS_WORKSPACES)


def endpoints_from_directory(raw):
    """Read only HTTPS WFS links on SYKE's GeoServer, stripping query strings."""
    if len(raw) > MAX_DIRECTORY_BYTES:
        raise ValueError("SYKEn rajapintahakemisto ylitti kokorajan")
    text = html.unescape(raw.decode("utf-8"))
    workspaces = re.findall(
        re.escape(BASE_URL) + r"([A-Za-z0-9_-]+)/wfs(?=[?\s<>\"'/]|$)", text
    )
    return sorted({BASE_URL + workspace + "/wfs" for workspace in workspaces})


def discover_endpoints(fetch):
    """Return (endpoints, warnings), using the bundled snapshot on failure."""
    try:
        endpoints = endpoints_from_directory(fetch(DIRECTORY_URL))
        if not endpoints:
            raise ValueError("hakemistosta ei löytynyt WFS-osoitteita")
        return endpoints, []
    except Exception as exc:
        return list(WFS_ENDPOINTS), [
            "SYKEn rajapintahakemisto ei avaudu; käytetään mukana toimitettua "
            "WFS-palveluluetteloa: {}".format(exc)
        ]
