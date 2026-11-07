# 開発ガイド

## 構成

- `app.py` / `geocoder.py`：Windows GUIと日本語検索。
- `core.py`：範囲、縮尺、Arnisコマンド。
- `jobs.py` / `jobcache.py`：生成工程と再試行。
- `tracking.py` / `realmap.py` / `nativepack.py`：座標対応、OSMベクトルからの地図描画、通常ワールドのアドオン。
- `treasure*` / `beds*` / `loot*`：屋内判定、配置計画、バランスを付けた地域のアイテム。
- `national_backend.py` / `national_pack.py` / `national_state.py`：PBF索引、固定全国座標での区画結合、パック、準備状態の検証。
- `furniture_backend.py` / `loot_native.py`：通常のチェスト・アイテムNBT・両側のベッドを生成時に保存します。生成者のSQLiteジャーナルで再試行時の補充を防ぎます。
- `regions-loader.js`：旧形式の、プレイヤー周辺で家具を配置するワールド向け互換処理です。事前配置版では実行しません。
- `geology.py` / `geology_backend.py`：GSJ/GSI参照と、普通の石だけを変更する地質用の後処理。
- `autogen.py`：実験的な無人処理。関東優先の等倍生成、再試行、容量を予約した出力。

`version.py`にアプリとパックのバージョンを定義します。更新パックをMinecraftに再取り込みさせる場合はパックバージョンを進めます。配置済み家具の永続IDは変更しないでください。変更すると補充・重複につながります。

## ビルド

Windows x64、Python 3.12以降で`requirements-build.txt`を導入します。`scripts/bootstrap_runtime.py`は公式配布元からArnis v3.4.0とPython 3.12.10を取得してSHA-256を確認し、固定バージョンのwheelを隔離ランタイムへ配置します。

PyInstallerのspecは相対パスで動作します。生成物やエンジンはGit管理しません。再現のために要求バージョンを固定していますが、ビット単位の再現可能ビルドを保証するものではありません。wheelのライセンスを削除しないでください。

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
python -m PyInstaller --noconfirm MapCraft.spec
python scripts/package_release.py
```

`.github/workflows/test.yml`は通常のテスト、`build-windows.yml`は手動のWindows ZIPビルドです。自動で公開Releaseへ発行しません。

## 無人生成（実験機能）

`config/autogen.example.json`を`config/autogen.json`へコピーし、PBF、索引、出力先と容量上限を自分の環境に合わせます。相対パスはソースまたは配布フォルダーを基準に解決します。

```powershell
engine\national-runtime\python.exe autogen.py --config config\autogen.json
```

この機能は等倍1mに固定し、関東を優先する広域生成です。汎用の通常生成や縮尺変更にはGUIを使ってください。進捗は設定したbase内の`自動生成`へ保存します。容量を予約し、上限に近づいた場合は未生成範囲を省いて出力します。停電・再起動を越えて自動実行するWindowsサービスではありません。

## 検証の範囲

リポジトリのテストは、決定的な配置選択、家具設定の独立性、座標変換、HUD比率、未作成索引を作ってしまわないことを検証します。開発中には、小区画の実エンジン生成、地下鉄・地盤の保持、無人処理から出力まで、JavaScriptの配置・再接続も検証しています。

これらはMinecraftの実画面での受け入れテストの代わりにはなりません。公開前には、配布ZIPを別の空フォルダーへ展開して起動し、小範囲生成、world/add-onの取り込み、ミニマップ、家具、再試行を実ゲームで確認してください。大規模な完成ワールドのゲーム動作は未検証です。

事前配置はネイティブLevelDBの読み戻し、チェストの中身、チャンク境界をまたぐベッド、再試行で補充しないこと、実エンジンから全国座標への結合を検証しています。就寝・チェスト開閉・マルチ参加の実ゲーム検証は未実施です。`engine\national-runtime\python.exe -m unittest discover -s tests -v`でネイティブテストも実行できます。

旧版の無人生成を止めずに最後だけ新形式へ変換する場合は、新ソースの別コピーから同梱Pythonで`scripts/finalize_furniture.py --config <旧処理の絶対パス設定JSON>`を実行します。旧処理の完了と両方のロック解放を待ち、生成者の元DBに家具を追加して再出力します。二重に巨大な出力を保持しないため、同じプロジェクトIDを検証した古い完成アーカイブを置き換えます。プレイ済みワールドには使用しないでください。待機処理はWindowsサービスではありません。

## 配布EXEの診断

展開したフォルダーで `MapCraft.exe --self-check report.json` を実行すると、画面を開かず、同梱カタログ・HUD・エンジンのハッシュ・Tk・隔離ランタイムの読み込み結果をJSONへ出力します。実ゲームの表示確認とは別のチェックです。
