
import logging
from operator import inv
from typing import Tuple
from typing import Set, Tuple
from typing import Union, Set, Tuple
from typing import List, Set, Tuple
from typing import Optional
from kivy.uix.widget import Widget
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.graphics import Color, Rectangle
import copy
import random
import re
import os
import math
import string
import json
import textwrap
from .resource_manager import ResourceManager
from .utils import get_related_items
from .hazard_engine import HazardEngine
from .achievements import AchievementsSystem
from .death_ai import DeathAI
from .qte_engine import QTE_Engine
from .utils import color_text, normalize_text
from .mixins.combat_mixins import CombatMixin
from .mixins.movement_mixins import MovementMixin 
from .mixins.inventory_mixins import InventoryMixin
from .mixins.interaction_mixins import InteractionMixin
from .mixins.system_mixins import SystemMixin
from .mixins.door_mixin import DoorMixin

HIDDEN_ROOM_LIST_BY_HAZARD = {
    "deaths_breath": {"cold breeze", "sudden draft", "chilling air"}
}

def format_object_message(template: str, hazard_instance: dict = None, object_name: str = None) -> str:
    """
    Replace {object_name} and other placeholders in a message template.
    """
    if not template:
        return ""
    name = object_name
    if not name and hazard_instance:
        # Try to get from hazard instance or master data
        name = hazard_instance.get("display_name") or hazard_instance.get("object_name")
        if not name:
            # Fallback to hazard type or id
            name = hazard_instance.get("type") or hazard_instance.get("id") or "object"
    return template.replace("{object_name}", str(name))

class GameLogic(CombatMixin, MovementMixin, InventoryMixin, InteractionMixin, SystemMixin, DoorMixin):
    """
    The Loom of Fate. This is the Model.
    It holds the entire state of the game world and enforces its rules.
    """
    def __init__(self, resource_manager, save_system=None):
        self.resource_manager = resource_manager
        self.logger = logging.getLogger("GameLogic")
        
        # Core systems will be injected after creation to prevent circular dependencies
        self.hazard_engine: HazardEngine = None
        self.achievements_system: AchievementsSystem = None
        self.death_ai: DeathAI = None
        self.qte_engine: 'QTE_Engine' = None 
        self.interaction_flags = set()
        self.audio_manager = None
        self.player = {}
        self.current_level_rooms_world_state = {}
        self.current_level_items_world_state = {}
        self.is_game_over = False
        self.game_won = False
        self.ui_events = []
        self.last_dialogue_context = {}  # tracks active NPC and options
        # Start the Heartbeat of the World
        Clock.schedule_interval(self.update, 0.1)
        self.set_player_flag("has_initialized", True)
        # The Command Map: A clean way to route player commands to the correct methods.
        self.command_map = {
            'move': self._command_move,
            'go': self._command_move,
            'run': self._command_move,
            'walk': self._command_move,
            'north': lambda _: self._command_move('north'),
            'south': lambda _: self._command_move('south'),
            'east': lambda _: self._command_move('east'),
            'west': lambda _: self._command_move('west'),
            'up': lambda _: self._command_move('up'),
            'down': lambda _: self._command_move('down'),
            'n': lambda _: self._command_move('north'),
            's': lambda _: self._command_move('south'),
            'e': lambda _: self._command_move('east'),
            'w': lambda _: self._command_move('west'),
            'u': lambda _: self._command_move('up'),
            'd': lambda _: self._command_move('down'),
            'examine': self._command_examine,
            'look': self._command_examine,
            'inspect': self._command_examine,
            'take': self._command_take,
            'get': self._command_take,
            'grab': self._command_take,
            'talk': self._command_talk,
            'speak': self._command_talk,
            'respond': self._command_respond,
            'use': self._command_use,
            "unlock" : self._command_unlock,
            'search': self._command_search,
            'inventory': self._command_inventory,
            'inv': self._command_inventory,
            'wait': self._command_wait,
            'rest': self._command_wait,
            'compass': self._command_compass,
            'map': self._command_map,
            'help': self._command_help,
            'test_qte': self._command_test_qte,
            'test_level': self._command_test_level,
            'force': self._command_force,
            'break': self._command_break,
            'set_qte_sr': self._command_set_qte_sr,
            'save': self._command_save,
            'load': self._command_load,
            'quicksave': lambda _: self._command_save('quicksave'),
            'quickload': lambda _: self._command_load('quicksave'),
            'main_menu': self._command_main_menu,
            'debug_room': self._command_debug_room,  # Add debug command
            'kick': self._command_kick,
            'punch': self._command_punch,
            'combine': self._command_combine,
            'roster': self._command_roster,
            'status': self._command_roster,  # Alias
            'gimme': self._command_gimme
        }
        
        self.logger.info("GameLogic instance created with a lean, focused design.")

    def _initialize_level_data(self, level_id, sandbox_config=None):
        self.logger.info(f"_initialize_level_data: Initializing {level_id}...")
        
        # 1. Normalizer: Converts raw integers/digits into "level_X" strings
        if isinstance(level_id, int) or (isinstance(level_id, str) and level_id.isdigit()):
            level_id = f"level_{level_id}"

        self.player['current_level'] = level_id
        
        # Handle both "level_police" (old sentinel) and "level_police_station" (correct ID)
        if level_id in ("level_police_station", "level_police"):
            self.player['current_level'] = "level_police_station"
            level_id = "level_police_station"
        
        # 2. Fetch or Generate Level Requirements
        all_reqs = self.resource_manager.get_data('level_requirements', {})
        if level_id not in all_reqs:
            self.logger.warning(f"No level requirements found for '{level_id}'. Generating blank defaults for testing.")
            safe_entry = "crossroads_room" if "finale" in level_id else None
            all_reqs[level_id] = {
                "name": f"Sandbox: {level_id}",
                "entry_room": safe_entry  
            }
        level_reqs = all_reqs[level_id]

        # 3. Level Builder Dispatcher
        if level_reqs.get('use_disaster_template'):
            self._init_premonition_level(level_reqs)
        elif sandbox_config and sandbox_config.get('use_procedural'):
            self._init_procedural_level(sandbox_config)
        else:
            self._init_static_level(level_id)
            
            # --- THE FIX 1: Populate Random Loot! ---
            # Extract the integer from the level string so the item level-restrictions work
            lvl_num = level_id
            if isinstance(level_id, str) and level_id.startswith("level_") and level_id[6:].isdigit():
                lvl_num = int(level_id[6:])
            self._populate_level_with_items(lvl_num)

        # --- THE FIX 2: Level Overrides go AFTER the rooms are built! ---
        if level_id in ["level_hub", "hub"]:
            # Pass the name of the room defined in rooms_level_hub.json
            self._setup_hub_exits(destination="Your Car")
            
        if level_id == "level_police_station":
            self._setup_police_interrogation()

        # 4. Entry Room Resolution & Map Building (Player Location is set HERE)
        self._resolve_entry_room_and_map(level_id, level_reqs, sandbox_config)

        # --- THE FIX 3: Prevent Finale Companion Dialogue Collision ---
        # 5. Distribute the NPCs (Skip for Finale to let the dedicated method handle it)
        if level_id != "level_finale":
            self._place_persistent_npcs(level_id)
        
        # 6. Check for Hub Softlocks
        self._check_hub_softlock()

        # 7. Global Hazards & Omens
        if getattr(self, 'hazard_engine', None):
            self.hazard_engine.initialize_for_level(level_id)

        self.current_level_omens = self._compile_level_omens(level_id)
        if str(level_id) in ("0", "level_0"):
            self._inject_dynamic_premonition_omens()

        self.player.setdefault('elevator_current_floor', None)
        self.player.setdefault('elevator_enabled_upper', False)
        
        # --- THE FIX: Initialize the popup tracker for the level ---
        self.player.setdefault('shown_entry_popups', [])
        
        # 8. Finale Setup
        if level_id == "level_finale":
            self._setup_finale_room()
        
        # 8. Finale Setup
        if level_id == "level_finale":
            self._setup_finale_room()
            
        self.logger.info(f"_initialize_level_data: Rite of Genesis for Level {level_id} is complete.")

        # ── Visionary Class: snapshot world state before player interaction ──
        if self.player.get('character_class') == 'Visionary' and str(level_id) in ('0', 'level_0'):
            self.player['is_visionary'] = True
            self.player['premonition_already_died'] = False
            self._snapshot_premonition_state()
            self.logger.info("Visionary class: premonition snapshot taken. Timer suppressed until intercept.")

    def start_new_game(self, character_class="Journalist", start_level=1, sandbox_config=None):
        self.logger.info(f"start_new_game: Starting new game with character: {character_class} on level {start_level}...")

        # 1. Full State Reset
        self.is_game_over = False
        self.game_won = False
        self.ui_events = []
        self.interaction_flags = set()
        self.current_level_rooms_world_state = {}
        self.current_level_items_world_state = {}

        # 2. Fetch Configs & Player Archetype
        char_classes = self.resource_manager.get_data('character_classes', {})
        game_config = self.resource_manager.get_data('game_config', {})
        char_data = char_classes.get(character_class, {})
        
        if not char_data:
            self.logger.warning(f"start_new_game: Character class '{character_class}' not found. Using defaults.")

        # 3. Initialize Base Player State 
        # (Notice 'location' and 'current_level' are blank! The Level Initializer will securely fill them.)
        self.player = {
            "location": None, 
            "inventory": [],
            "hp": char_data.get('max_hp', 30),
            "max_hp": char_data.get('max_hp', 30),
            "fear": 0.0,
            "score": 0,
            "turns_left": game_config.get('INITIAL_TURNS', 180),
            "actions_taken": 0,
            "visited_rooms": set(),
            "current_level": None, 
            "character_class": character_class,
            "status_effects": {},
            "qte_active": False,
            "qte_context": {},
            "evaded_hazards": [],
            "companion_location": None
        }
        self.logger.debug(f"start_new_game: Player initialized with base stats.")

        # 4. Initialize Level Data (This delegates all room building, placing, and SPAWNING)
        self.logger.debug("start_new_game: Initializing level data...")
        self._initialize_level_data(start_level, sandbox_config)

        # 5. Build the Initial UI Payload
        starting_room_id = self.player.get('location')

        # FORCE the engine to use the Perception Mixin to build the first narrative!
        if starting_room_id:
            self.player['visited_rooms'].add(starting_room_id)
            initial_message = self._get_rich_room_description(starting_room_id)
        else:
            self.logger.error("start_new_game: Engine failed to spawn the player into a room!")
            initial_message = "You are in a featureless void."

        self.logger.info(f"New game started successfully. Player is in '{starting_room_id}'.")

        # 6. Check for Entry Popups
        initial_events = []
        room_data = self.get_room_data(starting_room_id)
        if room_data and room_data.get('first_entry_text'):
            initial_events.append(self._make_first_entry_popup_event(starting_room_id, room_data['first_entry_text']))

        return self._build_response(
            message=initial_message,
            turn_taken=False,
            success=True,
            ui_events=initial_events
        )

    # -------------------------------------------------------------------------
    # --- Initialization Helpers ---
    # -------------------------------------------------------------------------

    def update(self, dt):
        """
        The Real-Time Update Loop.
        Handles time-based hazards and other continuous logic.
        """
        if self.is_game_over or not self.hazard_engine:
            return

        # 1. Update Hazards
        events = self.hazard_engine.update_realtime(dt)
        
        # 2. Process triggered events immediately
        if events:
            for ev in events:
                self.handle_hazard_consequence(ev)
                
            # If events occurred, refresh UI to show popups/messages
            self.add_ui_event({"event_type": "refresh_ui"})

    def _generate_intro_disaster(self) -> dict:
        """
        Selects a random disaster, formats the narrative, and securely saves 
        the intro object into the player's state.
        """
        self.logger.info("_generate_intro_disaster: Generating fully detailed random introductory disaster...")
        
        # 1. Roll and assign the city FIRST
        cities = ["McKinley", "Cloverdale", "Mt. Abraham", "Stonybrook", "Springfield"]
        chosen_city = random.choice(cities)
        self.player['current_city'] = chosen_city        

        disasters = self.resource_manager.get_data('disasters', {})
        visionaries = self.resource_manager.get_data('visionaries', {})
        survivor_fates = self.resource_manager.get_data('survivor_fates', {}).get('fates', [])

        if not disasters:
            self.logger.error("_generate_intro_disaster: Missing disaster data. Cannot generate intro.")
            return {"event_description": "a system error", "full_description_template": "CRITICAL ERROR: Game data is missing."}

        # 2. Select the Disaster Template
        disaster_key = random.choice(list(disasters.keys()))
        disaster_details = disasters[disaster_key]
        self.logger.debug(f"_generate_intro_disaster: Selected disaster '{disaster_key}'")

        # 3. Handle 'Chill' vs 'Fatal' logic
        is_chill_intro = (
            not disaster_details.get("warnings") and
            not disaster_details.get("visionary") and
            disaster_details.get("killed_count", 0) == 0
        )

        # Visionary Assignment
        if is_chill_intro:
            visionary_desc = "your friend"
        else:
            # If the Pre-Gen cast already picked a Visionary name, use it! Otherwise, pick a random description.
            visionary_desc = self.player.get('premonition_visionary')
            if not visionary_desc and visionaries:
                cat = random.choice(list(visionaries.keys()))
                visionary_desc = random.choice(visionaries[cat])
            elif not visionary_desc:
                visionary_desc = "a mysterious figure"

        # Killed Count Formatting
        killed_count_data = disaster_details.get("killed_count", 0)
        killed_count_str = ""
        if isinstance(killed_count_data, int):
            killed_count_str = str(killed_count_data)
        elif isinstance(killed_count_data, dict):
            min_c, max_c = killed_count_data.get("min", 10), killed_count_data.get("max", 50)
            killed_count_str = str(random.randint(min_c, max_c))

        # Warnings Formatting
        warning_list = disaster_details.get("warnings", [])
        if warning_list:
            warning_selected = random.choice(warning_list)
        else:
            greeting_list = disaster_details.get("greeting", [])
            warning_selected = random.choice(greeting_list) if greeting_list else "Ready for a movie?"

        # Survivor Fate Formatting
        if is_chill_intro or not survivor_fates:
            survivor_fate_selected = "" if is_chill_intro else "met a strange fate."
        else:
            num_to_pick = min(len(survivor_fates), random.randint(1, 3))
            selected = random.sample(survivor_fates, num_to_pick)
            if num_to_pick == 1:
                survivor_fate_selected = selected[0]
            elif num_to_pick == 2:
                survivor_fate_selected = f"{selected[0]} and {selected[1]}"
            else:
                survivor_fate_selected = f"{selected[0]}, {selected[1]}, and even {selected[2]}"

        # 4. Format the raw description to ensure {city_name} is injected
        raw_desc = disaster_details.get("description", "A terrible fate befell them all...")
        formatted_desc = raw_desc.replace('{city_name}', chosen_city)
        formatted_name = disaster_details.get("name", disaster_key).replace('{city_name}', chosen_city)

        # 5. Build and SAVE the object into the player state
        intro_disaster_object = {
            "event_description": disaster_key,
            "full_description_template": formatted_desc,
            "visionary": visionary_desc,
            "visionary_explains": disaster_details.get("visionary_explains", []),
            "warning": warning_selected,
            "killed_count": killed_count_str,
            "survivor_fates": survivor_fate_selected,
            "tags": disaster_details.get("tags", []),
            "name": formatted_name,
            "workplace_pool": disaster_details.get("workplace_pool", []) # Pass along workplaces if they exist here
        }

        # --- THE FIX: Save it permanently so the UI and Omens can find it! ---
        self.player['intro_disaster'] = intro_disaster_object

        self.logger.info(f"_generate_intro_disaster: Generated disaster: '{formatted_name}' claiming '{killed_count_str}' lives.")
        return intro_disaster_object

    def _init_premonition_level(self, level_req: dict):
        """Generates the dynamic Level 0 (Premonition) environment and cast."""
        import random
        
        # 1. Select Archetypes & Generate Names FIRST
        level_0_data = self.resource_manager.get_data('rooms_level_0', {}) or self.resource_manager.get_data('rooms', {}).get('0', {})
        archetypes_data = level_0_data.get('npc_archetypes_premonition', {})
        selected_archetypes = self._select_premonition_archetypes(archetypes_data, count_range=(4, 10))

        role_map = {}
        used_names = set()
        for role, data in selected_archetypes.items():
            if not isinstance(data, dict): continue
            name_pool = [n for n in data.get('name_pool', []) if n not in used_names] or ["Sam", "Alex", "Jordan", "Casey"]
            chosen_name = random.choice(name_pool)
            used_names.add(chosen_name)
            role_map[role] = chosen_name

        self.player['_premonition_role_map'] = role_map
        self.player['premonition_visionary'] = role_map.get('visionary', 'A stranger')

        # 2. NOW GENERATE THE DISASTER (It can finally use the Visionary's name!)
        if hasattr(self, '_generate_intro_disaster'):
            self._generate_intro_disaster()
            
        disaster = self.player.get('intro_disaster', {})

        # 3. Assign Dynamic Workplaces
        workplace_pool = list(disaster.get('workplace_pool') or level_0_data.get('workplace_pool', []))
        random.shuffle(workplace_pool)
        
        npc_workplaces = {}

        for role, npc_name in role_map.items():
            # Skip the player!
            if not npc_name or npc_name.lower() == 'player':
                continue

            # Handle the Authority Figure explicitly
            if role == "authority_figure":
                npc_workplaces[npc_name.lower()] = {
                    "job_title": "Officer", 
                    "workplace_name": "The Police Station",
                    "level_id": "level_police_station"
                }
                self.logger.info(f"Assigned Authority Figure '{npc_name}' to Police Station.")
                continue

            # Assign everyone else a random job from the JSON pool
            if workplace_pool:
                assigned_job = workplace_pool.pop()
                npc_workplaces[npc_name.lower()] = assigned_job
                self.logger.info(f"Assigned '{npc_name}' to {assigned_job['workplace_name']}.")
            else:
                self.logger.warning(f"Ran out of workplaces in the pool for '{npc_name}'!")

        self.player['npc_workplaces'] = npc_workplaces
        self.logger.info(f"Assigned {len(npc_workplaces)} NPC workplaces.")

        # 4. Build Rooms & Seed NPCs
        self.current_level_rooms_world_state = self._build_premonition_rooms(disaster)
        
        # --- THE FIX: Tag the rooms using their final, formatted names ---
        for formatted_room_name, room_data in self.current_level_rooms_world_state.items():
            if room_data.pop('entry_room', False):
                # We tag the room data directly so _resolve_entry_room_and_map can find it
                room_data['entry_room'] = True 
            if room_data.pop('is_disaster_view', False):
                room_data['is_exit'] = True 

        self._place_premonition_npcs(self.current_level_rooms_world_state, selected_archetypes)

    def _generate_authority_title(self) -> str:
        import random
        return random.choice(["Detective", "Sergeant", "Officer", "Deputy", "Inspector", "Captain"])

    def _select_premonition_archetypes(self, archetypes: dict, count_range=(4, 10)) -> dict:
        """
        Select a random subset of archetypes for this playthrough.
        The visionary is ALWAYS included. Other roles are randomly sampled.
        
        Args:
            archetypes: Full archetype dict from rooms_level_0.json
            count_range: (min, max) number of NPCs to place
            
        Returns:
            Filtered archetype dict with only the selected roles.
        """
        import random
        
        # Separate required from optional
        required_roles = ['visionary']
        optional_roles = [
            k for k in archetypes.keys()
            if not k.startswith('_') and k not in required_roles
        ]
        
        # How many total NPCs this run?
        min_count, max_count = count_range
        target_count = random.randint(min_count, max_count)
        
        # Start with required roles
        selected = list(required_roles)
        
        # Fill remaining slots from optional roles
        remaining = target_count - len(selected)
        if remaining > 0:
            # Prioritize skeptic and friend — they have the most story weight
            priority_roles = ['skeptic', 'friend', 'authority_figure']
            for role in priority_roles:
                if role in optional_roles and remaining > 0:
                    selected.append(role)
                    optional_roles.remove(role)
                    remaining -= 1
            
            # Fill rest randomly from bystanders and extras
            if remaining > 0 and optional_roles:
                extras = random.sample(
                    optional_roles, min(remaining, len(optional_roles)))
                selected.extend(extras)
        
        result = {k: v for k, v in archetypes.items()
                if k in selected or k.startswith('_')}
        
        return result

    def _build_premonition_rooms(self, disaster_data: dict) -> dict:
        """Dynamically constructs Level 0 rooms based on the active disaster template."""
        templates = self.resource_manager.get_data('rooms_level_0', {})
        if not templates:
            # Fallback just in case you haven't renamed it yet
            templates = self.resource_manager.get_data('rooms', {}).get('0', {})

        tags = disaster_data.get('tags', []) or []
        
        # --- THE FIX: Read the routing map dynamically from the JSON! ---
        shell_map = templates.get('shell_selection', {})
        
        shell_key = 'venue'  # absolute fallback
        for tag in tags:
            if tag in shell_map:
                shell_key = shell_map[tag]
                self.logger.info(f"_build_premonition_rooms: Tag '{tag}' routed to shell '{shell_key}'")
                break
        # ----------------------------------------------------------------

        if shell_key not in templates.get('shells', {}):
            self.logger.warning(f"_build_premonition_rooms: Shell '{shell_key}' not found, falling back to 'venue'")
            shell_key = 'venue'

        shell = templates['shells'][shell_key]
        
        shell_key = 'venue'  # default
        for tag in tags:
            if tag in shell_map:
                shell_key = shell_map[tag]
                break
        
        if shell_key not in templates.get('shells', {}):
            self.logger.warning(f"_build_premonition_rooms: Shell '{shell_key}' not found, falling back to 'venue'")
            shell_key = 'venue'

        shell = templates['shells'][shell_key]

        name_map = {}
        built_rooms = {}

        for room_key, room_template in shell['rooms'].items():
            room = copy.deepcopy(room_template['base'])

            # --- THE FIX: Compound Tag Scoring ---
            overrides = room_template.get('overrides', {})
            best_tag = None
            best_score = 0

            # 1. Search for compound keys first (e.g., "public_venue_accident+structural_collapse")
            for override_key in overrides.keys():
                if '+' in override_key:
                    required_tags = [t.strip() for t in override_key.split('+')]
                    
                    # If the disaster possesses EVERY tag required by this compound key...
                    if all(req_tag in tags for req_tag in required_tags):
                        score = len(required_tags) # 2 tags = Score of 2, 3 tags = Score of 3
                        
                        # Highest score wins!
                        if score > best_score:
                            best_score = score
                            best_tag = override_key

            # 2. Fallback: If no compound keys matched, check single tags normally (Left-to-Right)
            if not best_tag:
                for tag in tags:
                    if tag in overrides:
                        best_tag = tag
                        break

            if best_tag:
                self.logger.info(f"Applying override '{best_tag}' for room '{room['name']}'")
                for ok, ov in overrides[best_tag].items():
                    room[ok] = copy.deepcopy(ov)

            # --- THE FIX: Dynamically inject the Visionary's traits! ---
            # Grab the visionary's name, and generate flavor traits if missing
            visionary_name = disaster_data.get('visionary', 'someone')
            v_age = disaster_data.get('age', random.choice(['young', 'middle-aged', 'older', 'panicked']))
            v_app = disaster_data.get('appearance', random.choice(['guy', 'woman', 'teenager', 'person']))

            # Safely replace the tags in the room's narrative text
            for text_key in ['first_entry_text', 'description']:
                if text_key in room and isinstance(room[text_key], str):
                    narrative = room[text_key]
                    narrative = narrative.replace('{visionary}', visionary_name)
                    narrative = narrative.replace('{age}', v_age)
                    narrative = narrative.replace('{appearance}', v_app)
                    room[text_key] = narrative

            base_name = room_template['base'].get('name', room_key)
            final_name = room.get('name', base_name)
            
            # --- FIX 2: Format dynamic text for ALL narrative fields ---
            final_name = self._format_dynamic_text(final_name)
            room['name'] = final_name
            
            if 'description' in room:
                room['description'] = self._format_dynamic_text(room['description'])
            else:
                # Absolute fallback so you NEVER see 'featureless void' again
                room['description'] = "The destruction here is absolute. Smoke and dust choke the air."
                
            if 'first_entry_text' in room:
                room['first_entry_text'] = self._format_dynamic_text(room['first_entry_text'])

            # --- FIX 3: Map BOTH the base name AND the raw JSON room_key ---
            # This guarantees that whether your JSON exit says "north": "midway" OR "north": "Midway (Center)", 
            # the engine will successfully translate it to the final formatted name.
            name_map[base_name] = final_name
            name_map[room_key] = final_name
            
            built_rooms[room_key] = room

        # --- Second pass: resolve exit references ---
        for room_key, room in built_rooms.items():
            exits = room.get('exits', {})
            resolved_exits = {}
            for direction, target_name in exits.items():
                if isinstance(target_name, str) and target_name in name_map:
                    resolved_exits[direction] = name_map[target_name]
                else:
                    resolved_exits[direction] = target_name
            room['exits'] = resolved_exits

        # --- Build final dict keyed by room name ---
        rooms = {}
        for room_key, room in built_rooms.items():
            rooms[room['name']] = room

        return rooms
    
    def _place_premonition_npcs(self, built_rooms: dict, selected_archetypes: dict) -> None:
        """Scatters the pre-generated cast across Level 0 based on room capacity."""
        if not selected_archetypes:
            return

        role_map = self.player.get('_premonition_role_map', {})
        if not role_map:
            self.logger.warning("_place_premonition_npcs: No role map found! Cannot place NPCs.")
            return

        available_slots = []
        for room_id, room_data in built_rooms.items():
            if room_id == 'exit_room' or room_data.get('is_exit', False):
                continue
            raw_slots = room_data.get('npc_slots', 4)
            if isinstance(raw_slots, int):
                slots = raw_slots
            elif isinstance(raw_slots, str) and raw_slots.isdigit():
                slots = int(raw_slots)
            else:
                slots = 4
            for _ in range(slots):
                available_slots.append(room_id)

        import random
        random.shuffle(available_slots)

        for role, npc_name in role_map.items():
            if not available_slots:
                self.logger.warning(f"_place_premonition_npcs: Ran out of room slots! Could not place {npc_name}.")
                break

            target_room = available_slots.pop()
            arch_data = selected_archetypes.get(role, {})
            raw_dialogue_tree = arch_data.get("dialogue_by_shell", {}).get("_default", {})

            # ── Resolve all {placeholder} tokens in dialogue text NOW ──────────
            # so the player never sees raw template strings like {visionary_explains}
            def _resolve_premonition_text(text: str) -> str:
                if not isinstance(text, str):
                    return text
                return self._format_dynamic_text(text)

            import copy
            resolved_dialogue_tree = {}
            for state_key, state_data in raw_dialogue_tree.items():
                if state_key.startswith('_'):
                    continue
                resolved_state = copy.deepcopy(state_data)
                if isinstance(resolved_state.get('text'), str):
                    resolved_state['text'] = _resolve_premonition_text(resolved_state['text'])
                for opt in resolved_state.get('options', []):
                    if isinstance(opt.get('text'), str):
                        opt['text'] = _resolve_premonition_text(opt['text'])
                resolved_dialogue_tree[state_key] = resolved_state
            # ───────────────────────────────────────────────────────────────────

            npc_obj = {
                "name": npc_name,
                "role": role,
                "description": self._format_dynamic_text(arch_data.get("description_template", "A distressed individual.")),
                "initial_state": arch_data.get("initial_state", "greeting"),
                "dialogue_states": resolved_dialogue_tree
            }

            existing_npcs = [n.get('name') for n in built_rooms[target_room].get('npcs', []) if isinstance(n, dict)]
            if npc_name not in existing_npcs:
                built_rooms[target_room].setdefault('npcs', []).append(npc_obj)
                self.logger.info(f"Spawned Level 0 NPC: {npc_name} ({role}) into {target_room}")

    # =========================================================
    # --- LEVEL 0: REAL-TIME PREMONITION TIMER ---
    # =========================================================

    def _start_premonition_timer(self):
        """Starts a real-time countdown for Level 0."""
        # --- THE FIX: The Gatekeeper ---
        if str(self.player.get('current_level', '')) not in ["0", "level_0"]:
            self.logger.warning("Attempted to start premonition timer outside of Level 0. Aborting.")
            return
        # -------------------------------
        
        self._cancel_premonition_timer() # Safety clear
        
        game_config = self.resource_manager.get_data('game_config', {})
        timer_range = game_config.get('premonition_timer_range', [40, 120]) 
        
        min_seconds = timer_range[0]
        max_seconds = timer_range[1]
        
        self.premonition_time_left = random.randint(min_seconds, max_seconds)
        self.logger.info(f"Disaster countdown started: {self.premonition_time_left} seconds.")
        
        from kivy.clock import Clock
        self.premonition_timer_event = Clock.schedule_interval(self._tick_premonition_timer, 1.0)

    def _tick_premonition_timer(self, dt):
        """Ticks every second. Runs even while popups are open!"""
        
        # --- THE FIX: The Active Kill Switch ---
        if str(self.player.get('current_level', '')) not in ["0", "level_0"]:
            self.logger.info("Timer kill switch: Player is no longer in Level 0. Canceling timer.")
            self._cancel_premonition_timer()
            return False
        # ---------------------------------------

        # Stop the timer if they win the level or die
        if getattr(self, 'is_game_over', False) or getattr(self, 'is_transitioning', False) or self.player.get('level_complete_flag'):
            self._cancel_premonition_timer()
            return False
            
        self.premonition_time_left -= 1
        
        # Escalate tension through the UI while they play
        if self.premonition_time_left == 30:
            visionary_name = self.player.get('premonition_visionary', 'Your visionary')
            self.add_ui_event({"event_type": "show_message", "message": f"[color=ff4444]{visionary_name} is getting more agitated. You are running out of time![/color]"})
            
        elif self.premonition_time_left <= 15:
            # Flashes the screen every 3 seconds to induce panic!
            if self.premonition_time_left % 3 == 0:
                self.add_ui_event({"event_type": "screen_flash", "color": "ff0000", "duration": 0.5, "opacity": 0.3})
            
            if self.premonition_time_left == 15:
                self.add_ui_event({"event_type": "show_message", "message": "[color=ff0000]IT'S HAPPENING! GET TO SAFETY![/color]"})
        
        # Time's up!
        if self.premonition_time_left <= 0:
            self._trigger_premonition_death()
            return False

    def _trigger_premonition_death(self):
        if self._intercept_visionary_death():
            return
        disaster_name = self.player.get('intro_disaster', {}).get('name', 'the disaster')
        death_msg = f"You ran out of time.\n\nThe screams start before you even realize what's happening. You all meet your end after {disaster_name}.\n\nThanks for making things easy this time!"
        
        self.logger.info("Premonition timer hit 0! Triggering Game Over.")
        
        # 1. Force close any open NPC dialogue popups immediately
        self.add_ui_event({"event_type": "destroy_info_popup"})
        
        # 2. Trigger Game Over
        self.is_game_over = True
        self.player['death_reason'] = death_msg
        self.add_ui_event({"event_type": "game_over", "death_reason": death_msg})
        
        self._cancel_premonition_timer()

    def _cancel_premonition_timer(self):
        """Safely stops the clock."""
        if hasattr(self, 'premonition_timer_event') and self.premonition_timer_event:
            self.premonition_timer_event.cancel()
            self.premonition_timer_event = None

    def _snapshot_premonition_state(self):
        """
        Snapshots the level_0 world state immediately after init, before any
        player interaction. Used by the Visionary class to restore the premonition
        to its original state after the player 'dies' and wakes up.
        """
        import copy
        self._premonition_snapshot = {
            'rooms': copy.deepcopy(self.current_level_rooms_world_state),
            'items': copy.deepcopy(self.current_level_items_world_state),
            'omens': copy.deepcopy(getattr(self, 'current_level_omens', {})),
            'interaction_flags': set(),   # always clean on reset
            'player_subset': {
                # Only reset the in-level state — keep class, disaster, NPC data
                'hp': self.player.get('max_hp', 30),
                'fear': 0.0,
                'inventory': [],
                'visited_rooms': set(),
                'location': self.player.get('location'),
                'actions_taken': 0,
                'qte_active': False,
                'qte_context': {},
                'status_effects': {},
                'evaded_hazards': [],
                '_premonition_npc_states': {},
                'npc_states': {},
            }
        }
        self.logger.info("_snapshot_premonition_state: World state snapshot taken.")

    def reset_ui_state(self):
        """Full UI state reset for a clean new game. Call before start_new_game."""
        self._popup_is_active = False
        self._pending_popup_continuation = None
        self._pending_open_popup_event = None
        
        for attr in ('active_info_popup', 'active_qte_popup', 'active_map_popup'):
            popup = getattr(self, attr, None)
            if popup:
                try:
                    popup._suppress_on_dismiss = True
                    popup.dismiss()
                except Exception:
                    pass
            setattr(self, attr, None)
        
        if self.game_logic:
            self.game_logic.ui_events.clear()
            self.game_logic.is_game_over = False
            self.game_logic.game_won = False

    def reset_premonition_state(self):
        """
        Restores level_0 to its snapshotted state after the Visionary's
        premonition death intercept. The player retains knowledge of the layout
        but all world mutations (searched containers, triggered hazards, etc.) reset.
        Timer starts NOW.
        """
        import copy

        snap = getattr(self, '_premonition_snapshot', None)
        if not snap:
            self.logger.error("reset_premonition_state: No snapshot found! Cannot reset.")
            return

        # 1. Restore world state
        self.current_level_rooms_world_state = copy.deepcopy(snap['rooms'])
        self.current_level_items_world_state = copy.deepcopy(snap['items'])
        self.current_level_omens = copy.deepcopy(snap['omens'])
        self.interaction_flags = set()

        # 2. Restore player in-level state (preserve meta-knowledge and class data)
        for key, val in snap['player_subset'].items():
            if isinstance(val, (set, dict, list)):
                self.player[key] = copy.deepcopy(val)
            else:
                self.player[key] = val

        # 3. Reset engine state
        self.is_game_over = False
        self.game_won = False
        self.ui_events = []

        # 4. Re-initialize hazard engine for the level (new hazard IDs = fresh state)
        if getattr(self, 'hazard_engine', None):
            self.hazard_engine.initialize_for_level('level_0')

        # 5. Rebuild 3D map (it uses world state, so must come after room restore)
        self._build_3d_coordinate_map()

        self.logger.info("reset_premonition_state: World restored from snapshot. Timer starting.")

        # 6. Start the timer NOW — this is the real premonition run
        self._start_premonition_timer()

        # 7. UI: flash white + gasp, then show the entry room description
        self.add_ui_event({
            "event_type": "screen_flash",
            "color": "ffffff",
            "opacity": 0.95,
            "duration": 0.4
        })
        self.add_ui_event({
            "event_type": "play_sfx",
            "sfx_key": "gasp"
        })

        entry_room = self.player.get('location')
        entry_data = self.get_room_data(entry_room) or {}
        description = entry_data.get('description', '')

        self.add_ui_event({
            "event_type": "show_popup",
            "title": "I SAW IT ALL",
            "message": (
                "You bolt upright, gasping.\n\n"
                "The disaster. The screaming. The exact moment it all fell apart. "
                "You LIVED it. You saw everyone die.\n\n"
                "But you're still here. At the beginning.\n\n"
                "[color=ff4444]You know what is going to happen. "
                "You know this place. "
                "And now you have one chance to change it.[/color]\n\n"
                f"{description}"
            ),
            "priority": 1000
        })

    def _intercept_visionary_death(self) -> bool:
        """
        Intercepts a death event for the Visionary class on level_0.
        Returns True if the death was intercepted (caller should abort normal death flow).
        Returns False if interception does not apply.
        """
        if not self.player.get('is_visionary'):
            return False
        if str(self.player.get('current_level', '')) not in ('0', 'level_0'):
            return False
        if self.player.get('premonition_already_died'):
            # Second death (during the real timed run) — let it through normally
            return False

        # Mark that the intercept has fired — won't fire again this game
        self.player['premonition_already_died'] = True
        self.player['premonition_death_knowledge'] = True  # flag for Phase 2 persuasion

        self.logger.info(
            "_intercept_visionary_death: Intercepting level_0 death for Visionary. "
            "Resetting premonition state."
        )

        # Cancel any active timer (shouldn't exist yet, but safety)
        self._cancel_premonition_timer()

        # Clear game_over flag — we're not dead, we're waking up
        self.is_game_over = False

        # Wipe the standard death UI events that were queued before intercept
        # Keep any events that aren't game_over or player_death
        self.ui_events = [
            e for e in self.ui_events
            if e.get('event_type') not in ('game_over', 'player_death', 'switch_screen')
        ]

        # Reset the world and start the real run
        self.reset_premonition_state()
        return True

    def _check_premonition_complete(self) -> dict:
        """
        Master Dispatcher for Level 0 completion.
        Validates the exit, calculates survivors, builds the narrative, and transitions.
        """
        current_loc = self.player.get('location')
        room_data = self.get_room_data(current_loc)
        
        # 1. Validation Check
        if not room_data or not room_data.get('is_exit') or self.player.get('level_complete_flag'):
            return None
            
        self.logger.info(f"Premonition exit '{current_loc}' reached. Initiating completion sequence.")
        
        # --- THE FIX: Kill the Timer Immediately! ---
        self._cancel_premonition_timer()

        # 2. Phase 1: Determine who survived the initial blast
        survivors, casualties, interacted, npc_status = self._resolve_premonition_fates()
        self._process_witnessed_deaths(interacted)
        
        # 3. Phase 2: Death's Design and The Cull
        self._generate_deaths_list(survivors)
        survivors, offscreen_casualties = self._process_offscreen_cull(survivors, npc_status)
        if not self.player.get('_offscreen_cull_done'):
            survivors, offscreen_casualties = self._process_offscreen_cull(survivors, npc_status)
            self.player['offscreen_casualties'] = offscreen_casualties
            self.player['_offscreen_cull_done'] = True
        else:
            offscreen_casualties = self.player.get('offscreen_casualties', [])
        # Save finalized rosters to player state
        self.player['premonition_survivors'] = survivors
        self.player['premonition_casualties'] = casualties
        self.player['npc_status'] = npc_status
        self.player['offscreen_casualties'] = offscreen_casualties

        # --- Achievement Logic ---
        met_npcs = [n.lower() for n in self.player.get('met_npcs', [])]
        known_survivors = [s for s in survivors if s.lower() in met_npcs]
        known_casualties = [c for c in casualties if c.lower() in met_npcs]
        unknown_casualties_count = len(casualties) - len(known_casualties)
        known_offscreen = [oc for oc in offscreen_casualties if oc.get('name', '').lower() in met_npcs]

        if len(known_survivors) == 0 and len(known_offscreen) == 0:
            if getattr(self, 'achievements_system', None):
                self.achievements_system.unlock("cassandra_truth")
        elif len(known_casualties) == 0 and len(known_offscreen) == 0 and unknown_casualties_count == 0:
            if getattr(self, 'achievements_system', None):
                self.achievements_system.unlock("no_one_left_behind")

        if getattr(self, 'achievements_system', None):
            self.achievements_system.unlock("cheating_death")
        
        # 4. Phase 3: Narrative & Transition
        full_narrative = self._build_premonition_narrative(room_data, survivors, casualties, offscreen_casualties)
        self._finalize_premonition_state(survivors)
        
        # --- THE FIX: Hold the Door for the Escape Text ---
        escape_text = room_data.get('first_entry_text')
        
        if escape_text:
            self.logger.info("Intercepting transition to display escape text breather.")
            room_data['first_entry_text'] = ""  # Blank it to prevent the normal movement logic from double-firing it
            
            # Fire the completion event normally so the engine generates the payload
            self._trigger_premonition_completion_event(full_narrative)
            
            # Snatch the level_complete event back out of the queue!
            completion_event = None
            for i in range(len(getattr(self, 'ui_events', [])) - 1, -1, -1):
                if self.ui_events[i].get('event_type') == 'level_complete':
                    completion_event = self.ui_events.pop(i)
                    break
                    
            if completion_event:
                # Turn off the engine's flag so check_game_state_transitions doesn't double-fire
                self.player['level_complete_flag'] = False 
                
                # Wrap the transition event in an inescapable Breather Popup!
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "NARROW ESCAPE",
                    "message": escape_text,
                    "priority": 1000,
                    "on_close_emit_ui_events": [completion_event]
                })
        else:
            # If there's no escape text to show, just transition normally
            self._trigger_premonition_completion_event(full_narrative)
    
        return {
            "success": True,
            "message": "The premonition is complete.",
            "game_state": self.get_current_game_state()
        }

    # -------------------------------------------------------------------------
    # --- Premonition Completion Helpers ---
    # -------------------------------------------------------------------------

    def _resolve_premonition_fates(self):
        """Calculates which NPCs survived the disaster based on player persuasion."""
        import random
        global_states = self.player.get('_premonition_npc_states', {})
        role_map = self.player.get('_premonition_role_map', {})
        
        npc_status, survivors, casualties, interacted_casualties = {}, [], [], []
        
        for role, npc_name in role_map.items():
            if not npc_name: continue
            npc_key = npc_name.lower()
            p_state = global_states.get(npc_key)
            c_state, i_state = None, None
            
            # Find their current active state in the room dictionaries
            for room_id, r_data in self.current_level_rooms_world_state.items():
                for npc in r_data.get('npcs', []):
                    if isinstance(npc, dict) and npc.get('name', '').lower() == npc_key:
                        p_state = p_state or npc.get('persuasion_state')
                        c_state = self._get_npc_state(npc)
                        i_state = npc.get('initial_state')
                        break
            
            state = str(p_state or c_state or i_state or 'uncontacted').lower()

            # Survival Logic Tree
            if state == 'convinced' or any(k in state for k in ['convince', 'persuade', 'success', 'follow', 'save', 'evacuate', 'evacuated', 'upgrade', 'safe']):
                npc_status[npc_key] = 'alive'
                survivors.append(npc_name)
            elif state == 'hostile' or any(k in state for k in ['hostile', 'angry', 'refuse', 'fail']):
                npc_status[npc_key] = 'dead'
                casualties.append(npc_name)
                interacted_casualties.append(npc_name)
            elif state == 'unsure' or any(k in state for k in ['unsure', 'hesitant', 'panic', 'doubt']):
                if random.random() < 0.5:
                    npc_status[npc_key], survivors = 'alive', survivors + [npc_name]
                else:
                    npc_status[npc_key] = 'dead'
                    casualties.append(npc_name)
                    interacted_casualties.append(npc_name)
            else:
                if random.random() < 0.20:
                    npc_status[npc_key], survivors = 'alive', survivors + [npc_name]
                else:
                    npc_status[npc_key] = 'dead'
                    casualties.append(npc_name)
                    interacted_casualties.append(npc_name)
                    
        return survivors, casualties, interacted_casualties, npc_status


    def _process_witnessed_deaths(self, interacted_casualties: list):
        """Generates dynamic death text for NPCs the player failed to save."""
        import random
        witnessed_deaths = []
        if not interacted_casualties:
            self.player['witnessed_deaths'] = witnessed_deaths
            return
            
        disaster_tags = self.player.get('intro_disaster', {}).get('tags', [])
        to_describe = random.sample(interacted_casualties, min(len(interacted_casualties), 3))
        death_narratives = self.resource_manager.get_data('death_narratives', {})
        
        possible_fates = []
        for tag in disaster_tags:
            if tag in death_narratives.get('templates', {}):
                possible_fates.extend(death_narratives['templates'][tag])
                
        if not possible_fates:
            possible_fates = list(death_narratives.get('generic_deaths', ["{name} becoming just another casualty of the event"]))
            
        for victim in to_describe:
            if not possible_fates:
                possible_fates = list(death_narratives.get('generic_deaths', ["{name} becoming just another casualty of the event"]))

            chosen_fate = random.choice(possible_fates)
            possible_fates.remove(chosen_fate)
            
            formatted_fate = chosen_fate.replace('{name}', color_text(victim, 'npc', self.resource_manager))
            witnessed_deaths.append(self._format_dynamic_text(formatted_fate))
            
        self.player['witnessed_deaths'] = witnessed_deaths


    def _generate_deaths_list(self, survivors: list):
        """Builds the exact kill order for the rest of the game."""
        import random
        deaths_list = list(survivors)
        deaths_list.append('player')
        
        visionary_id = 'visionary_name' 
        visionary_survived = False
        if visionary_id in deaths_list:
            deaths_list.remove(visionary_id)
            visionary_survived = True
            
        random.shuffle(deaths_list)
        if visionary_survived:
            deaths_list.append(visionary_id)
            
        self.player['deaths_list'] = deaths_list
        self.player['death_design_skipped'] = []


    def _process_offscreen_cull(self, survivors: list, npc_status: dict):
        """Forces the survivor count down to manageable levels by killing them off-screen."""
        import random
        MAX_SURVIVORS = 4
        offscreen_casualties = []

        if len(survivors) > MAX_SURVIVORS:
            cull_count = len(survivors) - MAX_SURVIVORS
            deaths_list_target = self.player.get('deaths_list', [])
            victims = []
            
            for name in deaths_list_target:
                if name != 'player' and name in survivors:
                    victims.append(name)
                if len(victims) >= cull_count:
                    break
            
            self.player['deaths_list_index'] = self.player.get('deaths_list_index', 0) + len(victims)

            survivor_fates_data = self.resource_manager.get_data('survivor_fates', {})
            possible_fates = list(survivor_fates_data.get('fates', [])) or ["suffering a fatal, unexplained accident"]
            
            for victim in victims:
                survivors.remove(victim)
                npc_status[victim.lower()] = 'dead'
                
                chosen_fate = random.choice(possible_fates)
                possible_fates.remove(chosen_fate) 
                offscreen_casualties.append({"name": victim, "fate": chosen_fate})
                
        return survivors, offscreen_casualties


    def _build_premonition_narrative(self, room_data: dict, survivors: list, casualties: list, offscreen_casualties: list) -> str:
        """Assembles the massive cinematic block of text, filtering by NPCs the player actually met!"""
        disaster_narrative = room_data.get('first_entry_text', 'The disaster unfolds before your eyes.')
        disaster_narrative = self._format_dynamic_text(disaster_narrative)
        
        # Who does the player actually know?
        met_npcs = [n.lower() for n in self.player.get('met_npcs', [])]
        
        # Filter the rosters
        known_survivors = [s for s in survivors if s.lower() in met_npcs]
        unknown_survivors_count = len(survivors) - len(known_survivors)
        
        known_casualties = [c for c in casualties if c.lower() in met_npcs]
        unknown_casualties_count = len(casualties) - len(known_casualties)
        
        known_offscreen = [oc for oc in offscreen_casualties if oc['name'].lower() in met_npcs]
        unknown_offscreen_count = len(offscreen_casualties) - len(known_offscreen)
    
        # --- 1. THE SURVIVORS ---
        initial_known_escapees = list(known_survivors) + [oc['name'] for oc in known_offscreen]
        total_unknown_escapees = unknown_survivors_count + unknown_offscreen_count
        
        if initial_known_escapees or total_unknown_escapees > 0:
            survivor_text = "\n\nYou got out. And you weren't alone — "
            group = []
            if initial_known_escapees:
                group.append(", ".join(initial_known_escapees))
            if total_unknown_escapees > 0:
                group.append(f"{total_unknown_escapees} other {'person' if total_unknown_escapees == 1 else 'people'} you didn't get the chance to meet")
            
            survivor_text += " and ".join(group) + " made it out too."
        else:
            survivor_text = "\n\nYou got out. You were the only one."
    
        # --- 2. THE CASUALTIES ---
        if known_casualties or unknown_casualties_count > 0:
            casualty_text = "\n\n"
            group = []
            if known_casualties:
                group.append(", ".join(known_casualties))
            if unknown_casualties_count > 0:
                group.append("several strangers you saw in the crowd")
                
            verb = "wasn't" if (len(known_casualties) + unknown_casualties_count) == 1 else "weren't"
            casualty_text += " and ".join(group) + f" {verb} so lucky."
        else:
            casualty_text = ""
    
        # --- 3. THE OFFSCREEN CULL ---
        offscreen_text = ""
        if known_offscreen or unknown_offscreen_count > 0:
            offscreen_text += "\n\n[b]But the nightmare wasn't over.[/b]"
            
            for oc in known_offscreen:
                offscreen_text += f"\n\n[color=ff4444]{oc['name']} survived the disaster, only to die moments later — {oc['fate']}.[/color]"
                
            if unknown_offscreen_count > 0:
                offscreen_text += f"\n\n[color=ff4444]In the chaos outside, you hear the sickening sounds of {unknown_offscreen_count} other {'survivor' if unknown_offscreen_count == 1 else 'survivors'} meeting a sudden, gruesome end.[/color]"
                
            final_survivor_str = ", ".join(known_survivors) if known_survivors else "you"
            if unknown_survivors_count > 0:
                final_survivor_str += f" and {unknown_survivors_count} strangers"
                
            offscreen_text += f"\n\n[color=ffaa00]Something is wrong. This wasn't an accident. Out of everyone who made it out, only {final_survivor_str} remain.[/color]"
    
        return disaster_narrative + survivor_text + casualty_text + offscreen_text


    def _finalize_premonition_state(self, survivors: list):
        """Cleans up the engine state and ensures survivor roles transfer to Level 1."""
        original_role_map = self.player.get('_premonition_role_map', {})
        name_to_role = {name.lower(): role for role, name in original_role_map.items() if name}
        
        npc_roles = {s.lower(): name_to_role.get(s.lower(), "bystander_1") for s in survivors}
            
        self.player['level_complete_flag'] = True
        self.player['override_requirements'] = True
        self.player['current_interacted_npc'] = None
        self.player['qte_active'] = False
        self._drain_qte_queue()
        self.player['npc_roles'] = npc_roles
        self.player['npc_states'] = {}


    def _trigger_premonition_completion_event(self, full_narrative: str):
        """Hijacks the UI and executes the level transition."""
        current_level_str = str(self.player.get('current_level', 'level_0'))
        next_lvl, nxt_rm = self._evaluate_dynamic_transition(current_level_str)
    
        lvl_complete_event = {
            "event_type": "level_complete",
            "priority": 500,
            "level_name": "The Premonition",
            "narrative": full_narrative,
            "score": self.player.get('score', 0),
            "turns_taken": self.player.get('actions_taken', 0),
            "evidence_count": 0,
            "evaded_hazards": [],
            "omens_witnessed": 0,
            "qte_successes": 0,
            "qte_attempts": 0,
            "player_state": self.player.copy(),
            "next_level_id": next_lvl,
            "next_start_room": nxt_rm,
        }

        popup_found = False
        for event in getattr(self, 'ui_events', []):
            if event.get('event_type') == 'show_popup':
                event.setdefault('on_close_emit_ui_events', []).append(lvl_complete_event)
                popup_found = True
                self.logger.info("Chained level completion to the room's entry UI popup.")
                break
                
        if not popup_found:
            self.add_ui_event(lvl_complete_event)

    def _place_persistent_npcs(self, level_id) -> None:
        """
        The Master Dispatcher for seeding surviving cast NPCs.
        """
        # --- THE SAFETY GATE ---
        if not self.current_level_rooms_world_state:
            self.logger.error(f"Cannot place NPCs: World state for {level_id} is empty!")
            return
        # -----------------------

        roster = self.player.get('npc_status', {})
        persistent_roles = self.player.get('npc_roles', {})
        
        if not roster or not persistent_roles:
            return
            
        alive_npcs = self._get_alive_npcs_list(roster)
        if not alive_npcs:
            self.player['current_companion'] = None
            return
    
        companions = self.player.setdefault('companions', [])
        max_to_place = self._determine_placement_count(level_id, len(alive_npcs))
        to_place = self._select_npcs_to_place(alive_npcs, companions, persistent_roles, max_to_place)
    
        entry_room, available_rooms = self._get_room_slots(level_id)
        
        # --- THE FIX: Bulletproof Fallbacks ---
        # If the level JSON forgot to tag an "entry_room: true", force it to the player's location
        if not entry_room or entry_room not in self.current_level_rooms_world_state:
            entry_room = self.player.get('location')
            if not entry_room or entry_room not in self.current_level_rooms_world_state:
                entry_room = list(self.current_level_rooms_world_state.keys())[0]

        # If the level JSON forgot to add "npc_slots", just use all rooms!
        if not available_rooms:
            available_rooms = list(self.current_level_rooms_world_state.keys()) * 3
            import random
            random.shuffle(available_rooms)
        # --------------------------------------

        level_dialogues = self._get_persistent_npc_dialogue(level_id)
        placed_names = set()

        # --- NEW: COMPANION JEOPARDY CHECK ---
        deaths_list = self.player.get('deaths_list', [])
        deaths_list_index = self.player.get('deaths_list_index', 0)
        next_target = deaths_list[deaths_list_index].lower() if deaths_list_index < len(deaths_list) else None

        self.player['companion_is_hunt_target'] = False

        if next_target:
            for comp in companions:
                if roster.get(comp.lower(), 'alive') in ('alive', 'injured'):
                    if comp.lower() == next_target or comp.lower() in next_target:
                        self.player['companion_is_hunt_target'] = True
                        self.player['current_hunt_target'] = comp
                        self.logger.info(f"Death's Design: Companion '{comp}' is the active hunt target.")
                        self.add_ui_event({
                            "event_type": "show_message",
                            "message": "\n[color=ff4444]Something feels wrong. The air changes. You look over at your companions.[/color]\n"
                        })
                        break
        # -------------------------------------
    
        # 1. Place Companions in Entry Room
        for comp_name in companions:
            npc_dict = self._build_npc_entity(comp_name, persistent_roles, roster, level_dialogues, is_companion=True)
            existing = [n.get('name', n) if isinstance(n, dict) else n for n in self.current_level_rooms_world_state[entry_room].get('npcs', [])]
            if comp_name not in existing:
                self.current_level_rooms_world_state[entry_room].setdefault('npcs', []).append(npc_dict)
            placed_names.add(comp_name.lower())
            # --- ADD THIS LOG LINE ---
            self.logger.info(f"  Placed Companion {comp_name} → '{entry_room}'")
            
        # 2. INJECT THE HUNT TARGET (Workplace Check)
        hunt_target = self._get_workplace_target_for_level(level_id)
        if hunt_target and hunt_target.lower() not in placed_names:
            job_data = self.player.get('npc_workplaces', {}).get(hunt_target.lower(), {})
            workplace_name = job_data.get('workplace_name', 'their workplace')
            job_title = job_data.get('job_title', 'worker')
            
            custom_desc = f"{hunt_target.title()} is here, working as a {job_title} at {workplace_name}. They look up as you enter — something in their expression shifts."
            custom_fallback = f"'{hunt_target.title()} notices you immediately. Something flickers behind their eyes — relief, fear, recognition. They know. On some level, they know.'"
            
            target_dict = self._build_npc_entity(
                hunt_target, persistent_roles, roster, level_dialogues, 
                is_companion=False, custom_desc=custom_desc, 
                custom_fallback=custom_fallback, is_hunt_target=True
            )
            
            existing = [n.get('name', n) if isinstance(n, dict) else n for n in self.current_level_rooms_world_state[entry_room].get('npcs', [])]
            if hunt_target not in existing:
                self.current_level_rooms_world_state[entry_room].setdefault('npcs', []).append(target_dict)
            placed_names.add(hunt_target.lower())
            self.logger.info(f"  Hunt Target {hunt_target} ({target_dict['role']}) → '{entry_room}'")

        # 3. Scatter Remaining NPCs
        for npc_name in to_place:
            if npc_name.lower() in placed_names: continue
            if not available_rooms: break
    
            target_room = available_rooms.pop()
            npc_dict = self._build_npc_entity(npc_name, persistent_roles, roster, level_dialogues, is_companion=False)
            self.current_level_rooms_world_state[target_room].setdefault('npcs', []).append(npc_dict)
            placed_names.add(npc_name.lower())
            # --- ADD THIS LOG LINE ---
            self.logger.info(f"  Scattered NPC {npc_name} ({npc_dict.get('role', 'unknown')}) → '{target_room}'")
            
        self.logger.info(f"Total NPCs placed in {level_id}: {len(placed_names)}")

    def _init_procedural_level(self, sandbox_config: dict):
        """Generates a random level environment."""
        self.logger.info("Using procedural level generation")
        from .procedural_generator import generate_procedural_level
        
        hazards_master = self.resource_manager.get_data('hazards', {})
        valid_hazards = [h for h in sandbox_config.get('hazards', []) if h in hazards_master]
        
        self.current_level_rooms_world_state = generate_procedural_level(
            resource_manager=self.resource_manager,
            room_count=sandbox_config.get('room_count', 10),
            hazards=valid_hazards,
            items=sandbox_config.get('items', []),
            seed=sandbox_config.get('seed')
        )
        self._apply_sandbox_config(sandbox_config)

    def _init_static_level(self, level_id):
        self.logger.info(f"_init_static_level: Initializing {level_id}...")
        
        import os
        import json
        import copy # Crucial for the fix

        # --- 1. DATA DISCOVERY ---
        base_dir = os.path.dirname(os.path.abspath(__file__))
        possible_dirs = [
            os.path.join(base_dir, 'data'),
            os.path.join(base_dir, '..', 'data'),
            os.path.join(base_dir, 'assets', 'data'),
        ]
        
        str_level = str(level_id)
        level_num = str_level.replace("level_", "")
        
        possible_room_keys = [f"rooms_level_{level_num}", f"rooms_{level_num}", f"rooms_{str_level}", str_level]
        possible_item_keys = [f"items_level_{level_num}", f"items_{level_num}", f"items_{str_level}"]
        
        rooms_data = None
        items_data = None
        
        # Hunt for the Rooms JSON
        for key in possible_room_keys:
            data = self.resource_manager.get_data(key)
            if data:
                rooms_data = data
                self.logger.info(f"Successfully found rooms data via ResourceManager: '{key}'")
                break
                
        # Direct Disk Fallback
        if not rooms_data:
            for d in possible_dirs:
                for key in possible_room_keys:
                    filepath = os.path.join(d, f"{key}.json")
                    if os.path.exists(filepath):
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                rooms_data = json.load(f)
                        except Exception: pass
                        break
                if rooms_data: break

        # --- THE SAFETY GATE: If load failed, abort before crash ---
        if not rooms_data:
            self.logger.error(f"CRASH PREVENTION: No room data found for {level_id}. Aborting initialization.")
            return # This prevents the 'list index out of range' crash below

        # --- THE DEEP COPY FIX: Clone the data immediately ---
        # We clone ROOMS and ITEMS separately before the hydration loops begin.
        self.current_level_rooms_world_state = copy.deepcopy(rooms_data)
        
        # Clone Items master data for this session
        for key in possible_item_keys:
            data = self.resource_manager.get_data(key)
            if data:
                items_data = data
                break
        
        self.current_level_items_world_state = copy.deepcopy(items_data) if items_data else {}
        self.logger.info(f"Level {level_id} world state initialized with fresh, independent clones.")

        # --- 2. HYDRATION LOOPS ---
        npcs_master = self.resource_manager.get_data('npcs', {})
        master_items = self.resource_manager.get_data('items', {})
        
        # Helper to recursively search npcs.json for the target string
        def _find_npc_definition(target_id, data_block):
            if isinstance(data_block, dict):
                if target_id in data_block:
                    return data_block[target_id]
                for k, v in data_block.items():
                    res = _find_npc_definition(target_id, v)
                    if res: return res
            elif isinstance(data_block, list):
                for item in data_block:
                    if isinstance(item, dict) and item.get('id') == target_id:
                        return item
                    res = _find_npc_definition(target_id, item)
                    if res: return res
            return None

        import copy
        for r_id, r_data in self.current_level_rooms_world_state.items():
            hydrated_npcs = []
            for npc_ref in r_data.get('npcs', []):
                if isinstance(npc_ref, str):
                    npc_data = _find_npc_definition(npc_ref, npcs_master)
                    if npc_data:
                        hydrated_npc = copy.deepcopy(npc_data)
                        hydrated_npc['id'] = npc_ref
                        hydrated_npcs.append(hydrated_npc)
                        self.logger.debug(f"_init_static_level: Hydrated NPC '{npc_ref}' in '{r_id}'")
                    else:
                        # Fallback: Just keep them as a string so they don't disappear
                        self.logger.warning(f"_init_static_level: Could not find NPC '{npc_ref}' in npcs.json! Keeping as string.")
                        hydrated_npcs.append(npc_ref)
                else:
                    hydrated_npcs.append(npc_ref)
            r_data['npcs'] = hydrated_npcs

        # --- ITEM & CONTAINER HYDRATION LOOP ---
        master_items = self.resource_manager.get_data('items', {})
        
        for r_id, r_data in self.current_level_rooms_world_state.items():
            hydrated_items = []
            for item_ref in r_data.get('items_present', []):
                # 1. Top-level Hydration
                if isinstance(item_ref, str):
                    item_data = master_items.get(item_ref)
                    if item_data:
                        full_item = copy.deepcopy(item_data)
                        full_item['id'] = item_ref
                        
                        # 2. SUB-HYDRATION: Check for container contents
                        if "inventory" in full_item and isinstance(full_item["inventory"], list):
                            hydrated_sub_items = []
                            for sub_ref in full_item["inventory"]:
                                if isinstance(sub_ref, str):
                                    sub_data = master_items.get(sub_ref)
                                    if sub_data:
                                        f_sub = copy.deepcopy(sub_data)
                                        f_sub['id'] = sub_ref
                                        hydrated_sub_items.append(f_sub)
                                    else:
                                        hydrated_sub_items.append(sub_ref)
                                else:
                                    hydrated_sub_items.append(sub_ref)
                            full_item["inventory"] = hydrated_sub_items
                            
                        hydrated_items.append(full_item)
                        self.logger.debug(f"Hydrated item '{item_ref}' in room '{r_id}'")
                    else:
                        self.logger.warning(f"Could not find master data for '{item_ref}'")
                        hydrated_items.append(item_ref)
                else:
                    hydrated_items.append(item_ref)
            
            r_data['items_present'] = hydrated_items

        # --- HAZARD SYNC ---
        # Ensure hazards in this level are linked to these hydrated objects
        if hasattr(self, 'hazard_engine'):
            self.hazard_engine.sync_world_state(self.current_level_rooms_world_state)

    # ---------------------------------------------------------
    # --- Placement Helpers ---
    # ---------------------------------------------------------

    def _get_alive_npcs_list(self, roster: dict) -> list:
        """Retrieves and properly capitalizes the names of living NPCs."""
        survivors = self.player.get('premonition_survivors', [])
        deaths_list = self.player.get('deaths_list', [])
        
        original_names = {s.lower(): s for s in survivors}
        for name in deaths_list:
            original_names[name.lower()] = name
            
        return [
            original_names.get(name_lower, name_lower.title()) 
            for name_lower, status in roster.items()
            if status in ('alive', 'injured') and name_lower != 'player'
        ]

    def _determine_placement_count(self, level_id, num_alive: int) -> int:
        """Determines how many NPCs the game should spawn based on the current level."""
        _lid_str = str(level_id)
        if _lid_str.isdigit():
            level_int = int(_lid_str)
        elif _lid_str.startswith("level_") and _lid_str[6:].isdigit():
            level_int = int(_lid_str[6:])
        else:
            level_int = 99  # Non-numeric IDs (house, theater…) get full roster
    
        if level_int <= 1:
            return min(3, num_alive)
        elif level_int == 2:
            return min(4, num_alive)
        return num_alive

    def _select_npcs_to_place(self, alive_npcs: list, companions: list, persistent_roles: dict, max_to_place: int) -> list:
        """Prioritizes companions and story-critical roles, then randomly fills remaining slots."""
        import random
        to_place = []
        if companions:
            to_place.extend(companions)
    
        priority_order = ['visionary', 'friend', 'skeptic', 'authority_figure']
        for target_role in priority_order:
            name = next((n for n, r in persistent_roles.items() if r == target_role), None)
            if name:
                matched_name = next((an for an in alive_npcs if an.lower() == name.lower()), None)
                if matched_name and matched_name not in to_place and len(to_place) < max_to_place:
                    to_place.append(matched_name)
    
        remaining_alive = [n for n in alive_npcs if n not in to_place]
        random.shuffle(remaining_alive)
        while remaining_alive and len(to_place) < max_to_place:
            to_place.append(remaining_alive.pop(0))
            
        return to_place

    def _get_room_slots(self, level_id) -> tuple[str, list]:
        """Calculates valid rooms and slot capacities."""
        import random
        rooms = self.current_level_rooms_world_state
        level_reqs = self.resource_manager.get_data('level_requirements', {}).get(str(level_id), {})
        exit_room = level_reqs.get('exit_room', '')
        
        entry_room = next((r_name for r_name, r_data in rooms.items() if r_data.get('entry_room') is True), None)
        if not entry_room:
            entry_room = level_reqs.get('entry_room', '')
    
        available_rooms = []
        for room_name, room_data in rooms.items():
            if room_name in (exit_room, entry_room):
                continue
    
            raw_slots = room_data.get('npc_slots', 0)
            capacity = 0
            if isinstance(raw_slots, int) and raw_slots > 0:
                capacity = raw_slots
            elif isinstance(raw_slots, dict):
                capacity = sum(1 for v in raw_slots.values() if v is True and not str(v).startswith('_'))
            elif isinstance(raw_slots, str) and raw_slots.isdigit():
                capacity = int(raw_slots)
    
            available_rooms.extend([room_name] * capacity)
    
        random.shuffle(available_rooms)
        return entry_room, available_rooms

    def _get_workplace_target_for_level(self, level_id: str) -> str:
        """Checks if the current level is the workplace of the next target on Death's list."""
        npc_workplaces = self.player.get('npc_workplaces', {})
        npc_status_map = self.player.get('npc_status', {})
        deaths_list = self.player.get('deaths_list', [])
        deaths_idx = self.player.get('deaths_list_index', 0)

        for candidate in deaths_list[deaths_idx:]:
            if candidate.lower() == 'player': continue
            job_data = npc_workplaces.get(candidate.lower(), {})
            
            if isinstance(job_data, dict) and str(job_data.get('level_id')) == str(level_id):
                if npc_status_map.get(candidate.lower(), 'alive') in ('alive', 'injured'):
                    return candidate
        return None
    
    def _get_persistent_npc_dialogue(self, level_id: int) -> dict:
        """Load role-keyed dialogue from npcs.json for the given level."""
        npcs_data = self.resource_manager.get_data('npcs', {})
        all_dialogue = npcs_data.get('persistent_cast_dialogue', {})
        level_dialogue = all_dialogue.get(str(level_id))
        if not level_dialogue:
            level_dialogue = all_dialogue.get('_default', {})
        return {k: v for k, v in level_dialogue.items() if not k.startswith('_')}

    def _build_npc_entity(self, npc_name: str, persistent_roles: dict, roster: dict, level_dialogues: dict, is_companion: bool, custom_desc: str = None, custom_fallback: str = None, is_hunt_target: bool = False) -> dict:
        """Constructs the JSON entity dictionary for the NPC."""
        import copy
        import random

        role = persistent_roles.get(npc_name.lower(), 'friend' if is_companion else 'bystander_1')
        
        # --- THE FIX: Differentiate Workplace vs Public Encounters! ---
        current_level = str(self.player.get('current_level', '1'))
        workplace_id = str(self.player.get('npc_workplaces', {}).get(npc_name.lower(), {}).get('level_id', 'none'))
        at_workplace = (current_level == workplace_id)

        if is_hunt_target:
            dialogue_role_key = 'hunt_target_workplace' if at_workplace else 'hunt_target_public'
        else:
            dialogue_role_key = role
        # --------------------------------------------------------------
        
        raw_dialogue = level_dialogues.get(dialogue_role_key)
        
        # Old 'hunt_target' fallback just in case the JSON isn't updated yet
        if not raw_dialogue and is_hunt_target:
            raw_dialogue = level_dialogues.get('hunt_target')
            
        if not raw_dialogue:
            if custom_fallback:
                default_txt = custom_fallback
            else:
                default_txt = "'I'm right here with you. Whatever happens.'" if is_companion else "'...I'm just glad to be alive.'"
            raw_dialogue = {'greeting': {'text': default_txt}}
    
        visionary_name = self.player.get('premonition_visionary', 'them')
        player_class = self.player.get('character_class', 'Survivor')
        intro_disaster = self.player.get('intro_disaster', {})
        disaster_name = intro_disaster.get('name', intro_disaster.get('event_description', 'the disaster'))
        chosen_explanation = random.choice(intro_disaster.get('visionary_explains', ["I saw it happen."]))
    
        def _resolve(text):
            if not isinstance(text, str): return text
            text = text.replace('{visionary_name}', visionary_name).replace('{visionary}', visionary_name).replace('{player_class}', player_class).replace('{disaster_name}', disaster_name).replace('{visionary_explains}', chosen_explanation)
            return self._format_dynamic_text(text) # <--- Formats Universal Job Titles!
    
        resolved_dialogue = {}
        for k, v in raw_dialogue.items():
            if k.startswith('_'): continue
            r = copy.deepcopy(v)
            if 'text' in r: r['text'] = _resolve(r['text'])
            # --- THE FIX: Safely extract options whether 'r' is a dict or a list of randomized dicts ---
            options_list = []
            if isinstance(r, dict):
                options_list = r.get('options', [])
            elif isinstance(r, list):
                for sub_node in r:
                    if isinstance(sub_node, dict):
                        options_list.extend(sub_node.get('options', []))
                        
            for opt in options_list:
                if 'text' in opt: opt['text'] = _resolve(opt['text'])
            resolved_dialogue[k] = r
            
        status = roster.get(npc_name.lower(), 'alive')
        desc = custom_desc or (f"{npc_name} is here. They look exhausted." if status == 'injured' else f"{npc_name} is here.")
        
        # --- THE FIX: Dynamic Desperation ---
        initial_state = 'greeting'
        if is_hunt_target:
            deaths_list = self.player.get('deaths_list', [])
            dead_count = sum(1 for n in deaths_list if n.lower() != 'player' and roster.get(n.lower()) == 'dead')
            
            if dead_count == 0:
                initial_state = 'greeting_oblivious'
            elif dead_count <= 2:
                initial_state = 'greeting_uneasy'
            else:
                initial_state = 'greeting_terrified'
        # ------------------------------------
        
        npc_dict = {
            'name': npc_name, 'role': role, 'description': desc, 'examinable': True,
            'initial_state': initial_state, 'dialogue_states': resolved_dialogue,
        }
        if is_hunt_target: npc_dict['is_hunt_target'] = True
        if role == 'visionary' or is_hunt_target: npc_dict['deaths_list_knowledge'] = self.player.get('deaths_list', [])
        return npc_dict
    # -----------------------------------

    def _sync_companions_to_player(self):
        """Ensures all companions are physically located only in the player's current room."""
        current_room = self.player.get('location')
        if not current_room: return
        
        companions = self.player.get('companions', [])
        if not companions: return
        
        # 1. Scrub companions from ALL rooms and capture their full live dicts
        extracted_dicts = {}
        for r_id, r_data in self.current_level_rooms_world_state.items():
            if 'npcs' in r_data:
                new_npcs = []
                for npc in r_data['npcs']:
                    name = npc.get('name') if isinstance(npc, dict) else str(npc)
                    if name.lower() in [c.lower() for c in companions]:
                        if isinstance(npc, dict):
                            extracted_dicts[name.lower()] = npc
                    else:
                        new_npcs.append(npc)
                r_data['npcs'] = new_npcs
                
        # 2. Inject them exclusively into the current room
        dest_room = self.current_level_rooms_world_state.get(current_room)
        if dest_room:
            for c_name in companions:
                npc_dict = extracted_dicts.get(c_name.lower())
                if not npc_dict:
                    # Failsafe: Generate them fresh if they got lost!
                    level_id = self.player.get('current_level', '1')
                    roles = self.player.get('npc_roles', {})
                    roster = self.player.get('npc_status', {})
                    dialogues = self._get_persistent_npc_dialogue(level_id)
                    npc_dict = self._build_npc_entity(c_name, roles, roster, dialogues, is_companion=True)
                dest_room.setdefault('npcs', []).append(npc_dict)

    def _resolve_entry_room_and_map(self, level_id: str, level_req: dict, sandbox_config: dict):
        """Finds the starting room, sets player location, and builds the spatial map."""
        entry_room = None
        INVALID_SENTINELS = {None, '', 'null', '_templated', '_dynamic', '_procedural'}
        
        # 1. Static Level Check
        if not (sandbox_config and sandbox_config.get('use_procedural')):
            entry_room = level_req.get('entry_room')
            if entry_room in INVALID_SENTINELS or entry_room not in self.current_level_rooms_world_state:
                entry_room = None
            
        # 2. Dynamic/Procedural Check (Scan the generated rooms)
        # If entry_room wasn't found in the JSON, or it was renamed dynamically (Level 0)
        if not entry_room:
            for room_name, room_data in self.current_level_rooms_world_state.items():
                if room_data.get('entry_room'):
                    entry_room = room_name
                    break
                    
        # 3. Last Resort Fallback
        if not entry_room and self.current_level_rooms_world_state:
            entry_room = list(self.current_level_rooms_world_state.keys())[0]

        # 4. Explicitly teleport the player here!
        if entry_room:
            self.player['location'] = entry_room
            self._build_room_coordinate_map(entry_room)
            self.logger.info(f"Player location securely set to: {entry_room}")
        else:
            self.logger.warning("Could not resolve an entry room for this level!")
            
    def _inject_narrative_clues(self, level_id: str, level_rooms: dict):
        """
        Ensures the player always finds the clue for the next target on the deaths_list.
        Runs during level initialization before procedural loot scattering.
        """
        # Only inject clues if we are in the investigation hub (e.g., Level 2 / Bludworth's)
        if level_id != "level_house":
            return

        # 1. Identify who we are looking for
        target_id = self.player.get('active_death_target')
        if not target_id:
            return

        # 2. Get their associated clue
        npc_data = self.resource_manager.get_data('npcs', {}).get(target_id, {})
        clue_item_id = npc_data.get('investigation_clue_item')
        
        if not clue_item_id:
            return

        # 3. Find a designated narrative container in the level
        target_container = None
        for room_id, room_data in level_rooms.items():
            for furniture in room_data.get('furniture', []):
                if isinstance(furniture, dict) and furniture.get('is_narrative_container'):
                    target_container = furniture
                    break
            if target_container:
                break

        # 4. Inject the clue!
        if target_container:
            # Ensure the items list exists
            if 'items' not in target_container:
                target_container['items'] = []
                
            # Add the specific clue so the player can find it and unlock the next level
            if clue_item_id not in target_container['items']:
                target_container['items'].append(clue_item_id)
                self.logger.info(f"Injected narrative clue '{clue_item_id}' into {target_container['name']}")

    def _inject_dynamic_premonition_omens(self):
        """
        Enriches self.current_level_omens for Level 0 with:
        1. Disaster-specific omens already compiled by _compile_level_omens (base)
        2. Generic fear omens keyed to this run's disaster name
        3. Active hazard omens from rooms in this level
        Then distributes omen provider objects into rooms.
        """
        import random

        disaster = self.player.get('intro_disaster', {})
        d_name = self._format_dynamic_text(disaster.get('name', 'a terrible accident'))

        # ── 1. Seed from whatever _compile_level_omens already built ──────────
        # Those are the actual disasters.json environmental_omens entries.
        # We start with them so they are never thrown away.
        custom_omens_pool = {}
        for provider, omen_val in self.current_level_omens.items():
            if isinstance(omen_val, list):
                custom_omens_pool[provider] = list(omen_val)
            elif omen_val is not None:
                custom_omens_pool[provider] = [omen_val]

        # ── 2. Add/extend generic narrative omens (keyed by provider name) ────
        # These supplement the disasters.json omens rather than replacing them.
        generic_additions = {
            "television": [
                f"A news anchor is cut off by static. When the picture returns, the headline reads: [b]'{d_name.upper()} - NO SURVIVORS'[/b]. It flickers back to a commercial."
            ],
            "radio": [
                "You hear a voice through the static. It sounds frantic: '...evacuate the area immediately... total failure...' Then, just upbeat pop music."
            ],
            "reflection": [
                "For a split second, your reflection looks back at you with a face covered in ash and blood. You blink, and it's gone."
            ],
            "newspaper": [
                "A discarded newspaper catches your eye. The date is tomorrow. The front page shows a photo of this exact location, utterly destroyed-\n-until you blink.\nThe image is gone and the date is today."
            ],
        }
        for provider, texts in generic_additions.items():
            custom_omens_pool.setdefault(provider, []).extend(texts)

        # ── 3. Pull omens from hazards present in this level's rooms ──────────
        if hasattr(self, 'resource_manager'):
            hazards_db = self.resource_manager.get_data('hazards', {})

            active_hazard_keys = set()
            for room_data in self.current_level_rooms_world_state.values():
                for hazard_key in room_data.get('hazards_present', []):
                    active_hazard_keys.add(hazard_key)

            if disaster.get('primary_hazard'):
                active_hazard_keys.add(disaster['primary_hazard'])

            for hazard_key in active_hazard_keys:
                hazard_data = hazards_db.get(hazard_key, {})
                env_omens = hazard_data.get('environmental_omens', {})
                for provider, omen_list in env_omens.items():
                    if isinstance(omen_list, list):
                        custom_omens_pool.setdefault(provider, []).extend(omen_list)
                    elif isinstance(omen_list, str):
                        custom_omens_pool.setdefault(provider, []).append(omen_list)

        # ── 4. Collapse each provider's pool to a single chosen omen ──────────
        # (prevents crashes from _examine_omen expecting a single value)
        final_omens = {}
        for provider, options in custom_omens_pool.items():
            if options:
                final_omens[provider] = random.choice(options)

        self.current_level_omens = final_omens

        # ── 5. Distribute omen provider objects into rooms ─────────────────────
        if not final_omens:
            return

        providers = list(final_omens.keys())
        for room_id, room_data in self.current_level_rooms_world_state.items():
            if room_data.get('is_exit'):
                continue
            if random.random() < 0.7:
                provider = random.choice(providers)
                omen_obj = {"name": provider, "is_omen_provider": True}
                room_data.setdefault('objects', []).append(omen_obj)
                room_data.setdefault('examine_details', {})[provider] = (
                    "It looks ordinary, but something draws your eye to it."
                )

    def _apply_sandbox_config(self, config: dict):
        """
        Filters world state based on sandbox configuration.
        Removes hazards not in config['hazards'] and manages items based on flags.
        """
        self.logger.info(f"_apply_sandbox_config: Applying config: {config}")
        
        allowed_hazards = set(config.get('hazards', []))
        include_related = config.get('include_related', True)
        include_all_items = config.get('include_all_items', False)
        
        # 1. Determine Allowed Items
        allowed_items = set()
        if not include_all_items and include_related:
            for h_key in allowed_hazards:
                related = get_related_items(h_key)
                allowed_items.update(related)
            # Add some absolute defaults if needed (e.g. bandages)? 
            # For now, stick to strict filtering to prove it works.
        
        # 2. Iterate and Filter Rooms
        for room_id, room_data in self.current_level_rooms_world_state.items():
            
            # --- HAZARDS ---
            current_hazards = room_data.get('hazards_present', [])
            filtered_hazards = []
            for h in current_hazards:
                h_type = h if isinstance(h, str) else h.get('type')
                if h_type in allowed_hazards:
                    filtered_hazards.append(h)
            room_data['hazards_present'] = filtered_hazards
            
            # --- ITEMS ---
            if not include_all_items:
                current_items = room_data.get('items_present', [])
                filtered_items = []
                for item in current_items:
                    # Item can be string or dict? usually string in items_present list after population?
                    # _populate_level_with_items usually converts them to strings or dicts?
                    # Let's assume strings for simplest case, or dicts with 'type'/'item_key'
                    # Actually _populate usually puts strings in the room's item list, 
                    # OR they represent keys into items_world_state? 
                    # Let's check _populate data structure.
                    # Usually items_present is a list of strings [item_id_1, item_id_2].
                    # And current_level_items_world_state maps item_id -> item_data.
                    # So we need to look up the item's *type* (prototype key) from world state.
                    
                    item_id = item
                    item_data = self.current_level_items_world_state.get(item_id)
                    if not item_data: 
                        continue
                        
                    item_key = item_data.get('type') # The prototype key (e.g. 'candle')
                    
                    if include_related:
                         if item_key in allowed_items:
                            filtered_items.append(item)
                    else:
                        # If neither all nor related, we assume EMPTY (except maybe quest criticals? Nah, sandbox.)
                        pass 
                        
                room_data['items_present'] = filtered_items
            
            # --- FURNITURE / CONTAINERS ---
            if not include_all_items:
                for furniture in room_data.get('furniture', []):
                    if not isinstance(furniture, dict) or 'items' not in furniture:
                        continue
                        
                    current_contents = furniture['items']
                    filtered_contents = []
                    
                    for item_id in current_contents:
                        # Lookup item type
                        # Note: _populate_level_with_items uses loose strings for item keys in furniture lists
                        # AND registers them in current_level_items_world_state.
                        # So we can look up their type there.
                        
                        item_data = self.current_level_items_world_state.get(item_id)
                        # If not found in world state (rare/bug?), assume it's a raw prototype key?
                        # Fallback: check if item_id itself is a valid prototype key.
                        item_key = item_data.get('type') if item_data else item_id
                        
                        if include_related:
                            if item_key in allowed_items:
                                filtered_contents.append(item_id)
                        else:
                             # Strict mode: delete everything
                             pass
                    
                    furniture['items'] = filtered_contents

        self.logger.info("Sandbox Config Application Complete.")

    def _update_act_state(self):
        """Monitors game progress and updates the current narrative Act."""
        current_act = self.player.get('current_act', 'act_1_survival')

        # ACT 1 -> ACT 2 (The Investigation)
        # Trigger: Player leaves the first level and arrives at the Hub or Police Station.
        if current_act == 'act_1_survival':
            if self.player.get('location') in ['level_hub', 'level_police_station']:
                self.player['current_act'] = 'act_2_investigation'
                self.logger.info("NARRATIVE SHIFT: Entering Act 2 (Investigation).")

        # ACT 2 -> ACT 3 (The Hunt)
        # Trigger: Player learns the order of the list from Bludworth's or the Hospital.
        elif current_act == 'act_2_investigation':
            if 'learned_deaths_list' in self.player.get('flags', []):
                self.player['current_act'] = 'act_3_hunted'
                self.logger.info("NARRATIVE SHIFT: Entering Act 3 (The Hunt).")

        # ACT 3 -> ACT 4 (The Funnel / The Plan)
        # Trigger: Only 2 targets left alive, OR player finds the Defibrillator early.
        elif current_act == 'act_3_hunted':
            deaths_list = self.player.get('deaths_list', [])
            roster = self.player.get('npc_status', {})
            alive_count = sum(1 for n in deaths_list if roster.get(n.lower(), 'alive') == 'alive')
            
            inventory_ids = [item.get('id', item) if isinstance(item, dict) else item for item in self.player.get('inventory', [])]
            
            if alive_count <= 3 or 'defibrillator_pads' in inventory_ids:
                self.player['current_act'] = 'act_4_the_plan'
                self.logger.info("NARRATIVE SHIFT: Entering Act 4 (The Funnel).")

    def _setup_hub_exits(self, destination: str):
        p = self.player
        npc_workplaces  = p.get('npc_workplaces', {})
        npc_status      = p.get('npc_status', {})
        visited_levels  = p.get('visited_levels', set())
        inventory       = {normalize_text(i) for i in p.get('inventory', [])}
        flags           = p.get('flags', set())

        dynamic_exits = {}

        # ── 1. Bludworth ────────────────────────────────────────────────────────
        has_key = normalize_text('bludworths_house_key') in inventory
        if has_key and not p.get('visited_bludworth'):
            # THE FIX: Ensure dynamic exit keys are perfectly lowercased!
            dynamic_exits["drive to bludworth's"] = {
                "target": "LEVEL_TRANSITION_BLUDWORTH"
            }

        # ── 2. NPC Workplace exits ───────────────────────────────────────────────
        # One exit per alive NPC whose level hasn't been completed yet
        seen_levels = set()  # deduplicate if multiple NPCs share a level
        if 'learned_deaths_list' in flags:
            for npc_key, wp in npc_workplaces.items():
                level_id = wp.get('level_id')
                if not level_id or level_id in seen_levels:
                    continue
                if npc_status.get(npc_key, 'alive') not in ('alive', 'injured'):
                    continue
                if level_id in visited_levels:
                    continue
                seen_levels.add(level_id)
                wp_name = wp.get('workplace_name', npc_key.title())
                # Use a human-readable direction label
                exit_label = f"drive to {wp_name}".lower()
                dynamic_exits[exit_label] = {
                    "target": level_id,          # direct level ID — handled by _route_level_transition
                    "npc_target": npc_key,
                }

        # ── 3. Police ────────────────────────────────────────────────────────────
        dynamic_exits["surrender to police"] = {"target": "LEVEL_TRANSITION_POLICE"}
        dynamic_exits["fight through police"] = {"target": "LEVEL_TRANSITION_POLICE_FOUGHT"}

        # ── 4. Finale / Funnel check ─────────────────────────────────────────────
        deaths_list = p.get('deaths_list', [])
        alive_npcs  = [n for n in deaths_list
                    if str(n).lower() != 'player'
                    and npc_status.get(str(n).lower(), 'alive') in ('alive', 'injured')]
        all_workplaces_visited = all(
            npc_workplaces.get(str(n).lower(), {}).get('level_id') in visited_levels
            for n in alive_npcs
        )

        player_is_last = len(alive_npcs) == 0
        if player_is_last or all_workplaces_visited:
            dynamic_exits["confront death"] = {"target": "LEVEL_TRANSITION_FINALE"}

        # ── 5. Write exits into room ─────────────────────────────────────────────
        room_data = self.current_level_rooms_world_state.get(destination)
        if not room_data:
            room_data = {}
            self.current_level_rooms_world_state[destination] = room_data
        room_data['exits'] = dynamic_exits
        self.logger.info(f"_setup_hub_exits: Injected {len(dynamic_exits)} exits into '{destination}'")

    def _evaluate_dynamic_transition(self, current_level_id: str) -> tuple:
        """
        Merged Evaluator: Handles hard-coded progression, Hub sentinels, 
        and JSON-based conditional branching.
        """
        # 1. THE HARD ANCHORS: Guaranteed Story Beats
        if current_level_id in ('level_0', '0'):
            self.logger.info("_evaluate_dynamic_transition: Disaster escaped. Routing to Level 1.")
            return "level_1", None

        # 2. THE HUB SENTINELS: Parking Garage Transitions
        # This handles 'move drive to...' commands
        pending_transition = self.player.pop('pending_level_transition', None)
        if pending_transition:
            # Direct ID support (e.g., if the exit target is already 'level_hospital')
            if pending_transition.startswith('level_') and not pending_transition.startswith('LEVEL_TRANSITION_'):
                 return pending_transition, None

            # Police Status Flagging
            if pending_transition == 'LEVEL_TRANSITION_POLICE_FOUGHT':
                self.player['police_status'] = 'fought'
            
            # Catch dynamic hunt directly from a hub command
            if pending_transition in ('LEVEL_TRANSITION_DYNAMIC_HUNT', 'DYNAMIC_HUNT_EVAL'):
                return self._resolve_dynamic_hunt_level(), None
            
            # Transition Map Lookup
            transition_map = {
                'LEVEL_TRANSITION_POLICE':        ('level_police_station', None),
                'LEVEL_TRANSITION_POLICE_FOUGHT': ('level_police_fought', None),
                'LEVEL_TRANSITION_BLUDWORTH':      ('level_house', 'Front Porch'),
                'LEVEL_TRANSITION_FINALE':         ('level_finale', 'crossroads_room'),
                'LEVEL_TRANSITION_HOSPITAL':       ('level_1', None),
                'LEVEL_TRANSITION_HUB':            ('level_hub', 'Your Car')
            }

            if pending_transition in transition_map:
                resolved_level, resolved_room = transition_map[pending_transition]
                self.logger.info(f"Hub transition '{pending_transition}' -> {resolved_level}")
                return resolved_level, resolved_room

        # 3. JSON BRANCHING: level_requirements.json Logic
        level_reqs = self.resource_manager.get_data('level_requirements', {}).get(current_level_id, {})
        transitions = level_reqs.get('conditional_transitions', [])
        
        nxt_lvl = None
        nxt_rm = None

        # Evaluate conditions sequentially and break on the first match
        for trans in transitions:
            cond = trans.get('condition')
            eval_lvl = trans.get('next_level_id')
            eval_rm = trans.get('next_level_start_room')

            if cond == 'has_flag':
                if trans.get('flag_name') in self.player.get('interaction_flags', set()):
                    self.logger.info(f"Branch triggered: Flag '{trans.get('flag_name')}' found. Routing to {eval_lvl}.")
                    nxt_lvl, nxt_rm = eval_lvl, eval_rm
                    break

            elif cond == 'has_item':
                inv = [normalize_text(str(i)) for i in self.player.get('inventory', [])]
                if normalize_text(str(trans.get('item_name'))) in inv:
                    self.logger.info(f"Branch triggered: Item '{trans.get('item_name')}' found. Routing to {eval_lvl}.")
                    nxt_lvl, nxt_rm = eval_lvl, eval_rm
                    break
                    
            elif cond == 'has_companion':
                comps = [str(c).lower() for c in self.player.get('companions', [])]
                if str(trans.get('companion_name')).lower() in comps:
                    self.logger.info(f"Branch triggered: Companion '{trans.get('companion_name')}' found. Routing to {eval_lvl}.")
                    nxt_lvl, nxt_rm = eval_lvl, eval_rm
                    break

            elif cond == 'random':
                import random
                level_pool = trans.get('level_pool', [])
                if level_pool:
                    nxt_lvl = random.choice(level_pool)
                    nxt_rm = eval_rm
                    self.logger.info(f"Branch triggered: RNG selected '{nxt_lvl}' from pool.")
                    break

            elif cond == 'default':
                nxt_lvl, nxt_rm = eval_lvl, eval_rm
                break

        # --- THE FIX: CATCH THE DYNAMIC HUNT FROM JSON EVALUATION ---
        if nxt_lvl in ("DYNAMIC_HUNT_EVAL", "LEVEL_TRANSITION_DYNAMIC_HUNT"):
            nxt_lvl = self._resolve_dynamic_hunt_level()

        # If a valid level was found in the JSON logic, transition to it
        if nxt_lvl:
            return nxt_lvl, nxt_rm

        # 4. FINAL FALLBACK: Linear Progression
        if current_level_id == 'level_1':
            return "level_hub", "Your Car"

        fallback_lvl = level_reqs.get('next_level_id')
        fallback_rm = level_reqs.get('next_level_start_room')
        
        if not fallback_lvl:
            self.logger.warning(f"No transition found for {current_level_id}. Defaulting to Hub.")
            return "level_hub", "Your Car"

        return fallback_lvl, fallback_rm
    
    
    def _resolve_dynamic_hunt_level(self) -> str:
        """
        Picks the next level to visit based on who is next on Death's List.
        Uses npc_workplaces to map NPC name -> workplace level_id.
        Falls back to 'level_house' if no valid target can be found.
        """
        deaths_list  = self.player.get('deaths_list', [])
        current_idx  = self.player.get('deaths_list_index', 0)
        npc_workplaces = self.player.get('npc_workplaces', {})
        npc_status   = self.player.get('npc_status', {})
 
        for i in range(current_idx, len(deaths_list)):
            name = deaths_list[i]
            if name == 'player':
                continue
            if npc_status.get(name.lower(), 'alive') == 'dead':
                continue
 
            job_data = npc_workplaces.get(name.lower(), {})
            level_id = job_data.get('level_id') if isinstance(job_data, dict) else None
 
            if level_id:
                self.logger.info(
                    f"_resolve_dynamic_hunt_level: Next target is '{name}' "
                    f"at level '{level_id}' (list index {i})"
                )
                self.player['deaths_list_index'] = i
                return level_id
 
        self.logger.warning(
            "_resolve_dynamic_hunt_level: No valid next target found. "
            "Defaulting to 'level_house'."
        )
        return 'level_house'

    def _setup_police_interrogation(self):
        """
        Dynamically rewrites the police station based on hub choices and premonition state.
        Also injects the dynamic exit so the player can leave.
        """
        status = self.player.get('police_status', 'surrendered')
        role_map = self.player.get('_premonition_role_map', {})
        auth_figure = role_map.get('authority_figure', 'The Lead Detective')
        auth_status = self.player.get('npc_status', {}).get(auth_figure.lower(), 'alive')
        auth_is_dead = auth_status in ('dead', 'deceased', 'missing')
        is_fugitive = self.player.get('is_fugitive', False)

        # ── Entry narrative ─────────────────────────────────────────────────────
        if status == 'fought':
            if auth_is_dead:
                narrative = (
                    f"You are shoved into a metal chair, handcuffed to the table. You fought the cops, and they are furious. "
                    f"'{auth_figure} is dead,' a detective snarls, slamming a file down. "
                    f"'And you had something to do with it. Start talking, or you're going away for a very long time.'"
                )
            else:
                narrative = (
                    f"You are shoved into a metal chair, handcuffed to the table. "
                    f"{auth_figure} walks in, bruised from the struggle and visibly angry. "
                    f"'You fought my officers,' they growl. 'I wanted to protect you, but now you look like a suspect. "
                    f"Explain yourself.'"
                )
        else:
            if auth_is_dead:
                narrative = (
                    f"You sit quietly at the metal table. The detectives are on edge. "
                    f"'{auth_figure} didn't make it,' one of them says quietly, staring at you. "
                    f"'You did. And we need to know why.' They un-cuff you, but the door stays locked."
                )
            else:
                narrative = (
                    f"You sit at the metal table. {auth_figure} walks in, looking exhausted but alive. "
                    f"'I'm glad you came in peacefully,' they say, sitting across from you. "
                    f"'We need to talk about what happened. Off the record. "
                    f"Because the coroner's reports... they're some freaky shit.'"
                )
        self._check_police_softlock()

        # ── Dynamic exit injection ──────────────────────────────────────────────
        knows_list = 'learned_deaths_list' in self.interaction_flags
        alive_targets = [
            n for n in self.player.get('deaths_list', [])
            if n.lower() != 'player'
            and self.player.get('npc_status', {}).get(n.lower(), 'alive') in ('alive', 'injured')
            and not self.player.get(f"visited_workplace_{n.lower()}")
        ]

        dynamic_exits = {}

        if status == 'fought' or is_fugitive:
            dynamic_exits["slip out the back"] = {
                "target": "LEVEL_TRANSITION_HUB",
                "description": "The officer steps away. Now's your chance."
            }
        else:
            dynamic_exits["leave the station"] = {
                "target": "LEVEL_TRANSITION_HUB",
                "description": "They have nothing to hold you on. You walk."
            }

        if knows_list and alive_targets:
            next_name = alive_targets[0].title()
            dynamic_exits["demand to leave — lives are at stake"] = {
                "target": "LEVEL_TRANSITION_DYNAMIC_HUNT",
                "description": f"{next_name} is going to die if you stay here."
            }

        if not alive_targets and knows_list:
            dynamic_exits["request to speak to the detective alone"] = {
                "target": "LEVEL_TRANSITION_FINALE",
                "description": "It ends here. One way or another."
            }

        # ── Apply to room ───────────────────────────────────────────────────────
        try:
            room = self.current_level_rooms_world_state["Police Station Interview Room"]
            room["first_entry_text"] = narrative
            existing_exits = room.get("exits", {})
            existing_exits.update(dynamic_exits)
            room["exits"] = existing_exits
            self.logger.info(
                f"_setup_police_interrogation: Injected narrative and "
                f"{len(dynamic_exits)} dynamic exit(s) into interview room."
            )
        except KeyError:
            self.logger.error("_setup_police_interrogation: 'Police Station Interview Room' not in world state.")

    def _populate_level_with_npcs(self):
        """Injects surviving NPCs into the current level based on workplaces, companions, and hub logic."""
        # --- THE FIX: The Load Screen Guard ---
        from kivy.app import App # <-- Fixes the "App is not defined" error
        app = App.get_running_app()
        
        # If the app has a pending load slot, abort! The save file will restore the NPCs!
        if hasattr(app.root.get_screen('game'), 'pending_load_slot') and app.root.get_screen('game').pending_load_slot:
            self.logger.info("_populate_level_with_npcs: Aborting cast distribution. Pending save load detected.")
            return
        # --------------------------------------

        current_level = str(self.player.get('current_level', ''))
        survivors = self.player.get('premonition_survivors', [])
        companions = self.player.get('companions', [])
        workplaces = self.player.get('npc_workplaces', {})
        npc_status = self.player.get('npc_status', {})
        
        # Skip for Level 0 which handle their own static casting
        if current_level in ["0", "level_0"]:
            return

        # --- THE FIX: Missing start_room definition ---
        start_room = self.player.get('location')
        if not start_room: 
            return
        # ----------------------------------------------

        # Helper to safely spawn an NPC
        def _spawn(npc_name, room_id):
            room = self.current_level_rooms_world_state.get(room_id)
            if not room: return
            if 'npcs' not in room: room['npcs'] = []
            
            # Prevent duplicates
            existing = [n.get('name', n).lower() if isinstance(n, dict) else (n.lower() if isinstance(n, str) else '') for n in room['npcs']]
            if npc_name.lower() not in existing:
                # Inject as a dict so the engine can route their dialogue based on their archetype!
                role = self.player.get('npc_roles', {}).get(npc_name.lower(), 'bystander_1')
                room['npcs'].append({"name": npc_name, "role": role})
                self.logger.info(f"Spawned '{npc_name}' ({role}) into '{room_id}'")

        # Helper to find a suitable room for a workplace employee
        def _find_room():
            # 1. Look for a room explicitly requesting NPCs
            for r_id, r_data in self.current_level_rooms_world_state.items():
                if r_data.get('npc_slots', 0) > len(r_data.get('npcs', [])): return r_id
            # 2. Fallback to the entry room
            for r_id, r_data in self.current_level_rooms_world_state.items():
                if r_data.get('entry_room'): return r_id
            return start_room

        # 1. Spawn Companions (They always stick with the player)
        for comp in companions:
            if npc_status.get(comp.lower(), 'alive') not in ['dead', 'missing']:
                _spawn(comp, start_room)

        # 2. Spawn Police Station Interrogation (If surrendered)
        if current_level == "level_police_station" and self.player.get('police_status') != 'fought':
            for npc_name in survivors:
                if npc_name not in companions and npc_status.get(npc_name.lower(), 'alive') not in ['dead', 'missing']:
                    _spawn(npc_name, start_room) # Dump everyone in the interview room/lobby!
        else:
            # 3. Spawn Workplace Employees
            for npc_name in survivors:
                if npc_name in companions: continue
                if npc_status.get(npc_name.lower(), 'alive') in ['dead', 'missing']: continue
                
                # In step 3, before the workplace check — add a hub fallback:
                HUB_LEVELS = {"level_1", "level_hub", "level_police_station"}

                for npc_name in survivors:
                    if npc_name in companions: continue
                    if npc_status.get(npc_name.lower(), 'alive') in ['dead', 'missing']: continue

                    npc_level = workplaces.get(npc_name.lower(), {}).get('level_id')
                    
                    if npc_level == current_level:
                        # Spawn at their workplace as normal
                        target = _find_room()
                        _spawn(npc_name, target)
                    elif current_level in HUB_LEVELS:
                        # Hub levels: all survivors gather here
                        target = _find_room()
                        _spawn(npc_name, target)

                # Does this NPC work at this level?
                if workplaces.get(npc_name.lower(), {}).get('level_id') == current_level:
                    target = _find_room()
                    _spawn(npc_name, target)

        # 4. Announce the Hunt Target to the AI Director
        if self.hazard_engine:
            target_name, target_room = self.hazard_engine.get_next_npc_target()
            if target_name:
                self.player['current_hunt_target'] = target_name
                self.logger.info(f"Death's Design is actively hunting '{target_name}' in this level.")

        # 5. The Visionary's Last Will (Failsafe)
        visionary_name = self.player.get('premonition_visionary')
        if visionary_name and npc_status.get(visionary_name.lower(), 'alive') in ['dead', 'missing']:
            
            # Check if the player already has the notes
            if "visionary_notes" not in self.player.get('inventory', []):
                
                vis_workplace = workplaces.get(visionary_name.lower(), {}).get('level_id')
                spawn_room = None
                
                # Are we at the Visionary's workplace?
                if current_level == vis_workplace:
                    spawn_room = _find_room() 
                # Are we at the Police Station? (Cops recovered it from the body)
                elif current_level == "level_police_station":
                    spawn_room = "Evidence Storage"
                    
                if spawn_room and spawn_room in self.current_level_rooms_world_state:
                    room_data = self.current_level_rooms_world_state[spawn_room]
                    room_data.setdefault('items', [])
                    
                    if "visionary_notes" not in room_data['items']:
                        room_data['items'].append("visionary_notes")
                        self.logger.info(f"Failsafe: Spawned 'visionary_notes' in {spawn_room} because {visionary_name} is dead.")

    # --- NEW: Item Placement Logic ---
    def _populate_level_with_items(self, level_id: int):
        """
        Places items. Respects 'Canon' (items defined in rooms/furniture) 
        and fills gaps with 'Random' loot.
        """
        try:
            self.logger.debug(f"_populate_level_with_items: Populating items for level {level_id}")
            self.current_level_items_world_state = {}
            items_master = self.resource_manager.get_data('items', {})

            # --- 1. Census: Find everything explicitly placed by the Architect (JSON) ---
            pre_placed_item_keys = set()
            all_containers = []

            for room_id, room_data in self.current_level_rooms_world_state.items():
                
                # A. Catalog Loose Items (Canon)
                # Check both 'items' and 'items_present' keys
                loose_items = room_data.get('items', []) + room_data.get('items_present', [])
                # Filter: skip inline dict items (e.g. loose_brick defined as a full object
                # in rooms JSON). These carry all their own properties and can't be used as
                # dict keys. The room data already holds them; no world-state entry needed.
                loose_items = [i for i in loose_items if not isinstance(i, dict)]
                for item_key in loose_items:
                    self.current_level_items_world_state[item_key] = {"location": room_id}
                    pre_placed_item_keys.add(item_key)
                    self.logger.debug(f"Canon item identified: '{item_key}' loose in '{room_id}'")

                # B. Catalog Container Items (Canon)
                for furniture in room_data.get('furniture', []):
                    if isinstance(furniture, dict) and furniture.get('is_container'):
                        # Ensure 'items' list exists
                        furniture.setdefault('items', [])
                        
                        # Register items already inside as pre-placed
                        for inside_item in furniture['items']:
                            pre_placed_item_keys.add(inside_item)
                            self.logger.debug(f"Canon item identified: '{inside_item}' inside '{furniture.get('name')}'")
                        
                        # Track container for random filling later
                        all_containers.append({'room': room_id, 'furniture_data': furniture})

            # --- 2. Build the Random Loot Pool ---
            # Rules: 
            #   1. Must have "is_distributable_in_containers": True
            #   2. Must NOT be in pre_placed_item_keys (Unique items shouldn't spawn twice)
            loot_pool = []
            
            for item_key, item_data in items_master.items():
                # Check distributable flag
                if not item_data.get('is_distributable_in_containers'):
                    continue
                
                # Check if it's already existing (Story items shouldn't dup)
                if item_key in pre_placed_item_keys:
                    self.logger.debug(f"Skipping random spawn for '{item_key}': Already placed in world.")
                    continue

                # Check Level restrictions (Optional: if item has specific level tag)
                item_level = item_data.get('level')
                if item_level and isinstance(item_level, int) and item_level != level_id:
                    continue
                
                # Add to pool
                loot_pool.append(item_key)

            self.logger.info(f"Loot Pool Built. Candidates: {len(loot_pool)}. Containers to fill: {len(all_containers)}")

            # --- 3. The Scattering (Distribution) ---
            if not loot_pool:
                self.logger.warning("Random loot pool is empty! Check 'items.json' for 'is_distributable_in_containers' flags.")
                return

            random.shuffle(loot_pool)
            
            # Iterate containers and fill
            for container_ref in all_containers:
                room_id = container_ref['room']
                container = container_ref['furniture_data']
                capacity = container.get('capacity', 1)
                current_load = len(container.get('items', []))
                
                # Calculate space
                space_available = capacity - current_load
                
                if space_available <= 0:
                    continue

                # Filter the remaining loot pool for this specific container.
                allowed_tags = container.get('allowed_item_tags', [])
                if allowed_tags:
                    valid_pool = []
                    for item_id in loot_pool:
                        item_data = items_master.get(item_id, {})
                        item_tags = item_data.get('tags', [])
                        if any(tag in allowed_tags for tag in item_tags):
                            valid_pool.append(item_id)
                else:
                    valid_pool = list(loot_pool)

                if not valid_pool:
                    continue

                # Fill 'er up
                added_count = 0
                while space_available > 0 and valid_pool:
                    # Determine drop chance (don't always fill to 100% capacity to keep it varied)
                    # 50% chance to stop adding to this container, unless it's the huge test container
                    if capacity < 50 and random.random() > 0.7:
                        break

                    item_to_place = random.choice(valid_pool)
                    container.setdefault('items', [])
                    container['items'].append(item_to_place)
                    valid_pool.remove(item_to_place)
                    if item_to_place in loot_pool:
                        loot_pool.remove(item_to_place)
                    
                    space_available -= 1
                    added_count += 1
                    
                    # If we run out of unique items, maybe re-add to pool? 
                    # For now, let's keep them unique per run.
                
                if added_count > 0:
                    self.logger.info(f"Added {added_count} random items to '{container.get('name')}' in '{room_id}'")

        except Exception as e:
            self.logger.error(f"_populate_level_with_items: Error: {e}", exc_info=True)

    def _compile_level_omens(self, level_id: int) -> dict:
        """
        Gathers all environmental omens from disasters, hazards, NPCs,
        and all rooms in the current level (from their environmental_omens_config blocks),
        and organizes them by trigger object.
        Injected with robust debugging logic.
        """
        self.logger.info(f"_compile_level_omens: Compiling Omen Library for level {level_id}...")
        omen_library = {}

        # --- Identify the active disaster for this playthrough ---
        # We MUST compare against 'event_description' (the raw disasters.json key,
        # e.g. "a collapse of the {city_name} Memorial Bridge") NOT against 'name'
        # (which has {city_name} already replaced with the real city name).
        # The disasters dict is keyed by the raw template string, so only
        # event_description will ever produce an exact match.
        intro_disaster = self.player.get('intro_disaster', {})
        active_disaster_key = intro_disaster.get('event_description', '')
        self.logger.info(
            f"_compile_level_omens: Matching omens for disaster key='{active_disaster_key}' "
            f"(formatted name='{intro_disaster.get('name', '?')}')"
        )

        # Sources to search for the 'environmental_omens' key
        sources = [
            ("disasters", self.resource_manager.get_data('disasters', {})),
            ("hazards", self.resource_manager.get_data('hazards', {})),
            ("npcs", self.resource_manager.get_data('npcs', {}))
        ]
        
        for source_type, source_dict in sources:
            for entity_key, entity_data in source_dict.items():
                
                # --- Strict Filter & Debug Logic for Disasters ---
                if source_type == "disasters":
                    # Compare raw key against event_description (both are unformatted template strings)
                    if entity_key != active_disaster_key:
                        self.logger.debug(f"_compile_level_omens: Skipping '{entity_key}' (not active disaster).")
                        continue
                    else:
                        self.logger.info(f"_compile_level_omens: MATCH — compiling omens from '{entity_key}'.")
                # ------------------------------------------------------

                omens = entity_data.get('environmental_omens', {})
                if not omens:
                    continue
                    
                # Organize by trigger object (e.g., 'television', 'radio')
                for trigger_obj, omen_list in omens.items():
                    if trigger_obj not in omen_library:
                        omen_library[trigger_obj] = []
                        
                    if isinstance(omen_list, list):
                        omen_library[trigger_obj].extend(omen_list)
                    else:
                        omen_library[trigger_obj].append(omen_list)
                        
                    # Debug log to show exactly what was added and where it came from
                    self.logger.debug(f"_compile_level_omens: Added omens for '{trigger_obj}' from {source_type}: {entity_key}")

        # --- Merge in omens from all rooms in the current level ---
        all_rooms = self.resource_manager.get_data('rooms', {})
        level_rooms = all_rooms.get(str(level_id), {})
        for room_id, room_data in (level_rooms or {}).items():
            env_omens_cfg = room_data.get('environmental_omens_config', {})
            if env_omens_cfg:
                self.logger.debug(f"_compile_level_omens: Found 'environmental_omens_config' in room '{room_id}'.")
                for trigger, omen_text in env_omens_cfg.items():
                    if trigger not in omen_library:
                        omen_library[trigger] = []
                        self.logger.debug(f"_compile_level_omens: Created new trigger '{trigger}' from room '{room_id}'.")
                    if isinstance(omen_text, list):
                        self.logger.debug(f"_compile_level_omens: Adding list of omens for trigger '{trigger}' from room '{room_id}'.")
                        omen_library[trigger].extend(omen_text)
                    else:
                        self.logger.debug(f"_compile_level_omens: Adding single omen for trigger '{trigger}' from room '{room_id}'.")
                        omen_library[trigger].append(omen_text)

        # --- THE FIX: Forward-Looking Omens ---
        # Inject omens about upcoming workplaces and the finale!
        if not hasattr(self, 'omen_library'):
            self.omen_library = {'general': []}
            
        workplaces = self.player.get('npc_workplaces', {})
        deaths_list = self.player.get('deaths_list', [])
        
        for npc in deaths_list:
            job_data = workplaces.get(npc.lower())
            if job_data:
                level_hint = job_data.get('level_id')
                if level_hint == "level_vet":
                    self.omen_library['general'].extend([
                        "You hear the faint, phantom sound of a dog whining in pain.",
                        "For a split second, the room smells strongly of bleach and wet fur."
                    ])
                elif level_hint == "level_coffee":
                    self.omen_library['general'].extend([
                        "A sudden blast of steam hisses from a nearby pipe, sounding exactly like an espresso machine.",
                        "You catch the distinct, bitter scent of burnt coffee beans."
                    ])
                elif level_hint == "level_bowling":
                    self.omen_library['general'].extend([
                        "A heavy, rolling rumble echoes through the floorboards, ending in a sharp crash.",
                        "You notice a heavy sphere resting precariously near an edge."
                    ])
                    
        # --- THE FIX: Conditional Finale Foreshadowing ---
        # Only drop these heavy omens if the player has gathered 
        # the items necessary to unlock at least ONE of the Crossroads paths!
        
        inventory = self.player.get('inventory', [])
        
        # Define what constitutes a "Unlocked Path"
        path_override_unlocked = "Warehouse Key" in inventory and "Defibrillator Pads" in inventory
        path_flatline_unlocked = "Vet Sedatives" in inventory and "Adrenaline" in inventory
        path_dark_unlocked = "Leather Journal" in inventory in inventory
        # (Future-Proofing) As you build more endings, just add their requirements here!
        # path_dark_unlocked = "Coroner's Journal" in inventory
        # path_sacrifice_unlocked = "Loaded Revolver" in inventory
        
        if path_override_unlocked or path_flatline_unlocked:
            self.omen_library['general'].extend([
                "You feel a sudden, sharp pain in your chest, like an electrical shock.",
                "The rhythmic beeping of a machine momentarily replaces the silence in your ears."
            ])
            
        self.logger.info("Forward-Looking Omens injected into the library.")

        self.logger.info(f"_compile_level_omens: Omen Library compiled with {len(omen_library)} trigger types.")
        return omen_library

    def _build_visionary_call_events(self) -> list:
        """The Connective Tissue: The Visionary calls the player at the Hub."""
        events = []
        role_map = self.player.get('_premonition_role_map', {})
        visionary_name = role_map.get('visionary', 'The visionary').title()
        status = self.player.get('npc_status', {}).get(visionary_name.lower(), 'alive')
        
        # 1. Give them the crucial knowledge flag so the Hub unlocks!
        if not hasattr(self, 'interaction_flags'):
            self.interaction_flags = set()
        self.interaction_flags.add('learned_deaths_list')
        self.player['learned_deaths_list'] = True
        
        # 2. Add them as a companion if they are alive!
        if status in ('alive', 'injured'):
            if visionary_name not in self.player.setdefault('companions', []):
                self.player['companions'].append(visionary_name)
                
            narrative = (
                f"You reach the safety of your car and lock the doors. You finally have a moment to breathe.\n\n"
                f"Your phone vibrates. It's {visionary_name}.\n\n"
                f"'{visionary_name}? Where are you?'\n\n"
                f"'I figured it out,' they say, their voice trembling. 'The accidents. The people dying. I looked up situations like this online, it's happened before.\nA LOT.\n"
                f"The people who survived... if what these Reddit posts are saying is true.. we're dying in the exact order we would have died in the disaster.'\n\n"
                f"A chill runs down your spine.\n\n"
                f"'I'm not waiting around to be next,' {visionary_name} continues. 'There's ways to stop it. I'm coming to find you. We have to stick together.'"
            )
        else:
            # Fallback if the Visionary died
            narrative = (
                f"You reach the safety of your car and lock the doors.\n\n"
                f"You pull out your phone and start searching online for anything about people dying after walking away from an event that killed others.\nWhat you find is chilling.\n\n"
                f"Articles saying survivors claimed they were being stalked after their respective disasters. Sometimes by other survivors, and others, more creepily, that [color=00ff00]DEATH[/color] itself was after them.\n\n"
                f"It hit you like a freight train. A pattern. You're dying in the exact order you would have died in the disaster. "
                f"And your name is on that list."
            )
            
        events.append({
            "event_type": "show_popup",
            "title": "The Pattern Revealed",
            "message": narrative,
            "priority": 1000
        })
        
        return events

    def start_next_level(self, level_id=None, start_room=None):
        """Advances to the next level using a clean, delegated pipeline."""
        self.logger.info(f"start_next_level: Requested level_id={level_id}, start_room={start_room}")

        # 1. Resolve Level ID string
        level_id = self._resolve_next_level_id(level_id)
        self.logger.info(f"start_next_level: Advancing to '{level_id}'")

        # 2. Extract and preserve persistent player data
        saved_state = self._preserve_persistent_state()

        # 3. Wipe current level and rebuild the new one
        self._reset_for_new_level(level_id)
        self._initialize_level_data(level_id)

        # 4. Restore the persistent data
        self._restore_persistent_state(saved_state)
        
        # 5. Place the Player (and Companion)
        self._place_entities_in_start_room(level_id, start_room)

        # 6. Generate the UI response
        return self._generate_level_entry_response()


    # ---------------------------------------------------------
    # --- start_next_level Helpers ---
    # ---------------------------------------------------------
    def _resolve_next_level_id(self, requested_id):
        if requested_id is not None:
            return str(requested_id)
            
        current = str(self.player.get('current_level', 'level_0'))
        if current.startswith('level_') and current[6:].isdigit():
            return f"level_{int(current[6:]) + 1}"
        return "level_1"

    def _preserve_persistent_state(self) -> dict:
        """Pulls out all stats that should survive a level transition."""
        return {
            'inventory': self.player.get('inventory', []),
            'hp': self.player.get('hp', 30),
            'max_hp': self.player.get('max_hp', 30),
            'fear': self.player.get('fear', 0.0),
            'score': self.player.get('score', 0),
            'character_class': self.player.get('character_class'),
            'flags': self.player.get('flags', set()),
            'status_effects': self.player.get('status_effects', {}),
            'evaded_hazards': self.player.get('evaded_hazards', []),
            
            # --- THE FIX: Narrative & Tracking State ---
            'deaths_list': self.player.get('deaths_list', []),
            'deaths_list_index': self.player.get('deaths_list_index', 0),
            'npc_roles': self.player.get('npc_roles', {}),
            'npc_status': self.player.get('npc_status', {}),
            'npc_workplaces': self.player.get('npc_workplaces', {}),
            
            # --- THE FIX: The Party! ---
            'companions': self.player.get('companions', []),
            
            # World State tracking
            'intro_disaster': self.player.get('intro_disaster', {}),
            'current_city': self.player.get('current_city', 'McKinley'),
            'premonition_visionary': self.player.get('premonition_visionary', 'Someone'),
            '_premonition_role_map': self.player.get('_premonition_role_map', {}),
            'offscreen_casualties': self.player.get('offscreen_casualties', [])
            # --------------------------------------------
        }

    def _reset_for_new_level(self, level_id: str):
        self._cancel_premonition_timer()
        self.player.pop('level_complete_flag', None)
        self.player.pop('notified_requirements_met', None)
        self.is_transitioning = False

        game_config = self.resource_manager.get_data('game_config', {})
        self.player['turns_left'] = game_config.get('INITIAL_TURNS', 180)
        self.player['actions_taken'] = 0
        self.player['current_level'] = level_id
        self.player['qte_active'] = False
        self._drain_qte_queue()
        self.player['qte_context'] = {}
        self.last_dialogue_context = {} 
        self.current_level_rooms_world_state.clear()
        self.current_level_items_world_state.clear()
        self.interaction_flags.clear()
        keys_to_remove = [k for k in self.player if k.startswith('_entry_popup_shown_')]
        for k in keys_to_remove:
            del self.player[k]

    def _drain_qte_queue(self):
        """Fire the next queued QTE consequence if one exists and no QTE is active."""
        if self.player.get('qte_active'):
            return
        queue = self.player.get('_qte_queue', [])
        if queue:
            next_consequence = queue.pop(0)
            self.player['_qte_queue'] = queue
            self.logger.info(f"_drain_qte_queue: Dequeuing '{next_consequence.get('qte_type')}'")
            self._handle_conseq_start_qte(next_consequence, 0)

    def _restore_persistent_state(self, state: dict):
        for key, value in state.items():
            if key == 'hp':
                self.player[key] = min(value, state.get('max_hp', 30))
            else:
                self.player[key] = value

    def _place_entities_in_start_room(self, level_id: str, requested_start: str):
        # Determine Room
        entry_room = requested_start or self.resource_manager.get_data('level_requirements', {}).get(level_id, {}).get('entry_room')
        if not entry_room: entry_room = self.player.get('location')
        if not entry_room and self.current_level_rooms_world_state:
            entry_room = list(self.current_level_rooms_world_state.keys())[0]

        # Place Player
        self.player['location'] = entry_room
        self.player['visited_rooms'] = {entry_room}

        # Place Companion
        companion = self.player.get('companion_id')
        if companion:
            self._move_npc(companion, entry_room)

    def _generate_level_entry_response(self) -> dict:
        initial_room_id = self.player.get('location')
        initial_room_data = self.get_room_data(initial_room_id) or {}
        
        ui_events = []
        first_entry_text = initial_room_data.get('first_entry_text')
        
        # --- THE FIX: Use list [] default instead of set() ---
        already_shown = initial_room_id in self.player.setdefault('shown_entry_popups', [])
        
        if first_entry_text and not already_shown:
            
            # --- THE FIX: Use .append() instead of .add() ---
            self.player['shown_entry_popups'].append(initial_room_id)
            
            # 3. Format text
            first_entry_text = self._format_dynamic_text(first_entry_text)
            
            # 4. Push to the start_response array ONLY. 
            ui_events.append(self._make_first_entry_popup_event(initial_room_id, first_entry_text))
            
        elif not first_entry_text:
            # 5. Clean fallback if no text exists
            ui_events.append({
                "event_type": "show_popup", 
                "title": "Entering Next Area", 
                "message": "Stay sharp."
            })

        # 6. Return the perfectly sanitized response
        self.start_response = {
            "messages": [self._get_rich_room_description(initial_room_id)],
            "game_state": self.get_current_game_state(),
            "ui_events": ui_events,
            "turn_taken": False,
            "success": True
        }
        return self.start_response

    # --- NEW: Core Gameplay Loop ---

    def _handle_qte_resolution(self, qte_result: dict) -> dict:
        """Handle QTE completion natively with proper consequence processing"""
        self.player['qte_active'] = False
        
        self.add_ui_event({"event_type": "destroy_qte_popup", "priority": 1000})
        if getattr(self, 'death_ai', None):
            action = 'qte_success' if qte_result.get('success', False) else 'qte_failure'
            self.death_ai.analyze_player_action(action, location=self.player.get('location'))

        result_messages = []
        if qte_result.get('message'):
            color = "success" if qte_result.get('success') else "error"
            result_messages.append(color_text(qte_result['message'], color, self.resource_manager))

        # 1. Base Success Effects & Entropy
        if qte_result.get('success'):
            effects_list = qte_result.get('effects_on_success', [])
            self._apply_qte_success_effects(effects_list)
            if getattr(self, 'death_ai', None): 
                self.logger.info("QTE Success - Increasing Entropy.")
                self.death_ai.increase_entropy(2.0)

        # --- THE FIX: Halt all other processing if a Finale Chain is active! ---
        if self._qte_resolve_finale(qte_result):
            return self._build_response(messages=result_messages)

        # 3. Player Damage & Sacrifice
        self._qte_resolve_player_damage(qte_result, result_messages)
        if getattr(self, 'is_game_over', False):
            return self._build_response(messages=result_messages)

        # 4. NPC Fate
        self._qte_resolve_npc_fate(qte_result, result_messages)

        # 5. Hazard State Transitions & Chains
        self._qte_resolve_hazard_state(qte_result)
        if getattr(self, 'is_game_over', False):
            return self._build_response(messages=result_messages)

        # 6. Elevator Auto-Resume
        self._qte_resolve_elevator(qte_result)

        # 7. Resolve Pending Movement
        self.add_ui_event({"event_type": "_drain_qte_queue", "priority": -1})
        return self._qte_resolve_movement(qte_result, result_messages)

    # -------------------------------------------------------------------------
    # --- QTE Resolution Helpers ---
    # -------------------------------------------------------------------------
    def _qte_resolve_finale(self, qte_result: dict) -> bool:
        """Checks if this QTE was part of the final challenge. Returns True if handled."""
        pending_finale = self.player.get('pending_finale_victory')
        if not pending_finale:
            return False
            
        if not qte_result.get('success'):
            self.logger.info("Player failed a Finale QTE. Forcing death sequence.")
            self.player['finale_qte_chain'] = [] 
            
            self.player['hp'] = 0
            self.is_game_over = True
            
            # --- THE FIX: Set the exact variable names LoseScreen expects! ---
            self.death_reason = "You hesitated at the critical moment."
            self.death_narrative = "Your final attempt to cheat has failed. Death doesn't want to play with you anymore.\n...because it already won."
            self.player['death_reason'] = self.death_reason
            
            self.add_ui_event({
                "event_type": "show_popup", 
                "title": "YOU DIED", 
                "message": self.death_reason,
                "on_close_emit_ui_events": [{"event_type": "player_death"}]
            })
            return True
            
        self._trigger_next_finale_qte()
        return True

    def _qte_resolve_player_damage(self, qte_result: dict, result_messages: list):
        """Handles damage to the player and Companion Sacrifice mechanics."""
        skipped = self.player.setdefault('death_design_skipped', [])
        target_npc = qte_result.get('target_npc')
        
        # --- THE ULTIMATE SACRIFICE ---
        if not qte_result.get('success') and qte_result.get('is_fatal') and not target_npc:
            active_companions = [c.lower() for c in self.player.get('companions', [])]
            room_data = self.get_room_data(self.player.get('location')) or {}
            npcs_in_room = [n.get('name', n).lower() if isinstance(n, dict) else n.lower() for n in room_data.get('npcs', [])]
            
            sacrificed_companion = None
            for c in active_companions:
                if c in npcs_in_room:
                    sacrificed_companion = c.title()
                    break
                    
            if sacrificed_companion:
                qte_result['is_fatal'] = False
                qte_result['hp_damage'] = 0
                
                # Gracefully kill via the global Dispatcher so the Group array handles it properly
                self.handle_hazard_consequence({
                    "type": "npc_killed_by_hazard",
                    "hazard_id": qte_result.get("qte_source_hazard_id"),
                    "npc_name": sacrificed_companion,
                    "popup_text": f"\n\n[color=ff0000]Just as the hazard is about to claim you, {sacrificed_companion} shoves you out of the way! They take the lethal blow meant for you...[/color]"
                })
                
                if 'player' not in skipped:
                    skipped.append('player')
                    
        # --- STANDARD DAMAGE ---
        damage = 0
        if not qte_result.get('success'):
            damage = qte_result.get('hp_damage', 0)
            is_fatal = qte_result.get('is_fatal', False)
            
            if is_fatal and damage == 0:
                damage = self.player.get('hp', 100)
                
            if damage > 0:
                self.apply_damage(damage, source="failing to react")
                
            if self.player.get('hp', 30) <= 0 or is_fatal:
                self._trigger_qte_death(qte_result)
                return 

        # Bleeding status
        if damage > 0:
            self.player.setdefault('status_effects', {})['bleeding'] = 10
            self.logger.info("Player gained 'bleeding' status (10 turns)")

    def _qte_resolve_npc_fate(self, qte_result: dict, result_messages: list):
        """Delegates all NPC deaths, saves, and splash damage to the Dispatcher."""
        target_npc = qte_result.get('target_npc')
        skipped = self.player.setdefault('death_design_skipped', [])

        if not target_npc:
            return

        if qte_result.get("success"):
            if target_npc not in skipped:
                skipped.append(target_npc)

            if qte_result.get("npc_sacrifice_mode"):
                result_messages.append(f"\n\n[color=00ff00]{target_npc} pushed you clear! They're hurt but alive.[/color]")
                self.player.setdefault("npc_status", {})[target_npc.lower()] = "injured"
            else:
                result_messages.append(f"\n\n[color=00ff00]You pulled {target_npc} out of harm's way! Death will have to look elsewhere.[/color]")

            if getattr(self, 'death_ai', None):
                self.death_ai.increase_entropy(3.0)

        elif not qte_result.get("success"):
            if qte_result.get("npc_fatal_on_failure"):
                custom_failure_msg = qte_result.get("failure_message", "You failed to save them.")
                self.handle_hazard_consequence({
                    "type": "npc_killed_by_hazard",
                    "hazard_id": qte_result.get("qte_source_hazard_id"),
                    "npc_name": target_npc,
                    "popup_text": f"{custom_failure_msg}\n\n{target_npc} is gone."
                })
                
            elif qte_result.get("npc_sacrifice_mode"):
                splash = int(qte_result.get("player_splash_damage", 10))
                if splash > 0:
                    self.apply_damage(splash, source="hazard splash (sacrifice)")
                    
                self.handle_hazard_consequence({
                    "type": "npc_killed_by_hazard",
                    "hazard_id": qte_result.get("qte_source_hazard_id"),
                    "npc_name": target_npc,
                    "popup_text": f"{target_npc} gave their life for you — and it still wasn't enough."
                })

    def register_intervention(self, saved_npc: str):
        """Shifts Death's Design to the next target and handles narrative consequences."""
        self.logger.info(f"INTERVENTION: Player saved {saved_npc} from Death's Design!")
        
        # 1. Update the Death List (The Endless Loop)
        death_list = self.player.get('deaths_list', [])
        if saved_npc in death_list:
            # Remove them from their current spot in the firing squad...
            death_list.remove(saved_npc)
            # ...and put them at the absolute back of the line!
            death_list.append(saved_npc)
            self.player['deaths_list'] = death_list
            
        # Track the save for End-Game scoring
        self.player.setdefault('death_design_skipped', []).append(saved_npc)
        
        # 2. Heal Visionary Distrust
        flags = self.player.setdefault('flags', {})
        if flags.get('visionary_distrusted'):
            self.logger.info("Visionary witnessed the save. Distrust removed.")
            flags['visionary_distrusted'] = False
            flags['visionary_trusts_player'] = True
            
            self.add_ui_event({
                "event_type": "show_message",
                "message": "\n[color=800080]The Visionary watches you pull them from the brink. The look of betrayal in their eyes softens into an uneasy realization: you are fighting back.[/color]\n"
            })
            
        # 3. Tell the AI Director to immediately acquire the new target
        if hasattr(self, 'hazard_engine'):
            new_target, _ = self.hazard_engine.get_next_npc_target()
            self.player['current_hunt_target'] = new_target
            self.logger.info(f"Death's Design updated. New target is: {new_target}")
            
        # 4. Narrative UI Feedback
        self.add_ui_event({
            "event_type": "show_message",
            "message": f"\n[color=00ffff]You broke the chain! By saving {saved_npc}, Death has been forced to skip them... for now. The design immediately moves to the next survivor.[/color]\n"
        })

    def _check_police_softlock(self):
        """If player arrived at the police station without learning the deaths list,
        the detective shows them the evidence board as a narrative softlock bypass."""
        if 'learned_deaths_list' in getattr(self, 'interaction_flags', set()):
            return  # Already knows — no intervention needed

        inv_norm = {str(i).lower() for i in self.player.get('inventory', [])}
        has_clue = any(k in inv_norm for k in [
            'bludworths_house_key', 'coroners_office_key', 'coroners_report', 'deaths_list'
        ])
        if has_clue:
            return  # They have a physical clue — they'll figure it out

        # Inject the flag and the narrative evidence board item into the interview room
        if not hasattr(self, 'interaction_flags'):
            self.interaction_flags = set()
        self.interaction_flags.add('learned_deaths_list')
        self.player['learned_deaths_list'] = True

        # Grant bludworths_house_key as the exit item if they don't have it
        if 'bludworths_house_key' not in inv_norm:
            self.player.setdefault('inventory', []).append('bludworths_house_key')

        # Queue a narrative popup to explain it in-world
        auth_figure = self.player.get('_premonition_role_map', {}).get('authority_figure', 'The Detective')
        self.add_ui_event({
            "event_type": "show_popup",
            "title": "Evidence Board",
            "message": (
                f"{auth_figure} slides a folder across the table.\n\n"
                f"[color=00ffff]'We've been tracking the survivors. Some of them are already dead — "
                f"in ways that make no sense. Look at this list.'[/color]\n\n"
                f"You read the names. You know every single one of them.\n\n"
                f"At the bottom of the folder is a worn house key with a tag: [color=ffff00]BLUDWORTH[/color].\n\n"
                f"[color=aaaaaa]'{auth_figure} pushes it toward you. \"Go. Before this gets worse.\"'[/color]"
            )
        })
        self.logger.info("_check_police_softlock: Softlock bypassed via evidence board narrative.")

    def _check_hub_softlock(self):
        if str(self.player.get('current_level', '')) not in ["level_hub", "hub"]:
            return
            
        if 'learned_deaths_list' in getattr(self, 'interaction_flags', set()) or self.player.get('learned_deaths_list'):
            return

        inventory = self.player.get('inventory', [])
        clue_ids = {"bludworths_house_key", "coroners_office_key", "coroners_report", "deaths_list"}
        has_clue = any(normalize_text(item if isinstance(item, str) else item.get('id', '')) in clue_ids for item in inventory)
        
        if not has_clue and not self.player.get('hub_fallback_triggered'):
            self.player['hub_fallback_triggered'] = True
            
            visionary_name = self.player.get('premonition_visionary', 'Someone')
            status = self.player.get('npc_status', {}).get(visionary_name.lower(), 'alive')

            # --- DARK PATH CHECK ---
            deaths_list = self.player.get('deaths_list', [])
            alive_npcs = [n for n in deaths_list if n.lower() != 'player' and self.player.get('npc_status', {}).get(n.lower(), 'alive') in ('alive', 'injured')]
            
            if not alive_npcs:
                self.logger.warning("All NPCs are dead. Triggering Dark Path Finale.")
                self.interaction_flags.add('dark_path_finale')
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "Alone",
                    "message": "The silence in the car is deafening. There is no one left to call. No one left to save. You are the only one left on the list.\n\nYou have to face Death alone. Your chances of coming back are slim."
                })
                # Expose only the finale exit
                self._setup_hub_exits("Your Car") 
                self.add_ui_event({"event_type": "refresh_context_actions"})
                return
            # -----------------------

            if status in ('alive', 'injured'):
                # (Keep your existing Visionary alive fallback logic here)
                pass
            else:
                # --- THE GAMBLE FLOW ---
                self.logger.info("Visionary is dead. Triggering Blind Gamble.")
                self.interaction_flags.add('blind_gamble_active')
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "A Shot in the Dark",
                    "message": f"{visionary_name}'s dead, but you can't just sit here in your car.\n\nYou pull out your phone and start researching situations like this online, only to find it's happened before.\nA LOT.\n"
                                f"The people who survived... if what these Reddit posts are saying is true.. we're dying in the exact order we would have died in the disaster. It's a little late to be skeptical.\n\n You have to guess who is in danger and get to their workplace before it's too late.\n\nIf you guess wrong... whoever was actually next on the list is going to die."
                })                
                
                # Setup specific exits just for the gamble
                self._setup_gamble_exits(alive_npcs)
                self.add_ui_event({"event_type": "refresh_context_actions"})

    def _setup_gamble_exits(self, alive_npcs: list):
        """Populates the Hub with exits for every living NPC for the blind guess."""
        dynamic_exits = {}
        npc_workplaces = self.player.get('npc_workplaces', {})
        visited_levels = self.player.get('visited_levels', set())

        for npc in alive_npcs:
            wp = npc_workplaces.get(npc.lower(), {})
            level_id = wp.get('level_id')
            
            if not level_id or level_id in visited_levels:
                continue

            wp_name = wp.get('workplace_name', npc.title())
            exit_label = f"drive to {wp_name} (guess {npc.title()})".lower()
            
            dynamic_exits[exit_label] = {
                "target": level_id,
                "npc_target": npc.lower(),
                "gamble_choice": True # Special flag to catch in transition
            }

        room_data = self.current_level_rooms_world_state.get("Your Car")
        if not room_data:
            room_data = {}
            self.current_level_rooms_world_state["Your Car"] = room_data
            
        room_data['exits'] = dynamic_exits
        self.logger.info(f"Injected {len(dynamic_exits)} Gamble Exits into 'Your Car'.")

    def _qte_resolve_hazard_state(self, qte_result: dict):
        """Transitions the hazard state natively and queues un-buffered chained QTEs safely."""
        import copy
        
        hazard_id = qte_result.get('qte_source_hazard_id')
        if not hazard_id or not getattr(self, 'hazard_engine', None):
            return
            
        next_state = None
        if qte_result.get('success'):
            next_state = qte_result.get('next_state_success') or qte_result.get('next_state_after_qte_success')

            # --- THE FIX: Check for an Intervention! ---
            # qte_result contains the context from when the QTE was built
            qte_ctx = qte_result.get('qte_context', {})
            if "saved_npc_name" in qte_ctx:
                self.register_intervention(qte_ctx["saved_npc_name"])
            # -------------------------------------------

        else:
            next_state = qte_result.get('next_state_failure') or qte_result.get('next_state_after_qte_failure')

        if not next_state:
            return

        # 1. Native State Transition
        self.logger.info(f"_qte_resolve_hazard_state: Transitioning hazard '{hazard_id}' to '{next_state}'")
        hazard_transition_result = self.hazard_engine.set_hazard_state(hazard_id, next_state)
        
        for cons in hazard_transition_result.get("consequences", []):
            self.handle_hazard_consequence(cons)

        self.player.setdefault('_qte_processed_hazards_this_turn', set()).add(hazard_id)

        # ---------------------------------------------------------------------
        # THE ULTIMATE SAFETY NET & DEATH TRAP CATCHER
        # ---------------------------------------------------------------------

        try:
            # We explicitly use the hazard ID prefix to ensure we look up the right JSON block
            h_key = hazard_id.split('#')[0] 
            
            hazards_db = getattr(self.hazard_engine, 'hazards_master_data', {})
            state_data = hazards_db.get(h_key, {}).get('states', {}).get(next_state, {})
            
            if not state_data:
                self.logger.warning(f"SafetyNet: Could not find JSON state data for '{h_key}' -> '{next_state}'")
                return

            # If the QTE dumped the player into a terminal death state, kill them immediately!
            if state_data.get('instant_death_in_room') or (state_data.get('is_terminal_state') and 'death_message' in state_data):
                self.logger.info(f"SafetyNet: Caught terminal death trap in '{next_state}'!")
                
                desc = state_data.get('description', '')
                death_msg = state_data.get('death_message', 'You were killed.')
                final_narrative = f"{desc}\n\n[color=ff0000]{death_msg}[/color]" if desc else death_msg
                
                # --- THE UPGRADE FIX ---
                # If the player already died from generic HP loss this exact millisecond,
                # we UPGRADE it to this superior narrative death.
                if getattr(self, 'is_game_over', False):
                    self.logger.info("Upgrading generic HP death to Narrative Death!")
                    
                    if hasattr(self, 'ui_event_queue'):
                        # Using [:] modifies the original list in memory, preventing reference breaks!
                        self.ui_event_queue[:] = [e for e in self.ui_event_queue if e.get('event_type') != 'game_over']
                    
                    # Temporarily lift the death lock to allow the upgrade
                    self.is_game_over = False 
                # -----------------------
                # -----------------------
                
                self.is_game_over = True
                
                # 1. Fire a destroyer to violently wipe out any stranded popups blocking the screen
                self.add_ui_event({"event_type": "destroy_info_popup", "priority": 1000})
                
                # 2. Fire the upgraded Game Over event with maximum priority to jump the queue
                self.add_ui_event({
                    "event_type": "game_over",
                    "death_reason": death_msg,
                    "final_narrative": final_narrative,
                    "hide_stats": True,
                    "priority": 10000 
                })
                return

            # --- Stop Double-Queuing QTEs ---
            target_npc = qte_result.get('target_npc')
            if target_npc and 'npc_intervention_qte' in state_data:
                self.logger.info(f"SafetyNet: Forcing NPC intervention QTE for '{target_npc}'.")
                qte_cfg = state_data['npc_intervention_qte']
                
                cons = {
                    "type": "start_qte",
                    "hazard_id": hazard_id,
                    "qte_type": qte_cfg.get("qte_type"),
                    "qte_context": copy.deepcopy(qte_cfg.get("qte_context", {}))
                }
                
                desc = state_data.get('description', '')
                if desc:
                    cons['qte_context']['description'] = desc

                cons['qte_context']['target_npc'] = target_npc
                for k, v in cons['qte_context'].items():
                    if isinstance(v, str) and "{npc_name}" in v:
                        cons['qte_context'][k] = v.replace("{npc_name}", target_npc)
                self._handle_conseq_start_qte(cons, depth=0)

        except Exception as e:
            self.logger.error(f"SafetyNet: Exception caught: {e}", exc_info=True)

    def _qte_resolve_elevator(self, qte_result: dict):
        """
        Called by _handle_qte_resolution when a QTE originated from the elevator hazard.
        Routes outcome:
        - shaking QTE success  → resume transit timer (2s) → arrive
        - shaking QTE failure  → hazard escalates to cable_snap (handled by consequence pipeline)
        - cable_snap success   → emergency_brake_catches → force-move to basement
        - cable_snap failure   → plunge QTE (handled by consequence pipeline)
        - plunge success       → hard_landing_survival → force-move to basement
        - plunge failure       → impact (game over)
        """
        hazard_id = qte_result.get('qte_source_hazard_id')
        if not hazard_id or 'elevator_freefall' not in hazard_id:
            return  # Not an elevator QTE

        success = qte_result.get('success', False)

        # Determine which state the QTE just resolved
        if success:
            next_state = (
                qte_result.get('next_state_success') or
                qte_result.get('next_state_after_qte_success')
            )
        else:
            next_state = (
                qte_result.get('next_state_failure') or
                qte_result.get('next_state_after_qte_failure')
            )

        # Terminal states where we forcibly land the player — handled by hazard consequences.
        # For SUCCESSFUL recovery from shaking or cable_snap, resume the trip.
        recovery_states = ('emergency_brake_catches', 'hard_landing_survival')
        if next_state in recovery_states:
            # Hazard consequence pipeline will move player to Basement Elevator Lobby.
            # Clear transit flags so we don't double-arrive.
            self.player.pop('elevator_transit_active', None)
            self.player.pop('pending_elevator_dest', None)
            self.player.pop('pending_elevator_floor', None)
            return

        if success and next_state == 'moving_unstable':
            # Player survived the shaking QTE — resume travel with a short delay
            self.add_ui_event({
                "event_type": "show_message",
                "message": "\n[color=ffaa00]The elevator groans and lurches back into motion...[/color]\n"
            })
            from kivy.clock import Clock
            Clock.schedule_once(
                lambda dt: getattr(self, '_deliver_elevator_arrival', lambda: None)(),
                2.0
            )

    def _qte_resolve_movement(self, qte_result: dict, result_messages: list) -> dict:
        """Resolves auto-movement or legacy movement post-QTE for the whole group."""
        if qte_result.get('success'):
            # 1. Handle Pending Move (Auto-Enter Room)
            if qte_result.get('pending_move'):
                msg = qte_result.get('message')
                if msg:
                    self.add_ui_event({"event_type": "show_message", "message": msg})
                return self._handle_pending_move(qte_result)

            # 2. Handle Legacy Movement
            if qte_result.get('move_player_to'):
                dest = qte_result['move_player_to']
                self.player['location'] = dest
                self.player.setdefault('visited_rooms', set()).add(dest)
                self.add_ui_event({"event_type": "refresh_map"})
                
                # Move the whole survivor group seamlessly
                companions = self.player.get('companions', [])
                old_room_data = self.get_room_data(self.player.get('location')) or {}
                new_room_data = self.get_room_data(dest) or {}
                for c in companions:
                    if 'npcs' in old_room_data:
                        old_room_data['npcs'] = [n for n in old_room_data['npcs'] if (n.get('name', n) if isinstance(n, dict) else n).lower() != c.lower()]
                    new_room_data.setdefault('npcs', []).append(c)

        return self._build_response(messages=result_messages)

    def _is_fatal_success(self, qte_result: dict) -> bool:
        """Check if QTE was a fatal success (e.g., forcing MRI-sealed door)."""
        return qte_result.get('success', False) and qte_result.get('is_fatal', False)

    def _handle_fatal_success(self, qte_result: dict) -> dict:
        """
        Handle QTE success that results in player death.
        Transitions hazard to death state and triggers game over.
        """
        try:
            self.logger.info("_handle_fatal_success: Processing fatal success scenario")
            hazard_id = qte_result.get('qte_source_hazard_id')
            next_state = qte_result.get('next_state_success') or qte_result.get('next_state_after_qte_success')

            # Transition hazard to death state
            if hazard_id and next_state and self.hazard_engine:
                self.logger.info(f"Fatal success: transitioning hazard '{hazard_id}' to '{next_state}'")
                result = self.hazard_engine.set_hazard_state(hazard_id, next_state)
                if result:
                    for cons in result.get('consequences', []):
                        self.handle_hazard_consequence(cons)

            # Trigger game over
            self.is_game_over = True
            death_reason = (
                qte_result.get('death_reason') or
                self.player.get('death_reason') or
                qte_result.get('message', 'Died from a successful but fatal action.')
            )
            self.player['death_reason'] = death_reason
            
            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": death_reason,
                "final_narrative": self.get_death_narrative(),
                "player_state": self.player.copy(),
            })
            
            return self._build_response()

        except Exception as e:
            self.logger.error(f"_handle_fatal_success: Error: {e}", exc_info=True)
            # Fallback to generic death
            self.is_game_over = True
            self.player['death_reason'] = "Fatal error during resolution."
            return self._build_response()

    def _handle_qte_damage_and_death(self, qte_result: dict) -> bool:
        """
        Apply HP damage from QTE failure and check for death.
        Returns True if player died, False otherwise.
        """
        if qte_result.get('success', False):
            return False  # No damage on success

        hp_damage = int(qte_result.get('hp_damage', 0))
        if hp_damage <= 0:
            return False  # No damage to apply

        self.logger.info(f"_handle_qte_damage_and_death: Applying {hp_damage} damage")
        
        # Apply damage
        current_hp = int(self.player.get('hp', 30))
        new_hp = max(0, current_hp - hp_damage)
        self.player['hp'] = new_hp
        
        # FIXED: Only trigger death if HP reaches 0 OR if explicitly marked as fatal
        is_explicitly_fatal = bool(qte_result.get('is_fatal', False))
        
        if new_hp <= 0 or is_explicitly_fatal:
            self.logger.info(f"_trigger_qte_death: Player died - {'explicit fatal flag' if is_explicitly_fatal else 'HP depleted'}")
            self._trigger_qte_death(qte_result)
            return True
        
        # Player survived
        self.logger.info(f"_handle_qte_damage_and_death: Player survived with {new_hp} HP remaining")
        return False

    def _trigger_qte_death(self, qte_result: dict) -> dict:
        # --- THE FIX: Unconditional Screen Sweepers ---
        # We MUST destroy all popups BEFORE the death guard!
        self.add_ui_event({"event_type": "destroy_qte_popup", "priority": 1000})
        self.add_ui_event({"event_type": "destroy_info_popup", "priority": 1000})

        # --- THE FIX: Prevent double-deaths ---
        if getattr(self, 'is_game_over', False):
            self.logger.info("Player is already dead. Ignoring duplicate narrative death.")
            return self._build_response(message="", turn_taken=False)

        if self._intercept_visionary_death():
            return self._build_response(message="", turn_taken=False)
        try:
            self.is_game_over = True

            death_reason = (
                qte_result.get('death_reason') or
                self.player.get('death_reason') or
                "QTE failure"
            )

            # --- THE REASON FIX ---
            if death_reason == "freak_accident" or not death_reason:
                hazard_id_for_reason = self.player.get('death_hazard_id') or qte_result.get('qte_source_hazard_id')
                if hazard_id_for_reason and getattr(self, 'hazard_engine', None):
                    # Split instance ID to get the master key
                    h_key = hazard_id_for_reason.split('#')[0]
                    h_name = self.hazard_engine.hazards_master_data.get(h_key, {}).get('name', 'a fatal hazard')
                    death_reason = f"Killed by {h_name}."
                else:
                    death_reason = "Succumbed to fatal injuries."

            self.player['death_reason'] = death_reason

            # PATCH: Track the hazard and state for narrative
            hazard_id = qte_result.get('qte_source_hazard_id')
            hazard_state = qte_result.get('hazard_state')
            if hazard_id:
                self.last_terminal_hazard_id = hazard_id
                self.player['death_hazard_id'] = hazard_id
                if hazard_state:
                    self.player['death_hazard_state'] = hazard_state

            self.logger.info(f"_trigger_qte_death: Player died - {death_reason}")

            city_name = self.player.get('current_city', 'Unknown City')
            final_narrative = self.get_death_narrative()
            if final_narrative:
                final_narrative = final_narrative.replace('{city_name}', city_name)

            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": death_reason,
                "final_narrative": final_narrative,
                "hide_stats": False,
                "priority": 10000
            })

            return self._build_response()
        except Exception as e:
            self.logger.error(f"_trigger_qte_death: Error: {e}", exc_info=True)
            self.is_game_over = True
            return self._build_response()

    def _update_health_effects(self):
        """Update UI health status effects based on current HP."""
        try:
            if 0 < self.player['hp'] <= self._low_health_threshold():
                self.add_ui_event({"event_type": "player_low_health_effect"})
            else:
                self.add_ui_event({"event_type": "player_clear_low_health_effect"})
        except Exception as e:
            self.logger.error(f"_update_health_effects: Error: {e}", exc_info=True)

    def _apply_qte_success_effects(self, effects: list):
        """Applies the success effects of a QTE."""
        if not effects:
            return

        self.logger.info(f"_apply_qte_success_effects: Processing {len(effects)} effects: {effects}")
        for effect in effects:
            effect_type = effect.get('type')
            
            if effect_type == 'unlock_room':
                room_id = effect.get('room_id')
                
                # --- THE MRI FATALITY INTERCEPTOR ---
                if room_id == "MRI Scan Room":
                    # The player successfully forced the door open, triggering the magnetic trap
                    self.logger.info("_apply_qte_success_effects: Player forced the MRI door. Triggering death_by_door_bisection.")
                    
                    # Fetch the death narrative from the JSON
                    death_data = self.resource_manager.get_data('hazards', {}).get('hazards', {}).get('mri', {}).get('death_by_door_bisection', {})
                    
                    # Fallback string just in case the JSON path is slightly off
                    death_msg = death_data.get("death_message", 
                        "The air crackles. The magnet power surges.\n*WHAM!*\n"
                        "The metal door leaps out of your grasp - driven with mad force into you from groin to face. "
                        "The cartilage in your nose explodes as you're sandwiched in the frame; held tight in the "
                        "last embrace you'll ever know...\n\n"
                        "You are more or less bisected, hotdog style, as the door, with a final, juicy *squelch* finally clicks shut."
                    )
                    
                    # Push the fatality directly to the UI event queue to kill the player
                    self.add_ui_event({
                        "event_type": "show_message", 
                        "message": death_data.get("description", "You force the door open just enough...")
                    })
                    self._trigger_death("death_by_door_bisection", death_msg)
                    return 
                # ------------------------------------

                # 1. Fire the standard room unlock (handles global string exits)
                self._unlock_room_effect(effect)
                
                # 2. THE DOOR FIX: Also unlock the physical door in the current room's exit dictionary!
                current_room = self.player.get('location')
                room_data = self.current_level_rooms_world_state.get(current_room, {})
                for direction, exit_data in room_data.get('exits', {}).items():
                    if isinstance(exit_data, dict) and exit_data.get('target') == room_id:
                        exit_data['locked'] = False
                        self.logger.info(f"Force-unlocked dict exit '{direction}' to '{room_id}'")

            # --- THE CONTAINER FIX: Missing handler added ---
            elif effect_type == 'unlock_furniture':
                room_id = effect.get('room_id')
                furniture_name = effect.get('furniture_name')
                
                if hasattr(self, '_unlock_furniture_effect'):
                    self._unlock_furniture_effect(effect)
                else:
                    # Manually remove the lock from the world state
                    room_data = self.current_level_rooms_world_state.get(room_id, {})
                    for f in room_data.get('furniture', []):
                        if isinstance(f, dict) and f.get('name') == furniture_name:
                            f['locked'] = False
                            self.logger.info(f"Force-unlocked furniture '{furniture_name}' in room '{room_id}'")
                            break
                
            elif effect_type == 'break_furniture':
                self._break_furniture_effect(effect.get('room_id'), effect.get('furniture_name'))
            elif effect_type == 'clear_hazard':
                hazard_id = effect.get('hazard_id')
                # If there's a specific clearing function, call it. For now, log it.
                self.logger.info(f"_apply_qte_success_effects: Hazard {hazard_id} cleared.")
            else:
                self.logger.warning(f"Unknown QTE success effect type: {effect_type}")

    def _unlock_furniture_effect(self, effect: dict):
        """Unlock a piece of furniture in the live world state, with robust logging and error handling."""
        try:
            room_id = effect.get('room_id') or self.player.get('location')
            fname = (effect.get('furniture_name') or "").strip()
            if not (room_id and fname):
                self.logger.warning(f"_unlock_furniture_effect: Missing room_id or furniture_name in effect: {effect}")
                return

            room_state = self.current_level_rooms_world_state.get(room_id)
            if not room_state:
                self.logger.warning(f"_unlock_furniture_effect: Room '{room_id}' not found in world state.")
                return

            furn_list = room_state.get('furniture') or []
            norm = normalize_text
            updated = False

            for f in furn_list:
                # Match by name (normalized)
                if isinstance(f, dict) and norm(f.get('name')) == norm(fname):
                    try:
                        # 1. Unlock
                        f['locked'] = False
                        f.pop('locked_by_mri', None)
                        f.pop('unlocks_with', None)
                        if 'locking' in f:
                            if isinstance(f['locking'], dict):
                                f['locking']['locked'] = False
                            else:
                                # If it was malformed or just a boolean somehow, reset it
                                f['locking'] = {'locked': False}
                        
                        # 2. PATCH: Spawn items if this was a force/break action
                        self._populate_forced_items(f)

                        updated = True
                        self.logger.info(f"_unlock_furniture_effect: FORCE UNLOCKED furniture '{fname}' in room '{room_id}'")
                        break
                    except Exception as e:
                        self.logger.error(f"_unlock_furniture_effect: Error unlocking furniture '{fname}' in room '{room_id}': {e}", exc_info=True)
                        continue

            if updated:
                # Refresh contextual actions immediately so "Search" becomes available/primary
                self.add_ui_event({"event_type": "refresh_context_actions"})
            if not updated:
                actual_names = [f.get('name') if isinstance(f, dict) else f for f in furn_list]
                self.logger.warning(f"_unlock_furniture_effect: Furniture '{fname}' not found. Available: {actual_names}")
        except Exception as e:
            self.logger.error(f"_unlock_furniture_effect: Error: {e}", exc_info=True)

    def _break_furniture_effect(self, effect: dict):
        """Mark furniture as broken and unlocked in the live world state, with robust logging and error handling."""
        try:
            room_id = effect.get('room_id') or self.player.get('location')
            fname = (effect.get('furniture_name') or "").strip()
            if not (room_id and fname):
                self.logger.warning(f"_break_furniture_effect: Missing room_id or furniture_name in effect: {effect}")
                return

            room_state = self.current_level_rooms_world_state.get(room_id)
            if not room_state:
                self.logger.warning(f"_break_furniture_effect: Room '{room_id}' not found in world state")
                return

            furn_list = room_state.get('furniture') or []
            norm = normalize_text
            updated = False
            for f in furn_list:
                if isinstance(f, dict) and norm(f.get('name')) == norm(fname):
                    try:
                        # 1. Break and Unlock
                        f['is_broken'] = True
                        f['locked'] = False
                        if 'locking' in f:
                            if isinstance(f['locking'], dict):
                                f['locking']['locked'] = False
                            else:
                                f['locking'] = {'locked': False}
                        
                        # 2. PATCH: Spawn items
                        self._populate_forced_items(f)
                        
                        updated = True
                        self.logger.info(f"_break_furniture_effect: Broke furniture '{fname}'")
                        break
                    except Exception as e:
                        self.logger.error(f"_break_furniture_effect: Error breaking furniture '{fname}' in room '{room_id}': {e}", exc_info=True)
                        continue

            if updated:
                self.add_ui_event({"event_type": "refresh_context_actions"})
            else:
                self.logger.warning(f"_break_furniture_effect: Furniture '{fname}' not found or not a dict in '{room_id}'")
        except Exception as e:
            self.logger.error(f"_break_furniture_effect: Error: {e}", exc_info=True)

    def _unlock_room_effect(self, effect: dict):
        """
        Canonically unlocks a room in the World State.
        Used by QTE successes (Force).
        Injected with robust logging and error handling.
        """
        try:
            room_id = effect.get('room_id')
            if not room_id:
                self.logger.warning(f"_unlock_room_effect: Missing room_id in effect: {effect}")
                return

            room_state = self.current_level_rooms_world_state.get(room_id)
            if not room_state:
                self.logger.warning(f"_unlock_room_effect: Room '{room_id}' not found in world state.")
                return

            try:
                room_state['locked'] = False
                room_state['locked_by_mri'] = False  # Clear MRI locks too

                if 'locking' not in room_state or not isinstance(room_state['locking'], dict):
                    room_state['locking'] = {}
                room_state['locking']['locked'] = False

                self.logger.info(f"_unlock_room_effect: Force-unlocked room '{room_id}'")
            except Exception as e:
                self.logger.error(f"_unlock_room_effect: Error unlocking room '{room_id}': {e}", exc_info=True)
                return

            self.add_ui_event({"event_type": "refresh_map"})
        except Exception as e:
            self.logger.error(f"_unlock_room_effect: Error: {e}", exc_info=True)


    def _handle_pending_move(self, qte_result: dict) -> Optional[dict]:
        """
        Execute pending move after successful QTE (e.g., force-door auto-move).
        Returns full response if move executed, None otherwise.
        """
        try:
            if not qte_result.get('success', False):
                self.logger.debug("_handle_pending_move: QTE was not successful, no move executed.")
                return None

            pending_move = qte_result.get('pending_move') or self.player.pop('pending_move', None)
            if not pending_move:
                self.logger.debug("_handle_pending_move: No pending move found in QTE result or player state.")
                return None

            self.logger.info(f"_handle_pending_move: Executing pending move '{pending_move}'")
            move_result = self._command_move(pending_move)

            result = self._build_response()
            result['messages'] = qte_result.get('messages', []) + move_result.get('messages', [])
            result['ui_events'] = self.get_ui_events()
            result['game_state'] = self.get_current_game_state()

            return result

        except Exception as e:
            self.logger.error(f"_handle_pending_move: Error: {e}", exc_info=True)
            return None

    def _handle_hazard_state_transition(self, qte_result: dict) -> Optional[dict]:
        """
        Handle hazard state change after QTE resolution.
        Returns response if terminal state reached, None otherwise.
        Injected with robust logging and error handling.
        """
        try:
            self.logger.debug(f"_handle_hazard_state_transition: qte_result={qte_result}")
            hazard_id = qte_result.get('qte_source_hazard_id')
            if not hazard_id or not self.hazard_engine:
                self.logger.warning("_handle_hazard_state_transition: No hazard_id or hazard_engine.")
                return None

            next_state = (
                (qte_result.get('next_state_success') or qte_result.get('next_state_after_qte_success')) if qte_result.get('success')
                else (qte_result.get('next_state_failure') or qte_result.get('next_state_after_qte_failure'))
            )

            if not next_state:
                self.logger.info("_handle_hazard_state_transition: No next_state found in qte_result.")
                return None

            self.logger.info(f"_handle_hazard_state_transition: hazard_id={hazard_id}, next_state={next_state}")

            # Check if next state is terminal
            terminal_response = self._handle_terminal_hazard_state(
                hazard_id, next_state, qte_result
            )
            if terminal_response:
                self.logger.info("_handle_hazard_state_transition: Terminal state reached, returning terminal_response.")
                return terminal_response

            # Non-terminal state: defer transition to popup dismiss
            self.logger.debug("_handle_hazard_state_transition: Deferring hazard transition to popup dismiss.")
            return self._defer_hazard_transition(hazard_id, next_state, qte_result)

        except Exception as e:
            self.logger.error(f"_handle_hazard_state_transition: Error: {e}", exc_info=True)
            return None

    def _handle_terminal_hazard_state(
        self, hazard_id: str, next_state: str, qte_result: dict
    ) -> Optional[dict]:
        """
        Handle terminal hazard state (death or level complete).
        Ensures Setup (Description) -> Punchline (Death Message) ordering.
        """
        try:
            self.logger.debug(f"_handle_terminal_hazard_state: hazard_id={hazard_id}, next_state={next_state}")
            hazards_master = self.resource_manager.get_data('hazards', {})
            h_def = hazards_master.get(hazard_id, {})
            sdef = h_def.get('states', {}).get(next_state, {})

            # Track the primary popup so we can chain subsequent events to it
            primary_popup_consequence = None

            # 1. Build the Setup Popup (Description/QTE)
            setup_text = sdef.get('description')
            popup_title = "Notice"
            popup_msg = setup_text or qte_result.get('message', '')

            if popup_msg:
                primary_popup_consequence = {
                    "event_type": "show_popup",
                    "title": popup_title,
                    "message": popup_msg,
                    "output_panel": True
                }

            # 2. Determine Death Logic
            is_player_death = sdef.get('instant_death_in_room', False)
            if "Corbin" in sdef.get('death_message', ""):
                is_player_death = False

            # 3. Handle Player Death (Game Over)
            if is_player_death:
                death_msg = sdef.get('death_message') or sdef.get('description') or "You died."
                self.is_game_over = True
                self.player['death_reason'] = death_msg
                self.player['death_hazard_id'] = hazard_id
                self.player['death_hazard_state'] = next_state

                self.logger.info(f"Hazard '{hazard_id}' state '{next_state}' is fatal. Triggering Game Over.")
                game_over_event = {
                    "event_type": "game_over",
                    "death_reason": death_msg,
                    "final_narrative": self.get_death_narrative(),
                    "player_state": self.player.copy(),
                }

                if primary_popup_consequence:
                    primary_popup_consequence.setdefault("on_close_emit_ui_events", []).append(game_over_event)
                else:
                    self.add_ui_event(game_over_event)

            # 4. Handle NPC Death (Popup + Fear)
            elif sdef.get('death_message'):
                self.logger.info(f"Hazard '{hazard_id}' triggered non-player death event.")
                death_popup = {
                    "event_type": "show_popup",
                    "title": "Fatal Event",
                    "message": sdef.get('death_message'),
                    "output_panel": True,
                    "vfx_hint": "damage"
                }

                if primary_popup_consequence:
                    primary_popup_consequence.setdefault("on_close_emit_ui_events", []).append(death_popup)
                else:
                    self.add_ui_event(death_popup)

                self.add_ui_event({
                    "type": "update_fear",
                    "amount": 0.25,
                    "reason": "witness_npc_death"
                })

            # Emit the first link in the chain (Setup Popup)
            if primary_popup_consequence:
                self.add_ui_event(primary_popup_consequence)

            return self._build_response()

        except Exception as e:
            self.logger.error(f"_handle_terminal_hazard_state: Error: {e}", exc_info=True)
            return None

    def _defer_hazard_transition(
        self, hazard_id: str, next_state: str, qte_result: dict
    ) -> dict:
        """Defer hazard state change to popup dismiss for non-terminal states.
        Injected with robust logging and error handling.
        """
        try:
            self.logger.debug(f"_defer_hazard_transition: hazard_id={hazard_id}, next_state={next_state}, qte_result={qte_result}")
            self.add_ui_event({
                "event_type": "show_popup",
                "priority": 50,
                "title": "QTE Result",
                "message": qte_result.get('message', ''),
                "on_close_set_hazard_state": {
                    "hazard_id": hazard_id,
                    "target_state": next_state
                }
            })

            # Handle pending move
            pending_move = self.player.pop('pending_move', None)
            if pending_move:
                self.logger.info(f"_defer_hazard_transition: Executing pending move '{pending_move}' after non-terminal state.")
                return self._execute_move_with_health_check(qte_result, pending_move)

            self._update_health_effects()
            self.logger.debug("_defer_hazard_transition: Returning build_response after deferring hazard transition.")
            return self._build_response()

        except Exception as e:
            self.logger.error(f"_defer_hazard_transition: Error: {e}", exc_info=True)
            return self._build_response()

    def _execute_move_with_health_check(self, qte_result: dict, direction: str) -> dict:
        """Execute move and merge with QTE result, updating health effects."""
        try:
            self.logger.info(f"_execute_move_with_health_check: Moving '{direction}' after QTE")
            move_result = self._command_move(direction)
            
            self._update_health_effects()
            
            result = self._build_response()
            result['messages'] = qte_result.get('messages', []) + move_result.get('messages', [])
            result['ui_events'] = self.get_ui_events()
            result['game_state'] = self.get_current_game_state()
            
            return result

        except Exception as e:
            self.logger.error(f"_execute_move_with_health_check: Error: {e}", exc_info=True)
            return self._build_response()

    def _build_qte_result_popup(self, qte_result: dict) -> dict:
        """
        Build the QTE result popup event.
        PATCH: Check if transition already occurred to prevent double-triggering.
        PATCH: Skip popup if next state is immediate QTE chain to improve flow.
        """
        hazard_id = qte_result.get('qte_source_hazard_id')
        next_state = (
            (qte_result.get('next_state_success') or qte_result.get('next_state_after_qte_success')) if qte_result.get('success')
            else (qte_result.get('next_state_failure') or qte_result.get('next_state_after_qte_failure'))
        )
        
        # Flags to determine behavior
        should_defer_transition = True
        skip_popup_entirely = False
        
        if hazard_id and next_state and self.hazard_engine:
            hazard = self.hazard_engine.active_hazards.get(hazard_id)
            if hazard:
                # 1. CRITICAL FIX: Check if transition ALREADY happened
                # If _handle_qte_resolution already set the state, we MUST NOT defer it again.
                if hazard.get('state') == next_state:
                    self.logger.info(f"_build_qte_result_popup: Hazard '{hazard_id}' already in state '{next_state}'. Skipping deferred transition.")
                    should_defer_transition = False
                    
                    # 2. Check for QTE Chain
                    # If the state we just entered triggers another QTE immediately, 
                    # suppress the "Success" popup so the new QTE starts instantly.
                    master = hazard.get('master_data', {})
                    sdef = master.get('states', {}).get(next_state, {})
                    if sdef.get('triggers_qte_on_entry'):
                        self.logger.info(f"_build_qte_result_popup: Next state '{next_state}' triggers QTE. Skipping result popup to maintain flow.")
                        skip_popup_entirely = True

        # If we are chaining QTEs, exit early without showing the popup
        if skip_popup_entirely:
            self._update_health_effects()
            return self._build_response()

        # Otherwise, build the popup
        popup_payload = {
            "event_type": "show_popup",
            "priority": 50,
            "title": "QTE Result",
            "message": qte_result.get('message', '')
        }

        # Only attach the deferred state change if it hasn't happened yet
        if should_defer_transition and hazard_id and next_state:
            popup_payload["on_close_set_hazard_state"] = {
                "hazard_id": hazard_id,
                "target_state": next_state
            }

        self.add_ui_event(popup_payload)
        self._update_health_effects()
        return self._build_response()

    def _low_health_threshold(self) -> int:
        """Low-HP cutoff used for UI pulsing. Default: max(5 HP, 15% of max)."""
        try:
            max_hp = int(self.player.get('max_hp', 30))
            return max(5, int(max_hp * 0.15))
        except Exception:
            return 5

    def add_ui_event(self, event: dict):
        """Adds a UI event to the queue for the GameScreen to process."""
        self.ui_events.append(event)
        self.logger.debug(f"UI Event Added: {event}")

    def get_ui_events(self) -> list:
        """Returns all pending UI events and clears the queue."""
        if not self.ui_events:
            return []
        # Return a copy and clear the original list
        events_to_process = self.ui_events[:]
        self.ui_events.clear()
        return events_to_process

    def process_player_input(self, raw_input: Union[str, dict]) -> dict:
        """
        The main input entry point.
        PATCHED: Centralizes Elevator Transit intercept to ensure it works for ALL inputs.
        PHASE 6: Refined ui_events accumulation to prevent duplicates and ensure all queued events are drained.
        """
        self.logger.debug(f"process_player_input called with raw_input='{raw_input}' (type: {type(raw_input)})")
        # 1) Handle structured QTE events (dict) FIRST
        if isinstance(raw_input, dict):
            if self.qte_engine:
                # Snapshot: was a QTE active when we received this event?
                had_active_qte = bool(self.qte_engine.active_qte)
                result = self.qte_engine.handle_qte_input(raw_input) if had_active_qte else None

                if isinstance(result, dict):
                    # QTE resolved — active_qte is now None (cleared by resolve_qte)
                    self.logger.debug("process_player_input: QTE resolved via dict event; delegating to _handle_qte_resolution")
                    qte_response = self._handle_qte_resolution(result)

                    # --- THE FIX 1 ---
                    self._sync_companions_to_player()
                    # -----------------

                    # Drain queue after QTE resolution
                    final_ui_events = qte_response.get('ui_events', []) + self.get_ui_events()
                    qte_response['ui_events'] = final_ui_events
                    return qte_response

                if had_active_qte:
                    # QTE still active (e.g. mash count not yet met), return pending events
                    return self._build_response(ui_events=self.get_ui_events())

                self.logger.debug("process_player_input: Dict input received but no active QTE. Ignoring safely.")
                return self._build_response(ui_events=self.get_ui_events())

        # 2) Guard: if game over, bail out
        if self.is_game_over:
            return {
                "messages": ["The game is over."],
                "game_state": self.get_current_game_state(),
                "ui_events": self.get_ui_events()
            }

        # 3) Handle text input during QTE
        if self.player.get('qte_active', False) and isinstance(raw_input, str):
            if self.qte_engine and self.qte_engine.active_qte:
                result = self.qte_engine.handle_qte_input(raw_input)
                if result:  # QTE resolved
                    qte_response = self._handle_qte_resolution(result)
                    # Drain queue after QTE resolution
                    final_ui_events = qte_response.get('ui_events', []) + self.get_ui_events()
                    qte_response['ui_events'] = final_ui_events
                    return qte_response
                # QTE still in progress
                return self._build_response(ui_events=self.get_ui_events())

        # 4) Normal Command Processing
        verb, target = self._parse_command(raw_input)
        
        # Process Hazards/Interactions
        interaction_response = {}
        if self.hazard_engine:
            interaction_response = self.hazard_engine.process_player_interaction(verb, target)
            for consequence in interaction_response.get('consequences', []):
                self.handle_hazard_consequence(consequence)

        if interaction_response.get('blocks_action'):
            response = self._build_response(
                messages=interaction_response.get('messages', []),
                turn_taken=True
            )
        else:
            command_method = self.command_map.get(verb)
            if not command_method:
                response = self._build_response(message="You're not sure how to do that.", turn_taken=False)
            else:
                response = command_method(target)
                response = self._merge_responses(response, interaction_response)

        # 5) Process turn end if action was taken
        if response.get('turn_taken', False) and not self.is_game_over:
            end_of_turn_response = self._process_turn_end(verb, target, response.get('success', True))
            response = self._merge_responses(response, end_of_turn_response)
        self._sync_companions_to_player()
        # -----------------

        # --- PHASE 6: REFINED UI_EVENTS ACCUMULATION ---
        action_ui_events = response.get("ui_events", [])
        queued_ui_events = self.get_ui_events()
        
        # Merge both sources
        combined_ui_events = action_ui_events + queued_ui_events
        
        # Build the initial response dict
        result = {
            "messages": response.get("messages", []),
            "game_state": self.get_current_game_state(),
            "ui_events": combined_ui_events,
        }
        
        # 6) Check for game state transitions (level complete, game won, etc.)
        self.check_game_state_transitions()
        
        # 7) Drain the queue one final time to catch any events added by check_game_state_transitions()
        final_queued_events = self.get_ui_events()
        result['ui_events'].extend(final_queued_events)
        
        self.logger.debug(f"process_player_input: Final ui_events count: {len(result['ui_events'])}")
        return result

    def _process_level_events(self, event_key: str):
        """
        Process on_enter_level_events or on_exit_level_events from level_requirements.
        event_key: 'on_enter_level_events' or 'on_exit_level_events'
        """
        level_reqs = self.resource_manager.get_data('level_requirements', {})
        current_level = str(self.player.get('current_level', 1))
        reqs = level_reqs.get(current_level, {})
        events = reqs.get(event_key, [])
        
        if not events:
            return
        
        self.logger.info(f"Processing {len(events)} {event_key} for level {current_level}")
        
        for event in events:
            etype = event.get('type')
            
            if etype == 'show_popup':
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": event.get('title', 'Notice'),
                    "message": event.get('message', '')
                })
            elif etype == 'play_music':
                if self.audio_manager:
                    self.audio_manager.play_music(event.get('track'))
            elif etype == 'play_sfx':
                if self.audio_manager:
                    self.audio_manager.play_sfx(event.get('sfx'))
            elif etype == 'set_flag':
                self.set_interaction_flag(event.get('flag'))
            elif etype == 'unlock_achievement':
                if self.achievements_system:
                    self.achievements_system.unlock(event.get('achievement'))
            elif etype == 'spawn_hazard':
                if self.hazard_engine:
                    self.hazard_engine._add_active_hazard(
                        hazard_type=event.get('hazard_type'),
                        location=event.get('location', self.player.get('location')),
                        source_trigger_id="level_event"
                    )
            elif etype == 'give_item':
                item_id = event.get('item_id')
                if item_id:
                    self.player.setdefault('inventory', []).append(item_id)
            else:
                self.logger.warning(f"Unknown level event type: {etype}")

    def trigger_player_death(self, cause: str = "Unknown Causes", message: str = "You have succumbed to the horrors."):
        if hasattr(self, '_intercept_visionary_death') and self._intercept_visionary_death():
            return
            
        self.logger.info(f"Player died. Cause: {cause}")
        self.player['hp'] = 0
        
        # 1. Generate the fully cohesive Game Over narrative
        ending_text = self._build_death_epilogue(message)
        
        # 2. Fire the UI event to transition to the Lose Screen
        self.add_ui_event({
            "event_type": "switch_screen",
            "screen_name": "lose",
            "cause_of_death": cause,
            "death_message": message,  # UI can use this for the sub-header
            "ending_text": ending_text # This contains the entire Cascade -> Death -> Aftermath story
        })

    def handle_hazard_consequence(self, consequence: dict, depth: int = 0):
        """Processes consequences emitted by the HazardEngine."""
        if depth > 10:
            self.logger.warning("handle_hazard_consequence: max recursion depth hit, aborting chain")
            return
        ctype = consequence.get('type') or consequence.get('event_type')
        if not ctype:
            return

        handler_map = {
            'log_message':           self._handle_conseq_log_message,
            'damage':                self._handle_conseq_damage,
            'set_hp_to_1':           self._handle_conseq_set_hp_to_1,
            'show_popup':            self._handle_conseq_show_popup,
            'screen_flash':          self._handle_conseq_screen_flash,
            'update_fear':           self._handle_conseq_update_fear,
            'start_qte':             self._handle_conseq_start_qte,
            'trigger_qte':           self._handle_conseq_start_qte,  
            'force_move':            self._handle_conseq_force_move,
            'move_player_to_room':   self._handle_conseq_move_player_to_room,
            'hazard_state_change':   self._handle_conseq_hazard_state_change,
            'game_over':             self._handle_conseq_game_over,
            'npc_killed_by_hazard':  self._handle_conseq_npc_killed_by_hazard,
            'play_sfx':              self._handle_conseq_play_sfx,
        }

        handler = handler_map.get(ctype)
        if handler:
            handler(consequence, depth)
        else:
            self.logger.warning(f"handle_hazard_consequence: unhandled type '{ctype}'")
        # --------------------------------------------------

    # -------------------------------------------------------------------------
    # --- Consequence Handlers ---
    # -------------------------------------------------------------------------

    def _handle_conseq_show_popup(self, consequence: dict, depth: int):
        event_data = consequence.copy()
        event_data["event_type"] = "show_popup"
        self.add_ui_event(event_data)

    def _handle_conseq_screen_flash(self, consequence: dict, depth: int):
        self.add_ui_event({
            "event_type": "screen_flash",
            "color": consequence.get("color", "ffffff"),
            "duration": consequence.get("duration", 0.5),
            "opacity": consequence.get("opacity", 0.3)
        })

    def _handle_conseq_log_message(self, consequence: dict, depth: int):
        self.add_ui_event({"event_type": "show_message", "message": consequence.get("message")})

    def _handle_conseq_update_fear(self, consequence: dict, depth: int):
        amount = consequence.get("amount", 0.0)
        if self.death_ai:
            self.death_ai.update_fear(custom_amount=amount)
            self.add_ui_event({
                "event_type": "player_fear_effect_update",
                "fear": self.player.get('fear', 0.0)
            })

    def _handle_conseq_damage(self, consequence, depth):
        amount = consequence.get('amount', 0)
        if amount <= 0: 
            return

        # Apply damage safely
        current_hp = self.player.get('hp', self.player.get('max_hp', 30))
        self.player['hp'] = max(0, current_hp - amount)
        
        # Standard visual feedback
        self.add_ui_event({"event_type": "screen_shake", "intensity": min(20, amount * 2)})
        self.add_ui_event({"event_type": "screen_flash", "color": "ff0000", "duration": 0.3, "opacity": 0.4})
        self.add_ui_event({"event_type": "refresh_ui"})

        # --- THE FIX: Unconditional Screen Sweepers ---
        if self.player['hp'] <= 0:
            # We MUST destroy all popups BEFORE the death guard, or the screen will soft-lock!
            self.add_ui_event({"event_type": "destroy_qte_popup", "priority": 1000})
            self.add_ui_event({"event_type": "destroy_info_popup", "priority": 1000})
            
            if getattr(self, 'is_game_over', False):
                return  
                
            self.is_game_over = True
            self.logger.info("Player HP reached 0. Triggering immediate Game Over.")
            
            source = consequence.get('source', '').lower()
            narrative = consequence.get('message', 'You succumbed to your injuries.')
            n_lower = narrative.lower()

            reason = "Fatal Injury"
            if "bleed" in source or "bleed" in n_lower or "blood" in n_lower:
                reason = "Bled to Death"
            elif "smoke" in source or "suffocat" in n_lower or "asphyx" in n_lower:
                reason = "Asphyxiation"
            elif "fire" in source or "burn" in n_lower:
                reason = "Burned Alive"
            elif "crush" in n_lower or "shatter" in n_lower or "slam" in n_lower:
                reason = "Blunt Force Trauma"
            else:
                reason = "Killed by failing to react."
            
            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": reason,
                "final_narrative": f"The damage was too much. You collapse to the floor, gasping your final breath.\n\n[color=ff0000]{narrative}[/color]",
                "hide_stats": False,
                "priority": 10000
            })

    def _handle_conseq_set_hp_to_1(self, consequence: dict, depth: int):
        self.player['hp'] = 1
        self.add_ui_event({"event_type": "show_message",
                        "message": "[color=ff0000]You barely survived that... you are clinging to life.[/color]"})
        self.add_ui_event({"event_type": "player_damage_effect"})
        self.add_ui_event({"event_type": "player_low_health_effect"})

    def _handle_conseq_start_qte(self, consequence: dict, depth: int):
        if not self.qte_engine:
            return
        # If a QTE is already running, or there are unread popups in the queue, enqueue
        if self.player.get('qte_active') or self._has_pending_popup_in_queue():
            self._enqueue_qte(consequence)
            return
        try:
            qte_ctx = consequence.get("qte_context", {})
            hid = consequence.get("hazard_id")
            if hid and "qte_source_hazard_id" not in qte_ctx:
                qte_ctx["qte_source_hazard_id"] = hid
                
            self.qte_engine.start_qte(consequence.get("qte_type"), qte_ctx)
        except Exception as e:
            self.logger.error(f"Failed to start QTE: {e}", exc_info=True)

    def _has_pending_popup_in_queue(self) -> bool:
        """Returns True if there's a show_popup event already queued that hasn't been shown yet."""
        for event in self.ui_events:
            if event.get('event_type') == 'show_popup':
                return True
        return False

    def _handle_conseq_force_move(self, consequence: dict, depth: int):
        location = consequence.get("location")
        if location:
            response = self._finalize_move(self.player['location'], location)
            for m in response.get('messages', []):
                self.add_ui_event({"event_type": "show_message", "message": m})
            for ev in response.get('ui_events', []):
                self.add_ui_event(ev)
            self.add_ui_event({"event_type": "refresh_map"})

    # --- THE FIX: Add the missing JSON handler! ---
    def _handle_conseq_move_player_to_room(self, consequence: dict, depth: int):
        """Aliases the JSON consequence 'move_player_to_room' to the 'force_move' logic."""
        # Translate 'target_room' to 'location' so the engine understands it
        consequence['location'] = consequence.get('target_room')
        self._handle_conseq_force_move(consequence, depth)

    def _handle_conseq_hazard_state_change(self, consequence: dict, depth: int):
        hazard_id = consequence.get("hazard_id")
        target_state = consequence.get("target_state")
        if hazard_id and target_state:
            result = self.hazard_engine.set_hazard_state(hazard_id, target_state)
            for sub_consequence in result.get("consequences", []):
                self.handle_hazard_consequence(sub_consequence, depth + 1)
                if self.is_game_over: return

    def _handle_conseq_game_over(self, consequence: dict, depth: int):
        self.is_game_over = True
        self.player['death_reason'] = consequence.get("death_reason")
        self.add_ui_event({
            "event_type": "game_over",
            "death_reason": self.player['death_reason'],
            "final_narrative": self.get_death_narrative(),
            "player_state": self.player.copy(),
        })

    def _handle_conseq_npc_killed_by_hazard(self, consequence: dict, depth: int):
        npc_name = consequence.get("npc_name")
        if not npc_name: return

        # Mark dead & remove companion status from the GROUP
        self.player.setdefault("npc_status", {})[npc_name.lower()] = "dead"
        
        # --- NEW GROUP LOGIC ---
        companions = self.player.get('companions', [])
        if npc_name.lower() in [c.lower() for c in companions]:
            self.player['companions'] = [c for c in companions if c.lower() != npc_name.lower()]

            # 3. Synchronize Death's List Index for the Roster
            deaths_list = self.player.get('deaths_list', [])
            current_idx = self.player.get('deaths_list_index', 0)
            
            # Look ahead in the list starting from our current position
            for i in range(current_idx, len(deaths_list)):
                list_name = deaths_list[i].lower()
                # Find the first person who ISN'T dead
                if self.player.get('npc_status', {}).get(list_name, 'alive') != 'dead':
                    self.player['deaths_list_index'] = i
                    self.logger.info(f"Death's List index advanced to {i} ({list_name}).")
                    break
            else:
                # If the loop finishes without breaking, everyone ahead of us is dead.
                self.player['deaths_list_index'] = len(deaths_list)

        # Scrub from rooms
        for room_data in self.current_level_rooms_world_state.values():
            room_data["npcs"] = [
                n for n in room_data.get("npcs", [])
                if (n.get("name", n) if isinstance(n, dict) else n) != npc_name
            ]

        if self.death_ai: self.death_ai.update_fear(custom_amount=0.25)

        self.add_ui_event({
            "event_type": "show_popup",
            "title": "Death collects.",
            "message": consequence.get("popup_text", f"{npc_name} is gone."),
            "vfx_hint": "damage",
        })

    def _handle_conseq_play_sfx(self, consequence: dict, depth: int):
        sfx_key = consequence.get("sfx_key")
        if self.audio_manager and sfx_key:
            self.audio_manager.play_sfx(sfx_key)

    def _process_turn_end(self, verb: str, target: str, success: bool) -> dict:
        """Handles all events that happen after a player's action. Injected with robust debugging logic."""
        self.logger.debug(f"_process_turn_end called with verb='{verb}', target='{target}', success={success}")
        self._update_act_state()
        self.player['turns_left'] -= 1
        self.player['actions_taken'] += 1
        # --- FIX: Defensively handle JSON-deserialized sets ---
        current_level = self.player.get('current_level')
        if current_level:
            visited = self.player.get('visited_levels', set())
            
            # If the save file loaded this as a string, clean it up
            if isinstance(visited, str):
                import ast
                try:
                    visited = set(ast.literal_eval(visited))
                except Exception:
                    # Fallback if it's just a raw string like "level_0"
                    visited = {visited}
            # If the save file loaded this as a JSON array/list
            elif isinstance(visited, list):
                visited = set(visited)
            elif not isinstance(visited, set):
                visited = set()
                
            visited.add(current_level)
            self.player['visited_levels'] = visited
        # ------------------------------------------------------
        self.logger.debug(f"_process_turn_end: Player turns_left={self.player['turns_left']}, actions_taken={self.player['actions_taken']}")

        messages = []

        # ── NPC Targeting Tick ──────────────────────────────────────────
        if self.hazard_engine:
            npc_target_consequences = self.hazard_engine.tick_npc_targeting()
            for cons in npc_target_consequences:
                self.handle_hazard_consequence(cons)
        # ────────────────────────────────────────────────────────────────

        if self.hazard_engine:
            self.logger.debug("_process_turn_end: HazardEngine processing turn")
            hazard_response = self.hazard_engine.process_turn()
            self.logger.debug(f"_process_turn_end: HazardEngine response: {hazard_response}")
            messages.extend(hazard_response.get('messages', []))
            
            # --- Process Hazard Consequences (QTEs) ---
            processed_this_turn = self.player.get('_qte_processed_hazards_this_turn', set())
            for cons in hazard_response.get('consequences', []):
                hazard_id = cons.get('hazard_id')
                if hazard_id and hazard_id in processed_this_turn:
                    self.logger.debug(f"_process_turn_end: Skipping already-processed hazard '{hazard_id}' to prevent double-consequence")
                    continue
                self.handle_hazard_consequence(cons)
            
            # Clear the processed set at end of turn
            self.player['_qte_processed_hazards_this_turn'] = set()

        if self.death_ai:
            self.logger.debug("_process_turn_end: DeathAI analyzing player action")
            self.death_ai.analyze_player_action(verb, target, self.player['location'], success)
            self.death_ai.decay_fear()
            hallucination = self.death_ai.get_fear_hallucination()
            if hallucination:
                self.logger.info(f"_process_turn_end: Level {self.player.get('current_level', 1)} hallucination triggered: {hallucination}")
                messages.append(color_text(hallucination, 'special', self.resource_manager))

        # --- Add Ambient Barks ---
        ambient_msgs = self._process_ambient_barks()
        messages.extend(ambient_msgs)
        # --------------------------------

        # Add Death's Breath manifestation when fear is very high
        if self.death_ai and self.player.get('fear', 0) > 0.75:
            if random.random() < 0.3:  # 30% chance when fear is very high
                current_room = self.player.get('location')
                self.death_ai.manifest_deaths_presence(current_room)

        # ── Status Effects Tick ────────────────────────────────────────────
        status_effects = self.player.get('status_effects', {})

        if isinstance(status_effects, dict) and status_effects.get('bleeding', 0) > 0:
            bleed_turns = status_effects['bleeding']
            bleed_dmg = 2  # HP per turn while bleeding
            
            self.player['hp'] = max(0, self.player['hp'] - bleed_dmg)
            status_effects['bleeding'] = bleed_turns - 1
            
            if status_effects['bleeding'] <= 0:
                del status_effects['bleeding']
                messages.append(
                    color_text("The bleeding has stopped, but you're badly weakened.", 'warning', self.resource_manager)
                )
            else:
                messages.append(
                    color_text(
                        f"[color=aa0000]You're still bleeding. (-{bleed_dmg} HP) "
                        f"[{status_effects['bleeding']} turns remaining][/color]",
                        'warning', self.resource_manager
                    )
                )
            
            if not self.player.get('death_reason') and self.player['hp'] <= 0:
                self.player['death_reason'] = 'blood_loss'
        # ───────────────────────────────────────────────────────────────────

        # --- CANONICAL GAME OVER HANDLING ---
        if self.player['hp'] <= 0:
            # Safety Check: Prevent double-deaths
            if getattr(self, 'is_game_over', False):
                return self._build_response(messages=messages)
                
            self.is_game_over = True
            
            # Safety Check: Destroy open UI popups to prevent soft-locks!
            self.add_ui_event({"event_type": "destroy_qte_popup", "priority": 1000})
            self.add_ui_event({"event_type": "destroy_info_popup", "priority": 1000})

            cause = self.player.get('death_reason', '')
            
            if cause == 'blood_loss':
                payload = self._build_blood_loss_death_narrative()
            else:
                if not self.player.get('death_reason'):
                    self.player['death_reason'] = "Your injuries are too severe. You succumb to the darkness."
                payload = {
                    "event_type": "game_over",
                    "death_reason": self.player['death_reason'],
                    "final_narrative": self.get_death_narrative(),
                    "flavor_text": '',
                    "player_state": self.player.copy(),
                }
            
            # Ensure high priority so it executes immediately
            if "priority" not in payload:
                payload["priority"] = 10000
                
            self.logger.info("_process_turn_end: Player HP <= 0, game over")
            self.add_ui_event(payload)

        elif self.player['turns_left'] <= 0:
            if getattr(self, 'is_game_over', False):
                return self._build_response(messages=messages)
                
            self.is_game_over = True
            
            self.add_ui_event({"event_type": "destroy_qte_popup", "priority": 1000})
            self.add_ui_event({"event_type": "destroy_info_popup", "priority": 1000})

            if not self.player.get('death_reason'):
                self.player['death_reason'] = "You've run out of time. You feel a cold presence behind you..."
                
            self.logger.info("_process_turn_end: Player ran out of turns, game over")
            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": self.player['death_reason'],
                "final_narrative": self.get_death_narrative(),
                "player_state": self.player.copy(),
                "priority": 10000
            })

        self.logger.debug(f"_process_turn_end: Returning messages: {messages}, is_game_over={self.is_game_over}")
        return self._build_response(messages=messages)

    def _process_ambient_barks(self) -> list:
        """
        Polls active hazards and NPCs for ambient flavor text.
        Returns a list of messages.
        """
        messages = []
        
        # 1. Hazard Barks
        if self.hazard_engine:
            current_room = self.player.get('location')
            active_hazards = self.hazard_engine.get_room_hazards_descriptions(current_room)
            
            for hid, h_inst in active_hazards.items():
                # 15% chance per hazard per turn
                if random.random() < 0.15:
                    master = h_inst.get('master_data', {})
                    barks = master.get('ambient_messages', [])
                    if barks:
                        bark = random.choice(barks)
                        messages.append(color_text(f"[i]{bark}[/i]", 'warning', self.resource_manager))

        # 2. NPC Barks (Companion)
        # If fear is high, companion might whimper or complain
        companion = self._get_companion_npc()
        if companion and self.player.get('companion_location') == self.player.get('location'):
            # Only if alive
            if self.player.get('companion_status') != 'deceased':
                if random.random() < 0.1:
                    fear = self.player.get('fear', 0)
                    if fear > 0.7:
                        msg = "Your friend whispers, 'I have a really bad feeling about this...'"
                    else:
                        msg = "Your friend shifts nervously."
                    messages.append(color_text(msg, 'npc', self.resource_manager))

        return messages

    # --- NEW: Response Formatting ---

    def _build_response(self, message: Optional[str] = None, turn_taken: bool = False,
                        success: Optional[bool] = None, messages: Optional[list] = None,
                        ui_events: Optional[list] = None, game_state: Optional[dict] = None, **extras) -> dict:
        """
        A helper to construct the standard response dictionary, now with UI events and map refresh.
        Injected with robust debugging logic.
        """
        self.logger.debug(f"_build_response called with message='{message}', turn_taken={turn_taken}, success={success}, messages={messages}, ui_events={ui_events}")
        # Compose messages list
        response_messages = list(messages or [])
        if message:
            if isinstance(message, str):
                response_messages.insert(0, message)
            elif isinstance(message, list):
                response_messages = message + response_messages
        # Compose UI events and ensure map refresh on turn
        ui_events = list(ui_events or [])
        if turn_taken:
            ui_events.append({"event_type": "refresh_map"})
        # Build response dict
        response = {
            "messages": response_messages,
            "turn_taken": turn_taken,
            "success": success if success is not None else True,
            "ui_events": ui_events
        }
        # Optionally include game_state
        if game_state:
            response["game_state"] = game_state
        else:
            response["game_state"] = self.get_current_game_state()
        # Merge any extras
        response.update(extras or {})
        self.logger.debug(f"_build_response returning: {response}")
        return response

    def _merge_responses(self, r1: dict, r2: dict) -> dict:
        """Merges two response dictionaries, now including ui_events."""
        merged = r1.copy()
        merged['messages'] = r1.get('messages', []) + r2.get('messages', [])
        
        # --- NEW: Also merge the ui_events list ---
        merged['ui_events'] = r1.get('ui_events', []) + r2.get('ui_events', [])
        
        for key in ['is_game_over', 'game_won']:
            if key in r2:
                merged[key] = r2[key]
        return merged

    def _player_can_see_omens(self) -> bool:
        """
        Checks if the current player character has the ability to see omens.
        Medium and You: always see omens.
        All others: chance based on perception stat.
        """
        char_class = self.player.get('character_class')
        if char_class in ("Medium", "Visionary"):
            self.logger.debug(f"_player_can_see_omens: {char_class} always sees omens.")
            return True
        else:
            # Use perception stat as percent chance (e.g., 3 = 60%, 5 = 95% max)
            perception = self._get_stat('perception', 1)
            chance = min(perception * 0.2, 0.95)  # e.g., 3 = 60%, 5 = 95% max
            roll = random.random()
            can_see = roll < chance
            self.logger.debug(f"_player_can_see_omens: {char_class} perception={perception}, roll={roll:.2f}, chance={chance:.2f}, can_see={can_see}")
            return can_see

    def _make_first_entry_popup_event(self, room_id: str, text: str) -> dict:
        """Builds the UI event dictionary for a first-entry popup."""
        # PATCH: Always return a dict (even if text is empty, for consistency)
        if text:
            return {
                "event_type": "show_popup",
                "title": room_id.replace("_", " ").title(),
                "message": text
            }
        else:
            # Return empty dict instead of None to avoid list comprehension issues
            return {}

    def get_current_game_state(self) -> dict:
        """Returns a snapshot of the current game state for the UI. Injected with robust debugging logic."""
        player_location = self.player.get('location')
        room_desc = self.current_level_rooms_world_state.get(player_location, {}).get('description')
        state = {
            "player": self.player,
            "current_room_description": room_desc,
            "is_game_over": self.is_game_over,
            "game_won": self.game_won
        }
        self.logger.debug(f"get_current_game_state returning: {state}")
        return state

    def get_initial_ui_state(self) -> dict:
        """Returns the initial UI state. Injected with robust debugging logic."""
        state = self.get_current_game_state()
        self.logger.debug(f"get_initial_ui_state returning: {state}")
        return state

    def _build_room_coordinate_map(self, start_room_id: str):
        """
        Dynamically builds a 3D coordinate map (x, y, z) using Breadth-First Search.
        Handles Cardinals, Ordinals (Diagonals), and Verticality.
        """
        self.logger.info("Building 3D room coordinate map...")
        self.current_level_coord_map = {}
        
        # Queue stores: (room_id, x, y, z)
        q = [(start_room_id, 0, 0, 0)] 
        visited = {start_room_id}

        # Direction Vectors
        vectors = {
            # Cardinals
            'north': (0, 1, 0), 'n': (0, 1, 0),
            'south': (0, -1, 0), 's': (0, -1, 0),
            'east': (1, 0, 0), 'e': (1, 0, 0),
            'west': (-1, 0, 0), 'w': (-1, 0, 0),
            
            # Ordinals (Diagonals)
            'northeast': (1, 1, 0), 'ne': (1, 1, 0),
            'northwest': (-1, 1, 0), 'nw': (-1, 1, 0),
            'southeast': (1, -1, 0), 'se': (1, -1, 0),
            'southwest': (-1, -1, 0), 'sw': (-1, -1, 0),
            
            # Verticality
            'up': (0, 0, 1), 'upstairs': (0, 0, 1), 'climb': (0, 0, 1),
            'down': (0, 0, -1), 'downstairs': (0, 0, -1), 'descend': (0, 0, -1)
        }

        while q:
            room_id, x, y, z = q.pop(0)
            self.current_level_coord_map[room_id] = (x, y, z)
            
            room_data = self.get_room_data(room_id) or {}
            exits = room_data.get('exits', {})

            for direction, dest_id in exits.items():
                # Skip dynamic/locked dict exits for the map layout itself
                if isinstance(dest_id, dict): continue 
                
                # Normalize direction
                dir_norm = direction.lower().strip()
                
                if dest_id not in visited and dir_norm in vectors:
                    visited.add(dest_id)
                    dx, dy, dz = vectors[dir_norm]
                    q.append((dest_id, x + dx, y + dy, z + dz))
                    
        self.logger.info(f"3D Map built with {len(self.current_level_coord_map)} locations.")

    def _generate_map_string(self, radius: int = 2) -> str:
        """Generates a text-based map string centered on the player."""
        if not hasattr(self, 'current_level_coord_map'): return "Map data not available."
        
        player_loc = self.player.get('location')
        if not player_loc or player_loc not in self.current_level_coord_map:
            return "Current location unknown."

        px, py = self.current_level_coord_map[player_loc]
        
        # Create a reverse mapping from coordinates to room_id for quick lookups
        coord_to_room = {v: k for k, v in self.current_level_coord_map.items()}
        
        map_str = ""
        for y in range(py + radius, py - radius - 1, -1):
            row_str = ""
            for x in range(px - radius, px + radius + 1):
                is_player = (x == px and y == py)
                room_id = coord_to_room.get((x, y))

                if is_player:
                    row_str += f"[{color_text('P', 'error', self.resource_manager)}]"
                elif room_id:
                    if room_id in self.player['visited_rooms']:
                        row_str += "[ ]"
                    else:
                        # Check if adjacent to a visited room to show it as '?'
                        is_adjacent = False
                        for dx, dy in [(0,1), (0,-1), (1,0), (-1,0)]:
                            if coord_to_room.get((x+dx, y+dy)) in self.player['visited_rooms']:
                                is_adjacent = True
                                break
                        row_str += "[?]" if is_adjacent else "   "
                else:
                    row_str += "   "
            map_str += row_str + "\n"
            
        return map_str

    def get_full_level_map_string(self) -> str:
        """
        Generates a full ASCII map of the current level floor, color-coded:
        - TEAL: Current player location
        - WHITE: Visited rooms
        - YELLOW: Elevators/Stairwells
        - RED: Locked rooms
        """
        if not hasattr(self, 'current_level_coord_map') or not self.current_level_coord_map:
            return "Map data not available."

        rm = self.resource_manager
        player_loc = self.player.get('location')
        player_pos = self.current_level_coord_map.get(player_loc, (0,0,0))
        px, py, pz = player_pos

        # Filter to current floor
        coord_to_room = {
            (x, y): rid 
            for rid, (x, y, z) in self.current_level_coord_map.items() 
            if z == pz
        }

        # Determine bounds
        coords_2d = list(coord_to_room.keys())
        if not coords_2d:
            return "No rooms on this floor."
            
        xs = [c[0] for c in coords_2d]
        ys = [c[1] for c in coords_2d]
        min_x, max_x = min(xs) - 1, max(xs) + 1
        min_y, max_y = min(ys) - 1, max(ys) + 1

        # Build map
        map_lines = []
        map_lines.append(color_text(f"=== FLOOR {pz + 1} ===", "info", rm))
        map_lines.append("")
        
        for y in range(max_y, min_y - 1, -1):
            row_str = " " * 2  # Left margin
            
            for x in range(min_x, max_x + 1):
                room_id = coord_to_room.get((x, y))
                
                if not room_id:
                    row_str += " . "  # Empty space
                    row_str += " "
                    continue

                # Get room data
                room_data = self.current_level_rooms_world_state.get(room_id) or self.get_room_data(room_id) or {}
                exits = room_data.get('exits', {})
                is_visited = room_id in self.player.get('visited_rooms', set())
                is_locked = room_data.get('locked') or room_data.get('locked_by_mri')
                
                # Check if vertical transit room
                has_up = any(k in exits for k in ['up', 'upstairs', 'climb'])
                has_down = any(k in exits for k in ['down', 'downstairs', 'descend', 'basement'])
                is_elevator = 'Elevator' in room_id
                is_special = has_up or has_down or is_elevator
                
                # Determine symbol and color
                if x == px and y == py:
                    # TEAL: Current location
                    symbol = "[+]"
                    row_str += color_text(symbol, "info", rm)  # Cyan/Teal
                elif not is_visited:
                    # Show adjacent unvisited as fog
                    is_adjacent = any(
                        coord_to_room.get((x+dx, y+dy)) in self.player.get('visited_rooms', set())
                        for dx, dy in [(0,1), (0,-1), (1,0), (-1,0)]
                    )
                    if is_adjacent:
                        if is_locked:
                            row_str += color_text("[X]", "error", rm)  # RED: Locked
                        else:
                            row_str += color_text("[?]", "light_grey", rm)  # Grey: Unknown
                    else:
                        row_str += " . "  # Out of view
                elif is_locked:
                    # RED: Locked rooms
                    row_str += color_text("[X]", "error", rm)
                elif is_special:
                    # YELLOW: Elevators/Stairs
                    if is_elevator:
                        symbol = "[=]"
                    elif has_up and has_down:
                        symbol = "[=]"
                    elif has_up:
                        symbol = "[^]"
                    else:
                        symbol = "[v]"
                    row_str += color_text(symbol, "warning", rm)  # Yellow/Orange
                else:
                    # WHITE: Regular visited rooms
                    row_str += "[ ]"
                    
                row_str += " "
                
            map_lines.append(row_str)
            
        # Legend
        map_lines.append("")
        map_lines.append("Legend:")
        map_lines.append(color_text("[+]", "info", rm) + " You   " + color_text("[=]", "warning", rm) + " Elevator")
        map_lines.append("[ ] Visited   " + color_text("[X]", "error", rm) + " Locked")
        map_lines.append(color_text("[?]", "light_grey", rm) + " Unknown")
        
        return "\n".join(map_lines)

    def get_gui_map_string(self, width=None) -> str:
        """
        Generates a 5x5 ASCII Z-Slice of the map centered on the player.
        Only shows rooms on the current vertical level (Z-axis).
        """
        rm = self.resource_manager
        current_room_id = self.player.get('location')
        if not current_room_id:
            return "Signal Lost..."

        # Rebuild map if missing or empty
        if not hasattr(self, 'current_level_coord_map') or not self.current_level_coord_map:
            self._build_room_coordinate_map(current_room_id)

        # 1. Get Player Coordinates (x, y, z)
        player_coords = self.current_level_coord_map.get(current_room_id, (0, 0, 0))
        px, py, pz = player_coords

        # 2. Create reverse lookup for THIS FLOOR only
        # We filter: Only include rooms where z == pz
        coord_to_room = {
            (x, y): rid 
            for rid, (x, y, z) in self.current_level_coord_map.items() 
            if z == pz
        }

        # --- Visual Settings ---
        radius = 2 # 5x5 grid
        
        # Map Symbols
        SYM_VOID     = " . " 
        SYM_UNKNOWN  = color_text(" ? ", "light_grey", rm)
        SYM_LOCKED   = color_text("[X]", "error", rm)
        
        # Contextual Symbols (Yellow for special rooms)
        SYM_NORMAL   = "[ ]"  # WHITE for regular visited
        SYM_UP       = color_text("[^]", "warning", rm)     # YELLOW Up Arrow
        SYM_DOWN     = color_text("[v]", "warning", rm)     # YELLOW Down Arrow
        SYM_ELEV     = color_text("[=]", "warning", rm)     # YELLOW Elevator
        
        # Player Symbols (TEAL variants)
        SYM_P_NORM   = color_text("[+]", "info", rm)  # TEAL
        SYM_P_UP     = color_text("[^]", "info", rm)  # Player on Up Stairs (TEAL)
        SYM_P_DOWN   = color_text("[v]", "info", rm)  # Player on Down Stairs (TEAL)
        SYM_P_ELEV   = color_text("[=]", "info", rm)  # Player on Elevator (TEAL)

        map_lines = []

        # --- Header ---
        # Calculate floor relative to start (0). 
        # You might want to offset this by +1 if your start room is technically "Floor 1"
        display_floor = pz + 1 
        header = f"--- Floor {display_floor} : {current_room_id.replace('_', ' ')} ---"
        map_lines.append(color_text(header, "special", rm))
        map_lines.append("") # Spacer

        # --- Render Grid (Top-Down) ---
        for y in range(py + radius, py - radius - 1, -1):
            row_str = " " * 4 # Margin
            
            for x in range(px - radius, px + radius + 1):
                room_id = coord_to_room.get((x, y))
                
                if not room_id:
                    row_str += SYM_VOID
                    row_str += " "
                    continue

                # Get Room Data
                room_data = self.current_level_rooms_world_state.get(room_id) or self.get_room_data(room_id) or {}
                exits = room_data.get('exits', {})
                
                # Analyze Verticality
                # Check for any exit key that implies vertical movement
                has_up = any(k in exits for k in ['up', 'upstairs', 'climb', 'top'])
                has_down = any(k in exits for k in ['down', 'downstairs', 'descend', 'basement'])
                
                # Determine Base Symbol
                if has_up and has_down: symbol = SYM_ELEV
                elif has_up:            symbol = SYM_UP
                elif has_down:          symbol = SYM_DOWN
                else:                   symbol = SYM_NORMAL

                # Check State (Visited / Locked / Player)
                is_player_here = (x == px and y == py)
                is_visited = room_id in self.player.get('visited_rooms', set())
                is_locked = room_data.get('locked') or room_data.get('locked_by_mri')

                if is_player_here:
                    # Player overrides everything. 
                    # If player is on stairs, show Green Stairs so they know where they are.
                    if has_up and has_down: row_str += SYM_P_ELEV
                    elif has_up:            row_str += SYM_P_UP
                    elif has_down:          row_str += SYM_P_DOWN
                    else:                   row_str += SYM_P_NORM
                
                elif is_visited:
                    if is_locked:
                        row_str += SYM_LOCKED
                    else:
                        row_str += symbol
                
                else:
                    # Fog of War: Is this room adjacent to a visited room?
                    is_visible = False
                    is_locked_neighbor = False
                    
                    # Check the 4 cardinal neighbors on this floor
                    for nx, ny in [(0,1), (0,-1), (1,0), (-1,0)]:
                        neighbor_id = coord_to_room.get((x+nx, y+ny))
                        if neighbor_id and neighbor_id in self.player.get('visited_rooms', set()):
                            is_visible = True
                            
                            # Check if THIS unvisited room is locked
                            # We need to check the 'master' data or live state
                            room_data = self.current_level_rooms_world_state.get(room_id) or self.get_room_data(room_id) or {}
                            if room_data.get('locked') or room_data.get('locked_by_mri'):
                                is_locked_neighbor = True
                            break
                    
                    if is_visible:
                        if is_locked_neighbor:
                            # Red [?] for locked
                            row_str += color_text(" ? ", "error", rm)
                        else:
                            # Grey [?] for open
                            row_str += color_text(" ? ", "light_grey", rm)
                    else:
                        row_str += SYM_VOID

                row_str += " " # Cell Spacing
            
            map_lines.append(row_str)

        # --- Legend ---
        map_lines.append("")
        legend = f"{SYM_P_NORM}You {SYM_UP}Up {SYM_DOWN}Down {SYM_ELEV}Lift"
        map_lines.append(legend)

        return "\n".join(map_lines)

    def _player_has_active_light_source(self) -> bool:
        """Check if player has a working light source."""
        inventory = self.player.get('inventory', {})
        items_master = self.resource_manager.get_data('items', {})
        
        for item_key in inventory:
            item_data = items_master.get(item_key, {})
            if item_data.get('provides_light') and not item_data.get('broken', False):
                return True
        return False

    def get_valid_directions(self):
        """
        Returns a list of valid movement directions (strings) from the current room.
        - Robust: Handles missing player/location, malformed room data, and extensible for special exits.
        - Extensible: Ready for future expansion (e.g., locked exits, context-sensitive directions).
        Injected with robust debugging logic.
        """
        if not self.player or 'location' not in self.player:
            self.logger.warning("get_valid_directions: Player or location not set.")
            return []

        current_room_data = self._get_current_room_data()
        if not current_room_data or not isinstance(current_room_data.get("exits"), dict):
            self.logger.warning(f"get_valid_directions: No valid exits found for room '{self.player.get('location')}'.")
            return []

        # --- 1. Gather standard exits ---
        directions = list(current_room_data["exits"].keys())
        self.logger.debug(f"get_valid_directions: Standard exits found: {directions}")

        # --- 2. Optionally include special exits (future extensibility) ---
        special_exits = current_room_data.get("special_exits", {})
        if isinstance(special_exits, dict):
            special_keys = [d for d in special_exits.keys() if d not in directions]
            directions.extend(special_keys)
            self.logger.debug(f"get_valid_directions: Special exits added: {special_keys}")

        self.logger.debug(f"get_valid_directions: Final directions list: {directions}")
        return directions

    # --- NEW: NORMALIZATION & ENTITY COLLECTION HELPERS ---
    def _norm(self, s: str) -> str:
        """Normalize names for matching: lowercase, strip, collapse spaces/underscores."""
        if not isinstance(s, str): return ""
        s = s.strip().lower()
        return re.sub(r'[\s_]+', ' ', s)

    def _get_all_visible_entities_in_room(self, room_name: str) -> dict:
        """
        Gathers all visible entities: furniture, objects, loose items, and hazard-spawned entities.
        Returns them in a structured dictionary.
        If a hazard-spawned entity is present, hides any static object with the same normalized name.
        """
        all_entities = {'furniture': [], 'objects': [], 'items': []}
        room_data = self.get_room_data(room_name)
        if not room_data:
            return all_entities

        all_entities['furniture'] = self._get_visible_furniture_in_room(room_data)
        hazard_entities, hazard_entity_names = self._get_hazard_entities_in_room(room_name)
        all_entities['objects'] = self._get_visible_objects_in_room(room_data, hazard_entity_names)
        all_entities['objects'].extend(hazard_entities)
        all_entities['items'] = self._get_loose_items_in_room(room_name)

        return all_entities

    def _get_visible_furniture_in_room(self, room_data: dict) -> list:
        """
        Collects furniture from the room, resolving descriptions from:
        1. Instance data
        2. Room's examine_details map (Canonical)
        3. Master furniture.json
        Filters out hidden containers that haven't been revealed yet.
        """
        furniture = []
        # Load master data for fallbacks
        furn_master = self.resource_manager.get_data('furniture', {})
        # Load room-specific context descriptions
        room_examine_map = room_data.get('examine_details', {})
        
        # --- FIX: Grab the new state_examine_details map ---
        state_examine_map = room_data.get('state_examine_details', {}) 
        
        def _try_get_desc(key):
            if not key: return None
            
            # --- 1. Check active state overrides FIRST ---
            for flag in getattr(self, 'interaction_flags', set()):
                if flag in state_examine_map:
                    active_state_map = state_examine_map[flag]
                    if key in active_state_map: return active_state_map[key]
                    if key.replace('_', ' ') in active_state_map: return active_state_map[key.replace('_', ' ')]
                    if key.lower() in active_state_map: return active_state_map[key.lower()]
            # ---------------------------------------------
            
            # 2. Try exact base examine details
            if key in room_examine_map: return room_examine_map[key]
            # 3. Try spaces
            if key.replace('_', ' ') in room_examine_map: return room_examine_map[key.replace('_', ' ')]
            # 4. Try lower
            if key.lower() in room_examine_map: return room_examine_map[key.lower()]
            return None

        for f_data in room_data.get('furniture', []):
            # Handle case where furniture might be just a string ID
            if isinstance(f_data, str):
                f_data = {"name": f_data}
                
            if isinstance(f_data, dict) and 'name' in f_data:
                # --- FIX: Skip hidden containers that haven't been revealed ---
                if f_data.get('is_hidden_container', False):
                    reveal_flag = f_data.get('revealed_by_flag')
                    if reveal_flag and reveal_flag not in getattr(self, 'interaction_flags', {}):
                        continue  # Not yet revealed, skip entirely
                    # If no reveal_flag specified, also skip (it's hidden by default)
                    if not reveal_flag:
                        continue
                # ---------------------------------------------------------------

                name = f_data['name']
                
                # 1. Start with description in the instance itself
                desc = f_data.get('description') or f_data.get('examine_details')
                
                # 2. Look in Room's examine_details (High Priority for environmental storytelling)
                if not desc:
                    desc = _try_get_desc(name)

                # 3. Look in Master Data (furniture.json)
                master_entry = furn_master.get(name) or furn_master.get(name.replace(' ', '_'))
                if not desc and master_entry:
                    desc = master_entry.get('description') or master_entry.get('examine_details')

                # 4. Fallback
                if not desc:
                    desc = "It's a piece of furniture."

                # Augment the live f_data dict IN-PLACE with any master keys it lacks.
                # CRITICAL: Do NOT copy f_data here. We must return the original live dict
                # reference so that mutations (e.g. unlock, break) made via _find_entity_in_room
                # propagate back to current_level_rooms_world_state. A copy would silently
                # discard those writes, causing bugs like unlock succeeding but search still
                # seeing the container as locked.
                if master_entry:
                    for k, v in master_entry.items():
                        if k not in f_data:
                            f_data[k] = v

                f_data['description'] = desc
                furniture.append(f_data)
        return furniture

    def _get_visible_objects_in_room(self, room_data: dict, hazard_entity_names: set) -> list:
        objects = []
        
        # --- 1. Dynamically Inject State-Added Objects ---
        base_objects = list(room_data.get('objects', []))
        
        state_added = room_data.get('state_added_objects', {})
        for flag in getattr(self, 'interaction_flags', set()):
            if flag in state_added:
                base_objects.extend(state_added[flag])

        # --- 2. Setup Description Lookup ---
        room_examine_map = room_data.get('examine_details', {})
        state_examine_map = room_data.get('state_examine_details', {})

        def _try_get_desc(key):
            if not key: return None
            
            # Check active state overrides FIRST
            for flag in getattr(self, 'interaction_flags', set()):
                if flag in state_examine_map:
                    active_state_map = state_examine_map[flag]
                    if key in active_state_map: return active_state_map[key]
                    if key.replace('_', ' ') in active_state_map: return active_state_map[key.replace('_', ' ')]
                    if key.lower() in active_state_map: return active_state_map[key.lower()]
            
            # Fallback to base examine details
            if key in room_examine_map: return room_examine_map[key]
            if key.replace('_', ' ') in room_examine_map: return room_examine_map[key.replace('_', ' ')]
            if key.lower() in room_examine_map: return room_examine_map[key.lower()]
            return None

        # --- 3. Process the Unified Objects List ---
        for obj in base_objects:
            
            # Handle if the object is just a string (e.g., "X-ray films")
            if isinstance(obj, str):
                norm_name = normalize_text(obj)
                if norm_name in hazard_entity_names:
                    continue  # Skip static object if hazard entity overrides it
                    
                desc = _try_get_desc(obj)
                objects.append({"name": obj, "description": desc or f"You see a {obj}."})
                
            # Handle if the object is a dictionary (e.g., the wall-mounted TV)
            elif isinstance(obj, dict) and 'name' in obj:
                norm_name = normalize_text(obj['name'])
                if norm_name in hazard_entity_names:
                    continue  # Skip static object if hazard entity overrides it
                
                name = obj['name']
                
                # Try getting description directly from the object dict
                desc = obj.get('description') or obj.get('examine_details')
                
                # Fallback to room's examine details or state overrides
                if not desc:
                    desc = _try_get_desc(name)
                    # Also check aliases if the base name fails
                    if not desc and 'aliases' in obj:
                        for alias in obj['aliases']:
                            desc = _try_get_desc(alias)
                            if desc: break

                # Final fallback
                if not desc:
                    desc = "It's an object."

                entity = obj.copy()
                entity['description'] = desc
                objects.append(entity)

        return objects

    def _get_hazard_entities_in_room(self, room_name: str) -> tuple[list, set]:
        hazard_entity_names = set()
        hazard_entities = []
        if self.hazard_engine:
            hazards_master = self.resource_manager.get_data('hazards', {})
            active_hazard_keys = self.hazard_engine.get_active_hazards_for_room(room_name)
            for h_key in active_hazard_keys:
                h_def = hazards_master.get(h_key, {})
                for entity_name in h_def.get("spawn_entities", []):
                    entity_type = h_def.get("entity_type", "object")
                    if isinstance(entity_name, dict):
                        entity_name_str = entity_name.get('name') or str(entity_name)
                        if not entity_name_str:
                            continue

                        # --- FIX START: Prefer explicit description from JSON ---
                        desc = entity_name.get('description')

                        # Only fall back to state-based/generic if no description provided
                        if not desc:
                            curr_state = self.hazard_engine.get_hazard_state(h_key, room_name) or h_def.get('initial_state')
                            sdef = (h_def.get('states') or {}).get(curr_state or "", {})
                            desc = sdef.get('description')
                            if desc:
                                desc = desc.replace("{object_name}", entity_name_str)

                        # Final Fallback
                        if not desc:
                            desc = "It is a product of the hazard in this room."
                        # --- FIX END ---

                        # --- FIX: Stop hardcoding hazard_entity and pass original data ---
                        entity_data = {
                            "name": entity_name_str,
                            "description": desc,
                            "type": entity_name.get("type", "hazard_entity"), 
                            "hazard_key": h_key,
                            "is_usable": True,  # Assume hazard entities are usable
                            "data": entity_name # Pass the original dict
                        }
                        if entity_type == "item":
                            # Items will be handled in items list
                            continue
                        else:
                            hazard_entities.append(entity_data)
                        hazard_entity_names.add(normalize_text(entity_name_str))
                    else:
                        entity_name_str = str(entity_name)
                        hazard_entity_names.add(normalize_text(entity_name_str))
                        examine_responses = h_def.get("examine_responses", {})
                        desc = (
                            examine_responses.get(entity_name_str, {}).get("base_description") or
                            h_def.get('description') or
                            "It is a product of the hazard in this room."
                        )
                        entity_data = {
                            "name": entity_name_str,
                            "description": desc,
                            "type": entity_name.get("type", "hazard_entity"), 
                            "hazard_key": h_key,
                            "is_usable": True,  # Assume hazard entities are usable
                            "data": entity_name # Pass the original dict
                        }
                        if entity_type != "item":
                            hazard_entities.append(entity_data)
        return hazard_entities, hazard_entity_names

    def _get_loose_items_in_room(self, room_name: str) -> list:
        items = []
        items_master = self.resource_manager.get_data('items', {})
        for item_key, item_state in self.current_level_items_world_state.items():
            if item_state.get('location') == room_name:
                item_data = items_master.get(item_key, {})
                desc = (item_data.get('examine_details') or item_data.get('description') or "An item.")
                entity = {
                    "name": item_data.get('name', item_key),
                    "description": desc,
                    "type": "item",
                    "id_key": item_key,
                    "data": item_data
                }
                items.append(entity)
        return items

    # --- REFINED: The Definitive Entity Finder ---
    def _find_entity_in_room(self, target_str: str, room_name: str) -> Optional[dict]:
        """Finds any entity in the room or inventory with flexible name matching."""
        target_norm = normalize_text(target_str)
        if not target_norm:
            return None

        # Priority 1: Player Inventory
        entity = self._find_entity_in_inventory(target_norm)
        if entity:
            return entity

        visible_entities = self._get_all_visible_entities_in_room(room_name)

        # Priority 2: Furniture
        for f in visible_entities['furniture']:
            # Skip hidden containers that haven't been revealed
            if isinstance(f, dict) and f.get('is_hidden_container', False):
                continue

            if normalize_text(f.get('name', '')) == target_norm:
                return {
                    'id_key': f['name'],
                    'name': f['name'].replace('_', ' ').capitalize(),
                    'type': 'furniture',
                    'data': f
                }

        # Priority 3: Room Objects (static and hazard-spawned)
        entity = self._find_entity_in_objects(target_norm, visible_entities['objects'])
        if entity:
            return entity

        # Priority 4: Loose Items
        entity = self._find_entity_in_loose_items(target_norm, room_name)
        if entity:
            return entity

        return None

    def _find_entity_in_inventory(self, target_norm: str) -> Optional[dict]:
        # Strip apostrophes from the search term for fuzzy matching
        # e.g. "bludworths house key" matches "Bludworth's House Key"
        target_no_apos = target_norm.replace("'", "").replace("\u2019", "")

        for item_key in self.player.get('inventory', []):
            display_name = self._get_item_display_name(item_key)
            display_norm = normalize_text(display_name)
            display_no_apos = display_norm.replace("'", "").replace("\u2019", "")

            if display_norm == target_norm or display_no_apos == target_no_apos:
                master_data = self.resource_manager.get_data('items', {}).get(item_key, {})
                return {
                    'id_key': item_key,
                    'name': display_name,
                    'type': 'item_inventory',
                    'data': master_data
                }
        return None

    def _find_entity_in_furniture(self, target_norm: str, furniture_list: list) -> Optional[dict]:
        for f_data in furniture_list:
            if normalize_text(f_data.get('name', '')) == target_norm:
                return {
                    'id_key': f_data['name'],
                    'name': f_data['name'].replace('_', ' ').capitalize(),
                    'type': 'furniture',
                    'data': f_data
                }
        return None

    def _find_entity_in_objects(self, target_norm: str, objects_list: list) -> Optional[dict]:
        for o_data in objects_list:
            # Support matching by name and aliases
            aliases = [normalize_text(a) for a in o_data.get('aliases', [])]
            if normalize_text(o_data.get('name', '')) == target_norm or target_norm in aliases:
                return {
                    'id_key': o_data.get('id_key', o_data.get('name')),
                    'name': o_data['name'].replace('_', ' ').capitalize(),
                    'type': o_data.get('type', 'object'),
                    'data': o_data
                }
            # Also match hazard-spawned entities by their name
            if o_data.get('type') == 'hazard_entity':
                entity_name = o_data.get('name', '')
                if isinstance(entity_name, dict):
                    entity_name_str = entity_name.get('name') or str(entity_name)
                else:
                    entity_name_str = str(entity_name)
                if normalize_text(entity_name_str) == target_norm:
                    return {
                        'id_key': o_data.get('hazard_key', entity_name_str),
                        'name': entity_name_str.replace('_', ' ').capitalize(),
                        'type': 'hazard_entity',
                        'data': o_data
                    }
        return None

    def _find_entity_in_loose_items(self, target_norm: str, room_name: str) -> Optional[dict]:
        for item_key, world_data in self.current_level_items_world_state.items():
            if world_data.get("location") == room_name:
                if normalize_text(self._get_item_display_name(item_key)) == target_norm:
                    master_data = self.resource_manager.get_data('items', {}).get(item_key, {})
                    return {
                        'id_key': item_key,
                        'name': self._get_item_display_name(item_key),
                        'type': 'item',
                        'data': master_data
                    }
        return None

    # --- NPC helpers ---
    def _find_npc_in_room(self, npc_name_str: str, room_id: str):
        from .utils import normalize_text
        target_norm = normalize_text(npc_name_str)
        room_data = self.get_room_data(room_id) or {}
        npcs_master = self.resource_manager.get_data('npcs', {})
        
        # Safely combine arrays
        npcs_list = room_data.get('npcs', [])
        present_list = room_data.get('npcs_present', [])
        if not isinstance(npcs_list, list): npcs_list = []
        if not isinstance(present_list, list): present_list = []
        
        all_npcs = npcs_list + present_list
        
        for npc in all_npcs:
            npc_id = npc.get('id', npc.get('name', '')) if isinstance(npc, dict) else npc
            npc_data = npcs_master.get(npc_id) or npcs_master.get('npcs', {}).get(npc_id, {})
            
            # --- THE FIX: Never return an empty dictionary! ---
            # If master lookup fails, fall back to the local dictionary so 'respond' doesn't crash!
            actual_npc = npc_data if npc_data else (npc if isinstance(npc, dict) else {"name": npc_id, "id": npc_id})
            
            if normalize_text(actual_npc.get('name', npc_id)) == target_norm:
                return actual_npc
            if normalize_text(npc_id) == target_norm:
                return actual_npc
                
        # Check active companions
        for companion in self.player.get('companions', []):
            if normalize_text(companion) == target_norm:
                return npcs_master.get(companion) or {"name": companion, "id": companion}
                
        return None

    def _npc_key(self, npc: dict) -> str:
        return npc.get('id') or npc.get('name')

    def _get_npc_state(self, npc: dict) -> str:
        key = self._npc_key(npc)
        return self.player.get('npc_states', {}).get(key, npc.get('initial_state'))

    def _set_npc_state(self, npc: dict, state: str):
        key = self._npc_key(npc)
        self.player.setdefault('npc_states', {})[key] = state

    # --- REFINED: Perception Methods ---
    def _get_rich_room_description(self, room_id: str) -> str:
        """
        Compiles a full description.
        PATCHED:
        - Filters out Dead or Missing NPCs.
        - Omen providers glow for Detective and Medium.
        - Surfaces loose items so text matches Contextual Buttons.
        - Correctly formats dictionary-based conditional exits.
        """
        room_data = self.get_room_data(room_id)
        if not room_data:
            return "You are in a featureless void."

        rm = self.resource_manager
        char_class = self.player.get('character_class', '')

        # --- State-Based Environmental Overrides ---
        base_desc = room_data.get('description', '')
        state_descriptions = room_data.get('state_descriptions', {})
        if state_descriptions:
            for flag in getattr(self, 'interaction_flags', set()):
                if flag in state_descriptions:
                    base_desc = state_descriptions[flag]
                    break
                    
        # Inject JSON Variables
        base_desc = self._format_dynamic_text(base_desc)
        
        # Build the final description string with the bolded room name
        description = f"[b]{color_text(room_id, 'room', rm)}[/b]\n{base_desc}"

        visible_entities = self._get_all_visible_entities_in_room(room_id)
        furniture_names = [f['name'] for f in visible_entities.get('furniture', [])]

        # --- Sensory masking check ---
        sensory_masked = False
        masking_hazard_name = None
        if getattr(self, 'hazard_engine', None):
            hazards_master = self.resource_manager.get_data('hazards', {})
            for h_key in self.hazard_engine.get_active_hazards_for_room(room_id):
                h_def = hazards_master.get(h_key, {})
                h_state = self.hazard_engine.get_hazard_state(h_key, room_id)
                state_def = h_def.get('states', {}).get(h_state or '', {})
                if state_def.get('masks_sensory_feedback', False):
                    sensory_masked = True
                    masking_hazard_name = h_def.get('name', h_key)
                    break

        # --- Omen-aware object formatting & Hazard Gating ---
        formatted_objects = []
        for obj in visible_entities.get('objects', []):
            obj_name = ""
            is_omen = False

            if isinstance(obj, dict):
                # Catch escalating hazard keys
                h_key = obj.get('hazard_key', '')
                if char_class != 'Medium' and h_key in ['deaths_breath', 'malevolent_gust']:
                    continue  
                
                obj_name = obj.get('name', '')
                is_omen = bool(obj.get('is_omen_provider', False))
            else:
                obj_name = str(obj)
                # Catch all string aliases
                norm_name = obj_name.lower().replace('_', ' ')
                if char_class != 'Medium' and norm_name in {
                    "cold breeze", "sudden draft", "chilling air", 
                    "dark presence", "malevolent gust", "ominous shadow", "deaths breath"
                }:
                    continue

            pretty_name = obj_name.replace('_', ' ')
            if is_omen and char_class in ['Detective', 'Medium']:
                colored_name = color_text(pretty_name, 'special', rm)
            else:
                colored_name = color_text(pretty_name, 'item', rm)

            formatted_objects.append(colored_name)

        # --- NEW: LOOSE ITEMS FILTERING ---
        items_present = room_data.get('items_present', [])
        # Fallback for older JSON schemas that might just use 'items'
        if not items_present and 'items' in room_data:
            items_present = room_data.get('items', [])
            
        formatted_items = []
        for item in items_present:
            pretty_item = str(item).replace('_', ' ')
            formatted_items.append(color_text(pretty_item, 'item', rm))

        # --- NPC FILTERING LOGIC ---
        npc_names = []
        roster = self.player.get('npc_status', {})

        for npc in room_data.get('npcs', []):
            name = None
            npc_id = None
            
            if isinstance(npc, dict):
                name = npc.get('name', 'Someone')
                if not name:
                    continue
                npc_id = npc.get('id', name).lower()
                self._resolve_npc_dialogue_entry_state(npc, room_id)
                
            elif isinstance(npc, str):
                name = npc
                if not name:
                    continue
                npc_id = name.lower()
            else:
                continue

            status = roster.get(npc_id, 'alive')
            if status in ['dead', 'missing']:
                continue

            display_name = name
            if status == 'injured':
                display_name += " (Injured)"

            npc_names.append(color_text(display_name, 'npc', rm))

        # --- Combine All Entities (Furniture + Objects + Items + NPCs) ---
        if furniture_names or formatted_objects or formatted_items or npc_names:
            formatted_furniture = [color_text(name.replace('_', ' '), 'furniture', rm) for name in furniture_names]
            
            entity_list = formatted_furniture + formatted_objects + formatted_items
            if npc_names:
                entity_list += npc_names
                
            if entity_list:
                description += f"\n\nYou see: {', '.join(entity_list)}."

        if sensory_masked:
            description += f"\n[color=ff6600]The {masking_hazard_name} "
            description += "is so loud you can't hear anything else.[/color]"
    
        # --- FIX: Dictionary Exit Parsing ---
        exits = room_data.get('exits', {})
        if exits:
            exit_texts = []
            for direction, dest_room in exits.items():
                if isinstance(dest_room, dict):
                    # NEW FIX: Intercept dynamic destinations
                    if dest_room.get('dynamic_destination'):
                        dest_name = color_text("Floor Selection", 'room', rm)
                    else:
                        # Safely grab the target string
                        target_str = dest_room.get('target', 'Unknown Destination')
                        
                        # Strip out LEVEL_TRANSITION tags for a cleaner UI output
                        clean_target = target_str.replace("LEVEL_TRANSITION_", "").replace("_", " ").title()
                        dest_name = color_text(clean_target, 'room', rm)
                else:
                    # Standard string-based exit
                    dest_name = color_text(str(dest_room).replace('_', ' '), 'room', rm)

                exit_texts.append(f"{color_text(direction, 'exit', rm)} ({dest_name})")

            description += f"\nExits: {', '.join(exit_texts)}."

        return description

    def _format_dynamic_text(self, text: str) -> str:
        """Injects live game state variables into text strings."""
        if not text or not isinstance(text, str):
            return text
            
        # FIX: Check the disaster dictionary for the city name early in the game
        disaster = self.player.get('intro_disaster', {})
        city_name = self.player.get('current_city') or disaster.get('city') or 'McKinley'
        role_map = self.player.get('_premonition_role_map', {})
        visionary_name = role_map.get('visionary', 'A stranger')
        friend_name = role_map.get('friend', 'your friend')
        skeptic_name = role_map.get('skeptic', 'someone')
        authority_figure_name = role_map.get('authority_figure', 'an authority figure')
        panicking_name = role_map.get('panicking', 'someone')
        fatalist_name = role_map.get('fatalist', 'someone')
        distracted_name = role_map.get('distracted', 'someone')
        selfish_name = role_map.get('selfish', 'someone')
        bystander_1_name = role_map.get('bystander_1', 'someone')
        bystander_2_name = role_map.get('bystander_2', 'someone')

        # --- NEW: Grab the current Hunt Target's job info! ---
        target_name = self.player.get('current_hunt_target', '')
        job_title = "worker"
        workplace_name = "this place"
        
        if target_name:
            job_data = self.player.get('npc_workplaces', {}).get(target_name.lower(), {})
            job_title = job_data.get('job_title', 'worker')
            workplace_name = job_data.get('workplace_name', 'this place')

        text = text.replace('{city_name}', city_name)
        text = text.replace('{visionary}', visionary_name)
        text = text.replace('{friend}', friend_name)
        text = text.replace('{skeptic}', skeptic_name)
        text = text.replace('{authority_figure}', authority_figure_name)
        text = text.replace('{panicking}', panicking_name)
        text = text.replace('{fatalist}', fatalist_name)
        text = text.replace('{distracted}', distracted_name)
        text = text.replace('{selfish}', selfish_name)
        text = text.replace('{bystander_1}', bystander_1_name)
        text = text.replace('{bystander_2}', bystander_2_name)
        text = text.replace('{target_name}', target_name.title())
        text = text.replace('{job_title}', job_title)
        text = text.replace('{workplace_name}', workplace_name)

        # --- NEW: The DOA Rumor Injector ---
        if '{doa_rumor}' in text or '{weird_injuries_explanation}' in text:
            offscreen = self.player.get('offscreen_casualties', [])
            if offscreen:
                # Grab the most recent victim that Death claimed!
                latest = offscreen[-1]
                v_name = latest.get('name', 'Someone').title()
                v_fate = latest.get('fate', 'a freak accident')
                
                rumor = f"Between you and me — one of the people who walked away from that situation was just brought back in. Dead on arrival. Name was NOT *winks* {v_name}. The paramedics said it was a freak accident... something about {v_fate}, and boy do the injuries tell that story."
                
                exp = f"'They are saying it was an accident. But you don't just die from {v_fate} out of nowhere. It's like the universe bent over backward just to kill them.\\n\\nI've seen accident trauma. This felt... deliberate. Mean spirited, but not by a person.\\n\\nAnyway. I didn't say that, you know HIPAA laws.'"
            else:
                # Fallback if no one died offscreen
                rumor = "Between you and me — a DOA came in earlier. Unidentified, but the ambulance came from the disaster site so I knew it was related."
                
                exp = "'Consistent with the disaster, technically. I've seen accident trauma. This felt... deliberate. Mean spirited, but not by a person.\\n\\nAnyway. I didn't say that, you know HIPAA laws.'"
                
            text = text.replace('{doa_rumor}', rumor)
            text = text.replace('{weird_injuries_explanation}', exp)
        
        # --- NEW: Recent Victim Injector ---
        if '{recent_victim}' in text:
            deaths_list = self.player.get('deaths_list', [])
            roster = self.player.get('npc_status', {})
            dead_list = [n for n in deaths_list if n.lower() != 'player' and roster.get(n.lower()) == 'dead']
            recent = dead_list[-1].title() if dead_list else "one of the others"
            text = text.replace('{recent_victim}', recent)
        # -----------------------------------
        # --- NEW: Environmental Context Injector ---
        room_id = self.player.get('location')
        if room_id:
            room_data = getattr(self, 'get_room_data', lambda x: {})(room_id) or {}
            
            # 1. Room Name
            room_name = room_data.get('name', 'this place')
            text = text.replace('{current_room_name}', room_name)

            # 2. Active Hazard Name (for panic_state)
            hazards_in_room = room_data.get('hazards_present', [])
            active_hazard_name = "that thing"
            if hazards_in_room:
                first_haz = hazards_in_room[0]
                if isinstance(first_haz, str):
                    # It's a standard hazard string ID
                    haz_master = getattr(self, 'resource_manager', None)
                    if haz_master:
                        haz_data = haz_master.get_data('hazards', {}).get(first_haz, {})
                        active_hazard_name = haz_data.get('name', 'that thing')
                elif isinstance(first_haz, dict):
                    # It's a legacy or override dict
                    active_hazard_name = first_haz.get('object_name_override', 'that thing')
            
            text = text.replace('{active_hazard_name}', active_hazard_name.lower())

            # 3. Local Omen / Object (for workplace_target_state)
            objects_in_room = room_data.get('objects', [])
            local_omen = "this broken equipment"
            if objects_in_room:
                first_obj = objects_in_room[0]
                if isinstance(first_obj, str):
                    local_omen = first_obj.replace('_', ' ')
                elif isinstance(first_obj, dict):
                    local_omen = first_obj.get('name', 'this broken equipment')
                    
            text = text.replace('{local_omen_object}', local_omen.lower())
        # -------------------------------------------
        # --- NEW: The Reaction Generator ---
        if '{reaction}' in text:
            reaction_pool = [
                "Holy shit.",
                "Oh my god.",
                "Jesus Christ on a cross...",
                "What the hell just happened?",
                "Christ almighty.",
                "Sweet Jesus.",
                "This can't be real.",
                "What the fuck.",
                "Dear God."
            ]
            # Save the chosen reaction so if the text redraws, it doesn't randomly change phrases
            chosen_reaction = self.player.get('_chosen_reaction')
            if not chosen_reaction:
                chosen_reaction = random.choice(reaction_pool)
                self.player['_chosen_reaction'] = chosen_reaction
                
            text = text.replace('{reaction}', chosen_reaction)
        # -----------------------------------

        if '{visionary_explains}' in text:
            intro_disaster = self.player.get('intro_disaster', {})
            pool = intro_disaster.get('visionary_explains', [
                "I could smell the smoke, hear the screaming, the blood in my mouth— it felt so real, you know?"
            ])
            # Save the chosen explanation to the player state so they don't 
            # wildly change their story every time the player clicks "talk"!
            chosen = self.player.get('_chosen_visionary_explanation')
            if not chosen:
                chosen = random.choice(pool)
                self.player['_chosen_visionary_explanation'] = chosen
                
            text = text.replace('{visionary_explains}', chosen)
            
        return text

    def _join_names(self, names: list) -> str:
        """
        Joins a list of names grammatically.
        ['Maya'] -> "Maya"
        ['Maya', 'Lucas'] -> "Maya and Lucas"
        ['Maya', 'Lucas', 'Noah'] -> "Maya, Lucas, and Noah"
        """
        if not names: return ""
        if len(names) == 1: return names[0]
        if len(names) == 2: return f"{names[0]} and {names[1]}"
        return f"{', '.join(names[:-1])}, and {names[-1]}"

    def generate_narrative_epilogue(self) -> str:
        """
        Constructs a conversational summary of the survivors and the fallen.
        """
        roster = self.player.get('npc_status', {})
        if not roster:
            return "You survived alone."

        # Categorize the cast
        alive = []
        injured = []
        dead = []
        missing = []

        for name_key, status in roster.items():
            # Format name nicely (capitalize) and color it
            formatted_name = name_key.replace('_', ' ').title()
            
            if status == 'alive':
                alive.append(color_text(formatted_name, 'success', self.resource_manager))
            elif status == 'injured':
                injured.append(color_text(formatted_name, 'warning', self.resource_manager))
            elif status == 'dead':
                dead.append(color_text(formatted_name, 'error', self.resource_manager))
            elif status == 'missing':
                missing.append(color_text(formatted_name, 'light_grey', self.resource_manager))

        narrative = []

        # --- The Survivors ---
        total_survivors = len(alive) + len(injured)
        
        if total_survivors == 0:
            narrative.append("The silence is absolute. No one made it out.")
        else:
            # Healthy Survivors
            if alive:
                names_text = self._join_names(alive)
                if len(alive) == 1:
                    narrative.append(f"{names_text} emerged from the nightmare shaken, but physically unharmed.")
                else:
                    narrative.append(f"{names_text} managed to escape the design intact.")
            
            # Injured Survivors
            if injured:
                names_text = self._join_names(injured)
                verb = "carry" if len(injured) > 1 else "carries"
                narrative.append(f"{names_text} survived, but they {verb} the physical scars of the night.")

        # --- The Fallen ---
        if dead:
            names_text = self._join_names(dead)
            if len(dead) == 1:
                narrative.append(f"Tragically, {names_text} fell victim to the inevitability of death.")
            else:
                narrative.append(f"The cost of survival was high. {names_text} were claimed by the design.")

        # --- The Missing ---
        if missing:
            names_text = self._join_names(missing)
            verb = "their" if len(missing) > 1 else "their" # Singular 'their' works for both
            narrative.append(f"As for {names_text}... {verb} fate remains a mystery.")

        # Combine into paragraphs
        return "\n\n".join(narrative)

    def get_available_targets(self, verb: str) -> list:
        """
        Returns valid targets for a verb.
        PATCHED: Now includes a fallback to check Hazard definitions for dynamic verbs (e.g. 'answer').
        """
        try:
            current_room_id = self.player.get('location')
            if not current_room_id:
                self.logger.warning("get_available_targets: No current room set for player.")
                return []

            if verb in ('move', 'go'):
                return self._get_targets_move(current_room_id)

            if verb in ('examine', 'look', 'inspect'):
                return self._get_targets_examine(current_room_id)

            if verb in ('search',):
                return self._get_targets_search(current_room_id)

            if verb in ('take', 'get'):
                return self._get_targets_take(current_room_id)

            if verb == 'use':
                return self._get_targets_use(current_room_id)

            if verb == 'unlock':
                return self._get_targets_unlock(current_room_id)

            if verb == 'force':
                return self._get_targets_force(current_room_id)

            if verb == 'talk':
                return self._get_targets_talk(current_room_id)

            if verb == 'respond':
                return self._get_targets_respond()

            # >>> PATCH START: The Catch-All <<<
            # If the verb isn't one of the standard hardcoded ones, 
            # ask the active hazards in the room if they use it.
            hazard_targets = self._get_targets_from_hazards(verb, current_room_id)
            if hazard_targets:
                self.logger.debug(f"get_available_targets: Found hazard targets for '{verb}': {hazard_targets}")
                return hazard_targets
            # >>> PATCH END <<<

            self.logger.debug(f"get_available_targets: No handler for verb '{verb}'. Returning [].")
            return []
        except Exception as e:
            self.logger.error(f"get_available_targets: Error for verb '{verb}': {e}", exc_info=True)
            return []

    def _get_targets_from_hazards(self, verb: str, current_room_id: str) -> list:
        """
        Scans active hazards in the room. 
        If a hazard has a player_interaction rule for 'verb', returns the target names.
        """
        targets = set()
        
        if not self.hazard_engine:
            return []

        # Get hazards in current room
        active_hazards = self.hazard_engine.get_room_hazards_descriptions(current_room_id)
        
        for hid, h_inst in active_hazards.items():
            master = h_inst.get('master_data', {})
            current_state = h_inst.get('state')
            
            # Check 1: State-Specific Interactions (Highest Priority)
            state_def = master.get('states', {}).get(current_state, {})
            state_interactions = state_def.get('player_interaction', {})
            
            if verb in state_interactions:
                # The verb exists in this state!
                rules = state_interactions[verb]
                for rule in rules:
                    # Add the target name (e.g., "phone", "cell phone")
                    on_names = rule.get('on_target_name', [])
                    if isinstance(on_names, str):
                        targets.add(on_names)
                    else:
                        targets.update(on_names)

            # Check 2: Global Hazard Interactions
            global_interactions = master.get('player_interaction', {})
            if verb in global_interactions:
                rules = global_interactions[verb]
                for rule in rules:
                    # Check if this rule is valid for the current state
                    req_states = rule.get('requires_hazard_state')
                    if not req_states or current_state in req_states:
                        on_names = rule.get('on_target_name', [])
                        if isinstance(on_names, str):
                            targets.add(on_names)
                        else:
                            targets.update(on_names)

        return sorted([t.replace('_', ' ').title() for t in targets])

    def _get_targets_move(self, current_room_id: str) -> list:
        try:
            room_data = self.get_room_data(current_room_id)
            
            # --- THE FIX: Auto-Normalize JSON Typos ---
            if not room_data:
                # If the raw string fails, convert "Body & Soul" into "body_and_soul"
                norm_id = current_room_id.lower().replace(' ', '_').replace('&', 'and')
                room_data = self.get_room_data(norm_id)
                
            if not room_data:
                self.logger.warning(f"_get_targets_move: No data for current room '{current_room_id}'.")
                return []
                
            exits = room_data.get('exits', {})
            self.logger.debug(f"_get_targets_move: Exits for move/go: {list(exits.keys())}")
            return sorted(list(exits.keys()))
            
        except Exception as e:
            self.logger.error(f"_get_targets_move: Error: {e}", exc_info=True)
            return []

    def _get_targets_examine(self, current_room_id: str) -> list:
        try:
            targets = set()
            visible = self._get_all_visible_entities_in_room(current_room_id)
            items_master = self.resource_manager.get_data('items', {})
            for f in visible['furniture']:
                targets.add(f['name'])
            for o in visible['objects']:
                targets.add(o['name'])
            for item_key, world_data in self.current_level_items_world_state.items():
                if world_data.get("location") == current_room_id:
                    targets.add(self._get_item_display_name(item_key))
            self.logger.debug(f"_get_targets_examine: Examine targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_examine: Error: {e}", exc_info=True)
            return []

    def _get_targets_search(self, current_room_id: str) -> list:
        try:
            targets = set()
            visible = self._get_all_visible_entities_in_room(current_room_id)
            for f in visible['furniture']:
                if f.get('is_container'):
                    targets.add(f['name'])
            self.logger.debug(f"_get_targets_search: Search targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_search: Error: {e}", exc_info=True)
            return []

    def _get_targets_take(self, current_room_id: str) -> list:
        try:
            targets = set()
            items_master = self.resource_manager.get_data('items', {})
            # 1) Loose items in the room (takeable)
            for item_key, world_data in self.current_level_items_world_state.items():
                if world_data.get("location") == current_room_id:
                    item_data = items_master.get(item_key, {})
                    if item_data.get("takeable", False):
                        targets.add(self._get_item_display_name(item_key))
            # 2) Items in containers that have been searched (use exact flag id)
            room_data = self.get_room_data(current_room_id) or {}
            for furniture in room_data.get('furniture', []):
                if isinstance(furniture, dict) and furniture.get('is_container'):
                    flag_name = f"searched_{furniture.get('name', '')}"  # exact id key, no normalization
                    if flag_name in self.interaction_flags:
                        for val in furniture.get('items', []):
                            for key, data in items_master.items():
                                if (
                                    val == key
                                    or normalize_text(val) == normalize_text(key)
                                    or normalize_text(val) == normalize_text(data.get('name', ''))
                                ):
                                    if data.get("takeable", False):
                                        targets.add(data.get('name', key))
                                    break
            if targets:
                targets.add("all")
            self.logger.debug(f"_get_targets_take: Take targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_take: Error: {e}", exc_info=True)
            return []

    def _get_targets_use(self, current_room_id: str) -> list:
        try:
            targets = set()
            # 1. Inventory items
            for item_key in self.player.get('inventory', []):
                targets.add(self._get_item_display_name(item_key))
            
            # 2. Visible Room Objects (Furniture & Objects & Hazard Entities)
            visible = self._get_all_visible_entities_in_room(current_room_id)
            
            # Load definitions
            hazards_master = self.resource_manager.get_data('hazards', {})
            
            # Iterate over all visible objects (includes hazard entities)
            for entity in visible['objects'] + visible['furniture']:
                entity_name = entity.get('name', '')
                # Normalized check
                norm_name = normalize_text(entity_name)
                
                # Check Hazard Interaction Rules
                hazard_key = entity.get('hazard_key')
                if hazard_key:
                     h_def = hazards_master.get(hazard_key, {})
                     # FIX: Alias 'interact' rules to the 'use' verb button
                     use_rules = list(h_def.get('player_interaction', {}).get('use', []))
                     use_rules.extend(h_def.get('player_interaction', {}).get('interact', []))
                     
                     # Get current state
                     hazard_state = None
                     if self.hazard_engine:
                         hazard_state = self.hazard_engine.get_hazard_state(hazard_key, current_room_id)
                     if not hazard_state:
                         hazard_state = h_def.get('initial_state')
                     
                     # Check if ANY use rule matches this entity and state
                     for rule in use_rules:
                         on_names = rule.get('on_target_name', [])
                         if isinstance(on_names, str): on_names = [on_names]
                         
                         if any(normalize_text(n) == norm_name for n in on_names):
                             req_states = rule.get('requires_hazard_state', [])
                             if not req_states or hazard_state in req_states:
                                 targets.add(entity_name)
                                 break

                # Check generic 'use_interaction' on furniture/objects
                if entity.get('use_interaction') or entity.get('is_usable'):
                     targets.add(entity_name)

            # 3. Cross-match active room hazards' use rules against ALL visible object names.
            # This handles objects that are named targets of a hazard use rule but carry no
            # explicit hazard_key themselves (e.g. 'revolving door' in Hospital Morgue Exit).
            room_data = self.get_room_data(current_room_id) or {}
            hazards_in_room = room_data.get('hazards_present', [])
            all_visible_names = {normalize_text(e.get('name', '')) for e in visible['objects'] + visible['furniture']}
            for hazard in hazards_in_room:
                # --- THE FIX: Extract the string if 'hazard' is a dictionary object ---
                hazard_type = hazard
                if isinstance(hazard_type, dict):
                    # Safely pull the string ID out of the instance dict
                    hazard_type = hazard_type.get('type') or hazard_type.get('id', str(hazard))

                h_def = hazards_master.get(hazard_type, {})
                use_rules = h_def.get('player_interaction', {}).get('use', [])
                hazard_state = None
                if self.hazard_engine:
                    hazard_state = self.hazard_engine.get_hazard_state(hazard_type, current_room_id)
                if not hazard_state:
                    hazard_state = h_def.get('initial_state')
                for rule in use_rules:
                    req_states = rule.get('requires_hazard_state', [])
                    if req_states and hazard_state not in req_states:
                        continue
                    on_names = rule.get('on_target_name', [])
                    if isinstance(on_names, str):
                        on_names = [on_names]
                    for n in on_names:
                        if normalize_text(n) in all_visible_names:
                            targets.add(n)

            # 4. interactable_triggers with on_action == 'use'
            # These are room-level scripted interactions (e.g. the revolving door sequence).
            for trigger_name, trigger_data in room_data.get('interactable_triggers', {}).items():
                if trigger_data.get('on_action') == 'use':
                    # Respect requires_all_evidence gate if present
                    if trigger_data.get('requires_all_evidence'):
                        if not self._requirements_met_for_level_exit():
                            continue
                    targets.add(trigger_name)

            self.logger.debug(f"_get_targets_use: Use targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_use: Error: {e}", exc_info=True)
            return []

    def _get_targets_unlock(self, current_room_id: str) -> list:
        try:
            targets = set()
            room = self.get_room_data(current_room_id) or {}
            # exits
            for direction, dest in (room.get('exits') or {}).items():
                if not isinstance(dest, str):
                    continue
                dest_master = self.get_room_data(dest) or {}
                locking = dest_master.get('locking', {}) if isinstance(dest_master.get('locking'), dict) else {}
                if locking.get('locked'):
                    targets.add(direction)
            # furniture
            for f in (room.get('furniture') or []):
                if isinstance(f, dict) and (f.get('locked') or (isinstance(f.get('locking'), dict) and f['locking'].get('locked'))):
                    targets.add(f.get('name', 'Locked Object'))
            self.logger.debug(f"_get_targets_unlock: Unlock targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_unlock: Error: {e}", exc_info=True)
            return []

    def _get_targets_force(self, current_room_id: str) -> list:
        try:
            targets = set()
            room = self.get_room_data(current_room_id) or {}
            # exits
            for direction, dest in (room.get('exits') or {}).items():
                if isinstance(dest, dict):
                    target = dest.get('target', '')
                    dest_live = self.current_level_rooms_world_state.get(target, {}) or {}
                    key_locked = bool(dest.get('locked'))
                    mri_locked = bool(dest_live.get('locked_by_mri') or dest.get('locked_by_mri'))
                    forceable = bool(dest.get('forceable'))
                    if key_locked or mri_locked or forceable:
                        targets.add(direction)
                    continue
                if not isinstance(dest, str):
                    continue
                dest_live = self.current_level_rooms_world_state.get(dest, {}) or {}
                dest_master = self.get_room_data(dest) or {}
                locking = dest_master.get('locking', {}) if isinstance(dest_master.get('locking'), dict) else {}
                key_locked = bool(locking.get('locked'))
                mri_locked = bool(dest_live.get('locked_by_mri') or dest_master.get('locked_by_mri'))
                if key_locked or mri_locked or dest_master.get('forceable'):
                    targets.add(direction)
            # furniture
            for f in (room.get('furniture') or []):
                if not isinstance(f, dict):
                    continue
                if f.get('locked') or f.get('forceable') or f.get('is_breakable'):
                    targets.add(f.get('name', 'Sturdy Object'))
            self.logger.debug(f"_get_targets_force: Force targets: {targets}")
            return sorted([t.replace('_', ' ') for t in targets])
        except Exception as e:
            self.logger.error(f"_get_targets_force: Error: {e}", exc_info=True)
            return []

    def _get_targets_talk(self, current_room_id: str) -> list:
        try:
            room = self.get_room_data(current_room_id) or {}
            npc_names = []
            for n in room.get('npcs', []):
                if isinstance(n, dict) and n.get('name'):
                    npc_names.append(n.get('name'))
                elif isinstance(n, str):
                    npc_names.append(n)
            self.logger.debug(f"_get_targets_talk: Talk targets: {npc_names}")
            return npc_names
        except Exception as e:
            self.logger.error(f"_get_targets_talk: Error: {e}", exc_info=True)
            return []

    def _get_targets_respond(self) -> list:
        try:
            opts = (self.last_dialogue_context or {}).get('options', []) or []
            option_numbers = [str(i + 1) for i in range(len(opts))]
            self.logger.debug(f"_get_targets_respond: Respond targets: {option_numbers}")
            return option_numbers
        except Exception as e:
            self.logger.error(f"_get_targets_respond: Error: {e}", exc_info=True)
            return []

    # --- NEW: Hazard Scripture Reader ---
    def _hazard_examine_text(self, hazard_key: str, target_name: str, room_name: str) -> Optional[str]:
        """
        Pull contextual examine text from a hazard's examine_responses, prioritizing state-specific descriptions.
        """
        try:
            self.logger.debug(f"_hazard_examine_text: hazard_key='{hazard_key}', target_name='{target_name}', room_name='{room_name}'")
            hazards_master = self.resource_manager.get_data('hazards', {})
            h_def = hazards_master.get(hazard_key, {})
            if not h_def:
                self.logger.warning(f"_hazard_examine_text: No hazard definition for '{hazard_key}'")
                return None

            # Get current hazard state
            curr_state = None
            if self.hazard_engine:
                try:
                    curr_state = self.hazard_engine.get_hazard_state(hazard_key, room_name)
                except Exception as e:
                    self.logger.error(f"_hazard_examine_text: Error getting hazard state: {e}", exc_info=True)
            if not curr_state:
                curr_state = h_def.get("initial_state")
            self.logger.debug(f"_hazard_examine_text: Current state for '{hazard_key}' = '{curr_state}'")

            # Check examine_responses for this target
            examine_responses = h_def.get("examine_responses", {})
            target_norm = normalize_text(target_name)
            
            # Find matching response entry (case-insensitive)
            response_entry = None
            for key, value in examine_responses.items():
                if normalize_text(key) == target_norm:
                    response_entry = value
                    break
            
            if not response_entry:
                self.logger.debug(f"_hazard_examine_text: No examine_responses entry for '{target_name}'")
                return None
            
            # PATCH: Prioritize state-specific description
            if isinstance(response_entry, dict):
                # Try state-specific description first
                state_desc = response_entry.get(curr_state)
                if state_desc:
                    self.logger.info(f"_hazard_examine_text: Using state-specific description for '{target_name}' in state '{curr_state}'")
                    return state_desc
                
                # Fall back to base_description
                base_desc = response_entry.get('base_description')
                if base_desc:
                    self.logger.info(f"_hazard_examine_text: Using base_description for '{target_name}'")
                    return base_desc
            
            # Legacy: If response_entry is a plain string (old schema)
            if isinstance(response_entry, str):
                self.logger.info(f"_hazard_examine_text: Using legacy string description for '{target_name}'")
                return response_entry
            
            self.logger.debug(f"_hazard_examine_text: No description found for '{target_name}'")
            return None
            
        except Exception as e:
            self.logger.error(f"_hazard_examine_text: Error: {e}", exc_info=True)
            return None

    def check_game_state_transitions(self) -> bool:
        if getattr(self, 'is_game_over', False):
            return
        """Checks for overarching game state changes like death, victory, or level completion."""
        try:
            self.logger.debug("check_game_state_transitions: Checking game state transitions.")



            # --- Terminal game states ---
            if self.is_game_over:
                # Victory branch
                if self.game_won:
                    has_win_event = any(
                        e.get('event_type') == 'game_won'
                        for e in getattr(self, 'ui_events', [])
                    )
                    if not has_win_event:
                        self.add_ui_event({
                            "event_type": "game_won",
                            "final_score": self.player.get('score', 0),
                            "final_narrative": self.generate_narrative_epilogue()
                        })
                    return True

                # Death branch
                reason = self.player.get('death_reason', 'Death caught up with you.')
                self.logger.info(f"check_game_state_transitions: Game over. Death reason: {reason}")

                # Prevent duplicate game_over events
                has_go_event = any(
                    e.get('event_type') == 'game_over'
                    for e in getattr(self, 'ui_events', [])
                )

                if not has_go_event:
                    self.add_ui_event({
                        "event_type": "game_over",
                        "death_reason": reason,
                        "final_narrative": self.player.get('final_narrative') or self.get_death_narrative(),
                        "flavor_text": self.player.get('flavor_text'),
                        "hide_stats": self.player.get('hide_stats', False),
                        "player_state": self.player.copy(),
                    })
                return True

            # Notify once when exit requirements are met
            if self.check_level_exit_available():
                level_requirements = self.resource_manager.get_data('level_requirements', {})
                current_level = self.player.get('current_level', 1)
                current_level_req = level_requirements.get(str(current_level), {})
                exit_room = current_level_req.get('exit_room', 'UNKNOWN')

                if not self.player.get('notified_requirements_met'):
                    self.add_ui_event({
                        "event_type": "show_popup",
                        "title": "Level Exit Available",
                        "message": (
                            "You have collected all required items! You may now exit the level via the "
                            f"{exit_room.replace('_', ' ').title()}."
                        )
                    })
                    self.player['notified_requirements_met'] = True
                return True

            # Level completion gate
            if self.check_level_completion():
                try:
                    met, _missing = self._requirements_met_for_level_exit()
                except Exception as e:
                    self.logger.error(
                        f"check_game_state_transitions: Error checking requirements for level exit: {e}",
                        exc_info=True
                    )
                    met = False

                if not met and not self.player.get('override_requirements', False):
                    self.logger.warning(
                        "check_game_state_transitions: Level completion requested but requirements are not met; blocking transition."
                    )
                    return False

                try:
                    level_data = self.get_level_completion_data()
                except Exception as e:
                    self.logger.error(
                        f"check_game_state_transitions: Error getting level completion data: {e}",
                        exc_info=True
                    )
                    level_data = {}

                if not isinstance(level_data, dict):
                    level_data = {}

                # Prevent duplicate level_complete events
                has_level_event = any(
                    e.get('event_type') == 'level_complete'
                    for e in getattr(self, 'ui_events', [])
                )

                if not has_level_event:
                    self.add_ui_event({
                        "event_type": "level_complete",
                        "level_name": level_data.get('level_name', 'Unknown Area'),
                        "narrative": level_data.get('narrative', 'You survived this area.'),
                        "score": self.player.get('score', 0),
                        "turns_taken": self.player.get('actions_taken', 0),
                        "evidence_count": len(self.player.get('inventory', [])),
                        "evaded_hazards": self.player.get('evaded_hazards', []),
                        "omens_witnessed": self.player.get('omens_witnessed', 0),
                        "qte_successes": self.player.get('qte_successes', 0),
                        "qte_attempts": self.player.get('qte_attempts', 0),
                        "player_state": self.player.copy(),
                        "next_level_id": level_data.get('next_level_id'),
                        "next_start_room": level_data.get('next_start_room'),
                    })
                return True

            return False

        except Exception as e:
            self.logger.error(f"check_game_state_transitions: Unexpected error: {e}", exc_info=True)
            return False

    def check_level_exit_available(self) -> bool:
        """
        Returns True if all level exit requirements are met (items/evidence found).
        This only triggers a popup, not a level transition.
        Ensures the player is only notified once per level.
        """
        try:
            level_requirements = self.resource_manager.get_data('level_requirements', {})
            current_level = self.player.get('current_level', 1)
            reqs = level_requirements.get(str(current_level), {}) or {}
            
            # --- THE FIX: Don't show the "Items Collected" popup if the level has no items to collect! ---
            if not reqs.get('items_needed') and not reqs.get('evidence_needed'):
                return False

            requirements_met, _ = self._requirements_met_for_level_exit()
            
            # Only return True if requirements are met and player has NOT already been notified
            self.logger.debug(f"check_level_exit_available: requirements_met={requirements_met}, notified={self.player.get('notified_requirements_met', False)}")
            return requirements_met and not self.player.get('notified_requirements_met', False)
            
        except Exception as e:
            self.logger.error(f"check_level_exit_available: Error: {e}", exc_info=True)
            return False

    def check_level_completion(self) -> bool:
        """
        Returns True if the player has actually triggered the level exit (e.g., reached exit room or set flag).
        """
        try:
            # Fallback: Check if player reached an exit trigger
            result = self.player.get('level_complete_flag', False)
            self.logger.debug(f"check_level_completion: level_complete_flag={result}")
            return result
        except Exception as e:
            self.logger.error(f"check_level_completion: Error: {e}", exc_info=True)
            return False

    def _requirements_met_for_level_exit(self) -> Tuple[bool, list]:
        """
        Determine if level exit requirements are met.
        Returns (requirements_met: bool, missing: List[str]).
        Integrates inventory normalization and next-level info.
        """
        try:
            self.logger.debug("_requirements_met_for_level_exit: evaluating requirements")
            level_requirements = self.resource_manager.get_data('level_requirements', {})
            
            # --- THE FIX: Use the String Level ID (e.g., 'level_hub') instead of an integer ---
            current_level_id = self.player.get('current_level')
            reqs = level_requirements.get(current_level_id, {})
            # ---------------------------------------------------------------------------------

            items_needed = list(reqs.get('items_needed', []) or [])
            evidence_needed = list(reqs.get('evidence_needed', []) or [])

            # --- THE FIX: If there are no requirements, then they are technically MET! ---
            if not items_needed and not evidence_needed:
                self.logger.debug("_requirements_met_for_level_exit: no requirements authored; returning (True, [])")
                return True, []

            # Normalize inventory to a set of ids and names
            inv = self.player.get('inventory', {})
            have_norm: Set[str] = set()

            def _norm(s: str) -> str:
                try:
                    return (s or "").strip().lower().replace("’", "'")
                except Exception:
                    return str(s).lower()

            if isinstance(inv, dict):
                for item_id, data in inv.items():
                    have_norm.add(_norm(item_id))
                    if isinstance(data, dict):
                        name = data.get('name') or data.get('display_name')
                        if name:
                            have_norm.add(_norm(name))
                            
            elif isinstance(inv, list):
                for entry in inv:
                    if isinstance(entry, str):
                        have_norm.add(_norm(entry))
                    elif isinstance(entry, dict):
                        # Only trust the internal ID for requirements!
                        item_id = entry.get('id') or entry.get('item_id')
                        if item_id:
                            have_norm.add(_norm(item_id))

            missing: List[str] = []
            for need in items_needed:
                if _norm(need) not in have_norm:
                    missing.append(str(need))
            for need in evidence_needed:
                if _norm(need) not in have_norm:
                    missing.append(str(need))

            met = len(missing) == 0
            self.logger.debug(f"_requirements_met_for_level_exit: met={met}, missing={missing}")
            return met, missing
        except Exception as e:
            self.logger.error(f"_requirements_met_for_level_exit: Error: {e}", exc_info=True)
            return False, []

    def get_level_completion_data(self) -> dict:
        """
        Returns a dict with level completion info, integrating normalized inventory, 
        next-level logic, and dynamic Transition Narratives.
        """
        try:
            level_id = str(self.player.get('current_level', 'level_1'))
            rm = self.resource_manager
            levels_cfg = rm.get_data('level_requirements', {}) or {}
            
            cfg = levels_cfg.get(str(level_id), {}) or {}
            
            # 1. Determine the Next Level
            next_level_id = cfg.get('next_level_id')
            
            # Check if a dynamic transition was triggered (e.g., from the Hub)
            pending = self.player.get('next_level_id')
            if pending:
                next_level_id = pending
                
            if next_level_id is None:
                next_level_id = None
            else:
                next_level_id = str(next_level_id)
            
            next_start = cfg.get('next_level_start_room')
            if next_level_id and not next_start:
                next_cfg = levels_cfg.get(str(next_level_id), {}) or {}
                next_start = next_cfg.get('entry_room') or next_cfg.get('next_level_start_room')

            # 2. Build the Transition Narrative
            narrative = ""
            transition_data = cfg.get('transition_narratives', {})
            
            if transition_data:
                # Try to find the specific narrative for our destination
                narrative_array = transition_data.get(next_level_id)
                
                # If no specific destination matches, use the _default array
                if not narrative_array:
                    narrative_array = transition_data.get("_default")
                    
                # Format and join the array into paragraphs!
                if narrative_array and isinstance(narrative_array, list):
                    narrative = "\n\n".join(narrative_array)
                elif isinstance(narrative_array, str):
                    narrative = narrative_array
                    
            # 3. Fallback to the old 'description' key if no new narratives are found
            if not narrative:
                narrative = cfg.get('description', 'You survived this area. Keep moving.')

            # 4. Inject Dynamic Variables (e.g., {city_name}, {companion_name})
            narrative = self._format_dynamic_text(narrative)

            # 5. Normalize inventory keys for the stats screen
            inv = self.player.get('inventory', {}) or {}
            inv_keys = set()
            if isinstance(inv, dict):
                inv_keys = set(inv.keys())
            elif isinstance(inv, list):
                inv_keys = set(inv)

            result = {
                'level_name': cfg.get('name', f'Level {level_id}'),
                'narrative': narrative,
                'score': self.player.get('score', 0),
                'turns_taken': self.player.get('actions_taken', 0),
                'evidence_count': len(inv_keys),
                'evaded_hazards': self.player.get('evaded_hazards', []),
                'next_level_id': next_level_id,
                'next_start_room': next_start
            }
            
            self.logger.debug(f"get_level_completion_data: result={result}")
            return result
            
        except Exception as e:
            self.logger.error(f"get_level_completion_data: Error: {e}", exc_info=True)
            return {}

    # --- NEW: Command Helpers ---
    def _parse_command(self, raw_input: str) -> Tuple[str, str]:
        """Parses raw string input into a verb and a target (case-insensitive). Injected with robust debugging logic."""
        try:
            self.logger.debug(f"_parse_command called with raw_input='{raw_input}'")
            parts = raw_input.strip().split()
            self.logger.debug(f"_parse_command: Split parts: {parts}")
            if not parts:
                self.logger.warning("_parse_command: No input provided.")
                return None, None
            verb = parts[0].lower()
            target = " ".join(parts[1:]).lower() if len(parts) > 1 else ""
            self.logger.debug(f"_parse_command: Parsed verb='{verb}', target='{target}'")
            return verb, target
        except Exception as e:
            self.logger.error(f"_parse_command: Error parsing command: {e}", exc_info=True)
            return None, None

    def _parse_use_command(self, target_str: str) -> dict:
        """Parses the 'use' command into an item and an optional target."""
        try:
            match = re.match(r"(.+?)\s+on\s+(.+)", target_str, re.IGNORECASE)
            if match:
                return {"item_name": match.group(1).strip(), "target_name": match.group(2).strip()}
            else:
                return {"item_name": target_str.strip(), "target_name": None}
        except Exception as e:
            self.logger.error(f"_parse_use_command: Error parsing use command: {e}", exc_info=True)
            return {"item_name": "", "target_name": None}

    #--- Room Interactable Triggers ---
    def _try_trigger_room_interactable_use(self, target_name: str) -> bool:
        """
        Handles room-level interactable triggers for the 'use' verb.
        Canonical logic mirrors _try_trigger_room_interactable_examine.
        Returns True if a trigger was processed (even if requirements not met).
        """
        try:
            room_id = self.player.get('location')
            if not room_id:
                return False

            level_id = str(self.player.get('current_level', 1))
            rooms_key = f"rooms_level_{level_id}"
            rooms_data = self.resource_manager.get_data(rooms_key, {}) or {}
            room_def = rooms_data.get(room_id) or self.current_level_rooms_world_state.get(room_id, {}) or {}
            triggers = (room_def.get('interactable_triggers') or {})
            if not triggers:
                return False

            tnorm = normalize_text(target_name)
            aliases = {"revolving door", "the revolving door", "door", "exit", "revolving-door"}

            def _match():
                for k in triggers.keys():
                    kn = normalize_text(k)
                    if tnorm == kn:
                        return k
                    if tnorm in aliases and kn in {"revolving door", "door", "exit"}:
                        return k
                    if tnorm in kn or kn in tnorm:
                        return k
                return None

            key = _match()
            if not key:
                return False

            trigger_def = triggers.get(key) or {}
            if trigger_def.get('on_action') != 'use':
                return False

            requirements_met = True
            if trigger_def.get('requires_all_evidence'):
                met, missing = self._requirements_met_for_level_exit()
                self.logger.info(f"_try_trigger_room_interactable_use: requires_all_evidence -> met={met}, missing={missing}")
                requirements_met = met
                if not met:
                    self.add_ui_event({
                        "event_type": "show_popup",
                        "title": "Something's missing",
                        "message": f"You still need: {', '.join(missing)}."
                    })
                    return True

            hazard_change = trigger_def.get('triggers_hazard_state_change') or {}
            hazard_type, target_state = hazard_change.get('hazard_type'), hazard_change.get('target_state')
            if not (hazard_type and target_state):
                return False

            # Only allow hazard state change if requirements are met
            if requirements_met:
                if hasattr(self.hazard_engine, 'set_hazard_state_by_type'):
                    result = self.hazard_engine.set_hazard_state_by_type(room_id, hazard_type, target_state)
                    self.logger.info(f"_try_trigger_room_interactable_use: set '{hazard_type}' at '{room_id}' -> '{target_state}': {result}")
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)
                return True
            return False
        except Exception as e:
            self.logger.error(f"_try_trigger_room_interactable_use: Error: {e}", exc_info=True)
            return False

    def _try_trigger_room_interactable_search(self, target_name: str) -> bool:
        """
        Handles room-level interactable triggers for the 'search' verb.
        Canonical logic mirrors _try_trigger_room_interactable_examine.
        Returns True if a trigger was processed (even if requirements not met).
        """
        try:
            room_id = self.player.get('location')
            if not room_id:
                return False

            level_id = str(self.player.get('current_level', 1))
            rooms_key = f"rooms_level_{level_id}"
            rooms_data = self.resource_manager.get_data(rooms_key, {}) or {}
            room_def = rooms_data.get(room_id) or self.current_level_rooms_world_state.get(room_id, {}) or {}
            triggers = (room_def.get('interactable_triggers') or {})
            if not triggers:
                return False

            tnorm = normalize_text(target_name)
            aliases = {"revolving door", "the revolving door", "door", "exit", "revolving-door"}

            def _match():
                for k in triggers.keys():
                    kn = normalize_text(k)
                    if tnorm == kn:
                        return k
                    if tnorm in aliases and kn in {"revolving door", "door", "exit"}:
                        return k
                    if tnorm in kn or kn in tnorm:
                        return k
                return None

            key = _match()
            if not key:
                return False

            trigger_def = triggers.get(key) or {}
            if trigger_def.get('on_action') != 'search':
                return False

            requirements_met = True
            if trigger_def.get('requires_all_evidence'):
                met, missing = self._requirements_met_for_level_exit()
                self.logger.info(f"_try_trigger_room_interactable_search: requires_all_evidence -> met={met}, missing={missing}")
                requirements_met = met
                if not met:
                    self.add_ui_event({
                        "event_type": "show_popup",
                        "title": "Something's missing",
                        "message": f"You still need: {', '.join(missing)}."
                    })
                    return True

            hazard_change = trigger_def.get('triggers_hazard_state_change') or {}
            hazard_type, target_state = hazard_change.get('hazard_type'), hazard_change.get('target_state')
            if not (hazard_type and target_state):
                return False

            # Only allow hazard state change if requirements are met
            if requirements_met:
                if hasattr(self.hazard_engine, 'set_hazard_state_by_type'):
                    result = self.hazard_engine.set_hazard_state_by_type(room_id, hazard_type, target_state)
                    self.logger.info(f"_try_trigger_room_interactable_search: set '{hazard_type}' at '{room_id}' -> '{target_state}': {result}")
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)
                return True
            return False
        except Exception as e:
            self.logger.error(f"_try_trigger_room_interactable_search: Error: {e}", exc_info=True)
            return False

    def _try_trigger_room_interactable_take(self, target_name: str) -> bool:
        """
        Handles room-level interactable triggers for the 'take' verb.
        Canonical logic mirrors _try_trigger_room_interactable_examine.
        Returns True if a trigger was processed (even if requirements not met).
        """
        try:
            room_id = self.player.get('location')
            if not room_id:
                return False

            level_id = str(self.player.get('current_level', 1))
            rooms_key = f"rooms_level_{level_id}"
            rooms_data = self.resource_manager.get_data(rooms_key, {}) or {}
            room_def = rooms_data.get(room_id) or self.current_level_rooms_world_state.get(room_id, {}) or {}
            triggers = (room_def.get('interactable_triggers') or {})
            if not triggers:
                return False

            tnorm = normalize_text(target_name)
            aliases = {"revolving door", "the revolving door", "door", "exit", "revolving-door"}

            def _match():
                for k in triggers.keys():
                    kn = normalize_text(k)
                    if tnorm == kn:
                        return k
                    if tnorm in aliases and kn in {"revolving door", "door", "exit"}:
                        return k
                    if tnorm in kn or kn in tnorm:
                        return k
                return None

            key = _match()
            if not key:
                return False

            trigger_def = triggers.get(key) or {}
            if trigger_def.get('on_action') != 'take':
                return False

            requirements_met = True
            if trigger_def.get('requires_all_evidence'):
                met, missing = self._requirements_met_for_level_exit()
                self.logger.info(f"_try_trigger_room_interactable_take: requires_all_evidence -> met={met}, missing={missing}")
                requirements_met = met
                if not met:
                    self.add_ui_event({
                        "event_type": "show_popup",
                        "title": "Something's missing",
                        "message": f"You still need: {', '.join(missing)}."
                    })
                    return True

            hazard_change = trigger_def.get('triggers_hazard_state_change') or {}
            hazard_type, target_state = hazard_change.get('hazard_type'), hazard_change.get('target_state')
            if not (hazard_type and target_state):
                return False

            # Only allow hazard state change if requirements are met
            if requirements_met:
                if hasattr(self.hazard_engine, 'set_hazard_state_by_type'):
                    result = self.hazard_engine.set_hazard_state_by_type(room_id, hazard_type, target_state)
                    self.logger.info(f"_try_trigger_room_interactable_take: set '{hazard_type}' at '{room_id}' -> '{target_state}': {result}")
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)
                return True
            return False
        except Exception as e:
            self.logger.error(f"_try_trigger_room_interactable_take: Error: {e}", exc_info=True)
            return False

    def _try_trigger_room_interactable_examine(self, target_name: str) -> bool:
        try:
            room_id = self.player.get('location')
            if not room_id:
                return False

            level_id = str(self.player.get('current_level', 1))
            rooms_key = f"rooms_level_{level_id}"
            rooms_data = self.resource_manager.get_data(rooms_key, {}) or {}
            room_def = rooms_data.get(room_id) or self.current_level_rooms_world_state.get(room_id, {}) or {}
            triggers = (room_def.get('interactable_triggers') or {})
            if not triggers:
                return False

            tnorm = normalize_text(target_name)
            aliases = {"revolving door", "the revolving door", "door", "exit", "revolving-door"}

            def _match():
                for k in triggers.keys():
                    kn = normalize_text(k)
                    if tnorm == kn:
                        return k
                    if tnorm in aliases and kn in {"revolving door", "door", "exit"}:
                        return k
                    if tnorm in kn or kn in tnorm:
                        return k
                return None

            key = _match()
            if not key:
                return False

            trigger_def = triggers.get(key) or {}
            if trigger_def.get('on_action') != 'examine':
                return False

            requirements_met = True
            if trigger_def.get('requires_all_evidence'):
                met, missing = self._requirements_met_for_level_exit()
                self.logger.info(f"_try_trigger_room_interactable_examine: requires_all_evidence -> met={met}, missing={missing}")
                requirements_met = met
                if not met:
                    self.add_ui_event({
                        "event_type": "show_popup",
                        "title": "Something's missing",
                        "message": f"You still need: {', '.join(missing)}."
                    })
                    return True  # BLOCK further processing if requirements not met

            hazard_change = trigger_def.get('triggers_hazard_state_change') or {}
            hazard_type, target_state = hazard_change.get('hazard_type'), hazard_change.get('target_state')
            if not (hazard_type and target_state):
                return False

            # Only allow hazard state change if requirements are met
            if requirements_met:
                if hasattr(self.hazard_engine, 'set_hazard_state_by_type'):
                    result = self.hazard_engine.set_hazard_state_by_type(room_id, hazard_type, target_state)
                    self.logger.info(f"_try_trigger_room_interactable_examine: set '{hazard_type}' at '{room_id}' -> '{target_state}': {result}")
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)
                return True
            return False
        except Exception as e:
            self.logger.error(f"_try_trigger_room_interactable_examine: Error: {e}", exc_info=True)
            return False

    def _is_check_result_node(self, node: dict) -> bool:
        """Detects if the node is a special check_result/ticket_check_result node."""
        text = node.get('text', "")
        return (
            ("$ticket_check_result$" in text or "$check_result$" in text)
            and "on_talk_action" in node
            and "check_for_item" in node["on_talk_action"]
        )

    def _handle_check_result_node(self, npc: dict, node: dict, options_text: str = "") -> dict:
        """Handles special check_result/ticket_check_result dialogue nodes."""
        check = node["on_talk_action"]["check_for_item"]
        item = check.get("item") # This might now be a list!
        success_state = check.get("success_state")
        failure_state = check.get("failure_state")
        failure_text = check.get("failure_text", "You don't have the required items.")
        inventory = self.player.get("inventory", [])

        # --- THE LOGIC FIX ---
        if isinstance(item, list):
            # Check if EVERY item in the list is in inventory
            has_item = all(i in inventory for i in item)
        else:
            # Standard single item check
            has_item = item in inventory
        # ---------------------

        ui_events = []
        if has_item:
            next_node = npc.get("dialogue_states", {}).get(success_state, {})
            text = next_node.get("text", "You may proceed.")
            if "on_talk_action" in next_node:
                self._apply_on_talk_action(next_node["on_talk_action"])
            if "next_state" in next_node:
                self._set_npc_state(npc, next_node["next_state"])
        else:
            next_node = npc.get("dialogue_states", {}).get(failure_state, {})
            text = next_node.get("text", failure_text)
            if "next_state" in next_node:
                self._set_npc_state(npc, next_node["next_state"])

        ui_events.append({
            "event_type": "show_popup",
            "title": npc.get('name', 'NPC'),
            "message": text + (options_text or "")
        })
        self.logger.info(f"_handle_check_result_node: Player {'has' if has_item else 'does not have'} '{item}'.")
        return self._build_response(message=f"[{npc.get('name')}]\n{text}", turn_taken=True, ui_events=ui_events)

    def _build_options_text(self, node: dict) -> str:
        """Builds the options text, FILTERING out invalid options."""
        all_options = node.get('options', []) if node else []
        valid_options = []
        
        inventory = self.player.get('inventory', [])
        
        for opt in all_options:
            # 1. Check 'requires_item' (Single item)
            if 'requires_item' in opt and opt['requires_item'] not in inventory:
                continue 
            
            # 2. Check 'requires_no_items' (List of forbidden items)
            if 'requires_no_items' in opt and any(banned in inventory for banned in opt['requires_no_items']):
                continue

            valid_options.append(opt)
        
        # --- THE FIX: Wipe context if no valid options remain! ---
        if valid_options:
            if not hasattr(self, 'last_dialogue_context') or not self.last_dialogue_context:
                self.last_dialogue_context = {}
            self.last_dialogue_context['options'] = valid_options
            
            return "\n[Options:\n" + "\n".join(f"  {i+1}. {opt.get('text','')}" for i, opt in enumerate(valid_options)) + "\nUse 'respond X' to choose.]"
        else:
            # Wipe the engine's memory of the conversation
            self.last_dialogue_context = {}
            # Force the UI to instantly destroy the contextual buttons!
            self.add_ui_event({"event_type": "refresh_context_actions"})
            return ""

    def _process_on_talk_action(self, npc: dict, node: dict, ui_events: list):
        """Processes on_talk_action for a dialogue node using modular helpers."""
        action = node.get('on_talk_action')
        if not action:
            return

        try:
            # 1. Process Key-Based Actions (These can happen simultaneously)
            self._talk_handle_items(npc, action, ui_events)
            self._talk_handle_qte(action)
            self._talk_handle_hazards(action)
            self._talk_handle_persuasion(npc, action, ui_events)
            self._talk_handle_flags_and_ui(npc, action, ui_events)

            # 2. Process Specific Action Types (Usually mutually exclusive narrative beats)
            action_type = action.get('type')
            if action_type:
                self._talk_handle_action_types(npc, action, action_type, ui_events)

            # 3. Process Game Flow (Terminal actions like winning, losing, or level transition)
            self._talk_handle_game_flow(action, ui_events)

        except StopIteration as e:
            # A helper triggered a terminal game state (like death or level transition). 
            # We catch it here to cleanly halt any further dialogue processing.
            self.logger.info(str(e))


    # -------------------------------------------------------------------------
    # --- Dialogue Action Helpers ---
    # -------------------------------------------------------------------------

    def _talk_handle_items(self, npc: dict, action: dict, ui_events: list):
        """Handles giving specific or random items to the player."""
        # Specific Item
        gi = action.get('gives_item')
        if gi and gi not in self.player.get('inventory', []):
            self.player.setdefault('inventory', []).append(gi)
            self.logger.info(f"NPC '{npc.get('name')}' gave player item '{gi}'.")

        # Random Item Pool
        random_pool = action.get('gives_random_item')
        if random_pool and isinstance(random_pool, list):
            item_key = random.choice(random_pool)
            if item_key not in self.player.get('inventory', []):
                self.player.setdefault('inventory', []).append(item_key)
                self.logger.info(f"NPC '{npc.get('name')}' gave random item '{item_key}'.")
                ui_events.append({
                    "event_type": "show_popup",
                    "title": "Item Received",
                    "message": f"You received a ticket for {item_key.replace('ticket_', '').replace('_', ' ').title()}"
                })

    def _talk_handle_qte(self, action: dict):
        """Starts a QTE directly from dialogue."""
        if action.get('start_qte') and self.qte_engine:
            try:
                qte_type = action['start_qte'].get('qte_type')
                qte_context = action['start_qte'].get('qte_context', {})
                self.qte_engine.start_qte(qte_type, qte_context)
            except Exception as e:
                self.logger.error(f"_talk_handle_qte: Failed to start QTE: {e}", exc_info=True)

    def _talk_handle_hazards(self, action: dict):
        """Triggers a hazard state change from dialogue."""
        hsc = action.get('hazard_state_change')
        if not hsc or not self.hazard_engine:
            return

        try:
            hid = hsc.get('hazard_id')
            # Auto-lookup Hazard ID by Type if missing
            if not hid and hsc.get('hazard_type'):
                hazard_type = hsc.get('hazard_type')
                current_room = self.player.get('location')
                hid = self.hazard_engine.get_hazard_instance_id_by_type(current_room, hazard_type)
                if not hid:
                    self.logger.warning(f"_talk_handle_hazards: No active hazard of type '{hazard_type}' found.")

            if hid and 'target_state' in hsc:
                result = self.hazard_engine.set_hazard_state(hid, hsc['target_state'])
                for c in result.get("consequences", []):
                    self.handle_hazard_consequence(c)
            else:
                self.logger.warning(f"_talk_handle_hazards: Skipping hazard change. Valid ID resolved: {bool(hid)}")
        except Exception as e:
            self.logger.error(f"_talk_handle_hazards: Failed: {e}", exc_info=True)
    
    def _talk_handle_persuasion(self, npc: dict, action: dict, ui_events: list):
        """Handles persuasion rolls, explicit result assignments, and legacy evacuation upgrades."""
        # Persuasion Roll
        persuasion_check = action.get('persuasion_check')
        if persuasion_check and isinstance(persuasion_check, dict):
            success = self._resolve_persuasion_check(npc, persuasion_check)

            # --- THE FIX: Action Chaining ---
            # If the persuasion result should immediately trigger another action,
            # run it now (before dialogue flow continues).
            chained_action = (
                persuasion_check.get('success_action')
                if success else
                persuasion_check.get('failure_action')
            )
            if chained_action and isinstance(chained_action, dict):
                self.logger.info(
                    f"Action Chained: Firing {chained_action.get('type')} immediately."
                )
                # Reuse the same dialogue action pipeline recursively
                self._process_on_talk_action(
                    npc=npc,
                    node={"on_talk_action": chained_action},
                    ui_events=ui_events
                )

        # Forced Result
        persuasion_result = action.get('persuasion_result')
        if persuasion_result:
            npc_name = npc.get('name', 'NPC')
            premonition_states = self.player.setdefault('_premonition_npc_states', {})
            premonition_states[npc_name.lower()] = persuasion_result
            self.logger.info(f"NPC '{npc_name}' persuasion state set to '{persuasion_result}'")

            # Legacy Evacuation Support
            special = action.get('special_effect')
            # Normalize to a list whether it's a string or an array
            special_effects = special if isinstance(special, list) else [special] if special else []
            
            if 'upgrade_unsure_to_convinced' in special_effects:
                upgraded = []
                for name, state in premonition_states.items():
                    if state == 'unsure':
                        premonition_states[name] = 'convinced'
                        upgraded.append(name)
                if upgraded:
                    ui_events.append({
                        "event_type": "show_popup",
                        "title": "Evacuation",
                        "message": f"{', '.join(upgraded).title()} {'leaves' if len(upgraded) == 1 else 'leave'} with the evacuation order."
                    })

    def _talk_handle_flags_and_ui(self, npc: dict, action: dict, ui_events: list):
        """Sets internal interaction flags and displays custom popups."""
        flag = action.get('sets_flag')
        if flag:
            if not hasattr(self, 'interaction_flags'):
                self.interaction_flags = set()
            self.interaction_flags.add(flag)

        pop = action.get('ui_popup_event')
        if pop:
            ui_events.append({
                "event_type": pop.get('type', 'show_popup'),
                "title": pop.get('title', npc.get('name', 'NPC')),
                "message": pop.get('message', ''),
            })

    def _talk_handle_action_types(self, npc: dict, action: dict, action_type: str, ui_events: list):
        """Dispatcher for specific, highly custom narrative logic triggers."""
        
        if action_type == 'trigger_commotion_death':
            self._action_commotion_death()
            
        elif action_type == 'trigger_mass_evacuation':
            self._action_mass_evacuation()
            
        elif action_type == 'ask_who_is_next':
            self._action_ask_who_is_next(npc)
            
        elif action_type == 'attempt_recruit':
            self._action_attempt_recruit(npc, action)
            
        elif action_type == 'evacuate_room':
            self._action_evacuate_room(npc)

        elif action_type == 'grant_finale_bonus':
            self._apply_finale_bonus(action)


    # -------------------------------------------------------------------------
    # --- Dialogue Action Sub-Helpers ---
    # -------------------------------------------------------------------------

    #--- NPC Dialogue Condition Helpers
    def _resolve_npc_dialogue_entry_state(self, npc: dict, room_id: str, is_first_meeting: bool = False) -> str:
        """
        The Master State Funnel for dynamic dialogue.
        Prioritizes immediate survival, active conversations, and then global Act structure.
        """
        npc_id = npc.get('id', npc.get('name', '')).lower()
        archetype = npc.get('archetype', 'bystander')
        dialogue_states = npc.get('dialogue_states', {})
        
        current_act = self.player.get('current_act', 'act_1_survival')
        room_data = getattr(self, 'get_room_data', lambda x: {})(room_id) or {}

        # --- GET THE ACTUAL SAVED STATE ---
        # Fetch the state from global memory 
        saved_state = None
        if hasattr(self, '_get_npc_state'):
            saved_state = self._get_npc_state(npc)
        if not saved_state:
            saved_state = npc.get('current_state') or npc.get('initial_state', 'greeting')
        # -------------------------------------------

        # ---------------------------------------------------------
        # PRIORITY 1: IMMEDIATE DANGER
        # ---------------------------------------------------------
        if room_data.get('hazards_present') and 'panic_state' in dialogue_states:
            return 'panic_state'

        # ---------------------------------------------------------
        # PRIORITY 2: ACTIVE CONVERSATION 
        # ---------------------------------------------------------
        base_entry_states = [
            'greeting', 'initial_state', 
            'act_1_survival', 'act_2_investigation', 'act_3_hunted', 'act_4_the_plan',
            'companion_state', 'workplace_target_state', 'dynamic_funnel_state',
            'greeting_oblivious', 'greeting_uneasy', 'greeting_terrified'
        ]
        
        # If the saved state is a deep node (like 'persuasion_check_fatalist'), honor it!
        if saved_state and saved_state not in base_entry_states:
            return saved_state

        # ---------------------------------------------------------
        # PRIORITY 3: COMPANION OVERRIDE
        # ---------------------------------------------------------
        if npc_id == self.player.get('current_companion', '').lower():
            if 'companion_state' in dialogue_states:
                return 'companion_state'

        # ---------------------------------------------------------
        # PRIORITY 4: WORKPLACE / TARGET SPECIFIC (ACT 3)
        # ---------------------------------------------------------
        if npc_id == self.player.get('active_death_target', '').lower():
            workplace_data = self.player.get('npc_workplaces', {}).get(npc_id, {})
            workplace_level_id = workplace_data.get('level_id', '')
            is_at_work = bool(workplace_level_id and workplace_level_id in room_id.lower())
            
            archetype_data = getattr(self, 'resource_manager', None)
            if archetype_data:
                arch_dict = archetype_data.get_data('npcs', {}).get('archetype_dialogue', {})
                
                # Fetch the Act 3 block, then grab the 'generic_target' dictionary inside it
                act_3_block = arch_dict.get('act_3_hunted_workplace', {}) if is_at_work else arch_dict.get('act_3_hunted_public', {})
                target_dialogue_pool = act_3_block.get('generic_target', {})
                
                if target_dialogue_pool:
                    dialogue_states.update(target_dialogue_pool)
                    
                    # Determine greeting based on deaths
                    deaths_list = self.player.get('deaths_list', [])
                    roster = self.player.get('npc_status', {})
                    dead_count = sum(1 for n in deaths_list if n.lower() != 'player' and roster.get(n.lower()) == 'dead')
                    
                    if dead_count == 0: return 'greeting_oblivious'
                    elif dead_count == 1: return 'greeting_uneasy'
                    else: return 'greeting_terrified'

        # ---------------------------------------------------------
        # PRIORITY 5: SPECIFIC NPC ACT DIALOGUE
        # ---------------------------------------------------------
        if current_act in dialogue_states:
            return current_act

        # ---------------------------------------------------------
        # PRIORITY 6: DYNAMIC ARCHETYPE FALLBACK
        # ---------------------------------------------------------
        archetype_data = getattr(self, 'resource_manager', None)
        if archetype_data:
            arch_dict = archetype_data.get_data('npcs', {}).get('archetype_dialogue', {})
            act_pool = arch_dict.get(current_act, {})
            
            if archetype in act_pool:
                archetype_act_nodes = act_pool[archetype]
                
                # THE FIX: Intercept First Meetings!
                # If they haven't met the player, and this Act has a 'first_meeting' node written for it, use it!
                if is_first_meeting and 'first_meeting' in archetype_act_nodes:
                    dialogue_states['first_meeting'] = archetype_act_nodes['first_meeting']
                    
                    # ALSO load the standard_loop in case the first_meeting transitions directly into it
                    if 'standard_loop' in archetype_act_nodes:
                        dialogue_states['standard_loop'] = archetype_act_nodes['standard_loop']
                        
                    return 'first_meeting'

                # Otherwise, just pull the standard loop (or the root text if no loops are defined)
                if 'standard_loop' in archetype_act_nodes:
                    dialogue_states['standard_loop'] = archetype_act_nodes['standard_loop']
                    return 'standard_loop'
                else:
                    # Fallback for simple structures that just put text directly in the Act
                    dialogue_states[current_act] = archetype_act_nodes
                    return current_act
                
        # ---------------------------------------------------------
        # PRIORITY 7: JSON FALLBACK & CONDITIONAL OVERRIDES
        # ---------------------------------------------------------
        conds = npc.get('conditional_entry_state', [])
        for cond in conds:
            condition = cond.get('condition', {})
            if hasattr(self, '_npc_condition_met') and self._npc_condition_met(condition, room_id=room_id):
                return cond['state']
                
        # Return the actual saved state instead of hardcoded 'greeting'
        return saved_state

    def _move_companion_to_next_room(self, destination):
        """
        Moves the companion NPC to the specified destination room.
        """
        companion_id = self.player.get('companion_id')
        
        # If the player is flying solo, abort immediately!
        if not companion_id:
            return 
        
        # Try player-local NPCs first
        npcs = self.player.get('npcs', {})
        if companion_id in npcs:
            npcs[companion_id]['location'] = destination
            self.logger.info(f"Moved companion '{companion_id}' to {destination}")
            return
            
        # Try global NPCs if present
        if hasattr(self, 'npcs') and companion_id in self.npcs:
            self.npcs[companion_id]['location'] = destination
            self.logger.info(f"Moved global companion '{companion_id}' to {destination}")
            return
        self.logger.warning(f"Companion '{companion_id}' not found; cannot move to {destination}")

    def _npc_condition_met(self, condition: dict, room_id: str = None) -> bool:
        """
        Evaluates if a given condition is met.
        Supports 'or' (recursive), hazard state, and hazard activation.
        Optionally takes a room_id for context (defaults to player's location).
        """
        if "or" in condition:
            for sub in condition["or"]:
                if self._npc_condition_met(sub, room_id=room_id):
                    return True
            return False
        # Use provided room_id or fallback to player's location
        room = room_id or self.player.get('location')
        # Check for hazard state
        for k, v in condition.items():
            if k.endswith("_state"):
                hazard_type = k[:-6]
                for hid, hazard in self.hazard_engine.active_hazards.items():
                    if hazard.get('type') == hazard_type and hazard.get('location') == room:
                        if hazard.get('state') == v:
                            return True
            elif k.endswith("_activated"):
                hazard_type = k[:-10]
                for hid, hazard in self.hazard_engine.active_hazards.items():
                    if hazard.get('type') == hazard_type and hazard.get('location') == room:
                        if hazard.get('started_by_player'):
                            return True
        return False

    def _action_commotion_death(self):
        """Kills the player for causing a fatal panic."""
        disaster = self.player.get('intro_disaster', {})
        disaster_name = self._format_dynamic_text(disaster.get('name', 'the disaster'))
        
        # --- Fetch the visionary's name from the player state ---
        visionary = self.player.get('premonition_visionary', 'the visionary')
        
        narrative = (
            f"Instead of running, everyone froze to watch your meltdown. "
            f"By the time they realized you weren't the real threat, it was too late. "
            f"{disaster_name.capitalize()} claimed everyone around, including you, just like {visionary} said would happen."
        )
        
        # --- Save the data directly to the player state! ---
        self.player['suppress_death_details'] = True
        self.player['death_reason'] = "Good job, you caused a fatal distraction.\nDeath thanks you for saving it the trouble of cleaning up."
        self.player['final_narrative'] = narrative
        self.player['flavor_text'] = "[color=ff0000]Panic is just as deadly as the disaster.\nSometimes a clear head prevails, other times a head just rolls.[/color]"
        self.player['hide_stats'] = True
        
        self.is_game_over = True

    def _action_mass_evacuation(self):
        """Ends Level 0 early, saving everyone and putting them all on Death's List."""
        self.logger.info("Mass evacuation triggered! Ending Premonition early with maximum survivors.")
        
        # Flag the level as complete so the engine stops accepting normal commands
        self.player['level_complete_flag'] = True
        
        narrative = (
            "The crowd surges through the exits, a chaotic stampede of panicked bodies.\n\n"
            "Seconds later, the air is shattered by the deafening roar of the disaster. "
            "You watch from safety as the destruction unfolds exactly as you knew it would.\n\n"
            "You saved them. You saved all of them.\n\n"
            "But as the sirens begin to wail in the distance, a cold chill crawls up your spine. "
            "You weren't supposed to survive. None of you were. And Death doesn't like to be cheated."
        )
        
        self.add_ui_event({
            "event_type": "level_complete",
            "title": "Cheating Death",
            "narrative": narrative,
            "next_level_id": "level_1"
        })


    def _action_ask_who_is_next(self, npc: dict):
        """Reveals the exact order of Death's Design."""
        deaths_list = npc.get('deaths_list_knowledge', [])
        
        if hasattr(self, 'set_player_flag'):
            self.set_player_flag("learned_deaths_list", True)
        else:
            self.player['learned_deaths_list'] = True

        if not deaths_list:
            self.add_ui_event({"event_type": "show_message", "message": "'I... the memory is slipping. Everything is a blur,' they stammer."})
            return

        current_index = self.player.get('deaths_list_index', 0)
        list_text = "[b]THE PREMONITION ORDER:[/b]\n\n"
        
        for i, name in enumerate(deaths_list):
            display_name = name.title() if name != 'player' else "YOU"
            if i < current_index:
                list_text += f"[s][color=555555]{i+1}. {display_name}[/color][/s]\n"
            elif i == current_index:
                list_text += f"[color=ff0000]{i+1}. {display_name}  <-- NEXT[/color]\n"
            else:
                list_text += f"{i+1}. {display_name}\n"

        self.add_ui_event({"event_type": "show_popup", "title": "Death's Design", "message": list_text})
        self.add_ui_event({"event_type": "show_message", "message": "If you recall the order correctly, then the list should look like this."})


    def _action_attempt_recruit(self, npc: dict, action: dict):
        """RNG check to see if an NPC joins your party or flees."""
        import random
        npc_name = npc.get('name', 'NPC')
        roll = random.random()
        
        if roll > 0.5:
            # SUCCESS
            self.player.setdefault('companions', []).append(npc_name)
            self.add_ui_event({"event_type": "show_message", "message": f"\n[color=00ff00]{npc_name} has joined your group.[/color]\n"})
            npc['dialogue_states']['current'] = action.get('success_state', 'following')
        else:
            # FAILURE
            self.add_ui_event({"event_type": "show_message", "message": f"\n[color=ffaa00]{npc_name} shakes their head, panicked, and runs off to find another way out.[/color]\n"})
            
            depart_flag = action.get('depart_flag', f"{npc_name.lower()}_fled_to_later_level")
            if hasattr(self, 'set_player_flag'):
                self.set_player_flag(depart_flag, True)
            else:
                self.player[depart_flag] = True
            
            # Despawn them from the room instantly
            room_id = self.player.get('location')
            room_data = self.get_room_data(room_id)
            if room_data and 'npcs' in room_data:
                room_data['npcs'] = [n for n in room_data['npcs'] if (n.get('name') if isinstance(n, dict) else n).lower() != npc_name.lower()]


    def _action_evacuate_room(self, npc: dict):
        """Allows an authority figure to clear the current room of bystanders."""
        room_id = self.player.get('location')
        room_data = self.get_room_data(room_id)
        evacuated_npcs = []
        active_companions = [c.lower() for c in self.player.get('companions', [])]
        
        npcs_to_keep = []
        
        for room_npc in room_data.get('npcs', []):
            r_npc_name = room_npc.get('name', '') if isinstance(room_npc, dict) else room_npc
            
            # If they are NOT the authority figure and NOT a companion, evacuate them!
            if r_npc_name.lower() != npc.get('name', '').lower() and r_npc_name.lower() not in active_companions:
                evacuated_npcs.append(r_npc_name.title())
                # DO NOT append them to npcs_to_keep, effectively deleting them from the room!
            else:
                npcs_to_keep.append(room_npc)
                
        if evacuated_npcs:
            # Update the room data to only contain the NPCs who stayed
            room_data['npcs'] = npcs_to_keep
            
            evac_str = ", ".join(evacuated_npcs)
            self.add_ui_event({
                "event_type": "show_popup",
                "title": "Evacuation Ordered",
                "message": f"{npc.get('name')} takes charge! They successfully direct {evac_str} out of the immediate danger zone.",
                "vfx_hint": "success"
            })
            self.add_ui_event({
                "event_type": "show_message",
                "message": f"[color=00ff00]{evac_str} followed instructions and evacuated.[/color]"
            })
            self.add_ui_event({"event_type": "refresh_map"})

    def _talk_handle_game_flow(self, action: dict, ui_events: list):
        """Handles terminal triggers like winning, losing, or changing levels."""
        
        # --- FINALE QTE INTERCEPTOR ---
        action_effect = action.get("action_effect", "")
        if action_effect.startswith("trigger_finale_"):
            
            # 1. Check Items — compare against both display names AND item keys
            req_items = action.get("requires_items", [])
            inventory_keys = [str(i).lower() for i in self.player.get('inventory', [])]
            items_master = self.resource_manager.get_data('items', {})

            # Build a lookup: normalized display name -> item key
            display_to_key = {}
            for ikey, idata in items_master.items():
                display_to_key[normalize_text(idata.get('name', ikey))] = ikey
                display_to_key[normalize_text(ikey)] = ikey

            missing = []
            for req in req_items:
                req_norm = normalize_text(req)
                # First try direct key match
                if req_norm.replace(' ', '_') in inventory_keys:
                    continue
                if req_norm in inventory_keys:
                    continue
                # Then try display name -> key lookup
                mapped_key = display_to_key.get(req_norm)
                if mapped_key and mapped_key.lower() in inventory_keys:
                    continue
                missing.append(req)

            if missing:
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "Missing Items",
                    "message": f"You are missing: {', '.join(missing)}"
                })
                return

            # --- THE FIX: Start the Finale Gauntlet! ---
            # If they have all the items, kick off the QTE chain!
            self.add_ui_event({"event_type": "destroy_info_popup"}) # Clear the dialogue box
            self._start_finale_qte_chain(action_effect)
            return
            # -------------------------------------------

        if action.get("trigger_win_screen"):
            self.game_won = True
            self.is_game_over = True
            self.add_ui_event({
                "event_type": "game_won",
                "final_score": self.player.get('score', 0)
            })

        if action.get("trigger_lose_screen"):
            self.is_game_over = True
            self.player['death_reason'] = action.get("death_reason", "You failed to escape.")
            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": self.player['death_reason'],
                "final_narrative": self.get_death_narrative(),
                "player_state": self.player.copy(),
            })

        tlt = action.get('trigger_level_transition')
        if tlt is not None:
            next_level_id = None
            if isinstance(tlt, dict):
                next_level_id = tlt.get('next_level_id')
            self.player['level_complete_flag'] = True
            if next_level_id:
                self.player['next_level_id'] = next_level_id
            self.add_ui_event({
                "event_type": "level_complete",
                "title": "Level Complete",
                "message": "You have completed this area.",
                "next_level_id": next_level_id
            })

    def _resolve_persuasion_check(self, npc: dict, check_data: dict) -> bool:
        """
        Roll a persuasion check for an NPC during the premonition level.
        
        Args:
            npc: The NPC dict (with name, role, etc.)
            check_data: The persuasion_check dict from on_talk_action, containing:
                - base_difficulty (float, 0.0–1.0)
                - success_state (str)
                - failure_state (str)
                - success_message (str)
                - failure_message (str)
                - class_bonus_override (optional dict of class -> bonus)
                - auto_succeed_if_flag (optional str — succeed if this flag is set)
        
        Returns:
            True if persuasion succeeded, False if failed.
        """
        import random
    
        npc_name = npc.get('name', 'NPC')
    
        # Auto-succeed check
        auto_flag = check_data.get('auto_succeed_if_flag')
        if auto_flag and auto_flag in getattr(self, 'interaction_flags', set()):
            self.logger.info(
                f"Persuasion auto-succeed: '{npc_name}' (flag '{auto_flag}' is set)")
            target_state = check_data.get('success_state')
            if target_state:
                self._set_npc_state(npc, target_state)
            return True
    
        # Base difficulty
        difficulty = float(check_data.get('base_difficulty', 0.5))
    
        # Character class bonus
        char_class = self.player.get('character_class', '')
        class_overrides = check_data.get('class_bonus_override', {})
        default_bonuses = {
            'Medium': -0.25,
            'Detective': -0.1,
            'EMT': -0.15,
            'Athlete': 0.0,
            # Visionary: authority figures and skeptics won't believe you —
            # unless you have physical evidence (auto_succeed_if_flag handles that path)
            'Visionary': 0.0,  # base; role-specific penalty applied below
        }

        # ── Visionary role-specific persuasion penalty ───────────────────────
        if char_class == 'Visionary':
            npc_role = npc.get('role', '')
            if npc_role in ('authority_figure', 'skeptic'):
                difficulty += 0.35   # hard without evidence
            elif npc_role == 'friend':
                difficulty -= 0.10   # friend is more inclined to believe
            # 'premonition_death_knowledge' flag means they just lived the disaster
            # and evidence items count as automatic success (handled by auto_succeed_if_flag)
        if char_class in class_overrides:
            difficulty += class_overrides[char_class]
        elif char_class in default_bonuses:
            difficulty += default_bonuses[char_class]
    
        # Social proof bonus: each previously convinced NPC reduces difficulty
        premonition_npcs = self.player.get('_premonition_npc_states', {})
        convinced_count = sum(
            1 for state in premonition_npcs.values() if state == 'convinced')
        social_proof_bonus = convinced_count * 0.05
        difficulty -= social_proof_bonus
    
        # Clamp difficulty
        difficulty = max(0.05, min(0.95, difficulty))
    
        # Roll
        roll = random.random()
        success = roll >= difficulty
    
        self.logger.info(
            f"Persuasion check for '{npc_name}': "
            f"difficulty={difficulty:.2f} (base={check_data.get('base_difficulty')}, "
            f"class={char_class}, social_proof={convinced_count}), "
            f"roll={roll:.2f}, result={'SUCCESS' if success else 'FAILURE'}")
    
        if success:
            target_state = check_data.get('success_state')
            message = check_data.get('success_message', 'They agree to leave.')
        else:
            target_state = check_data.get('failure_state')
            message = check_data.get('failure_message', 'They refuse.')
    
        # Set NPC state
        if target_state:
            self._set_npc_state(npc, target_state)
            
            # --- THE FIX: Auto-trigger the outcome's persuasion result! ---
            # This ensures they are instantly locked in as 'convinced' or 'unsure'
            # without requiring the player to issue a second 'talk' command.
            target_node = npc.get('dialogue_states', {}).get(target_state, {})
            p_result = target_node.get('on_talk_action', {}).get('persuasion_result')
            
            if p_result:
                npc_name = npc.get('name', 'NPC')
                self.player.setdefault('_premonition_npc_states', {})[npc_name.lower()] = p_result
                self.logger.info(f"Auto-applied persuasion result '{p_result}' from '{target_state}'")
    
        # Show result message as a popup
        self.add_ui_event({
            "event_type": "show_popup",
            "title": npc_name,
            "message": message,
            "on_close_command": f"talk {npc_name}"
        })
    
        return success
    
    def _enqueue_qte(self, consequence: dict):
        """Routes the QTE natively to the QTE Engine to merge blueprints and inject into UI."""
        qte_type = consequence.get('qte_type', 'button_mash')
        context = consequence.get('qte_context', {})
        
        self.logger.info(f"_enqueue_qte: Routing '{qte_type}' to QTE_Engine.")
        
        if hasattr(self, 'qte_engine') and self.qte_engine:
            # The QTE engine handles the JSON blueprint merging (fixing the 'word' vs 'mash' bug)
            # and natively injects the correctly formatted event into the UI queue!
            self.qte_engine.start_qte(qte_type, context)
        else:
            self.logger.error("Cannot start QTE: QTE_Engine is not attached.")

    def _apply_on_talk_action(self, action: dict):
        """
        Handles on_talk_action triggers.
        Includes 'trigger_command' to allow dialogue to execute ANY player action (future-proofing).
        """
        if not action:
            return

        # 1. State & Flags (Direct manipulation)
        if "sets_interaction_flag" in action:
            self.set_interaction_flag(action["sets_interaction_flag"])
        
        if "set_player_flag" in action:
            self.set_player_flag(action["set_player_flag"], True)

        if "set_npc_behavior" in action:
            new_behavior = action["set_npc_behavior"]
            self.player['companion_behavior'] = new_behavior
            self.logger.info(f"_apply_on_talk_action: NPC behavior updated to: {new_behavior}")
            self.add_ui_event({
                "event_type": "show_popup",
                "title": "Companion Update",
                "message": "Your friend is now following you."
            })

        # 2. Legacy / Convenience Wrappers
        special = action.get('special_effect')
        # Normalize to a list
        special_effects = special if isinstance(special, list) else [special] if special else []

        def _extract_npc_name(source):
            if isinstance(source, dict):
                name = source.get('name')
                if isinstance(name, str) and name.strip():
                    return name.strip()
                return None
            if isinstance(source, str) and source.strip():
                return source.strip()
            return None

        def _resolve_current_npc_name():
            # 1) Prefer explicit NPC identifiers on the action payload.
            for key in ("npc", "target_npc", "npc_name"):
                resolved = _extract_npc_name(action.get(key))
                if resolved:
                    return resolved

            # 2) Fallback to active dialogue context.
            ctx = getattr(self, 'last_dialogue_context', {})
            if isinstance(ctx, dict):
                resolved = _extract_npc_name(ctx.get('npc'))
                if resolved:
                    return resolved
                resolved = _extract_npc_name(ctx.get('npc_name'))
                if resolved:
                    return resolved

            # 3) Final fallback: current interacted NPC on player state.
            return _extract_npc_name(self.player.get('current_interacted_npc'))

        # --- THE FIX: Companion Recruitment ---
        if "recruit_companion" in special_effects:
            npc_name = _resolve_current_npc_name()

            # 3. Apply the recruit if we successfully grabbed a name
            if npc_name:
                companions = self.player.setdefault('companions', [])
                if npc_name not in companions:
                    companions.append(npc_name)
                    if getattr(self, 'achievements_system', None):
                        self.achievements_system.unlock("accomplice")

                self.add_ui_event({
                    "event_type": "show_message",
                    "message": f"\n[color=00ff00]{npc_name} has joined your group.[/color]\n"
                })
                self.logger.info(f"_apply_on_talk_action: Successfully recruited '{npc_name}'.")
            else:
                self.logger.warning(
                    "_apply_on_talk_action: recruit_companion fired, but NPC name could not be resolved "
                    "from action payload, last_dialogue_context, or current_interacted_npc."
                )
        # --------------------------------------

        # --- THE FIX: The Despawn Hook (Array Compatible) ---
        if "despawn_npc" in special_effects:
            npc_name = _resolve_current_npc_name()
                
            if npc_name:
                room_id = self.player.get('location')
                room_data = self.get_room_data(room_id)
                
                if room_data and 'npcs' in room_data:
                    # Filter the NPC out of the room's array
                    room_data['npcs'] = [n for n in room_data['npcs'] if (n.get('name') if isinstance(n, dict) else n).lower() != npc_name.lower()]
                    
                self.add_ui_event({
                    "event_type": "show_message",
                    "message": f"\n[color=aaaaaa]{npc_name} hurries away, leaving the area.[/color]\n"
                })
                self.add_ui_event({"event_type": "refresh_map"})
        # ----------------------------------

        # 3. Item Rewards
        if "reward_item" in action or "gives_item" in action:
            item_name = action.get("reward_item", {}).get("item_name") or action.get("gives_item")
            if item_name:
                if item_name not in self.player.get('inventory', []):
                    self.player.setdefault('inventory', []).append(item_name)
                    items_master = self.resource_manager.get_data('items', {})
                    item_data = items_master.get(item_name, {})
                    self.logger.info(f"Rewarded item '{item_name}' via dialogue.")
                    self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "Item Received",
                    "message": f"You received: {item_data.get('name', item_name)}."
                    })

        if "trigger_examine" in action:
            # Syntactic sugar for trigger_command: "examine X"
            target = action["trigger_examine"]
            self._execute_triggered_command(f"examine {target}")

        # 4. THE MASTER KEY: Trigger Any Command
        if "trigger_command" in action:
            command_str = action["trigger_command"]
            self._execute_triggered_command(command_str)

        # 5. Specific Engine Calls
        if "start_qte" in action and self.qte_engine:
            qte_data = action["start_qte"]
            self.qte_engine.start_qte(qte_data.get("qte_type"), qte_data.get("qte_context", {}))

    def _execute_triggered_command(self, command_str: str):
        """
        Helper to execute a command as if the player typed it, 
        routing output to the log instead of the main response flow.
        """
        self.logger.info(f"Dialogue triggering command: '{command_str}'")
        
        # 1. Echo the command to the log so the player sees context
        self.add_ui_event({
            "event_type": "show_message",
            "message": f"\n[color=888888]> {command_str}[/color]"
        })

        # 2. Process via main engine logic
        # We do NOT use the return value directly because we are likely inside a dialogue loop.
        # Instead, we extract the side effects (UI events, messages) and queue them.
        result = self.process_player_input(command_str)
        
        # 3. Route Text Output
        if result.get('messages'):
            full_text = "\n".join(result['messages'])
            self.add_ui_event({
                "event_type": "show_message",
                "message": full_text
            })

        # 4. Route UI Events (Popups, hazards, map updates)
        # We skip 'refresh_map' if it's redundant, but keeping it is safer.
        if result.get('ui_events'):
            for ev in result['ui_events']:
                self.add_ui_event(ev)

    def _get_terminal_hazard_description(self):
        """
        Returns the canonical death message for the terminal hazard state.
        """
        # --- FIX: Bulletproof bypass for narrative-only deaths ---
        if hasattr(self, "player") and self.player.get("suppress_death_details"):
            return None
            
        hazard_id = getattr(self, "last_terminal_hazard_id", None)
        if not hazard_id and hasattr(self, "player"):
            hazard_id = self.player.get("death_hazard_id")
        if not hazard_id:
            return None

        hazard = self.hazard_engine.active_hazards.get(hazard_id) if self.hazard_engine else None
        if not hazard:
            return None

        master_data = hazard.get("master_data", {})
        state = self.player.get("death_hazard_state") or hazard.get("state")
        state_def = master_data.get("states", {}).get(state, {})

        # --- THE FIX ---
        parts = []
        
        # 1. The Setup (e.g., "Your phone flies across the room...")
        if "description" in state_def:
            parts.append(state_def["description"])

        # 2. The Aftermath (e.g., "You are bisected...")
        if "death_message" in state_def:
            parts.append(state_def["death_message"])

        return "\n\n".join(parts)

    def _compose_disaster_line(self) -> str:
        """
        Build: 'Your story began with {disaster}, {death_narrative}'
        Falls back gracefully if data is missing.
        PATCHED: Returns text wrapped in red ('error') color.
        """
        try:
            intro = self.player.get('intro_disaster', {}) or {}
            disaster_key = (intro.get('event_description') or "").strip()
            if not disaster_key:
                return ""

            disasters_master = self.resource_manager.get_data('disasters', {}) or {}
            dn = (disasters_master.get(disaster_key, {}) or {}).get('death_narrative', "").strip()

            # Start the sentence
            line = f"Your story began with {disaster_key}"
            if dn:
                # Ensure natural flow like ', but ...' or ', and ...'
                if not dn.startswith((",", ";", ":")):
                    line += ", "
                line += dn.lstrip()
            else:
                line += "."

            # --- FIX START: Color it Red ---
            return color_text(line.strip(), 'error', self.resource_manager)
            # --- FIX END ---
            
        except Exception:
            return ""

    def get_death_narrative(self) -> str:
        """
        Builds the lose-screen narrative, combining the disaster line and
        any terminal hazard description that caused death. Removes generic taglines.
        """
        narrative_parts = []

        # 1) Terminal hazard death message (prefer death_message)
        hazard_desc = self._get_terminal_hazard_description()
        if hazard_desc:
            narrative_parts.append(hazard_desc)

        # 2) Disaster opening line + disaster-specific death_narrative
        disaster_line = self._compose_disaster_line()
        if disaster_line:
            narrative_parts.append(disaster_line)

        # 3) Canonical stats block: player's live, cumulative stats
        score = int(self.player.get('score', 0))
        turns_taken = int(self.player.get('actions_taken', 0))
        fear_current = float(self.player.get('fear', 0.0))

        omens_seen = 0
        try:
            if self.death_ai and hasattr(self.death_ai, 'omens_seen'):
                omens_seen = int(self.death_ai.omens_seen)
        except Exception:
            pass

        # QTE stats if available
        qte_sr_pct = None
        qte_succ = None
        qte_att = None
        try:
            pbp = getattr(self.death_ai, 'player_behavior_patterns', None) or {}
            qte_sr = float(pbp.get('qte_success_rate', 0.0))
            qte_sr_pct = int(round(qte_sr * 100))
            qte_succ = int(pbp.get('qte_successes', 0))
            qte_att = int(pbp.get('qte_attempts', 0))
        except Exception:
            pass

        evaded = self.player.get('evaded_hazards', []) or []

        stats_lines = []
        stats_lines.append(f"Final Score: {score}")
        stats_lines.append(f"Fear Level (final): {fear_current:.2f}")
        stats_lines.append(f"Omens Witnessed: {omens_seen}")
        if qte_sr_pct is not None:
            if qte_succ is not None and qte_att is not None:
                stats_lines.append(f"QTE Success Rate: {qte_sr_pct}% ({qte_succ}/{qte_att})")
            else:
                stats_lines.append(f"QTE Success Rate: {qte_sr_pct}%")
        stats_lines.append(f"Hazards Evaded: {len(evaded)}")
        if evaded:
            stats_lines.append("Hazards Encountered: " + ", ".join(e.get("name", str(e)) for e in evaded))
        narrative_parts.append("\n".join(stats_lines))

        # Join with blank lines to render as separate paragraphs in UI
        return "\n\n".join([p for p in narrative_parts if p]).strip()

    # --- A Method to Record Memories ---
    def set_interaction_flag(self, flag_name: str):
        """Adds a new flag to the set of recorded interactions. Injected with robust debugging logic."""
        if flag_name not in self.interaction_flags:
            self.logger.info(f"Interaction flag set: '{flag_name}'")
            self.interaction_flags.add(flag_name)
        else:
            self.logger.debug(f"Interaction flag '{flag_name}' already set.")

    # --- Entity Finding Helpers ---
    def get_room_data(self, room_name: str) -> Optional[dict]:
        """
        Returns the live data dictionary for a room, injecting the companion NPC
        only if their location matches the current room.
        """
        room = self.current_level_rooms_world_state.get(room_name)
        if room is None:
            self.logger.debug(f"get_room_data: No data found for room '{room_name}'.")
            return None
        else:
            self.logger.debug(f"get_room_data: Retrieved data for room '{room_name}'.")

        # Inject companion NPC only if their location matches this room
        companion_npc = self._get_companion_npc()
        if companion_npc and self.player.get('companion_location') == room_name:
            # Avoid duplicate insertion if already present
            npcs = room.setdefault('npcs', [])
            if not any(npc.get('id') == companion_npc.get('id') for npc in npcs):
                npcs.append(companion_npc)
        return room

    def _get_companion_npc(self):
        # Always pull the latest master data for the companion
        friend_npc = self.resource_manager.get_data('npcs', {}).get('friend', {}).copy()
        if not friend_npc:
            # fallback if not found
            return {
                "id": "companion_friend",
                "name": "Your Friend",
                "description": "Your loyal movie companion.",
                "examinable": True,
                "initial_state": "default",
                "dialogue_states": {
                    "default": {
                        "text": "Ready for the movie?",
                        "options": [
                            {"text": "Let's go!", "target_state": "default"}
                        ]
                    }
                }
            }
        # Set the live state for this instance
        friend_npc['id'] = "companion_friend"
        friend_npc['initial_state'] = self.player.get('friend_dialogue_state', 'default')
        return friend_npc

    def on_hazard_state_change(self, hazard_key, new_state):
        # ...existing logic...
        if hazard_key == "falling_marquee_letters" and new_state == "near_miss":
            self.player['friend_dialogue_state'] = "marquee_near_miss"
        elif hazard_key == "popcorn_oil_flareup" and new_state in ("scattered_sparks", "simmer_down"):
            self.player['friend_dialogue_state'] = "popcorn_flare"
        elif hazard_key == "soda_spill_slip" and new_state in ("hard_fall", "sticky_save"):
            self.player['friend_dialogue_state'] = "soda_fall"
        # ...add more as needed...

    def get_friend_dialogue(self):
        friend_npc = self.resource_manager.get_data('npcs', {}).get('friend', {})
        state = self.player.get('friend_dialogue_state', 'default')
        dialogue = friend_npc.get('dialogue_states', {}).get(state, friend_npc.get('dialogue_states', {}).get('default', {}))
        # Reset after showing special dialogue
        if state != 'default':
            self.player['friend_dialogue_state'] = 'default'
        return dialogue

    def _get_item_display_name(self, item_key: str) -> str:
        """Gets the proper display name for an item from its master data. Injected with robust debugging logic."""
        items_master = self.resource_manager.get_data('items', {})
        item_data = items_master.get(item_key)
        if item_data is None:
            self.logger.debug(f"_get_item_display_name: No master data found for item '{item_key}'. Using fallback name.")
            return item_key.replace('_', ' ').capitalize()
        name = item_data.get('name')
        if not name:
            self.logger.debug(f"_get_item_display_name: No 'name' field for item '{item_key}'. Using fallback name.")
            return item_key.replace('_', ' ').capitalize()
        self.logger.debug(f"_get_item_display_name: Found display name '{name}' for item '{item_key}'.")
        return name

    def set_player_flag(self, flag_name: str, value: bool = True):
        """
        Sets or removes a boolean flag on the player object.
        These flags track temporary, narrative states.
        We use a 'set' to efficiently store the flags.
        """
        # Ensure the 'flags' set exists on the player dictionary
        if 'flags' not in self.player:
            self.player['flags'] = set()

        if value:
            # Add the flag to the set
            self.player['flags'].add(flag_name)
            self.logger.info(f"Player flag set: '{flag_name}'")
        else:
            # Remove the flag from the set if it exists
            self.player['flags'].discard(flag_name)
            self.logger.info(f"Player flag removed: '{flag_name}'")

    def get_player_flag(self, flag_name: str) -> bool:
        """
        Checks if a specific flag is currently set on the player.
        Returns True if the flag is present, False otherwise.
        """
        # The .get('flags', set()) ensures we don't crash if 'flags' doesn't exist
        return flag_name in self.player.get('flags', set())
    
    def get_items_in_room(self, room_id: str) -> list:
        """Returns a list of all item objects directly present in a specified room."""
        room_data = self.current_level_data.get(room_id, {})
        item_keys_in_room = room_data.get('items', [])
        
        items_in_room = [item for item in self.items_master_list if item['id'] in item_keys_in_room]
        return items_in_room

    def _maybe_emit_requirements_met_event(self):
        """If exit requirements are now met, queue a one-time notification popup."""
        try:
            met, _ = self._requirements_met_for_level_exit()
        except Exception as e:
            self.logger.error(f"_maybe_emit_requirements_met_event: check failed: {e}", exc_info=True)
            return

        if met and not self.player.get('notified_requirements_met'):
            self.player['notified_requirements_met'] = True
            self.add_ui_event({
                "event_type": "show_popup",
                "title": "You're ready to leave",
                "message": "You have everything you need to exit this level. Head to the Hospital Morgue Exit."
            })
            self.logger.info("Level exit requirements met; notification enqueued.")

    def _apply_qte_effects(self, effects: list):
        """
        Apply world changes embedded in QTE results.
        Supported:
        - {'type': 'unlock_room', 'room_id': 'Room Name'}
        - {'type': 'unlock_furniture', 'room_id': 'Room Name', 'furniture_name': 'name'}
        - {'type': 'break_furniture', 'room_id': 'Room Name', 'furniture_name': 'name'}
        """
        try:
            if not effects:
                return
            for effect in effects:
                et = effect.get('type')
                if et == 'unlock_room':
                    # Ensure both 'locked' and 'locking.locked' are set to False
                    room_id = effect.get('room_id')
                    if room_id:
                        room = self.current_level_rooms_world_state.get(room_id)
                        if room:
                            room['locked'] = False
                            if isinstance(room.get('locking'), dict):
                                room['locking']['locked'] = False
                    self._unlock_room_effect(effect)
                elif et == 'unlock_furniture':
                    rid = effect.get('room_id')
                    fname = effect.get('furniture_name')
                    if rid and fname:
                        r = self.current_level_rooms_world_state.get(rid) or {}
                        furns = r.get('furniture', [])
                        for f in furns:
                            if isinstance(f, dict) and normalize_text(f.get('name','')) == normalize_text(fname):
                                f['locked'] = False
                                break
                    self._unlock_furniture_effect(effect)
                elif et == 'break_furniture':
                    rid = effect.get('room_id')
                    fname = effect.get('furniture_name')
                    if rid and fname:
                        r = self.current_level_rooms_world_state.get(rid) or {}
                        furns = r.get('furniture', [])
                        for f in furns:
                            if isinstance(f, dict) and normalize_text(f.get('name','')) == normalize_text(fname):
                                f['locked'] = False
                                break
                    self._break_furniture_effect(effect)
                # ...existing effect types...
        except Exception as e:
            self.logger.error(f"_apply_qte_effects: Error applying effects: {e}", exc_info=True)
        # Make the HUD reflect changes
        self.add_ui_event({"event_type": "refresh_map"})
        
    #--- Force/Break Handlers and Helpers ---
    def _parse_force_command(self, target_str: str) -> dict:
        """
        Parse 'force <target> [with <tool>]' or 'break <target> [with <tool>]'.
        """
        s = (target_str or "").strip()
        m = re.match(r"(.+?)\s+with\s+(.+)$", s, re.IGNORECASE)
        if m:
            return {"target_name": m.group(1).strip(), "tool_name": m.group(2).strip()}
        return {"target_name": s, "tool_name": None}

    def _get_stat(self, stat_name: str, default: int = 0) -> int:
        """
        Pull a character stat from the active class, falling back to defaults.
        """
        try:
            cls = self.player.get('character_class')
            classes = self.resource_manager.get_data('character_classes', {}) or {}
            data = classes.get(cls, {})
            return int(data.get('stats', {}).get(stat_name, data.get(stat_name, default)))
        except Exception:
            return default

    def _player_has_affinity(self, category: str, value: str) -> bool:
        """
        Returns True if the player's character class has 'value' in affinities[category].
        E.g. _player_has_affinity('item_types', 'tool') -> True for Mechanic class.
        """
        try:
            cls = self.player.get('character_class')
            if not cls:
                return False
            classes = self.resource_manager.get_data('character_classes', {}) or {}
            class_data = classes.get(cls, {})
            affinities = class_data.get('affinities', {})
            return value in affinities.get(category, [])
        except Exception:
            return False

    def _is_tool_item(self, item_key: str) -> bool:
        items_master = self.resource_manager.get_data('items', {}) or {}
        d = items_master.get(item_key, {}) or {}
        return bool(d.get('type') == 'tool' or d.get('is_tool'))

    def _tool_bonus(self, item_key: str) -> int:
        items_master = self.resource_manager.get_data('items', {}) or {}
        d = items_master.get(item_key, {}) or {}
        # Prefer explicit bonus; fallback if item tagged as tool
        return int(d.get('force_bonus', 3 if self._is_tool_item(item_key) else 0))

    def _best_tool_in_inventory(self) -> Tuple[Optional[str], int]:
        """Return (tool_key, bonus) for the best force tool in inventory."""
        best_key, best_bonus = None, 0
        items_master = self.resource_manager.get_data('items', {}) or {}
        for key in self.player.get('inventory', []):
            data = items_master.get(key, {}) or {}
            if data.get('type') == 'tool' or data.get('is_tool'):
                bonus = int(data.get('force_bonus', 3))
                if bonus > best_bonus:
                    best_key, best_bonus = key, bonus
        return best_key, best_bonus

    def _compute_force_difficulty(self, room_or_entity: dict, base: int = 16, strength: int = None, tool_bonus: int = None) -> int:
        """
        Calculates QTE difficulty (mash count).
        Automatically applies bonuses from the best tool in inventory if not provided.
        Mechanics get DOUBLE the tool bonus.
        """
        # 1. Get Base Threshold
        threshold = None
        if isinstance(room_or_entity, dict):
            threshold = room_or_entity.get('force_threshold')
        
        base_target = int(threshold) if threshold is not None else base

        # 2. Get Player Strength (if not passed in)
        if strength is None:
            strength = self._get_stat('strength', 1)
        
        # 3. Find Best Tool (if not passed in)
        if tool_bonus is None:
            _, tool_bonus = self._best_tool_in_inventory()
        
        # 4. Apply Class Affinity (The Mechanic's Edge)
        if tool_bonus > 0 and self._player_has_affinity('item_types', 'tool'):
            self.logger.info("Class Affinity: Doubling tool bonus for Force action.")
            tool_bonus *= 2
            
        # 5. Calculate Final Target (Higher stats = Lower target)
        reduction = (strength * 1.5) + (tool_bonus * 2)
        final_target = max(5, int(base_target - reduction))
        
        return final_target

    def _force_or_break_entity(self, target_name: str, tool_key: str = None) -> dict:
        """
        Attempts to force open or break an entity.
        Automatically selects the best tool from inventory.
        """
        current_room_id = self.player.get('location')
        entity = self._find_entity_in_room(target_name, current_room_id)
        
        if not entity:
            return self._build_response(message=f"You don't see a '{target_name}' to force.", turn_taken=False)

        etype = entity.get('type')
        if etype not in ('furniture', 'object'):
            return self._build_response(message=f"You can't force the {entity.get('name')}.", turn_taken=False)

        fdata = entity.get('data', {}) or {}
        display = entity.get('name')
        
        can_force = fdata.get('locked') or fdata.get('forceable') or fdata.get('is_breakable')
        if not can_force:
            return self._build_response(message=f"There's nothing to force about the {display}.", turn_taken=False)

        # --- AUTO-DETECT BEST TOOL ---
        # We ignore the 'tool_key' argument and find the best one the player has.
        # This ensures the Mechanic's affinity bonus (handled in _compute_force_difficulty)
        # always applies if they have a valid tool.
        best_tool_key, tool_bonus = self._best_tool_in_inventory()
        
        strength = self._get_stat('strength', 1)
        
        # Calculate difficulty using the auto-detected tool
        tgt_mash = self._compute_force_difficulty(fdata, base=14, strength=strength, tool_bonus=tool_bonus)

        effects_on_success = []
        furniture_name = fdata.get('name')

        # Determine outcome type.
        # Priority: if the object is locked (i.e. it's a container being forced open),
        # always use unlock_furniture so items remain accessible after forcing.
        # Only use break_furniture if the object is breakable but NOT a locked container
        # (i.e. it's meant to be smashed/destroyed, not opened).
        # This means `forceable: true` on a locked container always results in force-open,
        # even when `is_breakable: true` is also set on the same object.
        is_locked_container = fdata.get('locked') and fdata.get('is_container')
        if fdata.get('is_breakable') and not is_locked_container:
            effects_on_success.append({"type": "break_furniture", "room_id": current_room_id, "furniture_name": furniture_name})
            success_msg = f"You smash the {display} apart!"
        else:
            effects_on_success.append({"type": "unlock_furniture", "room_id": current_room_id, "furniture_name": furniture_name})
            success_msg = f"You force the {display} open!"

        # Helper to update world state immediately if we aren't using QTEs
        def on_force_success():
            fdata["locked"] = False
            if isinstance(fdata.get("locking"), dict):
                fdata["locking"]["locked"] = False

        # Trigger Action
        if self.qte_engine:
            self.player['qte_active'] = True
            
            tool_msg = f" (Using {self._get_item_display_name(best_tool_key)})" if best_tool_key else ""
            
            self.qte_engine.start_qte("button_mash", {
                "ui_mode": "in-screen",
                "ui_prompt_message": f"You brace against the {display}{tool_msg}. Mash to force it!",
                "target_mash_count": tgt_mash,
                "duration": 4.0,
                "success_message": success_msg,
                "failure_message": f"You strain, but the {display} holds.",
                "effects_on_success": effects_on_success,
                "hp_damage_on_failure": 0
            })
            return self._build_response(message=f"You square up on the {display}...", turn_taken=True)
        else:
            # Fallback logic
            for eff in effects_on_success:
                if eff['type'] == "break_furniture":
                    self._break_furniture_effect(eff)
                elif eff['type'] == "unlock_furniture":
                    self._unlock_furniture_effect(eff)
            on_force_success()
            return self._build_response(message=success_msg, turn_taken=True, success=True)

    def _populate_forced_items(self, f_data: dict):
        """
        Generates items defined in 'on_force_action' or 'on_break_spill_items' 
        and adds them to the container's inventory.
        """
        # 1. Handle 'on_force_action' (Room JSON style)
        force_action = f_data.get('on_force_action', {})
        drops = force_action.get('drops_items', [])
        if drops:
            self.logger.info(f"_populate_forced_items: Spawning forced items for '{f_data.get('name')}': {drops}")
            f_data.setdefault('items', []).extend(drops)
            # Clear to prevent duplicate spawning
            f_data.pop('on_force_action', None)
            return

        # 2. Handle 'on_break_spill_items' (Furniture JSON style)
        spills = f_data.get('on_break_spill_items', [])
        if spills:
            self.logger.info(f"_populate_forced_items: Spawning spilled items for '{f_data.get('name')}': {spills}")
            for entry in spills:
                # Handle {"name": "x", "quantity": "1d3"} vs string "x"
                item_name = entry.get('name') if isinstance(entry, dict) else entry
                if item_name:
                    f_data.setdefault('items', []).append(item_name)
            f_data.pop('on_break_spill_items', None)

    def delete_save_game(self, slot_identifier: str) -> dict:
        """Delete a save game file."""
        try:
            from .utils import get_save_filepath
            import textwrap
            import random
            
            save_path = get_save_filepath(slot_identifier)
            
            if not os.path.exists(save_path):
                return {
                    "success": False,
                    "message": f"No save file found for slot '{slot_identifier}'."
                }
            
            os.remove(save_path)
            self.logger.info(f"Deleted save file: {save_path}")
            
            return {
                "success": True,
                "message": f"Save slot '{slot_identifier}' deleted successfully."
            }
            
        except Exception as e:
            self.logger.error(f"Failed to delete save file '{slot_identifier}': {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Failed to delete save: {str(e)}"
            }

    def _parse_option_number(self, option_str: str) -> Optional[int]:
        """
        Helper to parse the option number from a respond command.
        Returns zero-based index or None if invalid.
        """
        try:
            opt_num = int(option_str.split()[0]) - 1
            if opt_num < 0:
                return None
            return opt_num
        except Exception as e:
            self.logger.debug(f"_parse_option_number: Failed to parse option_str='{option_str}': {e}")
            return None

    #--- Use action handlers
    def _use_main(self, target_str: str) -> dict:
        """
        Main 'use' command handler for room furniture/hazards.
        NEW: Validate hazard state before forwarding to hazard engine.
        """
        target_norm = normalize_text(target_str or "")
        if not target_norm:
            self.logger.info("_use_main: No target specified.")
            return {"messages": ["Use what?"], "turn_taken": False}

        current_room_id = self.player['location']

        # 1) Room interactables FIRST
        try:
            if self._try_trigger_room_interactable_use(target_str):
                self.logger.info("_use_main: Room interactable triggered successfully.")
                return {
                    "messages": [],
                    "game_state": self.get_current_game_state(),
                    "ui_events": self.get_ui_events(),
                    "turn_taken": True,
                }
        except Exception as e:
            self.logger.error(f"_use_main: room interactable handling failed: {e}", exc_info=True)

        # 2) Hazards/objects with player_interaction['use']
        # PATCH: Check if target is a hazard-linked object and validate hazard state before forwarding
        visible_entities = self._get_all_visible_entities_in_room(current_room_id)
        hazard_type = None
        for entity in visible_entities['objects']:
            entity_name = entity.get('name', '')
            if target_norm == normalize_text(entity_name):
                hazard_type = entity.get('hazard_key')
                break

        if hazard_type:
            self.logger.info(f"_use_main: Object '{target_str}' is linked to hazard '{hazard_type}'. Forwarding to HazardEngine.")
            # NEW: Check if hazard is in a usable state
            hazard_instance = None
            for hid, hinst in (self.hazard_engine.active_hazards.items() if self.hazard_engine else []):
                if hinst.get('type') == hazard_type and hinst.get('location') == current_room_id:
                    hazard_instance = hinst
                    break

            if hazard_instance:
                state = hazard_instance.get('state')
                master_def = hazard_instance.get('master_data', {})
                state_def = (master_def.get('states') or {}).get(state, {})
                if state_def.get('is_terminal_state') or state in ['empty', 'destroyed', 'removed']:
                    return self._build_response(
                        message=f"The {target_str} is no longer usable.",
                        turn_taken=False,
                        success=False
                    )

            result = self.hazard_engine.process_player_interaction('use', target_str)
            if not result:
                # FIX: Fallback to 'interact' to process JSON rules
                result = self.hazard_engine.process_player_interaction('interact', target_str)
            if result:
                return result

        hazard_result = self._use_hazard_object(target_norm, current_room_id)
        if hazard_result:
            return hazard_result

        # 3) Inventory item use logic (support both 'use item' and 'use item on target')
        inventory_result = self._use_inventory_item(target_str, target_norm, current_room_id)
        if inventory_result:
            return inventory_result

        # 4) Try direct use on furniture/object
        entity_result = self._use_direct_entity(target_norm, current_room_id)
        if entity_result:
            return entity_result

        # If nothing matches
        self.logger.info(f"_use_main: No valid use target found for '{target_str}'.")
        item_name = self._parse_use_command(target_str).get('item_name', target_str)
        return self._build_response(message=f"You don't have a {item_name} to use.", turn_taken=False)

    def _use_hazard_object(self, target_norm: str, current_room_id: str) -> Optional[dict]:

        try:
            room_data = self.get_room_data(current_room_id) or {}

            # Safely combine arrays
            objects = room_data.get('objects', [])
            furniture = room_data.get('furniture', [])
            hazards = room_data.get('hazards_present', [])

            if not isinstance(objects, list): objects = []
            if not isinstance(furniture, list): furniture = []
            if not isinstance(hazards, list): hazards = []

            all_entities = objects + furniture + hazards

            # --- Type-safe entity match ---
            entity = None
            for e in all_entities:
                if isinstance(e, dict):
                    if normalize_text(e.get('name', '')) == target_norm:
                        entity = e
                        break
                elif isinstance(e, str):
                    if normalize_text(e) == target_norm:
                        entity = {"name": e, "id": e}
                        break

            # Fallback: check fully-resolved visible entities (includes hazard-spawned objects)
            if not entity:
                visible_entities = self._get_all_visible_entities_in_room(current_room_id)
                for e in visible_entities.get('objects', []):
                    if normalize_text(e.get('name', '')) == target_norm:
                        entity = e
                        break

            if not entity:
                return None

            entity_name = entity.get('name', '')
            hazard_key = entity.get('hazard_key')

            # If no explicit hazard_key on the entity, try matching hazards_present entry
            if not hazard_key:
                for h in hazards:
                    if isinstance(h, str) and normalize_text(h) == target_norm:
                        hazard_key = h
                        break
                    if isinstance(h, dict):
                        h_name = h.get('type') or h.get('id') or h.get('name')
                        if h_name and normalize_text(h_name) == target_norm:
                            hazard_key = h_name
                            break

            if not hazard_key or not self.hazard_engine:
                return None

            hazard_state = self.hazard_engine.get_hazard_state(hazard_key, current_room_id)
            hazards_master = self.resource_manager.get_data('hazards', {}) or {}
            h_def = hazards_master.get(hazard_key, {}) or {}

            # Alias 'interact' rules to 'use'
            use_rules = list(h_def.get('player_interaction', {}).get('use', []))
            use_rules.extend(h_def.get('player_interaction', {}).get('interact', []))

            for rule in use_rules:
                on_names = rule.get('on_target_name', [])
                if isinstance(on_names, str):
                    on_names = [on_names]

                # If rule specifies target names, enforce match
                if on_names and target_norm not in [normalize_text(n) for n in on_names]:
                    continue

                req_states = rule.get('requires_hazard_state', [])
                if req_states and hazard_state not in req_states:
                    continue

                msg = rule.get('message', f"You use the {entity_name}.")
                target_state = rule.get('target_state')
                special_action = rule.get('on_trigger_special_action')

                if target_state:
                    result = self.hazard_engine.set_hazard_state_by_type(
                        current_room_id, hazard_key, target_state, suppress_entry_effects=False
                    )
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)

                if special_action:
                    self.hazard_engine._maybe_run_special_action(
                        {'on_state_entry_special_action': special_action},
                        hazard_key
                    )

                self.logger.info(
                    f"_use_hazard_object: Used hazard object '{entity_name}' with rule '{rule}'."
                )
                return self._build_response(message=msg, turn_taken=True)

            return None

        except Exception as e:
            self.logger.error(f"_use_hazard_object: Error: {e}", exc_info=True)
            return self._build_response(
                message="Something went wrong using the object.",
                turn_taken=False,
                success=False
            )

    def _use_inventory_item(self, target_str: str, target_norm: str, current_room_id: str) -> Optional[dict]:
        try:
            parsed = self._parse_use_command(target_str)
            item_name = parsed['item_name']
            target_name = parsed['target_name']

            item_entity = self._find_entity_in_room(item_name, current_room_id)
            if item_entity and item_entity['type'] == 'item_inventory':
                item_key = item_entity['id_key']
                item_data = item_entity['data']

                # If 'use [item] on [target]'
                if target_name:
                    target_entity = self._find_entity_in_room(target_name, current_room_id)
                    if not target_entity:
                        return self._build_response(message=f"You don't see a '{target_name}' to use that on.", turn_taken=False)

                    # Priority 1: Check if the TARGET (e.g., furniture) has a rule for this ITEM.
                    if target_entity['type'] == 'furniture':
                        interaction_rules = target_entity['data'].get('use_item_interaction', [])
                        
                        # --- Support nested interactable_triggers ---
                        if 'interactable_triggers' in target_entity['data']:
                            interaction_rules.extend(target_entity['data']['interactable_triggers'].get('use', []))
                            
                        for rule in interaction_rules:
                            # Support both 'item_names_required' and 'required_item_name' syntax
                            req_items = rule.get('item_names_required', [])
                            if isinstance(req_items, str): req_items = [req_items]
                            if 'required_item_name' in rule: req_items.append(rule['required_item_name'])
                            
                            if item_key in req_items or item_name.lower() in [i.lower() for i in req_items]:
                                message = rule.get('message_success', rule.get('message', f"You use the {item_entity['name']} on the {target_entity['name']}.")).format(item_name=item_entity['name'])
                                
                                # --- REVERSE RUBE GOLDBERG SPAWNER ---
                                if 'spawns_hazard' in rule:
                                    hazard_type = rule['spawns_hazard']
                                    initial_state = rule.get('spawns_in_state', 'idle')
                                    
                                    self.logger.info(f"Player Sabotage! Spawning {hazard_type} in {current_room_id}")
                                    
                                    if hasattr(self, 'hazard_engine'):
                                        self.hazard_engine.spawn_hazard(hazard_type, current_room_id, initial_state)
                                        self.hazard_engine._check_synergies(current_room_id)
                                # --------------------------------------
                                
                                self.logger.info(f"_use_inventory_item: Used '{item_key}' on furniture '{target_entity['name']}'.")
                                return self._build_response(message=message, turn_taken=True)
                        
                            if item_key in rule.get('item_names_required', []):
                                message = rule.get('message_success', f"You use the {item_entity['name']} on the {target_entity['name']}.").format(item_name=item_entity['name'])
                                self.logger.info(f"_use_inventory_item: Used '{item_key}' on furniture '{target_entity['name']}'.")
                                return self._build_response(message=message, turn_taken=True)

                    # Priority 1.5: Check if the TARGET is an object and has use_item_interaction rules
                    if target_entity['type'] == 'object':
                        interaction_rules = target_entity['data'].get('use_item_interaction', [])
                        for rule in interaction_rules:
                            if item_key in rule.get('item_names_required', []):
                                action_effect = rule.get('action_effect')
                                message = rule.get('message_success', f"You use the {item_entity['name']} on the {target_entity['name']}.").format(item_name=item_entity['name'])
                                self.logger.info(f"_use_inventory_item: Used '{item_key}' on object '{target_entity['name']}'.")
                                return self._build_response(message=message, turn_taken=True)

                    # Priority 2: Check if the ITEM has a rule for this TARGET.
                    if target_entity['name'].lower() in [t.lower() for t in item_data.get('use_on', [])]:
                        message = item_data.get('use_result', {}).get(target_entity['name'], f"You use the {item_entity['name']} on the {target_entity['name']}.")
                        self.logger.info(f"_use_inventory_item: Used '{item_key}' on '{target_entity['name']}' via item rule.")
                        return self._build_response(message=message, turn_taken=True)

                    self.logger.info(f"_use_inventory_item: Can't use '{item_entity['name']}' on '{target_entity['name']}'.")
                    return self._build_response(message=f"You can't use the {item_entity['name']} on the {target_entity['name']}.", turn_taken=False)

                # If just 'use [item]'
                else:
                    has_heal = 'heal_amount' in item_data
                    has_cure = 'cures_status' in item_data

                    if has_heal or has_cure:
                        actual_healed = 0
                        cured_any = False
                        
                        default_msg = item_data.get('use_result', {}).get('general', f"You use the {item_entity['name']}.")
                        messages_to_show = [default_msg]

                        # --- 1. HEALING LOGIC ---
                        if has_heal:
                            base_heal = item_data['heal_amount']
                            multiplier = 1.0

                            # CHECK AFFINITY
                            if self._player_has_affinity('item_types', item_data.get('type', '')):
                                multiplier = 1.5
                                self.add_ui_event({"event_type": "screen_flash", "color": "green", "duration": 0.5})

                            # --- CLASS PERK LOGIC ---
                            character_class = self.player.get('character_class', '')
                            item_type = item_data.get('type', '')

                            if character_class == "EMT" and item_type == "medical_supply":
                                multiplier *= 1.5
                                self.logger.info(f"EMT Bonus applied to {item_entity['name']}")

                            final_heal = int(base_heal * multiplier)
                            old_hp = self.player['hp']
                            self.player['hp'] = min(self.player.get('max_hp', 30), old_hp + final_heal)
                            actual_healed = self.player['hp'] - old_hp

                            if actual_healed > 0:
                                messages_to_show.append(f"[color=00ff00]Recovered {actual_healed} HP.[/color]")
                                if self.death_ai:
                                    self.logger.info("Healing Item Used - Increasing Entropy.")
                                    self.death_ai.increase_entropy(5.0)

                        # --- 2. CURE LOGIC ---
                        if has_cure:
                            cures = item_data.get('cures_status', [])
                            status_effects = self.player.get('status_effects', {})
                            
                            for status in cures:
                                if status in status_effects and status_effects[status] > 0:
                                    del status_effects[status]
                                    cured_any = True
                                    
                            if cured_any:
                                messages_to_show.append("[color=00ff00]You successfully treated the wound and stopped the bleeding.[/color]")

                        # --- ANTI-WASTE QUALITY OF LIFE FIX ---
                        if actual_healed == 0 and not cured_any:
                            return self._build_response(
                                message=f"You don't need to use the {item_entity['name']} right now. You're fully healed and not bleeding.", 
                                turn_taken=False
                            )

                        # --- CONSUMPTION ---
                        if item_data.get('consumable_on_use'):
                            if item_key in self.player['inventory']:
                                self.player['inventory'].remove(item_key)

                        return self._build_response(message="\n".join(messages_to_show), turn_taken=True)

                    # Add more item self-use logic here as needed
                    self.logger.info(f"_use_inventory_item: No self-use effect for '{item_entity['name']}'.")
                    return self._build_response(message=f"Silly goose, you can't use the {item_entity['name']} by itself.", turn_taken=False)
            
            return None
        except Exception as e:
            self.logger.error(f"_use_inventory_item: Error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong using the item.", turn_taken=False, success=False)

    def _perform_item_transformation(self, current_entity: dict, transform_data: dict) -> list:
        """
        Swaps an item for another based on transformation data.
        Returns a list of UI events (like a popup or inventory refresh).
        """
        old_id = current_entity.get('id_key')
        new_id = transform_data.get('target_item_id')
        message = transform_data.get('message', "The item reveals its true nature.")
        
        if not old_id or not new_id:
            self.logger.error(f"Transformation failed: missing IDs. Old: {old_id}, New: {new_id}")
            return []

        # 1. Check Inventory Swap
        if current_entity.get('type') == 'item_inventory':
            if old_id in self.player['inventory']:
                # Find index, remove old, insert new (keeps order)
                idx = self.player['inventory'].index(old_id)
                self.player['inventory'][idx] = new_id
                self.logger.info(f"Transformed inventory item '{old_id}' -> '{new_id}'")

                # CANONICAL PATCH: Record evidence if the transformed item is evidence
                items_master = self.resource_manager.get_data('items', {})
                new_item_data = items_master.get(new_id, {})
                if new_item_data.get('is_evidence', False) and self.achievements_system:
                    self.achievements_system.record_evidence(
                        evidence_id=new_id,
                        name=new_item_data.get('name', new_id),
                        description=new_item_data.get('description', ''),
                        char_connection=new_item_data.get('character_connection')
                    )
        
        # 2. Check Room Swap (Loose Items)
        elif current_entity.get('type') == 'item':
            if old_id in self.current_level_items_world_state:
                # Copy location data from old to new
                location_data = self.current_level_items_world_state.pop(old_id)
                self.current_level_items_world_state[new_id] = location_data
                self.logger.info(f"Transformed world item '{old_id}' -> '{new_id}'")

        # Return the narrative result
        return [{
            "event_type": "show_popup",
            "title": "Discovery",
            "message": message,
            "image": "" # Optional: Add image path if your JSON supports it
        }, {
            "event_type": "refresh_context_actions" # Force UI to update buttons
        }]

    def _use_direct_entity(self, target_norm: str, current_room_id: str) -> Optional[dict]:
        try:
            entity = self._find_entity_in_room(target_norm, current_room_id)
            if entity and entity['type'] in ('furniture', 'object'):
                use_rules = entity['data'].get('use_interaction', [])
                for rule in use_rules:
                    if target_norm in [normalize_text(n) for n in rule.get('on_target_name', [entity['name']])]:
                        msg = rule.get('message', f"You use the {entity['name']}.")
                        self.logger.info(f"_use_direct_entity: Used '{entity['name']}' via direct use_interaction.")
                        return self._build_response(message=msg, turn_taken=True)
                self.logger.info(f"_use_direct_entity: Fallback use for '{entity['name']}'.")
                return self._build_response(message=f"You use the {entity['name']}.", turn_taken=True)
            return None
        except Exception as e:
            self.logger.error(f"_use_direct_entity: Error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong using the entity.", turn_taken=False, success=False)
            
    #--- Unlock command handlers
    def _get_player_keys(self) -> dict:
        """Return a dict of key_id: key_data for all keys in player inventory."""
        items_master = self.resource_manager.get_data('items', {})
        keys = {}
        for item_key in self.player.get('inventory', []):
            item_data = items_master.get(item_key, {})
            if item_data.get("type") == "key":
                keys[item_key] = item_data
        return keys

    def _unlock_exit(self, direction: str, dest_room_id: str, available_keys: dict) -> dict:
        """
        Unlock the specified exit if the player has the correct key.
        PATCH: Checks both root-level and nested 'locking' dict for 'unlocks_with'.
        PATCH: Persistently updates the live world state upon success.
        PATCH: Safely handles both String and List formats for 'unlocks_with'.
        """
        # 1. Get the LIVE world state for the destination room
        dest_data = self.current_level_rooms_world_state.get(dest_room_id)
        if not dest_data:
            # Fallback to master data if not in world state yet (rare)
            dest_data = self.get_room_data(dest_room_id) or {}

        # 2. Check if already unlocked
        locking = dest_data.get("locking", {})
        if not isinstance(locking, dict):
            locking = {}
        
        locked = dest_data.get("locked", False) or locking.get("locked", False)
        
        if not locked:
            return self._build_response(
                message=f"The way to {dest_room_id.replace('_', ' ')} is already unlocked.",
                turn_taken=False
            )

        # 3. Find the required key ID(s)
        required_keys_raw = locking.get("unlocks_with") or dest_data.get("unlocks_with")
        
        if not required_keys_raw:
            return self._build_response(
                message=f"The door to {dest_room_id.replace('_', ' ')} doesn't have a keyhole.",
                turn_taken=False
            )

        # Ensure required_keys is always a list for uniform checking
        if isinstance(required_keys_raw, str):
            required_keys = [required_keys_raw]
        else:
            required_keys = required_keys_raw

        # 4. Check player inventory for ANY matching key
        norm = normalize_text
        req_norms = [norm(k) for k in required_keys] # Normalize all acceptable keys
        
        key_found = None
        for key_id, key_data in available_keys.items():
            # Gather all identifiers for the current item
            checks = [
                norm(key_id),
                norm(key_data.get("name", "")),
            ]
            checks.extend([norm(u) for u in key_data.get("unlocks", [])])
            checks.extend([norm(a) for a in key_data.get("alias", [])])
            
            # If the item matches ANY of the required keys, or is a master key
            if any(req in checks for req in req_norms) or "*" in checks or key_data.get("is_master_key"):
                key_found = key_id
                break

        if key_found:
            # 5. SUCCESS: Update World State Permanently
            dest_data["locked"] = False
            dest_data.pop("locked_by_mri", None) # Remove MRI lock if present
            
            if "locking" not in dest_data or not isinstance(dest_data["locking"], dict):
                dest_data["locking"] = {}
            dest_data["locking"]["locked"] = False
            
            self.logger.info(f"_unlock_exit: Canonical unlock of '{dest_room_id}' using '{key_found}'")
            
            # Refresh Map UI to show the open path
            self.add_ui_event({"event_type": "refresh_map"})

            display_name = self._get_item_display_name(key_found)
            message = f"You unlock the door to {dest_room_id.replace('_', ' ')} with the {display_name}."
            
            return self._build_response(
                message=color_text(message, "success", self.resource_manager),
                turn_taken=True,
                success=True
            )

        return self._build_response(
            message=color_text(
                f"You don't have the right key to unlock the door to {dest_room_id.replace('_', ' ')}.",
                "warning",
                self.resource_manager
            ),
            turn_taken=True,
            success=False
        )

    def _try_unlock_furniture(self, target_name_str: str, current_room_id: str, available_keys: dict) -> Optional[dict]:
        """Try to unlock a furniture container in the current room."""
        entity = self._find_entity_in_room(target_name_str, current_room_id)
        if entity and entity['type'] == 'furniture':
            furniture_data = entity['data']
            # Type check: if locking is not a dict, treat as unlocked or create default dict
            locking = furniture_data.get("locking", {})
            if not isinstance(locking, dict):
                locking = {}
                furniture_data["locking"] = locking
            # Always check both top-level and locking['locked']
            already_unlocked = not furniture_data.get("locked", False) and not locking.get("locked", False)
            if already_unlocked:
                return self._build_response(
                    message=f"The {entity['name']} is already unlocked.",
                    turn_taken=False
                )
            required_key = furniture_data.get("unlocks_with")
            if not required_key:
                return self._build_response(
                    message=f"The {entity['name']} doesn't have a keyhole.",
                    turn_taken=False
                )
            for key_id, key_data in available_keys.items():
                unlocks = [normalize_text(u) for u in key_data.get("unlocks", [])]
                if (normalize_text(required_key) in unlocks or
                    normalize_text(furniture_data.get("name", "")) in unlocks or
                    normalize_text(entity['id_key']) in unlocks or
                    "*" in key_data.get("unlocks", []) or
                    key_data.get("is_master_key")):
                    # Always unlock both top-level and locking['locked']
                    furniture_data["locked"] = False
                    locking["locked"] = False
                    self.logger.info(f"_command_unlock: Unlocked {entity['name']} with {key_id}")
                    display_name = self._get_item_display_name(key_id)
                    message = f"You unlock the {entity['name']} with the {display_name}."
                    return self._build_response(
                        message=color_text(message, "success", self.resource_manager),
                        turn_taken=True,
                        success=True
                    )
            return self._build_response(
                message=color_text(
                    f"You don't have the right key to unlock the {entity['name']}.",
                    "warning",
                    self.resource_manager
                ),
                turn_taken=True,
                success=False
            )
        return None

    # --- 'Examine' handlers
    def _examine_main(self, target: str) -> dict:
        target = (target or "").strip()
        if not target:
            self.logger.info("_examine_main: No target specified.")
            return self._build_response(message="Examine what?", turn_taken=False)

        # 1) Room-level examine triggers FIRST
        try:
            if self._try_trigger_room_interactable_examine(target):
                self.logger.info("_examine_main: Room interactable examine triggered.")
                return {
                    "messages": [],
                    "game_state": self.get_current_game_state(),
                    "ui_events": self.get_ui_events(),
                    "turn_taken": True,
                    "success": True,
                }
        except Exception as e:
            self.logger.error(f"_examine_main: room interactable handling failed: {e}", exc_info=True)

        current_room_id = self.player['location']

        # 2) If examining the room itself
        if not target or normalize_text(target) in ['room', 'area', 'surroundings']:
            return self._examine_room(current_room_id)

        # 3) Check for NPCs in the room
        npc_result = self._examine_npc(target, current_room_id)
        if npc_result:
            return npc_result

        # 4) Examine entity (object, furniture, hazard, item)
        return self._examine_entity(target, current_room_id)

    def _examine_room(self, room_id: str) -> dict:
        try:
            room_data = self.get_room_data(room_id)
            description = self._get_rich_room_description(room_id)
            npc_list = room_data.get('npcs', []) if room_data else []
            if npc_list:
                npc_names = [color_text(npc.get('name', ''), 'npc', self.resource_manager) for npc in npc_list if npc.get('name')]
                description += f"\n\nNPCs present: {', '.join(npc_names)}."
            return self._build_response(message=description, turn_taken=True)
        except Exception as e:
            self.logger.error(f"_examine_room: Error: {e}", exc_info=True)
            return self._build_response(message="You see nothing special.", turn_taken=True)

    def _examine_npc(self, target: str, room_id: str) -> Optional[dict]:
        try:
            room_data = self.get_room_data(room_id) or {}
            npcs = room_data.get('npcs', []) or []
            target_norm = normalize_text(target)
            
            for npc_raw in npcs:
                # --- FIX: Safely handle both standard dicts and companion strings ---
                if isinstance(npc_raw, dict):
                    npc = npc_raw
                elif isinstance(npc_raw, str):
                    # Leverage our helper to dynamically reconstruct the companion dict!
                    npc = self._find_npc_in_room(npc_raw, room_id)
                    if not npc:
                        continue
                else:
                    continue
                # --------------------------------------------------------------------

                npc_name = npc.get('name', '')
                if normalize_text(npc_name) == target_norm:
                    desc = npc.get('examine_details') or npc.get('description') or f"You see {npc_name}. They seem approachable."
                    dialogue_states = npc.get('dialogue_states', {})
                    initial_state = self._get_npc_state(npc)
                    node = dialogue_states.get(initial_state, {})
                    options = node.get('options', []) if node else []
                    if options:
                        opts_text = "\nDialogue options:\n" + "\n".join(f"  {i+1}. {opt.get('text','')}" for i, opt in enumerate(options))
                        desc += opts_text
                    return self._build_response(message=desc, turn_taken=True, success=True)
            return None
        except Exception as e:
            self.logger.error(f"_examine_npc: Error: {e}", exc_info=True)
            return None

    def _examine_entity(self, target: str, room_id: str) -> dict:
        try:
            entity = self._find_entity_in_room(target, room_id)
            if not entity:
                self.logger.info(f"_examine_entity: '{target}' not found in room '{room_id}'")
                return self._build_response(message=f"You see nothing special about '{target}'.", turn_taken=False)

            entity_data = entity.get('data') or entity
            
            # --- NEW: Check for Transformation ---
            transform_data = entity_data.get('on_examine_transform')
            if transform_data:
                # Execute the swap
                events = self._perform_item_transformation(entity, transform_data)
                
                # We return the transformation message instead of the standard description
                # because the object has fundamentally changed.
                return self._build_response(
                    message=transform_data.get('message'), 
                    turn_taken=True, 
                    success=True, 
                    ui_events=events
                )
            # -------------------------------------

            # --- THE FINAL FIX: Trigger Read Actions! ---
            read_action = entity_data.get('on_read_action')
            if read_action:
                flag_to_set = read_action.get('set_player_flag')
                if flag_to_set:
                    self.set_player_flag(flag_to_set, True)
                    self.logger.info(f"Player read lore item. Set flag: {flag_to_set}")
            # --------------------------------------------

            description = ""
            is_hazard = (entity_data.get('type') == 'hazard_entity')
            
            # Track if the description came from a dynamic hazard state
            is_dynamic_hazard_text = False

            # 1. Get Description
            if is_hazard:
                # Try to get state-specific text
                hazard_text = self._hazard_examine_text(
                    entity_data.get('hazard_key'),
                    entity_data.get('name') or entity['name'],
                    room_id
                )
                if hazard_text:
                    description = hazard_text
                    is_dynamic_hazard_text = True

            if not description:
                # 1. Try the hydrated data from the room first
                description = entity_data.get('examine_details')
                
                # 2. If missing, look up directly in the master items (Last line of defense)
                if not description:
                    master_items = self.resource_manager.get_data('items', {})
                    item_id = entity_data.get('id') or entity.get('name')
                    master_entry = master_items.get(item_id, {})
                    description = master_entry.get('examine_details')

                # 3. Fallback to basic description or generic name
                if not description:
                    description = (
                        entity_data.get('description') or 
                        f"You see {entity.get('name', 'something')}. Nothing special stands out."
                    )

            # Check if the item/entity has an associated image in master data
            item_image = entity_data.get('image', '') 
            force_popup = entity_data.get('force_popup', False)
            
            # --- FIX START: Smarter Popup Logic ---
            ui_events = []
            is_generic_fail = "product of the hazard" in description
            
            # Check if this description was a special 'examine' message from hazards.json
            is_special_hazard_msg = False
            if is_hazard:
                # We know it's a special message if the hazard logic blocked 
                # the normal examine success or set a specific flag
                is_special_hazard_msg = entity_data.get('blocks_action_success') or \
                                        entity_data.get('sets_interaction_flag')

            # NEW CONDITION: Include special hazard messages in the popup logic
            should_popup = (item_image or force_popup or entity_data.get('is_evidence', False)) or \
                           (is_hazard and (is_dynamic_hazard_text or is_special_hazard_msg))
            
            if should_popup and not is_generic_fail:
                # POPUP for the ventilator, patient, and other narrative hazard points
                ui_events.append({
                    "event_type": "show_popup",
                    "title": entity.get('name', 'Examine').replace('_', ' ').title(),
                    "message": description,
                    "image": item_image
                })
                # Log it too for history
                log_message = f"[b]Examine {entity['name']}:[/b]\n{description}"
            else:
                # LOG ONLY for basic stuff (Power Strips, Tables, Chairs)
                log_message = f"[b]{entity['name'].title()}:[/b] {description}"
            # --- FIX END ---

            self._examine_first_popup(entity_data, room_id, ui_events)
            self._examine_hazard_trigger(entity_data, room_id, ui_events)
            self._examine_omen(entity_data, ui_events)

            return self._build_response(message=log_message, turn_taken=True, success=True, ui_events=ui_events)
            
        except Exception as e:
            self.logger.error(f"_examine_entity: Error: {e}", exc_info=True)
            return self._build_response(message="You see nothing special.", turn_taken=True, success=False)

    def _examine_first_popup(self, entity_data: dict, room_id: str, ui_events: list):
        try:
            hazard_key = entity_data.get('hazard_key')
            entity_name = entity_data.get('name')
            if hazard_key and entity_name:
                hazards_master = self.resource_manager.get_data('hazards', {}) or {}
                h_def = hazards_master.get(hazard_key, {}) or {}
                ex_resps = h_def.get('examine_responses', {}) or {}
                # Normalize keys for robust matching
                norm_entity_name = normalize_text(entity_name)
                resp = None
                for k, v in ex_resps.items():
                    if normalize_text(k) == norm_entity_name:
                        resp = v
                        break
                first_msg = resp.get('first_examine_description') if resp else None
                if first_msg:
                    flag = f"first_examine_shown::{room_id}::{norm_entity_name}"
                    if flag not in self.interaction_flags:
                        ui_events.append({
                            "event_type": "show_popup",
                            "title": room_id.replace("_", " ").title(),
                            "message": first_msg
                        })
                        self.interaction_flags.add(flag)
        except Exception as e:
            self.logger.error(f"_examine_first_popup: Error: {e}", exc_info=True)

    def _examine_hazard_trigger(self, entity_data: dict, room_id: str, ui_events: list):
        try:
            hazard_trigger = entity_data.get('triggers_hazard_state_change')
            if hazard_trigger and self.hazard_engine:
                hazard_type = hazard_trigger.get('hazard_type')
                target_state = hazard_trigger.get('target_state')
                message = hazard_trigger.get('message')
                if hazard_type and target_state:
                    result = self.hazard_engine.set_hazard_state_by_type(room_id, hazard_type, target_state)
                    self.logger.info(f"_examine_hazard_trigger: set hazard '{hazard_type}' at '{room_id}' to '{target_state}': {result}")
                    for consequence in (result.get('consequences', []) if isinstance(result, dict) else []):
                        self.handle_hazard_consequence(consequence)
                    if message:
                        ui_events.append({
                            "event_type": "show_popup",
                            "title": room_id.replace("_", " ").title(),
                            "message": message
                        })
                    if hazard_trigger.get('triggers_level_transition'):
                        self.player['level_complete_flag'] = True
        except Exception as e:
            self.logger.error(f"_examine_hazard_trigger: Error: {e}", exc_info=True)

    def _examine_omen(self, entity_data: dict, ui_events: list):
        try:
            omen_shown = False
            shown_trigger_key = None
            
            # --- FIX: Resolve the actual string key for the dictionary lookup! ---
            raw_omen_flag = entity_data.get('is_omen_provider')
            
            if raw_omen_flag is True:
                # If it's a boolean true, the key is the object's name (e.g., "television")
                from fd_terminal.utils import normalize_text
                omen_trigger_key = normalize_text(entity_data.get('name', ''))
            elif isinstance(raw_omen_flag, str):
                # If you explicitly passed a string key in the JSON, use that
                omen_trigger_key = raw_omen_flag
            else:
                omen_trigger_key = None
            # ---------------------------------------------------------------------

            # --- Default Title ---
            game_config = self.resource_manager.get_data('game_config', {})
            popup_title = random.choice(game_config.get('omen_popup_titles', ["A Glimpse of the Design"]))

            # --- Contextual Override ---
            hazard_key = entity_data.get('hazard_key')
            if hazard_key:
                hazards_master = self.resource_manager.get_data('hazards', {})
                hazard_def = hazards_master.get(hazard_key)
                if hazard_def and 'name' in hazard_def:
                    popup_title = f"Sign of the {hazard_def['name']}"

            if omen_trigger_key and self._player_can_see_omens():
                # Now this will correctly ask: `if "television" in self.current_level_omens:`
                if omen_trigger_key in self.current_level_omens:
                    omen_options = self.current_level_omens[omen_trigger_key]
                else:
                    omen_options = None
                omen_text = None

                # If the omen is state-dependent (dict), select by hazard state
                if isinstance(omen_options, dict):
                    hazard_key = None
                    hazard_room = None
                    if omen_trigger_key == "popcorn_oil_flareup":
                        hazard_key = "popcorn_oil_flareup"
                        hazard_room = "Concessions"
                    if hazard_key and hazard_room:
                        hazard_state = self.hazard_engine.get_hazard_state(hazard_key, hazard_room)
                        omen_text = omen_options.get(hazard_state)
                    if not omen_text:
                        omen_text = next(iter(omen_options.values()))
                elif isinstance(omen_options, list):
                    omen_text = random.choice(omen_options)
                elif omen_options is not None:
                    omen_text = str(omen_options)

                if omen_text:
                    omen_popup_command = {
                        "event_type": "show_popup",
                        "title": popup_title,  # <--- Use our dynamic title here
                        "message": color_text(omen_text, 'special', self.resource_manager),
                        "vfx_hint": "fear"
                    }
                    ui_events.append(omen_popup_command)
                    omen_shown = True
                    shown_trigger_key = omen_trigger_key
                    # PATCH: Increment omens_witnessed
                    self.player['omens_witnessed'] = self.player.get('omens_witnessed', 0) + 1
                    self.logger.info(f"Player witnessed omen '{shown_trigger_key}' - fear increased to {self.player.get('fear', 0)}")
                else:
                    self.logger.debug("No omen shown; skipping fear update for examine.")
            if omen_shown and self.death_ai:
                self.death_ai.update_fear('examine_omen')
        except Exception as e:
            self.logger.error(f"_examine_omen: Error: {e}", exc_info=True)

    def _hazard_has_tag(self, hazard_key: str, target_tag: str) -> bool:
        """
        Checks if a specific hazard definition contains a specific tag.
        Useful for character perks (e.g., Engineer sensing 'mechanical' hazards).
        """
        # 1. Load the Master Data for hazards
        hazards_master = self.resource_manager.get_data('hazards', {})
        
        # 2. Get the definition for hazard_key
        h_def = hazards_master.get(hazard_key, {})
        
        # 3. Retrieve the 'tags' list (default to empty list if missing)
        # We default to 'tags', but for backward compatibility, we check 'categories' too
        # until you complete your standardization refactor.
        tags = h_def.get('tags', [])
        if not tags:
            tags = h_def.get('categories', [])
        
        # 4. Return True if target_tag is in the list
        # We normalize to lowercase to prevent "Mechanical" != "mechanical" errors
        return target_tag.lower() in [t.lower() for t in tags]

    # --- 'Force/Break' handlers
    def _command_force(self, target_str: str) -> dict:
        """Handle the force action."""
        if not target_str:
            return self._build_response(message="Force what? (e.g., 'force door', 'force south')")
            
        target_norm = normalize_text(target_str)
        target_str = target_str.strip()
        
        parsed = self._parse_force_command(target_str)
        target_name = parsed['target_name']
        tool_name = parsed['tool_name']

        current_room_id = self.player.get('location')
        room = self.get_room_data(current_room_id) or {}
        exits = room.get('exits', {}) or {}

        tool_key, bonus = self._resolve_force_tool(tool_name, current_room_id)
        if tool_key is False:  # error already returned
            return bonus

        exit_result = self._force_try_exit(target_name, tool_key, bonus, room, exits)
        if exit_result is not None:
            return exit_result

        return self._force_or_break_entity(target_name, tool_key)

    def _command_break(self, target_name_str: str) -> dict:
        target_name_str = (target_name_str or "").strip()
        if not target_name_str:
            self.logger.info("_break_main: No target specified.")
            return self._build_response(message="Break what?", turn_taken=False)

        parsed = self._parse_force_command(target_name_str)
        tool_key = None
        if parsed.get('tool_name'):
            tool_entity = self._find_entity_in_room(parsed['tool_name'], self.player.get('location'))
            if not tool_entity or tool_entity.get('type') != 'item_inventory':
                msg = f"You don't have a {parsed['tool_name']}."
                self.logger.info(f"_break_main: {msg}")
                return self._build_response(message=msg, turn_taken=False)
            if not self._is_tool_item(tool_entity['id_key']):
                msg = f"The {tool_entity['name']} isn't suited for breaking things."
                self.logger.info(f"_break_main: {msg}")
                return self._build_response(message=msg, turn_taken=False)
            tool_key = tool_entity['id_key']
        else:
            tool_key, _ = self._best_tool_in_inventory()

        return self._force_or_break_entity(parsed['target_name'], tool_key)

    def _resolve_force_tool(self, tool_name: str, current_room_id: str):
        """Helper to resolve the tool for forcing, with logging and error handling."""
        try:
            if tool_name:
                tool_entity = self._find_entity_in_room(tool_name, current_room_id)
                if not tool_entity or tool_entity.get('type') != 'item_inventory':
                    msg = f"You don't have a {tool_name}."
                    self.logger.info(f"_resolve_force_tool: {msg}")
                    return False, self._build_response(message=msg, turn_taken=False)
                if not self._is_tool_item(tool_entity['id_key']):
                    msg = f"The {tool_entity['name']} isn't suited for forcing things."
                    self.logger.info(f"_resolve_force_tool: {msg}")
                    return False, self._build_response(message=msg, turn_taken=False)
                tool_key = tool_entity['id_key']
                bonus = self._tool_bonus(tool_key)
                self.logger.debug(f"_resolve_force_tool: Using explicit tool '{tool_key}' with bonus {bonus}")
                return tool_key, bonus
            else:
                tool_key, bonus = self._best_tool_in_inventory()
                self.logger.debug(f"_resolve_force_tool: Using best available tool '{tool_key}' with bonus {bonus}")
                return tool_key, bonus
        except Exception as e:
            self.logger.error(f"_resolve_force_tool: Error: {e}", exc_info=True)
            return False, self._build_response(message="Error resolving tool.", turn_taken=False)
        
    def _force_try_exit(self, target_name: str, tool_key, bonus, room, exits) -> Optional[dict]:
        """
        Handles 'force' on an exit/door, using hazard player_interaction QTE if present.
        PATCHED: Robust fuzzy matching for MRI door targets.
        """
        tnorm = normalize_text(target_name)
        
        # 1. Identify which exit is being targeted
        matched_dir = None
        matched_dest = None
        
        for direction, dest in exits.items():
            # Handle dict exits (lock info on the exit itself)
            if isinstance(dest, dict):
                dest_target = dest.get('target', '')
                d_norm = normalize_text(direction)
                dest_norm = normalize_text(dest_target)
                if (tnorm == d_norm or tnorm == f"{d_norm} door" or
                    tnorm == dest_norm or tnorm == "door" or tnorm == "back door"):
                    if not dest.get('forceable', False):
                        return self._build_response(
                            message=f"The way {direction} can't be forced open.",
                            turn_taken=False
                        )
                    matched_dir = direction
                    matched_dest = dest_target
                    # If it's locked, forcing it should unlock it
                    self._dict_exit_ref = dest  # Store reference for unlock on success
                    break
                continue
            
            # Standard string exits
            d_norm = normalize_text(direction)
            dest_norm = normalize_text(dest)
            
            if (tnorm == d_norm or 
                tnorm == f"{d_norm} door" or 
                tnorm == dest_norm or 
                tnorm == "door"):
                matched_dir = direction
                matched_dest = dest
                break

        if not matched_dir:
            return None

        # 2. Check for Active MRI Hazard in current room
        mri_hazard_id = None
        mri_hazard = None
        if self.hazard_engine:
            mri_hazard_id = self.hazard_engine.get_hazard_instance_id_by_type(self.player.get('location'), "mri")
            if mri_hazard_id:
                mri_hazard = self.hazard_engine.active_hazards.get(mri_hazard_id)

        qte_ctx = None
        
        # 3. Check Hazard-Specific Force Rules
        if mri_hazard:
            mri_state = mri_hazard.get('state')
            h_master = mri_hazard.get('master_data', {})
            force_rules = (h_master.get('player_interaction', {}).get('force', []) if h_master else [])
            
            for rule in force_rules:
                # State check
                required_states = rule.get('requires_hazard_state')
                if required_states and mri_state not in required_states:
                    continue
                
                # Target Name Check (The Critical Fix)
                valid_targets = rule.get('on_target_name', [])
                if isinstance(valid_targets, str): valid_targets = [valid_targets]
                
                # We check if the rule targets this specific direction or destination
                is_match = False
                for t in valid_targets:
                    t_norm = normalize_text(t)
                    # Does rule target "west", "west door", "control room door"?
                    if (t_norm == normalize_text(matched_dir) or 
                        t_norm == f"{normalize_text(matched_dir)} door" or
                        t_norm == normalize_text(matched_dest) or
                        (t_norm == "door" and matched_dir)): # 'door' matches any exit if generic
                        is_match = True
                        break
                
                if is_match:
                    qte_ctx = rule.get('qte_context', {}).copy()
                    qte_ctx['qte_source_hazard_id'] = mri_hazard_id
                    break

        # 4. Execute Force Action
        if qte_ctx:
            # MRI Case: Hazard-defined QTE
            effects_on_success = [{"type": "unlock_room", "room_id": matched_dest}]
            # Also unlock the dict exit reference if this was a dict exit
            if hasattr(self, '_dict_exit_ref') and self._dict_exit_ref:
                effects_on_success.append({"type": "unlock_dict_exit", "exit_ref": id(self._dict_exit_ref)})
            qte_ctx.setdefault('effects_on_success', effects_on_success)
            qte_ctx.setdefault('pending_move', matched_dir)
            
            self.player['qte_active'] = True
            self.qte_engine.start_qte("button_mash", qte_ctx)
            self.logger.info(f"_force_try_exit: MRI QTE started for forcing door to '{matched_dest}'")
            return self._build_response(message="You brace yourself against the magnetically sealed door...", turn_taken=True)

        # 5. Fallback: Standard Force QTE
        dest_data = self.get_room_data(matched_dest) or {}
        strength = self._get_stat('strength', 1)
        # Use dest_data force_threshold if available, else room default
        target_obj = dest_data if dest_data.get('forceable') else room
        
        tgt_mash = self._compute_force_difficulty(target_obj, base=16, strength=strength, tool_bonus=bonus)
        effects_on_success = [{"type": "unlock_room", "room_id": matched_dest}]
        
        ctx = {
            "ui_mode": "in-screen",
            "ui_prompt_message": f"The door resists! {'Use your tool! ' if tool_key else ''}Mash to force it!",
            "target_mash_count": tgt_mash,
            "duration": 4.0,
            "success_message": f"You wrench the door to {matched_dest.replace('_', ' ')} open just enough to slip through!",
            "failure_message": "You can't budge it. Your arms ache.",
            "hp_damage_on_failure": 0,
            "effects_on_success": effects_on_success,
            "pending_move": matched_dir,
        }
        
        self.player['qte_active'] = True
        self.qte_engine.start_qte("button_mash", ctx)
        self.logger.info(f"_force_try_exit: QTE started for forcing door to '{matched_dest}'")
        return self._build_response(message="You brace yourself and shove.", turn_taken=True)
    
    # --- ELEVATOR HELPERS ---
    def _is_elevator_room(self, room_id: str) -> bool:
        """Check if current room acts as an elevator car."""
        try:
            # Simple check based on room name or data property
            if room_id == "Elevator Car":
                return True
            data = self.get_room_data(room_id) or {}
            beh = data.get('special_room_behavior', {})
            return beh.get('type') == 'elevator'
        except Exception as e:
            self.logger.error(f"_is_elevator_room: Error: {e}", exc_info=True)
            return False

    def _elevator_floor_from_room(self, room_id: str) -> int:
        """Derive floor number from a lobby room name or stored mapping."""
        lobby_map = {"Basement Elevator Lobby": -1, "Elevator Lobby (Level 1)": 1, "Upper Floor Elevator Lobby": 2}
        return lobby_map.get(room_id, self.player.get('elevator_current_floor', 1))

    def _resolve_elevator_target(self, direction: str) -> tuple:
        """
        Resolve the destination room & new floor for a direction typed inside the Elevator Car.
        Reads floor_mappings from the room's exit data, OR falls back to a hardcoded map
        if the JSON uses direct string exits like 'floor 2': 'Upper Floor Elevator Lobby'.
        Returns (dest_room, new_floor) or (None, None) if invalid.
        """
        current_room_id = self.player.get('location')
        current_room = self.get_room_data(current_room_id) or {}
        exits = current_room.get('exits', {})

        norm_dir = direction.strip().lower()

        # --- Strategy 1: Dynamic destination with floor_mappings dict ---
        out_exit = exits.get('out', {})
        if isinstance(out_exit, dict) and out_exit.get('dynamic_destination'):
            floor_mappings = out_exit.get('floor_mappings', {})
            current_floor = self.player.get('elevator_current_floor', 1)

            target_floor = None
            if norm_dir in ('up', 'u'):
                target_floor = current_floor + 1
            elif norm_dir in ('down', 'd'):
                target_floor = current_floor - 1
            elif norm_dir in ('b', 'basement', '-1'):
                target_floor = -1
            elif norm_dir in ('1', 'one', 'floor 1', 'l1', 'ground', 'ground floor'):
                target_floor = 1
            elif norm_dir in ('2', 'two', 'floor 2', 'l2', 'upper', 'upper floor'):
                target_floor = 2
            elif norm_dir in ('out', 'exit', 'leave'):
                target_floor = current_floor

            dest_room = floor_mappings.get(str(target_floor)) if target_floor is not None else None
            if dest_room:
                return dest_room, target_floor

        # --- Strategy 2: Direct string exits in the JSON ('floor 2': 'Upper Floor Elevator Lobby') ---
        # Try exact match first
        if norm_dir in exits:
            dest = exits[norm_dir]
            if isinstance(dest, str):
                return self._infer_floor_and_room(dest, norm_dir)

        # Try fuzzy floor number matching against exit keys
        floor_aliases = {
            'floor 2': ['floor 2', 'l2', '2', 'upper', 'upper floor', 'floor2'],
            'floor 1': ['floor 1', 'l1', '1', 'ground', 'ground floor', 'floor1'],
            'basement': ['basement', 'b', '-1', 'floor -1'],
            'out': ['out', 'exit', 'leave'],
        }
        for exit_key, dest in exits.items():
            if not isinstance(dest, str):
                continue
            exit_norm = exit_key.strip().lower()
            for canonical, aliases in floor_aliases.items():
                if exit_norm in aliases or exit_norm == canonical:
                    if norm_dir in aliases or norm_dir == canonical:
                        return self._infer_floor_and_room(dest, exit_norm)

        return None, None


    def _infer_floor_and_room(self, dest_room: str, direction_hint: str) -> tuple:
        """
        Given a destination room name, infer which floor number it represents.
        """
        name_lower = dest_room.lower()
        if 'basement' in name_lower:
            return dest_room, -1
        if 'upper' in name_lower or 'level 2' in name_lower or 'floor 2' in name_lower:
            return dest_room, 2
        if 'level 1' in name_lower or 'ground' in name_lower or 'floor 1' in name_lower:
            return dest_room, 1
        # Fallback: infer from direction hint
        if 'basement' in direction_hint or '-1' in direction_hint:
            return dest_room, -1
        if '2' in direction_hint or 'upper' in direction_hint:
            return dest_room, 2
        return dest_room, 1

    def record_evaded_hazard(self, hazard_id: str, method: str = "survived"):
        """
        Records a hazard as 'evaded' or 'neutralized' for the end-of-level report.
        """
        if not hazard_id or not self.hazard_engine:
            return

        # De-duplicate: Don't record the same hazard ID twice
        current_evaded = self.player.get('evaded_hazards', [])
        if any(entry.get('id') == hazard_id for entry in current_evaded):
            return

        hazard = self.hazard_engine.active_hazards.get(hazard_id)
        if not hazard:
            # Try to parse type from ID if instance is gone
            h_type = hazard_id.split('#')[0]
            name = h_type.replace('_', ' ').title()
            desc = "Hazard neutralized."
        else:
            master = hazard.get('master_data', {})
            name = master.get('name', hazard.get('type', 'Unknown Threat'))
            # Use current state description or generic
            state = hazard.get('state')
            desc = master.get('states', {}).get(state, {}).get('description', 'Threat neutralized.')

        entry = {
            "id": hazard_id,
            "name": name,
            "description": desc,
            "method": method,  # 'survived', 'neutralized', 'banished'
            "timestamp": self.player.get('turns_left')
        }
        
        self.player.setdefault('evaded_hazards', []).append(entry)
        self.logger.info(f"Recorded evaded hazard: {name} ({method})")

    def apply_damage(self, amount: int, source: str = "unknown"):
        """
        Centralized method for applying damage.
        Handles HP reduction, death checks, and AUDIO triggers.
        """
        # Guard clause: Ignore 0 or negative damage (unless you want 0-damage events)
        if amount <= 0:
            return

        # 1. The Physical Toll
        self.player['hp'] = max(0, self.player['hp'] - amount)
        self.logger.info(f"Player took {amount} damage from {source}. HP: {self.player['hp']}")

        # 2. The Auditory Reaction (The Logic Fork)
        if self.audio_manager:
            if self.player['hp'] <= 0:
                # Priority: Death silences the grunt. Play the final sting.
                self.audio_manager.play_sfx("final_hit")
            else:
                # Survival: Play the pain grunt.
                self.audio_manager.play_sfx("medium_hit")

        # 2.5 Visual feedback
        self.add_ui_event({"event_type": "screen_shake", "intensity": 15})
        self.add_ui_event({
            "event_type": "screen_flash",
            "color": "ff0000",
            "duration": 0.3,
            "opacity": 0.4
        })
        self.add_ui_event({"event_type": "refresh_ui"})

        # 3. The Mortal Coil (Death Check)
        if self.player['hp'] <= 0:
            self.is_game_over = True
            if not self.player.get('death_reason'):
                self.player['death_reason'] = f"Killed by {source}."
            
            self.add_ui_event({
                "event_type": "game_over",
                "death_reason": self.player['death_reason'],
                "final_narrative": self.get_death_narrative()
            })

    def _handle_elevator_move(self, direction: str, dest_room: str = None) -> dict:
        self.logger.info(f"[ELEVATOR DEBUG] Initiating move to '{direction}'")
        if self.player.get('elevator_transit_active'):
            return self._build_response(message="The elevator is already in motion.", turn_taken=False, success=False)

        dest_room, new_floor = getattr(self, '_resolve_elevator_target', lambda d: (None, None))(direction)
        if not dest_room:
            self.logger.warning(f"[ELEVATOR DEBUG] Button '{direction}' failed to resolve to a destination.")
            return self._build_response(message="That button doesn't seem to work.", turn_taken=False, success=False)

        # Lock in the destination
        self.player['pending_elevator_dest'] = dest_room
        self.player['pending_elevator_floor'] = new_floor
        self.player['elevator_transit_active'] = True

        # Set hazard to 'moving' state silently
        if getattr(self, 'hazard_engine', None):
            hid = self.hazard_engine.get_hazard_instance_id_by_type("Elevator Car", "elevator_freefall")
            if hid:
                self.hazard_engine.set_hazard_state(hid, "moving", suppress_entry_effects=True)

        # --- Force UI Events directly into the queue ---
        self.add_ui_event({"event_type": "refresh_map"})
        self.add_ui_event({"event_type": "schedule_transit", "duration": 4.0})
        self.add_ui_event({
            "event_type": "show_message",
            "message": "\n[color=aaaaaa]The elevator doors slide shut. The car begins to move...[/color]\n"
        })

        self.logger.info("[ELEVATOR DEBUG] Protected UI timer injected into queue.")
        return self._build_response(message="", turn_taken=False, success=True)

    def process_elevator_arrival(self) -> dict:
        self.logger.info("[ELEVATOR DEBUG] process_elevator_arrival triggered by UI timer.")
        if not self.player.get('elevator_transit_active'):
            return self._build_response()

        if self.player.get('location') != "Elevator Car":
            self.player.pop('elevator_transit_active', None)
            return self._build_response()

        hid = None
        if getattr(self, 'hazard_engine', None):
            hid = self.hazard_engine.get_hazard_instance_id_by_type("Elevator Car", "elevator_freefall")

        if hid and self.hazard_engine:
            h = self.hazard_engine.active_hazards.get(hid, {})
            current_state = h.get('state', 'idle')

            if current_state not in ["idle", "moving"]:
                # --- THE AGILITY DODGE FIX ---
                if not self.player.get('qte_active'):
                    self.logger.info("[ELEVATOR DEBUG] Agility dodge detected! Self-healing to idle.")
                    self.hazard_engine.set_hazard_state(hid, "idle", suppress_entry_effects=True)
                    return self.finalize_elevator_arrival()

                self.logger.info("[ELEVATOR DEBUG] Waiting on active QTE. Polling in 2s.")
                return self._build_response(ui_events=[{"event_type": "schedule_transit", "duration": 2.0}])

            if current_state == 'moving':
                master = h.get('master_data', {})
                state_def = master.get('states', {}).get('moving', {})
                chance = float(state_def.get('chance_to_progress', 0.35))

                import random
                if chance > 0 and random.random() < chance:
                    next_state = state_def.get('next_state', 'shaking')
                    self.logger.warning(f"[ELEVATOR DEBUG] Hazard escalating to '{next_state}'")
                    result = self.hazard_engine.set_hazard_state(hid, next_state)

                    for cons in result.get('consequences', []):
                        self.handle_hazard_consequence(cons)

                    return self._build_response(
                        messages=result.get("messages", []),
                        ui_events=[{"event_type": "schedule_transit", "duration": 2.0}]
                    )

        self.logger.info("[ELEVATOR DEBUG] Safe arrival executing.")
        return self.finalize_elevator_arrival()

    def finalize_elevator_arrival(self) -> dict:
        dest_room = self.player.pop('pending_elevator_dest', None)
        new_floor = self.player.pop('pending_elevator_floor', None)
        self.player.pop('elevator_transit_active', None)
        
        timer = getattr(self, '_elevator_timer', None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
            self._elevator_timer = None

        if not dest_room or dest_room not in self.current_level_rooms_world_state:
            return self._build_response(message="The doors open to a brick wall. Something broke.", turn_taken=False)

        self.player['location'] = dest_room
        self.player.setdefault('visited_rooms', set()).add(dest_room)
        if new_floor is not None:
            self.player['elevator_current_floor'] = new_floor

        if getattr(self, 'hazard_engine', None):
            self.hazard_engine.reset_elevator_hazard("Elevator Car")
            ambush_cons = self.hazard_engine.trigger_ambushes_for_room(dest_room)
            for c in ambush_cons:
                self.handle_hazard_consequence(c)

        if getattr(self, 'audio_manager', None):
            try:
                self.audio_manager.play_sfx("elevator_end")
            except Exception:
                pass

        room_desc = self._get_rich_room_description(dest_room)
        self.logger.info(f"[ELEVATOR DEBUG] Successfully ejected player to '{dest_room}'.")
        return self._build_response(
            message=f"*DING* — The doors slide open.\n\n{room_desc}",
            turn_taken=False,
            success=True,
            ui_events=[{"event_type": "refresh_map"}]
        )

    # ------------------------------------------------------------------------
    # --- ENDGAME CALCULATION & EPILOGUES ---
    # ------------------------------------------------------------------------

    def _trigger_finale(self, finale_type: str):
        """Hijacks the game state and sends the player to their specific epilogue."""
        self.logger.info(f"Endgame triggered: {finale_type}")
        
        self.game_won = True
        self.is_game_over = True

        # Park the ending type in app state so calculate_ending can read it
        from kivy.app import App
        app = App.get_running_app()
        if app:
            app.epilogue_type = finale_type

        # Fetch the epic custom narrative from your dispatcher!
        narrative = self.calculate_ending()

        current_city = self.player.get('current_city', 'the city')
        flavor_text = flavor_text.replace('{city_name}', current_city)
        final_narrative = final_narrative.replace('{city_name}', current_city)

        # Fire the standard game_over event to trigger your existing UI
        self.add_ui_event({
            "event_type": "game_over",
            "death_reason": "You broke the design.",
            "final_narrative": narrative,
            "hide_stats": False
        })


    def _start_finale_qte_chain(self, finale_type: str):
        self.logger.info(f"Generating Randomized QTE Gauntlet for {finale_type}")
        import random
        
        qte_defs = getattr(self.qte_engine, 'qte_definitions', {}) if hasattr(self, 'qte_engine') else {}
        valid_types = list(qte_defs.keys())
        if not valid_types:
            valid_types = ["button_mash", "spam_any_key", "hold_to_threshold", "reaction_single_key"]

        # --- FINALE MODIFIERS (State & Hub choices) ---
        modifiers = self.player.get('finale_modifiers', {})
        duration_reduction = modifiers.get('duration_reduction', 0)
        duration_extension = modifiers.get('duration_extension', 0)
        simplify = modifiers.get('simplify_qtes', False)
        skeptic_risk = modifiers.get('skeptic_risk', 0)

        # Build simplified QTE pool if authority figure helped
        if simplify:
            valid_types = ["button_mash", "spam_any_key", "hold_to_threshold", "reaction_single_key"]

        # --- THE COMPANION MODIFIERS ---
        companions = self.player.get('companions', [])
        persistent_roles = self.player.get('npc_roles', {})
        
        time_modifier = 0.0
        mash_modifier = 0
        party_narrative = ""

        for comp in companions:
            role = persistent_roles.get(comp.lower(), 'bystander_1')
            if role == 'skeptic':
                time_modifier -= 1.0  # Skeptic hesitates, stealing 1 second!
                party_narrative += f"{comp} is freezing up! You have to push them! "
            elif role == 'friend':
                mash_modifier += 4    # Friend helps, reducing mash count by 4!
                party_narrative += f"{comp} grabs hold to help you! "
            elif role == 'panicking':
                time_modifier -= 0.5
                party_narrative += f"{comp} is screaming, distracting you! "
            elif role == 'visionary':
                time_modifier += 1.5  # Visionary anticipates, giving you extra time!
                party_narrative += f"{comp} calls out the danger before it happens! "
        # -------------------------------

        if finale_type == "trigger_finale_override":
            prompts = ["Apply the pads! ACT NOW!", "The machine surges! HOLD ON!", "300 JOULES! CLEAR!"]
        elif finale_type == "trigger_finale_flatline":
            prompts = ["Fight the panic! STAY CALM!", "Vision fading... STAY CONSCIOUS!", "Adrenaline hits! LIVE!"]
        elif finale_type == "trigger_finale_dark_path":
            prompts = ["They try to push you away! HOLD THEM!", "They are fighting back! OVERPOWER THEM!", "Take their time! DO IT NOW!"]
        else:
            prompts = ["They lunge at you! DODGE!", "Grapple them! FIGHT BACK!", "Push them into the hazard! NOW!"]
            
        chain = []
        for i, prompt in enumerate(prompts):
            # Apply skeptic interference: last QTE gets a shorter window
            is_last = (i == len(prompts) - 1)
            skeptic_penalty = 0
            if is_last and skeptic_risk > 0:
                if random.random() < skeptic_risk:
                    skeptic_penalty = 1.5
                    self.add_ui_event({"event_type": "show_message",
                        "message": "\n[color=ff8800]At the critical moment, a hand grabs your arm — "
                                   "\"STOP! You're going to kill yourself!\" "
                                   "You shake free, but precious seconds are lost.[/color]\n"})

            # Apply all modifiers dynamically
            base_duration = 5.0 + random.uniform(0.5, 1.5)
            final_duration = max(
                2.5,  # never below 2.5s
                base_duration
                + time_modifier
                + duration_extension
                - duration_reduction
                - skeptic_penalty
            )
            
            full_prompt = f"{party_narrative}\n\n{prompt}" if party_narrative else prompt
            
            qte_context = {
                "ui_prompt_message": full_prompt,
                "is_fatal_on_failure": True
            }
            
            # Select type and apply mash buff if applicable
            chosen_type = random.choice(valid_types)
            if "mash" in chosen_type: 
                qte_context["target_mash_count"] = max(5, 18 - mash_modifier)

            chain.append({
                "qte_type": chosen_type, 
                "duration": final_duration,
                "qte_context": qte_context
            })
            
        self.player['pending_finale_victory'] = finale_type
        self.player['finale_qte_chain'] = chain
        self._trigger_next_finale_qte()

    def _trigger_next_finale_qte(self):
        chain = self.player.get('finale_qte_chain', [])
        if not chain:
            finale_type = self.player.get('pending_finale_victory')
            self.logger.info(f"Finale QTE Chain Conquered! Triggering epilogue: {finale_type}")
            self.player['pending_finale_victory'] = None  
            self._trigger_finale(finale_type)
            return
            
        next_qte = chain.pop(0)
        self.player['finale_qte_chain'] = chain 
        
        qte_type = next_qte.get("qte_type")
        prompt = next_qte.get("ui_prompt_message", "Act quickly!")
        
        self.logger.info(f"Queueing Breather Popup for next QTE: {qte_type}")
        
        # --- THE FIX: The Breather Popup ---
        # The QTE will NOT start until the player clicks 'Close' on this popup!
        self.add_ui_event({
            "event_type": "show_popup",
            "title": "PHASE SHIFT",
            "message": f"The danger escalates. Prepare yourself!\n\n[b][color=ff0000]NEXT UP:[/color][/b] {prompt}",
            "priority": 500,
            "on_close_start_qte": {
                "qte_type": qte_type,
                "qte_context": next_qte
            }
        })
        
        # Signal that this qte_active was set by the finale chain,
        # so _bind_popup_defers knows NOT to skip on_close_start_qte
        self.player['qte_active'] = True
        self.player['finale_qte_chain_pending'] = True

    def _setup_finale_room(self):
        """
        Prepares the crossroads_room for the finale based on the player's run.
        Injects:
        1. Surviving companion NPCs with archetype-driven dialogue
        2. Plan C (Dark Path) if player has killed before
        3. Safety-net item seeding for any finale items not yet collected
        """
        import copy

        room = self.current_level_rooms_world_state.get('crossroads_room')
        if not room:
            self.logger.error("_setup_finale_room: crossroads_room not found in world state.")
            return

        companions       = self.player.get('companions', [])
        npc_status       = self.player.get('npc_status', {})
        npc_roles        = self.player.get('npc_roles', {})
        deaths_list      = self.player.get('deaths_list', [])
        witnessed_deaths = self.player.get('witnessed_deaths', [])
        inventory_keys   = {str(i).lower() for i in self.player.get('inventory', [])}
        is_fugitive      = self.player.get('is_fugitive', False)
        has_killed       = self.player.get('has_killed_npc', False)
        alive_count      = sum(1 for n, s in npc_status.items() if s in ('alive', 'injured'))
        dead_count       = sum(1 for n, s in npc_status.items() if s == 'dead')

        # ── 1. Inject surviving companion NPCs ──────────────────────────────────
        room.setdefault('npcs', [])
        placed_names = {
            (n.get('name','') if isinstance(n,dict) else n).lower()
            for n in room['npcs']
        }

        alive_companions = [
            c for c in companions
            if npc_status.get(c.lower(), 'alive') in ('alive', 'injured')
        ]

        for comp_name in alive_companions:
            if comp_name.lower() in placed_names:
                continue
            role = npc_roles.get(comp_name.lower(), 'friend')
            comp_npc = {
                "name": comp_name.title(),
                "role": role,
                "description": self._get_finale_companion_description(comp_name, role, alive_count, dead_count),
                "initial_state": "greeting",
                "dialogue_states": self._get_finale_companion_dialogue(comp_name, role, alive_count, dead_count, is_fugitive),
                "examinable": True,
                "is_finale_companion": True,
            }
            room['npcs'].append(comp_npc)
            placed_names.add(comp_name.lower())
            self.logger.info(f"_setup_finale_room: Injected companion '{comp_name}' ({role}) into crossroads_room.")

        # ── 2. Add Plan C (Dark Path) if player has blood on their hands ─────────
        # OR if the player is a fugitive with no companions — desperation unlocks it
        dark_path_unlocked = has_killed or (is_fugitive and not alive_companions)
        plan_c_already_present = any(
            (n.get('name','') if isinstance(n,dict) else n).lower() == 'plan c: the dark path'
            for n in room.get('npcs', [])
        )

        if dark_path_unlocked and not plan_c_already_present:
            victim_name = self._get_dark_path_target(deaths_list, npc_status)
            plan_c = {
                "name": "Plan C: The Dark Path",
                "action_verb": "talk",
                "description": "A length of rope, a discarded scalpel, a specific name on the list. You know what this means.",
                "initial_state": "start",
                "dialogue_states": {
                    "start": {
                        "text": (
                            f"Bludworth's notes were clear: if you can transfer your remaining time to Death's "
                            f"next chosen victim — {victim_name} — Death's balance is restored without your name on the ledger. "
                            f"You need no special items. Only the will to do what must be done."
                        ),
                        "options": [
                            {"text": "Do it.", "target_state": "execute"},
                            {"text": "Step away.", "target_state": "start"}
                        ]
                    },
                    "execute": {
                        "text": (
                            f"You look at {victim_name}. They don't know what's coming. "
                            f"No one ever does."
                        ),
                        "on_talk_action": {
                            "action_effect": "trigger_finale_dark_path",
                            "requires_items": []
                        }
                    }
                }
            }
            room['npcs'].append(plan_c)
            self.logger.info(f"_setup_finale_room: Plan C (Dark Path) unlocked. Target: {victim_name}")

        # ── 3. Safety-net item seeding ───────────────────────────────────────────
        # If the player reached the finale without collecting required items,
        # plant them in the crossroads_room as loose items. This ensures the
        # finale is always completable — the cost is narrative (you improvised
        # instead of prepared).
        finale_item_sets = {
            "trigger_finale_override":   ["warehouse_key", "defibrillator_pads"],
            "trigger_finale_flatline":   ["vet_sedatives", "adrenaline"],
        }
        all_finale_keys = set()
        for keys in finale_item_sets.values():
            all_finale_keys.update(keys)

        items_master = self.resource_manager.get_data('items', {})
        seeded = []
        for item_key in all_finale_keys:
            if item_key not in inventory_keys:
                # Only seed if the item actually exists in master data
                if item_key in items_master:
                    room.setdefault('items', [])
                    if item_key not in room['items']:
                        room['items'].append(item_key)
                        seeded.append(item_key)

        if seeded:
            item_names = [items_master.get(k, {}).get('name', k) for k in seeded]
            self.add_ui_event({
                "event_type": "show_message",
                "message": (
                    f"\n[color=aaaaaa]Scattered among the debris, you spot items that weren't there before. "
                    f"As if the room itself prepared for your arrival: "
                    f"{', '.join(item_names)}.[/color]\n"
                )
            })
            self.logger.info(f"_setup_finale_room: Safety-net seeded {seeded} into crossroads_room.")

    def _get_finale_companion_description(self, name: str, role: str, alive_count: int, dead_count: int) -> str:
        """Returns a description for a companion appearing in the finale."""
        base = f"{name.title()} is here with you."
        if role == 'visionary':
            return f"{base} They look drained — every premonition costs them something. But they came."
        elif role == 'skeptic':
            return f"{base} They don't say anything. They don't have to. Being here is enough."
        elif role == 'friend':
            return f"{base} Their presence is steadying. They believed in you when no one else did."
        elif role == 'authority_figure':
            return f"{base} Badge off. Off the record. They chose you over the system."
        else:
            return f"{base} Against all odds, they made it this far."

    def _get_finale_companion_dialogue(self, name: str, role: str, alive_count: int, dead_count: int, is_fugitive: bool) -> dict:
        """
        Generates archetype-driven dialogue for companions in the finale.
        All strings use straight ASCII quotes only — no curly/smart quotes.
        """
        name_title = name.title()
        many_dead = dead_count >= 4
        few_survived = alive_count <= 2

        # ── Visionary ─────────────────────────────────────────────────────────
        if role == "visionary":
            if many_dead:
                main_text = (
                    name_title + " is barely holding together. They have watched the list execute "
                    "itself, name by name, exactly as they saw it. "
                    "\"I didn't want to be right,\" they whisper. \"I never wanted to be right.\""
                )
            else:
                main_text = (
                    name_title + " grips your arm. Their eyes are glassy -- the strain of knowing "
                    "is written all over their face. "
                    "\"I can feel it. The design. It's almost complete. "
                    "Whatever you're going to do, do it NOW.\""
                )
            return {
                "greeting": {
                    "text": main_text,
                    "options": [
                        {"text": "We are going to end this.", "target_state": "committed"},
                        {"text": "What do you see right now?", "target_state": "premonition"}
                    ]
                },
                "committed": {
                    "text": "\"Then let's finish it. Whatever it takes.\""
                },
                "premonition": {
                    "text": (
                        "\"Two paths. Both end in something unrecognisable. "
                        "One path, you wake up. The other -- you don't. "
                        "But either way... it ends.\""
                    ),
                    "on_talk_action": {
                        "action_effect": "grant_finale_bonus",
                        "bonus_type": "qte_duration_reduction",
                        "amount": 2.0,
                        "one_time": True
                    }
                }
            }

        # ── Skeptic ───────────────────────────────────────────────────────────
        elif role == "skeptic":
            skeptic_text = (
                name_title + " looks at the equipment laid out before you. "
                "\"You know this is insane. You know that, right? "
                "I have watched three people die in front of me and I still "
                "can't believe what I'm about to help you do.\""
            )
            return {
                "greeting": {
                    "text": skeptic_text,
                    "options": [
                        {"text": "I need you to trust me. Just this once.", "target_state": "reluctant_trust"},
                        {"text": "Then don't help. Stand back.", "target_state": "stand_back"}
                    ]
                },
                "reluctant_trust": {
                    "text": (
                        "\"Fine. Tell me what to do and I'll do it. "
                        "But if this kills you, I am never forgiving myself.\""
                    ),
                    "on_talk_action": {
                        "action_effect": "grant_finale_bonus",
                        "bonus_type": "qte_failure_forgiveness",
                        "amount": 0.15,
                        "one_time": True
                    }
                },
                "stand_back": {
                    "text": (
                        "\"You want me to just watch? Fine. "
                        "But I am watching every second. "
                        "The moment something goes wrong, I am pulling you out.\""
                    ),
                    "on_talk_action": {
                        "action_effect": "grant_finale_bonus",
                        "bonus_type": "skeptic_interference_risk",
                        "amount": 0.20,
                        "one_time": True
                    }
                }
            }

        # ── Friend ────────────────────────────────────────────────────────────
        elif role == "friend":
            if few_survived:
                friend_text = (
                    name_title + "'s eyes carry the weight of everyone who didn't make it. "
                    "\"We owe it to them,\" they say quietly. \"Whatever this costs.\""
                )
            else:
                friend_text = (
                    name_title + " doesn't ask questions. "
                    "They hand you what you need before you ask. "
                    "\"I've been with you through all of it. I'm not stopping now.\""
                )
            return {
                "greeting": {
                    "text": friend_text,
                    "options": [
                        {"text": "I couldn't have made it here without you.", "target_state": "acknowledged"},
                        {"text": "Stay close. I might need you.", "target_state": "ready"}
                    ]
                },
                "acknowledged": {
                    "text": (
                        "\"Don't get sentimental on me now. "
                        "There's time for that later. After.\""
                    ),
                    "on_talk_action": {
                        "action_effect": "grant_finale_bonus",
                        "bonus_type": "qte_duration_extension",
                        "amount": 1.0,
                        "one_time": True
                    }
                },
                "ready": {
                    "text": "\"Right beside you. Always.\""
                }
            }

        # ── Authority Figure ──────────────────────────────────────────────────
        elif role == "authority_figure":
            return {
                "greeting": {
                    "text": (
                        name_title + " has removed their badge and placed it on the floor. "
                        "\"What we're about to do is illegal. Reckless. Probably fatal. "
                        "And I'd do it again tomorrow.\""
                    ),
                    "options": [
                        {"text": "Do you believe it now? All of it?", "target_state": "believer"},
                        {"text": "I need you on point.", "target_state": "ready"}
                    ]
                },
                "believer": {
                    "text": (
                        "\"I have seen the files. The dates don't line up. "
                        "The causes of death don't add up. "
                        "And I've watched it happen with my own eyes. "
                        "Yeah. I believe it. God help me.\""
                    ),
                    "on_talk_action": {
                        "action_effect": "grant_finale_bonus",
                        "bonus_type": "qte_type_simplify",
                        "one_time": True
                    }
                },
                "ready": {
                    "text": "\"Position secured. Do what you came here to do.\""
                }
            }

        # ── Default / Bystander ───────────────────────────────────────────────
        else:
            return {
                "greeting": {
                    "text": (
                        name_title + " is here. Scared, but here. "
                        "\"Just tell me what to do and I'll do it.\""
                    )
                }
            }

    def _get_dark_path_target(self, deaths_list: list, npc_status: dict) -> str:
        """Returns the name of the next living person on Death's list (excluding player)."""
        for name in deaths_list:
            if name.lower() == 'player':
                continue
            if npc_status.get(name.lower(), 'alive') in ('alive', 'injured'):
                return name.title()
        return "the next name on the list"

    def _build_death_epilogue(self, base_death_message: str) -> str:
        """
        Constructs the comprehensive Game Over narrative. 
        Calculates out-of-order death cascades (ahead of player) and 
        doomed survivor aftermaths (behind player).
        """
        deaths_list = self.player.get('deaths_list', [])
        npc_status = self.player.get('npc_status', {})
        current_idx = self.player.get('deaths_list_index', 0)
        workplaces = self.player.get('npc_workplaces', {})
        rm = self.resource_manager

        if not deaths_list:
            return base_death_message

        try:
            # Normalize list to lowercase to safely find the player's index
            lower_list = [n.lower() for n in deaths_list]
            player_idx = lower_list.index('player')
        except ValueError:
            return base_death_message

        narrative = ""

        # ─── BEAT 1: THE CASCADE (NPCs ahead of the player) ───────────────────
        if player_idx > current_idx:
            skipped_npcs = []
            for i in range(current_idx, player_idx):
                candidate = deaths_list[i].lower()
                if npc_status.get(candidate, 'alive') in ('alive', 'injured'):
                    skipped_npcs.append(candidate)

            if skipped_npcs:
                self.logger.info(f"Out of order death detected! Executing cascade for: {skipped_npcs}")
                narrative += (
                    "Death's design is absolute. As your life slipped away, a terrifying "
                    "realization washed over you: the timeline hadn't broken. It had accelerated.\n\n"
                    "To reach you, Death had to clear the board.\n\n"
                )
                for npc in skipped_npcs:
                    # Update status so the crawler/summary knows they died
                    self.player.setdefault('npc_status', {})[npc] = 'dead'
                    
                    wp_name = workplaces.get(npc, {}).get('workplace_name', 'somewhere in the city')
                    narrative += (
                        f"[color=ff0000]Off-screen at {wp_name}, {npc.title()} met a sudden, "
                        f"gruesome end, fulfilling their place on the list just seconds prior.[/color]\n"
                    )
                
                narrative += "\nWith the ledger balanced, Death finally claimed you.\n\n"

        # ─── BEAT 2: THE PLAYER'S DEATH ───────────────────────────────────────
        narrative += f"{base_death_message}\n\n"
        
        # Update the index so the game state is chronologically correct
        self.player['deaths_list_index'] = player_idx + 1

        # ─── BEAT 3: THE AFTERMATH (NPCs behind the player) ───────────────────
        doomed_npcs = []
        for i in range(player_idx + 1, len(deaths_list)):
            candidate = deaths_list[i].lower()
            if npc_status.get(candidate, 'alive') in ('alive', 'injured'):
                doomed_npcs.append(candidate)

        if doomed_npcs:
            narrative += (
                "[b]THE AFTERMATH[/b]\nWith you gone, the remaining survivors were left "
                "completely defenseless against Death's intricate design.\n\n"
            )
            
            import random
            for npc in doomed_npcs:
                # Kill them off so the final roster is accurate
                self.player.setdefault('npc_status', {})[npc] = 'dead' 
                
                # Randomize how they tried to cheat death (FD Lore)
                attempt = random.choice(['dark_path', 'revival', 'hide'])
                
                if attempt == 'dark_path':
                    narrative += f"Driven mad by paranoia, {npc.title()} tried to steal someone else's time by taking an innocent life. They hesitated. Death did not. A horrific 'accident' claimed them before they could strike.\n\n"
                elif attempt == 'revival':
                    narrative += f"{npc.title()} attempted to cheat the design by medically stopping their own heart, praying a companion would revive them. No one arrived in time. They died cold and alone on a clinic floor.\n\n"
                else:
                    narrative += f"{npc.title()} locked themselves away in a padded, 'safe' room, believing they could hide. A freak ventilation collapse and a single rogue spark proved otherwise.\n\n"

            narrative += "[color=ff0000]Death's list is finally complete.[/color]"
        else:
            narrative += "[color=ff0000]You were the last name on the list. Death's design is complete.[/color]"

        return narrative

    def _build_blood_loss_death_narrative(self) -> dict:
        """
        Constructs a full game_over payload for blood loss deaths.
        - Identifies NPCs scheduled to die ahead of player on deaths_list
        - Generates unique FD-style Rube Goldberg offscreen deaths for each
        - Rolls for whether NPCs behind player try to cheat death
        - Returns complete game_over event dict ready for add_ui_event
        """
        import random

        p = self.player
        deaths_list   = p.get('deaths_list', [])
        player_idx    = next((i for i, n in enumerate(deaths_list)
                            if str(n).lower() == 'player'), len(deaths_list))
        npc_status    = p.get('npc_status', {})
        workplaces    = p.get('npc_workplaces', {})
        current_city  = p.get('current_city', 'the city')
        disaster      = p.get('intro_disaster', {})
        location      = p.get('location', 'the hospital')

        # ── Categorize list members ─────────────────────────────────────────────
        npcs_ahead = [
            n for i, n in enumerate(deaths_list)
            if i < player_idx
            and str(n).lower() != 'player'
            and npc_status.get(str(n).lower(), 'alive') in ('alive', 'injured')
        ]
        npcs_behind = [
            n for i, n in enumerate(deaths_list)
            if i > player_idx
            and str(n).lower() != 'player'
            and npc_status.get(str(n).lower(), 'alive') in ('alive', 'injured')
        ]

        # ── FD death scenario building blocks ──────────────────────────────────
        CHAINS = [
            "knocking over a shelf that struck the main circuit breaker",
            "rupturing a gas line that caught on an exposed pilot light",
            "sending a loose cart into a structural support column",
            "shorting the sprinkler system which flooded the electrical panel",
            "dislodging a ceiling tile that struck a safety valve",
            "causing a pressure vessel to over-pressurize and rupture explosively",
            "overloading a power strip that ignited nearby materials",
            "snapping a tensioned cable that whipped across the room",
        ]
        FATES = [
            "crushed under collapsed equipment",
            "electrocuted when floodwater reached a live wire",
            "struck by shrapnel from a ruptured pipe fitting",
            "overcome by a gas cloud before anyone noticed the leak",
            "pinned beneath a toppled industrial shelving unit",
            "fatally struck by a section of ceiling that gave without warning",
            "thrown from a height when a platform gave way underfoot",
            "impaled by a length of rebar that sheared loose under pressure",
        ]
        WORKPLACE_PROPS = {
            'default':      [("a faulty conveyor belt", "an exposed gear housing"),
                            ("an unsecured overhead pipe", "a rusted wall bracket"),
                            ("a compressed air line", "a misfired nail gun")],
            'hospital':     [("an unlatched gurney", "an IV stand"),
                            ("an oxygen canister stored too close to a spark source", "a faulty wall outlet"),
                            ("a loose overhead light fixture", "a bed rail")],
            'construction': [("an unsecured scaffolding plank", "a length of exposed rebar"),
                            ("a snapped hoist cable", "a load of steel I-beams"),
                            ("an unattended angle grinder", "a sawdust accumulation")],
            'fairgrounds':  [("a ride harness latch", "a maintenance access panel"),
                            ("a loose electrical feed", "a water pipe junction"),
                            ("a toppled signage frame", "a crowd barrier")],
            'restaurant':   [("an unbalanced fryer oil container", "an open gas burner"),
                            ("a faulty refrigeration seal", "pooled condensation near the fuse box"),
                            ("a loose exhaust fan blade", "the grease trap below")],
            'warehouse':    [("an improperly secured pallet", "a racking upright"),
                            ("a leaking forklift hydraulic line", "an ignition point"),
                            ("a failed stretch-wrap machine arm", "the conveyor belt drive")],
            'bowling':      [("a jammed pin-setting arm", "the ball return mechanism"),
                            ("a snapped scoring system cable", "the overhead lighting rig"),
                            ("a lane oiler leak", "an electrical housing")],
            'community':    [("a chemical dispenser malfunction", "the pool pump housing"),
                            ("a cracked tile edge", "a water jet valve"),
                            ("a fallen lifeguard stand", "the pool drain cover")],
            'hotel':        [("a runaway luggage cart", "the revolving door mechanism"),
                            ("an overloaded laundry chute", "the service elevator counterweight"),
                            ("a faulty intercom surge", "an elevator cable tensioner")],
            'vet':          [("a pressurized anesthesia line", "a spark from a cauterizing tool"),
                            ("a loose X-ray arm", "the oxygen regulator"),
                            ("an autoclave door seal", "a toppled equipment rack")],
            'office':       [("a snapped elevator counterweight cable", "a mail cart"),
                            ("a shorted server rack", "the fire suppression nozzle"),
                            ("a toppled filing cabinet stack", "a glass partition railing")],
        }
        TEMPLATES = [
            ("{name} was killed at {workplace}. {obj_a} caught on {obj_b}, {chain}, "
            "and {name} had no way out in time. {fate}."),
            ("Emergency services found {name} at {workplace} after {obj_a} "
            "triggered a chain reaction — {chain} — that ended with {name} {fate}."),
            ("It took investigators three hours to reconstruct what happened to {name} "
            "at {workplace}. {obj_a} and {obj_b}. {chain}. {name} {fate}. "
            "The official report called it an accident."),
            ("{name}'s coworkers said they heard {obj_a} give way before anything else. "
            "Then {chain}. By the time anyone understood what was happening, {name} had already {fate}."),
        ]

        # ── SURVIVAL CHEAT rolls ────────────────────────────────────────────────
        NEAR_MISS = [
            ("{name} flatlined briefly at {workplace} and was revived. "
            "Whether Death considers that debt paid is another question entirely."),
            ("{name} walked away from {workplace} with injuries that should have been fatal. "
            "Nobody can explain what stopped the chain reaction when it did."),
            ("{name} was hospitalized after the incident at {workplace}, listed as stable. "
            "They've been asking questions about the others. Asking too many."),
        ]
        LIFE_STOLEN = [
            ("When {obj_a} collided with {obj_b} at {workplace}, it resulted in {chain}. "
            "{name} survived without a scratch, but the coworker they 'accidentally' shoved into the path of the destruction wasn't so lucky. Their time was stolen."),
            
            ("Surveillance footage at {workplace} shows {obj_a} failing, triggering a catastrophic reaction with {obj_b} and {chain}. "
            "In the final frames before the camera goes dark, {name} can be seen violently pulling a colleague in front of them to absorb the impact. {name} lived. The colleague did not."),
            
            ("A freak accident at {workplace} involving {obj_a} and {obj_b} led to {chain}. "
            "{name} walked away completely unscathed. Investigators noted the bizarre angle of the carnage—{name}'s coworker took the brunt of the lethal force, almost as if {name} had deliberately used them as a human shield.")
        ]

        # ── Roll cheat-death events for NPCs behind player ──────────────────────
        cheat_reports = []
        for name in npcs_behind:
            roll   = random.random()
            wp     = workplaces.get(str(name).lower(), {})
            wpname = wp.get('workplace_name', 'their location')
            
            # --- THE PATCH: Provide all expected keys to avoid KeyError ---
            # We can grab a random set of props just like we did above 
            # to make the 'cheat' reports look just as detailed.
            tag = 'default'
            for key in WORKPLACE_PROPS:
                if key in wp.get('level_id', ''):
                    tag = key
                    break
            
            props = WORKPLACE_PROPS.get(tag, WORKPLACE_PROPS['default'])
            obj_a, obj_b = random.choice(props)
            # -------------------------------------------------------------

            if roll < 0.25:
                # NEAR_MISS usually only needs name/workplace, but safe-formatting is best
                cheat_reports.append(random.choice(NEAR_MISS).format(
                    name=name, workplace=wpname
                ))
            elif roll < 0.40:
                # LIFE_STOLEN is the culprit — it needs obj_a!
                cheat_reports.append(random.choice(LIFE_STOLEN).format(
                    name=name, 
                    workplace=wpname,
                    obj_a=obj_a # <--- This fixes the KeyError: 'obj_a'
                ))

        # ── Shared prop resolver ─────────────────────────────────────────────────
        def _get_props(wp: dict) -> tuple:
            """Picks the right (obj_a, obj_b) pair for an NPC's workplace."""
            level_id = wp.get('level_id', '')
            tag = 'default'
            for key in WORKPLACE_PROPS:
                if key in level_id:
                    tag = key
                    break
            return random.choice(WORKPLACE_PROPS.get(tag, WORKPLACE_PROPS['default']))

        # ── Build offscreen death reports (NPCs ahead of player) ────────────────
        death_reports = []
        used_fates    = set()

        for name in npcs_ahead:
            wp     = workplaces.get(str(name).lower(), {})
            wpname = wp.get('workplace_name', 'their workplace')
            obj_a, obj_b = _get_props(wp)
            chain  = random.choice(CHAINS)
            fate   = random.choice([f for f in FATES if f not in used_fates] or FATES)
            used_fates.add(fate)

            report = random.choice(TEMPLATES).format(
                name=name, workplace=wpname,
                obj_a=obj_a, obj_b=obj_b,
                chain=chain, fate=fate
            )
            death_reports.append((name, report))
            npc_status[str(name).lower()] = 'dead'  # keep list coherent

        # ── Roll cheat-death events (NPCs behind player) ─────────────────────────
        cheat_reports = []
        for name in npcs_behind:
            roll   = random.random()
            wp     = workplaces.get(str(name).lower(), {})
            wpname = wp.get('workplace_name', 'their location')
            obj_a, obj_b = _get_props(wp)
            chain  = random.choice(CHAINS)

            if roll < 0.25:
                cheat_reports.append(random.choice(NEAR_MISS).format(
                    name=name, workplace=wpname
                ))
            elif roll < 0.40:
                cheat_reports.append(random.choice(LIFE_STOLEN).format(
                    name=name, workplace=wpname,
                    obj_a=obj_a, obj_b=obj_b, chain=chain
                ))

        # ── Assemble narrative ──────────────────────────────────────────────────
        city         = current_city
        disaster_str = (disaster.get('name') or disaster.get('event_description', 'a disaster')).replace('{city_name}', city)

        opening = (
            f"[color=#ff4444]Your story began with {disaster_str}.[/color]\n\n"
            f"You died bleeding out on the floor of {location}, alone, in {city}.\n\n"
            "Death didn't stop with you.\n\n"
        )

        if death_reports:
            ahead_block = (
                "In the time it took you to bleed out, Death had already started working "
                "down the list..setting up dominoes to fall:\n\n"
                + "\n\n".join(
                    f"[color=#ff8800]{name}[/color] — {report}"
                    for name, report in death_reports
                )
                + "\n\n"
            )
        else:
            ahead_block = (
                "By some alignment, no one ahead of you on the list had been "
                "collected yet. Death was patient. It could wait.\n\n"
            )

        behind_block = ""
        if cheat_reports:
            behind_block = (
                "As for those still left behind you on the list:\n\n"
                + "\n\n".join(cheat_reports)
                + "\n\n"
            )

        closing = (
            "The list doesn't care that you're gone, only that you left. "
            "It keeps moving. It always does, because-\n\n"
            "[color=#ff4444]Death is fucking complicated.[/color]"
        )

        final_narrative = opening + ahead_block + behind_block + closing

        return {
            'event_type':      'game_over',
            'death_reason':    'You bled out. No one found you in time.',
            'final_narrative': final_narrative,
            'flavor_text':     '',
            'hide_stats':      False,
            'priority':        10000,
            'player_state':    p.copy(),
        }

    def _check_finale_conditions(self):
        """Intercepts Hub travel and forces the Finale if conditions are met."""
        if str(self.player.get('current_level', '')) not in ["level_hub", "hub"]:
            return False

        roster = self.player.get('npc_status', {})
        alive_npcs = [n for n, s in roster.items() if s in ('alive', 'injured') and n != 'player']
        flags = self.player.get('flags', {})

        # Condition 1: You are the last one left (or it's just you and the Visionary)
        cast_decimated = len(alive_npcs) <= 1
        
        # Condition 2: You know too much (You found the Ledger AND the Autopsy Report)
        knows_too_much = flags.get('knows_blood_pact') and flags.get('knows_resurrection')

        if cast_decimated or knows_too_much:
            self.logger.info("Finale conditions met! Collapsing the Hub.")
            
            # Wipe normal hub exits and force the Finale
            hub_room = list(self.current_level_rooms_world_state.keys())[0]
            self.current_level_rooms_world_state[hub_room]['exits'] = {
                "Drive to the Final Confrontation": "level_finale"
            }
            
            self.add_ui_event({
                "event_type": "show_popup",
                "title": "Nowhere Left To Run",
                "message": "[color=ff0000]The air grows freezing cold. Your phone dies. The radio turns to static.[/color]\n\nThere is no one left to warn. You have run out of time and places to hide. Death is closing the loop, and it's pulling you toward the end.\n\nYou only have one exit left.",
                "priority": 1000
            })
            return True
        return False

    def _apply_finale_bonus(self, action: dict):
        """
        Applies a companion-granted finale bonus to the QTE chain.
        Stored in player['finale_modifiers'] for _start_finale_qte_chain to read.
        """
        bonus_type = action.get('bonus_type')
        amount = action.get('amount', 0)
        one_time = action.get('one_time', True)

        if not bonus_type:
            return

        modifiers = self.player.setdefault('finale_modifiers', {})

        if bonus_type == 'qte_duration_reduction':
            modifiers['duration_reduction'] = modifiers.get('duration_reduction', 0) + amount
            self.add_ui_event({"event_type": "show_message",
                "message": f"\n[color=00ff00]Their insight cuts the pressure. QTE windows shortened by {amount}s.[/color]\n"})

        elif bonus_type == 'qte_duration_extension':
            modifiers['duration_extension'] = modifiers.get('duration_extension', 0) + amount
            self.add_ui_event({"event_type": "show_message",
                "message": f"\n[color=00ff00]Their steady presence steadies your hands. +{amount}s on all QTE windows.[/color]\n"})

        elif bonus_type == 'qte_failure_forgiveness':
            modifiers['failure_forgiveness'] = modifiers.get('failure_forgiveness', 0) + amount
            self.add_ui_event({"event_type": "show_message",
                "message": f"\n[color=00ff00]Their reluctant trust gives you a margin. {int(amount*100)}% failure forgiveness applied.[/color]\n"})

        elif bonus_type == 'skeptic_interference_risk':
            modifiers['skeptic_risk'] = amount
            self.add_ui_event({"event_type": "show_message",
                "message": f"\n[color=ff8800]They're watching, not helping. Interference risk active on final phase.[/color]\n"})

        elif bonus_type == 'qte_type_simplify':
            modifiers['simplify_qtes'] = True
            self.add_ui_event({"event_type": "show_message",
                "message": f"\n[color=00ff00]Their operational experience streamlines the procedure.[/color]\n"})

        if one_time:
            self.logger.info(f"_apply_finale_bonus: Applied '{bonus_type}' (amount={amount}) to finale_modifiers.")


    def calculate_ending(self) -> str:
        """Generates the epic ending text based on how the player cheated death."""
        from kivy.app import App
        from .utils import color_text, normalize_text
        
        rm = self.resource_manager
        app = App.get_running_app()
        epilogue_type = getattr(app, 'epilogue_type', None) if app else None

        # --- THE EPILOGUE DISPATCHER ---
        if epilogue_type == "trigger_finale_override":
            return self._epilogue_administrative_override(rm)
        elif epilogue_type == "trigger_finale_flatline":
            return self._epilogue_chemical_flatline(rm)
        elif epilogue_type == "trigger_finale_dark_path":
            return self._epilogue_dark_path(rm)
            
        # --- THE NEW FD2 LORE EXPANSIONS ---
        elif epilogue_type == "trigger_finale_environmental_revival":
            return self._epilogue_leap_of_faith(rm)
        elif epilogue_type == "trigger_finale_isolation":
            return self._epilogue_isolation(rm)

        # --- FALLBACK: The Standard Survival State ---
        disaster_details = self.player.get('intro_disaster', {})
        raw_disaster_name = disaster_details.get('name', disaster_details.get('event_description', 'the disaster'))
        
        # Safely format the dynamic text
        if hasattr(self, '_format_dynamic_text'):
            disaster_name = self._format_dynamic_text(raw_disaster_name)
        else:
            disaster_name = raw_disaster_name
            
        city = self.player.get('current_city', 'McKinley')

        roster = self.player.get('npc_status', {})
        total = len(roster)
        alive_list = [name.title() for name, status in roster.items() if status in ('alive', 'injured')]
        dead_list = [name.title() for name, status in roster.items() if status == 'dead']

        alive_count = len(alive_list)

        if total == 0:
            base_text = "You survived the aftermath, entirely alone. There was no one left to save."
        elif alive_count == total:
            base_text = f"Against all odds, you broke the design. {color_text('Everyone survived.', 'special', rm)}"
        elif alive_count == 0:
            base_text = "You survived, but the cost was absolute. No one else made it."
        elif alive_count == 1:
            base_text = f"It was a massacre. Out of everyone, only {color_text(alive_list[0], 'npc', rm)} made it out with you."
        else:
            base_text = f"You cheated Death, but not without sacrifice. {color_text(str(alive_count), 'special', rm)} people survived."

        if dead_list:
            dead_str = color_text(", ".join(dead_list), 'error', rm)
            dead_text = f"\n\nBut Death still claimed its due: {dead_str}."
        else:
            dead_text = f"\n\nFor now, the list is clear. You have truly {color_text('cheated Death', 'special', rm)}."

        # THE LORE GUT-PUNCH RESOLUTION
        resolution = (
            f"\n\nYou walk away from {color_text(city, 'location', rm)}, leaving the nightmare of "
            f"{color_text(disaster_name, 'special', rm)} behind you. You survived. You beat the design.\n\n"
            f"Months pass. The trauma fades into a bad memory. To celebrate your continued survival, "
            f"you and the remaining survivors decide to take a weekend trip. You scored VIP tickets "
            f"for a race at {color_text('McKinley Speedway', 'location', rm)}. "
            f"\n\nAs you take your seats near the catch-fence and listen to the roar of the engines, you smile. "
            f"You're safe now. \n\n"
            f"Aren't you?"
        )
        # (Alternatively, they could be heading to a corporate retreat for Presage Paper!)

        return base_text + dead_text + resolution

    # --- NEW EPILOGUE GENERATORS ---

    def _epilogue_leap_of_faith(self, rm) -> str:
        """The Kimberly Corman route - dying without medical gear."""
        companions = [name.title() for name, status in self.player.get('npc_status', {}).items() if status in ('alive', 'injured')]
        comp_str = ", ".join(companions) if companions else "no one"
        
        return (
            f"The newspaper clipping held the answer. Kimberly Corman didn't just survive; she died and came back. "
            f"Without a hospital or a defibrillator, you had to take a terrifying leap of faith.\n\n"
            f"You plunged into the freezing rapids, letting the water fill your lungs until the world went black. "
            f"Clinical death. The list was broken.\n\n"
            f"But you didn't stay dead. {color_text(comp_str, 'npc', rm)} dragged your lifeless body from the water, "
            f"cracking your ribs with desperate, brutal CPR until you choked up river water and drew breath.\n\n"
            f"You found the loophole. You paid the toll. You are finally, truly safe."
        )

    def _epilogue_isolation(self, rm) -> str:
        """The Clear Rivers route - locking yourself away."""
        return (
            f"The clipping about the Route 23 pileup mentioned another survivor. A woman who realized that "
            f"Death's design is a puzzle that cannot be permanently solved, only delayed.\n\n"
            f"You stopped running. You walked into the state psychiatric facility and demanded to be committed. "
            f"Now, you sit in a padded white cell. No sharp corners. No gas lines. No loose screws or frayed wires. "
            f"You eat with a plastic spoon and sleep on a mattress on the floor.\n\n"
            f"You survived the design. But as you stare at the stark white walls, listening to the hum of the "
            f"fluorescent lights, you realize you haven't saved your life.\n\n"
            f"You've just built your own coffin."
        )

    def _epilogue_administrative_override(self, rm) -> str:
        from .utils import color_text
        return (
            f"The machine screams. {color_text('300 joules', 'warning', rm)} tear through your chest. Darkness. "
            f"Then... light. You gasp, choking on sterile air. You're in a hospital bed. A doctor clicks a pen. "
            f"'Welcome back. You were clinically dead for six minutes.'\n\n"
            f"You start to laugh. You did it. You beat the design.\n\n"
            f"[b]But something is wrong.[/b] You try to sit up, but your arms don't respond. You try to speak, "
            f"but only a wet rasp escapes. The doctor smiles sympathetically. 'Six minutes without oxygen. "
            f"The brain damage is extensive. You're experiencing locked-in syndrome.'\n\n"
            f"You realize you are trapped inside your own motionless body. A nurse walks in, wearing a badge from "
            f"{color_text('Presage Paper', 'location', rm)}'s experimental long-term care division. "
            f"They turn off the lights."
        )

    def _epilogue_chemical_flatline(self, rm) -> str:
        from .utils import color_text
        companion = self.player.get('companions', ['your companion'])[0]
        return (
            f"The sedative hits you like a freight train. Your heart slows, stutters, and stops. Silence. "
            f"Then, the brutal, rib-cracking thump of CPR. {color_text(companion, 'npc', rm)} slams the adrenaline into your chest. "
            f"Your eyes snap open. You are breathing! You hug them, weeping. You actually cheated Death.\n\n"
            f"You walk out of the clinic, the morning sun hitting your face. [b]But something is wrong.[/b] "
            f"The shadow cast by the awning doesn't match the angle of the sun. The birds aren't making any sound. "
            f"You look down at your hands; they are not there. YOU are not there;\nnot physically. Not anymore.\n\n"
            f"You turn around. Through the window of the clinic, you see {color_text(companion, 'npc', rm)} sitting on the floor, "
            f"sobbing uncontrollably, still performing CPR on your lifeless body.\n\n"
            f"You didn't wake up."
        )

    def _epilogue_dark_path(self, rm) -> str:
        from .utils import color_text
        return (
            f"You didn't want to do it, but it was them or you. The body lies dead at your feet. You stole their time. "
            f"You wait for the falling debris, the rogue spark, the collapsing ceiling... but nothing happens. "
            f"The air clears. The oppressive feeling vanishes. You walk away, your soul heavy, but your life your own.\n\n"
            f"Three weeks later, you are watching the news. 'Tragedy at {color_text('McKinley Speedway', 'location', rm)} today. "
            f"A massive pileup killed dozens.'\n\n"
            f"The anchor displays the face of the driver who caused the crash—it's the person you killed. "
            f"Because you killed them, they were never at the speedway. They never caused the crash. "
            f"You didn't just steal their time... you accidentally {color_text('saved forty people', 'special', rm)} who were supposed to die today.\n\n"
            f"And now, Death has a completely new design to balance, starting with the person who ruined the first one."
        )
    
    def wipe_active_state(self):
        """Scorched-earth reset for consecutive playthroughs."""
        self.player = {}
        self.interaction_flags = set()
        self.current_level_rooms_world_state = {}
        self.last_dialogue_context = {}
        
        # --- THE FIX: Clear undelivered UI Events ---
        if hasattr(self, '_ui_events'):
            self._ui_events = []
        # --------------------------------------------
        
        # Safely shut down the QTE engine
        if hasattr(self, 'qte_engine') and self.qte_engine:
            self.qte_engine.active_qte = None
            if hasattr(self.qte_engine, 'qte_timer_event') and self.qte_engine.qte_timer_event:
                self.qte_engine.qte_timer_event.cancel()
                self.qte_engine.qte_timer_event = None
            
        self.logger.info("Active state completely scrubbed for next session.")