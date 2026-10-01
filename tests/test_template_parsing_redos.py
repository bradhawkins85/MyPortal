from __future__ import annotations

import signal
from contextlib import contextmanager

import pytest

from app.services import conditional_expressions, value_templates

_HAS_SIGALRM_TIMEOUT = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")

@contextmanager
def _time_limit(seconds: float):
    def _raise_timeout(signum, frame):
        raise TimeoutError(f"parser exceeded {seconds} second limit")

    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.mark.skipif(
    not _HAS_SIGALRM_TIMEOUT,
    reason="SIGALRM-based timeout is only available on Unix-like platforms",
)
@pytest.mark.parametrize("size", [1000, 5000, 20000])
def test_unterminated_whitespace_heavy_tokens_complete_within_time_limit(size):
    conditional_input = "{{if" + (" " * size)
    variable_input = "${" + (" " * size)

    with _time_limit(0.5):
        assert (
            conditional_expressions.process_conditionals(conditional_input, {})
            == conditional_input
        )
        assert value_templates.render_string(variable_input, {}) == variable_input


@pytest.mark.skipif(
    not _HAS_SIGALRM_TIMEOUT,
    reason="SIGALRM-based timeout is only available on Unix-like platforms",
)
@pytest.mark.parametrize("size", [250, 1000, 4000])
def test_repeated_opening_delimiters_complete_within_time_limit(size):
    conditional_input = ("{{" * size) + "if" + (" " * size)
    variable_input = ("${" * size) + "vars.now.local.date"

    with _time_limit(0.5):
        assert (
            conditional_expressions.process_conditionals(conditional_input, {})
            == conditional_input
        )
        assert value_templates.render_string(variable_input, {}) == variable_input


@pytest.mark.skipif(
    not _HAS_SIGALRM_TIMEOUT,
    reason="SIGALRM-based timeout is only available on Unix-like platforms",
)
@pytest.mark.parametrize("size", [1000, 2000, 4000])
@pytest.mark.parametrize("suffix", ["", ">"])
def test_adversarial_comparisons_complete_within_time_limit(size, suffix):
    """Scanner-reported ``a/space`` and ``a>/space`` cases remain linear."""
    expression = "a" + suffix + (" " * size)

    with _time_limit(0.5):
        assert conditional_expressions.split_comparison(expression) is None
        assert conditional_expressions._evaluate_condition(expression, {}) is True
        value_templates._collect_tokens(
            f"{{{{if {expression} then 'yes' else 'no'}}}}"
        )


def test_comparison_scanner_preserves_operators_and_trims_operands():
    for operator in (">=", "<=", ">", "<", "==", "!="):
        assert conditional_expressions.split_comparison(
            f"  left  {operator}  'right value'  "
        ) == ("left", operator, "'right value'")


@pytest.mark.parametrize("expression", ["> value", "value >", "value ! other"])
def test_comparison_scanner_rejects_missing_or_malformed_parts(expression):
    assert conditional_expressions.split_comparison(expression) is None


def test_comparison_scanner_rejects_oversized_input_before_scanning():
    expression = "a" * (conditional_expressions._MAX_CONDITIONAL_LENGTH + 1)

    with pytest.raises(ValueError, match="exceeds 4096 characters"):
        conditional_expressions.split_comparison(expression)
    with pytest.raises(ValueError, match="exceeds 4096 characters"):
        conditional_expressions._evaluate_condition(expression, {})
