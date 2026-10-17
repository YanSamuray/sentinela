"""Minimize API responses before persistence; precise coordinates remain private.

This module is deliberately pure: it never reads files, environment variables or
network responses. The caller supplies the secret and an explicitly reviewed
category catalog. HMAC identifiers are pseudonyms, not anonymous identifiers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

POLICY_VERSION = "1.0.0"
_TOKEN = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?\Z"
)
_BOOL_FIELDS = (
    "houveDisparoArmaFogo", "houveUtilizacaoForcaFisica",
    "policiaCivilAcionada", "policiaCivilEsteveLocal",
)
_SCALARS = ("nivelRisco", "classificacaoSituacao", "origemRegistro")
_DESCRIPTORS = ("grupo", "codigoEncerramento", "fatoComunicado")
_LIST_DESCRIPTORS = ("unidades", "areasDespacho", "centraisAtendimento", "fatoConstatado")
_LABEL_FIELDS = ("id", "descricao", "sigla", "nome")
_RISK_LABELS = (
    "BAIXO", "MEDIO", "MÉDIO", "ALTO", "ALTISSIMO", "ALTÍSSIMO",
    "CRITICO", "CRÍTICO", "GRAVISSIMO", "GRAVÍSSIMO",
)
_QUESTIONS = (
    "Há pessoas feridas em risco de morte?",
    "Autor do fato está armado?",
)
_DEFAULT_CATALOG = {
    "nivelRisco": frozenset(v for s in _RISK_LABELS for v in (s, s.title(), s.lower())),
    "perguntasNivelRisco[].pergunta": frozenset(
        v for s in _QUESTIONS for v in (s, s.upper(), s.lower())
    ),
}
_CATALOG_PATHS = {*_SCALARS, "localizacao.municipio", "perguntasNivelRisco[].pergunta"}
for _root in (*_DESCRIPTORS, *(f"{s}[]" for s in _LIST_DESCRIPTORS)):
    _CATALOG_PATHS.update(f"{_root}.{field}" for field in _LABEL_FIELDS)
    _CATALOG_PATHS.add(f"{_root}.grupo")
    _CATALOG_PATHS.update(f"{_root}.grupo.{field}" for field in _LABEL_FIELDS)


class PrivacyError(ValueError):
    """A fixed error code only: no response value, URL or exception text."""

    _CODES = frozenset({
        "invalid_key", "invalid_catalog", "invalid_payload", "invalid_record",
        "invalid_protocol", "invalid_datetime", "invalid_coordinates",
        "invalid_boolean", "invalid_category", "unknown_category",
        "invalid_structure", "invalid_envelope", "invalid_signature",
    })

    def __init__(self, code: str):
        self.code = code if code in self._CODES else "invalid_payload"
        super().__init__(self.code)


def load_hmac_key(hex_value: str) -> bytes:
    """Decode at least 32 secret bytes, supplied as uninterrupted hexadecimal."""
    if (not isinstance(hex_value, str) or len(hex_value) < 64
            or len(hex_value) % 2 or re.fullmatch(r"[0-9a-fA-F]+", hex_value) is None):
        raise PrivacyError("invalid_key")
    return bytes.fromhex(hex_value)


def _check_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) < 32:
        raise PrivacyError("invalid_key")


def _catalog(category_catalog: Mapping[str, Collection[str]] | None) -> dict[str, frozenset[str]]:
    result = dict(_DEFAULT_CATALOG)
    if category_catalog is None:
        return result
    if not isinstance(category_catalog, Mapping):
        raise PrivacyError("invalid_catalog")
    for path, values in category_catalog.items():
        if (not isinstance(path, str) or path not in _CATALOG_PATHS
                or not isinstance(values, Collection) or isinstance(values, (str, bytes, Mapping))):
            raise PrivacyError("invalid_catalog")
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
            raise PrivacyError("invalid_catalog")
        # An explicit catalog replaces that path's defaults, rather than widening it.
        result[path] = frozenset(values)
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PrivacyError("invalid_payload") from None


def _mac(key: bytes, domain: bytes, value: bytes) -> str:
    return hmac.new(key, domain + b"\x00" + value, hashlib.sha256).hexdigest()


def _metadata(key: bytes, catalog: dict[str, frozenset[str]]) -> dict[str, str]:
    return {
        "policy_version": POLICY_VERSION,
        "key_id": _mac(key, b"totens-key-id-v1", b"")[:24],
        "catalog_sha256": hashlib.sha256(_canonical({k: sorted(v) for k, v in catalog.items()})).hexdigest(),
        "coordinate_policy": "precise_private",
    }


def _label(value: Any, path: str, catalog: dict[str, frozenset[str]]) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PrivacyError("invalid_category")
    try:
        label = str(value)
    except ValueError:
        raise PrivacyError("invalid_category") from None
    if label not in catalog.get(path, frozenset()):
        raise PrivacyError("unknown_category")
    return label


def _datetime(value: Any) -> str:
    if not isinstance(value, str) or _ISO.fullmatch(value) is None:
        raise PrivacyError("invalid_datetime")
    if re.search(r"[+-]\d{2}:\d{2}$", value):
        # fromisoformat normalizes overflowing offset minutes; ISO input must not.
        if int(value[-5:-3]) > 23 or int(value[-2:]) > 59:
            raise PrivacyError("invalid_datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PrivacyError("invalid_datetime") from None
    # Naive timestamps remain naive: no undocumented timezone assumption.
    return parsed.isoformat()


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        if value in ("sim", "Sim", "SIM", "true", "True", "TRUE", "1", "s", "S"):
            return True
        if value in ("não", "Não", "NÃO", "nao", "Nao", "NAO", "false", "False", "FALSE", "0", "n", "N"):
            return False
    raise PrivacyError("invalid_boolean")


def _protocol(value: Any, key: bytes, *, already_sanitized: bool) -> str:
    if already_sanitized:
        if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
            raise PrivacyError("invalid_protocol")
        return value
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PrivacyError("invalid_protocol")
    try:
        text = str(value).strip()
    except ValueError:
        raise PrivacyError("invalid_protocol") from None
    if not text or len(text) > 256 or text.startswith("hmac-sha256:"):
        raise PrivacyError("invalid_protocol")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise PrivacyError("invalid_protocol") from None
    return "hmac-sha256:" + _mac(key, b"totens-protocol-v1", encoded)


def _descriptor(value: Any, path: str, catalog: dict[str, frozenset[str]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PrivacyError("invalid_structure")
    result: dict[str, Any] = {}
    for field in _LABEL_FIELDS:
        if field in value and value[field] is not None:
            result[field] = _label(value[field], f"{path}.{field}", catalog)
    if value.get("grupo") is not None:
        group = value["grupo"]
        if isinstance(group, dict):
            # Only one explicitly allowed subgroup level; unknown nested keys vanish.
            result["grupo"] = {
                field: _label(group[field], f"{path}.grupo.{field}", catalog)
                for field in _LABEL_FIELDS if field in group and group[field] is not None
            }
        else:
            result["grupo"] = _label(group, f"{path}.grupo", catalog)
    return result


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise PrivacyError("invalid_structure")
    return value


def _location(value: Any, catalog: dict[str, frozenset[str]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PrivacyError("invalid_structure")
    result: dict[str, Any] = {}
    for field, maximum in (("latitude", 90), ("longitude", 180)):
        coordinate = value.get(field)
        if coordinate is None:
            continue
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float, str)):
            raise PrivacyError("invalid_coordinates")
        try:
            coordinate = float(coordinate)
        except (ValueError, OverflowError):
            raise PrivacyError("invalid_coordinates") from None
        if not math.isfinite(coordinate) or abs(coordinate) > maximum:
            raise PrivacyError("invalid_coordinates")
        result[field] = coordinate
    if value.get("municipio") is not None:
        result["municipio"] = _label(value["municipio"], "localizacao.municipio", catalog)
    return result


def _record(record: Any, key: bytes, catalog: dict[str, frozenset[str]], *, already_sanitized: bool) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise PrivacyError("invalid_record")
    result: dict[str, Any] = {
        "protocolo": _protocol(record.get("protocolo"), key, already_sanitized=already_sanitized),
        "dataCriacao": _datetime(record.get("dataCriacao")),
    }
    for field in _SCALARS:
        if record.get(field) is not None:
            result[field] = _label(record[field], field, catalog)
    for field in _DESCRIPTORS:
        if record.get(field) is not None:
            result[field] = _descriptor(record[field], field, catalog)
    for field in _LIST_DESCRIPTORS:
        if record.get(field) is not None:
            result[field] = [_descriptor(item, f"{field}[]", catalog) for item in _list(record[field])]
    if record.get("localizacao") is not None:
        result["localizacao"] = _location(record["localizacao"], catalog)
    if record.get("dadosEncerramento") is not None:
        result["dadosEncerramento"] = []
        for item in _list(record["dadosEncerramento"]):
            if not isinstance(item, dict):
                raise PrivacyError("invalid_structure")
            clean: dict[str, Any] = {}
            if item.get("dataHoraFato") is not None:
                clean["dataHoraFato"] = _datetime(item["dataHoraFato"])
            for field in _BOOL_FIELDS:
                if item.get(field) is not None:
                    clean[field] = _boolean(item[field])
            result["dadosEncerramento"].append(clean)
    if record.get("perguntasNivelRisco") is not None:
        result["perguntasNivelRisco"] = []
        for item in _list(record["perguntasNivelRisco"]):
            if not isinstance(item, dict):
                raise PrivacyError("invalid_structure")
            result["perguntasNivelRisco"].append({
                "pergunta": _label(item.get("pergunta"), "perguntasNivelRisco[].pergunta", catalog),
                "resposta": _boolean(item.get("resposta")),
            })
    return result


def _sanitize(payload: Any, key: bytes, catalog: dict[str, frozenset[str]], *, already_sanitized: bool) -> dict[str, Any]:
    if not isinstance(payload, dict) or "params" not in payload or not isinstance(payload["params"], list):
        raise PrivacyError("invalid_payload")
    output = {
        "params": [_record(record, key, catalog, already_sanitized=already_sanitized) for record in payload["params"]],
        "_privacy": _metadata(key, catalog),
    }
    output["_privacy"]["payload_hmac"] = _mac(key, b"totens-payload-v1", _canonical(output))
    return output


def sanitize_payload(payload: Any, key: bytes, *, category_catalog: Mapping[str, Collection[str]] | None = None) -> dict[str, Any]:
    """Return a new minimized, pseudonymized and authenticated JSON document.

    Unknown fields are removed. Unknown category values and incompatible types
    fail closed. Catalogs must come from a trusted reviewed source, never from
    the API response. Input data is neither mutated nor included in errors.
    """
    _check_key(key)
    return _sanitize(payload, key, _catalog(category_catalog), already_sanitized=False)


def validate_sanitized_payload(payload: Any, key: bytes, *, category_catalog: Mapping[str, Collection[str]] | None = None) -> dict[str, Any]:
    """Validate existing output without hashing its identifiers a second time."""
    _check_key(key)
    catalog = _catalog(category_catalog)
    if not isinstance(payload, dict) or set(payload) != {"params", "_privacy"}:
        raise PrivacyError("invalid_envelope")
    metadata = payload.get("_privacy")
    expected = _metadata(key, catalog)
    if not isinstance(metadata, dict) or set(metadata) != {*expected, "payload_hmac"}:
        raise PrivacyError("invalid_envelope")
    if any(metadata[field] != value for field, value in expected.items()):
        raise PrivacyError("invalid_envelope")
    signature = metadata["payload_hmac"]
    if not isinstance(signature, str) or re.fullmatch(r"[0-9a-f]{64}", signature) is None:
        raise PrivacyError("invalid_signature")
    expected_signature = _mac(key, b"totens-payload-v1", _canonical({"params": payload["params"], "_privacy": expected}))
    if not hmac.compare_digest(signature, expected_signature):
        raise PrivacyError("invalid_signature")
    canonical = _sanitize(payload, key, catalog, already_sanitized=True)
    # Strict JSON comparison rejects extra keys, alternative types and encodings.
    if _canonical(payload) != _canonical(canonical):
        raise PrivacyError("invalid_envelope")
    return canonical
