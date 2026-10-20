using FluentValidation;
using NzbDrone.Core.Annotations;
using NzbDrone.Core.Validation;
using NzbDrone.Plugin.SomeDL;

namespace NzbDrone.Core.Indexers.SomeDL
{
    public class SomeDLIndexerSettingsValidator : AbstractValidator<SomeDLIndexerSettings>
    {
        public SomeDLIndexerSettingsValidator()
        {
            RuleFor(c => c.BaseUrl).ValidRootUrl();
            RuleFor(c => c.MaxAlbumsPerSearch).InclusiveBetween(1, 20);
        }
    }

    public class SomeDLIndexerSettings : IIndexerSettings
    {
        private static readonly SomeDLIndexerSettingsValidator Validator = new SomeDLIndexerSettingsValidator();

        [FieldDefinition(0, Label = "URL", HelpText = "URL of the SomeDL WebUI, e.g. http://somedl:5000")]
        public string BaseUrl { get; set; } = "http://somedl:5000";

        [FieldDefinition(1, Type = FieldType.Select, SelectOptions = typeof(SomeDLReleaseQuality), Label = "Release Quality", HelpText = "Quality the releases are reported as, so Lidarr can match them to a quality profile. Auto follows SomeDL's download format. YouTube audio is lossy (~128-160 kbps) whatever is picked here")]
        public int ReleaseQuality { get; set; } = (int)SomeDLReleaseQuality.Auto;

        [FieldDefinition(2, Type = FieldType.Number, Label = "Max Albums Per Search", HelpText = "YouTube Music hits looked at per search. Each one costs an extra lookup in SomeDL for its length, which Lidarr needs for its size checks")]
        public int MaxAlbumsPerSearch { get; set; } = 5;

        [FieldDefinition(3, Type = FieldType.Checkbox, Label = "Only Matching Artist", HelpText = "Skip hits whose artist does not resemble the searched artist, before looking up their length. Saves lookups; turn off for artists credited differently on YouTube", Advanced = true)]
        public bool OnlyMatchingArtist { get; set; } = true;

        [FieldDefinition(4, Type = FieldType.Number, Label = "Early Download Limit", Unit = "days", HelpText = "Days before release date to allow downloads. Empty is no limit", Advanced = true)]
        public int? EarlyReleaseLimit { get; set; }

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }
}
