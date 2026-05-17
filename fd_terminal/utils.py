# fd_terminal/utils.py
import os
import sys
import json
import logging
import random
import tempfile
import shutil
from kivy.utils import platform
from typing import Optional

# We do not import ResourceManager at the top level to avoid circular imports.
# We type hint it as 'Any' or pass it dynamically.

logger = logging.getLogger("Utils")

def get_user_data_dir():
    """
    Returns a cross-platform user data directory.
    Essential for writing files on Android/iOS where the app dir is read-only.
    """
    app_name = "finaldestination"
    
    # Windows
    if platform == 'win':
        return os.path.join(os.environ['APPDATA'], app_name)
    # macOS
    elif platform == 'macosx':
        return os.path.expanduser(f"~/Library/Application Support/{app_name}")
    # Linux / Android (Kivy handles Android paths automatically via generic internal storage)
    else:
        return os.path.expanduser(f"~/.{app_name}")

# utils.py
def normalize_text(text: str) -> str:
    if not text: return ""
    return str(text).strip().lower().replace('_', ' ')

def color_text(text: str, category: str, rm) -> str:
    """
    Wraps text in Kivy markup color tags based on predefined categories.
    """
    if not text:
        return ""
        
    # 1. Fully Expanded Safety Net for DARK backgrounds
    default_colors = {
        # Core UI & Narrative
        'special': '#b266ff',     # Neon Purple
        'warning': '#ff4444',     # Bright Red
        'hazard': '#ff4444',      # Bright Red
        'error': '#ff4444',       # Bright Red
        'action': '#00ff00',      # Neon Green
        'success': '#00ff00',     # Neon Green
        'flavor': '#aaaaaa',      # Light Gray
        'system': '#aaaaaa',      # Light Gray
        
        # World Entities
        'npc': '#00ffff',         # Neon Cyan
        'location': '#ffff00',    # Neon Yellow
        'room': '#ffff00',        # Neon Yellow
        'room_name': '#ffff00',   # Neon Yellow
        'exit': '#00ccff',        # Bright Blue
        
        # Interactables
        'item': '#33ff33',        # Bright Green
        'evidence': '#ffb366',    # Light Orange
        'furniture': '#cc9900',   # Gold/Brown
        'object': '#ff99cc'       # Pink
    }
    
    # 2. Try to get colors from constants.json, fallback to safety net
    try:
        colors = rm.get_data('constants', {}).get('text_colors') or default_colors
    except Exception:
        colors = default_colors
        
    # 3. Get the specific category color, fallback to White
    hex_color = colors.get(category, default_colors.get(category, '#ffffff'))
    
    # 4. Kivy's markup parser requires the '#' prefix
    if not hex_color.startswith('#'):
        hex_color = f"#{hex_color}"
        
    return f"[color={hex_color}]{text}[/color]"

def obfuscate_text_dim(text: str, visibility_factor: float = 0.3) -> str:
    """
    Simulates reading in dim light. 
    visibility_factor: 0.0 (Pitch Black) to 1.0 (Perfect Vision).
    Replaces characters with '.' or similar low-contrast chars.
    """
    if not text: return ""
    
    output = []
    i = 0
    n = len(text)
    
    # Characters to obscure with
    blur_chars = ['.', ',', '-', ' ', '`']
    
    while i < n:
        # 1. Skip Kivy Markup Tags (CRITICAL)
        if text[i] == '[':
            close_bracket = text.find(']', i)
            if close_bracket != -1:
                output.append(text[i:close_bracket+1])
                i = close_bracket + 1
                continue
        
        char = text[i]
        
        # 2. Obfuscate Logic
        # Spaces and punctuation usually remain visible
        if not char.isalnum():
            output.append(char)
        else:
            # Chance to miss the character based on visibility
            if random.random() > visibility_factor:
                output.append(random.choice(blur_chars))
            else:
                output.append(char)
        
        i += 1
        
    return "".join(output)

