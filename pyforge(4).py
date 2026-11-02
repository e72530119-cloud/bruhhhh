#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyForge — лёгкий 2D-редактор игр на чистом Python (tkinter, без зависимостей).

Редактор:        python pyforge.py [проект.json]
Готовая игра:    python pyforge.py --play game.json

Игровая логика пишется на Python прямо в редакторе (вкладка «Скрипт»).
Опционально: pip install pillow  — для плавного масштабирования картинок.
"""
import copy, json, math, os, random, re, shutil, sys, time, traceback
from types import SimpleNamespace
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None

# ───────────────────────── тема (светлая) ─────────────────────────
T = dict(bg="#f4f5f7", panel="#ffffff", line="#d9dce1", text="#1f2430",
         accent="#3b82f6", grid="#e3e6ea", canvas="#eceef1", sel="#2563eb")
GRID = 32

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

PLAYER = '''speed = 280
jump = 640

def start():
    game.camera_follow = self

def update(dt):
    self.vx = Input.axis(("a", "left"), ("d", "right")) * speed
    if Input.pressed("space", "w", "up") and self.on_ground:
        self.vy = -jump
    if self.y > 900:
        die()

def die():
    game.vars["score"] = 0
    game.load_scene(game.scene)

def on_collide(other):
    if other.tag == "coin":
        game.destroy(other)
        game.vars["score"] = game.vars.get("score", 0) + 1
    elif other.tag == "enemy":
        die()
'''
ENEMY = '''def start():
    self.data["x0"] = self.x
    self.data["dir"] = 1

def update(dt):
    self.x += self.data["dir"] * 80 * dt
    if abs(self.x - self.data["x0"]) > 120:
        self.data["dir"] *= -1
'''
HUD = '''def update(dt):
    self.text = "Монеты: %d" % game.vars.get("score", 0)
'''
TEMPLATE = '''# self  — этот объект,  game — игра,  Input — клавиатура/мышь
def start():
    pass

def update(dt):
    pass

def on_collide(other):
    pass
'''

def demo_project():
    p = blank_project()
    o = p["scenes"]["Main"]
    o.append(new_obj("rect", name="Земля", x=-400, y=420, w=2600, h=140, color="#6b8e4e", body="static"))
    for i, (x, y) in enumerate([(300, 330), (520, 250), (760, 330), (1000, 260)]):
        o.append(new_obj("rect", name=f"Платформа{i+1}", x=x, y=y, w=150, h=20, color="#8b6b4a", body="static"))
    for i, (x, y) in enumerate([(360, 290), (580, 210), (820, 290), (1060, 220), (1300, 380)]):
        o.append(new_obj("ellipse", name=f"Монета{i+1}", x=x, y=y, w=22, h=22, color="#ffc400",
                         body="static", solid=False, tag="coin"))
    o.append(new_obj("rect", name="Враг", x=900, y=390, w=36, h=30, color="#e5484d", body="kinematic",
                     solid=False, tag="enemy", scripts=[S(ENEMY, "Patrol")]))
    o.append(new_obj("rect", name="Игрок", x=60, y=350, w=36, h=50, color="#2f3b52", body="dynamic",
                     tag="player", z=5, scripts=[S(PLAYER, "PlayerController")]))
    o.append(new_obj("text", name="HUD", x=16, y=12, w=300, h=36, size=28, color="#1f2430", ui=True,
                     z=10, text="Монеты: 0", scripts=[S(HUD, "HUD")]))
    return p

# ───────────────────────── библиотека игр ─────────────────────────
# Каждая игра — функция, возвращающая готовый проект (объекты + скрипты).
# Квадратики вместо графики: свои картинки подставляются через «+ Файлы…».

def _proj(bg, objs, gravity=0, w=960, h=540):
    p = blank_project()
    p["settings"].update(bg=bg, gravity=gravity, view_w=w, view_h=h)
    p["scenes"]["Main"] = list(objs)
    return p

def _hud(code, text="", color="#ffffff", size=26, x=16, y=10, w=700):
    return new_obj("text", name="HUD", x=x, y=y, w=w, h=size + 8, size=size, color=color, ui=True,
                   z=50, text=text, scripts=[S(code, "HUD")])

# ---------- Pong ----------
PONG_PLAYER = '''def update(dt):
    self.y += Input.axis(("w", "up"), ("s", "down")) * 440 * dt
    self.y = max(0, min(540 - self.h, self.y))
'''
PONG_AI = '''def update(dt):
    ball = game.find("Мяч")
    if not ball:
        return
    d = ball.cy - self.cy
    self.y += max(-300 * dt, min(300 * dt, d))
    self.y = max(0, min(540 - self.h, self.y))
'''
PONG_BALL = '''def start():
    game.vars["l"] = game.vars["r"] = 0
    reset(1)

def reset(side):
    self.cx, self.cy = 480, 270
    self.vx = 340 * side
    self.vy = random.choice([-1, 1]) * random.randint(120, 240)

def update(dt):
    if self.y < 0:
        self.y = 0
        self.vy = abs(self.vy)
    if self.y > 540 - self.h:
        self.y = 540 - self.h
        self.vy = -abs(self.vy)
    if self.x < -30:
        game.vars["r"] += 1
        reset(1)
    if self.x > 990:
        game.vars["l"] += 1
        reset(-1)

def on_collide(other):
    if other.tag != "paddle":
        return
    off = (self.cy - other.cy) / (other.h / 2)
    speed = min(760, abs(self.vx) * 1.06)
    if other.name == "Игрок":
        self.x = other.x + other.w
        self.vx = speed
    else:
        self.x = other.x - self.w
        self.vx = -speed
    self.vy = off * 300
'''
PONG_HUD = '''def update(dt):
    self.text = "%d   :   %d      (W/S или стрелки)" % (game.vars.get("l", 0), game.vars.get("r", 0))
'''
def game_pong():
    o = [new_obj("rect", name=f"Линия{i}", x=476, y=i * 50 + 12, w=8, h=26, color="#3a3f55") for i in range(11)]
    o += [new_obj("rect", name="Игрок", x=30, y=220, w=16, h=100, color="#ffffff", body="kinematic", solid=False,
                  tag="paddle", scripts=[S(PONG_PLAYER, "Control")]),
          new_obj("rect", name="Враг", x=914, y=220, w=16, h=100, color="#ff6b6b", body="kinematic", solid=False,
                  tag="paddle", scripts=[S(PONG_AI, "AI")]),
          new_obj("ellipse", name="Мяч", x=471, y=261, w=18, h=18, color="#ffd43b", body="kinematic", solid=False,
                  z=3, scripts=[S(PONG_BALL, "Ball")]),
          _hud(PONG_HUD, "0 : 0", x=240)]
    return _proj("#1b1f2e", o)

# ---------- Змейка ----------
SNAKE = '''CELL = 20
COLS, ROWS = 32, 24

def start():
    d = self.data
    d["head"] = game.find("Голова")
    d["food"] = game.find("Еда")
    d["segs"] = [d["head"]]
    d["dir"] = d["next"] = (1, 0)
    d["t"] = 0.0
    game.vars["score"] = 0
    place_food()

def place_food():
    d = self.data
    busy = {(int(s.x // CELL), int(s.y // CELL)) for s in d["segs"]}
    while True:
        c = (random.randrange(COLS), random.randrange(ROWS))
        if c not in busy:
            break
    d["food"].x, d["food"].y = c[0] * CELL, c[1] * CELL

def update(dt):
    d = self.data
    if Input.down("a", "left") and d["dir"] != (1, 0): d["next"] = (-1, 0)
    if Input.down("d", "right") and d["dir"] != (-1, 0): d["next"] = (1, 0)
    if Input.down("w", "up") and d["dir"] != (0, 1): d["next"] = (0, -1)
    if Input.down("s", "down") and d["dir"] != (0, -1): d["next"] = (0, 1)
    d["t"] += dt
    if d["t"] < max(0.05, 0.14 - 0.004 * game.vars["score"]):
        return
    d["t"] = 0
    d["dir"] = d["next"]
    head, segs = d["head"], d["segs"]
    nx, ny = head.x + d["dir"][0] * CELL, head.y + d["dir"][1] * CELL
    if not (0 <= nx < COLS * CELL and 0 <= ny < ROWS * CELL) or any(s.x == nx and s.y == ny for s in segs[:-1]):
        game.load_scene(game.scene)
        return
    tail = (segs[-1].x, segs[-1].y)
    for i in range(len(segs) - 1, 0, -1):
        segs[i].x, segs[i].y = segs[i - 1].x, segs[i - 1].y
    head.x, head.y = nx, ny
    if head.x == d["food"].x and head.y == d["food"].y:
        segs.append(game.spawn("Сегмент", tail[0], tail[1]))
        game.vars["score"] += 1
        place_food()
'''
def game_snake():
    o = [new_obj("rect", name="Сегмент", x=-100, y=-100, w=20, h=20, color="#51cf66", prefab=True),
         new_obj("ellipse", name="Еда", x=0, y=0, w=20, h=20, color="#ff6b6b", z=1),
         new_obj("rect", name="Голова", x=320, y=240, w=20, h=20, color="#2b8a3e", z=2),
         new_obj("empty", name="Игра", x=-100, y=-60, scripts=[S(SNAKE, "Snake")]),
         _hud('def update(dt):\n    self.text = "Счёт: %d   (WASD / стрелки)" % game.vars.get("score", 0)\n', color="#e9ecef", size=20, x=8, y=4)]
    return _proj("#1b2b22", o, w=640, h=480)

# ---------- Flappy ----------
FLAPPY_BIRD = '''def start():
    game.vars["score"] = 0
    game.vars.setdefault("best", 0)

def update(dt):
    if Input.pressed("space", "up", "w") or Input.mouse_hit:
        self.vy = -470
    if self.y > 540 or self.y < -80:
        die()

def die():
    game.vars["best"] = max(game.vars["best"], game.vars["score"])
    game.load_scene(game.scene)

def on_collide(other):
    if other.tag == "pipe":
        die()
'''
FLAPPY_SPAWNER = '''def start():
    self.data["t"] = 1.0

def update(dt):
    self.data["t"] += dt
    if self.data["t"] >= 1.6:
        self.data["t"] = 0
        gap_y = random.randint(150, 380)
        game.spawn("Труба", 980, gap_y - 450, vx=-210, h=450, top=True)
        game.spawn("Труба", 980, gap_y + 170, vx=-210, h=450, top=False)
'''
FLAPPY_PIPE = '''def update(dt):
    if self.x < -100:
        game.destroy(self)
        return
    bird = game.find("Птица")
    if bird and not self.data.get("passed") and self.x + self.w < bird.x:
        self.data["passed"] = True
        if self.top:
            game.vars["score"] += 1
'''
def game_flappy():
    o = [new_obj("rect", name="Труба", x=-300, y=0, w=70, h=450, color="#37b24d", body="kinematic", solid=False,
                 tag="pipe", prefab=True, scripts=[S(FLAPPY_PIPE, "Pipe")]),
         new_obj("rect", name="Земля", x=0, y=500, w=960, h=40, color="#c2915b", body="static", solid=False, tag="pipe", z=4),
         new_obj("empty", name="Спавнер", x=-100, y=-60, scripts=[S(FLAPPY_SPAWNER, "Spawner")]),
         new_obj("rect", name="Птица", x=200, y=250, w=34, h=34, color="#ffd43b", body="dynamic", z=5,
                 scripts=[S(FLAPPY_BIRD, "Bird")]),
         _hud('def update(dt):\n    self.text = "Счёт: %d    Рекорд: %d    (пробел / клик — взмах)" % (game.vars.get("score", 0), game.vars.get("best", 0))\n',
              color="#1f2430", size=24)]
    return _proj("#70c5ce", o, gravity=1500)

# ---------- Школа: собери тетради и убеги от учителя ----------
SCHOOL_MAP = [
    "####################",
    "#P...#....N....#...#",
    "#.##.#.######.##.#.#",
    "#.#N...#....#....#.#",
    "#.#.####.##.#.####.#",
    "#.#......#N...#....#",
    "#.######.####.#.##.#",
    "#N.......#....#.#..#",
    "#.####.#.#.####.#.##",
    "#......#....N....T.#",
    "####################",
]
SCHOOL_PLAYER = '''SPEED = 175

def start():
    game.vars["notes"] = 0
    game.vars["msg"] = ""

def update(dt):
    if game.vars["msg"]:
        self.vx = self.vy = 0
        return
    self.vx = Input.axis(("a", "left"), ("d", "right")) * SPEED
    self.vy = Input.axis(("w", "up"), ("s", "down")) * SPEED

def on_collide(other):
    if other.tag == "note":
        game.destroy(other)
        game.vars["notes"] += 1
        if game.vars["notes"] >= 5 and not game.vars["msg"]:
            game.vars["msg"] = "Все тетради собраны — победа!"
            game.after(2.5, lambda: game.load_scene(game.scene))
'''
SCHOOL_TEACHER = '''CELL = 48
MAP = __MAP__

def cell_of(o):
    return int(o.cx // CELL), int(o.cy // CELL)

def free(c, r):
    return 0 <= r < len(MAP) and 0 <= c < len(MAP[0]) and MAP[r][c] != "#"

def next_cell(start, goal):
    """Поиск пути по клеткам (BFS): возвращает следующую клетку на пути к цели."""
    prev = {start: None}
    q = [start]
    for cur in q:
        if cur == goal:
            break
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cur[0] + dc, cur[1] + dr)
            if n not in prev and free(*n):
                prev[n] = cur
                q.append(n)
    if goal not in prev:
        return start
    n = goal
    while prev[n] != start:
        n = prev[n]
    return n

def update(dt):
    p = game.find("Игрок")
    if not p or game.vars["msg"]:
        self.vx = self.vy = 0
        return
    speed = 85 + 22 * game.vars["notes"]   # с каждой тетрадью учитель быстрее
    a, b = cell_of(self), cell_of(p)
    if a == b:
        tx, ty = p.cx, p.cy
    else:
        n = next_cell(a, b)
        tx, ty = (n[0] + 0.5) * CELL, (n[1] + 0.5) * CELL
    dx, dy = tx - self.cx, ty - self.cy
    d = math.hypot(dx, dy) or 1
    self.vx, self.vy = dx / d * speed, dy / d * speed

def on_collide(other):
    if other.name == "Игрок" and not game.vars["msg"]:
        game.vars["msg"] = "Тебя поймали! Ещё раз..."
        game.after(1.5, lambda: game.load_scene(game.scene))
'''
SCHOOL_HUD = '''def update(dt):
    self.text = "Тетради: %d/5   %s" % (game.vars.get("notes", 0), game.vars.get("msg", ""))
'''
def game_school():
    C, o = 48, []
    for r, row in enumerate(SCHOOL_MAP):
        c = 0
        while c < len(row):
            if row[c] == "#":
                s = c
                while c < len(row) and row[c] == "#": c += 1
                o.append(new_obj("rect", name="Стена", x=s * C, y=r * C, w=(c - s) * C, h=C, color="#4a4e69", body="static"))
            else:
                x, y = c * C, r * C
                if row[c] == "N":
                    o.append(new_obj("rect", name="Тетрадь", x=x + 15, y=y + 12, w=18, h=24, color="#ffd43b",
                                     body="static", solid=False, tag="note", z=1))
                elif row[c] == "P":
                    o.append(new_obj("rect", name="Игрок", x=x + 9, y=y + 9, w=30, h=30, color="#339af0", body="dynamic",
                                     grav=0, z=5, scripts=[S(SCHOOL_PLAYER, "Control")]))
                elif row[c] == "T":
                    o.append(new_obj("rect", name="Учитель", x=x + 9, y=y + 9, w=30, h=30, color="#e03131", body="dynamic",
                                     grav=0, z=4, scripts=[S(SCHOOL_TEACHER.replace("__MAP__", repr(SCHOOL_MAP)), "Chase")]))
                c += 1
    o.append(_hud(SCHOOL_HUD, color="#1f2430", size=22, y=532 - 30, x=12))
    return _proj("#dee2e6", o)

# ---------- Монстры-кликалка ----------
MONSTER_GAME = '''def start():
    game.vars.update(coins=0, cost=10, owned=0)
    buy(free=True)

def buy(free=False):
    if not free:
        if game.vars["coins"] < game.vars["cost"]:
            return
        game.vars["coins"] -= game.vars["cost"]
        game.vars["cost"] = int(game.vars["cost"] * 1.6) + 1
    game.vars["owned"] += 1
    colors = ["#ff922b", "#845ef7", "#22b8cf", "#f06595", "#94d82d", "#fcc419"]
    game.spawn("Монстр", random.randint(150, 780), random.randint(190, 410), color=random.choice(colors))
'''
MONSTER_SHOP = '''def update(dt):
    if Input.mouse_hit and self.x <= Input.mx <= self.x + self.w and self.y <= Input.my <= self.y + self.h:
        game.find("Игра").script("Game").buy()
'''
MONSTER = '''def start():
    d = self.data
    d["stored"] = 0.0
    d["rate"] = random.choice([1, 1.5, 2, 3])        # монет в секунду
    d["base_y"] = self.y
    d["lbl"] = game.spawn("Метка", self.x, self.y - 24)

def update(dt):
    d = self.data
    d["stored"] = min(d["stored"] + d["rate"] * dt, 60)
    self.y = d["base_y"] + math.sin(game.time * 4 + d["rate"]) * 4
    d["lbl"].x, d["lbl"].y = self.x + 10, self.y - 24
    d["lbl"].text = str(int(d["stored"]))
    if Input.mouse_hit and self.x <= Input.mx <= self.x + self.w and self.y <= Input.my <= self.y + self.h:
        got = int(d["stored"])
        game.vars["coins"] += got
        d["stored"] -= got
'''
def game_monsters():
    o = [new_obj("ellipse", name="Остров", x=80, y=120, w=800, h=400, color="#8ce99a"),
         new_obj("ellipse", name="Монстр", x=-200, y=300, w=56, h=56, color="#ff922b", prefab=True, z=3,
                 scripts=[S(MONSTER, "Monster")]),
         new_obj("text", name="Метка", x=-200, y=200, w=40, h=22, size=20, color="#1f2430", prefab=True, z=9, text="0"),
         new_obj("empty", name="Игра", x=-100, y=-60, scripts=[S(MONSTER_GAME, "Game")]),
         new_obj("rect", name="Магазин", x=700, y=14, w=240, h=52, color="#f59f00", ui=True, z=40,
                 scripts=[S(MONSTER_SHOP, "Shop")]),
         new_obj("text", name="МагазинТекст", x=716, y=26, w=220, h=28, size=22, color="#ffffff", ui=True, z=41,
                 text="Купить монстра",
                 scripts=[S('def update(dt):\n    self.text = "Купить: %d монет" % game.vars.get("cost", 0)\n', "Label")]),
         _hud('def update(dt):\n    v = game.vars\n    self.text = "Монеты: %d     Монстров: %d     (клик по монстру собирает монеты)" % (v.get("coins", 0), v.get("owned", 0))\n',
              color="#1f2430", size=22, w=680)]
    return _proj("#74c0fc", o)

LIBRARY = [
    ("Платформер", "Прыжки, монеты, патрулирующий враг, камера. A/D или стрелки, пробел — прыжок.", demo_project),
    ("Pong (пинг-понг)", "Классика против ИИ: отскок зависит от места удара, мяч ускоряется. W/S или стрелки.", game_pong),
    ("Змейка", "Сетка, рост змейки, ускорение с очками, прячется еда под хвост. WASD или стрелки.", game_snake),
    ("Flappy: летающая птица", "Трубы генерируются таймером (prefab + spawn), счёт и рекорд. Пробел или клик.", game_flappy),
    ("Школа: собери тетради", "Лабиринт сверху: собери 5 тетрадей, учитель ищет тебя по лабиринту (поиск пути BFS) и ускоряется.", game_school),
    ("Монстры-кликалка", "Монстры копят монеты — кликай по ним, покупай новых в магазине (цена растёт). Только мышь.", game_monsters),
]

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
            q, self.spawn_q = [o for o in self.spawn_q if o.alive], []
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
        for o in sorted(self.objs, key=lambda o: o.z):
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

# ───────────────────────── редактор ─────────────────────────
KW = ("and as assert break class continue def del elif else except finally for from global if import in is "
      "lambda not or pass raise return try while with yield None True False self").split()
HL_RE = re.compile(r"#[^\n]*|\"[^\"\n]*\"|'[^'\n]*'|\b\d+\.?\d*\b|\b(?:%s)\b" % "|".join(KW))

ICON = dict(rect="▭", ellipse="◯", text="T", image="▣", empty="◇")
CHOICES = dict(kind=["rect", "ellipse", "text", "image", "empty"], body=["none", "static", "dynamic", "kinematic"])
OBJ_FIELDS = [("name", "Имя", "str"), ("kind", "Тип", "kind"), ("x", "X", "num"), ("y", "Y", "num"),
              ("w", "Ширина", "num"), ("h", "Высота", "num"), ("color", "Цвет", "color"),
              ("text", "Текст", "str"), ("size", "Размер шрифта", "num"), ("image", "Картинка", "image"),
              ("body", "Физика", "body"), ("grav", "Гравитация ×", "num"), ("solid", "Твёрдый", "bool"),
              ("tag", "Тег", "str"), ("z", "Слой Z", "num"), ("visible", "Видимый", "bool"),
              ("ui", "UI (к экрану)", "bool"), ("prefab", "Шаблон (prefab)", "bool")]
SET_FIELDS = [("gravity", "Гравитация", "num"), ("bg", "Фон", "color"), ("view_w", "Ширина экрана", "num"),
              ("view_h", "Высота экрана", "num"), ("start", "Стартовая сцена", "str")]

HELP = """СКРИПТЫ (Python). В скрипте доступны: self, game, Input, math, random, print.

