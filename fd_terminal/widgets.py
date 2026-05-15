# fd_terminal/widgets.py
"""
The Canvas of Souls.
This scroll defines the logical controllers for all custom UI widgets.
The visual composition is now handled entirely by the Loom (finaldestination.kv).
"""
import logging
import random
from sys import platform
import time
import math
from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import Color, Line
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty, NumericProperty, StringProperty
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.textinput import TextInput
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.app import App
from kivy.factory import Factory
from kivy.graphics import Color, Line, Ellipse, Rectangle
from .utils import glitch_text

class StatusDisplayWidget(BoxLayout):
    """Displays the player's core stats. Layout defined in KV."""
    hp_label = ObjectProperty(None)
    fear_label = ObjectProperty(None)
    score_label = ObjectProperty(None)
    bg_texture = StringProperty('')  # Default to empty string (no texture)

    def update(self, player_state: dict):
        """Updates the labels with new player state, including fear."""
        # --- FIX: Catch dead WeakProxy references ---
        try:
            if not all([self.hp_label, self.fear_label, self.score_label]):
                return
        except ReferenceError:
            return
        # --------------------------------------------

        # Get app and load thresholds from JSON config
        app = App.get_running_app()
        constants = app.resource_manager.get_data('constants', {})
        thresholds = constants.get('UI_THRESHOLDS', {
            'FEAR_CRITICAL': 0.8,
            'FEAR_WARNING': 0.6,
            'FEAR_CAUTION': 0.4,
            'HP_CRITICAL': 0.25,
            'HP_WARNING': 0.5
        })

        # Extract player stats
        hp = player_state.get('hp', '--')
        fear = player_state.get('fear', 0.0)
        score = player_state.get('score', '--')
        max_hp = player_state.get('max_hp', 30)

        # Safe numeric ratio for HP-based logic
        hp_ratio = None
        if isinstance(hp, (int, float)):
            try:
                max_hp_value = float(max_hp)
                if max_hp_value > 0:
                    hp_ratio = hp / max_hp_value
            except (TypeError, ValueError, ZeroDivisionError):
                hp_ratio = None

        # Fear Color Logic (using thresholds)
        fear_color = '00ff00'  # Green (default)
        if fear >= thresholds.get('FEAR_CRITICAL', 0.8):
            fear_color = 'ff0000'
        elif fear >= thresholds.get('FEAR_WARNING', 0.6):
            fear_color = 'ff6600'
        elif fear >= thresholds.get('FEAR_CAUTION', 0.4):
            fear_color = 'ffaa00'

        # HP Color Logic (using thresholds)
        hp_color = 'ffffff'
        if hp_ratio is not None:
            hp_critical = thresholds.get('HP_CRITICAL', 0.25)
            hp_warning = thresholds.get('HP_WARNING', 0.5)
            hp_color = 'ff0000' if hp_ratio <= hp_critical else 'ffaa00' if hp_ratio <= hp_warning else '00ff00'

        self.hp_label.text = f"[color={hp_color}]HP: {hp}[/color]"
        self.fear_label.text = f"[color={fear_color}]Fear: {fear:.2f}[/color]"
        self.score_label.text = f"[color=cccccc]Score: {score}[/color]"

        # Corruption Logic (using thresholds)
        if fear >= thresholds.get('FEAR_CRITICAL', 0.8) or (
            hp_ratio is not None and hp_ratio < thresholds.get('HP_CRITICAL', 0.25)
        ):
            self.bg_texture = 'assets/ui/panel_shattered.png'
        elif fear >= thresholds.get('FEAR_WARNING', 0.6):
            self.bg_texture = 'assets/ui/panel_cracked.png'
        else:
            self.bg_texture = ''

class OutputPanelWidget(BoxLayout):
    """Controller for the main text output area."""
    output_label = ObjectProperty(None)
    output_scroll_view = ObjectProperty(None)
    MAX_LOG_LENGTH = 15000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def append_text(self, text_to_append, clear_previous=False):
        # --- FIX: Catch dead WeakProxy references ---
        try:
            if not self.output_label: 
                return
        except ReferenceError:
            return
        # --------------------------------------------
        
        # --- PATCH: Apply Glitch based on Fear ---
        app = App.get_running_app()
        final_text = text_to_append
        
        if app and getattr(app, 'game_logic', None):
            fear = app.game_logic.player.get('fear', 0.0)
            # Only glitch if fear is significant (> 0.4)
            if fear > 0.4:
                # We apply the utility function here
                # Our utility handles tags, so pass raw.
                final_text = glitch_text(text_to_append, fear)
        # -----------------------------------------

        processed_text = self._ensure_color_tags_closed(final_text)
        
        if clear_previous:
            self.output_label.text = processed_text
        else:
            self.output_label.text += f"\n\n{processed_text}"
            
        # --- THE FIX: Culling the History ---
        # If the text creates a texture too large, the screen goes black.
        # We limit the "memory" of the terminal to the last ~5000 characters.
        max_length = 5000
        if len(self.output_label.text) > max_length:
            # Find a safe place to cut (a newline) so we don't slice a word in half
            cut_index = self.output_label.text.find('\n', len(self.output_label.text) - max_length)
            if cut_index != -1:
                self.output_label.text = "[i]...history truncated...[/i]\n" + self.output_label.text[cut_index+1:]
        # ------------------------------------

        # Auto-scroll to bottom
        if self.output_scroll_view:
            Clock.schedule_once(lambda dt: setattr(self.output_scroll_view, 'scroll_y', 0), 0.1)

    def _ensure_color_tags_closed(self, text):
        open_tags = text.count('[color=') - text.count('[/color]')
        if open_tags > 0:
            text += '[/color]' * open_tags
        return text

class MapDisplayWidget(BoxLayout):
    """Controller for the ASCII map display."""
    map_label = ObjectProperty(None)
    def update(self, map_string: str):
        # --- FIX: Catch dead WeakProxy references ---
        try:
            if self.map_label:
                self.map_label.text = map_string
        except ReferenceError:
            pass
        # --------------------------------------------

class InventoryDisplayWidget(BoxLayout):
    """Controller for the always-on inventory side panel."""
    inventory_label = ObjectProperty(None)
    
    def update(self, inventory_data: list, on_item_tap=None):
        """
        Update inventory display. If on_item_tap is provided, items become
        tappable buttons inside the ScrollView.
        """
        # Find the ScrollView reliably (it's always a direct child of self)
        scroll = None
        for child in self.children:
            if isinstance(child, ScrollView):
                scroll = child
                break
        
        if not scroll:
            return
        
        # Clear everything inside the ScrollView
        scroll.clear_widgets()
        
        if not inventory_data:
            # Rebuild the label for empty state
            lbl = Label(
                text="[color=666666][i]Empty[/i][/color]",
                markup=True,
                font_name='RobotoMono',
                font_size=dp(13),
                halign='left',
                valign='top',
                size_hint_y=None,
            )
            lbl.bind(width=lambda i, v: setattr(i, 'text_size', (v, None)))
            lbl.bind(texture_size=lambda i, v: setattr(i, 'height', v[1]))
            scroll.add_widget(lbl)
            return

        if on_item_tap:
            # Button mode: scrollable list of tappable items
            container = BoxLayout(
                orientation='vertical',
                size_hint_y=None,
                spacing=dp(2),
                padding=[dp(2), 0],
            )
            container.bind(minimum_height=container.setter('height'))
            
            for item in inventory_data:
                if isinstance(item, str):
                    item_key = item
                    name = item.replace('_', ' ')
                elif isinstance(item, dict):
                    item_key = item.get('id') or item.get('key') or item.get('name', '?')
                    name = item.get('name') or item_key.replace('_', ' ')
                else:
                    continue
                
                btn = Button(
                    text=f"  {name}",
                    markup=True,
                    font_name='RobotoMono',
                    font_size=dp(11),
                    size_hint_y=None,
                    height=dp(26),
                    halign='left',
                    valign='middle',
                    background_color=(0.1, 0.1, 0.1, 1),
                    color=(0.1, 0.8, 0.1, 1),
                )
                btn.bind(width=lambda i, v: setattr(i, 'text_size', (v - dp(4), None)))
                btn.bind(on_release=lambda _, k=item_key: on_item_tap(k))
                container.add_widget(btn)
            
            scroll.add_widget(container)
        else:
            # Plain text mode
            display_lines = []
            for item in inventory_data:
                name = "Unknown"
                if isinstance(item, str):
                    name = item.replace('_', ' ').title()
                elif isinstance(item, dict):
                    name = item.get('name') or item.get('display_name') or item.get('id') or "Unknown Item"
                display_lines.append(f"• {name}")
            
            lbl = Label(
                text="\n".join(display_lines),
                markup=True,
                font_name='RobotoMono',
                font_size=dp(13),
                halign='left',
                valign='top',
                size_hint_y=None,
            )
            lbl.bind(width=lambda i, v: setattr(i, 'text_size', (v, None)))
            lbl.bind(texture_size=lambda i, v: setattr(i, 'height', v[1]))
            scroll.add_widget(lbl)

class ActionInputWidget(BoxLayout):
    """Controller for the text input bar."""
    text_input = ObjectProperty(None)
    submit_button = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)
        # Removed: self._original_softinput_mode

    def on_text_input(self, instance, value):
        """
        Called automatically by Kivy when the 'text_input' property is set via KV.
        """
        pass

    def _on_submit(self):
        """Called by KV when Enter is pressed or Submit clicked."""
        # --- FIX: Catch dead WeakProxy references ---
        try:
            if not self.text_input: return
        except ReferenceError:
            return
        # The GameScreen binds to text_input.on_text_validate, so we just ensure focus logic here
        Clock.schedule_once(lambda dt: self._refocus_input(), 0.1)

    def _refocus_input(self):
        if self.text_input:
            # PATCH: Don't force refocus on mobile, or keyboard will never close
            # allowing the user to see the output panel updates.
            if platform not in ('android', 'ios'):
                self.text_input.focus = True
            
            # Clear QTE prompt artifacts if any
            if getattr(self, 'qte_prompt_active', False):
                self.text_input.text = ''
                self.qte_prompt_active = False

