using System;
using NLog;
using NzbDrone.Common.Http;
using NzbDrone.Common.Serializer;

namespace NzbDrone.Plugin.SomeDL.ExtraDL
{
    public interface IExtraDLProxy
    {
        ExtraDLVersion GetVersion(string baseUrl);
        ExtraDLDetails GetDetails(string baseUrl, string url);
        string Download(string baseUrl, string url, string artist, string album, int? year);
        ExtraDLJobs GetJobs(string baseUrl);
        void RemoveJob(string baseUrl, string jobId, bool deleteFiles);
    }

    /// <summary>Talks to tools/extradl.py, the Bandcamp/SoundCloud helper that runs next to SomeDL.</summary>
    public class ExtraDLProxy : IExtraDLProxy
    {
        private readonly IHttpClient _httpClient;
        private readonly Logger _logger;

        public ExtraDLProxy(IHttpClient httpClient, Logger logger)
        {
            _httpClient = httpClient;
            _logger = logger;
        }

        public static HttpRequest BuildSearchRequest(string baseUrl, string artist, string album, string sources)
        {
            var builder = new HttpRequestBuilder(baseUrl.TrimEnd('/')).Resource("api/search").Accept(HttpAccept.Json)
                .AddQueryParam("artist", artist)
                .AddQueryParam("sources", sources);

            if (!string.IsNullOrWhiteSpace(album))
            {
                builder.AddQueryParam("album", album);
            }

            var request = builder.Build();

            // two sites searched one after the other
            request.RequestTimeout = TimeSpan.FromSeconds(90);
            return request;
        }

        public ExtraDLVersion GetVersion(string baseUrl)
        {
            return Execute<ExtraDLVersion>(Request(baseUrl, "api/version").Build());
        }

        public ExtraDLDetails GetDetails(string baseUrl, string url)
        {
            return Execute<ExtraDLDetails>(Request(baseUrl, "api/details").AddQueryParam("url", url).Build());
        }

        public string Download(string baseUrl, string url, string artist, string album, int? year)
        {
            var request = Request(baseUrl, "api/download").Post().Build();
            request.Headers.ContentType = "application/json";
            request.SetContent(new { url, artist, album, year }.ToJson());

            return Execute<ExtraDLJobId>(request)?.Id;
        }

        public ExtraDLJobs GetJobs(string baseUrl)
        {
            return Execute<ExtraDLJobs>(Request(baseUrl, "api/jobs").Build());
        }

        public void RemoveJob(string baseUrl, string jobId, bool deleteFiles)
        {
            var builder = Request(baseUrl, $"api/jobs/{jobId}").AddQueryParam("delete_files", deleteFiles ? "1" : "0");
            builder.Method = System.Net.Http.HttpMethod.Delete;
            _httpClient.Execute(builder.Build());
        }

        private static HttpRequestBuilder Request(string baseUrl, string resource)
        {
            return new HttpRequestBuilder(baseUrl.TrimEnd('/')).Resource(resource).Accept(HttpAccept.Json);
        }

        private T Execute<T>(HttpRequest request)
            where T : new()
        {
            // a lookup resolves an album page on Bandcamp or SoundCloud, which can take a while
            request.RequestTimeout = request.RequestTimeout == TimeSpan.Zero ? TimeSpan.FromSeconds(90) : request.RequestTimeout;
            var response = _httpClient.Execute(request);
            _logger.Trace("extradl {0} -> {1}", request.Url, response.StatusCode);

            return Json.Deserialize<T>(response.Content);
        }
    }
}
