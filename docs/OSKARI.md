# Traficom Oskari -rajapinta

Suomenväylät lukee Traficomin Oskari-karttatasot palvelun omasta
`/oskari/action`-rajapinnasta. Tasot eivät vaadi API-avainta.

## Tasoluettelo

Työkalu pyytää dynaamisen tasoluettelon:

```text
GET https://julkinen.traficom.fi/oskari/action
    ?action_route=GetHierarchicalMapLayerGroups
    &srs=EPSG%3A3067
    &lang=fi
```

Tuloksen `layers`-listasta näytetään vain `type=wfslayer`-tasot, joiden
`orgName` on Traficom. Oskarin WMS- ja WMTS-tasot eivät ole ladattavia
vektoritasoja, joten ne jätetään tästä lähdevalikosta pois.

## Kohteiden haku

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

Testattu 24.9.2026. Tasoluettelossa oli 76 karttatasoa, joista neljä oli
Traficomin WFS-tasoja:

- Matkustaja-alusten D-alueet (`id=112`)
- Merivaroitukset (piste) (`id=117`)
- Merivaroitukset (viiva) (`id=118`)
- Merivaroitukset (alue) (`id=119`)

`GetWFSFeatures`-pyyntö tasolle `id=112` bboxilla
`260000,6600000,420000,6700000` palautti HTTP 200 -vastauksena
GeoJSON FeatureCollectionin: yksi MultiLineString-kohde, EPSG:3067 ja
1 414 785 tavua. Vastauksessa ollut geometria ulottui bboxin ulkopuolelle,
mikä vahvisti tarpeen leikata se paikallisesti ArcGIS Prossa.

## ArcGIS Pro -tarkistus

Täysi geoprocessing-ajo testattiin ArcGIS Pro 3.7:n Python-ympäristössä
komennolla:

```powershell
& 'C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat' `
  'tests\smoke_traficom_oskari_arcgispro.py'
```

Testi löysi tason `id=112`, latasi sen Oskari-rajapinnasta, muodosti ja leikkasi
aineiston ArcGIS Prossa ja kirjoitti tuloksen File GDB:hen. Tulos oli yksi
Polyline-kohde EPSG:3067:ssä. Propy suorittaa testin ilman avointa Pro-projektia,
joten automaattinen lisäys aktiiviseen karttaan ei kuulu tähän smoke-testin
tulokseen.

Testi käyttää oikeaa Pro GP-työkalua ja poistaa luomansa väliaikaisen GDB:n ajon
jälkeen:

```text
tests/smoke_traficom_oskari_arcgispro.py
```

1. Avaa `Toolboxes/VaylaWFSDownloader.pyt` ArcGIS Prossa.
2. Valitse **Traficom Oskari** ja päivitä tasolistaus.
3. Valitse **Matkustaja-alusten D-alueet** ja testialueeksi koko Suomi tai
   oma polygoni, joka leikkaa yllä testatun bboxin.
4. Aja työkalu. Lokissa pitää näkyä Oskari API, tunnus `112`, EPSG:3067 ja
   Oskari-yhteenveto. Lopputuloksen pitää olla karttaan lisätty viivatason
   feature class ilman epäonnistunutta tasoa.
5. Tarkista tulostason koordinaatistoksi EPSG:3067 ja vertaa Oskarin
   GeoJSON-kohdemäärää muunnetun väliaineiston kohdemäärään.
