"""Acordul numeralelor în textele afișate: „o variantă”, „5 variante”, „20 de variante”."""

from __future__ import annotations


def count(n: int, noun: str, singular: str | None = None) -> str:
    """„de” apare după numeralele terminate în 00 sau 20-99 (20, 101 fără, 120 cu);
    la 1 se folosește forma de singular, când e dată."""
    n = int(n)
    if n == 1 and singular:
        return f"1 {singular}"
    last = n % 100
    return f"{n} {noun}" if n < 20 or 1 <= last <= 19 else f"{n} de {noun}"
