"""Versioned research score; hard gates remain separate from weights."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreConfig:
    version: str = "research-weights-1"
    trend: int = 20
    momentum: int = 25
    participation: int = 20
    structure: int = 20
    execution: int = 15

    def __post_init__(self) -> None:
        weights = (self.trend, self.momentum, self.participation, self.structure, self.execution)
        if any(type(weight) is not int or weight < 0 for weight in weights) or sum(weights) != 100:
            raise ValueError("nonnegative integer weights must sum to 100")
        if not self.version:
            raise ValueError("weight version required")


DEFAULT = ScoreConfig()


def score(factors: dict[str, float], config: ScoreConfig = DEFAULT) -> dict:
    explanation = {}
    for name in ("trend", "momentum", "participation", "structure", "execution"):
        value = factors[name]
        if not 0 <= value <= 1:
            raise ValueError("factor must be within zero and one")
        explanation[name] = round(getattr(config, name) * value, 4)
    total = round(sum(explanation.values()), 4)
    priority = "HIGH" if total >= 85 else "STANDARD" if total >= 75 else "WATCH" if total >= 60 else "REJECT"
    return {"score": total, "priority": priority, "score_factors": explanation,
            "score_version": config.version}
