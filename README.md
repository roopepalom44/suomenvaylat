# Suomenväylät - ArcGIS Pro Add-in

ArcGIS Pro -laajennus Suomenväylät-aineistojen lataamiseen WFS-rajapinnoista.

## Vaatimukset

- ArcGIS Pro 3.x
- .NET 8.0 Desktop Runtime
- Kerran käännetty `suomenvaylat.dll`, jos käytät manuaalista paketointia

## Manuaalinen paketointi ilman MSBuildia

Kun C#-osa on jo käännetty esimerkiksi ArcGIS Pron AssemblyCacheen, paketoi nykyiset lähdetiedostot yhdellä PowerShell-komennolla:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1
```

Skripti etsii ensin `suomenvaylat.dll`-tiedoston bin-kansiosta ja sen jälkeen ArcGIS Pron AssemblyCachesta. Se rakentaa uuden `bin\Debug\net8.0-windows\suomenvaylat.esriAddInX`-paketin tyhjästä, sisältää Python-työkalun ja resurssit sekä validoi paketin sisällön.

Jos DLL on muualla, anna sen polku:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

Skripti ei käännä C#-lähdekoodia. Se on tarkoitettu erityisesti Python-työkalun ja sen resurssien päivittämiseen ilman MSBuildia. C#-lähdekoodin muutokset vaativat erillisen .NET/ArcGIS Pro SDK -käännöksen.
