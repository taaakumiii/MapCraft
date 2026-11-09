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
- `volcanoes.py` / `active-volcanoes.json`：気象庁の火山代表点とゲーム用の地下・宝箱調整。CPU/GPU共通の追加処理は元の普通の石だけを変更します。
- `villagers.py` / `villagers_backend.py` / `municipal-population.json`：実人口の面積按分、住宅内候補、通常の村人Actor・digp保存と再試行ジャーナル。出生地や建物別居住者の再現ではありません。
- `ecology.py` / `ecology_backend.py`：OSMの生息地・施設面積による配置計画、通常のActor・digp・攻略チェスト・水槽・エンド入口の事前保存。独自の実行時パックは使いません。`tests/test_native_mobs.py`でNative DB、面積ランク、宝箱、再試行を検証します。
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

## 地域の区切りと地盤計算の並列化

全国プロジェクトと同じ`map_id`・原点・投影・縮尺・区画幅を持つ計画JSON（`tiles`に`[tx,tz]`、`total`に個数）を指定して、地域単位で生成を区切れます。

```powershell
engine\national-runtime\python.exe scripts\finish_phase.py --config config\autogen.json --plan path\to\phase.json --takeover --workers 8
```

`--takeover`は現在の区画の保存完了を待ってから、両方の生成ロックを取得して引き継ぎます。外側の未生成区画は`deferred_phase`として保持し、全国計画や生成済み区画を削除しません。指定地域の生成、家具の事前配置、出力を終えると停止します。以後の拡張時は、追加する地域の計画に対して保留を解除します。出力済みのプレイワールドの変更を自動統合する機能ではありません。

地盤CPU計算は`MAPCRAFT_GEOLOGY_WORKERS=8`等で並列数を指定できます。ワーカーはサブチャンクのバイト列だけを受け取り、計算を分担します。LevelDBへの書き込みは親プロセスのみが行います。キューを並列数の2倍に制限してメモリー使用量を抑え、出力順を維持します。`MAPCRAFT_GEOLOGY_CACHE`で共有キャッシュの絶対パスを指定できます。単一と複数プロセスのネイティブDB内容の一致を検証しています。

家具の後処理には`parallel_furniture.ensure_parallel_furniture`を利用できます。通常の書き込み所有者が終了し、プロジェクトと無人生成コントローラーの両ロックを取得したプロセスから呼び出してください。実行中のLevelDBを別プロセスで開かないでください。

ワーカーは不変のSQLiteスナップショットを読み、区画ごとの独立した差分DBに変更レコードと読み込み時のハッシュを保存します。親は区画順に検証し、隣接区画の変更と競合する結果だけを再計算します。計算結果を待つ前に次の仕事を投入し、境界調整の間もワーカーを稼働させます。範囲検索は`cx`を等値指定し、`cz`の範囲を複合索引で絞り込みます。両軸を一度に範囲指定する全列走査を避けています。

`workers=16`等でCPU並列数（1〜32）を指定できます。`rebuild=True`では元DBを変更せず、全レコードと差分をキー順に新しいBedrock DBへ書き出します。設置前に全レコードのハッシュと件数を検証し、元DBを`backups`に保持して切り替えます。未知のレコードも複写し、宝箱の内容や既存のブロックエンティティを保持します。`.ldb`ファイルをそのまま混ぜて結合する方式ではありません。

`work_dir`にプロジェクト内の`.parallel-furniture-*`ディレクトリーを指定すると、途中停止後も区画ごとの計算結果を保持できます。再試行時は同じ作業先と、その中の`session.json`を読んだ`snapshot_hint`を渡します。元DBのSST・非空WALが一致しない場合はスナップショットと差分キャッシュを無効にし、現在のDBから作り直します。スナップショットだけで約85GBとなる事例があり、一時領域・元DBのバックアップ・完成ファイルを含めた容量確保が必要です。配置ルールを変更する際には差分キャッシュの識別バージョンも更新してください。途中停止、競合区画、チェストの再補充防止、切替失敗時の復元をネイティブDBで検証しています。

