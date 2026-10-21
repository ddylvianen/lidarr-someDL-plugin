using System;
using System.Threading.Tasks;
using FluentValidation;
using FluentValidation.Results;
using NLog;
using NzbDrone.Common.Http;
using NzbDrone.Core.Annotations;
using NzbDrone.Core.Configuration;
using NzbDrone.Core.Parser;
using NzbDrone.Core.Validation;
using NzbDrone.Plugin.SomeDL.ExtraDL;

namespace NzbDrone.Core.Indexers.ExtraDL
{
    public class ExtraDLSourceSettingsValidator : AbstractValidator<ExtraDLSourceSettings>
    {
        public ExtraDLSourceSettingsValidator()
        {
            RuleFor(c => c.BaseUrl).ValidRootUrl();
            RuleFor(c => c.MaxAlbumsPerSearch).InclusiveBetween(1, 20);
            RuleFor(c => c.MinimumNameMatch).InclusiveBetween(0, 100);
        }
    }

    public class ExtraDLSourceSettings : IIndexerSettings, IExtraDLSearchSettings
    {
        private static readonly ExtraDLSourceSettingsValidator Validator = new ExtraDLSourceSettingsValidator();

        [FieldDefinition(0, Label = "URL", HelpText = "URL of extradl, the helper install-somedl.sh puts next to SomeDL, e.g. http://somedl:5050")]
        public string BaseUrl { get; set; } = "http://somedl:5050";

        [FieldDefinition(1, Type = FieldType.Number, Label = "Max Albums Per Search", HelpText = "Matching hits looked at per search; a hit without a length costs an extra lookup")]
        public int MaxAlbumsPerSearch { get; set; } = 5;

        [FieldDefinition(2, Type = FieldType.Number, Label = "Minimum Name Match", Unit = "%", HelpText = "How closely the album title and artist must match what Lidarr searches for. Case, accents, punctuation and a bracketed suffix are ignored", Advanced = true)]
        public int MinimumNameMatch { get; set; } = 80;

        [FieldDefinition(3, Type = FieldType.Number, Label = "Early Download Limit", Unit = "days", HelpText = "Days before release date to allow downloads. Empty is no limit", Advanced = true)]
        public int? EarlyReleaseLimit { get; set; }

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }

    /// <summary>
    /// One site through extradl, so each gets its own priority, on/off switch and failure status in Lidarr.
    /// All of them hand their releases to the same "Extra sources (extradl)" download client.
    /// </summary>
    public abstract class ExtraDLSourceIndexer : HttpIndexerBase<ExtraDLSourceSettings>
    {
        private readonly IExtraDLProxy _proxy;

        protected ExtraDLSourceIndexer(IExtraDLProxy proxy,
            IHttpClient httpClient,
            IIndexerStatusService indexerStatusService,
            IConfigService configService,
            IParsingService parsingService,
            Logger logger)
            : base(httpClient, indexerStatusService, configService, parsingService, logger)
        {
            _proxy = proxy;
        }

        public override string Protocol => nameof(ExtraDLDownloadProtocol);
        public override bool SupportsRss => false;
        public override bool SupportsSearch => true;
        public override TimeSpan RateLimit => TimeSpan.FromSeconds(2);

        // extradl's name for the site
        protected abstract string Source { get; }

        public override IIndexerRequestGenerator GetRequestGenerator()
        {
            return new ExtraDLRequestGenerator(Settings, Source);
        }

        public override IParseIndexerResponse GetParser()
        {
            return new ExtraDLParser(Settings, _proxy, _logger);
        }

        protected override Task<ValidationFailure> TestConnection()
        {
            return Task.FromResult(ExtraDLIndexer.TestExtraDL(_proxy, Settings.BaseUrl, _logger));
        }
    }

    public class BandcampIndexer : ExtraDLSourceIndexer
    {
        public BandcampIndexer(IExtraDLProxy proxy, IHttpClient httpClient, IIndexerStatusService indexerStatusService, IConfigService configService, IParsingService parsingService, Logger logger)
            : base(proxy, httpClient, indexerStatusService, configService, parsingService, logger)
        {
        }

        public override string Name => "Bandcamp (extradl)";
        protected override string Source => "bandcamp";
    }

    public class SoundCloudIndexer : ExtraDLSourceIndexer
    {
        public SoundCloudIndexer(IExtraDLProxy proxy, IHttpClient httpClient, IIndexerStatusService indexerStatusService, IConfigService configService, IParsingService parsingService, Logger logger)
            : base(proxy, httpClient, indexerStatusService, configService, parsingService, logger)
        {
        }

        public override string Name => "SoundCloud (extradl)";
        protected override string Source => "soundcloud";
    }

    public class JamendoIndexer : ExtraDLSourceIndexer
    {
        public JamendoIndexer(IExtraDLProxy proxy, IHttpClient httpClient, IIndexerStatusService indexerStatusService, IConfigService configService, IParsingService parsingService, Logger logger)
            : base(proxy, httpClient, indexerStatusService, configService, parsingService, logger)
        {
        }

        public override string Name => "Jamendo (extradl)";
        protected override string Source => "jamendo";
    }

    public class InternetArchiveIndexer : ExtraDLSourceIndexer
    {
        public InternetArchiveIndexer(IExtraDLProxy proxy, IHttpClient httpClient, IIndexerStatusService indexerStatusService, IConfigService configService, IParsingService parsingService, Logger logger)
            : base(proxy, httpClient, indexerStatusService, configService, parsingService, logger)
        {
        }

        public override string Name => "Internet Archive (extradl)";
        protected override string Source => "archive";
    }

    public class AudiusIndexer : ExtraDLSourceIndexer
    {
        public AudiusIndexer(IExtraDLProxy proxy, IHttpClient httpClient, IIndexerStatusService indexerStatusService, IConfigService configService, IParsingService parsingService, Logger logger)
            : base(proxy, httpClient, indexerStatusService, configService, parsingService, logger)
        {
        }

        public override string Name => "Audius (extradl)";
        protected override string Source => "audius";
    }
}
