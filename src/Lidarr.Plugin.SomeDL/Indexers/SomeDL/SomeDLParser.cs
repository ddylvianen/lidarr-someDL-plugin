using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using NLog;
using NzbDrone.Common.Serializer;
using NzbDrone.Core.Parser.Model;
using NzbDrone.Plugin.SomeDL;

namespace NzbDrone.Core.Indexers.SomeDL
{
    public class SomeDLParser : IParseIndexerResponse
    {
        // Used when SomeDL cannot tell an album's length, so the release is not rejected for size 0
        private const int FallbackDurationSeconds = 40 * 60;

        private readonly SomeDLIndexerSettings _settings;
        private readonly ISomeDLProxy _proxy;
        private readonly Logger _logger;

        public SomeDLParser(SomeDLIndexerSettings settings, ISomeDLProxy proxy, Logger logger)
        {
            _settings = settings;
            _proxy = proxy;
            _logger = logger;
        }

        public IList<ReleaseInfo> ParseResponse(IndexerResponse indexerResponse)
        {
            var releases = new List<ReleaseInfo>();
            var hits = Json.Deserialize<SomeDLSearchResponse>(indexerResponse.Content)?.Result ?? new List<SomeDLAlbumHit>();
            var request = indexerResponse.Request as SomeDLIndexerRequest;
            var quality = SomeDLQualityInfo.For(ResolveQuality());

            var candidates = hits
                .Where(h => h.ResultType == "album" && !string.IsNullOrWhiteSpace(h.PlaylistId) && !string.IsNullOrWhiteSpace(h.Title))
                .Where(h => h.Artists.Any(a => !string.IsNullOrWhiteSpace(a.Name)))
                .Where(h => !_settings.OnlyMatchingArtist || request == null || ArtistMatches(request.Artist, h))
                .Take(_settings.MaxAlbumsPerSearch);

            foreach (var hit in candidates)
            {
                var seconds = LookUpSeconds(hit);

                if (request != null && !LengthFits(seconds, request))
                {
                    _logger.Debug("SomeDL: skipping '{0}', {1}s does not fit the album's {2}-{3}s", hit.Title, seconds, request.MinSeconds, request.MaxSeconds);
                    continue;
                }

                releases.Add(ToRelease(hit, seconds > 0 ? seconds : FallbackDurationSeconds, quality));
            }

            return releases;
        }

        // Search hits carry no length, and Lidarr's quality size limits need one. 0 when unknown.
        private int LookUpSeconds(SomeDLAlbumHit hit)
        {
            try
            {
                return _proxy.GetPlaylist(_settings.BaseUrl, hit.PlaylistId)?.DurationSeconds ?? 0;
            }
            catch (Exception ex)
            {
                _logger.Debug(ex, "SomeDL could not look up the length of {0}", hit.PlaylistId);
                return 0;
            }
        }

        // Generous on purpose: YouTube versions often add bonus tracks or drop an interlude, but a
        // single or intro sharing the album's name is a fraction of its length
        private static bool LengthFits(int seconds, SomeDLIndexerRequest request)
        {
            if (seconds <= 0 || request.MinSeconds <= 0)
            {
                return true;
            }

            return seconds >= request.MinSeconds * 0.6 && seconds <= request.MaxSeconds * 1.8;
        }

        private ReleaseInfo ToRelease(SomeDLAlbumHit hit, int seconds, SomeDLQualityInfo quality)
        {
            var artist = string.Join(", ", hit.Artists.Select(a => a.Name).Where(n => !string.IsNullOrWhiteSpace(n)));
            var hasYear = int.TryParse(hit.Year, out var year) && year > 1900;

            var title = hasYear
                ? $"{artist} - {hit.Title} ({year}) [{quality.TitleTag}] [WEB]"
                : $"{artist} - {hit.Title} [{quality.TitleTag}] [WEB]";

            return new ReleaseInfo
            {
                Guid = $"SomeDL-{hit.PlaylistId}",
                Title = title,
                Artist = artist,
                Album = hit.Title,
                Size = (long)seconds * quality.Kbps * 1000 / 8,
                DownloadUrl = $"https://music.youtube.com/playlist?list={hit.PlaylistId}",
                InfoUrl = string.IsNullOrWhiteSpace(hit.BrowseId)
                    ? $"https://music.youtube.com/playlist?list={hit.PlaylistId}"
                    : $"https://music.youtube.com/browse/{hit.BrowseId}",
                PublishDate = hasYear ? new DateTime(year, 1, 1, 0, 0, 0, DateTimeKind.Utc) : DateTime.UtcNow,
                DownloadProtocol = nameof(SomeDLDownloadProtocol),
                Source = "YouTube Music",
                Codec = quality.Codec,
                Container = quality.Container
            };
        }

        private SomeDLReleaseQuality ResolveQuality()
        {
            var configured = (SomeDLReleaseQuality)_settings.ReleaseQuality;
            if (configured != SomeDLReleaseQuality.Auto)
            {
                return configured;
            }

            try
            {
                return SomeDLQualityInfo.FromSomeDLConfig(_proxy.GetDownloadConfig(_settings.BaseUrl));
            }
            catch (Exception ex)
            {
                _logger.Debug(ex, "Could not read SomeDL's download format, assuming Opus");
                return SomeDLReleaseQuality.Opus;
            }
        }

        private static bool ArtistMatches(string searched, SomeDLAlbumHit hit)
        {
            var wanted = Normalize(searched);
            if (wanted.Length == 0 || wanted == "variousartists")
            {
                return true;
            }

            return hit.Artists.Select(a => Normalize(a.Name))
                .Any(n => n.Length > 0 && (n.Contains(wanted) || wanted.Contains(n)));
        }

        // Lowercase letters and digits only, accents folded and a leading "the" dropped, so
        // "The Beatles", "Beatles" and "Beyoncé"/"Beyonce" compare equal
        private static string Normalize(string name)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                return string.Empty;
            }

            var trimmed = name.Trim();
            if (trimmed.StartsWith("the ", StringComparison.OrdinalIgnoreCase))
            {
                trimmed = trimmed.Substring(4);
            }

            var builder = new StringBuilder();
            foreach (var c in trimmed.Normalize(NormalizationForm.FormD))
            {
                if (char.IsLetterOrDigit(c))
                {
                    builder.Append(char.ToLowerInvariant(c));
                }
            }

            return builder.ToString();
        }
    }
}