class CompassWidget(FloatLayout):
    """
    A dynamic HUD element displaying available exits and current floor.
    Layout: 
    - Left (15%): Floor Indicators (2, 1, B1)
    - Center (70%): Compass Grid
    - Right (15%): Vertical Arrows (Up/Down)
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.direction_labels = {}
        self.floor_labels = {}
        
        self._init_ui()
        
        # Bind drawing to resize events so lines stay sharp
        self.bind(pos=self._update_canvas, size=self._update_canvas)

    def _init_ui(self):
        # --- 1. FLOOR INDICATORS (LEFT SIDE) ---
        floors = [
            (2,  '2',  (0.15, 0.7)),
            (1,  '1',  (0.15, 0.5)),
            (-1, 'B1', (0.15, 0.3))
        ]
        
        for val, text, pos in floors:
            lbl = Label(
                text=text,
                font_name='RobotoMonoBold',
                font_size='16sp',
                pos_hint={'center_x': pos[0], 'center_y': pos[1]},
                size_hint=(None, None),  # <--- Added
                size=('40dp', '40dp'),   # <--- Added
                color=(0.2, 0.2, 0.2, 1)
            )
            self.add_widget(lbl)
            self.floor_labels[val] = lbl

        # --- 2. COMPASS GRID (CENTER) ---
        compass_positions = {
            'north':     (0.50, 0.8), 'south':     (0.50, 0.2),
            'east':      (0.70, 0.5), 'west':      (0.30, 0.5),
            'northeast': (0.65, 0.7), 'northwest': (0.35, 0.7),
            'southeast': (0.65, 0.3), 'southwest': (0.35, 0.3),
        }
        compass_symbols = {
            'north': 'N', 'south': 'S', 'east': 'E', 'west': 'W',
            'northeast': 'NE', 'northwest': 'NW', 'southeast': 'SE', 'southwest': 'SW',
        }

        for direction, pos in compass_positions.items():
            lbl = Label(
                text=compass_symbols[direction],
                font_name='RobotoMonoBold',
                font_size='18sp',
                pos_hint={'center_x': pos[0], 'center_y': pos[1]},
                size_hint=(None, None),  # <--- Added
                size=('40dp', '40dp'),   # <--- Added
                color=(0.2, 0.2, 0.2, 1)
            )
            self.add_widget(lbl)
            self.direction_labels[direction] = lbl

        # Center "Player" Indicator
        center_lbl = Label(
            text='[P]', 
            font_name='RobotoMonoBold',
            font_size='20sp',
            color=(0, 1, 0, 1),
            pos_hint={'center_x': 0.50, 'center_y': 0.5},
            size_hint=(None, None),      # <--- Added
            size=('40dp', '40dp')        # <--- Added
        )
        self.add_widget(center_lbl)

        # --- 3. VERTICAL ARROWS (RIGHT SIDE) ---
        vertical_positions = {
            'up':   (0.88, 0.7), # Slightly adjusted right
            'down': (0.88, 0.3)
        }
        
        # FIX: Use text instead of glyphs to guarantee rendering in RobotoMono
        vertical_symbols = {'up': 'UP', 'down': 'DN'}

        for direction, pos in vertical_positions.items():
            lbl = Label(
                text=vertical_symbols[direction],
                font_name='RobotoMonoBold',
                font_size='14sp', # Slightly smaller to fit 2 letters
                pos_hint={'center_x': pos[0], 'center_y': pos[1]},
                size_hint=(None, None),  # <--- Added
                size=('40dp', '40dp'),   # <--- Added
                color=(0.2, 0.2, 0.2, 1)
            )
            self.add_widget(lbl)
            self.direction_labels[direction] = lbl

    def _update_canvas(self, *args):
        """Redraws separator lines when the widget resizes."""
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.2, 0.2, 0.2, 1)
            
            # Left Line (Separates Floors from Compass)
            # x ~ 25% width
            Line(points=[self.x + self.width * 0.25, self.y + dp(10), 
                         self.x + self.width * 0.25, self.top - dp(10)], width=1)
            
            # Right Line (Separates Compass from Vertical Arrows)
            # x ~ 75% width
            Line(points=[self.x + self.width * 0.75, self.y + dp(10), 
                         self.x + self.width * 0.75, self.top - dp(10)], width=1)

    def update(self, room_data):
        """Updates compass directions AND floor indicators."""
        if not room_data: return

        # --- 1. UPDATE FLOORS ---
        current_floor = room_data.get('floor') # integer: 1, 2, -1
        
        # Dim all floors
        for lbl in self.floor_labels.values():
            lbl.color = (0.2, 0.2, 0.2, 0.5)
            lbl.bold = False
            
        # Highlight current
        if current_floor in self.floor_labels:
            lbl = self.floor_labels[current_floor]
            lbl.color = (0, 1, 0, 1) # Green
            lbl.bold = True

        # --- 2. UPDATE DIRECTIONS ---
        exits = room_data.get('exits', {})
        
        # Colors (RGBA)
        COLOR_OPEN = (0, 1, 0, 1)
        COLOR_DIM = (0.2, 0.2, 0.2, 0.5)

        aliases = {
            'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
            'ne': 'northeast', 'nw': 'northwest', 'se': 'southeast', 'sw': 'southwest',
            'u': 'up', 'upstairs': 'up', 'climb': 'up', 'top': 'up',
            'd': 'down', 'downstairs': 'down', 'basement': 'down'
        }

        # Dim all directions
        for lbl in self.direction_labels.values():
            lbl.color = COLOR_DIM
            lbl.bold = False

        # Light up active exits
        for direction in exits.keys():
            clean_dir = str(direction).lower().strip()
            canonical = aliases.get(clean_dir, clean_dir)

            if canonical in self.direction_labels:
                lbl = self.direction_labels[canonical]
                lbl.color = COLOR_OPEN
                lbl.bold = True

    def set_locked_status(self, direction, is_locked):
        """Overrides color for locked doors."""
        aliases = {
            'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
            'ne': 'northeast', 'nw': 'northwest', 'se': 'southeast', 'sw': 'southwest',
            'u': 'up', 'upstairs': 'up', 'd': 'down', 'downstairs': 'down'
        }
        canonical = aliases.get(direction.lower(), direction.lower())
        
        if canonical in self.direction_labels and is_locked:
            self.direction_labels[canonical].color = (1, 0, 0, 1) # Red

class InfoPopup(Popup):
    """A generic info popup. Layout defined in KV."""
    message_label = ObjectProperty(None)
    
    def __init__(self, title, message, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        # We set the message text on the label defined in KV
        if self.message_label:
            self.message_label.text = message
        
        # Dynamic sizing logic
        Clock.schedule_once(self._adjust_height, 0)

    def _adjust_height(self, *args):
        if not self.message_label: return
        # Force texture recalculation
        self.message_label.texture_update()
        content_h = self.message_label.texture_size[1]
        # title bar (~dp(50)) + padding (~dp(40)) + button (~dp(55)) + image area
        chrome_h = dp(145)
        desired = content_h + chrome_h
        max_h = Window.height * 0.85
        self.height = min(desired, max_h)

    def _on_close(self):
        self.dismiss()

class InventoryPopup(Popup):
    """
    Full-screen inventory browser.
    Shows character class header, item cards with all available flavor,
    and inline action buttons (Examine, Use, etc.) that fire game commands.
    """

    # ── Colours (mirror finaldestination.kv theme constants) ──────────────────
    C_BG        = (0.05, 0.05, 0.05, 1)
    C_PANEL     = (0.08, 0.08, 0.08, 1)
    C_BORDER    = (0.3,  0.3,  0.3,  1)
    C_GREEN     = (0.1,  0.8,  0.1,  1)   # term_fg
    C_ACCENT    = (1.0,  0.6,  0.0,  1)   # term_accent
    C_DIM       = (0.45, 0.45, 0.45, 1)
    C_WHITE     = (1.0,  1.0,  1.0,  1)
    C_RED       = (0.9,  0.2,  0.2,  1)

    # Item-type badge colours
    TYPE_COLORS = {
        'key':              (0.9,  0.7,  0.1,  1),
        'medical_supply':   (0.2,  0.8,  0.4,  1),
        'tool':             (0.3,  0.6,  1.0,  1),
        'evidence':         (0.8,  0.3,  0.8,  1),
        'weapon':           (0.9,  0.2,  0.2,  1),
        'document':         (0.7,  0.7,  0.4,  1),
        'electronic_evidence': (0.4, 0.7, 1.0, 1),
    }

    def __init__(self, game_logic, command_callback, **kwargs):
        """
        game_logic      – GameLogic instance (read-only here)
        command_callback – callable(str) that submits a game command
        """
        kwargs.setdefault('title', 'INVENTORY')
        kwargs.setdefault('size_hint', (0.95, 0.92))
        kwargs.setdefault('auto_dismiss', True)
        super().__init__(**kwargs)

        self.logger = logging.getLogger(self.__class__.__name__)
        self.game_logic = game_logic
        self._cmd = command_callback

        # Style the Popup chrome to match terminal theme
        self.title_color       = self.C_GREEN
        self.title_align       = 'center'
        self.background_color  = self.C_BG
        self.separator_color   = self.C_GREEN

        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Build the full popup layout."""
        gl = self.game_logic
        player        = gl.player
        items_master  = gl.resource_manager.get_data('items', {}) or {}
        char_classes  = gl.resource_manager.get_data('character_classes', {}) or {}
        char_class    = player.get('character_class', 'Unknown')
        class_data    = char_classes.get(char_class, {})
        inventory     = player.get('inventory', [])

        root = BoxLayout(orientation='vertical', padding=dp(6), spacing=dp(6))

        # ── 1. Character header ──────────────────────────────────────────────
        root.add_widget(self._build_header(player, char_class, class_data))

        # ── 2. Divider ───────────────────────────────────────────────────────
        root.add_widget(self._make_divider())

        # ── 3. Item list (scrollable) ────────────────────────────────────────
        if not inventory:
            empty = Label(
                text='[color=666666][i]Your pockets are empty.[/i][/color]',
                markup=True,
                font_name='RobotoMono',
                font_size=dp(14),
                size_hint_y=None,
                height=dp(50),
                halign='center',
            )
            root.add_widget(empty)
        else:
            root.add_widget(self._build_item_list(inventory, items_master))

        # ── 4. Close button ──────────────────────────────────────────────────
        close_btn = self._make_terminal_button(
            'CLOSE  [i](or press Back)[/i]', height=dp(42)
        )
        close_btn.bind(on_release=lambda *_: self.dismiss())
        root.add_widget(close_btn)

        self.add_widget(root)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self, player, char_class, class_data):
        """Two-row header: name/class on left, stats on right, affinities below."""
        affinities  = class_data.get('affinities', {})
        item_affs   = affinities.get('item_types', [])
        skill_affs  = affinities.get('skilled_actions', [])
        description = class_data.get('description', '')

        container = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            height=dp(90),
            spacing=dp(4),
        )
        self._draw_panel_bg(container)

        # Top row: class name + HP/turns
        top = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(32))

        class_lbl = Label(
            text=f'[b][color=1acc1a]{char_class.upper()}[/color][/b]',
            markup=True,
            font_name='RobotoMonoBold',
            font_size=dp(16),
            halign='left',
            valign='middle',
            size_hint_x=0.6,
        )
        class_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        top.add_widget(class_lbl)

        hp      = player.get('hp', '?')
        max_hp  = player.get('max_hp', '?')
        turns   = player.get('turns_left', '?')
        fear    = player.get('fear', 0.0)
        fear_pct = int(fear * 100)

        hp_color    = 'ff4444' if isinstance(hp, int) and isinstance(max_hp, int) and hp / max_hp < 0.3 else '00cc00'
        fear_color  = 'ff4444' if fear > 0.7 else 'ff8800' if fear > 0.4 else '00cc00'

        stats_lbl = Label(
            text=(
                f'[color={hp_color}]HP {hp}/{max_hp}[/color]  '
                f'[color=aaaaaa]T:{turns}[/color]  '
                f'[color={fear_color}]Fear:{fear_pct}%[/color]'
            ),
            markup=True,
            font_name='RobotoMono',
            font_size=dp(13),
            halign='right',
            valign='middle',
            size_hint_x=0.4,
        )
        stats_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        top.add_widget(stats_lbl)
        container.add_widget(top)

        # Class description (one line, dimmed)
        if description:
            desc_line = description.split('\n')[0]
            desc_lbl = Label(
                text=f'[color=888888][i]{desc_line}[/i][/color]',
                markup=True,
                font_name='RobotoMono',
                font_size=dp(11),
                halign='left',
                valign='middle',
                size_hint_y=None,
                height=dp(16),
            )
            desc_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            container.add_widget(desc_lbl)

        # Affinity badges row
        if item_affs or skill_affs:
            aff_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(26), spacing=dp(4))
            aff_prefix = Label(
                text='[color=555555]Affinities:[/color]',
                markup=True,
                font_name='RobotoMono',
                font_size=dp(11),
                size_hint_x=None,
                width=dp(75),
                halign='right',
                valign='middle',
            )
            aff_prefix.bind(size=lambda i, v: setattr(i, 'text_size', v))
            aff_row.add_widget(aff_prefix)

            aff_scroll = ScrollView(do_scroll_y=False, do_scroll_x=True, size_hint_x=1)
            aff_inner = BoxLayout(orientation='horizontal', spacing=dp(5), size_hint_x=None)
            aff_inner.bind(minimum_width=aff_inner.setter('width'))

            for aff in item_affs:
                aff_inner.add_widget(self._make_badge(aff, (0.1, 0.6, 0.3, 0.7)))
            for aff in skill_affs:
                aff_inner.add_widget(self._make_badge(aff, (0.5, 0.3, 0.1, 0.7)))

            aff_scroll.add_widget(aff_inner)
            aff_row.add_widget(aff_scroll)
            container.add_widget(aff_row)

        return container

    # ── Item list ─────────────────────────────────────────────────────────────

    def _build_item_list(self, inventory, items_master):
        """Scrollable list of item cards."""
        scroll = ScrollView(do_scroll_x=False)
        grid = GridLayout(cols=1, spacing=dp(5), size_hint_y=None, padding=[0, dp(2)])
        grid.bind(minimum_height=grid.setter('height'))

        for entry in inventory:
            item_key  = entry if isinstance(entry, str) else (
                entry.get('id') or entry.get('key') or entry.get('name') or ''
            )
            item_data = items_master.get(item_key, {})
            if not item_data and isinstance(entry, dict):
                item_data = entry  # inline dict fallback
            card = self._build_item_card(item_key, item_data)
            grid.add_widget(card)

        scroll.add_widget(grid)
        return scroll

    def _build_item_card(self, item_key, item_data):
        """
        One item card:
          [TYPE BADGE]  Name                        [Examine] [Use]
          Character connection (if present)
          Description / examine_details excerpt
        """
        name       = item_data.get('name') or item_key.replace('_', ' ')
        item_type  = item_data.get('type', '')
        subtype    = item_data.get('subtype', '')
        char_conn  = item_data.get('character_connection', '').strip()
        franchise  = item_data.get('franchise_source', '').strip()
        description= item_data.get('examine_details') or item_data.get('description', '')
        is_usable  = item_data.get('is_usable') or item_data.get('usable', False)
        is_readable= item_data.get('is_readable', False)
        heal_amt   = item_data.get('heal_amount')
        force_bon  = item_data.get('force_bonus')
        tags       = item_data.get('tags', [])
        is_critical= item_data.get('is_critical', False) or item_data.get('is_quest_item', False)

        card = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(3),
            padding=[dp(8), dp(6)],
        )
        self._draw_panel_bg(card, highlight=is_critical)

        # ── Row 1: Badge + Name + Action buttons ──
        top_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(36), spacing=dp(6))

        # Type badge (or subtype if no type)
        badge_text  = (subtype or item_type or '?').replace('_', ' ')
        badge_color = self.TYPE_COLORS.get(item_type, self.C_DIM)
        top_row.add_widget(self._make_badge(badge_text, badge_color, min_width=dp(80)))

        # Item name
        name_lbl = Label(
            text=f'[b]{name}[/b]' + (' [color=ffaa00]★[/color]' if is_critical else ''),
            markup=True,
            font_name='RobotoMonoBold',
            font_size=dp(14),
            color=self.C_WHITE,
            halign='left',
            valign='middle',
            size_hint_x=1,
        )
        name_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
        top_row.add_widget(name_lbl)

        # Action buttons — only show actions that make sense for this item
        examine_btn = self._make_terminal_button('Examine', height=dp(32), width=dp(80))
        examine_btn.bind(on_release=lambda *_, k=name: self._dispatch('examine', k))
        top_row.add_widget(examine_btn)

        if is_usable or heal_amt:
            use_btn = self._make_terminal_button('Use', height=dp(32), width=dp(64),
                                                  color=self.C_ACCENT)
            use_btn.bind(on_release=lambda *_, k=name: self._dispatch('use', k))
            top_row.add_widget(use_btn)

        if is_readable:
            read_btn = self._make_terminal_button('Read', height=dp(32), width=dp(64))
            read_btn.bind(on_release=lambda *_, k=name: self._dispatch('read', k))
            top_row.add_widget(read_btn)

        card.add_widget(top_row)
        card.height = dp(36) + dp(6) + dp(3)  # start; grows below

        # ── Row 2: Character connection ──
        if char_conn:
            franchise_suffix = f'  [color=555555]({franchise})[/color]' if franchise else ''
            conn_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(20), spacing=dp(6))
            skull = Label(text='[color=888888]☠[/color]', markup=True,
                          font_name='RobotoMono', font_size=dp(12),
                          size_hint_x=None, width=dp(20), halign='center')
            conn_lbl = Label(
                text=f'[color=cc8800]{char_conn}[/color]{franchise_suffix}',
                markup=True,
                font_name='RobotoMono',
                font_size=dp(12),
                halign='left',
                valign='middle',
                color=self.C_DIM,
            )
            conn_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            conn_row.add_widget(skull)
            conn_row.add_widget(conn_lbl)
            card.add_widget(conn_row)
            card.height += dp(20) + dp(3)

        # ── Row 3: Stat pills (heal / force bonus) ──
        stat_parts = []
        if heal_amt:
            stat_parts.append(f'[color=33cc66]+{heal_amt} HP[/color]')
        if force_bon:
            stat_parts.append(f'[color=4499ff]Force +{force_bon}[/color]')
        if tags:
            tag_str = '  '.join(f'[color=445544]{t}[/color]' for t in tags[:4])
            stat_parts.append(tag_str)

        if stat_parts:
            stat_lbl = Label(
                text='  '.join(stat_parts),
                markup=True,
                font_name='RobotoMono',
                font_size=dp(11),
                halign='left',
                valign='middle',
                size_hint_y=None,
                height=dp(17),
            )
            stat_lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))
            card.add_widget(stat_lbl)
            card.height += dp(17) + dp(3)

        # ── Row 4: Description excerpt ──
        if description:
            # Clamp to 2 lines worth; full text available via Examine
            excerpt = description[:160] + ('…' if len(description) > 160 else '')
            desc_lbl = Label(
                text=f'[color=888888]{excerpt}[/color]',
                markup=True,
                font_name='RobotoMono',
                font_size=dp(11),
                halign='left',
                valign='top',
                size_hint_y=None,
            )
            # Bind width → text_size so the label wraps correctly
            desc_lbl.bind(width=lambda i, v: setattr(i, 'text_size', (v, None)))
            desc_lbl.bind(texture_size=lambda i, v: setattr(i, 'height', v[1]))
            desc_lbl.height = dp(30)  # initial; texture_size callback resizes it
            card.add_widget(desc_lbl)
            # We'll add a rough estimate; actual height adjusts via texture callback
            card.height += dp(36)

        # Add bottom padding
        card.height += dp(4)

        return card

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _dispatch(self, verb, item_name):
        """Fire a command and dismiss the popup."""
        self.dismiss()
        self._cmd(f'{verb} {item_name}')

    def _make_terminal_button(self, text, height=dp(40), width=None, color=None):
        """Create a TerminalButton-styled Button in pure Python."""
        btn = Button(
            text=text,
            markup=True,
            font_name='RobotoMonoBold',
            font_size=dp(13),
            background_normal='',
            background_down='',
            background_color=(0, 0, 0, 0),
            color=color or self.C_GREEN,
            size_hint_y=None,
            height=height,
            halign='center',
            valign='middle',
        )
        if width:
            btn.size_hint_x = None
            btn.width = width
        btn.bind(size=lambda i, v: setattr(i, 'text_size', v))

        def draw_border(btn, *_):
            btn.canvas.before.clear()
            with btn.canvas.before:
                Color(*(self.C_ACCENT if btn.state == 'down' else self.C_GREEN))
                Line(
                    rounded_rectangle=(btn.x, btn.y, btn.width, btn.height, dp(4)),
                    width=dp(1.2),
                )
        btn.bind(pos=draw_border, size=draw_border, state=draw_border)
        return btn

    def _make_badge(self, text, color, min_width=dp(60)):
        """Small coloured rounded-rect label badge."""
        lbl = Label(
            text=text.replace('_', ' '),
            font_name='RobotoMono',
            font_size=dp(10),
            size_hint=(None, None),
            size=(max(min_width, dp(10) * len(text)), dp(22)),
            halign='center',
            valign='middle',
        )
        lbl.bind(size=lambda i, v: setattr(i, 'text_size', v))

        def draw_bg(lbl, *_):
            lbl.canvas.before.clear()
            with lbl.canvas.before:
                Color(*color)
                from kivy.graphics import RoundedRectangle as RR
                RR(pos=lbl.pos, size=lbl.size, radius=[dp(3)])
        lbl.bind(pos=draw_bg, size=draw_bg)
        return lbl

    def _make_divider(self, height=dp(1)):
        w = Widget(size_hint_y=None, height=height)
        def draw(w, *_):
            w.canvas.clear()
            with w.canvas:
                Color(*self.C_BORDER)
                Line(points=[w.x, w.center_y, w.right, w.center_y], width=dp(1))
        w.bind(pos=draw, size=draw)
        return w

    def _draw_panel_bg(self, widget, highlight=False):
        """Draw terminal-panel background onto a BoxLayout."""
        border_color = self.C_ACCENT if highlight else self.C_BORDER

        def draw(w, *_):
            w.canvas.before.clear()
            with w.canvas.before:
                Color(*self.C_PANEL)
                from kivy.graphics import RoundedRectangle as RR
                RR(pos=w.pos, size=w.size, radius=[dp(4)])
                Color(*border_color)
                Line(rounded_rectangle=(w.x, w.y, w.width, w.height, dp(4)), width=dp(1))
        widget.bind(pos=draw, size=draw)


