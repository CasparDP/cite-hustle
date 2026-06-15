"""Tests for the journal registry."""

from cite_hustle.collectors.journals import JournalRegistry


def test_issns_are_unique():
    """No two journals may share an ISSN.

    ISSNs key both CrossRef fetches and get_journal_dict(); a collision means one
    journal silently fetches another's articles and gets dropped from the dict.
    """
    issns = JournalRegistry.get_issn_list("all")
    duplicates = {i for i in issns if issns.count(i) > 1}
    assert not duplicates, f"Duplicate ISSNs in registry: {duplicates}"


def test_validate_unique_issns_passes():
    """The registry's own startup assertion should not raise."""
    JournalRegistry.validate_unique_issns()


def test_journal_dict_keeps_every_journal():
    """get_journal_dict() must not drop entries (it would on an ISSN collision)."""
    all_journals = JournalRegistry.get_all_journals()
    assert len(JournalRegistry.get_journal_dict()) == len(all_journals)
