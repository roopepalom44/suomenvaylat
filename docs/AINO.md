# Sitowise Aino -rajapinta

Kartoitus ja yhteystestit on tehty 22.9.2026. Palveluosoite on
`https://aino.sitowise.com/ows`; käyttö vaatii käyttäjäkohtaisen tokenin.
Tokenia ei kuulu lisätä lähdekoodiin tai jakaa lokien mukana.

## Palvelut ja versiot

Sama OWS-osoite julkaisee kaksi OGC-palvelua:

- WMS 1.3.0, palvelun nimi **Sitowise Aino Web Map Service**, 175 nimettyä
  karttatasoa;
- WFS 1.1.0, palvelun nimi **Sitowise Aino Web Feature Service**.

Suomenväylät käyttää kumpaakin palvelua käyttötarkoituksen mukaan. WFS-valinta
tuo muokattavat vektorikohteet attribuutteineen feature classiksi. WMS-valinta
lisää palvelimen valmiiksi piirtämän karttakuvan ArcGIS Pron aktiiviseen karttaan
live-palvelutasona. GetCapabilities-pyyntö palauttaa
WFS 1.1.0:n myös silloin, kun pyynnössä ehdotetaan versiota 2.0.0. WFS 2.0:n
GetFeature-parametrit hylätään HTTP 403 -vastauksella, joten Aino-haut tehdään
nimenomaisesti WFS 1.1 -muodossa:

- `typeName` eikä `typeNames`;
- `maxFeatures` eikä `count`;
- GeoServerin tukema `startIndex` sivutukseen;
- `sortBy` tarvittaessa vakaaseen sivutukseen;
- `outputFormat=application/json` GeoJSON-tulokseen.

Palvelun ilmoittamiin tulosmuotoihin kuuluvat GeoJSONin lisäksi GML 2/3/3.2,
KML, Shape-ZIP ja CSV. GeoJSON on ArcGIS-tuonnissa tarkoituksenmukaisin, koska
se säilyttää geometrian ja attribuutit ja sopii työkalun nykyiseen
JSONToFeatures-putkeen.

WMS:n 175 tasosta 112 oli myös WFS:n ladattavina vektoritasoina. WMS:ssä oli
63 sellaista tasoa, joita WFS ei julkaissut, ja WFS:ssä yksi taso
(`aineisto:Digiroad_dr_linkki`), jota WMS ei julkaissut. WMS-tasot jakautuivat
nimiavaruuksiin `aineisto` (60), `ymparistoaineistot` (53), `aluejaot` (21),
`kaavoitus` (20), `geologia` (20) ja `taustakartat` (1). WMS-only-sisältöön
kuului erityisesti kaavoitus- ja taustakarttatasoja, MML:n maastotietokannan
yhdistelmäkarttoja sekä muita valmiiksi tyyliteltyjä karttatuotteita.

WMS GetMap tukee muun muassa PNG-, JPEG-, TIFF/GeoTIFF-, PDF-, SVG-, KML- ja
KMZ-tuloksia. EPSG:3067 on laajasti tuettu. WMS palauttaa palvelimen piirtämän
kuvan ilman WFS-kohteiden attribuutti- ja muokkausominaisuuksia. Suomenväylät
listaa kaikki 175 WMS-tasoa nimenomaan live-WMS-valintoina, joten 63 WMS-only-
tasoa eivät näyttäydy virheellisesti feature classeina. WFS- ja WMS-valinnat
erotetaan `(WFS)`- ja `(WMS)`-tunnisteilla; luettelossa on kartoitushetken
sisällöllä yhteensä 288 Aino-valintaa.

## WFS- ja WMS-tasoluettelot

WFS GetCapabilities palautti kartoitushetkellä 113 tasoa. Työkalu ei kovakoodaa
tasojen nimiä, vaan lukee luettelon palvelusta aina token- ja välimuistiavaimen
mukaisesti. Näin palveluun myöhemmin lisättävät tai sieltä poistettavat tasot
päivittyvät käyttöliittymään.

