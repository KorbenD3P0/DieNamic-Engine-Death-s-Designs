from fd_terminal.utils import color_text
import copy
import os

class SystemMixin:
    
    def wipe_meta_progression(self):
        """
        Permanently erases all achievements and journal history.
        Called via the settings menu action.
        """
        self.logger.warning("Initiating meta-progression wipe...")
        import os
        import json
        from kivy.app import App
        
        data_dir = App.get_running_app().user_data_dir

        # --- 1. Clear Achievements ---
        if getattr(self, 'achievements_system', None):
            if hasattr(self.achievements_system, 'unlocked_achievements'):
                self.achievements_system.unlocked_achievements.clear()
            
            if hasattr(self.achievements_system, 'save_user_profile'):
                self.achievements_system.save_user_profile()
            elif hasattr(self.achievements_system, 'save_achievements'):
                self.achievements_system.save_achievements()
                
            self.logger.info("In-memory achievements cleared and saved.")
        else:
            # Fallback: Soft-reset the file directly
            ach_path = os.path.join(data_dir, 'user_profile.json')
            if os.path.exists(ach_path):
                try:
                    with open(ach_path, 'r') as f:
                        profile_data = json.load(f)
                    profile_data['unlocked_achievements'] = []
                    with open(ach_path, 'w') as f:
                        json.dump(profile_data, f, indent=4)
                    self.logger.info("user_profile.json achievements soft-reset.")
                except Exception as e:
                    self.logger.error(f"Failed to reset user_profile.json: {e}")

        # --- 2. Clear Journal/History ---
        if hasattr(self, 'player') and isinstance(self.player, dict):
            self.player['journal'] = []
            if 'journal_history' in self.player:
                self.player['journal_history'] = []

        journal_path = os.path.join(data_dir, 'journal_history.json')
        if os.path.exists(journal_path):
            try:
                with open(journal_path, 'w') as f:
                    json.dump([], f)
                self.logger.info("Journal history soft-reset to empty list.")
            except Exception as e:
                self.logger.error(f"Failed to reset journal history file: {e}")

        # --- 3. Save the Clean State ---
        if hasattr(self, 'player') and self.player.get('location'):
            try:
                self._command_save('quicksave')
                self.logger.info("Current run quicksaved to flush old meta-data.")
            except Exception as e:
                self.logger.warning(f"Could not quicksave after wipe: {e}")
        
        self.logger.info("Meta-progression wipe sequence complete.")

    def _command_debug_room(self, _) -> dict:
        """Debug command to show current room data and player inventory."""
        current_room_id = self.player.get('location', '')
        room_data = self.get_room_data(current_room_id)
        
        debug_info = [
            f"=== DEBUG INFO FOR {current_room_id} ===",
            f"Room Data: {room_data}",
            f"Player Inventory: {self.player.get('inventory', [])}",
            f"Player Location: {current_room_id}",
        ]
        
        # Show available exits and their lock status
        if room_data and 'exits' in room_data:
            debug_info.append("=== EXITS ===")
            for direction, dest in room_data['exits'].items():
                if isinstance(dest, dict):
                    debug_info.append(f"  {direction}: BLOCKED ({dest})")
                else:
                    dest_data = self.get_room_data(dest)
                    locked = dest_data.get('locked', False) if dest_data else 'NO DATA'
                    locked_by_mri = dest_data.get('locked_by_mri', False) if dest_data else False
                    locking_info = dest_data.get('locking', {}) if dest_data else {}
                    debug_info.append(f"  {direction} -> {dest}: locked={locked}, locked_by_mri={locked_by_mri}, locking={locking_info}")
        
        # Show key details
        items_master = self.resource_manager.get_data('items', {})
        debug_info.append("=== KEYS IN INVENTORY ===")
        for item_key in self.player.get('inventory', []):
            item_data = items_master.get(item_key, {})
            if item_data.get("type") == "key" or "key" in item_key.lower():
                debug_info.append(f"  {item_key}: {item_data}")
        
        return self._build_response(
            message="\n".join(debug_info),
            turn_taken=False
        )

    def _command_roster(self, target_str: str = None) -> dict:
        """Displays the surviving cast and the Death's List (if learned)."""
        roster = self.player.get('npc_status', {})
        alive = [n.title() for n, s in roster.items() if s in ('alive', 'injured') and n != 'player']
        dead = [n.title() for n, s in roster.items() if s == 'dead' and n != 'player']
        
        msg = "[b][color=ff0000]SURVIVOR ROSTER:[/color][/b]\n"
        msg += f"Alive: {', '.join(alive) if alive else 'None'}\n"
        msg += f"Deceased: {', '.join(dead) if dead else 'None'}\n"
        
        if self.get_player_flag("learned_deaths_list"):
            deaths_list = self.player.get('deaths_list', [])
            current_index = self.player.get('deaths_list_index', 0)
            
            msg += "\n[b]THE PREMONITION ORDER:[/b]\n"
            for i, name in enumerate(deaths_list):
                display_name = name.title() if name != 'player' else "YOU"
                
                if i < current_index:
                    msg += f"[s][color=555555]{i+1}. {display_name}[/color][/s]\n"
                elif i == current_index:
                    msg += f"[color=ff0000]{i+1}. {display_name}  <-- NEXT[/color]\n"
                else:
                    msg += f"{i+1}. {display_name}\n"
        else:
            msg += "\n[color=555555](You do not know the order of Death's design... yet.)[/color]\n"
            
        # --- THE FIX: Package the UI Event ---
        ui_events = [{
            "event_type": "show_popup",
            "title": "The Design",
            "message": msg
        }]
        
        return self._build_response(
            message="You recall the order the survivors are fated to die in, as well as who is still alive... for now.", 
            turn_taken=False, 
            ui_events=ui_events
        )

    def _command_main_menu(self, _=None) -> dict:
        """Return to the main menu from the gamescreen, clearing all player and world state."""
        self.logger.info("Returning to main menu via _command_main_menu. Resetting all game state.")

        # Reset all game state to initial values
        self.is_game_over = False
        self.game_won = False
        self.ui_events = []
        self.interaction_flags = set()
        self.player = {}
        self.current_level_rooms_world_state = {}
        self.current_level_items_world_state = {}
        self.last_dialogue_context = {}
        # Optionally reset hazard engine and death AI if needed
        if self.hazard_engine:
            try:
                self.hazard_engine.reset()
            except Exception:
                pass
        if self.death_ai:
            try:
                self.death_ai.reset()
            except Exception:
                pass

        # Add a UI event to trigger the main menu transition
        self.add_ui_event({
            "event_type": "go_to_main_menu"
        })
        return self._build_response(
            message="Returning to main menu...",
            event_type="go_to_main_menu",
            game_state=self.get_current_game_state()
        )

    def _command_set_qte_sr(self, arg: str) -> dict:
        """
        Developer helper: set the adaptive QTE success rate (0.0–1.0).
        Example: 'set_qte_sr 0.25'
        """
        try:
            val = max(0.0, min(1.0, float((arg or "").strip())))
        except Exception:
            return self._build_response(message="Usage: set_qte_sr <0.0–1.0>", turn_taken=False)

        da = getattr(self, 'death_ai', None)
        if not da:
            return self._build_response(message="DeathAI not available.", turn_taken=False)

        # Prefer the canonical field used by DeathAI.get_status_report
        if hasattr(da, 'player_behavior_patterns'):
            da.player_behavior_patterns['qte_success_rate'] = val
        elif hasattr(da, 'player_patterns'):
            da.player_patterns['qte_success_rate'] = val
        else:
            if not hasattr(da, 'patterns'):
                da.patterns = {}
            da.patterns['qte_success_rate'] = val

        self.logger.info(f"Set adaptive qte_success_rate={val:.2f}")
        return self._build_response(message=f"qte_success_rate set to {val:.2f}", turn_taken=False)

    def _command_test_qte(self, args: str) -> dict:
        """
        test_qte <type> — Tests a single QTE type.
        test_qte all — Queues up every QTE type to be played one by one.
        """
        from kivy.clock import Clock
        
        if not self.qte_engine:
            return self._build_response(message="QTE engine not available.", turn_taken=False)
        
        qte_defs = self.qte_engine.qte_definitions
        args = (args or "").strip().lower()
        
        if args == "all":
            types_to_test = list(qte_defs.keys())
        elif args in qte_defs:
            types_to_test = [args]
        else:
            available = ", ".join(sorted(qte_defs.keys()))
            return self._build_response(
                message=f"Usage: test_qte <type> or test_qte all\nAvailable: {available}",
                turn_taken=False
            )
        
        # Store queue in the instance
        self._test_qte_queue = types_to_test
        
        # Cancel any existing test sequence checker
        if getattr(self, '_test_qte_event', None):
            self._test_qte_event.cancel()
            
        def _check_qte_queue(dt):
            if not getattr(self, '_test_qte_queue', None):
                return False # Stop checking if queue is empty
                
            # If a QTE is currently active, wait for the player to finish it
            if self.player.get('qte_active', False):
                return True
                
            # Check if the UI is currently displaying the previous QTE's result popup
            from kivy.app import App
            app = App.get_running_app()
            if app and app.root:
                game_screen = app.root.get_screen('game') if hasattr(app.root, 'get_screen') else None
                if game_screen and getattr(game_screen, 'active_info_popup', None):
                    return True # Wait for player to dismiss the result popup
            
            # Pop the next QTE from the queue
            qte_type = self._test_qte_queue.pop(0)
            qte_def = self.qte_engine.qte_definitions.get(qte_type, {})
            
            # Start it with a small 0.5s delay to ensure smooth UI transition
            def _start_it(dt2):
                self.qte_engine.start_qte(qte_type, {
                    "ui_prompt_message": f"[TEST] {qte_def.get('name', qte_type)}",
                    "duration": qte_def.get('default_duration', 8.0),
                    "is_fatal_on_failure": False,
                    "hp_damage_on_failure": 0,
                })
            Clock.schedule_once(_start_it, 0.5)
            
            # If queue is now empty, cancel this interval
            if not self._test_qte_queue:
                return False
                
            return True
            
        self._test_qte_event = Clock.schedule_interval(_check_qte_queue, 1.0)
        
        return self._build_response(
            message=f"Starting QTE test sequence. ({len(types_to_test)} queued)", 
            turn_taken=False
        )

    def _command_test_level(self, args: str) -> dict:
        """
        test_level <level_id> — Debug Warp Command.
        Bypasses the game's linear progression, injects a mock Death's List, 
        auto-grants required items, and boots directly into the specified level.
        """
        level_id = (args or "").strip()
        if not level_id:
            return self._build_response(
                message="[color=ff0000]Error: Please specify a level ID. Example: test_level level_1[/color]", 
                turn_taken=False
            )

        import random
        
        # 1. Generate Mock Cast
        mock_names = ["Taylor", "Morgan", "Alex", "Sam", "Jordan", "Casey"]
        random.shuffle(mock_names)
        survivors = mock_names[:3]
        casualties = mock_names[3:]
        
        # 2. Build Death's List
        deaths_list = list(survivors)
        deaths_list.append('player')
        random.shuffle(deaths_list)
        
        # 3. Assign Roles & Status
        roles = ["friend", "skeptic", "visionary", "fatalist", "bystander", "authority"]
        npc_roles = {name.lower(): roles[i] if i < len(roles) else "bystander" for i, name in enumerate(mock_names)}
        npc_status = {name.lower(): "alive" if name in survivors else "dead" for name in mock_names}
            
        # 4. Inject Mock Data
        self.player['premonition_survivors'] = survivors
        self.player['premonition_casualties'] = casualties
        self.player['deaths_list'] = deaths_list
        self.player['deaths_list_index'] = 0
        self.player['npc_roles'] = npc_roles
        self.player['npc_status'] = npc_status
        self.player['companions'] = [random.choice(survivors)]
        self.player['intro_disaster'] = {
            "name": "a catastrophic debug testing event",
            "tags": ["explosive", "collapse"],
            "killed_count": 99
        }

        # --- AUTO-GRANT ALL REQUIRED ITEMS ---
        granted_items = set()
        
        all_reqs = self.resource_manager.get_data('level_requirements', {})
        level_reqs = all_reqs.get(level_id, {})
        granted_items.update(level_reqs.get('items_needed', []))
        granted_items.update(level_reqs.get('evidence_needed', []))
            
        level_num = str(level_id).replace("level_", "")
        possible_keys = [f"rooms_level_{level_num}", f"room_level_{level_num}", f"rooms_{level_num}", f"rooms_{level_id}", str(level_id)]
        rooms_data = {}
        for key in possible_keys:
            data = self.resource_manager.get_data(key)
            if data:
                rooms_data = data
                break
                
        npcs_master = self.resource_manager.get_data('npcs', {})
        
        # Laser-focused item extraction tool
        def extract_items(action_dict):
            items = set()
            if not isinstance(action_dict, dict): return items
            for k in ['requires_items', 'item_names_required', 'items_needed']:
                val = action_dict.get(k, [])
                if isinstance(val, list):
                    items.update(val)
            return items

        for r_id, r_data in rooms_data.items():
            if not isinstance(r_data, dict): continue
            
            # Scan Furniture & Objects
            for category in ['furniture', 'objects']:
                for item in r_data.get(category, []):
                    if isinstance(item, dict):
                        for interaction in item.get('use_item_interaction', []):
                            granted_items.update(extract_items(interaction))
            
            # Scan NPCs defined in the room
            for npc in r_data.get('npcs', []):
                if isinstance(npc, dict):
                    for state_id, state_data in npc.get('dialogue_states', {}).items():
                        # --- THE FIX: Ensure the state is actually a dictionary ---
                        if isinstance(state_data, dict):
                            granted_items.update(extract_items(state_data.get('on_talk_action', {})))

            # Scan NPCs referenced by string IDs (Like your Finales!)
            for npc_id in r_data.get('npcs_present', []):
                if isinstance(npc_id, str):
                    npc_data = npcs_master.get(npc_id) or npcs_master.get('npcs', {}).get(npc_id, {})
                    for state_id, state_data in npc_data.get('dialogue_states', {}).items():
                        # --- THE FIX: Ensure the state is actually a dictionary ---
                        if isinstance(state_data, dict):
                            granted_items.update(extract_items(state_data.get('on_talk_action', {})))

        # Merge into player inventory
        current_inv = set(self.player.get('inventory', []))
        self.player['inventory'] = list(current_inv.union(granted_items))
        
        self.logger.info(f"Warp Whistle: Auto-granted items: {granted_items}")

        # 5. Execute the Warp & Auto-Refresh
        try:
            self.start_next_level(level_id=level_id)
            
            # --- THE FIX: Force a 'look' command to auto-refresh the UI and Text Panel ---
            look_response = self._command_examine(target="")
            
            warp_message = (
                f"[b][color=00ff00]USED WARP WHISTLE[/color][/b]\n"
                f"Transporting to: {level_id}\n\n"
                f"[color=ffff00]Mock Data Injected:[/color]\n"
                f"Death's List: {', '.join(deaths_list)}\n"
                f"Active Companion: {self.player['companions'][0]}\n\n"
                f"[color=00ffff]Auto-Granted Items:[/color] {', '.join(granted_items) if granted_items else 'None'}\n"
            )
            
            # Combine the warp success message with the actual room description
            final_message = warp_message + "\n" + look_response.get('message', '')
            
            self.add_ui_event({
                "event_type": "show_popup",
                "title": "Used Warp Whistle",
                "message": warp_message
            })
            
            # Setting turn_taken=True guarantees the Kivy UI refreshes the action buttons!
            return self._build_response(
                message=final_message, 
                turn_taken=True, 
                ui_events=look_response.get('ui_events', [])
            )
            
        except Exception as e:
            self.logger.error(f"Debug Warp Failed: {e}", exc_info=True)
            return self._build_response(
                message=f"[color=ff0000]Warp failed. Error: {e}[/color]", 
                turn_taken=False
            )

    def _command_save(self, slot_identifier: str = None) -> dict:
        """
        Save the current game state to a specified slot.
        Injected with robust logging and error handling.
        """
        if not slot_identifier:
            slot_identifier = "quicksave"
        
        try:
            from fd_terminal.utils import get_save_filepath
            from datetime import datetime
            import json

            self.logger.info(f"_command_save: Attempting to save game to slot '{slot_identifier}'.")

            # Deep copy all mutable structures
            player_state = copy.deepcopy(self.player)
            # Convert sets to lists for JSON
            if isinstance(player_state.get("visited_rooms"), set):
                player_state["visited_rooms"] = list(player_state["visited_rooms"])
            if isinstance(player_state.get("flags"), set):
                player_state["flags"] = list(player_state["flags"])
            if isinstance(player_state.get("_qte_processed_hazards_this_turn"), set):
                player_state["_qte_processed_hazards_this_turn"] = list(player_state["_qte_processed_hazards_this_turn"])
            # ---------------------
            save_data = {
                "save_info": {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "location": player_state.get('location', 'Unknown'),
                    "character_class": player_state.get('character_class', ''),
                    "turns_left": player_state.get('turns_left', 0),
                    "current_level": player_state.get('current_level', 1),
                    "hp": player_state.get('hp', 0),
                    "fear": player_state.get('fear', 0.0),
                    "score": player_state.get('score', 0),
                    "actions_taken": player_state.get('actions_taken', 0),
                    "companion_location": player_state.get('companion_location', ''),
                },
                "player_state": player_state,
                "level_rooms_state": copy.deepcopy(self.current_level_rooms_world_state),
                "level_items_state": copy.deepcopy(self.current_level_items_world_state),
                "interaction_flags": list(self.interaction_flags),
                "game_flags": {
                    "is_game_over": self.is_game_over,
                    "game_won": self.game_won
                },
                "engine_states": {}
            }
            
            # Save hazard engine state if available
            if self.hazard_engine:
                try:
                    save_data["engine_states"]["hazard_engine"] = self.hazard_engine.get_save_state()
                except Exception as e:
                    self.logger.warning(f"_command_save: Could not save hazard engine state: {e}", exc_info=True)
            
            # Save death AI state if available
            if self.death_ai:
                try:
                    save_data["engine_states"]["death_ai"] = self.death_ai.get_save_state()
                except Exception as e:
                    self.logger.warning(f"_command_save: Could not save death AI state: {e}", exc_info=True)
            
            # Write to file
            save_path = get_save_filepath(slot_identifier)
            try:
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
            except Exception as e:
                self.logger.error(f"_command_save: Failed to write save file '{save_path}': {e}", exc_info=True)
                return self._build_response(
                    message=f"Failed to write save file: {str(e)}",
                    turn_taken=False,
                    success=False
                )
            
            self.logger.info(f"_command_save: Game saved to slot '{slot_identifier}' at {save_path}")
            
            # Trigger achievement for first save
            if self.achievements_system:
                try:
                    self.achievements_system.unlock("first_save")
                except Exception as e:
                    self.logger.warning(f"_command_save: Could not unlock 'first_save' achievement: {e}", exc_info=True)
            
            return self._build_response(
                message=f"Game saved to {slot_identifier.replace('_', ' ')}.",
                turn_taken=False,
                success=True
            )
            
        except Exception as e:
            self.logger.error(f"_command_save: Failed to save game to slot '{slot_identifier}': {e}", exc_info=True)
            return self._build_response(
                message=f"Failed to save game: {str(e)}",
                turn_taken=False,
                success=False
            )

    def _command_load(self, slot_identifier: str = None) -> dict:
        """
        Load game state from a specified slot, restoring all necessary aspects for a valid session.
        Injected with robust logging and error handling.
        """
        self.logger.info(f"_command_load: Attempting to load game from slot '{slot_identifier}'")
        if not slot_identifier:
            self.logger.warning("_command_load: No slot_identifier provided.")
            return self._build_response(
                message="Please specify a save slot (e.g., 'load quicksave' or 'load slot_1').",
                turn_taken=False
            )
        try:
            from fd_terminal.utils import get_save_filepath
            import json

            save_path = get_save_filepath(slot_identifier)
            self.logger.debug(f"_command_load: Computed save_path='{save_path}'")
            if not os.path.exists(save_path):
                self.logger.warning(f"_command_load: Save file '{save_path}' does not exist.")
                return self._build_response(
                    message=f"No save file found for slot '{slot_identifier}'.",
                    turn_taken=False,
                    success=False
                )

            with open(save_path, encoding='utf-8') as f:
                try:
                    save_data = json.load(f)
                except json.JSONDecodeError as e:
                    self.logger.error(f"_command_load: JSON decode error: {e}")
                    return self._build_response(
                        message=f"Save file '{slot_identifier}' is corrupted.",
                        turn_taken=False,
                        success=False
                    )

            if "player_state" not in save_data:
                self.logger.error(f"_command_load: 'player_state' missing in save file '{slot_identifier}'")
                return self._build_response(
                    message="Save file is corrupted or invalid.",
                    turn_taken=False,
                    success=False
                )

            # Restore player state and ensure sets are restored
            self.player = save_data["player_state"].copy()
            if isinstance(self.player.get("visited_rooms"), list):
                self.player["visited_rooms"] = set(self.player["visited_rooms"])
            if isinstance(self.player.get("flags"), list):
                self.player["flags"] = set(self.player["flags"])
            if isinstance(self.player.get("_qte_processed_hazards_this_turn"), list):
                self.player["_qte_processed_hazards_this_turn"] = set(self.player["_qte_processed_hazards_this_turn"])
            # ---------------------
            # Restore world state
            self.current_level_rooms_world_state = save_data.get("level_rooms_state", {})
            self.current_level_items_world_state = save_data.get("level_items_state", {})
            self.interaction_flags = set(save_data.get("interaction_flags", []))

            # Restore game flags
            game_flags = save_data.get("game_flags", {})
            self.is_game_over = game_flags.get("is_game_over", False)
            self.game_won = game_flags.get("game_won", False)

            # Restore engine states if available
            engine_states = save_data.get("engine_states", {})
            if self.hazard_engine and "hazard_engine" in engine_states:
                try:
                    self.hazard_engine.load_save_state(engine_states["hazard_engine"])
                except Exception as e:
                    self.logger.warning(f"_command_load: Could not restore hazard engine state: {e}", exc_info=True)

            if self.death_ai and "death_ai" in engine_states:
                try:
                    self.death_ai.load_state(engine_states["death_ai"])
                except Exception as e:
                    self.logger.warning(f"_command_load: Could not restore death AI state: {e}", exc_info=True)

            # Reset QTE state (don't restore active QTEs)
            self.player['qte_active'] = False
            self.player['qte_context'] = {}

            # Rebuild coordinate map for current location (ensures map and navigation work)
            current_room = self.player.get('location')
            if current_room:
                try:
                    self._build_room_coordinate_map(current_room)
                except Exception as e:
                    self.logger.warning(f"_command_load: Could not rebuild room coordinate map: {e}", exc_info=True)

            # Recompile omens for the current level (ensures omens system is live)
            level_id = self.player.get('current_level', 1)
            try:
                self.current_level_omens = self._compile_level_omens(level_id)
            except Exception as e:
                self.logger.warning(f"_command_load: Could not recompile omens: {e}", exc_info=True)

            # Ensure companion state is consistent
            self.player.setdefault('companion_location', 'Cineplex Lobby')

            # Restore any other persistent fields as needed
            self.last_dialogue_context = save_data.get("last_dialogue_context", {})

            self.logger.info(f"_command_load: Game loaded from slot '{slot_identifier}' successfully.")

            room_desc = self._get_rich_room_description(self.player.get('location', ''))
            self.start_response = {
                "messages": [room_desc],
                "game_state": self.get_current_game_state(),
                "ui_events": [],
                "turn_taken": False,
                "success": True
            }

            return self._build_response(
                message=f"Game loaded from {slot_identifier.replace('_', ' ')}.",
                turn_taken=False,
                success=True,
                ui_events=[{
                    "event_type": "game_loaded",
                    "room_description": room_desc
                }]
            )

        except Exception as e:
            self.logger.error(f"_command_load: Failed to load game from slot '{slot_identifier}': {e}", exc_info=True)
            return self._build_response(
                message=f"Failed to load game: {str(e)}",
                turn_taken=False,
                success=False
            )

    def _command_wait(self, target: str) -> dict:
        """Handles the 'wait' command, allowing the player to pass a turn."""
        
        # --- THE FIX: Elevator Timer Failsafe ---
        if self.player.get('elevator_transit_active'):
            self.logger.info("Player typed 'wait' during transit. Fast-forwarding arrival.")
            return getattr(self, 'process_elevator_arrival')()
        # ----------------------------------------
        
        message = "You wait for a moment, observing your surroundings."
        return self._build_response(message=message, turn_taken=True, success=True)

    def _command_help(self, target: str) -> dict:
        """Handles the 'help' command by listing available actions."""
        # We derive the available commands directly from the command_map.
        # This means as we add new commands, this help text updates automatically!
        
        # We use a set to get only unique method names, then capitalize them.
        available_verbs = sorted(list({v.__name__.replace('_command_', '').capitalize() for v in self.command_map.values()}))
        
        message = "[b]Available Actions:[/b]\n"
        message += ", ".join(available_verbs)
        message += "\n\nTry commands like 'move north', 'examine table', or 'take key'."
        
        return self._build_response(message=message, turn_taken=False)
    
    def _command_gimme(self, target_name_str: str) -> dict:
        """
        DEBUG COMMAND: Injects items directly into the player's inventory.
        Usage: 
            'gimme movie stub' (adds 1)
            'gimme 5 bandage' (adds 5)
            'gimme all' (adds 1 of every item in the game)
        """
        import re

        target_name_str = (target_name_str or "").strip()
        if not target_name_str:
            return self._build_response(
                message="[color=ffaa00]DEBUG:[/color] Gimme what? (e.g., 'gimme 3 bandages' or 'gimme all')", 
                turn_taken=False
            )

        # 1. Aggregate all items from the Resource Manager
        # This safely catches 'items.json' or 'items_medical.json', etc.
        items_db = {}
        for key, data in getattr(self, 'resource_manager', None).master_data.items():
            if key.startswith('items') and isinstance(data, dict):
                items_db.update(data)

        if not items_db:
            return self._build_response(
                message="[color=ff0000]DEBUG ERROR:[/color] Could not locate any item databases in the Resource Manager.", 
                turn_taken=False
            )

        # 2. Handle the "God Mode" keyword
        if target_name_str.lower() == "all":
            for item_id in items_db.keys():
                self.player.setdefault('inventory', []).append(item_id)
            return self._build_response(
                message=f"[color=00ff00]DEBUG:[/color] Added ALL {len(items_db)} items to your inventory.", 
                turn_taken=False,
                ui_events=[{"event_type": "refresh_ui"}] # Adjust event name to match your UI refresh hook
            )

        # 3. Parse optional quantity using regex (e.g., "5 bandages")
        quantity = 1
        match = re.match(r'^(\d+)\s+(.+)$', target_name_str)
        if match:
            quantity = int(match.group(1))
            target_name_str = match.group(2).strip()

        # 4. Omni-Lookup / Fuzzy Search
        search_query = target_name_str.lower()
        found_item_id = None
        
        # Pass A: Exact ID match
        if search_query in items_db:
            found_item_id = search_query
        # Pass B: ID match with spaces converted to underscores
        elif search_query.replace(' ', '_') in items_db:
            found_item_id = search_query.replace(' ', '_')
        # Pass C: Search the actual 'name' field inside the JSON
        else:
            for item_id, item_data in items_db.items():
                item_name = item_data.get('name', '').lower()
                # Try exact name match first
                if search_query == item_name:
                    found_item_id = item_id
                    break
                # Fallback to partial substring match (e.g., "stub" matches "movie stub")
                elif search_query in item_name:
                    found_item_id = item_id
                    break

        # 5. Injection & Feedback
        if not found_item_id:
            return self._build_response(
                message=f"[color=ffaa00]DEBUG:[/color] Could not find any item matching '{target_name_str}'.", 
                turn_taken=False
            )

        for _ in range(quantity):
            self.player.setdefault('inventory', []).append(found_item_id)

        display_name = items_db[found_item_id].get('name', found_item_id)
        
        return self._build_response(
            message=f"[color=00ff00]DEBUG:[/color] Injected {quantity}x [{display_name}] into inventory.", 
            turn_taken=False,
            ui_events=[{"event_type": "refresh_ui"}]  # Force the inventory UI to update immediately
        )