# fd_terminal/ui.py

# --- IMPORTS FIRST ---
import logging
import sys
import os
import glob
import random
import math
from typing import Optional
from kivy import app
from kivy.app import App
from kivy.core import text
from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.app import App
from kivy.uix.screenmanager import Screen, ScreenManager, FadeTransition, SlideTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.slider import Slider
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.gridlayout import GridLayout
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.uix.popup import Popup
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty, NumericProperty
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.core.text import LabelBase, Label as CoreLabel
from functools import partial
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.clock import Clock
from kivy.utils import get_color_from_hex
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.animation import Animation
from plyer import vibrator
from kivy.uix.checkbox import CheckBox
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from .utils import color_text, get_save_slot_info, normalize_text
from .game_logic import GameLogic
from .achievements import AchievementsSystem
from .widgets import (
    StatusDisplayWidget, OutputPanelWidget, MapDisplayWidget,
    ActionInputWidget, QTEPopup, InventoryDisplayWidget,
    ContextualActionsWidget, InfoPopup,
    ContextDockWidget
)


# --- CUSTOM POPUP CLASSES ---
class MapPopup(ModalView):
    """
    A full-screen popup for reviewing the level map.
    """
    map_content = StringProperty("")
    
    def __init__(self, map_content="", **kwargs):
        super().__init__(**kwargs)
        self.map_content = map_content


# --- NEW: FONT LOGIC AND GLOBAL DEFINITIONS AT THE TOP ---

# This set will keep track of fonts we've already registered.
REGISTERED_FONT_NAMES = set()

# Proclaim the global names for our fonts so all classes can see them.
DEFAULT_FONT_BOLD_NAME = "RobotoMonoBold"
DEFAULT_FONT_REGULAR_NAME = "RobotoMono"  # Added definition for regular font
THEMATIC_FONT_NAME = "RobotoMonoBold" # Start with a safe default

FONT_SIZE_SCALING = {}

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base_path, relative_path)

