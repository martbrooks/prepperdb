"""Build public source, runtime-with-wheels and artwork archives from clean tracked files."""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    version = Path("VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version):
        raise SystemExit("Invalid release version.")
    if not Path("LICENSE").is_file():
        raise SystemExit("Select and include the project licence before building a public release.")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"]).strip():
        raise SystemExit("Refusing a release from modified tracked source.")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    tracked = [path for path in tracked if path]
    # Audit names and private infrastructure markers before anything is published.
    for name in tracked:
        if any(
            part in {"var", ".venv", "__pycache__", "test-results"} for part in Path(name).parts
        ):
            raise SystemExit(f"Private/generated path in release: {name}")
        if any(part.startswith(".env") for part in Path(name).parts) or name.endswith(
            (".sqlite3", ".pem", ".key")
        ):
            raise SystemExit(f"Private configuration/state in release: {name}")
        content = Path(name).read_bytes()
        if (b"hinter" + b"lands") in content.lower() or (b"/home/" + b"codex") in content:
            raise SystemExit(f"Private infrastructure reference in release: {name}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = args.output_dir / f"prepperdb-{version}-source.tar.gz"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            f"--prefix=prepperdb-{version}/",
            "-o",
            str(source),
            revision,
        ],
        check=True,
    )
    runtime = args.output_dir / f"prepperdb-{version}-runtime-linux-x86_64.tar.gz"
    subprocess.run(
        [
            "python3",
            "ci/package.py",
            "--revision",
            revision,
            "--wheel-dir",
            str(args.wheel_dir),
            "--output",
            str(runtime),
        ],
        check=True,
    )
    graphics = args.output_dir / f"prepperdb-{version}-graphics.zip"
    artwork = sorted(
        path
        for path in tracked
        if path.startswith(("static/branding/", "static/icons/"))
        or path in {"static/placeholder.svg", "LICENSE", "THIRD_PARTY_NOTICES.md"}
    )
    with zipfile.ZipFile(graphics, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in artwork:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, Path(name).read_bytes())
    # All runtime graphics and licence files must survive packaging byte-for-byte.
    with tarfile.open(runtime) as archive:
        for name in artwork:
            assert archive.extractfile(name).read() == Path(name).read_bytes(), name
    checksums = "".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in [source, runtime, graphics]
    )
    (args.output_dir / "SHA256SUMS").write_text(checksums)
    print(f"Built v{version} from {revision}; {len(artwork)} artwork/licence files verified.")


if __name__ == "__main__":
    main()
