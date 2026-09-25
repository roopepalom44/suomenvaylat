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
