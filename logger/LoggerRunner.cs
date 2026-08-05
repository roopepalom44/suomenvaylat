using System;
using System.Diagnostics;
using System.Threading.Tasks;

namespace suomenvaylat.network
{
    internal static class LoggerRunner
    {
        internal static void RecordToolOpening(string toolName)
        {
            _ = Task.Run(async () =>
            {
                try
                {
                    LogData connection = await LogData.GetInstance("", "", toolName).ConfigureAwait(false);

                    if (!connection.InstanceOk)
                    {
                        Debug.WriteLine($"Logger did not accept the run event for {toolName}.");
                    }
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"Logger failed for {toolName}: {ex.Message}");
                }
            });
        }
    }
}
