#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyForge Player — отдельное приложение для запуска игр, сделанных в редакторе PyForge.

Запуск:   python pyforge_player.py            (откроется меню → «Открыть игру…» → выбираешь .json)
          python pyforge_player.py game.json  (сразу запустить игру)
Зависимости: только стандартный Python с tkinter (Pillow по желанию — для плавных картинок).
Файл собран из того же движка, что и редактор, поэтому игры работают одинаково.
"""
import copy, json, math, os, random, sys, time, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None

# ───────────────────────── данные ─────────────────────────
def new_obj(kind="rect", **kw):
    o = dict(name=kind.capitalize(), kind=kind, x=0, y=0, w=64, h=64, color="#4f8cff",
             text="Text", size=24, image="", body="none", grav=1.0, solid=True, tag="",
             z=0, visible=True, ui=False, prefab=False, scripts=[])
    if kind == "text":
        o.update(w=140, h=32, color="#222222")
    if kind == "empty":
        o.update(w=32, h=32)
    o.update(kw)
    return o

DEFAULTS = new_obj("rect")

def S(code="", name="Script", file="", on=True):
    """Один скрипт-компонент объекта."""
    return dict(name=name, code=code, file=file, on=on)

def blank_project():
    return dict(settings=dict(gravity=1500, bg="#9ad1ff", view_w=960, view_h=540, start="Main"),
                scenes={"Main": []})

def normalize(p):
    p.setdefault("settings", {})
    for k, v in blank_project()["settings"].items():
        p["settings"].setdefault(k, v)
    for sc in p["scenes"].values():
        for o in sc:
            if "scripts" not in o:  # миграция со старого формата: один script/script_file → список скриптов
                o["scripts"] = []
                if o.get("script", "").strip() or o.get("script_file"):
                    o["scripts"].append(S(o.get("script", ""), "Script", o.get("script_file", "")))
            o.pop("script", None); o.pop("script_file", None)
            for k, v in DEFAULTS.items():
                o.setdefault(k, copy.deepcopy(v))
            for x in o["scripts"]:
                x.setdefault("name", "Script"); x.setdefault("code", ""); x.setdefault("file", ""); x.setdefault("on", True)
    return p

# ───────────────────────── физика/коллизии ─────────────────────────
CELL = 128

def hit(a, b):
    return a.x < b.x + b.w and a.x + a.w > b.x and a.y < b.y + b.h and a.y + a.h > b.y

def build_grid(objs):
    g = {}
    for o in objs:
        for i in range(int(o.x // CELL), int((o.x + o.w) // CELL) + 1):
            for j in range(int(o.y // CELL), int((o.y + o.h) // CELL) + 1):
                g.setdefault((i, j), []).append(o)
    return g

def near(g, o):
    out, ids = [], set()
    for i in range(int(o.x // CELL), int((o.x + o.w) // CELL) + 1):
        for j in range(int(o.y // CELL), int((o.y + o.h) // CELL) + 1):
            for b in g.get((i, j), ()):
                if b is not o and id(b) not in ids:
                    ids.add(id(b))
                    out.append(b)
    return out

# ───────────────────────── картинки ─────────────────────────
class Images:
    def __init__(self, base=""):
        self.base, self.raw, self.cache = base, {}, {}

    def path(self, p):
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def clear(self):
        self.raw.clear(); self.cache.clear()

    def get(self, p, w, h):
        if not p:
            return None
        w, h = max(1, int(w)), max(1, int(h))
        key = (p, w, h)
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) > 400:
            self.cache.clear()
        try:
            if Image:
                src = self.raw.get(p) or self.raw.setdefault(p, Image.open(self.path(p)).convert("RGBA"))
                im = ImageTk.PhotoImage(src.resize((w, h), Image.NEAREST))
            else:
                src = self.raw.get(p) or self.raw.setdefault(p, tk.PhotoImage(file=self.path(p)))
                fx, fy = w / src.width(), h / src.height()
                zx, zy = max(1, round(fx)), max(1, round(fy))
                sx = max(1, round(1 / fx)) if fx < 1 else 1
                sy = max(1, round(1 / fy)) if fy < 1 else 1
                im = src.zoom(zx, zy) if (zx > 1 or zy > 1) else src
                if sx > 1 or sy > 1:
                    im = im.subsample(sx, sy)
        except Exception:
            im = None
        self.cache[key] = im
        return im

# ───────────────────────── отрисовка объекта ─────────────────────────
def draw_obj(cv, o, z, ox, oy, imgs):
    x0, y0 = o.x * z + ox, o.y * z + oy
    x1, y1 = (o.x + o.w) * z + ox, (o.y + o.h) * z + oy
    k = o.kind
    if k == "rect":
        cv.create_rectangle(x0, y0, x1, y1, fill=o.color, outline="")
    elif k == "ellipse":
        cv.create_oval(x0, y0, x1, y1, fill=o.color, outline="")
    elif k == "text":
        px = int(o.size * z)
        if px >= 1:
            cv.create_text(x0, y0, text=o.text, anchor="nw", fill=o.color, font=("Arial", -px))
    elif k == "image":
        im = imgs.get(o.image, o.w * z, o.h * z)
        if im:
            cv.create_image(x0, y0, image=im, anchor="nw")
        else:
            cv.create_rectangle(x0, y0, x1, y1, fill="#ddd", outline="#999")

# ───────────────────────── игра (рантайм) ─────────────────────────
def _t(x):
    return x if isinstance(x, (tuple, list)) else (x,)

KEYMAP = {"shift_l": "shift", "shift_r": "shift", "control_l": "ctrl", "control_r": "ctrl",
          "alt_l": "alt", "alt_r": "alt", "return": "enter"}

# Русская раскладка → латинские клавиши (ф→a, ы→s, в→d, ц→w ...), чтобы WASD работали в любой раскладке
_RU = "йцукенгшщзфывапролдячсмить"
_EN = "qwertyuiopasdfghjklzxcvbnm"
_RU_SYM = ("shorti tse u ka ie en ghe sha shcha ze ef yeru ve a pe er o el de ya che es em i te softsign").split()
CYR = dict(zip(_RU, _EN))
CYR_SYM = {"cyrillic_" + n: l for n, l in zip(_RU_SYM, _EN)}

def key_name(e):
    k = e.keysym.lower()
    if k in CYR_SYM:
        return CYR_SYM[k]
    ch = (e.char or "").lower()
    if ch in CYR:
        return CYR[ch]
    return KEYMAP.get(k, k)

class Input:
    def __init__(self):
        self.keys, self.hit = set(), set()
        self.px = self.py = 0
        self.mx = self.my = 0.0
        self.mouse = False
        self.mouse_hit = False

    def down(self, *k):
        return any(x in self.keys for a in k for x in _t(a))

    def pressed(self, *k):
        return any(x in self.hit for a in k for x in _t(a))

    def axis(self, neg, pos):
        return int(self.down(pos)) - int(self.down(neg))

class _NS:
    """Доступ к переменным/функциям другого скрипта: self.script("Health").hp"""
    def __init__(self, d): object.__setattr__(self, "_d", d)
    def __getattr__(self, k):
        try: return self._d[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self._d[k] = v

class GObj:
    def __init__(self, d, game):
        self.__dict__.update(copy.deepcopy(d))
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.alive = True
        self.data = {}
        self.nss, self.names = [], {}
        self.game = game

    @property
    def cx(self): return self.x + self.w / 2
    @cx.setter
    def cx(self, v): self.x = v - self.w / 2
    @property
    def cy(self): return self.y + self.h / 2
    @cy.setter
    def cy(self, v): self.y = v - self.h / 2

    def script(self, name):
        if name not in self.names:
            raise KeyError(f"У объекта «{self.name}» нет включённого скрипта «{name}»")
        return _NS(self.names[name])

    def overlaps(self, o): return hit(self, o)
    def dist(self, o): return math.hypot(self.cx - o.cx, self.cy - o.cy)
    def __repr__(self): return f"<{self.name}>"

class Game:
    def __init__(self, project, scene, inp, out=print, base=""):
        self.p, self.st, self.input, self.out, self.base = project, project["settings"], inp, out, base
        self._setup_paths()
        self.vars, self.time, self.running = {}, 0.0, True
        self.gravity = self.st["gravity"]
        self.tf = (1, 0, 0)
        self.camera_smooth = 8.0
        self.load(scene)

    def _setup_paths(self):
        """Папка проекта (и scripts/) попадает в sys.path — из скриптов работает `import мой_модуль`."""
        if not self.base:
            return
        b = os.path.abspath(self.base)
        for n, m in list(sys.modules.items()):  # свежие версии модулей при каждом запуске
            f = getattr(m, "__file__", None)
            if f and n not in ("__main__", "pyforge") and "site-packages" not in f \
                    and os.path.abspath(f).startswith(b + os.sep):
                del sys.modules[n]
        for d in (b, os.path.join(b, "scripts")):
            if d not in sys.path:
                sys.path.insert(0, d)

    # ----- сцены / объекты
    def load(self, name):
        self.scene, self.objs, self.spawn_q, self.timers = name, [], [], []
        self.camera_follow = None
        self.camera = [self.st["view_w"] / 2, self.st["view_h"] / 2]
        self.pending = None
        self.templates = {}
        for d in self.p["scenes"][name]:
            if d.get("prefab"):
                self.templates[d["name"]] = d
            else:
                self.objs.append(self._make(d))
        self.objs.sort(key=lambda o: o.z)
        for o in list(self.objs):
            self._call(o, "start")

    def _make(self, d):
        o = GObj(d, self)
        for sc in o.scripts:
            if not sc.get("on", True):
                continue
            src, fname = sc.get("code", ""), f"<{o.name}:{sc['name']}>"
            if sc.get("file"):
                fname = sc["file"] if os.path.isabs(sc["file"]) else os.path.join(self.base, sc["file"])
                try:
                    with open(fname, encoding="utf-8") as f:
                        src = f.read()
                except Exception as ex:
                    self.out(f"[Файл скрипта не найден: {fname}] {ex}")
                    src = ""
            if not src.strip():
                continue
            ns = dict(self=o, game=self, Input=self.input, math=math, random=random, print=self._print)
            o.nss.append(ns)
            o.names[sc["name"]] = ns
            try:
                exec(compile(src, fname, "exec"), ns)
            except Exception:
                self._fail(o)
        return o

    def _print(self, *a, **k):
        self.out(" ".join(str(x) for x in a))

    def _fail(self, o=None):
        self.out(f"[Ошибка{' в «' + o.name + '»' if o else ''}]\n{traceback.format_exc()}")
        self.running = False

    def _call(self, o, fn, *a):
        for ns in o.nss:
            f = ns.get(fn)
            if f:
                try:
                    f(*a)
                except Exception:
                    self._fail(o)
                    return

    # ----- API для скриптов
    def find(self, name):
        return next((o for o in self.objs if o.alive and o.name == name), None)

    def find_all(self, tag):
        return [o for o in self.objs if o.alive and o.tag == tag]

    def spawn(self, name=None, x=None, y=None, **props):
        if name in self.templates:
            d = copy.deepcopy(self.templates[name])
        else:
            d = new_obj(props.pop("kind", "rect"), name=name or "obj")
        d["prefab"] = False
        if x is not None: d["x"] = x
        if y is not None: d["y"] = y
        o = self._make(d)
        for k, v in props.items():
            setattr(o, k, v)
        self.spawn_q.append(o)
        return o

    def destroy(self, o):
        o.alive = False

    def load_scene(self, name):
        self.pending = name

    def after(self, sec, fn):
        self.timers.append([sec, fn])

    def stop(self):
        self.running = False

    # ----- шаг симуляции
    def step(self, dt):
        if not self.running:
            return
        inp = self.input
        z, ox, oy = self.tf
        inp.mx, inp.my = (inp.px - ox) / z, (inp.py - oy) / z
        self.time += dt
        for o in self.objs:
            if o.alive:
                self._call(o, "update", dt)
            if not self.running:
                return
        for t in self.timers[:]:
            t[0] -= dt
            if t[0] <= 0:
                self.timers.remove(t)
                try:
                    t[1]()
                except Exception:
                    self._fail()
        self._physics(dt)
        self._collide()
        self._flush()
        if self.camera_follow and self.camera_follow.alive:
            k = min(1.0, dt * self.camera_smooth)
            self.camera[0] += (self.camera_follow.cx - self.camera[0]) * k
            self.camera[1] += (self.camera_follow.cy - self.camera[1]) * k
        inp.hit.clear()
        inp.mouse_hit = False
        if self.pending:
            n, self.pending = self.pending, None
            self.load(n)

    def _physics(self, dt):
        solids = build_grid([o for o in self.objs if o.alive and o.body in ("static", "kinematic") and o.solid])
        for o in self.objs:
            if not o.alive:
                continue
            if o.body == "kinematic":
                o.x += o.vx * dt; o.y += o.vy * dt
                continue
            if o.body != "dynamic":
                continue
            o.vy += self.gravity * o.grav * dt
            o.on_ground = False
            o.x += o.vx * dt
            if o.solid:
                for b in near(solids, o):
                    if hit(o, b):
                        if o.vx > 0: o.x = b.x - o.w
                        elif o.vx < 0: o.x = b.x + b.w
                        o.vx = 0
            o.y += o.vy * dt
            if o.solid:
                for b in near(solids, o):
                    if hit(o, b):
                        if o.vy > 0:
                            o.y = b.y - o.h; o.on_ground = True
                        elif o.vy < 0:
                            o.y = b.y + b.h
                        o.vy = 0

    def _collide(self):
        cols = [o for o in self.objs if o.alive and o.body != "none"]
        g = build_grid(cols)
        for a in cols:
            if not any("on_collide" in ns for ns in a.nss):
                continue
            for b in near(g, a):
                if a.alive and b.alive and hit(a, b):
                    self._call(a, "on_collide", b)
                    if not self.running:
                        return

    def _flush(self):
        if any(not o.alive for o in self.objs):
            self.objs = [o for o in self.objs if o.alive]
        if self.spawn_q:
            q, self.spawn_q = self.spawn_q, []
            self.objs.extend(q)
            self.objs.sort(key=lambda o: o.z)
            for o in q:
                self._call(o, "start")

    # ----- рисование
    def draw(self, cv, cw, ch, imgs):
        vw, vh = self.st["view_w"], self.st["view_h"]
        z = min(cw / vw, ch / vh)
        ox, oy = cw / 2 - self.camera[0] * z, ch / 2 - self.camera[1] * z
        uox, uoy = (cw - vw * z) / 2, (ch - vh * z) / 2
        self.tf = (z, ox, oy)
        cv.delete("all")
        cv.configure(bg=self.st["bg"])
        for o in self.objs:
            if not o.visible or o.kind == "empty":
                continue
            if o.ui:
                draw_obj(cv, o, z, uox, uoy, imgs)
            else:
                x0, y0 = o.x * z + ox, o.y * z + oy
                if x0 > cw or y0 > ch or x0 + o.w * z < 0 or y0 + o.h * z < 0:
                    continue
                draw_obj(cv, o, z, ox, oy, imgs)

# ───────────────────────── запуск игры на canvas ─────────────────────────
class Runner:
    def __init__(self, root, cv, project, scene, out, on_stop, imgs):
        self.root, self.cv, self.on_stop, self.imgs = root, cv, on_stop, imgs
        self.inp = Input()
        self.pending, self.job, self.dead = {}, None, False
        cv.bind("<KeyPress>", self.kd)
        cv.bind("<KeyRelease>", self.ku)
        self.game = Game(project, scene, self.inp, out, imgs.base)

    def kd(self, e):
        k = key_name(e)
        if k in self.pending:
            self.root.after_cancel(self.pending.pop(k))
        elif k not in self.inp.keys:
            self.inp.hit.add(k)
        self.inp.keys.add(k)
        if k == "escape" and not getattr(self, "editor", False):
            self.game.stop()

    def ku(self, e):
        k = key_name(e)
        def rel():
            self.pending.pop(k, None)
            self.inp.keys.discard(k)
        self.pending[k] = self.root.after(8, rel)  # гасим автоповтор клавиш

    def md(self, e):
        self.inp.mouse = self.inp.mouse_hit = True
        self.inp.px, self.inp.py = e.x, e.y

    def mu(self, e):
        self.inp.mouse = False

    def mm(self, e):
        self.inp.px, self.inp.py = e.x, e.y

    def start(self):
        self.cv.focus_set()
        self.last = time.perf_counter()
        self.tick()

    def tick(self):
        if self.dead:
            return
        t0 = time.perf_counter()
        dt = min(t0 - self.last, 0.05)
        self.last = t0
        g = self.game
        g.step(dt)
        if g.running:
            cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
            if cw > 4 and ch > 4:
                g.draw(self.cv, cw, ch, self.imgs)
        if not g.running:
            self.stop()
            return
        self.job = self.root.after(max(1, int(16 - (time.perf_counter() - t0) * 1000)), self.tick)

    def stop(self):
        if self.dead:
            return
        self.dead = True
        if self.job:
            self.root.after_cancel(self.job)
        for j in self.pending.values():
            self.root.after_cancel(j)
        self.cv.unbind("<KeyPress>")
        self.cv.unbind("<KeyRelease>")
        self.on_stop()

# ───────────────────────── PyForge Player: интерфейс ─────────────────────────
RECENT_FILE = os.path.join(os.path.expanduser("~"), ".pyforge_player.json")
PT = dict(bg="#f4f5f7", panel="#ffffff", line="#d9dce1", text="#1f2430", accent="#3b82f6", muted="#6b7280")

def load_recent():
    try:
        with open(RECENT_FILE, encoding="utf-8") as f:
            return [p for p in json.load(f) if isinstance(p, str) and os.path.exists(p)][:10]
    except Exception:
        return []

def save_recent(lst):
    try:
        with open(RECENT_FILE, "w", encoding="utf-8") as f:
            json.dump(lst[:10], f, ensure_ascii=False)
    except Exception:
        pass

class Player:
    HOME_SIZE = "540x470"

    def __init__(self, root, path=None):
        self.root, self.runner, self.path, self.log, self.full = root, None, None, [], False
        self.recent = load_recent()
        root.title("PyForge Player"); root.geometry(self.HOME_SIZE); root.minsize(440, 380); root.configure(bg=PT["bg"])
        self._style(); self._menubar(); self._home(); self._view()
        r = root
        r.bind("<Control-o>", lambda e: self.open_dialog()); r.bind("<F5>", lambda e: self.restart())
        r.bind("<F11>", lambda e: self.fullscreen()); r.bind("<Escape>", lambda e: self.to_home())
        r.protocol("WM_DELETE_WINDOW", self.quit)
        self.show_home()
        if path:
            root.after(80, lambda: self.run(path))

    # ───── оформление
    def _style(self):
        st = ttk.Style(); st.theme_use("clam")
        st.configure(".", background=PT["bg"], foreground=PT["text"], fieldbackground=PT["panel"],
                     bordercolor=PT["line"], font=("Segoe UI", 10))
        st.configure("TButton", padding=(10, 5), background=PT["panel"])
        st.map("TButton", background=[("active", "#e8f0fe")])
        st.configure("Big.TButton", padding=(16, 10), font=("Segoe UI", 13, "bold"), background=PT["accent"], foreground="white")
        st.map("Big.TButton", background=[("active", "#2563eb")])

    def _menubar(self):
        m = tk.Menu(self.root); f = tk.Menu(m, tearoff=0); g = tk.Menu(m, tearoff=0)
        f.add_command(label="Открыть игру…   Ctrl+O", command=self.open_dialog)
        f.add_separator(); f.add_command(label="Выход", command=self.quit)
        g.add_command(label="Начать заново   F5", command=self.restart)
        g.add_command(label="В меню   Esc", command=self.to_home)
        g.add_command(label="Во весь экран   F11", command=self.fullscreen)
        g.add_separator(); g.add_command(label="Лог игры (print и ошибки)…", command=self.show_log)
        m.add_cascade(label="Файл", menu=f); m.add_cascade(label="Игра", menu=g)
        self.root.configure(menu=m)

    def _home(self):
        h = self.home = ttk.Frame(self.root, padding=24)
        ttk.Label(h, text="PyForge Player", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(h, text="Запускает игры, сделанные в редакторе PyForge (файлы .json).",
                  foreground=PT["muted"]).pack(anchor="w", pady=(2, 16))
        ttk.Button(h, text="Открыть игру…", style="Big.TButton", command=self.open_dialog).pack(fill="x")
        ttk.Label(h, text="Недавние игры", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(18, 4))
        box = ttk.Frame(h); box.pack(fill="both", expand=True)
        self.lb = tk.Listbox(box, activestyle="none", relief="flat", bg=PT["panel"], fg=PT["text"], font=("Segoe UI", 10),
                             selectbackground="#dbe7ff", selectforeground=PT["text"], highlightthickness=1,
                             highlightbackground=PT["line"], exportselection=False)
        sb = ttk.Scrollbar(box, command=self.lb.yview); self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.lb.pack(fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda e: self.run_selected()); self.lb.bind("<Return>", lambda e: self.run_selected())
        self.lb.bind("<Delete>", lambda e: self.forget_selected())
        bt = ttk.Frame(h); bt.pack(fill="x", pady=(8, 0))
        ttk.Button(bt, text="Запустить", command=self.run_selected).pack(side="left")
        ttk.Button(bt, text="Убрать из списка", command=self.forget_selected).pack(side="left", padx=6)
        ttk.Label(h, text="В игре:  Esc — в меню   F5 — заново   F11 — на весь экран",
                  foreground=PT["muted"]).pack(anchor="w", pady=(12, 0))

    def _view(self):
        self.cv = tk.Canvas(self.root, highlightthickness=0, takefocus=1, bg="#000")
        cv = self.cv
        cv.bind("<Button-1>", lambda e: self.runner and self.runner.md(e))
        cv.bind("<ButtonRelease-1>", lambda e: self.runner and self.runner.mu(e))
        cv.bind("<Motion>", lambda e: self.runner and self.runner.mm(e))
        cv.bind("<B1-Motion>", lambda e: self.runner and self.runner.mm(e))

    # ───── экраны
    def show_home(self):
        self.cv.pack_forget()
        if self.full: self.fullscreen()
        self.home.pack(fill="both", expand=True)
        self.root.geometry(self.HOME_SIZE); self.root.title("PyForge Player")
        self.lb.delete(0, "end")
        for p in self.recent:
            self.lb.insert("end", f"  {os.path.splitext(os.path.basename(p))[0]}     —  {os.path.dirname(p)}")
        if self.recent: self.lb.selection_set(0)

    def show_game(self, st, title):
        self.home.pack_forget()
        self.cv.configure(bg=st["bg"]); self.cv.pack(fill="both", expand=True)
        if not self.full:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            k = min(1.0, (sw - 120) / st["view_w"], (sh - 160) / st["view_h"])
            self.root.geometry(f"{max(320, int(st['view_w'] * k))}x{max(240, int(st['view_h'] * k))}")
        self.root.title(f"{title} — PyForge Player"); self.cv.focus_set()

    def fullscreen(self):
        self.full = not self.full
        self.root.attributes("-fullscreen", self.full)
        if self.runner: self.cv.focus_set()

    # ───── запуск игры
    def open_dialog(self):
        p = filedialog.askopenfilename(title="Выбери игру", filetypes=[("Игры PyForge", "*.json"), ("Все файлы", "*.*")])
        if p: self.run(p)

    def run_selected(self):
        i = self.lb.curselection()
        if i: self.run(self.recent[i[0]])

    def forget_selected(self):
        i = self.lb.curselection()
        if i:
            del self.recent[i[0]]; save_recent(self.recent); self.show_home()

    def run(self, path):
        path = os.path.abspath(path)
        if self.runner: self.runner.stop()
        try:
            with open(path, encoding="utf-8") as f: proj = json.load(f)
            if not isinstance(proj, dict) or not isinstance(proj.get("scenes"), dict) or not proj["scenes"]:
                raise ValueError("В файле нет сцен — это не игра PyForge.")
            proj = normalize(proj)
        except Exception as ex:
            if path in self.recent: self.recent.remove(path); save_recent(self.recent)
            messagebox.showerror("Не удалось открыть игру", f"{os.path.basename(path)}\n\n{ex}")
            if self.runner is None: self.show_home()
            return
        self.path, self.log = path, []
        self.recent = [path] + [p for p in self.recent if p != path]; save_recent(self.recent)
        st = proj["settings"]
        scene = st["start"] if st["start"] in proj["scenes"] else next(iter(proj["scenes"]))
        self.show_game(st, os.path.splitext(os.path.basename(path))[0])
        self.root.update_idletasks()
        self.runner = Runner(self.root, self.cv, proj, scene, self._out, self._stopped, Images(os.path.dirname(path)))
        self.runner.editor = True      # Esc обрабатывает плеер (возврат в меню)
        self.runner.start()

    def restart(self):
        if self.path and (self.runner or self.cv.winfo_ismapped()): self.run(self.path)

    def to_home(self):
        if self.runner: self.runner.stop()
        elif self.cv.winfo_ismapped(): self.show_home()

    def _out(self, msg):
        self.log.append(str(msg))
        del self.log[:-2000]

    def _stopped(self):
        self.runner = None
        self.show_home()
        errs = [m for m in self.log if m.startswith("[Ошибка") or m.startswith("[Файл скрипта")]
        if errs:
            self.root.after(50, lambda: messagebox.showerror("Игра остановлена из-за ошибки", errs[-1][-1500:]))

    def show_log(self):
        w = tk.Toplevel(self.root); w.title("Лог игры"); w.geometry("620x360")
        t = tk.Text(w, font=("Consolas", 10), bg=PT["panel"], fg=PT["text"], relief="flat", padx=8, pady=6)
        t.insert("1.0", "\n".join(self.log) or "(пусто)"); t.configure(state="disabled"); t.pack(fill="both", expand=True)

    def quit(self):
        if self.runner: self.runner.stop()
        self.root.destroy()

def main():
    root = tk.Tk()
    Player(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()

if __name__ == "__main__":
    main()
