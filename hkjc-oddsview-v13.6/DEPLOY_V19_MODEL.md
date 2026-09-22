# V19.1 即時量化模型部署

此套件只更新 `app-new.py`。請保留伺服器原有 `app.py`，不要覆蓋或刪除。

## 放置位置

將壓縮檔內以下項目上載到：

`/root/hkjc-dashboard/hkjc-oddsview-v13.6/`

- `app-new.py`
- `hkjc_quant/`（整個資料夾）
- `requirements-v19-model.txt`

完成後目錄應為：

```text
/root/hkjc-dashboard/hkjc-oddsview-v13.6/
├── app.py                    # 舊版，不變
├── app-new.py                # V19.1
├── requirements-v19-model.txt
└── hkjc_quant/
    ├── config.py
    ├── features.py
    ├── model.py
    ├── exotics.py
    ├── staking.py
    ├── data/runs_clean.csv
    └── models/latest_portable.json
```

## 安裝及啟動

```bash
cd /root/hkjc-dashboard/hkjc-oddsview-v13.6
python3 -m pip install -r requirements-v19-model.txt
streamlit run app-new.py --server.port 8502 --server.address 0.0.0.0
```

舊版可繼續使用原本 port（例如 8501）；新版使用 8502，兩者可以同時運行。

## 即時計算欄位

- 基本面勝率：歷史賽果與排位特徵模型
- 市場勝率：HKJC 即時獨贏賠率去水後的市場概率
- 綜合勝率：Benter 第二階合併概率
- 直博率：`綜合勝率 ÷ 市場勝率 − 1`
- Fair Odds：`1 ÷ 綜合勝率`
- EV：`綜合勝率 × 即時賠率 + 回扣率 × (1 − 綜合勝率)`
- 位置概率：按 λ2/λ3 折扣名次模型估算；少於 7 匹時改算前二

模型訊號只屬研究評分。現有 walk-forward 結果尚未證明優勢達統計顯著。
