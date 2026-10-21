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

        private readonly IExtraDLSearchSettings _settings;
        private readonly IExtraDLProxy _proxy;
        private readonly Logger _logger;

        public ExtraDLParser(IExtraDLSearchSettings settings, IExtraDLProxy proxy, Logger logger)
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
                foreach (var miss in hits.Where(h => !TitleMatches(request.Album, h)))
                {
                    _logger.Debug("extradl: skipping {0} '{1}', title matches '{2}' for {3:P0}", miss.Source, miss.Title, request.Album, NameMatch.Similarity(request.Album, miss.Title));
                }

                // A same-date concert counts as a full match; ties keep the order extradl gave (best tape first)
                hits = hits.Where(h => TitleMatches(request.Album, h))
                    .OrderByDescending(h => NameMatch.SameDate(request.Album, h.Title, h.Date) ? 1.0 : NameMatch.Similarity(request.Album, h.Title))
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

        private bool TitleMatches(string wanted, ExtraDLHit hit)
        {
            return NameMatch.Matches(wanted, hit.Title, _settings.MinimumNameMatch) || NameMatch.SameDate(wanted, hit.Title, hit.Date);
        }

        // What each site serves, so Lidarr's label (from the release title) matches the files it imports:
        // title tag, nominal kbps for the size estimate, codec, container, site name
        private static (string Tag, int Kbps, string Codec, string Container, string Site) QualityOf(ExtraDLHit hit)
        {
            return (hit.Source, hit.Format) switch
            {
                ("bandcamp", _) => ("MP3 128", 128, "MP3", "MP3", "Bandcamp"),
                ("soundcloud", _) => ("AAC VBR", 160, "AAC", "M4A", "SoundCloud"),
                ("jamendo", _) => ("FLAC", 900, "FLAC", "FLAC", "Jamendo"),
                ("audius", _) => ("MP3 320", 320, "MP3", "MP3", "Audius"),
                ("archive", "flac24") => ("FLAC 24bit", 2300, "FLAC", "FLAC", "Internet Archive"),
                ("archive", "flac") => ("FLAC", 900, "FLAC", "FLAC", "Internet Archive"),
                ("archive", _) => ("MP3 V0 VBR", 200, "MP3", "MP3", "Internet Archive"),
                _ => ("WEB", 192, null, null, hit.Source)
            };
        }

        private static ReleaseInfo ToRelease(ExtraDLHit hit, int seconds, int? year)
        {
            var (tag, kbps, codec, container, source) = QualityOf(hit);

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
                Codec = codec,
                Container = container
            };
        }
    }
}
