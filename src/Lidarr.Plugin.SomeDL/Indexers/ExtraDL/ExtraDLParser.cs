using System;
using System.Collections.Generic;
using System.Linq;
using NLog;
using NzbDrone.Common.Serializer;
using NzbDrone.Core.Parser.Model;
using NzbDrone.Plugin.SomeDL;
using NzbDrone.Plugin.SomeDL.ExtraDL;

namespace NzbDrone.Core.Indexers.ExtraDL
{
    public class ExtraDLParser : IParseIndexerResponse
    {
        private const int FallbackDurationSeconds = 40 * 60;

        private readonly ExtraDLIndexerSettings _settings;
        private readonly IExtraDLProxy _proxy;
        private readonly Logger _logger;

        public ExtraDLParser(ExtraDLIndexerSettings settings, IExtraDLProxy proxy, Logger logger)
        {
            _settings = settings;
            _proxy = proxy;
            _logger = logger;
        }

        public IList<ReleaseInfo> ParseResponse(IndexerResponse indexerResponse)
        {
            var response = Json.Deserialize<ExtraDLSearchResponse>(indexerResponse.Content) ?? new ExtraDLSearchResponse();
            foreach (var error in response.Errors ?? new Dictionary<string, string>())
            {
                _logger.Warn("extradl: searching {0} failed: {1}", error.Key, error.Value);
            }

            var request = indexerResponse.Request as ExtraDLIndexerRequest;
            var hits = response.Results
                .Where(h => !string.IsNullOrWhiteSpace(h.Url) && !string.IsNullOrWhiteSpace(h.Title) && !string.IsNullOrWhiteSpace(h.Artist))
                .ToList();

            if (request != null)
            {
                foreach (var miss in hits.Where(h => !NameMatch.ArtistMatches(request.ArtistNames, h.Artist, _settings.MinimumNameMatch)))
                {
                    _logger.Debug("extradl: skipping {0} '{1} - {2}', artist is none of {3}", miss.Source, miss.Artist, miss.Title, string.Join(" / ", request.ArtistNames));
                }

                hits = hits.Where(h => NameMatch.ArtistMatches(request.ArtistNames, h.Artist, _settings.MinimumNameMatch)).ToList();
            }

            if (request?.Album != null)
            {
                foreach (var miss in hits.Where(h => !NameMatch.Matches(request.Album, h.Title, _settings.MinimumNameMatch)))
                {
                    _logger.Debug("extradl: skipping {0} '{1}', title matches '{2}' for {3:P0}", miss.Source, miss.Title, request.Album, NameMatch.Similarity(request.Album, miss.Title));
                }

                hits = hits.Where(h => NameMatch.Matches(request.Album, h.Title, _settings.MinimumNameMatch))
                    .OrderByDescending(h => NameMatch.Similarity(request.Album, h.Title))
                    .ToList();
            }

            var releases = new List<ReleaseInfo>();
            foreach (var hit in hits.Take(_settings.MaxAlbumsPerSearch))
            {
                var seconds = hit.DurationSeconds ?? 0;
                var year = hit.Year;
                if (seconds <= 0)
                {
                    try
                    {
                        var details = _proxy.GetDetails(_settings.BaseUrl, hit.Url);
                        seconds = details?.DurationSeconds ?? 0;
                        year ??= details?.Year;
                    }
                    catch (Exception ex)
                    {
                        _logger.Debug(ex, "extradl could not look up {0}", hit.Url);
                    }
                }

                if (request != null && !NameMatch.LengthFits(seconds, request.MinSeconds, request.MaxSeconds))
                {
                    _logger.Debug("extradl: skipping {0} '{1}', {2}s does not fit the album's {3}-{4}s", hit.Source, hit.Title, seconds, request.MinSeconds, request.MaxSeconds);
                    continue;
                }

                releases.Add(ToRelease(hit, seconds > 0 ? seconds : FallbackDurationSeconds, year));
            }

            return releases;
        }

        private static ReleaseInfo ToRelease(ExtraDLHit hit, int seconds, int? year)
        {
            // What each site serves, so Lidarr's label matches the files it will import: Bandcamp's free
            // stream is MP3 128 kbps, SoundCloud's AAC around 160 kbps (Lidarr files that as AAC-VBR)
            var bandcamp = hit.Source == "bandcamp";
            var tag = bandcamp ? "MP3 128" : "AAC VBR";
            var kbps = bandcamp ? 128 : 160;
            var source = bandcamp ? "Bandcamp" : "SoundCloud";

            return new ReleaseInfo
            {
                Guid = $"ExtraDL-{hit.Url}",
                Title = year > 1900 ? $"{hit.Artist} - {hit.Title} ({year}) [{tag}] [WEB]" : $"{hit.Artist} - {hit.Title} [{tag}] [WEB]",
                Artist = hit.Artist,
                Album = hit.Title,
                Size = (long)seconds * kbps * 1000 / 8,
                DownloadUrl = hit.Url,
                InfoUrl = hit.Url,
                PublishDate = year > 1900 ? new DateTime(year.Value, 1, 1, 0, 0, 0, DateTimeKind.Utc) : DateTime.UtcNow,
                DownloadProtocol = nameof(ExtraDLDownloadProtocol),
                Source = source,
                Codec = bandcamp ? "MP3" : "AAC",
                Container = bandcamp ? "MP3" : "M4A"
            };
        }
    }
}
