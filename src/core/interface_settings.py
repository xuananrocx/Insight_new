"""Non-sensitive interface defaults shared with the sign-in screen."""
from typing import Literal
from src.core.config import settings

ThemeKey = Literal['minimal', 'stripe-light', 'linear', 'everforest', 'macos']
THEMES = ('minimal', 'stripe-light', 'linear', 'everforest', 'macos')


def default_theme() -> str:
    ui = settings.config.get('interface', {})
    value = ui.get('default_theme') if isinstance(ui, dict) else None
    if value == 'linear':
        return 'everforest'
    return value if value in THEMES else 'minimal'

AccentKey = Literal['graphite', 'blue', 'slate', 'terracotta', 'moss', 'stone']
ACCENTS = ('graphite', 'blue', 'slate', 'terracotta', 'moss', 'stone')

def default_accent() -> str:
    ui = settings.config.get('interface', {})
    value = ui.get('default_accent') if isinstance(ui, dict) else None
    return value if value in ACCENTS else 'graphite'