def shake_screen(widget, intensity=10):
    """
    Shakes a widget to simulate impact.
    Safe for Android because it moves the Widget, not the OS Window.
    """
    INTENSITY_MAP = {'small': 5, 'medium': 10, 'large': 20}
    if isinstance(intensity, str):
        intensity = INTENSITY_MAP.get(intensity, 10)
    # Ensure the widget has a 'padding_x' property
    if not hasattr(widget, 'padding_x'):
        widget.padding_x = 0  # Dynamically add the property

    anim = Animation(padding_x=intensity, duration=0.05) + \
           Animation(padding_x=-intensity, duration=0.05) + \
           Animation(padding_x=intensity // 2, duration=0.05) + \
           Animation(padding_x=0, duration=0.05)
    anim.start(widget)

def register_and_scale_fonts(
    font_dir,
    reference_font="RobotoMonoBold",
    reference_size=dp(40),
    test_string="AgTy"
):
    """
    Registers all fonts in font_dir, computes scaling factors, and selects a random thematic font.
    Returns (scaling_factors: dict, selected_thematic_font: str)
    """
    logger = logging.getLogger("UI.Fonts")
    scaling_factors = {}
    selected_font = None

    try:
        font_files = [f for f in os.listdir(font_dir) if f.lower().endswith(('.ttf', '.otf'))]
    except Exception as e:
        logger.error(f"Failed to read font directory '{font_dir}': {e}")
        return {"RobotoMonoBold": 1.0, "RobotoMono": 1.0}, None

    thematic_fonts = [f for f in font_files if "Roboto" not in f]
    logger.info(
        f"Font scan complete: total={len(font_files)}, thematic={len(thematic_fonts)}, dir='{font_dir}'"
    )

    # 1. Manually register Core fonts so their aliases match the .kv file perfectly
    reg_path = os.path.join(font_dir, 'RobotoMono-Regular.ttf')
    bold_path = os.path.join(font_dir, 'RobotoMono-Bold.ttf')

    try:
        if os.path.exists(reg_path):
            LabelBase.register(name="RobotoMono", fn_regular=reg_path)
        else:
            logger.warning(f"Missing core font file: {reg_path}")

        if os.path.exists(bold_path):
            LabelBase.register(name="RobotoMonoBold", fn_regular=bold_path)
        else:
            logger.warning(f"Missing core font file: {bold_path}")
    except Exception as e:
        logger.error(f"Failed registering core fonts: {e}")

    # 2. Measure the reference font to establish baseline
    active_reference_font = reference_font
    try:
        ref_label = CoreLabel(
            text=test_string,
            font_name=active_reference_font,
            font_size=reference_size
        )
        ref_label.refresh()
    except Exception as e:
        logger.error(
            f"Reference font '{active_reference_font}' failed to measure: {e}. "
            "Falling back to 'RobotoMonoBold'."
        )
        active_reference_font = "RobotoMonoBold"
        ref_label = CoreLabel(
            text=test_string,
            font_name=active_reference_font,
            font_size=reference_size
        )
        ref_label.refresh()

    ref_height = ref_label.texture.size[1] or 1

    scaling_factors["RobotoMonoBold"] = 1.0
    scaling_factors["RobotoMono"] = 1.0
    if active_reference_font not in scaling_factors:
        scaling_factors[active_reference_font] = 1.0

    # 3. Loop through thematic fonts only
    for font_file in thematic_fonts:
        font_name = os.path.splitext(font_file)[0]
        font_path = os.path.join(font_dir, font_file)
        try:
            LabelBase.register(name=font_name, fn_regular=font_path)

            test_label = CoreLabel(
                text=test_string,
                font_name=font_name,
                font_size=reference_size
            )
            test_label.refresh()
            test_height = test_label.texture.size[1] or 1

            scaling_factors[font_name] = ref_height / test_height
        except Exception as e:
            logger.error(
                f"Error registering/measuring font '{font_file}': {e}. "
                f"Current selected_font='{selected_font}'"
            )
            scaling_factors[font_name] = 1.0

    # 4. Select a random thematic font
    try:
        if thematic_fonts:
            selected_font_file = random.choice(thematic_fonts)
            selected_font = os.path.splitext(selected_font_file)[0]
            logger.info(f"Selected thematic font: '{selected_font}' from {len(thematic_fonts)} candidates")
        else:
            logger.warning("No thematic fonts found; selected_font=None")
    except Exception as e:
        logger.error(
            f"Failed to select thematic font: {e}. "
            f"Falling back to None. Current selected_font='{selected_font}'"
        )
        selected_font = None

    return scaling_factors, selected_font

def get_thematic_font_name():
    """
    Read the selected thematic font from the App if available, else fallback.
    """
    app = App.get_running_app()
    return getattr(app, 'thematic_font_name', THEMATIC_FONT_NAME)

def get_scaled_font_size(font_name, base_size):
    scale = FONT_SIZE_SCALING.get(font_name, 1.0)
    return base_size * scale

def _wrap_button_text(button, align=None):
    """
    Ensures a Kivy Button wraps its text and grows vertically to fit, optimized for Android screens.
    - Sets halign/valign, text_size, and binds height to texture_size.
    - Optionally sets alignment ('left', 'center', 'right').
    """
    # Set horizontal alignment
    if align:
        button.halign = align
    else:
        button.halign = 'center'
    button.valign = 'middle'

    # Padding for touch targets and visual comfort
    button.padding_x = dp(10)
    button.padding_y = dp(8)

    # Set text_size to wrap at button width minus padding
    def update_text_size(instance, value):
        # Subtract horizontal padding for accurate wrapping
        pad_x = getattr(instance, 'padding_x', 0)
        instance.text_size = (value - 2 * pad_x, None)
    button.bind(width=update_text_size)
    update_text_size(button, button.width)

    # Grow height to fit text
    def update_height(instance, value):
        # Add vertical padding to texture height
        pad_y = getattr(instance, 'padding_y', 0)
        instance.height = value[1] + 2 * pad_y
    button.bind(texture_size=update_height)
    update_height(button, button.texture_size)

    # Ensure minimum touch target (Android guideline: 48dp)
    min_height = dp(48)
    if button.height < min_height:
        button.height = min_height

# A base screen for common functionality
class BaseScreen(Screen):
    resource_manager = ObjectProperty(None, allownone=True) # <-- ADD THIS LINE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        # Track our graphics instructions so we can update them later
        self.bg_color_instruction = None
        self.bg_rect_instruction = None

        # Initialize the background immediately (safe to do here without clearing)
        with self.canvas.before:
            self.bg_color_instruction = Color(0.05, 0.05, 0.05, 1)  # Default Dark
            self.bg_rect_instruction = Rectangle(size=self.size, pos=self.pos)

        self.bind(size=self._update_rect, pos=self._update_rect)

        # Bind to App theme changes
        app = App.get_running_app()
        if app:
            app.bind(theme_mode=self._on_theme_change)
            # Apply current theme immediately
            self._on_theme_change(app, app.theme_mode)
        self._scheduled_events = [] # Now EVERY screen has this

    def reset_ui_state(self):
        """Clears scheduled events and UI locks for this screen."""
        if hasattr(self, '_scheduled_events'):
            for ev in self._scheduled_events:
                try: ev.cancel()
                except: pass
            self._scheduled_events.clear()

    def clear_ghost_timers(self):
        for ev in self._scheduled_events:
            try: ev.cancel()
            except: pass
        self._scheduled_events.clear()

    def _update_rect(self, instance, value):
        if self.bg_rect_instruction:
            self.bg_rect_instruction.pos = instance.pos
            self.bg_rect_instruction.size = instance.size

    def _on_theme_change(self, instance, theme_name):
        """Updates the background color AND text colors dynamically."""
        if not self.bg_color_instruction:
            return

        # 1. Determine Colors based on Theme
        if theme_name == "Dark":
            bg_color = (0.05, 0.05, 0.05, 1)  # Almost Black
            text_color = (1, 1, 1, 1)         # White Text
        else:
            bg_color = (0.9, 0.9, 0.9, 1)     # Off-White
            text_color = (0, 0, 0, 1)         # Black Text

        # 2. Apply Background
        self.bg_color_instruction.rgba = bg_color

        # 3. Apply Text Color to all child Labels
        # We iterate through every widget on the screen.
        try:
            for widget in self.walk():
                # We target Labels, but we EXCLUDE Buttons.
                # Buttons usually maintain a dark texture/background, so white text is still best for them.
                if isinstance(widget, Label) and not isinstance(widget, Button):
                    # Update the color property
                    widget.color = text_color
                    
                    # If the label relies on internal markup (like [color=...]), 
                    # this base color change might not override it, which is good. 
                    # It only affects the "plain" text parts.
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.warning(f"Theme update error: {e}")

    def go_to_screen(self, screen_name: str, direction: str = 'left'):
        try:
            if direction == 'fade':
                self.manager.transition = FadeTransition()
            elif direction in ('left', 'right', 'up', 'down'):
                self.manager.transition = SlideTransition(direction=direction)
            else:
                self.manager.transition = SlideTransition(direction='left')
            self.manager.current = screen_name
        except Exception as e:
            logging.error(f"go_to_screen: failed to switch to '{screen_name}': {e}", exc_info=True)

    def update_font_scale(self, scale: float):
        """
        Walk all child widgets and apply responsive font scaling.
        """
        try:
            for w in self.walk():
                if hasattr(w, 'font_size'):
                    if not hasattr(w, 'base_font_sp'):
                        try:
                            w.base_font_sp = float(w.font_size)
                        except Exception:
                            w.base_font_sp = 16.0
                    w.font_size = sp(max(10.0, w.base_font_sp * scale))
        except Exception:
            pass

class GlitchOverlay(FloatLayout):
    """
    A full-screen overlay that flashes subliminal images.
    Usually invisible (opacity 0).
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.glitch_image = Image(
            source='', 
            allow_stretch=True, 
            keep_ratio=False, 
            opacity=0,
            size_hint=(1, 1),
            pos_hint={'center_x': 0.5, 'center_y': 0.5}
        )
        self.add_widget(self.glitch_image)

    def trigger_glitch(self, image_path, duration=0.05, opacity=0.4):
        """
        Flashes an image for 'duration' seconds.
        """
        if not image_path: return
        
        self.glitch_image.source = image_path
        self.glitch_image.opacity = opacity
        
        # Schedule the cleanup
        Clock.schedule_once(lambda dt: setattr(self.glitch_image, 'opacity', 0), duration)

class TitleScreen(BaseScreen):
    # Expose colors to KV early so KV can read them without AttributeError
    color_white = StringProperty('ffffff')
    color_white = [1, 1, 1, 1] 
    color_red = StringProperty('ff0000')

    def __init__(self, **kwargs):
        # Pop custom deps BEFORE super so we can compute properties used by KV
        self.resource_manager = kwargs.pop('resource_manager', None)
        self.achievements_system = kwargs.pop('achievements_system', None)

        # Compute colors now so KV sees the final values during apply()
        if self.resource_manager:
            constants = self.resource_manager.get_data('constants', {})
            colors = constants.get('COLORS', {})
            self.color_white = colors.get('WHITE', self.color_white)
            self.color_red = colors.get('RED', self.color_red)

        super().__init__(**kwargs)

        # Bind to theme changes specifically for this screen's custom colors
        app = App.get_running_app()
        if app:
            app.bind(theme_mode=self._update_title_colors)
            self._update_title_colors(app, app.theme_mode)

    def _update_title_colors(self, instance, theme_name):
        """Updates the color properties used by KV markup."""
        if theme_name == "Light":
            self.color_white = "000000" # Black
            # Keep Red as Red, or make it DarkRed if needed
            self.color_red = "8B0000"   
        else:
            # Restore defaults from constants or hardcoded
            if self.resource_manager:
                constants = self.resource_manager.get_data('constants', {})
                self.color_white = constants.get('COLORS', {}).get('WHITE', 'ffffff')
                self.color_red = constants.get('COLORS', {}).get('RED', 'ff0000')
            else:
                self.color_white = 'ffffff'
                self.color_red = 'ff0000'

    def start_new_game_display(self, character_class="Journalist"):
        self.game_logic.start_new_game(character_class)
        initial_description = self.game_logic.get_game_start_description()
        self.display_message(initial_description)
        self.update_location_display(self.game_logic.player['location'])
        self.update_output(initial_description)

    def start_new_game_flow(self, *args):
        """Initiates the new game flow by resetting the game screen state."""
        app = App.get_running_app()
        app.start_new_session_flag = True 
        if self.manager and 'game' in self.manager.screen_names:
            game_screen = self.manager.get_screen('game')
            game_screen.reset_ui_state() # Call the method where it actually lives
            
        self.go_to_screen('character_select', direction='right')

class SandboxConfigurationScreen(BaseScreen):
    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.hazards_db = {}
        self.selected_hazards = set()
        self.include_related = True 
        self.include_all_items = False
    
    def on_enter(self, *args):
        """Populate hazard toggles."""
        self.ids['hazard_grid_id'].clear_widgets()
        self.selected_hazards.clear()
        
        # Default options
        self.ids['chk_related_id'].active = True
        self.ids['chk_all_items_id'].active = False
        
        if not self.resource_manager: return

        # Get Hazards
        hazards = self.resource_manager.get_data('hazards', {})
        self.hazards_db = hazards
        
        sorted_hazards = sorted(hazards.items(), key=lambda x: x[0])
        
        for h_key, h_data in sorted_hazards:
            name = h_data.get('name', h_key)
            
            # Row Layout
            row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(40), spacing=dp(10))
            
            # Label
            lbl = Label(
                text=name, 
                size_hint_x=0.7, 
                halign='left', 
                valign='middle',
                font_name='RobotoMono',
                font_size=dp(14),
                color=(0.8, 0.8, 0.8, 1)
            )
            lbl.bind(size=lbl.setter('text_size'))
            
            # Checkbox equivalent (ToggleButton)
            tgl = ToggleButton(
                text="OFF",
                size_hint_x=0.3,
                state='normal',
                font_name='RobotoMonoBold',
                font_size=dp(12)
            )
            
            # Bind logic
            tgl.bind(on_release=partial(self.toggle_hazard, h_key, tgl))
            
            row.add_widget(lbl)
            row.add_widget(tgl)
            self.ids['hazard_grid_id'].add_widget(row)

    def toggle_hazard(self, h_key, btn_instance, *args):
        if btn_instance.state == 'down':
            self.selected_hazards.add(h_key)
            btn_instance.text = "ON"
            btn_instance.background_color = (0, 0.8, 0, 1) # Green
        else:
            self.selected_hazards.discard(h_key)
            btn_instance.text = "OFF"
            btn_instance.background_color = (0.2, 0.2, 0.2, 1) # Grey

    def toggle_related_items(self, instance, value):
        self.include_related = value

    def toggle_all_items(self, instance, value):
        self.include_all_items = value

    def launch_simulation(self):
        app = App.get_running_app()
        self.logger.info(f"Sandbox Config Launch: {len(self.selected_hazards)} hazards.")
        
        # Check if procedural generation is enabled
        use_procedural = self.ids.get('chk_procedural_id', None)
        use_proc = use_procedural.active if use_procedural else False
        
        # Build Config
        config = {
            'hazards': list(self.selected_hazards),
            'include_related': self.include_related,
            'include_all_items': self.include_all_items,
            'use_procedural': use_proc
        }
        
        # Add procedural-specific settings if enabled
        if use_proc:
            room_slider = self.ids.get('room_count_slider_id', None)
            if room_slider:
                config['room_count'] = int(room_slider.value)
            else:
                config['room_count'] = 10  # Default
            
            # Optional: Add seed if provided
            seed_input = self.ids.get('seed_input_id', None)
            if seed_input and seed_input.text.strip():
                try:
                    config['seed'] = int(seed_input.text.strip())
                except ValueError:
                    self.logger.warning(f"Invalid seed value: {seed_input.text}")
        
        # --- NEW: Check for incomplete hazard chains ---
        from .hazard_chains import check_missing_dependencies, format_chain_warning
        
        missing_deps = check_missing_dependencies(
            list(self.selected_hazards),
            app.resource_manager
        )
        
        if missing_deps:
            warning_text = format_chain_warning(missing_deps)
            self.logger.info(f"Detected {len(missing_deps)} incomplete hazard chains")
            
            # Collect ALL missing hazards
            all_missing = set()
            for hazard, deps in missing_deps:
                all_missing.update(deps)
            
            # Show informative popup
            from kivy.uix.popup import Popup
            from kivy.uix.boxlayout import BoxLayout
            from kivy.uix.label import Label
            from kivy.uix.button import Button
            
            content = BoxLayout(orientation='vertical', spacing=10, padding=10)
            
            msg_label = Label(
                text=warning_text,
                markup=True,
                halign='left',
                valign='top',
                text_size=(400, None),
                size_hint_y=None
            )
            msg_label.bind(texture_size=lambda *x: setattr(msg_label, 'height', msg_label.texture_size[1]))
            
            button_box = BoxLayout(size_hint_y=None, height=50, spacing=10)
            
            def proceed(*args):
                popup.dismiss()
                self._launch_game(config, app)
            
            def add_missing_and_launch(*args):
                """Add all missing hazards and relaunch validation."""
                popup.dismiss()
                # Add missing hazards to selection
                self.selected_hazards.update(all_missing)
                self.logger.info(f"Auto-added {len(all_missing)} missing hazards: {all_missing}")
                # Update UI checkboxes
                self._update_hazard_checkboxes()
                # Relaunch (will re-check for more missing deps)
                self.launch_simulation()
            
            btn_cancel = Button(text="Go Back", on_release=lambda x: popup.dismiss())
            btn_add = Button(text="Add Missing & Launch", on_release=add_missing_and_launch)
            btn_proceed = Button(text="Launch Anyway", on_release=proceed)
            
            button_box.add_widget(btn_cancel)
            button_box.add_widget(btn_add)
            button_box.add_widget(btn_proceed)
            
            content.add_widget(msg_label)
            content.add_widget(button_box)
            
            popup = Popup(
                title="Incomplete Hazard Chains",
                content=content,
                size_hint=(0.8, 0.7),
                auto_dismiss=False
            )
            popup.open()
        else:
            # No missing dependencies, launch directly
            self._launch_game(config, app)
    
    def _update_hazard_checkboxes(self):
        """Update UI checkboxes to reflect current selected_hazards."""
        hazards_list = self.ids.get('hazards_list')
        if not hazards_list:
            return
        
        for child in hazards_list.children:
            if hasattr(child, 'children'):
                for widget in child.children:
                    if hasattr(widget, 'hazard_id'):
                        # This is a checkbox
                        widget.active = widget.hazard_id in self.selected_hazards
    
    def _launch_game(self, config, app):
        """Helper to actually launch the game after validation."""
        # Create session with sandbox config directly - avoids double initialization!
        game_logic = app.create_new_game_session(
            character_class="Journalist",
            sandbox_config=config
        )
        
        self.go_to_screen('game', direction='up')

class CharacterSelectScreen(BaseScreen):
    character_grid = ObjectProperty(None)

    _C_BG      = (0.08, 0.08, 0.08, 1)
    _C_BORDER  = (0.3,  0.3,  0.3,  1)
    _C_GREEN   = (0.1,  0.8,  0.1,  1)
    _C_ACCENT  = (1.0,  0.6,  0.0,  1)
    _STAT_COLORS = {
        1: (0.6, 0.1, 0.1, 1), 2: (0.7, 0.3, 0.1, 1), 3: (0.7, 0.6, 0.1, 1),
        4: (0.5, 0.7, 0.1, 1), 5: (0.2, 0.8, 0.2, 1), 6: (0.2, 0.8, 0.2, 1),
        7: (0.1, 0.9, 0.3, 1), 8: (0.0, 1.0, 0.4, 1),
    }

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + ".CharacterSelectScreen")

    def on_enter(self, *args):
        self.logger.info("CharacterSelectScreen: on_enter called.")
        if not self.character_grid:
            self.logger.error("CharacterSelectScreen: character_grid ID not found.")
            return
        self.character_grid.clear_widgets()
        app = App.get_running_app()
        character_data = app.resource_manager.get_data('character_classes', {})
        if not character_data:
            self.character_grid.add_widget(Label(text="No character classes available."))
            return
        for key, details in character_data.items():
            try:
                self.character_grid.add_widget(self._build_card(key, details))
            except Exception as e:
                self.logger.error(f"CharacterSelectScreen: card failed for '{key}': {e}", exc_info=True)

    def _build_card(self, key, details):
        from kivy.graphics import RoundedRectangle as RR, Line, Color as GColor
        description   = details.get('description', '')
        max_hp        = details.get('max_hp', 30)
        strength      = details.get('strength', 5)
        intuition     = details.get('intuition', 2)
        perception    = details.get('perception', 2)
        affinities    = details.get('affinities', {})
        quote         = details.get('observations', {}).get('inter_level_thought', '')
        all_affs = (
            [(a, (0.1, 0.55, 0.25, 0.8)) for a in affinities.get('item_types', [])] +
            [(a, (0.15, 0.35, 0.7,  0.8)) for a in affinities.get('hazard_tags', [])] +
            [(a, (0.55, 0.3, 0.05, 0.8)) for a in affinities.get('skilled_actions', [])] +
            [(a, (0.45, 0.1, 0.45, 0.8)) for a in affinities.get('disaster_tags', [])]
        )

        # Phone-friendly constants
        PAD_W    = dp(14)
        PAD_H    = dp(12)
        SPACING  = dp(8)
        NAME_H   = dp(38)   # tall enough to tap
        STATS_H  = dp(3*24 + 2*4)
        AFF_H    = dp(30)
        BTN_H    = dp(52)   # 52dp = comfortable phone tap target

        card = BoxLayout(orientation='vertical', size_hint_y=None,
                         padding=[PAD_W, PAD_H], spacing=SPACING)

        def _bg(w, *_):
            w.canvas.before.clear()
            with w.canvas.before:
                GColor(0.08, 0.08, 0.08, 1)
                RR(pos=w.pos, size=w.size, radius=[dp(6)])
                GColor(0.3, 0.3, 0.3, 1)
                Line(rounded_rectangle=(w.x, w.y, w.width, w.height, dp(6)), width=dp(1))
        card.bind(pos=_bg, size=_bg)

        # ── Name + HP badge ──
        row1 = BoxLayout(orientation='horizontal', size_hint_y=None,
                         height=NAME_H, spacing=dp(8))
        n = Label(text=f'[b]{key.upper()}[/b]', markup=True, font_name='RobotoMonoBold',
                  font_size=dp(18), color=self._C_GREEN,
                  halign='left', valign='middle', size_hint_x=1)
        n.bind(size=lambda i, v: setattr(i, 'text_size', v))
        row1.add_widget(n)
        row1.add_widget(self._badge(f'♥ {max_hp} HP', (0.6, 0.1, 0.1, 0.9)))
        card.add_widget(row1)
        fixed_h = NAME_H + SPACING

        # ── Description — deferred wrap ──
        desc_line = description.split('\n')[0].strip()
        desc_lbl = None
        if desc_line:
            desc_lbl = Label(text=f'[color=aaaaaa]{desc_line}[/color]', markup=True,
                             font_name='RobotoMono', font_size=dp(13),
                             halign='left', valign='top', size_hint_y=None, height=dp(20))
            card.add_widget(desc_lbl)
            fixed_h += dp(20) + SPACING

        # ── Stat bars: STR / INT / PER ──
        sc = BoxLayout(orientation='vertical', size_hint_y=None,
                       height=STATS_H, spacing=dp(4))
        for lbl_text, val in [('STR', strength), ('INT', intuition), ('PER', perception)]:
            sc.add_widget(self._stat_row(lbl_text, val, 8))
        card.add_widget(sc)
        fixed_h += STATS_H + SPACING

        # ── Affinity badges ──
        if all_affs:
            ar = BoxLayout(orientation='horizontal', size_hint_y=None,
                           height=AFF_H, spacing=dp(4))
            al = Label(text='[color=555555]Affinities:[/color]', markup=True,
                       font_name='RobotoMono', font_size=dp(11),
                       size_hint_x=None, width=dp(76),
                       halign='right', valign='middle')
            al.bind(size=lambda i, v: setattr(i, 'text_size', v))
            ar.add_widget(al)
            sv = ScrollView(do_scroll_y=False, do_scroll_x=True, size_hint_x=1)
            ai = BoxLayout(orientation='horizontal', spacing=dp(5), size_hint_x=None)
            ai.bind(minimum_width=ai.setter('width'))
            for t, c in all_affs:
                ai.add_widget(self._badge(t, c))
            sv.add_widget(ai)
            ar.add_widget(sv)
            card.add_widget(ar)
            fixed_h += AFF_H + SPACING

        # ── Character quote — deferred wrap ──
        quote_lbl = None
        if quote:
            quote_lbl = Label(text=f'[color=888888][i]"{quote}"[/i][/color]', markup=True,
                              font_name='RobotoMono', font_size=dp(12),
                              halign='left', valign='top', size_hint_y=None, height=dp(20))
            card.add_widget(quote_lbl)
            fixed_h += dp(20) + SPACING

        # ── Select button — 52dp phone-safe tap target ──
        btn = Button(text=f'[b]PLAY AS {key.upper()}[/b]', markup=True,
                     font_name='RobotoMonoBold', font_size=dp(15),
                     background_normal='', background_down='', background_color=(0,0,0,0),
                     color=self._C_ACCENT, size_hint_y=None, height=BTN_H,
                     halign='center', valign='middle')
        btn.bind(size=lambda i, v: setattr(i, 'text_size', v))
        def _btn_bg(b, *_):
            b.canvas.before.clear()
            with b.canvas.before:
                GColor(*(self._C_ACCENT if b.state == 'down' else (0.6, 0.4, 0.0, 1)))
                Line(rounded_rectangle=(b.x, b.y, b.width, b.height, dp(4)), width=dp(1.5))
        btn.bind(pos=_btn_bg, size=_btn_bg, state=_btn_bg)
        btn.bind(on_release=lambda _, k=key: self.select_character(k))
        card.add_widget(btn)
        fixed_h += BTN_H + PAD_H

        card.height = fixed_h

        # Deferred: correct any wrapping labels once card has a real width
        def _measure(dt, crd=card, dl=desc_lbl, ql=quote_lbl,
                     pw=PAD_W, base=fixed_h):
            avail = crd.width - pw * 2
            if avail <= 0:
                Clock.schedule_once(lambda dt2: _measure(dt2, crd=crd, dl=dl,
                                    ql=ql, pw=pw, base=base), 0)
                return
            delta = 0
            for lbl, placeholder in [(dl, dp(20)), (ql, dp(20))]:
                if lbl is None:
                    continue
                lbl.text_size = (avail, None)
                lbl.texture_update()
                real_h = lbl.texture_size[1]
                lbl.height = real_h
                delta += real_h - placeholder
            crd.height = base + delta

        Clock.schedule_once(_measure, 0)
        return card

    def _stat_row(self, label, value, max_val):
        from kivy.graphics import RoundedRectangle as RR, Color as GColor
        # 24dp rows — readable on phone without being wasteful
        row = BoxLayout(orientation='horizontal', size_hint_y=None,
                        height=dp(24), spacing=dp(6))
        sl = Label(text=f'[color=888888]{label}[/color]', markup=True,
                   font_name='RobotoMono', font_size=dp(12),
                   size_hint_x=None, width=dp(30), halign='right', valign='middle')
        sl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        row.add_widget(sl)
        bar_color = self._STAT_COLORS.get(min(value, 8), (0.2, 0.8, 0.2, 1))
        bar = Widget(size_hint_x=1)
        def _draw(w, *_):
            w.canvas.clear()
            with w.canvas:
                GColor(0.15, 0.15, 0.15, 1)
                RR(pos=w.pos, size=w.size, radius=[dp(2)])
                fw = int(w.width * (value / max_val))
                if fw > 0:
                    GColor(*bar_color)
                    RR(pos=w.pos, size=(fw, w.height), radius=[dp(2)])
        bar.bind(pos=_draw, size=_draw)
        row.add_widget(bar)
        vl = Label(text=f'[b]{value}[/b]', markup=True, font_name='RobotoMono',
                   font_size=dp(12), size_hint_x=None, width=dp(20),
                   halign='center', valign='middle', color=bar_color)
        row.add_widget(vl)
        return row

    def _badge(self, text, color):
        from kivy.graphics import RoundedRectangle as RR, Color as GColor
        t = text.replace('_', ' ')
        # 26dp height — finger-friendly badge
        w = max(dp(56), dp(7.5) * len(t))
        lbl = Label(text=t, font_name='RobotoMono', font_size=dp(11),
                    size_hint=(None, None), size=(w, dp(26)),
                    halign='center', valign='middle')
        lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        def _draw(l, *_):
            l.canvas.before.clear()
            with l.canvas.before:
                GColor(*color)
                RR(pos=l.pos, size=l.size, radius=[dp(3)])
        lbl.bind(pos=_draw, size=_draw)
        return lbl

    def select_character(self, char_class: str):
        app = App.get_running_app()
        app.create_new_game_session(char_class)
        app.start_new_session_flag = True
        # Pull the game screen from your ScreenManager
        if app and app.root and app.root.has_screen('game'):
            game_screen = app.root.get_screen('game')
            
            # THE FIX: Safely check that game_logic exists AND is not None
            if getattr(game_screen, 'game_logic', None) is not None:
                game_screen.game_logic.wipe_active_state()
        self.go_to_screen('intro', direction='fade')
        

class IntroScreen(BaseScreen):
    intro_text_label = ObjectProperty(None)

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + ".IntroScreen")

    def on_enter(self, *args):
        """
        Drop-in replacement for IntroScreen.on_enter.
        Detects premonition level (level 0) and shows a highly contextual intro.
        Skips the Intro Screen entirely for levels past Level 1.
        """
        self.logger.info("IntroScreen on_enter.")
        app = App.get_running_app()
        game_logic = getattr(app, 'game_logic', None)
        rm = self.resource_manager

        # --- Aggressive Error Catching ---
        if not self.intro_text_label:
            self.logger.error("IntroScreen: intro_text_label is None!")
            return

        if not game_logic:
            self.logger.error("IntroScreen: game_logic is missing!")
            self.intro_text_label.text = "A chilling premonition grips you... (Error: Engine disconnected)"
            return

        if 'intro_disaster' not in game_logic.player:
            self.logger.error("IntroScreen: 'intro_disaster' missing from player state!")
            self.intro_text_label.text = "A chilling premonition grips you... (Error: Disaster data lost)"
            return
        # ---------------------------------

        # City assignment (unchanged)
        available_hospitals = ["Hope River Hospital", "Lakeview Hospital", "Ellis Medical Center"]
        if 'current_city' not in game_logic.player:
            available_cities = ["McKinley", "Cloverdale", "Mt. Abraham", "Stonybrook", "Springfield"]
            game_logic.player['current_city'] = random.choice(available_cities)
            self.logger.info(f"City assigned: {game_logic.player['current_city']}")
            
        if 'current_hospital' not in game_logic.player:
            game_logic.player['current_hospital'] = random.choice(available_hospitals)

        from fd_terminal.utils import color_text
        selected_city = game_logic.player['current_city']
        selected_hospital = game_logic.player['current_hospital']
        details = game_logic.player['intro_disaster']
        
        current_level = str(game_logic.player.get('current_level', 'level_1'))
        char_class = game_logic.player.get('character_class', 'Survivor')

        # ── PREMONITION LEVEL (Level 0) ──────────────────────────────────
        if current_level in ["0", "level_0"]:
            visionary_name = game_logic.player.get('premonition_visionary', 'a stranger')
            disaster_name = details.get('name', details.get('event_description', 'something terrible'))

            # --- Contextual Parsing for Disaster Tags ---
            tags = details.get('tags', [])
            
            # Defaults
            weather = "The weather is completely unremarkable."
            time_of_day = "Tuesday afternoon"
            smell = "nothing in particular"
            crowd = "steady — just the rhythm of a normal day"
            level_0_data = rm.get_data('rooms_level_0', {}) if rm else {}
            # Replace lines 932–962 in ui.py with:

            atmosphere_map = level_0_data.get('atmosphere_defaults', {})
            fallback = atmosphere_map.get('_fallback', {
                'time_of_day': 'Tuesday afternoon',
                'weather': 'The weather is completely unremarkable.',
                'smell': 'nothing in particular',
                'crowd': 'steady — just the rhythm of a normal day'
            })

            # Walk disaster tags in order; first match wins
            atmosphere = fallback
            for tag in tags:
                if tag in atmosphere_map:
                    atmosphere = atmosphere_map[tag]
                    break

            time_of_day = atmosphere.get('time_of_day', fallback['time_of_day'])
            weather     = atmosphere.get('weather',     fallback['weather'])
            smell       = atmosphere.get('smell',       fallback['smell'])
            crowd       = atmosphere.get('crowd',       fallback['crowd'])

            location_name = game_logic.player.get('location', '')
            if not location_name or location_name.startswith('_'):
                # Find a better label from the disaster template or the first room name
                location_name = details.get('setting_name', 'the scene')
            full_intro = (
                f"Welcome to {color_text(selected_city, 'location', rm)}.\n\n"
                f"It's a {time_of_day}. {weather} The air smells vaguely of {smell}.\n\n"
                f"You've just arrived at the {color_text(location_name, 'location', rm)}.\n"
                f"The crowd around you is {crowd}.\n\n"
                f"Everything is normal. {color_text('Everything seems fine.','npc', rm)}\n\n"
                f"Except {color_text(visionary_name, 'special', rm)} suddenly doesn't think so.\n\n"
                f"One minute they were fine, and the next they're suddenly agitated. Talking fast. Warning anyone who'll "
                f"listen about {color_text(disaster_name, 'error', rm)}.\n"
                f"Except..that hasn't happened; everybody is here, {color_text('alive','npc', rm)}, so naturally, nobody's listening.\n\n"
                f"You're not sure why, but {color_text('something about the way they talk made the hair on the back of your neck stand up.','location',rm)} "
                f"You find yourself oddly concerned. {color_text('Like you almost believe them.','npc',rm)}\n\n"
                f"Maybe you should find them. Talk to them. {color_text('Try to get everyone away.','evidence',rm)} "
                f"Or maybe you should mind your own business.\n\n"
                f"Do you make your way to the exit alone or {color_text('try to convince anybody else to leave', 'special', rm)} if you can?\n\n"
                f"{color_text('You should probably figure that out soon and head for the exit.', 'warning', rm)}"
            )

            self.intro_text_label.text = full_intro

        # ── LATER LEVELS (2+) ────────────────────────────────────────────
        else:
            # The InterLevelScreen handled the narrative. Jump straight to the game!
            self.logger.info(f"IntroScreen bypassed for level {current_level}. Jumping to game.")
            Clock.schedule_once(self.proceed_to_game, 0)
            return

    def proceed_to_game(self, instance=None):
        logging.info("IntroScreen: Proceeding to GameScreen.")
        self.go_to_screen('game', direction='fade')

class TutorialScreen(BaseScreen):
    """
    How To Play screen — three tabs: Overview, Commands, QTE Demos.
    The QTE Demos tab renders live interactive demos of every QTE type
    using the same widget machinery the real game uses (QTEPopup._create_qte_interface).
    """

    _CURRENT_TAB = 'overview'

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self._active_demo = None   # currently running demo widget, if any

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def on_enter(self, *args):
        self._stop_active_demo()
        self._build_ui()

    def on_leave(self, *args):
        self._stop_active_demo()

    # ─────────────────────────────────────────────────────────────────────────
    # Top-level layout
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Build (or rebuild) the entire How to Play screen in Python."""
        from kivy.graphics import Color as GColor, Line, RoundedRectangle as RR

        self.clear_widgets()

        root = BoxLayout(
            orientation='vertical',
            padding=[dp(15), dp(50), dp(15), dp(40)], 
            spacing=dp(6),
        )

        # ── Header ────────────────────────────────────────────────────────────
        header = Label(
            text='[b][color=ff6600]HOW TO PLAY[/color][/b]',
            markup=True,
            font_name='RobotoMonoBold',
            font_size=dp(20),
            size_hint_y=None,
            height=dp(38),
            halign='center',
            valign='middle',
        )
        header.bind(size=lambda i, v: setattr(i, 'text_size', v))
        root.add_widget(header)

        # ── Tab bar  (48dp minimum touch target per Material guidelines) ──────
        self._tab_btns    = {}
        self._content_host = BoxLayout(orientation='vertical')

        tab_bar = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(48),
            spacing=dp(6),
        )

        for tab_id, tab_label in [
            ('overview',  'Overview'),
            ('commands',  'Commands'),
            ('qte_demos', 'QTE Types'),
        ]:
            btn = Button(
                text=tab_label,
                font_name='RobotoMonoBold',
                font_size=dp(14),
                background_normal='',
                background_down='',
                background_color=(0, 0, 0, 0),
                color=(0.1, 0.8, 0.1, 1),
                size_hint_y=None,
                height=dp(48),
                halign='center',
                valign='middle',
            )
            btn.bind(size=lambda i, v: setattr(i, 'text_size', v))

            def _draw_tab(b, *_, active=False):
                b.canvas.before.clear()
                with b.canvas.before:
                    if active:
                        GColor(0.1, 0.8, 0.1, 0.15)
                        RR(pos=b.pos, size=b.size, radius=[dp(4)])
                    GColor(*(
                        (0.1, 0.8, 0.1, 1) if active else (0.35, 0.35, 0.35, 1)
                    ))
                    Line(rounded_rectangle=(b.x, b.y, b.width, b.height, dp(4)),
                         width=dp(1.5) if active else dp(1))

            btn.bind(pos=lambda b, *_: _draw_tab(b, active=(b is self._tab_btns.get(self._CURRENT_TAB))),
                     size=lambda b, *_: _draw_tab(b, active=(b is self._tab_btns.get(self._CURRENT_TAB))))
            btn.bind(on_release=lambda _, t=tab_id: self._switch_tab(t))
            self._tab_btns[tab_id] = btn
            tab_bar.add_widget(btn)

        root.add_widget(tab_bar)
        root.add_widget(self._content_host)

        # Back button — tall enough for thumb tap
        back_btn = self._term_btn('← Back to Menu', height=dp(52))
        back_btn.bind(on_release=lambda *_: self.go_to_screen('title', 'right'))
        root.add_widget(back_btn)

        self.add_widget(root)
        self._switch_tab(self._CURRENT_TAB)

    def _switch_tab(self, tab_id):
        self._CURRENT_TAB = tab_id
        self._stop_active_demo()
        self._content_host.clear_widgets()

        # Refresh tab button highlights
        for tid, btn in self._tab_btns.items():
            from kivy.graphics import Color as GColor, Line, RoundedRectangle as RR
            active = (tid == tab_id)
            btn.canvas.before.clear()
            with btn.canvas.before:
                if active:
                    GColor(0.1, 0.8, 0.1, 0.15)
                    RR(pos=btn.pos, size=btn.size, radius=[dp(4)])
                GColor(*(
                    (0.1, 0.8, 0.1, 1) if active else (0.35, 0.35, 0.35, 1)
                ))
                Line(rounded_rectangle=(btn.x, btn.y, btn.width, btn.height, dp(4)),
                     width=dp(1.2) if active else dp(1))

        builder = {
            'overview':  self._build_overview_tab,
            'commands':  self._build_commands_tab,
            'qte_demos': self._build_qte_tab,
        }.get(tab_id, self._build_overview_tab)

        self._content_host.add_widget(builder())

    # ─────────────────────────────────────────────────────────────────────────
    # Tab: Overview
    # ─────────────────────────────────────────────────────────────────────────

    def _build_overview_tab(self):
        rm = self.resource_manager
        
        omens_warning = color_text("Omens foreshadow Death's attempts to kill you based on what spawned that game.", 'warning', rm)
        
        sections = [
            (
                'WTF Is This?',
                (
                    f"{color_text('DieNamic Engine: Death\'s Design', 'npc', rm)} is a dynamic, text-based survival horror engine.\n"
                    f"You cheated Death once. {color_text('It doesn\'t plan to let you do it again.', 'warning', rm)}\n\n"
                    "Every run begins with a procedurally generated disaster. The setting, the visionary, the victims, and the horrific ways people die all shift to ensure [color=FF3131]no two runs are exactly the same.[/color]\n\n"
                    "Based on who you [color=BC13FE]*have time*[/color] to save, who you [color=BC13FE] *choose* [/color] to leave behind, and [color=BC13FE]if you even make it out of the disaster alive[/color], a unique[color=FF3131] \"Death's Design\" [/color]is forged. "
                    f"You must navigate a persistent city hub, track down surviving NPCs at their procedural workplaces{color_text(' in the correct order', 'warning', rm)}, and try to break the chain before Death catches up to you all.\n\n"
                    f"Will you risk your life intervening to save the others, {color_text('or use them as meat shields to save yourself?', 'special', rm)}"
                ),
            ),
            (
                "The Stats You Can't Ignore",
                (
                    f"{color_text('HP', 'success', rm)} — Hit zero and you're bagged and tagged. "
                    f"Hazards, smoke from fires, bad decisions, failed QTEs and more all chip away at this, but bandages and health items can fix that.\n\n"
                    f"{color_text('Fear', 'warning', rm)} — Rises as danger mounts, as you see omens of Death in the world, and when you witness a gruesome demise. "
                    f"High fear corrupts your interface, triggers hallucinations, and makes every QTE more difficult. "
                    f"Lower it by continuing to press on, but you will take permanent fear if you witness a death.\n\n"
                    f"{color_text('Affinities', 'npc', rm)} — Your character's unique traits. "
                    f"High Perception spots omens before they strike. High Strength allows you to force jammed doors. "
                    f"Affinities can be the difference between life and death, so pick your class wisely.\n\n"
                    f"{color_text('Entropy', 'special', rm)} — Death's anger. As you successfully evade traps, Death loses its patience. "
                    f"The unseen high entropy spawns faster, deadlier, multi-stage Rube Goldberg hazards.\n\n"
                ),
            ),
            (
                'Omens, Evidence, & The Journal',
                (
                    f"Death always leaves a breadcrumb trail. {color_text('Past victims left things behind—photographs, coroner reports, and mementos ', 'special', rm)}"
                    f"{color_text('spanning the films, novels, and comics.', 'special', rm)} Collect them to unlock backstories in your Journal, and collect every character in a set to unlock the full story!\n\n"
                    f"{color_text('Examining your environment can make you aware of what\'s coming.', 'npc', rm)} A nearby radio, an ominous reflection, static on a television... "
                    f"{omens_warning}\n\n"
                    f"Investigate each level as thoroughly {color_text('(or not)', 'npc', rm)} as you want, keep the visionary alive {color_text('(if they aren\'t already dead)', 'npc', rm)} and find evidence or items to help ultimately cheat Death.\n"
                    f"Saving and later locating the visionary will unlock the order of Death's List, {color_text('giving you the vital clues needed to correctly choose your next destination from the Hub', 'npc', rm)}."
                ),
            ),
            (
                'The Gameplay Loop',
                (
                    f"1. {color_text('Explore', 'npc', rm)} dynamic environments using 'Move' + directions.\n"
                    f"2. {color_text('Examining the world will help AND hinder you.', 'npc', rm)} Search containers for hidden items, keys, and lore, but watch your fear level.\n"
                    f"3. {color_text('Watch your step.', 'npc', rm)} The environment is actively trying to kill you. That MRI machine or faulty wiring isn't just set dressing — [color=FF3131]everything is a trap waiting to be sprung.[/color]\n"
                    f"4. {color_text('Survive the QTEs.', 'npc', rm)} When a hazard triggers, rely on your reflexes and wits to type, mash, match the pattern, draw the shape and make quick decisions. [color=FF3131]..that could have even worse consequences[/color].\n"
                    f"5. {color_text('Death knows the order even if you don\'t','npc',rm)}: if your companion is next on [color=FF3131]Death's List[/color], hazards will target them instead. {color_text('Find the visionary to learn the order and intervene before you\'re out of NPCs, otherwise you won\'t have anybody to resucitate you in the finale!', 'npc', rm)}\n"
                    f"6. Navigate the Hub. After surviving a location, you'll retreat to your car and the engine will calculate locations you've unlocked based on who and what you've found. {color_text('Choose your next destination carefully', 'npc', rm)}; the next target is [color=FF3131]ALWAYS[/color] in danger, and can die offscreen if you don't save them in time!\n\n"
                ),
            ),
            (
                'From the Development Team (all one of me, haha)',
                (
                    f"{color_text('If you are reading this, you are a tester. Thank you for braving the engine! Please report any ideas or bugs (and the situation that caused them) to DieNamicEngine@gmail.com.', 'success', rm)}\n"
                    f"{color_text('I hope you enjoy it, and I hope it captures the true, terrifying spirit of the franchise.', 'warning', rm)} {color_text('Good luck.', 'success', rm)}\n"
                ),
            ),
        ]
        return self._scrollable_sections(sections)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab: Commands
    # ─────────────────────────────────────────────────────────────────────────

    def _build_commands_tab(self):
        rm = self.resource_manager

        # command, syntax, description
        commands = [
            ('move',    'move [direction]',
             'Move to an adjacent room. Directions: north, south, east, west, up, down, '
             'or abbreviations: n, s, e, w, u, d.'),
            ('examine', 'examine [target]',
             'Get a detailed description of a room, object, or item in your inventory. '
             'Always examine new rooms and items — descriptions contain vital clues.'),
            ('search',  'search [furniture]',
             'Look inside a container (desk, cabinet, locker, bag). '
             'Hidden items only appear after searching.'),
            ('take',    'take [item]',
             'Pick up an item from the current room or a searched container.'),
            ('use',     'use [item] on [target]',
             'Use a carried item on something in the room. Keys on doors, tools on locks, '
             'medical supplies on yourself.'),
            ('force',   'force [object]',
             'Attempt to physically force open a locked door or container. '
             'Can succeed or cause injury depending on your Strength stat.'),
            ('unlock',  'unlock [target]',
             'Use a key from your inventory on a specific lock. '
             'Prompts a target list if you have applicable keys.'),
            ('combine', 'combine [item1] with [item2]',
             'Combine two items in your inventory to create a new item. '
             'Only certain combinations work, and clues are hidden in item descriptions.'),
            ('inventory','inventory  /  inv',
             'Tap any of the inventory items in your possession to examine them. '
             'You can also type "examine [item]" to get a description of an item in your inventory.'),
            ('talk',    'talk [npc]',
             'Speak with a character in the room. Dialogue choices may appear.'),
            ('wait',    'wait',
             'Pass a turn without acting. Rarely useful — every turn costs you time.'),
            ('map',     'map',
             'Display the ASCII map of the current level.'),
            ('help',    'help',
             'Show all commands available in the current context.'),
            ('save',    'save',
             'Save the current game state.'),
        ]

        sv = ScrollView(do_scroll_x=False, bar_width=dp(4), bar_color=(0.1,0.8,0.1,1))
        grid = GridLayout(cols=1, spacing=dp(5), size_hint_y=None, padding=[0, dp(4)])
        grid.bind(minimum_height=grid.setter('height'))

        SYN_H   = dp(26)
        PAD_W   = dp(12)
        PAD_H   = dp(10)
        SPACING = dp(5)

        for verb, syntax, desc in commands:
            row = BoxLayout(orientation='vertical', size_hint_y=None,
                            padding=[PAD_W, PAD_H], spacing=SPACING)
            self._panel_bg(row)

            syn_lbl = Label(
                text=f'[b][color=1acc1a]{syntax}[/color][/b]',
                markup=True, font_name='RobotoMonoBold', font_size=dp(14),
                halign='left', valign='middle', size_hint_y=None, height=SYN_H,
            )
            syn_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            row.add_widget(syn_lbl)

            desc_lbl = Label(
                text=f'[color=bbbbbb]{desc}[/color]',
                markup=True, font_name='RobotoMono', font_size=dp(13),
                halign='left', valign='top', size_hint_y=None, height=dp(40),
            )
            row.add_widget(desc_lbl)

            row.height = SYN_H + SPACING + dp(40) + PAD_H * 2

            def _measure(dt, r=row, dl=desc_lbl,
                         pw=PAD_W, ph=PAD_H, sp=SPACING, sh=SYN_H):
                avail = r.width - pw * 2
                if avail <= 0:
                    Clock.schedule_once(lambda dt2: _measure(dt2, r=r, dl=dl,
                                        pw=pw, ph=ph, sp=sp, sh=sh), 0)
                    return
                dl.text_size = (avail, None)
                dl.texture_update()
                dh = dl.texture_size[1]
                dl.height = dh
                r.height = sh + sp + dh + ph * 2

            Clock.schedule_once(_measure, 0)
            grid.add_widget(row)

        sv.add_widget(grid)
        return sv

    # ─────────────────────────────────────────────────────────────────────────
    # Tab: QTE Demos
    # ─────────────────────────────────────────────────────────────────────────

    def _build_qte_tab(self):
        """
        Renders an accordion-style list of every QTE type.
        Tapping "Try It" spawns the actual interactive widget inline —
        the same QTEPopup._create_qte_interface machinery the real game uses.
        Tapping "Reset" tears it down and returns to the description card.
        """
 
        # ── Complete QTE catalog — every type from qte_definitions.json ──
        QTE_CATALOG = [
            # ── Text / Word ──────────────────────────────────────────────
            {
                'name':        'Word Input',
                'key':         'input',
                'input_type':  'word',
                'ui_type':     'text_input',
                'color':       (0.1, 0.6, 1.0, 1),
                'icon':        '⌨',
                'description': (
                    "A word appears on screen. Type it exactly — correct spelling, "
                    "correct case — within the time limit and hit Enter. "
                    "Works against your Fear level: the higher the fear, "
                    "the harder it is to read clearly."
                ),
                'demo_context': {
                    'ui_type': 'text_input',
                    'expected_input_word': 'DODGE',
                    'ui_prompt_message': 'TYPE THE WORD SHOWN:',
                    'description': 'Type the correct word to survive.',
                },
            },
            # ── Mash ─────────────────────────────────────────────────────
            {
                'name':        'Button Mash',
                'key':         'button_mash',
                'input_type':  'mash',
                'ui_type':     'tap_area',
                'color':       (1.0, 0.3, 0.1, 1),
                'icon':        '👊',
                'description': (
                    "Tap the MASH button (or any key on keyboard) as fast as "
                    "possible to reach the target count before time expires. "
                    "Used for brute-force situations: breaking free, forcing "
                    "a door, outrunning a wave."
                ),
                'demo_context': {
                    'ui_type': 'tap_area',
                    'effective_target_mash_count': 10,
                    'ui_prompt_message': 'MASH TO BREAK FREE:',
                    'description': 'Tap as fast as you can!',
                },
            },
            {
                'name':        'Break Free / Spam',
                'key':         'spam_any_key',
                'input_type':  'mash',
                'ui_type':     'tap_area',
                'color':       (1.0, 0.5, 0.0, 1),
                'icon':        '💥',
                'description': (
                    "A variant of Button Mash — mash any key or tap repeatedly "
                    "to build a score past the threshold. "
                    "Used when you're grabbed, pinned, or trapped."
                ),
                'demo_context': {
                    'ui_type': 'tap_area',
                    'effective_target_mash_count': 12,
                    'ui_prompt_message': 'SPAM TO BREAK FREE:',
                    'description': 'Tap repeatedly until the bar fills!',
                },
            },
            # ── Sequence ─────────────────────────────────────────────────
            {
                'name':        'Sequence Input',
                'key':         'sequence_input',
                'input_type':  'sequence',
                'ui_type':     'button_sequence',
                'color':       (0.6, 0.2, 1.0, 1),
                'icon':        '🔢',
                'description': (
                    "A target sequence is displayed. Tap the buttons in that "
                    "exact order. Wrong presses are recorded — the game tracks "
                    "your entered sequence vs the required one. "
                    "Used for access codes and multi-step input."
                ),
                'demo_context': {
                    'ui_type': 'button_sequence',
                    'required_sequence': ['D', 'A', 'C', 'B'],
                    'button_labels': ['A', 'B', 'C', 'D'],
                    'ui_prompt_message': 'ENTER SEQUENCE: A → C → B',
                    'description': 'Tap buttons in the required order.',
                },
            },
            {
                'name':        'Override Code',
                'key':         'multiword_code',
                'input_type':  'sequence',
                'ui_type':     'button_sequence',
                'color':       (0.8, 0.1, 0.4, 1),
                'icon':        '🔑',
                'description': (
                    "Similar to Sequence Input but with words instead of letters. "
                    "A specific phrase must be assembled in order from a pool of "
                    "buttons. Used for system overrides and security bypasses."
                ),
                'demo_context': {
                    'ui_type': 'button_sequence',
                    'required_sequence': ['SYSTEM', 'OVERRIDE', 'CONFIRM'],
                    'button_labels': ['SYSTEM', 'ABORT', 'OVERRIDE',
                                      'DENY', 'CONFIRM', 'RESET'],
                    'ui_prompt_message': 'INITIATE OVERRIDE SEQUENCE:',
                    'description': 'SYSTEM → OVERRIDE → CONFIRM',
                },
            },
            {
                'name':        'Evasive Maneuvers',
                'key':         'directional_input',
                'input_type':  'sequence',
                'ui_type':     'directional_pad',
                'color':       (0.2, 0.7, 1.0, 1),
                'icon':        '🕹',
                'description': (
                    "Directional arrows flash on screen. Tap the matching "
                    "direction buttons in the correct order — UP, LEFT, DOWN, "
                    "RIGHT. Think of it as a dodge-roll sequence. "
                    "Miss the order and you take the hit."
                ),
                'demo_context': {
                    'ui_type': 'directional_pad',
                    'required_sequence': ['UP', 'LEFT', 'DOWN', 'RIGHT'],
                    'button_labels': ['UP', 'DOWN', 'LEFT', 'RIGHT'],
                    'ui_prompt_message': 'TAP: UP → LEFT → DOWN → RIGHT',
                    'description': 'Follow the directional sequence.',
                },
            },
            # ── Hold ─────────────────────────────────────────────────────
            {
                'name':        'Hold and Release',
                'key':         'hold_and_release',
                'input_type':  'hold_release',
                'ui_type':     'hold_release',
                'color':       (0.9, 0.6, 0.1, 1),
                'icon':        '⏱',
                'description': (
                    "Press and hold the button. A timer bar fills up. "
                    "A green window appears — release INSIDE that window. "
                    "Too early or too late and you fail. "
                    "Used for timing a jump, releasing pressure, or "
                    "disengaging a lock at the right moment."
                ),
                'demo_context': {
                    'ui_type': 'hold_release',
                    'input_type': 'hold_release',
                    'release_window_default': [0.5, 0.75],
                    'duration': 4.0,
                    'ui_prompt_message': 'HOLD... THEN RELEASE IN THE GREEN ZONE:',
                    'description': 'Hold the button, release when the bar is green.',
                },
            },
            {
                'name':        'Hold to Threshold',
                'key':         'hold_to_threshold',
                'input_type':  'hold',
                'ui_type':     'hold',
                'color':       (0.8, 0.5, 0.0, 1),
                'icon':        '✊',
                'description': (
                    "Press and hold the button until the bar fills past the "
                    "target threshold — then release. Let go too early and you "
                    "fail. Used for sustained effort: holding a door shut, "
                    "applying pressure to a wound, gripping a ledge."
                ),
                'demo_context': {
                    'ui_type': 'hold',
                    'input_type': 'hold',
                    'required_hold_time_default': 2.0,
                    'duration': 6.0,
                    'ui_prompt_message': 'HOLD UNTIL THE BAR TURNS GREEN:',
                    'description': 'Hold for 2 seconds, then release.',
                },
            },
            # ── Alternate ────────────────────────────────────────────────
            {
                'name':        'Struggle / Alternate',
                'key':         'alternating_keys',
                'input_type':  'alternate',
                'ui_type':     'alternating_buttons',
                'color':       (0.1, 0.9, 0.5, 1),
                'icon':        '⇄',
                'description': (
                    "Two buttons alternate. Press LEFT, then RIGHT, then LEFT, "
                    "and keep going. Miss the pattern or slow down and you lose "
                    "ground. Models physical struggle: keeping your balance, "
                    "fighting a current, wrestling free."
                ),
                'demo_context': {
                    'ui_type': 'alternating_buttons',
                    'button_labels': ['LEFT', 'RIGHT'],
                    'target_alternations_default': 8,
                    'ui_prompt_message': 'ALTERNATE LEFT ↔ RIGHT (8 times):',
                    'description': 'Tap LEFT and RIGHT in alternating order.',
                },
            },
            {
                'name':        'Balance Meter',
                'key':         'balance_meter',
                'input_type':  'alternate',
                'ui_type':     'alternating_buttons',
                'color':       (0.3, 0.9, 0.7, 1),
                'icon':        '⚖',
                'description': (
                    "A tighter, faster version of Alternate. The time limit is "
                    "shorter and the alternation target is high. "
                    "Used when balance is critical — walking a narrow beam, "
                    "stabilizing on a slippery surface."
                ),
                'demo_context': {
                    'ui_type': 'alternating_buttons',
                    'button_labels': ['LEFT', 'RIGHT'],
                    'target_alternations_default': 6,
                    'ui_prompt_message': 'KEEP YOUR BALANCE (6 taps):',
                    'description': 'Alternate quickly — don\'t fall!',
                },
            },
            # ── Reaction ─────────────────────────────────────────────────
            {
                'name':        'Reflex Check',
                'key':         'reaction_single_key',
                'input_type':  'single_key',
                'ui_type':     'reaction_button',
                'color':       (1.0, 0.85, 0.0, 1),
                'icon':        '⚡',
                'description': (
                    "Wait. The target turns GREEN. Tap immediately. "
                    "Tapping early counts as a failure. Tests pure reaction time "
                    "— used for sudden dodge events, catching a falling object, "
                    "hitting a switch at the right moment."
                ),
                'demo_context': {
                    'ui_type': 'reaction_button',
                    'wait_time_range': [1.5, 3.0],
                    'ui_prompt_message': 'WAIT FOR GREEN... THEN TAP!',
                    'description': 'Do not tap until the button turns green.',
                },
            },
            # ── Choice ───────────────────────────────────────────────────
            {
                'name':        'Critical Decision',
                'key':         'timed_choice',
                'input_type':  'choice',
                'ui_type':     'choice_buttons',
                'color':       (1.0, 0.4, 0.0, 1),
                'icon':        '?',
                'description': (
                    "A situation presents itself. Multiple choices appear. "
                    "Pick the right one before time runs out — or pick wrong "
                    "and pay the price. Indecision is the same as wrong."
                ),
                'demo_context': {
                    'ui_type': 'choice_buttons',
                    'choices': ['Run', 'Hide', 'Fight', 'Freeze'],
                    'button_colors': {},
                    'ui_prompt_message': 'MAKE YOUR CHOICE:',
                    'description': 'One option is correct. Choose wisely.',
                },
            },
            {
                'name':        'Emergency Abort',
                'key':         'targeted_cancel',
                'input_type':  'cancel',
                'ui_type':     'choice_buttons',
                'color':       (0.9, 0.05, 0.05, 1),
                'icon':        '🚨',
                'description': (
                    "A grid of buttons appears. Only one is the correct abort "
                    "action — usually highlighted in red. All the wrong options "
                    "have consequences. Don't hesitate. Read the labels."
                ),
                'demo_context': {
                    'ui_type': 'choice_buttons',
                    'choices': ['INITIATE', 'DEPLOY', 'CANCEL', 'EXECUTE'],
                    'button_colors': {'CANCEL': 'ff0000', 'default': '333333'},
                    'ui_prompt_message': 'PRESS THE RED BUTTON TO ABORT:',
                    'description': 'Find and press the correct abort button.',
                },
            },
            # ── Rhythm ───────────────────────────────────────────────────
            {
                'name':        'Frequency Match',
                'key':         'rhythm_timing',
                'input_type':  'rhythm',
                'ui_type':     'rhythm_bar',
                'color':       (0.2, 0.9, 0.8, 1),
                'icon':        '♪',
                'description': (
                    "A cursor sweeps back and forth across a bar. "
                    "Tap when the cursor is inside the green zone — hit the "
                    "required number of beats to succeed. "
                    "Used for calibrating equipment or timing an intervention."
                ),
                'demo_context': {
                    'ui_type': 'rhythm_bar',
                    'beat_speed': 1.5,
                    'target_zone': [0.35, 0.65],
                    'ui_prompt_message': 'TAP IN THE GREEN ZONE (3 times):',
                    'description': 'Tap when the cursor is in the green zone.',
                },
            },
            # ── Memory ───────────────────────────────────────────────────
            {
                'name':        'Neural Link',
                'key':         'pattern_memory',
                'input_type':  'pattern',
                'ui_type':     'memory_grid',
                'color':       (0.5, 0.3, 0.9, 1),
                'icon':        '🧠',
                'description': (
                    "A grid of tiles flashes a pattern — watch carefully. "
                    "Once the pattern finishes, tap the tiles in the same order "
                    "from memory. One wrong tap and you fail. "
                    "Used for hacking panels, neural interfaces, and puzzles."
                ),
                'demo_context': {
                    'ui_type': 'memory_grid',
                    'grid_size': 3,
                    'pattern': [0, 4, 8],  # diagonal pattern for demo
                    'ui_prompt_message': 'WATCH THE PATTERN, THEN REPEAT IT:',
                    'description': 'Memorize, then tap tiles in the same order.',
                },
            },
            # ── Aim ──────────────────────────────────────────────────────
            {
                'name':        'Target Practice',
                'key':         'aim_and_click',
                'input_type':  'aim_click',
                'ui_type':     'aim_area',
                'color':       (1.0, 0.15, 0.15, 1),
                'icon':        '🎯',
                'description': (
                    "Red targets appear at random positions. Tap them before "
                    "they vanish. Each hit spawns the next target in a new spot. "
                    "Hit the required number to succeed. "
                    "Used for shooting, swatting, or disabling threats."
                ),
                'demo_context': {
                    'ui_type': 'aim_area',
                    'target_count': 3,
                    'ui_prompt_message': 'TAP THE RED TARGETS (3 hits):',
                    'description': 'Hit all targets before time runs out.',
                },
            },
            # ── Trace / Drag ─────────────────────────────────────────────
            {
                'name':        'Circuit Trace',
                'key':         'drag_track',
                'input_type':  'drag',
                'ui_type':     'trace_path',
                'color':       (0.3, 0.7, 0.3, 1),
                'icon':        '✎',
                'description': (
                    "A path is displayed — a wavy line, zigzag, or arc. "
                    "Drag your finger from the green start node to the red end. "
                    "Stay within tolerance or the cursor decays back. "
                    "Used for rewiring circuits, tracing pipes, or following a route."
                ),
                'demo_context': {
                    'ui_type': 'trace_path',
                    'path_type': 'line',
                    'ui_prompt_message': 'TRACE THE PATH FROM GREEN TO RED:',
                    'description': 'Drag along the line from start to end.',
                },
            },
            {
                'name':        'Stabilize Vortex',
                'key':         'mouse_spiral',
                'input_type':  'spiral',
                'ui_type':     'trace_path',
                'color':       (0.4, 0.5, 0.9, 1),
                'icon':        '🌀',
                'description': (
                    "A spiral path appears. Drag from the center outward (or "
                    "inward) following the spiral curve. Going off-path causes "
                    "the cursor to decay backward. "
                    "Used for stabilizing vortexes, spinning valves, and containment."
                ),
                'demo_context': {
                    'ui_type': 'trace_path',
                    'path_type': 'spiral',
                    'ui_prompt_message': 'TRACE THE SPIRAL FROM CENTER TO EDGE:',
                    'description': 'Follow the spiral — stay on the path.',
                },
            },
            # ── Precision Gauge ──────────────────────────────────────────
            {
                'name':        'Pressure Calibration',
                'key':         'precision_tap_count',
                'input_type':  'tap',
                'ui_type':     'precision_gauge',
                'color':       (0.9, 0.7, 0.1, 1),
                'icon':        '⏲',
                'description': (
                    "A vertical gauge decays over time. Tap PUMP to fill it. "
                    "Keep the bar inside the green target zone when time expires "
                    "to succeed. Overshoot or undershoot and you fail. "
                    "Used for regulating pressure, maintaining flow, or "
                    "balancing a volatile system."
                ),
                'demo_context': {
                    'ui_type': 'precision_gauge',
                    'target_zone': [10, 15],
                    'decay_rate': 0.5,
                    'ui_prompt_message': 'PUMP TO KEEP THE BAR IN THE GREEN ZONE:',
                    'description': 'Tap to fill — don\'t overshoot or let it drain.',
                },
            },
        ]
 
        outer = BoxLayout(orientation='vertical', spacing=dp(5))
        sv = ScrollView(do_scroll_x=False, bar_width=dp(4),
                         bar_color=(0.1, 0.8, 0.1, 1))
        grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                           padding=[0, dp(4)])
        grid.bind(minimum_height=grid.setter('height'))
 
        for qte in QTE_CATALOG:
            card = self._build_qte_card(qte, grid)
            grid.add_widget(card)
 
        sv.add_widget(grid)
        outer.add_widget(sv)
        return outer

    def _build_qte_card(self, qte_def: dict, parent_grid) -> Widget:
        """Card for one QTE type: icon, name, description, Try It button, demo area."""
        from kivy.graphics import Color as GColor, Line, RoundedRectangle as RR

        c = qte_def['color']
        border_c = (c[0]*0.7, c[1]*0.7, c[2]*0.7, 1)

        card = BoxLayout(orientation='vertical', size_hint_y=None,
                         padding=[dp(10), dp(10)], spacing=dp(8))

        def _bg(w, *_):
            w.canvas.before.clear()
            with w.canvas.before:
                GColor(0.07, 0.07, 0.07, 1)
                RR(pos=w.pos, size=w.size, radius=[dp(5)])
                GColor(*border_c)
                Line(rounded_rectangle=(w.x, w.y, w.width, w.height, dp(5)), width=dp(1))
        card.bind(pos=_bg, size=_bg)

        # Header row: icon + name
        hrow = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(30), spacing=dp(8))
        icon_lbl = Label(
            text=qte_def['icon'],
            font_name='RobotoMono', font_size=dp(20),
            size_hint_x=None, width=dp(28),
            color=c, halign='center', valign='middle',
        )
        hrow.add_widget(icon_lbl)
        name_lbl = Label(
            text=f'[b]{qte_def["name"].upper()}[/b]',
            markup=True, font_name='RobotoMonoBold', font_size=dp(14),
            color=c, halign='left', valign='middle', size_hint_x=1,
        )
        name_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        hrow.add_widget(name_lbl)
        card.add_widget(hrow)
        total_h = dp(30) + dp(8)

        # Description — height resolved after layout via Clock
        desc_lbl = Label(
            text=f'[color=bbbbbb]{qte_def["description"]}[/color]',
            markup=True, font_name='RobotoMono', font_size=dp(13),
            halign='left', valign='top', size_hint_y=None, height=dp(60),
        )
        card.add_widget(desc_lbl)
        total_h += dp(60) + dp(8)

        # Demo area (starts empty, expands when Try It pressed)
        demo_area = BoxLayout(orientation='vertical', size_hint_y=None, height=0, spacing=dp(6))
        card.add_widget(demo_area)

        # Try It — 48dp minimum touch target
        try_btn = self._term_btn(f'{qte_def["icon"]}  Try It', height=dp(48), accent_color=c)
        card.add_widget(try_btn)
        total_h += dp(48) + dp(8)

        card.height = total_h + dp(10)

        # Deferred: set text_size once we know card width, then correct card height
        def _measure_desc(dt, crd=card, dl=desc_lbl, base_h=total_h + dp(10)):
            avail = crd.width - dp(10) * 2  # match card padding
            if avail <= 0:
                Clock.schedule_once(lambda dt2: _measure_desc(dt2, crd=crd, dl=dl, base_h=base_h), 0)
                return
            dl.text_size = (avail, None)
            dl.texture_update()
            dh = dl.texture_size[1]
            delta = dh - dp(60)           # how much bigger/smaller than placeholder
            dl.height = dh
            crd.height = base_h + delta

        Clock.schedule_once(_measure_desc, 0)

        def _launch_demo(_, qdef=qte_def, da=demo_area, btn=try_btn, crd=card):
            self._stop_active_demo()
            if da.height > 0:
                # Already open — close it
                da.clear_widgets()
                da.height = 0
                crd.height -= dp(220)
                btn.text = f'{qdef["icon"]}  Try It'
                return

            # Build the demo widget
            demo_widget = self._build_inline_demo(qdef)
            if demo_widget is None:
                return
            da.add_widget(demo_widget)
            da.height = dp(210)
            crd.height += dp(220)
            btn.text = f'{qdef["icon"]}  Close Demo'
            self._active_demo = (demo_widget, da, crd, dp(220))

        try_btn.bind(on_release=_launch_demo)
        return card

    def _build_inline_demo(self, qte_def: dict) -> Widget:
        """
        Spawn a live, playable demo widget for a QTE type.
        Uses the same widget classes the real game uses but in a contained
        BoxLayout — no popup chrome, no timer.
        """
        from .widgets import (QTEPopup, RhythmWidget, ReactionWidget,
                              TracePathWidget, AimTargetWidget,
                              MemoryGridWidget, PrecisionGaugeWidget)
 
        ctx         = qte_def.get('demo_context', {})
        ui_type     = ctx.get('ui_type', 'text_input')
        prompt_msg  = ctx.get('ui_prompt_message', '')
        desc        = ctx.get('description', '')
 
        container = BoxLayout(orientation='vertical', spacing=dp(6),
                              size_hint_y=None, height=dp(200),
                              padding=[dp(4), dp(4)])
        from kivy.graphics import Color as GColor, RoundedRectangle as RR
        def _bg(w, *_):
            w.canvas.before.clear()
            with w.canvas.before:
                GColor(0.04, 0.04, 0.04, 1)
                RR(pos=w.pos, size=w.size, radius=[dp(3)])
        container.bind(pos=_bg, size=_bg)
 
        # Result feedback label at top
        result_lbl = Label(
            text='',
            markup=True, font_name='RobotoMono', font_size=dp(12),
            size_hint_y=None, height=dp(20),
            halign='center', valign='middle',
        )
        result_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        container.add_widget(result_lbl)
 
        def _show_result(success: bool, msg: str = ''):
            if success:
                result_lbl.text = (
                    f'[color=00cc44][b]✓  {msg or "SUCCESS"}[/b][/color]')
            else:
                result_lbl.text = (
                    f'[color=ff4444][b]✗  {msg or "TRY AGAIN"}[/b][/color]')
 
        # Prompt label
        if prompt_msg:
            pl = Label(
                text=(f'[color=ffaa00][b]{prompt_msg}[/b][/color]\n'
                      f'[color=777777][size=11sp]{desc}[/size][/color]'),
                markup=True, font_name='RobotoMono', font_size=dp(12),
                size_hint_y=None, height=dp(36),
                halign='center', valign='middle',
            )
            pl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            container.add_widget(pl)
 
        def _demo_callback(event):
            """Generic callback — shows a result indicator for demo purposes."""
            if isinstance(event, str):
                _show_result(True, f'Input: {event[:20]}')
            elif isinstance(event, dict):
                ev = event.get('event', '')
                if ev == 'choice_selected':
                    _show_result(True, f'Chose: {event.get("choice", "?")}')
                elif ev == 'rhythm_tap':
                    hit = event.get('predicted_success', False)
                    _show_result(hit, 'On beat!' if hit else 'Missed zone')
                elif ev == 'reaction_tap':
                    rt = event.get('reaction_time', 99)
                    _show_result(rt < 5,
                                 f'Reaction: {rt:.2f}s' if rt < 5
                                 else 'Too early!')
                elif ev in ('aim_success', 'trace_complete'):
                    _show_result(True, 'Complete!')
                elif ev == 'mash_press':
                    cnt = event.get('count', 0)
                    tgt = ctx.get('effective_target_mash_count', 10)
                    if cnt >= tgt:
                        _show_result(True, f'Done! ({cnt}/{tgt})')
                    else:
                        result_lbl.text = (
                            f'[color=ffaa00]Progress: {cnt}/{tgt}[/color]')
                elif ev == 'sequence_input':
                    seq = event.get('sequence', [])
                    result_lbl.text = (
                        f'[color=ffaa00]Entered: '
                        f'{" → ".join(str(x) for x in seq)}[/color]')
                elif ev == 'hold_release':
                    dur = event.get('duration', 0)
                    result_lbl.text = (
                        f'[color=ffaa00]Held: {dur:.1f}s[/color]')
                elif ev == 'memory_input':
                    result_lbl.text = (
                        f'[color=ffaa00]Tile: {event.get("index", "?")}[/color]')
                elif ev == 'gauge_update':
                    val = event.get('value', 0)
                    tz = ctx.get('target_zone', [10, 15])
                    in_zone = tz[0] <= val <= tz[1]
                    color = '00cc44' if in_zone else 'ffaa00'
                    result_lbl.text = (
                        f'[color={color}]Gauge: {val:.1f} '
                        f'(zone: {tz[0]}-{tz[1]})[/color]')
                elif ev == 'key_press':
                    _show_result(True, f'Key: {event.get("key", "?")}')
                elif ev == 'alternation':
                    cnt = event.get('count', 0)
                    tgt = ctx.get('target_alternations_default', 8)
                    if cnt >= tgt:
                        _show_result(True, f'Done! ({cnt}/{tgt})')
                    else:
                        result_lbl.text = (
                            f'[color=ffaa00]Alternations: '
                            f'{cnt}/{tgt}[/color]')
                else:
                    _show_result(True, str(ev)[:30])
 
        # ── Widget selection — mirrors QTEPopup._create_qte_interface ────
        try:
            if ui_type == 'text_input':
                target = ctx.get('expected_input_word', 'DODGE')
                disp_lbl = Label(
                    text=f'Type: [b][color=00ff00]'
                         f'{str(target).upper()}[/color][/b]',
                    markup=True, font_name='RobotoMono', font_size=dp(14),
                    size_hint_y=None, height=dp(28), halign='center',
                )
                disp_lbl.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(disp_lbl)
                ti = TextInput(multiline=False, size_hint_y=None,
                               height=dp(36), halign='center',
                               hint_text='Type here...')
                def _on_validate(ti=ti, tgt=target):
                    entered = ti.text.strip().lower()
                    _show_result(
                        entered == str(tgt).lower(),
                        'Correct!' if entered == str(tgt).lower()
                        else f'Got: "{entered}"')
                    ti.text = ''
                ti.bind(on_text_validate=lambda *_: _on_validate())
                container.add_widget(ti)
                sub = self._term_btn('Submit', height=dp(32))
                sub.bind(on_release=lambda *_: _on_validate())
                container.add_widget(sub)
                Clock.schedule_once(
                    lambda dt: setattr(ti, 'focus', True), 0.3)
 
            elif ui_type == 'tap_area':
                target = ctx.get('effective_target_mash_count', 10)
                ctr_lbl = Label(
                    text=f'Presses: 0/{target}',
                    font_name='RobotoMono', font_size=dp(16),
                    size_hint_y=None, height=dp(30), halign='center',
                )
                ctr_lbl.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(ctr_lbl)
                mash_count = [0]
                mash_btn = self._term_btn(
                    'MASH!', height=dp(52),
                    accent_color=(1.0, 0.3, 0.1, 1))
                def _mash(*_):
                    mash_count[0] += 1
                    ctr_lbl.text = f'Presses: {mash_count[0]}/{target}'
                    _demo_callback({
                        'event': 'mash_press',
                        'count': mash_count[0]})
                mash_btn.bind(on_release=_mash)
                container.add_widget(mash_btn)
 
            elif ui_type in ('button_sequence', 'directional_pad'):
                req_seq    = ctx.get('required_sequence', [])
                btn_labels = ctx.get('button_labels', req_seq)
                entered    = []
                seq_lbl = Label(
                    text=(f'Target: [b]'
                          f'{" → ".join(str(x) for x in req_seq)}[/b]\n'
                          f'Entered: —'),
                    markup=True, font_name='RobotoMono', font_size=dp(12),
                    size_hint_y=None, height=dp(36), halign='center',
                )
                seq_lbl.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(seq_lbl)
                # Use 2 columns for directional pad, 3 for sequences
                n_cols = 2 if ui_type == 'directional_pad' else 3
                rows = math.ceil(len(btn_labels) / n_cols)
                btns_row = GridLayout(
                    cols=n_cols, spacing=dp(4),
                    size_hint_y=None, height=dp(rows * 48))
                def _seq_press(val, entered=entered, req=req_seq,
                               sl=seq_lbl):
                    entered.append(val)
                    entered_str = ' → '.join(str(x) for x in entered)
                    sl.text = (
                        f'Target: [b]'
                        f'{" → ".join(str(x) for x in req)}[/b]\n'
                        f'Entered: {entered_str}')
                    if len(entered) == len(req):
                        match = [str(a).upper() == str(b).upper()
                                 for a, b in zip(entered, req)]
                        _show_result(
                            all(match),
                            'Correct!' if all(match) else 'Wrong order')
                        entered.clear()
                for lbl in btn_labels:
                    b = self._term_btn(str(lbl), height=dp(40))
                    b.bind(on_release=lambda _, v=lbl: _seq_press(v))
                    btns_row.add_widget(b)
                container.add_widget(btns_row)
 
            elif ui_type == 'reaction_button':
                rw = ReactionWidget(
                    callback=_demo_callback,
                    wait_range=ctx.get('wait_time_range', [1.5, 3.0]),
                    size_hint_y=None, height=dp(80),
                )
                container.add_widget(rw)
                self._active_demo_widget = rw
 
            elif ui_type == 'rhythm_bar':
                rw = RhythmWidget(
                    callback=_demo_callback,
                    speed=ctx.get('beat_speed', 1.5),
                    target_zone=tuple(
                        ctx.get('target_zone', [0.35, 0.65])),
                    size_hint_y=None, height=dp(60),
                )
                container.add_widget(rw)
                beats  = [0]
                beat_t = 3
                beat_lbl = Label(
                    text=f'Beats: 0/{beat_t}',
                    font_name='RobotoMono', font_size=dp(12),
                    size_hint_y=None, height=dp(22), halign='center',
                )
                beat_lbl.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(beat_lbl)
                orig_cb = _demo_callback
                def _rhythm_cb(ev, bl=beat_lbl, bt=beats, tt=beat_t):
                    if (isinstance(ev, dict)
                            and ev.get('event') == 'rhythm_tap'):
                        if ev.get('predicted_success'):
                            bt[0] += 1
                            bl.text = f'Beats: {bt[0]}/{tt}'
                            if bt[0] >= tt:
                                _show_result(True, 'Rhythm mastered!')
                            else:
                                result_lbl.text = (
                                    f'[color=00cc44]'
                                    f'Hit! ({bt[0]}/{tt})[/color]')
                        else:
                            result_lbl.text = (
                                '[color=ff4444]Missed the zone[/color]')
                    orig_cb(ev)
                rw.callback = _rhythm_cb
                self._active_demo_widget = rw
 
            elif ui_type == 'choice_buttons':
                choices    = ctx.get('choices', ['Option A', 'Option B'])
                btn_colors = ctx.get('button_colors', {})
                rows = math.ceil(len(choices) / 2)
                btns_row = GridLayout(
                    cols=2, spacing=dp(6),
                    size_hint_y=None, height=dp(rows * 52),
                )
                from kivy.utils import get_color_from_hex
                for ch in choices:
                    hex_c = btn_colors.get(str(ch))
                    accent = (get_color_from_hex(hex_c) if hex_c
                              else (0.1, 0.8, 0.1, 1))
                    b = self._term_btn(str(ch), height=dp(44),
                                       accent_color=accent)
                    b.bind(on_release=lambda _, v=ch: _demo_callback(
                        {'event': 'choice_selected', 'choice': v}))
                    btns_row.add_widget(b)
                container.add_widget(btns_row)
 
            elif ui_type == 'trace_path':
                tw = TracePathWidget(
                    callback=_demo_callback,
                    path_type=ctx.get('path_type', 'line'),
                    size_hint_y=None, height=dp(140),
                )
                container.add_widget(tw)
 
            # ── NEW DEMO WIDGETS ─────────────────────────────────────────
 
            elif ui_type in ('hold', 'hold_release'):
                import time as _time
                input_type = ctx.get('input_type', 'hold')
                is_release = input_type in ('hold_release',
                                            'hold_and_release')
 
                # Progress bar
                from kivy.uix.progressbar import ProgressBar
 
                hold_display = Label(
                    text='Press and hold...',
                    markup=True, font_name='RobotoMono',
                    font_size=dp(14),
                    size_hint_y=None, height=dp(28),
                    halign='center',
                )
                hold_display.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(hold_display)
 
                hold_progress = ProgressBar(
                    max=100, value=0,
                    size_hint_y=None, height=dp(16))
                container.add_widget(hold_progress)
 
                # Calculate target window
                demo_dur = float(ctx.get('duration', 5.0))
                if is_release:
                    window = ctx.get('release_window_default', [0.5, 0.75])
                    lo_frac = float(window[0])
                    hi_frac = float(window[1])
                    if lo_frac <= 1.0 and hi_frac <= 1.0:
                        target_lo = lo_frac * demo_dur
                        target_hi = hi_frac * demo_dur
                    else:
                        target_lo, target_hi = lo_frac, hi_frac
                    hold_progress.max = demo_dur
                else:
                    target_lo = float(ctx.get('required_hold_time_default',
                                              2.0))
                    target_hi = demo_dur
                    hold_progress.max = target_lo
 
                hold_state = {
                    'start': None,
                    'timer': None,
                }
 
                def _hold_press(*_):
                    hold_state['start'] = _time.time()
                    hold_display.text = 'Holding...'
                    hold_progress.value = 0
                    def _tick(dt):
                        if hold_state['start'] is None:
                            return False
                        elapsed = _time.time() - hold_state['start']
                        hold_progress.value = min(elapsed,
                                                  hold_progress.max)
                        if is_release:
                            if elapsed < target_lo:
                                hold_display.text = (
                                    f'Holding... {elapsed:.1f}s '
                                    f'(too early!)')
                            elif elapsed <= target_hi:
                                hold_display.text = (
                                    f'[color=00ff00]RELEASE NOW! '
                                    f'{elapsed:.1f}s[/color]')
                            else:
                                hold_display.text = (
                                    f'[color=ff0000]Too late! '
                                    f'{elapsed:.1f}s[/color]')
                        else:
                            if elapsed >= target_lo:
                                hold_display.text = (
                                    f'[color=00ff00]RELEASE! '
                                    f'{elapsed:.1f}s / '
                                    f'{target_lo:.1f}s[/color]')
                            else:
                                hold_display.text = (
                                    f'Holding... {elapsed:.1f}s / '
                                    f'{target_lo:.1f}s')
                    hold_state['timer'] = Clock.schedule_interval(
                        _tick, 0.05)
 
                def _hold_release(*_):
                    if hold_state['timer']:
                        hold_state['timer'].cancel()
                        hold_state['timer'] = None
                    if hold_state['start'] is not None:
                        dur = _time.time() - hold_state['start']
                        hold_state['start'] = None
                        hold_display.text = f'Released: {dur:.1f}s'
                        if is_release:
                            ok = target_lo <= dur <= target_hi
                            _show_result(
                                ok,
                                f'{dur:.1f}s — '
                                f'{"In window!" if ok else "Out of window"}'
                                f' [{target_lo:.1f}-{target_hi:.1f}s]')
                        else:
                            ok = dur >= target_lo
                            _show_result(
                                ok,
                                f'{dur:.1f}s — '
                                f'{"Held!" if ok else "Too short"}'
                                f' (need {target_lo:.1f}s)')
 
                hold_btn = self._term_btn(
                    '[b]HOLD[/b]', height=dp(60),
                    accent_color=(0.9, 0.6, 0.1, 1))
                hold_btn.bind(on_press=_hold_press)
                hold_btn.bind(on_release=_hold_release)
                container.add_widget(hold_btn)
 
            elif ui_type == 'alternating_buttons':
                labels = ctx.get('button_labels', ['LEFT', 'RIGHT'])
                target = ctx.get('target_alternations_default', 8)
                alt_state = {'count': 0, 'expected': 0}
 
                alt_display = Label(
                    text=(f'Press: [b]{labels[0]}[/b]  '
                          f'(0/{target})'),
                    markup=True, font_name='RobotoMono',
                    font_size=dp(14),
                    size_hint_y=None, height=dp(28), halign='center',
                )
                alt_display.bind(
                    size=lambda i, v: setattr(i, 'text_size', v))
                container.add_widget(alt_display)
 
                btn_row = BoxLayout(
                    orientation='horizontal',
                    size_hint_y=None, height=dp(56), spacing=dp(8))
                for i, lbl in enumerate(labels):
                    def _alt_press(_, idx=i, lbls=labels,
                                   st=alt_state, disp=alt_display,
                                   tgt=target):
                        if idx == st['expected']:
                            st['count'] += 1
                            st['expected'] = ((st['expected'] + 1)
                                              % len(lbls))
                            next_lbl = lbls[st['expected']]
                            disp.text = (
                                f'Press: [b]{next_lbl}[/b]  '
                                f'({st["count"]}/{tgt})')
                            _demo_callback({
                                'event': 'alternation',
                                'count': st['count']})
                        else:
                            disp.text = (
                                f'[color=ff4444]Wrong! Press: '
                                f'[b]{lbls[st["expected"]]}[/b]'
                                f'[/color]')
                    b = self._term_btn(
                        f'[b]{lbl}[/b]', height=dp(52),
                        accent_color=(0.1, 0.9, 0.5, 1))
                    b.bind(on_release=_alt_press)
                    btn_row.add_widget(b)
                container.add_widget(btn_row)
 
            elif ui_type == 'memory_grid':
                grid_size = ctx.get('grid_size', 3)
                pattern = ctx.get('pattern', [0, 4, 8])
                mgw = MemoryGridWidget(
                    callback=_demo_callback,
                    pattern=pattern,
                    grid_size=grid_size,
                    size_hint_y=None, height=dp(120),
                )
                container.add_widget(mgw)
                # Track input for demo feedback
                demo_input = []
                orig_mem_cb = _demo_callback
                def _mem_cb(ev, di=demo_input, pat=pattern):
                    if (isinstance(ev, dict)
                            and ev.get('event') == 'memory_input'):
                        di.append(ev.get('index'))
                        pos = len(di) - 1
                        if pos < len(pat):
                            if di[pos] != pat[pos]:
                                _show_result(
                                    False,
                                    f'Wrong! Expected tile {pat[pos]}')
                                di.clear()
                            elif len(di) == len(pat):
                                _show_result(True, 'Pattern matched!')
                            else:
                                result_lbl.text = (
                                    f'[color=00cc44]'
                                    f'Correct ({len(di)}/{len(pat)})'
                                    f'[/color]')
                    orig_mem_cb(ev)
                mgw.callback = _mem_cb
 
            elif ui_type == 'aim_area':
                target_count = ctx.get('target_count', 3)
                aw = AimTargetWidget(
                    callback=_demo_callback,
                    target_count=target_count,
                    size_hint_y=None, height=dp(120),
                )
                container.add_widget(aw)
                self._active_demo_widget = aw
 
            elif ui_type == 'precision_gauge':
                decay = float(ctx.get('decay_rate', 0.5))
                tz = ctx.get('target_zone', [10, 15])
                gw = PrecisionGaugeWidget(
                    callback=_demo_callback,
                    decay=decay,
                    target_range=tuple(tz),
                    size_hint_y=None, height=dp(80),
                )
                container.add_widget(gw)
                self._active_demo_widget = gw
 
                # Pump button
                pump_btn = self._term_btn(
                    '[b]PUMP[/b]', height=dp(48),
                    accent_color=(0.9, 0.7, 0.1, 1))
                pump_btn.bind(on_release=lambda *_: gw.tap())
                container.add_widget(pump_btn)
 
            else:
                container.add_widget(Label(
                    text=(f'[color=888888][i]Demo not available for '
                          f'ui_type: {ui_type}[/i][/color]'),
                    markup=True, font_name='RobotoMono',
                    font_size=dp(12),
                    size_hint_y=None, height=dp(40),
                ))
 
        except Exception as e:
            self.logger.warning(
                f"TutorialScreen: demo widget failed for {ui_type}: {e}",
                exc_info=True)
            container.add_widget(Label(
                text=f'[color=ff4444]Demo error: {e}[/color]',
                markup=True, font_name='RobotoMono', font_size=dp(11),
                size_hint_y=None, height=dp(40),
            ))
 
        return container

    # ─────────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _stop_active_demo(self):
        if self._active_demo:
            try:
                widget, da, card, delta = self._active_demo
                if hasattr(widget, '_update_event') and widget._update_event:
                    widget._update_event.cancel()
                if hasattr(widget, '_activation_event') and widget._activation_event:
                    widget._activation_event.cancel()
                da.clear_widgets()
                da.height = 0
                card.height = max(0, card.height - delta)
            except Exception:
                pass
            self._active_demo = None

    def _scrollable_sections(self, sections: list) -> Widget:
        """
        Render a list of (title, body) tuples as a scrollable panel.

        Height management strategy:
          Labels need a real width before they can wrap and compute texture_size.
          We defer the measurement to the next frame with Clock.schedule_once,
          by which time Kivy will have completed the layout pass and every
          widget has a correct width.  The section card height is then set
          from the actual rendered label heights, not from guesses.
        """
        sv = ScrollView(do_scroll_x=False, bar_width=dp(4),
                        bar_color=(0.1, 0.8, 0.1, 1))
        grid = GridLayout(cols=1, spacing=dp(10), size_hint_y=None,
                          padding=[dp(2), dp(4)])
        grid.bind(minimum_height=grid.setter('height'))

        PAD_H   = dp(10)  # vertical padding per side inside each card
        PAD_W   = dp(14)  # horizontal padding per side inside each card
        SPACING = dp(6)   # spacing between title and body inside card
        T_H     = dp(26)  # fixed title row height

        for title, body in sections:
            section = BoxLayout(orientation='vertical', size_hint_y=None,
                                padding=[PAD_W, PAD_H], spacing=SPACING)
            self._panel_bg(section)

            title_lbl = Label(
                text=f'[b][color=ff6600]{title}[/color][/b]',
                markup=True, font_name='RobotoMonoBold', font_size=dp(15),
                halign='left', valign='middle', size_hint_y=None, height=T_H,
            )
            title_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            section.add_widget(title_lbl)

            body_lbl = Label(
                text=body, markup=True,
                font_name='RobotoMono', font_size=dp(14),
                halign='left', valign='top',
                size_hint_y=None, height=dp(60),
                color=(0.87, 0.87, 0.87, 1),
            )
            # DO NOT bind width→text_size here — width is 100 at this moment.
            # The deferred callback below does it correctly after layout.
            section.add_widget(body_lbl)

            # Start with a reasonable placeholder so the grid doesn't collapse
            section.height = T_H + SPACING + dp(60) + PAD_H * 2

            def _measure(dt, sec=section, bl=body_lbl,
                         pad_w=PAD_W, pad_h=PAD_H, sp=SPACING, th=T_H):
                """Runs after Kivy's first layout pass — widths are now real."""
                available_w = sec.width - pad_w * 2
                if available_w <= 0:
                    # Screen not laid out yet — try again next frame
                    Clock.schedule_once(lambda dt2, s=sec, b=bl,
                                        pw=pad_w, ph=pad_h, spacing=sp, t=th:
                                        _measure(dt2, sec=s, bl=b, pad_w=pw,
                                                 pad_h=ph, sp=spacing, th=t), 0)
                    return
                bl.text_size = (available_w, None)
                bl.texture_update()
                body_h = bl.texture_size[1]
                bl.height = body_h
                sec.height = th + sp + body_h + pad_h * 2

            Clock.schedule_once(_measure, 0)
            grid.add_widget(section)

        sv.add_widget(grid)
        return sv

    def _panel_bg(self, widget):
        """Add terminal-style panel background/border to a BoxLayout."""
        from kivy.graphics import Color as GColor, Line, RoundedRectangle as RR
        def _draw(w, *_):
            w.canvas.before.clear()
            with w.canvas.before:
                GColor(0.08, 0.08, 0.08, 1)
                RR(pos=w.pos, size=w.size, radius=[dp(4)])
                GColor(0.28, 0.28, 0.28, 1)
                Line(rounded_rectangle=(w.x, w.y, w.width, w.height, dp(4)), width=dp(1))
        widget.bind(pos=_draw, size=_draw)

    def _term_btn(self, text, height=dp(40), accent_color=None):
        """Create a TerminalButton-style Button in pure Python."""
        from kivy.graphics import Color as GColor, Line
        ac = accent_color or (0.1, 0.8, 0.1, 1)
        btn = Button(
            text=text, markup=True,
            font_name='RobotoMonoBold', font_size=dp(13),
            background_normal='', background_down='', background_color=(0,0,0,0),
            color=ac, size_hint_y=None, height=height,
            halign='center', valign='middle',
        )
        btn.bind(size=lambda i, v: setattr(i, 'text_size', v))
        def _border(b, *_):
            b.canvas.before.clear()
            with b.canvas.before:
                GColor(*(ac if b.state == 'down' else
                         (ac[0]*0.7, ac[1]*0.7, ac[2]*0.7, 1)))
                Line(rounded_rectangle=(b.x, b.y, b.width, b.height, dp(4)), width=dp(1.2))
        btn.bind(pos=_border, size=_border, state=_border)
        return btn


