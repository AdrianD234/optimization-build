"""The benchmark pack (instances, referee, best-known solutions) must never change."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HASHES = json.loads((Path(__file__).parent / "pack_hashes.json").read_text())


@pytest.mark.parametrize("rel", sorted(HASHES))
def test_pack_file_unchanged(rel):
    data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")  # line endings may differ by checkout
    assert hashlib.sha256(data).hexdigest() == HASHES[rel]
