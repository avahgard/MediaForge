// Build-only .NET Framework 4.x launcher. Core hashes are compiled into this
// executable through one immutable manifest digest; no user profile registry.
using System;
using System.IO;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("MediaForge")]
[assembly: System.Reflection.AssemblyProduct("MediaForge")]
[assembly: System.Reflection.AssemblyDescription("MediaForge portable launcher")]
[assembly: System.Reflection.AssemblyCopyright("Copyright © 2026 the MediaForge contributors")]
[assembly: System.Reflection.AssemblyVersion("@RELEASE_VERSION@.0")]
[assembly: System.Reflection.AssemblyFileVersion("@RELEASE_VERSION@.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("@RELEASE_VERSION@")]

internal static partial class Launcher {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
    const string ManifestHash = "@MANIFEST_SHA256@";
    [StructLayout(LayoutKind.Sequential)] struct Info {
        public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Created,Accessed,Written;
        public uint Volume,High,Low,Links,IdHigh,IdLow;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFileW(string name,uint access,uint sharing,IntPtr security,uint disposition,uint flags,IntPtr template);
    [DllImport("kernel32.dll",SetLastError=true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Info info);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle,StringBuilder name,uint length,uint flags);
    static readonly List<IDisposable> leases = new List<IDisposable>();
    static string Root;
    static string Hash(Stream stream) { using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(stream)).Replace("-","").ToLowerInvariant(); }
    static void Require(bool condition,string detail) { if(!condition) throw new InvalidOperationException(detail); }
    static string SafePath(string relative) {
        Require(relative.Length>0 && relative.Length<1024 && !relative.Contains('\\'),"Invalid core path.");
        var parts=relative.Split('/');
        Require(parts[0]=="Core" || parts[0]=="external","Undeclared payload root.");
        foreach(var part in parts) Require(part.Length>0 && part!="." && part!=".." && !part.EndsWith(".")
            && !part.EndsWith(" ") && part.IndexOfAny(Path.GetInvalidFileNameChars())<0,"Unsafe core member.");
        return Path.Combine(Root,relative.Replace('/',Path.DirectorySeparatorChar));
    }
    static Info DirectoryLease(string path) {
        var h=CreateFileW(path,0x80,3,IntPtr.Zero,3,0x02200000,IntPtr.Zero);
        if(h.IsInvalid) { h.Dispose(); throw new IOException("Cannot own core directory: "+path); }
        leases.Add(h);Info info;
        Require(GetFileInformationByHandle(h,out info) && (info.Attributes&0x410)==0x10,"Linked core directory refused.");
        return info;
    }
    static FileStream FileLease(string path) {
        var stream=OpenFile(path);
        leases.Add(stream);
        return stream;
    }
    static FileStream OpenFile(string path) {
        var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,4096,FileOptions.SequentialScan);
        try {
            Info info;
            Require(GetFileInformationByHandle(stream.SafeFileHandle,out info) && info.Links==1 && (info.Attributes&0x410)==0,"Linked core file refused.");
            var final=new StringBuilder(32768);
            uint n=GetFinalPathNameByHandleW(stream.SafeFileHandle,final,32768,0);
            string actual=final.ToString();if(actual.StartsWith("\\\\?\\"))actual=actual.Substring(4);
            Require(n>0 && n<32768 && String.Equals(actual,path,StringComparison.OrdinalIgnoreCase),"Core file resolves through an alias.");
            return stream;
        } catch { stream.Dispose(); throw; }
    }
    static void RetirePrivateImageCache(string path) {
        // Portable folder movement can leave an UNUSED image section with its
        // old filename, yielding a NULL debugger hFile in a later worker. Ask
        // NTFS to retire that cache BEFORE parent Python maps the image. No
        // bytes, attributes, ACL or system/driver files are written. An active
        // reader/image or unavailable write-data access refuses this startup.
        var h=CreateFileW(path,0xC0000000,1,IntPtr.Zero,3,0x00200000,IntPtr.Zero);
        if(h.IsInvalid) { h.Dispose(); throw new IOException("Private Python is in use or cannot be safely relocated. Close MediaForge instances and retry: "+path); }
        using(h) {
            Info info;Require(GetFileInformationByHandle(h,out info) && info.Links==1 && (info.Attributes&0x410)==0,"Unsafe private interpreter cache object.");
            var final=new StringBuilder(32768);uint n=GetFinalPathNameByHandleW(h,final,32768,0);
            string actual=final.ToString();if(actual.StartsWith("\\\\?\\"))actual=actual.Substring(4);
            Require(n>0 && n<32768 && String.Equals(actual,path,StringComparison.OrdinalIgnoreCase),"Private interpreter cache alias refused.");
        }
    }
    static IEnumerable<string> Inventory(string root) {
        var stack=new Stack<string>();stack.Push(root);int objects=0;
        while(stack.Count>0) {
            string folder=stack.Pop();DirectoryLease(folder);
            foreach(string path in Directory.GetFileSystemEntries(folder)) {
                Require(++objects<=20000,"Core inventory limit exceeded.");
                FileAttributes attributes=File.GetAttributes(path);
                Require((attributes&FileAttributes.ReparsePoint)==0,"Core reparse member refused.");
                if((attributes&FileAttributes.Directory)!=0) stack.Push(path); else yield return path;
            }
        }
    }
    [STAThread] static int Main(string[] args) {
        long startup = Stopwatch.GetTimestamp();
        Mutex instance = null;
        bool ownsInstance = false;
        bool verifyOnly = args.Length==1 && args[0]=="--verify-only";
        try {
            if(!verifyOnly)SetCurrentProcessExplicitAppUserModelID("Avahgard.MediaForge");
            Require(args.Length==0 || verifyOnly,"Unsupported launcher argument.");
            string exe=System.Reflection.Assembly.GetExecutingAssembly().Location;
            Root=Path.GetDirectoryName(exe);
            var ancestors=new Stack<string>();for(var d=new DirectoryInfo(Root);d!=null;d=d.Parent) ancestors.Push(d.FullName);
            Info rootInfo = new Info();
            foreach(var d in ancestors)rootInfo=DirectoryLease(d);
            // Key by the leased directory, including aliases of the same
            // deployment. Own through verification, Python exit and lease release.
            string key=String.Format(@"Global\MediaForge.Portable.{0:x8}.{1:x8}{2:x8}",rootInfo.Volume,rootInfo.IdHigh,rootInfo.IdLow);
            instance=new Mutex(false,key);
            try { ownsInstance=instance.WaitOne(0); }
            catch(AbandonedMutexException) { ownsInstance=true; }
            if(!ownsInstance) {
                if(!verifyOnly)return 0;
                Console.Error.WriteLine("Verification unavailable while this MediaForge deployment is starting or running.");
                return 3;
            }
#if PORTABLE_BOOTSTRAP
            if(!PrepareBootstrap(verifyOnly))return 4;
#endif
            var manifest=FileLease(Path.Combine(Root,"CoreManifest.tsv"));
            Require(manifest.Length<=4*1024*1024 && Hash(manifest)==ManifestHash,"Core manifest is not authenticated. Extract a fresh MediaForge ZIP.");
            manifest.Position=0;
            string text;using(var reader=new StreamReader(manifest,Encoding.UTF8,true,4096,true))text=reader.ReadToEnd();
            var expected=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
            long total=0;
            foreach(string line in text.Split('\n')) {
                if(line.Length==0)continue;
                var fields=line.Split('\t');long size;
                Require(fields.Length==3 && fields[0].Length==64 && Int64.TryParse(fields[1],out size) && size>=0,"Invalid core manifest row.");
                string path=SafePath(fields[2]);
                Require(!expected.ContainsKey(path) && expected.Count<16000,"Duplicate core identity.");
                total+=Int64.Parse(fields[1]);Require(total<=2L*1024*1024*1024,"Core exceeds its byte limit.");
                expected.Add(path,fields);
            }
            var files=Inventory(Path.Combine(Root,"Core")).Concat(Inventory(Path.Combine(Root,"external"))).ToArray();
            Require(files.Length==expected.Count,"Core has missing or extra files. Extract a fresh ZIP.");
            foreach(string path in files) {
                string[] row;Require(expected.TryGetValue(path,out row),"Unlisted core file refused.");
                string python=Path.Combine(Root,"Core","Python");
                string parent=Path.GetDirectoryName(path),extension=Path.GetExtension(path).ToLowerInvariant();
                if((parent==python && (extension==".dll" || Path.GetFileName(path)=="python.exe"))
                    || (parent==Path.Combine(python,"DLLs") && (extension==".dll" || extension==".pyd")))RetirePrivateImageCache(path);
                var stream=FileLease(path);
                Require(stream.Length==Int64.Parse(row[1]) && Hash(stream)==row[0],"Core file changed: "+row[2]);
            }
            long verified = Stopwatch.GetTimestamp();
            if(verifyOnly)return 0;
            var start=new ProcessStartInfo(Path.Combine(Root,"Core","Python","python.exe"));
            start.UseShellExecute=false;start.CreateNoWindow=true;start.WorkingDirectory=Root;
            start.Arguments="-I -S -B -X utf8 \""+Path.Combine(Root,"Core","portable_boot.py")+"\" \""+exe+"\" "+ManifestHash;
            start.EnvironmentVariables["MEDIAFORGE_LAUNCH_TIMING"] =
                String.Join(":", new object[] { startup, verified, Stopwatch.GetTimestamp(), Stopwatch.Frequency });
            using(var child=Process.Start(start)) {
                child.WaitForExit();
                if(child.ExitCode==2)
                    MessageBox.Show("MediaForge could not safely release its resources.\n\nSee Data\\v2\\logs\\application.log for the retained resource and cleanup error.","MediaForge",MessageBoxButtons.OK,MessageBoxIcon.Error);
                else if(child.ExitCode!=0)
                    MessageBox.Show("MediaForge stopped unexpectedly (exit "+child.ExitCode+").\n\nCheck that the complete ZIP is extracted to a writable local folder. If available, see Data\\v2\\logs\\application.log.","MediaForge",MessageBoxButtons.OK,MessageBoxIcon.Error);
                return child.ExitCode;
            }
        } catch(Exception error) {
            if(verifyOnly)Console.Error.WriteLine(error.Message);
            else MessageBox.Show("MediaForge could not start.\n\n"+error.Message,"MediaForge",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        } finally {
            try { for(int i=leases.Count-1;i>=0;i--)leases[i].Dispose(); }
            finally {
                if(ownsInstance)instance.ReleaseMutex();
                if(instance!=null)instance.Dispose();
            }
        }
    }
}
