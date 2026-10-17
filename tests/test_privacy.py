from __future__ import annotations

import copy
import hashlib
import hmac
import json

import pytest

from totens.privacy import (
    POLICY_VERSION,
    PrivacyError,
    load_hmac_key,
    sanitize_payload,
    validate_sanitized_payload,
)

# Public test fixtures, deliberately unrelated to any operational key or record.
KEY = bytes(range(32))
CANARY = "SYNTHETIC_PRIVATE_CANARY_837"
CATALOG = {
    "localizacao.municipio": ["MUNICIPIO SINTETICO"],
    "grupo.descricao": ["GRUPO SINTETICO"],
    "unidades[].id": ["7", "8"],
    "unidades[].descricao": ["UNIDADE SINTETICA"],
    "fatoConstatado[].descricao": ["CATEGORIA SINTETICA"],
}


def sample() -> dict:
    return {"params": [{"protocolo": 12345, "dataCriacao": "2024-01-02T03:04:05Z"}]}


def test_minimizes_every_list_item_and_nested_field_without_mutating_input():
    payload = sample()
    payload.update({"message": CANARY, "token": CANARY, "extra": {"cpf": CANARY}})
    record = payload["params"][0]
    record.update({
        "descricao": CANARY,
        "cpf": CANARY,
        "envolvidos": [{"nome": CANARY}],
        "documentos": [{"numero": CANARY}],
        "arquivos": [{"url": CANARY}],
        "localizacao": {
            "latitude": "-8.2", "longitude": -63.1, "municipio": "MUNICIPIO SINTETICO",
            "logradouro": CANARY, "numero": CANARY, "complemento": {"nome": CANARY},
        },
        "grupo": {"descricao": "GRUPO SINTETICO", "pessoa": CANARY},
        "nivelRisco": "ALTO",
        "unidades": [
            {"id": 7, "descricao": "UNIDADE SINTETICA", "cpf": CANARY},
            {"id": 8, "descricao": "UNIDADE SINTETICA", "nested": {"nome": CANARY}},
        ],
        "fatoConstatado": [{"descricao": "CATEGORIA SINTETICA", "relato": CANARY}],
        "dadosEncerramento": [
            {"relatoPolicial": CANARY, "motivoAbordagem": CANARY,
             "houveDisparoArmaFogo": True, "dataHoraFato": "2024-01-02T03:00:00-04:00"},
            {"relatoPolicial": CANARY, "policiaCivilAcionada": "Não"},
        ],
        "perguntasNivelRisco": [
            {"pergunta": "Autor do fato está armado?", "resposta": "Sim", "justificativa": CANARY},
        ],
    })
    before = copy.deepcopy(payload)
    output = sanitize_payload(payload, KEY, category_catalog=CATALOG)

    assert payload == before
    assert CANARY not in json.dumps(output, ensure_ascii=False)
    assert set(output) == {"params", "_privacy"}
    clean = output["params"][0]
    assert clean["localizacao"] == {"latitude": -8.2, "longitude": -63.1, "municipio": "MUNICIPIO SINTETICO"}
    assert [unit["id"] for unit in clean["unidades"]] == ["7", "8"]
    assert clean["dadosEncerramento"][0]["houveDisparoArmaFogo"] is True
    assert clean["dadosEncerramento"][1] == {"policiaCivilAcionada": False}
    assert clean["dataCriacao"] == "2024-01-02T03:04:05+00:00"
    assert clean["perguntasNivelRisco"][0]["resposta"] is True
    assert output["_privacy"]["coordinate_policy"] == "precise_private"
    assert validate_sanitized_payload(output, KEY, category_catalog=CATALOG) == output


