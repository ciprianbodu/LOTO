"""Benchmark / comparator for time-series scoring methods (per joc, per urn)."""

# Percentages refer to trailing validation targets. Scorers see the earlier draws.
VALIDATION_PERCENTILES = (10, 30, 70, 100)
DEFAULT_REPLAY_PERCENT = 30


def validate_percentiles(values):
    """Reject ambiguous windows before counting folds or opening output files."""
    if not values or any(type(value) is not int or not 1 <= value <= 100 for value in values):
        raise ValueError("Ferestrele de validare trebuie să fie procente întregi între 1 și 100.")
    if len(set(values)) != len(values):
        raise ValueError("Fiecare procent de validare trebuie specificat o singură dată.")
