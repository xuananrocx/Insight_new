"""Non-sensitive interface defaults shared with the sign-in screen."""
from typing import Literal
from src.core.config import settings

ThemeKey = Literal['minimal', 'stripe-light', 'linear', 'macos']
THEMES = ('minimal', 'stripe-light', 'linear', 'macos')


def default_theme() -> str:
    ui = settings.config.get('interface', {})
    value = ui.get('default_theme') if isinstance(ui, dict) else None
    return value if value in THEMES else 'minimal'