class QTEPopup(Popup):
    container = ObjectProperty(None)
    timer_bar = ObjectProperty(None)
    prompt_label = ObjectProperty(None)

    def __init__(self, qte_data=None, prompt=None, duration=None, input_type=None,
                 submit_callback=None, qte_context=None, **kwargs):
        super().__init__(**kwargs)

        payload = qte_data if isinstance(qte_data, dict) else {}
        if not payload and qte_data is not None and prompt is None:
            prompt = qte_data

        context_from_payload = payload.get('qte_context', {}) if payload else {}

        self.qte_data = payload
        self.input_type = (
            input_type
            or payload.get('input_type')
            or context_from_payload.get('input_type')
            or ''
        )
        self.submit_callback = submit_callback or payload.get('submit_callback')
        self.qte_context = qte_context or context_from_payload or {}
        self.duration = float(duration or payload.get('duration') or 0.0)
        self.time_elapsed = 0.0
        self.mash_count = 0
        self.tap_count = 0
        self.alt_count = 0
        self.key_sequence = []
        self.hold_start_time = None
        self.text_input = None
        self.tap_counter = None
        self.logger = logging.getLogger(self.__class__.__name__)

        context = self.qte_context
        ui_type = context.get('ui_type', '')
        prompt_msg = context.get('ui_prompt_message') or prompt or 'REACT!'
        desc_msg = context.get('description', '')

        if ui_type in ('hold', 'hold_release') and not desc_msg:
            desc_msg = "TAP and HOLD the button below, then RELEASE at the right moment!"

        if desc_msg:
            full_text = f"{desc_msg}\n\n[b]{prompt_msg}[/b]"
        else:
            full_text = f"[b]{prompt_msg}[/b]"

        label = None
        if hasattr(self, 'ids'):
            label = self.ids.get('message_label') or self.ids.get('prompt_label')
        if not label and getattr(self, 'prompt_label', None):
            label = self.prompt_label
        if not label and getattr(self, 'message_label', None):
            label = self.message_label
        if label:
            label.text = full_text

        if self.timer_bar:
            self.timer_bar.max = self.duration
            self.timer_bar.value = self.duration

        if self.container:
            self._create_qte_interface(self.container)
        self.timer_event = None
        self.is_started = False

    def start_countdown(self):
        """Starts the QTE logic once the popup is frontmost."""
        if not self.is_started:
            self.is_started = True
            self.logger.info(f"QTE START: Input={self.input_type} Duration={self.duration}")
            
            # Start listening for keys ONLY when the timer starts
            Window.bind(on_key_down=self._on_key_down, on_key_up=self._on_key_up)
            
            # Start the clock
            self.timer_event = Clock.schedule_interval(self._update_timer, 1 / 60.0)

    def _create_qte_interface(self, layout):
        qctx = self.qte_context or {}
        ui_type = qctx.get('ui_type', 'text_input')
        desc = qctx.get('description', '')

        # --- WIDGET SELECTION ---
        
        if ui_type == "choice_buttons":
            choices = qctx.get('choices') or qctx.get('button_labels', [])
            colors = qctx.get('button_colors', {})

            grid = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
            grid.bind(minimum_height=grid.setter('height'))

            from kivy.utils import get_color_from_hex
            for c in choices:
                c_str = str(c)
                hex_c = colors.get(c_str)
                btn = Factory.TerminalButton(
                    text=f"[b]{c_str.upper()}[/b]", markup=True,
                    size_hint_y=None, height=dp(60)
                )
                if hex_c:
                    try:
                        btn.accent_color = get_color_from_hex(hex_c)
                    except Exception:
                        pass
                btn.bind(on_release=lambda x, ch=c: self.submit_callback(
                    {'event': 'choice_selected', 'choice': ch}))
                grid.add_widget(btn)
            layout.add_widget(grid)

        elif ui_type == "directional_pad":
            choices = qctx.get('button_labels', [])

            self.sequence_display = Label(
                text="", font_size='18sp', markup=True,
                size_hint_y=None, height=dp(60)
            )
            layout.add_widget(self.sequence_display)
            self._update_sequence_display()

            grid = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
            grid.bind(minimum_height=grid.setter('height'))

            for c in choices:
                c_str = str(c)
                btn = Factory.TerminalButton(
                    text=f"[b]{c_str.upper()}[/b]", markup=True,
                    size_hint_y=None, height=dp(60)
                )
                btn.bind(on_release=lambda x, ch=c: self._direction_press(ch))
                grid.add_widget(btn)
            layout.add_widget(grid)


        elif ui_type == "memory_grid":
            grid_size = qctx.get('grid_size', 3)
            button_labels = qctx.get('button_labels', [])
            pattern = qctx.get('pattern') or qctx.get('required_pattern') or qctx.get('required_sequence')

            if pattern:
                # Normalize: if pattern contains strings, convert to int indices via button_labels map.
                # This handles hazards that supply ['left','up','down','right'] instead of [0,1,2,3].
                if pattern and isinstance(pattern[0], str):
                    # Build a case-insensitive label -> index map
                    label_to_idx = {lbl.lower(): i for i, lbl in enumerate(button_labels)}
                    converted = []
                    for p in pattern:
                        key = str(p).lower()
                        if key in label_to_idx:
                            converted.append(label_to_idx[key])
                        else:
                            # Fallback: try to parse as int, else use 0
                            try:
                                converted.append(int(p))
                            except (ValueError, TypeError):
                                converted.append(0)
                                self.logger.warning(
                                    f"memory_grid: could not map pattern label '{p}' "
                                    f"to an index. Available labels: {button_labels}. Defaulting to 0."
                                )
                    pattern = converted
            else:
                # Auto-generate if absent
                length = qctx.get('target_sequence_length') or qctx.get('pattern_length_default') or 4
                max_idx = (grid_size * grid_size) - 1
                pattern = [random.randint(0, max_idx) for _ in range(length)]

            qctx['pattern'] = pattern  # Write back normalized ints for engine validation

            mg = MemoryGridWidget(
                callback=self.submit_callback,
                pattern=pattern,
                button_labels=button_labels,  # Pass labels so buttons can be labelled
                grid_size=grid_size,
                size_hint_y=None, height=dp(250)
            )
            layout.add_widget(mg)

        elif ui_type == "button_sequence":
            required_seq = qctx.get('required_sequence', [])
            button_labels = qctx.get('button_labels', [])

            if not button_labels and required_seq:
                button_labels = sorted(list(set(str(x) for x in required_seq)))

            self.sequence_display = Label(
                text="",
                font_size='18sp',
                markup=True,
                size_hint_y=None, height=dp(60)
            )
            layout.add_widget(self.sequence_display)
            self._update_sequence_display()

            grid = GridLayout(cols=3, spacing=dp(5), size_hint_y=None)
            grid.bind(minimum_height=grid.setter('height'))

            for lbl in button_labels:
                btn = Factory.TerminalButton(text=str(lbl), size_hint_y=None, height=dp(50))
                btn.bind(on_release=lambda _, k=lbl: self._virtual_key_sequence(str(k)))
                grid.add_widget(btn)
            layout.add_widget(grid)

        elif ui_type == "precision_gauge":
            decay = float(qctx.get('decay_rate', 0.5))
            target = qctx.get('target_zone', [10, 15])
            gauge = PrecisionGaugeWidget(
                callback=self.submit_callback,
                decay=decay, target_range=tuple(target),
                size_hint_y=None, height=dp(150)
            )
            layout.add_widget(gauge)
            
            pump_btn = Factory.TerminalButton(text="[b]PUMP[/b]", markup=True,
                                            size_hint_y=None, height=dp(80))
            pump_btn.bind(on_release=lambda *_: gauge.tap())
            layout.add_widget(pump_btn)

        elif ui_type == "trace_path":
            path_type = qctx.get('path_type', 'spiral')
            tp = TracePathWidget(callback=self.submit_callback, path_type=path_type, size_hint_y=None, height=dp(300))
            layout.add_widget(tp)

        elif ui_type == "rhythm_bar":
            tz = qctx.get('target_zone', [0.4, 0.6])
            rb = RhythmWidget(callback=self.submit_callback, speed=qctx.get('beat_speed', 1.5), target_zone=tuple(tz), size_hint_y=None, height=dp(80))
            layout.add_widget(rb)

        elif ui_type == "reaction_button":
            rw = ReactionWidget(callback=self.submit_callback, wait_range=qctx.get('wait_time_range', [2.0, 4.0]), size_hint_y=None, height=dp(150))
            layout.add_widget(rw)

        elif ui_type == "directional_pad":
            choices = qctx.get('button_labels', [])
            grid = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
            grid.bind(minimum_height=grid.setter('height'))
            
            for c in choices:
                c_str = str(c)
                btn = Factory.TerminalButton(
                    text=f"[b]{c_str.upper()}[/b]", markup=True,
                    size_hint_y=None, height=dp(60)
                )
                btn.bind(on_release=lambda x, ch=c: self._direction_press(ch))
                grid.add_widget(btn)
            layout.add_widget(grid)

        elif ui_type in ('hold', 'hold_release'):

            self.hold_display = Label(
                text="Press and hold...", font_size=dp(18),
                markup=True, 
                size_hint_y=None, height=dp(40)
            )
            layout.add_widget(self.hold_display)

            from kivy.uix.progressbar import ProgressBar
            hold_progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(20))
            layout.add_widget(hold_progress)

            hold_btn = Factory.TerminalButton(
                text="[b]HOLD[/b]", markup=True,
                size_hint_y=None, height=dp(80)
            )

            import time as _time

            is_hold_release = (qctx.get('input_type') or self.input_type) in ('hold_release', 'hold_and_release')
            if is_hold_release:
                window = qctx.get('release_window') or qctx.get('release_window_default') or [0.6, 0.8]
                lo_frac, hi_frac = float(window[0]), float(window[1])
                if lo_frac <= 1.0 and hi_frac <= 1.0:
                    total_dur = float(qctx.get('duration', self.duration))
                    target_lo = lo_frac * total_dur
                    target_hi = hi_frac * total_dur
                else:
                    target_lo, target_hi = lo_frac, hi_frac
                hold_progress.max = self.duration
            else:
                target_lo = float(qctx.get('required_hold_time', qctx.get('required_hold_time_default', 2.0)))
                target_hi = self.duration
                hold_progress.max = target_lo

            self._hold_timer_event = None

            def on_hold_press(*_):
                self.hold_start_time = _time.time()
                self.hold_display.text = "Holding..."
                hold_progress.value = 0
                def update_hold_progress(dt):
                    if self.hold_start_time:
                        elapsed = _time.time() - self.hold_start_time
                        hold_progress.value = min(elapsed, hold_progress.max)
                        if is_hold_release:
                            if elapsed < target_lo:
                                self.hold_display.text = (
                                    f"Holding... {elapsed:.2f}s (too early!)")
                            elif elapsed <= target_hi:
                                self.hold_display.text = (
                                    f"[color=00ff00]RELEASE NOW! "
                                    f"{elapsed:.2f}s[/color]")
                            else:
                                self.hold_display.text = (
                                    f"[color=ff0000]Too late! "
                                    f"{elapsed:.2f}s[/color]")
                        else:
                            if elapsed >= target_lo:
                                self.hold_display.text = (
                                    f"[color=00ff00]RELEASE! "
                                    f"{elapsed:.2f}s / {target_lo:.2f}s[/color]")
                            else:
                                self.hold_display.text = (
                                    f"Holding... {elapsed:.2f}s / {target_lo:.2f}s")
                self._hold_timer_event = Clock.schedule_interval(
                    update_hold_progress, 0.05)

            def on_hold_release(*_):
                if self._hold_timer_event:
                    self._hold_timer_event.cancel()
                    self._hold_timer_event = None
                if self.hold_start_time:
                    duration = _time.time() - self.hold_start_time
                    self.hold_display.text = f"Released: {duration:.1f}s"
                    self.submit_callback({'event': 'hold_release', 'duration': duration})
                    self.hold_start_time = None

            hold_btn.bind(on_press=on_hold_press)
            hold_btn.bind(on_release=on_hold_release)
            layout.add_widget(hold_btn)

        elif ui_type == "aim_area":
            target_count = qctx.get('target_count', 3)
            aim_w = AimTargetWidget(
                callback=self.submit_callback,
                target_count=target_count,
                size_hint_y=None, height=dp(200)
            )
            layout.add_widget(aim_w)

        elif ui_type == "text_input":
            target = (qctx.get('expected_input_word') or 
                      qctx.get('required_code') or 
                      qctx.get('valid_responses', [''])[0])
            
            if target:
                if isinstance(target, list):
                    target = target[0]
                display_text = f"Type: [b][color=00ff00]{str(target).upper()}[/color][/b]"
            else:
                display_text = "Code Required:"

            layout.add_widget(Label(
                text=display_text, 
                markup=True,
                size_hint_y=None, 
                height=dp(40)
            ))

            ti = TextInput(multiline=False, size_hint_y=None, height=dp(40), halign="center")
            self.text_input = ti
            ti.bind(on_text_validate=self._submit_text_input)
            layout.add_widget(ti)
            
            btn = Factory.TerminalButton(text="Submit", size_hint_y=None, height=dp(40))
            btn.bind(on_release=lambda *_: self._submit_text_input())
            layout.add_widget(btn)
            
            def _grab_focus(dt):
                from kivy.app import App
                app = App.get_running_app()
                if app and app.root:
                    game_screen = app.root.get_screen('game') if hasattr(app.root, 'get_screen') else None
                    if game_screen:
                        action_input = game_screen.ids.get('action_input')
                        if action_input and hasattr(action_input, 'text_input') and action_input.text_input:
                            action_input.text_input.focus = False
                if self.text_input:
                    self.text_input.focus = True
            Clock.schedule_once(_grab_focus, 0.3)

        elif ui_type == "alternating_buttons":
            labels = qctx.get('button_labels', ['LEFT', 'RIGHT'])
            self._alt_count = 0
            self._alt_expected = 0
            
            alt_display = Label(text=f"Press: [b]{labels[0]}[/b]", markup=True,
                                size_hint_y=None, height=dp(40))
            layout.add_widget(alt_display)
            self._alt_display = alt_display
            
            btn_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(80), spacing=dp(10))
            for i, lbl in enumerate(labels):
                btn = Factory.TerminalButton(text=f"[b]{lbl}[/b]", markup=True)
                btn.bind(on_release=lambda x, idx=i: self._alt_btn_press(idx, labels))
                btn_row.add_widget(btn)
            layout.add_widget(btn_row)

        elif ui_type == "tap_area":
            self.tap_count = 0
            target = (
                self.qte_context.get('effective_target_mash_count')
                or self.qte_context.get('target_mash_count')
                or self.qte_context.get('target_mash_count_default')
                or self.qte_context.get('required_tap_count')
                or self.qte_context.get('required_tap_count_default')
                or 15
            )
            self.tap_counter = Label(
                text=f"Presses: {self.tap_count}/{target}",
                font_size='20sp',
                size_hint_y=None,
                height='40dp'
            )
            layout.add_widget(self.tap_counter)

            tap_btn = Factory.TerminalButton(
                text="MASH!",
                size_hint_y=None,
                height='80dp'
            )

            def on_tap_btn_press(*_):
                self.mash_count += 1
                if self.tap_counter:
                    self.tap_counter.text = f"Presses: {self.mash_count}/{target}"
                self.submit_callback({'event': 'mash_press', 'count': self.mash_count})

            tap_btn.bind(on_release=on_tap_btn_press)
            layout.add_widget(tap_btn)

    def _direction_press(self, direction):
        if not hasattr(self, 'key_sequence'):
            self.key_sequence = []
        self.key_sequence.append(str(direction).lower())
        self._update_sequence_display()
        self.submit_callback({'event': 'sequence_input', 'sequence': self.key_sequence})

    def _alt_btn_press(self, idx, labels):
        if idx == self._alt_expected:
            self._alt_count += 1
            self._alt_expected = (self._alt_expected + 1) % len(labels)
            if hasattr(self, '_alt_display'):
                self._alt_display.text = f"Press: [b]{labels[self._alt_expected]}[/b]  ({self._alt_count})"
            self.submit_callback({'event': 'alternation', 'count': self._alt_count})

    def _virtual_key_sequence(self, key_char):
        if not hasattr(self, 'key_sequence'):
            self.key_sequence = []
        self.key_sequence.append(key_char)
        self._update_sequence_display()
        self.submit_callback({'event': 'sequence_input', 'sequence': self.key_sequence})

    def _submit_text_input(self, *args):
        self.logger.info(f"_submit_text_input called. popup_id={id(self)}, "
                        f"is_dismissed={getattr(self, 'is_dismissed', False)}, "
                        f"text_input={self.text_input}, "
                        f"text='{self.text_input.text.strip() if self.text_input else 'NO_WIDGET'}'")
        if not self.text_input:
            return
        text = self.text_input.text.strip()
        if text:
            self.text_input.text = ''
            self.logger.info(f"_submit_text_input: Firing submit_callback with text='{text}'")
            self.submit_callback({'event': 'submit_text', 'text': text})
        else:
            self.logger.warning("_submit_text_input: Empty text, not submitting.")

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        if getattr(self, 'is_dismissed', False) or not self.parent:
            return False

        if self.text_input and self.text_input.focus:
            return False

        key_char = codepoint if codepoint else (chr(scancode) if scancode < 128 else str(scancode))
        input_type = self.input_type.lower()
        
        if input_type in ['mash', 'button_mash', 'force']:
            self.mash_count += 1
            target = (self.qte_context.get('effective_target_mash_count') or 15)
            if hasattr(self, 'tap_counter') and self.tap_counter:
                self.tap_counter.text = f"Presses: {self.mash_count}/{target}"
            self.submit_callback({'event': 'mash_press', 'count': self.mash_count})
            return True

        elif input_type in ('alternate', 'balance'):
            key_lower = key_char.lower() if key_char else ''
            self.submit_callback({'event': 'key_press', 'key': key_lower})

        elif input_type == 'single_key':
            key_lower = key_char.lower() if key_char else ''
            if key_lower:
                self.submit_callback({'event': 'key_press', 'key': key_lower})

        elif input_type in ('hold', 'hold_release'):
            if not self.hold_start_time:
                import time
                self.hold_start_time = time.time()
                if hasattr(self, 'hold_display') and self.hold_display:
                    self.hold_display.text = "Holding..."
                self.submit_callback({'event': 'hold_start', 'time': self.hold_start_time})

        elif input_type in ('sequence', 'pattern', 'directional'):
            if key_char:
                self.key_sequence.append(key_char.lower())
                self._update_sequence_display()
                self.submit_callback({'event': 'sequence_input', 'sequence': self.key_sequence})
        elif self.text_input and key == 13:
             self.submit_callback({'event': 'submit_text', 'text': self.text_input.text})

        return True

    def _on_mouse_down(self, window, x, y, button, modifiers):
        if getattr(self, 'is_dismissed', False):
            return False
        if self.input_type in ['mash', 'button_mash', 'force']:
            self.mash_count += 1
            if hasattr(self, 'tap_counter') and self.tap_counter:
                 target = (self.qte_context.get('effective_target_mash_count') or 15)
                 self.tap_counter.text = f"Presses: {self.mash_count}/{target}"
            self.submit_callback({'event': 'mash_press', 'count': self.mash_count})
            return True
        return False

    def _on_key_up(self, window, key, scancode):
        if self.input_type in ('hold', 'hold_release') and self.hold_start_time:
            import time
            duration = time.time() - self.hold_start_time
            if hasattr(self, 'hold_display') and self.hold_display:
                self.hold_display.text = f"Released: {duration:.1f}s"
            self.submit_callback({'event': 'hold_release', 'duration': duration})
            self.hold_start_time = None
        return True

    def _on_choice_selected(self, choice):
        self.submit_callback({'event': 'choice_selected', 'choice': choice})

    def on_open(self):
        if getattr(self, 'text_input', None):
            Clock.schedule_once(self._focus_text_input, 0.1)

    def _focus_text_input(self, dt):
        if self.text_input:
            self.text_input.focus = True

    def _update_sequence_display(self):
        if not hasattr(self, 'sequence_display') or not self.sequence_display:
            return

        req_seq = self.qte_context.get('required_sequence') or []
        current_seq = getattr(self, 'key_sequence', [])

        req_text = ' '.join(str(x).upper() for x in req_seq)
        entered_text = ' '.join(str(x).upper() for x in current_seq)
        self.sequence_display.text = f"Required: [b]{req_text}[/b]\nEntered: {entered_text}"

    def _update_timer(self, dt):
        if not hasattr(self, 'time_elapsed'):
            return False
        self.time_elapsed += dt
        remaining = max(0.0, self.duration - self.time_elapsed)
        if hasattr(self, 'timer_bar') and self.timer_bar:
            self.timer_bar.value = remaining
        
        if self.time_elapsed >= self.duration:
            if hasattr(self, 'timer_event') and self.timer_event:
                self.timer_event.cancel()
                self.timer_event = None
            self.logger.info("QTEPopup._update_timer: Visual timer expired. Waiting for engine timeout to resolve.")
            return False
        return True

    def dismiss(self, *largs, **kwargs):
        import traceback
        self.logger.info(f"QTEPopup.dismiss called. popup_id={id(self)}, "
                        f"already_dismissed={getattr(self, 'is_dismissed', False)}, "
                        f"time_elapsed={getattr(self, 'time_elapsed', '?')}, "
                        f"duration={getattr(self, 'duration', '?')}")
        self.logger.debug(f"QTEPopup.dismiss call stack:\n{''.join(traceback.format_stack()[-4:])}")
        self.is_dismissed = True
        Window.unbind(on_key_down=self._on_key_down, on_key_up=self._on_key_up)
        if hasattr(self, 'timer_event') and self.timer_event:
            self.timer_event.cancel()
        super().dismiss(*largs, **kwargs)