# --- Achievements Screen (from new ui.py, looks good) ---
class AchievementsScreen(BaseScreen):
    grid_layout = ObjectProperty(None)

    def __init__(self, achievements_system=None, **kwargs):
        # Always pass the AchievementsSystem from the app if not provided
        if achievements_system is None:
            app = App.get_running_app()
            achievements_system = getattr(app, 'achievements_system', None)
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.achievements_system = achievements_system
        # Layout removed; handled by KV

    def on_enter(self, *args):
        # Ensure we have the reference
        if not self.grid_layout:
            logging.warning("AchievementsScreen: grid_layout not connected via KV.")
            return

        self.grid_layout.clear_widgets()
        
        # Refresh system reference just in case
        if not self.achievements_system:
             app = App.get_running_app()
             self.achievements_system = getattr(app, 'achievements_system', None)

        if self.achievements_system:
            # load_achievements() returns a dict {id: {name, unlocked, ...}}
            raw = self.achievements_system.load_achievements()
            if isinstance(raw, dict):
                sorted_achievements = sorted(
                    raw.values(),
                    key=lambda ach: (not ach.get('unlocked', False), ach.get('name', ''))
                )
            elif isinstance(raw, list):
                sorted_achievements = sorted(
                    (ach for ach in raw if isinstance(ach, dict)),
                    key=lambda ach: (not ach.get('unlocked', False), ach.get('name', ''))
                )
            else:
                sorted_achievements = []
            
            for ach_data in sorted_achievements:
                is_unlocked = ach_data.get('unlocked', False)
                status_color_name = 'success' if is_unlocked else 'error'
                icon = ach_data.get('icon', '★')
                
                # Use color_text utility if available
                status_text = color_text('Unlocked' if is_unlocked else 'Locked', status_color_name, self.resource_manager)
                
                text = f"{icon} [b]{ach_data.get('name', 'Unknown')}[/b] ({status_text})\n   {ach_data.get('description', '')}"
                
                ach_label = Label(
                    text=text, 
                    font_name=DEFAULT_FONT_REGULAR_NAME, 
                    markup=True, 
                    size_hint_y=None, 
                    height=dp(70),
                    halign='left', 
                    valign='top', 
                    padding=(dp(5), dp(5))
                )
                
                # Bind text_size to width for wrapping
                ach_label.bind(width=lambda instance, value: setattr(instance, 'text_size', (value - dp(10), None)))
                self.grid_layout.add_widget(ach_label)
        else:
            self.grid_layout.add_widget(Label(
                text="Achievements system not available.", 
                font_name=DEFAULT_FONT_REGULAR_NAME
            ))

