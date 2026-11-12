"""Build a reproducible runtime artifact from tracked source and hash-locked wheels."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.revision):
        parser.error("revision must be a full Git commit SHA")
    if subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip() != args.revision:
        raise SystemExit("Package revision does not match the checkout.")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"]).strip():
        raise SystemExit("Refusing to label modified source as a clean Git release.")
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    sources = [
        Path(p)
        for p in tracked
        if p
        and (
            p
            in [
                "manage.py",
                "requirements.lock",
                "LICENSE",
                "ARTWORK-LICENSE.txt",
                "THIRD_PARTY_NOTICES.md",
                "README.md",
                "CHANGELOG.md",
                "VERSION",
            ]
            or p.startswith(
                ("prepperdb/", "stock/", "templates/", "static/", "docs/", "deploy/examples/")
            )
        )
        and not p.startswith("stock/tests/")
    ]
    expected = set(
        re.findall(r"--hash=sha256:([a-f0-9]{64})", Path("requirements.lock").read_text())
    )
    wheels = sorted(args.wheel_dir.glob("*.whl"))
    actual = {hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheels}
    if actual != expected:
        raise SystemExit("Wheel files do not match the runtime lock exactly.")
    files = {str(path): path.read_bytes() for path in sources}
    files.update({f"wheels/{wheel.name}": wheel.read_bytes() for wheel in wheels})
    files["release.json"] = (
        json.dumps(
            {
                "revision": args.revision,
                "files": {
                    name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())
                },
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with (
        args.output.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as zipped,
        tarfile.open(fileobj=zipped, mode="w") as archive,
    ):
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(digest + "\n")
    print(f"Packaged {args.revision}: {len(files)} files, sha256 {digest}")


if __name__ == "__main__":
    main()
