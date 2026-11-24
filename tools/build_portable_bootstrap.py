"""Build the source-only Windows bootstrap ZIP from a qualified runtime manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile


REPO = Path(__file__).resolve().parents[1]
CSC = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(output, manifest, expected_digest, ffmpeg_archive):
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a numeric three-part release version.")
    lock = (REPO / "packaging/core-lock.json").read_bytes()
    ffmpeg = (REPO / "external/manifests/ffmpeg.json").read_bytes()
    pins = json.loads(ffmpeg)
    with ffmpeg_archive.open("rb") as archive:
        if hashlib.file_digest(archive, "sha256").hexdigest() != pins["sha256"]:
            raise ValueError("The local FFmpeg archive differs from its pinned identity.")
        ffmpeg_size = archive.tell()
        with zipfile.ZipFile(archive) as vendor:
            notice = vendor.read(pins["ffmpeg_path"].split("/")[0] + "/LICENSE")
    inputs = [
        ("python" if asset["name"] == "CPython" else "wheel", asset["name"],
         str(asset["size"]), asset["sha256"], asset["url"])
        for asset in json.loads(lock)["assets"]
    ]
    inputs.append(("ffmpeg", "FFmpeg / FFprobe", str(ffmpeg_size), pins["sha256"],
                   pins["primary_url"], pins["mirror_url"]))
    acquisition = "".join("\t".join(row) + "\n" for row in inputs).encode()
    raw = manifest.read_bytes()
    if digest(raw) != expected_digest:
        raise ValueError("Qualified input manifest SHA-256 differs.")
    runtime = {}
    for line in raw.decode().splitlines():
        sha, size, name = line.split("\t")
        if name.startswith(("Core/Python/", "Core/Site/", "external/ffmpeg/")):
            runtime[name] = (int(size), sha)
    for name in ("ffmpeg", "ffprobe"):
        if runtime["external/ffmpeg/" + pins[name + "_path"]][1] != pins[name + "_sha256"]:
            raise ValueError("The qualified managed pair differs from its pin.")
    metadata = f"Core/Site/mediaforge-{version}.dist-info/METADATA"
    if not runtime.pop(metadata, None):
        raise ValueError("The qualified manifest must use the selected MediaForge version.")
    runtime["Core/Notices/Gyan-FFmpeg.txt"] = (len(notice), digest(notice))
    seed = {
        "Core/portable_boot.py": (REPO / "packaging/portable_boot.py").read_bytes(),
        "Core/Provenance/core-lock.json": lock,
        "external/manifests/ffmpeg.json": ffmpeg,
        metadata: f"Metadata-Version: 2.1\nName: mediaforge\nVersion: {version}\n".encode(),
    }
    for file in (REPO / "src/mediaforge").rglob("*"):
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
            seed["Core/App/" + file.relative_to(REPO / "src").as_posix()] = file.read_bytes()
    if runtime.keys() & seed.keys():
        raise ValueError("A seed member overlaps a vendor-supplied member.")
    runtime.update({name: (len(data), digest(data)) for name, data in seed.items()})
    encoded = "".join(f"{sha}\t{size}\t{name}\n" for name, (size, sha) in sorted(runtime.items())).encode()
    files = {f"Bootstrap/Seed/{name}": data for name, data in seed.items()}
    files.update({"Bootstrap/CoreManifest.tsv": encoded, "Bootstrap/Inputs.tsv": acquisition})
    for path in ("README.md", "RELEASE_NOTES.md", "LICENSE", "COPYRIGHT.md",
                 "docs/THIRD_PARTY_NOTICES.md", "docs/PORTABLE_BOOTSTRAP.md", "licenses/Apache-2.0.txt"):
        files[path] = (REPO / path).read_bytes()
    readme = files["README.md"].decode()
    start, end = readme.index("Start development with"), readme.index("## License")
    files["README.md"] = (readme[:start] + readme[end:]).encode()
    files["docs/THIRD_PARTY_NOTICES.md"] = files["docs/THIRD_PARTY_NOTICES.md"].replace(
        b"../src/mediaforge", b"../Bootstrap/Seed/Core/App/mediaforge"
    )
    files["packaging/core-lock.json"], files["external/manifests/ffmpeg.json"] = lock, ffmpeg
    output.mkdir()
    for key in ("Launcher", "Bootstrap"):
        code = (REPO / f"packaging/{key}.cs").read_text(encoding="utf-8")
        code = code.replace("@MANIFEST_SHA256@", digest(encoded)).replace("@INPUT_SHA256@", digest(acquisition))
        (output / f"{key}.cs").write_text(code.replace("@RELEASE_VERSION@", version), encoding="utf-8")
    executable = output / "MediaForge.exe"
    references = ["mscorlib", "System", "System.Core", "System.Windows.Forms", "System.Drawing", "System.IO.Compression"]
    subprocess.run([str(CSC), "/nologo", "/noconfig", "/nostdlib+", "/target:winexe", "/platform:x64",
                    "/optimize+", "/debug-", "/codepage:65001", "/define:PORTABLE_BOOTSTRAP",
                    *["/reference:" + str(CSC.parent / f"{r}.dll") for r in references],
                    "/out:" + str(executable), str(output / "Launcher.cs"), str(output / "Bootstrap.cs")],
                   check=True, timeout=60)
    files["MediaForge.exe"] = executable.read_bytes()
    archive = output / f"MediaForge-{version}-win64.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(files.items()):
            package.writestr("MediaForge/" + name, data)
    with zipfile.ZipFile(archive) as package:
        if sorted(package.namelist()) != sorted("MediaForge/" + name for name in files):
            raise ValueError("Bootstrap archive membership differs.")
        for name, data in files.items():
            if package.read("MediaForge/" + name) != data:
                raise ValueError(f"Bootstrap archive member differs: {name}")
    receipt = {
        "version": version, "zip": archive.name, "sha256": digest(archive.read_bytes()),
        "files": {name: digest(data) for name, data in sorted(files.items())},
        "download_bytes": sum(int(row[2]) for row in inputs),
        "runtime_bytes": sum(size for size, _ in runtime.values()),
        "runtime_manifest_sha256": digest(encoded),
    }
    (output / "bootstrap-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text(f"{receipt['sha256']}  {archive.name}\n", encoding="ascii")
    print(f"{archive} ({len(files)} files, SHA-256 {receipt['sha256']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--ffmpeg-archive", required=True, type=Path)
    args = parser.parse_args()
    build(args.output.resolve(), args.manifest, args.manifest_sha256, args.ffmpeg_archive)