class SettingsScreen(BaseScreen):
    text_slider = ObjectProperty(None)
    sample_text_label = ObjectProperty(None)
    theme_btn = ObjectProperty(None)
    wipe_btn = ObjectProperty(None) 
    current_volume = NumericProperty(0.8) # The Source of Truth
    
    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.app = App.get_running_app()

    def on_pre_enter(self, *args):
        """Sync the slider to the actual audio volume when screen opens."""
        if self.app and self.app.audio_manager:
            self.current_volume = self.app.audio_manager.master_volume
        super().on_pre_enter(*args)

    def _on_text_scale_change(self, instance, value):
        self.app.text_scale = value
        if hasattr(self, 'sample_text_label') and self.sample_text_label:
            self.sample_text_label.text = f"Sample Text ({value:.1f}x)"
            self.sample_text_label.font_size = sp(16 * value)
        self.update_font_scale(value)

    def _toggle_theme(self, instance):
        new_theme = "Light" if self.app.theme_mode == "Dark" else "Dark"
        self.app.theme_mode = new_theme

    def _on_volume_change(self, instance, value):
        # Update the Source of Truth
        self.current_volume = value 
        self.app.audio_manager.set_master_volume(value)

    def _go_back(self, instance):
        self.app.save_app_settings()
        self.go_to_screen('title', 'right')

    def prompt_wipe_data(self):
        """Displays a themed confirmation popup for wiping data."""
        from kivy.factory import Factory
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.metrics import dp

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        lbl = Label(
            text="WARNING: This will erase all achievements and journal history.\nThis cannot be undone.",
            text_size=(dp(250), None),
            halign='center',
            valign='middle',
            color=(1, 0.6, 0, 1)  # Thematic Dark Orange / term_accent
        )
        content.add_widget(lbl)

        btn_layout = BoxLayout(orientation='horizontal', spacing=dp(10), size_hint_y=None, height=dp(50))
        
        # --- FIX 1: Spawn your styled MenuButtons via Factory ---
        cancel_btn = Factory.MenuButton(text="CANCEL")
        confirm_btn = Factory.MenuButton(text="ERASE DATA", background_color=(0.8, 0.1, 0.1, 1))
        
        btn_layout.add_widget(cancel_btn)
        btn_layout.add_widget(confirm_btn)
        content.add_widget(btn_layout)

        # --- FIX 2: Spawn your styled TerminalPopup via Factory ---
        self.wipe_popup = Factory.TerminalPopup(
            title="Wipe Meta Progression?",
            content=content,
            size_hint=(0.85, 0.4),
            auto_dismiss=False
        )
        
        cancel_btn.bind(on_release=self.wipe_popup.dismiss)
        confirm_btn.bind(on_release=self._execute_wipe_data)
        
        self.wipe_popup.open()

    def _execute_wipe_data(self, instance):
        """Executes the wipe, dismisses the popup, and alerts the user."""
        self.wipe_popup.dismiss()
        import os
        import json
        
        data_dir = self.app.user_data_dir
        
        # --- FIX: PROPERLY SOFT-RESET THE DICTIONARY ---
        profile_path = os.path.join(data_dir, 'user_profile.json')
        if os.path.exists(profile_path):
            try:
                with open(profile_path, 'r') as f:
                    profile_data = json.load(f)
                
                # 1. Lock all achievements individually
                achievements = profile_data.get('achievements', {})
                for ach_id, ach_data in achievements.items():
                    if isinstance(ach_data, dict):
                        ach_data['unlocked'] = False
                        ach_data['unlock_date'] = None
                
                # 2. Clear collections
                profile_data['unlocked_stories'] = []
                profile_data['evidence_collection'] = {}
                
                with open(profile_path, 'w') as f:
                    json.dump(profile_data, f, indent=4)
                    
                # 3. Force the live memory to sync with the clean file!
                if getattr(self.app, 'achievements_system', None):
                    self.app.achievements_system.load_achievements() 
                    
            except Exception as e:
                print(f"SettingsScreen Wipe Error: Could not reset user_profile.json: {e}")

        # Reset Journal History
        journal_path = os.path.join(data_dir, 'journal_history.json')
        if os.path.exists(journal_path):
            try:
                with open(journal_path, 'w') as f:
                    json.dump([], f)
            except Exception as e:
                print(f"SettingsScreen Wipe Error: Could not reset journal_history: {e}")

        # If in-game, clear the player's live journal
        if getattr(self.app, 'game_logic', None) and getattr(self.app.game_logic, 'player', None):
            self.app.game_logic.player['journal'] = []
            self.app.game_logic.player['journal_history'] = []

        # --- THEMED SUCCESS POPUP ---
        from kivy.factory import Factory
        from kivy.uix.label import Label
        from kivy.metrics import dp
        
        success_content = Label(
            text="Your achievements and journal have been locked and reset.",
            text_size=(dp(250), None),
            halign='center',
            color=(0.1, 0.8, 0.1, 1) # Thematic green
        )
        
        success_popup = Factory.TerminalPopup(
            title="DATA ERASED",
            content=success_content,
            size_hint=(0.8, 0.3)
        )
        success_popup.open()

