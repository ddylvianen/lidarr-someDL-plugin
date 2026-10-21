using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using NzbDrone.Common.Extensions;
using NzbDrone.Core.Download;
using NzbDrone.Core.Indexers;
using NzbDrone.Core.Parser.Model;

namespace NzbDrone.Core.Blocklisting
{
    /// <summary>
    /// Lidarr only knows how to blocklist usenet and torrent releases; without this a failed Bandcamp/SoundCloud
    /// download is grabbed again straight away. A release is identified by its Guid (its page on the
    /// site), stored in the blocklist's info hash column the way torrents store theirs.
    /// </summary>
    public class ExtraDLBlocklist : IBlocklistForProtocol
    {
        private readonly IBlocklistRepository _blocklistRepository;

        public ExtraDLBlocklist(IBlocklistRepository blocklistRepository)
        {
            _blocklistRepository = blocklistRepository;
        }

        public string Protocol => nameof(ExtraDLDownloadProtocol);

        public bool IsBlocklisted(int artistId, ReleaseInfo release)
        {
            return release.Guid.IsNotNullOrWhiteSpace() &&
                   _blocklistRepository.BlocklistedByTorrentInfoHash(artistId, release.Guid).Any();
        }

        public Blocklist GetBlocklist(DownloadFailedEvent message)
        {
            return new Blocklist
            {
                ArtistId = message.ArtistId,
                AlbumIds = message.AlbumIds,
                SourceTitle = message.SourceTitle,
                Quality = message.Quality,
                Date = DateTime.UtcNow,
                PublishedDate = DateTime.TryParse(message.Data.GetValueOrDefault("publishedDate"), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var publishedDate)
                    ? publishedDate
                    : DateTime.UtcNow,
                Size = long.TryParse(message.Data.GetValueOrDefault("size", "0"), out var size) ? size : 0,
                Indexer = message.Data.GetValueOrDefault("indexer"),
                Protocol = message.Data.GetValueOrDefault("protocol"),
                Message = message.Message,
                TorrentInfoHash = message.Data.GetValueOrDefault("guid")
            };
        }
    }
}
