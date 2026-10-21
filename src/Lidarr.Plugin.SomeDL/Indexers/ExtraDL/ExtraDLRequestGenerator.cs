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
        public ExtraDLIndexerRequest(ExtraDLIndexerSettings settings, List<string> artistNames, string album, int minSeconds = 0, int maxSeconds = 0)
            : base(ExtraDLProxy.BuildSearchRequest(settings.BaseUrl, artistNames.FirstOrDefault(), album, settings.Sources))
        {
            ArtistNames = artistNames;
            Album = album;
            MinSeconds = minSeconds;
            MaxSeconds = maxSeconds;
        }

        public List<string> ArtistNames { get; }
        public string Album { get; }
        public int MinSeconds { get; }
        public int MaxSeconds { get; }
    }

    public class ExtraDLRequestGenerator : IIndexerRequestGenerator
    {
        private readonly ExtraDLIndexerSettings _settings;

        public ExtraDLRequestGenerator(ExtraDLIndexerSettings settings)
        {
            _settings = settings;
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
                NameMatch.ArtistNames(searchCriteria.Artist),
                searchCriteria.AlbumTitle,
                durations.MinOrDefault(),
                durations.MaxOrDefault()));
        }

        public IndexerPageableRequestChain GetSearchRequests(ArtistSearchCriteria searchCriteria)
        {
            return Single(new ExtraDLIndexerRequest(_settings, NameMatch.ArtistNames(searchCriteria.Artist), null));
        }

        private static IndexerPageableRequestChain Single(IndexerRequest request)
        {
            var chain = new IndexerPageableRequestChain();
            chain.Add(new[] { request });

            return chain;
        }
    }
}
