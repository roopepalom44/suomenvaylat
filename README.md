# Suomenväylät - ArcGIS Pro Add-in

ArcGIS Pro -laajennus Suomenväylät-aineistojen lataamiseen WFS-rajapinnoista.

## Vaatimukset


`Config.daml` käyttää oletusnimitilaa `suomenvaylat` ja luokkien lyhyitä nimiä
`Module1` ja `OpenSuomenvaylatToolButton`. ArcGIS Pro ratkaisee ne tällöin
tyypeiksi `suomenvaylat.Module1` ja `suomenvaylat.OpenSuomenvaylatToolButton`.

Jos ArcGIS Pro näyttää edelleen `TypeNotFound`-virheen, aja ensin paketin diagnostiikka:

```powershell
powershell -ExecutionPolicy Bypass -File .\diagnose-addin.ps1
```

Skriptin tuloksesta olennaiset rivit ovat `Assembly identity`, `Referenced ArcGIS assemblies`, `Installed`, `Type found`, `TYPE MISSING` ja `Loader error`. Skripti lataa DLL:n muistista, joten diagnostiikan lopussa purettu väliaikaiskansio voidaan poistaa normaalisti. Se vertailee myös pakatun DLL:n SDK-versioita koneen ArcGIS Pro -asennukseen.

## Manuaalinen paketointi ilman MSBuildia

Kun C#-osa on jo käännetty build-kansioon, paketoi nykyiset lähdetiedostot yhdellä PowerShell-komennolla:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1
```

Skripti käyttää vain käännöksen build-kansiossa olevaa DLL:ää eikä koskaan valitse
automaattisesti ArcGIS Pron AssemblyCache-kopiota. Se rakentaa uuden
`bin\Debug\net8.0-windows\suomenvaylat.esriAddInX`-paketin tyhjästä, sijoittaa
DLL:n ja työkalut viralliseen `Install\`-hakemistoon, tarkistaa CLR-tyypit ja
validoi paketin sisällön. Paketti käyttää versiota `1.0.11`, jotta ArcGIS Pro
tunnistaa sen päivitykseksi.

Kapsin tarkat mittakaavatasot jaetaan tarvittaessa useaan enintään 25 laatan latauserään ja yhdistetään lopuksi yhdeksi rasteriksi.

Jos DLL on muualla, anna sen polku:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

Jos uutta käännöstä ei voida tehdä, olemassa olevaa AssemblyCache-DLL:ää voi
käyttää vain, jos se sisältää nykyiset tyypit. Paketoitava tiedosto voidaan
antaa suoraan näin:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin-fixed.ps1 `
  -AssemblyPath "$env:LOCALAPPDATA\ESRI\ArcGISPro\AssemblyCache\{2aea3c93-c012-4ed4-b416-6b3a844e0204}\suomenvaylat.dll"
```

Skripti tarkistaa assemblyn nimen ja odotetut tyypit ennen paketointia.

Skripti ei käännä C#-lähdekoodia. Tee ArcGIS Pro SDK -koneella puhdas
uudelleenkäännös Visual Studion täydellä MSBuildilla ja paketoi heti perään:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-addin.ps1 -Configuration Debug
```

C#-lähdekoodin muutoksia ei saa paketoida vanhan DLL:n kanssa. Jos käännös on
eri kansiossa, anna juuri uudelleen käännetyn DLL:n polku `-AssemblyPath`-parametrilla.

`dotnet build` ei sovellu tämän ArcGIS Pro SDK -projektin kääntämiseen, koska
Esrin nykyinen `Esri.ProApp.SDK.Desktop.targets` käyttää täyden MSBuildin
`CodeTaskFactory`-tehtävää. Jos MSBuild löytyy epätyypillisestä paikasta, anna
polku parametrilla `-MSBuildPath`.

### TypeNotFound / command unavailable

