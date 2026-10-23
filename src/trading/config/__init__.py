"""
Configuration Loader

Loads configuration from YAML file and environment variables.
Uses Pydantic Settings for validation.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..canonical import (
    Instrument,
    Timeframe,
    InstrumentSpec,
    RiskConfig,
    TechnicalConfig,
    AIConfig,
    BlackoutConfig,
    QualityConfig,
    PersistenceConfig,
    AppConfig,
    NewsImpact,
)


class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Required
    axle_api_key: str = Field(..., alias="AXLE_API_KEY")
    openrouter_api_key: str = Field(..., alias="OPENROUTER_API_KEY")
    
    # Optional with defaults
    biquote_ws_url: str = "wss://biquote.io/hubs/tick"
    db_path: str = "./data/trading.db"
    journal_path: str = "./data/journal.log"
    log_level: str = "INFO"
    ai_model_provider: str = "openrouter"
    ai_model_id: str = "inclusionAI/Ling-3.0-flash-Fin"


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get global settings instance (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """
    Load complete application configuration from YAML file.
    Merges with environment variables.
    """
    settings = get_settings()
    path = Path(config_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    if not data:
        raise ValueError("Empty config file")
    
    # Parse instruments
    instrument_strs = data.get("instruments", ["XAUUSD", "EURUSD", "GBPUSD", "WTI"])
    instruments = [Instrument(s) for s in instrument_strs]
    
    # Parse instrument specs
    precision_map = data.get("instrument_precision", {
        "XAUUSD": 2, "EURUSD": 5, "GBPUSD": 5, "WTI": 2
    })
    point_value_map = data.get("instrument_point_value", {
        "XAUUSD": "0.01", "EURUSD": "0.00001", "GBPUSD": "0.00001", "WTI": "0.01"
    })
    
    instrument_specs = {}
    for inst in instruments:
        inst_str = inst.value
        instrument_specs[inst] = InstrumentSpec(
            symbol=inst,
            provider_symbol=inst_str,
            description=f"{inst_str} CFD",
            precision=precision_map.get(inst_str, 5),
            point_value=Decimal(point_value_map.get(inst_str, "0.00001")),
            contract_size=Decimal("1.0"),
            tick_value=Decimal("1.0"),
            trading_hours={"open": "22:00", "close": "21:00"},
            timezone="UTC",
        )
    
    # Risk config
    risk_data = data.get("risk", {})
    max_spread = {}
    for k, v in risk_data.get("max_spread_pct", {}).items():
        try:
            max_spread[Instrument(k)] = Decimal(str(v))
        except ValueError:
            pass
    
    risk_config = RiskConfig(
        default_risk_pct=Decimal(str(risk_data.get("default_risk_pct", "0.01"))),
        max_trade_risk_pct=Decimal(str(risk_data.get("max_trade_risk_pct", "0.02"))),
        daily_loss_limit_pct=Decimal(str(risk_data.get("daily_loss_limit_pct", "0.03"))),
        max_trades_per_day=risk_data.get("max_trades_per_day", 3),
        min_risk_reward=Decimal(str(risk_data.get("min_risk_reward", "2.0"))),
        max_spread_pct=max_spread,
        entry_buffer_points=risk_data.get("entry_buffer_points", 30),
        sl_buffer_points=risk_data.get("sl_buffer_points", 10),
    )
    
    # Technical config
    tech_data = data.get("technical", {})
    technical_config = TechnicalConfig(
        h1_timeframe=Timeframe(tech_data.get("h1_timeframe", "1h")),
        m15_timeframe=Timeframe(tech_data.get("m15_timeframe", "15m")),
        rsi_period=tech_data.get("rsi_period", 14),
        rsi_overbought=tech_data.get("rsi_overbought", 70),
        rsi_oversold=tech_data.get("rsi_oversold", 30),
        swing_lookback=tech_data.get("swing_lookback", 5),
        equal_level_tolerance_atr_mult=Decimal(str(tech_data.get("equal_level_tolerance_atr_mult", "0.5"))),
    )
    
    # AI config
    ai_data = data.get("ai", {})
    ai_config = AIConfig(
        provider=ai_data.get("provider", "openrouter"),
        model=ai_data.get("model", "inclusionAI/Ling-3.0-flash-Fin"),
        temperature=ai_data.get("temperature", 1.0),
        top_p=ai_data.get("top_p", 0.95),
        top_k=ai_data.get("top_k", 20),
        max_tokens=ai_data.get("max_tokens", 8192),
        timeout_seconds=ai_data.get("timeout_seconds", 30),
        max_retries=ai_data.get("max_retries", 2),
    )
    
    # Blackout config
    bl_data = data.get("blackout", {})
    inst_events = {}
    for k, v in bl_data.get("instrument_events", {}).items():
        try:
            inst_events[Instrument(k)] = v
        except ValueError:
            pass
    
    blackout_config = BlackoutConfig(
        minutes_before=bl_data.get("minutes_before", 30),
        minutes_after=bl_data.get("minutes_after", 30),
        high_impact_only=bl_data.get("high_impact_only", True),
        currencies=bl_data.get("currencies", ["USD", "EUR", "GBP"]),
        instrument_events=inst_events,
    )
    
    # Quality config
    qual_data = data.get("quality", {})
    quality_config = QualityConfig(
        max_quote_age_seconds=qual_data.get("max_quote_age_seconds", 300),
        max_ohlc_gap_minutes=qual_data.get("max_ohlc_gap_minutes", 120),
        min_tick_volume=qual_data.get("min_tick_volume", 1),
        stale_data_action=qual_data.get("stale_data_action", "skip"),
    )
    
    # Persistence config
    pers_data = data.get("persistence", {})
    persistence_config = PersistenceConfig(
        db_path=pers_data.get("db_path", "./data/trading.db"),
        journal_path=pers_data.get("journal_path", "./data/journal.log"),
        journal_max_size_mb=pers_data.get("journal_max_size_mb", 100),
        journal_rotation=pers_data.get("journal_rotation", "daily"),
        backup_interval_hours=pers_data.get("backup_interval_hours", 6),
        backup_retention_days=pers_data.get("backup_retention_days", 30),
        wal_mode=pers_data.get("wal_mode", True),
        sync_on_write=pers_data.get("sync_on_write", True),
    )
    
    return AppConfig(
        instruments=instruments,
        instrument_specs=instrument_specs,
        risk=risk_config,
        technical=technical_config,
        ai=ai_config,
        blackout=blackout_config,
        quality=quality_config,
        persistence=persistence_config,
    )