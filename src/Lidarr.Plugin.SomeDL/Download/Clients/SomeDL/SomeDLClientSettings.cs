using FluentValidation;
using NzbDrone.Common.Extensions;
using NzbDrone.Core.Annotations;
using NzbDrone.Core.ThingiProvider;
using NzbDrone.Core.Validation;

namespace NzbDrone.Core.Download.Clients.SomeDL
{
    public class SomeDLClientSettingsValidator : AbstractValidator<SomeDLClientSettings>
    {
        public SomeDLClientSettingsValidator()
        {
            RuleFor(c => c.Host).ValidHost();
            RuleFor(c => c.Port).InclusiveBetween(1, 65535);
            RuleFor(c => c.UrlBase).ValidUrlBase().When(c => c.UrlBase.IsNotNullOrWhiteSpace());
        }
    }

    public class SomeDLClientSettings : IProviderConfig
    {
        private static readonly SomeDLClientSettingsValidator Validator = new SomeDLClientSettingsValidator();

        [FieldDefinition(0, Label = "Host", Type = FieldType.Textbox, HelpText = "Host of the SomeDL WebUI, e.g. the container name 'somedl'. Also the host Remote Path Mappings are matched on")]
        public string Host { get; set; } = "somedl";

        [FieldDefinition(1, Label = "Port", Type = FieldType.Textbox)]
        public int Port { get; set; } = 5000;

        [FieldDefinition(2, Label = "Url Base", Type = FieldType.Textbox, Advanced = true, HelpText = "Only when SomeDL sits behind a reverse proxy on a sub-path")]
        public string UrlBase { get; set; }

        [FieldDefinition(3, Label = "Use SSL", Type = FieldType.Checkbox)]
        public bool UseSsl { get; set; }

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }
}
