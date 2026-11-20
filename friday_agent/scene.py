"""Friday Code ambient scene: an original pixel mascot ("Patch") in a living seasonal landscape.

Everything is drawn on a small RGB pixel canvas and rendered with half-block characters
(two pixels per terminal cell), so it costs one ``Static`` update per frame and no images.

* Four seasons with their own landscape (spring sunflowers and crops, summer trees, autumn
  falling leaves, winter snow and a snowman). The season follows a Northern Hemisphere
  calendar; ``/scene cycle`` lets the 10-minute change pick seasons too.
* Six weathers (sunny, cloudy, rainy, snowy, windy, clear) that change the sky, particles,
  ground and Patch's outfit (umbrella and raincoat, scarf and beanie, sunglasses, flower...).
* Day and night from the computer's local time, with smooth dawn and dusk.
* A new variation every 10 minutes, cross-faded over a few seconds; Patch keeps walking.
* Reactions to chat activity: acknowledge, attentive while generating, happy, concerned.

The module has no Textual dependency: ``Scene.render(width, now)`` returns a Rich renderable.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from rich.segment import Segment
from rich.style import Style

RGB = tuple[int, int, int]
Pixel = RGB | None

SEASONS = ("spring", "summer", "autumn", "winter")
WEATHERS = ("sunny", "cloudy", "rainy", "snowy", "windy", "clear")
# Which weather suits which season, and how often (weights).
SEASON_WEATHER: dict[str, dict[str, int]] = {
    "spring": {"sunny": 3, "clear": 2, "cloudy": 2, "rainy": 2, "windy": 2},
    "summer": {"sunny": 4, "clear": 3, "cloudy": 1, "rainy": 1, "windy": 1},
    "autumn": {"windy": 3, "cloudy": 2, "rainy": 2, "sunny": 2, "clear": 1},
    "winter": {"snowy": 4, "cloudy": 2, "clear": 2, "windy": 1, "sunny": 1},
}
VARIATION_SECONDS = 600.0   # a new season/weather variation every 10 minutes
FADE_SECONDS = 4.0          # cross-fade between variations
SCENE_PX_HEIGHT = 20        # 10 terminal rows
GROUND_PX = 2


def season_for(date: datetime) -> str:
    """Northern Hemisphere meteorological seasons."""
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(date.month, "autumn")


# Sunrise / sunset windows (local hours) per season: (rise start, rise end, set start, set end)
_DAYLIGHT = {
    "spring": (6.0, 7.0, 18.5, 19.5), "summer": (5.0, 6.0, 20.0, 21.0),
    "autumn": (6.5, 7.5, 17.5, 18.5), "winter": (7.0, 8.0, 16.5, 17.5),
}


def daylight(season: str, hour: float) -> float:
    """0.0 = night, 1.0 = full day, smooth in between (dawn and dusk)."""
    rise0, rise1, set0, set1 = _DAYLIGHT[season]
    if hour <= rise0 or hour >= set1:
        return 0.0
    if rise1 <= hour <= set0:
        return 1.0
    t = (hour - rise0) / (rise1 - rise0) if hour < rise1 else (set1 - hour) / (set1 - set0)
    return t * t * (3 - 2 * t)


def hex_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def shade(c: RGB, f: float) -> RGB:
    return (min(255, int(c[0] * f)), min(255, int(c[1] * f)), min(255, int(c[2] * f)))


# --------------------------------------------------------------------------- canvas


class Canvas:
    def __init__(self, width: int, height: int, fill: Pixel = None):
        self.w, self.h = width, height
        self.px: list[list[Pixel]] = [[fill] * width for _ in range(height)]

    def set(self, x: int, y: int, c: Pixel) -> None:
        if c is not None and 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = c

    def get(self, x: int, y: int) -> Pixel:
        return self.px[y][x] if 0 <= x < self.w and 0 <= y < self.h else None

    def rect(self, x: int, y: int, w: int, h: int, c: Pixel) -> None:
        for yy in range(max(0, y), min(self.h, y + h)):
            row = self.px[yy]
            for xx in range(max(0, x), min(self.w, x + w)):
                row[xx] = c

    def sprite(self, x: int, y: int, rows: Iterable[str], colors: dict[str, Pixel], flip: bool = False) -> None:
        for dy, line in enumerate(rows):
            if flip:
                line = line[::-1]
            for dx, ch in enumerate(line):
                if ch != "." and ch != " ":
                    self.set(x + dx, y + dy, colors.get(ch))

    def map(self, fn) -> None:
        for row in self.px:
            for i, c in enumerate(row):
                if c is not None:
                    row[i] = fn(c)


_STYLE_CACHE: dict[tuple[Pixel, Pixel], Style] = {}


def _style(fg: Pixel, bg: Pixel) -> Style:
    key = (fg, bg)
    style = _STYLE_CACHE.get(key)
    if style is None:
        style = Style(color=f"rgb({fg[0]},{fg[1]},{fg[2]})" if fg else None,
                      bgcolor=f"rgb({bg[0]},{bg[1]},{bg[2]})" if bg else None)
        if len(_STYLE_CACHE) > 4096:
            _STYLE_CACHE.clear()
        _STYLE_CACHE[key] = style
    return style


class PixelFrame:
    """Rich renderable: a canvas drawn with '▀' (top pixel = fg, bottom pixel = bg)."""

    def __init__(self, canvas: Canvas, base: Pixel = None):
        self.lines: list[list[Segment]] = []
        px = canvas.px
        for y in range(0, canvas.h - 1, 2):
            top_row, bottom_row = px[y], px[y + 1]
            segments: list[Segment] = []
            run_text, run_style = "", None
            for x in range(canvas.w):
                top, bottom = top_row[x] or base, bottom_row[x] or base
                if top == bottom:
                    ch, style = " ", _style(None, top)
                elif top is None:
                    ch, style = "▄", _style(bottom, None)
                else:
                    ch, style = "▀", _style(top, bottom)
                if style is run_style:
                    run_text += ch
                else:
                    if run_text:
                        segments.append(Segment(run_text, run_style))
                    run_text, run_style = ch, style
            if run_text:
                segments.append(Segment(run_text, run_style))
            self.lines.append(segments)

    def __rich_console__(self, console, options):
        for i, line in enumerate(self.lines):
            yield from line
            if i < len(self.lines) - 1:
                yield Segment.line()


# --------------------------------------------------------------------------- mascot "Patch"

# 11 x 14 pixels, facing right. Legend:
#   a antenna light   s stalk   H head   h head shade   V visor   E eye
#   B body   b body shade   P chest light   A arm   L leg   F foot
PATCH_BODY = [
    ".....a.....",
    ".....s.....",
    "..HHHHHHH..",
    ".HHHHHHHHHh",
    ".HVVVVVVVHh",
    ".HVVVVVVVHh",
    ".HVVVVVVVHh",
    ".HVVVVVVVHh",
    "..hhhhhhh..",
    "...BBBBBb..",
    "..ABBPBbA..",
    "...BBBBBb..",
]
LEGS = {  # two walking frames + standing
    "stand": ["...L...L...", "..FF...FF.."],
    "walk1": ["..L.....L..", ".FF.....FF."],
    "walk2": ["....L.L....", "...FF.FF..."],
}
# eyes: (x, y) pixels inside the visor, facing right
EYES = {
    "normal": [(4, 5), (4, 6), (7, 5), (7, 6)],
    "blink": [(4, 6), (7, 6)],
    "happy": [(3, 6), (4, 5), (5, 6), (6, 6), (7, 5), (8, 6)],
    "concerned": [(4, 6), (7, 6), (3, 5), (8, 5)],
    "up": [(4, 4), (4, 5), (7, 4), (7, 5)],
    "wide": [(4, 5), (4, 6), (5, 5), (5, 6), (7, 5), (7, 6), (8, 5), (8, 6)],
}
PATCH_W, PATCH_H = 11, 14

PALETTE_PATCH = {
    "H": hex_rgb("#ece3d2"), "h": hex_rgb("#c9bca5"), "V": hex_rgb("#2a2838"), "E": hex_rgb("#8fe3cf"),
    "B": hex_rgb("#e3d8c4"), "b": hex_rgb("#bfb19a"), "P": hex_rgb("#d87756"), "A": hex_rgb("#8c8a96"),
    "L": hex_rgb("#7d7a88"), "F": hex_rgb("#4f4c5a"), "s": hex_rgb("#8c8a96"), "a": hex_rgb("#d87756"),
}


@dataclass
class Outfit:
    beanie: bool = False
    scarf: RGB | None = None
    sweater: bool = False
    raincoat: bool = False
    umbrella: bool = False
    sunglasses: bool = False
    flower: bool = False
    straw_hat: bool = False

    @property
    def label(self) -> str:
        parts = [name for name, on in (("beanie", self.beanie), ("scarf", self.scarf), ("sweater", self.sweater),
                                       ("raincoat", self.raincoat), ("umbrella", self.umbrella),
                                       ("sunglasses", self.sunglasses), ("flower", self.flower),
                                       ("straw hat", self.straw_hat)) if on]
        return ", ".join(parts) or "no accessories"


def outfit_for(season: str, weather: str, night: bool) -> Outfit:
    """Clothing follows season and weather, so it always matches the environment."""
    o = Outfit()
    if weather == "rainy":
        o.raincoat = o.umbrella = True
    if season == "winter":
        o.beanie, o.sweater, o.scarf = True, True, hex_rgb("#d87756")
    elif season == "autumn":
        o.scarf = hex_rgb("#c99a3a")
    elif season == "spring" and weather != "rainy":
        o.flower = True
    elif season == "summer" and weather in {"sunny", "clear"} and not night:
        o.sunglasses = True
        o.straw_hat = weather == "sunny"
    return o


# --------------------------------------------------------------------------- environment


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    color: RGB
    kind: str
    phase: float = 0.0
    life: float = 99.0


@dataclass
class Cloud:
    x: float
    y: int
    w: int
    h: int
    speed: float
    seed: int


@dataclass
class Variation:
    season: str
    weather: str
    seed: int

    @property
    def key(self) -> tuple[str, str]:
        return self.season, self.weather


class Environment:
    """Everything behind Patch for one season/weather variation, including its own particles."""

    def __init__(self, variation: Variation, width: int):
        self.v = variation
        self.rng = random.Random(variation.seed)
        self.width = 0
        self.particles: list[Particle] = []
        self.clouds: list[Cloud] = []
        self.spawn_acc = 0.0
        self.props_seed = self.rng.randint(0, 1_000_000)
        self.resize(width)

    # ---------------------------------------------------------- layout

    def resize(self, width: int) -> None:
        if width == self.width:
            return
        self.width = width
        rng = random.Random(self.props_seed)
        count = {"clear": 0, "sunny": 2, "cloudy": 5, "rainy": 5, "snowy": 4, "windy": 3}[self.v.weather]
        count = max(0, int(count * width / 100 + 0.5)) if count else 0
        speed = 5.0 if self.v.weather == "windy" else 0.7
        self.clouds = [Cloud(x=rng.uniform(0, width), y=rng.randint(1, 5), w=rng.randint(12, 22),
                             h=rng.randint(3, 5), speed=speed * rng.uniform(0.7, 1.3), seed=rng.randint(0, 999))
                       for _ in range(count)]
        self.tree_x = max(28, width - 18)
        self.flowers = [(rng.randint(2, max(3, width - 24)), rng.choice(["#e98aa5", "#f2d06b", "#f4f1e8", "#b48be0"]))
                        for _ in range(max(4, width // 9))]
        self.sunflowers = sorted(rng.sample(range(4, max(6, width // 3)), k=min(3, max(1, width // 40))))
        self.crops_x = max(20, width // 2 - 8)
        self.pumpkins = [rng.randint(width // 3, max(width // 3 + 1, self.tree_x - 6)) for _ in range(2)]
        self.snowman_x = rng.randint(4, max(5, width // 4))
        self.stars = [(rng.randint(0, width - 1), rng.randint(0, 9), rng.random() * 6.28) for _ in range(width // 5)]
        self.leaf_litter = [(rng.randint(0, width - 1), rng.choice(["#c4622d", "#d99a3a", "#8a4b2a", "#b8742a"]))
                            for _ in range(width // 3)]

    # ---------------------------------------------------------- simulation

    @property
    def wind(self) -> float:
        return {"windy": -9.0, "rainy": -3.0, "snowy": -1.5}.get(self.v.weather, -0.8)

    def update(self, dt: float, height: int) -> None:
        w = self.width
        for cloud in self.clouds:
            cloud.x -= cloud.speed * dt
            if cloud.x + cloud.w < -2:
                cloud.x = w + self.rng.uniform(0, 10)
        weather, season = self.v.weather, self.v.season
        rate, kind = 0.0, ""
        if weather == "rainy":
            rate, kind = w * 0.9, "rain"
        elif weather == "snowy":
            rate, kind = w * 0.18, "snow"
        elif season == "autumn":
            rate, kind = (w * 0.06 if weather == "windy" else w * 0.02), "leaf"
        elif weather == "windy":
            rate, kind = w * 0.03, "petal" if season == "spring" else "leaf"
        if season == "winter" and weather in {"cloudy", "windy"}:
            rate, kind = w * 0.05, "snow"
        self.spawn_acc += rate * dt
        limit = max(40, w * 2)
        while self.spawn_acc >= 1 and len(self.particles) < limit:
            self.spawn_acc -= 1
            self.particles.append(self._spawn(kind, height))
        self.spawn_acc = min(self.spawn_acc, 5)
        ground = height - GROUND_PX
        alive = []
        for p in self.particles:
            p.phase += dt
            if p.kind == "snow":
                p.x += (p.vx + math.sin(p.phase * 2 + p.y) * 1.5) * dt
            elif p.kind in {"leaf", "petal"}:
                p.x += (p.vx + math.sin(p.phase * 3) * 3) * dt
            else:
                p.x += p.vx * dt
            p.y += p.vy * dt
            if p.kind == "splash":
                p.life -= dt
                if p.life > 0:
                    alive.append(p)
                continue
            if p.y >= ground:
                if p.kind == "rain" and self.rng.random() < 0.35:
                    alive.append(Particle(p.x, ground - 1, 0, 0, hex_rgb("#a9c3dd"), "splash", life=0.15))
                continue
            if -4 <= p.x <= w + 12:
                alive.append(p)
        self.particles = alive

    def _spawn(self, kind: str, height: int) -> Particle:
        r, w, wind = self.rng, self.width, self.wind
        if kind == "rain":
            return Particle(r.uniform(0, w + 8), r.uniform(-4, 0), wind, r.uniform(26, 34), hex_rgb("#8fb0d0"), "rain")
        if kind == "snow":
            return Particle(r.uniform(0, w + 6), r.uniform(-3, 0), wind, r.uniform(2.5, 4.5),
                            hex_rgb("#f4f6fb"), "snow", phase=r.random() * 6)
        if kind == "petal":
            return Particle(r.uniform(w * 0.3, w + 8), r.uniform(0, height * 0.6), wind * 1.2, r.uniform(1.5, 3),
                            hex_rgb(r.choice(["#f2a7bd", "#f7d1dc"])), "petal")
        colors = (["#d9692f", "#e3a13b", "#a8532a", "#c98a2b"] if self.v.season == "autumn"
                  else ["#6fa25a", "#8cbf6a"])
        origin = self.tree_x + r.uniform(-6, 6) if self.v.season == "autumn" and r.random() < 0.7 else r.uniform(0, w + 8)
        return Particle(origin, r.uniform(3, 8), wind * r.uniform(0.6, 1.2), r.uniform(2, 4),
                        hex_rgb(r.choice(colors)), "leaf", phase=r.random() * 6)

    def kick(self, x: int, y: int, kind: str) -> None:
        """A small burst from Patch's feet (leaves, snow or splashes)."""
        for _ in range(6):
            if kind == "leaf":
                color = hex_rgb(self.rng.choice(["#d9692f", "#e3a13b", "#a8532a"]))
            elif kind == "snow":
                color = hex_rgb("#f4f6fb")
            else:
                color = hex_rgb("#a9c3dd")
            self.particles.append(Particle(x + self.rng.uniform(-1, 3), y, self.rng.uniform(-6, 6),
                                           self.rng.uniform(-9, -4), color, "splash", life=0.6))

    # ---------------------------------------------------------- drawing

    def sky(self, light: float) -> tuple[RGB, RGB]:
        weather = self.v.weather
        day = {
            "sunny": ("#3f74a6", "#9cc3de"), "clear": ("#4a80b3", "#a9cde4"),
            "cloudy": ("#5a6574", "#8d97a3"), "rainy": ("#46505d", "#6c7682"),
            "snowy": ("#7f8b99", "#c3cad3"), "windy": ("#4d7398", "#a2bfd3"),
        }[weather]
        night = ("#14131f", "#262840") if weather in {"sunny", "clear", "windy"} else ("#1a1a24", "#2b2c38")
        dusk = ("#3b3556", "#d98a6a") if weather in {"sunny", "clear", "windy"} else ("#3a3846", "#7b6f74")
        top_d, bot_d = hex_rgb(day[0]), hex_rgb(day[1])
        top_n, bot_n = hex_rgb(night[0]), hex_rgb(night[1])
        top_k, bot_k = hex_rgb(dusk[0]), hex_rgb(dusk[1])
        if light >= 0.5:
            t = (light - 0.5) * 2
            return lerp(top_k, top_d, t), lerp(bot_k, bot_d, t)
        t = light * 2
        return lerp(top_n, top_k, t), lerp(bot_n, bot_k, t)

    def draw(self, c: Canvas, t: float, light: float, hour: float) -> None:
        w, h = c.w, c.h
        ground_y = h - GROUND_PX
        season, weather = self.v.season, self.v.weather
        top, bottom = self.sky(light)
        for y in range(ground_y):
            c.rect(0, y, w, 1, lerp(top, bottom, y / max(1, ground_y - 1)))

        # stars and moon at night, sun by day
        overcast = weather in {"cloudy", "rainy", "snowy"}
        if light < 0.6 and not overcast:
            for sx, sy, ph in self.stars:
                tw = 0.55 + 0.45 * math.sin(t * 1.7 + ph)
                if tw > 0.35:
                    c.set(sx, sy, lerp(top, hex_rgb("#f2f2f6"), tw * (1 - light / 0.6)))
        rise0, _, _, set1 = _DAYLIGHT[season]
        if light > 0.05 and weather not in {"rainy", "snowy"}:
            frac = min(1.0, max(0.0, (hour - rise0) / (set1 - rise0)))
            self._sun(c, int(4 + frac * (w - 14)), int(2 + 4 * (1 - math.sin(frac * math.pi))), light, overcast, t)
        if light < 0.95:
            night_frac = min(1.0, max(0.0, ((hour - set1) % 24) / max(1.0, 24 - (set1 - rise0))))
            self._moon(c, int(w - 12 - night_frac * (w - 20)), 2, 1 - light, overcast)

        self._hills(c, ground_y, light)
        for cloud in self.clouds:
            self._cloud(c, cloud, light)
        self._ground(c, ground_y, t)
        self._props(c, ground_y, t, light)

    def draw_particles(self, c: Canvas) -> None:
        for p in self.particles:
            x, y = int(p.x), int(p.y)
            if p.kind == "rain":
                c.set(x, y, p.color)
                c.set(x + 1, y - 1, shade(p.color, 0.8))
            else:
                c.set(x, y, p.color)

    def _sun(self, c: Canvas, x: int, y: int, light: float, overcast: bool, t: float) -> None:
        core, glow = hex_rgb("#fde7a3"), hex_rgb("#f6c56b")
        if overcast:
            core, glow = lerp(core, hex_rgb("#b9bcc4"), 0.55), lerp(glow, hex_rgb("#9aa0aa"), 0.6)
        disc = ["..###..", ".#####.", "#######", "#######", "#######", ".#####.", "..###.."]
        for dy, row in enumerate(disc):
            for dx, ch in enumerate(row):
                if ch == "#":
                    edge = dx in (0, 6) or dy in (0, 6) or row[max(0, dx - 1)] == "." or row[min(6, dx + 1)] == "."
                    c.set(x + dx, y + dy, lerp(c.get(x + dx, y + dy) or core, glow if edge else core, light))
        if self.v.weather == "sunny" and not overcast:
            pulse = 1 if int(t * 1.5) % 2 else 0
            for dx, dy in ((-2 - pulse, 3), (8 + pulse, 3), (3, -2 - pulse), (3, 8 + pulse)):
                c.set(x + dx, y + dy, lerp(c.get(x + dx, y + dy) or glow, glow, light))

    def _moon(self, c: Canvas, x: int, y: int, strength: float, overcast: bool) -> None:
        moon = hex_rgb("#f9faf4") if not overcast else hex_rgb("#9a9ca6")
        disc = [".####.", "##....", "#.....", "#.....", "##....", ".####."]  # crescent, like a thin C
        thick = [".####.", "###...", "##....", "##....", "###...", ".####."]
        for dy, row in enumerate(thick):
            for dx, ch in enumerate(row):
                if ch == "#":
                    base = c.get(x + dx, y + dy) or moon
                    tone = moon if disc[dy][dx] == "#" else shade(moon, 0.78)
                    c.set(x + dx, y + dy, lerp(base, tone, strength))

    def _hills(self, c: Canvas, ground_y: int, light: float) -> None:
        tone = {"spring": "#5d8a5a", "summer": "#4b7a4a", "autumn": "#8a6a45", "winter": "#c9d0d8"}[self.v.season]
        base = lerp(hex_rgb(tone), c.get(0, ground_y - 1) or hex_rgb(tone), 0.45)
        for x in range(c.w):
            height = 2 + int(1.6 * math.sin(x / 9.0 + self.props_seed % 7) + 1.2 * math.sin(x / 4.3 + 1.3))
            for y in range(ground_y - max(1, height), ground_y):
                c.set(x, y, base)

    def _cloud(self, c: Canvas, cloud: Cloud, light: float) -> None:
        weather = self.v.weather
        tone = {"rainy": "#5d6470", "cloudy": "#7d8591", "snowy": "#b9c0ca"}.get(weather, "#e8ecf2")
        body = hex_rgb(tone)
        night_tone = hex_rgb("#3c3c42") if weather not in {"rainy", "cloudy"} else hex_rgb("#34343c")
        body = lerp(night_tone, body, light)
        dark = shade(body, 0.86)
        x0, y0, w, h = int(cloud.x), cloud.y, cloud.w, cloud.h
        for dy in range(h):
            inset = max(0, (h - 1 - dy) * 2 - 1) if dy < h - 1 else 0
            bump = 3 if dy == 0 else 0
            for dx in range(inset + bump, w - inset):
                # dithered underside, like the reference's pixel clouds
                tone_px = dark if dy == h - 1 and (dx + dy) % 2 else body
                c.set(x0 + dx, y0 + dy, tone_px)

    def _ground(self, c: Canvas, ground_y: int, t: float) -> None:
        season, weather = self.v.season, self.v.weather
        top = {"spring": "#6faa4f", "summer": "#4f9a45", "autumn": "#9b7a3f", "winter": "#eef2f7"}[season]
        soil = {"spring": "#5a4231", "summer": "#4d3a2a", "autumn": "#5b3f28", "winter": "#c8d2de"}[season]
        top_c, soil_c = hex_rgb(top), hex_rgb(soil)
        if weather == "rainy":
            top_c, soil_c = shade(top_c, 0.72), shade(soil_c, 0.75)
        c.rect(0, ground_y, c.w, 1, top_c)
        c.rect(0, ground_y + 1, c.w, GROUND_PX - 1, soil_c)
        sway = int(math.sin(t * (3.5 if weather == "windy" else 1.2)) * (1.2 if weather == "windy" else 0.6) + 0.5)
        if season in {"spring", "summer"}:
            blade = shade(top_c, 1.2)
            for x in range(1, c.w, 5):
                c.set(x + (sway if x % 2 else 0), ground_y - 1, blade)
        if season == "autumn":
            for x, color in self.leaf_litter:
                c.set(x, ground_y, hex_rgb(color))
        if weather == "rainy":  # puddles
            for x in range(6, c.w, 23):
                for dx in range(5):
                    c.set(x + dx, ground_y, hex_rgb("#7d98b3"))
                c.set(x + 1, ground_y, hex_rgb("#b9cfe3"))

    def _props(self, c: Canvas, ground_y: int, t: float, light: float) -> None:
        season, weather = self.v.season, self.v.weather
        windy = weather == "windy"
        sway = math.sin(t * (3.0 if windy else 1.1)) * (1.4 if windy else 0.5)
        self._tree(c, self.tree_x, ground_y, sway)
        if season == "spring":
            for x, color in self.flowers:
                if abs(x - self.tree_x) > 5:
                    c.set(x + int(sway), ground_y - 1, hex_rgb(color))
                    c.set(x, ground_y, hex_rgb("#4f8c3d"))
            for i, x in enumerate(self.sunflowers):
                self._sunflower(c, x, ground_y, sway, i)
            self._crops(c, self.crops_x, ground_y, t, sway)
        elif season == "summer":
            for x in (self.tree_x - 14, max(2, self.tree_x // 3)):
                self._bush(c, x, ground_y)
        elif season == "autumn":
            for x in self.pumpkins:
                self._pumpkin(c, x, ground_y)
        elif season == "winter":
            self._snowman(c, self.snowman_x, ground_y)

    def _tree(self, c: Canvas, x: int, ground_y: int, sway: float) -> None:
        season = self.v.season
        trunk = hex_rgb("#6b4a33")
        for y in range(ground_y - 7, ground_y):
            c.set(x + 3, y, trunk)
            c.set(x + 4, y, shade(trunk, 0.85))
        canopy = [
            "....######....",
            "..##########..",
            ".############.",
            "##############",
            "##############",
            ".############.",
            "...########...",
        ]
        s = int(round(sway))
        top = ground_y - 7 - len(canopy) + 1
        if season == "winter":
            branch = hex_rgb("#5a4030")
            snow = hex_rgb("#f2f5fa")
            for dx, dy in ((1, -2), (2, -3), (6, -2), (5, -3), (3, -4), (4, -5), (0, -1), (7, -1)):
                c.set(x + dx + s * (dy < -3), ground_y - 7 + dy, branch)
            for dx, dy in ((1, -3), (2, -4), (6, -3), (5, -4), (4, -6)):
                c.set(x + dx, ground_y - 7 + dy, snow)
            return
        colors = {
            "spring": ["#7cbf62", "#93cf72", "#f2a7bd"],
            "summer": ["#3f8a3e", "#4f9d48", "#2f6e33"],
            "autumn": ["#d9692f", "#e3a13b", "#a8532a"],
        }[season]
        rng = random.Random(self.props_seed + 5)
        for dy, row in enumerate(canopy):
            for dx, ch in enumerate(row):
                if ch == "#":
                    pick = rng.random()
                    color = colors[0] if pick < 0.55 else colors[1] if pick < 0.85 else colors[2]
                    c.set(x - 5 + dx + (s if dy < 3 else 0), top + dy, hex_rgb(color))

    def _sunflower(self, c: Canvas, x: int, ground_y: int, sway: float, index: int) -> None:
        height = 6 + index % 2
        stem = hex_rgb("#4f8c3d")
        s = int(round(sway))
        for y in range(ground_y - height, ground_y):
            c.set(x + (s if y < ground_y - height + 2 else 0), y, stem)
        c.set(x + 1, ground_y - 3, stem)  # leaf
        hx, hy = x + s, ground_y - height - 2
        petal, center = hex_rgb("#f2c230"), hex_rgb("#6b4220")
        for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            c.set(hx + dx, hy + dy, petal)
        c.set(hx, hy, center)

    def _crops(self, c: Canvas, x: int, ground_y: int, t: float, sway: float) -> None:
        soil = hex_rgb("#6b4c34")
        sprout = hex_rgb("#86c45f")
        growth = 1 + int((t / 40) % 3)  # the crops slowly grow
        for row in range(4):
            px = x + row * 3
            c.set(px, ground_y, soil)
            c.set(px + 1, ground_y, soil)
            for g in range(growth):
                c.set(px + (int(sway) if g == growth - 1 else 0), ground_y - 1 - g, sprout)

    def _bush(self, c: Canvas, x: int, ground_y: int) -> None:
        c.sprite(x, ground_y - 3, [".###.", "#####", "#####"],
                 {"#": hex_rgb("#3f8a3e")})
        c.set(x + 1, ground_y - 3, hex_rgb("#5aa64f"))

    def _pumpkin(self, c: Canvas, x: int, ground_y: int) -> None:
        c.sprite(x, ground_y - 3, ["..g..", ".ooo.", "ooOoo"],
                 {"g": hex_rgb("#5a7a32"), "o": hex_rgb("#d9732f"), "O": hex_rgb("#b85a22")})

    def _snowman(self, c: Canvas, x: int, ground_y: int) -> None:
        c.sprite(x, ground_y - 9, [
            "..kkk..", ".kkkkk.", "..www..", ".wbwbw.", "..wnw..", ".rrrrr.",
            "wwwwwww", "wwwbwww", ".wwwww.",
        ], {"k": hex_rgb("#2a2838"), "w": hex_rgb("#f4f6fb"), "b": hex_rgb("#2a2838"),
            "n": hex_rgb("#e0823a"), "r": hex_rgb("#c8553d")})


# --------------------------------------------------------------------------- Patch behaviour


@dataclass
class Patch:
    x: float = 6.0
    facing: int = 1              # 1 right, -1 left
    action: str = "idle"         # idle | walk | interact
    action_until: float = 0.0
    hop: float = 0.0             # seconds left in a hop
    blink_at: float = 0.0
    blink_until: float = 0.0
    reaction: str = ""           # ack | attentive | happy | concerned
    reaction_until: float = 0.0
    interaction: str = ""
    step_phase: float = 0.0
    rng: random.Random = field(default_factory=random.Random)


class Scene:
    """Owns the variation schedule, environments, Patch and rendering."""

    def __init__(self, *, seed: int | None = None, clock=time.time, monotonic=time.monotonic):
        self.rng = random.Random(seed)
        self.clock = clock
        self.monotonic = monotonic
        self.mode = "calendar"            # calendar | cycle  (does the 10-minute change pick seasons?)
        self.season_override: str | None = None
        self.weather_override: str | None = None
        self.daylight_override: float | None = None
        self.width = 0
        self.current: Environment | None = None
        self.previous: Environment | None = None
        self.fade_started = 0.0
        self.next_change = 0.0
        self.changes = 0
        self.patch = Patch(rng=random.Random(self.rng.randint(0, 99999)))
        self.last_update = None
        self.history: list[tuple[str, str]] = []

    # ---------------------------------------------------------- variations

    def _season(self) -> str:
        if self.season_override:
            return self.season_override
        if self.mode == "cycle" and self.current is not None:
            return self.rng.choice(SEASONS)
        return season_for(datetime.fromtimestamp(self.clock()))

    def pick_variation(self) -> Variation:
        season = self._season()
        if self.weather_override:
            weather = self.weather_override
            if weather not in SEASON_WEATHER[season]:
                # keep combinations sensible: snow only in winter, etc.
                season = next(s for s in SEASONS if weather in SEASON_WEATHER[s])
        else:
            options = dict(SEASON_WEATHER[season])
            if self.current is not None and self.current.v.season == season and len(options) > 1:
                options.pop(self.current.v.weather, None)  # never repeat the same variation back to back
            names, weights = zip(*options.items())
            weather = self.rng.choices(names, weights)[0]
        return Variation(season, weather, self.rng.randint(0, 10**6))

    def change(self, variation: Variation | None = None) -> Variation:
        """Start a new variation now (cross-fades from the current one)."""
        now = self.monotonic()
        variation = variation or self.pick_variation()
        if self.current is not None:
            self.previous, self.fade_started = self.current, now
        self.current = Environment(variation, max(self.width, 40))
        self.next_change = now + VARIATION_SECONDS
        self.changes += 1
        self.history.append(variation.key)
        return variation

    @property
    def variation(self) -> Variation | None:
        return self.current.v if self.current else None

    def describe(self) -> str:
        if not self.current:
            return "starting"
        v = self.current.v
        light = self.light()
        tod = "night" if light < 0.2 else "day" if light > 0.8 else "dawn" if datetime.fromtimestamp(self.clock()).hour < 12 else "dusk"
        weather = "clear" if v.weather == "sunny" and light < 0.2 else v.weather
        return f"{v.season} · {weather} · {tod}"

    def light(self) -> float:
        if self.daylight_override is not None:
            return self.daylight_override
        now = datetime.fromtimestamp(self.clock())
        season = self.current.v.season if self.current else season_for(now)
        return daylight(season, now.hour + now.minute / 60 + now.second / 3600)

    # ---------------------------------------------------------- chat reactions

    def react(self, kind: str) -> None:
        now = self.monotonic()
        p = self.patch
        if kind == "ack":
            p.reaction, p.reaction_until, p.hop = "ack", now + 1.0, 0.4
        elif kind == "attentive":
            p.reaction, p.reaction_until = "attentive", now + 3600
            p.action = "idle"
        elif kind == "happy":
            p.reaction, p.reaction_until, p.hop = "happy", now + 1.8, 0.4
        elif kind == "concerned":
            p.reaction, p.reaction_until = "concerned", now + 3.5
        elif kind == "idle":
            if p.reaction == "attentive":
                p.reaction, p.reaction_until = "", 0.0

    # ---------------------------------------------------------- simulation

    def update(self, now: float | None = None) -> None:
        now = self.monotonic() if now is None else now
        if self.current is None:
            self.change()
        elif now >= self.next_change:
            self.change()
        dt = 0.0 if self.last_update is None else min(0.5, max(0.0, now - self.last_update))
        self.last_update = now
        if self.previous is not None and now - self.fade_started >= FADE_SECONDS:
            self.previous = None
        for env in (self.current, self.previous):
            if env is not None:
                env.resize(max(self.width, 40))
                env.update(dt, SCENE_PX_HEIGHT)
        self._update_patch(now, dt)

    def _update_patch(self, now: float, dt: float) -> None:
        p, rng = self.patch, self.patch.rng
        env = self.current
        width = max(self.width, 40)
        left, right = 2.0, max(10.0, (env.tree_x - PATCH_W - 4) if env else width - PATCH_W - 2)
        if p.reaction and now >= p.reaction_until:
            p.reaction = ""
        if p.hop > 0:
            p.hop = max(0.0, p.hop - dt)
        if now >= p.blink_at:
            p.blink_until, p.blink_at = now + 0.16, now + rng.uniform(2.5, 6.0)
        busy = p.reaction in {"attentive", "concerned"}
        if busy:
            p.action = "idle"
        elif now >= p.action_until:
            if p.action == "walk":
                p.action, p.action_until = "idle", now + rng.uniform(2.5, 6.0)
                if env and rng.random() < 0.35:
                    p.interaction = self._interaction_for(env.v)
                    if p.interaction:
                        p.action, p.action_until = "interact", now + 2.2
                        self._start_interaction(p.interaction)
            else:
                p.interaction = ""
                p.action, p.action_until = "walk", now + rng.uniform(3.0, 8.0)
                if rng.random() < 0.5:
                    p.facing = -p.facing
        if p.action == "walk":
            speed = 3.0 * (0.7 if env and env.v.weather in {"snowy", "rainy"} else 1.0)
            p.x += p.facing * speed * dt
            p.step_phase += dt
            if p.x <= left:
                p.x, p.facing = left, 1
            elif p.x >= right:
                p.x, p.facing = right, -1
        p.x = min(max(p.x, left), right)

    @staticmethod
    def _interaction_for(v: Variation) -> str:
        if v.weather == "rainy":
            return "splash"
        if v.season == "autumn":
            return "kick_leaves"
        if v.season == "winter":
            return "catch_snow"
        if v.season == "spring":
            return "smell_flower"
        if v.season == "summer" and v.weather == "sunny":
            return "wipe_brow"
        return "look_up"

    def _start_interaction(self, name: str) -> None:
        env, p = self.current, self.patch
        if env is None:
            return
        feet_x, feet_y = int(p.x) + 4, SCENE_PX_HEIGHT - GROUND_PX - 1
        if name == "kick_leaves":
            env.kick(feet_x, feet_y, "leaf")
        elif name == "splash":
            p.hop = 0.35
            env.kick(feet_x, feet_y, "splash")
        elif name == "catch_snow":
            env.kick(feet_x, feet_y - 14, "snow")

    # ---------------------------------------------------------- render

    def render(self, width: int, base: str | None = None) -> PixelFrame:
        return PixelFrame(self.render_canvas(width), hex_rgb(base) if base else None)

    def render_canvas(self, width: int) -> Canvas:
        self.width = max(40, width)
        if self.current is None:
            self.update()
        now = self.monotonic()
        hour_dt = datetime.fromtimestamp(self.clock())
        hour = hour_dt.hour + hour_dt.minute / 60 + hour_dt.second / 3600
        light = self.light()
        canvas = self._draw_env(self.current, now, light, hour)
        if self.previous is not None:
            fade = min(1.0, (now - self.fade_started) / FADE_SECONDS)
            old = self._draw_env(self.previous, now, light, hour)
            for y in range(canvas.h):
                new_row, old_row = canvas.px[y], old.px[y]
                for x in range(canvas.w):
                    a, b = old_row[x], new_row[x]
                    if a is not None and b is not None and a != b:
                        new_row[x] = lerp(a, b, fade)
        self._draw_patch(canvas, now, light)
        night_tint = hex_rgb("#1b1a2e")
        if light < 1.0:
            dim = (1 - light) * 0.35
            moon = hex_rgb("#f9faf4")
            canvas.map(lambda c: lerp(c, night_tint, dim) if c != moon else c)
        return canvas

    def _draw_env(self, env: Environment, now: float, light: float, hour: float) -> Canvas:
        canvas = Canvas(self.width, SCENE_PX_HEIGHT)
        env.resize(self.width)
        env.draw(canvas, now, light, hour)
        env.draw_particles(canvas)
        return canvas

    def _draw_patch(self, c: Canvas, now: float, light: float) -> None:
        p, env = self.patch, self.current
        v = env.v if env else Variation("autumn", "clear", 0)
        night = light < 0.25
        outfit = outfit_for(v.season, v.weather, night)
        colors = dict(PALETTE_PATCH)
        if outfit.sweater:
            colors.update(B=hex_rgb("#4f7a5a"), b=hex_rgb("#3f6449"), P=hex_rgb("#e9dfcd"), A=hex_rgb("#4f7a5a"))
        if outfit.raincoat:
            colors.update(B=hex_rgb("#e2b84b"), b=hex_rgb("#c79a35"), A=hex_rgb("#e2b84b"), P=hex_rgb("#c79a35"))
        pulse = 0.5 + 0.5 * math.sin(now * (9 if p.reaction == "attentive" else 2.2))
        colors["a"] = lerp(hex_rgb("#8a4a36"), hex_rgb("#ff9d7a") if night else hex_rgb("#e58a68"), pulse)
        if night:
            colors["E"] = hex_rgb("#b6fff0")
        hop_px = 2 if p.hop > 0.2 else 1 if p.hop > 0 else 0
        x = int(round(p.x))
        y = SCENE_PX_HEIGHT - GROUND_PX - PATCH_H - hop_px
        flip = p.facing < 0

        if p.action == "walk":
            legs = LEGS["walk1"] if int(p.step_phase * 4) % 2 else LEGS["walk2"]
        else:
            legs = LEGS["stand"]
        body = list(PATCH_BODY)
        windy = v.weather == "windy"
        if windy and not outfit.beanie:
            body[0] = "......a...." if not flip else "....a......"
        if outfit.beanie or outfit.straw_hat:
            body[0], body[1] = "...........", "..........."
        c.sprite(x, y, body + legs, colors, flip=flip)

        # arms: breathing, or raised for some interactions
        arm_up = p.action == "interact" and p.interaction in {"catch_snow", "wipe_brow", "look_up"}
        if arm_up or int(now * 0.8) % 2:
            arm_y = y + (8 if arm_up else 9)
            ax1, ax2 = (2, 8)
            c.set(x + (PATCH_W - 1 - ax1 if flip else ax1), arm_y, colors["A"])
            c.set(x + (PATCH_W - 1 - ax2 if flip else ax2), arm_y, colors["A"])

        def put(dx: int, dy: int, color: RGB) -> None:
            c.set(x + (PATCH_W - 1 - dx if flip else dx), y + dy, color)

        # expression
        if p.reaction == "happy":
            eyes = "happy"
        elif p.reaction == "concerned":
            eyes = "concerned"
        elif now < p.blink_until:
            eyes = "blink"
        elif p.reaction == "attentive":
            eyes = "wide"
        elif p.action == "interact" and p.interaction in {"catch_snow", "look_up"}:
            eyes = "up"
        else:
            eyes = "normal"
        if outfit.sunglasses and eyes in {"normal", "wide", "up", "blink"}:
            lens, glint = hex_rgb("#141218"), hex_rgb("#6f7a90")
            for dx in (3, 4, 6, 7):
                put(dx, 5, lens)
                put(dx, 6, lens)
            put(5, 5, lens)
            put(3, 5, glint)
        else:
            for ex, ey in EYES[eyes]:
                put(ex, ey, colors["E"])
            if eyes in {"happy", "normal"} and p.reaction != "concerned":
                put(2, 7, hex_rgb("#e79a9a"))  # cheek
        if p.reaction == "concerned":
            put(10, 3, hex_rgb("#9cc8f0"))
            put(10, 4, hex_rgb("#9cc8f0"))

        # clothing
        if outfit.beanie:
            knit, stripe, pom = hex_rgb("#c8553d"), hex_rgb("#e9dfcd"), hex_rgb("#f4f1e8")
            put(5, 0, pom)
            for dx in range(2, 9):
                put(dx, 1, knit)
            for dx in range(1, 10):
                put(dx, 2, stripe if dx % 2 else knit)
        if outfit.straw_hat:
            straw, band = hex_rgb("#e3c27a"), hex_rgb("#d87756")
            for dx in range(3, 8):
                put(dx, 0, straw)
            for dx in range(2, 9):
                put(dx, 1, band if dx in (3, 4, 5, 6, 7) else straw)
            for dx in range(0, 11):
                put(dx, 2, straw)
        if outfit.flower:
            petal, center = hex_rgb("#f2a7bd"), hex_rgb("#f2d06b")
            put(5, 0, center)
            put(4, 0, petal)
            put(6, 0, petal)
            put(5, -1, petal)
        if outfit.scarf:
            scarf = outfit.scarf
            stripe = shade(scarf, 1.25)
            for dx in range(2, 9):
                put(dx, 8, stripe if dx % 3 == 0 else scarf)
            flutter = windy or p.action == "walk"
            if flutter and int(now * 4) % 2:
                put(1, 8, scarf)
                put(0, 9, stripe)
            else:
                put(2, 9, scarf)
                put(2, 10, stripe)
        if outfit.umbrella:
            canopy, rib, handle = hex_rgb("#d87756"), hex_rgb("#a85a40"), hex_rgb("#5d5a66")
            top = -4
            for dx, row in enumerate(["....#####....", "..#########..", ".###########.", "#.#.#.#.#.#.#"]):
                for i, ch in enumerate(row):
                    if ch == "#":
                        put(i - 1, top + dx, rib if dx == 3 else canopy)
            for dy in range(0, 10):
                put(9, dy, handle)

        # bubbles
        bubble_x = x + (PATCH_W if not flip else -4)
        if p.reaction == "attentive":
            dots = int(now * 3) % 4
            for i in range(3):
                c.set(bubble_x + i * 2, y + 1, hex_rgb("#f0f0f6") if i < dots else hex_rgb("#66666c"))
        elif p.reaction == "ack":
            c.set(bubble_x + 1, y, hex_rgb("#d87756"))
            c.set(bubble_x + 1, y + 1, hex_rgb("#d87756"))
            c.set(bubble_x + 1, y + 3, hex_rgb("#d87756"))
        elif p.reaction == "happy":
            sparkle = hex_rgb("#f2d06b")
            for dx, dy in ((0, 1), (1, 0), (1, 2), (2, 1)):
                c.set(bubble_x + dx, y + dy, sparkle)
        elif p.action == "interact" and p.interaction == "smell_flower":
            heart = hex_rgb("#e98aa5")
            for dx, dy in ((0, 0), (2, 0), (0, 1), (1, 1), (2, 1), (1, 2)):
                c.set(bubble_x + dx, y + 1 + dy, heart)


# --------------------------------------------------------------------------- logo

_FONT = {
    "F": ["#####", "#....", "####.", "#....", "#...."],
    "R": ["####.", "#...#", "####.", "#..#.", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "#####"],
    "D": ["####.", "#...#", "#...#", "#...#", "####."],
    "A": [".###.", "#...#", "#####", "#...#", "#...#"],
    "Y": ["#...#", ".#.#.", "..#..", "..#..", "..#.."],
    "C": [".####", "#....", "#....", "#....", ".####"],
    "O": [".###.", "#...#", "#...#", "#...#", ".###."],
    "E": ["#####", "#....", "####.", "#....", "#####"],
    " ": [".....", ".....", ".....", ".....", "....."],
}


def logo(lines: Iterable[str], color: str = "#d87756", shadow: str = "#7a3f2c", base: str | None = None) -> PixelFrame:
    """Block-letter wordmark with an offset outline shadow (each letter pixel = 2 cols x 1 row)."""
    lines = list(lines)
    cols = max(len(line) for line in lines)
    cell_w, gap = 5, 1
    w_px = cols * (cell_w + gap) * 2 + 2
    h_px = len(lines) * 6 * 2 + 2
    c = Canvas(w_px, h_px)
    main, dark = hex_rgb(color), hex_rgb(shadow)
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line.upper()):
            glyph = _FONT.get(ch, _FONT[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit != "#":
                        continue
                    px = ci * (cell_w + gap) * 2 + gx * 2
                    py = li * 12 + gy * 2
                    for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                        # shadow one pixel right and down, under the main stroke
                        if c.get(px + ox + 1, py + oy + 1) is None:
                            c.set(px + ox + 1, py + oy + 1, dark)
                    for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                        c.set(px + ox, py + oy, main)
    return PixelFrame(c, hex_rgb(base) if base else None)
