"""Friday Code scene: seasons, weather, day/night, 10-minute variations, mascot reactions."""
from __future__ import annotations

from datetime import datetime

from rich.console import Console

from friday_agent.scene import (FADE_SECONDS, SCENE_PX_HEIGHT, SEASON_WEATHER, SEASONS, VARIATION_SECONDS, Scene,
                                Variation, daylight, logo, outfit_for, season_for)


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _scene(seed=1, when=datetime(2026, 11, 20, 13, 0)):
    mono = Clock()
    scene = Scene(seed=seed, clock=lambda: when.timestamp(), monotonic=mono)
    scene.width = 100
    return scene, mono


def test_calendar_seasons_northern_hemisphere():
    assert [season_for(datetime(2026, m, 1)) for m in (1, 4, 7, 10, 12)] == [
        "winter", "spring", "summer", "autumn", "winter"]


def test_daylight_is_smooth_and_follows_local_time():
    assert daylight("autumn", 2.0) == 0.0 and daylight("autumn", 13.0) == 1.0
    dawn = [daylight("autumn", 6.5 + i / 10) for i in range(11)]
    assert dawn == sorted(dawn) and 0 < dawn[5] < 1


def test_variation_changes_every_ten_minutes_without_immediate_repeats():
    scene, mono = _scene()
    scene.update()
    first = scene.variation
    assert scene.changes == 1 and first.season == "autumn"  # 20 Nov → autumn
    mono.t += VARIATION_SECONDS - 1
    scene.update()
    assert scene.changes == 1  # nothing before 10 minutes
    keys = [first.key]
    for _ in range(12):
        mono.t += VARIATION_SECONDS
        scene.update()
        keys.append(scene.variation.key)
    assert scene.changes == 13
    assert all(a != b for a, b in zip(keys, keys[1:]))  # never the same variation twice in a row
    assert all(weather in SEASON_WEATHER[season] for season, weather in keys)


def test_cycle_mode_visits_other_seasons_and_keeps_pairs_sensible():
    scene, mono = _scene(seed=4)
    scene.mode = "cycle"
    seen = set()
    for _ in range(40):
        scene.update()
        seen.add(scene.variation.season)
        assert scene.variation.weather in SEASON_WEATHER[scene.variation.season]
        mono.t += VARIATION_SECONDS
    assert seen == set(SEASONS)


def test_weather_override_picks_a_compatible_season():
    scene, _ = _scene()
    scene.weather_override = "snowy"
    assert scene.pick_variation().season == "winter"


def test_change_crossfades_and_fade_ends():
    scene, mono = _scene()
    scene.update()
    scene.change(Variation("winter", "snowy", 3))
    assert scene.previous is not None
    scene.render(100)  # renders while fading
    mono.t += FADE_SECONDS + 0.1
    scene.update()
    assert scene.previous is None


def test_outfits_match_environment():
    assert outfit_for("winter", "snowy", False).beanie and outfit_for("winter", "snowy", False).scarf
    assert outfit_for("spring", "rainy", False).umbrella and outfit_for("autumn", "rainy", True).raincoat
    assert outfit_for("summer", "sunny", False).sunglasses
    assert not outfit_for("summer", "sunny", True).sunglasses  # no sunglasses at night
    assert outfit_for("autumn", "windy", False).scarf


def test_render_fits_width_and_height_and_is_cheap():
    import time

    scene, mono = _scene()
    for season in SEASONS:
        for weather in SEASON_WEATHER[season]:
            scene.change(Variation(season, weather, 9))
            for _ in range(10):
                mono.t += 0.2
                scene.update()
            console = Console(width=120, record=True, color_system="truecolor")
            console.print(scene.render(120))
            lines = console.export_text().rstrip("\n").split("\n")
            assert len(lines) == SCENE_PX_HEIGHT // 2
            assert all(len(line) <= 120 for line in lines)
    started = time.perf_counter()
    for _ in range(30):
        mono.t += 1 / 6
        scene.update()
        scene.render(160)
    assert (time.perf_counter() - started) / 30 < 0.05  # well under a 6 fps frame budget


def test_reactions_and_walking():
    scene, mono = _scene()
    scene.update()
    scene.react("ack")
    assert scene.patch.reaction == "ack" and scene.patch.hop > 0
    scene.react("attentive")
    for _ in range(20):
        mono.t += 0.5
        scene.update()
    assert scene.patch.action == "idle"  # stands still and pays attention while generating
    scene.react("happy")
    assert scene.patch.reaction == "happy"
    mono.t += 2
    scene.update()
    assert scene.patch.reaction == ""
    scene.react("concerned")
    assert scene.patch.reaction == "concerned"
    xs = set()
    for _ in range(200):
        mono.t += 0.25
        scene.update()
        xs.add(round(scene.patch.x))
    assert len(xs) > 3  # keeps moving between reactions


def test_logo_renders():
    console = Console(width=100, record=True)
    console.print(logo(["FRIDAY", "CODE"]))
    assert len(console.export_text().strip().splitlines()) >= 10
