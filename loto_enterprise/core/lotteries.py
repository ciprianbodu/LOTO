"""Loteriile cunoscute de aplicație: țara și numele jocului.

Singura sursă pentru eticheta afișată pe bilete și în rezultate, ca să se vadă
mereu pentru ce țară și ce joc sunt generate numerele. Cheia este eticheta
internă a jocului (`_game_label_for`: "6/49", "5/40", "joker").
"""

from __future__ import annotations

from dataclasses import dataclass

ROMANIA = "România"


@dataclass(frozen=True)
class Lottery:
    key: str
    country: str
    name: str

    @property
    def display(self) -> str:
        return f"{self.country} · {self.name}"


LOTTERIES: dict[str, Lottery] = {
    "6/49": Lottery("6/49", ROMANIA, "Loto 6/49"),
    "5/40": Lottery("5/40", ROMANIA, "Loto 5/40"),
    "joker": Lottery("joker", ROMANIA, "Joker"),
}


def display_name(game: str) -> str:
    """„România · Loto 6/49”; un joc necunoscut rămâne cu eticheta lui."""
    lottery = LOTTERIES.get(str(game))
    return lottery.display if lottery else str(game)
