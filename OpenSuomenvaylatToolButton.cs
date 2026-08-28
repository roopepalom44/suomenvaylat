using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Dialogs;
using System;
using System.IO;
using System.Reflection;

namespace suomenvaylat
{
    internal class OpenSuomenvaylatToolButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                // 1) Hae add-inin asennuskansio varmasti (UriBuilder estää välilyöntien ja erikoismerkkien hajoamisen polussa)
                string assemblyLocation = Assembly.GetExecutingAssembly().Location;
                UriBuilder uri = new UriBuilder(assemblyLocation);
                string addinFolder = Path.GetDirectoryName(Uri.UnescapeDataString(uri.Path));

                // 2) Määritä absoluuttinen polku .pyt-tiedostoon
                string pytPath = Path.Combine(addinFolder, "Toolboxes", "VaylaWFSDownloader.pyt");

                // Varmistus: Onko tiedosto siirtynyt työkaverin koneelle asti?
                if (!File.Exists(pytPath))
                {
                    MessageBox.Show(
                        $"Python-työkalua ei löytynyt asennuskansiosta!\n\nEtsitty polku:\n{pytPath}\n\nRatkaisu: Varmista kääntäessä (Visual Studio), että tiedoston Build Action on 'AddInContent'.",
                        "Suomenväylät.fi - Puuttuva tiedosto",
                        System.Windows.MessageBoxButton.OK,
                        System.Windows.MessageBoxImage.Warning);
                    return;
                }

                // 3) Määritä työkalun polku TÄSMÄLLEEN Esrin vaatimassa lokaalissa muodossa:
                // Syntaksi: "X:\koko\polku\tyokalu.pyt\PythonLuokanNimi"
                string toolClassName = "VaylaWFSDownloader";
                string toolPath = $@"{pytPath}\{toolClassName}";

                // 4) Avaa työkalu
                Geoprocessing.OpenToolDialog(toolPath, null, null, false);
            }
            catch (Exception ex)
            {
                // Jos työkalun avaus kaatuu (esim. Pythonin syntaksivirheeseen toisella koneella), tämä näyttää todellisen syyn
                MessageBox.Show(
                    $"Työkalun käynnistys epäonnistui.\n\nVirheilmoitus:\n{ex.Message}\n\nJos polku on oikein, vika voi olla työkaverin Python-ympäristössä.",
                    "Suomenväylät.fi - Geoprocessing Virhe",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Error);
            }
        }
    }
}
