# Rajapintojen symboliikka

Tarkistettu 7.10.2026 GitHubin `main`-version `6e6302f` päälle. Toteutus:
ArcGIS Pro 1.0.20 ja QGIS 0.2.10. Aiemmat paikalliset korjaukset säilytettiin.

## Mitkä lähteet tarjoavat tyylejä?

WFS palauttaa geometriat ja attribuutit. Esitystyyli haetaan saman julkaisijan
WMS-palvelun `GetStyles`-pyynnöllä. `GetLegendGraphic` on selitekuva: sitä ei
voi sellaisenaan käyttää paikallisen vektoritason symboliikkana.
GetStyles voi toimia, vaikka sitä ei mainita WMS:n capabilities-vastauksessa.

| Työkalun lähde | Tarkistettu tyylimääritys | Julkisen WMS-luettelon tarkistus |
| --- | --- | --- |
| Väylä | SLD, attribuuttiluokat, viivanleveydet, värit, kuviot ja mittakaavarajat | 332/332 tasolla ladattava SLD |
| DigiRoad | SLD, esimerkiksi rajoituslajien värit ja leveydet | 52/52 |
| Liiteri | SLD, esimerkiksi etäisyysvyöhykkeiden luokat ja värit | Asuinalueet 24/24, etäisyysvyöhykkeet 6/6, taajamat 8/8 |
| Syke | SLD, suojelualueiden alue- ja pistesymbolit | 12/12 |
| Tilastokeskus | SLD, alue-, ruutu-, piste- ja teematyylit | 420/420 |
| Traficom Oskari | Oskarin valitsema tyyli / SLD kolmesta julkisesta INSPIRE-WMS-palvelusta | Avoin 33/34, rajoitettu 14/14, ilmaliikenne 13/13 |
| Karttapaikka, MML INSPIRE | Osa INSPIRE-palveluista tarjoaa SLD:n; WFS- ja WMS-nimet on yhdistettävä täsmällisesti | `cp` 2/2, `au` 8/9; `mu` ei julkaissut WMS-tasoja. `bu_mtk_point`, `bu_mtk_polygon`, `gn`, `hy` vastasivat WFS-capabilitiesilla, eivät WMS-tyyliluettelolla |
| MML:n OGC API Features / Karttapaikan Maastotiedot | Näiden kokoelmien yhteydessä ei ole toteutettu ladattavaa kokoelmakohtaista SLD/QML-tyyliä | Vektoritiilien tyyliä ei käytetä näihin aineistoihin, koska tiilien tasot, kentät ja yleistys eroavat |
| MML:n vektoritiilit | Mapbox Style JSON, sprite- ja tekstitysresurssit | MML dokumentoi Taustakartan `taustakartta`- ja Maastokartan `backgroundmap`-tyylit sekä Kiinteistöjaotuksen tyylit. Live-palvelu tarvitsee API-avaimen |
| Aino | Tunnuksellinen WMS; WFS-tason SLD:tä yritetään hakea samalla tokenilla | Ilman tokenia HTTP 403. Ladattavan SLD:n saatavuutta ei vahvistettu oikeilla tunnuksilla |
| Kapsi | Palvelimen valmiiksi piirtämä WMS-rasteri | Peruskartan 10 WMS-merkintää; GetStyles palautti tyhjän SLD:n. Rasterin esitystyyli tulee valmiiksi palvelusta |
| MML Karttakuva | Palvelimen piirtämä WMS/WMTS-rasteri | Symboliikka sisältyy kuvaan; tunnuksellista palvelua ei testattu oikeilla tunnuksilla |
| OpenStreetMap / Overpass | Ei palvelun tarjoamaa karttatyyliä | Rajapinta välittää kohteet ja tägit; ei WMS/SLD-tyyliä |

