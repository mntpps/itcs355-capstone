"""Get the exact data version named in the committed DVC pointers.

Locally the files are already in data/. In the cloud (no data/ folder) they are
downloaded from the DVC remote by md5 (the same content-addressed objects that
`dvc push` uploaded) and verified against that md5 before use.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def pointer_md5(name: str, pointer_dir: Path) -> str:
    meta = yaml.safe_load((pointer_dir / f"{name}.dvc").read_text())
    return meta["outs"][0]["md5"]


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dvc_key(md5: str) -> str:
    return f"dvc/files/md5/{md5[:2]}/{md5[2:]}"


def _download(blob_uri: str, key: str, dest: Path) -> None:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    u = urlparse(blob_uri)
    container, _, prefix = u.path.strip("/").partition("/")
    blob = "/".join(p for p in (prefix, key) if p)
    client = BlobServiceClient(f"{u.scheme}://{u.netloc}", credential=DefaultAzureCredential())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        client.get_blob_client(container, blob).download_blob().readinto(f)


def ensure(path: Path, blob_uri: str | None, pointer_dir: Path = REPO_ROOT / "data",
           download=_download) -> str:
    """Make sure `path` holds exactly the version in its .dvc pointer; return that md5."""
    want = pointer_md5(path.name, pointer_dir)
    if not path.exists():
        if not blob_uri:
            raise FileNotFoundError(f"{path} is missing and BLOB_URI is not set")
        download(blob_uri, dvc_key(want), path)
    got = file_md5(path)
    if got != want:
        raise ValueError(f"{path.name}: md5 {got[:8]} does not match the committed pointer {want[:8]}")
    return want