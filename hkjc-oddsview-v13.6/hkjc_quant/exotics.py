"""
名次聯合機率 — 打連贏 / 位置Q 必須嘅一層
================================================================================
勝率模型只出 P(第一)。但你要打嘅係連贏同位置Q（因為佢哋有 12% 回扣，
有效抽水最低），所以要由單匹勝率推出「名次組合」嘅機率。

Harville (1973) 基本假設：
    P(a 第一, b 第二) = p_a × p_b/(1−p_a)
即係「第一名跑咗出嚟之後，剩低嘅馬按原機率重新比一次」。

呢個假設**實證上係錯**嘅：佢會高估熱門馬跑第二三名嘅機率。
直覺解釋：一隻熱門馬如果冇贏，通常係因為當日狀態唔好或者受阻，
而唔係「差少少」，所以佢跑第二嘅機率冇 Harville 估嘅咁高。

修正（Stern / Lo & Bacon-Shone 折扣模型，佢哋當年正正用港日數據校準）：
    P(b 第二 | a 第一) = p_b^λ₂ / Σ_{k≠a} p_k^λ₂     λ₂ < 1
    P(c 第三 | a,b)    = p_c^λ₃ / Σ_{k≠a,b} p_k^λ₃   λ₃ < λ₂

文獻經驗值 λ₂≈0.81、λ₃≈0.65，但**唔好照抄** — 用你自己嘅數據重估。
呢個係少數你有機會做得好過公開文獻嘅位，因為你可以按路程、
場地、跑法分開估唔同嘅 λ。
================================================================================
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar

from config import LAMBDA2_INIT, LAMBDA3_INIT

EPS = 1e-12


# ============================================================================
# λ 估計
# ============================================================================
def fit_lambda2(races: list[dict]) -> float:
    """
    用最大概似估 λ₂。

    races: [{"p": np.array 勝率, "first": int 索引, "second": int 索引}, ...]

    LL(λ) = Σ_r [ λ·log p_second − log Σ_{k≠first} p_k^λ ]
    """
    def neg_ll(lam):
        total = 0.0
        for r in races:
            p = np.clip(r["p"], EPS, 1.0)
            mask = np.ones(len(p), dtype=bool)
            mask[r["first"]] = False
            denom = (p[mask] ** lam).sum()
            total += lam * np.log(p[r["second"]]) - np.log(max(denom, EPS))
        return -total

    res = minimize_scalar(neg_ll, bounds=(0.1, 1.5), method="bounded")
    return float(res.x)


def fit_lambda3(races: list[dict]) -> float:
    """同上，但條件係頭兩名都已知。races 額外需要 "third"。"""
    def neg_ll(lam):
        total = 0.0
        for r in races:
            p = np.clip(r["p"], EPS, 1.0)
            mask = np.ones(len(p), dtype=bool)
            mask[[r["first"], r["second"]]] = False
            denom = (p[mask] ** lam).sum()
            total += lam * np.log(p[r["third"]]) - np.log(max(denom, EPS))
        return -total

    res = minimize_scalar(neg_ll, bounds=(0.1, 1.5), method="bounded")
    return float(res.x)


# ============================================================================
# 名次機率
# ============================================================================
def ordered_triple_matrix(p: np.ndarray, lam2: float, lam3: float) -> np.ndarray:
    """
    回傳 T[a,b,c] = P(a 第一, b 第二, c 第三)，a/b/c 互不相同時先有值。
    n ≤ 14，所以 O(n³) 完全冇問題。
    """
    n = len(p)
    p = np.clip(np.asarray(p, dtype=np.float64), EPS, 1.0)
    p2 = p ** lam2
    p3 = p ** lam3

    T = np.zeros((n, n, n))
    for a in range(n):
        d2 = p2.sum() - p2[a]
        if d2 <= EPS:
            continue
        for b in range(n):
            if b == a:
                continue
            pb = p2[b] / d2
            d3 = p3.sum() - p3[a] - p3[b]
            if d3 <= EPS:
                continue
            # 一次過填 c 呢一維，快好多
            pc = p3 / d3
            pc[a] = pc[b] = 0.0
            T[a, b, :] = p[a] * pb * pc
    return T


def quinella_probs(p: np.ndarray, lam2: float = LAMBDA2_INIT) -> np.ndarray:
    """
    Q[i,j] = P(i 同 j 係頭兩名，唔計次序)，i < j。
    只需要 λ₂，唔使算三重矩陣，所以快好多。
    """
    n = len(p)
    p = np.clip(np.asarray(p, dtype=np.float64), EPS, 1.0)
    p2 = p ** lam2
    tot2 = p2.sum()

    Q = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            # i 第一 j 第二
            a = p[i] * p2[j] / max(tot2 - p2[i], EPS)
            # j 第一 i 第二
            b = p[j] * p2[i] / max(tot2 - p2[j], EPS)
            Q[i, j] = Q[j, i] = a + b
    return Q


def quinella_place_probs(p: np.ndarray, lam2: float, lam3: float) -> np.ndarray:
    """
    QP[i,j] = P(i 同 j 兩匹都跑入前三)。
    等於將所有「頭三名包含 i 同 j」嘅排列加埋。
    """
    T = ordered_triple_matrix(p, lam2, lam3)
    n = len(p)
    QP = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            s = 0.0
            for k in range(n):
                if k == i or k == j:
                    continue
                s += (T[i, j, k] + T[i, k, j] + T[j, i, k]
                      + T[j, k, i] + T[k, i, j] + T[k, j, i])
            QP[i, j] = QP[j, i] = s
    return QP


def place_probs(p: np.ndarray, lam2: float, lam3: float) -> np.ndarray:
    """每匹馬跑入前三嘅機率（香港 7–20 匹賽事位置派彩取前三）。"""
    T = ordered_triple_matrix(p, lam2, lam3)
    return T.sum(axis=(1, 2)) + T.sum(axis=(0, 2)) + T.sum(axis=(0, 1))


# ============================================================================
# 合理性檢查
# ============================================================================
def sanity_check(p: np.ndarray, lam2: float, lam3: float) -> dict:
    """
    每次改完 λ 都應該跑一次。
    · 位置機率總和應該 ≈ 3.0（前三名共三個位）
    · 連贏機率總和應該 ≈ 1.0
    """
    pl = place_probs(p, lam2, lam3)
    Q = quinella_probs(p, lam2)
    return {
        "sum_place (應 ≈ 3.0)": float(pl.sum()),
        "sum_quinella (應 ≈ 1.0)": float(np.triu(Q, 1).sum()),
        "max_place (應 ≤ 1.0)": float(pl.max()),
    }
