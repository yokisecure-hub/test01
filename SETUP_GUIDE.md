# 日経225先物ミニ AI相場判定システム - セットアップガイド

## 前提条件

- Windows 10 / 11
- Python 3.10 以上がインストール済み
  - まだの場合: https://www.python.org/downloads/ からダウンロード
  - インストール時に **「Add Python to PATH」にチェック** を入れること

---

## セットアップ手順（ゼロから）

### 1. プロジェクトフォルダを開く

PowerShell を開いて、プロジェクトフォルダに移動する:

```powershell
cd C:\Users\yosim\test01\test01
```

### 2. パッケージをインストール

以下を1行ずつ実行する:

```powershell
python -m pip install --upgrade pip
```

```powershell
python -m pip install python-dotenv
```

```powershell
python -m pip install requests
```

```powershell
python -m pip install playwright
```

### 3. ブラウザ（Chromium）をインストール

```powershell
python -m playwright install chromium
```

### 4. APIキーを設定

`.env` ファイルを開く:

```powershell
notepad .env
```

以下の内容を書いて保存する:

```
GOOGLE_API_KEY=ここにあなたのGemini APIキーを貼り付け
```

APIキーの取得先: https://aistudio.google.com/apikey

### 5. アプリを実行

```powershell
python nikkei_trader.py
```

---

## 2回目以降の起動方法（毎日の使い方）

パッケージのインストールは初回だけでOK。2回目以降はこれだけ:

```powershell
cd C:\Users\yosim\test01\test01
python nikkei_trader.py
```

---

## よくあるエラーと対処法

| エラー内容 | 原因 | 対処法 |
|---|---|---|
| `ModuleNotFoundError: No module named 'xxx'` | パッケージ未インストール | `python -m pip install パッケージ名` |
| `GOOGLE_API_KEY が設定されていません` | APIキー未設定 | `.env` ファイルにキーを記入 |
| `playwright install` が認識されない | コマンドが違う | `python -m playwright install chromium` を使う |
| `greenlet` のビルドエラー | コンパイラ不足 | `pip install greenlet --only-binary=:all:` を先に実行 |
| `Gemini API エラー (HTTP 400)` | APIキーが無効 | https://aistudio.google.com/apikey で新しいキーを取得 |
