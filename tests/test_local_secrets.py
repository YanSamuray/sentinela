"""DPAPI tests use disposable synthetic keys, never the operational secret."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from totens import local_secrets
from totens.local_secrets import SecretError, load_local_key


WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows user-scoped DPAPI required")
SYNTHETIC_KEY = bytes(range(32))
PREFIX = b"TOTENS-DPAPI-V1\n"


@WINDOWS_ONLY
def test_dpapi_roundtrip_persists_only_ciphertext_and_reuses_key(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "synthetic-key.dpapi"
    monkeypatch.setattr(local_secrets.secrets, "token_bytes", lambda size: SYNTHETIC_KEY)

    first = load_local_key(path, create=True)
    ciphertext = path.read_bytes()
    assert first == SYNTHETIC_KEY
    assert len(first) == 32
    assert ciphertext.startswith(PREFIX)
    assert SYNTHETIC_KEY not in ciphertext
    assert SYNTHETIC_KEY.hex().encode("ascii") not in ciphertext

    def unexpected_generation(size: int) -> bytes:
        pytest.fail("An existing key must not trigger secret generation")

    monkeypatch.setattr(local_secrets.secrets, "token_bytes", unexpected_generation)
    assert load_local_key(path) == first
    assert load_local_key(path, create=True) == first
    assert path.read_bytes() == ciphertext


def test_missing_key_without_create_does_not_create_directories(tmp_path: Path) -> None:
    path = tmp_path / "absent-directory" / "synthetic-key.dpapi"
    with pytest.raises(SecretError, match="^secret_missing$"):
        load_local_key(path)
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("create", [False, True])
def test_invalid_envelope_is_never_overwritten(tmp_path: Path, monkeypatch, create: bool) -> None:
    path = tmp_path / "synthetic-key.dpapi"
    corrupted = b"SYNTHETIC-INVALID-ENVELOPE"
    path.write_bytes(corrupted)

    def unexpected_generation(size: int) -> bytes:
        pytest.fail("Corrupt existing files must not trigger secret replacement")

    monkeypatch.setattr(local_secrets.secrets, "token_bytes", unexpected_generation)
    with pytest.raises(SecretError, match="^invalid_secret_envelope$"):
        load_local_key(path, create=create)
    assert path.read_bytes() == corrupted


@WINDOWS_ONLY
def test_tampered_ciphertext_fails_without_replacement(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "synthetic-key.dpapi"
    monkeypatch.setattr(local_secrets.secrets, "token_bytes", lambda size: SYNTHETIC_KEY)
    load_local_key(path, create=True)
    tampered = bytearray(path.read_bytes())
    tampered[-1] ^= 0x80
    path.write_bytes(tampered)

    with pytest.raises(SecretError, match="^dpapi_operation_failed$"):
        load_local_key(path, create=True)
    assert path.read_bytes() == bytes(tampered)


@WINDOWS_ONLY
def test_valid_encryption_with_wrong_key_length_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-key.dpapi"
    wrong_length = PREFIX + local_secrets._dpapi(b"synthetic-short", decrypt=False)
    path.write_bytes(wrong_length)
    with pytest.raises(SecretError, match="^invalid_secret_length$"):
        load_local_key(path, create=True)
    assert path.read_bytes() == wrong_length


@pytest.mark.parametrize("parent_link", [False, True])
def test_symlink_path_or_parent_is_blocked_when_supported(tmp_path: Path, parent_link: bool) -> None:
    original = tmp_path / "original"
    original.mkdir()
    target = original / "synthetic-key.dpapi"
    original_bytes = b"SYNTHETIC-NOT-A-SECRET"
    target.write_bytes(original_bytes)
    link = tmp_path / "linked"
    try:
        link.symlink_to(original if parent_link else target, target_is_directory=parent_link)
    except (OSError, NotImplementedError):
        pytest.skip("This environment does not allow creating symbolic links")

    path = link / target.name if parent_link else link
    with pytest.raises(SecretError, match="^secret_path_link_forbidden$"):
        load_local_key(path, create=True)
    assert target.read_bytes() == original_bytes