class AimTargetWidget(Widget):
    """Widget for aim/tap QTEs. Tapping the target area triggers success."""
    def __init__(self, target_radius=dp(40), callback=None, target_count=3, **kwargs):
        super().__init__(**kwargs)
        self.target_radius = target_radius
        self.callback = callback
        self.target_count = target_count
        self.hits = 0
        self.target_pos = (0, 0)  # Will be set on layout
        self.bind(size=self._update_target, pos=self._update_target)
        Clock.schedule_once(lambda dt: self._update_target(), 0.1)  # Delay until laid out

    def _update_target(self, *args):
        w, h = self.size
        r = self.target_radius
        
        # Calculate boundaries safely
        max_x = int(w - r)
        max_y = int(h - r)
        
        # Failsafe: If Kivy hasn't sized the widget yet, or the screen is too small,
        # just place the target directly in the center to prevent a crash.
        if int(r) >= max_x or int(r) >= max_y:
            self.target_pos = (w / 2, h / 2)
        else:
            from random import randint
            self.target_pos = (randint(int(r), max_x), randint(int(r), max_y))
            
        self._draw()

    def _draw(self):
        """Actually draws the red target on the canvas."""
        self.canvas.clear()
        with self.canvas:
            from kivy.graphics import Color, Ellipse, Line
            
            # Outer ring (Target Area)
            Color(1, 0, 0, 0.3)
            Ellipse(
                pos=(self.x + self.target_pos[0] - self.target_radius, 
                     self.y + self.target_pos[1] - self.target_radius),
                size=(self.target_radius * 2, self.target_radius * 2)
            )
            
            # Inner bullseye
            Color(1, 0, 0, 1)
            inner_r = self.target_radius * 0.4
            Ellipse(
                pos=(self.x + self.target_pos[0] - inner_r, 
                     self.y + self.target_pos[1] - inner_r),
                size=(inner_r * 2, inner_r * 2)
            )
            
            # Crosshairs
            Line(points=[self.x + self.target_pos[0] - self.target_radius, self.y + self.target_pos[1],
                         self.x + self.target_pos[0] + self.target_radius, self.y + self.target_pos[1]], width=1.5)
            Line(points=[self.x + self.target_pos[0], self.y + self.target_pos[1] - self.target_radius,
                         self.x + self.target_pos[0], self.y + self.target_pos[1] + self.target_radius], width=1.5)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            local_x = touch.pos[0] - self.x
            local_y = touch.pos[1] - self.y
            tx, ty = self.target_pos
            if (local_x - tx)**2 + (local_y - ty)**2 <= self.target_radius**2:
                self.hits += 1
                if self.hits >= self.target_count:
                    if self.callback:
                        self.callback({'event': 'aim_success', 'pos': touch.pos})
                else:
                    # Target hit but more needed — move to new position
                    self._update_target()
                return True
        return super().on_touch_down(touch)

