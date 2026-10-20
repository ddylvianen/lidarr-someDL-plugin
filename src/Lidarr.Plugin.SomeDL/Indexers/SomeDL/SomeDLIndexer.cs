using System;
using System.Threading.Tasks;
using FluentValidation.Results;
using NLog;
using NzbDrone.Common.Http;
using NzbDrone.Core.Configuration;
using NzbDrone.Core.Parser;
using NzbDrone.Plugin.SomeDL;

namespace NzbDrone.Core.Indexers.SomeDL
{
    /// <summary>
    /// Searches YouTube Music albums through SomeDL. Each album hit becomes one release whose download
    /// URL is the album's YouTube Music playlist, which the SomeDL download client hands back to SomeDL.
    /// </summary>
    public class SomeDLIndexer : HttpIndexerBase<SomeDLIndexerSettings>
    {
        private readonly ISomeDLProxy _proxy;

        public SomeDLIndexer(ISomeDLProxy proxy,
            IHttpClient httpClient,
            IIndexerStatusService indexerStatusService,
            IConfigService configService,
            IParsingService parsingService,
            Logger logger)
            : base(httpClient, indexerStatusService, configService, parsingService, logger)
        {
            _proxy = proxy;
        }

        public override string Name => "SomeDL";
        public override string Protocol => nameof(SomeDLDownloadProtocol);
        public override bool SupportsRss => false;
        public override bool SupportsSearch => true;

        // ytmusicapi is unofficial; keep a gap between searches so YouTube does not start refusing them
        public override TimeSpan RateLimit => TimeSpan.FromSeconds(2);

        public override IIndexerRequestGenerator GetRequestGenerator()
        {
            return new SomeDLRequestGenerator(Settings);
        }

        public override IParseIndexerResponse GetParser()
        {
            return new SomeDLParser(Settings, _proxy, _logger);
        }

        // The base test fetches the RSS feed, which YouTube Music does not have
        protected override Task<ValidationFailure> TestConnection()
        {
            try
            {
                var version = _proxy.GetVersion(Settings.BaseUrl);
                if (string.IsNullOrWhiteSpace(version?.Version))
                {
                    return Task.FromResult(new ValidationFailure("BaseUrl", "This does not look like a SomeDL WebUI: /get-version gave no version"));
                }

                _logger.Debug("Connected to SomeDL {0}", version.Version);
                return Task.FromResult<ValidationFailure>(null);
            }
            catch (Exception ex)
            {
                _logger.Warn(ex, "Unable to connect to SomeDL");
                return Task.FromResult(new ValidationFailure("BaseUrl", $"Unable to connect to SomeDL: {ex.Message}"));
            }
        }
    }
}