class JournalScreen(BaseScreen):
    # ObjectProperties bound in KV
    evidence_list_layout = ObjectProperty(None)
    stories_list_layout = ObjectProperty(None)
    evidence_details_title = ObjectProperty(None)
    evidence_details_description = ObjectProperty(None)
    story_details_title = ObjectProperty(None)
    story_details_description = ObjectProperty(None)
    content_manager = ObjectProperty(None)
    btn_evidence_tab = ObjectProperty(None)
    btn_stories_tab = ObjectProperty(None)

    def __init__(self, achievements_system=None, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.achievements_system = achievements_system

    def switch_view(self, view_name):
        """Switch between evidence and stories views."""
        if self.content_manager and view_name in ['evidence', 'stories']:
            self.content_manager.current = view_name

    def on_enter(self, *args):
        if not self.achievements_system:
            app = App.get_running_app()
            if app: self.achievements_system = getattr(app, 'achievements_system', None)
        
        self.populate_evidence_list()
        self.populate_unlocked_stories_list()
        self.evidence_details_scroll = self.ids.get('evidence_details_scroll_id')

        if self.content_manager: self.content_manager.current = 'evidence'
        if self.btn_evidence_tab: self.btn_evidence_tab.state = 'down'
        if self.btn_stories_tab: self.btn_stories_tab.state = 'normal'

    def populate_evidence_list(self):
        """Populate the evidence list with collected evidence items."""
        self.evidence_list_layout.clear_widgets()
        
        if not self.achievements_system or not self.achievements_system.evidence_collection:
            lbl = Label(
                text="No evidence collected yet.\nExplore and search to find clues!", 
                size_hint_y=None, height=dp(60),
                halign='center', valign='middle'
            )
            lbl.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
            self.evidence_list_layout.add_widget(lbl)
            self.evidence_details_title.text = "No Evidence Yet"
            self.evidence_details_description.text = "Start exploring to find your first piece of evidence!"
            return
        
        # Sort evidence by found_date (most recent first)
        try:
            sorted_evidence = sorted(
                self.achievements_system.evidence_collection.items(), 
                key=lambda item: item[1].get('found_date', '1970-01-01 00:00'),
                reverse=True  # Most recent first
            )
        except Exception as e:
            logging.error(f"JournalScreen: Error sorting evidence: {e}")
            sorted_evidence = list(self.achievements_system.evidence_collection.items())

        for ev_id, ev_data in sorted_evidence:
            btn_text = ev_data.get('name', ev_id).title()
            
            # PATCH START: Use standard Button to avoid KV conflicts
            # We manually apply the style to ensure sizing works perfectly
            btn = Button(
                text=btn_text,
                size_hint_y=None,
                height=dp(45), # Initial height
                background_normal='',
                background_color=(0, 0, 0, 0), # Transparent
                color=(0.1, 0.8, 0.1, 1),      # Terminal Green
                font_name=DEFAULT_FONT_BOLD_NAME,
                font_size=dp(14),
                halign='left',
                valign='middle',
                padding_x=dp(10)
            )

            # --- The Sizing Fix ---
            # 1. Define a concise update function
            def update_btn_geometry(instance, _):
                # Constrain text width to button width minus padding
                instance.text_size = (instance.width - dp(20), None)
                # Force button height to match the text height + padding
                if instance.texture_size[1] > 0:
                    instance.height = instance.texture_size[1] + dp(20)

            # 2. Bind to critical properties
            btn.bind(width=update_btn_geometry)
            btn.bind(texture_size=update_btn_geometry)

            # 3. Add custom border drawing (manual since we aren't using TerminalButton)
            with btn.canvas.before:
                Color(0.1, 0.8, 0.1, 1) # Border Color
                btn.border_line = Factory.Line(width=dp(1), rounded_rectangle=(btn.x, btn.y, btn.width, btn.height, dp(4)))
            
            # Update border position when button moves/resizes
            def update_border(instance, _):
                instance.border_line.rounded_rectangle = (instance.x, instance.y, instance.width, instance.height, dp(4))
            btn.bind(pos=update_border, size=update_border)

            # 4. Bind action
            btn.bind(on_release=lambda x, eid=ev_id: self.show_evidence_details(eid))
            
            self.evidence_list_layout.add_widget(btn)
            # Force one update immediately to set initial size
            Clock.schedule_once(lambda dt, b=btn: update_btn_geometry(b, None), 0)
            # PATCH END
        
        # Reset details panel
        self.evidence_details_title.text = "Select to View"
        self.evidence_details_description.text = "Click on an evidence item from the list to see its details here."

    def populate_unlocked_stories_list(self):
        """Populate the stories list with unlocked complete story sets."""
        self.stories_list_layout.clear_widgets()
        
        if not self.achievements_system or not self.achievements_system.unlocked_stories:
            # [Existing logic for empty state remains same...]
            evidence_count = len(self.achievements_system.evidence_collection) if self.achievements_system else 0
            if evidence_count == 0:
                message = "No stories unlocked yet.\nStart collecting evidence to unlock complete backstories!"
            else:
                message = f"No complete stories yet.\nYou have {evidence_count} evidence pieces.\nKeep collecting to unlock full backstories!"
            
            lbl = Label(
                text=message,
                size_hint_y=None, height=dp(80),
                halign='center', valign='middle'
            )
            # Simple bind for the label is fine
            lbl.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
            self.stories_list_layout.add_widget(lbl)
            
            self.story_details_title.text = "No Stories Unlocked"
            self.story_details_description.text = "Collect all evidence from a story set to unlock its complete backstory."
            return

        # Sort stories alphabetically
        sorted_stories = sorted(list(self.achievements_system.unlocked_stories))

        for story_name in sorted_stories:
            story_icon = "🎬" 
            if "Book:" in story_name: story_icon = "📖"
            elif "Comic:" in story_name: story_icon = "📚"
            elif "Archives" in story_name: story_icon = "🗃️"
            
            btn_text = f"{story_icon} {story_name}"

            # --- ROBUST LAYOUT LOGIC ---
            btn = Factory.TerminalButton(
                text=btn_text,
                size_hint_y=None,
                height=dp(40),
                halign='left',
                valign='middle',
                padding=(dp(10), dp(8)),
                text_size=(None, None),
                shorten=True,
                shorten_from='right',
            )
            
            def update_story_btn_layout(instance, width):
                if width < dp(80):
                    return
                instance.text_size = (width - dp(20), None)
                instance.texture_update()
                instance.height = max(dp(40), instance.texture_size[1] + dp(16))

            btn.bind(width=update_story_btn_layout)
            # Force initial layout
            Clock.schedule_once(lambda dt, b=btn: update_story_btn_layout(b, b.width), 0.1)
            
            def update_story_btn_layout(instance, width):
                # Width Guard: Skip only the Kivy default 100px init
                if width < dp(80): 
                    return
                
                # Apply text wrapping and auto-height
                instance.text_size = (width - dp(20), None)
                instance.texture_update()
                instance.height = max(dp(36), instance.texture_size[1] + dp(16))

            btn.bind(width=update_story_btn_layout)
            # ---------------------------

            btn.bind(on_release=lambda x, s_name=story_name: self.show_story_details(s_name))
            self.stories_list_layout.add_widget(btn)

        # Reset details panel
        self.story_details_title.text = "Select a Story to Read"
        self.story_details_description.text = "Click on an unlocked story from the list to read its complete backstory."

    def show_evidence_details(self, evidence_id):
        """
        Display detailed information about a specific piece of evidence.
        Canonical: Always shows character association, description, and story set(s).
        """
        if not self.achievements_system or evidence_id not in self.achievements_system.evidence_collection:
            self.evidence_details_title.text = "Evidence Not Found"
            self.evidence_details_description.text = "This evidence could not be found in your collection."
            return

        evidence_data = self.achievements_system.evidence_collection[evidence_id]

        # Title: Evidence name (colored)
        title_text = evidence_data.get('name', evidence_id).title()
        self.evidence_details_title.text = color_text(title_text, 'special', self.resource_manager)

        # Description: Prefer 'description', fallback to 'examine_details'
        desc = evidence_data.get('description')
        if not desc or desc.strip() == "":
            # Try to get from items.json
            app = App.get_running_app()
            items_data = app.resource_manager.get_data('items', {}) if app and app.resource_manager else {}
            item_master = items_data.get(evidence_id.lower()) or items_data.get(evidence_id)
            desc = (item_master.get('description') if item_master else "") or evidence_data.get('examine_details', "No description available.")

        found_date_str = evidence_data.get('found_date', 'Unknown time')

        # --- PATCH START: Robust Story Set Lookup using centralized normalize_text ---
        
        app = App.get_running_app()
        evidence_by_source = app.resource_manager.get_data('evidence_by_source', {}) if app and app.resource_manager else {}
        story_sets = []
        
        # Prepare our comparison keys (ID and Name) using centralized normalization
        target_keys = {normalize_text(evidence_id), normalize_text(evidence_data.get('name', ''))}

        for story_name, story_data in evidence_by_source.items():
            raw_list = story_data.get('evidence_list', [])
            # Check if any item in the story list matches our ID or Name
            if any(normalize_text(story_item) in target_keys for story_item in raw_list):
                story_sets.append(story_name)
        
        # --- PATCH END ---

        if story_sets:
            story_text = "\n\n[b]Story Set(s):[/b] " + ", ".join(color_text(s, 'special', self.resource_manager) for s in story_sets)
        else:
            story_text = "\n\n[i][color=aaaaaa]This evidence is not part of any known story set.[/color][/i]"

        # --- Canonical: Always show character association if present ---
        # Try evidence_data, then items.json
        character_assoc = evidence_data.get('character_connection')
        if not character_assoc:
            app = App.get_running_app()
            items_data = app.resource_manager.get_data('items', {}) if app and app.resource_manager else {}
            item_master = items_data.get(evidence_id.lower()) or items_data.get(evidence_id)
            character_assoc = item_master.get('character_connection') if item_master else None

        if character_assoc:
            # Use canonical color name 'special' for character association
            char_info_text = f"\n[b]Victim/Character:[/b] {color_text(character_assoc, 'special', self.resource_manager)}"
        else:
            char_info_text = ""

        # Use canonical color name 'light_grey' via color_text for found date
        self.evidence_details_description.text = (
            f"[b]Description:[/b]\n{desc}\n\n"
            f"[size={int(dp(13))}sp]{color_text(f'Found: {found_date_str}', 'light_grey', self.resource_manager)}[/size]"
            f"{story_text}"
            f"{char_info_text}"
        )
        if self.evidence_details_scroll:
            self.evidence_details_scroll.scroll_y = 1  # Scroll to top

    def show_story_details(self, story_key):
        """Display the full backstory for a selected story."""
        if not self.achievements_system: return

        stories_data = self.resource_manager.get_data('evidence_by_source', {})
        story = stories_data.get(story_key, {})
        
        # Use key as title if not defined
        title = story.get('title', story_key.replace('_', ' ').title())
        backstory = story.get('backstory', "No details available.")
        
        # Update UI
        self.story_details_title.text = title
        
        # --- FIX: Enable Markup ---
        self.story_details_description.markup = True  # <--- Force this ON
        self.story_details_description.text = f"[b]Backstory:[/b]\n\n{backstory}"
        # --------------------------

        # Scroll to top
        if self.story_details_scroll:
            self.story_details_scroll.scroll_y = 1

    def _get_evidence_story_info(self, evidence_id):
        """Get information about which story set this evidence belongs to."""
        app = App.get_running_app()
        if not app or not app.resource_manager:
            return None
            
        evidence_by_source = app.resource_manager.get_data('evidence_by_source', {})
        if not evidence_by_source:
            return None
        
        for story_name, story_data in evidence_by_source.items():
            if evidence_id in story_data.get('evidence_list', []):
                # Count how many pieces from this story we have
                story_evidence_ids = set(story_data['evidence_list'])
                collected_ids = set(self.achievements_system.evidence_collection.keys()) if self.achievements_system else set()
                collected_from_story = story_evidence_ids.intersection(collected_ids)
                
                return {
                    'story_name': story_name,
                    'collected_count': len(collected_from_story),
                    'total_count': len(story_evidence_ids),
                    'is_complete': story_name in (self.achievements_system.unlocked_stories if self.achievements_system else set())
                }
        
        return None
    
class LoadGameScreen(BaseScreen):
    slots_layout = ObjectProperty(None)
    status_label = ObjectProperty(None)
    btn_back = ObjectProperty(None)

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        # NO MANUAL LAYOUT CREATION HERE. KV HANDLES IT.

    def on_enter(self, *args): 
        self.populate_load_slots()
        if self.status_label:
            self.status_label.text = "Select a slot to load."

        game_screen = self.manager.get_screen('game')
        if self.btn_back:
            if game_screen and getattr(game_screen, 'game_started', False):
                self.btn_back.text = "< Back to Game"
                self.btn_back.unbind(on_release=self._go_to_title_screen_action)
                self.btn_back.bind(on_release=self._go_to_game_screen_action)
            else:
                self.btn_back.text = "< Back to Title"
                self.btn_back.unbind(on_release=self._go_to_game_screen_action)
                self.btn_back.bind(on_release=self._go_to_title_screen_action)

    def _go_to_game_screen_action(self, instance):
        self.go_to_screen('game', 'right')

    def _go_to_title_screen_action(self, instance):
        self.go_to_screen('title', 'right')

    def populate_load_slots(self):
        if not self.slots_layout: return
        self.slots_layout.clear_widgets()
        
        # Use MAX_SAVE_SLOTS from GameLogic if available, else default to 5
        max_slots = getattr(GameLogic, "MAX_SAVE_SLOTS", 5)
        slots_to_show = ["quicksave"] + [f"slot_{i}" for i in range(1, max_slots + 1)]
        
        for slot_id in slots_to_show:
            preview_info = get_save_slot_info(slot_id)
            display_text = f"{slot_id.replace('_', ' ').capitalize()}"
            
            if preview_info:
                if preview_info.get("corrupted"):
                    # Use styled label from Factory or standard Label with specific font
                    btn = Label(
                        text=f"{display_text} [color=ff0000](Corrupted)[/color]", 
                        markup=True,
                        font_name=DEFAULT_FONT_REGULAR_NAME,
                        size_hint_y=None, height=dp(60)
                    )
                else:
                    loc = preview_info.get('location', '?')
                    ts = preview_info.get('timestamp', 'No date')
                    char_class = preview_info.get('character_class', '')
                    turns = preview_info.get('turns_left', '')
                    
                    # Detailed text
                    full_text = f"[b]{display_text}[/b]\n[size=13sp]{loc} | {char_class}\nTurn {turns} | {ts}[/size]"

                    # Use Factory to create a KV-styled button
                    btn = Factory.SlotButton(text=full_text)
                    btn.bind(on_release=lambda x, s_id=slot_id: self.load_game_action(s_id))
                
                self.slots_layout.add_widget(btn)
            else:
                 # Empty slot
                 lbl = Label(
                     text=f"{display_text} (Empty)", 
                     font_name=DEFAULT_FONT_REGULAR_NAME,
                     color=(0.5, 0.5, 0.5, 1),
                     size_hint_y=None, height=dp(60)
                 )
                 self.slots_layout.add_widget(lbl)

    def load_game_action(self, slot_identifier):
        app = App.get_running_app()
        
        # PATCH START: Ensure the Vessel Exists
        # If loading from Title Screen, game_logic is None. We must forge it.
        if not getattr(app, 'game_logic', None):
            logging.info("LoadGameScreen: Forging new session for save data...")
            # We pass a dummy class; the save file will overwrite this immediately.
            app.create_new_game_session(character_class="Loading...")
        # PATCH END

        game_screen = self.manager.get_screen('game')
        if game_screen:
            game_screen.pending_load = True
            game_screen.load_slot_identifier = slot_identifier
        ev = Clock.schedule_once(lambda dt: self.go_to_screen('game', 'fade'), 0.2)
        self._scheduled_events.append(ev) # Safe now if added to BaseScreen
        
class SaveGameScreen(BaseScreen):
    slots_layout = ObjectProperty(None)
    status_label = ObjectProperty(None)

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        # NO MANUAL LAYOUT CREATION HERE. KV HANDLES IT.

    def on_enter(self, *args):
        self.populate_save_slots()
        if self.status_label:
            self.status_label.text = "Select a slot to save."

    def populate_save_slots(self):
        if not self.slots_layout:
            return

        self.slots_layout.clear_widgets()
        max_slots = getattr(GameLogic, "MAX_SAVE_SLOTS", 5)
        slots_to_show = ["quicksave"] + [f"slot_{i}" for i in range(1, max_slots + 1)]

        for slot_id in slots_to_show:
            try:
                slot_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(60), spacing=dp(10))
                preview_info = get_save_slot_info(slot_id)
                display_text = f"{slot_id.replace('_', ' ').capitalize()}"

                if preview_info:
                    loc = preview_info.get('location', '?')
                    ts = preview_info.get('timestamp', 'No date')
                    char_class = preview_info.get('character_class', '')
                    turns = preview_info.get('turns_left', '')
                    
                    details = f"[b]{display_text}[/b]\n[size=13sp]{loc} - {char_class} (Turn {turns})\n{ts}[/size]"
                    
                    if preview_info.get("corrupted"):
                        details += color_text(" (Corrupted)", 'error', self.resource_manager)
                else:
                    details = f"[b]{display_text}[/b]\n[size=13sp]{color_text('(Empty Slot)', 'light_grey', self.resource_manager)}[/size]"

                # Save Button (Main click area)
                # Uses SlotButton from KV for styling
                save_btn = Factory.SlotButton(text=details, size_hint_x=0.8)
                save_btn.bind(on_release=lambda x, s_id=slot_id: self.confirm_save(s_id))
                slot_box.add_widget(save_btn)

                # Delete Button (Small side button)
                delete_btn = Factory.TerminalButton(text="DEL", size_hint_x=0.2)
                if not preview_info:
                    delete_btn.disabled = True
                    delete_btn.opacity = 0.3
                
                delete_btn.bind(on_release=lambda x, s_id=slot_id: self.confirm_delete_popup(s_id))
                slot_box.add_widget(delete_btn)

                self.slots_layout.add_widget(slot_box)
            except Exception as e:
                logging.error(f"Error populating save slot '{slot_id}': {e}", exc_info=True)

    def confirm_save(self, slot_identifier):
        gs = self.manager.get_screen('game')
        if gs and gs.game_logic:
            save_response = gs.game_logic._command_save(slot_identifier)
            
            # --- FIX START ---
            # The engine returns a list of 'messages', not a single 'message' string.
            msgs = save_response.get("messages", [])
            if msgs:
                # Remove color tags for the status label if you want it plain, 
                # or keep them if the label supports markup (it does).
                self.status_label.text = msgs[0]
            else:
                self.status_label.text = "Save successful."
            # --- FIX END ---

            if save_response.get("success"):
                self.populate_save_slots()
        else:
            self.status_label.text = color_text("Cannot save: No active game logic.", 'error')
            
    def confirm_delete_popup(self, slot_identifier):
        # Simple popup content
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        content.add_widget(Label(
            text=f"Delete save '{slot_identifier}'?\nThis cannot be undone.",
            font_name=DEFAULT_FONT_REGULAR_NAME, 
            halign='center'
        ))
        
        btns = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        yes = Factory.TerminalButton(text="DELETE")
        yes.bind(on_release=lambda x: self.do_delete_save(slot_identifier))
        no = Factory.TerminalButton(text="CANCEL")
        
        btns.add_widget(yes)
        btns.add_widget(no)
        content.add_widget(btns)

        self.popup = Popup(title="Confirm Deletion", content=content,
                           size_hint=(0.8, 0.35), auto_dismiss=False,
                           separator_color=get_color_from_hex('ff0000'))
        no.bind(on_release=self.popup.dismiss)
        self.popup.open()

    def do_delete_save(self, slot_identifier):
        if hasattr(self, 'popup') and self.popup:
            self.popup.dismiss()
            self.popup = None

        gs = self.manager.get_screen('game')
        app = App.get_running_app()
        logic = getattr(gs, 'game_logic', None) or getattr(app, 'game_logic', None)

        if logic:
            delete_response = logic.delete_save_game(slot_identifier)
            if self.status_label:
                self.status_label.text = delete_response.get("message", "Delete status unknown.")
            if delete_response.get("success"):
                self.populate_save_slots()
        else:
            # Fallback manual delete
            try:
                from .utils import get_save_filepath
                import random
                from .utils import normalize_text
                path = get_save_filepath(slot_identifier)
                if os.path.exists(path):
                    os.remove(path)
                    if self.status_label:
                        self.status_label.text = f"Deleted {slot_identifier}."
                    self.populate_save_slots()
                else:
                    if self.status_label:
                        self.status_label.text = "File not found."
            except Exception as e:
                logging.error(f"Manual delete failed: {e}")
                if self.status_label:
                    self.status_label.text = "Error deleting file."
                    
class EvadedHazardEntry(RecycleDataViewBehavior, Label):
    """ Displays a single evaded hazard. """
    index = None
    selected = BooleanProperty(False)
    selectable = BooleanProperty(True)

    def refresh_view_attrs(self, rv, index, data):
        ''' Catch and handle the view changes '''
        self.index = index
        self.text = data.get('text', '')
        self.markup = True
        self.font_name = DEFAULT_FONT_REGULAR_NAME
        self.font_size = dp(14)
        self.halign = 'left'
        self.valign = 'top'
        self.text_size = (rv.width * 0.9, None) # Ensure wrapping
        self.size_hint_y = None
        self.height = self.texture_size[1] + dp(10) # Add padding
        return super(EvadedHazardEntry, self).refresh_view_attrs(rv, index, data)

    def on_touch_down(self, touch):
        ''' Add selection on touch down '''
        if super(EvadedHazardEntry, self).on_touch_down(touch):
            return True
        if self.collide_point(*touch.pos) and self.selectable:
            return self.parent.select_with_touch(self.index, touch)

    def apply_selection(self, rv, index, is_selected):
        ''' Respond to a selection change. '''
        self.selected = is_selected
        # You can change background color or something on selection if desired

class InterLevelScreen(BaseScreen):
    next_level_id = StringProperty("")
    previous_level_id = StringProperty("")
    
    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        super().__init__(**kwargs)
        self.next_level_start_room = None
        self.logger = logging.getLogger(__name__ + ".InterLevelScreen")

    def on_enter(self, *args):
        app = App.get_running_app()
        
        # 1. CAPTURE DATA: Ensure we handle the attribute name correctly
        # The logs suggest getattr is returning an empty string. 
        # Make sure the App class actually has these attributes set during level_complete.
        self.next_level_id = str(getattr(app, 'interlevel_next_level_id', ""))
        self.previous_level_id = str(getattr(app, 'interlevel_previous_level_id', ""))
        
        # 2. ROBUST CHECK: Normalizing the ID to catch 'level_0', '0', or empty
        prev_id_norm = self.previous_level_id.lower().strip()
        
        self.logger.info(f"InterLevelScreen: Logic Branching. Prev: '{prev_id_norm}'")

        game_logic = app.game_logic
        rm = self.resource_manager
        
        # --- THE CORRECTED NARRATIVE GATE ---
        # Check for both 'level_0' and '0' and ensure it's not empty
        if prev_id_norm in ("level_0", "0"):
            self.title_label.text = "THE SURVIVORS"
            self.narrative_label.text = self._build_post_premonition_intro(game_logic, rm)
        else:
            # Fallback to midgame intro
            self.title_label.text = "THE DESIGN CONTINUES"
            self.narrative_label.text = self._build_midgame_intro(
                game_logic, rm, self.next_level_id, self.previous_level_id
            )
    def _build_post_premonition_intro(self, game_logic, rm) -> str:
        """
        Build the Level 1 intro text using actual game state from
        the premonition level. Branches entirely if there are no survivors.
        """
        from .utils import color_text
        
        details = game_logic.player.get('intro_disaster', {})
        city = game_logic.player.get('current_city', 'McKinley')
        hospital = game_logic.player.get('current_hospital', 'the hospital')
        char_class = game_logic.player.get('character_class', 'Survivor')
        visionary_name = game_logic.player.get('premonition_visionary', 'a stranger')
        
        disaster_name = details.get('name', details.get('event_description', 'the disaster'))
        killed_count = details.get('killed_count', 'dozens')
        
        survivors = game_logic.player.get('premonition_survivors', [])
        
        # --- BRANCH 1: THE SOLE SURVIVOR (False Security) ---
        if not survivors:
            intro = (
                f"It's been about a week since {color_text(disaster_name, 'special', rm)} killed {color_text(killed_count, 'warning', rm)}. "
                f"The news called it a freak tragedy. Not wrong. You called it a miracle that you walked away with just a minor concussion.\n\n"
                f"You are still a little weirded out about {color_text(visionary_name, 'special', rm)} and their crashing out right before everyone died, but that problem died with them so you try not to think about it too much. "
                f"{color_text('You\'re alive and well!', 'success', rm)} Life is finally starting to feel normal again. You just got that promotion at work, you finally met a quality guy.. {color_text('Dying didn\'t really fit into your 5-year plan, you know?', 'furniture', rm)}\n\nYou've just arrived at {color_text(hospital, 'location', rm)} "
                f"for a routine follow-up scan in the {color_text('Radiology department', 'special', rm)}, just to be safe, and then you can go home.\n\n"
                f"Whatever you escaped from in {color_text(city, 'location', rm)}... {color_text('You\'re glad it\'s in the past!', 'success', rm)}\n\n"
                f"Commands: type {color_text('help', 'special', rm)} to see everything you can do.\n"
                f"Find keys, examine clues, and {color_text('have an awesome life!', 'warning', rm)}\n\n"
            )
            return intro

        # --- Process Off-Screen Casualties First ---
        offscreen_casualties = game_logic.player.get('offscreen_casualties', [])
        
        # Calculate everyone who made it out of Level 0 (Active Survivors + Culled Survivors)
        initial_survivors = list(survivors)
        for c in offscreen_casualties:
            if c['name'] not in initial_survivors:
                initial_survivors.append(c['name'])

        # --- BRANCH 2: THE PARANOID SURVIVORS (Hunting for Answers) ---
        if len(initial_survivors) == 0:
            survivor_text = "You didn't get anyone else out with you."
        else:
            # Format the list with correct grammar (Oxford comma style)
            if len(initial_survivors) == 1:
                formatted_names = initial_survivors[0]
            elif len(initial_survivors) == 2:
                formatted_names = f"{initial_survivors[0]} and {initial_survivors[1]}"
            else:
                formatted_names = ", ".join(initial_survivors[:-1]) + f", and {initial_survivors[-1]}"

            survivor_text = (
                f"{len(initial_survivors)} "
                f"{'person' if len(initial_survivors) == 1 else 'people'} either left with you or because {visionary_name} freaked them out enough: "
                f"{color_text(formatted_names, 'npc', rm)}."
            )

        # --- Inject Witnessed Deaths (Failed Interactions) ---
        witnessed_deaths = game_logic.player.get('witnessed_deaths', [])
        witnessed_text = ""
        
        if witnessed_deaths:
            # Wrap every death description in the bright red 'warning' color!
            wd = [color_text(death, 'warning', rm) for death in witnessed_deaths]
            
            if len(wd) == 1:
                witnessed_text = f"\n\nBut you couldn't save everyone you saw or tried to warn. You watched {wd[0]}."
            elif len(wd) == 2:
                witnessed_text = f"\n\nBut you couldn't save everyone you saw or tried to warn. You watched {wd[0]}, and saw {wd[1]}."
            else:
                witnessed_text = f"\n\nBut you couldn't save everyone you saw or tried to warn. You watched {wd[0]}, saw {wd[1]}, and could do nothing about {wd[2]}."

        # Add it directly to the end of the survivor text
        survivor_text += witnessed_text

        # --- Resolve the Post-Disaster Deaths Narrative ---
        if offscreen_casualties:
            if len(offscreen_casualties) == 1:
                c = offscreen_casualties[0]
                post_deaths = (
                    f"But since the disaster, {color_text('things have gotten worse.', 'warning', rm)} "
                    f"{color_text(c['name'], 'npc', rm)} died yesterday after {color_text(c['fate'], 'warning', rm)}. "
                    f"The police called it a freak accident. You're not so sure."
                )
            else:
                c1 = offscreen_casualties[0]
                c2 = offscreen_casualties[1]
                post_deaths = (
                    f"But since the disaster, {color_text('things have gotten worse.', 'warning', rm)} "
                    f"{color_text(c1['name'], 'npc', rm)} died yesterday after {color_text(c1['fate'], 'warning', rm)}. "
                    f"Then, just hours ago, you got a call from {color_text(visionary_name, 'npc', rm)} about how {color_text(c2['name'], 'npc', rm)} died after {color_text(c2['fate'], 'warning', rm)}. "
                    f"The police are calling them freak accidents. {color_text('At this point you\'re not so sure.', 'warning', rm)}"
                )
        else:
            if len(survivors) == 0:
                post_deaths = (
                    "You are the only one left breathing. "
                    "But you can't shake the feeling that your own time is still coming."
                )
            elif len(survivors) == 1:
                post_deaths = (
                    f"{color_text(survivors[0], 'npc', rm)}, the only person who survived with you, is still breathing. "
                    f"But you can't shake the feeling that {color_text('something bad is going to happen.', 'warning', rm)}"
                )
            else:
                post_deaths = (
                   f"{color_text('Everyone who survived with you', 'npc', rm)} is still breathing."
                    f"But you can't shake the feeling that {color_text('something bad is going to happen.', 'warning', rm)}"
                )
        
        intro = (
            f"It's been three days since "
            f"{color_text(disaster_name, 'special', rm)} "
            f"in {color_text(city, 'location', rm)}.\n\n"
            f"{color_text(str(killed_count), 'warning', rm)} "
            f"people didn't make it.\n\n"
            f"{survivor_text}\n\n"
            f"The news calls it a tragedy. {color_text('An act of God.', 'npc', rm)} "
            f"But you were there. You know "
            f"{color_text(visionary_name, 'npc', rm)} saw it coming. ..and you don't really know how to reconcile that with everything you believe in.\n\n"
            f"{post_deaths}\n\n"
            f"You came to "
            f"{color_text(hospital, 'location', rm)} for some routine scans after a possible concussion at the disaster site. "
            f"The weird thing is you feel like it's where you need to be, "
            f"but {color_text('you can\'t say if that\'s good or bad', 'location', rm)}.\n\n"
            f"The {color_text(char_class, 'special', rm)} in you is saying you're running "
            f"out of time to figure out which it is.\n\n"
            f"Until you figure that out, however, make your way to {color_text('Radiology', 'special', rm)} "
            f"for those scans in the MRI suite. \n"
            f"{color_text('You just be careful, now.', 'warning', rm)}"
        )
        return intro

    def _build_midgame_intro(self, game_logic, rm, next_level_id, prev_level_id=None) -> str:
        """
        Builds the escalating narrative for levels 2 through the finale.
        Patched: Prioritizes disaster 'name' over 'event_description' and 
        includes unique 'previously on' context.
        """
        from .utils import color_text
        p = game_logic.player

        # 1. Resolve level names and config
        level_reqs    = rm.get_data('level_requirements', {})
        next_cfg      = level_reqs.get(str(next_level_id), {})
        prev_cfg      = level_reqs.get(str(prev_level_id), {}) if prev_level_id else {}

        level_name      = next_cfg.get('name', 'your next destination')
        prev_level_name = prev_cfg.get('name', 'there')
        base_intro      = next_cfg.get('intro_text', '')

        # 2. Extract active Game State
        roster      = p.get('npc_status', {})
        alive_npcs  = [n.title() for n, s in roster.items()
                       if s in ('alive', 'injured') and n != 'player']
        offscreen   = p.get('offscreen_casualties', [])
        visited     = p.get('visited_levels', set())
        npc_wp      = p.get('npc_workplaces', {})
        city        = p.get('current_city', 'the city')
        
        # --- THE DISASTER NAME PATCH ---
        # Prioritize 'name', fall back to 'event_description'
        disaster = p.get('intro_disaster', {})
        raw_disaster = disaster.get('name') or disaster.get('event_description', 'the disaster')
        disaster_str = raw_disaster.replace('{city_name}', city)
        # -------------------------------

        is_hub       = 'hub' in str(next_level_id).lower()
        is_finale    = 'finale' in str(next_level_id).lower()
        is_funnel    = 'funnel' in str(next_level_id).lower()
        is_bludworth = 'house' in str(next_level_id).lower() or 'level_house' == str(next_level_id)

        narrative_parts = []

        # ── SECTION A: What just happened (The "Previously on..." opener) ──
        if prev_level_id in ('level_1', 'level_hospital'):
            narrative_parts.append(
                f"You push through the {color_text('hospital doors', 'location', rm)}, "
                f"the fluorescent hum fading behind you. "
                f"Whatever answers the {color_text('hospital', 'location', rm)} held, "
                f"you've squeezed them out. Time to move."
            )
        elif prev_level_id == 'level_house' or (is_bludworth and prev_level_id):
            narrative_parts.append(
                f"Bludworth's house is in ruins behind you. "
                f"You got what you came for. Now the question is whether it's enough."
            )
        elif prev_level_id == 'level_police_station':
            narrative_parts.append(
                f"The precinct's lights shrink in your mirrors. "
                f"You're not cuffed. That's something. "
                f"Now you have to decide what comes next."
            )
        elif prev_level_id and prev_level_name != 'there':
            prev_npc_wp_name = next(
                (wp.get('workplace_name', prev_level_name) for wp in npc_wp.values()
                 if wp.get('level_id') == prev_level_id),
                prev_level_name
            )
            target_npc = next(
                (n.title() for n, wp in npc_wp.items()
                 if wp.get('level_id') == prev_level_id
                 and roster.get(n, 'alive') in ('alive', 'injured')),
                None
            )
            if target_npc:
                narrative_parts.append(
                    f"You leave {color_text(prev_npc_wp_name, 'location', rm)} behind, "
                    f"{color_text(target_npc, 'npc', rm)} still on your mind. "
                    f"You warned them. Whether they'll listen is out of your hands now."
                )
            else:
                narrative_parts.append(
                    f"You leave {color_text(prev_npc_wp_name, 'location', rm)} in the rearview mirror. "
                    f"Another stop on Death's itinerary, checked off."
                )
        else:
            narrative_parts.append(
                f"You leave {color_text(prev_level_name or 'the last danger', 'location', rm)} behind."
            )

        # ── SECTION B: Destination context ──
        if is_hub:
            visits = len([l for l in visited if l not in ('level_0', 'level_1', 'level_hub')])
            if visits == 0:
                narrative_parts.append(
                    f"You're back in the car. {color_text(city, 'location', rm)} spreads out around you. "
                    f"The memory of {color_text(disaster_str, 'special', rm)} is still fresh. "
                    f"The list is real. The question now is who you go to first."
                )
            else:
                narrative_parts.append(
                    f"Back in the car. {color_text(str(visits), 'special', rm)} "
                    f"stop{'s' if visits != 1 else ''} down. "
                    f"Death keeps moving. So do you."
                )
        elif is_bludworth:
            narrative_parts.append(
                f"""Bludworth's house. You've heard the name before, but it's been a few years since he passed.\n"""
                f"""If anyone understood the rules of {color_text("Death's design", 'warning', rm)} or had a hint of who else might be able to help, it's him.\nYou can only hope there's answers at his old place.\n\n"""
                f"""You arrive and see signs of construction equipment. You'd better be both quiet and quick about this. Try the front door."""
            )
        elif is_funnel:
            narrative_parts.append(
                f"Everything is converging. The survivors, the list, the design — "
                f"all of it is collapsing toward a single point. "
                f"You can feel it in the way the city feels quieter than it should."
            )
        elif is_finale:
            narrative_parts.append(
                f"[b][color=ff0000]This is it. The end of the line. "
                f"There's nowhere left to run.[/color][/b]"
            )
        elif base_intro:
            narrative_parts.append(base_intro)

        # ── SECTION C: Recent offscreen death ──
        shown_offscreen_idx = p.get('_interlevel_shown_offscreen_idx', 0)
        if offscreen and shown_offscreen_idx < len(offscreen):
            recent = offscreen[shown_offscreen_idx]
            narrative_parts.append(
                f"But the relief is shattered by a grim reality check. "
                f"You get word that {color_text(recent['name'], 'npc', rm)} is dead. "
                f"They were killed after {color_text(recent['fate'], 'warning', rm)}.\n\n"
                f"The news is calling it a freak accident. "
                f"You know better. Death is just tying up loose ends."
            )
            p['_interlevel_shown_offscreen_idx'] = shown_offscreen_idx + 1

        # ── SECTION D: Survivor tally ──
        n = len(alive_npcs)
        if n == 0:
            narrative_parts.append(
                f"There is {color_text('no one left', 'error', rm)} to warn. "
                f"You are the sole survivor. The design has isolated you completely."
            )
        elif n == 1:
            narrative_parts.append(
                f"Only you and {color_text(alive_npcs[0], 'npc', rm)} remain. "
                f"The circle is closing fast."
            )
        else:
            names = ", ".join(alive_npcs[:-1]) + f", and {alive_npcs[-1]}"
            narrative_parts.append(
                f"There are {color_text(str(n), 'special', rm)} other survivors still out there: "
                f"{color_text(names, 'npc', rm)}. But for how much longer?"
            )

        # ── SECTION E: Closer ──
        if not is_finale:
            narrative_parts.append(
                color_text(
                    'Keep your eyes open. Assume every object around you is a weapon. Cheat Death.',
                    'warning', rm
                )
            )

        return "\n\n".join(narrative_parts)

    def proceed_to_next_level(self, instance=None):
        app = App.get_running_app()
        app.start_new_session_flag = False
        game_screen = self.manager.get_screen('game')

        if self.next_level_id is None:
            self.next_level_id = getattr(app, 'interlevel_next_level_id', None)
            self.next_level_start_room = getattr(app, 'interlevel_next_start_room', None)

        # ── NEW: If STILL None, ask game_logic to evaluate the transition ──
        if self.next_level_id is None and game_screen and game_screen.game_logic:
            resolved_level, resolved_room = game_screen.game_logic._evaluate_dynamic_transition(
                game_screen.game_logic.player.get('current_level', 'level_1')
            )
            if resolved_level:
                self.next_level_id = resolved_level
                self.next_level_start_room = resolved_room
                self.logger.info(f"InterLevelScreen: Resolved missing next_level via dynamic transition: {resolved_level}")

        if self.next_level_id and game_screen and game_screen.game_logic:
            self.logger.info(f"InterLevelScreen: Proceeding to level {self.next_level_id}.")
            start_room = self.next_level_start_room or None
            try:
                # Start the next level logic FIRST
                game_screen.game_logic.start_next_level(self.next_level_id, start_room)
                
                # >>> GO DIRECTLY TO GAME SCREEN <<<
                self.go_to_screen('game', direction='left')
                return
            except Exception as e:
                self.logger.error(f"InterLevelScreen: Failed to start next level: {e}", exc_info=True)
                # If loading fails, fall through to Win Screen to avoid softlock
            return
        # Only go to win if we genuinely have no next level AND the game is flagged won
        if game_screen and game_screen.game_logic and game_screen.game_logic.game_won:
            self.go_to_screen('win', direction='fade')
        else:
            # Safety: go back to game rather than falsely winning
            self.logger.warning("InterLevelScreen: next_level_id is None but game not won — returning to game.")
            self.go_to_screen('game', direction='left')
            
