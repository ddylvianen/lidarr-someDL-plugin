using System.Linq;
using FluentValidation;
using NzbDrone.Core.Annotations;
using NzbDrone.Core.Validation;

namespace NzbDrone.Core.Indexers.ExtraDL
{
    public class ExtraDLIndexerSettingsValidator : AbstractValidator<ExtraDLIndexerSettings>
    {
        public ExtraDLIndexerSettingsValidator()
        {
            RuleFor(c => c.BaseUrl).ValidRootUrl();
            RuleFor(c => c.MaxAlbumsPerSearch).InclusiveBetween(1, 20);
            RuleFor(c => c.MinimumNameMatch).InclusiveBetween(0, 100);
            RuleFor(c => c).Must(c => c.Sources.Length > 0).WithMessage("Pick at least one source");
        }
    }

    /// <summary>What searching through extradl needs, from the combined indexer or a one-site one.</summary>
    public interface IExtraDLSearchSettings
    {
        string BaseUrl { get; }
        int MaxAlbumsPerSearch { get; }
        int MinimumNameMatch { get; }
    }

    public class ExtraDLIndexerSettings : IIndexerSettings, IExtraDLSearchSettings
    {
        private static readonly ExtraDLIndexerSettingsValidator Validator = new ExtraDLIndexerSettingsValidator();

        [FieldDefinition(0, Label = "URL", HelpText = "URL of extradl, the helper install-somedl.sh puts next to SomeDL, e.g. http://somedl:5050")]
        public string BaseUrl { get; set; } = "http://somedl:5050";

        [FieldDefinition(1, Type = FieldType.Checkbox, Label = "Bandcamp", HelpText = "Search Bandcamp. Downloads are its free stream, MP3 128 kbps")]
        public bool Bandcamp { get; set; } = true;

        [FieldDefinition(2, Type = FieldType.Checkbox, Label = "SoundCloud", HelpText = "Search SoundCloud albums and tracks. Downloads are AAC, about 160 kbps")]
        public bool SoundCloud { get; set; } = true;

        [FieldDefinition(3, Type = FieldType.Checkbox, Label = "Jamendo", HelpText = "Free Creative Commons albums, downloaded as FLAC")]
        public bool Jamendo { get; set; } = true;

        [FieldDefinition(4, Type = FieldType.Checkbox, Label = "Internet Archive", HelpText = "Live recordings (Live Music Archive) and old public domain recordings, FLAC where the uploader provided it. A concert matches the Lidarr album with the same artist and date in its title. Stream-only recordings are skipped")]
        public bool InternetArchive { get; set; } = true;

        [FieldDefinition(5, Type = FieldType.Checkbox, Label = "Audius", HelpText = "Albums of verified Audius artists, MP3 320 kbps")]
        public bool Audius { get; set; } = true;

        [FieldDefinition(6, Type = FieldType.Number, Label = "Max Albums Per Search", HelpText = "Matching hits looked at per search; a Bandcamp hit costs an extra lookup for its length")]
        public int MaxAlbumsPerSearch { get; set; } = 5;

        [FieldDefinition(7, Type = FieldType.Number, Label = "Minimum Name Match", Unit = "%", HelpText = "How closely the album title and artist must match what Lidarr searches for. Case, accents, punctuation and a bracketed suffix are ignored", Advanced = true)]
        public int MinimumNameMatch { get; set; } = 80;

        [FieldDefinition(8, Type = FieldType.Number, Label = "Early Download Limit", Unit = "days", HelpText = "Days before release date to allow downloads. Empty is no limit", Advanced = true)]
        public int? EarlyReleaseLimit { get; set; }

        public string Sources => string.Join(",", new[]
        {
            Bandcamp ? "bandcamp" : null,
            SoundCloud ? "soundcloud" : null,
            Jamendo ? "jamendo" : null,
            InternetArchive ? "archive" : null,
            Audius ? "audius" : null
        }.Where(s => s != null));

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }
}
