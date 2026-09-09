# HKJC 背景記錄引擎 — VPS 部署教學

## 呢個係咩
一個獨立程式 `recorder.py`，24 小時喺 VPS 背景跑，自動：
- 用 activeMeetings 搵所有賽馬日
- 掃描每場開賣狀態
- 一開賣就自動記錄（WIN/PLA/QIN/QPL 賠率 + 彩池 + 開跑時間）
- 每 30 秒寫 snapshot 落 /root/hkjc_data（同 dashboard 同格式）
- 同時記全部開賣中場次

Dashboard 就照舊顯示 + 翻睇（REPLAY 會讀到 recorder 記低嘅數據）。

## 兩個程式分工
- **dashboard**（hkjc.service）：顯示 + 翻睇（你開網址睇）
- **recorder**（hkjc-recorder.service）：背景記錄（24 小時自己跑，唔使人）

兩個都寫／讀同一個 `/root/hkjc_data`，所以 recorder 記低嘅，dashboard REPLAY 就睇到。

═══════════════════════════════════════════════════════
  部署步驟（喺 VPS console 做）
═══════════════════════════════════════════════════════

## 步驟 1 — 放 recorder.py 上 GitHub（同 dashboard 一樣方法）
1. 去你 GitHub repo：github.com/mytristan1122/hkjc-dashboard
2. 撳 Add file → Create new file
3. 檔名打：`recorder/recorder.py`（咁會自動建立 recorder 資料夾）
4. 將 recorder.py 內容全部貼入去
5. Commit changes

## 步驟 2 — VPS 下載
喺 VPS console：
```
cd /root/hkjc-dashboard && git pull
```
（會將 recorder/recorder.py 抽落嚟）

## 步驟 3 — 放去獨立資料夾
```
mkdir -p /root/hkjc-recorder
cp /root/hkjc-dashboard/recorder/recorder.py /root/hkjc-recorder/
```

## 步驟 4 — 先手動測試一次（好重要）
```
/root/hkjc-dashboard/hkjc-oddsview-v13.6/.venv/bin/python3 /root/hkjc-recorder/recorder.py
```
睇 log：
- 「冇 active meetings」= 而家唔係賽馬日或未開賣（正常，唔係錯）
- 「記錄咗 X 場（開賣中）」= 有場開賣，正在記錄 ✓
- 撳 Ctrl+C 停手動測試

## 步驟 5 — 設定 24 小時 service
建立 service 檔（整段貼）：
```
cat > /etc/systemd/system/hkjc-recorder.service << 'EOF'
[Unit]
Description=HKJC Background Recorder
After=network.target

[Service]
User=root
WorkingDirectory=/root/hkjc-recorder
ExecStart=/root/hkjc-dashboard/hkjc-oddsview-v13.6/.venv/bin/python3 /root/hkjc-recorder/recorder.py
Restart=always
RestartSec=10
Environment=HKJC_DATA_DIR=/root/hkjc_data
Environment=HKJC_SCAN_SEC=30

[Install]
WantedBy=multi-user.target
EOF
```
啟動：
```
systemctl daemon-reload
systemctl enable hkjc-recorder
systemctl start hkjc-recorder
```
確認：
```
systemctl status hkjc-recorder
```
見到綠色 active (running) = 成功！24 小時背景記錄中。

═══════════════════════════════════════════════════════
  日後常用指令
═══════════════════════════════════════════════════════
睇 recorder 狀態：      systemctl status hkjc-recorder
睇 recorder log：       journalctl -u hkjc-recorder -n 50 -f
重啟 recorder：         systemctl restart hkjc-recorder
停 recorder：           systemctl stop hkjc-recorder
睇記錄咗咩場次：        ls -la /root/hkjc_data/

═══════════════════════════════════════════════════════
  ⚠️ 關鍵：實測驗證（部署後一定要做）
═══════════════════════════════════════════════════════
因為 recorder 嘅邏輯喺開發環境（連唔到馬會）淨係用假數據驗證過，
真正「連到馬會、搵到賽馬日、記到嘢」一定要喺 VPS 實測：

1. 部署後，賽馬日開賣期間（前一日中午 12 點後），睇：
   journalctl -u hkjc-recorder -n 30 -f
   應該見到「XXXX-XX-XX ST：記錄咗 X 場（開賣中）」

2. 過幾分鐘後，check 硬碟：
   ls -la /root/hkjc_data/
   應該見到多咗場次資料夾（例如 2026-09-13__ST__1）

3. 如果一直「冇 active meetings」但你知有賽馬日開緊賣：
   → 可能 activeMeetings query 唔 work，要抓「賽期表」API（話我知）

4. 開 dashboard REPLAY，揀返 recorder 記低嗰場，拉時間軸應該睇返到。

═══════════════════════════════════════════════════════
  疑難
═══════════════════════════════════════════════════════
- status 唔係 running：journalctl -u hkjc-recorder -n 50 睇錯誤，copy 俾我
- 「冇 active meetings」但明明有賽馬日：activeMeetings 可能唔夠，要抓賽期 API
- 硬碟冇檔案：可能未開賣、或未有賠率、或 IP 問題，睇 log
