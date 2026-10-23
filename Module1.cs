using ArcGIS.Desktop.Extensions;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace suomenvaylat
{
    internal class Module1 : Module
    {

        protected override bool Initialize()
        {
            PaikkatietoOhje.SharedHelp.Register("suomenvaylat", System.Reflection.Assembly.GetExecutingAssembly());
            return true;
        }

        protected override void Uninitialize()
        {
            PaikkatietoOhje.SharedHelp.Unregister("suomenvaylat");
        }

        private static Module1 _this = null;

        /// <summary>
        /// Retrieve the singleton instance to this module here
        /// </summary>
        public static Module1 Current => _this ??= (Module1)FrameworkApplication.FindModule("suomenvaylat_Module");

        #region Overrides
        /// <summary>
        /// Called by Framework when ArcGIS Pro is closing
        /// </summary>
        /// <returns>False to prevent Pro from closing, otherwise True</returns>
        protected override bool CanUnload()
        {
            //TODO - add your business logic
            //return false to ~cancel~ Application close
            return true;
        }

        #endregion Overrides

    }
}
