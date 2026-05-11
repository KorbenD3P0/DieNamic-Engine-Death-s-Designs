"""
Procedural Level Generator - Phase 1
Generates simple single-floor layouts for testing.

Phase 1 Features:
- Basic room graph generation
- Simple connection logic
- Hazard distribution
- Item placement
- Entry/Exit designation

Future phases will add:
- Multi-floor support
- Anti-softlock validation
- Difficulty scaling
"""
import random
import logging
from typing import Dict, List, Set, Tuple, Optional

logger = logging.getLogger("ProceduralGenerator")

def resolve_sandbox_dependencies(selected_hazards: List[str], hazards_master: dict) -> Tuple[List[str], List[str], List[str]]:
    """
    Recursively crawls hazards.json to find chained hazards, related items, and required furniture.
    """
    from fd_terminal.utils import get_related_items
    
    final_hazards = set(selected_hazards)
    final_items = set()
    final_furniture = set()
    
    queue = list(selected_hazards)
    processed = set()
    
    # --- FIX: Omni-Hazard Blacklist ---
    # Hazards that interact with everything will pull the entire game into the sandbox.
    # By adding them here, they won't automatically pull their interaction targets, 
    # but they will still react to them if you select them manually.
    OMNI_HAZARDS = {"deaths_breath"}
    
    def _find_chained_hazards(data, current_hazard_id: str) -> List[str]:
        """Recursively search dictionaries ({}) and lists ([])."""
        found = []
        
        if isinstance(data, dict):
            # 1. Look for explicit target strings
            if 'target_hazard_type' in data and isinstance(data['target_hazard_type'], str):
                found.append(data['target_hazard_type'])
            if 'hazard_key' in data and isinstance(data['hazard_key'], str):
                 found.append(data['hazard_key'])
                 
            # 2. FIX: Extract buried hazard_interactions
            # If a dictionary is named "hazard_interaction", its keys are the target hazards!
            if 'hazard_interaction' in data and isinstance(data['hazard_interaction'], dict):
                if current_hazard_id not in OMNI_HAZARDS:
                    found.extend(list(data['hazard_interaction'].keys()))
            
            # 3. Recursively dig deeper into nested dictionaries
            for k, v in data.items():
                found.extend(_find_chained_hazards(v, current_hazard_id))
                
        elif isinstance(data, list):
            # Recursively dig through items in a list
            for item in data:
                found.extend(_find_chained_hazards(item, current_hazard_id))
                
        return found

    while queue:
        hz_id = queue.pop(0)
        if hz_id in processed:
            continue
        processed.add(hz_id)
        
        # 1. Grab required tools/items
        for item in get_related_items(hz_id):
            final_items.add(item)
            
        hz_data = hazards_master.get(hz_id, {})
        if not hz_data:
            continue
            
        # 2. Grab required placement objects (furniture)
        generics = {"room", "window", "doorway", "floor", "wall", "vent", "ceiling"}
        for p_obj in hz_data.get("placement_object", []):
            if p_obj.lower() not in generics:
                final_furniture.add(p_obj.replace(' ', '_').lower())
                
        # 3. Grab chained hazards (Passing the current ID to check the blacklist)
        chained = _find_chained_hazards(hz_data, hz_id)
        for c in chained:
            # Ensure the found hazard actually exists in the master list before adding it
            if c and c not in final_hazards and c in hazards_master:
                final_hazards.add(c)
                queue.append(c)
                
    return list(final_hazards), list(final_items), list(final_furniture)

class RoomNode:
    """Represents a single room in the generated level."""
    
    def __init__(self, room_id: str, floor: int = 1):
        self.room_id = room_id
        self.floor = floor
        self.exits: Dict[str, str] = {}  # direction -> room_id
        self.hazards: List[str] = []
        self.items: List[str] = []
        self.furniture: List[Dict] = []
        self.locked = False
        self.is_entry = False
        self.is_exit = False
        self.description = ""
        
    def connect_to(self, other: 'RoomNode', direction: str):
        """Create bidirectional connection between rooms."""
        opposite = {
            'north': 'south', 'south': 'north',
            'east': 'west', 'west': 'east',
            'up': 'down', 'down': 'up',
            'northeast': 'southwest', 'southwest': 'northeast',
            'northwest': 'southeast', 'southeast': 'northwest'
        }
        
        self.exits[direction] = other.room_id
        if direction in opposite:
            other.exits[opposite[direction]] = self.room_id


