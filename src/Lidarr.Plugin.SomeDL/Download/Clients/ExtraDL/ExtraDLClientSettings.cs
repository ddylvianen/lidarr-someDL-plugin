using FluentValidation;
using NzbDrone.Common.Extensions;
using NzbDrone.Core.Annotations;
using NzbDrone.Core.ThingiProvider;
using NzbDrone.Core.Validation;

namespace NzbDrone.Core.Download.Clients.ExtraDL
{
    public class ExtraDLClientSettingsValidator : AbstractValidator<ExtraDLClientSettings>
    {
        public ExtraDLClientSettingsValidator()
        {
            RuleFor(c => c.Host).ValidHost();
            RuleFor(c => c.Port).InclusiveBetween(1, 65535);
            RuleFor(c => c.UrlBase).ValidUrlBase().When(c => c.UrlBase.IsNotNullOrWhiteSpace());
        }
    }

    public class ExtraDLClientSettings : IProviderConfig
    {
        private static readonly ExtraDLClientSettingsValidator Validator = new ExtraDLClientSettingsValidator();

        [FieldDefinition(0, Label = "Host", Type = FieldType.Textbox, HelpText = "Host of extradl (runs next to SomeDL). Also the host Remote Path Mappings are matched on")]
        public string Host { get; set; } = "somedl";

        [FieldDefinition(1, Label = "Port", Type = FieldType.Textbox)]
        public int Port { get; set; } = 5050;

        [FieldDefinition(2, Label = "Url Base", Type = FieldType.Textbox, Advanced = true)]
        public string UrlBase { get; set; }

        [FieldDefinition(3, Label = "Use SSL", Type = FieldType.Checkbox)]
        public bool UseSsl { get; set; }

        public NzbDroneValidationResult Validate()
        {
            return new NzbDroneValidationResult(Validator.Validate(this));
        }
    }
}
