"""Get the exact data version the code is meant to use.

Training: the version named in the committed DVC pointers (data/*.dvc).
Serving: the version named in the serving manifest (serving/current.json in the
DVC remote's storage), written by the daily refresh (src/refresh.py). If there is
no manifest yet, serving falls back to the committed pointers.

Locally the files are already in data/. In the cloud (no data/ folder) they are
downloaded from the DVC remote by md5 (the same content-addressed objects that
`dvc push` uploads) and verified against that md5 before use.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_KEY = "serving/current.json"


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


# ---------- blob storage (only these touch Azure) ----------

def _blob(blob_uri: str, key: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    u = urlparse(blob_uri)
    container, _, prefix = u.path.strip("/").partition("/")
    blob = "/".join(p for p in (prefix, key) if p)
    client = BlobServiceClient(f"{u.scheme}://{u.netloc}", credential=DefaultAzureCredential())
    return client.get_blob_client(container, blob)


def _download(blob_uri: str, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        _blob(blob_uri, key).download_blob().readinto(f)


def _read_text(blob_uri: str, key: str) -> str | None:
    from azure.core.exceptions import ResourceNotFoundError
    try:
        return _blob(blob_uri, key).download_blob().readall().decode("utf-8")
    except ResourceNotFoundError:
        return None


def _upload(blob_uri: str, key: str, data: bytes, overwrite: bool) -> None:
    from azure.core.exceptions import ResourceExistsError
    try:
        _blob(blob_uri, key).upload_blob(data, overwrite=overwrite)
    except ResourceExistsError:
        pass        # content-addressed: an existing object already has these exact bytes


# ---------- versions ----------

def read_manifest(blob_uri: str | None, read=_read_text) -> dict | None:
    if not blob_uri:
        return None
    text = read(blob_uri, MANIFEST_KEY)
    return json.loads(text) if text else None


def serving_versions(names, blob_uri: str | None, pointer_dir: Path = REPO_ROOT / "data",
                     read=_read_text) -> tuple[dict, str]:
    """md5 per file for serving, and where it came from ("manifest" or "pointer")."""
    manifest = read_manifest(blob_uri, read)
    if manifest:
        return {n: manifest["files"][n]["md5"] for n in names}, "manifest"
    return {n: pointer_md5(n, pointer_dir) for n in names}, "pointer"


def ensure(path: Path, blob_uri: str | None, pointer_dir: Path = REPO_ROOT / "data",
           download=_download, want: str | None = None) -> str:
    """Make sure `path` holds exactly version `want` (default: its .dvc pointer); return it."""
    want = want or pointer_md5(path.name, pointer_dir)
    if path.exists() and blob_uri and file_md5(path) != want:
        path.unlink()           # cloud copy of an older version (e.g. before a refresh)
    if not path.exists():
        if not blob_uri:
            raise FileNotFoundError(f"{path} is missing and BLOB_URI is not set")
        download(blob_uri, dvc_key(want), path)
    got = file_md5(path)
    if got != want:
        raise ValueError(f"{path.name}: md5 {got[:8]} does not match the expected version {want[:8]}")
    return want