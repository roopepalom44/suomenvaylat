## Suomenväylät QGIS 0.2.11

- Korjasi Ainon tasoluettelon HTTP 403 -virheen oikeallakin tokenilla: palvelun Cloudflare estää Pythonin oletus-User-Agentin (error code 1010), joten kaikki Ainon urllib-pyynnöt tunnistavat nyt Suomenväylät-lisäosan. Korjaus kattaa WFS- ja WMS-tasoluettelot sekä rajapintatyylit.

## Suomenväylät QGIS 0.2.2

- Järjesti aineistotyönkulun lähteisiin, aineistovalintaan sekä aluerajaukseen ja tallennukseen.
- Näyttää vain valituille palveluille tarvittavat tunnuskentät ja jakaa MML-avaimen aineistohaun ja taustakarttojen välille.
- Säilyttää valitut tasot, kun käyttäjä muuttaa hakusuodatusta, ja sallii valita kaikki näkyvät tulokset kerralla.
- Piilottaa aluerajauksen ja tallennuskansion käytöltä live-karttatasojen valinnassa. Keskeytys näytetään erikseen aineistovirheistä.

## Suomenväylät QGIS 0.2.1 — esijulkaisu

Uusi Windows-asennus: lataa `Suomenvaylat-QGIS-0.2.1-Windows.zip`, pura se ja suorita `install_windows.bat` QGISin ollessa suljettu. Asennin kopioi lisäosan QGIS-profiileihin ja aktivoi sen. Vaihtoehtoisesti asenna `Suomenvaylat-QGIS-0.2.1.zip` QGISin **Lisäosat → Hallitse ja asenna lisäosia → Asenna ZIP-tiedostosta** -toiminnolla. Vaatii QGIS 3.44:n.

Tässä versiossa toimivat WFS- ja OGC API Features -aineistojen haku aluerajauksella, hallinnolliset alueet, Kapsin GeoTIFF-lataus, OSM:n Overpass-haku sekä Kapsin ja MML:n taustakarttatasot. Uutta: useita lähteitä voi valita samaan ajoon, Aino WMS- ja MML Karttakuva -tasot voidaan lisätä live-tasoina, ja Aino-token säilytetään QGISin tunnistautumistietokannassa. QGIS 3.44:ssä on testattu viiden WFS-lähteen tasoluettelot, Väylän rajattu lataus, Kapsin rasterilataus, OSM:n rajattu lataus, OGC-sivutus sekä tunnistautumista vaativien tasojen määritys ilman oikeita palvelutunnuksia.

**Tämä ei vielä ole toiminnallisesti identtinen ArcGIS Pro -version kanssa.** Aino WMS:n, MML Karttakuvan ja MML-vektoritiilien oikeaa palvelukäyttöä ei ole voitu validoida ilman tunnuksia. OSM-attribuutit tallennetaan eri tavalla. Tarkempi tilanne: [QGIS-ohje](https://github.com/roopepalom44/suomenvaylat/blob/main/qgis_plugin/README.md).
## Suomenväylät QGIS 0.2.3

- Julkaistaan samassa GitHub-releasessa ArcGIS Pro AddInX:n kanssa sekä QGISin omana asennus-ZIPinä että Windows-asennus-ZIPinä.
- Traficom Oskari -lähde näyttää kaikki 76 tasoa. Suorat WFS-vastineet (57), Oskarin omat WFS-tasot (4), WMS-kuvatasot (10) ja WMTS-karttasarjat (5) tallentuvat alueen mukaan.
- Oskarin WFS-, WMS- ja WMTS-lataus tarkistettu oikealla Traficomin palvelulla QGIS 3.44:ssä.
## Suomenväylät QGIS 0.2.4

- Tallennetun QGIS-projektin kansio täyttyy oletuksena ladattavien aineistojen tallennuskansioksi.

## Suomenväylät QGIS 0.2.5

- Kapsista ladattu GeoTIFF tallentaa EPSG:3067-koordinaattijärjestelmän, jolloin rasteri sijoittuu QGISissä Suomeen.
- MML:n XYZ-vektoritiilet pyydetään Web Mercator -tiilistössä, jota QGIS käyttää näillä tasoilla.

## Suomenväylät QGIS 0.2.6

- Jos QGIS-projektilta puuttuu koordinaattijärjestelmä, lisäosa asettaa sen ensimmäisen ladatun tason mukaan ennen tason lisäämistä. Näin QGIS voi sovittaa EPSG:3067-aineistot ja eri CRS:ssä olevan karttapohjan yhteen.
- Latauksen valmistumisviesti kertoo, kun projektin CRS asetettiin automaattisesti.

## Suomenväylät QGIS 0.2.7

- OSM-relaatiot kootaan alueiksi (aiemmin ne pudotettiin, joten esim. *Hallinnolliset alueet* jäi tyhjäksi). Suljetut tiet, aidat ja muut viivakohteet pysyvät viivoina.
- Jos OSM-tasossa on useita geometriatyyppejä, pisteet, viivat ja alueet tallentuvat omiksi tasoikseen.
- OGC API Features -latauksen kentät päätellään kaikkien sivujen kohteista, joten myöhemmillä sivuilla esiintyvät ominaisuudet eivät katoa.
- Muut kuin Väylän ja DigiRoadin vektoriaineistot (myös Traficom Oskari) leikataan rajaukseen kuten ArcGIS Prossa.
- Oma viivarajaus muutetaan kohteiden konveksiksi peitteeksi kuten ArcGIS Prossa; tason valinta rajaa käytettävät kohteet.
- Tasoluettelo ja lataukset ajetaan taustatehtävinä; latauksen voi keskeyttää.
- Tunnistautumisasetukset päivitetään nimellä eikä uutta kopiota luoda jokaisella tason lisäyksellä.
- Tunnisteotsaketta ei lähetetä uudelleenohjauksessa toiselle palvelimelle, eikä HTTPS-yhteyttä alenneta HTTP:ksi.
- Windows-asennin poistaa edellisen version tiedostot ennen asennusta.

## Suomenväylät QGIS 0.2.8

- Rajaukseen leikatusta aineistosta säilytetään vain lähdetason geometriatyyppi. Rajaa sivuavat alueet eivät enää muutu alue- ja viivaosien kokoelmiksi, joiden koordinaatistoa QGIS ei tunnistanut (esim. Liiterin taajamat eivät latautuneet 0.2.7:ssä).

## Suomenväylät QGIS 0.2.9

- Uusi lähde **Tilastokeskus**: kaikki Tilastokeskuksen WFS-tasot (tilastointialueet, Paavo-postinumeroalueet, väestöalueet ja -ruudut, tieliikenneonnettomuudet, oppilaitokset). Rajaukseen osuvat alueet ja ruudut tallennetaan kokonaisina, koska tilastoarvot koskevat koko aluetta.
- OpenStreetMap-haku lähettää tunnistettavan User-Agentin. overpass-api.de hylkää nykyisin Pythonin oletustunnisteen (HTTP 406), joten OSM-haut epäonnistuivat, kun ensisijainen Overpass-palvelu oli ruuhkautunut.
