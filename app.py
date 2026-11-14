"""MapCraft: search anywhere in Japan and select a Minecraft generation area."""
import base64
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import urllib.request
import webbrowser
from core import (ENGINE_SHA256, SCALES, MODES, pixel, latlon, validate_bbox,
                  dimensions, square, command, find_worlds)
from geocoder import Geocoder, USER_AGENT
from tracking import MapReference, load_reference
from nativepack import write_addon, embed_native_pack
from jobs import GenerationJob,load_job,list_jobs,ensure_legacy_idle,normalized_command,STAGES
from jobcache import atomic_json
from version import VERSION

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
ENGINE = BASE/'engine/arnis-windows.exe'
BG, CARD, FG, MUTED, ACCENT = '#101b26', '#192938', '#ecf4f8', '#9db2c2', '#72dfb6'
UA = USER_AGENT
JAPAN_VIEW = (24., 123., 46., 146.)

class App:
    def __init__(self, root):
        self.root = root
        root.title('MapCraft '+VERSION+' — 日本の街並みをMinecraftへ')
        root.geometry('1260x850'); root.minsize(1080, 770); root.configure(bg=BG)
        self.bbox = None; self.zoom = 4; self.center = (35.,134.5)
        self.geocoder=Geocoder(BASE);self.search_results=[];self.search_place=None;self.searching=False
        self.events = queue.Queue(); self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.tiles = {}; self.pending = set(); self.failed = {}; self.images = {}
        self.process = None; self.running = False; self.cancelled = threading.Event()
        self.job_stage=''
        self.last_folder = None; self.last_world = None; self.job_edition = 'bedrock'; self.importing = False
        self.navigator=None;self.navigation_source=None
        self.national_window=None
        self.status_window=None
        self.resize_timer = None; self.drag_start = None; self.current_view = None
        self.status = tk.StringVar(value='地名・駅名・住所を検索して、地図上で生成範囲を指定してください')
        self.search_query=tk.StringVar();self.search_choice=tk.StringVar()
        self.search_hint=tk.StringVar(value='日本国内を検索 · 地名・駅名・住所を入力してEnter')
        self.edition = tk.StringVar(value='Windows統合版'); self.scale = tk.StringVar(value=next(iter(SCALES)))
        self.mode = tk.StringVar(value=next(iter(MODES))); self.height = tk.StringVar(value='1.0')
        self.vanilla = tk.BooleanVar(value=True); self.overture = tk.BooleanVar(value=True)
        self.trees = tk.BooleanVar(value=True); self.interaction = tk.StringVar(value='select')
        self.fillground=tk.BooleanVar(value=True);self.geology_enabled=tk.BooleanVar(value=True);self.gpu_enabled=tk.BooleanVar(value=True);self.include_navigation=tk.BooleanVar(value=False)
        self.treasure_enabled=tk.BooleanVar(value=True);self.treasure_probability=tk.StringVar(value='25')
        self.beds_enabled=tk.BooleanVar(value=True);self.beds_probability=tk.StringVar(value='25')
        self.villagers_enabled=tk.BooleanVar(value=True);self.villagers_ratio=tk.StringVar(value='250');self.villagers_outdoors=tk.BooleanVar(value=True)
        self.wildlife_enabled=tk.BooleanVar(value=True);self.wildlife_density=tk.StringVar(value='200');self.bosses_enabled=tk.BooleanVar(value=False);self.boss_beacons_enabled=tk.BooleanVar(value=True)
        self.bathymetry_enabled=tk.BooleanVar(value=True)
        self.output = tk.StringVar(value=str(BASE/'worlds')); self.coordinates = tk.StringVar()
        self.style(); self.layout(); self.update_selection()
        root.after(200, self.fit); root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def style(self):
        self.root.option_add('*Font', ('Yu Gothic UI', 10))
        s = ttk.Style(); s.theme_use('clam')
        s.configure('.', background=CARD, foreground=FG, font=('Yu Gothic UI', 10))
        s.configure('TFrame', background=BG); s.configure('Card.TFrame', background=CARD)
        s.configure('TLabel', background=CARD, foreground=FG)
        s.configure('Muted.TLabel', foreground=MUTED)
        s.configure('TButton', padding=(10,7), background='#2b4153', foreground=FG, borderwidth=0)
        s.map('TButton', background=[('active','#3c586b'),('disabled','#22313c')])
        s.configure('Accent.TButton', background=ACCENT, foreground='#082b22', font=('Yu Gothic UI',11,'bold'))
        s.map('Accent.TButton', background=[('active','#a0efd3'),('disabled','#3e6056')])
        s.configure('TCombobox', fieldbackground='#263c4c', background='#263c4c', foreground=FG, padding=4)
        s.map('TCombobox', fieldbackground=[('readonly','#263c4c')], foreground=[('readonly',FG)])
        s.configure('TEntry', fieldbackground='#263c4c', foreground=FG, padding=5)
        s.configure('TSpinbox',fieldbackground='#263c4c',foreground=FG,
                    insertcolor=FG,selectbackground=ACCENT,selectforeground=BG,
                    background='#2b4153',arrowcolor=FG,padding=5)
        s.map('TSpinbox',fieldbackground=[('disabled','#1e2c39'),('!disabled','#263c4c')],
              foreground=[('disabled',MUTED),('!disabled',FG)],
              arrowcolor=[('disabled',MUTED),('!disabled',FG)])
        s.configure('TCheckbutton', background=CARD, foreground=FG)
        s.map('TCheckbutton', background=[('active',CARD)])
        s.configure('TRadiobutton', background=CARD, foreground=FG)
        s.map('TRadiobutton', background=[('active',CARD)])

    def layout(self):
        top = tk.Frame(self.root, bg=BG); top.pack(fill='x', padx=24, pady=(17,12))
        tk.Label(top, text='MAP CRAFT', font=('Segoe UI',23,'bold'), fg=FG,bg=BG).pack(side='left')
        tk.Label(top, text='日本の街並みを検索して、ワールドへ。', fg=MUTED,bg=BG).pack(side='left',padx=20)
        ttk.Button(top,text='生成状況・一時停止・再開…',command=self.open_generation_status).pack(side='right')
        main=ttk.Frame(self.root); main.pack(fill='both',expand=True,padx=20)
        side=ttk.Frame(main,style='Card.TFrame',padding=16,width=340); side.pack(side='left',fill='y',padx=(0,14)); side.pack_propagate(False)
        self.label(side,'出力設定',bold=True)
        self.label(side,'検索 → 範囲指定 → ワールド生成',muted=True,pady=(5,10))
        self.summary=tk.Label(side,bg=CARD,fg=ACCENT,justify='left',anchor='w',font=('Yu Gothic UI',12,'bold'))
        self.summary.pack(fill='x',pady=(3,10))
        self.combo(side,'Minecraft',self.edition,['Java版','Windows統合版'])
        self.combo(side,'縮尺',self.scale,list(SCALES))
        self.combo(side,'生成するもの',self.mode,list(MODES))
        ttk.Button(side,text='詳細設定…',command=self.advanced).pack(fill='x',pady=(9,2))
        ttk.Button(side,text='全国・都道府県・地方から出力…',command=self.open_national).pack(fill='x',pady=(4,2))
        self.label(side,'標準設定：山を通常の高さに圧縮。\n建物補完・実際の樹木データは有効。',muted=True,pady=4)
        self.label(side,'保存先',bold=True,pady=(12,4))
        dest=ttk.Frame(side,style='Card.TFrame');dest.pack(fill='x')
        ttk.Entry(dest,textvariable=self.output,width=22).pack(side='left',fill='x',expand=True)
        ttk.Button(dest,text='…',width=3,command=self.choose_output).pack(side='right',padx=(4,0))
        self.generate_button=ttk.Button(side,text='ワールドを生成',style='Accent.TButton',command=self.generate)
        self.generate_button.pack(fill='x',pady=(13,5))
        actions=ttk.Frame(side,style='Card.TFrame');actions.pack(fill='x')
        self.cancel_button=ttk.Button(actions,text='生成を中止',command=self.cancel,state='disabled');self.cancel_button.pack(side='left',fill='x',expand=True,padx=(0,4))
        self.retry_button=ttk.Button(actions,text='履歴・再試行…',command=self.retry_history);self.retry_button.pack(side='right',fill='x',expand=True)
        self.open_button=ttk.Button(side,text='完成ワールドを開く / 取り込む',command=self.open_world,state='disabled')
        self.open_button.pack(fill='x',pady=(5,0))
        navigation_row=ttk.Frame(side,style='Card.TFrame');navigation_row.pack(fill='x',pady=(12,0))
        self.native_button=ttk.Button(navigation_row,text='ゲーム内の実地図を取り込む',command=self.open_navigation)
        self.native_button.pack(side='left',fill='x',expand=True)
        ttk.Button(navigation_row,text='対象…',width=5,command=lambda:self.open_navigation(choose=True)).pack(side='right',padx=(4,0))
        self.label(side,'Windows統合版：現実の地図を右上へ',muted=True,pady=3)
        right=ttk.Frame(main);right.pack(side='left',fill='both',expand=True)
        search=ttk.Frame(right,style='Card.TFrame',padding=10);search.pack(fill='x',pady=(0,8))
        self.label(search,'日本の地名・駅名・住所を検索',bold=True,pady=(0,5))
        entryrow=ttk.Frame(search,style='Card.TFrame');entryrow.pack(fill='x')
        self.search_entry=ttk.Entry(entryrow,textvariable=self.search_query)
        self.search_entry.pack(side='left',fill='x',expand=True);self.search_entry.bind('<Return>',lambda e:self.search())
        self.search_button=ttk.Button(entryrow,text='検索',command=self.search);self.search_button.pack(side='right',padx=(8,0))
        self.results_combo=ttk.Combobox(search,textvariable=self.search_choice,values=[],state='disabled')
        self.results_combo.pack(fill='x',pady=(7,0));self.results_combo.bind('<<ComboboxSelected>>',lambda e:self.choose_result())
        ttk.Label(search,textvariable=self.search_hint,style='Muted.TLabel',font=('Yu Gothic UI',9)).pack(anchor='w',pady=(5,0))
        tools=ttk.Frame(right,style='Card.TFrame',padding=8);tools.pack(fill='x',pady=(0,8))
        ttk.Radiobutton(tools,text='地図を移動',variable=self.interaction,value='pan').pack(side='left',padx=5)
        ttk.Radiobutton(tools,text='範囲を描く',variable=self.interaction,value='select').pack(side='left',padx=5)
        ttk.Button(tools,text='＋',width=3,command=lambda:self.change_zoom(1)).pack(side='right',padx=2)
        ttk.Button(tools,text='−',width=3,command=lambda:self.change_zoom(-1)).pack(side='right',padx=2)
        ttk.Button(tools,text='範囲に合わせる',command=self.fit).pack(side='right',padx=4)
        area_tools=ttk.Frame(right,style='Card.TFrame',padding=(8,5));area_tools.pack(fill='x',pady=(0,8))
        self.use_search_button=ttk.Button(area_tools,text='検索結果の範囲を使用',command=self.use_search_area,state='disabled')
        self.use_search_button.pack(side='left',padx=(0,5))
        ttk.Button(area_tools,text='表示範囲を使用',command=self.use_view).pack(side='left',padx=3)
        ttk.Button(area_tools,text='選択を解除',command=self.clear_selection).pack(side='right')
        self.canvas=tk.Canvas(right,bg='#233946',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self.resize);self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.drag);self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<MouseWheel>',lambda e:self.change_zoom(1 if e.delta>0 else -1))
        credit=tk.Label(right,text='© OpenStreetMap contributors · ODbL  |  地名検索：Photon  |  緑：生成範囲',bg=BG,fg=MUTED,cursor='hand2',anchor='w')
        credit.pack(fill='x',pady=4);credit.bind('<Button-1>',lambda e:webbrowser.open('https://www.openstreetmap.org/copyright'))
        coords=ttk.Frame(right);coords.pack(fill='x',pady=4)
        ttk.Entry(coords,textvariable=self.coordinates).pack(side='left',fill='x',expand=True)
        ttk.Button(coords,text='座標を反映',command=self.apply_coords).pack(side='right',padx=(7,0))
        self.log=tk.Text(right,height=6,bg='#0b141d',fg=MUTED,relief='flat',font=('Consolas',9),wrap='word',state='disabled')
        self.log.pack(fill='x',pady=(5,0))
        bar=tk.Frame(self.root,bg=BG);bar.pack(fill='x',padx=22,pady=(10,12))
        tk.Label(bar,textvariable=self.status,bg=BG,fg=FG,anchor='w').pack(fill='x')
        self.scale.trace_add('write',lambda *_:self.update_selection())
        self.write('地名・駅名・住所を検索し、候補を選んで地図へ移動します。\n「範囲を描く」でドラッグ、または「検索結果の範囲を使用」で範囲を指定できます。\n')

    def label(self,parent,text,bold=False,muted=False,pady=0):
        ttk.Label(parent,text=text,style='Muted.TLabel' if muted else 'TLabel',
                  font=('Yu Gothic UI',10,'bold' if bold else 'normal')).pack(anchor='w',pady=pady)

    def combo(self,parent,label,var,values):
        ttk.Label(parent,text=label,style='Muted.TLabel',font=('Yu Gothic UI',9)).pack(anchor='w',pady=(3,1))
        ttk.Combobox(parent,textvariable=var,values=values,state='readonly').pack(fill='x')

    def advanced(self):
        dialog=tk.Toplevel(self.root);dialog.title('詳細設定');dialog.configure(bg=CARD);dialog.transient(self.root)
        dialog.geometry(f'610x{min(900,self.root.winfo_screenheight()-130)}');dialog.minsize(580,500)
        scrolling=ttk.Frame(dialog,style='Card.TFrame');scrolling.pack(fill='both',expand=True)
        canvas=tk.Canvas(scrolling,bg=CARD,highlightthickness=0)
        scrollbar=ttk.Scrollbar(scrolling,orient='vertical',command=canvas.yview);scrollbar.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True);canvas.configure(yscrollcommand=scrollbar.set)
        pane=ttk.Frame(canvas,style='Card.TFrame',padding=22);content=canvas.create_window((0,0),window=pane,anchor='nw')
        pane.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(content,width=event.width))
        self.combo(pane,'地形の高さ倍率',self.height,['1.0','0.5','0.25','0.1'])
        ttk.Checkbutton(pane,text='通常の高さに収める（山は圧縮）',variable=self.vanilla).pack(anchor='w',pady=(14,4))
        self.label(pane,'オフにするとArnisが必要に応じて高さ拡張パックを追加。\nJava版は実験的機能、統合版は1.21.120以降が必要です。',muted=True)
        ttk.Checkbutton(pane,text='不足する建物をOvertureで補完',variable=self.overture).pack(anchor='w',pady=(12,4))
        ttk.Checkbutton(pane,text='実際の樹木データを使用',variable=self.trees).pack(anchor='w',pady=4)
        ttk.Checkbutton(pane,text='地中を埋める（地下の大空洞を作らない）',variable=self.fillground).pack(anchor='w',pady=(12,4))
        self.label(pane,'標準でオン。基準の地面Y=64から地中を石・鉱石で埋めます。\n地下鉄トンネルは地図に登録された路線から生成します。\n生成済みのワールドへの反映には再生成が必要です。',muted=True)
        ttk.Checkbutton(pane,text='公的地質・人工地形を地下に反映（Windows統合版）',variable=self.geology_enabled).pack(anchor='w',pady=(5,2))
        self.label(pane,'地中を埋める設定と併用。Y=1〜56の石を地質に応じて変更。\n深さ・鉱石の分布は冒険向けのゲーム用モデルです。',muted=True)
        self.label(pane,'活火山の近くでは地下に小さなマグマだまりを生成し、\n地下の鉱石と宝箱の鉱物が増えます（ゲーム用の近似）。',muted=True)
        ttk.Checkbutton(pane,text='対応GPUで地盤計算を高速化（自動判定）',variable=self.gpu_enabled).pack(anchor='w',pady=(8,2))
        self.label(pane,'速度を比較して速い方式を使用。GPU未対応・利用不可・\nCPUの方が速い場合は、CPUで処理します。',muted=True)
        ttk.Separator(pane).pack(fill='x',pady=12)
        ttk.Checkbutton(pane,text='実地図ミニマップ・到着通知を同梱する',variable=self.include_navigation).pack(anchor='w',pady=(4,2))
        self.label(pane,'オフでも宝箱・ベッド・村人・動物・ボスを利用できます。\n通常の高さなら追加パックなしのマップを出力します。',muted=True)
        self.label(pane,'地域の宝箱（Windows統合版）',bold=True)
        probability=ttk.Spinbox(pane,from_=0,to=100,increment=1,textvariable=self.treasure_probability,width=8)
        def treasure_changed():probability.configure(state='normal' if self.treasure_enabled.get() else 'disabled')
        ttk.Checkbutton(pane,text='生成した建物にチェストを配置する',variable=self.treasure_enabled,command=treasure_changed).pack(anchor='w',pady=(8,4))
        self.label(pane,'各フロアの配置確率（%・0〜100）',muted=True)
        probability.pack(anchor='w');treasure_changed()
        self.label(pane,'初期値25%。各階を独立に抽選し、当選した階へ最大1箱。\n商店街は食料、農地は作物、工業用地は道具が中心です。\n空きの屋内だけに配置し、既存のチェストは変更しません。',muted=True,pady=5)
        ttk.Separator(pane).pack(fill='x',pady=10)
        self.label(pane,'ランダムなベッド（Windows統合版）',bold=True)
        bed_probability=ttk.Spinbox(pane,from_=0,to=100,increment=1,textvariable=self.beds_probability,width=8)
        def beds_changed():bed_probability.configure(state='normal' if self.beds_enabled.get() else 'disabled')
        ttk.Checkbutton(pane,text='生成した建物にベッドを配置する',variable=self.beds_enabled,command=beds_changed).pack(anchor='w',pady=(7,4))
        self.label(pane,'建物ごとの配置確率（%・0〜100）',muted=True)
        bed_probability.pack(anchor='w');beds_changed()
        self.label(pane,'初期値25%。チェストとは別に抽選します。\n2マス分の空きがある室内に1台。取得・破壊後は補充しません。',muted=True,pady=5)
        ttk.Separator(pane).pack(fill='x',pady=10)
        self.label(pane,'人口に応じた住民（Windows統合版）',bold=True)
        resident_ratio=ttk.Spinbox(pane,from_=100,to=100000,increment=100,textvariable=self.villagers_ratio,width=10)
        def residents_changed():resident_ratio.configure(state='normal' if self.villagers_enabled.get() else 'disabled')
        ttk.Checkbutton(pane,text='住宅内に村人を生成する',variable=self.villagers_enabled,command=residents_changed).pack(anchor='w',pady=(7,4))
        self.label(pane,'実人口何人につき村人1人（小さいほど多く生成）',muted=True)
        resident_ratio.pack(anchor='w');residents_changed()
        ttk.Checkbutton(pane,text='住宅周辺の屋外にも村人を配置する',variable=self.villagers_outdoors).pack(anchor='w',pady=4)
        self.label(pane,'標準オン、実人口250人につき1人。小さいほど増加。\n2026年1月の市区町村人口を生成面積で按分します。\n屋内は低い階を優先。屋外は住宅周辺の安全な地面。\n通常の村人なので表示用アドオンなしでも遊べます。',muted=True,pady=5)
        ttk.Separator(pane).pack(fill='x',pady=10)
        self.label(pane,'動物の生息地とボス（Windows統合版）',bold=True)
        ttk.Checkbutton(pane,text='海底地形を実データで補正する（Windows統合版）',variable=self.bathymetry_enabled).pack(anchor='w',pady=4)
        self.label(pane,'NOAAの約400〜500mメッシュを補間。標準オン。\n既存の海水域だけを補正し、陸地・建物・地下設備を保護。\n深海は高さ制限に合わせ最大60ブロックに制限します。',muted=True,pady=5)
        density=ttk.Spinbox(pane,from_=0,to=300,increment=25,textvariable=self.wildlife_density,width=8)
        def wildlife_changed():density.configure(state='normal' if self.wildlife_enabled.get() else 'disabled')
        ttk.Checkbutton(pane,text='環境に合った動物を出現させる（標準オン）',variable=self.wildlife_enabled,command=wildlife_changed).pack(anchor='w',pady=(7,4))
        self.label(pane,'動物の多さ（%・標準200、0〜300）',muted=True);density.pack(anchor='w');wildlife_changed()
        self.label(pane,'海辺はカメ、山はヤギやウサギ、森林はキツネなど。\n生成時に足場・水深・空きを確認して通常モブを保存。\n追加MOD・パック不要。独自の再出現処理はありません。',muted=True,pady=5)
        beacon_option=ttk.Checkbutton(pane,text='ボスの近くにビーコンを設置する（標準オン）',variable=self.boss_beacons_enabled)
        def bosses_changed():beacon_option.configure(state='normal' if self.bosses_enabled.get() else 'disabled')
        ttk.Checkbutton(pane,text='神社・寺院・墓地にボスと攻略宝箱を配置する',variable=self.bosses_enabled,command=bosses_changed).pack(anchor='w',pady=(7,4))
        beacon_option.pack(anchor='w',padx=(18,0),pady=(0,4));bosses_changed()
        self.label(pane,'ビーコンは鉄の台座付き。空が開けた場所に配置し、\n建物・宝箱と重なる場合は省略します。追加MOD不要。\n討伐後も残ります。破壊後の再設置はありません。',muted=True,pady=5)
        self.label(pane,'ボスは標準オフ。面積に応じブルート、エルダー、\nウォーデン、ウィザーへ段階的に強化します。\n最大規模はエンドへの入口と宝箱を設置。\nドラゴンは通常のエンドで戦います。\nウィザー等の攻撃・地形破壊は通常の挙動です。\n討伐後の自動復活や宝箱の補充はありません。',muted=True,pady=5)
        ttk.Button(dialog,text='閉じる',command=dialog.destroy).pack(fill='x',padx=22,pady=(8,16))
        def bind_scroll(widget):
            widget.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'),add='+')
            for child in widget.winfo_children():bind_scroll(child)
        bind_scroll(pane)

    def open_national(self):
        if self.running:messagebox.showinfo('生成中です','現在の範囲の生成完了後に全国モードを開いてください。');return
        if self.national_window:
            self.national_window.window.lift();return
        from national_ui import NationalWindow
        self.national_window=NationalWindow(self,BASE)

    def open_generation_status(self):
        if self.status_window:self.status_window.window.lift();return
        from generation_status_ui import GenerationStatusWindow
        self.status_window=GenerationStatusWindow(self,BASE)

    def treasure_options(self):
        try:probability=float(self.treasure_probability.get())
        except ValueError:raise ValueError('チェスト配置確率は0〜100の数値で入力してください。')
        if not math.isfinite(probability) or not 0<=probability<=100:
            raise ValueError('チェスト配置確率は0〜100%で指定してください。')
        return {'enabled':self.treasure_enabled.get(),'probability':probability/100}

    def bed_options(self):
        try:probability=float(self.beds_probability.get())
        except ValueError:raise ValueError('ベッド配置確率は0〜100の数値で入力してください。')
        if not math.isfinite(probability) or not 0<=probability<=100:
            raise ValueError('ベッド配置確率は0〜100%で指定してください。')
        return {'enabled':self.beds_enabled.get(),'probability':probability/100}

    def villager_options(self):
        from villagers import options
        try:ratio=float(self.villagers_ratio.get())
        except ValueError:raise ValueError('村人の人数比率を数値で入力してください。')
        return options({'enabled':self.villagers_enabled.get(),'people_per_villager':ratio,'outdoors_enabled':self.villagers_outdoors.get()})

    def mob_options(self):
        from ecology import options
        try:density=float(self.wildlife_density.get())/100
        except ValueError:raise ValueError('動物の多さを0〜300%で入力してください。')
        return options({'wildlife_enabled':self.wildlife_enabled.get(),'density':density,'bosses_enabled':self.bosses_enabled.get(),'boss_beacons_enabled':self.boss_beacons_enabled.get()})

    def search(self):
        if self.searching:return
        query=self.search_query.get().strip()
        if not query:
            self.search_hint.set('地名・駅名・住所を入力してください。');self.search_entry.focus_set();return
        if len(query)>200:
            self.search_hint.set('検索文字列は200文字以内にしてください。');return
        self.searching=True;self.search_button.configure(state='disabled')
        self.search_hint.set('検索中…');self.results_combo.configure(state='disabled')
        self.use_search_button.configure(state='disabled')
        self.search_results=[];self.search_place=None;self.search_choice.set('');self.bbox=None;self.update_selection()
        def worker():
            try:self.events.put(('search_results',(query,self.geocoder.search(query))))
            except Exception as e:self.events.put(('search_error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def choose_result(self):
        index=self.results_combo.current()
        if not 0<=index<len(self.search_results):return
        self.search_place=self.search_results[index]
        self.bbox=None;self.update_selection()
        self.fit_bbox(self.search_place['bbox'])
        self.use_search_button.configure(state='normal')
        self.interaction.set('select')
        self.search_hint.set('地図をドラッグして範囲指定できます。候補を変えると選択範囲を解除します。')

    def use_search_area(self):
        if self.search_place:
            self.bbox=tuple(self.search_place['bbox']);self.update_selection();self.fit()

    def use_view(self):
        n,w=self.point(0,0);s,e=self.point(self.canvas.winfo_width(),self.canvas.winfo_height())
        try:self.bbox=validate_bbox((s,w,n,e));self.update_selection()
        except ValueError as exc:messagebox.showerror('範囲を確認',str(exc))

    def clear_selection(self):
        self.bbox=None;self.update_selection()

    def update_selection(self):
        if self.bbox is None:
            self.summary.configure(text='範囲を指定してください\n地図上をドラッグして選択')
            self.coordinates.set('')
            self.generate_button.configure(state='disabled')
            if not self.running and not self.importing:self.status.set('検索して場所を選び、地図上で生成範囲を指定してください')
            self.overlay();return
        w,h,bw,bh=dimensions(self.bbox,SCALES[self.scale.get()])
        self.summary.configure(text=f'{w/1000:.2f} × {h/1000:.2f} km\n約 {bw:,} × {bh:,} ブロック\n横面積 {bw*bh/1e6:.1f}百万ブロック')
        if not self.running and not self.importing:self.generate_button.configure(state='normal' if w*h<=300e6 else 'disabled')
        self.coordinates.set(', '.join(f'{x:.7f}' for x in self.bbox))
        if not self.running and not self.importing:
            self.status.set(f'選択範囲 {w/1000:.2f} × {h/1000:.2f} km · 生成する版と保存先を確認してください')
            if w*h>300e6:self.status.set('広域の生成は「全国・都道府県・地方から出力…」で「いま選択した範囲」を選んでください。')
        if hasattr(self,'canvas'):self.overlay()

    def apply_coords(self):
        try:
            self.bbox=validate_bbox(tuple(float(x.strip()) for x in self.coordinates.get().split(',')))
            self.update_selection();self.fit()
        except ValueError as e:messagebox.showerror('座標を確認',str(e))

    def fit(self):
        self.fit_bbox(self.bbox or (self.search_place['bbox'] if self.search_place else JAPAN_VIEW))

    def fit_bbox(self,bbox):
        s,w,n,e=bbox;self.center=((s+n)/2,(w+e)/2)
        cw,ch=max(200,self.canvas.winfo_width()-75),max(150,self.canvas.winfo_height()-65)
        for z in range(18,3,-1):
            x1,y1=pixel(n,w,z);x2,y2=pixel(s,e,z)
            if x2-x1<=cw and y2-y1<=ch:break
        self.zoom=z;self.redraw()

    def resize(self,event):
        if self.resize_timer:self.root.after_cancel(self.resize_timer)
        self.resize_timer=self.root.after(180,self.redraw)

    def change_zoom(self,amount):
        self.zoom=max(4,min(18,self.zoom+amount));self.redraw()

    def origin(self):
        x,y=pixel(*self.center,self.zoom)
        return x-self.canvas.winfo_width()/2,y-self.canvas.winfo_height()/2

    def xy(self,lat,lon):
        x,y=pixel(lat,lon,self.zoom);ox,oy=self.origin();return x-ox,y-oy

    def point(self,x,y):
        ox,oy=self.origin();return latlon(x+ox,y+oy,self.zoom)

    def press(self,event):
        self.drag_start=(event.x,event.y,self.point(event.x,event.y),self.center)

    def drag(self,event):
        if not self.drag_start:return
        x,y,geo,center=self.drag_start
        if self.interaction.get()=='pan':
            cx,cy=pixel(*center,self.zoom);self.center=latlon(cx-(event.x-x),cy-(event.y-y),self.zoom);self.redraw()
        else:
            lat,lon=self.point(event.x,event.y)
            if abs(lat-geo[0])>1e-7 and abs(lon-geo[1])>1e-7:
                self.bbox=(min(lat,geo[0]),min(lon,geo[1]),max(lat,geo[0]),max(lon,geo[1]));self.update_selection()

    def release(self,event):
        if self.drag_start and self.interaction.get()=='select':
            self.drag(event)
        self.drag_start=None

    def redraw(self):
        self.resize_timer=None;self.canvas.delete('all');self.images={}
        cw,ch=self.canvas.winfo_width(),self.canvas.winfo_height();ox,oy=self.origin();z=self.zoom
        self.current_view=(z,ox,oy,cw,ch)
        for tx in range(math.floor(ox/256),math.floor((ox+cw)/256)+1):
            for ty in range(math.floor(oy/256),math.floor((oy+ch)/256)+1):
                if not 0<=tx<2**z or not 0<=ty<2**z:continue
                key=(z,tx,ty)
                if key in self.tiles:self.draw_tile(key,self.tiles[key])
                else:
                    x,y=tx*256-ox,ty*256-oy
                    tag='slot_'+'_'.join(map(str,key))
                    self.canvas.create_rectangle(x,y,x+256,y+256,outline='#314b5b',tags=tag)
                    self.canvas.create_text(x+128,y+128,text='地図を読み込み中…' if key not in self.failed else '地図を取得できません',fill=MUTED,font=('Yu Gothic UI',9),tags=tag)
                    if key not in self.pending and time.time()-self.failed.get(key,0)>60:
                        self.pending.add(key);self.pool.submit(self.load_tile,key)
        self.overlay()

    def load_tile(self,key):
        try:
            z,x,y=key;cache=BASE/'cache/tiles'/str(z)/str(x)/f'{y}.png'
            if cache.exists() and time.time()-cache.stat().st_mtime<7*86400:data=cache.read_bytes()
            else:
                req=urllib.request.Request(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png',headers={'User-Agent':UA})
                with urllib.request.urlopen(req,timeout=18) as r:data=r.read()
                if not data.startswith(b'\x89PNG'):raise ValueError('invalid map tile')
                cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(data)
            self.events.put(('tile',(key,data)))
        except Exception as e:self.events.put(('tile_error',(key,str(e))))

    def draw_tile(self,key,data):
        if key[0]!=self.zoom:return
        self.canvas.delete('slot_'+'_'.join(map(str,key)))
        ox,oy=self.origin();image=tk.PhotoImage(data=base64.b64encode(data))
        self.images[key]=image
        item=self.canvas.create_image(key[1]*256-ox,key[2]*256-oy,image=image,anchor='nw',tags='tile')
        self.canvas.tag_lower(item)

    def overlay(self):
        self.canvas.delete('overlay')
        if self.bbox:
            s,w,n,e=self.bbox;x1,y1=self.xy(n,w);x2,y2=self.xy(s,e)
            self.canvas.create_rectangle(x1,y1,x2,y2,fill=ACCENT,stipple='gray12',outline=ACCENT,width=3,tags='overlay')
            for x,y in [(x1,y1),(x2,y1),(x1,y2),(x2,y2)]:
                self.canvas.create_rectangle(x-4,y-4,x+4,y+4,fill=ACCENT,outline=BG,tags='overlay')
        if self.search_place:
            x,y=self.xy(self.search_place['lat'],self.search_place['lon'])
            self.canvas.create_oval(x-6,y-6,x+6,y+6,fill='#f3cc72',outline='#102b25',width=2,tags='overlay')
            self.canvas.create_text(x+10,y-14,text=self.search_place['name'][:24],fill='#132d35',font=('Yu Gothic UI',10,'bold'),anchor='w',tags='overlay')
        self.canvas.tag_raise('overlay')

    def choose_output(self):
        path=filedialog.askdirectory(title='新しいワールドの保存先')
        if path:self.output.set(path)

    def open_navigation(self,choose=False):
        if self.running:
            messagebox.showinfo('生成中です','生成が完了または停止してからアドオンを作成してください。');return
        source=self.navigation_source
        if not source and not choose:
            candidates=[]
            worlds=BASE/'worlds'
            if worlds.exists():
                for path in worlds.glob('*/generation.json'):
                    try:
                        data=json.loads(path.read_text(encoding='utf-8'))
                        if data.get('edition')=='bedrock' and data.get('status')=='complete':candidates.append(path)
                    except (OSError,ValueError):pass
            if candidates:source=max(candidates,key=lambda p:p.stat().st_mtime)
        if choose or not source:
            source=filedialog.askopenfilename(title='対象ワールドの generation.json またはGPS付きワールドを選択',
                initialdir=str(self.last_folder or BASE/'worlds'),
                filetypes=[('ワールド・生成情報','*.json *.mcworld *.mcpack *.mcaddon'),('すべて','*.*')])
            if not source:return
        try:
            reference=load_reference(source)
            from dataclasses import replace
            options=self.treasure_options()
            beds=self.bed_options()
            reference=replace(reference,treasure_enabled=options['enabled'],treasure_probability=options['probability'],beds_enabled=beds['enabled'],beds_probability=beds['probability'])
            self.navigation_source=Path(source)
            self.native_button.configure(state='disabled');self.status.set('現実の地図を作成中…')
            def build():
                try:
                    pack=write_addon(reference,BASE/'addons'/f'MapCraftRealMap_{reference.map_id[:8]}.mcaddon')
                    self.events.put(('realmap_ready',(pack,reference.map_id[:8])))
                except Exception as error:self.events.put(('realmap_error',str(error)))
            threading.Thread(target=build,daemon=True).start()
        except Exception as exc:messagebox.showerror('現在地ナビを開けません',str(exc))

    def generate(self):
        if self.national_window and self.national_window.busy:
            messagebox.showinfo('全国モードで処理中です','全国モードの処理が完了するまでお待ちください。');return
        if self.running or self.importing:return
        try:
            if self.bbox is None:raise ValueError('地図上で生成範囲を指定してください。')
            bbox=validate_bbox(self.bbox); scale=SCALES[self.scale.get()]
            treasure=self.treasure_options()
            beds=self.bed_options()
            villagers=self.villager_options()
            mobs=self.mob_options()
            output_text=self.output.get().strip()
            if not output_text:raise ValueError('保存先を指定してください。')
            parent=Path(output_text).expanduser().resolve()
            if not ENGINE.exists():raise ValueError('engine/arnis-windows.exe がありません。元のフォルダー構成で起動してください。')
            w,h,bw,bh=dimensions(bbox,scale)
            if w*h>300e6:raise ValueError('300km²を超える範囲は「全国・都道府県・地方から出力…」の「いま選択した範囲」で分割生成してください。')
            if shutil.disk_usage(parent if parent.exists() else BASE).free<2*1024**3:
                raise ValueError('保存先に2GB以上の空き容量を確保してください。広い範囲ではさらに必要になります。')
            self.job_edition='bedrock' if self.edition.get()=='Windows統合版' else 'java'
            name=time.strftime('MapCraft_%Y%m%d_%H%M%S')+'_'+self.job_edition
            folder=parent/name
            args=command(ENGINE,bbox,folder,edition=self.job_edition,scale=scale,mode=MODES[self.mode.get()],
                         height=float(self.height.get()),vanilla=self.vanilla.get(),overture=self.overture.get(),trees=self.trees.get(),
                         spawn=(self.search_place['lat'],self.search_place['lon']) if self.search_place else None,fillground=self.fillground.get())
            folder.mkdir(parents=True,exist_ok=False)
            self.last_folder=folder;self.last_world=None
            manifest={'app':'MapCraft '+VERSION,'job_schema':1,'engine':'Arnis v3.4.0','bbox':bbox,'scale':scale,'edition':self.job_edition,'fillground':self.fillground.get(),
                      'command':args,'status':'running','data_license':'https://www.openstreetmap.org/copyright',
                      'scope':'User-selected bounding rectangle','treasure':treasure,'beds':beds,'villagers':villagers,'mobs':mobs,'bathymetry':{'enabled':self.bathymetry_enabled.get(),'max_depth':60},'geology':{'enabled':self.geology_enabled.get(),'backend':'auto' if self.gpu_enabled.get() else 'cpu'},'include_navigation':self.include_navigation.get()}
            atomic_json(folder/'generation.json',manifest)
            self.launch_job(folder,manifest)
        except Exception as e:messagebox.showerror('生成を開始できません',str(e))

    def launch_job(self,folder,manifest):
        self.last_folder=Path(folder);self.last_world=None;self.job_edition=manifest['edition']
        self.running=True;self.cancelled.clear();self.started=time.monotonic();self.job_stage='保存済みデータを確認中'
        self.generate_button.configure(state='disabled');self.cancel_button.configure(state='normal');self.open_button.configure(state='disabled');self.retry_button.configure(state='disabled')
        self.status.set('保存済みデータを確認して生成を開始します…')
        self.write('\n生成先: '+str(folder)+'\n')
        threading.Thread(target=self.run_engine,args=(manifest['command'],Path(folder),manifest),daemon=True).start()

    def retry_folder(self,folder):
        if self.running or self.importing or (self.national_window and self.national_window.busy):
            messagebox.showinfo('処理中です','現在の処理が完了または停止してから再試行してください。');return False
        try:
            folder=Path(folder).resolve();data=load_job(folder);ensure_legacy_idle(data)
            args=normalized_command(data,folder,ENGINE)
            self.bbox=tuple(data['bbox']);self.edition.set('Windows統合版' if data['edition']=='bedrock' else 'Java版')
            self.output.set(str(folder.parent))
            for label,value in SCALES.items():
                if value==data['scale']:self.scale.set(label);break
            for label,value in MODES.items():
                if '--mode' in args and value==args[args.index('--mode')+1]:self.mode.set(label);break
            for flag,var in [('--height-multiplier',self.height),('--overture',self.overture),('--canopy-height',self.trees)]:
                if flag in args:
                    value=args[args.index(flag)+1];var.set(value if var is self.height else value=='true')
            self.vanilla.set('--vanilla-height' in args);self.fillground.set('--fillground' in args);self.geology_enabled.set(data.get('geology',{}).get('enabled',False));self.include_navigation.set(data.get('include_navigation',True))
            self.gpu_enabled.set(data.get('geology',{}).get('backend','auto')!='cpu')
            self.bathymetry_enabled.set(data.get('bathymetry',{}).get('enabled',False))
            self.villagers_enabled.set(data.get('villagers',{}).get('enabled',False));self.villagers_ratio.set(str(data.get('villagers',{}).get('people_per_villager',1000)));self.villagers_outdoors.set(data.get('villagers',{}).get('outdoors_enabled',False))
            mobs=data.get('mobs',{});self.wildlife_enabled.set(mobs.get('wildlife_enabled',False));self.wildlife_density.set(str(mobs.get('density',1)*100));self.bosses_enabled.set(mobs.get('bosses_enabled',False));self.boss_beacons_enabled.set(mobs.get('boss_beacons_enabled',True))
            for kind,enabled,probability in [('treasure',self.treasure_enabled,self.treasure_probability),('beds',self.beds_enabled,self.beds_probability)]:
                enabled.set(data.get(kind,{}).get('enabled',True));probability.set(f"{data.get(kind,{}).get('probability',.25)*100:g}")
            self.update_selection();self.fit()
            self.launch_job(folder,data);return True
        except Exception as error:messagebox.showerror('再試行を開始できません',str(error));return False

    def retry_history(self):
        if self.running or self.importing:return
        dialog=tk.Toplevel(self.root);dialog.title('生成履歴・再試行');dialog.transient(self.root);dialog.configure(bg=CARD)
        dialog.geometry('850x480');pane=ttk.Frame(dialog,style='Card.TFrame',padding=16);pane.pack(fill='both',expand=True)
        self.label(pane,'保存した範囲・設定で、完成済みの工程を再利用して続けます。',bold=True)
        self.label(pane,'旧版の処理中の生成は、元のアプリで停止して閉じてから選択してください。',muted=True,pady=5)
        style=ttk.Style();style.configure('History.Treeview',background='#263c4c',fieldbackground='#263c4c',foreground=FG,rowheight=27)
        tree=ttk.Treeview(pane,columns=('name','status','stage'),show='headings',style='History.Treeview',selectmode='browse')
        for key,label,width in [('name','生成フォルダー',330),('status','状態',90),('stage','最後の工程',260)]:tree.heading(key,text=label);tree.column(key,width=width,stretch=True)
        tree.pack(fill='both',expand=True,pady=8)
        details=tk.StringVar();ttk.Label(pane,textvariable=details,wraplength=800).pack(fill='x',pady=5)
        rows=list_jobs([self.output.get(),BASE/'worlds']);states={'failed':'失敗','cancelled':'中止','running':'処理中・中断','complete':'完了'}
        for i,(folder,data,_) in enumerate(rows):tree.insert('','end',iid=str(i),values=(folder.name,states.get(data.get('status'),'?'),STAGES.get(data.get('stage'),'旧版：保存データを確認')))
        def selected():
            item=tree.selection();return rows[int(item[0])] if item else None
        def changed(_=None):
            row=selected()
            if row:
                folder,data,_=row;error=data.get('error',{}).get('message','')
                details.set(str(folder)+('\n'+error[:350] if error else ''))
        def retry():
            row=selected()
            if row and self.retry_folder(row[0]):dialog.destroy()
        def choose():
            filename=filedialog.askopenfilename(parent=dialog,title='再試行する generation.json を選択',initialdir=self.output.get(),filetypes=[('生成情報','generation.json')])
            if filename and self.retry_folder(Path(filename).parent):dialog.destroy()
        tree.bind('<<TreeviewSelect>>',changed);tree.bind('<Double-1>',lambda _:retry())
        buttons=ttk.Frame(pane,style='Card.TFrame');buttons.pack(fill='x',pady=(8,0))
        ttk.Button(buttons,text='生成情報を選ぶ…',command=choose).pack(side='left')
        ttk.Button(buttons,text='選択した生成を再試行',command=retry,style='Accent.TButton').pack(side='right')
        if rows:
            first=next((i for i,row in enumerate(rows) if row[1].get('status') in ('failed','cancelled')),0)
            tree.selection_set(str(first));tree.see(str(first));changed()
        else:details.set('履歴がありません。「生成情報を選ぶ…」で別の保存先の generation.json を選択できます。')

    def run_engine(self,args,folder,manifest):
        try:
            job=GenerationJob(folder,ENGINE,cancelled=self.cancelled,notify=lambda kind,value:self.events.put((kind,value)),process_changed=lambda process:setattr(self,'process',process))
            result,worlds=job.run()
        except Exception as error:
            result='failed';worlds=[];self.events.put(('log',str(error)+'\n'))
        self.events.put(('done',(result,worlds)))

    def cancel(self):
        self.cancelled.set();self.cancel_button.configure(state='disabled');self.status.set('中止処理中…')
        if self.process and self.process.poll() is None:self.process.terminate()

    def write(self,text):
        self.log.configure(state='normal');self.log.insert('end',text)
        if int(self.log.index('end-1c').split('.')[0])>1200:self.log.delete('1.0','400.0')
        self.log.see('end');self.log.configure(state='disabled')

    def poll(self):
        try:
            for _ in range(100):
                kind,data=self.events.get_nowait()
                if kind=='tile':
                    key,content=data;self.pending.discard(key);self.tiles[key]=content
                    if len(self.tiles)>180:
                        for old in list(self.tiles)[:40]:self.tiles.pop(old,None)
                    self.draw_tile(key,content)
                    if self.navigator:self.navigator.on_tile(key)
                elif kind=='tile_error':
                    key,error=data;self.pending.discard(key);self.failed[key]=time.time()
                    self.write(f'地図タイル {key} の取得失敗: {error}\n');self.redraw()
                elif kind=='log':self.write(data)
                elif kind=='stage':self.job_stage=data
                elif kind=='realmap_ready':
                    pack,short_id=data;self.native_button.configure(state='normal');self.status.set('現実の地図アドオンを作成しました')
                    self.write('現実の地図アドオン: '+str(pack)+'\n');os.startfile(str(pack))
                    messagebox.showinfo('現実の地図アドオン',
                        'Minecraftへ取り込みます。対象ワールドで\n「MapCraft Real Map '+short_id+'」と\n'
                        '「MapCraft Real Map HUD '+short_id+'」を有効にしてください。\n\n'
                        '旧GPS・旧Minimapは無効にしてください。\nプレイ中は外部アプリやログ設定が不要です。')
                elif kind=='realmap_error':
                    self.native_button.configure(state='normal');self.status.set('地図アドオンを作成できませんでした')
                    messagebox.showerror('現実の地図',data)
                elif kind=='search_results':
                    query,results=data;self.searching=False;self.search_button.configure(state='normal')
                    self.search_results=results
                    self.results_combo.configure(values=[f'{i+1}. {r["label"]}' for i,r in enumerate(results)],state='readonly' if results else 'disabled')
                    if results:
                        self.results_combo.current(0);self.choose_result()
                        self.search_hint.set(f'{len(results)}件の候補 · 候補を選び、地図上で範囲を指定してください')
                    else:
                        self.search_choice.set('');self.search_hint.set('見つかりません。都道府県名を付けるか、近くの駅名・地名で検索してください。')
                elif kind=='search_error':
                    self.searching=False;self.search_button.configure(state='normal');self.search_hint.set(data)
                elif kind=='import_done':
                    ok,path=data;self.importing=False;self.open_button.configure(state='normal')
                    self.update_selection()
                    self.status.set('Java版へのコピー完了 · シングルプレイから選択できます' if ok else 'コピー失敗 · ログをご確認ください')
                elif kind=='done':
                    result,worlds=data;self.running=False;self.process=None
                    self.update_selection();self.cancel_button.configure(state='disabled');self.retry_button.configure(state='normal')
                    if result=='complete':
                        self.last_world=worlds[0];self.open_button.configure(state='normal')
                        if self.job_edition=='bedrock':self.navigation_source=self.last_folder/'mapcraft_tracking.json'
                        self.status.set('生成完了 · 「完成ワールドを開く / 取り込む」から遊べます')
                        self.write('\n完成: '+str(self.last_world)+'\n')
                    else:
                        self.status.set('中止しました · 「履歴・再試行」から続けられます' if result=='cancelled' else '生成失敗 · 保存済みデータは「履歴・再試行」から再利用できます')
                        self.write('詳細ログ: '+str(self.last_folder/'generation.log')+'\n')
        except queue.Empty:pass
        if self.running and not self.cancelled.is_set():
            self.status.set(f'{self.job_stage} · 経過 {int(time.monotonic()-self.started)//60}分 · 進捗はログに表示')
        self.root.after(100,self.poll)

    def open_world(self):
        if not self.last_world:return
        if self.last_world.suffix=='.mcworld':os.startfile(str(self.last_world));return
        default=Path(os.environ.get('APPDATA',''))/'.minecraft/saves'
        dest=filedialog.askdirectory(title='Java版の saves フォルダーを選んでください',initialdir=str(default if default.exists() else Path.home()))
        if not dest:return
        target=Path(dest)/self.last_folder.name
        if target.exists():messagebox.showerror('コピー先が存在します','別の保存先を選んでください。既存ワールドには上書きしません。');return
        self.importing=True;self.open_button.configure(state='disabled');self.status.set('Java版のsavesへコピー中…')
        self.generate_button.configure(state='disabled');source=self.last_world
        def copy():
            ok=False
            try:shutil.copytree(source,target);ok=True;self.events.put(('log',f'Java版へのコピー完了: {target}\n'))
            except Exception as e:self.events.put(('log',f'コピー失敗: {e}\n'))
            finally:self.events.put(('import_done',(ok,str(target))))
        # Copy in a worker; keep the UI responsive even for town-size worlds.
        threading.Thread(target=copy,daemon=True).start()

    def close(self):
        if self.status_window and self.status_window.busy:
            messagebox.showinfo('操作中です','一時停止・再開の操作完了までお待ちください。');return
        if self.national_window and self.national_window.busy:
            self.national_window.close();return
        if self.importing:
            messagebox.showinfo('コピー中です','ワールドのコピーが完了するまでお待ちください。');return
        if self.running:
            if not messagebox.askyesno('生成中です','生成を中止して閉じますか？'):return
            self.cancel()
        if self.navigator:self.navigator.close()
        self.pool.shutdown(wait=False,cancel_futures=True);self.root.destroy();os._exit(0)

if __name__=='__main__':
    if '--self-check' in sys.argv:
        report={'version':VERSION,'ok':False}
        destination=Path(sys.argv[sys.argv.index('--self-check')+1])
        try:
            from loot import script as loot_script
            from realmap import frame_bytes
            from map_water import ocean_area
            report['catalogue_loaded']=loot_script().startswith('// Item identifiers:')
            report['hud_loaded']=len(frame_bytes())>0
            from shapely.geometry import Point
            sea=ocean_area([[(5,-1),(5,11)]],(0,0,10,10))
            report['water_cartography_valid']=sea.covers(Point(2,5)) and not sea.covers(Point(8,5))
            from regional_scope import PREFECTURES,REGIONS
            report['regional_menu_valid']=len(PREFECTURES)==47 and sorted(c for _,codes in REGIONS.values() for c in codes)==list(range(1,48))
            import managed_control
            report['generation_controls_valid']=managed_control.process_identity(os.getpid()) is not None
            report['engine_valid']=hashlib.sha256(ENGINE.read_bytes()).hexdigest()==ENGINE_SHA256
            report['gpu_assets_valid']=all((BASE/name).is_file() for name in ('gpu_driver.py','geology_gpu.ptx'))
            hidden=tk.Tk();hidden.withdraw();report['tk_version']=hidden.tk.call('info','patchlevel');hidden.destroy()
            result=subprocess.run([str(BASE/'engine/national-runtime/python.exe'),'-c',
                'import leveldb,amulet_nbt,osmium,shapely,numpy,PIL;print("runtime-ok")'],capture_output=True,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=60)
            report['runtime_valid']=result.returncode==0 and b'runtime-ok' in result.stdout
            report['ok']=all(report[k] for k in ('catalogue_loaded','hud_loaded','water_cartography_valid','regional_menu_valid','generation_controls_valid','engine_valid','runtime_valid','gpu_assets_valid'))
        except Exception as error:report['error']=str(error)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        sys.exit(0 if report['ok'] else 1)
    root=tk.Tk();App(root);root.mainloop()
