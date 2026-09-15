# ArcGIS Pro -hyväksymis- ja vertailutestit

Nämä testit vaativat ArcGIS Pron ja `arcpy`-ympäristön. Tavalliset Python-testit eivät voi todentaa geoprocessing-operaatioiden todellista suorituskykyä.

## 1. Hallinnollinen luokka / Etelä-Pohjanmaa

1. Valitse lähteeksi **Väylä**, rajaukseksi **Maakunta / Etelä-Pohjanmaa** ja tasoksi **Hallinnollinen luokka (Digiroad)**.
2. Varmista lokista tason näyttönimi, lähde, sanitisoitu WFS-osoite, `typeName`, geometriakenttä, sivukoko sekä scratch- ja lopputulospolut.
3. Jos yhtenäinen CQL GET saa HTTP 414 -vastauksen ja POST epäonnistuu, varmista että lokiin tulee ensin pilkottujen CQL-osien yritys. BBOX saa käynnistyä vasta, jos myös pilkotut CQL GET/POST -pyynnöt epäonnistuvat.
4. Jos CQL onnistuu, varmista että Clip näkyy muodossa `ohitettu (CQL palauttaa kokonaiset leikkaavat geometriat)`.
5. Varmista, että tuloksessa ovat kokonaiset rajauksen kanssa leikkaavat geometriat. CQL `INTERSECTS` ei katkaise geometrioita aluerajaan.

## 2. Aikakirjanpidon eheys

Tarkista jokaiselta WFS-sivulta erilliset ajat: requestin muodostaminen, verkkopyyntö, vastauksen lukeminen, JSON-jäsennys, JSON-kirjoitus, `JSONToFeatures` ja sivun kokonaisaika.

Tarkista tason ja koko työkalun yhteenvedoista:

```text
vaiheiden summa + Muu-aika = kokonaisaika
```

Pyöristyksen vuoksi enintään muutaman millisekunnin esitysero on hyväksyttävä. Käyttämättömän vaiheen pitää näkyä tekstinä `ei käytetty`, `ohitettu` tai `ei tarpeen`.

## 3. CopyFeatures ja verkko-GDB

1. Aja sama taso, rajaus ja sivukoko ensin paikalliseen file geodatabaseen ja sitten V-aseman file geodatabaseen.
2. Vertaa lokien varsinaista `lopullinen CopyFeatures` -aikaa.
3. Varmista, että verkko-GDB:n tulosnimessä on ajokohtainen tunniste ja nimitarkistus valmistuu ilman hitaita verkon `Exists`-kyselyitä.
4. Varmista, että kohdemäärän laskenta tehdään nopeasti paikallisesta staging-aineistosta ennen lopullista kopiointia.

## 4. JSONToFeatures-vertailun portti

Nykyinen sivukohtainen `JSONToFeatures` on lähtötaso. Tallenna lokista samalla tasolla, rajauksella ja sivukoolla ainakin JSON-kirjoitus, `JSONToFeatures`, Merge ja haun kokonaisaika.

Yhdistetyn JSONin tai suoran feature class -kirjoituksen toteutusta ei pidä ottaa tuotantoon ennen rinnakkaista ArcGIS Pro -testiä, jossa verrataan:

- kohdemäärää ja geometrioita;
- kenttiä ja tietotyyppejä;
- JSON-kirjoituksen, muunnoksen ja Mergen yhteisaikaa;
- muistinkäyttöä ja virhepalautumista.

## 5. Scratch ja tunnisteet

- Onnistuneen ajon jälkeen lokissa ilmoitettua `%TEMP%\suomenvaylat_*`-hakemistoa ei pidä enää olla.
- Virheajossa scratch säilyy aina. Siivousvirhe ei saa peittää varsinaista virhettä.
- WFS-osoitteessa tai virheilmoituksissa ei saa näkyä API-avainta, käyttäjätunnusta eikä salasanaa.
- `%APPDATA%\Suomenvaylat\service_credentials.json`-tiedoston arvojen pitää alkaa `dpapi:`; selväkielisiä tunnisteita ei saa jäädä tiedostoon.

