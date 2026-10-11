# Auditointilöydösten käsittely 11.10.2026

Lähde: projektin uusin paikallinen [AUDIT_REPORT.md](../AUDIT_REPORT.md),
päivätty 11.10.2026. Alkuperäinen raportti säilytetään löydöshetken kuvauksena.
Tämä tiedosto kertoo toteutuksen ja perustellut tarkennukset kaikkiin 36 tunnisteeseen.
Versiot: ArcGIS 1.0.22, QGIS 0.2.12. Käyttäjän pyynnöstä 1.0.23 yhdistää
taustakartat Suomenväylät-työkaluun; erillinen painike ja työkaluluokka poistettiin.

| Löydös | Toteutus / tarkennus |
|---|---|
| F-CRIT-01 | `download_to_files` käyttää taustalla geometria-arvoja, verkkopyyntöjä ja tiedostokirjoittimia. Se palauttaa vain sanakirjoja. `finished` avaa QgsMapLayerit, soveltaa tyylit ja lisää tasot projektiin pääsäikeessä. `moveToThread` poistettiin ja muunnoskonteksti kopioidaan ennen työn käynnistämistä. |
| F-CRIT-02 | WFS:n HTTP-sivutus lukee vastaukset eksplisiittisesti, tarkistaa hits/numberMatched-arvot ennen paikallista suodatusta sekä havaitsee toistuvat ja tyhjät sivut. Verkko-/JSON-/GML-virhe hylkää latauksen. Rajauksen jälkeen kirjoitettujen kohteiden määrää ei verrata koko BBOX-haun määrään. |
| F-HIGH-01 | Geometriakenttä selvitetään DescribeFeatureType-vastauksesta. Tuettujen lähteiden tarkka EPSG:3067-monikulmio lähetetään CQL INTERSECTS -suodattimena, tarvittaessa KVP POSTilla. Konveksia peitettä ei käytetä korvaamaan tarkkaa rajausta. |
| F-HIGH-02 | ArcGIS HTTP-transport neuvottelee gzip/deflate-pakkauksen ja purkaa vastaukset kokorajan puitteissa. Katkennut vastaus nostaa virheen. |
| F-HIGH-03 | WFS GetCapabilities määrää JSON-outputFormatin; JSONia tukemattoman palvelun GML avataan OGR:llä. QGISin WFS URI:hin ei lisätä tukemattomia parametreja. |
| F-HIGH-04 | ArcGIS näyttää tasokohtaisen progressorin ja palauttaa sen finally-lohkossa. Raportin väite `canRunInBackground=False`-asetuksen vaikutuksesta ArcGIS Prohon on virheellinen: [Esrin dokumentaatio](https://pro.arcgis.com/en/pro-app/2.7/arcpy/geoprocessing_and_python/defining-a-tool-in-a-python-toolbox.htm) kertoo asetuksen koskevan vain ArcGIS Desktopia. |
| F-HIGH-05 | QGISin JSON- ja WFS-pyynnöt käyttävät transienttien HTTP-/verkkovirheiden uudelleenyrityksiä, eksponentiaalista viivettä ja Retry-After-arvoa. Pysyviä 4xx-virheitä ei toisteta. |
| F-HIGH-06 | WFS-, WMS-, OGC- ja tyylivirheiden ExceptionText/ServiceException jäsennetään. ArcGIS peittää tunnukset myös jäsennetystä viestistä. |
| F-HIGH-07 | WKT-välivaihe poistettiin. Yksityinen levypuskuri sisältää WKB:n ja attribuutit. Kirjoitusvaihe tarkistaa keskeytyksen ja säilyttää geometriatyypin sekä Z/M-ulottuvuudet. |
| F-HIGH-08 | Clip-varareitti korjaa syötteen lisäksi ajokohtaisen rajausaineiston. Käyttäjän alkuperäistä rajausaineistoa ei korjata paikallaan. |
| F-HIGH-09 | Raportin ehdottama kirjoitusvirheiden ohitus hylättiin: se tuottaisi F-CRIT-02:n kaltaisen vajaan aineiston. Kirjoitusvirhe raportoidaan, kirjoitin suljetaan ja keskeneräinen GPKG poistetaan; muut valitut tasot voivat silti valmistua. Tämä eheysperiaate on testattu. |
| F-HIGH-10 | Epäonnistunut MML:n oletustyylin lataus kirjataan varoituksena, eikä kelvollista vektoritiilitasoa hylätä. |
| F-HIGH-11 | QGIS-vähimmäisversio laajennettiin 3.34:ään ja GeoJSON-geometrialle lisättiin OGR/WKB-varareitti. Väite, ettei 3.44 olisi julkaistu, on vanhentunut; [QGISin virallinen versiolista](https://changelog.qgis.org/en/version/list/) sisältää 3.44:n. Paikallinen natiivivalidointi tehtiin 3.44.14:llä, vanhan API:n puuttuminen simuloitiin. |
| F-HIGH-12 | ArcGIS siivoaa ajokohtaisen scratchin myös osittaisen epäonnistumisen jälkeen. Virheitä ei enää säilytetä automaattisesti levyllä. Lukitus-/siivousvirhe ilmoitetaan; olemassa olevia vanhoja tai muiden ajojen kansioita ei poisteta nimen perusteella. |
| F-HIGH-13 | `.pyt` on pieni latauskuori. ArcGISin työkalut ja työnkulku ovat `arcgis_downloader.py`:ssä, transport/rekisteri/adapterit `arcgis_support.py`:ssä. HTTP/OGC-turvakoodi ja OSM-geometriat ovat alustariippumattomissa yhteisissä moduuleissa. Kaikki moduulit sisältyvät AddInX:ään. Työnkulkumoduuli on edelleen suuri; tätä ei esitetä täydellisenä arkkitehtuurin uudelleenkirjoituksena. |
| F-HIGH-14 | QGISin oikeat protokollamoduulit testataan tavallisessa unittest-ajossa. Natiivi QGIS saa oman verkottoman CI-työn: geometria-, GeoPackage-, UI-, virhe- ja taustatehtävätestit. Julkaisu odottaa näitä tarkistuksia. |
| F-MED-01 | Kun ladatussa aineistossa on ei-tyhjä gml_id, deduplikointi käyttää sitä ja geometriaa. Muut lähteet säilyttävät täydellisen attribuuttivertailun; yleistä id-kenttää ei oleteta uniikiksi. |
| F-MED-02 | WGS84-laajuus lasketaan yhdistetystä geometriasta `projectAs`-muunnoksella ilman väliaikaista projektioaineistoa. ArcGIS JSONToFeatures tarvitsee edelleen tiedostosyötteen; sen olemassaoloa ei tulkita itsessään virheeksi. |
| F-MED-03 | QGISin lataussilmukka käyttää esivalmisteltuja lähde- ja kohderajauksen QgsGeometryEnginejä. |
| F-MED-04 | OSM-kohteet käsitellään ruutu kerrallaan ja puskuroidaan WKB:nä levylle. Koko alueen QgsFeature-listat poistettiin. Kohdetunnusten joukko jää muistiin ruutujen duplikaattien poistamiseksi. |
| F-MED-05 | Aluenimet luetaan SQLite-attribuuttikyselyllä ja välimuistitetaan tiedostopolun sekä muutosajan mukaan. Geometrioita ei skannata nimiluetteloa varten. |
| F-MED-06 | Kaikki muunnettavat shapefile-kentät täytetään yhdellä UpdateCursor-kierroksella. DBF-nimet ovat enintään 10 merkkiä ja erottuvat myös olemassa olevista kentistä. |
| F-MED-07 | APPDATA-tallennus oli jo olemassa, joten raportin pääväite ei vastannut koodia. Vain varareitti korjattiin: LOCALAPPDATA tai käyttäjän kotihakemisto, ei asennuskansio. DPAPI-suojaus säilyy. |
| F-MED-08 | Molemmat paketit käyttävät samoja OSM-geometrioita, POI-JSONia ja uudelleenohjausten turvakoodia. ArcGIS-paketointi ja projekti lisäävät yhteiset tiedostot. |
| F-MED-09 | Käyttäjä tarkensi yhden työkalun toimintatavan: erillinen Taustakartat-painike ja luokka poistettiin 1.0.23:ssa. MML:n kolme vektoritiilikarttaa löytyvät Suomenväylät-työkalun MML-aineistovalikosta. Kapsin rasterilataus oli jo samassa työkalussa ja säilyy siellä. Pelkkä live-karttavalinta ei edellytä aluerajausta tai tallennuskohdetta. |
| F-MED-10 | WFS BBOX -varareitti käyttää ArcGISin tavoin 1 000 metrin hakumarginaalia. Tarkka paikallinen rajaus säilyy. CQL-reitillä käytetään tarkkaa monikulmiota. |
| F-MED-11 | Karttakuvan tasoluettelo tarjoaa EPSG:3067-WMTS-tasot ensin. QGIS käyttää natiivia WMTS-matriisia ja tunnistautumisasetusta. WMS säilyy vaihtoehtona ja WMTS:n epäonnistumisen varareittinä. Tunnuksellista palvelua ei tässä validoitu oikeilla tunnuksilla. |
| F-MED-12 | Kirjoittimet vapautetaan ennen siivousta. TemporaryDirectory-siivoaminen ja GPKG-sivutiedostojen poisto eivät korvaa alkuperäistä latausvirhettä. Siivouksen lukitusvirhe kirjataan. GeoPackage-tyylin tallennuksen jälkeen OGR-lukuyhteys avataan uudelleen, jotta SQLite-skeeman muutos ei riko tason seuraavaa kohdehakua. |
| F-MED-13 | XML jäsennetään kokorajoitettuna; DTD/ENTITY-määritykset hylätään myös UTF-16/32-enkoodauksissa. Sama tarkistus kattaa myös tyylien WMS-tasoluettelot. |
| F-MED-14 | 999 kohteen keinotekoinen prosenttiraja poistettiin. Tilasignaali näyttää vaihtuvan vaiheen ja todellisen kohdemäärän; tunnettu kokonaismäärä ohjaa edistymistä ja valmistunut taso nostaa kokonaisprosenttia. |
| F-LOW-01 | Kapsin live-ortokuva pyytää JPEG-muotoa, viivakartat PNG:tä. Tiedostolataus käytti jo JPEG:iä. |
| F-LOW-02 | WMTS:n GDAL-tietolähteet ja tulos suljetaan finally-lohkossa myös virheessä. |
| F-LOW-03 | Molemmat toteutukset käyttävät yhteistä Overpass-listaa (overpass-api.de, overpass.kumi.systems). maps.mail.ru poistettiin. |
| F-LOW-04 | Hakukentän 200 ms QTimer vähentää suodatuksia; listakohteet piilotetaan ja käytetään uudelleen. Valitse näkyvät -toiminto ohittaa piilotetut kohteet. |
| F-LOW-05 | Kaikki latausgeometrian transform-paluuarvot tarkistetaan. Virheellinen muunnos keskeyttää tason eikä tallennu hiljaisesti väärässä koordinaatistossa. |
| F-LOW-06 | WFS-, Kapsi- ja Traficom-tasoluetteloiden riippumattomat GetCapabilities-pyynnöt haetaan rajatulla rinnakkaisuudella. Tulosten järjestys säilyy. |

## Validointi

- Tavallinen unittest-ajo: 212 testiä, ei ArcGIS/QGIS-asennusta eikä verkkoyhteyttä. Uudet testit kattavat yhden julkisen työkalun, MML-livekarttojen ja Kapsi-rasterien yhdistämisen sekä alueparametrien validoinnin.
- Natiivi QGIS 3.44.14: 9 tiedosto-/virhetestiä sekä verkottomat auditointi- ja valintatestit. Auditointitesti käyttää oikeaa QgsTaskManageria ja osoittaa HTTP-työn tapahtuvan taustasäikeessä.
- Pienet oikeat WFS-haut: DigiRoad `dr_pysakki` (39 kohdetta), Tilastokeskus `vaki2024_1km` (4 kohdetta), Karttapaikka `gn:NamedPlace` (5 kohdetta, GML). Tulokset EPSG:3067:ssä, tallennetut määrät tarkistettu.
- Pyflakes sekä Python-syntaksitarkistukset.
- Release-käännös täydellä MSBuildilla ja SDK 3.5:llä; AddInX:n moduulit ja C#-tyypit tarkistetaan paketoinnissa. Koneen käännösympäristö ei pystynyt lataamaan ArcGIS Desktop -runtime-assemblyja, joten CLR-tarkistus käytti olemassa olevaa metadata-varareittiä. ArcGIS Pron ribbonia ei ajettu sovelluksessa.
- QGISin molempien ZIP-pakettien luonti ja sisältötarkistukset.

CI:n natiivi QGIS-työ lisättiin tämän muutoksen mukana. Sen GitHub-tulos tulee
pushin jälkeen; paikallinen onnistuminen ei korvaa palvelimella tehtävää tarkistusta.
