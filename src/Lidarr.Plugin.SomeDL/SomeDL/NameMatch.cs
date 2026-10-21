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