Jos ArcGIS Pro näyttää virheen `TypeNotFound` tai ilmoittaa komennon olevan unavailable, paketissa on ollut vanha/väärä DLL tai väärä AddInX-rakenne. `package-addin.ps1` tarkistaa nyt assemblyn nimen, varmistaa että DLL sisältää tyypit `suomenvaylat.Module1` ja `suomenvaylat.OpenSuomenvaylatToolButton`, sekä pakottaa runtime-tiedostot `Install\`-hakemistoon. Add-in-versiona on `1.0.11`, jotta ArcGIS Pro tunnistaa tämän päivitykseksi.

Jos tarkistus ilmoittaa väärästä DLL:stä, käännä C#-projekti ArcGIS Pro SDK:n kanssa ja anna tulos suoraan:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

WFS- ja rasterikäsittely tehdään ajokohtaisessa paikallisessa scratch-geodatabasessa. Verkkotyötilaan kopioidaan vasta valmis taso tai rasteri, joten verkkoaseman hitaus ei hidasta jokaista välivaihetta.

## WFS-haun rajaus ja suorituskykyloki

- CQL `INTERSECTS` on Väylä- ja Digiroad-tasojen ensisijainen hakutapa. Nykyinen 2D-WKT-muunnos säilyy käytössä.
- Jos yhtenäinen CQL GET ja POST hylätään ja suodatin on pitkä, työkalu yrittää samaa rajausgeometriaa pienempinä CQL-osina. BBOXiin siirrytään vasta näiden yritysten jälkeen.
- CQL palauttaa kokonaiset suunnittelualueeseen leikkaavat geometriat; geometrioita ei katkaista rajaan paikallisella Clip-vaiheella.
- Jokainen WFS-sivu lokitetaan erikseen. Verkkopyyntö, vastauksen lukeminen, JSON-jäsennys, JSON-tiedoston kirjoitus, `JSONToFeatures` ja sivun koko käsittelyaika ovat erillisiä lukuja.
- Taso- ja työkaluyhteenvedoissa käyttämättömät vaiheet näkyvät tekstinä, eivät harhaanjohtavana nolla-aikana. Vaiheiden summa, Muu-aika ja kokonaisaika raportoidaan erikseen.
- Ylimääräistä CopyFeatures-vertailukopiota ei tehdä normaalissa ajossa. Kopioinnin suorituskyky näkyy varsinaisen kohdekopioinnin lokista.
- Scratch-aineisto poistetaan onnistuneen ajon jälkeen ja säilytetään aina virhetilanteessa vianmääritystä varten.
- Verkko-GDB:n tulosnimelle lisätään ajokohtainen tunniste. Näin nimi pysyy yksilöllisenä ilman useita hitaita `Exists`-kyselyitä verkkoasemalle.
- Kohdemäärä lasketaan paikallisesta valmiista staging-aineistosta ennen verkkokopiointia. Lopullista verkko-GDB:n tasoa ei avata uudelleen pelkkää laskentaa varten.

`JSONToFeatures`-toteutusta ei ole vaihdettu ilman ArcGIS Prossa tehtävää saman aineiston vertailutestiä. Uusi loki antaa tarvittavat vertailuluvut nykyiselle sivukohtaiselle toteutukselle ennen mahdollista yhdistetyn JSONin tai suoran feature class -kirjoituksen kokeilua.

## Karttapaikka / Maanmittauslaitos

Yleisen **Suomenväylät.fi**-työkalun **MML**-lähde käyttää Maanmittauslaitoksen
nykyistä kiinteistöaineistojen OGC API Features -palvelua:
`https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/simple-features/v3/`.
Kokoelmat haetaan dynaamisesti palvelun `collections`-resurssista, joten myös
uudet MML-kiinteistötasot tulevat valikkoon ilman koodimuutosta. Jokainen valittu
kokoelma haetaan GeoJSON-sivuina, projisoidaan EPSG:3067:ään ja leikataan
paikallisesti rajaukseen.

MML:n OGC API Features- ja vector tile -pyynnöt käyttävät samaa MML API-avainta
HTTP Basic -tunnistautumisessa. Avainta ei lisätä OGC API -osoitteeseen eikä
lokiviesteihin.

**Taustakartat (MML/Kapsi)** -työkalun MML-valinnat **Taustakartta**,
**Maastokartta** ja **Kiinteistojaotus** lisätään nykyisistä MML TileJSON-
vektoritiilipalveluista suoraan aktiiviseen karttaan. MML:n vector tile -tasot
ovat live-palveluja, joten ne noudattavat karttanäkymää eivätkä tuota paikallista
rasterikopiota; alue- ja tallennuskohdeparametrit koskevat tässä työkalussa vain
Kapsin rasterilatausta. MML:n kiinteistöjaotuksen TileJSON on:
`https://avoin-karttakuva.maanmittauslaitos.fi/kiinteisto-avoin/v3/kiinteistojaotus/ETRS-TM35FIN/tilejson.json`.

