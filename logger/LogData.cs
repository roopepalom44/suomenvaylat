using Microsoft.Win32;
using System;
using System.Net.Http;
using System.Threading.Tasks;


namespace suomenvaylat.network
{

    public class LogData
    {
        private static LogData instance;
        private static readonly HttpClient _httpClient = new HttpClient();
        private bool updateOk;
        public bool InstanceOk
        {
            get
            {
                return updateOk;
            }
            set
            {
                updateOk = value;
            }
        }

        private string toolName;

        public string ToolName
        {
            get { return toolName; }
            set
            {
                toolName = value;
            }
        }
        private LogData()
        {

        }

        public static async Task<LogData> GetInstance(string ipv4Address, string toolType, string toolName)
        {
            if (instance == null)
            {
                instance = new LogData();
            }

            instance.InstanceOk = await instance.LoadData(ipv4Address, toolType, toolName);
            instance.ToolName = toolName;

            return instance;
        }

        private async Task<bool> LoadData(string ipv4Address, string toolType, string toolName)
        {
            try
            {
                string uri = @"https://rfilogger-functions.azurewebsites.net/api/HttpTriggerGeneral";

                string apiKey = string.Empty;
                try
                {
                    apiKey = await KeyVault.GetKeyVaultSecretAsync(KeyVault.QueryType.Secrets, "rfilogger-HttpTriggerGeneral-ApiKey").ConfigureAwait(false);
                }
                catch (Exception)
                {
                }

                string timestamp = String.Format("{0:s}.{0:ff}{0:zzz}", DateTime.Now);

                string query = uri +
                                $"?code={Uri.EscapeDataString(apiKey ?? "")}" +
                                $"&UserName={Uri.EscapeDataString(Environment.UserName ?? "")}" +
                                $"&UserFullName={Uri.EscapeDataString(ReadUserFullNameFromRegistry() ?? "")}" +
                                $"&ComputerName={Uri.EscapeDataString(Environment.MachineName ?? "")}" +
                                $"&UserDomainName={Uri.EscapeDataString(Environment.UserDomainName ?? "")}" +
                                $"&Ipv4Address={Uri.EscapeDataString(ipv4Address ?? "")}" +
                                $"&Ipv6Address=" +
                                $"&UtcTime={Uri.EscapeDataString(timestamp)}" +
                                $"&SoftwareName=ArcGISPro" +
                                $"&ToolName={Uri.EscapeDataString(toolName ?? "")}" +
                                $"&ToolVersion={Uri.EscapeDataString(System.Reflection.Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "1.0.0.0")}" +
                                $"&RobotUser=False" +
                                $"&ProjectNumber=" +
                                $"&BusinessUnit=TCD" +
                                $"&Market=Transport" +
                                $"&InputValues={Uri.EscapeDataString(toolType ?? "")}" +
                                $"&OutputValues=" +
                                $"&Status=Opening" +
                                $"&Developer={Uri.EscapeDataString(amIDeveloper("TCD_DEVELOPER") ?? "False")}" +
                                $"&BufferTime=0" +
                                $"&CalculationsAtBufferTime=0" +
                                $"&Errors=";

                HttpResponseMessage response = await _httpClient.GetAsync(query).ConfigureAwait(false);

                response.EnsureSuccessStatusCode();
                string _response = response.ReasonPhrase;

                return _response == "OK";
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static string amIDeveloper(string environmentVariable)
        {
            string developer = Environment.GetEnvironmentVariable(environmentVariable);
            if (String.IsNullOrEmpty(developer))
            {
                return "False";
            }
            else
            {
                return "True";
            }
        }

        private string ReadUserFullNameFromRegistry()
        {
            try
            {
                string registryKeyPath = @"SOFTWARE\Microsoft\Office\Common\UserInfo";
                string valueName = "UserName";

                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(registryKeyPath))
                {
                    if (key != null)
                    {
                        object value = key.GetValue(valueName);

                        if (value != null)
                        {
                            string userName = value.ToString();
                            if (!string.IsNullOrEmpty(userName))
                            {
                                return userName;
                            }
                        }
                    }
                }
            }
            catch (Exception)
            {
                // Registry read failed - fall through to fallback
            }

            // Fallback: return Windows username instead of throwing
            return Environment.UserName;
        }


     
    }

}