class ProceduralGenerator:
    """
    Phase 1: Basic single-floor procedural level generator.
    """
    
    # Room name templates based on existing patterns
    ROOM_TEMPLATES = {
        "lobby": ["Entrance Hall", "Main Lobby", "Reception Area", "Foyer"],
        "corridor": ["Hallway", "Corridor", "Passage", "Connecting Hall"],
        "office": ["Office", "Study", "Workspace", "Administration Room"],
        "storage": ["Storage Room", "Supply Closet", "Storage Area", "Warehouse"],
        "hazard_room": ["Equipment Room", "Utility Room", "Maintenance Area", "Technical Room"],
        "special": ["Observation Deck", "Control Room", "Testing Chamber", "Research Lab"]
    }
    
    # Hazard categories for distribution
    HAZARD_CATEGORIES = {
        "mechanical": ["wobbling_ceiling_fan", "falling_scaffolding", "robo_vacuum"],
        "electrical": ["frayed_lamp_cord", "exposed_wiring", "electrified_fence"],
        "fire": ["gas_leak", "spilled_hot_oil", "propane_tanks"],
        "special": ["deaths_breath", "mri", "elevator_freefall", "stray_cat"]
    }
    
    def __init__(self, seed: Optional[int] = None):
        """Initialize generator with optional seed for reproducibility."""
        if seed is not None:
            random.seed(seed)
        self.rooms: Dict[str, RoomNode] = {}
        self.room_count = 0
        
    def generate_single_floor(
        self,
        room_count: int = 10,
        hazards: List[str] = None,
        items: List[str] = None,
        required_furniture: List[str] = None # <-- ADD THIS
    ) -> Dict[str, Dict]:
        """
        Generate a simple single-floor level.
        """
        logger.info(f"Generating single floor with {room_count} rooms")
        
        # Phase 1.1: Generate room graph
        self._generate_room_graph(room_count)
        
        # Phase 1.1b: Inject required furniture FIRST so locks exist
        if required_furniture:
            self._distribute_furniture(required_furniture)
        
        # Phase 1.2: Distribute hazards
        if hazards:
            self._distribute_hazards(hazards)
            
        # --- ANTI-SOFTLOCK AUDIT & INJECTION (STEP 1 & 2) ---
        items_to_spawn = set(items or [])
        
        # Audit the rooms (and the furniture we just placed) for locks
        keys_needed = self.extract_required_keys(self.rooms)
        
        # 1. AUDIT: Make sure every lock has a key in the 'to spawn' list
        self._inject_missing_keys(items_to_spawn)

        # 2. LOGISTICS: Group the keys by what they unlock
        required_locks = self._map_keys_to_targets(keys_needed)

        # 3. PLACEMENT: Use the expanding bubble to place the keys first
        self._distribute_keys_safely(required_locks, items_to_spawn)

        # 4. FILLER: Distribute all non-essential items (heals, lore, etc.) randomly
        remaining_items = [i for i in items_to_spawn if i not in keys_needed]
        self._distribute_items(remaining_items)
        
        # Phase 1.4: Generate descriptions
        self._generate_descriptions()
        
        # Phase 1.5: Convert to JSON format
        return self._to_json_format()
    
    def _generate_room_graph(self, room_count: int):
        """
        Phase 1: Simple linear + branch layout.
        Creates a main path with occasional branches.
        """
        self.rooms = {}
        self.room_count = room_count
        
        # Create entry room
        entry = self._create_room("Entry", is_entry=True)
        self.rooms[entry.room_id] = entry
        
        # Create main path
        previous_room = entry
        main_path_length = max(5, room_count // 2)
        
        for i in range(1, main_path_length):
            room_type = self._choose_room_type(i, main_path_length)
            room = self._create_room(f"{room_type}_{i}")
            
            # Connect to previous room
            direction = random.choice(['north', 'east', 'west'])
            previous_room.connect_to(room, direction)
            
            self.rooms[room.room_id] = room
            previous_room = room
        
        # Designate exit room
        previous_room.is_exit = True
        
        # Add branch rooms
        remaining = room_count - len(self.rooms)
        main_rooms = list(self.rooms.values())[1:-1]  # Exclude entry/exit
        
        for i in range(remaining):
            if not main_rooms:
                break
            # Attach to random room on main path
            parent = random.choice(main_rooms)
            
            # Find unused direction
            available_dirs = ['north', 'south', 'east', 'west']
            used_dirs = set(parent.exits.keys())
            available = [d for d in available_dirs if d not in used_dirs]
            
            if available:
                direction = random.choice(available)
                room_type = random.choice(['storage', 'office', 'hazard_room'])
                branch_room = self._create_room(f"{room_type}_{len(self.rooms)}")
                parent.connect_to(branch_room, direction)
                self.rooms[branch_room.room_id] = branch_room
        
        logger.info(f"Generated graph with {len(self.rooms)} rooms")
    
    def _create_room(self, base_name: str, is_entry: bool = False) -> RoomNode:
        """Create a new room with procedural naming."""
        # Use template-based naming
        if is_entry:
            name = random.choice(self.ROOM_TEMPLATES["lobby"])
        else:
            room_type = base_name.split('_')[0]
            if room_type in self.ROOM_TEMPLATES:
                template_names = self.ROOM_TEMPLATES[room_type]
                name = random.choice(template_names)
            else:
                name = base_name.replace('_', ' ').title()
        
        # Ensure unique name
        counter = 1
        original_name = name
        while name in self.rooms:
            name = f"{original_name} {counter}"
            counter += 1
        
        room = RoomNode(name)
        room.is_entry = is_entry
        return room
    
    def _choose_room_type(self, index: int, total: int) -> str:
        """Choose appropriate room type based on position in level."""
        # Entry/Exit use lobby
        if index == 0 or index == total - 1:
            return "lobby"
        
        # Middle sections use variety
        weights = {
            "corridor": 30,
            "office": 20,
            "storage": 15,
            "hazard_room": 25,
            "special": 10
        }
        
        return random.choices(
            list(weights.keys()),
            weights=list(weights.values())
        )[0]
    
    def _distribute_furniture(self, required_furniture: List[str]):
        """Distribute required furniture across rooms so hazards and locks can exist."""
        room_ids = list(self.rooms.keys())
        for furn_id in required_furniture:
            target_room = self.rooms[random.choice(room_ids)]
            
            furn_def = {
                "name": furn_id.replace('_', ' '),
                "description": f"A {furn_id.replace('_', ' ')}.",
                "is_container": False,
                "is_metallic": True # Safe default
            }
            target_room.furniture.append(furn_def)
        
    def extract_required_keys(self) -> set:
        """Scans the generated rooms for any locked doors or furniture."""
        required_keys = set()
        for room in self.rooms.values():
            if room.locked and hasattr(room, 'requires_key'):
                required_keys.add(room.requires_key)
            for furn in room.furniture:
                if furn.get("locked") and "requires_key" in furn:
                    required_keys.add(furn["requires_key"])
        return required_keys

    def _inject_missing_keys(self, items_to_spawn: set):
        """Ensures every lock in the level has a corresponding key in the spawn list."""
        keys_needed = self.extract_required_keys()
        for key_id in keys_needed:
            if key_id not in items_to_spawn:
                logger.warning(f"Failsafe: Injecting missing required key '{key_id}'")
                items_to_spawn.add(key_id)

    def _is_lock_bordering_zone(self, locked_targets: List[str], accessible_rooms: Set[str]) -> bool:
        """
        Determines if a locked target (room or furniture) is reachable from the current zone.
        """
        for target in locked_targets:
            # Case 1: The target is a Room
            if target in self.rooms:
                # Is there an exit from any accessible room leading into this locked room?
                for room_id in accessible_rooms:
                    if target in self.rooms[room_id].exits.values():
                        return True
            
            # Case 2: The target is Furniture (Container)
            # We check if the piece of furniture exists inside any currently accessible room.
            for room_id in accessible_rooms:
                room = self.rooms[room_id]
                for furn in room.furniture:
                    # Match by name (e.g., "large_desk")
                    if furn.get("name") == target:
                        return True
        return False

    def _expand_accessible_zone(self, newly_unlocked_id: str, accessible_rooms: Set[str], locked_rooms: Set[str]):
        """
        Adds a newly unlocked room to the accessible zone and recursively 
        finds all other rooms now reachable through it.
        """
        if newly_unlocked_id not in locked_rooms:
            return
            
        locked_rooms.remove(newly_unlocked_id)
        queue = [newly_unlocked_id]
        
        while queue:
            curr_id = queue.pop(0)
            accessible_rooms.add(curr_id)
            curr_room = self.rooms[curr_id]
            
            # Find all exits from this room
            for target_id in curr_room.exits.values():
                # If the connected room isn't in our locked list, we can walk into it!
                if target_id not in accessible_rooms and target_id not in locked_rooms:
                    queue.append(target_id)

    def _get_required_locks_map(self, keys_needed: Set[str], items_master: dict) -> dict:
        """
        Creates a map of KeyID -> [List of Targets it unlocks] 
        based on the master items database.
        """
        lock_map = {}
        for key_id in keys_needed:
            item_data = items_master.get(key_id, {})
            # Get the list of rooms/furniture this key is designed to open
            lock_map[key_id] = item_data.get("unlocks", [])
        return lock_map

    def _distribute_keys_safely(self, required_locks: dict, items_master: dict):
        """
        Placer: Uses the Expanding Bubble logic and spawn pools to place keys safely.
        required_locks: { 'key_id': ['target_room_or_furniture_id'], ... }
        """
        # 1. Start at the beginning
        entry_room = next(r for r in self.rooms.values() if r.is_entry)
        accessible_rooms = set()
        locked_rooms = {r.room_id for r in self.rooms.values() if r.locked}
        
        # 2. Initial Reachability BFS (Zone 0)
        queue = [entry_room.room_id]
        while queue:
            curr = queue.pop(0)
            accessible_rooms.add(curr)
            for target_id in self.rooms[curr].exits.values():
                if target_id not in accessible_rooms and target_id not in locked_rooms:
                    queue.append(target_id)

        # 3. Placement Loop
        placed_keys = set()
        while len(placed_keys) < len(required_locks):
            progress_made = False
            
            for key_id, locked_targets in required_locks.items():
                if key_id in placed_keys:
                    continue
                
                # Helper: checks if the lock is inside or adjacent to Zone 0
                if self._is_lock_bordering_zone(locked_targets, accessible_rooms):
                    
                    # --- THE COHESIVE RANDOMIZATION ---
                    target_room = None
                    key_data = items_master.get(key_id, {})
                    spawn_pool = key_data.get("spawn_pool", [])
                    
                    # A. Try Spawn Pool first (if an entry is in our current bubble)
                    for pool_entry in spawn_pool:
                        pref_room_name = pool_entry.get("room")
                        for acc_id in accessible_rooms:
                            if pref_room_name in acc_id: # Flexible naming match
                                target_room = self.rooms[acc_id]
                                break
                        if target_room: break

                    # B. Fallback: Any random room in the bubble
                    if not target_room:
                        target_room = self.rooms[random.choice(list(accessible_rooms))]
                    
                    # C. Execute Placement
                    target_room.items.append(key_id)
                    placed_keys.add(key_id)
                    progress_made = True
                    logger.info(f"Safe Placement: {key_id} -> {target_room.room_id}")

                    # D. Expand Bubble: The newly unlocked areas are now part of Zone 0
                    for target in locked_targets:
                        if target in locked_rooms:
                            self._expand_accessible_zone(target, accessible_rooms, locked_rooms)
            
            if not progress_made:
                logger.error(f"Softlock Error: Remaining keys {set(required_locks.keys()) - placed_keys} are unreachable!")
                break
    
    def _distribute_items(self, items: List[str]):
        """Distribute items across rooms."""
        available_rooms = [r for r in self.rooms.values()]
        
        for item in items:
            room = random.choice(available_rooms)
            room.items.append(item)
    
    def _generate_descriptions(self):
        """Generate descriptions for all rooms."""
        description_templates = {
            "generic": [
                "A {adjective} room. {detail}.",
                "This {adjective} space {state}. {detail}.",
                "The room is {adjective}. {detail}."
            ],
            "adjectives": ["cramped", "spacious", "dimly lit", "sterile", "cluttered", "abandoned"],
            "states": ["feels unsettling", "seems ordinary", "has an oppressive atmosphere"],
            "details": [
                "Dust covers most surfaces",
                "The air is stale",
                "Equipment hums in the background",
                "Something feels wrong here"
            ]
        }
        
        for room in self.rooms.values():
            if not room.description:
                template = random.choice(description_templates["generic"])
                adjective = random.choice(description_templates["adjectives"])
                detail = random.choice(description_templates["details"])
                state = random.choice(description_templates["states"])
                
                room.description = template.format(
                    adjective=adjective,
                    detail=detail,
                    state=state
                )
    
    def _to_json_format(self) -> Dict[str, Dict]:
        """Convert room graph to rooms_level_X.json format with validation."""
        result = {}
        entry_room_found = False
        exit_room_found = False
        
        for room in self.rooms.values():
            room_data = {
                "description": room.description,
                "floor": room.floor,
                "exits": room.exits,
                "hazards_present": room.hazards,
                "items": room.items,
                "locked": room.locked,
                "objects": []  # Simple for Phase 1
            }
            
            if room.is_entry:
                room_data["entry_room"] = True
                entry_room_found = True
                logger.debug(f"Entry room designated: {room.room_id}")
            
            if room.is_exit:
                # Mark exit rooms visually in description
                if "exit" not in room.description.lower():
                    room_data["description"] = room.description + " A clearly marked exit beckons."
                exit_room_found = True
                logger.debug(f"Exit room designated: {room.room_id}")
            
            if room.furniture:
                room_data["furniture"] = room.furniture
            
            result[room.room_id] = room_data
        
        # Validation
        if not entry_room_found:
            logger.error("No entry room designated in procedural level!")
        if not exit_room_found:
            logger.warning("No exit room designated in procedural level")
        
        # Validate all exits point to existing rooms
        all_room_ids = set(result.keys())
        for room_id, room_data in result.items():
            for exit_dir, target_room in room_data["exits"].items():
                if target_room not in all_room_ids:
                    logger.error(f"Room '{room_id}' has exit '{exit_dir}' to non-existent room '{target_room}'")
        
        logger.info(f"Generated {len(result)} rooms with entry={entry_room_found}, exit={exit_room_found}")
        return result


    def generate_procedural_level(
        resource_manager,
        room_count: int = 10,
        hazards: List[str] = None,
        items: List[str] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Dict]:
        """
        Convenience function for generating a procedural level.
        """
        hazards_master = resource_manager.get_data('hazards', {})
        
        # --- DEPENDENCY INJECTION ---
        full_hazards, full_items, required_furniture = resolve_sandbox_dependencies(
            hazards or [], 
            hazards_master
        )
        
        combined_items = list(set((items or []) + full_items))
        
        logger.info(f"Sandbox Dependency Resolver loaded {len(full_hazards)} hazards, {len(combined_items)} items, and {len(required_furniture)} required furniture pieces.")

        generator = ProceduralGenerator(seed=seed)
        
        # Pass EVERYTHING in so the generator can audit it all together
        level_data = generator.generate_single_floor(
            room_count=room_count,
            hazards=full_hazards,
            items=combined_items,
            required_furniture=required_furniture
        )
                
        return level_data