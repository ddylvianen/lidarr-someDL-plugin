using System;
using System.Collections.Generic;
using System.IO;
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
using NzbDrone.Plugin.SomeDL;

namespace NzbDrone.Core.Download.Clients.SomeDL
{
    /// <summary>
    /// Hands a YouTube Music album to SomeDL and follows its tracks until all have finished. SomeDL
    /// decides where the files go (its output template), so the template must give each album its
    /// own folder; Test checks for that.
    /// </summary>
    public class SomeDLClient : DownloadClientBase<SomeDLClientSettings>
    {
        // How long the unfinished tracks of a download may be absent from SomeDL entirely before the
        // download is failed. Covers the moment between SomeDL taking a track off its queue and
        // listing it as active.
        private static readonly TimeSpan MissingGracePeriod = TimeSpan.FromMinutes(2);

        // Left in every album folder this client downloads, so tools/somedl_mover.py (which moves what
        // was downloaded through SomeDL's own WebUI into the library) leaves Lidarr's downloads alone.
        // Lidarr deletes the folder, marker included, after importing.
        public const string MarkerFileName = ".lidarr-somedl";

        private readonly ISomeDLProxy _proxy;
        private readonly ISomeDLDownloadStore _store;

        public SomeDLClient(ISomeDLProxy proxy,
            ISomeDLDownloadStore store,
            IConfigService configService,
            IDiskProvider diskProvider,
            IRemotePathMappingService remotePathMappingService,
            ILocalizationService localizationService,
            Logger logger)
            : base(configService, diskProvider, remotePathMappingService, localizationService, logger)
        {
            _proxy = proxy;
            _store = store;
        }

        public override string Name => "SomeDL";

        public override string Protocol => nameof(SomeDLDownloadProtocol);

        private string BaseUrl => HttpRequestBuilder.BuildBaseUrl(Settings.UseSsl, Settings.Host, Settings.Port, Settings.UrlBase);

        public override Task<string> Download(RemoteAlbum remoteAlbum, IIndexer indexer)
        {
            var release = remoteAlbum.Release;
            var ids = _proxy.DownloadAlbum(BaseUrl, release.DownloadUrl);

            if (ids.Count == 0)
            {
                throw new DownloadClientException("SomeDL found no tracks in {0}", release.DownloadUrl);
            }

            var downloadId = $"somedl-{Guid.NewGuid():N}";

            _store.Add(new SomeDLDownload
            {
                DownloadId = downloadId,
                ClientId = Definition.Id,
                Title = release.Title,
                Size = release.Size,
                AddedUtc = DateTime.UtcNow,
                SomeDLIds = ids
            });

            _logger.Info("SomeDL queued {0} tracks for {1}", ids.Count, release.Title);

            return Task.FromResult(downloadId);
        }

        public override IEnumerable<DownloadClientItem> GetItems()
        {
            var downloads = _store.GetAll(Definition.Id);
            if (downloads.Count == 0)
            {
                return Array.Empty<DownloadClientItem>();
            }

            var status = _proxy.GetStatus(BaseUrl) ?? new SomeDLStatus();
            var queued = new HashSet<string>((_proxy.GetQueue(BaseUrl)?.Queue ?? new List<SomeDLSong>()).Select(s => s.SomeDLId));
            SomeDLDownloadConfig config = null;

            var items = new List<DownloadClientItem>();

            foreach (var download in downloads)
            {
                var item = ToItem(download, status, queued, ref config);
                items.Add(item);
            }

            return items;
        }

