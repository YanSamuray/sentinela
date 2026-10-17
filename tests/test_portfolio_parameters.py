import pytest

from totens.portfolio.parameters import case_parameters


def test_parameters_are_explicit_and_override_defaults():
    parameters = case_parameters({"portfolio": {"forecast": {"horizon": 14}}})
    assert parameters["forecast"]["horizon"] == 14
    assert case_parameters({})["forecast"]["horizon"] == 30


@pytest.mark.parametrize("settings", [
    {"portfolio": {"forecast": {"horizon": 0}}},
    {"portfolio": {"forecast": {"typo": 30}}},
    {"portfolio": {"spatial": {"eps": 17}}},
])
def test_invalid_or_ignored_configuration_is_rejected(settings):
    with pytest.raises(ValueError):
        case_parameters(settings)