Kapsi säilyy rajauskohtaisena JPEG-rasterilatauksena. MML:n vanhat WMTS-rasterin
muunnosrutiinit ovat edelleen lähdekoodissa yhteensopivuus-/varareitteinä,
mutta niitä ei käytetä MML:n nykyisessä tasolistauksessa tai taustakarttatyökalun
MML-polussa.

ArcGIS Pron tasovalinta säilytetään suodatinlistan päivityksen yli, jos valittu
taso kuuluu edelleen valittuihin lähteisiin. Tämä koskee muun muassa Kapsi- ja
Karttapaikka-tasoja.

## OpenStreetMap POI-pisteet

**OpenStreetMap**-lähteen **POI-pisteet**-taso hakee rajauksen POI-kohteet
Overpass APIsta ja kokoaa ne yhdeksi pistetasoksi. Luokitus vastaa Geofabrikin
`gis_osm_pois_free`-rakennetta: tuloksessa ovat `osm_id`, `code`, `fclass` ja
`name`. Lisäksi `osm_type` kertoo, oliko alkuperäinen OSM-kohde `node`, `way` vai
`relation`.

Haku kattaa sekä OSM:ssä valmiiksi pisteinä kuvatut POI:t että alueina kuvatut
kohteet. `way`- ja `relation`-kohteista muodostetaan Overpassin laskema
keskipiste, joten esimerkiksi rakennuksen alueena piirretty hotelli tai ravintola
päätyy samaan pistetasoon. Moniluokkainen OSM-kohde tuottaa yhden rivin jokaista
osuvaa Geofabrik-`fclass`-luokkaa kohti. Julkisen Overpass-palvelun häiriössä
työkalu kokeilee automaattisesti toista palvelinosoitetta ja tiheässä haussa
pienempiä ruutuja.

Rajapinnan, vertailuaineiston ja luokkaryhmien tarkempi kartoitus on tiedostossa
[`docs/OSM_POI.md`](docs/OSM_POI.md).

Karttapaikka-lähde käyttää nykyisiä Maanmittauslaitoksen INSPIRE WFS -palveluja. Vanhat
`avoin-karttakuva.maanmittauslaitos.fi/inspire/wfs`- ja
`.../geoserver/maastotiedot/wfs`-osoitteet eivät enää ole käytössä. API-avaimella
lähde hakee lisäksi nykyisen Maastotiedot OGC API Features -palvelun kokoelmat
(liikenneverkot, rakennukset ja rakenteet). Kokoelmat haetaan uudelleen aina, kun
API-avain muuttuu.

INSPIRE WFS -palvelut ovat avoimia ilman API-avainta. Maastotiedot OGC API Features
-palvelu vaatii Maanmittauslaitoksen API-avaimen; työkalu lähettää sen HTTP Basic
-tunnistautumisessa käyttäjätunnuksena ja jättää salasanan tyhjäksi. WFS- ja OGC
-tasot näytetään samaan Karttapaikka-lähteeseen, mutta kummallekin tallennetaan oma
palveluosoite, jotta rakennusten piste- ja polygoniversiot eivät sekoitu.

## Tunnisteiden käsittely

API-avaimet ja salasanat ovat käyttöliittymässä piilotettuja kenttiä. Tallennetut tunnisteet suojataan Windowsin käyttäjäkohtaisella DPAPI-salauksella. Aiemman version selväkieliset arvot migroidaan salattuun muotoon niitä luettaessa. Lokissa WFS-palvelusta näytetään vain sanitisoitu perusosoite ilman query-parametreja, käyttäjätunnusta tai salasanaa.

Jos tunniste on ehtinyt näkyä jaetussa ArcGIS-lokissa, vaihda se palveluntarjoajan hallinnassa. Lokin poistaminen ei yksin peruuta paljastunutta avainta.

ArcGIS Prossa ajettava hyväksymis- ja vertailutestilista on tiedostossa [`docs/ARCGIS_PRO_TESTIT.md`](docs/ARCGIS_PRO_TESTIT.md).
