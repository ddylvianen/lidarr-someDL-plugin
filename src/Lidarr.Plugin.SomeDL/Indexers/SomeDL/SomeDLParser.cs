using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
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

        // Album pages looked up when YouTube's album search has nothing with the wanted title
        private const int MaxAlbumsFromSongs = 3;

        private static readonly Regex BracketedSuffix = new (@"\s*[\(\[][^\)\]]*[\)\]]\s*$", RegexOptions.Compiled);

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
                .Where(h => !_settings.OnlyMatchingArtist || request == null || ArtistMatches(request.Artist, h.Artists))
                .ToList();

            if (request?.Album != null)
            {
                foreach (var miss in candidates.Where(h => !NameMatches(request.Album, h.Title)))
                {
                    _logger.Debug("SomeDL: skipping '{0}', title matches '{1}' for {2:P0}", miss.Title, request.Album, Similarity(request.Album, miss.Title));
                }

                // Best match first, so the limit below never cuts off the wanted album
                candidates = candidates
                    .Where(h => NameMatches(request.Album, h.Title))
                    .OrderByDescending(h => Similarity(request.Album, h.Title))
                    .ToList();

                // YouTube's album search often leaves out singles and EPs that its song search does find
                if (candidates.Count == 0)
                {
                    candidates.AddRange(AlbumsFromSongs(request));
                }
            }

            foreach (var hit in candidates.Take(_settings.MaxAlbumsPerSearch))
            {
                var seconds = hit.DurationSeconds ?? LookUpSeconds(hit);

                if (request != null && !LengthFits(seconds, request))
                {
                    _logger.Debug("SomeDL: skipping '{0}', {1}s does not fit the album's {2}-{3}s", hit.Title, seconds, request.MinSeconds, request.MaxSeconds);
                    continue;
                }

                releases.Add(ToRelease(hit, seconds > 0 ? seconds : FallbackDurationSeconds, quality));
            }

            return releases;
        }

        private List<SomeDLAlbumHit> AlbumsFromSongs(SomeDLIndexerRequest request)
        {
            var albums = new List<SomeDLAlbumHit>();

            try
            {
                var albumIds = (_proxy.SearchSongs(_settings.BaseUrl, $"{request.Artist} {request.Album}")?.Result ?? new List<SomeDLSongHit>())
                    .Where(s => !string.IsNullOrWhiteSpace(s.Album?.Id) && NameMatches(request.Album, s.Album.Name))
                    .Where(s => !_settings.OnlyMatchingArtist || ArtistMatches(request.Artist, s.Artists))
                    .Select(s => s.Album.Id)
                    .Distinct()
                    .Take(MaxAlbumsFromSongs);

                foreach (var albumId in albumIds)
                {
                    var page = _proxy.GetAlbumPage(_settings.BaseUrl, albumId);
                    if (string.IsNullOrWhiteSpace(page?.AudioPlaylistId) || string.IsNullOrWhiteSpace(page.Title))
                    {
                        continue;
                    }

                    albums.Add(new SomeDLAlbumHit
                    {
                        ResultType = "album",
                        Type = page.Type,
                        Title = page.Title,
                        Year = page.Year,
                        PlaylistId = page.AudioPlaylistId,
                        BrowseId = albumId,
                        Artists = page.Artists,
                        DurationSeconds = page.DurationSeconds
                    });
                }
            }
            catch (Exception ex)
            {
                _logger.Debug(ex, "SomeDL song search for '{0}' failed", request.Album);
            }

            _logger.Debug("SomeDL: album search had no '{0}', song search found {1} album(s)", request.Album, albums.Count);
            return albums;
        }

        private bool NameMatches(string wanted, string found)
        {
            return Similarity(wanted, found) * 100 >= _settings.MinimumNameMatch;
        }

        // 0..1, from the edit distance between the normalized names. Also compared without a trailing
        // bracketed part, so "Album (Deluxe Edition)" and "Album" count as the same name.
        private static double Similarity(string wanted, string found)
        {
            return Math.Max(
                Ratio(Normalize(wanted), Normalize(found)),
                Ratio(Normalize(BracketedSuffix.Replace(wanted ?? string.Empty, string.Empty)), Normalize(BracketedSuffix.Replace(found ?? string.Empty, string.Empty))));
        }

        private static double Ratio(string a, string b)
        {
            if (a.Length == 0 || b.Length == 0)
            {
                return 0;
            }

            var previous = new int[b.Length + 1];
            var current = new int[b.Length + 1];

            for (var j = 0; j <= b.Length; j++)
            {
                previous[j] = j;
            }

            for (var i = 1; i <= a.Length; i++)
            {
                current[0] = i;
                for (var j = 1; j <= b.Length; j++)
                {
                    var cost = a[i - 1] == b[j - 1] ? 0 : 1;
                    current[j] = Math.Min(Math.Min(current[j - 1] + 1, previous[j] + 1), previous[j - 1] + cost);
                }

                (previous, current) = (current, previous);
            }

            return 1.0 - ((double)previous[b.Length] / Math.Max(a.Length, b.Length));
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

        // Any of the credited artists, so "Dreamville, J. Cole" still matches J. Cole
        private bool ArtistMatches(string searched, IEnumerable<SomeDLArtist> artists)
        {
            var wanted = Normalize(searched);
            if (wanted.Length == 0 || wanted == "variousartists")
            {
                return true;
            }

            return (artists ?? Enumerable.Empty<SomeDLArtist>()).Any(a => NameMatches(searched, a.Name));
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
