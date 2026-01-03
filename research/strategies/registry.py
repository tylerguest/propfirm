from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import pandas as pd

from research.strategies.bollinger_breakout import BollingerBreakoutConfig, generate_target_position as bb_target
from research.strategies.donchian_breakout import DonchianBreakoutConfig, generate_target_position as donchian_target
from research.strategies.do_nothing import generate_target_position as do_nothing_target
from research.strategies.ema_crossover import EmaCrossoverConfig, generate_target_position as ema_target
from research.strategies.momentum_time_based import TimeMomentumConfig, generate_target_position as momentum_target
from research.strategies.rsi_mean_reversion import RsiMeanReversionConfig, generate_target_position as rsi_target
from research.strategies.sma_crossover import SmaCrossoverConfig, generate_target_position as sma_target


class StrategyFn(Protocol):
    def __call__(self, df: pd.DataFrame, config: object) -> pd.Series: ...


@dataclass(frozen=True)
class StrategySpec:
    name: str
    config_type: type
    generate_target_position: StrategyFn


_REGISTRY: dict[str, StrategySpec] = {
    "do_nothing": StrategySpec(
        name="do_nothing",
        config_type=type(None),
        generate_target_position=lambda df, _: do_nothing_target(df),
    ),
    "sma_crossover": StrategySpec(
        name="sma_crossover",
        config_type=SmaCrossoverConfig,
        generate_target_position=sma_target,
    ),
    "ema_crossover": StrategySpec(
        name="ema_crossover",
        config_type=EmaCrossoverConfig,
        generate_target_position=ema_target,
    ),
    "rsi_mean_reversion": StrategySpec(
        name="rsi_mean_reversion",
        config_type=RsiMeanReversionConfig,
        generate_target_position=rsi_target,
    ),
    "donchian_breakout": StrategySpec(
        name="donchian_breakout",
        config_type=DonchianBreakoutConfig,
        generate_target_position=donchian_target,
    ),
    "bollinger_breakout": StrategySpec(
        name="bollinger_breakout",
        config_type=BollingerBreakoutConfig,
        generate_target_position=bb_target,
    ),
    "time_momentum": StrategySpec(
        name="time_momentum",
        config_type=TimeMomentumConfig,
        generate_target_position=momentum_target,
    ),
}


def list_strategies() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_strategy(name: str) -> StrategySpec:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {name}. Available: {', '.join(list_strategies())}")
    return _REGISTRY[name]