class WinScreen(BaseScreen):
    score_display = ObjectProperty(None)
    narrative_display = ObjectProperty(None)  # You need to add this Label to your KV file

    def on_enter(self, *args):
        app = App.get_running_app()
        if app.game_logic:
            app.game_logic.ui_events.clear()
            
        final_score = getattr(app, 'last_game_score', 0)
        self.score_display.text = f"Final Score: {final_score}"

        # --- NEW: Pull the epic narrative from game_logic ---
        game_screen = self.manager.get_screen('game')
        if game_screen and game_screen.game_logic:
            epic_ending = game_screen.game_logic.calculate_ending()
            self.narrative_display.text = epic_ending
        else:
            self.narrative_display.text = "Your friendly neighborhood developer, KorbenD3P0, is hard at work bringing you the complete game! Stay tuned for upcoming features, including:\nPlayable intro disasters!(actually I did that)\nMore hazards!(did that too)\nAn ending!(nope just started those, too)\n\nThank you for playing this demo version of 'DieNamic Engine: Death's Designs'!"

# --- Lose Screen ---
class LoseScreen(BaseScreen):
    def on_enter(self, *args):
        self.logger.info("LoseScreen on_enter triggered.")
        app = App.get_running_app()
        game_logic = getattr(app, 'game_logic', None)
        
        if not game_logic:
            return

        player_state = game_logic.player
        current_city = player_state.get('current_city', 'the city')

        # 1. Clean the Reason
        reason = player_state.get('death_reason', 'a freak accident')
        self.death_reason_label.text = reason.replace('{city_name}', current_city)

        # 2. Clean the Flavor Text
        flavor = player_state.get('flavor_text', '')
        
        # --- THE FIX: De-duplication check ---
        # If flavor is empty OR identical to the reason, hide the label to prevent double-display
        if not flavor or flavor.strip() == reason.strip():
            self.flavor_text_label.text = ""
            self.flavor_text_label.height = 0
            self.flavor_text_label.opacity = 0
        else:
            self.flavor_text_label.text = flavor.replace('{city_name}', current_city)
            self.flavor_text_label.opacity = 1

    def set_death_info(self, death_reason, final_narrative, flavor_text, hide_stats=False, player_state=None):
        """
        Bridge method to satisfy the UI Orchestrator call.
        This stores the specific death event data before we transition to this screen.
        """
        self.logger.info(f"LoseScreen: Receiving death info - Reason: {death_reason}")
        
        # We update the labels immediately if they are available
        if hasattr(self, 'death_reason_label') and self.death_reason_label:
            self.death_reason_label.text = death_reason
        
        if hasattr(self, 'final_narrative_label') and self.final_narrative_label:
            self.final_narrative_label.text = final_narrative
            
        if hasattr(self, 'flavor_text_label') and self.flavor_text_label:
            self.flavor_text_label.text = flavor_text

        # If stats panel exists, handle visibility
        if hasattr(self, 'ids') and 'stats_panel' in self.ids:
            self.ids.stats_panel.opacity = 0 if hide_stats else 1

    def return_to_title(self):
        app = App.get_running_app()
        # Clean up state to prevent the "game already in progress" lock
        if hasattr(app, 'game_logic') and app.game_logic:
            app.game_logic.current_level = None
            app.game_logic.player['hp'] = 0
            self.reset_ui_state()
            # Pull the game screen from your ScreenManager (assuming it's named 'game')
        if app and app.root and app.root.has_screen('game'):
            game_screen = app.root.get_screen('game')
            
            # NOW call the scrub method on the live object
            if hasattr(game_screen, 'game_logic'):
                game_screen.game_logic.wipe_active_state()
        self.go_to_screen('title', 'right')