        private DownloadClientItem ToItem(SomeDLDownload download, SomeDLStatus status, HashSet<string> queued, ref SomeDLDownloadConfig config)
        {
            var changed = false;
            int running = 0, waiting = 0, missing = 0;

            foreach (var id in download.SomeDLIds.Where(id => !download.Finished.ContainsKey(id)))
            {
                if (status.FinishedItems.TryGetValue(id, out var finished))
                {
                    download.Finished[id] = finished;
                    changed = true;
                    MarkFolder(finished);
                }
                else if (status.ActiveItems.ContainsKey(id))
                {
                    running++;
                }
                else if (queued.Contains(id))
                {
                    waiting++;
                }
                else
                {
                    missing++;
                }
            }

            // Tracks that vanished from SomeDL altogether only count as failed once they stay gone
            var lost = false;
            if (missing > 0 && running == 0 && waiting == 0)
            {
                download.MissingSinceUtc ??= DateTime.UtcNow;
                lost = DateTime.UtcNow - download.MissingSinceUtc > MissingGracePeriod;
                changed = true;
            }
            else if (download.MissingSinceUtc != null)
            {
                download.MissingSinceUtc = null;
                changed = true;
            }

            if (changed)
            {
                _store.Update(download);
            }

            var total = download.SomeDLIds.Count;
            var states = download.Finished.Values.Select(f => f.FirstOrDefault()).ToList();
            var succeeded = states.Count(s => s is SomeDLFinishState.Success or SomeDLFinishState.AlreadyDownloaded);
            var failed = states.Count - succeeded + (lost ? missing : 0);
            var pending = total - states.Count - (lost ? missing : 0);

            var item = new DownloadClientItem
            {
                DownloadClientInfo = DownloadClientItemClientInfo.FromDownloadClient(this, false),
                DownloadId = download.DownloadId,
                Title = download.Title,
                TotalSize = download.Size,
                RemainingSize = total == 0 ? 0 : download.Size * pending / total,
                CanMoveFiles = true,
                CanBeRemoved = true
            };

            if (pending > 0)
            {
                item.Status = running > 0 || states.Count > 0 ? DownloadItemStatus.Downloading : DownloadItemStatus.Queued;
                item.Message = $"{states.Count}/{total} tracks done";
                return item;
            }

            if (succeeded == 0)
            {
                item.Status = DownloadItemStatus.Failed;
                item.Message = lost
                    ? "SomeDL lost track of this download (was it restarted or its queue cleared?)"
                    : $"All {total} tracks failed in SomeDL, see its log or download report";
                return item;
            }

            var folder = AlbumFolder(download);
            if (folder.IsEmpty)
            {
                item.Status = DownloadItemStatus.Warning;
                item.Message = "SomeDL finished but reported no file paths";
                return item;
            }

            // Lidarr imports a whole folder. If SomeDL's template puts files straight into its output
            // folder, that folder holds every download and must never be handed over as one album.
            config ??= TryGetConfig();
            if (config?.OutputDir != null && new OsPath(config.OutputDir).AsDirectory().Equals(folder.AsDirectory()))
            {
                item.Status = DownloadItemStatus.Warning;
                item.Message = "SomeDL saved the tracks directly in its output folder. Add {album} as a folder to SomeDL's output template, e.g. {album_artist}/{album}/{track_pos} - {song}";
                return item;
            }

            item.Status = DownloadItemStatus.Completed;
            item.OutputPath = _remotePathMappingService.RemapRemoteToLocal(Settings.Host, folder);

            if (failed > 0)
            {
                item.Message = $"{failed} of {total} tracks failed in SomeDL (often YouTube refusing yt-dlp; see SomeDL's log)";
            }

            return item;
        }

        /// <summary>
        /// The folder holding most of the album. Tracks SomeDL already had elsewhere (say as a single)
        /// are reported at their old location; taking the majority folder keeps those from widening
        /// the import to a common parent that would sweep in unrelated albums.
        /// </summary>
        private static OsPath AlbumFolder(SomeDLDownload download)
        {
            return download.Finished.Values
                .Select(f => f.Count > 1 ? f[1] : null)
                .Where(p => !string.IsNullOrWhiteSpace(p))
                .Select(p => new OsPath(p).Directory)
                .GroupBy(d => d.FullPath)
                .OrderByDescending(g => g.Count())
                .Select(g => g.First())
                .FirstOrDefault();
        }

        private void MarkFolder(List<string> finished)
        {
            if (finished.Count < 2 || string.IsNullOrWhiteSpace(finished[1]))
            {
                return;
            }

            var folder = _remotePathMappingService.RemapRemoteToLocal(Settings.Host, new OsPath(finished[1])).Directory;
            var marker = (folder + MarkerFileName).FullPath;

            try
            {
                if (_diskProvider.FolderExists(folder.FullPath) && !_diskProvider.FileExists(marker))
                {
                    _diskProvider.WriteAllText(marker, "Downloaded for Lidarr; tools/somedl_mover.py leaves this folder alone.\n");
                }
            }
            catch (Exception ex)
            {
                _logger.Debug(ex, "Could not write {0}", marker);
            }
        }

