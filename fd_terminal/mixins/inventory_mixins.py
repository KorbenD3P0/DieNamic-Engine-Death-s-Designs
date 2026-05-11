from fd_terminal.utils import normalize_text, color_text
import re

class InventoryMixin:
    # --- The Rite of Observation ---
    def _command_examine(self, target: str) -> dict:
        target = (target or "").strip()
        
        if not target:
            # --- THE FIX: Pass the empty target string into the method! ---
            return self._examine_main(target)
            
        # --- Intercept Interactive State Machines ---
        npcs_master = self.resource_manager.get_data('npcs', {})
        npc_data = npcs_master.get(target) or npcs_master.get('npcs', {}).get(target, {})
        
        if npc_data and npc_data.get('action_verb') == 'examine':
            # Hijack the command and pass it to the dialogue UI engine!
            return self._command_talk(target)
        
        try:
            self.logger.debug(f"_command_examine called with target='{target}'")
            
            # --- TERMINAL INTERCEPTOR ---
            if target:
                char_class = self.player.get('character_class', '')
                if char_class != 'Medium':
                    norm_target = str(target).lower().replace('_', ' ')
                    blocked_aliases = {
                        "death's presence", "deaths presence", "death's breath", "deaths breath",
                        "dark presence", "cold breeze", "sudden draft", "chilling air", 
                        "malevolent gust", "ominous shadow"
                    }
                    if any(alias in norm_target for alias in blocked_aliases):
                        return self._build_response(message=f"You don't see any '{target}' here.", turn_taken=False, success=False)
            # ----------------------------
            
            return self._examine_main(target)
        except Exception as e:
            self.logger.error(f"_command_examine: Unexpected error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong while examining.", turn_taken=False, success=False)

    # =========================================================================
    # --- INVENTORY SYSTEM: TAKE COMMANDS ---
    # =========================================================================

    def _command_take(self, target_str: str) -> dict:
        """The Master Dispatcher for taking items."""
        target_str = (target_str or "").strip()
        if not target_str:
            return self._build_response(message="Take what?", turn_taken=False, success=False)

        # 1. Death's Presence Blocker
        char_class = self.player.get('character_class', '')
        if char_class != 'Medium':
            norm_target = normalize_text(target_str)
            blocked_aliases = {
                "death's presence", "deaths presence", "death's breath", "deaths breath",
                "dark presence", "cold breeze", "sudden draft", "chilling air",
                "malevolent gust", "ominous shadow"
            }
            if any(alias in norm_target for alias in blocked_aliases):
                return self._build_response(
                    message=f"You don't see any '{target_str}' here.", turn_taken=False, success=False
                )

        # 2. MRI Key Intercept (Story Beat)
        lowered = target_str.lower()
        if lowered in ("coroner's office key", "coroners office key", "coroner office key"):
            intercept = self._maybe_intercept_mri_key_take("coroners_office_key")
            if intercept: return intercept

        # 3. Route: Take All
        if normalize_text(target_str) == "all":
            return self._take_all_items()

        # 4. Route: Take [Item] from [Container]
        import re
        match = re.match(r"(.+?)\s+from\s+(.+)", target_str, re.IGNORECASE)
        if match:
            return self._take_from_explicit_container(match.group(1).strip(), match.group(2).strip())

        # 5. Route: Take [Item]
        return self._take_single_item(target_str)

    # ---------------------------------------------------------
    # --- Take Helpers ---
    # ---------------------------------------------------------

    def _take_single_item(self, target_str: str) -> dict:
        """Hunts for a specific item in the room."""
        current_room_id = self.player.get('location')
        target_norm = normalize_text(target_str)
        room_data = self.get_room_data(current_room_id) or {}
        items_master = self.resource_manager.get_data('items', {})
        
        # Check searched containers first
        for furniture in room_data.get('furniture', []):
            if isinstance(furniture, dict) and furniture.get('is_container'):
                # Check EXACT flag formatting
                flag_name = f"searched_{furniture.get('name', '')}"
                if flag_name in self.interaction_flags:
                    for item_id in list(furniture.get('items', [])):
                        # Look up aliases for robust matching
                        item_data = items_master.get(item_id, {})
                        disp = item_data.get('name', item_id)
                        aliases = [normalize_text(a) for a in item_data.get('aliases', [])]
                        aliases.extend([normalize_text(a) for a in item_data.get('alias', [])])
                        
                        if normalize_text(disp) == target_norm or normalize_text(item_id) == target_norm or target_norm in aliases:
                            return self._finalize_item_take(item_id, container_obj=furniture)
                            
        # Check loose items/objects
        entity = self._find_entity_in_room(target_str, current_room_id)
        if not entity:
            return self._build_response(message=f"You don't see any '{target_str}' to take.", turn_taken=False)
            
        entity_data = entity.get('data', {})
        if not entity_data.get('takeable', False):
            return self._build_response(message=f"You can't take the {entity.get('name', target_str)}.", turn_taken=False)
            
        item_id = entity.get('id_key') or entity_data.get('id_key') or entity.get('name') or target_str
        return self._finalize_item_take(item_id)

    def _take_from_explicit_container(self, item_str: str, container_str: str) -> dict:
        """Hunts for a specific item inside a specific container."""
        current_room_id = self.player.get('location')
        container = self._find_entity_in_room(container_str, current_room_id)
        
        if not container or not container.get('data', {}).get('is_container'):
            return self._build_response(message=f"You don't see a container called '{container_str}'.", turn_taken=False)
            
        c_data = container['data']
        items_master = self.resource_manager.get_data('items', {})
        target_norm = normalize_text(item_str)
        
        for i_id in list(c_data.get('items', [])):
            item_data = items_master.get(i_id, {})
            disp = item_data.get('name', i_id)
            aliases = [normalize_text(a) for a in item_data.get('aliases', [])]
            aliases.extend([normalize_text(a) for a in item_data.get('alias', [])])
            
            if normalize_text(disp) == target_norm or normalize_text(i_id) == target_norm or target_norm in aliases:
                return self._finalize_item_take(i_id, container_obj=c_data)
                
        return self._build_response(message=f"The {container['name']} doesn't contain a '{item_str}'.", turn_taken=False)

    def _take_all_items(self) -> dict:
        """Vacuums up all available loose items and items inside searched containers."""
        current_room_id = self.player.get('location')
        taken_display_names = []
        room_data = self.get_room_data(current_room_id) or {}
        items_master = self.resource_manager.get_data('items', {})
        
        # 1. Take from Searched Containers
        for furniture in room_data.get('furniture', []):
            if isinstance(furniture, dict) and furniture.get('is_container'):
                flag_name = f"searched_{furniture.get('name', '')}"
                if flag_name in self.interaction_flags:
                    for item_id in list(furniture.get('items', [])):
                        # ALWAYS SAVE SNAKE CASE KEYS
                        safe_id = item_id.lower().replace(' ', '_').replace("'", "").replace('"', '')
                        self.player.setdefault('inventory', []).append(safe_id)
                        taken_display_names.append(self._get_item_display_name(safe_id))
                        furniture['items'].remove(item_id)
                        self._process_take_side_effects(safe_id, current_room_id)
                        self._record_item_lore(safe_id)

        # 2. Take Loose Items
        items_to_remove = []
        for item_id, world_data in self.current_level_items_world_state.items():
            if world_data.get("location") == current_room_id:
                item_data = items_master.get(item_id, {})
                if item_data.get("takeable", False):
                    safe_id = item_id.lower().replace(' ', '_').replace("'", "").replace('"', '')
                    self.player.setdefault('inventory', []).append(safe_id)
                    taken_display_names.append(self._get_item_display_name(safe_id))
                    items_to_remove.append(item_id)
                    self._process_take_side_effects(safe_id, current_room_id)
                    self._record_item_lore(safe_id)

        for item_id in items_to_remove:
            del self.current_level_items_world_state[item_id]

        if not taken_display_names:
            return self._build_response(message="There is nothing here to take.", turn_taken=False)

        self.logger.info(f"_take_all_items: Items taken: {taken_display_names}")
        self.add_ui_event({"event_type": "refresh_context_actions"})
        self.add_ui_event({"event_type": "refresh_map"})
        
        return self._build_response(message=f"You took: {', '.join(taken_display_names)}.", turn_taken=True, success=True)

    def _process_take_side_effects(self, item_key: str, room_id: str):
        """Process on_action_effects for taking an item (e.g., revealing hidden containers)."""
        items_master = self.resource_manager.get_data('items', {})
        item_def = items_master.get(item_key, {})
        
        effects = item_def.get('on_action_effects', {}).get('take', {})
        if not effects:
            return

        # Check conditions (e.g., player must be in specific room)
        conditions = effects.get('conditions', [])
        for cond in conditions:
            if cond.get('type') == 'player_in_room':
                if self.player.get('location') != cond.get('value'):
                    self.logger.info(f"_process_take_side_effects: Condition not met — player not in '{cond.get('value')}'")
                    return
            
        # Process the specific effects
        for effect in effects.get('effects', []):
            etype = effect.get('type')
            
            if etype == 'reveal_hidden_container':
                container_name = effect.get('container_name')
                if container_name:
                    self._reveal_hidden_container(container_name, room_id)
                    # Also set the interaction flag so _get_visible_furniture_in_room can find it via revealed_by_flag
                    reveal_flag = f"{container_name}_revealed"
                    self.set_interaction_flag(reveal_flag)
                    self.logger.info(f"_process_take_side_effects: Set interaction flag '{reveal_flag}'")
                    
            elif etype == 'set_room_flag':
                flag = effect.get('flag')
                if flag:
                    # Set BOTH room_flags (for room-scoped logic) AND interaction_flags (for furniture visibility checks)
                    self.player.setdefault('room_flags', {}).setdefault(room_id, set()).add(flag)
                    self.set_interaction_flag(flag)
                    self.logger.info(f"_process_take_side_effects: Set flag '{flag}' (room + interaction)")
                    
            elif etype == 'display_message':
                msg = effect.get('message', '')
                if msg:
                    self.add_ui_event({"event_type": "show_popup", "title": "Discovery", "message": msg})


    def _reveal_hidden_container(self, container_name: str, room_id: str):
        """Unhide a container in the room's world state so it becomes searchable."""
        from fd_terminal.utils import normalize_text
        
        room_state = self.current_level_rooms_world_state.get(room_id, {})
        furniture_list = room_state.get('furniture', [])
        
        for furn in furniture_list:
            # Use normalize_text to bridge the gap between "fireplace_cavity" and "Fireplace Cavity"
            if isinstance(furn, dict) and normalize_text(furn.get('name', '')) == normalize_text(container_name):
                furn['is_hidden_container'] = False
                self.logger.info(f"Revealed hidden container '{container_name}' in '{room_id}'")
                
                # Instantly refresh the UI so 'Search Fireplace Cavity' appears
                self.add_ui_event({"event_type": "refresh_context_actions"})
                return
        
        self.logger.warning(f"_reveal_hidden_container: '{container_name}' not found in '{room_id}'")

    def _finalize_item_take(self, item_id: str, container_obj: dict = None) -> dict:
        """The single source of truth for putting an item in the player's pocket."""
        current_room_id = self.player.get('location')
        
        # 1. Sanitize the ID aggressively
        safe_id = item_id.lower().replace(' ', '_').replace("'", "").replace('"', '')
        
        # 2. Add to Inventory
        self.player.setdefault('inventory', []).append(safe_id)
        
        # 3. Remove from World
        if container_obj and 'items' in container_obj:
            if item_id in container_obj['items']:
                container_obj['items'].remove(item_id)
        else:
            if item_id in self.current_level_items_world_state:
                del self.current_level_items_world_state[item_id]
                
        # 4. Side Effects & Lore
        self._process_take_side_effects(item_key=safe_id, room_id=current_room_id)
        self._record_item_lore(safe_id)

        try:
            self._maybe_emit_requirements_met_event()
        except Exception:
            pass

        self.add_ui_event({"event_type": "refresh_context_actions"})
        self.add_ui_event({"event_type": "refresh_map"})
        
        display_name = self._get_item_display_name(safe_id)
        return self._build_response(message=f"You take the {display_name}.", turn_taken=True, success=True)

    def _record_item_lore(self, item_id: str):
        """Silently handles achievements, evidence, and narrative flags when an item is acquired."""
        items_master = self.resource_manager.get_data('items', {})
        item_data = items_master.get(item_id, {})
        
        if item_data.get('is_evidence', False) and getattr(self, 'achievements_system', None):
            self.achievements_system.record_evidence(
                evidence_id=item_id,
                name=item_data.get('name', item_id),
                description=item_data.get('description', ''),
                char_connection=item_data.get('character_connection')
            )
            
        if getattr(self, 'achievements_system', None) and item_data.get('unlocks_achievement'):
            self.achievements_system.unlock(item_data['unlocks_achievement'])
            
        flag = item_data.get('narrative_flag_on_collect')
        if flag:
            self.set_interaction_flag(flag)

    # --- NEW: The Rite of Discovery ---
    def _command_search(self, target: str) -> dict:
        """Handles the 'search' command to find items within a container. Injected with robust debugging logic."""
        self.logger.debug(f"_command_search called with target='{target}'")
        current_room_id = self.player['location']

        if not target:
            self.logger.info("_command_search: No target specified")
            return self._build_response(message="Search what?", turn_taken=False, success=False)

        entity = self._find_entity_in_room(target, current_room_id)
        self.logger.debug(f"_command_search: Entity found: {entity}")

        if not entity:
            self.logger.info(f"_command_search: '{target}' not found in room '{current_room_id}'")
            return self._build_response(message=f"You don't see a '{target}' to search here.", turn_taken=False, success=False)

        if entity['type'] != 'furniture' or not entity['data'].get('is_container'):
            self.logger.info(f"_command_search: Entity '{entity['name']}' is not a searchable container")
            return self._build_response(message=f"You can't search the {entity['name']}.", turn_taken=False, success=False)

        container_data = entity['data']
        self.logger.debug(f"_command_search: Container data: {container_data}")

        # Check both the top-level 'locked' flag and the nested 'locking.locked' sub-field.
        # _try_unlock_furniture and _break_furniture_effect both clear these paths, but we
        # check both defensively to avoid false positives from a stale sub-field.
        locking_sub = container_data.get('locking') if isinstance(container_data.get('locking'), dict) else {}
        locked = container_data.get('locked') or locking_sub.get('locked', False)
        if locked:
            self.logger.info(f"_command_search: Container '{entity['name']}' is locked")
            return self._build_response(message=f"The {entity['name']} is locked.", turn_taken=False, success=False)

        items_in_container = container_data.get('items', [])
        self.logger.debug(f"_command_search: Items in container: {items_in_container}")

        self.set_interaction_flag(f"searched_{entity['id_key']}")

        if not items_in_container:
            message = f"You search the {entity['name']} but find nothing."
            self.logger.info(f"_command_search: No items found in '{entity['name']}'")
            return self._build_response(message=message, turn_taken=True, success=True)
        else:
            item_names = [self._get_item_display_name(key) for key in items_in_container]
            colored_item_names = [color_text(name, 'item', self.resource_manager) for name in item_names]
            message = f"You search the {entity['name']} and find: {', '.join(colored_item_names)}."
            self.logger.info(f"_command_search: Found items in '{entity['name']}': {item_names}")
            return self._build_response(message=message, turn_taken=True, success=True)

    def _command_inventory(self, target: str) -> dict:
        """
        Opens the full inventory popup screen.
        Emits a 'show_inventory_popup' UI event which GameScreen handles
        by instantiating InventoryPopup directly (it needs a live command callback).
        Returns a silent, no-turn response so the game log stays clean.
        """
        inventory_list = self.player.get('inventory', [])
        if not inventory_list:
            return self._build_response(message="You are not carrying anything.", turn_taken=False)
        return self._build_response(
            ui_events=[{"event_type": "show_inventory_popup"}],
            turn_taken=False,
        )
    
    def _command_use(self, target_str: str) -> dict:
        target = (target_str or "").strip()
        
        # --- THE FIX: Intercept Interactive State Machines ---
        npcs_master = self.resource_manager.get_data('npcs', {})
        # Omni-Lookup to survive JSON nesting
        npc_data = npcs_master.get(target) or npcs_master.get('npcs', {}).get(target, {})
        
        if npc_data and npc_data.get('action_verb') == 'use':
            # Hijack the command and pass it to the dialogue UI engine!
            return self._command_talk(target)

        """Handle the 'use' command. Try room interactables, hazards/objects, then inventory."""
        self.logger.debug(f"_command_use: target='{target_str}'")
        try:
            return self._use_main(target_str)
        except Exception as e:
            self.logger.error(f"_command_use: Unexpected error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong while trying to use that.", turn_taken=False, success=False)
    def _command_combine(self, target_str: str) -> dict:
        """
        Combines two items from inventory. 
        Syntax: "combine A with B" or "combine A and B"
        """
        if not target_str:
            return self._build_response(message="Combine what with what?", turn_taken=False)

        # 1. Parse Input
        parts = re.split(r'\s+(?:with|and)\s+', target_str.lower())
        if len(parts) != 2:
            return self._build_response(message="Try 'combine [item] with [item]'.", turn_taken=False)
        
        item_a_name, item_b_name = parts[0].strip(), parts[1].strip()
        
        # 2. Validate Inventory
        inv_keys = self.player.get('inventory', [])
        # Helper to find key by name
        def find_key(name):
            items_master = self.resource_manager.get_data('items', {})
            for key in inv_keys:
                # Handle list of strings or dicts
                k_str = key if isinstance(key, str) else key.get('id')
                data = items_master.get(k_str, {})
                if normalize_text(data.get('name', k_str)) == normalize_text(name):
                    return k_str
            return None

        key_a = find_key(item_a_name)
        key_b = find_key(item_b_name)

        if not key_a:
            return self._build_response(message=f"You don't have '{item_a_name}'.", turn_taken=False)
        if not key_b:
            return self._build_response(message=f"You don't have '{item_b_name}'.", turn_taken=False)
        if key_a == key_b:
            return self._build_response(message="You can't combine an item with itself.", turn_taken=False)

        # 3. Check Recipes
        recipes = self.resource_manager.get_data('recipes', {})
        found_recipe = None
        
        # We check if the set of ingredients matches
        player_ingredients = {key_a, key_b}
        
        for r_id, r_data in recipes.items():
            required = set(r_data.get('ingredients', []))
            if required == player_ingredients:
                found_recipe = r_data
                break
        
        if not found_recipe:
            return self._build_response(message=f"You can't combine {item_a_name} and {item_b_name}.", turn_taken=False)

        # 4. Execute Combination
        # Remove ingredients
        if isinstance(self.player['inventory'], list):
            # Safe removal handling
            new_inv = [i for i in self.player['inventory'] if (i if isinstance(i, str) else i.get('id')) not in player_ingredients]
            self.player['inventory'] = new_inv
        
        # Add result
        result_item = found_recipe['result']
        self.player['inventory'].append(result_item)
        
        # Log & Return
        success_msg = found_recipe.get('message', "Items combined successfully.")
        self.logger.info(f"Crafted {result_item} from {key_a} + {key_b}")
        
        # Award achievement if applicable
        if self.achievements_system:
            self.achievements_system.unlock("macgyver")

        return self._build_response(message=color_text(success_msg, 'success', self.resource_manager), turn_taken=True, success=True)