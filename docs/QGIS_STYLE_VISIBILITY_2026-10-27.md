# QGISin näkymättömät ladatut kohteet – 0.2.17

Käyttäjän ilmoittama oire: tiedosto ja attribuutit latautuvat, mutta kohteet
näkyvät kartalla vasta symboliikan vaihtamisen jälkeen. Tarkkaa ongelmatasoa
ei ollut vielä tiedossa korjausta tehtäessä.

Sama oire toistettiin QGIS 3.44.14:ssä SLD:llä, jonka luokitteluarvo ei esiinny
ladatuissa kohteissa. QGIS palauttaa loadSldStyle-kutsusta onnistumisen, vaikka
mikään sääntö ei piirrä kohteita: karttakuvan ei-valkoisten pikselien määrä oli 0.
Pelkkä tyylitiedoston latauksen onnistuminen ei siis varmista karttanäkyvyyttä.

Korjaus lisää näkyvän varasymbolin niille kohteille, joille alkuperäinen tyyli
ei anna symbolia. Alkuperäinen sääntöpuu on oma ryhmänsä, joten sen omat
ELSE-säännöt ja osuvien luokkien värit säilyvät. Puuttuva kenttä tai
latautumaton kuvasymboli palauttaa näkyvän oletussymbolin. Kuvasymbolin
valmistelun epäonnistuminen välittyy myös taustatyöltä käyttöliittymälle;
alkuperäinen SLD säilytetään, mutta keskeneräistä tyyliä ei oteta käyttöön.

Kaikki seuraavat verkottomat kuvavientitapaukset läpäisivät QGISin omassa Pythonissa:

| Tapaus | Tarkistettu tulos |
| --- | --- |
| Osa arvoista sekä NULL ilman SLD-luokkaa | Osuvat kohteet alkuperäisellä punaisella, muut näkyvällä varasymbolilla |
| Mikään arvo ei osu SLD-luokkaan | Alkuperäinen QGIS-tuonti tuottaa tyhjän kuvan, korjattu ei |
| Tyylin suodatin viittaa puuttuvaan kenttään | Varoitus ja näkyvä oletussymboli |
| Lähdetyylin oma ELSE | Alkuperäinen sininen ELSE säilyy; varasymboli ei peitä sitä |
| Geometriasääntö mittakaavarajan ulkopuolella | Kohteet säilyvät näkyvinä varasymbolilla; lähdesymboli säilyy omalla alueellaan |
| Puuttuva palvelinpaikallinen PNG | Valmisteluvirhe välittyy työntekijältä ja näkyvä oletussymboli säilyy |

Jokainen tapaus kulkee download_to_files → GeoPackage-kirjoitus →
open_downloaded_layer -ketjun läpi. Kuvan värit ja näkyvyys tarkistetaan myös
GeoPackagen oletustyylin ja erillisen QML-tiedoston uudelleenavauksen jälkeen.

Oikean julkisen WFS-palvelun latausketju tarkistettiin lisäksi Väylän
Hailuodon nopeusrajoituksilla (28 kohdetta) ja Helsingin aidoilla (118 kohdetta).
Molemmista tuli näkyvät karttakuvat ilman renderöintivirheitä.
Hailuodon nopeusrajoitukset toimivat ennen tätä korjaustakin; nämä tarkistukset
eivät yksin yksilöi käyttäjän alkuperäistä ongelmatasoa.

Testit: python -m unittest discover -s tests; QGISin Pythonilla
qgis_plugin/smoke_style_visibility.py, smoke_labels.py, smoke_styles.py,
smoke_audit_fixes.py ja test_downloads.py. Verkkoa käyttävä lisädiagnostiikka:
qgis_plugin/diagnose_visibility.py.
