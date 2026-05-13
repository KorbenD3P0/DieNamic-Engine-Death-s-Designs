# fd_terminal/main.py
"""
The Alpha.
"""
import os
import json
import logging
from datetime import datetime
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, SlideTransition
from kivy.uix.label import Label
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty
from kivy.utils import platform
# Import all screen classes from the UI module
from .ui import (
    register_and_scale_fonts, get_scaled_font_size, 
    TitleScreen, IntroScreen, CharacterSelectScreen, TutorialScreen,
    GameScreen, WinScreen, LoseScreen, LoadGameScreen, SaveGameScreen,
    AchievementsScreen, JournalScreen, InterLevelScreen, SettingsScreen, SandboxConfigurationScreen
)
from .audio_manager import AudioManager
from kivy.lang import Builder
from .resource_manager import ResourceManager
from .hazard_engine import HazardEngine
from .achievements import AchievementsSystem
from .death_ai import DeathAI
from .utils import load_user_settings, save_user_settings

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
font_dir = os.path.join(project_root, 'assets', 'fonts')

# LOAD FONTS IMMEDIATELY (Before the App class is defined or .kv is parsed)
scaling_factors, thematic_font = register_and_scale_fonts(font_dir)


class FinalDestinationApp(App):
    # Add Kivy Properties so UI can bind to them automatically!
    safe_top = NumericProperty(0)
    safe_bottom = NumericProperty(0)
    safe_left = NumericProperty(0)
    safe_right = NumericProperty(0)
    text_scale = NumericProperty(1.0)
    theme_mode = StringProperty("Dark")
    kv_file = None

    def __init__(self, **kwargs):
        """
        The moment of conception for the App.
        Core, non-visual systems are forged here to ensure they exist for the entire app lifecycle.
        """
        super().__init__(**kwargs)
        
        # 1. Load Settings
        self.user_settings = load_user_settings()
        
        # 2. Apply to Properties (UI will bind to these)
        self.text_scale = self.user_settings.get('text_scale', 1.0)
        self.theme_mode = self.user_settings.get('theme', 'Dark')
        
        # Configure logging FIRST
        self._configure_app_logging()
        
        # --- 1. Forge the Grand Library (ResourceManager) ---
        self.resource_manager = ResourceManager()
        self.resource_manager.load_master_data()
        
        # --- 2. Apply Audio (after Audio Manager is created) ---
        self.audio_manager = AudioManager(self.resource_manager)
        self.audio_manager.set_master_volume(self.user_settings.get('music_volume', 0.8))
        
        # --- 3. Appoint the Chronicler (AchievementsSystem) ---
        self.achievements_system = AchievementsSystem(
            notify_callback=None,
            resource_manager=self.resource_manager
        )

        # --- 4. Ignite the Engine of Calamity (HazardEngine) ---
        self.hazard_engine = HazardEngine(resource_manager=self.resource_manager)
        self.game_logic = None
        self.death_ai = None
        self.qte_engine = None

        # --- FONT SCALING ---
        font_dir = os.path.join(os.path.dirname(__file__), '..', 'assets', 'fonts')
        from . import ui
        ui.FONT_SIZE_SCALING, chosen = register_and_scale_fonts(font_dir)
        self.thematic_font_name = chosen or "RobotoMonoBold"
        self.get_scaled_font_size = get_scaled_font_size
        # Kivy will automatically load 'finaldestination.kv' because of the App name.
        
    def create_new_game_session(self, character_class: str, sandbox_config: dict = None):
        """Centralized factory so all screens consistently build a session."""
        from .game_logic import GameLogic
        from .death_ai import DeathAI
        from .qte_engine import QTE_Engine

        # 1. RESET PERSISTENT SYSTEMS
        # Since HazardEngine is shared across sessions, we must wipe it clean.
        if self.hazard_engine:
            self.hazard_engine.reset()

        # Create all objects first
        self.game_logic = GameLogic(resource_manager=self.resource_manager)
        self.qte_engine = QTE_Engine(
            resource_manager=self.resource_manager,
            game_logic_ref=self.game_logic
        )
        self.death_ai = DeathAI(self.game_logic)

        # Set all cross-references
        self.hazard_engine.game_logic = self.game_logic
        self.game_logic.hazard_engine = self.hazard_engine
        self.game_logic.qte_engine = self.qte_engine
        self.game_logic.achievements_system = self.achievements_system
        self.game_logic.death_ai = self.death_ai
        self.game_logic.audio_manager = self.audio_manager
        
        # --- DETERMINE START LEVEL ---
        # If sandbox_config provided, use "gym" for procedural/sandbox
        # Otherwise check legacy sandbox_mode setting or default to level 1
        if sandbox_config:
            start_level = "gym"
            self.logger.info("Sandbox Config provided: Starting in 'gym' level.")
        elif self.user_settings.get('sandbox_mode', False):
            start_level = "gym"
            self.logger.info("Sandbox Mode ACTIVE: Starting in 'gym' level.")
        else:
            start_level = 0

        # Start the game ONCE with proper config
        start_response = self.game_logic.start_new_game(
            character_class=character_class,
            start_level=start_level,
            sandbox_config=sandbox_config
        )
        self.game_logic.start_response = start_response

        self.logger.info(f"Game session created. HazardEngine.game_logic set: {self.hazard_engine.game_logic is not None}")
        return self.game_logic

    def build(self):
        font_dir = os.path.join(project_root, 'assets', 'fonts')
        """
        The genesis of the VISUALS.
        """
        try:
            Window.softinput_mode = 'below_target'

            self.title = "DieNamic Engine: Death's Designs"
            self.icon = 'assets/icon.png'
            # --- Construct the Oracle's Window (ScreenManager) ---
            sm = ScreenManager(transition=SlideTransition(direction='left', duration=0.25))
            
            sm.add_widget(TitleScreen(
                name='title', 
                achievements_system=self.achievements_system,
                resource_manager=self.resource_manager
            ))
            sm.add_widget(CharacterSelectScreen(
                name='character_select',
                resource_manager=self.resource_manager
            ))
            sm.add_widget(IntroScreen(name='intro', resource_manager=self.resource_manager))
            sm.add_widget(TutorialScreen(name='tutorial', resource_manager=self.resource_manager))
            sm.add_widget(GameScreen(name='game', resource_manager=self.resource_manager))
            sm.add_widget(WinScreen(name='win', resource_manager=self.resource_manager))
            sm.add_widget(LoseScreen(name='lose', resource_manager=self.resource_manager))
            sm.add_widget(LoadGameScreen(name='load_game', resource_manager=self.resource_manager))
            sm.add_widget(SaveGameScreen(name='save_game', resource_manager=self.resource_manager))
            sm.add_widget(AchievementsScreen(name='achievements', resource_manager=self.resource_manager))
            sm.add_widget(JournalScreen(name='journal', achievements_system=self.achievements_system, resource_manager=self.resource_manager))
            sm.add_widget(InterLevelScreen(name='inter_level', resource_manager=self.resource_manager))
            sm.add_widget(SettingsScreen(name='settings', resource_manager=self.resource_manager))
            sm.add_widget(SandboxConfigurationScreen(name='sandbox_select', resource_manager=self.resource_manager))

            sm.bind(current=self.on_screen_changed)

            sm.current = 'title'
            self.logger.info("FinalDestinationApp build() completed successfully.")
            return sm

        except Exception as e:
            logging.critical(f"FATAL BUILD ERROR: {e}", exc_info=True)
            return Label(text=f"Fatal Error: {e}")

    def trigger_vibration(self, duration=0.3):
        """Triggers a physical device vibration if supported by the hardware."""
        try:
            from plyer import vibrator
            vibrator.vibrate(time=duration)
            self.logger.debug(f"Device vibrated for {duration} seconds.")
        except NotImplementedError:
            # This will naturally hit on Windows/Mac/Linux where vibration isn't a thing
            self.logger.debug("Vibration requested, but not supported on this platform.")
        except Exception as e:
            self.logger.warning(f"Could not trigger vibration: {e}")

    def save_app_settings(self):
        """Helper to save current state to disk."""
        self.user_settings['text_scale'] = self.text_scale
        self.user_settings['theme'] = self.theme_mode
        self.user_settings['music_volume'] = self.audio_manager.master_volume
        save_user_settings(self.user_settings)

    def on_start(self):
        """Initializes the game engine and loads the first screen."""
        self.logger.info("Application starting.")

        # Logging is already configured in __init__; call setup_logging only if present.
        if hasattr(self, "setup_logging") and callable(self.setup_logging):
            try:
                self.setup_logging()
            except Exception as e:
                self.logger.warning(f"setup_logging() failed: {e}")

        # --- Bind to hardware safe zones ---
        try:

            if hasattr(Window, "system_padding"):
                def _apply_system_padding(_window, padding):
                    try:
                        # Expected order: (left, top, right, bottom)
                        self.safe_left, self.safe_top, self.safe_right, self.safe_bottom = padding
                    except Exception:
                        self.update_safe_area()

                Window.bind(system_padding=_apply_system_padding)
                _apply_system_padding(Window, Window.system_padding)
            else:
                # Fallback for environments without system_padding
                if platform == "android":
                    self.safe_top = dp(35)
                    self.safe_bottom = dp(45)
                elif platform == "ios":
                    self.safe_top = dp(45)
                    self.safe_bottom = dp(30)

            # Android-specific window layout fix
            if platform == "android" and hasattr(self, "_android_fix_window_layout"):
                self._android_fix_window_layout()

        except Exception as e:
            self.logger.warning(f"Safe-area setup failed: {e}")
            self.update_safe_area()

        self._cleanup_corrupted_saves()
        self.achievements_system.load_achievements()

        # Start title music on initial launch
        if hasattr(self, "audio_manager"):
            self.audio_manager.play_music("title_theme")

    def on_stop(self):
        """Called when the application is closing."""
        self.logger.info("Application stopping.")
        self.achievements_system.save_achievements()

    def on_screen_changed(self, instance, screen_name):
        """
        The Watcher. Reacts when the screen changes.
        Uses a map to determine which track belongs to which reality.
        """
        self.logger.info(f"Screen transition detected: -> {screen_name}")

        # 1. The Map of Resonance
        # Map screen names to audio.json music keys. 
        # Use None to command silence.
        screen_music_map = {
            'title': 'title_theme',
            'settings': 'intro_theme',
            'character_select': 'intro_theme',
            'intro': 'intro_theme',
            'tutorial': 'title_theme',
            'achievements': 'title_theme',
            'load_game': 'title_theme',
            'save_game': 'title_theme',
            # The Reality
            'game': None,  # Silence (or 'lobby_ambience' if you prefer)
            
            # The Endings (Currently defaulting to None/Silence, or add keys here)
            'lose': 'game_over_theme',
            'win': 'victory_theme',
            'inter_level': None 
        }

        # 2. Determine the Target
        # Default to 'title_theme' if a new screen is added and not mapped? 
        # Or default to None? Let's default to keeping the current logic safe:
        target_track = screen_music_map.get(screen_name, 'title_theme')

        # 3. Execute the Command
        if target_track:
            # If mapped to a song, play it (AudioManager handles continuity automatically)
            self.audio_manager.play_music(target_track)
        else:
            # If mapped to None, fade out the music
            # We use a longer fade for the game start, shorter for others
            duration = 2.0 if screen_name == 'game' else 1.0
            self.audio_manager.stop_music(fade=True, duration=duration)


    def _cleanup_corrupted_saves(self):
        """Finds and renames any save files that are not valid JSON."""
        self.logger.info("Checking for corrupted save files...")
        save_dir = os.path.join(self.user_data_dir, 'saves')
        if not os.path.exists(save_dir):
            return

        for filename in os.listdir(save_dir):
            if not filename.endswith('.json'):
                continue
            
            filepath = os.path.join(save_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    if not f.read().strip(): continue
                    f.seek(0)
                    json.load(f)
            except json.JSONDecodeError:
                backup_path = filepath + ".corrupted"
                try:
                    os.rename(filepath, backup_path)
                    self.logger.warning(f"Found and renamed corrupted save file: {filename} -> {filename}.corrupted")
                except OSError as e:
                    self.logger.error(f"Could not rename corrupted save file {filename}: {e}")

    def _configure_app_logging(self):
        self.logger = logging.getLogger("FinalDestinationApp")
        try:
            from kivy.utils import platform

            if platform == 'android':
                # PRIMARY: App's own internal files dir
                from android.storage import app_storage_path  # type: ignore
                base_dir = os.path.join(app_storage_path(), 'logs')
                
                # SECONDARY: The permission-free Android external data directory
                self._android_public_log_dir = None
                try:
                    ext_base = "/storage/emulated/0/Android/data/org.dienamicengine.dep_fdt/files"
                    public_dir = os.path.join(ext_base, "FDTerminal_Logs")
                    
                    # THE FIX: Use mkdir instead of makedirs. 
                    # Android already created the 'files' folder for us. This skips the root permission check!
                    if not os.path.exists(public_dir):
                        os.mkdir(public_dir) 
                        
                    self._android_public_log_dir = public_dir
                except Exception as e:
                    print(f"Failed to create external log directory: {e}")
            else:
                # Desktop: project_root/logs/
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                base_dir = os.path.join(project_root, "logs")

            # It's safe to use makedirs here because this is the internal, unprotected app storage
            if not os.path.exists(base_dir):
                os.makedirs(base_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_log_file = os.path.join(base_dir, f"session_{timestamp}.txt")
            consolidated_log_file = os.path.join(base_dir, "fd_terminal_consolidated.txt")
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

            session_handler = logging.FileHandler(session_log_file, mode='w', encoding='utf-8')
            session_handler.setFormatter(formatter)
            consolidated_handler = logging.FileHandler(consolidated_log_file, mode='a', encoding='utf-8')
            consolidated_handler.setFormatter(formatter)
            
            # --- THE ADB FIX: Put the console stream back! ---
            import sys
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)

            root_logger = logging.getLogger()
            root_logger.handlers.clear() 
            
            root_logger.addHandler(stream_handler) # <-- ADB Logcat restored!
            root_logger.addHandler(session_handler)
            root_logger.addHandler(consolidated_handler)
            root_logger.setLevel(logging.INFO)
            
            # Android: Also write to the accessible external folder
            if hasattr(self, '_android_public_log_dir') and self._android_public_log_dir:
                try:
                    public_session = os.path.join(self._android_public_log_dir, f"session_{timestamp}.txt")
                    public_handler = logging.FileHandler(public_session, mode='w', encoding='utf-8')
                    public_handler.setFormatter(formatter)
                    root_logger.addHandler(public_handler)
                    self.logger.info(f"Public log mirror initialized: {public_session}")
                except Exception as e:
                    self.logger.warning(f"Failed to create public log mirror: {e}")

            self.logger.info(f"Smile! You're on candid logging!\nSnitch log ID: {session_log_file}")

        except Exception as e:
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
            self.logger = logging.getLogger("FinalDestinationApp")
            self.logger.error(f"Ope, we done fucked up: {e}. Using basic console config.", exc_info=True)

    def update_safe_area(self, window, system_padding):
        """Dynamically updates the padding if the screen rotates or changes."""
        self.logger.info(f"System padding detected, you betta werk, bitch: {system_padding}")
        
        # system_padding returns (left, top, right, bottom)
        self.safe_left = system_padding[0]
        self.safe_top = system_padding[1]
        self.safe_right = system_padding[2]
        self.safe_bottom = system_padding[3]

if __name__ == '__main__':
    FinalDestinationApp().run()