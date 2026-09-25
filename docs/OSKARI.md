# Traficom Oskari -rajapinta

Suomenväylät lukee Oskarin tasoluettelon palvelun omasta
`/oskari/action`-rajapinnasta. Tasot eivät vaadi API-avainta.

## Tasoluettelo ja lataustavat

Työkalu pyytää dynaamisen tasoluettelon:

```text
GET https://julkinen.traficom.fi/oskari/action
    ?action_route=GetHierarchicalMapLayerGroups
    &srs=EPSG%3A3067
    &lang=fi
```

Oskarin kaikki luettelotasot näytetään **Traficom Oskari** -valikossa,
mukaan lukien palveluun liitettyjen muiden organisaatioiden tasot. Aineiston
palvelutyyppi määrää, miten se ladataan:

- Oskarin `wfslayer`-tasot haetaan `GetWFSFeatures`-toiminnolla.
- WMS-tasot, joiden tekniselle nimelle löytyy avoimen WFS:n FeatureType,
  ladataan vektorina osoitteesta `inspirepalvelu/avoin/wfs`.
- Muut WMS-tasot haetaan Oskarin `GetLayerTile`-välityspalvelun kautta
  aluerajauksen georeferoituna PNG-kuvana ja tallennetaan GeoTIFFiksi.
- WMTS-tasot ladataan `rasteripalvelu/wmts`-palvelun tiilistä ja yhdistetään
  GeoTIFF-mosaiikiksi.

WMS/WMTS-karttakuvat ovat katselutasojen kuvallisia esityksiä. Niistä ei synny
vektorikohteita tai attribuuttitauluja.

## Vektorikohteiden haku

Valitun WFS-tason Oskari-tunnus ja rajauksen EPSG:3067-bbox lähetetään
`GetWFSFeatures`-toiminnolle:

```text
GET https://julkinen.traficom.fi/oskari/action
    ?action_route=GetWFSFeatures
    &id=<oskari-tasotunnus>
    &srs=EPSG%3A3067
    &bbox=<xmin>,<ymin>,<xmax>,<ymax>
```

Oskari palauttaa GeoJSON FeatureCollectionin, jonka koordinaatisto on
`EPSG:3067`. ArcGIS Pro muuntaa sen feature classiksi ja leikkaa kohteet
valittuun rajaukseen. Oskarin vastaus voi sisältää koko rajaukseen osuvan
geometrian, joten paikallinen Clip säilytetään.

## Rajapinnan testaus

Testattu 25.9.2026. Tasoluettelossa oli 76 karttatasoa: 67 WMS-, viisi
WMTS- ja neljä WFS-tasoa. Mukana on myös Oskariin kytkettyjen muiden
organisaatioiden tasoja. Avoimen WFS:n capabilities-luettelossa oli 36
FeatureTypeä; ArcGIS Pro -smoke-ajossa 32 Oskarin WMS-tason teknistä nimeä
vastasi niistä yhtä.

Natiivit Oskari WFS -tasot olivat:

- Matkustaja-alusten D-alueet (`id=112`)
- Merivaroitukset (piste) (`id=117`)
- Merivaroitukset (viiva) (`id=118`)
- Merivaroitukset (alue) (`id=119`)

`GetWFSFeatures`-pyyntö tasolle `id=112` bboxilla
`260000,6600000,420000,6700000` palautti HTTP 200 -vastauksena
GeoJSON FeatureCollectionin: yksi MultiLineString-kohde, EPSG:3067 ja
1 414 785 tavua. Vastauksessa ollut geometria ulottui bboxin ulkopuolelle,
mikä vahvisti tarpeen leikata se paikallisesti ArcGIS Prossa. Lisäksi WMS
`id=53` palautti `GetLayerTile`-pyynnöllä PNG-kuvan. Traficomin WMTS
GetCapabilities vastasi viittä Oskarin WMTS-tasoa; `GetTile` palautti
onnistuneesti PNG-laatan EPSG:3067-matriisista.

## ArcGIS Pro -tarkistus

Täysi geoprocessing-ajo testattiin ArcGIS Pro 3.7:n Python-ympäristössä:

```powershell
& 'C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat' `
  'tests\smoke_traficom_oskari_arcgispro.py'
```

Smoke-testi varmisti, että kaikki 76 tasoa näkyvät valikossa (32 WFS-vastinetta,
neljä Oskarin omaa WFS-tasoa, 35 WMS-kuvatasoa ja viisi WMTS-tasoa). Se latasi
natiivin WFS-tason, WMS-rasterin ja WMTS-mosaiikin oikeilla ArcPy-toiminnoilla.
Tuloksena oli yksi Polyline-kohde EPSG:3067:ssä, WMS GeoTIFF (2048 × 1280) ja
WMTS GeoTIFF (2816 × 1792, 77 tiiltä); kaikkien tulosten koordinaatisto oli
EPSG:3067. Propy suorittaa testin ilman avointa Pro-projektia, joten tulosten
lisääminen aktiiviseen karttaan ei kuulu smoke-testin tarkistuksiin.

Testi käyttää oikeaa Pro GP-työkalua ja poistaa luomansa väliaikaisen GDB:n ajon
jälkeen:

```text
tests/smoke_traficom_oskari_arcgispro.py
```

Käsin ArcGIS Prossa voit valita Traficom Oskari -lähteestä joko
**Matkustaja-alusten D-alueet** tai WFS-vastineen kuten **Runway Area**
vektoritulokseksi. Muut WMS- ja WMTS-tasot tuottavat georeferoidun rasterin.
