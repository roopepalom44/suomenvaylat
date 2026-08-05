using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace suomenvaylat.network
{
    public static class KeyVault
    {
        internal enum QueryType
        {
            [Description("Keys")]
            Keys,
            [Description("Secrets")]
            Secrets,
            [Description("Certificates")]
            Certificates

        }

        private static readonly HttpClient _client = new HttpClient();

        internal static string ToDescriptionString(this QueryType val)
        {
            DescriptionAttribute[] attributes = (DescriptionAttribute[])val
                .GetType()
                .GetField(val.ToString())
                .GetCustomAttributes(typeof(DescriptionAttribute), false);
            return attributes.Length > 0 ? attributes[0].Description : string.Empty;
        }

        internal static async Task<string> GetKeyVaultSecretAsync(QueryType queryType, string secretName)
        {
            try
            {
                string url = "https://tcdfunctionsnet48.azurewebsites.net/api/GetKeyVaultValue?QueryType=" + ToDescriptionString(queryType) + "&Secrets=" + secretName;

                using (HttpResponseMessage response = await _client.GetAsync(url).ConfigureAwait(false))
                {
                    response.EnsureSuccessStatusCode();

                    string secretValue = await response.Content.ReadAsStringAsync().ConfigureAwait(false);

                    if (string.IsNullOrWhiteSpace(secretValue))
                        throw new KeyNotFoundException($"Secret '{secretName}' was found but is empty.");

                    return secretValue;
                }
            }
            catch (Exception ex)
            {
                throw new Exception($"Error retrieving secret '{secretName}': {ex.Message}", ex);
            }
        }
    }
}
