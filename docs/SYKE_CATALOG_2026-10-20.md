# SYKEn puuttuvat tasot – korjaus 20.10.2026

ArcGIS Pro 1.0.24 ja QGIS 0.2.13 korjaavat Syke-valinnan WFS-tasoluettelon.

## Syy ja korjaus

Molempiin versioihin oli rekisteröity vain
`https://paikkatiedot.ymparisto.fi/geoserver/inspire_ps/wfs`.
Se palautti 12 suojelualueiden tasoa. Pohjavesialueet sijaitsevat
`inspire_ge`-työtilassa, joten ne eivät voineet löytyä alkuperäisestä hausta.
SYKEn globaali `/geoserver/wfs` palauttaa virheen eikä tarjoa kaikkia työtiloja.

Yhteinen `syke_sources.py` lukee WFS-osoitteet
[SYKEn virallisesta rajapintahakemistosta](https://www.syke.fi/fi/ymparistotieto/avoimet-rajapinnat/paikkatietorajapinnat).
Hakemistossa oli tarkistushetkellä 76 erillistä WFS-palvelua, mukaan lukien
Liiterin WFS-palvelut. Jokaisen palvelun tasot luetaan GetCapabilities-vastauksesta
enintään neljällä rinnakkaisella pyynnöllä. Uudet hakemistoon julkaistut WFS-
työtilat löytyvät seuraavalla tasoluettelon päivityksellä. Pelkät WMS-/WCS- ja
OGC API -palvelut eivät kuulu tähän WFS-luetteloon.

Mukana on päivätty varaluettelo hakemistosivun häiriöitä tai rakenteen
muuttumista varten. Varaluettelon käytöstä sekä palvelukohtaisista virheistä
annetaan varoitus; onnistuneiden palvelujen tasot säilyvät valittavina.
Virheellistä tai puutteellista ArcGIS-luetteloa ei tallenneta levyvälimuistiin.
Välimuistiavain päivitettiin, jotta vanha 12 tason luettelo ei peitä korjausta.
Päivitä tasoluettelo -valinta tyhjentää myös lähderekisterin muistivälimuistin.

ArcGIS säilyttää nyt jokaisen WFS-tason oman palveluosoitteen sekä latausta
että symboliikkaa varten. Tämä korjaa myös Liiterin erillisten työtilojen reitityksen.
QGISin vastausmuodon valinta rajattiin GetFeature-operaatioon ja
`application/json` asetettiin etusijalle. SYKE mainostaa `application/geo+json`-
muotoa, mutta esimerkiksi `inspire_ge` vastasi sillä HTTP 400 -virheeseen:
`Failed to find response for output format application/geo+json`.

## Validointi

- 225 verkkoyhteydetöntä yksikkötestiä läpäisty, mukana uudet hakemiston,
  pohjavesitasojen, palveluosoitteiden, puutteellisen katalogin, välimuistin ja
  vastausmuodon regressiotestit. Pyflakes ja Python-käännöstarkistus läpäisty.
- QGIS 3.44.14:n 11 natiivia lataus- ja katalogitestiä läpäisty.
- Molempien versioiden oikea tasoluettelo palautti **2 119 tasoa**.
  Mukana olivat `inspire_ge:GE.Groundwaterbody` (Pohjavesialueet) ja
  `inspire_ge:GE.Pohjavesialuerajat` (Pohjavesialuerajat).
- ArcGIS Pron omalla Pythonilla ladattiin pieneltä testialueelta 6
  pohjavesialuekohdetta ja 11 rajaviivaa geodatabaseen. Tallennetut määrät
  vastasivat palvelusta saatuja määriä; koordinaatisto oli EPSG:3067.
- QGISissä ladattiin ja avattiin samalta testialueelta 6 pohjavesialuekohdetta
  ja 9 rajaviivaa GeoPackage-tiedostoista EPSG:3067-koordinaatistossa.
  QGIS leikkaa kohteet täsmälliseen rajaukseen, mikä selittää rajaviivojen
  määrän eron ArcGISin latausvaiheen BBOX-tulokseen.
- AddInX käännettiin ja paketoitiin paikallisesti; uusi yhteinen moduuli kuuluu
  sekä MSBuildin sisältöihin että `package-addin.ps1`-paketointiin. QGISin
  tavallinen ja Windows-asennuspaketti muodostettiin versiolle 0.2.13.

Viisi hakemistossa mainittua WFS-palvelua ei ollut tarkistushetkellä käytettävissä:
`liiteri_porot`, `syke_vesikasvivyohyke`, `syke_virtavesienlohikalakannat` ja
`ylalapin_luontotyypit` ilmoittivat WFS-palvelun olevan poistettu käytöstä;
`syke_arvokkaatpienvedet1990` palautti HTTP 404. Työkalu näyttää näistä
varoituksen. Näiden palvelujen sisältö riippuu SYKEn palveluiden saatavuudesta.
