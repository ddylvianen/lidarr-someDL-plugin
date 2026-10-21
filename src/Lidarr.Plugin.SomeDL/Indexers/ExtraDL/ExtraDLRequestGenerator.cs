using System.Collections.Generic;
using System.Linq;
using NzbDrone.Common.Extensions;
using NzbDrone.Core.IndexerSearch.Definitions;
using NzbDrone.Plugin.SomeDL;
using NzbDrone.Plugin.SomeDL.ExtraDL;

namespace NzbDrone.Core.Indexers.ExtraDL
{
    public class ExtraDLIndexerRequest : IndexerRequest
    {
        public ExtraDLIndexerRequest(IExtraDLSearchSettings settings, string sources, List<string> artistNames, string album, int minSeconds = 0, int maxSeconds = 0, HashSet<int> trackCounts = null)
            : base(ExtraDLProxy.BuildSearchRequest(settings.BaseUrl, artistNames.FirstOrDefault(), album, sources))
        {
            ArtistNames = artistNames;
            Album = album;
            MinSeconds = minSeconds;
            MaxSeconds = maxSeconds;
            TrackCounts = trackCounts ?? new HashSet<int>();
        }

        public List<string> ArtistNames { get; }
        public string Album { get; }
        public int MinSeconds { get; }
        public int MaxSeconds { get; }
        public HashSet<int> TrackCounts { get; }
    }

    public class ExtraDLRequestGenerator : IIndexerRequestGenerator
    {
        private readonly IExtraDLSearchSettings _settings;
        private readonly string _sources;

        public ExtraDLRequestGenerator(IExtraDLSearchSettings settings, string sources)
        {
            _settings = settings;
            _sources = sources;
        }

        // Neither site has a feed Lidarr could follow
        public IndexerPageableRequestChain GetRecentRequests()
        {
            return new IndexerPageableRequestChain();
        }

        public IndexerPageableRequestChain GetSearchRequests(AlbumSearchCriteria searchCriteria)
        {
            var durations = searchCriteria.Albums
                .SelectMany(a => a.AlbumReleases.Value.Where(r => r.Monitored || a.AnyReleaseOk))
                .Select(r => r.Duration / 1000)
                .Where(d => d > 0)
                .ToList();

            return Single(new ExtraDLIndexerRequest(_settings,
                _sources,
                NameMatch.ArtistNames(searchCriteria.Artist),
                searchCriteria.AlbumTitle,
                durations.MinOrDefault(),
                durations.MaxOrDefault(),
                NameMatch.WantedTrackCounts(searchCriteria.Albums)));
        }

        public IndexerPageableRequestChain GetSearchRequests(ArtistSearchCriteria searchCriteria)
        {
            return Single(new ExtraDLIndexerRequest(_settings, _sources, NameMatch.ArtistNames(searchCriteria.Artist), null));
        }

        private static IndexerPageableRequestChain Single(IndexerRequest request)
        {
            var chain = new IndexerPageableRequestChain();
            chain.Add(new[] { request });

            return chain;
        }
    }
}