def glitch_text(text: str, intensity: float) -> str:
    """
    Corrupts text based on intensity (0.0 to 1.0).
    Preserves Kivy markup tags [...] to prevent rendering errors.
    """
    if intensity <= 0.1:
        return text

    # Zalgo-lite chars and confusables
    glitch_chars = ['¡', '!', '?', '.', ',', ';', ':', '|', '/', '\\', '_', '-', '~', '^', '`', "'", '"']
    replacements = {'e': '3', 'a': '@', 'l': '1', 'o': '0', 's': '5', 't': '7'}
    
    output = []
    i = 0
    n = len(text)
    
    while i < n:
        # Skip over Kivy markup tags [color=...] or [/color]
        if text[i] == '[':
            close_bracket = text.find(']', i)
            if close_bracket != -1:
                output.append(text[i:close_bracket+1])
                i = close_bracket + 1
                continue
        
        char = text[i]
        
        # Chance to glitch is proportional to intensity
        if random.random() < (intensity * 0.05): # 5% chance at max fear per char
            effect_roll = random.random()
            
            if effect_roll < 0.4 and char.lower() in replacements:
                # Leet speak swap
                output.append(replacements[char.lower()])
            elif effect_roll < 0.7:
                # Stutter
                output.append(char + '-' + char)
            elif effect_roll < 0.9:
                # Random symbol injection
                output.append(random.choice(glitch_chars))
            else:
                # Case swap
                output.append(char.swapcase())
        else:
            output.append(char)
        
        i += 1
        
    return "".join(output)

def get_save_dir() -> str:
    """
    Ensures the save directory exists and returns its path.
    Tries Kivy App user_data_dir first, falls back to OS specific paths.
    """
    try:
        from kivy.app import App
        app = App.get_running_app()
        if app:
            save_dir = os.path.join(app.user_data_dir, 'saves')
        else:
            raise AttributeError("App not running")
    except (ImportError, AttributeError):
        # Fallback for testing or pre-init
        save_dir = os.path.join(get_user_data_dir(), 'saves')
    
    if not os.path.exists(save_dir):
        try:
            os.makedirs(save_dir, exist_ok=True)
            logger.info(f"Created save directory at: {save_dir}")
        except OSError as e:
            logger.error(f"Failed to create save directory: {e}")
            
    return save_dir

def get_save_filepath(slot_identifier: str = "quicksave") -> str:
    """
    Generates the absolute filepath for a given save slot identifier.
    Sanitizes the input to prevent file system errors.
    """
    # Sanitize: Allow only Alphanumeric, spaces, underscores, dashes
    safe_id = "".join(c for c in slot_identifier if c.isalnum() or c in (' ', '_', '-')).strip()
    if not safe_id:
        safe_id = "unnamed_save"
        
    filename = f"{safe_id}.json"
    return os.path.join(get_save_dir(), filename)

def json_serializer(obj):
    """
    Helper to convert Python objects (like Sets) that JSON cannot handle.
    """
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

def save_data_to_json(data: dict, filepath: str) -> bool:
    """
    Safely writes data to a JSON file using an atomic write pattern
    (write to temp, then rename) to prevent corruption on crash.
    """
    try:
        # 1. Create a temporary file in the same directory
        dir_name = os.path.dirname(filepath)
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
            # 2. Dump JSON using our custom serializer for Sets
            json.dump(data, tf, indent=2, ensure_ascii=False, default=json_serializer)
            temp_name = tf.name
        
        # 3. Atomic rename (overwrites destination safely)
        shutil.move(temp_name, filepath)
        return True
        
    except Exception as e:
        logger.error(f"Failed to write save file '{filepath}': {e}", exc_info=True)
        if 'temp_name' in locals() and os.path.exists(temp_name):
            os.remove(temp_name)
        return False