Myös WMS-luettelo luetaan dynaamisesti. Vain sellaiset WMS `Layer` -elementit,
joilla on tekninen `Name`, tuodaan valittaviksi. Otsikko näytetään käyttäjälle,
mutta tekninen nimi säilytetään ArcGIS Pron alitason yksiselitteistä valintaa
varten. Kartoitushetken 175 WMS-tasoa jakautuivat näin:

| Nimiavaruus | Tasoja |
|---|---:|
| `aineisto` | 60 |
| `ymparistoaineistot` | 53 |
| `aluejaot` | 21 |
| `kaavoitus` | 20 |
| `geologia` | 20 |
| `taustakartat` | 1 |

| Nimiavaruus | Tasoja | Sisällön pääryhmät |
|---|---:|---|
| `ymparistoaineistot` | 50 | SYKE:n suojelu-, Natura-, pohjavesi-, tulva-, valuma- ja maisema-aineistot sekä Museoviraston ja BirdLifen aineistot |
| `aineisto` | 25 | rakennuspuskurit, Digiroad, Fintraffic, kiinteistörekisterikartta, maakuntakaavojen tuulivoima ja rakennukset |
| `aluejaot` | 20 | kunta- ja maakuntarajat, YKR-aluejaot, PAAVO-postinumeroalueet, seutukunnat, AVI/ELY, toimipaikat ja väestöruudut |
| `geologia` | 18 | GTK:n kallioperä-, maaperä-, happamat sulfaattimaat, maa-ainesluvat ja geologiset muodostumat |

Esimerkkejä laajoista aineistoperheistä:

- SYKE:n meri- ja vesistötulva-alueet useilla toistuvuuksilla;
- Natura 2000 -alueet, SAC-, SCI- ja SPA-aineistot sekä viivakohteet;
- valtion ja yksityiset luonnonsuojelualueet, erämaa-alueet ja muut
  suojelualueet;
- Museoviraston muinaisjäännökset, rakennettu kulttuuriympäristö ja suojellut
  kohteet piste-, viiva- ja alue-esityksinä;
- GTK:n maaperä- ja kallioperäkartat sekä kartoitus- ja tutkimuspisteet;
- Tilastokeskuksen aluejaot, PAAVO-postinumeroalueet, toimipaikat ja 1/5 km
  väestöruudut;
- MML:n kunta- ja maakuntarajat sekä kiinteistörajat, palstatunnukset ja
  rajamerkit;
- Digiroad-linkit sekä Fintrafficin lentoasemat, lentoesteet ja
  korkeusrajoitusalueet.

## Skeemat, geometriat ja koordinaatistot

DescribeFeatureType onnistui kaikille 113 tasolle. Jokaisella tasolla
geometriakentän nimi oli `geom`; tasoa kohti oli yksi geometriakenttä.
Skeemoissa oli 1–113 attribuuttikenttää, keskimäärin noin 9 kenttää tasoa
kohti. Palvelu ilmoitti geometriatyypin yleisenä GeometryPropertyType-tyyppinä
109 tasolle sekä tarkempana surface-, curve- tai point-tyyppinä neljälle
tasolle. Käytännön GeoJSON-testeissä palautui Point-, LineString-, Polygon- ja
MultiPolygon-geometrioita.

110 tason oletuskoordinaatisto oli EPSG:3067 ja kolmen Fintraffic-tason
EPSG:4326. Suomenväylät pyytää kaikki GetFeature-tulokset `srsName=EPSG:3067`-
parametrilla. Palvelin muunsi myös EPSG:4326-lähtöiset Fintrafficin kohteet
onnistuneesti EPSG:3067:ään.

## Tuonti ArcGIS Prohon

Työkalun Aino-polku toimii seuraavasti:

1. token lisätään GetCapabilities-, DescribeFeatureType- ja GetFeature-
   pyyntöjen query-parametriksi;
2. tasoluettelo ja tekniset `typeName`-arvot luetaan dynaamisesti;
3. geometriakenttä ja mahdollinen vakaa järjestyskenttä luetaan
   DescribeFeatureType-skeemasta;
