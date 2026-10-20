using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace NzbDrone.Plugin.SomeDL
{
    // Shapes of SomeDL's WebUI endpoints (SomeDL/webui/server.py). SomeDL mixes ytmusicapi's
    // camelCase with its own snake_case, so every name is spelled out.
    public class SomeDLVersion
    {
        [JsonProperty("v")]
        public string Version { get; set; }
    }

    public class SomeDLSearchResponse
    {
        [JsonProperty("result")]
        public List<SomeDLAlbumHit> Result { get; set; } = new ();
    }

    public class SomeDLAlbumHit
    {
        [JsonProperty("resultType")]
        public string ResultType { get; set; }

        [JsonProperty("type")]
        public string Type { get; set; }

        [JsonProperty("title")]
        public string Title { get; set; }

        [JsonProperty("year")]
        public string Year { get; set; }

        [JsonProperty("playlistId")]
        public string PlaylistId { get; set; }

        [JsonProperty("browseId")]
        public string BrowseId { get; set; }

        [JsonProperty("artists")]
        public List<SomeDLArtist> Artists { get; set; } = new ();
    }

    public class SomeDLArtist
    {
        [JsonProperty("id")]
        public string Id { get; set; }

        [JsonProperty("name")]
        public string Name { get; set; }
    }

    public class SomeDLPlaylistResponse
    {
        [JsonProperty("result")]
        public SomeDLPlaylist Result { get; set; }
    }

    public class SomeDLPlaylist
    {
        [JsonProperty("title")]
        public string Title { get; set; }

        [JsonProperty("trackCount")]
        public int? TrackCount { get; set; }

        [JsonProperty("duration_seconds")]
        public int? DurationSeconds { get; set; }
    }

    public class SomeDLSongListResponse
    {
        [JsonProperty("song_list")]
        public List<SomeDLSong> SongList { get; set; } = new ();
    }

    public class SomeDLSong
    {
        [JsonProperty("somedl_id")]
        public string SomeDLId { get; set; }
    }

    public class SomeDLStatus
    {
        // Keyed by somedl_id. Only presence matters to the plugin, so the per-step detail is left raw.
        [JsonProperty("active_items")]
        public Dictionary<string, JToken> ActiveItems { get; set; } = new ();

        // somedl_id -> [status, path]. Lives in SomeDL's memory only, so it is empty after a restart.
        [JsonProperty("finished_items")]
        public Dictionary<string, List<string>> FinishedItems { get; set; } = new ();

        [JsonProperty("items_in_queue")]
        public int ItemsInQueue { get; set; }
    }

    public class SomeDLQueue
    {
        [JsonProperty("queue")]
        public List<SomeDLSong> Queue { get; set; } = new ();
    }

    public class SomeDLSettingsResponse
    {
        [JsonProperty("config")]
        public SomeDLConfig Config { get; set; }
    }

    public class SomeDLConfig
    {
        [JsonProperty("download")]
        public SomeDLDownloadConfig Download { get; set; }
    }

    public class SomeDLDownloadConfig
    {
        [JsonProperty("format")]
        public string Format { get; set; }

        [JsonProperty("quality")]
        public int Quality { get; set; }

        [JsonProperty("output")]
        public string Output { get; set; }

        [JsonProperty("output_dir")]
        public string OutputDir { get; set; }

        [JsonProperty("check_if_file_exists")]
        public bool CheckIfFileExists { get; set; }
    }

    public static class SomeDLFinishState
    {
        public const string Success = "success";
        public const string Failed = "failed";
        public const string AlreadyDownloaded = "already_downloaded";
        public const string DownloadDisabled = "download_disabled";
    }
}