地形を新規生成する経路でも、Arnisの出力は区画ごとに独立しています。ただし現状の`run_tiles`は生成から結合までを順番に行います。地形の地区別並列生成は別途必要であり、家具後処理の並列化だけで新規地形まで並列になるわけではありません。

0.16のCPU並列数は既定で論理CPU数の半分、最大8です。`MAPCRAFT_COMPUTE_BACKEND`は`auto`（既定）・`cpu`・`gpu`を指定でき、保存されたプロジェクトの`geology.backend`があればそちらを優先します。`auto`はプロセス内の最初のサンプルで両経路をウォームアップし、保存バイト列の一致と転送・パレット変換を含む時間を比較します。GPUが10%以上速い場合に使用し、結果の不一致や初期化・途中のGPUエラー時は未出力のバッチからCPUで再計算します。ワールドへの書き込みは常にCPUの1プロセスです。

GPU経路でもパレット変換はCPUプールで分担します。GPUコンテキストは親プロセスだけが持ち、子プロセスへ渡すのは通常の配列とバイト列だけです。1回のGPUバッチは最大1,024サブチャンクに制限します。

`gpu_driver.py`はシステムの`nvcuda.dll`を遅延ロードします。独自カーネル`geology_gpu.cu`から生成した`geology_gpu.ptx`だけを同梱し、CUDA DLLやコンパイラー、CuPy等は配布しません。PTXはNVRTC 12.4.127、`compute_52`・C++11・`--fmad=false`で生成しています。再生成には開発者が用意したNVRTCを指定して`python scripts/compile_gpu_kernel.py --nvrtc-bin <binフォルダー>`を使います。整数演算、負座標、既存鉱石・空気・副ストレージの保持を検証しています。実GPUのテストは`MAPCRAFT_TEST_GPU=1`を設定して実行します。通常のテストはGPUなしで実行できます。

APIの一次資料：[CUDA Driver API](https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/cuda_8h_source.html)、[CUDA Best Practices](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/)。

## 検証の範囲

リポジトリのテストは、決定的な配置選択、家具設定の独立性、座標変換、HUD比率、未作成索引を作ってしまわないことを検証します。開発中には、小区画の実エンジン生成、地下鉄・地盤の保持、無人処理から出力まで、JavaScriptの配置・再接続も検証しています。

これらはMinecraftの実画面での受け入れテストの代わりにはなりません。公開前には、配布ZIPを別の空フォルダーへ展開して起動し、小範囲生成、world/add-onの取り込み、ミニマップ、家具、再試行を実ゲームで確認してください。大規模な完成ワールドのゲーム動作は未検証です。

事前配置はネイティブLevelDBの読み戻し、チェストの中身、チャンク境界をまたぐベッド、再試行で補充しないこと、実エンジンから全国座標への結合を検証しています。就寝・チェスト開閉・マルチ参加の実ゲーム検証は未実施です。`engine\national-runtime\python.exe -m unittest discover -s tests -v`でネイティブテストも実行できます。

旧版の無人生成を止めずに最後だけ新形式へ変換する場合は、新ソースの別コピーから同梱Pythonで`scripts/finalize_furniture.py --config <旧処理の絶対パス設定JSON>`を実行します。旧処理の完了と両方のロック解放を待ち、生成者の元DBに家具を追加して再出力します。二重に巨大な出力を保持しないため、同じプロジェクトIDを検証した古い完成アーカイブを置き換えます。プレイ済みワールドには使用しないでください。待機処理はWindowsサービスではありません。

## 完成版を別保存してから同じプロジェクトを拡張

