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
