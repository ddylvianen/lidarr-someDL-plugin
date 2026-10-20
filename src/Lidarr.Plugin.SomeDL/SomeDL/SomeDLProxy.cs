using System;
using System.Collections.Generic;
using NLog;
using NzbDrone.Common.Http;
using NzbDrone.Common.Serializer;

namespace NzbDrone.Plugin.SomeDL
{
    public interface ISomeDLProxy
    {
        SomeDLVersion GetVersion(string baseUrl);
        SomeDLDownloadConfig GetDownloadConfig(string baseUrl);
        SomeDLSearchResponse SearchAlbums(string baseUrl, string query);
        SomeDLPlaylist GetPlaylist(string baseUrl, string playlistId);
        List<string> DownloadAlbum(string baseUrl, string url);
        SomeDLStatus GetStatus(string baseUrl);
        SomeDLQueue GetQueue(string baseUrl);
        void RemoveQueuedItem(string baseUrl, string someDLId);
    }

    /// <summary>
    /// Talks to SomeDL's Flask WebUI. SomeDL documents these endpoints as internal and subject to
    /// change, so everything that depends on their shape stays in this class and SomeDLModels.
    /// </summary>
    public class SomeDLProxy : ISomeDLProxy
    {
        private readonly IHttpClient _httpClient;
        private readonly Logger _logger;

        public SomeDLProxy(IHttpClient httpClient, Logger logger)
        {
            _httpClient = httpClient;
            _logger = logger;
        }

        public SomeDLVersion GetVersion(string baseUrl)
        {
            return Get<SomeDLVersion>(baseUrl, "get-version");
        }

        public SomeDLDownloadConfig GetDownloadConfig(string baseUrl)
        {
            return Get<SomeDLSettingsResponse>(baseUrl, "settings-read")?.Config?.Download;
        }

        public SomeDLSearchResponse SearchAlbums(string baseUrl, string query)
        {
            return Post<SomeDLSearchResponse>(baseUrl, "yt-search", new { search_query = query, filter = "albums" });
        }

        public SomeDLPlaylist GetPlaylist(string baseUrl, string playlistId)
        {
            return Post<SomeDLPlaylistResponse>(baseUrl, "yt-get-album", new { album_id = playlistId })?.Result;
        }

        public List<string> DownloadAlbum(string baseUrl, string url)
        {
            // /yt-download is what SomeDL's own WebUI uses for an album picked from a search: it marks
            // every track skip_album_check, so SomeDL tags them with this album instead of re-guessing
            // the album per song (which can scatter one album over several folders).
            var response = Post<SomeDLSongListResponse>(baseUrl, "yt-download", new { url });
            var ids = new List<string>();

            foreach (var song in response?.SongList ?? new List<SomeDLSong>())
            {
                if (!string.IsNullOrWhiteSpace(song.SomeDLId))
                {
                    ids.Add(song.SomeDLId);
                }
            }

            return ids;
        }

        public SomeDLStatus GetStatus(string baseUrl)
        {
            return Get<SomeDLStatus>(baseUrl, "status");
        }

        public SomeDLQueue GetQueue(string baseUrl)
        {
            return Get<SomeDLQueue>(baseUrl, "get-queue");
        }

        public void RemoveQueuedItem(string baseUrl, string someDLId)
        {
            Post<object>(baseUrl, "remove-item", new { somedl_id = someDLId });
        }

        public static HttpRequest BuildSearchRequest(string baseUrl, string query)
        {
            return BuildRequest(baseUrl, "yt-search", new { search_query = query, filter = "albums" });
        }

        private static HttpRequest BuildRequest(string baseUrl, string resource, object body = null)
        {
            var builder = new HttpRequestBuilder(baseUrl.TrimEnd('/')).Resource(resource).Accept(HttpAccept.Json);

            if (body != null)
            {
                builder.Post();
            }

            var request = builder.Build();

            // ytmusicapi lookups behind SomeDL regularly take several seconds
            request.RequestTimeout = TimeSpan.FromSeconds(60);

            if (body != null)
            {
                request.Headers.ContentType = "application/json";
                request.SetContent(body.ToJson());
            }

            return request;
        }

        private T Get<T>(string baseUrl, string resource)
            where T : new()
        {
            return Execute<T>(BuildRequest(baseUrl, resource));
        }

        private T Post<T>(string baseUrl, string resource, object body)
            where T : new()
        {
            return Execute<T>(BuildRequest(baseUrl, resource, body));
        }

        private T Execute<T>(HttpRequest request)
            where T : new()
        {
            var response = _httpClient.Execute(request);
            _logger.Trace("SomeDL {0} -> {1}", request.Url, response.StatusCode);

            return Json.Deserialize<T>(response.Content);
        }
    }
}
