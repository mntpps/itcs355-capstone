import hashlib

import pytest

from src import data_source


def write_pointer(dir_, name, content: bytes):
    md5 = hashlib.md5(content).hexdigest()
    (dir_ / f"{name}.dvc").write_text(f"outs:\n- md5: {md5}\n  path: {name}\n")
    return md5


def test_downloads_the_pointer_version_and_verifies_it(tmp_path):
    md5 = write_pointer(tmp_path, "targets.csv", b"date,pm25\n")
    asked = []

    def fake_download(blob_uri, key, dest):
        asked.append(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"date,pm25\n")

    got = data_source.ensure(tmp_path / "cache" / "targets.csv", "https://x/c/p",
                             pointer_dir=tmp_path, download=fake_download)
    assert got == md5
    assert asked == [f"dvc/files/md5/{md5[:2]}/{md5[2:]}"]


def test_refuses_content_that_does_not_match_the_pointer(tmp_path):
    write_pointer(tmp_path, "targets.csv", b"date,pm25\n")

    def tampered(blob_uri, key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"something else\n")

    with pytest.raises(ValueError, match="does not match"):
        data_source.ensure(tmp_path / "cache" / "targets.csv", "https://x/c/p",
                           pointer_dir=tmp_path, download=tampered)


def test_missing_file_without_blob_uri_fails_clearly(tmp_path):
    write_pointer(tmp_path, "targets.csv", b"x")
    with pytest.raises(FileNotFoundError, match="BLOB_URI"):
        data_source.ensure(tmp_path / "empty" / "targets.csv", None, pointer_dir=tmp_path)