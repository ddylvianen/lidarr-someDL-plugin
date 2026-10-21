using System.Collections.Generic;
using System.Linq;
using NzbDrone.Common.Extensions;
using NzbDrone.Core.IndexerSearch.Definitions;
using NzbDrone.Plugin.SomeDL;

namespace NzbDrone.Core.Indexers.SomeDL
{
    /// <summary>
    /// Carries what was searched for to the parser, which uses it to drop hits that cannot be the
    /// album: other artists before spending a lookup on them, and other lengths after.
    /// </summary>
    public class SomeDLIndexerRequest : IndexerRequest
    {
        public SomeDLIndexerRequest(string baseUrl, List<string> artistNames, string album, int minSeconds = 0, int maxSeconds = 0)
            : base(SomeDLProxy.BuildSearchRequest(baseUrl, album == null ? artistNames.FirstOrDefault() : $"{artistNames.FirstOrDefault()} {album}"))
        {
            ArtistNames = artistNames;
            Artist = artistNames.FirstOrDefault();
            Album = album;
            MinSeconds = minSeconds;
            MaxSeconds = maxSeconds;
        }

        public string Artist { get; }
        public List<string> ArtistNames { get; }
        public string Album { get; }

        // Shortest and longest wanted release of the album on MusicBrainz; 0 when unknown
        public int MinSeconds { get; }
        public int MaxSeconds { get; }
    }

    public class SomeDLRequestGenerator : IIndexerRequestGenerator
    {
        private readonly SomeDLIndexerSettings _settings;

        public SomeDLRequestGenerator(SomeDLIndexerSettings settings)
        {
            _settings = settings;
        }

        // YouTube Music has no feed of new releases
        public IndexerPageableRequestChain GetRecentRequests()
        {
            return new IndexerPageableRequestChain();
        }

        public IndexerPageableRequestChain GetSearchRequests(AlbumSearchCriteria searchCriteria)
        {
            // The release durations Lidarr's own size check uses, so a single or a live set that shares
            // the album's name can be told apart from the album itself
            var durations = searchCriteria.Albums
                .SelectMany(a => a.AlbumReleases.Value.Where(r => r.Monitored || a.AnyReleaseOk))
                .Select(r => r.Duration / 1000)
                .Where(d => d > 0)
                .ToList();

            // Plain names rather than Lidarr's Clean*Query: those are '+'-joined for URL query strings,
            // and ytmusicapi searches best on the title as people write it
            return Single(new SomeDLIndexerRequest(_settings.BaseUrl,
                NameMatch.ArtistNames(searchCriteria.Artist),
                searchCriteria.AlbumTitle,
                durations.MinOrDefault(),
                durations.MaxOrDefault()));
        }

        public IndexerPageableRequestChain GetSearchRequests(ArtistSearchCriteria searchCriteria)
        {
            return Single(new SomeDLIndexerRequest(_settings.BaseUrl, NameMatch.ArtistNames(searchCriteria.Artist), null));
        }

        private static IndexerPageableRequestChain Single(IndexerRequest request)
        {
            var chain = new IndexerPageableRequestChain();
            chain.Add(new[] { request });

            return chain;
        }
    }
}
