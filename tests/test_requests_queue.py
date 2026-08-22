"""Dropbox-synced request queue."""

import json

import pytest

from cite_hustle import requests_queue as rq


@pytest.fixture(autouse=True)
def patched_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")


def test_append_and_read_roundtrip():
    assert rq.append_request("10.1/x", note="for lit review") is True
    entries = rq.read_requests()
    assert entries[0]["doi"] == "10.1/x"
    assert entries[0]["attempts"] == 0


def test_append_is_idempotent():
    rq.append_request("10.1/x")
    assert rq.append_request("10.1/x") is False
    assert len(rq.read_requests()) == 1


def test_read_missing_file_returns_empty():
    assert rq.read_requests() == []


def test_write_is_atomic_rewrite():
    rq.append_request("10.1/x")
    rq.append_request("10.1/y")
    entries = [e for e in rq.read_requests() if e["doi"] != "10.1/x"]
    rq.write_requests(entries)
    assert [e["doi"] for e in rq.read_requests()] == ["10.1/y"]
