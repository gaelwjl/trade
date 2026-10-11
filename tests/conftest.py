import pytest

from tradelab.data.prices import PRICES_DIR
from tradelab.store.db import open_db


@pytest.fixture
def engine():
    """A fresh, migrated in-memory database."""
    return open_db(":memory:")


requires_data = pytest.mark.skipif(
    not (PRICES_DIR / "QQQ.parquet").exists(),
    reason="needs cached prices (tradelab fetch)",
)
