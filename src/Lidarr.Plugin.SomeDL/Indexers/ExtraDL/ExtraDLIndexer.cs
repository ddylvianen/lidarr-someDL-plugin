using System;
using System.Threading.Tasks;
using FluentValidation.Results;
using NLog;
using NzbDrone.Common.Http;
using NzbDrone.Core.Configuration;
using NzbDrone.Core.Parser;
using NzbDrone.Plugin.SomeDL.ExtraDL;

namespace NzbDrone.Core.Indexers.ExtraDL
{
    /// <summary>
    /// Searches Bandcamp and SoundCloud through extradl. Every album (or single track) hit becomes one
    /// release whose download URL is its page on that site, which the matching download client hands back.
    /// </summary>
    public class ExtraDLIndexer : HttpIndexerBase<ExtraDLIndexerSettings>
    {
        private readonly IExtraDLProxy _proxy;

        public ExtraDLIndexer(IExtraDLProxy proxy,
            IHttpClient httpClient,
            IIndexerStatusService indexerStatusService,
            IConfigService configService,
            IParsingService parsingService,
            Logger logger)
            : base(httpClient, indexerStatusService, configService, parsingService, logger)
        {
            _proxy = proxy;
        }

        public override string Name => "Bandcamp/SoundCloud (extradl)";
        public override string Protocol => nameof(ExtraDLDownloadProtocol);
        public override bool SupportsRss => false;
        public override bool SupportsSearch => true;
        public override TimeSpan RateLimit => TimeSpan.FromSeconds(2);

        public override IIndexerRequestGenerator GetRequestGenerator()
        {
            return new ExtraDLRequestGenerator(Settings);
        }

        public override IParseIndexerResponse GetParser()
        {
            return new ExtraDLParser(Settings, _proxy, _logger);
        }

        protected override Task<ValidationFailure> TestConnection()
        {
            try
            {
                var version = _proxy.GetVersion(Settings.BaseUrl);
                if (string.IsNullOrWhiteSpace(version?.Version))
                {
                    return Task.FromResult(new ValidationFailure("BaseUrl", "This does not look like extradl: /api/version gave no version"));
                }

                _logger.Debug("Connected to extradl {0} (yt-dlp {1})", version.Version, version.YtDlp);
                return Task.FromResult<ValidationFailure>(null);
            }
            catch (Exception ex)
            {
                _logger.Warn(ex, "Unable to connect to extradl");
                return Task.FromResult(new ValidationFailure("BaseUrl", $"Unable to connect to extradl: {ex.Message}"));
            }
        }
    }
}