class DragTrackWidget(Widget):
    """Widget for drag QTEs. User must drag along a path."""
    def __init__(self, callback=None, **kwargs):
        super().__init__(**kwargs)
        self.callback = callback
        self.drag_line = None
        self.drag_points = []
        self.path_color = (0, 0.7, 1, 1)
        self.drag_color = (1, 0.8, 0, 1)
        self.path = [(dp(40), self.height//2), (self.width-dp(40), self.height//2)]
        self.bind(size=self._update_path, pos=self._update_path)
        self._update_path()

    def _update_path(self, *args):
        self.path = [(dp(40), self.height//2), (self.width-dp(40), self.height//2)]
        self.canvas.clear()
        with self.canvas:
            Color(*self.path_color)
            Line(points=[p for xy in self.path for p in xy], width=dp(4))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.drag_points = [touch.pos]
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self.drag_points:
            self.drag_points.append(touch.pos)
            self._draw_drag()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self.drag_points:
            # Evaluate drag accuracy (simple: did user stay near the path?)
            # For demo, just call callback
            if self.callback:
                self.callback({'event': 'drag_complete', 'points': self.drag_points})
            self.drag_points = []
            self._draw_drag()
            return True
        return super().on_touch_up(touch)

    def _draw_drag(self):
        self.canvas.after.clear()
        if self.drag_points:
            with self.canvas.after:
                Color(*self.drag_color)
                Line(points=[p for xy in self.drag_points for p in xy], width=dp(3))


class PrecisionGaugeWidget(Widget):
    """A gauge that fills when tapped and decays over time. Keep in green."""
    def __init__(self, callback, decay=0.5, target_range=(10,15), **kwargs):
        super().__init__(**kwargs)
        self.callback = callback
        self.value = 0
        self.decay = decay
        self.target_range = target_range
        self.max_val = target_range[1] * 1.5
        Clock.schedule_interval(self._update, 1/60.0)
        self.bind(size=self._draw, pos=self._draw)

    def tap(self):
        self.value += 1
        self._draw()
        self.callback({'event': 'gauge_update', 'value': self.value})

    def _update(self, dt):
        self.value = max(0, self.value - (self.decay * dt * 5))
        self._draw()

    def _draw(self, *args):
        self.canvas.clear()
        with self.canvas:
            # Background
            Color(0.2, 0.2, 0.2)
            Rectangle(pos=self.pos, size=self.size)
            
            # Target Zone
            Color(0, 0.8, 0, 0.4)
            y_start = self.y + (self.height * (self.target_range[0] / self.max_val))
            y_height = (self.height * ((self.target_range[1] - self.target_range[0]) / self.max_val))
            Rectangle(pos=(self.x, y_start), size=(self.width, y_height))
            
            # Fill Bar
            norm_val = min(1.0, self.value / self.max_val)
            Color(1, 0.2, 0.2) if not (self.target_range[0] <= self.value <= self.target_range[1]) else Color(0.2, 1, 0.2)
            Rectangle(pos=self.pos, size=(self.width, self.height * norm_val))


class MemoryGridWidget(GridLayout):
    """Grid of buttons that flash a pattern.
    
    pattern must be a list of INTEGER indices (0..grid_size²-1).
    The _create_qte_interface branch is responsible for converting string labels to ints
    before passing them here.
    """
    def __init__(self, callback, pattern, grid_size=3, button_labels=None, **kwargs):
        super().__init__(**kwargs)
        self.cols = grid_size

        from kivy.metrics import dp
        self.spacing = dp(5)
        self.padding = dp(5)

        self.callback = callback
        self.pattern = pattern  # guaranteed list of ints by the time we get here
        self.buttons = []
        self.input_enabled = False

        # If button_labels are provided and match grid count, use them; else number the tiles.
        max_tiles = grid_size * grid_size
        labels = list(button_labels or [])

        for i in range(max_tiles):
            label_text = labels[i] if i < len(labels) else str(i)
            btn = Button(
                text=label_text,
                background_normal='',
                background_color=(0.3, 0.3, 0.3, 1)
            )
            btn.bind(on_press=lambda x, idx=i: self._on_btn_press(idx))
            self.add_widget(btn)
            self.buttons.append(btn)

        Clock.schedule_once(self._play_pattern, 1.0)

    def _play_pattern(self, dt):
        delay = 0.0
        for idx in self.pattern:
            # Guard: idx must be a valid int index. Skip and warn if not.
            if not isinstance(idx, int) or idx < 0 or idx >= len(self.buttons):
                import logging
                logging.getLogger("MemoryGridWidget").warning(
                    f"_play_pattern: invalid index {idx!r} (type={type(idx).__name__}), "
                    f"buttons count={len(self.buttons)}. Skipping."
                )
                delay += 0.8
                continue
            Clock.schedule_once(lambda dt, i=idx: self._flash_btn(i), delay)
            delay += 0.8
        Clock.schedule_once(lambda dt: self._enable_input(), delay)

    def _flash_btn(self, idx):
        # Belt-and-suspenders int guard so a stale scheduled callback never crashes.
        if not isinstance(idx, int) or idx < 0 or idx >= len(self.buttons):
            return
        btn = self.buttons[idx]
        btn.background_color = (0.1, 0.8, 0.1, 1)
        Clock.schedule_once(lambda dt: setattr(btn, 'background_color', (0.3, 0.3, 0.3, 1)), 0.5)

    def _enable_input(self):
        self.input_enabled = True

    def _on_btn_press(self, idx):
        if not self.input_enabled:
            return
        btn = self.buttons[idx]
        btn.background_color = (0.8, 0.8, 0.8, 1)
        Clock.schedule_once(lambda dt: setattr(btn, 'background_color', (0.3, 0.3, 0.3, 1)), 0.2)
        self.callback({'event': 'memory_input', 'index': idx, 'pattern': self.pattern})

class SpiralInputWidget(Widget):
    def __init__(self, callback, **kwargs):
        super().__init__(**kwargs)
        self.callback = callback
        self.points = []
        self.bind(size=self._update_canvas, pos=self._update_canvas)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        self.points = [touch.pos]
        self._update_canvas()
        return True

    def on_touch_move(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        self.points.append(touch.pos)
        self._update_canvas()
        # Send each point to QTE engine for spiral analysis
        self.callback({'event': 'spiral_move', 'x': touch.x, 'y': touch.y})
        return True

    def _update_canvas(self, *args):
        self.canvas.clear()
        if len(self.points) > 1:
            with self.canvas:
                Color(1, 0, 0)
                Line(points=sum(self.points, ()), width=2)


 
class TracePathWidget(Widget):
    """Widget for tracing a path (spiral, line, curvy track) with a finger/mouse.
    
    Live feedback: a green cursor dot follows the player's finger along the path.
    On out-of-bounds or finger lift, the cursor decays back along the path toward start.
    """
 
    # --- Tunables ---
    TOLERANCE = 50          # dp — how far from path center the touch can stray
    LOOK_AHEAD = 20         # path points to scan forward per touch_move
    COMPLETION_FRAC = 0.95  # 95% = close enough to count as complete
    DECAY_SPEED = 120.0     # path points per second to rewind during decay
    CURSOR_RADIUS = 18      # dp — radius of the green cursor dot
    TRAIL_WIDTH = 6         # dp — width of the progress trail
    PATH_WIDTH = 15         # dp — width of the guide path
    NODE_RADIUS = 20        # dp — radius of start/end nodes
 
    def __init__(self, path_type='spiral', callback=None, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger('TracePathWidget')
        self.path_type = path_type
        self.callback = callback
 
        self.tolerance = dp(self.TOLERANCE)
        self.path_points = []       # list of (x, y) tuples defining the guide path
        self.progress_index = 0     # int index into path_points — how far the player is
        self._display_index = 0.0   # float — smoothed version for rendering during decay
        self._is_touching = False   # True while the player's finger is down and valid
        self._is_complete = False   # True once the QTE has been resolved
        self._decay_event = None    # Clock event for the decay animation
        self._current_touch_pos = None
        # --- Procedural shape parameters (set once, survive resizes) ---
        self.spiral_dir = random.choice([1, -1])
        self.spiral_loops = random.uniform(1.8, 2.8)
        self.spiral_offset = random.uniform(0, math.pi * 2)
 
        self.track_variant = random.choice(['sine', 'zigzag', 'arc', 'diagonal'])
        self.track_invert = random.choice([1, -1])
        self.track_waves = random.uniform(1.2, 2.5)
 
        self.bind(pos=self._on_layout, size=self._on_layout)
        Clock.schedule_once(lambda dt: self._on_layout(), 0.1)
 
    # ------------------------------------------------------------------
    #  Layout / Path Generation
    # ------------------------------------------------------------------
 
    def _on_layout(self, *args):
        """Regenerate path points and redraw when the widget moves or resizes."""
        if self.width <= 100 or self.height <= 100:
            return
        self._generate_path_points()
        # Clamp progress to new path length
        if self.path_points:
            self.progress_index = min(self.progress_index, len(self.path_points) - 1)
            self._display_index = min(self._display_index, len(self.path_points) - 1)
        self._full_redraw()
 
    def _generate_path_points(self):
        """Build the list of (x, y) waypoints for the guide path."""
        self.path_points = []
        cx, cy = self.center_x, self.center_y
        max_radius = min(self.width, self.height) / 2.2
 
        if self.path_type in ('mouse_spiral', 'spiral'):
            steps = 150
            for i in range(steps):
                t = i / float(steps - 1)
                angle = self.spiral_offset + (t * math.pi * 2 * self.spiral_loops * self.spiral_dir)
                r = max_radius * t
                px = cx + math.cos(angle) * r
                py = cy + math.sin(angle) * r
                self.path_points.append((px, py))
        else:
            steps = 100
            start_x = self.x + dp(30)
            end_x = self.right - dp(30)
            width_range = end_x - start_x
            amp = max_radius * 0.7
 
            for i in range(steps):
                t = i / float(steps - 1)
                px = start_x + (width_range * t)
 
                if self.track_variant == 'sine':
                    py = cy + math.sin(t * math.pi * 2 * self.track_waves) * amp * self.track_invert
                elif self.track_variant == 'zigzag':
                    py = cy + (math.asin(math.sin(t * math.pi * 2 * self.track_waves)) / (math.pi / 2)) * amp * self.track_invert
                elif self.track_variant == 'arc':
                    py = cy + math.sin(t * math.pi) * amp * self.track_invert
                elif self.track_variant == 'diagonal':
                    py = cy + ((t * 2) - 1) * amp * self.track_invert
                else:
                    py = cy
 
                self.path_points.append((px, py))
 
    # ------------------------------------------------------------------
    #  Drawing
    # ------------------------------------------------------------------
 
    def _full_redraw(self, *args):
        """Complete redraw: guide path + nodes + trail + cursor."""
        self.canvas.clear()
        self.canvas.after.clear()
        if not self.path_points:
            return
 
        pw = dp(self.PATH_WIDTH)
        nr = dp(self.NODE_RADIUS)
 
        with self.canvas:
            # --- Guide path (dim blue) ---
            Color(0.2, 0.4, 0.6, 0.35)
            flat = []
            for px, py in self.path_points:
                flat.extend([px, py])
            Line(points=flat, width=pw, cap='round', joint='round')
 
            # --- End node (red, always visible) ---
            Color(0.8, 0.1, 0.1, 1)
            ex, ey = self.path_points[-1]
            Ellipse(pos=(ex - nr, ey - nr), size=(nr * 2, nr * 2))
 
        # Draw the dynamic elements (trail + cursor) on canvas.after
        self._draw_progress()
 
    def _draw_progress(self):
        """Draw the trail (completed portion) and the cursor dot. Called every frame during decay."""
        self.canvas.after.clear()
        if not self.path_points:
            return
 
        # The display index determines where the cursor visually sits
        disp = int(self._display_index)
        disp = max(0, min(disp, len(self.path_points) - 1))
 
        tw = dp(self.TRAIL_WIDTH)
        cr = dp(self.CURSOR_RADIUS)
 
        with self.canvas.after:
            # --- Completed trail (bright green) ---
            if disp > 0:
                Color(0.1, 0.9, 0.3, 0.7)
                trail_flat = []
                for i in range(disp + 1):
                    trail_flat.extend(self.path_points[i])
                Line(points=trail_flat, width=tw, cap='round', joint='round')
 
            # --- Cursor dot ---
            cx, cy = self.path_points[disp]
 
            if self._is_complete:
                # Completed: bright gold
                Color(1.0, 0.85, 0.0, 1)
            elif self._is_touching:
                # Actively dragging: bright green
                Color(0.1, 1.0, 0.3, 1)
            elif self._decay_event:
                # Decaying: pulsing orange-red
                Color(1.0, 0.4, 0.1, 0.85)
            else:
                # Idle at start: green
                Color(0.1, 0.8, 0.1, 1)
 
            Ellipse(pos=(cx - cr, cy - cr), size=(cr * 2, cr * 2))
 
            # --- Small inner dot (white highlight for depth) ---
            ir = cr * 0.4
            Color(1, 1, 1, 0.6)
            Ellipse(pos=(cx - ir, cy - ir), size=(ir * 2, ir * 2))
 
            # --- Progress percentage text ---
            pct = (disp / max(1, len(self.path_points) - 1)) * 100
            # Only show percentage if the player has started
            if disp > 0 or self._is_touching:
                Color(1, 1, 1, 0.9)
                from kivy.core.text import Label as CoreLabel
                lbl = CoreLabel(text=f"{pct:.0f}%", font_size=dp(14), bold=True)
                lbl.refresh()
                tex = lbl.texture
                # Position above the cursor
                tx = cx - tex.width / 2
                ty = cy + cr + dp(6)
                Rectangle(texture=tex, pos=(tx, ty), size=tex.size)
 
    # ------------------------------------------------------------------
    #  Touch Handling
    # ------------------------------------------------------------------
 
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if not self.path_points or self._is_complete:
            return True
 
        # Determine the touch target — either the start node or the current cursor position
        # This lets the player re-engage after a partial decay
        target_idx = int(self._display_index)
        target_x, target_y = self.path_points[target_idx]
        dist = math.hypot(touch.pos[0] - target_x, touch.pos[1] - target_y)
 
        if dist <= self.tolerance:
            self._stop_decay()
            self.progress_index = target_idx
            self._is_touching = True
            
            # Capture the exact initial touch
            self._current_touch_pos = touch.pos 
            
            touch.grab(self)
            self._draw_progress()
            return True
 
        return super().on_touch_down(touch)
 
    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)
        if not self.path_points or self._is_complete:
            return True
 
        # --- Two-tier look-ahead ---
        n = len(self.path_points)
        tier1_end = min(self.progress_index + self.LOOK_AHEAD, n)
 
        # Tier 1: advance window
        best_idx = None
        min_dist = self.tolerance + 1 # Start just outside tolerance
        
        for i in range(self.progress_index, tier1_end):
            px, py = self.path_points[i]
            d = math.hypot(touch.pos[0] - px, touch.pos[1] - py)
            if d <= self.tolerance:
                # THE FIX: Find the point physically CLOSEST to the finger, 
                # rather than the one furthest down the array.
                if d < min_dist:
                    min_dist = d
                    best_idx = i
 
        if best_idx is not None:
            self.progress_index = best_idx + 1
            self._display_index = float(self.progress_index)
            
            # Update position for smooth tracking
            self._current_touch_pos = touch.pos 
            
            self._draw_progress()
            
            # Check for completion (95% forgiveness)
            if self.progress_index >= n * self.COMPLETION_FRAC:
                self._complete()
                touch.ungrab(self)
            return True
 
        # Tier 2: safe-hold — check if finger is near ANY future point
        # (prevents false decay on spiral/overlapping paths)
        for i in range(tier1_end, n):
            px, py = self.path_points[i]
            d = math.hypot(touch.pos[0] - px, touch.pos[1] - py)
            if d <= self.tolerance:
                # Update position here too so it doesn't freeze visually while safe-holding
                self._current_touch_pos = touch.pos 
                self._draw_progress()
                return True
 
        # Neither tier matched — genuinely out of bounds
        self._is_touching = False
        touch.ungrab(self)
        self._start_decay()
 
        return True
 
    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if not self._is_complete:
                self._is_touching = False
                self._start_decay()
            return True
        return super().on_touch_up(touch)
 
    # ------------------------------------------------------------------
    #  Completion
    # ------------------------------------------------------------------
 
    def _complete(self):
        """Player has traced enough of the path. Fire the success callback."""
        if self._is_complete:
            return
        self._is_complete = True
        self._is_touching = False
        self._stop_decay()
        # Snap cursor to end
        self.progress_index = len(self.path_points) - 1
        self._display_index = float(self.progress_index)
        self._draw_progress()
        self.logger.info("TracePathWidget: trace completed successfully.")
        if self.callback:
            self.callback({'event': 'aim_success'})
 
    # ------------------------------------------------------------------
    #  Decay Animation
    # ------------------------------------------------------------------
 
    def _start_decay(self):
        """Begin animating the cursor back toward start along the path."""
        if self._is_complete:
            return
        self._stop_decay()
        self._decay_event = Clock.schedule_interval(self._decay_tick, 1 / 60.0)
 
    def _stop_decay(self):
        if self._decay_event:
            self._decay_event.cancel()
            self._decay_event = None
 
    def _decay_tick(self, dt):
        """Move the display cursor backward along the path each frame."""
        if self._is_touching or self._is_complete:
            self._stop_decay()
            return
 
        # Rewind along the path
        self._display_index -= self.DECAY_SPEED * dt
        if self._display_index <= 0:
            self._display_index = 0.0
            self.progress_index = 0
            self._stop_decay()
 
        # Keep logical progress in sync with the visual decay
        # (so re-engaging starts from where the cursor visually is)
        self.progress_index = int(self._display_index)
 
        self._draw_progress()
 
    # ------------------------------------------------------------------
    #  Cleanup
    # ------------------------------------------------------------------
 
    def _on_parent_change(self, instance, value):
        """Stop decay when removed from widget tree."""
        if not value:
            self._stop_decay()
 
    def __del__(self):
        self._stop_decay()
 
    # ------------------------------------------------------------------
    #  Completion
    # ------------------------------------------------------------------
 
    def _complete(self):
        """Player has traced enough of the path. Fire the success callback."""
        if self._is_complete:
            return
        self._is_complete = True
        self._is_touching = False
        self._stop_decay()
        # Snap cursor to end
        self.progress_index = len(self.path_points) - 1
        self._display_index = float(self.progress_index)
        self._draw_progress()
        self.logger.info("TracePathWidget: trace completed successfully.")
        if self.callback:
            self.callback({'event': 'aim_success'})
 
    # ------------------------------------------------------------------
    #  Decay Animation
    # ------------------------------------------------------------------
 
    def _start_decay(self):
        """Begin animating the cursor back toward start along the path."""
        if self._is_complete:
            return
        self._stop_decay()
        self._decay_event = Clock.schedule_interval(self._decay_tick, 1 / 60.0)
 
    def _stop_decay(self):
        if self._decay_event:
            self._decay_event.cancel()
            self._decay_event = None
 
    def _decay_tick(self, dt):
        """Move the display cursor backward along the path each frame."""
        if self._is_touching or self._is_complete:
            self._stop_decay()
            return
 
        # Rewind along the path
        self._display_index -= self.DECAY_SPEED * dt
        if self._display_index <= 0:
            self._display_index = 0.0
            self.progress_index = 0
            self._stop_decay()
 
        # Keep logical progress in sync with the visual decay
        # (so re-engaging starts from where the cursor visually is)
        self.progress_index = int(self._display_index)
 
        self._draw_progress()
 
    # ------------------------------------------------------------------
    #  Cleanup
    # ------------------------------------------------------------------
 
    def _on_parent_change(self, instance, value):
        """Stop decay when removed from widget tree."""
        if not value:
            self._stop_decay()
 
    def __del__(self):
        self._stop_decay()
 
class RhythmWidget(Widget):
    """A bar with a moving cursor. Player taps when cursor is in target zone."""
    def __init__(self, callback, speed=2.0, target_zone=(0.4, 0.6), **kwargs):
        super().__init__(**kwargs)
        self.callback = callback
        self.speed = speed
        self.cursor_pos = 0.0
        self.direction = 1
        self.target_zone = target_zone # normalized 0-1
        self.running = True
        self._update_event = None
        
        # Defer starting the clock until we are actually added to the widget tree
        self.bind(parent=self._on_parent_change)
        self.bind(size=self._draw, pos=self._draw)

    def _on_parent_change(self, instance, value):
        """Start clock when added to UI, stop when removed."""
        if value: # Added to parent
            self.running = True
            if self._update_event:
                self._update_event.cancel()
            self._update_event = Clock.schedule_interval(self._update, 1/60.0)
            self._draw()
        else: # Removed from parent
            self.running = False
            if self._update_event:
                self._update_event.cancel()
                self._update_event = None

    def _update(self, dt):
        if not self.running: 
            return False
        self.cursor_pos += self.direction * self.speed * dt * 0.5 
        if self.cursor_pos >= 1.0:
            self.cursor_pos = 1.0
            self.direction = -1
        elif self.cursor_pos <= 0.0:
            self.cursor_pos = 0.0
            self.direction = 1
        self._draw()

    def _draw(self, *args):
        self.canvas.clear()
        with self.canvas:
            # Track
            Color(0.2, 0.2, 0.2, 1)
            Rectangle(pos=self.pos, size=self.size)
            # Target Zone
            Color(0, 1, 0, 0.5)
            tz_x = self.x + (self.width * self.target_zone[0])
            tz_w = self.width * (self.target_zone[1] - self.target_zone[0])
            Rectangle(pos=(tz_x, self.y), size=(tz_w, self.height))
            # Center Marker
            Color(1, 1, 1, 0.5)
            Line(points=[self.center_x, self.y, self.center_x, self.top], width=1)
            # Cursor
            Color(1, 0.8, 0, 1)
            cursor_x = self.x + (self.width * self.cursor_pos) - dp(3)
            Rectangle(pos=(cursor_x, self.y), size=(dp(6), self.height))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            # Check hit (Prediction for visual feedback)
            hit_predicted = self.target_zone[0] <= self.cursor_pos <= self.target_zone[1]
            
            # Visual Feedback
            with self.canvas.after:
                Color(0, 1, 0) if hit_predicted else Color(1, 0, 0)
                Line(circle=(touch.x, touch.y, dp(20)), width=2)
            
            # DECOUPLED: Send cursor position to engine for authoritative validation
            self.callback({'event': 'rhythm_tap', 'cursor_pos': self.cursor_pos, 'predicted_success': hit_predicted})
            return True
        return False

class ReactionWidget(Widget):
    """Button that waits then flashes."""
    def __init__(self, callback, wait_range=(2.0, 4.0), **kwargs):
        # Set ALL instance attributes BEFORE super().__init__
        # because Kivy may dispatch property changes (including parent)
        # during Widget.__init__ on Android/Cython builds.
        self.logger = logging.getLogger('ReactionWidget')
        self.callback = callback
        self.wait_range = wait_range
        self.active = False
        self.start_time = 0
        self._activation_event = None
        
        super().__init__(**kwargs)
        
        self.bind(size=self._draw, pos=self._draw, parent=self._on_parent_change)
        self._draw()

    def _on_parent_change(self, instance, value):
        import random
        if value:  # Added to tree
            # Clamp wait range so the button always turns green before the QTE ends.
            # Find the popup's duration to use as the upper bound.
            popup = self
            while popup and not isinstance(popup, Popup):
                popup = popup.parent
            max_wait = getattr(popup, 'duration', 8.0) * 0.5 if popup else 3.0
            
            lo = min(self.wait_range[0], max_wait * 0.5)
            hi = min(self.wait_range[1], max_wait)
            delay = random.uniform(lo, hi)
            
            self.logger.info(f"ReactionWidget: Will activate in {delay:.1f}s (range [{lo:.1f}, {hi:.1f}])")
            if self._activation_event: self._activation_event.cancel()
            self._activation_event = Clock.schedule_once(self.activate, delay)
        else:
            if self._activation_event:
                self._activation_event.cancel()
                self._activation_event = None

    def activate(self, dt):
        self.active = True
        import time
        self.start_time = time.time()
        self._draw() # Redraw (Green/Go)

    def _draw(self, *args):
        self.canvas.clear()
        with self.canvas:
            if self.active:
                Color(0, 1, 0, 1) # GREEN
            else:
                Color(0.5, 0, 0, 1) # RED/DIM
            Rectangle(pos=self.pos, size=self.size)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self.active:
                import time
                from .utils import glitch_text
                diff = time.time() - self.start_time
                self.callback({'event': 'reaction_tap', 'reaction_time': diff})
            else:
                # Early tap
                self.callback({'event': 'reaction_tap', 'reaction_time': 999.0})
            return True
        return False

class ContextDockWidget(BoxLayout):
    """Controller for the command button dock."""
    core_grid = ObjectProperty(None)
    context_grid = ObjectProperty(None)
    
    def update(self, game_logic):
        try:
            self._update_core_actions()
            self._update_context_actions(game_logic)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in ContextDockWidget.update: {e}", exc_info=True)

    def _update_core_actions(self):
        if not self.core_grid: return
        self.core_grid.clear_widgets()
        core_actions = ['move', 'examine', 'wait', 'roster', 'save', 'main menu']
        for verb in core_actions:
            # Use Factory.TerminalButton instead of Button
            btn = Factory.TerminalButton(
                text=verb.capitalize()
            )
            btn.bind(on_release=lambda _, v=verb: self.on_command(v))
            self.core_grid.add_widget(btn)

    def _update_context_actions(self, game_logic):
        if not self.context_grid: return
        self.context_grid.clear_widgets()
        if not game_logic: return

        context_actions = self._gather_context_actions(game_logic)
        seen = set()
        context_actions = [x for x in context_actions if not (x in seen or seen.add(x))]

        for verb in context_actions:
            # Use Factory.TerminalButton instead of Button
            btn = Factory.TerminalButton(
                text=verb.capitalize()
            )
            btn.bind(on_release=lambda _, v=verb: self.on_command(v))
            self.context_grid.add_widget(btn)

    def _gather_context_actions(self, game_logic):
        actions = []
        try:
            room_id = getattr(game_logic.player, 'get', lambda x: None)('location')
            room_data = game_logic.get_room_data(room_id) or {}
            items_master = game_logic.resource_manager.get_data('items', {})
            hazards_master = game_logic.resource_manager.get_data('hazards', {})
            hazard_engine = getattr(game_logic, 'hazard_engine', None)

            # 1. Standard Checks (Locked, Breakables, Containers)
            if self._has_locked_things(game_logic, room_data): actions.extend(['unlock', 'force'])
            if hasattr(game_logic, 'last_dialogue_context') and getattr(game_logic.last_dialogue_context, 'get', lambda x: None)('options'): actions.append('respond')
            if self._has_breakables(game_logic, room_data): actions.append('break')
            if self._has_containers(game_logic, room_data): actions.append('search')
            
            # --- THE FIX: Omni-Lookup for Dock Verbs ---
            npcs_master = game_logic.resource_manager.get_data('npcs', {})
            all_npcs = list(room_data.get('npcs', []) + room_data.get('npcs_present', []))
            
            # Catch active companions too!
            for companion in getattr(game_logic.player, 'get', lambda x, y: [])('companions', []):
                if companion not in all_npcs: all_npcs.append(companion)
                
            for npc in all_npcs:
                npc_id = npc.get('id', npc.get('name', '')) if isinstance(npc, dict) else npc
                npc_data = npcs_master.get(npc_id) or npcs_master.get('npcs', {}).get(npc_id, {})
                
                # Fetch the dynamic verb (talk, examine, use) and add it to the Dock!
                verb = npc_data.get('action_verb', 'talk')
                if verb not in actions: actions.append(verb)

            try:
                for furniture in room_data.get('furniture', []):
                    if isinstance(furniture, dict) and furniture.get('is_container'):
                        flag_name = f"searched_{furniture.get('name', '')}"
                        if flag_name in getattr(game_logic, 'interaction_flags', {}):
                            for key in furniture.get('items', []):
                                item_data = items_master.get(key, {})
                                if item_data.get("takeable", False):
                                    actions.append('take'); break
            except Exception: pass

            try:
                inventory = game_logic.player.get('inventory', [])
                for item in inventory:
                    key = item if isinstance(item, str) else item.get('id') or item.get('key') or item.get('name')
                    item_data = items_master.get(key, {})
                    if item_data.get("is_usable", False) or item_data.get("usable", False):
                        actions.append('use'); break
            except Exception: pass

            # >>> PATCH START: Scan Active Hazards for Dynamic Verbs <<<
            # This detects "answer" when the phone is ringing, or "fix" when a generator is broken.
            if hazard_engine:
                # Get hazards in current room
                active_hazards = hazard_engine.get_room_hazards_descriptions(room_id)
                for hid, h_inst in active_hazards.items():
                    # 1. Get Master Data & Current State
                    master = h_inst.get('master_data', {})
                    current_state = h_inst.get('state')
                    
                    # 2. Check State-Specific Interactions (Highest Priority)
                    # e.g. "ringing": { "player_interaction": { "answer": ... } }
                    state_def = master.get('states', {}).get(current_state, {})
                    state_interactions = state_def.get('player_interaction', {})
                    for verb in state_interactions.keys():
                        if verb not in actions: actions.append(verb)

                    # 3. Check Global Hazard Interactions (if state matches)
                    # e.g. "player_interaction": { "kick": [ { "requires_hazard_state": ["docked"] } ] }
                    global_interactions = master.get('player_interaction', {})
                    for verb, rules in global_interactions.items():
                        for rule in rules:
                            req_states = rule.get('requires_hazard_state')
                            # If rule has no state req, or matches current state, add the verb
                            if not req_states or current_state in req_states:
                                if verb not in actions: actions.append(verb)
                                break
            # >>> PATCH END <<<

            try:
                world_items = getattr(game_logic, 'current_level_items_world_state', {})
                for item_key, item_state in world_items.items():
                    if item_state.get('location') == room_id:
                        actions.append('take'); break
            except Exception: pass
        except Exception as e:
            logging.getLogger(__name__).error(f"_gather_context_actions error: {e}")
        return actions
    
    def _has_locked_things(self, gl, room_data) -> bool:
        for dest in (room_data.get('exits') or {}).values():
            if isinstance(dest, str):
                dest_data = gl.get_room_data(dest) or {}
                if dest_data.get('locked') or dest_data.get('locked_by_mri'): return True
        for f in room_data.get('furniture', []):
            if isinstance(f, dict) and f.get('locked'): return True
        return False
    
    def _has_breakables(self, gl, room_data) -> bool:
        for f in room_data.get('furniture', []):
            if isinstance(f, dict) and f.get('is_breakable'): return True
        return False
    
    def _has_containers(self, gl, room_data) -> bool:
        for f in room_data.get('furniture', []):
            if isinstance(f, dict) and (f.get('is_container') or f.get('contains_items')): return True
        return False
    
    def on_command(self, verb: str): pass

class ContextualActionsWidget(ScrollView):
    """Controller for contextual target buttons."""
    grid = ObjectProperty(None)
    action_callback = None

    def populate(self, buttons: list):
        if not self.grid: return
        self.grid.clear_widgets()
        for btn in buttons:
            btn.size_hint = (1, None)
            self.grid.add_widget(btn)

    def clear_buttons(self):
        if self.grid: self.grid.clear_widgets()

    def _add_target_button(self, text, verb, target_cmd):
        btn = Factory.TerminalButton(
            text=text
        )
        btn.halign = 'center' 
        btn.bind(on_release=lambda _, v=verb, t=target_cmd: self.on_target_selected(v, t))
        self.add_button(btn)

    def on_target_selected(self, verb, target):
        if self.action_callback:
            command = f"{verb} {target}"
            self.action_callback(command)

    def add_button(self, button):
        self.grid.add_widget(button)
    
    def populate_contextual_targets(self, game_logic, verb: str):
        self.clear_buttons()
        room_id = game_logic.player.get('location')
        room_data = game_logic.get_room_data(room_id) or {}
        items_master = game_logic.resource_manager.get_data('items', {})
        
        char_class = game_logic.player.get('character_class', '')

        # --- THE ULTIMATE MEDIUM GATE ---
        blocked_aliases = {
            "death's presence", "deaths presence", "death's breath", "deaths breath",
            "dark presence", "cold breeze", "sudden draft", "chilling air", 
            "malevolent gust", "ominous shadow"
        }

        def _is_blocked(item_name, item_id=""):
            if char_class == 'Medium': 
                return False
            n_name = str(item_name).lower().replace('_', ' ')
            n_id = str(item_id).lower().replace('_', ' ')
            return (n_name in blocked_aliases) or (n_id in blocked_aliases)
        # --------------------------------

        # 1. EXITS
        for direction, dest in (room_data.get('exits') or {}).items():
            is_locked = False
            dest_name = direction.title() # Safe fallback
            
            # --- THE FIX: Support Dictionary Exits (Level Transitions) ---
            if isinstance(dest, dict):
                is_locked = bool(dest.get('locked'))
            elif isinstance(dest, str):
                dest_live = getattr(game_logic, 'current_level_rooms_world_state', {}).get(dest, {})
                dest_master = game_logic.get_room_data(dest) or {}
                locking = dest_master.get('locking', {}) if isinstance(dest_master.get('locking'), dict) else {}
                is_locked = bool(dest_live.get('locked') or dest_master.get('locked') or locking.get('locked'))
                dest_name = dest.replace('_', ' ').title()
            else:
                continue

            if verb in ['move', 'go']:
                self._add_target_button(f"{direction.title()} ({dest_name})", verb, direction)
            elif verb in ['unlock', 'force'] and is_locked:
                self._add_target_button(f"{direction.title()} Door", verb, direction)
            # -------------------------------------------------------------

        # 2. OBJECTS & HAZARD ENTITIES
        triggers = room_data.get('interactable_triggers', {})
        for obj in room_data.get('objects', []):
            if isinstance(obj, dict):
                name = obj.get('name', '')
                h_key = obj.get('hazard_key', '')
                
                # Check the gate!
                if _is_blocked(name, h_key): continue
                
                if verb in ['examine', 'use']:
                    self._add_target_button(name, verb, h_key or name)
            
            elif isinstance(obj, str):
                # Check the gate!
                if _is_blocked(obj, obj): continue
                
                obj_triggers = triggers.get(obj, {})
                t_verb = obj_triggers.get('on_action', '')
                
                if t_verb == verb or verb in ['use', 'examine']:
                    self._add_target_button(obj.replace('_', ' '), verb, obj)

        # 3. INVENTORY
        if verb in ['examine', 'use', 'drop']:
            inventory = game_logic.player.get('inventory', [])
            if isinstance(inventory, dict): inventory = list(inventory.keys())
            for item_key in inventory:
                if _is_blocked(item_key, item_key): continue
                item_data = items_master.get(item_key, {})
                item_name = item_data.get('name', str(item_key))
                if verb == 'use' and not item_data.get('is_usable', False) and not item_data.get('usable', False): continue
                self._add_target_button(item_name, verb, item_key)

        # 4. FURNITURE
        for furniture in room_data.get('furniture', []):
            if isinstance(furniture, dict):
                fname = furniture.get('name', '')
                if _is_blocked(fname, fname): continue
                if verb == 'search' and not furniture.get('is_container', False): continue
                if verb == 'use' and not furniture.get('use_interaction') and not furniture.get('is_usable'): continue
                self._add_target_button(fname, verb, fname)
            elif isinstance(furniture, str):
                if _is_blocked(furniture, furniture): continue
                if verb in ['examine', 'search']:
                    self._add_target_button(furniture.replace('_', ' '), verb, furniture)

        # 5. FLOOR ITEMS
        for item_key, item_state in getattr(game_logic, 'current_level_items_world_state', {}).items():
            if item_state.get('location') == room_id:
                if _is_blocked(item_key, item_key): continue
                item_data = items_master.get(item_key, {})
                item_name = item_data.get('name', str(item_key))
                if verb == 'take' and not item_data.get('takeable', False): continue
                self._add_target_button(item_name, verb, item_key)

        # 6. NPCS, COMPANIONS & INTERACTIVE STATE MACHINES
        npcs_master = game_logic.resource_manager.get_data('npcs', {})
        
        # Merge room NPCs with Active Companions!
        npcs_in_room = list(room_data.get('npcs', []) + room_data.get('npcs_present', []))
        for companion in getattr(game_logic.player, 'get', lambda x, y: [])('companions', []):
            if companion not in npcs_in_room:
                npcs_in_room.append(companion)
        
        for npc in npcs_in_room:
            npc_id = npc.get('id', npc.get('name', '')) if isinstance(npc, dict) else npc
            if _is_blocked(npc_id, npc_id): continue
            
            # --- THE FIX: Respect Embedded Room Dictionaries! ---
            # Look up in master, but IF NOT FOUND, use the local embedded dictionary!
            master_data = npcs_master.get(npc_id) or npcs_master.get('npcs', {}).get(npc_id, {})
            actual_data = master_data if master_data else (npc if isinstance(npc, dict) else {})
            
            expected_verb = str(actual_data.get('action_verb', 'talk')).lower()
            safe_verb = str(verb).lower()
            valid_verbs = ['examine', 'talk', 'use', 'interact']
            
            if safe_verb in valid_verbs or safe_verb == expected_verb:
                display_name = actual_data.get('name', npc_id.replace('_', ' ').title())
                self._add_target_button(display_name, verb, npc_id)
                
        # Back Button Generation
        from kivy.factory import Factory
        
        back_btn = Factory.TerminalButton(
            text="< Back", 
            background_color=(0.3, 0.3, 0.3, 1)
            # STRIPPED: size_hint_y and height (KV will now handle this dynamically!)
        )
        back_btn.bind(on_release=lambda x: self.clear_buttons())
        self.grid.add_widget(back_btn)