## 6. Karttapaikka ja API-avain

1. Valitse lähteeksi **Karttapaikka** ilman API-avainta. Listaan pitää tulla
   Maanmittauslaitoksen nykyiset INSPIRE WFS -tasot.
2. Syötä Karttapaikan API-avain. Listan pitää latautua uudelleen ja täydentyä
   **Maastotiedot (OGC API Features)** -kokoelmilla, kuten `tieviiva` ja
   `rakennus`. Vanha `(ei osumia – tyhjennä haku)` ei saa jäädä
   ladattavaksi valinnaksi.
3. Valitse OGC-taso pienellä rajauksella. Varmista, että koordinaatit
   projisoidaan EPSG:3067:ään ennen paikallista Clip-vaihetta ja että lokiin
   tulee OGC-yhteenveto. API-avainta ei saa näkyä lokissa, URL:ssa tai
   virheilmoituksessa.

## 7. MML-, Kapsi- ja Karttapaikka-valinnat

1. Valitse **MML**, syötä API-avain ja varmista, että kiinteistöjen OGC API
   -kokoelmat tulevat dynaamisesti valikkoon. Valitse esimerkiksi
   **Kiinteistojaotus** ja lataa pieni alue. Varmista lokista OGC API Features
   -haku, EPSG:3067-projisointi ja paikallinen Clip.
2. Valitse **Kapsi**, valitse listattu taso ja varmista, ettei valinta katoa
   validointikierroksella. Lataa pieni alue ja tarkista kuvan sijainti kartalla.
3. Valitse **Karttapaikka**, valitse ensin avoin INSPIRE-taso ja sitten
   API-avaimella Maastotiedot-taso. Kummankaan valinta ei saa kadota heti.

## 8. MML:n rinnakkaiset taustakarttatasot

1. Avaa **Taustakartat (MML/Kapsi)**, syötä MML API-avain ja valitse
   **Taustakartta**, **Maastokartta** tai **Kiinteistojaotus**.
2. Varmista lokista, että MML TileJSON lisätään `VECTOR_TILE`-tyyppinä
   `Taustakartta`-ryhmään ja että API-avain välitetään custom request
   parameterina. Avainta ei saa näkyä URL:ssa tai lokissa.
3. Tarkista kartalta, että valittu MML vector tile -taso on näkyvä. MML-polussa
   ei pidä syntyä paikallista RGB-rasteria eikä WMTS-tiilien lataus-/mosaiikki-
   vaiheita.
4. Valitse **Kapsi** ja varmista erikseen, että rajaus, rasterilataus ja
   tallennus File GDB:hen toimivat edelleen.

## 9. OpenStreetMap / POI-pisteet

1. Valitse lähteeksi **OpenStreetMap** ja tasoksi **POI-pisteet**. Tee ensin
   pieni kuntarajaus, esimerkiksi Helsingin keskusta omalla polygonilla.
2. Varmista lokista hakutapa `Geofabrik POI-luokitus`, Overpass-yhteys ja
   paikallinen Clip. API-avainta ei tarvita.
3. Tarkista, että lopputulos on pistetaso ja sisältää kentät `osm_id`,
   `osm_type`, `code`, `fclass` ja `name`.
4. Tarkista ainakin `node`- ja `way`-arvoja `osm_type`-kentästä. `way`- ja
   `relation`-kohteiden geometrian pitää olla alueen keskipiste, ei polygoni.
5. Etsi moniluokkainen kohde ja varmista, että sama `osm_id` voi esiintyä
   usealla rivillä eri `code`/`fclass`-arvoilla.
6. Testaa yhteyskatkon varautuminen estämällä ensisijainen Overpass-osoite
   testiympäristössä. Haun pitää jatkua toisesta osoitteesta. Laajassa ja
   tiheässä rajauksessa lokiin pitää tulla ruudutuksen eteneminen.
