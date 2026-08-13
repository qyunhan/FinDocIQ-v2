import os
from pathlib import Path

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from common import source_store as ss


def test_key_for_strips_sources_root():
    p = ss.SOURCES_ROOT / "financial_statements" / "DBS_1Q25_trading_update.pdf"
    assert ss.key_for(p) == "financial_statements/DBS_1Q25_trading_update.pdf"


def test_local_path_roundtrips_key():
    k = "pillar3/OCBC_1Q26_pillar3.pdf"
    assert ss.key_for(ss.local_path(k)) == k


def test_uri_uses_prefix_and_bucket(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    k = "financial_statements/DBS_1Q25_trading_update.pdf"
    assert ss.uri(k) == (
        "gs://findociq-sources-igc2026-team08-6311/"
        "data/sources/financial_statements/DBS_1Q25_trading_update.pdf")


def test_uri_honors_env_override(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    assert ss.uri("pillar3/x.pdf") == "gs://my-bucket/data/sources/pillar3/x.pdf"


def test_doc_id_for_matches_run_doc_convention():
    assert ss.doc_id_for("financial_statements/OCBC 1H25 FS.pdf") == "OCBC_1H25_FS"


def test_gcs_uri_for_source_is_derived_from_bare_key(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    # source_file (== K) -> gs:// uri, no double prefixing
    assert ss.gcs_uri_for_source("financial_statements/DBS_1Q25_trading_update.pdf") == (
        "gs://findociq-sources-igc2026-team08-6311/"
        "data/sources/financial_statements/DBS_1Q25_trading_update.pdf")


class _FakeBlob:
    def __init__(self, name, size=None, present=True):
        self.name = name
        self.size = size
        self._present = present
        self.downloaded_to = None
        self.uploaded_from = None

    def exists(self):
        return self._present

    def reload(self):
        if not self._present:
            raise Exception("404: blob not found (simulated reload on absent blob)")

    def download_to_filename(self, path):
        self.downloaded_to = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x" * (self.size or 1))

    def upload_from_filename(self, path):
        self.uploaded_from = path


class _FakeBucket:
    def __init__(self, blobs):
        self._blobs = {b.name: b for b in blobs}

    def list_blobs(self, prefix):
        return [b for n, b in self._blobs.items() if n.startswith(prefix)]

    def blob(self, name):
        return self._blobs.get(name) or _FakeBlob(name, present=False)


def test_list_sources_returns_keys(monkeypatch):
    bucket = _FakeBucket([
        _FakeBlob("data/sources/financial_statements/DBS_1Q25_trading_update.pdf"),
        _FakeBlob("data/sources/pillar3/OCBC_1Q26_pillar3.pdf"),
        _FakeBlob("data/sources/NAMING.md"),          # non-pdf, skipped
        _FakeBlob("data/sources/financial_statements/"),  # dir marker, skipped
    ])
    monkeypatch.setattr(ss, "_bucket", lambda: bucket)
    assert ss.list_sources() == [
        "financial_statements/DBS_1Q25_trading_update.pdf",
        "pillar3/OCBC_1Q26_pillar3.pdf",
    ]


def test_materialize_downloads_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    blob = _FakeBlob("data/sources/financial_statements/x.pdf", size=5)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([blob]))
    p = ss.materialize("financial_statements/x.pdf")
    assert p == tmp_path / "financial_statements/x.pdf"
    assert p.read_bytes() == b"xxxxx"


def test_materialize_noops_when_present_and_same_size(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    dest = tmp_path / "financial_statements/x.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"xxxxx")
    blob = _FakeBlob("data/sources/financial_statements/x.pdf", size=5)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([blob]))
    ss.materialize("financial_statements/x.pdf")
    assert blob.downloaded_to is None   # did not re-download


def test_materialize_missing_blob_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([]))
    with pytest.raises(FileNotFoundError):
        ss.materialize("financial_statements/missing.pdf")


def test_materialize_returns_cache_when_remote_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    dest = tmp_path / "financial_statements/x.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"cached")
    gone = _FakeBlob("data/sources/financial_statements/x.pdf", present=False)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([gone]))
    # fixed materialize() must check exists() first and return the cache WITHOUT
    # calling reload() on the absent blob. If it called reload() first (the old
    # bug), _FakeBlob.reload() would raise and this test would error.
    p = ss.materialize("financial_statements/x.pdf")
    assert p == dest
    assert p.read_bytes() == b"cached"


def test_resolve_to_local_passes_through_existing_path(tmp_path):
    f = tmp_path / "real.pdf"
    f.write_bytes(b"x")
    assert ss.resolve_to_local(str(f)) == f.resolve()


def test_resolve_to_local_materializes_bare_key(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    blob = _FakeBlob("data/sources/financial_statements/y.pdf", size=3)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([blob]))
    p = ss.resolve_to_local("financial_statements/y.pdf")
    assert p == tmp_path / "financial_statements/y.pdf"
    assert p.read_bytes() == b"xxx"
