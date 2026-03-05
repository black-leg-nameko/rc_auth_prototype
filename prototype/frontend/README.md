# RC Auth PoC Frontend (TypeScript)

TypeScriptで書いた継続認証PoCの可視化フロントエンドです。  
現在の実装は `run_demo_api.py` が提供するAPIに接続して動作します。

## できること

- ブラウザ上で打鍵イベントを収集（Enrollment / Authentication）
- RCモデルの登録学習（`POST /api/enroll`）
- 本人性判定（`POST /api/authenticate`）
- スコア時系列の再生表示とリスク判定ログの可視化

## 起動方法（推奨）

```bash
cd /home/blackleg/ws/mitou_target/prototype
python3 scripts/run_demo_api.py --port 8080
```

ブラウザで `http://127.0.0.1:8080` を開いてください。

## APIエンドポイント

- `GET /api/health`
- `GET /api/enroll/status`
- `POST /api/enroll`
- `POST /api/enroll/reset`
- `POST /api/authenticate`
- `GET /api/session?mode=normal|takeover`（互換デモ用）

## TypeScriptチェック

```bash
cd /home/blackleg/ws/mitou_target/prototype/frontend
npm install
npm run typecheck
```

注: `src/main.js` は同梱済みです。UI実行だけならTypeScriptビルドは不要です。