4. rajaus lähetetään EPSG:3067 BBOXina;
5. aineisto haetaan GeoJSON-sivuina `maxFeatures`/`startIndex`-parametreilla;
6. sivut kootaan erissä ArcGISin JSONToFeatures-muunnokseen;
7. tulos leikataan tarkasti käyttäjän rajaukseen paikallisessa scratch-GDB:ssä;
8. valmis taso kopioidaan käyttäjän geodatabaseen tai kansioon ja lisätään
   aktiiviseen karttaan.

Ainolle käytetään BBOX-hakua ja paikallista Clip-vaihetta. Näin toteutus ei ole
riippuvainen palvelinkohtaisen CQL-suodatuksen yksityiskohdista, ja lopullinen
rajaus vastaa tarkasti Suomenväylien muita lähteitä.

WMS-valinnan polku on erillinen:

1. palvelun puhdas OWS-osoite annetaan ArcGIS Prolle palvelutyypillä `WMS`;
2. token annetaan `custom_parameters`-sanakirjassa, jolloin se välittyy
   GetCapabilities-, GetMap- ja GetFeatureInfo-pyyntöihin mutta ei URL-lokiin;
3. ArcGISin WMS-komposiittitasosta etsitään ensisijaisesti valittu tekninen
   `ServiceLayerID` ja toissijaisesti palvelun otsikko;
4. muut WMS-alitasot kytketään pois näkyvistä;
5. `taustakartat`-nimiavaruus sijoitetaan **Taustakartta**-ryhmään kartan
   tasopinon alimmaiseksi ja muut tasot **Aino WMS** -ryhmään.

WMS-tasoa ei leikata aluerajauksella eikä kopioida tulosgeodatabaseen, koska se
säilyy ArcGIS Prossa dynaamisena karttapalveluna. Jos valittua alitasoa ei löydy
ArcGISin palvelupuusta, lisätty palvelu poistetaan ja ajo ilmoittaa virheen;
koko 175 tason palvelua ei jätetä vahingossa näkyviin.

## Tunnisteen käsittely

Käyttöliittymän **Aino-token** on `GPStringHidden`-kenttä. Token:

- säilytetään Windowsin käyttäjäkohtaisella DPAPI-salauksella;
- lisätään vain ajonaikaisiin Aino-pyyntöihin;
- peitetään kaikista työkaluviesteistä ja virheistä;
- poistetaan URL:sta ennen palveluosoitteen lokittamista;
- korvataan SHA-256-tiivisteellä tasoluettelon välimuistiavaimessa;
- ei sisälly tasomäärityksiin, lähdekoodiin eikä Git-historiaan.

## Tehdyt yhteystestit

- WMS 1.3.0 GetCapabilities: HTTP 200, 175 nimettyä tasoa.
- WFS 1.1.0 GetCapabilities: HTTP 200, 113 tasoa.
- DescribeFeatureType: 113/113 tasoa onnistui.
- Oulun testirajauksessa piste-, viiva-, polygoni- ja multipolygonikohteet
  palautuivat GeoJSONina EPSG:3067:ssä.
- WFS 1.1 -sivutus testattiin kahdella peräkkäisellä sivulla sekä kunta- että
  Digiroad-aineistolla; kohdetunnukset eivät toistuneet.
- Ilman tietokannan luonnollista järjestystä palvelun palauttama virhe
  tunnistettiin, skeemasta valittiin `id`-kenttä ja pyyntö uusittiin vakaalla
  `sortBy=id`-järjestyksellä.
- Fintrafficin EPSG:4326-lähtöinen lentoasemataso palautui pyynnöstä oikein
  EPSG:3067-koordinaatistossa.

Erittäin suuria aineistoja, kuten koko Suomen miljoonien kohteiden
Digiroad-linkkejä, kannattaa käyttää alueellisella rajauksella. Työkalu sivuttaa
vastauksen ja hylkää katkenneen tai maksimisivumäärään päätyneen vajaan
aineiston, mutta koko maan haku voi silti olla palvelulle ja ArcGIS Prolle
raskas.
