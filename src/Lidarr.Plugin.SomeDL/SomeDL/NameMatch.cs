using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using NzbDrone.Core.Music;

namespace NzbDrone.Plugin.SomeDL
{
    /// <summary>
    /// How the indexers decide a hit is the album Lidarr searched for, shared so every source applies
    /// the same rule: names compared after normalizing, and a length that fits the album.
    /// </summary>
    public static class NameMatch
    {
        private static readonly Regex BracketedSuffix = new (@"\s*[\(\[][^\)\]]*[\)\]]\s*$", RegexOptions.Compiled);

        public static bool Matches(string wanted, string found, int minimumPercent)
        {
            return Similarity(wanted, found) * 100 >= minimumPercent;
        }

        // 0..1, from the edit distance between the normalized names. Also compared without a trailing
        // bracketed part, so "Album (Deluxe Edition)" and "Album" count as the same name.
        public static double Similarity(string wanted, string found)
        {
            return Math.Max(
                Ratio(Normalize(wanted), Normalize(found)),
                Ratio(Normalize(BracketedSuffix.Replace(wanted ?? string.Empty, string.Empty)), Normalize(BracketedSuffix.Replace(found ?? string.Empty, string.Empty))));
        }

        /// <summary>
        /// The artist's name plus its MusicBrainz aliases, so a band that renamed itself (Osees, released
        /// as Thee Oh Sees) or is spelled differently online still matches.
        /// </summary>
        public static List<string> ArtistNames(Artist artist)
        {
            var names = new List<string> { artist?.Name };
            try
            {
                names.AddRange(artist?.Metadata?.Value?.Aliases ?? new List<string>());
            }
            catch (Exception)
            {
                // metadata not loadable: the name alone still works
            }

            return names.FindAll(n => !string.IsNullOrWhiteSpace(n));
        }

        public static bool ArtistMatches(IEnumerable<string> wanted, string found, int minimumPercent)
        {
            foreach (var name in wanted)
            {
                if (IsVariousArtists(name) || Matches(name, found, minimumPercent))
                {
                    return true;
                }
            }

            return false;
        }

        private static readonly Regex Date = new (@"(\d{4})[-./](\d{2})[-./](\d{2})", RegexOptions.Compiled);

        /// <summary>
        /// A recording of a concert is named by venue on one site and by date on another
        /// ("1977-05-08: Barton Hall" against "Live at Barton Hall on 1977-05-08"); the same date in the
        /// wanted title and in the hit means the same show.
        /// </summary>
        public static bool SameDate(string wanted, string foundTitle, string foundDate)
        {
            var a = Date.Match(wanted ?? string.Empty);
            if (!a.Success)
            {
                return false;
            }

            var b = Date.Match($"{foundDate} {foundTitle}");
            return b.Success && a.Groups[1].Value == b.Groups[1].Value && a.Groups[2].Value == b.Groups[2].Value && a.Groups[3].Value == b.Groups[3].Value;
        }

        public static bool IsVariousArtists(string name)
        {
            var normalized = Normalize(name);
            return normalized.Length == 0 || normalized == "variousartists";
        }

        // Generous on purpose: online versions often add bonus tracks or drop an interlude, but a
        // single or intro sharing the album's name is a fraction of its length
        public static bool LengthFits(int seconds, int minSeconds, int maxSeconds)
        {
            if (seconds <= 0 || minSeconds <= 0)
            {
                return true;
            }

            return seconds >= minSeconds * 0.6 && seconds <= maxSeconds * 1.8;
        }

        // Strict, unlike the length: Lidarr rejects a new download with even one track more or less than
        // the release it matches ("Has missing tracks" / "Has unmatched tracks"). Unknown counts pass.
        public static bool TrackCountFits(int? trackCount, ICollection<int> wanted)
        {
            return trackCount is null or <= 0 || wanted == null || wanted.Count == 0 || wanted.Contains(trackCount.Value);
        }

        // Track counts of the releases Lidarr may match a download against: the monitored release, or
        // all of them when the album allows any
        public static HashSet<int> WantedTrackCounts(IEnumerable<Album> albums)
        {
            var counts = new HashSet<int>();
            foreach (var album in albums)
            {
                foreach (var release in album.AlbumReleases.Value)
                {
                    if ((release.Monitored || album.AnyReleaseOk) && release.TrackCount > 0)
                    {
                        counts.Add(release.TrackCount);
                    }
                }
            }

            return counts;
        }

        // Lowercase letters and digits only, accents folded and a leading "the" dropped, so
        // "The Beatles", "Beatles" and "Beyoncé"/"Beyonce" compare equal
        public static string Normalize(string name)
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
    }
}
