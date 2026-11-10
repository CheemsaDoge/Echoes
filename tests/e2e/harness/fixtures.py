"""Pytest fixtures for Hemory E2E test suite."""

from __future__ import annotations
import pytest
from starlette.testclient import TestClient

from .server_harness import MemoryStore, create_e2e_server_app
from .queue_harness import MockUploadQueueStorage
from .audio_harness import synthesize_adts_file, build_adts_header


@pytest.fixture
def store() -> MemoryStore:
    s = MemoryStore()
    s.seed_default_hierarchy()
    return s


@pytest.fixture
def app(store: MemoryStore):
    return create_e2e_server_app(store)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_token() -> str:
    return "token_alpha_user_001"


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def sample_adts_audio() -> bytes:
    """Generate 15 valid ADTS frames of 16kHz mono audio."""
    return synthesize_adts_file(num_frames=15, payload_size=128, sample_rate=16000, channels=1)


@pytest.fixture
def queue_storage():
    storage = MockUploadQueueStorage()
    yield storage
    storage.cleanup()