def get_save_slot_info(slot_id: str) -> Optional[dict]:
    """
    Reads the 'save_info' block from a save file for UI previews.
    """
    save_path = get_save_filepath(slot_id)
    if not os.path.exists(save_path):
        return None
        
    try:
        with open(save_path, 'r', encoding="utf-8") as f:
            # Load the whole thing (JSON doesn't support partial reads easily)
            # But since it's local, it's fast enough.
            save_data = json.load(f)

        info = save_data.get("save_info", {})
        return {
            "timestamp": info.get("timestamp", "No date"),
            "location": info.get("location", "?"),
            "character_class": info.get("character_class", "Unknown"),
            "turns_left": info.get("turns_left", "--"),
            "score": info.get("score", 0),
            "corrupted": False
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Save file for slot '{slot_id}' appears corrupted: {e}")
        return {"corrupted": True, "timestamp": "Corrupted File"}
    except Exception as e: 
        logger.error(f"Error reading save slot info for '{slot_id}': {e}", exc_info=True)
        return {"corrupted": True, "timestamp": "Read Error"}

# --- SETTINGS MANAGEMENT ---

def get_settings_filepath():
    return os.path.join(get_save_dir(), 'settings.json')

def load_user_settings() -> dict:
    """Loads settings from disk, or returns defaults if missing."""
    path = get_settings_filepath()
    defaults = {
        "text_scale": 1.0,      # 1.0 = 100% (Normal)
        "music_volume": 0.8,    # 0.0 to 1.0
        "theme": "Dark"         # "Dark" or "Light"
    }
    
    if not os.path.exists(path):
        return defaults
        
    try:
        with open(path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            # Merge saved with defaults to ensure new keys exist
            defaults.update(saved)
            return defaults
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return defaults

def save_user_settings(settings_dict: dict):
    """Writes the settings dictionary to disk."""
    path = get_settings_filepath()
    # Use the existing atomic save function
    save_data_to_json(settings_dict, path)

# --- SANDBOX HELPERS ---

def get_related_items(hazard_key: str) -> list[str]:
    """
    Returns a list of item keys that are logically related to a given hazard.
    Used for 'Smart Stocking' in Sandbox Mode.
    """
    mapping = {
        'deaths_breath': ['candle', 'flashlight', 'lighter', 'matches', 'thermometer', 'camera'],
        'robo_vacuum': ['vacuum_battery', 'screwdriver', 'kicking_boots', 'robot_vacuum_item', 'broken_vacuum_parts'],
        'mri': ['scalpel', 'radiology_key_card', 'oxygen_tank', 'gurney', 'wheelchair'],
        'hospital_exit': ['bludworths_house_key', 'revolving_door', 'crowbar', 'exit_sign'],
        'gas_leak': ['lighter', 'matches', 'wrench', 'valve_wheel', 'mask'],
        'oxygen_tank': ['wrench', 'mask', 'tubing'],
        'electrified_fence': ['insulating_tape', 'rubber_gloves', 'wire_cutters'],
        'water_puddle': ['mop', 'bucket', 'wet_floor_sign', 'towel'],
        'frayed_lamp_cord': ['electrical_tape', 'scissors'],
        'liquid_spill': ['coaster', 'towel'],
        'spilled_hot_oil': ['sand_bucket', 'extinguisher', 'warning_layout'],
        'faulty_generator': ['wrench', 'fuel_can', 'spark_plug'],
        'wobbling_ceiling_fan': ['screwdriver', 'ladder', 'helmet'],
        'falling_scaffolding': ['hardhat', 'warning_tape'],
        'wrecking_ball': ['crane_key', 'sledgehammer'],
        'malfunctioning_ventilator': ['filter', 'screwdriver'],
        'elevator_freefall': ['elevator_key', 'maintenance_log'],
        'photo_booth_electrocution': ['coins', 'photo_strip'],
        'test_your_strength_game': ['hammer', 'token'],
        'prize_bull': ['lasso', 'cowboy_hat', 'red_flag'],
        'bull_pen_gate': ['padlock_key', 'bolt_cutters'],
        'stampeding_bull': ['red_cape', 'tranquilizer_dart'],
        'propane_tanks': ['lighter', 'valve_handle'],
        'propane_tank_explosion': ['extinguisher'],
        'food_stall_structure': ['support_beam'],
    }
    return mapping.get(hazard_key, [])

class TraceLogger:
    def __init__(self, logger_name="Trace"):
        self.trace = []
        import logging
        self.logger = logging.getLogger(logger_name)

    def mark(self, step: str, **data):
        """Records a timestamped snapshot of variables for debugging."""
        entry = {"step": step, **data}
        self.trace.append(entry)
        self.logger.debug(f"[{step}] | {data}")
        
    def dump(self):
        """Returns the full trace array for the crash log."""
        return self.trace