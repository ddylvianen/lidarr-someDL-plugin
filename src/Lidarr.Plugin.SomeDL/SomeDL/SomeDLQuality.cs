using System;

namespace NzbDrone.Plugin.SomeDL
{
    public enum SomeDLReleaseQuality
    {
        Auto = 0,
        Mp3V0 = 1,
        Mp3V2 = 2,
        Mp3320 = 3,
        Mp3256 = 4,
        Mp3192 = 5,
        Mp3128 = 6,
        Aac256 = 7,
        Aac192 = 8,
        Opus = 9,
        Vorbis = 10,
        Flac = 11
    }

    /// <summary>
    /// What a release from SomeDL claims to be. Lidarr reads the quality from the release title, so
    /// TitleTag is phrased to hit Lidarr's QualityParser; Kbps only feeds the size estimate that
    /// Lidarr's quality size limits check. YouTube itself serves ~128-160 kbps Opus or AAC; anything
    /// "better" is a transcode, so pick a label your quality profile accepts rather than a high one.
    /// </summary>
    public class SomeDLQualityInfo
    {
        public string TitleTag { get; private set; }
        public int Kbps { get; private set; }
        public string Codec { get; private set; }
        public string Container { get; private set; }

        public static SomeDLQualityInfo For(SomeDLReleaseQuality quality)
        {
            return quality switch
            {
                SomeDLReleaseQuality.Mp3V0 => Make("MP3 V0 VBR", 245, "MP3", "MP3"),
                SomeDLReleaseQuality.Mp3V2 => Make("MP3 V2 VBR", 190, "MP3", "MP3"),
                SomeDLReleaseQuality.Mp3320 => Make("MP3 320", 320, "MP3", "MP3"),
                SomeDLReleaseQuality.Mp3256 => Make("MP3 256", 256, "MP3", "MP3"),
                SomeDLReleaseQuality.Mp3192 => Make("MP3 192", 192, "MP3", "MP3"),
                SomeDLReleaseQuality.Mp3128 => Make("MP3 128", 128, "MP3", "MP3"),
                SomeDLReleaseQuality.Aac256 => Make("AAC 256", 256, "AAC", "M4A"),
                SomeDLReleaseQuality.Aac192 => Make("AAC 192", 192, "AAC", "M4A"),
                SomeDLReleaseQuality.Opus => Make("Opus 160", 160, "Opus", "OGG"),
                SomeDLReleaseQuality.Vorbis => Make("OGG Vorbis 160", 160, "Vorbis", "OGG"),
                SomeDLReleaseQuality.Flac => Make("FLAC", 900, "FLAC", "FLAC"),
                _ => throw new ArgumentOutOfRangeException(nameof(quality), "Auto has to be resolved with FromSomeDLConfig first")
            };
        }

        /// <summary>
        /// Maps SomeDL's [download] format to the quality Lidarr will see once the files are imported,
        /// so a grab is not followed by an "upgrade" to what is in fact the same thing.
        /// </summary>
        public static SomeDLReleaseQuality FromSomeDLConfig(SomeDLDownloadConfig config)
        {
            var format = config?.Format?.Trim().ToLowerInvariant() ?? "mp3";

            switch (format)
            {
                case "mp3":
                    // SomeDL hands its quality (0-9) to yt-dlp as a LAME VBR level, and Lidarr files every
                    // VBR MP3 it imports as MP3-VBR-V0, whatever the level
                    return SomeDLReleaseQuality.Mp3V0;
                case "m4a":
                case "best/m4a":
                    // YouTube's AAC is 128 kbps without Premium; Lidarr's lowest AAC quality is 192
                    return SomeDLReleaseQuality.Aac192;
                case "vorbis":
                case "ogg":
                    return SomeDLReleaseQuality.Vorbis;
                case "flac":
                    return SomeDLReleaseQuality.Flac;
                default:
                    // "best", "best/opus", "opus": YouTube's Opus stream, usually without re-encoding
                    return SomeDLReleaseQuality.Opus;
            }
        }

        private static SomeDLQualityInfo Make(string tag, int kbps, string codec, string container)
        {
            return new SomeDLQualityInfo { TitleTag = tag, Kbps = kbps, Codec = codec, Container = container };
        }
    }
}
