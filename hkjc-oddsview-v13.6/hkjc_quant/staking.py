"""
注碼層 — 決定「買唔買」同「買幾多」
================================================================================
呢一層係你嘅情況同大戶最唔同嘅地方，所以要睇清楚。

**勝率模型 P_true 你同大戶係完全一樣嘅**（你講得啱，算式冇分別）。
但落注篩選同注碼計算有兩處必然唔同：

  1. 回扣門檻 — 每條 betline 輸注要 HK$10,000 以上先有 10–12% 回扣。
     你用 $100,000 模擬會攞到回扣，實際落 $500 就冇。
     結果：**模擬揀到嘅馬，同實際值博嘅馬，唔係同一批。**
     回扣會令落注門檻賠率由 O* = 1/p 降到 O* = (1−r+rp)/p。

  2. 價格衝擊 — 大戶落 $100,000 會壓低自己嘅賠率，你落 $500 唔會。
     呢點對你係著數，但補唔返冇回扣嘅損失。

所以下面所有函數都有 `rebate_rate` 參數。
做模擬嗰陣，記住要跑兩個版本（有回扣 / 冇回扣）再比較，
唔好用有回扣嘅結果去指導冇回扣嘅實戰。
================================================================================
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar

from config import (KELLY_FRACTION, MAX_BET_PCT_OF_POOL,
                    REBATE_MIN_LOSING_STAKE, PAYOUT_RATIO)


# ============================================================================
# 價格衝擊
# ============================================================================
def odds_after_bet(pool_total: float, horse_pool: float, stake: float,
                   payout_ratio: float = PAYOUT_RATIO["WIN"]) -> float:
    """
    你落注之後，你自己攞到嘅最終賠率。

        O = (總池 + 你注碼) × 派彩比例 / (該馬池 + 你注碼)

    香港係派最終賠率，所以呢個先係你真正嘅成交價，
    唔係你落注前見到嗰個數字。
    """
    if horse_pool + stake <= 0:
        return 0.0
    return (pool_total + stake) * payout_ratio / (horse_pool + stake)


def max_stake_before_edge_gone(p: float, pool_total: float, horse_pool: float,
                               payout_ratio: float = PAYOUT_RATIO["WIN"]) -> float:
    """
    解 p × O(b) = 1，即係 edge 被自己完全食光嗰個注碼。
    大戶實際落注會停喺呢個數之前（凱利最優點），所以枱面通常仲有殘值 —
    呢個就係「跟大戶」理論上仲有得諗嘅唯一原因。
    """
    num = p * payout_ratio * pool_total - horse_pool
    den = 1 - p * payout_ratio
    return max(0.0, num / den) if den > 0 else float("inf")


# ============================================================================
# 期望值
# ============================================================================
def expected_value(p: float, odds: float, stake: float = 0.0,
                   rebate_rate: float = 0.0) -> float:
    """
    每 $1 投注嘅期望回報。

        EV = p·O + r·(1−p)        （r = 回扣率，只喺輸注 ≥ $10,000 先適用）

    EV > 1 先值得落注。
    """
    r = rebate_rate if stake >= REBATE_MIN_LOSING_STAKE else 0.0
    return p * odds + r * (1 - p)


def breakeven_odds(p: float, rebate_rate: float = 0.0) -> float:
    """
    打和門檻賠率 O* = (1 − r + r·p) / p

    例：p = 20%
        冇回扣      → O* = 5.00
        10% 回扣    → O* = 4.60   （門檻鬆咗 8%）
        12% 回扣    → O* = 4.52
    """
    return (1 - rebate_rate + rebate_rate * p) / p


# ============================================================================
# 凱利
# ============================================================================
def kelly_stake(p: float, bankroll: float, pool_total: float, horse_pool: float,
                rebate_rate: float = 0.0,
                payout_ratio: float = PAYOUT_RATIO["WIN"],
                fraction: float = KELLY_FRACTION) -> dict:
    """
    數值解凱利 — 同時考慮價格衝擊同回扣，所以冇解析解。

        max_b  p·log(W − b + b·O(b)) + (1−p)·log(W − b + r·b)

    回傳 dict，包含建議注碼、實際成交賠率、落注後 EV。
    """
    def neg_growth(b):
        if b <= 0 or b >= bankroll:
            return 1e9
        O = odds_after_bet(pool_total, horse_pool, b, payout_ratio)
        r = rebate_rate if b >= REBATE_MIN_LOSING_STAKE else 0.0
        win_wealth = bankroll - b + b * O
        lose_wealth = bankroll - b + r * b
        if win_wealth <= 0 or lose_wealth <= 0:
            return 1e9
        return -(p * np.log(win_wealth) + (1 - p) * np.log(lose_wealth))

    upper = min(bankroll * 0.5, horse_pool * MAX_BET_PCT_OF_POOL * 50 + 1.0)
    res = minimize_scalar(neg_growth, bounds=(1.0, max(upper, 2.0)), method="bounded")

    full = float(res.x) if res.success else 0.0
    # 分數凱利：防模型誤差。全凱利喺模型有偏差時破產風險極高。
    b = full * fraction
    # 硬上限：單注唔好超過該馬彩池嘅某個比例，否則衝擊太大
    b = min(b, horse_pool * MAX_BET_PCT_OF_POOL)

    O_eff = odds_after_bet(pool_total, horse_pool, b, payout_ratio)
    return {
        "stake": round(b, 0),
        "full_kelly": round(full, 0),
        "effective_odds": round(O_eff, 3),
        "ev_after_impact": round(expected_value(p, O_eff, b, rebate_rate), 4),
        "gets_rebate": b >= REBATE_MIN_LOSING_STAKE,
    }


# ============================================================================
# 統計顯著性 — 最容易被忽略但最重要
# ============================================================================
def required_bets(edge: float, avg_odds: float, hit_rate: float,
                  z: float = 1.96) -> int:
    """
    要幾多注先可以話「我個策略真係正 ROI」而唔係好彩？

        σ ≈ O·√(p(1−p))
        n ≈ (z·σ / edge)²

    例：
        賠率 5、勝率 20%、想驗證 +5% ROI   →  約 6,200 注
        賠率 15、勝率 6.7%、想驗證 +5% ROI →  約 21,600 注

    香港一季 700–800 場。即使每場落兩注，一季得千五注。
    **即係你需要 4–10 季嘅有效樣本先有統計信心。**

    呢條式嘅用途：喺你見到回測 ROI +25% 而心跳加速嗰陣，
    先計下嗰個結果跑咗幾多注。500 注嘅 +25% 係純噪音。
    """
    sigma = avg_odds * np.sqrt(hit_rate * (1 - hit_rate))
    return int(np.ceil((z * sigma / edge) ** 2))


def bootstrap_roi_ci(returns: np.ndarray, n_boot: int = 10_000,
                     alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """
    ROI 嘅自助法信賴區間。
    returns: 每注嘅淨回報（以注碼為單位，例如贏 5 倍即 +4.0，輸即 −1.0）

    回報分佈嚴重右偏（大部分 −1，偶爾 +14），所以唔可以用常態近似，
    一定要用 bootstrap。

    **判讀：如果區間下限係負數，你冇證據話個策略賺錢。**
    """
    rng = np.random.default_rng(seed)
    returns = np.asarray(returns, dtype=np.float64)
    n = len(returns)
    if n == 0:
        return (np.nan, np.nan, np.nan)
    idx = rng.integers(0, n, size=(n_boot, n))
    means = returns[idx].mean(axis=1)
    return (
        float(np.quantile(means, alpha / 2)),
        float(returns.mean()),
        float(np.quantile(means, 1 - alpha / 2)),
    )
