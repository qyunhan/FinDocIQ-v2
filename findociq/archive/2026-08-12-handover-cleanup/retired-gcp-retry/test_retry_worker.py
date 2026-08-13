import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from common import source_store as ss
from common import retry_worker as rw


def test_retry_worker_source_file_matches_source_store_key():
    p = ss.SOURCES_ROOT / "pillar3" / "OCBC_1Q26_pillar3.pdf"
    # retry_worker must key ingest_status the same way run_doc does.
    assert rw._source_file_for(p) == ss.key_for(p) == "pillar3/OCBC_1Q26_pillar3.pdf"
