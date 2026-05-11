from kivy.core.window import Window
from kivy.metrics import dp, sp

def _width_dp() -> float:
    """Logical width in dp so decisions are density-independent."""
    return Window.width / dp(1)

def scale_sp(base_sp: float, min_sp: float | None = None, max_sp: float | None = None) -> float:
    """
    Scale a base font size by width breakpoints, then clamp.
    Targets phones first; tablets get a small bump.
    """
    w = _width_dp()
    if w < 360:
        k = 0.90
    elif w < 412:
        k = 1.00
    elif w < 600:
        k = 1.10
    elif w < 720:
        k = 1.20
    else:
        k = 1.35
    val = base_sp * k
    if min_sp is not None:
        val = max(val, min_sp)
    if max_sp is not None:
        val = min(val, max_sp)
    return sp(val)

# Convenience presets used from KV/Python
def title_sp() -> float:
    return scale_sp(46, min_sp=26, max_sp=64)

def button_sp() -> float:
    return scale_sp(20, min_sp=16, max_sp=24)

def body_sp() -> float:
    return scale_sp(15, min_sp=12, max_sp=18)

def small_sp() -> float:
    return scale_sp(12, min_sp=10, max_sp=14)