def test_protocol_is_stable_across_batches_and_type_normalization():
    one = sanitize_payload(sample(), KEY)
    second = sample()
    second["params"][0]["protocolo"] = "12345"
    second["params"].append({"protocolo": 12346, "dataCriacao": "2024-01-03T03:04:05Z"})
    two = sanitize_payload(second, KEY)
    assert one["params"][0]["protocolo"] == two["params"][0]["protocolo"]
    assert two["params"][0]["protocolo"] != two["params"][1]["protocolo"]
    changed_key = sanitize_payload(sample(), b"x" * 32)
    assert changed_key["params"][0]["protocolo"] != one["params"][0]["protocolo"]
    assert changed_key["_privacy"]["key_id"] != one["_privacy"]["key_id"]
    assert "12345" not in one["params"][0]["protocolo"]


def test_hmac_derivation_is_domain_separated_and_deterministic():
    output = sanitize_payload(sample(), KEY)
    token = hmac.new(KEY, b"totens-protocol-v1\x0012345", hashlib.sha256).hexdigest()
    assert output["params"][0]["protocolo"] == "hmac-sha256:" + token
    assert output["_privacy"]["policy_version"] == POLICY_VERSION


@pytest.mark.parametrize("value", [None, "", "ab" * 31, "ab " * 32, "z" * 64, "a" * 65, b"a" * 64])
def test_key_decoder_rejects_invalid_values_without_echo(value):
    with pytest.raises(PrivacyError, match="^invalid_key$"):
        load_hmac_key(value)


def test_key_decoder_accepts_longer_hex_keys_and_uppercase():
    assert load_hmac_key(KEY.hex().upper()) == KEY
    assert load_hmac_key("ab" * 48) == b"\xab" * 48


@pytest.mark.parametrize("value", [None, True, [], {}, "", "\ud800", "hmac-sha256:" + "a" * 64])
def test_rejects_invalid_or_already_hashed_protocol(value):
    payload = sample()
    payload["params"][0]["protocolo"] = value
    with pytest.raises(PrivacyError, match="^invalid_protocol$"):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("value", ["2024-02-30T10:00:00Z", "2024-01-01", "2024-01-01 10:00:00", "2024-01-01T10:00:00+00:99", 1700000000, CANARY])
def test_rejects_invalid_datetime_and_non_iso_input(value):
    payload = sample()
    payload["params"][0]["dataCriacao"] = value
    with pytest.raises(PrivacyError, match="^invalid_datetime$"):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("field,value", [
    ("latitude", 91), ("latitude", float("nan")), ("longitude", -181),
    ("longitude", float("inf")), ("latitude", True), ("longitude", CANARY),
])
def test_rejects_invalid_coordinates_without_echo(field, value):
    payload = sample()
    payload["params"][0]["localizacao"] = {field: value}
    with pytest.raises(PrivacyError, match="^invalid_coordinates$"):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("field,value", [
    ("grupo", {"descricao": CANARY}), ("nivelRisco", CANARY),
    ("localizacao", {"municipio": CANARY}),
    ("perguntasNivelRisco", [{"pergunta": CANARY, "resposta": True}]),
])
def test_unknown_categories_fail_closed_in_every_approved_path(field, value):
    payload = sample()
    payload["params"][0][field] = value
    with pytest.raises(PrivacyError, match="^unknown_category$") as caught:
        sanitize_payload(payload, KEY, category_catalog=CATALOG)
    assert CANARY not in str(caught.value)


def test_approved_value_cannot_be_reused_at_another_path():
    payload = sample()
    payload["params"][0]["grupo"] = {"descricao": "UNIDADE SINTETICA"}
    with pytest.raises(PrivacyError, match="^unknown_category$"):
        sanitize_payload(payload, KEY, category_catalog=CATALOG)


@pytest.mark.parametrize("value", [CANARY, {"text": CANARY}, [], None, 2, 0.0])
def test_boolean_does_not_accept_free_text_or_arbitrary_types(value):
    payload = sample()
    payload["params"][0]["perguntasNivelRisco"] = [{"pergunta": "Autor do fato está armado?", "resposta": value}]
    with pytest.raises(PrivacyError, match="^invalid_boolean$"):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("payload", [{}, {"params": None}, {"params": {}}, {"params": CANARY}, {"params": [CANARY]}])
