"""Deterministic extraction of trading concepts, formulas, rules and risk techniques.

Rule-based on purpose: it runs offline and its output is reproducible. An optional LLM
pass can enrich it later, but nothing depends on that. Every extracted rule is stored as
a *hypothesis* to be tested, never as a fact.
"""

from __future__ import annotations

import re
from collections import Counter

CONCEPTS: dict[str, list[str]] = {
    "vwap": [r"\bvwap\b", r"volume[- ]weighted average price"],
    "mean_reversion": [r"mean[- ]revers\w*", r"revert\w* to the mean"],
    "momentum": [r"\bmomentum\b", r"\brate of change\b", r"\broc\b"],
    "trend_following": [r"trend[- ]follow\w*", r"moving average cross\w*"],
    "breakout": [r"\bbreak[- ]?outs?\b", r"opening range"],
    "volume_profile": [r"volume profile", r"point of control", r"value area"],
    "order_flow": [r"order[- ]flow", r"\bdelta\b", r"footprint", r"order book imbalance"],
    "liquidity_sweep": [r"liquidity sweep\w*", r"stop[- ]hunt\w*", r"liquidity grab\w*"],
    "market_structure": [r"market structure", r"higher highs?", r"lower lows?",
                         r"break of structure"],
    "statistical_arbitrage": [r"stat(istical)? arb\w*", r"pairs? trad\w*", r"cointegrat\w*"],
    "volatility": [r"\bvolatility\b", r"\batr\b", r"average true range", r"bollinger"],
    "scalping": [r"\bscalp\w*"],
    "multi_timeframe": [r"multi[- ]time ?frame", r"higher time ?frame"],
    "machine_learning": [r"machine learning", r"random forest", r"gradient boost\w*",
                         r"neural network", r"xgboost", r"lightgbm"],
    "regime": [r"\bregimes?\b", r"hidden markov"],
    "indicators_rsi": [r"\brsi\b", r"relative strength index"],
    "indicators_macd": [r"\bmacd\b"],
}

RISK_TECHNIQUES: dict[str, list[str]] = {
    "stop_loss": [r"stop[- ]loss\w*", r"\bstops?\b"],
    "take_profit": [r"take[- ]profit", r"profit target"],
    "position_sizing": [r"position siz\w*", r"fixed fractional", r"risk per trade"],
    "kelly": [r"\bkelly\b"],
    "volatility_targeting": [r"volatility target\w*", r"risk parity"],
    "drawdown_control": [r"max(imum)? drawdown", r"drawdown limit"],
    "trailing_stop": [r"trailing stop"],
}

_FORMULA_LINE = re.compile(r"[A-Za-z_\u0370-\u03ff][\w\u0370-\u03ff()\[\],]*\s*=\s*[^=\n]{2,}")
_MATH = re.compile(r"[\u2211\u221a\u03c3\u03bc\u0394]|\\(frac|sum|sqrt|sigma|mu)|[*/^]"
                   r"|\b(sum|sqrt|log|exp|max|min)\(")
_RULE = re.compile(
    r"[^.!?\n]*\b(if|when|whenever|once)\b[^.!?\n]*\b(buy|sell|long|short|enter|exit|close)\b[^.!?\n]*[.!?]?",
    re.IGNORECASE)


def _count(patterns: dict[str, list[str]], text: str) -> dict[str, int]:
    low = text.lower()
    out = {}
    for name, pats in patterns.items():
        n = sum(len(re.findall(p, low)) for p in pats)
        if n:
            out[name] = n
    return dict(Counter(out).most_common())


def find_formulas(text: str, limit: int = 50) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        for m in _FORMULA_LINE.finditer(line):
            cand = m.group(0).strip()
            if _MATH.search(cand) and cand not in found:
                found.append(cand[:300])
    return found[:limit]


def find_rules(text: str, limit: int = 50) -> list[str]:
    rules = []
    for m in _RULE.finditer(text):
        s = " ".join(m.group(0).split())
        if 15 <= len(s) <= 400 and s not in rules:
            rules.append(s)
    return rules[:limit]


def extract_concepts(text: str) -> dict:
    return {
        "concepts": _count(CONCEPTS, text),
        "risk_techniques": _count(RISK_TECHNIQUES, text),
        "formulas": find_formulas(text),
        "rules": find_rules(text),
        "status": "hypotheses_untested",
    }
