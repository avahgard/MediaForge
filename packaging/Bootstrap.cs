using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static partial class Launcher {
    const string InputHash = "@INPUT_SHA256@";
    const string ReleaseVersion = "@RELEASE_VERSION@";
    static readonly HashSet<string> Hosts = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
        "files.pythonhosted.org", "github.com", "release-assets.githubusercontent.com", "www.gyan.dev"
    };

    sealed class Asset {
        public string Kind, Name, Digest;
        public long Bytes;
        public string[] Sources;
    }

    static byte[] AuthenticatedText(string path, string digest, int limit) {
        using(var stream = OpenFile(path)) {
            Require(stream.Length <= limit && Hash(stream) == digest, "The setup files have changed. Extract a fresh ZIP.");
            stream.Position = 0;
            var data = new byte[(int)stream.Length];
            ReadExact(stream, data, data.Length);
            return data;
        }
    }

    static bool PrepareBootstrap(bool verifyOnly) {
        string runtime = Path.Combine(Root, "Runtime");
        string installed = Path.Combine(runtime, ReleaseVersion);
        if(Directory.Exists(installed)) {
            DirectoryLease(runtime);
            DirectoryLease(installed);
            Root = installed;
            return true;
        }
        if(verifyOnly) {
            Console.Error.WriteLine("Runtime is not prepared. Launch MediaForge.exe and confirm setup.");
            return false;
        }
        string setup = Path.Combine(Root, "Bootstrap");
        DirectoryLease(setup);
        byte[] pinned = AuthenticatedText(Path.Combine(setup, "Inputs.tsv"), InputHash, 16384);
        byte[] manifest = AuthenticatedText(Path.Combine(setup, "CoreManifest.tsv"), ManifestHash, 4*1024*1024);
        var assets = Encoding.UTF8.GetString(pinned).Split(new[]{'\n'}, StringSplitOptions.RemoveEmptyEntries).Select(line => {
            var fields = line.Split('\t');
            Require(fields.Length == 5 || fields.Length == 6, "Invalid setup input.");
            return new Asset {Kind=fields[0], Name=fields[1], Bytes=Int64.Parse(fields[2]), Digest=fields[3], Sources=fields.Skip(4).ToArray()};
        }).ToArray();
        long maximum = assets.Sum(x => x.Bytes);
        var answer = MessageBox.Show("MediaForge needs a one-time download to prepare this folder.\n\n"
            + "Download from PyPI, GitHub and Gyan (up to " + ((maximum + 1048575)/1048576)
            + " MiB). Once prepared, Media works offline. Optional dubbing models are managed in Settings.\n\n"
            + "Prepare this portable copy now?", "MediaForge setup", MessageBoxButtons.YesNo,
            MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
        if(answer != DialogResult.Yes)return false;
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        MakeDirectory(runtime);
        string cache = Path.Combine(runtime, "Cache");
        MakeDirectory(cache);
        string stage = Path.Combine(runtime, ".preparing-" + ReleaseVersion);
        if(!WithProgress((token, report) => {
            if(Directory.Exists(stage))RemoveStage(stage);
            long needed = manifest.Length + Encoding.UTF8.GetString(manifest).Split('\n')
                .Where(line => line.Length > 0).Sum(line => Int64.Parse(line.Split('\t')[1]));
            foreach(var asset in assets)
                if(!File.Exists(Path.Combine(cache, asset.Digest)))needed += asset.Bytes;
            Require(new DriveInfo(Path.GetPathRoot(Root)).AvailableFreeSpace >= needed + 128L*1024*1024,
                "Free some space on the portable drive and launch MediaForge again.");
            var paths = new Dictionary<Asset,string>();
            for(int i = 0; i < assets.Length; i++) {
                var asset = assets[i];
                string prefix = String.Format("{0} of {1} — {2}", i+1, assets.Length, asset.Name);
                report(prefix);
                paths.Add(asset, Acquire(cache, asset, token, (done) => report(prefix + String.Format("\n{0:0.0} / {1:0.0} MiB", done/1048576.0, asset.Bytes/1048576.0))));
            }
            report("Preparing and verifying the local runtime…");
            int hold = leases.Count;
            try {
                MakeDirectory(stage);
                Assemble(setup, stage, manifest, paths, token);
            } finally {
                for(int i = leases.Count-1; i >= hold; i--) { leases[i].Dispose(); leases.RemoveAt(i); }
            }
            token.ThrowIfCancellationRequested();
            Directory.Move(stage, installed);
        }))return false;
        DirectoryLease(installed);
        Root = installed;
        return true;
    }

    static void MakeDirectory(string path) {
        Directory.CreateDirectory(path);
        DirectoryLease(path);
    }

    static void RemoveStage(string folder) {
        // Only the reserved per-release staging path is passed here.
        int entries = 0;
        RemoveStageContents(folder, ref entries);
    }

    static void RemoveStageContents(string folder, ref int entries) {
        int lease = leases.Count;
        try {
            DirectoryLease(folder);
            foreach(string child in Directory.EnumerateFileSystemEntries(folder)) {
                Require(++entries <= 20000, "Setup staging inventory is too large.");
                var attributes = File.GetAttributes(child);
                Require((attributes & FileAttributes.ReparsePoint) == 0, "Unexpected link in setup staging.");
                if((attributes & FileAttributes.Directory) != 0)RemoveStageContents(child, ref entries);
                else { using(OpenFile(child)) {} File.Delete(child); }
            }
        } finally {
            if(leases.Count > lease) { leases[lease].Dispose(); leases.RemoveAt(lease); }
        }
        Directory.Delete(folder);
    }

    static string Acquire(string cache, Asset asset, CancellationToken token, Action<long> progress) {
        string target = Path.Combine(cache, asset.Digest), part = target + ".part";
        if(File.Exists(target)) {
            using(var input = OpenFile(target))
                Require(input.Length == asset.Bytes && Hash(input) == asset.Digest,
                    "The saved " + asset.Name + " download failed verification. Remove " + target + " and retry.");
            return target;
        }
        if(File.Exists(part)) { using(OpenFile(part)) {} File.Delete(part); }
        Exception failure = null;
        foreach(string source in asset.Sources) {
            try {
                Download(source, part, asset, token, progress);
                using(var input = OpenFile(part))
                    Require(input.Length == asset.Bytes && Hash(input) == asset.Digest,
                        asset.Name + " download has the wrong SHA-256.");
                token.ThrowIfCancellationRequested();
                File.Move(part, target);
                return target;
            } catch(Exception error) {
                if(File.Exists(part)) { using(OpenFile(part)) {} File.Delete(part); }
                token.ThrowIfCancellationRequested();
                failure = error;
            }
        }
        throw new IOException(asset.Name + " could not be downloaded. Check your connection and try again.\n\n" + failure.Message);
    }

    static void Download(string source, string part, Asset asset, CancellationToken token, Action<long> progress) {
        var url = new Uri(source);
        for(int redirect = 0; redirect < 5; redirect++) {
            token.ThrowIfCancellationRequested();
            Require(url.Scheme == "https" && url.IsDefaultPort && url.UserInfo.Length == 0 && Hosts.Contains(url.Host),
                "Setup refused an unexpected download source.");
            var request = (HttpWebRequest)WebRequest.Create(url);
            request.AllowAutoRedirect = false;
            request.Timeout = 20000;
            request.ReadWriteTimeout = 20000;
            request.UserAgent = "MediaForge/" + ReleaseVersion;
            using(token.Register(request.Abort))
            using(var response = (HttpWebResponse)request.GetResponse()) {
                int status = (int)response.StatusCode;
                if(status >= 300 && status < 400 && response.Headers["Location"] != null) {
                    url = new Uri(url, response.Headers["Location"]);
                    continue;
                }
                Require(status == 200 && (response.ContentLength == -1 || response.ContentLength == asset.Bytes)
                    && (String.IsNullOrEmpty(response.ContentEncoding) ||
                        String.Equals(response.ContentEncoding, "identity", StringComparison.OrdinalIgnoreCase)),
                    "The upstream server returned unexpected download metadata.");
                using(var input = response.GetResponseStream())
                using(var output = new FileStream(part, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    var elapsed = System.Diagnostics.Stopwatch.StartNew();
                    long count = 0, next = 0;
                    byte[] buffer = new byte[128*1024];
                    int read;
                    while((read = input.Read(buffer, 0, buffer.Length)) > 0) {
                        token.ThrowIfCancellationRequested();
                        count += read;
                        Require(count <= asset.Bytes && elapsed.Elapsed.TotalMinutes < 20, "The download exceeded its size or time limit.");
                        output.Write(buffer, 0, read);
                        if(count >= next) { progress(count); next = count + 1048576; }
                    }
                    Require(count == asset.Bytes, "The download ended early.");
                }
                return;
            }
        }
        throw new IOException("Too many upstream redirects.");
    }

    static void ReadExact(Stream input, byte[] bytes, int size) {
        int offset = 0;
        while(offset < size) {
            int count = input.Read(bytes, offset, size-offset);
            if(count == 0)throw new EndOfStreamException("Truncated setup archive.");
            offset += count;
        }
    }

    static void Assemble(string setup, string stage, byte[] manifest, Dictionary<Asset,string> paths, CancellationToken token) {
        var expected = new Dictionary<string,string[]>(StringComparer.Ordinal);
        foreach(string line in Encoding.UTF8.GetString(manifest).Split('\n')) {
            if(line.Length == 0)continue;
            var row = line.Split('\t');
            SafePath(row[2]);
            expected.Add(row[2], row);
        }
        var dirs = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {stage, setup};
        var complete = new HashSet<string>(StringComparer.Ordinal);
        foreach(var item in paths) {
            token.ThrowIfCancellationRequested();
            using(var archive = OpenFile(item.Value)) {
                Require(archive.Length == item.Key.Bytes && Hash(archive) == item.Key.Digest, "The cached setup input changed.");
                archive.Position = 0;
                if(item.Key.Kind == "python") {
                    using(var tar = new GZipStream(archive, CompressionMode.Decompress))
                        ReadTar(tar, (name, input, length) => {
                            token.ThrowIfCancellationRequested();
                            if(name.StartsWith("python/", StringComparison.Ordinal))
                                WriteMember(stage, "Core/Python/" + name.Substring(7), input, length, expected, complete, dirs, token);
                        });
                } else {
                    using(var zip = new ZipArchive(archive, ZipArchiveMode.Read))
                        foreach(var itemFile in zip.Entries) {
                            token.ThrowIfCancellationRequested();
                            string name = item.Key.Kind == "wheel" ? "Core/Site/" + itemFile.FullName :
                                itemFile.FullName.EndsWith("/LICENSE", StringComparison.Ordinal) ? "Core/Notices/Gyan-FFmpeg.txt" :
                                "external/ffmpeg/" + itemFile.FullName;
                            if(!expected.ContainsKey(name))continue;
                            Require(((itemFile.ExternalAttributes >> 16) & 0xF000) != 0xA000, "Archive links are refused.");
                            using(var input = itemFile.Open())
                                WriteMember(stage, name, input, itemFile.Length, expected, complete, dirs, token);
                        }
                }
            }
        }
        foreach(string name in expected.Keys.Where(x => !complete.Contains(x))) {
            token.ThrowIfCancellationRequested();
            string seed = Path.Combine(setup, "Seed", name.Replace('/', '\\'));
            HoldParents(Path.GetDirectoryName(seed), setup, dirs, false);
            using(var input = OpenFile(seed))
                WriteMember(stage, name, input, input.Length, expected, complete, dirs, token);
        }
        Require(complete.Count == expected.Count, "Setup did not produce the complete runtime.");
        using(var file = new FileStream(Path.Combine(stage, "CoreManifest.tsv"), FileMode.CreateNew))
            file.Write(manifest, 0, manifest.Length);
    }

    static void HoldParents(string folder, string boundary, HashSet<string> held, bool create) {
        if(held.Contains(folder))return;
        Require(folder.StartsWith(boundary + "\\", StringComparison.OrdinalIgnoreCase), "Setup path escaped its directory.");
        HoldParents(Path.GetDirectoryName(folder), boundary, held, create);
        if(create)Directory.CreateDirectory(folder);
        DirectoryLease(folder);
        held.Add(folder);
    }

    static void WriteMember(string stage, string name, Stream input, long length, Dictionary<string,string[]> expected,
                            HashSet<string> complete, HashSet<string> dirs, CancellationToken token) {
        string[] row;
        if(!expected.TryGetValue(name, out row) || complete.Contains(name))return;
        Require(length == Int64.Parse(row[1]), "Unexpected size for runtime member " + name);
        string path = Path.Combine(stage, name.Replace('/', '\\'));
        HoldParents(Path.GetDirectoryName(path), stage, dirs, true);
        byte[] block = new byte[64*1024];
        using(var output = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None)) {
            for(long left = length; left > 0;) {
                token.ThrowIfCancellationRequested();
                int count = (int)Math.Min(left, block.Length);
                ReadExact(input, block, count);
                output.Write(block, 0, count);
                left -= count;
            }
            output.Position = 0;
            Require(Hash(output) == row[0], "Runtime member failed verification: " + name);
        }
        complete.Add(name);
    }

    // Read only regular members from the authenticated Python archive. Nothing in
    // the tar can create a path unless the release manifest names that exact file.
    static void ReadTar(Stream input, Action<string,Stream,long> member) {
        byte[] header = new byte[512], block = new byte[64*1024];
        string extendedName = null;
        while(true) {
            ReadExact(input, header, header.Length);
            if(header.All(x => x == 0))return;
            string name = TarText(header, 0, 100), prefix = TarText(header, 345, 155);
            if(prefix.Length != 0 && TarText(header, 257, 6) == "ustar")name = prefix + "/" + name;
            long size = Convert.ToInt64(TarText(header, 124, 12).Trim(), 8);
            Require(size >= 0 && size <= 256L*1024*1024, "Unexpected tar member size.");
            int type = header[156];
            if(type == 'L' || type == 'x') {
                Require(size <= 65536, "Oversized tar metadata.");
                ReadExact(input, block, (int)size);
                string text = TarText(block, 0, (int)size);
                extendedName = type == 'L' ? text : text.Split('\n').Where(x => x.Contains(" path="))
                    .Select(x => x.Substring(x.IndexOf(" path=", StringComparison.Ordinal)+6)).LastOrDefault();
            } else {
                if(type == 0 || type == '0') {
                    using(var section = new TarSection(input, size)) {
                        member(extendedName ?? name, section, size);
                        while(section.Read(block, 0, block.Length) > 0) {}
                    }
                } else { Require(size == 0, "Unsupported tar data member."); }
                extendedName = null;
            }
            int padding = (int)((512-size%512)%512);
            if(padding > 0)ReadExact(input, block, padding);
        }
    }

    static string TarText(byte[] bytes, int offset, int count) {
        int end = Array.IndexOf(bytes, (byte)0, offset, count);
        return Encoding.UTF8.GetString(bytes, offset, end < 0 ? count : end-offset);
    }

    sealed class TarSection : Stream {
        readonly Stream input;
        long left;
        public TarSection(Stream value, long bytes) { input = value; left = bytes; }
        public override int Read(byte[] buffer, int offset, int count) {
            if(left == 0)return 0;
            int read = input.Read(buffer, offset, (int)Math.Min(count, left));
            if(read == 0)throw new EndOfStreamException("Truncated tar member.");
            left -= read;
            return read;
        }
        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return false; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { throw new NotSupportedException(); } }
        public override long Position { get { throw new NotSupportedException(); } set { throw new NotSupportedException(); } }
        public override void Flush() { throw new NotSupportedException(); }
        public override long Seek(long value, SeekOrigin origin) { throw new NotSupportedException(); }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }

    static bool WithProgress(Action<CancellationToken,Action<string>> work) {
        using(var cancel = new CancellationTokenSource())
        using(var form = new Form {Text="Preparing MediaForge", Width=440, Height=165, StartPosition=FormStartPosition.CenterScreen,
                                  FormBorderStyle=FormBorderStyle.FixedDialog, MaximizeBox=false, MinimizeBox=false}) {
            var caption = new Label {Left=16, Top=14, Width=390, Height=40, Text="Checking portable storage…"};
            var activity = new ProgressBar {Left=16, Top=59, Width=390, Height=7, Style=ProgressBarStyle.Marquee};
            var stop = new Button {Left=319, Top=80, Width=87, Text="Cancel"};
            form.Controls.AddRange(new Control[]{caption, activity, stop});
            Action requestCancel = () => { stop.Enabled = false; caption.Text = "Cancelling setup…"; cancel.Cancel(); };
            stop.Click += (sender, args) => requestCancel();
            bool done = false, success = false;
            Exception failure = null;
            form.FormClosing += (sender, args) => { if(!done) { args.Cancel = true; requestCancel(); } };
            form.Shown += (sender, args) => new Thread(() => {
                try {
                    work(cancel.Token, message => form.BeginInvoke((Action)(() => { if(!cancel.IsCancellationRequested)caption.Text = message; })));
                    success = true;
                } catch(OperationCanceledException) {}
                catch(Exception error) { failure = error; }
                finally { form.BeginInvoke((Action)(() => { done = true; form.Close(); })); }
            }) {IsBackground = true}.Start();
            form.ShowDialog();
            if(failure != null)throw failure;
            return success;
        }
    }
}
