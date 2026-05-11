# fd_terminal/hazard_engine.py
"""
The Engine of Calamity.

This system manages the state and progression of all environmental hazards.
It operates autonomously each turn and responds to player interactions.
"""
from collections.abc import Set
import copy
import logging
import random
import uuid
from kivy import app
from kivy.clock import Clock
from .resource_manager import ResourceManager
from .utils import color_text, normalize_text, Optional

class HazardEngine:
    def __init__(self, resource_manager: ResourceManager):
        self.resource_manager = resource_manager
        self.logger = logging.getLogger("HazardEngine")
        
        # This reference will be injected by GameLogic after initialization
        # to prevent a circular import dependency.
        self.game_logic = None 
        
        self.active_hazards = {}
        self.hazards_master_data = self.resource_manager.get_data('hazards', {})
        self.deferred_ambushes = {}
        self.logger.info("Engine of Calamity initialized.")

    # ── Class-level dispatch table for mobile hazard movement handlers ──────
    # Maps hazard type string -> unbound method.
    # Populated after the method definitions below.  Any hazard type that has
    # can_move_between_rooms: true in its JSON definition must have an entry here
    # or _tick_mobile_hazards will log a warning and skip it.
    MOBILE_HAZARD_HANDLERS = {}   # filled via _register_mobile_handlers() at bottom of class

    def initialize_for_level(self, level_id: int):
        """
        Loads ALL potential hazards. Fails RNG checks -> 'dormant'.
        Allows DeathAI to wake them later instead of spawning from thin air.
        """
        self.active_hazards.clear()
        self.logger.info(f"Hazard Engine (re)initialized for Level {level_id}.")
        
        if not getattr(self, 'game_logic', None): return

        rooms = self.game_logic.current_level_rooms_world_state or {}
        
        for room_name, room in rooms.items():
            hazard_entries = room.get('hazards_present') or room.get('hazards') or []
            
            for h in hazard_entries:
                if isinstance(h, str):
                    hazard_type = h; chance = 1.0
                elif isinstance(h, dict):
                    hazard_type = h.get('type') or h.get('hazard_type'); chance = h.get('chance', 1.0)
                else: continue

                if not hazard_type or hazard_type not in self.hazards_master_data: continue

                # DECISION: Active or Dormant?
                initial_state = None
                if random.random() > float(chance):
                    initial_state = "dormant" # The seed is planted, but sleeping.
                
                self._add_active_hazard(
                    hazard_type=hazard_type,
                    location=room_name,
                    source_trigger_id="level_seed",
                    initial_state_override=initial_state
                )

    def _add_active_hazard(
        self,
        hazard_type: str,
        location: str,
        initial_state_override: str | None = None,
        target_object_override: str | None = None,
        support_object_override: str | None = None,
        source_trigger_id: str | None = None
    ) -> str | None:
        # >>> PATCH START: Enforce Physical Validation <<<
        # We skip validation ONLY for 'level_seed' (initial load) to trust the level designer,
        # but enforce it for AI dynamic generation.
        if source_trigger_id in ["death_ai_escalation", "death_ai_contamination", "death_ai_forced_activation"]:
            if not self.validate_hazard_spawn_conditions(hazard_type, location):
                self.logger.warning(f"HazardEngine prevented invalid AI spawn: '{hazard_type}' in '{location}'.")
                return None
        # >>> PATCH END <<<

        h_def = self.hazards_master_data.get(hazard_type)
        if not h_def:
            self.logger.warning(f"_add_active_hazard: Unknown hazard type '{hazard_type}'.")
            return None

        hazard_id = f"{hazard_type}#{uuid.uuid4().hex[:8]}"
        initial_state = initial_state_override or h_def.get("initial_state") or "dormant"

        self.active_hazards[hazard_id] = {
            "id": hazard_id,
            "type": hazard_type,
            "location": location,
            "state": initial_state,
            "master_data": h_def,
            "spawned_entities": {},
            "target_object_override": target_object_override,
            "support_object_override": support_object_override,
            "source_trigger_id": source_trigger_id,
            "started_by_player": False,
            # --- MOBILE HAZARD RUNTIME FIELDS ---
            # Populated only when can_move_between_rooms: true in master_data.
            # Safe to leave None for all static hazards — the mobile tick checks
            # can_move_between_rooms before reading any of these.
            "movement_cooldown_turns": 0,    # Turns remaining before next move is allowed
            "path_to_target": [],            # Pre-computed BFS route: [room_a, room_b, ...]
            "seek_target_hazard_id": None,   # ID of specific hazard instance being hunted
            "seek_target_room": None,        # Room we are navigating toward
            "behavior_state": "patrolling",  # "patrolling" | "seeking" | "waiting"
            "memory": {},                    # Persistent per-hazard scratchpad
            "turns_in_state": 0,             # Integer turn counter; reset by set_hazard_state.
                                             # Use this for turn-based logic ("waited 3 turns").
                                             # time_in_state is the real-time equivalent in seconds.
            # -------------------------------------
        }
        self.logger.info(f"Spawned hazard '{hazard_type}' in '{location}' (id={hazard_id}) at state '{initial_state}'.")

        # >>> PATCH: Determine if we should suppress entry effects <<<
        suppress = True
        if source_trigger_id in ["death_ai_escalation", "death_ai_manifestation", "death_ai_forced_activation"]:
            if initial_state not in ["dormant", "idle", "hidden", "sealed", "stable"]:
                suppress = False
                self.logger.info(f"Hazard '{hazard_id}' spawned by AI in active state '{initial_state}'. Triggering immediate effects.")

        try:
            result = self.set_hazard_state(hazard_id, initial_state, suppress_entry_effects=suppress)
            if not suppress and self.game_logic and result.get("consequences"):
                for cons in result.get("consequences", []):
                    self.game_logic.handle_hazard_consequence(cons)
        except Exception as e:
            self.logger.error(f"_add_active_hazard: Failed to finalize state entry for '{hazard_id}': {e}", exc_info=True)

        return hazard_id

    def update_realtime(self, dt: float) -> list:
        """
        Checks 'trigger_after_seconds' in hazard states.
        Returns a list of consequences (like State Changes or QTEs).
        """
        events = []
        for hid, hazard in list(self.active_hazards.items()):
            if hazard.get('state') in ['dormant', 'inactive', 'resolved']: continue
                
            # Track lifetime
            hazard['time_active'] = hazard.get('time_active', 0.0) + dt
            hazard['time_in_state'] = hazard.get('time_in_state', 0.0) + dt
            
            # Check Trigger
            state_def = self._resolve_state_def(hazard, hazard['state'])
            trigger_time = state_def.get('trigger_after_seconds')
            
            if trigger_time and hazard['time_in_state'] >= trigger_time:
                next_state = state_def.get('next_state')
                if next_state:
                    self.logger.info(f"Time trigger: '{hid}' -> '{next_state}' ({trigger_time}s)")
                    # Reset state timer
                    hazard['time_in_state'] = 0.0
                    # Transition
                    res = self.set_hazard_state(hid, next_state)
                    if res.get('consequences'):
                        events.extend(res['consequences'])
        return events

    def activate_dormant_hazard(self, room_id: str, hazard_type: str = None) -> bool:
        """
        Finds a dormant hazard in the room and wakes it up.
        If hazard_type is None, picks one at random.
        """
        candidates = []
        for hid, h in list(self.active_hazards.items()):
            if h.get('location') == room_id and h.get('state') == 'dormant':
                if hazard_type and h.get('type') != hazard_type:
                    continue
                candidates.append(hid)
        
        if not candidates:
            return False
            
        target_id = random.choice(candidates)
        hazard = self.active_hazards[target_id]
        
        # Determine the "Active" state (usually 'initial_state' from master data)
        master = hazard.get('master_data', {})
        wake_state = master.get('initial_state', 'active')
        
        self.logger.info(f"DeathAI waking dormant hazard '{target_id}' -> '{wake_state}'")
        self.set_hazard_state(target_id, wake_state)
        return True

    def validate_hazard_spawn_conditions(self, hazard_type: str, room_id: str) -> bool:
        """
        Strictly validates if a hazard can physically exist in the target room.
        Checks:
        1. 'placement_object': Does the room contain required furniture/objects?
        2. 'required_room_tags': (Optional) Does the room match specific tags (e.g. 'exterior')?
        3. 'forbidden_rooms': Is this room explicitly safe from this hazard?
        """
        if not self.game_logic:
            return False
            
        h_def = self.hazards_master_data.get(hazard_type)
        if not h_def:
            return False

        room_data = self.game_logic.get_room_data(room_id)
        if not room_data:
            return False

        # 1. Check Placement Objects (The Physical Anchor)
        # The hazard MUST attach to something that exists in the room.
        required_objects = h_def.get('placement_object', [])
        
        # If no placement requirements are defined, assume it's a universal/ambient hazard (like 'unseen_force')
        # But if it IS defined, we must enforce it.
        if required_objects:
            found_anchor = False
            
            # Gather all potential anchors in the room
            room_contents = []
            
            # Check Furniture names
            for f in room_data.get('furniture', []):
                if isinstance(f, dict):
                    room_contents.append(f.get('name', '').lower())
            
            # Check Object names
            for o in room_data.get('objects', []):
                if isinstance(o, str):
                    room_contents.append(o.lower())
                elif isinstance(o, dict):
                    room_contents.append(o.get('name', '').lower())
                    # Check aliases too
                    for alias in o.get('aliases', []):
                        room_contents.append(alias.lower())

            # Check Room Name itself (e.g., 'Elevator Car' is its own anchor)
            room_contents.append(room_id.lower().replace('_', ' '))

            # Perform the check
            # We look for partial matches (e.g. "gas pipe" matches "rusty gas pipe")
            for req in required_objects:
                req_lower = req.lower()
                for content in room_contents:
                    if req_lower in content:
                        found_anchor = True
                        break
                if found_anchor:
                    break
            
            if not found_anchor:
                self.logger.debug(f"Spawn Blocked: '{hazard_type}' requires {required_objects} but '{room_id}' lacks them.")
                return False

        # 2. Check Forbidden Rooms (Explicit exclusions)
        if room_id in h_def.get('forbidden_rooms', []):
             self.logger.debug(f"Spawn Blocked: '{room_id}' is forbidden for '{hazard_type}'.")
             return False

        # 3. Check Room Tags (Optional architecture support)
        req_tags = h_def.get('required_room_tags', [])
        if req_tags:
            room_tags = room_data.get('tags', [])
            # If room lacks tags, it fails specific tag requirements
            if not set(req_tags).intersection(set(room_tags)):
                self.logger.debug(f"Spawn Blocked: '{hazard_type}' requires tags {req_tags}.")
                return False

        return True

    # Convenience used by DeathAI/escalation code
    def get_hazards_in_location(self, room_name: str) -> list:
        """Return all active hazard instances in a room."""
        return [h for h in self.active_hazards.values() if h.get("location") == room_name]

    def get_room_hazards_descriptions(self, room_name: str) -> dict:
        """Return a mapping of hazard_id -> hazard instance for a room (used by DeathAI)."""
        return {hid: h for hid, h in self.active_hazards.items() if h.get("location") == room_name}

    def _spawn_entities_for_hazard(self, hazard_id: str):
        """Place hazard-related objects into the room, tagging them for UI interaction."""
        hazard_inst = self.active_hazards.get(hazard_id)
        if not hazard_inst:
            return
        
        hazard_type = hazard_inst.get('type')
        room_name = hazard_inst.get('location')
        trigger_id = hazard_inst.get('source_trigger_id')  # <--- GET TRIGGER

        # --- ROOT KILL SWITCH FOR DEATH'S BREATH ---
        if hazard_type == 'deaths_breath':
            char_class = self.game_logic.player.get('character_class', '') if self.game_logic else ''
            if char_class != 'Medium':
                return  # Instantly abort. Do not spawn the physical entity!
        # -------------------------------------------

        hazard_def = self.hazards_master_data.get(hazard_type, {})
        entity_keys = list(hazard_def.get('spawn_entities', []))
        if not entity_keys:
            return

        rooms = self.game_logic.current_level_rooms_world_state
        room = rooms.get(room_name)
        if not room:
            return

        objs = room.get('objects')
        if objs is None:
            room['objects'] = objs = []

        existing = set(normalize_text(o['name'] if isinstance(o, dict) else str(o)) for o in objs)
        spawned = {}

        def _add_entity(name, desc_text=None, key_hint=None):
            norm_name = normalize_text(name)
            final_desc = desc_text or "A strange object."

            if trigger_id == "death_ai_gaslight":
                gaslight_prefixes = [
                    "You could have sworn this wasn't here a moment ago. ",
                    "You don't remember seeing this when you entered. ",
                    "This seems to have appeared out of nowhere. ",
                    "Strange... you must have missed this before. "
                ]
                prefix = random.choice(gaslight_prefixes)
                # Prepend the gaslighting text to the normal description
                final_desc = f"{prefix}{final_desc}"
            # >>> PATCH END <<<

            obj = {
                "name": name,
                "description": final_desc,
                "type": "hazard_entity",
                "hazard_key": hazard_type,
                "key_hint": key_hint or normalize_text(str(name))
            }

            # --- THE DUPLICATION KILL SWITCH ---
            if norm_name in existing:
                # Scrub the old version out so we can replace it with the updated state
                for i in range(len(objs) - 1, -1, -1):
                    o = objs[i]
                    if (isinstance(o, dict) and normalize_text(o.get('name', '')) == norm_name) or \
                       (isinstance(o, str) and normalize_text(o) == norm_name):
                        objs.pop(i)
            # -----------------------------------

            objs.append(obj)
            existing.add(normalize_text(name))
            if key_hint:
                spawned[key_hint] = name
            else:
                spawned[name] = name

        items_master = self.game_logic.resource_manager.get_data('items', {})

        for ekey in entity_keys:
            if isinstance(ekey, dict):
                entity_name = ekey.get('name')
                if not entity_name:
                    continue

                desc = ekey.get('description')
                if not desc:
                    curr_state = hazard_inst.get('state') or hazard_def.get('initial_state')
                    sdef = (hazard_def.get('states') or {}).get(curr_state or "", {})
                    desc = sdef.get('description')
                    if desc:
                        desc = desc.replace("{object_name}", entity_name)
                _add_entity(entity_name, desc_text=desc, key_hint=str(entity_name).lower())
            elif isinstance(ekey, str):
                display = self._choose_display_name_for_entity(ekey, hazard_def, items_master)
                curr_state = hazard_inst.get('state') or hazard_def.get('initial_state')
                sdef = (hazard_def.get('states') or {}).get(curr_state or "", {})
                desc = sdef.get('description')
                if desc:
                    desc = desc.replace("{object_name}", display)
                _add_entity(display, desc_text=desc, key_hint=normalize_text(str(ekey)))

        hazard_inst['spawned_entities'] = spawned
        self.logger.info(f"Spawned hazard entities for '{hazard_type}' in '{room_name}' (Trigger: {trigger_id})")

    def _choose_display_name_for_entity(self, entity_key: str, hazard_def: dict, items_master: dict) -> str:
        """Pick a random user-facing name for an entity from item name/aliases and hazard object_name_options."""
        candidates = []
        # From items.json
        item = items_master.get(entity_key) or items_master.get(entity_key.replace(' ', '_')) or {}
        if item:
            base = item.get('name') or entity_key
            aliases = item.get('aliases') or []
            candidates.extend([base] + list(aliases))
        else:
            candidates.append(entity_key)

        # From hazard-level object_name_options (simple keyword relevance)
        extras = hazard_def.get('object_name_options') or []
        ek_norm = entity_key.replace('_', ' ').lower()
        for opt in extras:
            o_norm = str(opt).lower()
            if any(tok and tok in o_norm for tok in ek_norm.split()):
                candidates.append(opt)

        # Dedup and choose
        seen = set()
        filtered = []
        for c in candidates:
            if c and c not in seen:
                filtered.append(c)
                seen.add(c)

        return random.choice(filtered) if filtered else entity_key

    def sync_world_state(self, rooms_world_state: dict):
        """
        Links the Hazard Engine to the current level's hydrated room data.
        This allows hazards to interact with objects and NPCs directly.
        """
        self.rooms_world_state = rooms_world_state
        self.logger.info("HazardEngine: World state synced. Calamity pathways updated.")
        
        # Optional: Validate that all active hazards have valid targets in the new state
        for h_id, h_data in self.active_hazards.items():
            target_id = h_data.get('affected_objects', [None])[0]
            if target_id:
                # Find which room contains this object
                found = False
                for r_id, r_data in self.rooms_world_state.items():
                    if any(item.get('id') == target_id for item in r_data.get('items_present', []) if isinstance(item, dict)):
                        found = True
                        break
                if not found:
                    self.logger.warning(f"Hazard '{h_id}' targets '{target_id}', but it wasn't found in hydrated rooms!")

    def process_turn(self) -> dict:
        messages, consequences = [], []
        if not self.game_logic: return {}

        player_location = self.game_logic.player.get('location')

        # 1. AI Strategies
        if self.game_logic.death_ai:
            ai_msgs = self.game_logic.death_ai.execute_counter_strategies()
            messages.extend(ai_msgs)

        # 2. Mobile Hazard Tick — handles all can_move_between_rooms hazards,
        #    including the generalised Deaths Breath migration and robo_vacuum seeking.
        if player_location:
            mobile_msgs, mobile_cons = self._tick_mobile_hazards(player_location)
            messages.extend(mobile_msgs)
            consequences.extend(mobile_cons)

        # 3. Hazard Progression
        # Optimization: Pre-group hazards by location for O(1) interaction lookups
        hazards_by_loc = {}
        for hid, h in list(self.active_hazards.items()):
             loc = h.get('location')
             if loc not in hazards_by_loc: hazards_by_loc[loc] = []
             hazards_by_loc[loc].append((hid, h))

        for hazard_id, hazard in list(self.active_hazards.items()):
            is_local = (hazard.get('location') == player_location)
            master_def = hazard.get('master_data', {})
            if not is_local and not master_def.get('progress_while_offscreen', False):
                continue

            # Always increment the turn counter regardless of gating.
            # This is the canonical source for "how many turns has this hazard
            # been in its current state?" — use it in JSON via turns_in_state
            # checks or in handler code via hazard['turns_in_state'].
            hazard['turns_in_state'] = hazard.get('turns_in_state', 0) + 1

            current_state_key = hazard.get('state')
            state_data = master_def.get('states', {}).get(current_state_key, {})

            # --- TERMINAL STATE GUARD ---
            # Hazards in terminal states (blown fuse, post-explosion, etc.)
            # must NOT progress further. Skip all autonomous logic.
            if state_data.get('is_terminal_state', False):
                continue

            # --- FIX START: The "Active Hazard" Bypass ---
            # Determine if we should allow autonomous progression
            allow_autonomous = False

            # 1. Standard AI Gating (Progress/Fear)
            player_progress = self.game_logic.death_ai.calculate_level_progression()
            player_fear = self.game_logic.player.get('fear', 0.0)
            if player_progress >= 0.25 or player_fear >= 0.35:
                allow_autonomous = True

            # 2. BYPASS: If the hazard has moved beyond its initial state, 
            #    it is "Active" and must be allowed to resolve.
            #    (e.g., Elevator is 'moving', Ventilator is 'sparking')
            initial_state = master_def.get('initial_state', 'dormant')
            if current_state_key != initial_state:
                allow_autonomous = True
                # Optional: Log this bypass for debugging
                # self.logger.debug(f"Hazard '{hazard_id}' bypassing AI gate (Active State: {current_state_key})")

            # Only gate autonomous (RNG) progression if NOT allowed
            base_chance = state_data.get('chance_to_progress', 0)
            if 0 < base_chance < 1.0 and not allow_autonomous:
                continue  # Skip autonomous progression for this hazard
            # --- FIX END ---

            # Flags
            if state_data.get('autonomous_action') == '_check_progression_by_flags':
                consequences.extend(self._maybe_progress_on_flags(hazard_id))

            # Interactions
            room_hazards = hazards_by_loc.get(hazard.get('location'), [])
            i_msgs, i_cons = self._process_hazard_interactions(hazard_id, hazard, room_hazards)
            messages.extend(i_msgs)
            consequences.extend(i_cons)

            # Standard Progression (RNG)
            # (If not skipped above)

            # --- ADDED: Duration Gating ---
            # Look up how long the hazard MUST stay in this state from JSON
            required_duration = state_data.get('duration_in_state', 0)
            current_duration = hazard.get('turns_in_state', 0)

            # Skip RNG progression if the minimum duration hasn't been met
            if current_duration < required_duration:
                can_progress = False
            else:
                can_progress = True

            if can_progress and (base_chance >= 1.0 or (allow_autonomous and 0 < base_chance < 1.0)):
                # Aggression scaling as before
                if 0 < base_chance < 1.0 and self.game_logic.death_ai:
                    aggression = self.game_logic.death_ai.get_effective_aggression()
                    final_chance = base_chance * max(0.1, aggression)
                else:
                    final_chance = base_chance

                if final_chance > 0 and random.random() < final_chance:
                    next_st = state_data.get('next_state')
                    if next_st:
                        res = self.set_hazard_state(hazard_id, next_st)
                        consequences.extend(res.get('consequences', []))

        # --- NEW: Check for Compound Hazard Synergies ---
        if player_location:
            self._check_compound_synergies(player_location)

        return {
            "messages": messages,
            "consequences": consequences,
            "death_triggered": False,
            "qte_triggered": None
        }

    # --- Helpers for set_hazard_state ---

    def _check_compound_synergies(self, location: str):
        """
        Dynamically checks for overlapping hazard tags in the same room.
        If a synergy is found (e.g. 'water' + 'electrical'), it forces the 
        hazard into its fatal/QTE state to create a deadly trap.
        """
        synergies = self.resource_manager.get_data('hazard_synergies', {})
        if not synergies:
            return

        # Get all active hazards in the current room
        room_hazards = [
            (hid, self.active_hazards[hid]) 
            for hid in self.get_active_hazards_for_room(location)
            if hid in self.active_hazards
        ]

        # Compare every hazard against every other hazard
        for i, (h1_id, h1) in enumerate(room_hazards):
            h1_data = h1.get('master_data', {})
            h1_tags = h1_data.get('tags', [])
            h1_state_def = h1_data.get('states', {}).get(h1.get('state'), {})
            
            # If the hazard is already dead/terminal, it can't react anymore
            if h1_state_def.get('is_terminal_state'):
                continue

            for j, (h2_id, h2) in enumerate(room_hazards):
                if i == j: continue # Don't compare a hazard to itself

                h2_data = h2.get('master_data', {})
                h2_tags = h2_data.get('tags', [])
                
                # Check if any tag in H1 reacts with any tag in H2 based on hazard_synergies.json
                synergy_found = False
                for tag1 in h1_tags:
                    if tag1 in synergies:
                        interacting_tags = synergies[tag1]
                        for tag2 in h2_tags:
                            if tag2 in interacting_tags:
                                synergy_found = True
                                break
                    if synergy_found: break
                
                # We have a match! (e.g. H1 is electrical, H2 is water)
                if synergy_found:
                    # Dynamically find the most dangerous state for H1 to jump to
                    fatal_state = None
                    states = h1_data.get('states', {})
                    
                    for state_key, state_info in states.items():
                        # We want it to jump straight to the QTE or terminal trap
                        if state_info.get('triggers_qte_on_entry') or state_info.get('instant_death_in_room'):
                            fatal_state = state_key
                            break
                    
                    # Fallback just in case it doesn't have a QTE
                    if not fatal_state:
                        fatal_state = h1_state_def.get('next_state')

                    # Trigger the Chain Reaction!
                    if fatal_state and h1.get('state') != fatal_state:
                        h1_name = h1_data.get('name', 'hazard')
                        h2_name = h2_data.get('name', 'hazard')
                        
                        msg = (
                            f"[color=ff4444][COMPOUND TRAP][/color] "
                            f"The {h1_name} directly interacts with the {h2_name}, "
                            f"creating a deadly chain reaction!"
                        )
                        
                        if self.game_logic:
                            self.game_logic.add_ui_event({
                                "event_type": "show_popup", 
                                "title": "Chain Reaction!", 
                                "message": msg
                            })
                            
                        self.logger.warning(f"SYNERGY TRIGGERED! {h1_id} accelerated by {h2_id} to {fatal_state}!")
                        
                        # Fast-forward H1 to the trap state
                        self.set_hazard_state(h1_id, fatal_state)
                        
                        # RETURN IMMEDIATELY: Only trigger one synergy per tick 
                        # to prevent the engine from looping infinitely and crashing.
                        return

    def _inject_dynamic_variables(self, data):
        """
        Recursively replaces placeholders in dictionaries, lists, and strings.
        - {companion_name} -> Display Name (e.g. "Maya")
        - $ACTIVE_COMPANION -> Internal ID (e.g. "maya")
        """
        if not self.game_logic: return data

        # 1. Get Companion Data
        comp_id = self.game_logic.player.get('active_companion_id')
        
        # Fallback if no companion exists but hazard triggered anyway
        if not comp_id:
            comp_name = "Your Friend"
            comp_id = "unknown_npc"
        else:
            # Fetch name from NPCs master data
            npcs = self.resource_manager.get_data('npcs', {})
            # Handle dict vs list structure for NPCs
            if comp_id in npcs:
                comp_name = npcs[comp_id].get('name', comp_id.title())
            else:
                # Try list search
                comp_name = comp_id.title()
                for n in npcs.values():
                    if n.get('id') == comp_id:
                        comp_name = n.get('name')
                        break

        # 2. Recursive Replacement
        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                # Replace in Keys (Critical for QTE choices mapping)
                new_key = k.replace("{companion_name}", comp_name).replace("$ACTIVE_COMPANION", comp_id)
                new_dict[new_key] = self._inject_dynamic_variables(v)
            return new_dict
            
        elif isinstance(data, list):
            return [self._inject_dynamic_variables(item) for item in data]
            
        elif isinstance(data, str):
            # Replace in Values
            return data.replace("{companion_name}", comp_name).replace("$ACTIVE_COMPANION", comp_id)
            
        return data

    def _resolve_state_def(self, hazard: dict, new_state: str) -> dict:
        """Resolve state definition, inject dynamic variables, AND apply Tool Bonuses."""
        # 1. Get raw definition
        hdef = hazard.get('master_data', {}) or {}
        states = hdef.get('states', {}) or {}
        raw_sdef = states.get(new_state, {}) or {}
        
        # 2. Deep Copy
        sdef = copy.deepcopy(raw_sdef)
        sdef['__state_name__'] = new_state
        sdef['__hazard_id__'] = hazard.get('id')  # Tag for template resolution
        
        # 3. Inject Variables
        sdef = self._inject_dynamic_variables(sdef)
        
        # >>> NEW PATCH: Universally inject {object_name} into all nested strings <<<
        obj_override = hazard.get('target_object_override')
        if obj_override:
            import json
            sdef_str = json.dumps(sdef)
            sdef_str = sdef_str.replace("{object_name}", obj_override)
            sdef = json.loads(sdef_str)
        # >>> END PATCH <<<

        # 4. --- PATCH: APPLY TOOL BONUS TO QTE (ONCE ONLY) ---
        # Guard: Only apply bonus if we haven't already for this hazard+state combo
        hazard_id = hazard.get('id')
        bonus_cache_key = f"{hazard_id}#{new_state}:tool_bonus_applied"
        
        if (self.game_logic and 'triggers_qte_on_entry' in sdef and 
            not hazard.get(bonus_cache_key)):
            
            qte_cfg = sdef['triggers_qte_on_entry']
            qte_type = qte_cfg.get('qte_type')
            
            # Only apply to physical QTEs
            if qte_type == 'button_mash':
                # Check for Force Tools
                best_tool, bonus = self.game_logic._best_tool_in_inventory()
                
                if bonus > 0:
                    ctx = qte_cfg.setdefault('qte_context', {})
                    
                    # Get Base Target (Handle dicts for character classes if present)
                    base = ctx.get('target_mash_count')
                    if isinstance(base, dict): base = base.get('default', 20)
                    if base is None: base = 20
                    
                    # Apply Math
                    new_target = max(5, int(base) - (bonus * 2))
                    ctx['target_mash_count'] = new_target
                    
                    # Update Prompt
                    tool_name = self.game_logic._get_item_display_name(best_tool)
                    base_prompt = ctx.get('ui_prompt_message', 'MASH!')
                    ctx['ui_prompt_message'] = f"{base_prompt}\n[color=00ff00](Using {tool_name})[/color]"
                    
                    self.logger.info(f"HazardEngine: Auto-applied {tool_name} bonus to state {new_state}. Mash {base} -> {new_target}")
            
            # Mark this bonus as applied to prevent re-runs
            hazard[bonus_cache_key] = True
        # -----------------------------------------
        
        return sdef

    def _apply_entry_actions(self, sdef: dict, hazard_id: str) -> list:
        """Run special action and entry rewards. Returns generated consequences."""
        self.logger.debug(f"[_apply_entry_actions] Applying entry actions for hazard_id: {hazard_id}")
        
        if hazard_id and sdef.get("__state_name__") == "hard_landing_survival":
            if self.game_logic:
                self.game_logic.player['hp'] = 1
                self.game_logic.player['location'] = "Basement Elevator Lobby"
                self.game_logic.player['elevator_current_floor'] = -1
        
        # THE FIX: Only run the special action ONCE, and capture its consequences!
        special_cons = self._maybe_run_special_action(sdef, hazard_id)
        reward_cons = self._process_state_entry_rewards(sdef, hazard_id)
        
        # >>> PATCH START: Record Evasion on Reward/Success <<<
        rewards = sdef.get('on_state_entry_rewards', {})
        if self.game_logic and (rewards.get('score_bonus', 0) > 0 or rewards.get('achievements_to_unlock')):
            self.game_logic.record_evaded_hazard(hazard_id, method="Neutralized")
        # >>> PATCH END <<<

        self.logger.debug(f"[_apply_entry_actions] Entry actions applied for hazard_id: {hazard_id}")
        return special_cons + reward_cons

    def _resolve_qte_type(self, qte_type: str) -> str:
        """Intercepts 'random' strings in JSON and swaps them for an actual QTE type."""
        if str(qte_type).lower() == "random":
            import random
            # Fallback types just in case
            valid_types = ["button_mash", "hold_to_threshold", "reaction_single_key"]
            
            # Pull the actual live definitions from the Kivy Engine!
            if getattr(self, 'game_logic', None) and getattr(self.game_logic, 'qte_engine', None):
                valid_types = list(self.game_logic.qte_engine.qte_definitions.keys())
                
            return random.choice(valid_types)
            
        return qte_type

    def _extract_entry_metadata(self, sdef: dict):
        """Extracts and formats state components, delegating complex logic to helpers."""
        hazard_id = sdef.get('__hazard_id__')

        # 1. Base Extraction
        popup_event = sdef.get('ui_popup_event') or {}
        msg = sdef.get('description') or popup_event.get('message')
        title = popup_event.get('title', 'Notice')
        pause = bool(sdef.get('pause_for_player_acknowledgement'))
        nxt = sdef.get('next_state')

        # 2. Dynamic Targeting Intercept
        qte = self._apply_dynamic_targeting(sdef)
        
        # --- THE FIX: Intercept 'random' QTEs! ---
        if qte and 'qte_type' in qte:
            qte['qte_type'] = self._resolve_qte_type(qte['qte_type'])
        # -----------------------------------------

        # 3. Class Stats & Affinities Intercept
        if qte and getattr(self, 'game_logic', None):
            qte, msg, pause, nxt = self._apply_class_modifiers(qte, msg, pause, nxt, hazard_id)

        # 4. Message Formatting
        msg = self._format_hazard_message(msg, hazard_id)

        return msg, title, qte, pause, nxt


    # -------------------------------------------------------------------------
    # --- State Extraction Helpers ---
    # -------------------------------------------------------------------------

    def _apply_dynamic_targeting(self, sdef: dict) -> dict:
        """Determines if the QTE should target the player or a present NPC."""
        if not getattr(self, 'game_logic', None):
            return sdef.get('triggers_qte_on_entry')

        # 1. Figure out who Death is actively hunting right now
        deaths_list = self.game_logic.player.get('deaths_list', [])
        deaths_idx = self.game_logic.player.get('deaths_list_index', 0)
        active_target = deaths_list[deaths_idx] if deaths_idx < len(deaths_list) else 'player'

        # 2. Check if the active target is in the room (Checks both companions and room NPCs)
        room_id = self.game_logic.player.get('location')
        room_data = self.game_logic.get_room_data(room_id) or {}
        npcs_in_room = [n.get('name', n).lower() if isinstance(n, dict) else n.lower() for n in room_data.get('npcs', [])]
        companions = [c.lower() for c in self.game_logic.player.get('companions', [])]
        
        target_is_present = (active_target.lower() in npcs_in_room) or (active_target.lower() in companions)

        # 3. Swap to the Intervention QTE if the NPC is targeted and present
        if target_is_present and active_target.lower() != 'player' and 'npc_intervention_qte' in sdef:
            self.logger.info(f"Death's Design Intercept: Hazard is bypassing player to target {active_target.title()}!")
            
            import copy
            qte = copy.deepcopy(sdef['npc_intervention_qte'])
            
            # Dynamically inject the NPC's name
            ctx = qte.setdefault('qte_context', {})
            for key in ['description', 'ui_prompt_message', 'success_message', 'failure_message']:
                if key in ctx and '{npc_name}' in ctx[key]:
                    ctx[key] = ctx[key].replace('{npc_name}', active_target.title())
            
            # Flag the QTE payload so GameLogic knows who to kill on failure
            ctx['target_npc'] = active_target.title()
            return qte

        return sdef.get('triggers_qte_on_entry')


    def _apply_class_modifiers(self, qte: dict, msg: str, pause: bool, nxt: str, hazard_id: str):
        """Applies Intuition (time), Agility (auto-dodge), and Affinities (nerf) to the QTE."""
        if not self.game_logic or not qte:
            return qte, msg, pause, nxt
            
        import random
        
        char_class = self.game_logic.player.get('character_class', 'Survivor')
        class_master = self.resource_manager.get_data('character_classes', {}).get(char_class, {})
        
        # A. INTUITION: Time Dilation (+0.25s per point)
        intuition = class_master.get('intuition', 1)
        if intuition > 1:
            bonus_time = intuition * 0.25
            qte['duration'] = qte.get('duration', 5) + bonus_time

        # B. AGILITY: The Auto-Dodge (1 per level max)
        agility = class_master.get('agility', 1)
        agility_uses = self.game_logic.player.get('agility_uses_this_level', 0)
        
        ctx = qte.setdefault('qte_context', {})
        
        # 10% chance per agility point to intercept the QTE!
        if agility_uses < 1 and random.random() < (agility * 0.10):
            self.logger.info(f"Agility Triggered! Player auto-dodged hazard.")
            self.game_logic.player['agility_uses_this_level'] = agility_uses + 1
            
            # --- THE PATCH: Preserve the Chain ---
            # Even if we auto-dodge, we need to know where the hazard goes next.
            # We map the single 'tap' to the intended next state (nxt).
            if nxt:
                # If the user taps once, it sends the success signal to the nxt state
                ctx['input_to_next_state'] = {"success": nxt}
            # -------------------------------------
            
            ctx['ui_prompt_message'] = "[color=00ff00][AGILITY DODGE][/color] Your reflexes take over!\nTAP to evade!"
            ctx['target_mash_count'] = 1 # One tap to win
            
            qte['duration'] = 999.0 # Effectively infinite time to react
            ctx['duration'] = 999.0
            ctx['input_type'] = 'mash'
            qte['qte_type'] = 'button_mash'
            
            ctx['ui_type'] = 'tap_area'
            ctx['button_labels'] = ['TAP!']
            
            ctx['agility_dodged'] = True
            
            # We return early here, but now ctx['input_to_next_state'] 
            # contains the 'nxt' variable, keeping the player in line!
            return qte, msg, pause, nxt
            
        # C. AFFINITIES: Hazard Advantage
        hazard = self.active_hazards.get(hazard_id, {}) if hazard_id else {}
        h_tags = hazard.get('master_data', {}).get('tags', [])
        
        class_affinities = class_master.get('affinities', {}).get('hazard_tags', [])
        
        # If the player's class matches the hazard type (e.g., Mechanic vs Electrical)
        if any(t in class_affinities for t in h_tags):
            # Nerf the QTE difficulty
            if 'target_mash_count' in ctx:
                ctx['target_mash_count'] = max(3, ctx['target_mash_count'] // 2)
            elif 'pattern' in ctx:
                ctx['pattern'] = ctx['pattern'][:max(2, len(ctx['pattern'])-1)]
                
            ctx['ui_prompt_message'] = f"[color=00ff00][CLASS ADVANTAGE][/color]\n{ctx.get('ui_prompt_message', '')}"
            
        return qte, msg, pause, nxt


    def _format_hazard_message(self, msg: str, hazard_id: str) -> str:
        """Resolves template placeholders in the description message."""
        if not msg or '{' not in msg:
            return msg

        hazard = self.active_hazards.get(hazard_id, {}) if hazard_id else {}
        master = hazard.get('master_data', {})
        
        obj_name = (
            hazard.get('target_object_override')
            or (master.get('object_name_options', [None]) or [None])[0]
            or master.get('name', 'hazard')
        )
        support_obj = hazard.get('support_object_override') or 'surface'
        
        msg = msg.replace('{object_name}', obj_name)
        msg = msg.replace('{support_object}', support_obj)
        
        return msg

    def _build_popup_consequence(self, hid, state, title, msg, qte, pause, nxt):
        evt = {"type": "show_popup", "title": title, "message": msg, "meta": {"hazard_id": hid, "state": state}}
        if qte:
            ctx = qte.get('qte_context', {}).copy()
            ctx['qte_source_hazard_id'] = hid
            ctx['duration'] = qte.get('duration', qte.get('default_duration', 8.0))
            evt["on_close_start_qte"] = {"qte_type": qte.get("qte_type"), "qte_context": ctx}
        elif nxt: # <<< PATCH: Always chain the next state if there's no QTE
            evt["on_close_set_hazard_state"] = {"hazard_id": hid, "target_state": nxt}
        return evt

    def _build_immediate_qte_consequence(self, hazard_id: str, qte_entry: dict) -> dict:
        """Construct an immediate start_qte consequence when no popup is present."""
        self.logger.debug(f"[_build_immediate_qte_consequence] Building immediate QTE consequence for hazard_id: {hazard_id}, qte_entry: {qte_entry}")
        qte_ctx = dict(qte_entry.get('qte_context', {}))
        qte_ctx['qte_source_hazard_id'] = hazard_id
        qte_ctx['duration'] = qte_entry.get('duration', qte_entry.get('default_duration', 8.0))
        consequence = {
            "type": "start_qte",
            "qte_type": qte_entry.get("qte_type"),
            "qte_context": qte_ctx
        }
        self.logger.debug(f"[_build_immediate_qte_consequence] Built consequence: {consequence}")
        return consequence

    def _build_auto_advance_consequence(self, hazard_id: str, next_state: str) -> dict:
        """Construct a follow-up state change consequence for non-paused states."""
        self.logger.debug(f"[_build_auto_advance_consequence] Building auto-advance consequence for hazard_id: {hazard_id}, next_state: {next_state}")
        consequence = {
            "type": "hazard_state_change",
            "hazard_id": hazard_id,
            "target_state": next_state
        }
        self.logger.debug(f"[_build_auto_advance_consequence] Built consequence: {consequence}")
        return consequence

    def set_hazard_state(self, hazard_id: str, new_state: str, suppress_entry_effects: bool = False, messages=None) -> dict:
        """
        Transition a hazard to a new state and return the resulting consequences.
        Orchestrates validation, state updates, entry actions, and consequence chaining.
        """
        try:
            # 1. Validate Hazard Existence
            hazard = self.active_hazards.get(hazard_id)
            if not hazard:
                self.logger.warning(f"[set_hazard_state] Hazard '{hazard_id}' not found.")
                return {"consequences": []}

            # 2. Proximity & Ambush Checks (Defer if offscreen)
            if self._should_defer_state_change(hazard, new_state, suppress_entry_effects):
                return {"consequences": []}

            # 3. Update Internal State
            current_state = hazard.get('state')
            if current_state == new_state:
                return {"consequences": []}

            # Block transitions OUT of terminal states
            h_master = hazard.get('master_data', {})
            current_sdef = (h_master.get('states', {}) or {}).get(current_state, {})
            if current_sdef.get('is_terminal_state', False):
                self.logger.info(f"[set_hazard_state] Blocked: '{hazard_id}' is in terminal state '{current_state}'. Cannot transition to '{new_state}'.")
                return {"consequences": []}

            prev_state = current_state
            hazard['state'] = new_state
            # 3b. Terminal State Guard — do not transition OUT of terminal states
            sdef_current = self._resolve_state_def_raw(hazard, current_state)
            if sdef_current and sdef_current.get('is_terminal_state', False):
                self.logger.info(f"[set_hazard_state] Blocked transition '{hazard_id}': '{current_state}' is a terminal state.")
                hazard['state'] = current_state  # Revert
                return {"consequences": []}
            hazard['time_in_state'] = 0.0    # Real-time seconds counter — reset
            hazard['turns_in_state'] = 0     # Turn counter — reset
            
            self.logger.info(f"[set_hazard_state] Hazard '{hazard_id}' transitioned: '{prev_state}' -> '{new_state}'")

            if suppress_entry_effects:
                return {"consequences": []}

            # 4. Resolve State Definition
            sdef = self._resolve_state_def(hazard, new_state)

            # 5. Execute Immediate Entry Actions
            # triggered_consequences now contains the "Time Steal" message
            triggered_consequences = self._handle_state_entry_logic(hazard_id, sdef)

            # Filter: Separate "Immediate" events (SFX/Move) from "Deferred" messages (Time Steal)
            immediate_cons = []
            deferred_reward_cons = []
            
            for cons in triggered_consequences:
                if cons.get('type') == 'show_message':
                    deferred_reward_cons.append(cons)
                else:
                    immediate_cons.append(cons)

            # 6. Build User-Facing Consequences
            # Pass deferred_reward_cons to the chain builder
            chain_consequences = self._build_consequences_chain(
                hazard_id, sdef, hazard.get('master_data', {}), deferred_reward_cons
            )

            return {"consequences": chain_consequences + immediate_cons}

        except Exception as e:
            self.logger.error(f"[set_hazard_state] Critical failure for '{hazard_id}': {e}", exc_info=True)
            return {"consequences": []}

    # --- Helper: Ambush Logic ---
    def _should_defer_state_change(self, hazard: dict, new_state: str, suppress_entry_effects: bool) -> bool:
        """Determines if a state change should be deferred until the player is present."""
        location = hazard.get('location')
        player_loc = self.game_logic.player.get('location') if self.game_logic else None
        is_present = (location == player_loc)
        
        if hazard.get('can_follow_player') and location == player_loc:
            is_present = True

        master = hazard.get('master_data', {})
        offscreen_ok = master.get('progress_while_offscreen', False)

        if not is_present and not suppress_entry_effects and not offscreen_ok:
            self.logger.info(f"Deferring state change for '{hazard.get('id')}' -> '{new_state}' (Ambush).")
            if location not in self.deferred_ambushes:
                self.deferred_ambushes[location] = []
            self.deferred_ambushes[location].append({"hazard_id": hazard.get('id'), "target_state": new_state})
            return True
        return False

    # --- Helper: Entry Logic (Side Effects) ---
    def _handle_state_entry_logic(self, hazard_id: str, sdef: dict) -> list:
        """
        Executes internal logic for state entry:
        - Applying entry actions/rewards
        - Checking for forced movement
        - Checking for audio triggers
        Returns a list of immediate consequences (like forced moves or SFX).
        """
        consequences = []
        
        # A. Apply Entry Actions & Capture Rewards
        reward_cons = self._apply_entry_actions(sdef, hazard_id)
        consequences.extend(reward_cons)  # Add them to the list

        entry_cons = sdef.get('on_state_entry_consequences', [])
        # Normalize: if it's a flat dict (legacy format), convert to consequence list
        if isinstance(entry_cons, dict):
            normalized = []
            if 'message' in entry_cons:
                normalized.append({'type': 'log_message', 'message': entry_cons['message']})
            if 'hp_damage' in entry_cons:
                normalized.append({'type': 'damage', 'amount': entry_cons['hp_damage']})
            if 'fear_increase' in entry_cons:
                normalized.append({'type': 'update_fear', 'amount': entry_cons['fear_increase']})
            entry_cons = normalized
        for consequence in entry_cons:
            consequences.append(consequence)

        # B. Check for Forced Movement
        move_location = sdef.get('on_state_entry_move_player_to')
        if move_location:
            consequences.append({
                "type": "force_move",
                "location": move_location
            })

        # C. Check for Audio Trigger
        sfx_key = sdef.get('on_state_entry_play_sfx')
        if sfx_key:
            consequences.append({
                "type": "play_sfx",
                "sfx_key": sfx_key
            })
            
        return consequences

    # --- Helper: Consequence Chain Builder ---
    def _build_consequences_chain(self, hazard_id: str, sdef: dict, master: dict, extra_deferred_cons: list = None) -> list:
        """
        Constructs the sequence of UI events:
        1. Log Message (Description)
        2. Setup Popup (Context) OR Native QTE Popup
        3. Death/NPC Death Popup
        """
        consequences = []
        
        # 1. Extract Metadata
        popup_msg, popup_title, qte_entry, pause, next_st = self._extract_entry_metadata(sdef)

        # 2. Log Message (Persist to history)
        if popup_msg:
            consequences.append({
                "type": "log_message",
                "message": popup_msg
            })

        # 3. Build Primary Consequence
        primary_popup_consequence = None
        
        if qte_entry:
            # --- THE FIX: We MUST generate a trigger_qte event if a QTE is defined! ---
            # Do NOT wrap this in a standard text popup unless you explicitly want to.
            # We bypass _build_popup_consequence and emit the native QTE structure.
            qte_type = qte_entry.get('qte_type', 'button_mash')
            qte_duration = qte_entry.get('duration', 5.0)
            qte_context = qte_entry.get('qte_context', {}).copy()
            
            qte_context['qte_source_hazard_id'] = hazard_id
            if next_st:
                qte_context['next_state_on_qte_success'] = next_st
                qte_context['next_state_after_qte_success'] = next_st
            if 'next_state_on_qte_failure' not in qte_context:
                fail_state = sdef.get('next_state_on_qte_failure')
                if fail_state:
                    qte_context['next_state_on_qte_failure'] = fail_state
                    qte_context['next_state_after_qte_failure'] = fail_state
                    
            consequences.append({
                "type": "trigger_qte",
                "qte_type": qte_type,
                "duration": qte_duration,
                "qte_context": qte_context
            })
            
            # If there's ALSO a narrative message, emit it as a loose popup so it 
            # shows up right before the QTE fires.
            if popup_msg:
                consequences.append({
                    "type": "show_popup",
                    "title": popup_title or "Danger!",
                    "message": popup_msg
                })
                
        elif popup_msg:
            # Just a narrative popup
            primary_popup_consequence = self._build_popup_consequence(hazard_id, sdef.get('__state_name__'), popup_title, popup_msg, None, pause, next_st)
        
        # 4. Attach Death / NPC Death Logic (The "Punchline")
        self._attach_death_logic(primary_popup_consequence, consequences, hazard_id, sdef, sdef.get('__state_name__'), extra_deferred_cons)

        # 5. Finalize Chain and Auto-Advance
        
        # --- THE FIX: Kivy Popup Stack Bypass ---
        # When multiple popups fire rapidly (like the Agility Dodge + Doors Sealed),
        # Kivy's 'on_dismiss' callbacks get overwritten and dropped. 
        # We must strip the unreliable UI callback and convert it into a guaranteed backend consequence.
        if primary_popup_consequence and "on_close_set_hazard_state" in primary_popup_consequence:
            guaranteed_target = primary_popup_consequence["on_close_set_hazard_state"].get("target_state")
            if guaranteed_target:
                consequences.append(self._build_auto_advance_consequence(hazard_id, guaranteed_target))
            del primary_popup_consequence["on_close_set_hazard_state"]
        # ----------------------------------------

        if primary_popup_consequence:
            consequences.append(primary_popup_consequence)
            
        # If we have a next state, but NO qte_entry, and NO popup to bind the callback to, 
        # we MUST force the auto-advance immediately.
        if next_st and not qte_entry and not primary_popup_consequence:
            consequences.append(self._build_auto_advance_consequence(hazard_id, next_st))

        return consequences

    # --- Helper: Death Logic ---
    def _attach_death_logic(self, primary_popup: dict, consequences: list, hazard_id: str, sdef: dict, new_state: str, extra_deferred_cons: list = None):
        """
        Determines if this state causes Player Death or NPC Death.
        If so, creates the events and chains them to the primary popup (if it exists),
        or appends them directly to the list (fallback).
        """
        hazard = self.active_hazards.get(hazard_id, {})

        # --- THE FIX: Ensure player is in the blast zone! ---
        player_loc = self.game_logic.player.get('location') if self.game_logic and getattr(self.game_logic, 'player', None) else None
        hazard_loc = hazard.get('location')

        if sdef.get('instant_death_in_room'):
            # Only kill if the hazard has no location (global), or the player is in the same room
            if hazard_loc and player_loc and hazard_loc != player_loc:
                self.logger.info(f"Hazard {hazard_id} is fatal in {hazard_loc}, but player is safe in {player_loc}.")
                return  # Abort the death execution!
        # ----------------------------------------------------

        # Determine Death Type
        is_player_death = sdef.get('instant_death_in_room', False)
        
        # Safety override: If Corbin is mentioned, it's likely NPC death, not player
        if "Corbin" in sdef.get('death_message', ""):
            is_player_death = False

        # Case A: Player Death (Game Over)
        if is_player_death:
            # --- THE FIX: Delegate to GameLogic ---
            # Check that game_logic exists and has the method before calling it
            if self.game_logic and hasattr(self.game_logic, '_intercept_visionary_death'):
                if self.game_logic._intercept_visionary_death():
                    # [Let it handle the premonition reset]
                    return
            # --------------------------------------
            self.is_game_over = True
            death_msg = sdef.get('death_message') or sdef.get('description') or "You died."
            
            # Update GameLogic State
            if self.game_logic:
                self.game_logic.player['death_hazard_id'] = hazard_id
                self.game_logic.player['death_hazard_state'] = new_state
                self.game_logic.is_game_over = True
                self.game_logic.player['death_reason'] = death_msg
            
            self.logger.info(f"Hazard '{hazard_id}' state '{new_state}' is fatal. Triggering Game Over.")
            
            game_over_event = {"type": "game_over", "death_reason": death_msg}
            
            # Chain to Setup Popup if possible, else immediate
            if primary_popup:
                primary_popup.setdefault("on_close_emit_ui_events", []).append(game_over_event)
            else:
                consequences.append(game_over_event)

        # Case B: NPC Death (Narrative Popup + Fear)
        elif sdef.get('death_message'):
            self.logger.info(f"Hazard '{hazard_id}' triggered non-player death event.")
            
            # The "Punchline" Popup
            death_popup = {
                "type": "show_popup",
                "title": "Fatal Event",
                "message": sdef.get('death_message'),
                "output_panel": True,
                "vfx_hint": "damage",
                "on_close_emit_ui_events": [] # Prepare list
            }

            # 1. Add Fear Update
            death_popup["on_close_emit_ui_events"].append({
                 "type": "update_fear",
                 "amount": 0.25,
                 "reason": "witness_npc_death"
            })

            # 2. Add Time Steal / Reward Messages
            if extra_deferred_cons:
                death_popup["on_close_emit_ui_events"].extend(extra_deferred_cons)

            # Chain to Setup Popup
            if primary_popup:
                primary_popup.setdefault("on_close_emit_ui_events", []).append(death_popup)
            else:
                consequences.append(death_popup)

    def trigger_ambushes_for_room(self, room_id):
        if room_id not in self.deferred_ambushes: return []
        self.logger.info(f"Triggering ambushes for room '{room_id}'")
        ambushes = self.deferred_ambushes.pop(room_id)
        all_cons = []
        for amb in ambushes:
            res = self.set_hazard_state(amb['hazard_id'], amb['target_state'], suppress_entry_effects=False)
            all_cons.extend(res.get('consequences', []))
        return all_cons

    def get_save_state(self) -> dict:
        """Get the current state for saving."""
        return {
            "active_hazards": self.active_hazards.copy(),
            "escalation_level": getattr(self, 'escalation_level', 0),
            "room_hazard_counters": getattr(self, 'room_hazard_counters', {}),
            "global_flags": getattr(self, 'global_flags', {})
        }

    def _handle_timed_transition(self, hazard_id: str, target_state: str):
        """Handle timed state transitions by notifying GameLogic"""
        if self.game_logic:
            result = self.set_hazard_state(hazard_id, target_state)
            # Let GameLogic handle the consequences
            for consequence in result.get("consequences", []):
                self.game_logic.handle_hazard_consequence(consequence)

    def _find_targetable_hazard_in_room(self, room_name: str, self_id: str, interaction_rule: dict) -> dict | None:
        """Finds another hazard in the same room that can be influenced."""
        potential_targets = []
        valid_target_types = {i.get('if_target_is') for i in interaction_rule.get('interactions', [])}

        for hazard_id, hazard in list(self.active_hazards.items()):
            # A hazard cannot influence itself, and must be in the same room.
            if hazard_id == self_id or hazard.get('location') != room_name:
                continue
            
            # Check if the hazard is one of the types we can influence.
            if hazard.get('type') in valid_target_types:
                potential_targets.append(hazard)

        if potential_targets:
            return random.choice(potential_targets)
        
        return None

    def set_hazard_state_by_type(self, room_name: str, hazard_type: str, new_state: str, suppress_entry_effects: bool = False) -> list:
        """Set state for the hazard of a given type at a specific room. Returns UI events."""
        hid = self.get_hazard_instance_id_by_type(room_name, hazard_type)
        if not hid:
            self.logger.warning(f"[set_hazard_state_by_type] No '{hazard_type}' hazard found at '{room_name}'.")
            return []
        
        # Return the UI events from set_hazard_state
        return self.set_hazard_state(hid, new_state, suppress_entry_effects=suppress_entry_effects)

    def _resolve_state_def_raw(self, hazard: dict, state_name: str) -> dict:
        """Quick lookup of a state definition without deep copy or injection."""
        hdef = hazard.get('master_data', {}) or {}
        return (hdef.get('states', {}) or {}).get(state_name, {})

    def get_hazard_instance_id_by_type(self, room_name: str, hazard_type: str) -> str | None:
        """Return the hazard instance id for the given type in the given room, if any."""
        try:
            ht = (hazard_type or "").strip().lower()
            rn = (room_name or "").strip()
        except Exception:
            ht = hazard_type
            rn = room_name
        for hid, inst in (self.active_hazards or {}).items():
            if inst.get('location') == rn and (inst.get('type') or '').lower() == ht:
                return hid
        return None
    
    # ------------------------------------------------------------------------
    # --- HAZARD SPECIAL ACTION DISPATCHER ---
    # ------------------------------------------------------------------------

    def _maybe_run_special_action(self, sdef: dict, hazard_id: str) -> list:
        """
        Dispatch on_state_entry_special_action to canonical helper methods.
        """
        consequences = []
        if not self.game_logic:
            return consequences

        action = sdef.get('on_state_entry_special_action')
        if not action:
            return consequences

        # Parse action into name + data
        action_name = action.get('action', '') if isinstance(action, dict) else (action if isinstance(action, str) else '')
        action_data = action if isinstance(action, dict) else {}

        if not action_name:
            return consequences

        self.logger.info(f"Executing special action '{action_name}' for hazard '{hazard_id}'")

        # 1. Immediate Intercepts (Endgame)
        action_effect = action_data.get("action_effect", "")
        if action_effect.startswith("trigger_finale_"):
            self._trigger_finale(action_effect)
            return consequences # Halt all other processing

        # 2. The Modular Dispatcher
        if action_name == 'trigger_level_transition':
            consequences.extend(self._action_trigger_level_transition())
            
        elif action_name == 'mri_lock_doors_and_initiate_qtes':
            self._action_mri_lock_doors(hazard_id, sdef, consequences)
            
        elif action_name == 'mri_unlock_doors_and_release_items':
            self._action_mri_unlock_doors(hazard_id, sdef, consequences)
            
        elif action_name == 'mri_process_wave':
            self._mri_process_wave(hazard_id, sdef, consequences)
            
        elif action_name == 'propagate_hazard':
            consequences.extend(self._action_propagate_hazard(action_data, hazard_id))

        # Inside _maybe_run_special_action, add to the action_name dispatcher:
        elif action_name == 'move_player_to_room' or action_data.get('type') == 'move_player_to_room':
            target_room = action_data.get('target_room')
            if target_room and self.game_logic:
                self.logger.info(
                    f"_maybe_run_special_action: Force-moving player to '{target_room}' "
                    f"(elevator terminal state)"
                )
                # Clear transit flags before moving
                self.game_logic.player.pop('elevator_transit_active', None)
                self.game_logic.player.pop('pending_elevator_dest', None)
                self.game_logic.player.pop('pending_elevator_floor', None)

                # Apply HP damage for hard landing if flagged
                h = self.active_hazards.get(hazard_id, {})
                if h.get('state') == 'hard_landing_survival':
                    self.game_logic.player['hp'] = 1
                    consequences.append({
                        "type": "show_message",
                        "message": "[color=ff4444]You hit the bottom of the shaft. You're barely alive.[/color]"
                    })

                # Force move
                if target_room in (self.game_logic.current_level_rooms_world_state or {}):
                    self.game_logic.player['location'] = target_room
                    self.game_logic.player.setdefault('visited_rooms', set()).add(target_room)
                    consequences.append({"type": "move_player_to_room", "target_room": target_room})
                    # Reset elevator hazard to prevent it being used again
                    self.reset_elevator_hazard("Elevator Car")
            
        # 3. Dynamic String Fallback
        elif hasattr(self, action_name):
            try:
                getattr(self, action_name)(hazard_id, sdef, consequences)
            except Exception as e:
                self.logger.error(f"[special] Failed to invoke '{action_name}': {e}", exc_info=True)

        return consequences

    # ------------------------------------------------------------------------
    # --- ACTION HELPERS ---
    # ------------------------------------------------------------------------

    def _action_trigger_level_transition(self) -> list:
        """Flags the level for completion. Relies on game_logic to safely emit UI events."""
        if self.game_logic.qte_engine:
            self.game_logic.qte_engine._force_qte_cleanup()

        self.game_logic.is_transitioning = True
        self.game_logic.player['level_complete_flag'] = True
        
        self.logger.info("[special] Level transition flagged. Delegating UI emission to game_logic.")
        return []

    def _action_propagate_hazard(self, action_data: dict, hazard_id: str) -> list:
        """Spreads a hazard through connections (like HVAC vents) to adjacent rooms."""
        consequences = []
        existing = None
        target_room = None
        hazard = self.active_hazards.get(hazard_id)
        if not hazard: return consequences

        source_room = hazard.get('location')
        connections = (self.game_logic.get_room_data(source_room) or {}).get('connections', {})
        
        conn_type = action_data.get('connection_type', 'hvac')
        target_rooms = connections.get(conn_type, [])
        max_rooms = int(action_data.get('max_propagation_rooms', 99))
        
        spawn_type = action_data.get('spawns_hazard')
        spawn_state = action_data.get('spawns_in_state')
        escalate_state = action_data.get('escalates_existing_to')
        target_msg = action_data.get('message_in_target', '')

        if not spawn_type:
            self.logger.warning(f"propagate_hazard: no spawns_hazard defined for {hazard_id}")
            return consequences

        propagated = 0
        for target_room in target_rooms:
            if propagated >= max_rooms: break

            # Check sealed vents
            if conn_type == 'hvac':
                seal_flag = f"vent_sealed_{target_room}"
                if seal_flag in getattr(self.game_logic, 'interaction_flags', set()):
                    continue

            # Check existing hazard in target room
            existing = next(((hid, h) for hid, h in self.active_hazards.items() 
                           if h.get('type') == spawn_type and h.get('location') == target_room), None)

        if existing and escalate_state:
            hid, h = existing
            if h.get('state') != escalate_state:
                self.set_hazard_state(hid, escalate_state)  # ← complete the call
                propagated += 1
                if target_msg and self.game_logic:
                    consequences.append({
                        "type": "show_message",
                        "message": target_msg
                    })
        elif not existing:
            # Spawn a fresh instance of the hazard in the target room
            new_id = self._add_active_hazard(
                hazard_type=spawn_type,
                location=target_room,
                initial_state_override=spawn_state,
                source_trigger_id=hazard_id   # track origin
            )
            if new_id:
                propagated += 1
                if target_msg and self.game_logic:
                    consequences.append({
                        "type": "show_message",
                        "message": target_msg
                    })

        return consequences

    def _action_mri_lock_doors(self, hazard_id: str, state_info: dict, consequences: list):
        """Locks doors defined in the hazard state's door lock configuration."""
        self.logger.info(f"[_action_mri_lock_doors] Executing for hazard '{hazard_id}'")
        
        if not self.game_logic:
            self.logger.error("[_action_mri_lock_doors] game_logic not set")
            return
        
        # Get doors to lock from state definition
        doors_to_lock = state_info.get("doors_to_lock", [
            {"room": "MRI Scan Room", "exit": "west", "target": "MRI Control Room"},
            {"room": "MRI Scan Room", "exit": "south", "target": "Stairwell"}
        ])
        
        locked_count = 0
        for lock_rule in doors_to_lock:
            room_name = lock_rule.get("room")
            target_room = lock_rule.get("target")
            
            # --- THE FIX: Try exact match, then try slugified match ---
            target_data = self.game_logic.current_level_rooms_world_state.get(target_room)
            
            if not target_data:
                # Try lowercasing and replacing spaces with underscores
                slug_target = target_room.lower().replace(" ", "_")
                target_data = self.game_logic.current_level_rooms_world_state.get(slug_target)
            
            if not target_data:
                self.logger.warning(f"[_action_mri_lock_doors] Room '{target_room}' NOT FOUND in world state.")
                continue
            
            # Store original lock state before modifying
            if "locked_by_mri" not in target_data:
                target_data["original_locked_state"] = target_data.get("locked", False)
            
            # Lock the room
            target_data["locked"] = True
            target_data["magnetically_sealed"] = True
            target_data["locked_by_mri"] = True
            locked_count += 1
            
            self.logger.info(f"[_action_mri_lock_doors] Locked '{target_room}' (from '{room_name}')")
        
        if locked_count > 0:
            consequences.append({
                "type": "show_popup",
                "title": "Doors Sealed!",
                "message": f"As your fingers touch the key, the door you entered through closes with a deafening [color=FFFF00]SLAM![/color]\n\nThe vibration knocks a clipboard in the control room from its hook; it falls onto the workstation, the metal clip sliding across the console's keyboard, and...\n\n[color=ff0000]*BEEP!*[/color]\n\n...shit.",
                "output_panel": True
            })

    def _action_mri_unlock_doors(self, hazard_id: str, state_info: dict, consequences: list):
        """Unlocks doors that were locked by the MRI hazard."""
        self.logger.info(f"[_action_mri_unlock_doors] Executing for hazard '{hazard_id}'")
        self.game_logic.set_player_flag("post_mri", True)
        if not self.game_logic:
            self.logger.error("[_action_mri_unlock_doors] game_logic not set")
            return
        
        # Find all rooms locked by MRI and restore their original state
        unlocked_count = 0
        for room_id, room_data in self.game_logic.current_level_rooms_world_state.items():
            if room_data.get("locked_by_mri"):
                # Restore original lock state
                original = room_data.get("original_locked_state", False)
                room_data["locked"] = original
                room_data.pop("locked_by_mri", None)
                room_data.pop("original_locked_state", None)
                room_data.pop("magnetically_sealed", None)
                unlocked_count += 1
                self.logger.info(f"[_action_mri_unlock_doors] Unlocked '{room_id}'")
        
        if unlocked_count > 0:
            consequences.append({
                "type": "show_popup",
                "title": "Magnetic Field Collapsed",
                "message": "The doors are no longer sealed by the magnetic field!",
                "output_panel": True
            })

    def _mri_process_wave(self, hazard_id: str, sdef: dict, consequences: list):
        """
        Scans for ALL metallic items under the weight class based on the current MRI power wave.
        Each wave increases the search radius and weight threshold.
        If dynamic items are found, they fly at the player as a massive grouped QTE barrage.
        If not, falls back to the scripted chain for this wave.
        """
        hazard_inst = self.active_hazards.get(hazard_id)
        if not hazard_inst:
            return
        
        items_master = self.resource_manager.get_data('items', {}) or {}
        constants = self.resource_manager.get_data('constants', {})
        weight_cats = constants.get('PHYSICS', {}).get('WEIGHT_CATEGORIES', {})
        
        wave = hazard_inst.get('mri_wave', 0)
        already_pulled = set(hazard_inst.get('mri_pulled_items', []))
        mri_room = hazard_inst.get('location')
        
        # Wave configuration: weight thresholds, search radius, fallback states
        wave_config = [
            {   # Wave 1: Light items from adjacent rooms
                'max_weight': weight_cats.get('light', 1.0),
                'radius': 1,
                'fallback_state': sdef.get('wave_1_fallback', 'projectile_stage_1_cart')
            },
            {   # Wave 2: Medium items from 2-room radius
                'max_weight': weight_cats.get('medium', 3.0),
                'radius': 2,
                'fallback_state': sdef.get('wave_2_fallback', 'projectile_stage_2_window')
            },
            {   # Wave 3: Heavy items from the Radiology Wing
                'max_weight': weight_cats.get('very_heavy', 10.0),
                'radius': 3,
                'fallback_state': sdef.get('wave_3_fallback', 'final_barrage_wheelchair')
            },
        ]
        
        if wave >= len(wave_config):
            # All waves done — cooldown
            res = self.set_hazard_state(hazard_id, "mri_cooldown")
            consequences.extend(res.get('consequences', []))
            return
        
        config = wave_config[wave]
        
        # --- Scan for ALL metallic items within radius ---
        rooms_in_range = self._get_rooms_within_radius(mri_room, config['radius'])
        
        pulled_items = []
        
        for room_id in rooms_in_range:
            room_data = self.game_logic.current_level_rooms_world_state.get(room_id, {})
            
            # Helper to check item validity and add to pulled list
            def _check_item(i_key, source_info):
                if i_key in already_pulled: return
                item_def = items_master.get(i_key, {})
                if not item_def.get('is_metallic', False): return
                raw_weight = item_def.get('weight', 'default')
                i_weight = weight_cats.get(raw_weight, weight_cats.get('default', 2.0)) if isinstance(raw_weight, str) else float(raw_weight)
                
                if i_weight <= config['max_weight']:
                    pulled_items.append({"key": i_key, "weight": i_weight, "source": source_info})

            # 1. Scan loose items
            for item_key in room_data.get('items', []):
                _check_item(item_key, {'type': 'items', 'list': room_data.get('items')})
            # 2. Scan furniture
            for furn in room_data.get('furniture', []):
                if isinstance(furn, str):
                    _check_item(furn, {'type': 'furniture', 'list': room_data.get('furniture')})
                elif isinstance(furn, dict):
                    furn_id = furn.get('id') or furn.get('name')
                    if furn_id: _check_item(furn_id, {'type': 'furniture', 'list': room_data.get('furniture')})
                    for item_key in furn.get('items', []):
                        _check_item(item_key, {'type': 'furniture_contents', 'list': furn.get('items')})
            # 3. Scan objects
            for obj in room_data.get('objects', []):
                if isinstance(obj, str):
                    _check_item(obj, {'type': 'objects', 'list': room_data.get('objects')})
                elif isinstance(obj, dict):
                    obj_id = obj.get('id') or obj.get('name')
                    if obj_id: _check_item(obj_id, {'type': 'objects', 'list': room_data.get('objects')})
            # 4. Fallback: world state
            for item_key, item_state in self.game_logic.current_level_items_world_state.items():
                if item_state.get('location') == room_id:
                    if not any(p['key'] == item_key for p in pulled_items):
                        _check_item(item_key, {'type': 'world_state'})
        
        hazard_inst['mri_wave'] = wave + 1
        
        if pulled_items:
            display_names = []
            has_chain_reaction = None
            
            for pulled in pulled_items:
                candidate = pulled['key']
                candidate_source = pulled['source']
                item_name = items_master.get(candidate, {}).get('name', candidate.replace('_', ' ').title())
                display_names.append(item_name)
                
                if candidate_source['type'] in ('items', 'furniture', 'furniture_contents', 'objects'):
                    source_list = candidate_source['list']
                    for element in list(source_list):
                        if element == candidate or (isinstance(element, dict) and (element.get('id') == candidate or element.get('name') == candidate)):
                            source_list.remove(element)
                            break

                if candidate in self.game_logic.current_level_items_world_state:
                    self.game_logic.current_level_items_world_state[candidate]['location'] = mri_room
                else:
                    self.game_logic.current_level_items_world_state[candidate] = {'location': mri_room}
                
                hazard_inst.setdefault('mri_pulled_items', []).append(candidate)
                trigger_hazard = items_master.get(candidate, {}).get('trigger_hazard_on_action', {}).get('mri_pull')
                if trigger_hazard: has_chain_reaction = trigger_hazard
            
            unique_names = list(dict.fromkeys(display_names))
            joined_names = self.game_logic._join_names(unique_names)
            hazard_inst['target_object_override'] = joined_names
            
            self.logger.info(f"[MRI Wave {wave+1}] Dynamic pull: {len(pulled_items)} items -> ({joined_names})")
            
            if has_chain_reaction:
                self.logger.info(f"[MRI Wave {wave+1}] Chain reaction: '{joined_names}' triggers '{has_chain_reaction}'!")
                hazard_inst['mri_chain_trigger'] = has_chain_reaction
            
            # --- THE FIX: Inject QTE Continuation States ---
            self.logger.info(f"[MRI DEBUG] Injecting dynamic states into wave {wave+1}")
            master_state = hazard_inst.get('master_data', {}).get('states', {}).get('dynamic_projectile_incoming', {})
            qte_def = master_state.get('triggers_qte_on_entry')
            
            if qte_def:
                # 1. Prepare the Context in the MASTER data so set_hazard_state reads it natively!
                ctx = qte_def.setdefault('qte_context', {})
                
                # 2. Inject Continuation States directly into the hazard's active memory
                ctx['next_state_after_qte_success'] = 'mri_wave_evaluator'
                ctx['next_state_on_qte_success'] = 'mri_wave_evaluator'
                
                fail_state = sdef.get('next_state_on_qte_failure', 'mri_projectile_impact')
                ctx['next_state_after_qte_failure'] = fail_state
                ctx['next_state_on_qte_failure'] = fail_state
                
                # 3. Format the UI Prompt to include the pulled items
                prompt = ctx.get('ui_prompt_message', 'Incoming projectile!')
                if "{object_name}" in prompt:
                    ctx['ui_prompt_message'] = prompt.replace("{object_name}", joined_names)
                else:
                    ctx['ui_prompt_message'] = prompt + f" ({joined_names})"

            # 4. Transition state natively! Do NOT suppress entry effects.
            # This allows the engine to naturally catch triggers_qte_on_entry and build the real UI popup!
            self.logger.info("[MRI DEBUG] Triggering 'dynamic_projectile_incoming' natively.")
            res = self.set_hazard_state(hazard_id, "dynamic_projectile_incoming", suppress_entry_effects=False)
            consequences.extend(res.get('consequences', []))
            
        else:
            self.logger.info(f"[MRI Wave {wave+1}] No dynamic items found. Using fallback: {config['fallback_state']}")
            res = self.set_hazard_state(hazard_id, config['fallback_state'])
            consequences.extend(res.get('consequences', []))
    
    def _get_rooms_within_radius(self, start_room: str, radius: int) -> set:
        """BFS to find all rooms within N exits of the start room."""
        rooms = self.game_logic.current_level_rooms_world_state or {}
        visited = {start_room}
        frontier = [start_room]
        
        for _ in range(radius):
            next_frontier = []
            for room_id in frontier:
                room_data = rooms.get(room_id, {})
                for dest in room_data.get('exits', {}).values():
                    target = dest.get('target') if isinstance(dest, dict) else dest
                    if target and target not in visited:
                        visited.add(target)
                        next_frontier.append(target)
            frontier = next_frontier
        
        visited.discard(start_room)  # Don't include the MRI room itself
        return visited

    def _process_state_entry_rewards(self, sdef, hazard_id=None) -> list:
        """
        Award items, achievements, score, and TIME bonuses.
        Returns a list of UI consequences (e.g. messages) to be displayed later.
        """
        rewards = sdef.get('on_state_entry_rewards', {})
        consequences = []
        
        if not rewards or not self.game_logic:
            return consequences

        # >>> PATCH START: Auto-record evasion on reward <<<
        # If the player is getting points or achievements from a hazard state, 
        # they have likely neutralized or survived it.
        if (rewards.get('score_bonus', 0) > 0 or rewards.get('achievements_to_unlock')) and hazard_id:
            if hasattr(self.game_logic, "record_hazard_evasion"):
                try:
                    self.game_logic.record_hazard_evasion(hazard_id)
                    self.logger.info(f"Auto-recorded hazard evasion for '{hazard_id}' due to reward.")
                except Exception as e:
                    self.logger.error(f"Failed to auto-record hazard evasion for '{hazard_id}': {e}", exc_info=True)
        # >>> PATCH END <<<

        # Award items
        items = rewards.get('items_granted', [])
        for item_id in items:
            # Ensure inventory is a LIST (GameLogic standard)
            if 'inventory' not in self.game_logic.player:
                self.game_logic.player['inventory'] = []
            elif isinstance(self.game_logic.player['inventory'], dict):
                # Convert dict inventory back to list keys (rescue data)
                old_keys = list(self.game_logic.player['inventory'].keys())
                self.game_logic.player['inventory'] = old_keys
                self.logger.warning(f"Helper: Converted player inventory from dict {old_keys} back to list.")
            
            self.game_logic.player['inventory'].append(item_id)
            self.logger.info(f"Awarded item '{item_id}' to player.")

        # Unlock achievements
        achievements = rewards.get('achievements_to_unlock', [])
        for ach_id in achievements:
            if hasattr(self.game_logic, 'achievements_system'):
                self.game_logic.achievements_system.unlock(ach_id)
                self.logger.info(f"Unlocked achievement '{ach_id}'.")

        # Add score bonus
        score_bonus = rewards.get('score_bonus', 0)
        if score_bonus:
            self.game_logic.player['score'] = self.game_logic.player.get('score', 0) + score_bonus
            self.logger.info(f"Added score bonus: {score_bonus}")

        # --- Time Steal Mechanic ---
        turns_bonus = rewards.get('turns_bonus', 0)
        if turns_bonus:
            self.game_logic.player['turns_left'] = self.game_logic.player.get('turns_left', 0) + turns_bonus
            self.logger.info(f"Time Steal! Added {turns_bonus} turns to player lifespan.")
            
            # Create the consequence but DO NOT add_ui_event yet. Return it.
            msg = f"'Did you or didn't you?'\nYou may have gained {turns_bonus} turns of life for playing a part in what just happened."
            consequences.append({
                "type": "show_message",
                "message": color_text(msg, "special", self.resource_manager)
            })
        
        return consequences

    def _maybe_progress_on_flags(self, hazard_id: str) -> list:
        """Check flags and advance state. Ensures one-time execution per state."""
        hazard = self.active_hazards.get(hazard_id)
        if not hazard: return []
        
        hdef = hazard.get('master_data', {}) or {}
        cur_state = hazard.get('state')
        sdef = hdef.get('states', {}).get(cur_state, {}) or {}
        
        # 1. Check if this state actually relies on flags
        prog = sdef.get('progression_condition') or {}
        req_flags = set(prog.get('requires_all_flags', []))
        if not req_flags: return []

        if not self.game_logic: return []

        # 2. Check if flags match
        if not req_flags.issubset(self.game_logic.interaction_flags):
            return []

        # 3. FIX: Prevent infinite loop. 
        # If we already triggered this specific state progression, stop.
        if hazard.get('last_flag_progression_state') == cur_state:
            return []

        # 4. Execute Progression
        next_state = sdef.get('next_state')
        if next_state:
            self.logger.info(f"Hazard '{hazard_id}' progressing on flags from '{cur_state}' -> '{next_state}'")
            
            # Mark this state as "processed" so we don't loop
            hazard['last_flag_progression_state'] = cur_state 
            
            try:
                result = self.set_hazard_state(hazard_id, next_state)
                return result.get("consequences", [])
            except Exception as e:
                self.logger.error(f"_maybe_progress_on_flags error: {e}", exc_info=True)
                return []
        return []

    # --- Helpers for process_player_interaction ---

    def _norm_text(self, s: str) -> str:
        try:
            return self.game_logic._norm(s)
        except Exception:
            return str(s).strip().lower().replace('_', ' ')

    def _synonyms_for(self, name: str, items_master: dict) -> Set[str]:
        """Build alias/synonym set for a typed target using items.json"""
        norm = self._norm_text
        syns = {norm(name)}
        for key, data in items_master.items():
            names = {norm(key), norm(data.get('name', key))}
            aliases = {norm(a) for a in (data.get('aliases') or [])}
            if norm(name) in names or norm(name) in aliases:
                # FIX: Use standard set update. 
                # Do NOT use Union[names, aliases] as an operand.
                syns.update(names)
                syns.update(aliases)
        return syns

    def _collect_rules_for_hazard(self, h_master: dict, verb: str) -> list[dict]:
        """Merge player_interaction[verb] with triggered_by_room_action rules for same verb."""
        pi_rules = (h_master.get('player_interaction', {}) or {}).get(verb, [])
        tra_rules = [
            r for r in (h_master.get('triggered_by_room_action', []) or [])
            if isinstance(r, dict) and r.get('action_verb') == verb
        ]
        return list(pi_rules) + tra_rules

    def _rule_matches(self, rule: dict, current_state: str, target_syns: Set[str]) -> bool:
        """Check required state, target names, AND inventory."""
        norm = self._norm_text
        valid_targets = rule.get('on_target_name', [])
        if not isinstance(valid_targets, list):
            valid_targets = [valid_targets]
        valid_targets_norm = {norm(v) for v in valid_targets if isinstance(v, str)}
        required_states = rule.get('requires_hazard_state')

        if required_states and current_state not in required_states:
            return False
        if valid_targets_norm and target_syns.isdisjoint(valid_targets_norm):
            return False
            
        # --- NEW LOGIC STARTS HERE ---
        # Check if the rule requires specific items in inventory
        required_items = rule.get('requires_inventory', [])
        if required_items and self.game_logic:
            # Get current inventory keys safely
            player_inv = self.game_logic.player.get('inventory', [])
            if isinstance(player_inv, dict):
                inv_keys = set(player_inv.keys())
            else:
                inv_keys = set(player_inv) # Assume list of strings
            
            # If any required item is missing, the rule does not match
            for item in required_items:
                if item not in inv_keys:
                    return False
        # --- NEW LOGIC ENDS HERE ---

        # Check if the rule requires level completion (all items + evidence collected)
        if rule.get('requires_level_completion') and self.game_logic:
            met, missing = self.game_logic._requirements_met_for_level_exit()
            if not met:
                self.logger.info(f"[_rule_matches] requires_level_completion not met, missing: {missing}")
                return False

        return True

    def _apply_rule_side_effects(self, hazard_id: str, rule: dict) -> tuple[list, list, bool]:
        """
        Apply flags, popup, state changes, QTEs, and messages for a single rule.
        Returns (consequences, messages, blocks_action_for_rule).
        """
        consequences: list = []
        messages: list = []
        blocks_action = bool(rule.get('blocks_action_success'))

        # Flags
        if 'set_player_flag' in rule:
            flag = rule['set_player_flag']
            self.logger.debug(f"[process_player_interaction] set_player_flag: {flag}")
            self.game_logic.set_player_flag(flag, True)

        if 'sets_interaction_flag' in rule:
            flag = rule['sets_interaction_flag']
            self.logger.debug(f"[process_player_interaction] sets_interaction_flag: {flag}")
            self.game_logic.set_interaction_flag(flag)

        # Popup
        popup_event = rule.get('ui_popup_event')
        if popup_event and self.game_logic:
            popup_cmd = {
                "event_type": popup_event.get('type', 'show_popup'),
                "title": popup_event.get('title', 'Alert'),
                "message": popup_event.get('message', ''),
                "takes_turn": popup_event.get('takes_turn', False)
            }
            self.game_logic.add_ui_event(popup_cmd)

        # State change via rule
        effect = rule.get('effect_on_self') or {}
        next_state = effect.get('target_state') or rule.get('target_state')
        if next_state:
            self.logger.debug(f"[process_player_interaction] Setting hazard '{hazard_id}' state -> '{next_state}' from rule")
            try:
                result = self.set_hazard_state(hazard_id, next_state)
                consequences.extend(result.get("consequences", []))
            except Exception as e:
                self.logger.error(f"[process_player_interaction] Failed to set state '{next_state}' for '{hazard_id}': {e}")

        # QTE
        raw_qte_type = rule.get('qte_type') or rule.get('qte_to_trigger')
        if raw_qte_type and self.game_logic and hasattr(self.game_logic, 'qte_engine'):
            
            # --- THE FIX: Sanitize the QTE Type! ---
            qte_type = self._resolve_qte_type(raw_qte_type)
            
            # <<< PATCH START: Use .copy() and inject the hazard_id >>>
            raw_ctx = rule.get('qte_context', {})
            qte_context = raw_ctx.copy() if isinstance(raw_ctx, dict) else {}
            qte_context['qte_source_hazard_id'] = hazard_id
            # <<< PATCH END >>>

            if qte_type == 'button_mash':
                best_tool, bonus = self.game_logic._best_tool_in_inventory()
                if bonus > 0:
                    original_target = qte_context.get('target_mash_count', 15)
                    new_target = max(5, int(original_target) - (bonus * 2))
                    qte_context['target_mash_count'] = new_target

                    tool_name = self.game_logic._get_item_display_name(best_tool)
                    base_prompt = qte_context.get('ui_prompt_message', 'MASH to overcome!')
                    qte_context['ui_prompt_message'] = f"{base_prompt}\n(Using {tool_name}: Difficulty Reduced!)"
                    self.logger.info(f"Applied tool bonus from {best_tool}: Mash Target {original_target} -> {new_target}")

            self.logger.debug(f"[process_player_interaction] Calling QTE Engine to start '{qte_type}' with context: {qte_context}")
            try:
                self.game_logic.qte_engine.start_qte(qte_type, qte_context)
                self.logger.info(f"[process_player_interaction] QTE '{qte_type}' started successfully.")
            except Exception as e:
                self.logger.error(f"[process_player_interaction] Exception while starting QTE '{qte_type}': {e}")
        elif rule.get('qte_type') or rule.get('qte_to_trigger'):
            self.logger.error("[process_player_interaction] HazardEngine cannot trigger QTE: game_logic.qte_engine not found!")

        # Message
        if 'message' in rule:
            try:
                colored_message = color_text(rule['message'], 'info', self.resource_manager)
            except Exception:
                colored_message = rule['message']
            messages.append(colored_message)

        return consequences, messages, blocks_action

    # --- NEW: The Observer Method ---

    def process_player_interaction(self, verb: str, target: str) -> dict:
        """
        Process player interactions and return structured consequences.
        Checks active hazards to see if the player's action triggers any special
        interactions, messages, or flags. Applies all matching rules and forwards
        consequences, including those from flag progression paths.
        """
        self.logger.debug(f"[process_player_interaction] Called with verb='{verb}', target='{target}'")
        consequences: list = []
        messages: list = []
        matched_rules: list = []
        if not self.game_logic:
            self.logger.warning("[process_player_interaction] Game logic not set. Cannot process player interaction.")
            return {"consequences": consequences, "messages": messages, "blocks_action": False}

        player_location = self.game_logic.player.get('location')
        items_master = self.resource_manager.get_data('items', {})
        target_syns = self._synonyms_for(target, items_master)

        self.logger.debug(f"[process_player_interaction] Player location: {player_location}")
        for hazard_id, hazard in list(self.active_hazards.items()):
            self.logger.debug(f"[process_player_interaction] Checking hazard '{hazard_id}' at location '{hazard.get('location')}'")
            if hazard.get('location') != player_location:
                continue

            h_master = hazard.get('master_data', {}) or {}
            current_state = hazard.get('state')
            all_rules = self._collect_rules_for_hazard(h_master, verb)
            self.logger.debug(f"[process_player_interaction] Found {len(all_rules)} rules for verb '{verb}'")

            for rule_idx, rule in enumerate(all_rules):
                self.logger.debug(f"[process_player_interaction] Evaluating rule #{rule_idx}: {rule}")
                if not self._rule_matches(rule, current_state, target_syns):
                    continue

                matched_rules.append(rule)
                hazard['started_by_player'] = True

                # Apply all side effects for this rule
                rule_cons, rule_msgs, _ = self._apply_rule_side_effects(hazard_id, rule)
                if rule_cons:
                    consequences.extend(rule_cons)
                if rule_msgs:
                    messages.extend(rule_msgs)

                # After each matched rule, we may progress by flags; append consequences
                progressed_cons = self._maybe_progress_on_flags(hazard_id)
                if progressed_cons:
                    consequences.extend(progressed_cons)

        # SAFETY NET: run flag progression once more after rules to catch pure-flag paths
        try:
            player_location = self.game_logic.player.get('location')
            for hid, hz in list(self.active_hazards.items()):
                if hz.get('location') == player_location:
                    extra_cons = self._maybe_progress_on_flags(hid)
                    if extra_cons:
                        consequences.extend(extra_cons)
        except Exception as e:
            self.logger.error(f"[process_player_interaction] post-flag progression failed: {e}", exc_info=True)

        self.logger.debug(f"[process_player_interaction] Player interaction complete. Messages: {messages}")
        
        # Default to True if rule explicitly specifies, or if omitted (safe default)
        explicit_allow = any(rule.get('blocks_action_success') is False for rule in matched_rules)
        should_block = any(rule.get('blocks_action_success', True) for rule in matched_rules)
        
        # If the Hazard Engine found a rule for 'examine', we ALWAYS want to block 
        # the default room description so we don't get "Double Vision".
        if verb == 'examine' and matched_rules:
            should_block = True

        # Ensure that if a rule generates a message or consequence (like a QTE), 
        # it blocks the underlying action UNLESS explicitly allowed by the JSON.
        if (messages or consequences) and not explicit_allow:
            should_block = True
        
        # ABSOLUTE DOMINANCE: If the MRI generates ANY consequences, it MUST block the action.
        if any('mri' in str(c).lower() for c in consequences):
            should_block = True
        # --------------------------------

        return {
            "consequences": consequences,
            "messages": messages,
            "blocks_action": should_block
        }

    def get_active_hazards_for_room(self, room_name: str) -> list:
        """
        Returns a list of hazard types for all active hazards in a given room.
        Enhanced with robust logging and debugging.
        """
        self.logger.debug(f"[get_active_hazards_for_room] Called for room: '{room_name}'")
        hazards_in_room = [
            h['type'] for h in self.active_hazards.values()
            if h.get('location') == room_name
        ]
        self.logger.info(f"[get_active_hazards_for_room] Found hazards in '{room_name}': {hazards_in_room}")
        return hazards_in_room

    def get_hazard_state(self, hazard_key, room_name):
        for h in self.active_hazards.values():
            if h['type'] == hazard_key and h['location'] == room_name:
                return h['state']
        return None

    def _check_icu_examination_flags(self, hazard: dict):
        """
        A specific autonomous action for the ventilator hazard. Checks if both
        required flags have been set.
        """
        self.logger.debug("Checking ICU examination flags for hazard progression.")
        if not self.game_logic:
            self.logger.warning("Game logic not set. Cannot check ICU examination flags.")
            return

        required_flags = {'patient_examined_icu_bay', 'ventilator_examined_icu_bay'}
        self.logger.debug(f"Required flags: {required_flags}, current flags: {self.game_logic.interaction_flags}")
        if required_flags.issubset(self.game_logic.interaction_flags):
            self.logger.info("Ventilator hazard progressing due to player examination.")
            hazard['state'] = 'erratic_hiss'  # Or whatever the next state is
            self.logger.debug(f"Hazard state updated to 'erratic_hiss' for hazard: {hazard}")
            # We would also append a message about the change here.

    def _process_autonomous_actions(self, hazard_id, hazard_data):
        """Processes any autonomous actions for a hazard's current state, with robust logging and debugging."""
        self.logger.debug(f"[_process_autonomous_actions] Called for hazard_id='{hazard_id}'")
        state_key = hazard_data.get('state')
        self.logger.debug(f"[_process_autonomous_actions] Current state: '{state_key}'")
        state_info = hazard_data.get('master_data', {}).get('states', {}).get(state_key)

        if not state_info:
            self.logger.warning(f"[_process_autonomous_actions] No state info found for state '{state_key}' in hazard '{hazard_id}'")
            return

        action_name = state_info.get('autonomous_action')
        self.logger.debug(f"[_process_autonomous_actions] Autonomous action: '{action_name}'")

        if action_name:
            # Switchboard for all autonomous actions
            if action_name == '_find_and_launch_projectile_qte':
                self.logger.info(f"[_process_autonomous_actions] Executing '_find_and_launch_projectile_qte' for hazard '{hazard_id}'")
                try:
                    self._action_find_and_launch_projectile(hazard_id, state_info)
                except Exception as e:
                    self.logger.error(f"[_process_autonomous_actions] Exception in '_action_find_and_launch_projectile': {e}", exc_info=True)
            else:
                self.logger.debug(f"[_process_autonomous_actions] Unknown autonomous action '{action_name}' for hazard '{hazard_id}'")
        else:
            self.logger.debug(f"[_process_autonomous_actions] No autonomous action defined for state '{state_key}' in hazard '{hazard_id}'")

    def _action_find_and_launch_projectile(self, hazard_id, state_info, consequences: list = None):
        """
        Finds a metallic object and launches it at the player via a QTE.
        Enhanced with robust logging and debugging.
        """
        if consequences is None:
            consequences = []
        self.logger.debug(f"[_action_find_and_launch_projectile] Called for hazard_id='{hazard_id}'")
        if not self.game_logic:
            self.logger.error("[_action_find_and_launch_projectile] game_logic not set; cannot proceed.")
            return

        in_danger_zone = self.game_logic.get_player_flag('in_mri_danger_zone')
        self.logger.debug(f"[_action_find_and_launch_projectile] Player in danger zone: {in_danger_zone}")
        if not in_danger_zone:
            self.logger.info("[_action_find_and_launch_projectile] Player is not in the danger zone. Projectile will not launch.")
            return

        context = state_info.get('qte_stage_context', {})
        rooms_to_search = context.get('pull_from_rooms', [])
        weight_cats = context.get('pull_weight_categories', [])
        self.logger.info(f"[_action_find_and_launch_projectile] Searching for projectiles in rooms: {rooms_to_search} with weight categories: {weight_cats}")

        potential_projectiles = []
        for room_id in rooms_to_search:
            try:
                items_in_room = self.game_logic.get_items_in_room(room_id)
                self.logger.debug(f"[_action_find_and_launch_projectile] Items in room '{room_id}': {items_in_room}")
            except Exception as e:
                self.logger.error(f"[_action_find_and_launch_projectile] Failed to get items in room '{room_id}': {e}", exc_info=True)
                continue

            for item in items_in_room:
                try:
                    item_master_data = self.game_logic._get_item_master_data(item['id'])
                    is_metallic = item_master_data.get('is_metallic')
                    weight = item_master_data.get('weight')
                    self.logger.debug(f"[_action_find_and_launch_projectile] Checking item '{item['id']}': is_metallic={is_metallic}, weight={weight}")
                    if is_metallic and weight in weight_cats:
                        potential_projectiles.append(item)
                        self.logger.debug(f"[_action_find_and_launch_projectile] Added projectile candidate: {item}")
                except Exception as e:
                    self.logger.error(f"[_action_find_and_launch_projectile] Error processing item '{item}': {e}", exc_info=True)

        if not potential_projectiles:
            self.logger.info("[_action_find_and_launch_projectile] No more projectiles found for this stage.")
            next_state = state_info.get('next_state_if_no_projectiles')
            if next_state:
                self.logger.info(f"[_action_find_and_launch_projectile] Transitioning hazard '{hazard_id}' to next state '{next_state}' due to no projectiles.")
                try:
                    self.set_hazard_state(hazard_id, next_state)
                except Exception as e:
                    self.logger.error(f"[_action_find_and_launch_projectile] Failed to set hazard state '{next_state}' for '{hazard_id}': {e}", exc_info=True)
            return

        # A projectile was found. Pick one and launch it.
        projectile_to_launch = random.choice(potential_projectiles)
        projectile_key = projectile_to_launch['id']
        try:
            projectile_name = self.game_logic._get_item_display_name(projectile_key)
        except Exception as e:
            self.logger.error(f"[_action_find_and_launch_projectile] Failed to get display name for projectile '{projectile_key}': {e}", exc_info=True)
            projectile_name = projectile_key

        self.logger.info(f"[_action_find_and_launch_projectile] Launching projectile: {projectile_name} ({projectile_key})")

        # Trigger the QTE defined in the hazard state
        qte_info = state_info.get('triggers_qte_on_entry', {})
        qte_type = qte_info.get('qte_to_trigger')
        self.logger.debug(f"[_action_find_and_launch_projectile] QTE info: {qte_info}, qte_type: {qte_type}")

        if qte_type and getattr(self.game_logic, 'qte_engine', None):
            try:
                qte_context = dict(qte_info.get('qte_context', {}))
                qte_context['ui_prompt_message'] = f"A {projectile_name} is pulled through the window and flies at your head! DODGE!"
                qte_context['expected_input_word'] = qte_context.get('expected_input_word', 'dodge')
                qte_context['next_state_on_qte_success'] = state_info.get('next_state_on_qte_success')
                qte_context['next_state_on_qte_failure'] = state_info.get('next_state_on_qte_failure')
                qte_context['qte_source_hazard_id'] = hazard_id
                qte_context['projectile_item_id'] = projectile_key

                self.logger.info(f"[_action_find_and_launch_projectile] Starting QTE '{qte_type}' with context: {qte_context}")
                self.game_logic.qte_engine.start_qte(qte_type, qte_context)
            except Exception as e:
                self.logger.error(f"[_action_find_and_launch_projectile] Exception while starting QTE '{qte_type}' for projectile '{projectile_key}': {e}", exc_info=True)
            # Remove the item from the world so it can't be launched again
            try:
                self.logger.info(f"[_action_find_and_launch_projectile] Removing projectile '{projectile_key}' from world.")
                self.game_logic.remove_item_from_world(projectile_key)
            except Exception as e:
                self.logger.error(f"[_action_find_and_launch_projectile] Failed to remove projectile '{projectile_key}' from world: {e}", exc_info=True)
        else:
            self.logger.error(f"[_action_find_and_launch_projectile] Could not trigger QTE for projectile '{projectile_key}'. QTE info missing or engine not found.")

    # =========================================================================
    # MOBILE HAZARD SYSTEM
    # =========================================================================
    #
    # Architecture:
    #   _tick_mobile_hazards(player_location)
    #       └── iterates every active hazard whose master_data has
    #           can_move_between_rooms: true
    #       └── dispatches to a per-type handler via MOBILE_HAZARD_HANDLERS
    #
    #   Each handler signature:
    #       _move_<type>(self, hazard_id, hazard, player_location) -> (msgs, cons)
    #       Returns (list[str], list[dict]) — messages and consequences to merge
    #       upward into process_turn's return value.
    #
    #   MOBILE_HAZARD_HANDLERS is a class-level dict populated after the methods
    #   are defined.  New mobile hazard types only need to add one entry here
    #   plus one handler method.
    #
    # Hazard instance fields used by this system (all set in _add_active_hazard):
    #   movement_cooldown_turns  int   — turns left before next move allowed (≥0)
    #   path_to_target           list  — ordered room names from BFS
    #   seek_target_hazard_id    str|None — specific hazard instance we are hunting
    #   seek_target_room         str|None — destination room
    #   behavior_state           str   — "patrolling" | "seeking" | "waiting"
    #   memory                   dict  — per-instance scratchpad
    # =========================================================================

    def _find_path(self, from_room: str, to_room: str) -> list:
        """
        BFS shortest-path through the room graph.

        Uses the exits dicts in current_level_rooms_world_state as directed edges.
        Both keys (direction string) and values (destination room name) are used
        so that one-way doors still participate in pathfinding.

        Returns:
            list[str] — ordered list of room names starting with the first step
            AFTER from_room and ending with to_room (empty list = no path or same room).

        Example:
            _find_path("Lobby", "Corridor C")
            -> ["Hallway A", "Corridor B", "Corridor C"]
        """
        if not self.game_logic:
            return []

        rooms = self.game_logic.current_level_rooms_world_state
        if not rooms:
            return []

        if from_room == to_room:
            return []

        # Standard BFS — each node is a room name string.
        # `came_from` maps room -> the room we arrived from, for path reconstruction.
        from collections import deque
        queue = deque([from_room])
        came_from = {from_room: None}

        while queue:
            current = queue.popleft()

            if current == to_room:
                # Reconstruct path (excludes from_room, includes to_room)
                path = []
                node = to_room
                while node != from_room:
                    path.append(node)
                    node = came_from[node]
                path.reverse()
                return path

            room_data = rooms.get(current, {})
            exits = room_data.get('exits', {})

            for destination in exits.values():
                # exits values can be strings (room name) or dicts (structured exit)
                if isinstance(destination, dict):
                    destination = destination.get('room') or destination.get('target')
                if not destination or not isinstance(destination, str):
                    continue
                if destination not in came_from:
                    came_from[destination] = current
                    queue.append(destination)

        # No path found (disconnected graph or invalid room names)
        self.logger.debug(f"_find_path: No path found from '{from_room}' to '{to_room}'")
        return []

    def _tick_mobile_hazards(self, player_location: str) -> tuple:
        """
        Per-turn tick for all mobile hazards (can_move_between_rooms: true).

        Dispatches to a registered per-type movement handler.
        Deaths Breath and robo_vacuum are registered by default; new types only
        need a handler method + one entry in MOBILE_HAZARD_HANDLERS.

        Returns:
            (messages: list[str], consequences: list[dict])
        """
        all_messages = []
        all_consequences = []

        for hazard_id, hazard in list(self.active_hazards.items()):
            master = hazard.get('master_data', {})
            if not master.get('can_move_between_rooms', False):
                continue

            # Skip truly inert states — dormant hazards don't wander
            if hazard.get('state') in ('dormant', 'resolved', 'inactive', 'neutralized'):
                continue

            # Cooldown gate — decrement and skip if still cooling down
            cooldown = hazard.get('movement_cooldown_turns', 0)
            if cooldown > 0:
                hazard['movement_cooldown_turns'] = cooldown - 1
                continue

            hazard_type = hazard.get('type', '')
            handler = self.MOBILE_HAZARD_HANDLERS.get(hazard_type)

            # --- Phase 2B: Bleeding boost for seeking hazards ---
            bleeding_boost = False
            if self.game_logic:
                status = self.game_logic.player.get('status_effects')
                # Guard: status_effects may be a list or dict depending on game state
                if isinstance(status, dict):
                    bleeding_turns = status.get('bleeding', 0)
                    if bleeding_turns > 0:
                        bleeding_boost = True
                        hazard['_bleeding_boost'] = True

            if handler is None:
                # No registered handler — log once then skip silently
                if not hazard.get('_warned_no_handler'):
                    self.logger.warning(
                        f"_tick_mobile_hazards: hazard type '{hazard_type}' has "
                        f"can_move_between_rooms=true but no movement handler registered."
                    )
                    hazard['_warned_no_handler'] = True
                continue

            try:
                msgs, cons = handler(self, hazard_id, hazard, player_location)
                all_messages.extend(msgs)
                all_consequences.extend(cons)
            except Exception as e:
                self.logger.error(
                    f"_tick_mobile_hazards: handler for '{hazard_type}' raised: {e}",
                    exc_info=True
                )

        return all_messages, all_consequences

    def _move_deaths_breath(self, hazard_id: str, hazard: dict, player_location: str) -> tuple:
        messages = []
        consequences = []

        if not self.game_logic:
            return messages, consequences

        player_fear = self.game_logic.player.get('fear', 0.0)
        current_location = hazard.get('location')

        if player_fear < 0.7:
            return messages, consequences

        if current_location == player_location:
            return messages, consequences

        current_room_data = self.game_logic.get_room_data(current_location)
        if not current_room_data:
            return messages, consequences

        exits = current_room_data.get('exits', {})
        adjacent_rooms = set()
        for dest in exits.values():
            if isinstance(dest, dict):
                dest = dest.get('room') or dest.get('target')
            if dest:
                adjacent_rooms.add(dest)

        if player_location not in adjacent_rooms:
            return messages, consequences

        hazard['location'] = player_location
        hazard['movement_cooldown_turns'] = 1  
        self.logger.info(f"Deaths Breath '{hazard_id}' followed player to '{player_location}'")

        states_progression = ["subtle_chill", "cold_breeze", "icy_presence", "malevolent_gust"]
        current_state = hazard.get('state')
        if current_state in states_progression:
            idx = states_progression.index(current_state)
            
            # --- THE FIX: Gate the maximum state by the current level! ---
            current_level = str(self.game_logic.player.get('current_level', '1')).replace('level_', '')
            lvl = int(current_level) if current_level.isdigit() else 1
            
            max_allowed_idx = 1 # Default max: cold_breeze
            if lvl >= 2: max_allowed_idx = 2 # level 2+: icy_presence
            if lvl >= 3: max_allowed_idx = 3 # level 3+: malevolent_gust
            
            if idx < max_allowed_idx:
                next_state = states_progression[idx + 1]
                result = self.set_hazard_state(hazard_id, next_state)
                consequences.extend(result.get('consequences', []))

        # --- PREVENT PURSUIT TEXT FOR NON-MEDIUMS ---
        char_class = self.game_logic.player.get('character_class', '') if self.game_logic else ''
        if char_class == 'Medium':
            self.game_logic.add_ui_event({
                "event_type": "append_text",
                "message": color_text(
                    "The unnatural cold follows you into the room, as if drawn to your presence.",
                    "error",
                    self.resource_manager
                )
            })
        # --------------------------------------------

        return messages, consequences

    def _resolve_death_target_location(self, target_id: str) -> Optional[str]:
        """Finds the room ID of the current death target."""
        if not target_id or not self.game_logic:
            return None

        # Resolve the dynamic variable
        if target_id == "$ACTIVE_DEATH_TARGET":
            # Pull this from your game_logic state where you track the deaths_list
            target_id = self.game_logic.player.get('active_death_target') 
            if not target_id:
                return None

        # 1. Is the target the player?
        if target_id == "player":
            return self.game_logic.player.get('location')

        # 2. Is the target the active companion?
        if target_id == self.game_logic.player.get('active_companion_id'):
            return self.game_logic.player.get('companion_location')

        # 3. Search the world state for a static/wandering NPC
        for room_id, room_data in self.game_logic.current_level_rooms_world_state.items():
            for npc in room_data.get('npcs', []):
                n_id = npc.get('id', npc.get('name', '')).lower()
                if n_id == target_id.lower():
                    return room_id

        return None

    def _move_bull(self, hazard_id: str, hazard: dict, player_location: str) -> tuple:
        """
        Movement handler for stampeding_bull.
        Actively hunts the $ACTIVE_DEATH_TARGET using BFS pathfinding.
        
        Returns (messages, consequences).
        """
        messages = []
        consequences = []

        if not self.game_logic:
            return messages, consequences

        current_state = hazard.get('state')
        current_location = hazard.get('location')
        master = hazard.get('master_data', {})

        # 1. State Gate: Only move if actively hunting
        if current_state != 'hunting_target':
            return messages, consequences

        move_cost = int(master.get('movement_speed_turns', 1))

        # 2. Find the Prey
        target_id = master.get('target_id', '$ACTIVE_DEATH_TARGET')
        target_room = self._resolve_death_target_location(target_id)

        if not target_room:
            # Target is dead, missing, or not spawned yet. Bull holds position.
            return messages, consequences

        # 3. Check if we already caught them
        if current_location == target_room:
            terminal_state = hazard.get('states', {}).get(current_state, {}).get('on_reach_destination_state', 'target_acquired')
            result = self.set_hazard_state(hazard_id, terminal_state)
            messages.extend(result.get('messages', []))
            consequences.extend(result.get('consequences', []))
            return messages, consequences

        # 4. Pathfind to target using existing BFS
        path = self._find_path(current_location, target_room)
        if not path:
            # No valid path (door locked). 
            # Advanced logic: have the bull attack the locked door! For now, it waits.
            return messages, consequences

        next_room = path[0]

        # 5. Execute the physical move
        if self._execute_hazard_move(hazard_id, hazard, next_room, move_cost, player_location):
            
            # --- Player Perception & Flavor ---
            
            if next_room == player_location:
                # It just crashed into the player's room!
                messages.append("[color=ff0000][b][i]With a splintering crash, the Stampeding Bull bursts into the room![/i][/b][/color]")
                consequences.append({"type": "play_sfx", "sfx_key": "bull_crash_in"})
                
                # If the player ISN'T the target, it just causes collateral damage
                if target_room != player_location:
                    messages.append("It doesn't seem focused on you, but it's tearing the place apart!")
                    consequences.append({
                        "type": "apply_room_effect",
                        "room_id": player_location,
                        "effect_data": {"effect_type": "break_furniture"}
                    })
            
            elif current_location == player_location:
                # It just left the player's room
                messages.append("[color=ffaa00][i]The bull charges out, leaving a trail of destruction in its wake.[/i][/color]")
            
            else:
                # It's moving off-screen. If it's close (e.g., 1 or 2 rooms away), play a sound.
                import random
                if len(path) <= 2 and random.random() < 0.4:
                    messages.append("[color=ffaa00][i]You hear heavy hooves and panicked screaming nearby...[/i][/color]")

            # 6. Immediate Capture Check
            # If the room we just stepped into IS the target room, trigger the end state immediately.
            if next_room == target_room:
                terminal_state = hazard.get('states', {}).get(current_state, {}).get('on_reach_destination_state', 'target_acquired')
                result = self.set_hazard_state(hazard_id, terminal_state)
                messages.extend(result.get('messages', []))
                consequences.extend(result.get('consequences', []))

        return messages, consequences

    def _move_robo_vacuum(self, hazard_id: str, hazard: dict, player_location: str) -> tuple:
        """
        Movement handler for robo_vacuum.

        State-gated behaviour:
        ┌─────────────────┬──────────────────────────────────────────────────────┐
        │ Hazard state    │ Movement behaviour                                   │
        ├─────────────────┼──────────────────────────────────────────────────────┤
        │ patrolling      │ Wander one room per turn along random valid exits.   │
        │                 │ Seeks water_puddle or gas_leak if found in scan      │
        │                 │ radius (set by seek_radius in master_data, default 3)│
        ├─────────────────┼──────────────────────────────────────────────────────┤
        │ sparking        │ Pathfinds directly to the nearest gas_leak.          │
        │                 │ If no gas_leak exists, continues patrolling.         │
        │                 │ Waits in the gas_leak room until player is present   │
        │                 │ before the hazard_interaction fires (handled by      │
        │                 │ _process_hazard_interactions — the vacuum just needs │
        │                 │ to be co-located with the leak at turn end).         │
        ├─────────────────┼──────────────────────────────────────────────────────┤
        │ all other       │ No movement — the hazard is resolving or neutralised.│
        └─────────────────┴──────────────────────────────────────────────────────┘

        Cooldown:  master_data.movement_speed_turns (default 1) sets turns between moves.
        Proximity: The vacuum never enters a room it just came from (anti-oscillation).

        Returns (messages, consequences).
        """
        messages = []
        consequences = []

        if not self.game_logic:
            return messages, consequences

        current_state = hazard.get('state')
        current_location = hazard.get('location')
        master = hazard.get('master_data', {})
        memory = hazard.setdefault('memory', {})

        # Apply movement cooldown from master data each time we successfully move
        move_cost = int(master.get('movement_speed_turns', 1))

        # ── SPARKING: hunt the nearest gas leak ─────────────────────────────
        if current_state == 'sparking':
            behavior = 'seeking'
            hazard['behavior_state'] = behavior

            # Refresh path if we don't have one or our stored target has resolved
            target_id = hazard.get('seek_target_hazard_id')
            target_room = hazard.get('seek_target_room')
            target_still_valid = (
                target_id and
                target_id in self.active_hazards and
                self.active_hazards[target_id].get('state') not in
                    ('resolved', 'neutralized', 'dormant', 'inactive')
            )

            if not target_still_valid:
                # Scan all rooms for the nearest gas_leak
                nearest_id, nearest_room = self._find_nearest_hazard_type(
                    current_location, 'gas_leak'
                )
                if nearest_id:
                    hazard['seek_target_hazard_id'] = nearest_id
                    hazard['seek_target_room'] = nearest_room
                    hazard['path_to_target'] = self._find_path(current_location, nearest_room)
                    self.logger.info(
                        f"robo_vacuum '{hazard_id}' locked onto gas_leak "
                        f"'{nearest_id}' in '{nearest_room}'. "
                        f"Path: {hazard['path_to_target']}"
                    )
                else:
                    # No gas leak in level — fall through to patrol
                    hazard['behavior_state'] = 'patrolling'
                    hazard['seek_target_hazard_id'] = None
                    hazard['seek_target_room'] = None
                    hazard['path_to_target'] = []

            if hazard.get('behavior_state') == 'seeking' and hazard.get('path_to_target'):
                # Already at destination?
                if current_location == hazard.get('seek_target_room'):
                    # We are co-located with the gas leak.
                    # _process_hazard_interactions will fire the interaction on the
                    # NEXT process_turn() call when player is present.
                    # The vacuum waits here — no further movement needed.
                    hazard['behavior_state'] = 'waiting'
                    self.logger.info(
                        f"robo_vacuum '{hazard_id}' reached gas_leak room "
                        f"'{current_location}'. Waiting for player."
                    )
                    return messages, consequences

                # Step one room along the pre-computed path
                next_room = hazard['path_to_target'][0]
                if self._execute_hazard_move(hazard_id, hazard, next_room, move_cost, player_location):
                    hazard['path_to_target'].pop(0)
                    msgs = self._movement_flavor_message(hazard_id, hazard, next_room, player_location)
                    messages.extend(msgs)
                return messages, consequences

        # ── PATROLLING: random walk, opportunistically seek puddles/leaks ───
        if current_state in ('patrolling', 'idle', 'active'):
            hazard['behavior_state'] = 'patrolling'

            seek_radius = int(master.get('seek_radius', 3))
            scan_types = master.get('seek_hazard_types', ['water_puddle', 'gas_leak'])

            # Opportunistic seek — if a target is within seek_radius, head for it
            for seek_type in scan_types:
                nearest_id, nearest_room = self._find_nearest_hazard_type(
                    current_location, seek_type, max_distance=seek_radius
                )
                if nearest_id and nearest_room != current_location:
                    path = self._find_path(current_location, nearest_room)
                    if path:
                        next_room = path[0]
                        if self._execute_hazard_move(
                            hazard_id, hazard, next_room, move_cost, player_location
                        ):
                            hazard['path_to_target'] = path[1:]
                            msgs = self._movement_flavor_message(
                                hazard_id, hazard, next_room, player_location
                            )
                            messages.extend(msgs)
                        return messages, consequences

            # No interesting target nearby — random wander
            rooms = self.game_logic.current_level_rooms_world_state
            room_data = rooms.get(current_location, {})
            exits = room_data.get('exits', {})
            valid_exits = []
            last_room = memory.get('last_room')
            for dest in exits.values():
                if isinstance(dest, dict):
                    dest = dest.get('room') or dest.get('target')
                if dest and isinstance(dest, str) and dest != last_room:
                    valid_exits.append(dest)
            # If all exits lead back (dead-end), allow the backtrack
            if not valid_exits:
                valid_exits = [v for v in exits.values() if v and isinstance(v, str)]

            if valid_exits:
                next_room = random.choice(valid_exits)
                if self._execute_hazard_move(
                    hazard_id, hazard, next_room, move_cost, player_location
                ):
                    msgs = self._movement_flavor_message(
                        hazard_id, hazard, next_room, player_location
                    )
                    messages.extend(msgs)

        return messages, consequences

    def _move_stray_cat(self, hazard_id: str, hazard: dict, player_location: str) -> tuple:
        '''
        Random wander for the stray cat.  Every N turns it moves to
        a random adjacent room.  If startled, it moves immediately.
        '''
        messages, consequences = [], []
        current_state = hazard.get('state', 'lurking')
        location = hazard.get('location')
 
        # Only move while lurking (fled = terminal, startled = instant)
        if current_state != 'lurking':
            return messages, consequences
 
        speed = hazard.get('master_data', {}).get('movement_speed_turns', 3)
        turns = hazard.get('turns_in_state', 0)
 
        if turns % speed != 0 or turns == 0:
            return messages, consequences
 
        # Pick a random adjacent room
        if not self.game_logic:
            return messages, consequences
 
        room_data = self.game_logic.get_room_data(location)
        if not room_data:
            return messages, consequences
 
        exits = room_data.get('exits', {})
        if not exits:
            return messages, consequences
 
        new_room = random.choice(list(exits.values()))
        if isinstance(new_room, dict):
            new_room = new_room.get('target', new_room.get('room'))
        if not new_room or not isinstance(new_room, str):
            return messages, consequences
 
        hazard['location'] = new_room
 
        # If it arrives in the player's room, describe it
        if new_room == player_location:
            messages.append(
                color_text(
                    "A stray cat slinks into the room, eyes wide and wary.",
                    "warning", self.resource_manager
                )
            )
 
        return messages, consequences

    def _move_pigeon(self, hazard_id: str, hazard: dict, player_location: str) -> tuple:
        '''
        Random wander for pigeons. They fly between rooms quickly, 
        often triggering vertical/falling hazards when they land.
        '''
        messages, consequences = [], []
        current_state = hazard.get('state', 'roosting')
        location = hazard.get('location')
 
        if current_state != 'roosting':
            return messages, consequences
 
        speed = hazard.get('master_data', {}).get('movement_speed_turns', 2)
        turns = hazard.get('turns_in_state', 0)
 
        if turns % speed != 0 or turns == 0:
            return messages, consequences
 
        if not self.game_logic: return messages, consequences
 
        room_data = self.game_logic.get_room_data(location) or {}
        exits = room_data.get('exits', {})
        if not exits: return messages, consequences
 
        new_room = random.choice(list(exits.values()))
        if isinstance(new_room, dict):
            new_room = new_room.get('target', new_room.get('room'))
        if not new_room or not isinstance(new_room, str):
            return messages, consequences
 
        hazard['location'] = new_room
 
        if new_room == player_location:
            messages.append(color_text("A pigeon flutters into the rafters overhead, cooing loudly.", "flavor", self.resource_manager))
 
        return messages, consequences

    # ── Mobile hazard helpers ────────────────────────────────────────────────

    def _find_nearest_hazard_type(
        self, from_room: str, hazard_type: str, max_distance: int = 999
    ) -> tuple:
        """
        BFS across the room graph to find the closest active hazard of a given type.

        Only considers hazards that are not resolved/neutralized/dormant/inactive.

        Returns:
            (hazard_id: str, room: str) of the nearest match, or (None, None).
        """
        if not self.game_logic:
            return None, None

        rooms = self.game_logic.current_level_rooms_world_state
        if not rooms:
            return None, None

        # Build room -> [hazard_ids] index for fast lookup during BFS
        hazards_by_room = {}
        inactive = {'resolved', 'neutralized', 'dormant', 'inactive'}
        for hid, h in list(self.active_hazards.items()):
            if h.get('type') == hazard_type and h.get('state') not in inactive:
                room = h.get('location')
                hazards_by_room.setdefault(room, []).append(hid)

        if not hazards_by_room:
            return None, None

        # BFS from from_room, stop at first room that contains the target type
        from collections import deque
        visited = {from_room}
        queue = deque([(from_room, 0)])

        while queue:
            current, dist = queue.popleft()

            if dist > max_distance:
                break

            if current in hazards_by_room:
                # Return the first (or least-recently-added) match
                return hazards_by_room[current][0], current

            room_data = rooms.get(current, {})
            for dest in room_data.get('exits', {}).values():
                if isinstance(dest, dict):
                    dest = dest.get('room') or dest.get('target')
                if dest and isinstance(dest, str) and dest not in visited:
                    visited.add(dest)
                    queue.append((dest, dist + 1))

        return None, None

    def _execute_hazard_move(
        self,
        hazard_id: str,
        hazard: dict,
        next_room: str,
        move_cost_turns: int,
        player_location: str
    ) -> bool:
        """
        Physically moves a hazard to next_room and handles deferred ambush
        registration if the player is not present.

        Sets movement_cooldown_turns so the hazard pauses for move_cost_turns
        before moving again.

        Returns True if move executed, False if blocked (room not found).
        """
        if not self.game_logic:
            return False

        rooms = self.game_logic.current_level_rooms_world_state
        if next_room not in rooms:
            self.logger.warning(
                f"_execute_hazard_move: Target room '{next_room}' not found in world state."
            )
            return False

        hazard['memory']['last_room'] = hazard['location']
        hazard['location'] = next_room
        hazard['movement_cooldown_turns'] = move_cost_turns

        self.logger.debug(
            f"Hazard '{hazard_id}' ({hazard.get('type')}) moved to '{next_room}'"
        )

        # If the hazard moved into the player's current room, check for deferred
        # ambushes that were waiting for this exact co-location.
        if next_room == player_location:
            ambush_cons = self.trigger_ambushes_for_room(next_room)
            # Consequences are returned to _tick_mobile_hazards via the caller
            # (the caller checks this return value; we rely on
            # _process_hazard_interactions to handle the chain naturally
            # since the hazard is now co-located.)

        return True

    def _movement_flavor_message(
        self,
        hazard_id: str,
        hazard: dict,
        new_room: str,
        player_location: str
    ) -> list:
        messages = []
        hazard_type = hazard.get('type')
        
        # --- PREVENT AMBIENT TEXT FOR NON-MEDIUMS ---
        if hazard_type == 'deaths_breath':
            char_class = self.game_logic.player.get('character_class', '') if self.game_logic else ''
            if char_class != 'Medium':
                return []
        # --------------------------------------------
        
        current_location = hazard.get('location')   

        if current_location != player_location:
            if self.game_logic:
                room_data = self.game_logic.get_room_data(player_location) or {}
                adjacent = set(room_data.get('exits', {}).values())
                if current_location not in adjacent:
                    return messages

        flavor_map = {
            'robo_vacuum': {
                'patrolling': "You hear a distant whirring — a vacuum cleaner making its rounds.",
                'seeking':    "The whirring of the vacuum grows louder, purposeful, closer.",
                'waiting':    "The vacuum hums quietly nearby. Waiting.",
            },
            'deaths_breath': {
                'subtle_chill':   "A cold draft passes under the door.",
                'cold_breeze':    "The temperature drops noticeably.",
                'icy_presence':   "An icy chill seeps through the walls.",
                'malevolent_gust':"The air turns hostile.",
            },
        }

        behavior = hazard.get('behavior_state', hazard.get('state', ''))
        type_flavors = flavor_map.get(hazard_type, {})
        msg = type_flavors.get(behavior) or type_flavors.get(hazard.get('state', ''))

        if msg:
            messages.append(color_text(msg, 'warning', self.resource_manager))

        return messages

    # ── Legacy shim — keeps any external callers of the old method working ──

    def _handle_deaths_breath_movement(self, player_location: str):
        """
        Legacy shim. Deaths Breath movement is now handled by _tick_mobile_hazards
        via the registered _move_deaths_breath handler.

        Kept to avoid AttributeError if any external code still calls this directly.
        Produces no output — the real work already happened in _tick_mobile_hazards.
        """
        pass  # Superseded by _move_deaths_breath + _tick_mobile_hazards

    def _process_hazard_interactions(self, hazard_id: str, hazard: dict, room_hazards: list = None) -> tuple[list, list]:
        """
        Check if this hazard can interact with other hazards in the same room.
        Returns (messages, consequences).
        """
        hazard_type = hazard.get('type')
        current_state = hazard.get('state')
        location = hazard.get('location')
        
        hazard_def = self.hazards_master_data.get(hazard_type, {})
        state_def = hazard_def.get('states', {}).get(current_state, {})
        interactions = state_def.get('hazard_interaction', {})
        
        messages = []
        consequences = []  # <--- NEW LIST
        
        if not interactions:
            return messages, consequences

        if room_hazards is None:
            # Fallback for unoptimized calls
            room_hazards = [
                (hid, h) for hid, h in self.active_hazards.items()
                if h.get('location') == location and hid != hazard_id
            ]
        else:
             # Filter out self from passed list
             room_hazards = [
                 (hid, h) for hid, h in room_hazards 
                 if hid != hazard_id
             ]
        
        for target_hazard_id, target_hazard in room_hazards:
            target_type = target_hazard.get('type')
            
            if target_type in interactions:
                interaction = interactions[target_type]
            elif '*' in interactions:
                interaction = interactions['*']
            else:
                continue
            
            chance = float(interaction.get('chance', 0))
            if random.random() > chance:
                continue
            
            effect = interaction.get('effect')
            msg = interaction.get('message', '')
            
            try:
                if effect == 'instant_progression' or 'target_state' in interaction:
                    # Determine target state
                    next_state = None
                    if 'target_state' in interaction:
                        next_state = interaction['target_state']
                    elif effect == 'instant_progression':
                        target_state_key = target_hazard.get('state')
                        target_state_def = self._resolve_state_def(target_hazard, target_state_key)
                        next_state = target_state_def.get('next_state')

                    # Execute transition and CAPTURE CONSEQUENCES
                    if next_state:
                        result = self.set_hazard_state(target_hazard_id, next_state)
                        consequences.extend(result.get("consequences", [])) # <--- CAPTURE
                        if msg:
                            messages.append(color_text(msg, "error", self.resource_manager))

                elif effect == 'accelerate_progression':
                    # (Logic unchanged, no consequences to capture)
                    target_state_key = target_hazard.get('state')
                    target_state_def = self._resolve_state_def(target_hazard, target_state_key)
                    if 'chance_to_progress' in target_state_def:
                        target_state_def['chance_to_progress'] = min(1.0, target_state_def['chance_to_progress'] * 2)
                    if msg:
                        messages.append(color_text(msg, "warning", self.resource_manager))
                
                elif effect == 'reduce_qte_duration':
                    # (Logic unchanged)
                    if self.game_logic:
                        modifier = float(interaction.get('amount', 0.8))
                        self.game_logic.player['qte_duration_modifier'] = modifier
                    if msg:
                        messages.append(color_text(msg, "warning", self.resource_manager))
                
                self.logger.info(f"Hazard interaction: {hazard_type} ({current_state}) -> {target_type}")
            
            except Exception as e:
                self.logger.error(f"_process_hazard_interactions: Failed to apply effect: {e}", exc_info=True)
        
        return messages, consequences
    
    def reset(self):
        """
        Tabula Rasa.
        Wipes all active hazards and ambushes to ensure a clean slate for a new game.
        """
        self.active_hazards.clear()
        
        # Defensive coding: If attribute is missing, create it; otherwise clear it.
        if hasattr(self, 'deferred_ambushes'):
            self.deferred_ambushes.clear()
        else:
            self.deferred_ambushes = {}
            
        self.logger.info("HazardEngine has been reset. All active threats cleared.")

    def reset_elevator_hazard(self, room_name: str):
        """
        Resets the elevator_freefall hazard in the given room to its initial state.
        If the hazard is missing (e.g. due to bug or cleanup), it respawns it to ensure safety.
        """
        found = False
        for hid, hazard in list(self.active_hazards.items()):
            if hazard.get('type') == 'elevator_freefall' and hazard.get('location') == room_name:
                # Force state to idle
                self.set_hazard_state(hid, 'idle', suppress_entry_effects=True)
                self.logger.info(f"reset_elevator_hazard: Reset hazard '{hid}' to 'idle'.")
                found = True
                break # Assuming one elevator hazard per room
        
        if not found:
            self.logger.warning(f"reset_elevator_hazard: Hazard missing in '{room_name}'. Respawning for safety.")
            self._add_active_hazard('elevator_freefall', room_name, initial_state_override='idle', source_trigger_id="elevator_reset_failsafe")
            return True
            
        return found

    def _check_npc_endangerment(self, hazard_id: str, hazard: dict, room_name: str) -> bool:
        """
        Checks if a hazard state threatens an NPC and triggers a rescue QTE if the player is present to witness it.
        Returns True if an intervention QTE was triggered.
        """
        player_loc = self.game_logic.player.get('location')
        
        # The player must be in the room to witness and intervene!
        if room_name != player_loc:
            return False 

        state_key = hazard.get('current_state')
        hazard_data = self.hazards_master_data.get(hazard.get('type'), {}).get(state_key, {})
        
        intervention = hazard_data.get('npc_intervention')
        if not intervention:
            return False

        # Check for living companions in the room
        room_data = self.game_logic.get_room_data(room_name)
        npcs_in_room = room_data.get('npcs', [])
        roster = self.game_logic.player.get('npc_status', {})
        
        alive_companions = []
        for npc in npcs_in_room:
            name = npc.get('name', npc) if isinstance(npc, dict) else npc
            if roster.get(name.lower()) in ('alive', 'injured'):
                alive_companions.append(name)

        if not alive_companions:
            return False # No one to save!

        # Pick a victim
        target_npc = random.choice(alive_companions)
        
        # Build the dynamic QTE Event
        qte_ctx = copy.deepcopy(intervention.get('qte_context', {}))
        
        # Format the strings to include the NPC's actual name!
        qte_ctx['ui_prompt_message'] = qte_ctx.get('ui_prompt_message', '').replace('{npc_name}', target_npc)
        qte_ctx['success_message'] = qte_ctx.get('success_message', '').replace('{npc_name}', target_npc)
        qte_ctx['failure_message_timeout'] = qte_ctx.get('failure_message_timeout', '').replace('{npc_name}', target_npc)
        
        # Pass the victim's name so the engine knows who to kill on failure
        qte_ctx['target_npc'] = target_npc

        # Fire the QTE to the UI
        qte_event = {
            "type": "show_qte",
            "qte_type": intervention.get("qte_type", "input"),
            "duration": intervention.get("duration", 5.0),
            "qte_context": qte_ctx,
            "source_hazard_id": hazard_id
        }
        self.game_logic.ui_events.append(qte_event)
        self.logger.info(f"HazardEngine: {target_npc} is in danger from {hazard_id}! Triggering rescue QTE.")
        
        return True

    def load_save_state(self, state):
        if not state: return
        self.active_hazards = state.get("active_hazards", {})
        self.deferred_ambushes = state.get("deferred_ambushes", {})

    def get_next_npc_target(self) -> tuple[str | None, str | None]:
        """
        Returns (npc_name, npc_room) for whoever is next on Death's List,
        skipping already-skipped and dead characters.
        Returns (None, None) if no live NPC targets remain.
        """
        if not self.game_logic:
            return None, None
 
        player = self.game_logic.player
        deaths_list: list = player.get("deaths_list", [])
        skipped: list     = player.get("death_design_skipped", [])
        npc_status: dict  = player.get("npc_status", {})
 
        for name in deaths_list:
            if name == "player":
                continue
            if name in skipped:
                continue
            status = npc_status.get(name.lower(), "alive")
            if status in ("dead", "missing"):
                continue
            # Found the next live target — locate their room
            room = self._find_npc_room(name)
            return name, room
 
        return None, None
 
    def _find_npc_room(self, npc_name: str) -> str | None:
        """Walk all live rooms and return the room containing this NPC."""
        if not self.game_logic:
            return None
        rooms = self.game_logic.current_level_rooms_world_state or {}
        norm = npc_name.lower()
        for room_id, room_data in rooms.items():
            for npc in room_data.get("npcs", []):
                n = npc.get("name", "") if isinstance(npc, dict) else npc
                if isinstance(n, str) and n.lower() == norm:
                    return room_id
        return None
 
    def tick_npc_targeting(self) -> list:
        """
        Called once per turn from process_turn().
        For every active hazard that has an "npc_targeting" block and is in
        an active (non-dormant) state, check whether its current target NPC
        is reachable (same room).  If so, fire the kill/intervention sequence.
 
        Returns a list of consequence dicts for GameLogic to process.
        """
        consequences = []
        if not self.game_logic:
            return consequences
 
        player      = self.game_logic.player
        player_room = player.get("location", "")
 
        for hid, hazard in list(self.active_hazards.items()):
            if hazard.get("state") in ("dormant", "inactive", "resolved", "neutralized"):
                continue
 
            master = hazard.get("master_data", {})
            npc_tgt_cfg: dict = master.get("npc_targeting", {})
            if not npc_tgt_cfg:
                continue
 
            # Throttle: only re-evaluate every N turns
            interval = int(npc_tgt_cfg.get("retarget_interval", 1))
            hazard["_npc_target_tick"] = hazard.get("_npc_target_tick", 0) + 1
            if hazard["_npc_target_tick"] % interval != 0:
                continue
 
            npc_name, npc_room = self.get_next_npc_target()
 
            if not npc_name:
                # Death's list is exhausted — pivot to the player
                self.logger.info(
                    f"[NPC Targeting] Hazard '{hid}': list exhausted, player is next.")
                continue   # player-targeting is handled by normal hazard flow
 
            hazard_room = hazard.get("location", "")
 
            # ── CASE 1: Hazard is in the same room as the NPC ───────────────
            if hazard_room == npc_room:
                self.logger.info(
                    f"[NPC Targeting] Hazard '{hid}' is hunting {npc_name} in {npc_room}.")
 
                acquired_msg = npc_tgt_cfg.get(
                    "target_acquired_message",
                    f"Something feels wrong about {npc_name}..."
                ).replace("{npc_name}", npc_name)
 
                # Is the player also in the room?  Offer intervention.
                if player_room == npc_room:
                    intervention_cfg = npc_tgt_cfg.get("intervention_qte")
                    if intervention_cfg:
                        consequences.extend(
                            self._build_npc_intervention_sequence(
                                hid, npc_name, intervention_cfg, acquired_msg
                            )
                        )
                    else:
                        # No QTE defined — NPC dies automatically
                        consequences.append({
                            "type": "show_popup",
                            "title": "Death's Design",
                            "message": acquired_msg,
                        })
                        consequences.extend(
                            self._kill_npc_consequence(hid, npc_name, npc_tgt_cfg)
                        )
                else:
                    # Player is NOT present — NPC dies off-screen
                    self.logger.info(
                        f"[NPC Targeting] {npc_name} killed off-screen by '{hid}'.")
                    consequences.extend(
                        self._kill_npc_consequence(hid, npc_name, npc_tgt_cfg)
                    )
 
            # ── CASE 2: Hazard is in the player's room but NPC is elsewhere ─
            elif hazard_room == player_room and npc_room and npc_room != player_room:
                # Hazard can't reach NPC yet — normal turn, nothing extra
                pass
 
        return consequences
 
    def _build_npc_intervention_sequence(
        self,
        hazard_id: str,
        npc_name: str,
        intervention_cfg: dict,
        preamble_message: str,
    ) -> list:
        """
        Build the consequence chain that:
          1. Shows a popup describing the danger to the NPC.
          2. Starts an intervention QTE for the player.
        """
        qte_ctx = dict(intervention_cfg.get("qte_context", {}))
        # Inject dynamic values into QTE context
        for key, val in qte_ctx.items():
            if isinstance(val, str):
                qte_ctx[key] = val.replace("{npc_name}", npc_name)
 
        qte_ctx["qte_source_hazard_id"] = hazard_id
        qte_ctx["target_npc"]           = npc_name
        qte_ctx["npc_fatal_on_failure"] = True   # NPC dies if player fails / ignores
        # Preserve the on_npc_saved_popup for _handle_qte_resolution
        qte_ctx["on_npc_saved_popup"] = (
            intervention_cfg.get("on_npc_saved_popup", "You saved {npc_name}!")
            .replace("{npc_name}", npc_name)
        )
 
        return [
            {
                "type": "show_popup",
                "title": "⚠ Death's Design",
                "message": preamble_message,
                "output_panel": True,
            },
            {
                "type": "start_qte",
                "hazard_id": hazard_id,
                "qte_type": intervention_cfg.get("qte_type", "button_mash"),
                "qte_context": qte_ctx,
            },
        ]
 
    def _kill_npc_consequence(
        self,
        hazard_id: str,
        npc_name: str,
        npc_tgt_cfg: dict,
    ) -> list:
        """
        Returns the consequence list that marks an NPC as dead and advances
        Death's List.  Called when intervention is impossible or failed.
        """
        popup_text = (
            npc_tgt_cfg.get(
                "on_npc_killed_popup",
                "{npc_name} couldn't escape. Death collects."
            ).replace("{npc_name}", npc_name)
        )
        return [
            {
                "type": "npc_killed_by_hazard",
                "hazard_id": hazard_id,
                "npc_name": npc_name,
                "popup_text": popup_text,
            }
        ]
 
    def try_npc_sacrifice(
        self,
        npc_name: str,
        hazard_id: str,
    ) -> list:
        """
        Builds a sacrifice QTE consequence list when an NPC jumps in front of
        a hazard targeting the player.  Called by DeathAI or special hazard states.
        """
        master = self.active_hazards.get(hazard_id, {}).get("master_data", {})
        sacrifice_cfg: dict = master.get("npc_targeting", {}).get("npc_sacrifice_qte", {})
        if not sacrifice_cfg:
            return []
 
        qte_ctx = dict(sacrifice_cfg.get("qte_context", {}))
        for key, val in qte_ctx.items():
            if isinstance(val, str):
                qte_ctx[key] = val.replace("{npc_name}", npc_name)
 
        qte_ctx["qte_source_hazard_id"]  = hazard_id
        qte_ctx["target_npc"]            = npc_name
        qte_ctx["npc_sacrifice_mode"]    = True
        qte_ctx["player_splash_damage"]  = int(
            master.get("npc_targeting", {}).get("player_splash_damage", 10)
        )
 
        preamble = (
            f"[color=ff6600]{npc_name} shoves you aside — taking the hit meant for you![/color]"
        )
 
        return [
            {
                "type": "show_popup",
                "title": "Sacrifice",
                "message": preamble,
                "output_panel": True,
            },
            {
                "type": "start_qte",
                "hazard_id": hazard_id,
                "qte_type": sacrifice_cfg.get("qte_type", "button_mash"),
                "qte_context": qte_ctx,
            },
        ]

    def _check_compound_synergies(self, room_name: str):
        """
        If Entropy is high, hazards with synergistic tags will instantly accelerate each other,
        creating un-dodgeable, multi-stage room traps!
        """
        # 1. Check if the AI Director is angry enough to allow Compound Traps
        entropy = getattr(self.game_logic, 'entropy', 0.0)
        if entropy < 6.0: 
            return # AI Director is playing nice for now
            
        active_hazards = self.get_active_hazards_for_room(room_name)
        if len(active_hazards) < 2:
            return # Need at least two hazards to combo
            
        # Synergy Map (Can be loaded from your JSON later)
        synergies = {
            "water": ["electrical", "slip_and_fall"],
            "electrical": ["water", "flammable_gas"],
            "fire": ["flammable_gas", "flammable_liquid"]
        }

        # 2. Compare every hazard in the room against every other hazard
        for h1_id in active_hazards:
            h1 = self.active_hazards[h1_id]
            h1_tags = h1.get('tags', [])
            
            for h2_id in active_hazards:
                if h1_id == h2_id: continue
                h2 = self.active_hazards[h2_id]
                h2_tags = h2.get('tags', [])
                
                # 3. Check for a match
                for tag1 in h1_tags:
                    if tag1 in synergies:
                        for target_tag in synergies[tag1]:
                            if target_tag in h2_tags:
                                # SYNERGY FOUND! (e.g. Water hit Electrical)
                                
                                # Prevent infinite loops by flagging that they synergized
                                if h1.get('_synergized_with') == h2_id: continue
                                h1['_synergized_with'] = h2_id
                                
                                # Instantly force Hazard 2 into its next state!
                                current_state_def = h2.get('states', {}).get(h2['current_state'], {})
                                next_state = current_state_def.get('next_state')
                                
                                if next_state:
                                    msg = f"[color=ff4444][COMPOUND TRAP][/color] The {h1.get('name', 'hazard')} directly interacts with the {h2.get('name', 'hazard')}, creating a deadly chain reaction!"
                                    
                                    if self.game_logic:
                                        self.game_logic.add_ui_event({"event_type": "show_popup", "title": "Chain Reaction!", "message": msg})
                                        
                                    self.logger.warning(f"SYNERGY TRIGGERED! {h1_id} accelerated {h2_id} to {next_state}!")
                                    self.set_hazard_state(h2_id, next_state)
                                    return # Only trigger one synergy per tick to avoid crashing

# ── Register mobile hazard handlers ─────────────────────────────────────────
# This runs once at module load time.  The dict is a class attribute so every
# HazardEngine instance shares the same dispatch table.
#
# To add a new mobile hazard type:
#   1. Write  def _move_my_hazard(self, hazard_id, hazard, player_location) -> tuple
#   2. Add one line:  HazardEngine.MOBILE_HAZARD_HANDLERS['my_hazard'] = HazardEngine._move_my_hazard
#
HazardEngine.MOBILE_HAZARD_HANDLERS = {
    'deaths_breath': HazardEngine._move_deaths_breath,
    'robo_vacuum':   HazardEngine._move_robo_vacuum,
    'stray_cat':     HazardEngine._move_stray_cat,
    'pigeon':        HazardEngine._move_pigeon,
    'stampeding_bull': HazardEngine._move_bull
}