        public override void RemoveItem(DownloadClientItem item, bool deleteData)
        {
            var download = _store.GetAll(Definition.Id).FirstOrDefault(d => d.DownloadId == item.DownloadId);
            if (download == null)
            {
                return;
            }

            // Tracks still waiting in SomeDL's queue; one already being downloaded cannot be stopped
            foreach (var id in download.SomeDLIds.Where(id => !download.Finished.ContainsKey(id)))
            {
                try
                {
                    _proxy.RemoveQueuedItem(BaseUrl, id);
                }
                catch (Exception ex)
                {
                    _logger.Debug(ex, "Could not remove {0} from SomeDL's queue", id);
                }
            }

            if (deleteData)
            {
                DeleteFiles(download);
            }

            _store.Remove(download.DownloadId);
        }

        // Deletes only the files of this download, never a folder that still has anything else in it:
        // SomeDL's output folder is shared by every download and possibly by the user's own.
        private void DeleteFiles(SomeDLDownload download)
        {
            var folders = new HashSet<string>();

            foreach (var finished in download.Finished.Values.Where(f => f.Count > 1 && f[0] == SomeDLFinishState.Success && !string.IsNullOrWhiteSpace(f[1])))
            {
                var local = _remotePathMappingService.RemapRemoteToLocal(Settings.Host, new OsPath(finished[1]));

                if (_diskProvider.FileExists(local.FullPath))
                {
                    _diskProvider.DeleteFile(local.FullPath);
                }

                folders.Add(local.Directory.FullPath);
            }

            foreach (var marker in folders.Select(f => Path.Combine(f, MarkerFileName)).Where(_diskProvider.FileExists))
            {
                _diskProvider.DeleteFile(marker);
            }

            foreach (var folder in folders.Where(f => _diskProvider.FolderExists(f) && _diskProvider.FolderEmpty(f)))
            {
                _diskProvider.DeleteFolder(folder, false);
            }
        }

        public override DownloadClientInfo GetStatus()
        {
            var config = TryGetConfig();

            return new DownloadClientInfo
            {
                IsLocalhost = Settings.Host is "127.0.0.1" or "localhost",
                OutputRootFolders = config?.OutputDir.IsNotNullOrWhiteSpace() == true
                    ? new List<OsPath> { _remotePathMappingService.RemapRemoteToLocal(Settings.Host, new OsPath(config.OutputDir)) }
                    : new List<OsPath>()
            };
        }

        protected override void Test(List<ValidationFailure> failures)
        {
            failures.AddIfNotNull(TestSettings());
        }

        private ValidationFailure TestSettings()
        {
            SomeDLDownloadConfig config;

            try
            {
                var version = _proxy.GetVersion(BaseUrl);
                if (string.IsNullOrWhiteSpace(version?.Version))
                {
                    return new NzbDroneValidationFailure("Host", "This does not look like a SomeDL WebUI: /get-version gave no version");
                }

                config = _proxy.GetDownloadConfig(BaseUrl);
            }
            catch (Exception ex)
            {
                _logger.Warn(ex, "Unable to connect to SomeDL");
                return new NzbDroneValidationFailure("Host", $"Unable to connect to SomeDL: {ex.Message}")
                {
                    InfoLink = BaseUrl,
                    DetailedDescription = "Check host and port, and that SomeDL's WebUI listens on 0.0.0.0 (somedl web --host 0.0.0.0) rather than 127.0.0.1"
                };
            }

            if (config == null)
            {
                return new NzbDroneValidationFailure(string.Empty, "SomeDL answered, but /settings-read returned no download settings");
            }

            if (!new OsPath(config.OutputDir).IsRooted)
            {
                return new NzbDroneValidationFailure(string.Empty, $"SomeDL's output_dir '{config.OutputDir}' is relative. Set an absolute path (the Docker image uses /downloads), so Lidarr can find the files");
            }

            // Without {album} as a folder, an album's tracks share a folder with everything else SomeDL
            // downloaded, and Lidarr has no folder it could import as just that album
            var output = config.Output ?? string.Empty;
            var lastSlash = output.LastIndexOf('/');
            if (lastSlash < 0 || !output.Substring(0, lastSlash).Contains("{album}"))
            {
                return new NzbDroneValidationFailure(string.Empty, $"SomeDL's output template '{output}' does not put each album in its own folder")
                {
                    DetailedDescription = "Set download.output in SomeDL (WebUI settings or config.toml) to e.g. {album_artist}/{album}/{track_pos} - {song}. Lidarr imports per folder and cannot import downloads otherwise."
                };
            }

            return null;
        }

        private SomeDLDownloadConfig TryGetConfig()
        {
            try
            {
                return _proxy.GetDownloadConfig(BaseUrl);
            }
            catch (Exception ex)
            {
                _logger.Debug(ex, "Could not read SomeDL's settings");
                return null;
            }
        }
    }
}