Функции-события (все необязательные):
  start()            — при создании сцены
  update(dt)         — каждый кадр, dt в секундах
  on_collide(other)  — пока объект касается другого (у обоих должна быть физика ≠ none)

self:   x y w h cx cy vx vy color text size visible z tag name on_ground data(dict)
        self.overlaps(o)  self.dist(o)  self.script("Имя") — доступ к переменным/функциям другого скрипта объекта
game:   vars (общий словарь)   scene   time   gravity   camera=[x,y]   camera_follow=obj
        find(имя)  find_all(тег)  spawn(шаблон, x, y, **свойства)  destroy(obj)
        load_scene(имя)  after(сек, функция)  stop()
Input:  down("a","left")  pressed("space")  axis("a","d")  mx my (мышь в мире)  mouse  mouse_hit

Физика: static — стоит;  dynamic — гравитация + столкновения с твёрдыми static/kinematic;
        kinematic — двигается сам (vx/vy или x/y), не падает.
        «Твёрдый» выключен → объект работает как триггер (проходится насквозь, но шлёт on_collide).
Шаблон (prefab): объект не появляется в игре, но его можно клонировать: game.spawn("Пуля", x, y, vx=600)
UI: объект с галочкой «UI» рисуется в координатах экрана (HUD) и не двигается с камерой.

