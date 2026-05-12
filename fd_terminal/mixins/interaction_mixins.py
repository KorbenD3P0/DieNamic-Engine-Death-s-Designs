from fd_terminal.utils import color_text, normalize_text


class InteractionMixin:
        # --- NPC commands ---

    def _command_talk(self, target_name_str: str) -> dict:

        """
        Talk to an NPC: logs the conversation to the output panel AND shows a popup.
        """
        try:
            target_name_str = (target_name_str or "").strip()
            if not target_name_str:
                return self._build_response(message="Talk to whom?", turn_taken=False)

            room_id = self.player.get('location')

            # 1. Find them in the room
            npc = self._find_npc_in_room(target_name_str, room_id)

            # --- THE FIX: Omni-Lookup & String Resolver ---
            # If the engine only found a string ID (because it was in npcs_present)
            # OR if it missed it entirely, scrape the master NPC data.
            if not npc or isinstance(npc, str):
                npcs_master = self.resource_manager.get_data('npcs', {})
                search_key = npc if isinstance(npc, str) else target_name_str

                # Omni-Lookup: check root, then nested 'npcs' block
                master_data = npcs_master.get(search_key) or npcs_master.get('npcs', {}).get(search_key, {})
                if master_data:
                    npc = master_data

            if not npc or isinstance(npc, str):
                return self._build_response(message=f"You don't see {target_name_str} here.", turn_taken=False)

            # --- THE FIX: Record that you met them! ---
            npc_name = npc.get('name', target_name_str).title()
            if npc_name not in self.player.setdefault('met_npcs', []):
                self.player['met_npcs'].append(npc_name)
            # ------------------------------------------
            npc_name = npc.get('name', target_name_str).title()
            met_npcs = self.player.setdefault('met_npcs', [])
            is_first_meeting = npc_name not in met_npcs      # ← compute BEFORE appending
            if is_first_meeting:
                met_npcs.append(npc_name)

            # Pass is_first_meeting into the resolver
            current_state = self._resolve_npc_dialogue_entry_state(npc, room_id, is_first_meeting=is_first_meeting)
            # 2. Resolve State
            dialogue_states = npc.get('dialogue_states') or {}
            node = dialogue_states.get(current_state)

            # --- THE FIX: Engine-level Companion Fallback with Tags ---
            is_companion = npc.get('name', '').lower() == self.player.get('current_companion', '').lower()

            if not node and is_companion:
                # 1. Get room tags
                room_data = self.get_room_data(room_id) or {}
                room_tags = room_data.get('tags', [])
                
                # 2. Check if NPC has thematic barks for these tags
                ambient_barks = npc.get('ambient_barks', {})
                chosen_bark = "I'm right behind you. Lead the way." # Default
                
                for tag in room_tags:
                    if tag in ambient_barks and ambient_barks[tag]:
                        import random
                        chosen_bark = random.choice(ambient_barks[tag])
                        break # Stop at the first matching tag we find
                
                # 3. Dynamically generate the fallback node
                node = {
                    "text": f"They glance around. {chosen_bark}",
                    "options": []
                }

            # 3. Apply Side Effects
            ui_events = []
            if 'on_talk_action' in node:
                try:
                    self._process_on_talk_action(npc, node, ui_events)
                    self._apply_on_talk_action(node['on_talk_action'])
                except StopIteration:
                    return self._build_response(message=f"You talk to {npc.get('name')}.", turn_taken=True, ui_events=ui_events)

            # 4. Handle State Transition
            next_state = node.get('next_state')
            if next_state:
                self._set_npc_state(npc, next_state)

            # 5. Option Filtering
            raw_options = node.get('options', [])
            valid_options = []

            # Inventory helper
            inventory = self.player.get('inventory', [])
            def player_has(item_id):
                if isinstance(inventory, dict):
                    return item_id in inventory
                for i in inventory:
                    if isinstance(i, str) and i == item_id:
                        return True
                    if isinstance(i, dict) and i.get('id') == item_id:
                        return True
                return False

            for opt in raw_options:
                if 'requires_no_item' in opt and player_has(opt['requires_no_item']):
                    continue
                if 'requires_item' in opt and not player_has(opt['requires_item']):
                    continue
                if 'requires_items' in opt:
                    reqs = opt['requires_items'] if isinstance(opt['requires_items'], list) else [opt['requires_items']]
                    if not all(player_has(r) for r in reqs):
                        continue
                if 'requires_flag' in opt and opt['requires_flag'] not in self.interaction_flags:
                    continue
                if 'condition' in opt and not self._npc_condition_met(opt['condition']):
                    continue
                valid_options.append(opt)

            # 6. Build Outputs
            text = node.get('text')

            # --- PATCH: Silent Node Support ---
            if not text:
                self.logger.info(f"_command_talk: Silent node '{current_state}' triggered. Skipping popup.")
                return self._build_response(
                    turn_taken=True,
                    ui_events=ui_events
                )

            # --- FIX: Format the base text from the node ---
            text = self._format_dynamic_text(text)

            # Dynamic text replacement
            if "$ticket_check_result$" in text:
                has_ticket = "movie_ticket" in self.player.get('inventory', [])
                text = text.replace(
                    "$ticket_check_result$",
                    "Ah, here you go.\n*You show Ron your ticket*" if has_ticket else "Uh, I think I lost it."
                )

            # Construct Options Text
            options_text = ""
            if valid_options:
                options_text = "\n\n[color=00ff00]Responses:[/color]"
                for i, opt in enumerate(valid_options):
                    formatted_opt_text = self._format_dynamic_text(opt.get('text', ''))
                    opt['text'] = formatted_opt_text
                    options_text += f"\n{i+1}. {formatted_opt_text}"

            # Save context
            self.last_dialogue_context = {
                "npc_name": npc.get('name'),
                "options": valid_options
            }

            # Queue "Destroy Old Popup" event
            ui_events.append({"event_type": "destroy_info_popup"})

            # Build New Popup
            popup_event = {
                "event_type": "show_popup",
                "title": npc.get('name', 'NPC'),
                "message": text + options_text
            }

            # Auto-Advance Logic
            if not valid_options and node.get('next_state'):
                self.logger.info(f"_command_talk: Auto-advance chain to '{node.get('next_state')}'.")
                popup_event['on_close_command'] = f"talk {npc.get('name', '')}"

            ui_events.append(popup_event)

            # Log to history
            npc_display = color_text(npc.get('name', 'NPC'), 'npc', self.resource_manager)
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