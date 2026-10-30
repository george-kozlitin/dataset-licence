from tests import parity


def test_parity_fixture_is_current():
    # The Worker's live check is tested against this file; regenerate with `python -m tests.parity`.
    assert parity.FIXTURE.read_text(encoding="utf-8") == parity.render()
