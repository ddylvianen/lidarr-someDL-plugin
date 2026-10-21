using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using FluentValidation.Results;
using NLog;
using NzbDrone.Common.Disk;
using NzbDrone.Common.Extensions;
using NzbDrone.Common.Http;
using NzbDrone.Core.Configuration;
using NzbDrone.Core.Indexers;
using NzbDrone.Core.Localization;
using NzbDrone.Core.Parser.Model;
using NzbDrone.Core.RemotePathMappings;
using NzbDrone.Core.Validation;
using NzbDrone.Plugin.SomeDL.ExtraDL;

namespace NzbDrone.Core.Download.Clients.ExtraDL
{
    /// <summary>
    /// Hands a Bandcamp or SoundCloud album to extradl and follows its job. extradl keeps the jobs itself
    /// (they survive restarts on both sides), so unlike the SomeDL client nothing is stored here.
    /// </summary>
    public class ExtraDLClient : DownloadClientBase<ExtraDLClientSettings>
    {
        private const string IdPrefix = "extradl-";

        private readonly IExtraDLProxy _proxy;

        public ExtraDLClient(IExtraDLProxy proxy,
            IConfigService configService,
            IDiskProvider diskProvider,
            IRemotePathMappingService remotePathMappingService,
            ILocalizationService localizationService,
            Logger logger)
            : base(configService, diskProvider, remotePathMappingService, localizationService, logger)
        {
            _proxy = proxy;
        }

        public override string Name => "Extra sources (extradl)";

        public override string Protocol => nameof(ExtraDLDownloadProtocol);

        private string BaseUrl => HttpRequestBuilder.BuildBaseUrl(Settings.UseSsl, Settings.Host, Settings.Port, Settings.UrlBase);

        public override Task<string> Download(RemoteAlbum remoteAlbum, IIndexer indexer)
        {
            // Tagged with Lidarr's own names rather than the site's, so the import matches the album
            var album = remoteAlbum.Albums?.FirstOrDefault();
            var artist = remoteAlbum.Artist?.Name ?? remoteAlbum.Release.Artist;
            var title = album?.Title ?? remoteAlbum.Release.Album;
            var year = album?.ReleaseDate?.Year;

            var id = _proxy.Download(BaseUrl, remoteAlbum.Release.DownloadUrl, artist, title, year);
            if (string.IsNullOrWhiteSpace(id))
            {
                throw new DownloadClientException("extradl did not accept {0}", remoteAlbum.Release.DownloadUrl);
            }

            _logger.Info("extradl queued {0} as {1}", remoteAlbum.Release.Title, id);
            return Task.FromResult(IdPrefix + id);
        }

        public override IEnumerable<DownloadClientItem> GetItems()
        {
            var jobs = _proxy.GetJobs(BaseUrl)?.Jobs ?? new List<ExtraDLJob>();

            return jobs.Select(job =>
            {
                var item = new DownloadClientItem
                {
                    DownloadClientInfo = DownloadClientItemClientInfo.FromDownloadClient(this, false),
                    DownloadId = IdPrefix + job.Id,
                    Title = $"{job.Artist} - {job.Album}",
                    TotalSize = Math.Max(job.Total, 1),
                    RemainingSize = Math.Max(job.Total - job.Done, 0),
                    CanMoveFiles = true,
                    CanBeRemoved = true
                };

                switch (job.State)
                {
                    case "completed":
                        item.Status = DownloadItemStatus.Completed;
                        item.RemainingSize = 0;
                        item.OutputPath = _remotePathMappingService.RemapRemoteToLocal(Settings.Host, new OsPath(job.Path));
                        break;
                    case "failed":
                        item.Status = DownloadItemStatus.Failed;
                        item.Message = job.Error;
                        break;
                    case "downloading":
                        item.Status = DownloadItemStatus.Downloading;
                        item.Message = job.Total > 0 ? $"{job.Done}/{job.Total} tracks done" : "fetching album info";
                        break;
                    default:
                        item.Status = DownloadItemStatus.Queued;
                        break;
                }

                return item;
            }).ToList();
        }

        public override void RemoveItem(DownloadClientItem item, bool deleteData)
        {
            if (item.DownloadId?.StartsWith(IdPrefix) == true)
            {
                _proxy.RemoveJob(BaseUrl, item.DownloadId.Substring(IdPrefix.Length), deleteData);
            }
        }

        public override DownloadClientInfo GetStatus()
        {
            return new DownloadClientInfo
            {
                IsLocalhost = Settings.Host is "127.0.0.1" or "localhost",
                OutputRootFolders = new List<OsPath>()
            };
        }

        protected override void Test(List<ValidationFailure> failures)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(_proxy.GetVersion(BaseUrl)?.Version))
                {
                    failures.Add(new NzbDroneValidationFailure("Host", "This does not look like extradl: /api/version gave no version"));
                }
            }
            catch (Exception ex)
            {
                _logger.Warn(ex, "Unable to connect to extradl");
                failures.Add(new NzbDroneValidationFailure("Host", $"Unable to connect to extradl: {ex.Message}")
                {
                    InfoLink = BaseUrl,
                    DetailedDescription = "extradl is installed and started by install-somedl.sh next to SomeDL (port 5050)"
                });
            }
        }
    }
}