class GameScreen(BaseScreen):
    status_display = ObjectProperty(None)
    output_panel = ObjectProperty(None)
    action_input = ObjectProperty(None)
    main_actions = ObjectProperty(None)
    contextual_actions = ObjectProperty(None)
    compass_display = ObjectProperty(None)
    inventory_display = ObjectProperty(None)

    def __init__(self, **kwargs):
        self.resource_manager = kwargs.pop('resource_manager', None)
        kwargs.pop('achievements_system', None)
        kwargs.pop('hazard_engine', None)
        kwargs.pop('death_ai', None)
        super().__init__(**kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.game_logic = None
        self.active_qte_popup = None
        self.active_info_popup = None
        self._fear_hold_threshold = 0.6  
        self._popup_vfx_lock = {"fear": False, "damage": False}  
        self._scheduled_events = []
        Clock.schedule_interval(self._update, 1/60.0) 

    def _get_widget(self, name: str):
        return (getattr(self, name, None)
                or self.ids.get(name)
                or self.ids.get(f"{name}_id"))

    def _update(self, dt):
        """The main UI update loop, driven by the Clock."""
        if self.game_logic:
            # Check for any signals from the engine
            events = self.game_logic.get_ui_events()
            if events:
                self._handle_ui_events(events)

    def on_pre_enter(self, *args):
        """Attach engine references before any UI events or input occur."""
        app = App.get_running_app()

        # --- FORCE REBIND: always use the latest session's engines ---
        try:
            # Always rebind GameLogic from App (prevents stale/stuck sessions)
            self.game_logic = getattr(app, 'game_logic', None)

            # Also refresh engine cross-links every time
            if self.game_logic:
                # Ensure GL <-> engines are consistent
                hz = getattr(app, 'hazard_engine', None)
                da = getattr(app, 'death_ai', None)
                qte = getattr(app, 'qte_engine', None)

                self.game_started = True
                self.game_logic.hazard_engine = hz
                self.game_logic.death_ai = da
                self.game_logic.qte_engine = qte

                if hz and getattr(hz, 'game_logic', None) is not self.game_logic:
                    hz.game_logic = self.game_logic

                # Clear any lingering QTE flags from old overlays
                try:
                    if isinstance(self.game_logic.player, dict):
                        self.game_logic.player['qte_active'] = False
                        self.game_logic.player['qte_context'] = {}
                except Exception:
                    pass
            else:
                self.game_started = False
        except Exception as e:
            self.logger.error(f"GameScreen.on_pre_enter: rebind failed: {e}", exc_info=True)

        self.logger.info("GameScreen: Engine references attached.")

    def on_enter(self, *args):
        # --- THE FIX: Release all transition locks unconditionally ---
        self._ui_transition_lock = False
        if self.game_logic:
            self.game_logic.is_transitioning = False
        try:
            app = App.get_running_app()

            # Integrate engine/callback wiring on screen entry
            if hasattr(app, 'game_logic') and app.game_logic:
                self.game_logic = app.game_logic
                self.hazard_engine = getattr(app.game_logic, 'hazard_engine', None)

                ui_cb = getattr(self, '_process_ui_event', None)
                if ui_cb is None:
                    ui_cb = lambda event: self._handle_ui_events(
                        event if isinstance(event, list) else [event]
                    )
                if hasattr(self.game_logic, 'set_ui_callback'):
                    self.game_logic.set_ui_callback(ui_cb)

                context_cb = getattr(self, 'update_context_dock', None)
                if context_cb is None:
                    context_cb = lambda *a, **k: self._handle_refresh_context_actions()
                if hasattr(self.game_logic, 'set_context_actions_callback'):
                    self.game_logic.set_context_actions_callback(context_cb)

                map_cb = getattr(self, 'update_map_dock', None)
                if map_cb is None:
                    map_cb = lambda *a, **k: self._refresh_map()
                if hasattr(self.game_logic, 'set_map_callback'):
                    self.game_logic.set_map_callback(map_cb)

                # Start the premonition timer only when arriving at level 0
                if str(self.game_logic.player.get('current_level', '')) in ["0", "level_0"]:
                    is_visionary = self.game_logic.player.get('is_visionary', False)
                    already_died = self.game_logic.player.get('premonition_already_died', False)
                    # Visionary: timer only starts after intercept (via reset_premonition_state)
                    # Non-Visionary: timer starts normally on GameScreen entry
                    if not is_visionary and (
                        not hasattr(self.game_logic, '_premonition_event_trigger')
                        and hasattr(self.game_logic, '_start_premonition_timer')
                    ):
                        self.game_logic._start_premonition_timer()

                self.logger.info("GameScreen: Engine references attached.")

                if hasattr(self.game_logic, 'force_ui_refresh'):
                    self.game_logic.force_ui_refresh()

            # Execute pending load if coming from LoadGameScreen
            if getattr(self, 'pending_load', False) and self.game_logic:
                slot = getattr(self, 'load_slot_identifier', None)
                self.logger.info(f"GameScreen: Executing pending load for slot '{slot}'")
                self.game_logic._command_load(slot)
                self.pending_load = False
                self.load_slot_identifier = None

            if not self.game_logic:
                out = self._get_widget('output_panel')
                if out and hasattr(out, 'append_text'):
                    out.append_text("[color=ff4444]Engine not initialized. Return to main menu.[/color]")
                return

            # Ensure start_response exists
            if not getattr(self.game_logic, 'start_response', None):
                try:
                    location = self.game_logic.player.get('location', '')
                    initial_desc = self.game_logic._get_rich_room_description(location)

                    room_data = self.game_logic.get_room_data(location) or {}
                    ui_events = []
                    first_text = room_data.get('first_entry_text')
                    already_shown = location in self.game_logic.player.setdefault('shown_entry_popups', [])
                    
                    if first_text and not already_shown:
                        # Lock it immediately
                        self.game_logic.player['shown_entry_popups'].add(location)
                        # Push to UI queue
                        ui_events.append(
                            self.game_logic._make_first_entry_popup_event(location, first_text)
                        )

                    self.game_logic.start_response = {
                        'messages': [initial_desc],
                        'game_state': self.game_logic.get_current_game_state(),
                        'ui_events': ui_events,
                        'turn_taken': False,
                        'success': True
                    }
                except Exception as e:
                    self.logger.error(f"on_enter: Failed to build start_response: {e}", exc_info=True)
                    return

            initial_response = self.game_logic.start_response
            room_desc = initial_response.get('messages', [''])[0] if initial_response.get('messages') else ''

            ui_events = initial_response.get('ui_events', [])
            if ui_events:
                self._handle_ui_events(ui_events)

            panel = self._get_widget('output_panel')
            if panel and hasattr(panel, 'append_text') and room_desc:
                panel.append_text(room_desc, clear_previous=True)

            ai = self._get_widget('action_input')
            if ai:
                if hasattr(ai, 'submit_button'):
                    ai.submit_button.unbind(on_release=self.on_submit_command)
                    ai.submit_button.bind(on_release=self.on_submit_command)
                if hasattr(ai, 'text_input'):
                    ai.text_input.unbind(on_text_validate=self.on_submit_command)
                    ai.text_input.bind(on_text_validate=self.on_submit_command)

                    if sys.platform not in ('android', 'ios'):
                        ai.text_input.focus = False
                    else:
                        self.logger.debug("on_enter: Skipping auto-focus on mobile platform")

            # Wire contextual actions
            ctx_widget = self._get_widget('contextual_actions')
            if ctx_widget:
                ctx_widget.action_callback = self.process_and_clear
                self.logger.info("GameScreen: ContextualActionsWidget callback wired successfully.")
            else:
                self.logger.error("GameScreen: Could not find 'contextual_actions' widget to wire callback.")

            # Update map display
            map_widget = self._get_widget('map_display')
            if map_widget and hasattr(map_widget, 'update'):
                try:
                    room = self.game_logic.current_level_rooms_world_state.get(
                        self.game_logic.player.get('location'), {}
                    )
                    map_widget.update(room)
                except Exception as e:
                    self.logger.error(f"Failed to update map widget: {e}", exc_info=True)

            self._populate_main_action_buttons()
            self.update_all_ui_elements(self.game_logic.get_current_game_state())

        except Exception as e:
            self.logger.error(f"on_enter error: {e}", exc_info=True)
            


    # Add this helper to GameScreen to make wrapping 24 lines easier
    def _schedule_safe(self, callback, delay):
        ev = Clock.schedule_once(callback, delay)
        if hasattr(self, '_scheduled_events'):
            self._scheduled_events.append(ev)
        return ev

    def update_all_ui_elements(self, game_state: dict):
        status = self._get_widget('status_display')
        if status and hasattr(status, 'update'):
            status.update(game_state.get('player', {}))
        else:
            self.logger.warning("GameScreen: status_display not wired.")
        
        # Update Map
        self._refresh_map()

        # --- NEW: Update Compass ---
        compass = self._get_widget('compass_display')  # We will rename the ID in KV later
        if compass and self.game_logic:
            player_loc = self.game_logic.player.get('location')
            current_room_data = self.game_logic.get_room_data(player_loc)
            
            # Add safety check before accessing current_room_data
            if current_room_data:
                # 1. Base Update (Lights up directions)
                compass.update(current_room_data)
                
                # 2. Advanced Lock Check
                # Iterate exits to see if destination is locked
                exits = current_room_data.get('exits', {})
                for direction, dest_room_id in exits.items():
                    # Skip dynamic exits (dicts) for lock checking, assume open or handled elsewhere
                    if isinstance(dest_room_id, str):
                        dest_data = self.game_logic.get_room_data(dest_room_id)
                        if dest_data:
                            # Check standard lock, hazard lock (MRI), or specific locking dict
                            is_locked = (dest_data.get('locked') or 
                                         dest_data.get('locked_by_mri') or 
                                         dest_data.get('locking', {}).get('locked'))
                            
                            if is_locked:
                                compass.set_locked_status(direction, True)

        # --- Update Inventory Widget ---
        inv_widget = self._get_widget('inventory_display')
        if inv_widget and hasattr(inv_widget, 'update'):
            player = (game_state or {}).get('player', {})
            inventory_data = player.get('inventory', [])
            inv_widget.update(inventory_data, on_item_tap=self._on_inventory_item_tap)

        # Keep the low-health VFX in sync with current HP/max_hp
        try:
            player = (game_state or {}).get('player', {}) or {}
            hp = int(player.get('hp', 0))
            max_hp = int(player.get('max_hp', 30))
            threshold = max(5, int(max_hp * 0.15))
            if 0 < hp <= threshold:
                self.show_low_health_effect()
            else:
                # Do not clear if a popup is forcing damage VFX
                if not self._popup_vfx_lock.get("damage"):
                    self.clear_low_health_effect()
        except Exception:
            pass
        
        # Fear VFX sync
        try:
            player = (game_state or {}).get('player', {}) or {}
            fear_val = float(player.get('fear', 0.0))
            # While popup lock is on, keep it forced; otherwise normal rules
            self.show_fear_effect(fear_val, force_override=self._popup_vfx_lock.get("fear", False))
            if not self._popup_vfx_lock.get("fear", False) and fear_val < 0.15:
                self.clear_fear_effect()
        except Exception:
            pass

    def _refresh_map(self):
        try:
            if not self.game_logic: return
            
            # --- NEW LOGIC (ADD) ---
            compass = self.compass_display
            if compass and hasattr(compass, 'update'):
                player_loc = self.game_logic.player.get('location')
                room_data = self.game_logic.get_room_data(player_loc)
                
                # Ensure the room actually exists before trying to read exits!
                if room_data:
                    # 1. Update active directions
                    compass.update(room_data)
                    
                    # 2. Check for locks
                    exits = room_data.get('exits', {})
                    for direction, dest in exits.items():
                        if isinstance(dest, str):
                            d_data = self.game_logic.get_room_data(dest)
                            if d_data: # Ensure room data exists before checking locks
                                # Check canonical locks + MRI locks
                                is_locked = d_data.get('locked') or d_data.get('locked_by_mri')
                                if is_locked:
                                    compass.set_locked_status(direction, True)

        except Exception as e:
            self.logger.error(f"_refresh_map failed: {e}", exc_info=True)

    # --- NEW: The UI Event Handler ---
    def on_qte_input_submit(self, user_input):
        """Handle QTE input safely, trusting the Engine and UI Event Queue."""
        if not self.game_logic: 
            return

        try:
            result = self.game_logic.process_player_input(user_input)
            
            if not isinstance(result, dict): 
                return

            out = self._get_widget('output_panel')
            for m in result.get('messages', []):
                if out and hasattr(out, 'append_text'):
                    out.append_text(m)

            self.update_all_ui_elements(result.get('game_state', {}))
            self._handle_ui_events(result.get('ui_events', []))
            
            # Safe Cleanup: Only clear popup if the engine explicitly says QTE is over,
            # AND there is no new QTE currently active in the chain.
            qte_engine = getattr(self.game_logic, 'qte_engine', None)
            active_after = bool(getattr(qte_engine, 'active_qte', None))
            
            if not active_after and self.active_qte_popup:
                if not getattr(self.active_qte_popup, 'is_dismissed', False):
                    self.active_qte_popup.dismiss()
                self.active_qte_popup = None

        except Exception as e:
            self.logger.error(f"Error processing QTE input: {e}", exc_info=True)

    def _normalize_ui_events(self, events):
        """Accept list/dict and unwrap common containers to a flat list of UI events."""
        if not events:
            return []
        # If a dict container with 'consequences' was accidentally pushed to UI, unwrap it
        if isinstance(events, dict) and 'consequences' in events and not events.get('event_type') and not events.get('type'):
            cons = events.get('consequences') or []
            return cons if isinstance(cons, list) else [cons]
        # If already a list, return as-is
        if isinstance(events, list):
            return events
        # Anything else, wrap to list
        return [events]

    def _synthesize_deferred_qte_if_missing(self, event: dict) -> Optional[dict]:
        """If a popup has no defer fields but references a state that triggers a QTE, synthesize on_close_start_qte."""
        try:
            meta = event.get("meta") or {}
            hid = meta.get("hazard_id")
            state = meta.get("state")
            if not (hid and state and self.game_logic and self.game_logic.hazard_engine):
                return None
            h = self.game_logic.hazard_engine.active_hazards.get(hid)
            sdef = ((h or {}).get("master_data") or {}).get("states", {}).get(state, {})
            qte_entry = sdef.get("triggers_qte_on_entry")
            if not qte_entry:
                return None
            qte_ctx = dict(qte_entry.get("qte_context") or {})
            qte_ctx["qte_source_hazard_id"] = hid
            self.logger.info(f"Synthesizing QTE for state {state}")
            return {"qte_type": qte_entry.get("qte_type"), "qte_context": qte_ctx}
        except Exception as e:
            self.logger.error(f"_synthesize_deferred_qte_if_missing error: {e}", exc_info=True)
            return None

    def _bind_popup_defers(self, popup, event: dict):
        """Bind deferred actions to popup dismissal with robust fallback."""
        deferred_qte = event.get("on_close_start_qte") or event.get("deferred_qte")
        defer_state = event.get("on_close_set_hazard_state")
        emit_events = event.get("on_close_emit_ui_events") or []
        
        # --- NEW: Support for chaining commands (Auto-Advance) ---
        deferred_command = event.get("on_close_command")
        # ---------------------------------------------------------

        def on_dismiss(*args):
            if getattr(args[0] if args else None, '_suppress_on_dismiss', False):
                return   # silent dismiss — do nothing
            # Before defers: clear popup-scoped VFX if thresholds do not demand persistence
            try:
                player = (self.game_logic.get_current_game_state().get('player', {})
                            if self.game_logic else {})
            except Exception:
                player = (self.game_logic.player if self.game_logic else {}) or {}

            if event.get("vfx_hint") == "damage":
                # Keep if HP remains below threshold, else clear
                hp = int(player.get('hp', 0))
                max_hp = int(player.get('max_hp', 30))
                low_thr = max(5, int(max_hp * 0.15))
                if not (0 < hp <= low_thr):
                    self.clear_low_health_effect()
                self._popup_vfx_lock["damage"] = False

            if event.get("vfx_hint") == "fear":
                fear_val = float(player.get('fear', 0.0))
                # Keep if fear high enough, else clear
                if fear_val < self._fear_hold_threshold:
                    self.clear_fear_effect()
                self._popup_vfx_lock["fear"] = False

            if deferred_qte and self.game_logic and self.game_logic.qte_engine:
                qte_type = deferred_qte.get('qte_type')
                qte_ctx = deferred_qte.get('qte_context', {})
                # Only skip if a non-finale QTE is already active.
                # If finale_qte_chain_pending is set, the active flag was placed
                # by _trigger_next_finale_qte and this popup IS the intended gate.
                is_finale_chain = self.game_logic.player.pop('finale_qte_chain_pending', False)
                if self.game_logic.player.get('qte_active') and not is_finale_chain:
                    self.logger.info(
                        f"_bind_popup_defers: Skipping deferred QTE '{qte_type}' — "
                        f"a non-finale QTE is already active."
                    )
                else:
                    try:
                        self.logger.info(f"Starting deferred QTE '{qte_type}' after popup")
                        self.game_logic.qte_engine.start_qte(qte_type, qte_ctx)
                    except Exception as e:
                        self.logger.error(f"Failed to start deferred QTE '{qte_type}': {e}", exc_info=True)

            # 2. Apply Hazard State
            if defer_state and self.game_logic and self.game_logic.hazard_engine:
                hid = defer_state.get("hazard_id")
                t_state = defer_state.get("target_state")
                try:
                    # Guard: don't apply if already applied for this hazard+state combo
                    applied_key = f"_deferred_applied_{hid}_{t_state}"
                    if self.game_logic.player.get(applied_key):
                        self.logger.warning(
                            f"_bind_popup_defers: Skipping duplicate deferred state {hid} -> {t_state}"
                        )
                    else:
                        self.game_logic.player[applied_key] = True
                        # Schedule cleanup so the guard doesn't persist across replays
                        Clock.schedule_once(
                            lambda dt, k=applied_key: self.game_logic.player.pop(k, None), 5.0
                        )
                        self.logger.info(f"Applying deferred state change: {hid} -> {t_state}")
                        result = self.game_logic.hazard_engine.set_hazard_state(hid, t_state)
                        cons = (result or {}).get("consequences", [])
                        if cons:
                            self._handle_consequences_sequentially(cons)
                            return
                except Exception as e:
                    self.logger.error(f"Error applying deferred hazard state: {e}", exc_info=True)

            # 3. Emit Events
            if emit_events and self.game_logic:
                for ev in emit_events:
                    self.game_logic.add_ui_event(ev)
                # CRITICAL: Drain the queue NOW so game_over (and other critical
                # events) process immediately instead of sitting orphaned until
                # the next player command.
                pending = self.game_logic.get_ui_events()
                if pending:
                    self.logger.info(f"_bind_popup_defers: Draining {len(pending)} queued events after popup dismiss")
                    self._handle_ui_events(pending)

            # 4. Execute Deferred Command (The Fix)
            if deferred_command:
                self.logger.info(f"Executing deferred command: '{deferred_command}'")
                # We use a slight delay to allow the popup to fully clear
                ev = Clock.schedule_once(lambda dt: self.on_submit_command(command_override=deferred_command), 0.1)
                self._scheduled_events.append(ev)

            self.active_info_popup = None

        popup.bind(on_dismiss=on_dismiss)

    def _handle_ui_events(self, events):
        """
        Orchestrator for UI event processing with normalization, prioritization, and delegation.
        Delegates to small helpers with robust logging and error handling.
        """
        if not events:
            return

        try:
            # 1. Normalize and validate events
            events = self._normalize_ui_events(events)

            # --- THE FIX: Clear stale popup locks for QTE chains ---
            # If the batch contains a QTE show event, clear any stale popup lock
            # so it isn't blocked by a popup that was just dismissed.
            has_qte_event = any(
                (e.get('event_type') or e.get('type', '')) in ('show_qte', 'trigger_qte')
                for e in events
            )
            if has_qte_event:
                self._popup_is_active = False
            # -------------------------------------------------------

            self.logger.info(f"\n{'='*40}\nUI EVENT BATCH INCOMING: {len(events)} events\n{'='*40}")
            for i, e in enumerate(events):
                e_type = e.get('event_type') or e.get('type', 'UNKNOWN')
                title = e.get('title', '')
                self.logger.info(f"  [{i+1}/{len(events)}] Type: {e_type} | Title/Info: {title} | Payload: {e}")
            self.logger.info(f"{'='*40}\n")
            
            valid_events = []
            for e in events:
                if not isinstance(e, dict):
                    continue
                e_type = e.get('event_type') or e.get('type', '')
                
                if e_type == "schedule_transit":
                    duration = e.get("duration", 4.0)
                    try:
                        Clock.schedule_once(self._on_elevator_timer_complete, duration)
                        self.logger.info(f"_handle_ui_events: Scheduled elevator transit in {duration}s")
                    except Exception as e:
                        self.logger.error(f"_handle_ui_events: Failed scheduling elevator transit: {e}", exc_info=True)
                    continue
                    
                valid_events.append(e)
                
            sorted_events = sorted(valid_events, key=lambda e: e.get('priority', 0), reverse=True)
            self.logger.debug(f"_handle_ui_events: Processing {len(sorted_events)} valid events")

            # 2. Delegate to the master switchboard
            inventory_changed = self._process_all_events(sorted_events)

            # 3. Final UI refresh if needed
            if inventory_changed and self.game_logic:
                self.update_all_ui_elements(self.game_logic.get_current_game_state())
                
        except Exception as e:
            self.logger.error(f"_handle_ui_events: Orchestrator error: {e}", exc_info=True)


    def _process_all_events(self, sorted_events: list) -> bool:
        """
        Process all events in priority order. 
        Ensures Narrative Popups -> QTEs -> Terminal Events are handled in correct sequence.
        """
        inventory_affecting_types = {
            "take", "use", "drop", "give", "talk", "talk_to", "talkto", "respond 1", "respond 2", "respond 3",
            "equip", "unequip", "consume", "pickup", "pick_up", "steal", "loot", "combine", "craft", "respond 4"
        }
        inventory_changed = False
        batch_tail = []

        # 1. THE TERMINAL PRE-CHECK
        # We find all game_over events and pick the 'best' one based on priority.
        terminal_events = [e for e in sorted_events if e.get('event_type') == 'game_over']
        if terminal_events:
            self.logger.info(f"Terminal events detected. Selecting highest priority from {len(terminal_events)} events.")
            best = max(terminal_events, key=lambda e: e.get('priority', 0))
            self._handle_game_over(best)
            return inventory_changed

        # 2. BLOCKING UI GUARD
        # Do not process a new batch while a blocking popup/QTE is active.
        if getattr(self, '_popup_is_active', False):
            return False

        # 3. THE SORTING PATch
        # We sort so that standard logic (-1) runs first, then Popups (0), 
        # then QTEs (1), and finally game_over (99) is pushed safely to the end.
        def _priority_key(e):
            t = e.get('event_type') or e.get('type', '')
            if t == 'game_over':  return 99   # Handled by pre-check; push to end to be safe
            if t == 'show_popup': return 0    # Info popups before QTEs
            if t == 'trigger_qte': return 1   # Changed to match your trigger key
            if t == 'show_qte':    return 1 
            return -1

        sorted_events.sort(key=_priority_key)

        # 4. PROCESSING LOOP
        for i, event in enumerate(sorted_events):
            try:
                event_type = event.get('event_type') or event.get('type')
                if not event_type:
                    continue

                self.logger.debug(f"_process_all_events: Handling event_type '{event_type}'")

                # Handle batch halting for Popups and QTEs
                # Use 'trigger_qte' to match your intercept logic
                if event_type in {"show_popup", "trigger_qte"}:
                    self._popup_is_active = True
                    batch_tail = sorted_events[i + 1:]

                if event_type == 'show_qte':       # ← ADD
                    self._handle_show_qte(event)
                    return inventory_changed

                # Setup continuation if we hit a blocking event
                if batch_tail:
                    def _continuation(instance, _tail=batch_tail, *args):
                        if getattr(instance, '_suppress_on_dismiss', False):
                            return
                        self._popup_is_active = False
                        self._process_all_events(_tail)
                    self._pending_popup_continuation = _continuation
                else:
                    def _clear_lock(instance, *args):
                        if getattr(instance, '_suppress_on_dismiss', False):
                            return
                        self._popup_is_active = False
                    self._pending_popup_continuation = _clear_lock

                # --- DIRECT EVENT INTERCEPTS ---
                if event_type == "destroy_info_popup":
                    self._handle_destroy_info_popup()
                    continue

                if event_type == "screen_shake":
                    try:
                        shake_screen(self, intensity=event.get("intensity", 10))
                    except NameError:
                        self.logger.warning("shake_screen function not found!")
                    continue

                if event_type == 'trigger_qte':
                    self.logger.info("UI: Catching QTE Trigger!")
                    self._handle_show_qte(event)
                    # Returning here prevents standard dispatch from trying to handle the QTE
                    return inventory_changed

                # --- DISPATCHER FALLBACKS ---
                if hasattr(self, '_dispatch_to_handler_map') and self._dispatch_to_handler_map(event_type, event):
                    pass 
                elif hasattr(self, '_try_consequences_fallback') and self._try_consequences_fallback(event):
                    pass

                if event_type.lower() in inventory_affecting_types:
                    inventory_changed = True

            except Exception as e:
                self.logger.error(f"_process_all_events error: {e}", exc_info=True)

        return inventory_changed

    def _dispatch_to_handler_map(self, event_type: str, event: dict) -> bool:
        """
        Dispatch event to the appropriate handler from the handler map.
        Returns True if handler was found and executed, False otherwise.
        """
        handler_map = {
            "show_popup": lambda e: self._handle_show_popup_with_defers(e),
            "show_qte": self._handle_show_qte,
            "hide_qte": self._handle_hide_qte,
            "qte_finished": self._handle_hide_qte,
            "destroy_qte_popup": self._handle_destroy_qte_popup,
            "game_over": self._handle_game_over,
            "game_won": self._handle_game_won,
            "level_complete": self._handle_level_complete,
            "append_text": self._handle_show_message,
            "show_message": self._handle_show_message,
            "game_loaded": self._handle_game_loaded,
            "show_map_popup": lambda e: self._show_map_popup(),
            "refresh_map": lambda e: self._refresh_map(),
            "refresh_ui": lambda e: self._refresh_map(),
            "refresh_context_actions": lambda e: self._handle_refresh_context_actions(),
            "player_damage_effect": lambda e: self.show_damage_effect(),
            "player_low_health_effect": lambda e: self.show_low_health_effect(),
            "player_clear_low_health_effect": lambda e: self.clear_low_health_effect(),
            "player_fear_effect_update": self._handle_update_fear,
            "update_fear": self._handle_update_fear,
            "_drain_qte_queue": lambda e: self.game_logic._drain_qte_queue(),
            "schedule_transit": self._handle_schedule_transit, 
            "go_to_main_menu": lambda e: self._go_to_main_menu_from_game(),
            "vibrate": lambda e: self.trigger_vibration(e.get('duration', 0.1)),
            "screen_shake": lambda e: shake_screen(self.children[0], intensity=e.get("intensity", 10)) if self.children else None,
            "trigger_glitch": self._handle_trigger_glitch,
            "screen_flash": self._handle_screen_flash,
        }

        handler = handler_map.get(event_type)
        if handler:
            try:
                handler(event)
                return True
            except Exception as e:
                self.logger.error(f"_dispatch_to_handler_map: Error handling '{event_type}': {e}", exc_info=True)
                return False
        return False


    def _handle_screen_flash(self, event):
        """
        Creates a temporary colored overlay that flashes on screen.
        """
        hex_color = event.get('color', 'ffffff')
        duration = event.get('duration', 0.5)
        max_opacity = event.get('opacity', 0.3)

        from kivy.utils import get_color_from_hex
        color = get_color_from_hex(hex_color)
        
        # Kivy colors are 0-1, so we append the opacity
        color_rgba = (color[0], color[1], color[2], max_opacity) 

        flash = Widget(size_hint=(1, 1), pos_hint={'x': 0, 'y': 0})

        with flash.canvas:
            Color(*color_rgba)
            Rectangle(size=self.size, pos=self.pos) 

        self.add_widget(flash)

        anim = Animation(opacity=0, duration=duration)
        def on_complete(*args):
            self.remove_widget(flash)

        anim.bind(on_complete=on_complete)
        anim.start(flash)

    def _handle_update_fear(self, event):
        """
        Dynamically updates the UI fear meter and triggers tension effects.
        """
        if not self.game_logic:
            return
            
        # 1. Force the status display to refresh with the new fear values
        self.update_all_ui_elements(self.game_logic.player)
        
        # 2. Extract current fear (0.0 to 1.0)
        current_fear = self.game_logic.player.get('fear', 0.0)
        
        # 3. Add sensory feedback based on the fear tier
        if current_fear >= 0.8:
            # Critical Fear: Violent shake and a red blood-rush flash
            if hasattr(self, 'trigger_screen_shake'):
                self.trigger_screen_shake(intensity=12)
            self._handle_screen_flash({
                "color": "ff0000", 
                "duration": 0.4, 
                "opacity": 0.3
            })
        elif current_fear >= 0.5:
            # Warning Fear: Subtle nervous tremor
            if hasattr(self, 'trigger_screen_shake'):
                self.trigger_screen_shake(intensity=4)

    def trigger_vibration(self, duration=0.1):
        """
        Vibrates the device for a specific duration (in seconds).
        Safe for Desktop (logs only) and Android.
        """
        try:
            self.logger.info(f"Hardware: Vibrating for {duration}s")
            # Vibrator exists on Android/iOS
            vibrator.vibrate(duration)
        except NotImplementedError:
            # This catches the error on Windows/Linux/WSL
            self.logger.debug("Vibration not supported on this platform (Running on Desktop?)")
        except Exception as e:
            self.logger.error(f"Vibration failed: {e}")

    def _try_consequences_fallback(self, event: dict) -> bool:
        """
        Fallback: if event has 'consequences' list, pass to sequential processor.
        Returns True if consequences were found and processed.
        """
        if 'consequences' in event and isinstance(event.get('consequences'), list):
            try:
                self._handle_consequences_sequentially(event['consequences'])
                return True
            except Exception as e:
                self.logger.error(f"_try_consequences_fallback: Error handling consequences: {e}", exc_info=True)
        return False

    def _handle_refresh_context_actions(self):
        """
        Refresh contextual actions after game state changes (e.g., container revealed, door unlocked).
        Uses context_dock if present, else falls back to legacy contextual_actions widget.
        """
        try:
            # Prefer context_dock (modern approach)
            if hasattr(self, 'context_dock') and self.context_dock:
                self.context_dock.update(self.game_logic)
                self.logger.info("Refreshed context_dock after game state change")
                return

            # Fallback to legacy contextual_actions widget
            ctx = self._get_widget('contextual_actions')
            if ctx and hasattr(ctx, 'update'):
                ctx.update(self.game_logic)
                self.logger.info("Refreshed contextual_actions widget after game state change")
                return

            self.logger.warning("_handle_refresh_context_actions: No context widget found to refresh")
        except Exception as e:
            self.logger.error(f"_handle_refresh_context_actions: Failed to refresh: {e}", exc_info=True)

    def _go_to_main_menu_from_game(self):
        """
        Reset session and navigate to title from in-game context
        (e.g., after _command_main_menu or any fatal error fallback).
        """
        app = App.get_running_app()
        try:
            if app and hasattr(app, 'reset_session'):
                app.reset_session()
        except Exception:
            pass
        self.go_to_screen('title', direction='left')

    def _collect_unlock_force_targets(self, verb: str) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        try:
            gl = self.game_logic
            if not gl:
                return pairs
            room_id = gl.player.get('location')
            room = gl.get_room_data(room_id) or {}

            for direction, dest in (room.get('exits') or {}).items():
                # Dict exits (like hub transitions) are never force/unlock targets
                if not isinstance(dest, str):
                    continue

                dest_live = gl.current_level_rooms_world_state.get(dest, {}) or {}
                dest_master = gl.get_room_data(dest) or {}

                # Support both top-level locked AND locking sub-object
                locking = dest_master.get('locking', {}) if isinstance(dest_master.get('locking'), dict) else {}
                key_locked = (
                    bool(locking.get('locked')) or
                    bool(dest_master.get('locked')) or     # top-level locked: true
                    bool(dest_live.get('locked'))           # runtime locked state
                )
                mri_locked = bool(dest_live.get('locked_by_mri') or dest_master.get('locked_by_mri'))
                is_forceable = bool(dest_master.get('forceable') or dest_live.get('forceable'))

                show_unlock = False
                if verb == 'unlock' and key_locked:
                    available_keys = gl._get_player_keys()
                    required_keys_raw = (
                        locking.get("unlocks_with") or
                        dest_master.get("unlocks_with")     # also check top-level unlocks_with
                    )
                    if isinstance(required_keys_raw, str):
                        required_keys = [required_keys_raw]
                    elif isinstance(required_keys_raw, list):
                        required_keys = required_keys_raw
                    else:
                        required_keys = []

                    norm = gl._norm
                    req_norms = [norm(k) for k in required_keys]

                    for key_id, key_data in available_keys.items():
                        unlocks = [norm(u) for u in key_data.get("unlocks", [])]
                        aliases = [norm(a) for a in key_data.get("alias", [])]
                        if (any(req in unlocks for req in req_norms) or
                                any(req in aliases for req in req_norms) or
                                any(req == norm(key_id) for req in req_norms) or
                                any(req == norm(key_data.get("name", "")) for req in req_norms) or
                                "*" in key_data.get("unlocks", []) or
                                key_data.get("is_master_key")):
                            show_unlock = True
                            break

                # Show force button if room is locked OR explicitly forceable
                show_force = (verb == 'force' and (key_locked or mri_locked or is_forceable))

                if show_unlock or show_force:
                    btn_text = f"{direction.title()} Door ({dest.replace('_', ' ').title()})"
                    pairs.append((btn_text, f"{verb} {direction}"))

            # Furniture (unchanged)
            for f in (room.get('furniture') or []):
                if not isinstance(f, dict):
                    continue
                fname = f.get('name', 'Unknown')
                locking = f.get('locking', {}) if isinstance(f.get('locking'), dict) else {}
                locked = bool(f.get('locked') or locking.get('locked'))
                forceable = bool(f.get('forceable') or f.get('is_breakable'))
                show_unlock = (verb == 'unlock' and locked)
                show_force = (verb == 'force' and (locked or forceable))
                if show_unlock or show_force:
                    pairs.append((fname, f"{verb} {fname}"))

            return pairs
        except Exception as e:
            self.logger.error(f"_collect_unlock_force_targets: Error: {e}", exc_info=True)
            return []

    # --- Simple visual effects for player status ---
    def _update_fx_rect(self, *_):
        """Keep FX overlays sized to the screen."""
        try:
            if hasattr(self, "_damage_rect") and self._damage_rect is not None:
                self._damage_rect.pos = self.pos
                self._damage_rect.size = self.size
            if hasattr(self, "_low_health_rect") and self._low_health_rect is not None:
                self._low_health_rect.pos = self.pos
                self._low_health_rect.size = self.size
            # NEW: keep fear overlay in sync
            if hasattr(self, "_fear_rect") and self._fear_rect is not None:
                self._fear_rect.pos = self.pos
                self._fear_rect.size = self.size
        except Exception:
            pass

    # --- NEW: helper to apply popup-scoped VFX ---
    def _apply_popup_vfx_hint(self, event: dict):
        try:
            hint = (event or {}).get("vfx_hint")
            if not hint:
                return
            if hint == "fear":
                # Force show even if fear below threshold while popup is open
                self._popup_vfx_lock["fear"] = True
                # Use current fear to scale intensity; force overlay visible
                try:
                    fear_val = float(self.game_logic.player.get('fear', 0.0)) if self.game_logic else 0.0
                except Exception:
                    fear_val = 0.0
                self.show_fear_effect(fear_val, force_override=True)
            elif hint == "damage":
                # Force a persistent red pulse while popup is open
                self._popup_vfx_lock["damage"] = True
                self.show_low_health_effect()
        except Exception as e:
            self.logger.error(f"_apply_popup_vfx_hint error: {e}", exc_info=True)

    # --- NEW: Fear pulse overlay (blue), scales with player fear in [0..1] ---
    # --- NEW: adjust fear effect to support forced visibility ---
    def show_fear_effect(self, fear_value: float = None, force_override: bool = False):
        """
        Show or update a blue pulsing overlay whose intensity increases with fear.
        fear_value: 0..1. Clears overlay if below threshold unless force_override=True.
        """
        try:
            if fear_value is None and self.game_logic:
                fear_value = float(self.game_logic.player.get('fear', 0.0))
            fear = max(0.0, min(1.0, float(fear_value or 0.0)))

            # Threshold below which we don't show the effect, unless forced by popup
            if not force_override and fear < 0.15:
                # Respect lock: do not clear if a popup has forced it on
                if not self._popup_vfx_lock.get("fear"):
                    self.clear_fear_effect()
                return

            # ...existing creation/pulse code...
            if not getattr(self, "_fear_color", None) or not getattr(self, "_fear_rect", None):
                with self.canvas.after:
                    self._fear_color = Color(0.6, 0.2, 0.8, 0.0)
                    self._fear_rect = Rectangle(pos=self.pos, size=self.size)
                self.bind(size=self._update_fx_rect, pos=self._update_fx_rect)

            min_alpha = 0.05 + 0.10 * fear
            max_alpha = min(0.45, 0.12 + 0.30 * fear)
            speed = 1.0 + 2.0 * fear

            if getattr(self, "_fear_pulse_ev", None):
                try:
                    self._fear_pulse_ev.cancel()
                except Exception:
                    pass
                self._fear_pulse_ev = None

            phase = 0.0
            def pulse(dt):
                if not self._fear_color:
                    return False
                nonlocal phase
                phase += dt * speed * 2 * 3.1415926
                s = (1 + __import__("math").sin(phase)) * 0.5
                self._fear_color.a = min_alpha + (max_alpha - min_alpha) * s
                return True

            self._fear_pulse_ev = Clock.schedule_interval(pulse, 1/60.0)
        except Exception as e:
            self.logger.error(f"show_fear_effect error: {e}", exc_info=True)

    def clear_fear_effect(self):
        """Remove fear overlay and pulse."""
        try:
            if getattr(self, "_fear_pulse_ev", None):
                try:
                    self._fear_pulse_ev.cancel()
                except Exception:
                    pass
                self._fear_pulse_ev = None
            if getattr(self, "_fear_color", None):
                try:
                    self.canvas.after.remove(self._fear_color)
                    self.canvas.after.remove(self._fear_rect)
                except Exception:
                    pass
                self._fear_color = None
                self._fear_rect = None
        except Exception as e:
            self.logger.error(f"clear_fear_effect error: {e}", exc_info=True)

    def _handle_trigger_glitch(self, event):
        """
        Handles the 'trigger_glitch' UI event.
        Event payload: {'event_type': 'trigger_glitch', 'image': 'assets/glitch/skull.png', 'duration': 0.05}
        """
        glitch_overlay = self.ids.get('glitch_overlay_id')
        if glitch_overlay:
            image = event.get('image')
            duration = event.get('duration', 0.05)
            opacity = event.get('opacity', 0.4)
            glitch_overlay.trigger_glitch(image, duration, opacity)

    def trigger_screen_shake(self, intensity=20):
        """
        Violently shakes the screen content to simulate impact.
        """
        # We target the root layout of the screen (usually the first child)
        if not self.children:
            return
            
        content = self.children[0]
        original_pos = (0, 0) # Screens usually position children at 0,0
        
        # Create a jagged, chaotic movement pattern
        # We use 't' (transition) to make it snap rather than slide
        anim = Animation(pos=(dp(intensity), dp(5)), duration=0.04, t='out_quad')
        anim += Animation(pos=(dp(-intensity), dp(-5)), duration=0.04, t='out_quad')
        anim += Animation(pos=(dp(intensity * 0.5), dp(-intensity * 0.5)), duration=0.04, t='out_quad')
        anim += Animation(pos=(dp(-intensity * 0.5), dp(intensity * 0.5)), duration=0.04, t='out_quad')
        
        # Always reset to 0,0 at the end so the UI doesn't get stuck offset
        anim += Animation(pos=original_pos, duration=0.04, t='out_quad')
        
        anim.start(content)

    def show_damage_effect(self):
        """
        The Trinity of Pain: Flash (Visual), Rumble (Tactile), Shake (Kinetic).
        """
        # 1. Haptic Feedback (Vibration)
        App.get_running_app().trigger_vibration(0.3)
        
        # 2. Screen Shake
        self.trigger_screen_shake(intensity=20)
        try:
            # Clean previous flash if running
            if getattr(self, "_damage_cleanup_ev", None):
                self._damage_cleanup_ev.cancel()
                self._damage_cleanup_ev = None
            if getattr(self, "_damage_color", None):
                # Remove previous instructions
                try:
                    self.canvas.after.remove(self._damage_color)
                    self.canvas.after.remove(self._damage_rect)
                except Exception:
                    pass
                self._damage_color = None
                self._damage_rect = None

            with self.canvas.after:
                self._damage_color = Color(1, 0, 0, 0.0)  # start transparent
                self._damage_rect = Rectangle(pos=self.pos, size=self.size)
            # Ensure it tracks size/pos
            self.bind(size=self._update_fx_rect, pos=self._update_fx_rect)

            # Flash timeline: ramp up quickly then fade out
            duration_up = 0.08
            duration_down = 0.28
            peak_alpha = 0.55
            total = duration_up + duration_down
            elapsed = 0.0

            def step(dt):
                nonlocal elapsed
                if not self._damage_color:
                    return False
                elapsed += dt
                if elapsed <= duration_up:
                    # ramp in
                    t = max(0.0, min(1.0, elapsed / duration_up))
                    self._damage_color.a = peak_alpha * t
                elif elapsed <= total:
                    # fade out
                    t = (elapsed - duration_up) / duration_down
                    self._damage_color.a = peak_alpha * (1.0 - max(0.0, min(1.0, t)))
                else:
                    # cleanup
                    try:
                        if self._damage_color:
                            self.canvas.after.remove(self._damage_color)
                        if self._damage_rect:
                            self.canvas.after.remove(self._damage_rect)
                    except Exception:
                        pass
                    self._damage_color = None
                    self._damage_rect = None
                    self._damage_cleanup_ev = None
                    return False
                return True

            self._damage_cleanup_ev = Clock.schedule_interval(step, 1/60.0)
        except Exception as e:
            self.logger.error(f"show_damage_effect error: {e}", exc_info=True)

    def show_low_health_effect(self):
        """Persistent subtle pulsing red tint when health is low."""
        try:
            # If already active, do nothing
            if getattr(self, "_low_health_color", None) and getattr(self, "_low_health_pulse_ev", None):
                return
            with self.canvas.after:
                self._low_health_color = Color(1, 0, 0, 0.14)
                self._low_health_rect = Rectangle(pos=self.pos, size=self.size)
            self.bind(size=self._update_fx_rect, pos=self._update_fx_rect)

            # Pulse between two alpha values
            min_alpha, max_alpha, speed = 0.10, 0.22, 2.0
            phase = 0.0
            def pulse(dt):
                if not self._low_health_color:
                    return False
                nonlocal phase
                phase += dt * speed * 2 * 3.1415926
                s = (1 + __import__("math").sin(phase)) * 0.5
                self._low_health_color.a = min_alpha + (max_alpha - min_alpha) * s
                return True
            self._low_health_pulse_ev = Clock.schedule_interval(pulse, 1/60.0)
        except Exception as e:
            self.logger.error(f"show_low_health_effect error: {e}", exc_info=True)

    def clear_low_health_effect(self):
        """Remove low-health overlay and pulse."""
        try:
            if getattr(self, "_low_health_pulse_ev", None):
                try:
                    self._low_health_pulse_ev.cancel()
                except Exception:
                    pass
                self._low_health_pulse_ev = None
            if getattr(self, "_low_health_color", None):
                try:
                    self.canvas.after.remove(self._low_health_color)
                    self.canvas.after.remove(self._low_health_rect)
                except Exception:
                    pass
                self._low_health_color = None
                self._low_health_rect = None
        except Exception as e:
            self.logger.error(f"clear_low_health_effect error: {e}", exc_info=True)

    def _handle_show_popup(self, event):
        """Show an info popup, with optional deferred actions on dismiss."""
        # Don't show popups if we're transitioning away from the game screen
        if self.manager and self.manager.current != 'game':
            self.logger.info("_handle_show_popup: Suppressed popup — not on game screen.")
            return
        title = event.get("title", "Notice")
        message = event.get("message", "")
        deferred_qte = event.get("on_close_start_qte")
        deferred_state = event.get("on_close_set_hazard_state")
        emit_events = event.get("on_close_emit_ui_events") or []

        def on_dismiss(*args):
            if getattr(args[0] if args else None, '_suppress_on_dismiss', False):
                return   # silent dismiss — do nothing
            # Resume queued UI events/popups after this popup closes.
            self._popup_is_active = False
            try:
                Clock.schedule_once(lambda dt: self._drain_pending_ui_events(), 0.1)
            except Exception:
                pass

            # Start QTE first if requested
            if deferred_qte and self.game_logic and self.game_logic.qte_engine:
                try:
                    self.logger.info(f"Starting deferred QTE '{deferred_qte.get('qte_type')}' after popup")
                    self.game_logic.qte_engine.start_qte(
                        deferred_qte.get('qte_type'),
                        deferred_qte.get('qte_context', {})
                    )
                    return # QTE will drive the rest
                except Exception as e:
                    self.logger.error(f"Error starting deferred QTE: {e}", exc_info=True)
            
            # 2. Emit Events
            if emit_events and self.game_logic:
                for ev in emit_events:
                    self.game_logic.add_ui_event(ev)

        try:
            popup = InfoPopup(title=title, message=message)
            popup.bind(on_dismiss=on_dismiss)

            if deferred_state:
                hazard_id = deferred_state.get("hazard_id")
                target_state = deferred_state.get("target_state")

                if hazard_id and target_state:
                    def _on_dismissed(instance, *args):
                        if getattr(args[0] if args else None, '_suppress_on_dismiss', False):
                            return   # silent dismiss — do nothing
                        try:
                            self.logger.info(
                                f"Executing deferred hazard state change on dismiss: hazard_id={hazard_id}, target_state={target_state}"
                            )
                            self._apply_deferred_hazard_state(hazard_id, target_state)
                        except Exception as e:
                            self.logger.error(f"Error applying deferred hazard state: {e}", exc_info=True)

                    popup.bind(on_dismiss=_on_dismissed)

            popup.open()
            if hasattr(self, 'active_info_popup'):
                self.active_info_popup = popup
            self.logger.info(f"_handle_show_popup: Showing info popup with title '{title}'.")
        except Exception as e:
            self.logger.error(f"Error showing popup: {e}", exc_info=True)

    def _drain_pending_ui_events(self):
        if self.game_logic:
            pending = self.game_logic.get_ui_events()
            if pending:
                self._handle_ui_events(pending)

    def _handle_show_popup_with_defers(self, event):
        """Enhanced popup handler with deferred action binding."""  # ← docstring first
        
        # Screen guard FIRST
        if self.manager and self.manager.current != 'game':
            self.logger.info(...)
            return

        # Cancel any in-flight open_new_popup before scheduling a replacement
        if hasattr(self, '_pending_open_popup_event') and self._pending_open_popup_event:
            self._pending_open_popup_event.cancel()
            self._pending_open_popup_event = None
        
        title = event.get('title', 'Notification')
        message = event.get('message', '...')
        image_path = event.get('image', '')
        self.logger.info(f"_handle_show_popup: Showing info popup with title '{title}'.")

        # Dismiss prior popup AFTER guard
        if hasattr(self, 'active_info_popup') and self.active_info_popup:
            try:
                self.active_info_popup._suppress_on_dismiss = True
                self.active_info_popup.dismiss()
            except Exception:
                pass
            self.active_info_popup = None

        # ── NEW: set a sentinel so the crawler sees a popup is incoming
        # even during the 0.1s scheduling gap before open_new_popup fires.
        self._popup_pending = True   # ← add this

        def open_new_popup(dt):
            self._pending_open_popup_event = None
            self._popup_pending = False
            if self.manager and self.manager.current != 'game':
                return
            popup = InfoPopup(title=title, message=message)
            popup.image_source = image_path
            self.active_info_popup = popup
            self._last_opened_popup = popup          # ← ADD THIS
            self._bind_popup_defers(popup, event)
            # Fire any pending batch continuation that was waiting for this popup
            if hasattr(self, '_pending_popup_continuation') and self._pending_popup_continuation:
                popup.bind(on_dismiss=self._pending_popup_continuation)
                self._pending_popup_continuation = None
            popup.open()

        self._pending_open_popup_event = Clock.schedule_once(open_new_popup, 0.1)

    def _handle_destroy_qte_popup(self, event):
        if self.active_qte_popup:
            try:
                if not getattr(self.active_qte_popup, 'is_dismissed', False):
                    self.active_qte_popup.dismiss()
            except Exception as e:
                self.logger.error(f"Error dismissing QTE popup: {e}", exc_info=True)
            self.active_qte_popup = None
        # Ensure the flag is always cleared so the next QTE can start
        if self.game_logic:
            self.game_logic.player['qte_active'] = False
        self.logger.info("QTE popup destroyed successfully")

    def _handle_show_qte(self, event):
        """Displays a QTE popup. Always routes through qte_engine.start_qte."""
        
        # 1. THE SHIELD: Dismiss everything that isn't a QTE
        app = App.get_running_app()
        for child in [c for c in app.root.children if isinstance(c, ModalView)]:
            if not isinstance(child, QTEPopup):
                child.dismiss()

        # 2. OVERWRITE GUARD
        if getattr(self, 'active_qte_popup', None):
            self.active_qte_popup.dismiss()
            self.active_qte_popup = None

        # 3. ROUTE THROUGH ENGINE if event came from trigger_qte (engine not yet started)
        qte_engine = getattr(self.game_logic, 'qte_engine', None) if self.game_logic else None
        if qte_engine and not qte_engine.active_qte:
            # Engine doesn't know about this QTE yet — start it so active_qte gets set
            qte_type = event.get('qte_type')
            qte_ctx  = event.get('qte_context', {})
            if qte_type:
                try:
                    self.logger.info(f"_handle_show_qte: Bootstrapping engine for '{qte_type}'")
                    qte_engine.start_qte(qte_type, qte_ctx)
                    # start_qte will emit 'show_qte' back to the queue, which will call us again.
                    # Return now so we don't double-create the popup.
                    return
                except Exception as e:
                    self.logger.error(f"_handle_show_qte: engine bootstrap failed: {e}", exc_info=True)

        # 4. VISIONARY HINT — only runs when engine already has active_qte set
        if self.game_logic and self.game_logic.player.get('is_visionary'):
            qte_ctx    = event.get('qte_context', {})
            input_type = event.get('input_type', '')
            hint_text  = None

            if self.game_logic.player.get('premonition_already_died'):
                if input_type == 'mash':
                    hint_text = "[color=00ff00]TAP FAST![/color]"
                elif input_type == 'word':
                    hint_text = f"[color=00ff00]{qte_ctx.get('expected_input_word', '').upper()}[/color]"
                elif input_type == 'choice':
                    choices = qte_ctx.get('choices', [])
                    if choices:
                        hint_text = f"[color=00ff00]{choices[0].upper()}[/color]"

            if hint_text:
                self.add_ui_event_immediate({
                    "event_type": "show_message",
                    "message": f"\n[color=555555][Sight]: {hint_text}[/color]\n"
                })

        # 5. CREATE POPUP
        try:
            popup = QTEPopup(
                prompt=event.get("prompt", "React!"),
                duration=event.get('duration', 5.0),
                input_type=event.get('input_type', 'word'),
                submit_callback=self.on_qte_input_submit,
                qte_context=event.get('qte_context', {})
            )
            popup.bind(on_open=lambda *a: popup.start_countdown())
            popup.open()
            self.active_qte_popup = popup
        except Exception as e:
            self.logger.error(f"Failed to create QTEPopup: {e}", exc_info=True)

    def _start_qte_timer_after_open(self, popup, event):
        """Callback to trigger the logic once the UI is actually visible to the player."""
        self.logger.info(f"QTE visible: Starting countdown for popup_id={id(popup)}")
        
        # Trigger the visual countdown inside the widget if it has one
        if hasattr(popup, 'start_countdown'):
            popup.start_countdown()
            
        # If your QTE_Engine needs a manual start signal to begin the fail-clock:
        if hasattr(self.game_logic, 'qte_engine'):
            # Pass any required metadata to the engine to start the real clock
            self.game_logic.qte_engine.activate_current_timer()

    def _handle_level_complete(self, event):
        """
        Handles the transition to the InterLevelScreen after a level is completed.
        """
        app = App.get_running_app()
        if not app:
            self.logger.error("_handle_level_complete: Could not get running App instance.")
            return
            
        # Stash the IDs on the app so the next screen can find them
        current_lvl = self.game_logic.player.get('current_level') if self.game_logic else 'Unknown'
        app.interlevel_previous_level_id = event.get('level_id', current_lvl)
        app.interlevel_next_level_id = event.get('next_level_id')

        # --- THE FIX: Dedicated UI Lock ---
        # We check our local UI lock instead of the GameLogic lock
        if getattr(self, '_ui_transition_lock', False):
            self.logger.info("_handle_level_complete: UI Transition already in progress. Ignoring duplicate event.")
            return
        self._ui_transition_lock = True
        # ----------------------------------

        self.logger.info("_handle_level_complete: Transitioning to InterLevelScreen.")
        
        # Clear the Logic's Event Queue
        if self.game_logic:
            self.game_logic.ui_events.clear()

        # Force close any "Flavor" or "Omen" popups currently on screen.
        self._handle_destroy_info_popup({})

        try:
            # Dismiss any active QTE popup
            if hasattr(self, 'active_qte_popup') and self.active_qte_popup:
                try:
                    self.active_qte_popup.dismiss()
                except Exception as e:
                    self.logger.warning(f"Failed to dismiss active QTE popup: {e}")
                self.active_qte_popup = None

            # Set all required attributes with robust error handling
            try:
                app.last_level_complete = event
                app.interlevel_completed_level_name = event.get('level_name', 'Unknown Area')
                app.interlevel_narrative_text = event.get('narrative', 'You survived this area.')
                app.interlevel_score_for_level = event.get('score', 0)
                app.interlevel_turns_taken_for_level = event.get('turns_taken', 0)
                app.interlevel_evidence_found_for_level_count = event.get('evidence_count', 0)
                app.interlevel_evaded_hazards = event.get('evaded_hazards', [])
                app.interlevel_next_start_room = event.get('next_start_room')
                
                self.logger.info(f"Level complete data set for: {app.interlevel_completed_level_name}")
            except Exception as e:
                self.logger.error(f"_handle_level_complete: Error setting interlevel attributes: {e}", exc_info=True)
                return

            # Schedule transition to InterLevelScreen
            try:
                Clock.schedule_once(lambda dt: self.go_to_screen('inter_level', 'right'), 1.0)
                self.logger.info("Scheduled transition to 'inter_level' screen.")
            except Exception as e:
                self.logger.error(f"_handle_level_complete: Failed to schedule screen transition: {e}", exc_info=True)

        except Exception as e:
            self.logger.error(f"_handle_level_complete: Unexpected error: {e}", exc_info=True)

    def _show_map_popup(self):
        """Generates full map and shows popup."""
        full_map = self.game_logic.get_full_level_map_string()
        popup = MapPopup(map_content=full_map)
        popup.open()

    def _handle_trigger_dark_path_choice(self, event):
        """Sets the app state for the dark path and moves to epilogue."""
        import kivy.app
        app = kivy.app.App.get_running_app()
        
        # Mark the choice in the app global state
        app.epilogue_type = "trigger_finale_dark_path"
        
        # Logically kill the NPC so the epilogue builder sees the 'sacrifice'
        target_name = event.get('target_name', 'your companion').lower()
        if self.game_logic:
            self.game_logic.player['npc_status'][target_name] = 'dead'
        
        # Immediately move to the epilogue trigger
        self.add_ui_event({"event_type": "go_to_epilogue"})

    def _handle_game_over(self, event: dict):
        self.logger.info("_handle_game_over: Player has died. Transitioning to LoseScreen.")
        
        # WRONG — GameScreen has no .player attribute:
        # death_reason = self.player.get('death_reason', '...')
        
        # RIGHT — player state is on game_logic:
        player_state = self.game_logic.player if self.game_logic else {}
        
        death_reason   = event.get('death_reason') or player_state.get('death_reason', 'a freak accident')
        final_narrative = event.get('final_narrative') or player_state.get('final_narrative', '')
        flavor_text = event.get('flavor_text') or ''
        hide_stats     = event.get('hide_stats', False)

        # Resolve {city_name} here too (fixes the previous session's bug)
        current_city = player_state.get('current_city', 'the city')
        death_reason    = death_reason.replace('{city_name}', current_city)
        final_narrative = final_narrative.replace('{city_name}', current_city)
        flavor_text     = flavor_text.replace('{city_name}', current_city)

        import kivy.app
        app = kivy.app.App.get_running_app()
        if app and app.root and 'lose' in app.root.screen_names:
            lose_screen = app.root.get_screen('lose')
            lose_screen.set_death_info(
                death_reason=death_reason,
                final_narrative=final_narrative,
                flavor_text=flavor_text,
                hide_stats=hide_stats,
                player_state=player_state
            )
            app.root.current = 'lose'
        elif app and app.root:
            app.root.current = 'title'

    def _check_popup_and_transition(self, target_screen):
        """Polls until the active popup is dismissed, then safely transitions."""
        if hasattr(self, 'active_info_popup') and self.active_info_popup:
            self.logger.info(f"Delaying transition to {target_screen} until popup is dismissed. Checking again in 0.2s...")
            # --- THE FIX: The Polling Loop ---
            # Instead of binding to Kivy's on_dismiss (which causes screen-tearing if transitioning during the fade out),
            # we simply check again in 0.2 seconds. When the popup is fully dead, active_info_popup will naturally be None!
            from kivy.clock import Clock
            from kivy.app import App
            ev = Clock.schedule_once(lambda dt: self._check_popup_and_transition(target_screen), 0.2)
            self._scheduled_events.append(ev)
        else:
            self.logger.info(f"Popup cleared. Executing transition to {target_screen}.")
            self._execute_screen_transition(target_screen)

    def _execute_screen_transition(self, target_screen):
        """Actually executes the Kivy screen transition."""
        app = App.get_running_app()
        if app and app.root:
            if target_screen == 'lose':
                app.root.transition.direction = 'up'
            elif target_screen == 'inter_level':
                app.root.transition.direction = 'right'
            else:
                app.root.transition.direction = 'fade'
            app.root.current = target_screen

    def _handle_game_won(self, event):
        """Handles the player's victory and transitions to the WinScreen."""
        self.logger.info("_handle_game_won: Player has won. Transitioning to WinScreen.")
        
        # 1. Kill any active popups so they don't zombie over the win screen
        self._handle_destroy_info_popup({})
        if hasattr(self, 'active_qte_popup') and self.active_qte_popup:
            try:
                self.active_qte_popup.dismiss()
            except Exception as e:
                self.logger.warning(f"Failed to dismiss active QTE popup during victory: {e}")
            self.active_qte_popup = None

        # 2. Extract victory details and park them in the App state
        app = App.get_running_app()
        if app:
            app.victory_reason = event.get('reason', "You have won.")
            app.victory_narrative = event.get('narrative', "The design is fulfilled.")
            app.victory_flavor_text = event.get('flavor', "[color=00ff00]Victory is yours.[/color]")
            
            # Optional: Add an achievement check here if you have a "Win for the first time" achievement!

        # 3. Schedule the dramatic fade to the Win Screen
        app = App.get_running_app()
        app.last_game_score = event.get('final_score', 0)
        Clock.schedule_once(lambda dt: self.go_to_screen('win', 'fade'), 1.0)

    def _handle_show_message(self, event):
        message = event.get("message")
        self.logger.info(f"_handle_show_message: Displaying message: {message}")
        if message and self.output_panel and hasattr(self.output_panel, 'append_text'):
            self.output_panel.append_text(message)

    def _handle_hide_qte(self, event):
        self.logger.info(f"_handle_hide_qte: popup={id(self.active_qte_popup) if self.active_qte_popup else None}, "
                        f"is_dismissed={getattr(self.active_qte_popup, 'is_dismissed', 'N/A')}")
        if self.active_qte_popup:
            try:
                self.active_qte_popup.dismiss()
            except Exception as e:
                self.logger.warning(f"Error dismissing QTE popup in hide_qte: {e}")
            self.active_qte_popup = None
        if self.game_logic and hasattr(self.game_logic, 'player') and isinstance(self.game_logic.player, dict):
            self.game_logic.player['qte_active'] = False

    def _handle_consequences_sequentially(self, consequences: list):
        """
        Main orchestrator to process consequences one at a time.
        Delegates to specific helpers based on consequence type for clean asynchronous chaining.
        """
        try:
            if not consequences:
                self.logger.debug("_handle_consequences_sequentially: No consequences to process.")
                return

            first, rest = consequences[0], consequences[1:]
            ctype = first.get("type") or first.get("event_type")
            self.logger.debug(f"_handle_consequences_sequentially: Processing type '{ctype}' with data: {first!r}")

            # Route to the appropriate helper
            if ctype == "show_popup":
                self._seq_handle_show_popup(first, rest)
            elif ctype == "start_qte":
                self._seq_handle_start_qte(first, rest)
            elif ctype == "hazard_state_change":
                self._seq_handle_hazard_state_change(first, rest)
            else:
                self._seq_handle_fallback(first, rest)

        except Exception as e:
            self.logger.error(f"Unexpected error in _handle_consequences_sequentially: {e}", exc_info=True)


    # ==========================================
    # SEQUENTIAL CONSEQUENCE HELPERS
    # ==========================================

    def _seq_handle_show_popup(self, first: dict, rest: list):
        """Handles rendering UI popups safely and chains the next consequence on dismiss."""
        title = first.get("title", "Notice")
        message = first.get("message")

        # --- THE FIX: Fallback to QTE payload fields ---
        if not message:
            message = first.get('popup_message')
            
        if not message and 'qte_context' in first:
            message = first.get('qte_context', {}).get('ui_prompt_message')
            
        if not message and 'on_close_emit_ui_events' in first:
            for sub_event in first.get('on_close_emit_ui_events', []):
                if 'qte_context' in sub_event:
                    message = sub_event.get('qte_context', {}).get('ui_prompt_message')
                    break

        # Guarantee it's a string before slicing so the logger never crashes
        safe_msg = str(message) if message else "No message provided"
        # ------------------------------------------------

        self.logger.info(f"Sequential consequence: Showing popup '{title}' with message '{safe_msg[:80]}...'")
        
        try:
            popup = InfoPopup(title=title, message=safe_msg)
            self._bind_popup_defers(popup, first)

            def _continue(*_):
                try:
                    if rest:
                        self.logger.debug("Continuing with remaining consequences after popup.")
                        self._handle_consequences_sequentially(rest)
                except Exception as e:
                    self.logger.error(f"Error in popup continuation: {e}", exc_info=True)

            popup.bind(on_dismiss=_continue)
            popup.open()
            
        except Exception as e:
            self.logger.error(f"Error showing popup: {e}", exc_info=True)
            if rest:
                self._handle_consequences_sequentially(rest)

    def _seq_handle_start_qte(self, first: dict, rest: list):
        """Passes QTE payloads to the engine. Halts sequence chain (relies on QTE resolve to continue)."""
        self.logger.info(f"Sequential consequence: Starting QTE '{first.get('qte_type')}' with context {first.get('qte_context', {})}")
        
        if self.game_logic and self.game_logic.qte_engine:
            try:
                qte_ctx = first.get("qte_context", {})
                
                # Inject the hazard ID so chained QTEs know who to transition!
                hid = first.get("hazard_id")
                if hid and "qte_source_hazard_id" not in qte_ctx:
                    qte_ctx["qte_source_hazard_id"] = hid
                    
                self.game_logic.qte_engine.start_qte(first.get("qte_type"), qte_ctx)
                self.logger.debug("QTE started successfully. Remaining consequences will be handled by QTE resolution.")
                return  # DO NOT call rest here; the QTE resolution takes over the chain
            except Exception as e:
                self.logger.error(f"Seq: failed to start QTE: {e}", exc_info=True)
        else:
            self.logger.warning("QTE engine not available, cannot start QTE.")
            
        # Only continue if the QTE failed to start
        if rest:
            self.logger.debug("Continuing with remaining consequences after failed QTE start.")
            self._handle_consequences_sequentially(rest)

    def _seq_handle_hazard_state_change(self, first: dict, rest: list):
        """Forces a hazard state shift and dynamically prepends the resulting consequences to the queue."""
        hazard_id = first.get('hazard_id')
        target_state = first.get('target_state')
        self.logger.info(f"Sequential consequence: Changing hazard state for '{hazard_id}' to '{target_state}'")
        
        if self.game_logic and getattr(self.game_logic, 'hazard_engine', None):
            try:
                res = self.game_logic.hazard_engine.set_hazard_state(hazard_id, target_state)
                nxt = (res or {}).get("consequences", [])
                self.logger.debug(f"Hazard state changed. Next consequences: {nxt!r}")
                
                # Prepend the new consequences to the remaining queue
                self._handle_consequences_sequentially((nxt or []) + rest)
                return
            except Exception as e:
                self.logger.error(f"Seq: hazard_state_change error: {e}", exc_info=True)
        else:
            self.logger.warning("Hazard engine not available, cannot change hazard state.")
            
        if rest:
            self.logger.debug("Continuing with remaining consequences after failed hazard state change.")
            self._handle_consequences_sequentially(rest)

    def _seq_handle_fallback(self, first: dict, rest: list):
        """Passes unsupported generic consequences to GameLogic, then continues the chain."""
        if hasattr(self.game_logic, 'handle_hazard_consequence'):
            try:
                self.logger.info(f"Sequential consequence: Passing to game_logic.handle_hazard_consequence: {first!r}")
                self.game_logic.handle_hazard_consequence(first)
            except Exception as e:
                self.logger.error(f"Seq: handle_hazard_consequence error: {e}", exc_info=True)
        else:
            self.logger.warning("game_logic.handle_hazard_consequence not available.")
            
        if rest:
            self.logger.debug("Continuing with remaining consequences after fallback handler.")
            self._handle_consequences_sequentially(rest)

    def _populate_main_action_buttons(self):
        """Use context-sensitive dock instead of flat button grid."""
        container = self._get_widget('main_actions')
        if not container:
            return
        
        container.clear_widgets()
        
        # Replace with context dock
        dock = ContextDockWidget()
        dock.on_command = self.on_main_action_press  # Wire to existing handler
        dock.update(self.game_logic)  # Initial population
        
        container.add_widget(dock)
        
        # Store reference for updates
        self.context_dock = dock

    def on_main_action_press(self, verb: str):
        self.logger.debug(f"GameScreen: on_main_action_press called with verb '{verb}'")
        if not self.game_logic:
            self.logger.error("GameScreen: game_logic missing on main action press.")
            return

        # Special verbs handling (Main Menu, Save, Load)
        if verb == 'main menu':
            self.go_to_screen('title', direction='left')
            return
        elif verb == 'save':
            # --- THE FIX: Use Kivy screen names, not Python method names ---
            self.go_to_screen('save_game', direction='right')
            return
        elif verb == 'load':
            self.go_to_screen('load_game', direction='right')
            return

        # Verbs that do NOT require a target
        no_target_verbs = {'inventory', 'inv', 'wait', 'rest', 'help', 'roster', 'list', 'map', 'save', 'load'}
        if verb in no_target_verbs:
            self.on_submit_command(command_override=verb)
            return

        ctx = self._get_widget('contextual_actions')
        if not ctx or not hasattr(ctx, 'populate'):
            self.logger.error("GameScreen: contextual_actions missing.")
            return

        # Special handling for 'use'
        if verb == "use":
            ctx.populate_contextual_targets(self.game_logic, "use")
            return

        # --- SHARED HELPER FOR BUTTON CREATION ---
        def create_context_button(text, command):
            btn = Factory.TerminalButton(
                text=text,
                size_hint_y=None,
                height=dp(48),    # Taller fixed height to fit 2 lines
                shorten=False,    # Disable "..." truncation
                font_size=dp(13), # Slightly smaller font for density
                halign='center',
                valign='middle'
            )
            # Bind text_size to width (minus padding) to force wrapping
            btn.bind(width=lambda instance, value: setattr(instance, 'text_size', (value - dp(10), instance.height)))
            
            if command:
                btn.bind(on_release=lambda _i, c=command: self.process_and_clear(c))
            return btn

        # NEW: special handling for 'unlock' and 'force'
        if verb in ('unlock', 'force'):
            pairs = self._collect_unlock_force_targets(verb)
            buttons = []
            for btn_text, command in pairs:
                try:
                    buttons.append(create_context_button(btn_text, command))
                except Exception as e:
                    self.logger.error(f"GameScreen: Error adding {verb} button: {e}", exc_info=True)
            
            # Back Button
            back = create_context_button("< Back", None)
            back.bind(on_release=lambda *_: ctx.populate([]))
            buttons.append(back)
            
            ctx.populate(buttons)
            return

        # Default path (Move, Examine, etc.)
        try:
            targets = self.game_logic.get_available_targets(verb) or []
        except Exception as e:
            self.logger.error(f"GameScreen: Error getting targets: {e}", exc_info=True)
            targets = []

        buttons = []
        for target in targets:
            command = f"{verb} {target}"
            display_text = target.replace('_', ' ').title()
            try:
                buttons.append(create_context_button(display_text, command))
            except Exception as e:
                self.logger.error(f"GameScreen: Error adding button: {e}", exc_info=True)
        
        # Back Button
        back = create_context_button("< Back", None)
        back.bind(on_release=lambda *_: ctx.populate([]))
        buttons.append(back)
        
        ctx.populate(buttons)
        self.logger.debug(f"GameScreen: Contextual actions populated for verb '{verb}'")

    def process_and_clear(self, command: str):
        self.logger.debug(f"GameScreen: process_and_clear called with command '{command}'")
        self.on_submit_command(command_override=command)
        self.clear_contextual_actions()

    def on_submit_command(self, instance=None, command_override: str = None):
        self.logger.debug(f"GameScreen: on_submit_command called. instance={instance}, command_override={command_override}")
        if not self.game_logic:
            self.logger.error("GameScreen: game_logic is None on submit.")
            return

        ai = self._get_widget('action_input')
        out = self._get_widget('output_panel')
        text = command_override or (ai.text_input.text.strip() if ai and hasattr(ai, 'text_input') else '')
        
        if not text:
            return

        # --- ENHANCED CHAOS CRAWLER INTERCEPT ---
        if text.lower().startswith("crawl"):
            parts = text.strip().split()
            
            # 1. Handle Abort
            if len(parts) >= 2 and parts[1].lower() == "stop":
                crawler = getattr(self, '_crawler', None)
                if crawler and crawler.is_running:
                    crawler.stop("Manual stop via command.")
                    if out: out.append_text("[color=ffaa00]Crawler stopped.[/color]")
                if ai: ai.text_input.text = ""
                return

            # 2. Parse Arguments: crawl <turns> [fast] [<runs>]
            turns = 500
            fast  = False
            runs  = 1

            try:
                # Parse turns (first arg after 'crawl')
                if len(parts) >= 2 and parts[1].isdigit():
                    turns = int(parts[1])
                
                # Parse 'fast' and 'runs'
                if 'fast' in [p.lower() for p in parts]:
                    fast = True
                    fast_idx = parts.index('fast')
                    # Look for runs count immediately following the word 'fast'
                    if fast_idx + 1 < len(parts) and parts[fast_idx + 1].isdigit():
                        runs = int(parts[fast_idx + 1])
                elif len(parts) >= 3 and parts[2].isdigit():
                    # Fallback for "crawl 500 5" (no 'fast' keyword)
                    runs = int(parts[2])
            except (ValueError, IndexError):
                pass

            # 3. Initialization & God Mode
            # Ensure sys.path is correct for dynamic loading
            import sys
            import os
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            if root_dir not in sys.path:
                sys.path.insert(0, root_dir)

            from chaos_crawler import ChaosCrawler
            
            # Grant God Mode and start the session
            self.game_logic.player['turns_left'] = 99999
            self._crawler = ChaosCrawler(self)
            self._crawler.start(turns=turns, fast=fast, runs=runs)

            # 4. UI Feedback
            speed_label = "FAST" if fast else "NORMAL"
            if out:
                out.append_text(
                    f"[color=00ff00]Crawler started: {runs} run(s) of {turns} turns ({speed_label}).[/color]\n"
                    f"[color=aaaaaa]Logging to: {self._crawler.log_path}[/color]"
                )
            if ai: ai.text_input.text = ""
            return
        # --- END CHAOS CRAWLER ---
            
            self._crawler = ChaosCrawler(self)
            self.game_logic.player['turns_left'] = 99999
            self._crawler.start(turns=turns, fast=fast)
 
            speed_label = "fast" if fast else "normal"

        # If a QTE is active, route input to QTE engine instead of normal commands
        if self.game_logic.player.get('qte_active') and getattr(self.game_logic, 'qte_engine', None) and self.game_logic.qte_engine.active_qte:
            self.logger.info("GameScreen: QTE active, routing input to QTE engine.")
            if out and hasattr(out, 'append_text'):
                out.append_text(f"> {text}")
            # Clear box for next input
            if ai and hasattr(ai, 'text_input'):
                ai.text_input.text = ""

            try:
                result = self.game_logic.qte_engine.handle_qte_input(text)
                self.logger.debug(f"GameScreen: QTE engine result: {result}")
            except Exception as e:
                self.logger.error(f"GameScreen: Error handling QTE input: {e}", exc_info=True)
                result = None
            # If QTE resolved, route through _handle_qte_resolution for proper effects
            if isinstance(result, dict):
                qte_response = self.game_logic._handle_qte_resolution(result)
                for m in qte_response.get('messages', []):
                    if out and hasattr(out, 'append_text'):
                        out.append_text(m)
                self.update_all_ui_elements(qte_response.get('game_state', {}))
                self._handle_ui_events(qte_response.get('ui_events', []) + self.game_logic.get_ui_events())
                self.in_qte_mode = False
                if ai and hasattr(ai, 'text_input'):
                    ai.text_input.hint_text = ""
                # Force-dismiss popup since QTE is resolved
                if self.active_qte_popup and not getattr(self.active_qte_popup, 'is_dismissed', False):
                    self.active_qte_popup.dismiss()
                    self.active_qte_popup = None
            return

        # Normal command flow
        command = text
        if ai and hasattr(ai, 'text_input'):
            ai.text_input.text = ""
        if out and hasattr(out, 'append_text'):
            out.append_text(f"> {command}")

        try:
            response = self.game_logic.process_player_input(command)
            self.logger.debug(f"GameScreen: process_player_input response: {response}")
        except Exception as e:
            self.logger.error(f"Engine error: {e}", exc_info=True)
            response = {"messages": [f"[color=ff4444]Engine error: {e}[/color]"], "game_state": self.game_logic.get_current_game_state(), "ui_events": []}

        self.update_all_ui_elements(response.get('game_state', {}))
        for m in response.get('messages', []):
            if out and hasattr(out, 'append_text'):
                out.append_text(m)
        self._handle_ui_events(response.get('ui_events', []))

        # Drain any late-queued events (ensures immediate popup without needing another action)
        pending = getattr(self.game_logic, "get_ui_events", lambda: [])()
        if pending:
            self._handle_ui_events(pending)

        if hasattr(self, 'context_dock'):
            self.context_dock.update(self.game_logic)

        self.logger.info(f"GameScreen: Finished processing command '{command}'")

    def clear_contextual_actions(self, *args):
        self.logger.debug("clear_contextual_actions called")
        ctx = self._get_widget('contextual_actions')
        if ctx and hasattr(ctx, 'populate'):
            self.logger.debug("Contextual actions widget found, clearing actions")
            ctx.populate([])
        else:
            self.logger.warning("Contextual actions widget not found or missing 'populate' method")

    def on_leave(self, *args):
        self.logger.info("GameScreen.on_leave: Cleaning up UI state...")
        
        # 1. Clean up QTEs
        if hasattr(self, '_handle_destroy_qte_popup'):
            self._handle_destroy_qte_popup(None)

        # 2. Nuke standard popups and reset the lock flags
        if hasattr(self, 'active_info_popup') and self.active_info_popup:
            try:
                self.active_info_popup._suppress_on_dismiss = True
                self.active_info_popup.dismiss()
            except Exception:
                pass
            self.active_info_popup = None

        self._popup_is_active = False
        self._pending_popup_continuation = None

        # 3. Clear any pending UI event queues so old messages don't bleed over
        if hasattr(self, 'ui_event_queue'):
            self.ui_event_queue.clear()
            
        if hasattr(self, 'pending_events'):
            self.pending_events.clear()

        # --- THE MISSING FIX: Restore the transition lock resets! ---
        if hasattr(self, 'transition_in_progress'):
            self.transition_in_progress = False
        if hasattr(self, '_transition_in_progress'):
            self._transition_in_progress = False
        self.logger.info("GameScreen.on_leave: Transition state reset.")
        # ------------------------------------------------------------

    def _handle_game_loaded(self, event):
        """Handle game loaded event by refreshing the entire UI."""
        self.logger.info("_handle_game_loaded: Refreshing UI after loading game")
        
        # Clear and update output panel with room description
        room_desc = event.get('room_description', '')
        if room_desc and self.output_panel and hasattr(self.output_panel, 'append_text'):
            self.output_panel.append_text(room_desc, clear_previous=True)
        
        # Force full UI refresh
        if self.game_logic:
            self.update_all_ui_elements(self.game_logic.get_current_game_state())

    def _handle_schedule_transit(self, event: dict):
        """
        Schedule the elevator arrival callback.
        """
        duration = event.get("duration", 3.0)
        self.logger.info(f"Scheduling elevator arrival in {duration} seconds.")
        
        # Optional: Show a non-blocking notification or just let the output panel text persist
        # We could disable input here if we wanted, but let's trust the game state flags
        
        Clock.schedule_once(self._on_elevator_timer_complete, duration)

    def _on_elevator_timer_complete(self, dt):
        """
        Callback when the elevator timer finishes. 
        Triggers logic in GameLogic to check hazards and arrive.
        """
        self.logger.info("Elevator timer complete. Calling logic.")
        if self.game_logic:
            try:
                # Call the new logic method
                response = self.game_logic.process_elevator_arrival()
                
                # Update UI with the result (e.g. "Ding!", new room desc, or QTE popup)
                if response:
                    out = self._get_widget('output_panel')
                    for m in response.get('messages', []):
                        if out and hasattr(out, 'append_text'):
                            out.append_text(m)
                    
                    self.update_all_ui_elements(response.get('game_state', {}))
                    
                    # --- THE FIX: Drain the engine's UI Event Queue! ---
                    # If the hazard escalated, the QTE is sitting in the engine's queue.
                    # We MUST extract it manually since this is a Clock callback.
                    all_events = response.get('ui_events', [])
                    if hasattr(self.game_logic, 'get_ui_events'):
                        all_events.extend(self.game_logic.get_ui_events())
                        
                    self._handle_ui_events(all_events)
                    
            except Exception as e:
                self.logger.error(f"Error in _on_elevator_timer_complete: {e}", exc_info=True)

    def _handle_destroy_info_popup(self, event=None):
        self.logger.info("_handle_destroy_info_popup: Attempting to close info popup.")
        if hasattr(self, 'active_info_popup') and self.active_info_popup:
            try:
                popup = self.active_info_popup
                self.active_info_popup = None
                popup._suppress_on_dismiss = True  # signal handlers to no-op
                popup.dismiss()
            except Exception as e:
                self.logger.warning(f"Failed to dismiss info popup: {e}")
        self._pending_popup_continuation = None
        self._popup_is_active = False

    def _on_inventory_item_tap(self, item_key: str):
        """Called when player taps an item in the side panel. Opens examine command."""
        display_name = item_key.replace('_', ' ')
        self.on_submit_command(command_override=f"examine {display_name}")