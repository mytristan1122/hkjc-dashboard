    if not _pla.empty:
        _inv = 1.0 / _pla["即場"]
        pla_part = {int(h): v for h, v in zip(_pla["馬號"], _inv / _inv.sum() * 100.0)}
    else:
        pla_part = {}

    # ═══ ③ 四池綜合熱度（分層 ⚡🔥💥）+ 30分鐘訊號彙總 ═══
    pool_totals = {"WIN": win_inv, "PLA": pla_inv,
                   "QIN": this_inv.get("QIN"), "QPL": this_inv.get("QPL")}
    rise_thresh = st.session_state.get("rise_thresh", 0.5)
    with st.container(key="heat_signal_row"):
        hcol1, hcol2 = st.columns([1.3, 1])
        with hcol1:
            four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S,
                                 pool_totals, cold_odds=10.0, rise_thresh=rise_thresh)
        with hcol2:
            # 統一用 ACTIVE_RACE_KEY：REPLAY 讀返自己嗰場嘅 signals.jsonl，
            # 唔會再寫／讀錯上面選單嗰場。
            if replay_mode and replay_snaps and replay_idx is not None:
                try:
                    if ACTIVE_RACE_KEY and not os.path.isfile(_signal_log_path(ACTIVE_RACE_KEY)):
                        backfill_signals_from_disk(ACTIVE_RACE_KEY)
                except Exception:
                    pass
                _sig_events = load_signal_log(ACTIVE_RACE_KEY, 0.0)
            else:
                _sig_events = list(S["signal_log"])
            signal_summary_panel(_sig_events, minutes=30, as_of_ts=ACTIVE_NOW_TS)

    # ═══ ④ 投注額棒型圖（直向）═══
    _m1 = st.session_state.get("money_t1", MONEY_TIER1)
    _m2 = st.session_state.get("money_t2", MONEY_TIER2)
    _m3 = st.session_state.get("money_t3", MONEY_TIER3)
    bar_sort = st.radio("棒型圖排序", ["順馬號", "順賠率（熱→冷）"], horizontal=True,
                        label_visibility="collapsed", key="bar_sort")
    sort_key = "賠率" if "賠率" in bar_sort else "馬號"

    # ── REPLAY 時間軸：快捷跳點 + 滑桿微調（擺喺排序之後、棒型圖之前）──
    if replay_mode and replay_snaps:
        _n_snaps = len(replay_snaps)
        st.markdown('<div style="font-size:11px;color:var(--subtext);margin:4px 0 4px">⏱️ REPLAY 時間軸 · 快捷跳到（撳邊個欄位＝跳去嗰段結束，該欄就顯示完整金額）</div>',
                    unsafe_allow_html=True)
        # 15粒chip同落注表15個欄位一一對應。每粒跳去「該時段結束」嗰一刻：
        #   隔夜 → 當日00:00 ／ 當日 → 開跑前60分 ／ 60 → 開跑前30分 ／ …
        #   2 → 開跑前1分 ／ 開跑 → 最後一個記錄點
        _chip_defs = [
            ("隔夜", "midnight"), ("當日", 60), ("60", 30), ("30", 20), ("20", 10),
            ("10", 9), ("9", 8), ("8", 7), ("7", 6), ("6", 5),
            ("5", 4), ("4", 3), ("3", 2), ("2", 1), ("開跑", "post"),
        ]
        _chip_cols = st.columns(len(_chip_defs))
        for (_clbl, _cval), _ccol in zip(_chip_defs, _chip_cols):
            with _ccol:
                if st.button(_clbl, key=f"chip_{_clbl}", use_container_width=True):
                    _target_idx = None
                    # 用 ACTIVE_POST_TIME（＝REPLAY 揀嗰場自己嘅開跑時間），
                    # 唔再讀 S["post_time"]（嗰個可能係上面選單另一場）。
                    _pt = ACTIVE_POST_TIME
                    if _cval == "post":
                        # 跳去最接近「開跑時間」嗰個記錄點，唔可以盲跳最後一個
                        # —— recorder 有時開跑後仲會繼續錄（實測第1場最後一個
                        # 記錄點係 23:54，即開跑後成 5 個鐘）。
                        if _pt is not None:
                            _target_idx = _nearest_snap_idx(replay_snaps, _pt.timestamp())
                        else:
                            _target_idx = _n_snaps - 1
                    elif _pt is not None:
                        if _cval == "midnight":
                            _mts = datetime(_pt.year, _pt.month, _pt.day, 0, 0, 0,
                                            tzinfo=HKT).timestamp()
                            _target_idx = _nearest_snap_idx(replay_snaps, _mts)
                        else:
                            _tts = _pt.timestamp() - _cval * 60
                            _target_idx = _nearest_snap_idx(replay_snaps, _tts)
                    if _target_idx is not None:
                        st.session_state["replay_idx_slider"] = _target_idx
                        st.rerun()
                    else:
                        st.warning("呢場冇開跑時間記錄，跳唔到；可以用落面個滑桿微調。")

        def _replay_lbl(i):
            s = replay_snaps[i]
            # 用 ACTIVE_POST_TIME（取眾數），唔用 snapshot 自己嗰個 post_time
            # —— 第1場入面有 15 個寫住 19:00、2 個寫住 19:40，用佢哋會令標籤
            # 拉去唔同點就跳嚟跳去（實測顯示成「開跑前70分」）。
            if ACTIVE_POST_TIME is not None:
                _mtp = (s["ts"] - ACTIVE_POST_TIME.timestamp()) / 60.0
                return f"開跑前 {abs(_mtp):.0f} 分" if _mtp < 0 else f"開跑後 {_mtp:.0f} 分"
            return datetime.fromtimestamp(s["ts"], HKT).strftime("%H:%M:%S")

        replay_idx = st.slider("時間軸（拉去任何一刻，微調）", 0, _n_snaps - 1, key="replay_idx_slider")
        st.caption(f"時間點：{_replay_lbl(replay_idx)}　（共 {_n_snaps} 個記錄點，每 30 秒一個）")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        stake_bar_chart_v(df[df["池"] == "WIN"], "獨贏", win_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
    with bcol2:
        stake_bar_chart_v(df[df["池"] == "PLA"], "位置", pla_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)

    # ═══ ⑤ 落注金額表（獨贏 / 位置）═══
    # 統一用 ACTIVE 變數：睇邊場（ACTIVE_RACE_KEY）、睇邊一刻（ACTIVE_NOW_TS）。
    # LIVE 同 REPLAY 行同一條 code，唔再分開兩套。
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="WIN", m1=_m1, m2=_m2, m3=_m3,
                       disk_race_key=ACTIVE_RACE_KEY, as_of_ts=ACTIVE_NOW_TS)
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="PLA", m1=_m1, m2=_m2, m3=_m3,
                       disk_race_key=ACTIVE_RACE_KEY, as_of_ts=ACTIVE_NOW_TS)

    # ── footer ──
    now_str = datetime.now(HKT).strftime("%H:%M:%S")
    st.markdown(
        f'<div style="text-align:center;margin-top:1rem;padding:8px;border-top:1px solid var(--border);'
        f'font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted)">'
        f'{APP_NAME} {APP_VERSION} · 每 5 秒自動更新 · {now_str} HKT</div>',
        unsafe_allow_html=True)
