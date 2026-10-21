using System.Collections.Generic;
using Newtonsoft.Json;

namespace NzbDrone.Plugin.SomeDL.ExtraDL
{
    // Shapes of tools/extradl.py's API
    public class ExtraDLVersion
    {
        [JsonProperty("v")]
        public string Version { get; set; }

        [JsonProperty("yt_dlp")]
        public string YtDlp { get; set; }
    }

    public class ExtraDLSearchResponse
    {
        [JsonProperty("results")]
        public List<ExtraDLHit> Results { get; set; } = new ();

        // source -> error, when one site failed while the others answered
        [JsonProperty("errors")]
        public Dictionary<string, string> Errors { get; set; } = new ();
    }

    public class ExtraDLHit
    {
        [JsonProperty("source")]
        public string Source { get; set; }

        [JsonProperty("type")]
        public string Type { get; set; }

        [JsonProperty("url")]
        public string Url { get; set; }

        [JsonProperty("title")]
        public string Title { get; set; }

        [JsonProperty("artist")]
        public string Artist { get; set; }

        [JsonProperty("year")]
        public int? Year { get; set; }

        [JsonProperty("track_count")]
        public int? TrackCount { get; set; }

        [JsonProperty("duration_seconds")]
        public int? DurationSeconds { get; set; }

        // flac24, flac or mp3 where the site says; Bandcamp and SoundCloud leave it out
        [JsonProperty("format")]
        public string Format { get; set; }

        // Recording date (YYYY-MM-DD) of an Internet Archive concert
        [JsonProperty("date")]
        public string Date { get; set; }
    }

    public class ExtraDLDetails
    {
        [JsonProperty("track_count")]
        public int? TrackCount { get; set; }

        [JsonProperty("duration_seconds")]
        public int? DurationSeconds { get; set; }

        [JsonProperty("year")]
        public int? Year { get; set; }
    }

    public class ExtraDLJobId
    {
        [JsonProperty("id")]
        public string Id { get; set; }
    }

    public class ExtraDLJobs
    {
        [JsonProperty("jobs")]
        public List<ExtraDLJob> Jobs { get; set; } = new ();
    }

    public class ExtraDLJob
    {
        [JsonProperty("id")]
        public string Id { get; set; }

        [JsonProperty("album")]
        public string Album { get; set; }

        [JsonProperty("artist")]
        public string Artist { get; set; }

        // queued, downloading, completed or failed
        [JsonProperty("state")]
        public string State { get; set; }

        [JsonProperty("done")]
        public int Done { get; set; }

        [JsonProperty("total")]
        public int Total { get; set; }

        [JsonProperty("path")]
        public string Path { get; set; }

        [JsonProperty("error")]
        public string Error { get; set; }
    }
}
