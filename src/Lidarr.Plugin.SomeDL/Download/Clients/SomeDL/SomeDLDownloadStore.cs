using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NLog;
using NzbDrone.Common.EnvironmentInfo;
using NzbDrone.Common.Serializer;

namespace NzbDrone.Core.Download.Clients.SomeDL
{
    public class SomeDLDownload
    {
        public string DownloadId { get; set; }
        public int ClientId { get; set; }
        public string Title { get; set; }
        public long Size { get; set; }
        public DateTime AddedUtc { get; set; }
        public List<string> SomeDLIds { get; set; } = new ();

        // somedl_id -> [state, path] as SomeDL reported it once the track finished. Kept here because
        // SomeDL only holds finished tracks in memory, and a SomeDL restart before Lidarr imports
        // would otherwise lose them.
        public Dictionary<string, List<string>> Finished { get; set; } = new ();

        // Set while none of the unfinished tracks can be found in SomeDL at all (it restarted, or
        // its queue was cleared), so the download can be failed instead of waiting forever
        public DateTime? MissingSinceUtc { get; set; }

        // The YouTube Music playlist, to queue the album again when tracks failed (YouTube refuses
        // yt-dlp now and then); SomeDL skips the tracks it already has
        public string Url { get; set; }
        public int Retries { get; set; }

        // Files this download wrote in earlier attempts; a retry reports them as already downloaded,
        // which says nothing about who wrote them, and removing the download must still delete them
        public List<string> OwnFiles { get; set; } = new ();
    }

    public interface ISomeDLDownloadStore
    {
        List<SomeDLDownload> GetAll(int clientId);
        void Add(SomeDLDownload download);
        void Update(SomeDLDownload download);
        void Remove(string downloadId);
    }

    /// <summary>
    /// SomeDL has no notion of an album download, only of single tracks with a somedl_id. This maps
    /// the download id Lidarr tracks to those track ids, in a JSON file in Lidarr's AppData folder so a
    /// Lidarr restart does not orphan running downloads. Registered as a singleton by Lidarr.
    /// </summary>
    public class SomeDLDownloadStore : ISomeDLDownloadStore
    {
        private readonly object _lock = new ();
        private readonly string _path;
        private readonly Logger _logger;
        private Dictionary<string, SomeDLDownload> _downloads;

        public SomeDLDownloadStore(IAppFolderInfo appFolderInfo, Logger logger)
        {
            _path = Path.Combine(appFolderInfo.AppDataFolder, "somedl-downloads.json");
            _logger = logger;
        }

        public List<SomeDLDownload> GetAll(int clientId)
        {
            lock (_lock)
            {
                return Load().Values.Where(d => d.ClientId == clientId).ToList();
            }
        }

        public void Add(SomeDLDownload download)
        {
            lock (_lock)
            {
                Load()[download.DownloadId] = download;
                Save();
            }
        }

        public void Update(SomeDLDownload download)
        {
            Add(download);
        }

        public void Remove(string downloadId)
        {
            lock (_lock)
            {
                if (Load().Remove(downloadId))
                {
                    Save();
                }
            }
        }

        private Dictionary<string, SomeDLDownload> Load()
        {
            if (_downloads != null)
            {
                return _downloads;
            }

            _downloads = new Dictionary<string, SomeDLDownload>();

            try
            {
                if (File.Exists(_path))
                {
                    foreach (var download in Json.Deserialize<List<SomeDLDownload>>(File.ReadAllText(_path)) ?? new List<SomeDLDownload>())
                    {
                        _downloads[download.DownloadId] = download;
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.Error(ex, "Could not read {0}, SomeDL downloads started before this restart are no longer tracked", _path);
            }

            return _downloads;
        }

        private void Save()
        {
            try
            {
                // Write-then-rename, so a crash mid-write cannot leave a truncated file behind
                var temp = _path + ".tmp";
                File.WriteAllText(temp, _downloads.Values.ToList().ToJson());
                File.Move(temp, _path, true);
            }
            catch (Exception ex)
            {
                _logger.Error(ex, "Could not write {0}", _path);
            }
        }
    }
}
