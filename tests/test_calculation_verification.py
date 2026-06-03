from src.swarm.verification import (
    value_survives_in_text,
    verify_calculation_expression,
)


def test_verify_calculation_expression_handles_currency_and_percent():
    result = verify_calculation_expression("1000000 * 2%", "$20,000")

    assert result["verified"] is True
    assert result["computed"] == 20000


def test_verify_calculation_expression_rejects_wrong_result():
    result = verify_calculation_expression("1000000 * 2%", "$25,000")

    assert result["verified"] is False


def test_value_survives_in_text_finds_contextual_value():
    assert value_survives_in_text(
        "$20,000",
        "The annual fee equals $20,000 under the LPA.",
        ["annual", "fee"],
    )
