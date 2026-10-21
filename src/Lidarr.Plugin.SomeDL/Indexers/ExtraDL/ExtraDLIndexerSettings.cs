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
            RuleFor(c => c).Must(c => c.Bandcamp || c.SoundCloud).WithMessage("Pick at least one source");
        }
    }

    public class ExtraDLIndexerSettings : IIndexerSettings
    {
        private static readonly ExtraDLIndexerSettingsValidator Validator = new ExtraDLIndexerSettingsValidator();

        [FieldDefinition(0, Label = "URL", HelpText = "URL of extradl, the helper install-somedl.sh puts next to SomeDL, e.g. http://somedl:5050")]
        public string BaseUrl { get; set; } = "http://somedl:5050";

        [FieldDefinition(1, Type = FieldType.Checkbox, Label = "Bandcamp", HelpText = "Search Bandcamp. Downloads are its free stream, MP3 128 kbps")]
        public bool Bandcamp { get; set; } = true;

        [FieldDefinition(2, Type = FieldType.Checkbox, Label = "SoundCloud", HelpText = "Search SoundCloud albums and tracks. Downloads are AAC, about 160 kbps")]
        public bool SoundCloud { get; set; } = true;

        [FieldDefinition(3, Type = FieldType.Number, Label = "Max Albums Per Search", HelpText = "Matching hits looked at per search; a Bandcamp hit costs an extra lookup for its length")]
        public int MaxAlbumsPerSearch { get; set; } = 5;

        [FieldDefinition(4, Type = FieldType.Number, Label = "Minimum Name Match", Unit = "%", HelpText = "How closely the album title and artist must match what Lidarr searches for. Case, accents, punctuation and a bracketed suffix are ignored", Advanced = true)]
        public int MinimumNameMatch { get; set; } = 80;

        [FieldDefinition(5, Type = FieldType.Number, Label = "Early Download Limit", Unit = "days", HelpText = "Days before release date to allow downloads. Empty is no limit", Advanced = true)]
        public int? EarlyReleaseLimit { get; set; }

        public string Sources => string.Join(",", Bandcamp ? "bandcamp" : null, SoundCloud ? "soundcloud" : null).Trim(',');

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }
}
