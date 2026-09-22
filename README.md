# Suomenväylät - ArcGIS Pro Add-in

**QGIS-versio (esijulkaisu):** [asennus ja nykyinen toiminnallisuus](qgis_plugin/README.md). Ladattava ZIP on [GitHub-julkaisussa](https://github.com/roopepalom44/suomenvaylat/releases/tag/qgis-v0.1.0).

ArcGIS Pro -laajennus Suomenväylät-aineistojen lataamiseen WFS-rajapinnoista.

## Lataus ja asennus

1. Lataa uusin [Suomenvaylat.esriAddInX](https://github.com/roopepalom44/suomenvaylat/releases/latest/download/Suomenvaylat.esriAddInX) ([kaikki julkaisut](https://github.com/roopepalom44/suomenvaylat/releases)).
2. Sulje ArcGIS Pro ja asenna tiedosto kaksoisklikkaamalla sitä.

### Julkaisun tekeminen (kehittäjille)

Nosta versio `Config.daml`-tiedostossa, commitoi ja pushaa, ja aja sitten:

```powershell
powershell -ExecutionPolicy Bypass -File .\release.ps1
```

Skripti rakentaa Release-version, paketoi sen ja luo GitHub-releasen `v<versio>` (vaatii ArcGIS Pron ja `gh auth login`).

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
validoi paketin sisällön. Paketti käyttää versiota `1.0.16`, jotta ArcGIS Pro
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

Jos ArcGIS Pro näyttää virheen `TypeNotFound` tai ilmoittaa komennon olevan unavailable, paketissa on ollut vanha/väärä DLL tai väärä AddInX-rakenne. `package-addin.ps1` tarkistaa nyt assemblyn nimen, varmistaa että DLL sisältää tyypit `suomenvaylat.Module1` ja `suomenvaylat.OpenSuomenvaylatToolButton`, sekä pakottaa runtime-tiedostot `Install\`-hakemistoon. Add-in-versiona on `1.0.16`, jotta ArcGIS Pro tunnistaa tämän päivitykseksi.

Jos tarkistus ilmoittaa väärästä DLL:stä, käännä C#-projekti ArcGIS Pro SDK:n kanssa ja anna tulos suoraan:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

WFS- ja rasterikäsittely tehdään ajokohtaisessa paikallisessa scratch-geodatabasessa. Verkkotyötilaan kopioidaan vasta valmis taso tai rasteri, joten verkkoaseman hitaus ei hidasta jokaista välivaihetta.

## Suorituskyky ja virheensieto

- **Pysyvät HTTP-yhteydet.** Sivupyynnöt käyttävät samaa TCP/TLS-yhteyttä hostia
  kohti sen sijaan, että jokainen sivu avaisi uuden. Vanhentunut keep-alive-
  yhteys uusitaan automaattisesti kerran ennen virheen nostamista.
- **Rinnakkainen sivujen esihaku.** Ensimmäinen sivu haetaan aina sarjallisesti,
  jotta outputFormat-, geometriakenttä- ja sortBy-päättely sekä CQL:n varareitit
  toimivat ennallaan. Vasta kun sivutus on todistetusti käynnissä, seuraavat
  sivut haetaan rinnakkain (`_page_workers`, oletus 4). Jos palvelu kertoo
  `numberMatched`- tai `totalFeatures`-arvon, esihaun aalto rajataan siihen,
  jottei hännästä pyydetä tyhjiä sivuja.
- **JSONToFeatures ajetaan erissä.** Sivut kootaan yhdeksi FeatureCollectioniksi
  (`_json_batch_pages`, oletus 8) ennen muunnosta. Tämä vähentää sekä
  GP-kutsujen määrää että myöhemmän Mergen syötteitä. Ensimmäisen sivun
  ylätason jäsenet (esim. `crs` ja `geometry_name`) säilyvät ennallaan.
- **Uudelleenyritys ohimenevissä virheissä.** Aikakatkaisut, katkenneet
  yhteydet sekä 408/425/429/500/502/503/504 yritetään uudelleen
  eksponentiaalisella viiveellä (oletus 3 yritystä); palvelimen `Retry-After`
  voittaa oman viiveen. 4xx-virheitä **ei** yritetä uudelleen, koska esimerkiksi
  400 ja 414 ovat CQL:n varareiteille merkitseviä signaaleja. Jokainen
  uudelleenyritys lokitetaan sanitisoidulla palveluosoitteella.
- **Vaillinaista aineistoa ei enää tallenneta hiljaisesti.** Jos sivutuksen
  `max_requests` täyttyy, taso kaatuu ja näkyy ajon yhteenvedon
  epäonnistuneissa tasoissa. Aiemmin vajaa aineisto tallennettiin ja lisättiin
  kartalle kuin se olisi täysi.
- **Rasterilaatat ladataan rinnakkain.** Kapsin JPEG-laatat ja MML:n WMTS-tiilet
  haetaan säikeissä (`_tile_workers`, oletus 5); tiedostojen kirjoitus ja
  nimien varaus tehdään pääsäikeessä, joten nimet eivät voi törmätä.
- **Tasolistauksen levyvälimuisti.** GetCapabilities-haut talletetaan
  `%LOCALAPPDATA%\Suomenvaylat\layer_catalog_cache.json`-tiedostoon 24 tunniksi,
  joten työkalun avaus ei enää odota verkkoa joka kerta. Välimuistiavain on
  SHA-256-tiiviste lähteistä ja tunnisteista — tunnisteita itseään ei kirjoiteta
  levylle. Valinta **Päivitä tasolistaus palvelusta** ohittaa välimuistin
  kertaluonteisesti.
- **Keskeneräiset väliaineistot siivotaan.** Kun ruudukkotaso epäonnistuu ja
  siirrytään hienompaan ruudukkoon, jo luodut scratch-feature classit
  poistetaan sen sijaan että ne jäisivät paisuttamaan scratch-GDB:tä.
- `ExportFeatures` korvaa deprecated `FeatureClassToFeatureClass`-työkalun;
  vanha jää varareitiksi vanhemmille ArcGIS Pro -versioille.

Kaikki rinnakkaisuus koskee vain verkkopyyntöjä. Geoprosessointi ja arcpy-kutsut
tehdään edelleen yksinomaan pääsäikeessä.

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

## Sitowise Aino

**Aino** on tokenilla suojattu WFS- ja WMS-lähde. Työkalu hakee molemmat
tasoluettelot dynaamisesti GetCapabilities-vastauksista. Kartoitushetkellä
valittavina oli 113 WFS-vektoritasoa ja kaikki 175 nimettyä WMS-karttatasoa.
Saman aineiston WFS- ja WMS-versiot erotetaan nimissä `(WFS)`- ja
`(WMS)`-tunnisteilla. Syötä token käyttöliittymän piilotettuun
**Aino-token**-kenttään.

Jos token on kopioitu lähteestä, jossa yhtäsuuruusmerkki on koodattu muotoon
`=3D`, kenttään voi päätyä ylimääräinen `3D` varsinaisen tokenin alkuun. Versio
1.0.15 ja uudemmat tunnistavat ja korjaavat tämän tarkan kopiointimuodon. Haku
raportoi lisäksi HTTP 401/403 -tunnistusvirheen suoraan tokenkentässä sen sijaan,
että aineistolista jäisi selityksettä tyhjäksi.

Aino käyttää WFS 1.1.0:aa. Suomenväylät muodostaa sille palvelun vaatimat
`typeName`- ja `maxFeatures`-parametrit, sivuttaa `startIndex`-parametrilla,
pyytää GeoJSONin EPSG:3067:ssä ja tekee lopullisen tarkan Clip-rajauksen
paikallisesti. Token lisätään kaikkiin GetCapabilities-, DescribeFeatureType-,
GetFeature- ja POST-pyyntöihin, mutta lokiin ja levyvälimuistin tasomäärityksiin
se ei tallennu.

WMS-valinta lisätään ArcGIS Pron aktiiviseen karttaan oikeana live-WMS-
palvelutasona, ei feature classina eikä ladattuna kuvana. Työkalu välittää
tokenin ArcGIS Pron erillisenä palveluparametrina, kytkee näkyviin vain valitun
WMS-alitason ja sijoittaa `taustakartat`-nimiavaruuden tason
**Taustakartta**-ryhmään karttatasopinon alimmaiseksi. Muut WMS-tasot
sijoitetaan **Aino WMS** -ryhmään.

Versiosta 1.0.16 alkaen WMS-palvelun CIM-puusta poistetaan muut kuin valittu
alitaso ja sen välttämätön yläpolku. ArcGIS Pron Contents-paneeliin ei siten
jää kaikkia 175 pois kytkettyä Aino-alitasoa. Ryhmään kopioinnin jälkeen myös
alkuperäinen kartan juuritason palvelukopio poistetaan, joten valittu WMS-taso
näkyy kartassa vain kerran.

Rajapinnan sisältö, protokollat, koordinaatistot ja yhteystestit on dokumentoitu
tiedostossa [`docs/AINO.md`](docs/AINO.md).

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

> **Huom.** Aiemmissa versioissa repositoriossa oli tiedosto
> `Toolboxes/Resources/credentials.wmts`, joka sisälsi WMTS-palvelun
> käyttäjätunnuksen ja sisäisen verkkopolun. Tiedostoa ei koskaan käytetty
> koodista, ja se on nyt poistettu paketista, projektitiedostosta ja
> paketointiskriptistä. `*.wmts`-tiedostot on lisätty `.gitignore`en.
> **Tiedostossa ollut tunnus on vaihdettava palveluntarjoajan hallinnassa** —
> tiedoston poisto ei peruuta jo paljastunutta tunnusta.


API-avaimet, Aino-token ja salasanat ovat käyttöliittymässä piilotettuja kenttiä. Tallennetut tunnisteet suojataan Windowsin käyttäjäkohtaisella DPAPI-salauksella. Aiemman version selväkieliset arvot migroidaan salattuun muotoon niitä luettaessa. Lokissa WFS-palvelusta näytetään vain sanitisoitu perusosoite ilman query-parametreja, käyttäjätunnusta tai salasanaa.

Jos tunniste on ehtinyt näkyä jaetussa ArcGIS-lokissa, vaihda se palveluntarjoajan hallinnassa. Lokin poistaminen ei yksin peruuta paljastunutta avainta.

ArcGIS Prossa ajettava hyväksymis- ja vertailutestilista on tiedostossa [`docs/ARCGIS_PRO_TESTIT.md`](docs/ARCGIS_PRO_TESTIT.md).
