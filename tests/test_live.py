"""Real HTTP tests must be explicitly requested; no huge model is a test dependency."""

import os

import pytest

from localforge.models import discover


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("LOCALFORGE_LIVE_MODEL_URL"), reason="Opt in with LOCALFORGE_LIVE_MODEL_URL"
)
def test_real_repository():
    repo = discover(os.environ["LOCALFORGE_LIVE_MODEL_URL"])
    assert repo.revision and repo.files
    assert any(file.sha256 for file in repo.files)
