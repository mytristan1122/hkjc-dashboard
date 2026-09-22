"""
特徵工程 — 大戶模型嘅靈魂，亦都係最易做錯嘅一步
================================================================================
最重要嘅一條規矩：**每一個特徵，都必須喺開跑前一刻已經知得到。**

新手最常犯、最難察覺、而且最會令你輸錢嘅錯誤叫「資料洩漏」(data leakage)：
唔覺意將賽後先知嘅資訊放咗入特徵，回測 ROI 突然變 +40%，實戰即刻爆炸。

典型洩漏（呢個檔案會主動幫你攔截）：
  · 用「今場完成時間」計速度指數           → 今場結果
  · 用「騎師整季勝率」                      → 包含未來賽事
  · 用「馬匹歷史平均名次」但冇 shift        → 包含今場名次
  · 用「跑道偏差」但用全日賽果計            → 包含今場

正確做法：所有滾動統計都要 groupby().shift(1) 或者 expanding 之後再 shift。
================================================================================
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import LEAKY_COLUMNS, MIN_RUNS_FOR_FORM


# ============================================================================
# 洩漏防護
# ============================================================================
def assert_no_leakage(feature_cols: list[str]) -> None:
    """喺訓練前叫一次。撞到黑名單就直接拋錯，唔好俾自己有機會 debug 到天光。"""
    bad = {c for c in feature_cols if c.lower() in LEAKY_COLUMNS}
    if bad:
        raise ValueError(
            f"偵測到資料洩漏特徵：{sorted(bad)}\n"
            "呢啲欄位係賽後先知，唔可以做輸入。如果你覺得係誤報，"
            "請修改 config.LEAKY_COLUMNS 而唔係喺呢度繞過。"
        )


def _prior(df: pd.DataFrame, group: str | list[str], col: str, how: str = "mean"):
    """
    計「唔包含當前呢一行」嘅歷史統計。
    必須先按時間排序。shift(1) 係關鍵 — 冇佢就係洩漏。
    """
    g = df.groupby(group, observed=True)[col]
    if how == "mean":
        out = g.transform(lambda s: s.shift(1).expanding().mean())
    elif how == "count":
        out = g.transform(lambda s: s.shift(1).expanding().count())
    elif how == "last":
        out = g.transform(lambda s: s.shift(1))
    elif how == "std":
        out = g.transform(lambda s: s.shift(1).expanding().std())
    else:
        raise ValueError(how)
    return out


# ============================================================================
# 速度指數
# ============================================================================
def adjusted_speed_figure(df: pd.DataFrame) -> pd.DataFrame:
    """
    修正速度指數 (ASF)。

    邏輯：同一場賽事所有馬跑同一條跑道、同一日天氣，所以「場內相對」
    已經自動抵消咗跑道偏差同天氣。呢個比 Gemini 建議嘅
    「歷史最佳時間 − 跑道偏差 − 負磅修正」穩陣好多，因為你唔使估
    跑道偏差係幾多。

        raw_speed = (該場中位時間 − 本馬時間) / 該場時間標準差

    再按路程分組標準化，令 1000m 同 2400m 嘅分數可比較。

    注意：呢個函數用咗 finish_time，所以**只可以用喺歷史行**，
    而且輸出一定要 shift 之後先做特徵（見 build_features）。
    """
    df = df.copy()
    grp = df.groupby("race_id", observed=True)["finish_time_sec"]
    med = grp.transform("median")
    sd = grp.transform("std").replace(0, np.nan)
    df["raw_speed"] = (med - df["finish_time_sec"]) / sd

    # 按路程再標準化
    dgrp = df.groupby("distance", observed=True)["raw_speed"]
    df["speed_figure"] = (df["raw_speed"] - dgrp.transform("mean")) / dgrp.transform("std")
    return df


# ============================================================================
# 主函數
# ============================================================================
def build_features(runs: pd.DataFrame) -> pd.DataFrame:
    """
    輸入：一行 = 一匹馬喺一場賽事嘅記錄，需要以下欄位
        race_id, race_date, horse_id, jockey_id, trainer_id,
        draw, actual_weight, declared_horse_weight, distance, venue,
        going, track_config, class_level, field_size,
        finish_time_sec, finishing_position        ← 後兩個只用嚟計歷史，唔做特徵

    輸出：加咗特徵欄位嘅 DataFrame，另加 FEATURE_COLS 清單。
    """
    df = runs.copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df = df.sort_values(["race_date", "race_id"]).reset_index(drop=True)

    df["is_winner"] = (df["finishing_position"] == 1).astype(int)
    df["top3"] = (df["finishing_position"] <= 3).astype(int)

    df = adjusted_speed_figure(df)

    # ---- 馬匹往績（全部 shift 過，唔包含今場）------------------------------
    df["horse_runs_prior"] = _prior(df, "horse_id", "is_winner", "count")
    df["horse_win_rate"] = _prior(df, "horse_id", "is_winner", "mean")
    df["horse_top3_rate"] = _prior(df, "horse_id", "top3", "mean")
    df["horse_speed_avg"] = _prior(df, "horse_id", "speed_figure", "mean")
    df["horse_speed_best"] = df.groupby("horse_id", observed=True)["speed_figure"].transform(
        lambda s: s.shift(1).expanding().max()
    )
    df["horse_speed_last"] = _prior(df, "horse_id", "speed_figure", "last")
    df["horse_speed_std"] = _prior(df, "horse_id", "speed_figure", "std")  # 穩定性
    df["last_position"] = _prior(df, "horse_id", "finishing_position", "last")

    # 出賽次數太少嘅，往績當缺失，唔好俾模型信一兩場嘅噪音
    thin = df["horse_runs_prior"] < MIN_RUNS_FOR_FORM
    for c in ["horse_win_rate", "horse_top3_rate", "horse_speed_avg", "horse_speed_std"]:
        df.loc[thin, c] = np.nan
    df["is_lightly_raced"] = thin.astype(int)

    # ---- 休賽日數 ---------------------------------------------------------
    last_date = df.groupby("horse_id", observed=True)["race_date"].shift(1)
    df["days_since_run"] = (df["race_date"] - last_date).dt.days
    df["is_first_start"] = df["days_since_run"].isna().astype(int)
    df["days_since_run"] = df["days_since_run"].clip(upper=365)

    # ---- 騎師 / 練馬師近況 ------------------------------------------------
    df["jockey_win_rate"] = _prior(df, "jockey_id", "is_winner", "mean")
    df["jockey_rides_prior"] = _prior(df, "jockey_id", "is_winner", "count")
    df["trainer_win_rate"] = _prior(df, "trainer_id", "is_winner", "mean")
    df["trainer_runs_prior"] = _prior(df, "trainer_id", "is_winner", "count")
    # 騎練組合（香港好重要，好多馬房有固定騎師）
    df["jt_combo"] = df["jockey_id"].astype(str) + "_" + df["trainer_id"].astype(str)
    df["jt_win_rate"] = _prior(df, "jt_combo", "is_winner", "mean")

    # ---- 馬匹 × 條件 適應性 -----------------------------------------------
    df["horse_dist"] = df["horse_id"].astype(str) + "_" + df["distance"].astype(str)
    df["horse_dist_win_rate"] = _prior(df, "horse_dist", "is_winner", "mean")
    df["horse_venue"] = df["horse_id"].astype(str) + "_" + df["venue"].astype(str)
    df["horse_venue_speed"] = _prior(df, "horse_venue", "speed_figure", "mean")

    # ---- 檔位 -------------------------------------------------------------
    # 絕對檔號意義有限（12 匹馬嘅 8 檔 ≠ 8 匹馬嘅 8 檔），要正規化
    df["draw_pct"] = (df["draw"] - 1) / (df["field_size"] - 1).replace(0, np.nan)
    df["is_inside_draw"] = (df["draw"] <= 3).astype(int)
    df["is_wide_draw"] = (df["draw"] >= df["field_size"] - 2).astype(int)
    # 跑馬地短途內欄優勢遠大過沙田，所以要交互項
    df["draw_x_hv_sprint"] = df["draw_pct"] * (
        (df["venue"] == "HV") & (df["distance"] <= 1200)
    ).astype(int)

    # ---- 負磅 / 體重 ------------------------------------------------------
    df["weight_vs_field"] = df["actual_weight"] - df.groupby("race_id", observed=True)[
        "actual_weight"
    ].transform("mean")
    prev_bw = df.groupby("horse_id", observed=True)["declared_horse_weight"].shift(1)
    df["bodyweight_change"] = df["declared_horse_weight"] - prev_bw
    df["bodyweight_change"] = df["bodyweight_change"].fillna(0).clip(-40, 40)

    # ---- 班次變化 ---------------------------------------------------------
    prev_class = df.groupby("horse_id", observed=True)["class_level"].shift(1)
    df["class_change"] = df["class_level"] - prev_class   # 負數 = 升班

    # ---- 相對班次（feature_discovery.py 驗證過：五季方向一致，通過噪音水平）
    # 唔止睇「同上一場比」，仲睇「同自己歷史平均班次比」。
    hist_class_mean = df.groupby("horse_id", observed=True)["class_level"].transform(
        lambda s: s.shift(1).expanding().mean()
    )
    df["class_relative"] = df["class_level"] - hist_class_mean

    # ---- 近況名次走勢（feature_discovery.py 驗證過：五季全部負、mean_abs_beta
    # 0.187、0 次正負反覆，係目前搜到最強嘅新特徵）。
    # 區別於 horse_win_rate（終身平均），呢個淨睇最近 3 場，捕捉短期走勢。
    df["recent3_position"] = df.groupby("horse_id", observed=True)[
        "finishing_position"
    ].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())

    df["field_size_n"] = df["field_size"]

    return df


FEATURE_COLS = [
    "horse_speed_avg", "horse_speed_last",
    "horse_top3_rate", "last_position",
    "horse_runs_prior", "is_lightly_raced",
    "jockey_win_rate", "jt_win_rate",
    "horse_dist_win_rate", "horse_venue_speed",
    "draw_pct", "draw_x_hv_sprint",
    "class_relative", "recent3_position",
]
# 註：2026-09-20 用 diagnose_features.py（21 特徵、五季逐季係數）剪走
# 以下十個噪音特徵 —— 全部 |β| 細、逐季正負反覆（sign_flips 高），
# 唔係穩定訊號：
#   horse_speed_best, horse_speed_std, horse_win_rate, trainer_win_rate,
#   is_inside_draw, is_wide_draw, weight_vs_field, bodyweight_change,
#   class_change, days_since_run
# 呢批特徵嘅計算式喺 build_features() 度冇刪（唔影響其他用途），
# 淨係喺 FEATURE_COLS 度剔走，即係話唔會餵落模型。
#
# 已剔走（結構性冇用，唔關噪音事，見之前討論）：field_size_n、is_first_start。
#
# 候選中，未驗證：draw_x_hv、draw_x_st_long（feature_discovery.py，
# 源自 pattern_analysis.py 嘅檔位 lift 發現）—— 用真實數據跑
# feature_discovery.py 確認通過噪音水平先加落嚟。



def prepare_matrix(df: pd.DataFrame, cols: list[str] = None) -> tuple:
    """
    轉成模型輸入：標準化 + 缺失值處理。
    缺失值用「全體中位數」填，並加一個 _isna 旗標，
    等模型自己學「冇往績」本身係咪一個訊號。
    """
    cols = cols or FEATURE_COLS
    assert_no_leakage(cols)

    X = df[cols].copy()
    flags = X.isna().astype(int).add_suffix("_isna")
    # 只保留真係有缺失嘅旗標，避免成堆全 0 欄位
    flags = flags.loc[:, flags.sum() > 0]

    med = X.median()
    X = X.fillna(med)
    mu, sd = X.mean(), X.std().replace(0, 1.0)
    X = (X - mu) / sd

    X = pd.concat([X, flags], axis=1)
    scaler = {"median": med, "mean": mu, "std": sd, "flag_cols": list(flags.columns)}
    return X.values.astype(np.float64), list(X.columns), scaler