ПЛЕЕР: отдельное приложение pyforge_player.py — запускаешь, «Открыть игру…», выбираешь .json игры из редактора.
  Экспорт кладёт плеер рядом с игрой (если pyforge_player.py лежит рядом с pyforge.py).

БИБЛИОТЕКА ИГР: кнопка «Библиотека игр» в панели — выбираешь игру, редактор создаёт готовый проект
  (Платформер, Pong, Змейка, Flappy, Школа с догоняющим учителем, Монстры-кликалка). Смотри их скрипты как примеры.
  Собственную игру в библиотеку добавляют так: функция, возвращающая проект, и строка в списке LIBRARY в начале pyforge.py.

ФАЙЛЫ И КОД:
  «+ Файлы…» в панели — сразу добавить несколько картинок (спрайты) и/или .py (объект со скриптом из файла).
  Скрипты: у объекта может быть несколько («+ Скрипт»), каждый можно переименовать, выключить («Вкл») и удалить.
      Они работают независимо, у каждого свои переменные; события start/update/on_collide вызываются у всех.
      Значок ƒ2 в списке объектов — число скриптов. Кнопка ⤢ — развернуть/свернуть панель кода.
  Меню «Код ▾»: «Связать с .py файлом» — код берётся из файла при каждом запуске, его можно править в VS Code
      (правки в PyForge тоже пишутся в файл); «Загрузить из .py» — копия кода; «Сохранить в .py» — выгрузка.
  В скриптах работает  import мой_модуль  (файлы .py рядом с проектом или в папке scripts/).
  Проект лучше сохранить до добавления файлов — тогда пути будут относительными, и проект можно переносить.

