from typing import Optional

from fd_terminal.utils import color_text, normalize_text


class InteractionMixin:
        # --- NPC commands ---

    def _resolve_talk_target(self, target_name_str: str, room_id: str) -> Optional[dict]:
        """Finds the NPC in the room or falls back to scraping master data."""
        npc = self._find_npc_in_room(target_name_str, room_id)
        
        # Omni-Lookup & String Resolver
        if not npc or isinstance(npc, str):
            npcs_master = self.resource_manager.get_data('npcs', {})
            search_key = npc if isinstance(npc, str) else target_name_str
            master_data = npcs_master.get(search_key) or npcs_master.get('npcs', {}).get(search_key, {})
            if master_data:
                return master_data
                
        return npc if isinstance(npc, dict) else None

    def _get_companion_fallback_node(self, npc: dict, room_id: str) -> dict:
        """Generates dynamic ambient barks for companions if no explicit dialogue exists."""
        room_data = self.get_room_data(room_id) or {}
        room_tags = room_data.get('tags', [])
        
        ambient_barks = npc.get('ambient_barks', {})
        chosen_bark = "I'm right behind you. Lead the way." # Default
        
        import random
        for tag in room_tags:
            if tag in ambient_barks and ambient_barks[tag]:
                chosen_bark = random.choice(ambient_barks[tag])
                break # Stop at the first matching tag
                
        return {
            "text": f"They glance around. {chosen_bark}",
            "options": []
        }

    def _filter_dialogue_options(self, raw_options: list) -> list:
        """Filters options based on items, logic conditions, and system flags."""
        valid_options = []
        
        # 1. Get raw flag data
        raw_flags = self.player.get('flags', {})
        if not isinstance(raw_flags, dict):
            raw_flags = {}
            self.player['flags'] = raw_flags
        interaction_flags = getattr(self, 'interaction_flags', set())
        inventory = self.player.get('inventory', [])
        
        # 2. Inventory helper
        def player_has(item_id):
            if isinstance(inventory, dict):
                return item_id in inventory
            for i in inventory:
                if isinstance(i, str) and i == item_id: return True
                if isinstance(i, dict) and i.get('id') == item_id: return True
            return False

        for opt in raw_options:
            # Item requirements
            if 'requires_no_item' in opt and player_has(opt['requires_no_item']): continue
            if 'requires_item' in opt and not player_has(opt['requires_item']): continue
            if 'requires_items' in opt:
                reqs = opt['requires_items'] if isinstance(opt['requires_items'], list) else [opt['requires_items']]
                if not all(player_has(r) for r in reqs): continue
                    
            # --- THE BULLETPROOF FLAG CHECK ---
            req_flag = opt.get('requires_flag')
            if req_flag:
                # Check the temporary interaction set
                has_temp_flag = req_flag in interaction_flags
                
                # Check the persistent flags (handles both dict and set types)
                has_perm_flag = False
                if isinstance(raw_flags, dict):
                    has_perm_flag = bool(raw_flags.get(req_flag))
                elif isinstance(raw_flags, (set, list)):
                    has_perm_flag = req_flag in raw_flags
                
                if not has_temp_flag and not has_perm_flag:
                    continue 
            # ----------------------------------
                    
            if 'condition' in opt and not self._npc_condition_met(opt['condition']):
                continue
                
            valid_options.append(opt)
            
        return valid_options

    def _command_talk(self, target_name_str: str) -> dict:
        """Main interaction pipeline: resolves NPC, generates outputs, and pushes UI events."""
        try:
            target_name_str = (target_name_str or "").strip()
            if not target_name_str:
                return self._build_response(message="Talk to whom?", turn_taken=False)

            room_id = self.player.get('location')

            # 1. Resolve Target
            npc = self._resolve_talk_target(target_name_str, room_id)
            if not npc:
                return self._build_response(message=f"You don't see {target_name_str} here.", turn_taken=False)

            # 2. Record Meeting
            npc_name = npc.get('name', target_name_str).title()
            met_npcs = self.player.setdefault('met_npcs', [])
            is_first_meeting = npc_name not in met_npcs
            if is_first_meeting:
                met_npcs.append(npc_name)

            # 3. Resolve Dialogue State
            current_state = self._resolve_npc_dialogue_entry_state(npc, room_id, is_first_meeting=is_first_meeting)
            dialogue_states = npc.get('dialogue_states') or {}
            node = dialogue_states.get(current_state)

            # 4. Companion Fallback
            is_companion = npc.get('name', '').lower() == self.player.get('current_companion', '').lower()
            if not node and is_companion:
                node = self._get_companion_fallback_node(npc, room_id)

            if not node:
                 return self._build_response(message=f"{npc_name} has nothing to say right now.", turn_taken=True)

            # 5. Process Pre-Talk Actions (Side Effects)
            ui_events = []
            if 'on_talk_action' in node:
                try:
                    self._process_on_talk_action(npc, node, ui_events)
                    self._apply_on_talk_action(node['on_talk_action'])
                except StopIteration:
                    return self._build_response(message=f"You talk to {npc_name}.", turn_taken=True, ui_events=ui_events)

            # 6. Push Narrative Logic Forward
            next_state = node.get('next_state')
            if next_state:
                self._set_npc_state(npc, next_state)

            # 7. Build Filtered Output Options
            valid_options = self._filter_dialogue_options(node.get('options', []))
            
            # 8. Render Outputs
            text = node.get('text')
            if not text:
                self.logger.info(f"_command_talk: Silent node '{current_state}' triggered. Skipping popup.")
                return self._build_response(turn_taken=True, ui_events=ui_events)

            text = self._format_dynamic_text(text)
            
            # Dynamic text replacements
            if "$ticket_check_result$" in text:
                has_ticket = "movie_ticket" in self.player.get('inventory', [])
                text = text.replace(
                    "$ticket_check_result$",
                    "Ah, here you go.\n*You show Ron your ticket*" if has_ticket else "Uh, I think I lost it."
                )

            options_text = ""
            if valid_options:
                options_text = "\n\n[color=00ff00]Responses:[/color]"
                for i, opt in enumerate(valid_options):
                    formatted_opt_text = self._format_dynamic_text(opt.get('text', ''))
                    opt['text'] = formatted_opt_text  # Save formatted text back for context handling
                    options_text += f"\n{i+1}. {formatted_opt_text}"

            # Save Context for Option Selection later
            self.last_dialogue_context = {
                "npc_name": npc_name,
                "options": valid_options
            }

            ui_events.append({"event_type": "destroy_info_popup"})
            
            popup_event = {
                "event_type": "show_popup",
                "title": npc_name,
                "message": text + options_text
            }

            # Auto-Advance Logic
            if not valid_options and node.get('next_state'):
                self.logger.info(f"_command_talk: Auto-advance chain to '{node.get('next_state')}'.")
                popup_event['on_close_command'] = f"talk {npc.get('name', '')}"

            ui_events.append(popup_event)

            npc_display = color_text(npc_name, 'npc', self.resource_manager)
            log_message = f"\n{npc_display}: \"{text}\""

            return self._build_response(
                message=log_message,
                turn_taken=True,
                ui_events=ui_events
            )

        except Exception as e:
            self.logger.error(f"_command_talk: Unexpected error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong during the conversation.", turn_taken=True)
        
    def _command_respond(self, option_str: str) -> dict:
        """
        Choose a dialogue option. Logs the player's choice text, then triggers the NPC's reply.
        """
        try:
            option_str = (option_str or "").strip()
            self.logger.debug(f"_command_respond called with option_str='{option_str}'")

            # 1. Validate Input
            opt_num = self._parse_option_number(option_str)
            if opt_num is None:
                self.logger.warning(f"_command_respond: Invalid option number '{option_str}'.")
                return self._build_response(
                    message="Please specify a valid option number (e.g., 'respond 1').",
                    turn_taken=False
                )

            # 2. Get Context
            ctx = self.last_dialogue_context or {}
            npc_name = ctx.get("npc_name")
            options = ctx.get("options", [])
            if not npc_name or not options:
                self.logger.warning("_command_respond: No active conversation context.")
                return self._build_response(
                    message="There's no active conversation to respond to.",
                    turn_taken=False
                )
            if opt_num >= len(options) or opt_num < 0:
                self.logger.warning(f"_command_respond: Option {opt_num+1} out of range for options: {options}")
                return self._build_response(
                    message=f"That's not a valid option. Choose between 1 and {len(options)}.",
                    turn_taken=False
                )

            # 3. Get Selection
            selected = options[opt_num]
            target_state = selected.get("target_state")
            player_speech_text = selected.get("text", "...")  # Capture what the player said

            # 4. Execute Action Side-Effects
            if "on_talk_action" in selected:
                self.logger.info(f"_command_respond: Executing action for option {opt_num+1}")
                self._apply_on_talk_action(selected["on_talk_action"])

            if "on_select_action" in selected:
                self._process_on_select_action(selected["on_select_action"])

            # 5. Locate NPC and Transition
            room_id = self.player.get('location')
            npc = self._find_npc_in_room(npc_name, room_id)
            if not npc:
                self.logger.warning(f"_command_respond: NPC '{npc_name}' not found in room '{room_id}'. Clearing stale dialogue context.")
                self.last_dialogue_context = {}  # Wipe stale context so respond commands stop firing
                return self._build_response(
                    message="They are no longer here.",
                    turn_taken=True  # Consume the turn so the engine advances instead of looping
                )

            if not target_state:
                # If no state change (just an action), refresh current state
                if "on_talk_action" in selected:
                    response = self._command_talk(npc_name)
                else:
                    self.logger.warning(f"_command_respond: Option {opt_num+1} has no target_state.")
                    return self._build_response(
                        message="That option leads nowhere.",
                        turn_taken=True
                    )
            else:
                self.logger.info(f"_command_respond: Advancing NPC '{npc_name}' to state '{target_state}' via option {opt_num+1}.")
                self._set_npc_state(npc, target_state)
                response = self._command_talk(npc_name)

            # -- INJECT PLAYER DIALOGUE INTO LOG (The Fix) --
            # Prepend the player's text to the response messages so it appears BEFORE the NPC reply.
            # Format: "> I'm fine, thanks."
            player_log = f"[color=aaaaaa]> \"{player_speech_text}\"[/color]"
            if response.get('messages') is not None:
                response['messages'].insert(0, player_log)
            else:
                response['messages'] = [player_log]

            return response
        except Exception as e:
            self.logger.error(f"_command_respond: Unexpected error: {e}", exc_info=True)
            return self._build_response(
                message="Something went wrong while responding to the conversation.",
                turn_taken=True
            )
    
    def _command_unlock(self, target_name_str: str) -> dict:
        """
        Handles the 'unlock' command for exits (doors) and furniture.
        Injected with robust logging and error handling.
        """
        try:
            if not target_name_str:
                self.logger.info("_command_unlock: No target specified.")
                return self._build_response(message="Unlock what?", turn_taken=False)

            self.logger.debug(f"_command_unlock: Attempting to unlock '{target_name_str}'")
            current_room_id = self.player.get('location', '')
            current_room_data = self.get_room_data(current_room_id)
            if not current_room_data:
                self.logger.warning(f"_command_unlock: No data for current room '{current_room_id}'.")
                return self._build_response(message="You can't unlock anything here.", turn_taken=False)

            target_norm = normalize_text(target_name_str)
            available_keys = self._get_player_keys()
            if not available_keys:
                self.logger.info("_command_unlock: Player has no keys in inventory.")
                return self._build_response(message="You don't have any keys.", turn_taken=False)

            # Try to unlock an exit (door)
            exits = current_room_data.get('exits', {})
            for direction, dest in exits.items():
                try:
                    # Handle dict exits (lock info on the exit itself)
                    if isinstance(dest, dict):
                        dest_target = dest.get('target', '')
                        if normalize_text(direction) == target_norm or normalize_text(dest_target) == target_norm:
                            required_key = dest.get('unlocks_with')
                            if not dest.get('locked', False):
                                return self._build_response(
                                    message=f"The way {direction} is already unlocked.",
                                    turn_taken=False
                                )
                            if not required_key:
                                return self._build_response(
                                    message=f"That doesn't have a keyhole. Try forcing it.",
                                    turn_taken=False
                                )
                            # Convert to list if it isn't one already for uniform processing
                            if isinstance(required_key, list):
                                req_norms = [normalize_text(k) for k in required_key]
                            else:
                                req_norms = [normalize_text(required_key)]

                            key_found = None
                            for key_id, key_data in available_keys.items():
                                checks = [
                                    normalize_text(key_id),
                                    normalize_text(key_data.get("name", "")),
                                ]
                                checks.extend([normalize_text(u) for u in key_data.get("unlocks", [])])
                                
                                # If ANY of the required keys match this inventory item, or it's a master key
                                if any(req in checks for req in req_norms) or key_data.get("is_master_key"):
                                    key_found = key_id
                                    break
                            # --------------------------------------------------
                            if key_found:
                                dest['locked'] = False
                                self.logger.info(f"_command_unlock: Unlocked dict exit '{direction}' with '{key_found}'")
                                display = self._get_item_display_name(key_found)
                                return self._build_response(
                                    message=f"You unlock the way {direction} with the {display}.",
                                    turn_taken=True, success=True
                                )
                            else:
                                return self._build_response(
                                    message=f"You don't have the right key for that.",
                                    turn_taken=False
                                )
                    # Handle string exits (lock info on destination room)
                    elif normalize_text(direction) == target_norm:
                        exit_result = self._unlock_exit(direction, dest, available_keys)
                        if exit_result is not None:
                            return exit_result
                except Exception as e:
                    self.logger.error(f"_command_unlock: Error unlocking exit '{direction}': {e}", exc_info=True)
                    return self._build_response(
                        message=f"Something went wrong while trying to unlock the exit '{direction}'.",
                        turn_taken=False
                    )

            # Try to unlock furniture
            try:
                furniture_result = self._try_unlock_furniture(target_name_str, current_room_id, available_keys)
                if furniture_result is not None:
                    return furniture_result
            except Exception as e:
                self.logger.error(f"_command_unlock: Error unlocking furniture '{target_name_str}': {e}", exc_info=True)
                return self._build_response(
                    message=f"Something went wrong while trying to unlock the {target_name_str}.",
                    turn_taken=False
                )

            # Target not found
            self.logger.info(f"_command_unlock: Target '{target_name_str}' not found in exits or furniture.")
            return self._build_response(
                message=f"You don't see '{target_name_str}' here to unlock.",
                turn_taken=False
            )

        except Exception as e:
            self.logger.error(f"_command_unlock: Error unlocking '{target_name_str}': {e}", exc_info=True)
            return self._build_response(
                message=f"Something went wrong while trying to unlock {target_name_str}.",
                turn_taken=False
            )
        
    def _process_on_select_action(self, action: dict):
        effect = action.get("action_effect")
        if effect == "add_companion":
            name = action.get("companion_display_name") or action.get("companion_name", "")
            if name and name not in self.player.get("companions", []):
                self.player.setdefault("companions", []).append(name)
                # Insert into deaths list at current index + 1
                deaths_list = self.player.get("deaths_list", [])
                idx = self.player.get("deaths_list_index", 0) + 1
                if name.lower() not in [n.lower() for n in deaths_list]:
                    deaths_list.insert(idx, name)
                    self.player["deaths_list"] = deaths_list
                self.add_ui_event({"event_type": "show_message",
                    "message": f"\n[color=00ff00]{name} has joined your group.[/color]\n"})
                self.add_ui_event({"event_type": "refresh_context_actions"})