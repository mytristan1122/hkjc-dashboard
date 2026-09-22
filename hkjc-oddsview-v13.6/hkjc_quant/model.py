"""
勝率模型 — 條件 Logit (Conditional Logit / MNL)
================================================================================
點解唔用 Gemini 建議嘅 XGBRegressor + softmax：

    model = xgb.XGBRegressor(objective="reg:squarederror")   # 目標 0/1
    p_true = softmax(model.predict(X))                        # ← 呢度爆

XGBRegressor 對 0/1 目標嘅輸出大約落喺 0.05–0.25。softmax 對咁窄嘅數值範圍
幾乎冇分辨力：12 匹馬你會得出 8.1%、8.3%、8.5% ⋯⋯ 即係接近平均分佈，完全冇用。

正確做法係條件 Logit：佢由定義上就係「一場得一個贏家」嘅模型，
機率自動加總等於 1，而且係 Benter 論文用嘅嗰隻。

    P(馬 i 喺場 r 勝出) = exp(xᵢ·β) / Σⱼ∈r exp(xⱼ·β)

對數概似：
    LL(β) = Σ_r [ x_{winner(r)}·β − log Σⱼ∈r exp(xⱼ·β) ]

梯度（有解析解，所以收斂好快）：
    ∂LL/∂β = Σ_r [ x_{winner(r)} − Σⱼ pⱼ xⱼ ]
================================================================================
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from config import L2_PENALTY


# ============================================================================
# 分組運算工具
# ============================================================================
def _group_softmax(v: np.ndarray, race_idx: np.ndarray, n_races: int):
    """
    按場計 softmax，同時回傳每場嘅 log-sum-exp。
    減 max 係數值穩定性標準做法，防止 exp 爆 overflow。
    """
    m = np.full(n_races, -np.inf)
    np.maximum.at(m, race_idx, v)
    e = np.exp(v - m[race_idx])
    s = np.bincount(race_idx, weights=e, minlength=n_races)
    p = e / s[race_idx]
    lse = m + np.log(s)
    return p, lse


class ConditionalLogit:
    """
    參數
    ----
    l2 : float
        L2 正則化強度。香港一季得 700 幾場，特徵又多，唔加正則化好易過擬合。
    """

    def __init__(self, l2: float = L2_PENALTY):
        self.l2 = l2
        self.beta_: np.ndarray | None = None
        self.feature_names_: list[str] | None = None
        self.ll_: float | None = None

    # ------------------------------------------------------------------
    def fit(self, X: np.ndarray, race_idx: np.ndarray, is_winner: np.ndarray,
            feature_names: list[str] | None = None):
        X = np.asarray(X, dtype=np.float64)
        race_idx = np.asarray(race_idx, dtype=np.int64)
        n_races = int(race_idx.max()) + 1
        win_mask = np.asarray(is_winner).astype(bool)

        if win_mask.sum() != n_races:
            # 通常係死熱或者資料有重複／缺失，一定要查清楚先訓練
            print(f"⚠ 警告：{n_races} 場但有 {win_mask.sum()} 個頭馬標記（死熱？重複資料？）")

        X_win_sum = X[win_mask].sum(axis=0)

        def neg_ll_and_grad(beta):
            v = X @ beta
            p, lse = _group_softmax(v, race_idx, n_races)
            ll = v[win_mask].sum() - lse.sum() - 0.5 * self.l2 * beta @ beta
            grad = X_win_sum - (p[:, None] * X).sum(axis=0) - self.l2 * beta
            return -ll, -grad

        res = minimize(
            neg_ll_and_grad, np.zeros(X.shape[1]), jac=True,
            method="L-BFGS-B", options={"maxiter": 500},
        )
        self.beta_ = res.x
        self.ll_ = -res.fun
        self.feature_names_ = feature_names
        return self

    # ------------------------------------------------------------------
    def predict_proba(self, X: np.ndarray, race_idx: np.ndarray) -> np.ndarray:
        race_idx = np.asarray(race_idx, dtype=np.int64)
        # 重新壓縮場次索引，令佢由 0 開始連續
        _, race_idx = np.unique(race_idx, return_inverse=True)
        v = np.asarray(X, dtype=np.float64) @ self.beta_
        p, _ = _group_softmax(v, race_idx, race_idx.max() + 1)
        return p

    # ------------------------------------------------------------------
    def coefficients(self) -> pd.DataFrame:
        names = self.feature_names_ or [f"x{i}" for i in range(len(self.beta_))]
        return (
            pd.DataFrame({"feature": names, "beta": self.beta_})
            .assign(abs_beta=lambda d: d.beta.abs())
            .sort_values("abs_beta", ascending=False)
            .drop(columns="abs_beta")
            .reset_index(drop=True)
        )


# ============================================================================
# 模型評估
# ============================================================================
def log_likelihood_per_race(p: np.ndarray, race_idx: np.ndarray,
                            is_winner: np.ndarray) -> float:
    """
    每場平均對數概似 — 比準確率有用好多嘅指標。
    基準線：12 匹馬隨機亂估 = log(1/12) = −2.485
    你嘅模型一定要明顯高過呢個數；而更重要嘅基準係「單純用公眾賠率」。
    """
    return float(np.log(np.clip(p[is_winner.astype(bool)], 1e-12, 1)).mean())


def calibration_table(p: np.ndarray, is_winner: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """
    校準檢查 — **呢一步唔做，你個 EV 就係亂咁計。**
    將預測機率分組，睇每組嘅實際勝出率對唔對得上。
    理想：predicted ≈ actual，逐行遞增，而且 95% 信賴區間覆蓋到對角線。
    """
    df = pd.DataFrame({"p": p, "y": is_winner.astype(int)})
    df["bin"] = pd.qcut(df["p"], n_bins, labels=False, duplicates="drop")
    out = df.groupby("bin").agg(
        n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean")
    )
    # 二項分佈標準誤
    out["se"] = np.sqrt(out.actual * (1 - out.actual) / out.n)
    out["diff_in_se"] = (out.actual - out.predicted) / out.se.replace(0, np.nan)
    return out.reset_index()


# ============================================================================
# Benter 第二階：合併基本面模型同公眾賠率
# ============================================================================
class SecondStage:
    """
    Benter (1994) 最關鍵嘅一步，亦都係 Gemini 嗰份筆記完全漏咗嘅一步。

        P_i  ∝  f_i^α · π_i^β

    f_i = 你嘅基本面模型機率
    π_i = 公眾賠率去抽水正規化之後嘅機率

    α、β 用同一套條件 logit 估，因為兩邊取 log 之後：
        log P_i = α·log f_i + β·log π_i − log(正規化常數)
    即係一個「兩個特徵」嘅條件 logit。

    Benter 報告過 β（公眾權重）相當大 — 公眾賠率本身係極強嘅預測變數。
    你個基本面模型嘅價值喺於邊際修正，唔係取代佢。

    判斷標準：second stage 嘅每場對數概似，必須明顯高過
      (a) 單用 f，同埋 (b) 單用 π。
    如果高唔過 (b)，即係你個基本面模型冇加到任何嘢，唔好落注。
    """

    def __init__(self, l2: float = 0.0):
        self.model = ConditionalLogit(l2=l2)
        self.alpha_ = None
        self.beta_ = None

    @staticmethod
    def _design(f: np.ndarray, pi: np.ndarray) -> np.ndarray:
        eps = 1e-9
        return np.column_stack([np.log(np.clip(f, eps, 1)), np.log(np.clip(pi, eps, 1))])

    def fit(self, f, pi, race_idx, is_winner):
        Z = self._design(np.asarray(f), np.asarray(pi))
        self.model.fit(Z, race_idx, is_winner, feature_names=["log_f", "log_pi"])
        self.alpha_, self.beta_ = self.model.beta_
        return self

    def predict_proba(self, f, pi, race_idx):
        return self.model.predict_proba(self._design(np.asarray(f), np.asarray(pi)), race_idx)

    def __repr__(self):
        return f"SecondStage(alpha={self.alpha_:.3f}, beta={self.beta_:.3f})"


# ============================================================================
# 公眾賠率 → 機率
# ============================================================================
def public_probabilities(odds: np.ndarray, race_idx: np.ndarray) -> np.ndarray:
    """
    去抽水正規化。

    注意：呢個係「按比例」去抽水，做法簡單但有已知偏差 —
    佢冇修正「冷門偏誤」(favourite–longshot bias)，即係公眾系統性
    高估大冷門、低估大熱門。

    想做好啲，可以改用冪次正規化 π_i ∝ (1/O_i)^γ，γ 用歷史數據估
    （香港嘅 γ 通常略大於 1）。不過如果你有用 SecondStage，
    第二階嘅 β 已經會部分吸收呢個偏差，所以呢度用簡單版就夠。
    """
    inv = 1.0 / np.asarray(odds, dtype=np.float64)
    _, race_idx = np.unique(race_idx, return_inverse=True)
    tot = np.bincount(race_idx, weights=inv, minlength=race_idx.max() + 1)
    return inv / tot[race_idx]