Мышь: колесо — зум, правая/средняя кнопка — панорама, Home — показать всё.
Горячие клавиши: F5 играть, Esc стоп, Ctrl+S, Ctrl+Z/Y, Ctrl+D дубль, Delete, стрелки — сдвиг.
"""

def num(v):
    f = float(str(v).replace(",", "."))
    return int(f) if f == int(f) else round(f, 3)

class App:
    def __init__(self, root, path=None):
        self.root = root
        root.title("PyForge"); root.geometry("1300x800"); root.configure(bg=T["bg"])
        self.project, self.path, self.scene, self.sel = demo_project(), None, "Main", None
        self.undo_st, self.redo_st, self.dirty = [], [], False
        self.zoom, self.ox, self.oy = 1.0, 60.0, 40.0
        self.mode, self.runner, self._sp, self._hl = None, None, False, None
        self.si, self._last_sel, self._expanded, self._sash_prev = 0, None, False, 0
        self.imgs = Images(os.getcwd())
        self.snap_on = tk.BooleanVar(value=True)
        self._style(); self._build(); self._keys()
        if path:
            self.open(path)
        self.refresh_all()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(200, self.fit)

    @property
    def objs(self):
        return self.project["scenes"][self.scene]

    # ───── интерфейс
    def _style(self):
        st = ttk.Style(); st.theme_use("clam")
        st.configure(".", background=T["bg"], foreground=T["text"], fieldbackground=T["panel"],
                     bordercolor=T["line"], lightcolor=T["bg"], darkcolor=T["bg"], font=("Segoe UI", 10))
        st.configure("TButton", padding=(9, 4), background=T["panel"])
        st.map("TButton", background=[("active", "#e8f0fe")])
        st.configure("Accent.TButton", background=T["accent"], foreground="white")
        st.map("Accent.TButton", background=[("active", "#2563eb")])
        st.configure("Treeview", background=T["panel"], fieldbackground=T["panel"], rowheight=24)
        st.map("Treeview", background=[("selected", "#dbe7ff")], foreground=[("selected", T["text"])])
        st.configure("TNotebook.Tab", padding=(12, 4))
        st.configure("TLabelframe", background=T["bg"])

    def _build(self):
        r = self.root
        tb = ttk.Frame(r, padding=6); tb.pack(fill="x")
        def B(text, cmd, style="TButton", side="left"):
            b = ttk.Button(tb, text=text, command=cmd, style=style); b.pack(side=side, padx=2); return b
        def sep(): ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        B("Новый", self.new); B("Открыть", self.open_dlg); B("Сохранить", self.save)
        B("Библиотека игр", self.library, "Accent.TButton"); sep()
        for k, l in [("rect", "Прямоугольник"), ("ellipse", "Круг"), ("text", "Текст"),
                     ("image", "Картинка"), ("empty", "Пустой")]:
            B("+ " + l, lambda k=k: self.add(k))
        B("+ Файлы…", self.add_files)
        sep(); B("↶", self.undo); B("↷", self.redo); sep()
        self.play_btn = B("▶ Играть (F5)", self.toggle_play, "Accent.TButton")
        B("Экспорт", self.export, side="right")
        ttk.Checkbutton(tb, text="Привязка к сетке", variable=self.snap_on).pack(side="right", padx=8)
        B("+ Сцена", self.add_scene, side="right")
        self.scene_cb = ttk.Combobox(tb, state="readonly", width=14); self.scene_cb.pack(side="right", padx=2)
        self.scene_cb.bind("<<ComboboxSelected>>", lambda e: self.switch_scene(self.scene_cb.get()))

        pw = ttk.PanedWindow(r, orient="horizontal"); pw.pack(fill="both", expand=True)
        left, center, right = ttk.Frame(pw, width=210), ttk.Frame(pw), ttk.Frame(pw, width=280)
        pw.add(left, weight=0); pw.add(center, weight=1); pw.add(right, weight=0)

        ttk.Label(left, text="Объекты", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._tree_sel)

        self.vp = vp = ttk.PanedWindow(center, orient="vertical"); vp.pack(fill="both", expand=True)
        cvf = ttk.Frame(vp); vp.add(cvf, weight=3)
        self.cv = tk.Canvas(cvf, bg=T["canvas"], highlightthickness=0, takefocus=1)
        self.cv.pack(fill="both", expand=True)
        cv = self.cv
        cv.bind("<Button-1>", self.on_down); cv.bind("<B1-Motion>", self.on_move); cv.bind("<ButtonRelease-1>", self.on_up)
        cv.bind("<Motion>", lambda e: self.runner and self.runner.mm(e))
        for b in (2, 3):
            cv.bind(f"<Button-{b}>", self.pan_down); cv.bind(f"<B{b}-Motion>", self.pan_move)
        cv.bind("<MouseWheel>", self.wheel); cv.bind("<Button-4>", self.wheel); cv.bind("<Button-5>", self.wheel)
        cv.bind("<Configure>", lambda e: self.redraw())

        self.notes = ttk.Notebook(vp); vp.add(self.notes, weight=1)
        sf = ttk.Frame(self.notes); self.notes.add(sf, text="Скрипт")
        hd = ttk.Frame(sf); hd.pack(fill="x", padx=6, pady=(4, 0))
        self.sc_cb = ttk.Combobox(hd, state="readonly", width=26); self.sc_cb.pack(side="left")
        self.sc_cb.bind("<<ComboboxSelected>>", lambda e: (setattr(self, "si", self.sc_cb.current()), self.load_script()))
        self.sc_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(hd, text="Вкл", variable=self.sc_on, command=self.toggle_script).pack(side="left", padx=6)
        ttk.Button(hd, text="+ Скрипт", command=self.add_script).pack(side="left", padx=2)
        ttk.Button(hd, text="Имя…", command=self.rename_script).pack(side="left", padx=2)
        ttk.Button(hd, text="Удалить", command=self.delete_script).pack(side="left", padx=2)
        ttk.Button(hd, text="⤢", width=3, command=self.toggle_expand).pack(side="right", padx=2)
        fm = tk.Menu(self.root, tearoff=0)
        fm.add_command(label="Шаблон кода", command=self.insert_template)
        fm.add_separator()
        fm.add_command(label="Загрузить код из .py (копия)…", command=self.script_from_file)
        fm.add_command(label="Сохранить код в .py…", command=self.script_to_file)
        fm.add_separator()
        fm.add_command(label="Связать с .py файлом…", command=self.script_link)
        fm.add_command(label="Отвязать от файла", command=self.script_unlink)
        ttk.Menubutton(hd, text="Код ▾", menu=fm).pack(side="right", padx=2)
        self.script_lbl = ttk.Label(sf, text="Выберите объект"); self.script_lbl.pack(anchor="w", padx=8, pady=(2, 2))
        ef = ttk.Frame(sf); ef.pack(fill="both", expand=True)
        self.code = tk.Text(ef, font=("Consolas", 11), undo=True, wrap="none", bg=T["panel"], fg=T["text"],
                            insertbackground=T["text"], relief="flat", height=8, tabs=("4c",), padx=6, pady=4)
        sb = ttk.Scrollbar(ef, command=self.code.yview); self.code.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.code.pack(fill="both", expand=True)
        for tag, col in (("kw", "#d6336c"), ("str", "#2b8a3e"), ("com", "#868e96"), ("num", "#e8590c")):
            self.code.tag_configure(tag, foreground=col)
        self.code.bind("<KeyPress>", self._script_press); self.code.bind("<KeyRelease>", self._script_key)
        self.code.bind("<FocusIn>", lambda e: setattr(self, "_sp", False))
        self.code.bind("<Return>", self._autoindent); self.code.bind("<Tab>", lambda e: (self.code.insert("insert", "    "), "break")[1])

        cf = ttk.Frame(self.notes); self.notes.add(cf, text="Консоль"); self.console_tab = cf
        self.console = tk.Text(cf, font=("Consolas", 10), state="disabled", bg=T["panel"], fg=T["text"],
                               relief="flat", height=8, padx=6, pady=4)
        self.console.pack(fill="both", expand=True)
        hf = ttk.Frame(self.notes); self.notes.add(hf, text="Справка")
        ht = tk.Text(hf, font=("Consolas", 10), bg=T["panel"], fg=T["text"], relief="flat", height=8, padx=8, pady=6)
        ht.insert("1.0", HELP); ht.configure(state="disabled"); ht.pack(fill="both", expand=True)

        ttk.Label(right, text="Свойства", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
        self.obj_f, self.set_f = ttk.Frame(right, padding=8), ttk.Frame(right, padding=8)
        self.ovars, self.svars = {}, {}
        for i, (k, l, t) in enumerate(OBJ_FIELDS):
            ttk.Label(self.obj_f, text=l).grid(row=i, column=0, sticky="w", pady=2)
            self._field(self.obj_f, i, k, t, self.set_prop, self.ovars)
        ttk.Label(self.set_f, text="Настройки проекта", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for i, (k, l, t) in enumerate(SET_FIELDS, 1):
            ttk.Label(self.set_f, text=l).grid(row=i, column=0, sticky="w", pady=2)
            self._field(self.set_f, i, k, t, self.set_setting, self.svars)

    def _field(self, parent, row, key, typ, cb, store):
        if typ == "bool":
            v = tk.BooleanVar()
            ttk.Checkbutton(parent, variable=v, command=lambda: cb(key, v.get())).grid(row=row, column=1, sticky="w")
        elif typ in CHOICES:
            v = tk.StringVar()
            c = ttk.Combobox(parent, textvariable=v, values=CHOICES[typ], state="readonly", width=16)
            c.grid(row=row, column=1, sticky="ew"); c.bind("<<ComboboxSelected>>", lambda e: cb(key, v.get()))
        else:
            v = tk.StringVar(); fr = ttk.Frame(parent); fr.grid(row=row, column=1, sticky="ew")
            e = ttk.Entry(fr, textvariable=v, width=16 if typ in ("color", "image", "pyfile") else 19)
            e.pack(side="left", fill="x", expand=True)
            e.bind("<Return>", lambda ev: cb(key, v.get())); e.bind("<FocusOut>", lambda ev: cb(key, v.get()))
            if typ == "color":
                ttk.Button(fr, text="■", width=3, command=lambda: self._pick_color(key, v, cb)).pack(side="left", padx=2)
            if typ == "image":
                ttk.Button(fr, text="…", width=3, command=lambda: self._pick_image(key, v, cb)).pack(side="left", padx=2)
            if typ == "pyfile":
                ttk.Button(fr, text="…", width=3, command=lambda: self._pick_pyfile(key, v, cb)).pack(side="left", padx=2)
                ttk.Button(fr, text="✕", width=3, command=lambda: (v.set(""), cb(key, ""))).pack(side="left")
        store[key] = v

    def _pick_color(self, key, v, cb):
        c = colorchooser.askcolor(color=v.get() or None)[1]
        if c: v.set(c); cb(key, c)

    def _pick_image(self, key, v, cb):
        p = filedialog.askopenfilename(filetypes=[("Изображения", "*.png *.gif *.jpg *.jpeg *.bmp *.webp"), ("Все", "*.*")])
        if p:
            v.set(self._store_path(p)); cb(key, v.get())

    def _pick_pyfile(self, key, v, cb):
        p = filedialog.askopenfilename(filetypes=[("Python", "*.py"), ("Все", "*.*")])
        if p:
            v.set(self._store_path(p)); cb(key, v.get())

    def _store_path(self, p):
        if self.path:
            try: return os.path.relpath(p, self.imgs.base).replace("\\", "/")
            except ValueError: pass
        return p

    def _keys(self):
        r = self.root
        r.bind("<Control-s>", lambda e: self.save()); r.bind("<F5>", lambda e: self.toggle_play())
        r.bind("<Escape>", lambda e: self.stop_play()); r.bind("<Home>", self.guard(self.fit))
        r.bind("<Control-z>", self.guard(self.undo)); r.bind("<Control-y>", self.guard(self.redo))
        r.bind("<Control-d>", self.guard(self.duplicate)); r.bind("<Delete>", self.guard(self.delete))
        for k, (dx, dy) in dict(Left=(-1, 0), Right=(1, 0), Up=(0, -1), Down=(0, 1)).items():
            r.bind(f"<{k}>", self.guard(lambda dx=dx, dy=dy: self.nudge(dx, dy)))

    def guard(self, fn):
        def h(e=None):
            if self.runner: return
            try: f = self.root.focus_get()
            except Exception: f = None
            if isinstance(f, (tk.Entry, tk.Text)): return
            fn(); return "break"
        return h

    # ───── консоль
    def log(self, msg):
        c = self.console; c.configure(state="normal"); c.insert("end", str(msg) + "\n")
        c.see("end"); c.configure(state="disabled")

    # ───── выбор и синхронизация
    def cur(self):
        return self.objs[self.sel] if self.sel is not None and self.sel < len(self.objs) else None

    def refresh_all(self):
        self.scene_cb.configure(values=list(self.project["scenes"])); self.scene_cb.set(self.scene)
        self.refresh_tree(); self.select(self.sel, redraw=False); self.redraw(); self._title()

    def _title(self):
        self.root.title(f"PyForge — {os.path.basename(self.path) if self.path else 'без имени'}{' *' if self.dirty else ''}")

    def refresh_tree(self):
        t = self.tree; t.delete(*t.get_children())
        for i, o in enumerate(self.objs):
            t.insert("", "end", iid=str(i), text=f" {ICON.get(o['kind'], '?')}  {o['name']}{'  ★' if o['prefab'] else ''}{'  ƒ' + str(len(o['scripts'])) if o['scripts'] else ''}")
        if self.sel is not None and self.sel < len(self.objs):
            t.selection_set(str(self.sel))

    def _tree_sel(self, e):
        s = self.tree.selection()
        if s and int(s[0]) != self.sel:
            self.select(int(s[0]))

    def select(self, i, redraw=True):
        if i is not None and i >= len(self.objs): i = None
        if i != self._last_sel: self.si = 0
        self._last_sel = i; self.sel = i; self._sp = False
        if i is not None and self.tree.selection() != (str(i),):
            self.tree.selection_set(str(i)); self.tree.see(str(i))
        if i is None and self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self.obj_f.pack_forget(); self.set_f.pack_forget()
        (self.obj_f if i is not None else self.set_f).pack(fill="x", anchor="n")
        self.sync(); self.load_script()
        if redraw: self.redraw()

    def sync(self):
        o = self.cur()
        if o:
            for k, v in self.ovars.items(): v.set(o[k])
        else:
            for k, v in self.svars.items(): v.set(self.project["settings"][k])

    # ───── правки свойств
    def snap_undo(self):
        cur = json.dumps(self.project)
        if self.undo_st and self.undo_st[-1] == cur: return
        self.undo_st.append(cur); del self.undo_st[:-100]; self.redo_st.clear()
        self.dirty = True; self._title()

    def _coerce(self, key, typ, raw):
        if typ == "num": return num(raw)
        if typ == "color":
            self.root.winfo_rgb(raw); return raw
        return raw

    def set_prop(self, key, raw):
        o = self.cur()
        if not o: return
        typ = next(t for k, _, t in OBJ_FIELDS if k == key)
        try: val = self._coerce(key, typ, raw)
        except Exception: self.sync(); return
        if o[key] == val: return
        self.snap_undo(); o[key] = val
        if key in ("name", "kind", "prefab"): self.refresh_tree()
        if key == "name": self.load_script()
        self.sync(); self.redraw()

    def set_setting(self, key, raw):
        typ = next(t for k, _, t in SET_FIELDS if k == key)
        try: val = self._coerce(key, typ, raw)
        except Exception: self.sync(); return
        if self.project["settings"][key] == val: return
        self.snap_undo(); self.project["settings"][key] = val; self.sync(); self.redraw()

    def uniq(self, base):
        names = {o["name"] for o in self.objs}
        if base not in names: return base
        i = 2
        while f"{base}{i}" in names: i += 1
        return f"{base}{i}"

    def snapv(self, v):
        return round(v / GRID) * GRID if self.snap_on.get() else round(v)

    def add(self, kind):
        if self.runner: return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        wx, wy = self.to_world(cw / 2, ch / 2)
        o = new_obj(kind, x=self.snapv(wx - 32), y=self.snapv(wy - 32))
        o["name"] = self.uniq(o["name"])
        if kind == "image":
            p = filedialog.askopenfilename(filetypes=[("Изображения", "*.png *.gif *.jpg *.jpeg *.bmp *.webp")])
            if not p: return
            o["image"] = self._store_path(p); o["w"], o["h"] = self._img_size(p)
        self.snap_undo(); self.objs.append(o); self.sel = len(self.objs) - 1
        self.refresh_tree(); self.select(self.sel)

    def delete(self):
        if self.sel is None: return
        self.snap_undo(); self.objs.pop(self.sel); self.sel = None
        self.refresh_tree(); self.select(None)

    def duplicate(self):
        o = self.cur()
        if not o: return
        self.snap_undo(); n = copy.deepcopy(o); n["name"] = self.uniq(o["name"]); n["x"] += GRID; n["y"] += GRID
        self.objs.append(n); self.sel = len(self.objs) - 1
        self.refresh_tree(); self.select(self.sel)

    def nudge(self, dx, dy):
        o = self.cur()
        if not o: return
        self.snap_undo(); step = GRID if self.snap_on.get() else 1
        o["x"] += dx * step; o["y"] += dy * step; self.sync(); self.redraw()

    # ───── сцены
    def add_scene(self):
        n = simpledialog.askstring("Новая сцена", "Название сцены:")
        if not n or n in self.project["scenes"]: return
        self.snap_undo(); self.project["scenes"][n] = []; self.switch_scene(n)

    def switch_scene(self, n):
        if self.runner or n not in self.project["scenes"]: return
        self.scene, self.sel = n, None; self.refresh_all()

    # ───── скрипты (у объекта может быть несколько)
    def cur_script(self):
        o = self.cur()
        if not o or not o["scripts"]: return None
        self.si = min(max(self.si, 0), len(o["scripts"]) - 1)
        return o["scripts"][self.si]

    def refresh_script_bar(self):
        o = self.cur(); sc = o["scripts"] if o else []
        self.sc_cb.configure(values=[("● " if x["on"] else "○ ") + x["name"] + ("  ⇄" if x["file"] else "") for x in sc])
        s = self.cur_script()
        if s: self.sc_cb.current(self.si); self.sc_on.set(s["on"])
        else: self.sc_cb.set(""); self.sc_on.set(False)

    def load_script(self):
        o = self.cur(); c = self.code; s = self.cur_script()
        self.refresh_script_bar()
        c.configure(state="normal"); c.delete("1.0", "end")
        if not o:
            self.script_lbl.config(text="Выберите объект, чтобы добавить скрипт"); c.configure(state="disabled"); return
        if not s:
            self.script_lbl.config(text=f"У «{o['name']}» пока нет скриптов — нажмите «+ Скрипт»"); c.configure(state="disabled"); return
        text, lbl = s["code"], f"«{o['name']}» → {s['name']}" + ("" if s["on"] else "   (выключен — в игре не работает)")
        if s["file"]:
            try:
                with open(self.imgs.path(s["file"]), encoding="utf-8") as f: text = f.read()
                lbl += f"   ⇄ {os.path.basename(s['file'])} (правки пишутся в файл)"
            except Exception:
                text, lbl = "", lbl + f"   ⚠ файл не найден: {s['file']}"
        c.insert("1.0", text); self.script_lbl.config(text=lbl); self.highlight()

    def _script_press(self, e):
        if not self._sp and self.cur_script() and e.keysym not in ("Left", "Right", "Up", "Down", "Control_L", "Control_R", "Shift_L", "Shift_R"):
            self.snap_undo(); self._sp = True

    def _script_key(self, e):
        s = self.cur_script()
        if not s: return
        text = self.code.get("1.0", "end-1c")
        if s["file"]:
            try:
                with open(self.imgs.path(s["file"]), "w", encoding="utf-8") as f: f.write(text)
            except Exception as ex:
                self.log(f"Не удалось записать файл скрипта: {ex}")
        else:
            s["code"] = text
        if self._hl: self.root.after_cancel(self._hl)
        self._hl = self.root.after(150, self.highlight)

    def highlight(self):
        c = self.code
        for t in ("kw", "str", "com", "num"): c.tag_remove(t, "1.0", "end")
        src = c.get("1.0", "end-1c")
        for m in HL_RE.finditer(src):
            s = m.group(0)[0]
            tag = "com" if s == "#" else "str" if s in "\"'" else "num" if s.isdigit() else "kw"
            c.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def _autoindent(self, e):
        line = self.code.get("insert linestart", "insert")
        ind = re.match(r"\s*", line).group(0)
        if line.rstrip().endswith(":"): ind += "    "
        self.code.insert("insert", "\n" + ind); self.code.see("insert")
        return "break"

    def _uniq_script(self, o, base):
        names = {x["name"] for x in o["scripts"]}; n, i = base, 2
        while n in names: n = f"{base}{i}"; i += 1
        return n

    def add_script(self, code=None):
        o = self.cur()
        if not o or self.runner: return
        self.snap_undo()
        o["scripts"].append(S(TEMPLATE if (code is None and not o["scripts"]) else (code or "def update(dt):\n    pass\n"),
                              self._uniq_script(o, "Script")))
        self.si = len(o["scripts"]) - 1
        self.refresh_tree(); self.load_script(); self.code.focus_set()

    def rename_script(self):
        s = self.cur_script()
        if not s: return
        n = simpledialog.askstring("Имя скрипта", "Новое имя (по нему можно обратиться: self.script(\"имя\")):", initialvalue=s["name"])
        if not n or not n.strip() or n == s["name"]: return
        if any(x["name"] == n.strip() for x in self.cur()["scripts"]):
            messagebox.showinfo("PyForge", "Скрипт с таким именем уже есть у этого объекта."); return
        self.snap_undo(); s["name"] = n.strip(); self.load_script()

    def delete_script(self):
        o, s = self.cur(), self.cur_script()
        if not s or not messagebox.askyesno("PyForge", f"Удалить скрипт «{s['name']}»?"): return
        self.snap_undo(); o["scripts"].pop(self.si); self.si = max(0, self.si - 1)
        self.refresh_tree(); self.load_script()

    def toggle_script(self):
        s = self.cur_script()
        if not s: return
        self.snap_undo(); s["on"] = bool(self.sc_on.get()); self.load_script()

    def toggle_expand(self):
        vp = self.vp
        if self._expanded:
            vp.sashpos(0, self._sash_prev)
        else:
            self._sash_prev = vp.sashpos(0); vp.sashpos(0, int(vp.winfo_height() * 0.2))
        self._expanded = not self._expanded

    def _ensure_script(self):
        if self.cur() and not self.cur_script(): self.add_script("")
        return self.cur_script()

    def insert_template(self):
        s = self.cur_script()
        if not s or s["code"].strip() or s["file"]: return
        self.snap_undo(); s["code"] = TEMPLATE; self.load_script()

    def script_from_file(self):
        """Скопировать код из .py в текущий скрипт (без связи с файлом)."""
        if not self.cur(): return
        p = filedialog.askopenfilename(filetypes=[("Python", "*.py"), ("Все", "*.*")])
        if not p: return
        try:
            with open(p, encoding="utf-8") as f: text = f.read()
        except Exception as ex:
            messagebox.showerror("PyForge", f"Не удалось прочитать файл:\n{ex}"); return
        self.snap_undo(); s = self._ensure_script(); s["code"], s["file"] = text, ""; self.load_script()

    def script_to_file(self):
        """Сохранить код текущего скрипта в .py и при желании связать его с файлом."""
        s = self.cur_script()
        if not s: return
        p = filedialog.asksaveasfilename(defaultextension=".py", initialfile=f"{s['name']}.py", filetypes=[("Python", "*.py")])
        if not p: return
        with open(p, "w", encoding="utf-8") as f: f.write(self.code.get("1.0", "end-1c"))
        if messagebox.askyesno("PyForge", "Связать скрипт с этим файлом?\nТогда код можно править во внешнем редакторе (VS Code и т.п.)."):
            self.snap_undo(); s["file"] = self._store_path(p); self.load_script()

    def script_link(self):
        if not self.cur(): return
        p = filedialog.askopenfilename(filetypes=[("Python", "*.py"), ("Все", "*.*")])
        if not p: return
        self.snap_undo(); s = self._ensure_script(); s["file"] = self._store_path(p); self.load_script()

    def script_unlink(self):
        s = self.cur_script()
        if not s or not s["file"]: return
        try:
            with open(self.imgs.path(s["file"]), encoding="utf-8") as f: s["code"] = f.read()
        except Exception: pass
        self.snap_undo(); s["file"] = ""; self.load_script()

    def add_files(self):
        """Массовое добавление: картинки → объекты-спрайты, .py → пустой объект со связанным скриптом."""
        if self.runner: return
        ps = filedialog.askopenfilenames(filetypes=[
            ("Картинки и скрипты", "*.png *.gif *.jpg *.jpeg *.bmp *.webp *.py"), ("Все", "*.*")])
        if not ps: return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        wx, wy = self.to_world(cw / 2, ch / 2); self.snap_undo()
        for n, p in enumerate(ps):
            x, y = self.snapv(wx - 32) + n * GRID * 2, self.snapv(wy - 32)
            stem = os.path.splitext(os.path.basename(p))[0]
            if p.lower().endswith(".py"):
                o = new_obj("empty", x=x, y=y, name=self.uniq(stem), scripts=[S("", stem, self._store_path(p))])
            else:
                o = new_obj("image", x=x, y=y, name=self.uniq(stem), image=self._store_path(p))
                o["w"], o["h"] = self._img_size(p)
            self.objs.append(o)
        self.sel = len(self.objs) - 1; self.refresh_tree(); self.select(self.sel)

    def _img_size(self, p):
        try:
            im = tk.PhotoImage(file=p); return im.width(), im.height()
        except Exception:
            if Image:
                try:
                    with Image.open(p) as im: return im.size
                except Exception: pass
        return 64, 64

    # ───── canvas: вид и мышь
    def to_world(self, px, py):
        return (px - self.ox) / self.zoom, (py - self.oy) / self.zoom

    def fit(self):
        if self.runner: return
        st = self.project["settings"]; w, h = self.cv.winfo_width(), self.cv.winfo_height()
        if w < 50: return
        self.zoom = min(1.0, (w - 60) / st["view_w"], (h - 60) / st["view_h"])
        self.ox = (w - st["view_w"] * self.zoom) / 2; self.oy = (h - st["view_h"] * self.zoom) / 2
        self.redraw()

    def order(self):
        return sorted(range(len(self.objs)), key=lambda i: self.objs[i]["z"])

    def pick(self, wx, wy):
        for i in reversed(self.order()):
            o = self.objs[i]
            if o["x"] <= wx <= o["x"] + o["w"] and o["y"] <= wy <= o["y"] + o["h"]:
                return i
        return None

    def redraw(self):
        if self.runner: return
        cv, z = self.cv, self.zoom; cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height(); st = self.project["settings"]
        g = GRID * z
        if g >= 8:
            x = self.ox % g
            while x < w: cv.create_line(x, 0, x, h, fill=T["grid"]); x += g
            y = self.oy % g
            while y < h: cv.create_line(0, y, w, y, fill=T["grid"]); y += g
        cv.create_rectangle(self.ox, self.oy, self.ox + st["view_w"] * z, self.oy + st["view_h"] * z,
                            fill=st["bg"], outline=T["accent"], width=1)
        for i in self.order():
            d = self.objs[i]; o = SimpleNamespace(**d)
            x0, y0 = o.x * z + self.ox, o.y * z + self.oy; x1, y1 = x0 + o.w * z, y0 + o.h * z
            if x1 < 0 or y1 < 0 or x0 > w or y0 > h: continue
            if o.visible and o.kind != "empty": draw_obj(cv, o, z, self.ox, self.oy, self.imgs)
            if o.kind == "empty":
                cv.create_rectangle(x0, y0, x1, y1, outline="#888", dash=(3, 3))
                cv.create_text(x0, y1 + 2, text=o.name, anchor="nw", fill="#666", font=("Arial", 8))
            if o.prefab:
                cv.create_rectangle(x0, y0, x1, y1, outline="#f08c00", dash=(4, 3))
            if o.body != "none" and o.kind != "empty":
                cv.create_rectangle(x0, y0, x1, y1, outline="#2f9e44" if o.solid else "#be4bdb", dash=(2, 4))
            if i == self.sel:
                cv.create_rectangle(x0, y0, x1, y1, outline=T["sel"], width=2)
                cv.create_rectangle(x1 - 5, y1 - 5, x1 + 5, y1 + 5, fill="white", outline=T["sel"], width=2)

    def on_down(self, e):
        self.cv.focus_set()
        if self.runner: self.runner.md(e); return
        wx, wy = self.to_world(e.x, e.y); o = self.cur()
        if o:
            sx, sy = (o["x"] + o["w"]) * self.zoom + self.ox, (o["y"] + o["h"]) * self.zoom + self.oy
            if abs(e.x - sx) <= 8 and abs(e.y - sy) <= 8:
                self.mode = dict(t="resize", pushed=False); return
        i = self.pick(wx, wy)
        if i is None: self.mode = None; self.select(None); return
        self.select(i); o = self.objs[i]
        self.mode = dict(t="move", dx=wx - o["x"], dy=wy - o["y"], pushed=False)

    def on_move(self, e):
        if self.runner: self.runner.mm(e); return
        m, o = self.mode, self.cur()
        if not m or not o: return
        if not m["pushed"]: self.snap_undo(); m["pushed"] = True
        wx, wy = self.to_world(e.x, e.y)
        if m["t"] == "move":
            o["x"], o["y"] = self.snapv(wx - m["dx"]), self.snapv(wy - m["dy"])
        else:
            o["w"], o["h"] = max(4, self.snapv(wx - o["x"])), max(4, self.snapv(wy - o["y"]))
        self.sync(); self.redraw()

    def on_up(self, e):
        if self.runner: self.runner.mu(e)
        self.mode = None

    def pan_down(self, e):
        self._pan = (e.x, e.y, self.ox, self.oy)

    def pan_move(self, e):
        if self.runner: return
        x, y, ox, oy = self._pan; self.ox, self.oy = ox + e.x - x, oy + e.y - y; self.redraw()

    def wheel(self, e):
        if self.runner: return
        d = 1 if (getattr(e, "delta", 0) > 0 or e.num == 4) else -1
        z = min(8, max(0.1, self.zoom * 1.1 ** d)); wx, wy = self.to_world(e.x, e.y)
        self.zoom = z; self.ox, self.oy = e.x - wx * z, e.y - wy * z; self.redraw()

    # ───── undo / redo
    def _restore(self, s):
        self.project = json.loads(s)
        if self.scene not in self.project["scenes"]: self.scene = next(iter(self.project["scenes"]))
        self.refresh_all()

    def undo(self):
        if self.runner or not self.undo_st: return
        self.redo_st.append(json.dumps(self.project)); self._restore(self.undo_st.pop())

    def redo(self):
        if self.runner or not self.redo_st: return
        self.undo_st.append(json.dumps(self.project)); self._restore(self.redo_st.pop())

    # ───── запуск
    def toggle_play(self):
        self.stop_play() if self.runner else self.play()

    def play(self):
        if self.runner: return
        self.console.configure(state="normal"); self.console.delete("1.0", "end"); self.console.configure(state="disabled")
        self.notes.select(self.console_tab); self.log("— запуск —")
        proj = normalize(copy.deepcopy(self.project))
        self.play_btn.config(text="■ Стоп (Esc)")
        r = Runner(self.root, self.cv, proj, self.scene, self.log, self._stopped, self.imgs)
        r.editor = True; self.runner = r; r.start()

    def stop_play(self):
        if self.runner: self.runner.stop()

    def _stopped(self):
        self.runner = None; self.play_btn.config(text="▶ Играть (F5)")
        self.log("— стоп —"); self.cv.configure(bg=T["canvas"]); self.redraw()

    # ───── файлы
    def confirm(self):
        return not self.dirty or messagebox.askyesno("PyForge", "Есть несохранённые изменения. Продолжить?")

    def new(self):
        if self.runner or not self.confirm(): return
        self.project, self.path, self.scene, self.sel = blank_project(), None, "Main", None
        self.undo_st.clear(); self.redo_st.clear(); self.dirty = False; self.imgs.clear(); self.refresh_all()

    def library(self):
        """Окно выбора готовой игры: выбранная игра создаётся как новый проект."""
        if self.runner: return
        win = tk.Toplevel(self.root); win.title("Библиотека игр"); win.geometry("640x380")
        win.configure(bg=T["bg"]); win.transient(self.root); win.grab_set()
        left = ttk.Frame(win, padding=8); left.pack(side="left", fill="y")
        right = ttk.Frame(win, padding=12); right.pack(side="left", fill="both", expand=True)
        lb = tk.Listbox(left, width=26, font=("Segoe UI", 11), activestyle="none", exportselection=False,
                        relief="flat", bg=T["panel"], fg=T["text"], selectbackground="#dbe7ff", selectforeground=T["text"],
                        highlightthickness=1, highlightbackground=T["line"])
        for name, _, _ in LIBRARY: lb.insert("end", "  " + name)
        lb.pack(fill="y", expand=True); lb.selection_set(0)
        title = ttk.Label(right, font=("Segoe UI", 13, "bold"), wraplength=360); title.pack(anchor="w")
        desc = ttk.Label(right, wraplength=360, justify="left"); desc.pack(anchor="w", pady=10)
        ttk.Label(right, wraplength=360, justify="left", foreground="#6b7280",
                  text="Игра создаётся с готовыми объектами и скриптами — их можно менять как угодно. "
                       "Вместо графики квадратики: свои картинки добавляются через «+ Файлы…».").pack(anchor="w")
        def show(e=None):
            i = lb.curselection()[0] if lb.curselection() else 0
            title.config(text=LIBRARY[i][0]); desc.config(text=LIBRARY[i][1])
        def create(e=None):
            i = lb.curselection()[0] if lb.curselection() else 0
            if self.new_from(LIBRARY[i][2], LIBRARY[i][0]): win.destroy()
        lb.bind("<<ListboxSelect>>", show); lb.bind("<Double-Button-1>", create); show()
        bt = ttk.Frame(right); bt.pack(side="bottom", anchor="e", fill="x")
        ttk.Button(bt, text="Создать игру", style="Accent.TButton", command=create).pack(side="right", padx=2)
        ttk.Button(bt, text="Отмена", command=win.destroy).pack(side="right", padx=2)

    def new_from(self, builder, name=""):
        if not self.confirm(): return False
        self.project, self.path, self.scene, self.sel = normalize(builder()), None, "Main", None
        self.undo_st.clear(); self.redo_st.clear(); self.dirty = False; self.imgs.clear(); self.imgs.base = os.getcwd()
        self.refresh_all(); self.root.after(50, self.fit)
        self.log(f"Создана игра: {name}. Нажми F5, чтобы запустить.")
        return True

    def open_dlg(self):
        if self.runner or not self.confirm(): return
        p = filedialog.askopenfilename(filetypes=[("Проект PyForge", "*.json")])
        if p: self.open(p)

    def open(self, p):
        try:
            with open(p, encoding="utf-8") as f: pr = json.load(f)
            assert "scenes" in pr and pr["scenes"]
        except Exception as ex:
            messagebox.showerror("PyForge", f"Не удалось открыть проект:\n{ex}"); return
        self.project = normalize(pr); self.path = p; self.imgs.base = os.path.dirname(os.path.abspath(p)); self.imgs.clear()
        st = self.project["settings"]["start"]
        self.scene = st if st in pr["scenes"] else next(iter(pr["scenes"]))
        self.sel = None; self.undo_st.clear(); self.redo_st.clear(); self.dirty = False
        self.refresh_all(); self.root.after(100, self.fit)

    def save(self):
        if not self.path:
            p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Проект PyForge", "*.json")])
            if not p: return
            self.path = p; self.imgs.base = os.path.dirname(os.path.abspath(p))
        with open(self.path, "w", encoding="utf-8") as f: json.dump(self.project, f, ensure_ascii=False, indent=1)
        self.dirty = False; self._title(); self.log(f"Сохранено: {self.path}")

    def export(self):
        d = filedialog.askdirectory(title="Папка для готовой игры")
        if not d: return
        proj = copy.deepcopy(self.project); proj["settings"]["start"] = self.scene
        os.makedirs(os.path.join(d, "assets"), exist_ok=True)
        for sc in proj["scenes"].values():
            for o in sc:
                src = self.imgs.path(o["image"]) if o.get("image") else None
                if src and os.path.exists(src):
                    shutil.copy(src, os.path.join(d, "assets", os.path.basename(src)))
                    o["image"] = "assets/" + os.path.basename(src)
        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        for sc in proj["scenes"].values():
            for o in sc:
                for x in o["scripts"]:
                    src = self.imgs.path(x["file"]) if x["file"] else None
                    if src and os.path.exists(src):
                        shutil.copy(src, os.path.join(d, "scripts", os.path.basename(src)))
                        x["file"] = "scripts/" + os.path.basename(src)
        # модули, которые скрипты импортируют (import мой_модуль), — копируем только их и то, что они сами импортируют
        texts = []
        for sc in self.project["scenes"].values():
            for o in sc:
                for x in o["scripts"]:
                    if not x["file"]: texts.append(x["code"]); continue
                    try:
                        with open(self.imgs.path(x["file"]), encoding="utf-8") as f: texts.append(f.read())
                    except Exception: pass
        done = {"pyforge", "pyforge_player"}
        while texts:
            for m in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", texts.pop(), re.M):
                if m in done: continue
                done.add(m)
                for cand in (os.path.join(self.imgs.base, m + ".py"), os.path.join(self.imgs.base, "scripts", m + ".py")):
                    if os.path.exists(cand):
                        shutil.copy(cand, os.path.join(d, os.path.basename(cand)))
                        with open(cand, encoding="utf-8") as f: texts.append(f.read())
        with open(os.path.join(d, "game.json"), "w", encoding="utf-8") as f: json.dump(proj, f, ensure_ascii=False, indent=1)
        # лёгкий плеер (если лежит рядом с редактором) вместо целого редактора
        here = os.path.dirname(os.path.abspath(__file__)); pl = os.path.join(here, "pyforge_player.py")
        use_player = os.path.exists(pl)
        runner = pl if use_player else os.path.abspath(__file__)
        shutil.copy(runner, os.path.join(d, os.path.basename(runner)))
        cmd = f"{os.path.basename(runner)} " + ("game.json" if use_player else "--play game.json")
        with open(os.path.join(d, "run.bat"), "w") as f: f.write(f"@echo off\r\ncd /d \"%~dp0\"\r\npython {cmd}\r\n")
        with open(os.path.join(d, "run.sh"), "w") as f: f.write(f"#!/bin/sh\ncd \"$(dirname \"$0\")\"\npython3 {cmd}\n")
        messagebox.showinfo("PyForge", f"Игра экспортирована в:\n{d}\n\nЗапуск: run.bat / run.sh\nEXE: pip install pyinstaller, затем\npyinstaller --onefile --noconsole {os.path.basename(runner)}")

    def quit(self):
        self.stop_play()
        if self.confirm(): self.root.destroy()

# ───────────────────────── точка входа ─────────────────────────
def play_standalone(root, path):
    with open(path, encoding="utf-8") as f: proj = normalize(json.load(f))
    st = proj["settings"]
    root.title(os.path.splitext(os.path.basename(path))[0]); root.geometry(f"{st['view_w']}x{st['view_h']}")
    cv = tk.Canvas(root, highlightthickness=0, bg=st["bg"]); cv.pack(fill="both", expand=True)
    imgs = Images(os.path.dirname(os.path.abspath(path)))
    scene = st["start"] if st["start"] in proj["scenes"] else next(iter(proj["scenes"]))
    r = Runner(root, cv, proj, scene, print, root.destroy, imgs)
    cv.bind("<Button-1>", r.md); cv.bind("<ButtonRelease-1>", r.mu); cv.bind("<Motion>", r.mm)
    root.after(50, r.start)

def main():
    a = sys.argv[1:]; root = tk.Tk()
    if a and a[0] == "--play" and len(a) > 1: play_standalone(root, a[1])
    else: App(root, a[0] if a else None)
    root.mainloop()

if __name__ == "__main__":
    main()