Yhteensä 926 julkisen WMS:n nimettyä tasoa tarkistettiin, ja 924 palautti
vähintään yhden oletustyylin säännön. `navigational_warnings` ja
`AU.MaritimeBoundary` palauttivat vain NamedLayer-määrityksen ilman sääntöjä.
Luvut ovat WMS-tasoja, eivät työkalun valikon tasomääriä: esimerkiksi `au`:n
merialuetasot eivät vastaa sen WFS:n hallinnollisia aluetasoja. Tyylin saatavuus
ei myöskään tarkoita, että jokainen SLD-laajennus voidaan muuntaa ArcGISiin.
Tasokohtainen tarkistus on tallennettu
[JSON-raporttiin](symboliikka_rajapinnat_2026-10-07.json).

## Toteutus ja tallennus

Tyyli haetaan automaattisesti vektoriaineiston tallennuksen yhteydessä.
GetStyles-vastauksesta valitaan vain oletustyyli tai Oskarin ilmoittama tyyli;
vaihtoehtoista GeoServerin `generic`-tyyliä ei oteta sen rinnalle. Palvelimen
virhevastausta tai tyhjää SLD:tä ei tulkita onnistuneeksi tyyliksi.

WFS:n työtilallinen nimi, kuten `digiroad:dr_ajoneuvokoht_rajoitus`, voi olla
WMS:ssä `dr_ajoneuvokoht_rajoitus`. Tässä tapauksessa tarkistetaan palvelun
WMS-luettelo ja hyväksytään vain yksiselitteinen vastaava nimi. MML:n
`cp:CadastralBoundary` yhdistetään julkaistuun `CP.CadastralBoundary`-nimeen.
Kokoelmia ei yhdistetä näyttönimien samankaltaisuuden perusteella.

**QGIS:** valittu SLD luetaan QGISin omalla `loadSldStyle`-toiminnolla ennen
tason siirtoa taustatehtävästä pääsäikeeseen. Tyyli tallennetaan GeoPackagen
oletustyyliksi sekä aineiston rinnalle `.sld`- ja `.qml`-tiedostoina. GeoPackagea
uudelleen avattaessa myös tyyli palautuu ilman verkkoyhteyttä. Ulkoiset kuvasymbolit
ladataan aineiston vieressä olevaan `<nimi>_symbols`-kansioon.

**ArcGIS Pro:** SLD muunnetaan CIM-symboliikaksi. Tuettuja ovat vakiovärit,
viivanleveydet, opasiteetti, viivanpäät, katkoviivat, viivan sivusiirto,
kiinteät peruspistesymbolit sekä paikalliset PNG/JPEG/GIF-kuvasymbolit.
Luokittelu tukee vertailuja, `And`/`Or`/`Not`, null-arvoja ja lukuvälejä.
Päällekkäiset säännöt yhdistetään; ElseFilter koskee omaa FeatureTypeStyle-ryhmäänsä.
Arcade-lauseke huomioi myös sääntöjen mittakaavavälit 2D-kartalla.
Luokkayhdistelmät muodostetaan ladatun aineiston luokitteluattribuuteista
muuttamatta käyttäjän kenttiä tai geometrioita.

ArcGISin `.sld` ja uudelleen avattava `.lyrx` tallennetaan shapefilen rinnalle.
Geodatabasen tapauksessa ne ovat GDB:n viereisessä `Suomenvaylat_tyylit`-kansiossa,
ja nimi sisältää sekä GDB:n että tason nimen. Tyylit valmistellaan myös ilman
aktiivista karttaa. Kartalle lisätään valmis LYRX-taso. Pelkän feature classin
avaaminen GDB:stä ei palauta tyyliä: avaa silloin sen LYRX.

**MML:n vektoritiilit:** paljas TileJSON ei määrittele symboliikkaa. ArcGISiin
lisätään palvelun Style JSON `VECTOR_TILE`-lähteenä. QGISille asetetaan erillinen
`styleUrl` ja ladataan tyyli `loadDefaultStyle`-toiminnolla. Taustakartalla ja
Maastokartalla on nyt eri esitystyylit. Kiinteistöjaotuksessa käytetään MML:n
pelkistettyä tyyliä, joka toimii laajemmin eri asiakasohjelmissa.

## Rajat ja virhetilanteet