全国座標のナビゲーションパックでは、`national_navigation.py`が生成済み区画と交差する市町村境界だけを切り出し、到着判定用の輪郭を約1mの許容差で簡略化します。境界のID・穴・離れた島を保持し、実地図の画像やゲーム内地形は変更しません。全国分の境界約119MBをそのままJavaScriptの静的importへ渡すと、標準Bedrockサーバーで`InternalError: out of memory`となり、到着通知と同じエントリーから読み込むミニマップも起動できません。23区の実データでは42自治体・約376KBとなり、同じサーバーで正常起動を確認しました。既存完成版のパック修正は0.19.3、以降の地域出力はパック改訂番号4以上とし、修正版のキャッシュと衝突しないようにします。

`scripts/continue_region.py --request <request.json>`は、指定フェーズの家具・海底・村人・動物・ボス・ビーコンと完成出力を待ちます。作成用とコントローラーのロックを取得し、アーカイブのID・区画数・反映設定を検証してから、`base/完成ワールド`へ物理コピーします。SHA-256、ZIP内全ファイルのCRC、完了メタデータを確認した後で、同じ作成用プロジェクトの次フェーズを有効にします。既存の保存先は上書きせず、内容が違う場合は連番を付けます。

要求JSONには`base`、`project`、固定座標の`map_id/projection/origin/scale/tile`、`from_phase/from_completed`、次の`phase/title/filename`、`snapshot`、`settings`、`plan/plan_sha256`、`wait_states`、完成版に要求する`mobs/villagers/bathymetry`設定、`workers`、`budget_bytes`、一意な`request_id`を指定します。`--once`は待機条件を1回だけ確認します。完了済みならコピーと拡張まで実行するため、本番での単なる診断には完成前に使用してください。

再試行では保存済みコピーを再検証し、生成済み区画を維持します。容量上限には作成データ、別保存した完成版、新たな完成出力の予約領域を含めます。停止要求は対象フェーズの出力用停止だけを解除し、ユーザーの別の停止要求は保持します。既にインストールしたプレイ用ワールドを開きません。プレイ側の建築や持ち物は、後で作成側へ自動統合されません。Windowsの再起動後には同じ要求ファイルで再実行が必要です。

海底補正の区画被覆ハッシュは完成マーカーの再利用判定に使います。拡張後は新しい区画も補正しますが、チャンク別ジャーナルの設定識別子は変えず、既に補正したチャンクを再加工しません。

陸地と沿岸だけを区画選択すると、離島間の外洋は未生成になります。元のArnisの`TheVoid`設定では、その部分は海ではなく空間です。連続した海を必要とする場合には海域の生成または別の補完が必要です。

継続要求に`"ocean":{"enabled":true,"depth":60}`を指定すると、23区版を別保存した後、拡張側のプロジェクトに簡易海の設定を保存します。次の完成出力時に`ocean_backend.py`が`FlatWorldLayers`を更新し、Minecraft標準の生成器で未生成チャンクに一定水深の海を作ります。水面はプロジェクトの`ground_level`（現在64）、海底は砂・砂利・石、最下層はY=-64の岩盤です。既存のDBレコード・スポーン・シード・座標は変更しません。未知の生成設定や通常の無限ワールドには適用せず、旧メタデータをバックアップします。

簡易海は実測海底地形ではありません。選択範囲外の未生成の陸地位置も海になるため、対象範囲は完成物内の`OCEAN-README.txt`と`mapcraft_ocean.json`に明示します。実地形として追加する場合は作成側で新しい区画を生成してください。プレイ側で訪問した海は必要に応じて保存され、探索・建築に応じて容量が増えます。単に生成設定を加える段階では広い外洋全体のDBを事前作成しません。

## 配布EXEの診断

展開したフォルダーで `MapCraft.exe --self-check report.json` を実行すると、画面を開かず、同梱カタログ・HUD・エンジンのハッシュ・Tk・隔離ランタイムの読み込み結果をJSONへ出力します。実ゲームの表示確認とは別のチェックです。