def test_requires_response_and_record_shapes(payload):
    with pytest.raises(PrivacyError):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("field,value", [("unidades", '[{"nome":"private"}]'), ("dadosEncerramento", {}), ("fatoConstatado", [None])])
def test_nested_json_strings_and_wrong_collection_shapes_are_rejected(field, value):
    payload = sample()
    payload["params"][0][field] = value
    with pytest.raises(PrivacyError, match="^invalid_structure$"):
        sanitize_payload(payload, KEY)


@pytest.mark.parametrize("catalog", [[], {"unknown.path": ["x"]}, {"grupo.descricao": "text"}, {"grupo.descricao": [123]}])
def test_invalid_catalog_configuration_is_rejected(catalog):
    with pytest.raises(PrivacyError, match="^invalid_catalog$"):
        sanitize_payload(sample(), KEY, category_catalog=catalog)


def test_catalog_fingerprint_is_order_independent_and_versions_exact_values():
    a = {"grupo.descricao": ["A", "B"], "localizacao.municipio": ["C"]}
    b = {"localizacao.municipio": ["C"], "grupo.descricao": ["B", "A"]}
    first = sanitize_payload(sample(), KEY, category_catalog=a)
    assert sanitize_payload(sample(), KEY, category_catalog=b) == first
    with pytest.raises(PrivacyError, match="^invalid_envelope$"):
        validate_sanitized_payload(first, KEY, category_catalog={"grupo.descricao": ["A"]})


def test_existing_signed_output_validates_without_rehashing_identifiers():
    output = sanitize_payload(sample(), KEY)
    restored = json.loads(json.dumps(output))
    assert validate_sanitized_payload(restored, KEY) == output
    with pytest.raises(PrivacyError, match="^invalid_protocol$"):
        sanitize_payload(restored, KEY)
    with pytest.raises(PrivacyError, match="^invalid_envelope$"):
        validate_sanitized_payload(restored, b"y" * 32)


@pytest.mark.parametrize("where", ["record", "root", "metadata", "date", "token", "signature"])
def test_signed_output_rejects_tampering_or_extra_keys(where):
    output = sanitize_payload(sample(), KEY)
    if where == "record":
        output["params"][0]["cpf"] = CANARY
    elif where == "root":
        output["message"] = CANARY
    elif where == "metadata":
        output["_privacy"]["unexpected"] = CANARY
    elif where == "date":
        output["params"][0]["dataCriacao"] = "2024-01-03T03:04:05+00:00"
    elif where == "token":
        output["params"][0]["protocolo"] = "hmac-sha256:" + "a" * 64
    else:
        output["_privacy"]["payload_hmac"] = "0" * 64
    with pytest.raises(PrivacyError):
        validate_sanitized_payload(output, KEY)


def test_valid_signature_does_not_bypass_schema_allowlist():
    output = sanitize_payload(sample(), KEY)
    output["params"][0]["descricao"] = CANARY
    del output["_privacy"]["payload_hmac"]
    raw = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    output["_privacy"]["payload_hmac"] = hmac.new(KEY, b"totens-payload-v1\x00" + raw, hashlib.sha256).hexdigest()
    with pytest.raises(PrivacyError, match="^invalid_envelope$"):
        validate_sanitized_payload(output, KEY)


def test_empty_valid_response_remains_an_authenticated_empty_batch():
    output = sanitize_payload({"params": [], "message": CANARY}, KEY)
    assert output["params"] == []
    assert validate_sanitized_payload(output, KEY) == output


def test_error_class_does_not_accept_dynamic_messages():
    error = PrivacyError(CANARY)
    assert error.code == "invalid_payload"
    assert str(error) == "invalid_payload"