Tyylipyyntö käyttää 15 sekunnin aikakatkaisua. Tyylin puuttuminen tai
muunnosvirhe ei poista jo tallennettua aineistoa eikä keskeytä muiden tasojen
latausta. ArcGIS kirjoittaa varoituksen GP-lokiin; QGIS näyttää tilan ajon
yhteenvedossa ja viestilokissa. Tyylitiedostoihin ei tallenneta API-avaimia,
tokeneita tai URL-käyttäjätunnuksia.

ArcGIS-muunnos ei kata koko SLD/SE-standardia: esimerkiksi SVG-kuvasymbolit,
kuviotäytöt ja kuviolliset viivat, laskennalliset symboliominaisuudet,
`PropertyIsLike`, GeoServerin funktiot ja vaihtoehtoiset geometriakentät
voivat estää muunnoksen. Näissä tilanteissa alkuperäinen SLD säilytetään ja
tasolle jää ArcGISin oletussymboli. Tekstisymbolit jätetään pois erillisellä
varoituksella; muita tuettuja symboleita voidaan käyttää. QGIS käyttää omaa
SLD-tukeaan, jonka toteutus ei sekään kata kaikkia palvelinkohtaisia laajennuksia.
Shapefilen kenttänimen katkeaminen voi estää luokittelukentän tunnistamisen;
työkalu ilmoittaa siitä eikä arvaa luokittelua. GDB/GeoPackage säilyttää pitkät nimet.

Osa palvelutyyleistä viittaa palvelimen omiin `file:/opt/geoserver-data/...`-
kuvatiedostoihin tai käyttää attribuutista laskettua kuvanimeä `${symboli}`.
Nämä eivät ole ladattavia HTTP-symboliosoitteita. Jos symboliresurssia ei voi
ladata, myös QGIS-tuonti ilmoittaa puutteesta ja säilyttää alkuperäisen SLD:n;
pelkkä tyylin palautuminen GetStylesista ei takaa kaikkien kuvien saatavuutta.

## Testaus

- Verkottomat Python-testit: `python -m unittest discover -s tests`.
- ArcGIS Pron oma Python: `tests/smoke_styles_arcgispro.py`. Kuuden julkisen
  palvelun oikeat SLD-näytteet, CIM-luokat, LYRX:n uudelleenavaus, kartalle lisäys
  ja mittakaavan mukana vaihtuvan värin todellinen kuvavienti.
- QGISin oma Python: `qgis_plugin/smoke_styles.py`. Samat palvelutyylit,
  GeoPackagen oletustyylin palautuminen ja kartan kuvavienti.
- `tests/smoke_vector_tile_styles_arcgispro.py` ja
  `qgis_plugin/smoke_vector_tile_styles.py`: paikallinen HTTP-palvelu tarjoilee
  oikean MVT-tiilen ja Mapbox-tyylin; molempien ohjelmien kuvaviennistä tarkistetaan
  tyylin määräämä punainen tie. Testi ei tarvitse MML:n tunnuksia.
- `qgis_plugin/smoke_styles_live.py`: oikeat WFS-nimet ja elävä SLD-haku
  Väylältä, DigiRoadilta, Liiteristä, SYKEltä, Tilastokeskukselta,
  MML:n CP-palvelusta ja Traficom Oskarista.

## Lähteet

- [GeoServerin SLD-tyylit](https://docs.geoserver.org/main/en/styling/sld/working/)
- [QGISin SLD-tuonti](https://api.qgis.org/api/3.40/classQgsMapLayer.html)
- [Esrin CIM-symbolit](https://github.com/Esri/cim-spec/blob/main/docs/v3/CIMSymbols.md)
- [Arcade-visualisoinnin mittakaava](https://developers.arcgis.com/arcade/profiles/visualization/)
- [MML:n vektoritiilien tekninen kuvaus ja tyylit](https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/map-interface-services/map-image-service-wms-wmts-2)
- [MML:n erillisten GeoPackage-tyylien tarkoitus](https://www.maanmittauslaitos.fi/geopackage/maastotietokannan-tyylitiedostojen-hyodyntaminen)
