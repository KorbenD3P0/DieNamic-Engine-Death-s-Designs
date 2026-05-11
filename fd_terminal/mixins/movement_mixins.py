import random
from fd_terminal.utils import color_text

class MovementMixin:
    def _command_compass(self, target_str: str = None) -> dict:
        """
        Display the local area compass (3x3 grid).
        Calls the GUI-compatible 3x3 grid map generator.
        """
        try:
            map_string = self.get_gui_map_string()
            
            return {
                "message": map_string,
                "success": True,
                "turn_taken": False  # Checking compass shouldn't burn a turn
            }
        except Exception as e:
            self.logger.error(f"_command_compass: Error generating compass: {e}", exc_info=True)
            return {
                "message": "Your compass is unreadable right now.",
                "success": False,
                "turn_taken": False
            }

    def _command_map(self, target_str: str = None) -> dict:
        """
        Opens the full level map popup.
        """
        # Trigger UI event for popup
        self.add_ui_event({"event_type": "show_map_popup"})
        return {
            "message": "Checking map...",
            "success": True,
            "turn_taken": False
        }

    # --- The Rite of Passage ---
    def _command_move(self, direction: str) -> dict:
        """Core movement dispatcher."""
        current_room_id = self.player.get('location')
        exits = self.get_room_data(current_room_id).get('exits', {})

        def _can_player_leave_room(self, current_room_id):
            # 1. Find active MRI hazard
            mri_hazards = [h for h_id, h in self.hazard_engine.active_hazards.items()
                        if h.get('master_data', {}).get('id') == 'mri']
            
            if mri_hazards:
                mri = mri_hazards[0]
                state = mri.get('state')
                
                # Define the states where the magnetic seal is active
                # The key card shouldn't work while the electromagnets are haywire
                if state in ("field_active_doors_locked", "mri_wave_evaluator", "projectile_qte_active"):
                    return False, ("The door's electronic lock is screaming with static! "
                                "The MRI's magnetic field has effectively sealed the room shut. "
                                "You're not getting out until that machine stops!")
            
            return True, ""

        # --- THE ABSOLUTE ELEVATOR BYPASS ---
        from fd_terminal.utils import normalize_text
        norm_dir = normalize_text(direction)

        if current_room_id == "Elevator Car":
            dest_data = exits.get(direction)
            if not dest_data:
                for k, v in exits.items():
                    if normalize_text(k) == norm_dir:
                        dest_data = v; direction = k
                        break
            
            if not dest_data:
                floor_aliases = {
                    'floor 2': ['floor 2', 'l2', '2', 'upper', 'upper floor', 'floor2'],
                    'floor 1': ['floor 1', 'l1', '1', 'ground', 'ground floor', 'floor1'],
                    'basement': ['basement', 'b', '-1', 'floor -1'],
                    'out': ['out', 'leave', 'exit'],
                }
                for exit_key, v in exits.items():
                    k_norm = normalize_text(exit_key)
                    for canonical, aliases in floor_aliases.items():
                        if (k_norm in aliases or k_norm == canonical) and (norm_dir in aliases or norm_dir == canonical):
                            dest_data = v; direction = exit_key
                            break
                    if dest_data: break

            if dest_data:
                # 1. OUT COMMAND (VOID FIX)
                if norm_dir in ["out", "leave", "exit"]:
                    if self.player.get('elevator_transit_active'):
                        return self._build_response(message="The elevator is in motion. The doors are sealed.", turn_taken=False)
                    
                    # Safely fetch the tracked lobby door
                    target_room_id = self.player.get('elevator_door_open_to')
                    if target_room_id:
                        self.player.pop('elevator_transit_active', None)
                        return self._finalize_move(current_room_id, target_room_id)
                    return self._build_response(message="The doors won't open.", turn_taken=False)
                
                # 2. FLOOR BUTTON
                target_room_id = dest_data.get('target') if isinstance(dest_data, dict) else dest_data
                return getattr(self, '_handle_elevator_move')(direction, target_room_id)
        else:
            # We are OUTSIDE. If we are entering the elevator, log the lobby we came from!
            resolved = getattr(self, '_find_exit_by_target', lambda t, e: (None, None))(direction, exits)
            tgt = resolved[1].get('target_room') if len(resolved) > 1 and resolved[1] else None
            if tgt == "Elevator Car":
                self.player['elevator_door_open_to'] = current_room_id
        # ------------------------------------

        # Now we can safely let the Hazard Engine check for standard door blockages
        if getattr(self, 'hazard_engine', None):
            hazard_block = self._handle_hazard_move_block(direction, current_room_id, exits)
            if hazard_block and not hazard_block.get('success', True):
                return hazard_block

        try:
            direction = (direction or "").strip().lower()
            norm_dir = direction.strip().lower()
            if not direction:
                return self._build_response(message="Move where?", turn_taken=False)

            current_room_id = self.player.get('location', '')
            current_room = self.get_room_data(current_room_id)
            if not current_room:
                return self._build_response(message="You're nowhere?", turn_taken=False)

            exits = current_room.get('exits', {})

            # --- THE ELEVATOR FIX: Handle buttons BEFORE static exit validation ---
            if current_room_id == "Elevator Car":
                # Handle 'out/exit/leave' — only allowed when elevator is idle
                if norm_dir in ("out", "exit", "leave"):
                    if getattr(self, 'hazard_engine', None):
                        h_state = self.hazard_engine.get_hazard_state(
                            "elevator_freefall", "Elevator Car")
                        if h_state and h_state not in ("idle",):
                            return self._build_response(
                                message="The doors are sealed — the elevator is in motion!",
                                turn_taken=True, success=False
                            )
                    dest_room, new_floor = self._resolve_elevator_target(norm_dir)
                    if dest_room:
                        return self._finalize_move(current_room_id, dest_room)
                    # Fall through — 'out' not in exits means no lobby connection this direction
                    return self._build_response(
                        message="The doors won't open here.",
                        turn_taken=False, success=False
                    )

                # Floor button presses
                dest_room, new_floor = self._resolve_elevator_target(norm_dir)
                if dest_room:
                    if self.player.get('elevator_transit_active'):
                        return self._build_response(
                            message="The elevator is already moving.",
                            turn_taken=False, success=False
                        )
                    return self._handle_elevator_move(norm_dir)

                # Unknown direction inside elevator
                if norm_dir not in exits:
                    return self._build_response(
                        message="That button doesn't exist on this panel.",
                        turn_taken=False, success=False
                    )

            # 2. Enforce Dict-Exit Requirements (Items, Companions)
            exit_target = exits.get(direction)
            req_block = self._check_exit_requirements(exit_target)
            if req_block:
                return req_block

            # 3. Use the centralized door resolver
            resolved = self._resolve_exit(direction, exits)

            if resolved['exit_type'] == 'dynamic':
                return self._handle_dynamic_exit(resolved['_raw_ref'], direction, current_room_id)

            # 4. Handle Blocked/Locked Doors (and Auto-Unlock)
            if not resolved['can_pass']:
                auto_unlock_resp = self._attempt_auto_unlock(direction, exits, resolved, current_room_id)
                if auto_unlock_resp:
                    return auto_unlock_resp
                    
                from fd_terminal.utils import color_text
                return self._build_response(
                    message=color_text(resolved['lock_message'], "warning", self.resource_manager),
                    turn_taken=False
                )

            # 5. Handle Level Transitions or Standard Moves
            target_room = resolved.get('target_room') or ""
            target_str = str(target_room)
            
            # --- THE FIX: Let the dedicated router handle macros first! ---
            transition_resp = self._route_level_transition(target_str)
            if transition_resp:
                return transition_resp
            
            # Catch raw level IDs only if they weren't caught by the router
            if target_str.startswith('level_'):
                self.logger.info(f"Cross-Level Travel Detected! Transitioning to {target_str}")
                
                self.add_ui_event({
                    "event_type": "level_complete",
                    "level_name": "Leaving Area",
                    "narrative": "You start the engine and head to your next destination...",
                    "next_level_id": target_str,
                    "score": 0,
                    "turns_taken": 1,
                    "evidence_count": len(self.player.get('inventory', [])),
                    "evaded_hazards": []
                })
                
                return self._build_response(message="\n[color=00ff00]Leaving area...[/color]\n", turn_taken=True)

            return self._finalize_move(current_room_id, target_room)

        except Exception as e:
            self.logger.error(f"_command_move: Error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong.", turn_taken=False)


    # -------------------------------------------------------------------------
    # --- Movement Helpers ---
    # -------------------------------------------------------------------------

    def _check_exit_requirements(self, exit_target) -> dict:
        """Checks item and companion requirements for dictionary-based exits."""
        if not isinstance(exit_target, dict):
            return None

        from fd_terminal.utils import normalize_text

        req_item = exit_target.get('requires_item')
        req_flag = exit_target.get('requires_flag')

        if req_item:
            # Safely normalize the inventory set to check for the item (ignores spaces and punctuation)
            inv = {normalize_text(str(i)) for i in self.player.get('inventory', [])}
            if normalize_text(req_item) not in inv:
                msg = exit_target.get('locked_message', f"You need a {req_item} to go that way.")
                self.add_ui_event({"event_type": "show_popup", "title": "Locked", "message": msg})
                return self._build_response(message=msg, turn_taken=False, success=False)

        if req_flag:
            # Check both the dedicated flags set AND top-level boolean triggers
            flags = self.player.get('flags', set())
            if req_flag not in flags and not self.player.get(req_flag):
                msg = exit_target.get('locked_message', "You can't go that way yet.")
                self.add_ui_event({"event_type": "show_popup", "title": "Locked", "message": msg})
                return self._build_response(message=msg, turn_taken=False, success=False)

        # Companion requirement
        req_npc = exit_target.get('requires_companion')
        if req_npc:
            # Apply the same safety normalization to companion names
            active_companions = {normalize_text(str(c)) for c in self.player.get('companions', [])}
            if normalize_text(req_npc) not in active_companions:
                return self._build_response(
                    message=exit_target.get('locked_message') or f"You refuse to leave {req_npc} behind. You need to find them first.",
                    turn_taken=False,
                    success=False
                )
                
        return None


    def _route_level_transition(self, target_room: str):
        """Intercepts specific exit targets to dynamically route level transitions."""
        if not isinstance(target_room, str):
            return None

        # ── 1. THE GAMBLE EVALUATOR (Must happen first) ──────────────────────────
        # Catch the raw level guess before the direct level bypass triggers
        if 'blind_gamble_active' in getattr(self, 'interaction_flags', set()):
            if target_room.startswith('level_') and not target_room.startswith('LEVEL_TRANSITION_'):
                actual_next_level = self._resolve_dynamic_hunt_level()
                
                if target_room == actual_next_level:
                    self.logger.info("Player guessed correctly! Proceeding safely.")
                    self.interaction_flags.remove('blind_gamble_active')
                    self.interaction_flags.add('learned_deaths_list')
                    self.player['learned_deaths_list'] = True
                else:
                    self.logger.warning(f"Player guessed wrong. Transitioning to {target_room}, actual target dies.")
                    deaths_list = self.player.get('deaths_list', [])
                    current_idx = self.player.get('deaths_list_index', 0)
                    
                    if current_idx < len(deaths_list):
                        actual_target_name = deaths_list[current_idx]
                        self.logger.warning(f"Killing {actual_target_name} off-screen.")
                        
                        self.player.setdefault('npc_status', {})[actual_target_name.lower()] = 'dead'
                        self.player['deaths_list_index'] += 1
                        self.player['hub_fallback_triggered'] = False

        # ── 2. DIRECT LEVEL BYPASS ───────────────────────────────────────────────
        if target_room.startswith('level_') and not target_room.startswith('LEVEL_TRANSITION_'):
            self.logger.info(f"Direct level transition detected: '{target_room}'")
            return self._trigger_dynamic_transition(target_room)

        # ── 3. SENTINEL GUARD ────────────────────────────────────────────────────
        if not target_room.startswith("LEVEL_TRANSITION_"):
            return None

        # ── 4. STATIC SENTINEL ROUTES ────────────────────────────────────────────
        if target_room == "LEVEL_TRANSITION_FINALE":
            return self._trigger_dynamic_transition("level_finale")

        if target_room == "LEVEL_TRANSITION_POLICE":
            self.player['police_status'] = 'surrendered'
            return self._trigger_dynamic_transition("level_police_station")

        if target_room == "LEVEL_TRANSITION_POLICE_FOUGHT":
            self.player['police_status'] = 'fought'
            self.player['is_fugitive'] = True
            self.player['hp'] = max(1, self.player.get('hp', 100) - 15)
            self.add_ui_event({
                "event_type": "show_message",
                "message": "You fought your way through the cordon, taking some bruising hits..."
            })
            return self._trigger_dynamic_transition("level_police_fought")

        if target_room == "LEVEL_TRANSITION_BLUDWORTH":
            if self.player.get("visited_bludworth"):
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "Nothing Left",
                    "message": "You already learned everything you could from Bludworth's house. There's nothing left there for you."
                })
                return self._build_response(message="You've already been there.", turn_taken=False)
            
            self.player["visited_bludworth"] = True
            return self._trigger_dynamic_transition("level_house")

        if target_room == "LEVEL_TRANSITION_HUB":
            self.logger.info("Returning to the Hub (Your Car).")
            self.player['global_dread'] = self.player.get('global_dread', 0) + 0.1
            self.player.pop('hub_police_event_triggered', None)
            return self._trigger_dynamic_transition("level_hub")

        # ── 5. WORKPLACE LEVEL COMPLETION ────────────────────────────────────────
        if target_room == "LEVEL_TRANSITION_TRIGGER":
            deaths_list = self.player.get('deaths_list', [])
            npc_status = self.player.get('npc_status', {})
            deaths_idx = self.player.get('deaths_list_index', 0)

            current_target = None
            for i in range(deaths_idx, len(deaths_list)):
                candidate = deaths_list[i]
                if candidate.lower() == 'player':
                    continue
                if npc_status.get(candidate.lower()) in ('alive', 'injured'):
                    current_target = candidate
                    self.player['deaths_list_index'] = i + 1
                    break

            if current_target:
                self.add_ui_event({
                    "event_type": "show_message",
                    "message": f"\n[color=00ff00]You've done what you can for {current_target.title()}. Get back to the car.[/color]\n"
                })

            self.player['global_dread'] = self.player.get('global_dread', 0) + 0.1
            return self._trigger_dynamic_transition("level_hub")

        # ── 6. DYNAMIC HUNT ROUTER ───────────────────────────────────────────────
        if target_room == "LEVEL_TRANSITION_DYNAMIC_HUNT":
            deaths_list = self.player.get('deaths_list', [])
            npc_status = self.player.get('npc_status', {})

            next_target = None
            for candidate in deaths_list:
                if candidate.lower() == 'player':
                    continue
                
                status = npc_status.get(candidate.lower())
                visited_flag = f"visited_workplace_{candidate.lower()}"
                
                if status in ('alive', 'injured') and not self.player.get(visited_flag):
                    next_target = candidate
                    self.player[visited_flag] = True
                    break

            if next_target:
                workplaces = self.player.get('npc_workplaces', {})
                target_job_data = workplaces.get(next_target.lower())
                
                if target_job_data:
                    job_level_id = target_job_data.get('level_id')
                    workplace_name = target_job_data.get('workplace_name', 'their workplace')
                    
                    if job_level_id:
                        self.add_ui_event({
                            "event_type": "show_message",
                            "message": f"\n[color=ff0000]Death's Design points to {next_target.title()}. They are currently at {workplace_name}. Get there before it's too late.[/color]\n"
                        })
                        return self._trigger_dynamic_transition(job_level_id)

            # Endgame Check
            remaining_alive = [
                n for n in deaths_list
                if n.lower() != 'player'
                and npc_status.get(n.lower(), 'alive') in ('alive', 'injured')
                and not self.player.get(f"visited_workplace_{n.lower()}")
            ]

            if not remaining_alive:
                self.logger.info("Deaths list exhausted — routing to finale.")
                self.add_ui_event({
                    "event_type": "show_popup",
                    "title": "The End of the Line",
                    "message": "There is no one left to warn. Everyone on Death's list has been accounted for.\n\nYou are the only name left.\n\nIt is time to prepare for the end."
                })
                return self._trigger_dynamic_transition("level_finale")

            # Fallback
            self.player.pop('hub_police_event_triggered', None)
            return self._trigger_dynamic_transition("level_1", start_room="Hospital Parking Garage")

        # ── 7. GENERIC SENTINEL FALLBACK ─────────────────────────────────────────
        next_level_id = target_room.replace("LEVEL_TRANSITION_", "", 1).lower()
        check_id = next_level_id.replace("level_", "")
        
        if not self.resource_manager.get_data(f"rooms_level_{check_id}", None):
            self.logger.error(f"_route_level_transition: No rooms data for derived level '{next_level_id}' (from '{target_room}'). Returning player to hub.")
            self.player.pop('hub_police_event_triggered', None)
            return self._trigger_dynamic_transition("level_1", start_room="Hospital Parking Garage")
            
        return self._trigger_dynamic_transition(next_level_id)

    def _handle_elevator_move(self, direction: str, dest_room: str = None) -> dict:
        self.logger.info(f"[ELEVATOR DEBUG] Initiating move to '{direction}'. Target resolved to: '{dest_room}'")
        
        if self.player.get('elevator_transit_active'):
            return self._build_response(message="The elevator is already in motion.", turn_taken=False, success=False)

        if not dest_room:
            self.logger.warning(f"[ELEVATOR DEBUG] Button '{direction}' has no target defined in the room JSON.")
            return self._build_response(message="That button doesn't seem to work.", turn_taken=False, success=False)

        # Lock in the destination
        self.player['pending_elevator_dest'] = dest_room
        self.player['elevator_door_open_to'] = dest_room  # <-- NEW: Updates the door memory to the destination!
        self.player['elevator_transit_active'] = True

        # Set hazard to 'moving' state silently so it doesn't print double messages
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
        """
        The single authoritative function that moves the player to the elevator destination.
        """
        self.logger.info("[ELEVATOR DEBUG] finalize_elevator_arrival called.")
        
        dest_room = self.player.pop('pending_elevator_dest', None)
        new_floor = self.player.pop('pending_elevator_floor', None)
        self.player.pop('elevator_transit_active', None)
        
        self.logger.info(f"[ELEVATOR DEBUG] Popped destinations -> Room: '{dest_room}', Floor: '{new_floor}'")

        # Cancel any pending timer (safety)
        timer = getattr(self, '_elevator_timer', None)
        if timer:
            try:
                self.logger.debug("[ELEVATOR DEBUG] Cancelling legacy Kivy Clock timer.")
                timer.cancel()
            except Exception:
                pass
            self._elevator_timer = None

        if not dest_room or dest_room not in self.current_level_rooms_world_state:
            self.logger.error(f"[ELEVATOR DEBUG] FATAL: dest_room '{dest_room}' is invalid or missing from world state! Failsafe triggered.")
            return self._build_response(
                message="The elevator doors open... onto a solid brick wall. Something went terribly wrong.",
                turn_taken=False
            )

        self.logger.info(f"[ELEVATOR DEBUG] Safely ejecting player into '{dest_room}'.")

        # Move player
        self.player['location'] = dest_room
        self.player.setdefault('visited_rooms', set()).add(dest_room)
        if new_floor is not None:
            self.player['elevator_current_floor'] = new_floor

        # Reset hazard
        if getattr(self, 'hazard_engine', None):
            self.logger.debug("[ELEVATOR DEBUG] Resetting elevator hazard to idle and triggering destination ambushes.")
            self.hazard_engine.reset_elevator_hazard("Elevator Car")
            ambush_cons = self.hazard_engine.trigger_ambushes_for_room(dest_room)
            for c in ambush_cons:
                self.handle_hazard_consequence(c)

        # Audio
        if getattr(self, 'audio_manager', None):
            try:
                self.audio_manager.play_sfx("elevator_end")
            except Exception:
                pass

        room_desc = self._get_rich_room_description(dest_room)

        self.logger.info("[ELEVATOR DEBUG] Arrival complete. Pushing final room description to UI.")
        return self._build_response(
            message=f"*DING* — The doors slide open.\n\n{room_desc}",
            turn_taken=False,
            success=True,
            ui_events=[{"event_type": "refresh_map"}]
        )

    def _elevator_autonomous_roll(self, dt):
        """LEGACY / DEAD CODE"""
        self.logger.warning("[ELEVATOR DEBUG] WARNING: Legacy '_elevator_autonomous_roll' was called! This is dead code and should not be executing.")
        # Keeping your logic inside just in case it's secretly wired up to a legacy event somewhere

    def _deliver_elevator_arrival(self, *args):
        """LEGACY / DEAD CODE"""
        self.logger.warning("[ELEVATOR DEBUG] WARNING: Legacy '_deliver_elevator_arrival' was called! This is dead code and should not be executing.")

    def _handle_hazard_move_block(self, direction, current_room_id, exits):
        if self.hazard_engine:
            return self._process_hazard_move_interactions(direction, current_room_id, exits)
        return None

    def _process_hazard_move_interactions(self, direction, current_room_id, exits):
        """
        Helper: Process hazard move rules before moving.
        Returns response dict if move is blocked or handled.
        """
        try:
            self.logger.debug(f"_process_hazard_move_interactions: direction='{direction}', current_room_id='{current_room_id}'")
            hazards_master = getattr(self, 'resource_manager', None).get_data('hazards', {}) if getattr(self, 'resource_manager', None) else {}
            active_hazards = getattr(self, 'hazard_engine').get_active_hazards_for_room(current_room_id)
            for hazard_key in active_hazards:
                h_def = hazards_master.get(hazard_key, {})
                move_rules = h_def.get('player_interaction', {}).get('move', [])
                hazard_state = getattr(self, 'hazard_engine').get_hazard_state(hazard_key, current_room_id)
                for rule in move_rules:
                    on_dirs = rule.get('on_direction', []) or rule.get('on_target_name', [])
                    if isinstance(on_dirs, str):
                        on_dirs = [on_dirs]
                        
                    if direction in on_dirs and (not rule.get('requires_hazard_state') or hazard_state in rule.get('requires_hazard_state', [])):
                        msg = rule.get('message', "Something stops you.")
                        qte_def = rule.get('qte_on_move', None)
                        target_state = rule.get('target_state', None)
                        
                        if qte_def and getattr(self, 'qte_engine', None):
                            ctx = qte_def.copy()
                            ctx['qte_source_hazard_id'] = hazard_key
                            ctx['next_state_success'] = rule.get('next_state_on_qte_success')
                            ctx['next_state_failure'] = rule.get('next_state_on_qte_failure')
                            ctx['message'] = msg
                            self.player['qte_active'] = True
                            self.qte_engine.start_qte(qte_def.get('qte_type', 'button_mash'), ctx)
                            return self._build_response(message=msg, turn_taken=True)

                        if target_state:
                            getattr(self, 'hazard_engine').set_hazard_state_by_type(current_room_id, hazard_key, target_state)

                        should_block = rule.get('blocks_move', rule.get('blocks_action_success', True))
                        
                        if should_block:
                            self.logger.info(f"_process_hazard_move_interactions: Blocking move with message: {msg}")
                            return self._build_response(message=msg, turn_taken=True, success=False)
            return None
        except Exception as e:
            self.logger.error(f"_process_hazard_move_interactions: Error: {e}", exc_info=True)
            return self._build_response(message="A hazard interferes with your movement.", turn_taken=False, success=False)

    def _setup_hub_car(self):
        """Dynamically rewrites the Hub Car text and injects EXITS based on state!"""
        room = self.current_level_rooms_world_state.get("Your Car")
        if not room:
            return

        companion = self.player.get('companion_id')
        covered_in_blood = self.player.get('status_effects', {}).get('covered_in_blood', False)

        if companion and covered_in_blood:
            narrative = (
                f"The engine idles. The silence in the car is suffocating. You are both covered in blood. "
                f"{companion} is staring blankly out the window, trembling, trying to process the wet, horrific sound of the target dying. "
                f"You failed. Death is still hunting. Pick the next destination."
            )
        elif companion and not covered_in_blood:
            narrative = (
                f"The engine idles. {companion} is in the passenger seat, nervously checking their phone for news updates. "
                f"'Where to next?' they ask quietly. Choose the next destination."
            )
        elif covered_in_blood:
            narrative = (
                f"The engine idles. You grip the steering wheel with bloody hands, shivering. "
                f"You can't unsee what just happened. You failed. But you can't stay parked forever."
            )
        else:
            narrative = (
                f"The engine idles quietly. The streetlights wash over the dashboard. You're safe in here for the moment, "
                f"but you can't stay parked forever. You need to decide where to drive next."
            )

        room["first_entry_text"] = narrative

        # --- THE FIX: Dynamically inject the exits into the room! ---
        inventory = {str(i).lower() for i in self.player.get('inventory', [])}
        flags = self.player.get('flags', set())

        # 1. Properly target the player dictionary for the deaths list flag (Your fix)
        knows_list = 'learned_deaths_list' in flags or bool(self.player.get('learned_deaths_list'))
        
        # 2. Safely define has_key to prevent a NameError crash
        has_key = any("coroner" in item and "key" in item for item in inventory)

        # ── Finale escape valve ──────────────────────────────────────────────
        # If the player has visited Bludworth AND been to the police station,
        # and has any remaining turns, surface the finale regardless of list status.
        # This prevents endless hub loops.
        visited_bludworth = self.player.get('visited_bludworth', False)
        visited_police    = self.player.get('police_status') is not None
        turns_taken       = self.player.get('actions_taken', 0)

        # Build the dynamic exits dictionary
        dynamic_exits = {
            "surrender to police": {"target": "LEVEL_TRANSITION_POLICE"}
        }

        if has_key and not visited_bludworth:
            dynamic_exits["drive to Bludworth's"] = {"target": "LEVEL_TRANSITION_BLUDWORTH"}

        if knows_list:
            # Check if there are still living targets to hunt
            npc_status = self.player.get('npc_status', {})
            alive_huntable = [
                n for n in self.player.get('deaths_list', [])
                if n.lower() != 'player'
                and npc_status.get(n.lower(), 'alive') in ('alive', 'injured')
                and not self.player.get(f"visited_workplace_{n.lower()}")
            ]
            if alive_huntable:
                dynamic_exits["find next target"] = {"target": "LEVEL_TRANSITION_DYNAMIC_HUNT"}
            else:
                # List done — this is the end
                dynamic_exits["prepare for the end"] = {"target": "LEVEL_TRANSITION_FINALE"}

        if visited_bludworth and visited_police and turns_taken >= 10:
            if "prepare for the end" not in dynamic_exits and "find next target" not in dynamic_exits:
                dynamic_exits["prepare for the end"] = {"target": "LEVEL_TRANSITION_FINALE"}

        dynamic_exits["fight through police"] = {"target": "LEVEL_TRANSITION_POLICE_FOUGHT"}

        room['exits'] = dynamic_exits
        # ------------------------------------------------------------

        if covered_in_blood:
            self.player.setdefault('status_effects', {})['covered_in_blood'] = False

    def _handle_dynamic_exit(self, destination, direction, current_room_id):
        """Handles setting up the elevator move."""
        if destination.get('dynamic_destination'):
            if self._is_elevator_room(current_room_id):
                # --- FIX: Let the player walk out without triggering an elevator transit! ---
                if direction in ["out", "leave", "exit"]:
                    dest_room, _ = getattr(self, '_resolve_elevator_target', lambda d: (None, None))(direction)
                    if dest_room:
                        return self._finalize_move(current_room_id, dest_room)
                    else:
                        return self._build_response(message="The doors won't open.", turn_taken=False)
                
                # Otherwise, it's a floor button, proceed with transit
                return self._handle_elevator_move(direction)
            else:
                return self._build_response(message="That way adjusts dynamically and can't be used here.", turn_taken=False)

    def _finalize_move(self, current_room_id: str, destination: str) -> dict:
        """Finalizes the player's movement, updates room states, and triggers room-specific events."""
        old_room_data = self.get_room_data(current_room_id) or {}
        new_room_data = self.get_room_data(destination) or {}

        visited_rooms = self.player.setdefault('visited_rooms', set())
        is_first_visit = destination not in visited_rooms

        # 1. Update Core Location
        self.player['location'] = destination

        # --- THE FIX: Cure Companion Amnesia! ---
        interacted_npc = self.player.get('current_interacted_npc')
        companions = self.player.get('companions', [])

        # Only wipe the conversation memory if the NPC is NOT following you
        if interacted_npc not in companions and interacted_npc != self.player.get('companion_id'):
            self.player.pop('current_interacted_npc', None)
            self.player.pop('current_conversation_state', None)

        # Always kill the UI respond buttons so they don't linger across rooms
        if hasattr(self, 'last_dialogue_context'):
            self.last_dialogue_context = {}

        self.player.setdefault('visited_rooms', set()).add(destination)

        # --- Companions Follow You! ---
        companion = self.player.get('companion_id')
        if companion:
            self._move_npc(companion, destination)

        # 2. Check for Narrative Hub Intercepts (e.g., Police Arrest)
        hub_intercept = self._check_hub_intercepts(destination)
        if hub_intercept:
            return hub_intercept

        # 3. Move Survivor Group & Dynamic Recruitment
        companions = self.player.get('companions', [])
        self._move_survivor_group(companions, old_room_data, new_room_data)

        if not companions:
            self._intercept_next_target(new_room_data)
            companions = self.player.get('companions', [])  # Refresh list if someone was recruited

        # 4. Check Level/Premonition Completion
        premonition_result = self._check_premonition_complete()
        if premonition_result:
            return premonition_result

        # 5. Handle Specific Room Mechanics (Elevator, Hazards)
        self._process_elevator_state(current_room_id, destination)
        self._trigger_room_ambushes(destination)

        # 6. Build Output (Description & UI Events)
        message = self._generate_arrival_narrative(destination, companions)
        ui_events = []
        
        # --- THE FIX: JSON-Safe Deduplication & Self-Healing ---
        # 1. Grab the tracker. If it's a corrupted string or missing, force it to be a clean list.
        popups = self.player.get('shown_entry_popups', [])
        if not isinstance(popups, list):
            # Self-heal from sets or corrupted JSON strings
            popups = list(popups) if isinstance(popups, set) else []
            self.player['shown_entry_popups'] = popups

        first_text = new_room_data.get('first_entry_text')
        already_shown = destination in popups
        
        if first_text and not already_shown:
            # Lock it immediately using .append() instead of .add()
            self.player['shown_entry_popups'].append(destination)
            
            ui_events.append(
                self._make_first_entry_popup_event(destination, first_text)
            )

        return self._build_response(message=message, turn_taken=True, success=True, ui_events=ui_events)

    # -------------------------------------------------------------------------
    # --- Movement Finalization Helpers ---
    # -------------------------------------------------------------------------

    def _trigger_dynamic_transition(self, next_level_id: str, start_room: str = None) -> dict:
        """
        Safely packages player stats and forces a dynamic level transition
        without relying on static level_requirements.json paths.
        """
        self.logger.info(f"Dynamically transitioning to level: {next_level_id} (room: {start_room})")
        
        # Prevent the engine from auto-firing duplicate completion events
        self.player['level_complete_flag'] = False
        
        # Build the payload for the InterLevelScreen
        completion_event = {
            "event_type": "level_complete",
            "level_name": self.player.get('location', 'Unknown Area'),
            "narrative": "You slip away into the shadows, following the trail of Death's design...",
            "next_level_id": next_level_id,
            "score": self.player.get('score', 0),
            "turns_taken": self.player.get('actions_taken', 0),
            "evidence_count": len(self.player.get('inventory', [])),
            "evaded_hazards": self.player.get('evaded_hazards', [])
        }
        
        if start_room:
            completion_event["next_start_room"] = start_room
            
        # Fire the event directly into the UI queue
        self.add_ui_event(completion_event)
        
        # Return a standard movement response to satisfy the _command_move pipeline
        return self._build_response(message="Leaving the area...")

    def _check_hub_intercepts(self, destination: str):
        """
        Intercepts movement into the Hub (Hospital Parking Garage / Your Car).
        Refactored into a Traffic Cop dispatcher.
        """
        if destination not in ["Hospital Parking Garage", "Your Car"]:
            return None
    
        # 1. Once per game
        if self.player.get('hub_police_event_triggered'):
            return None
        self.player['hub_police_event_triggered'] = True
        
        # 2. Gather World State
        hub_state = self._gather_hub_state()
        
        # 3. Inject Dynamic Exits into the Room
        self._inject_hub_exits(destination, hub_state)
        room_data = self.current_level_rooms_world_state.get(destination)
        
        # 4. RNG CHECK: 40% chance you slip away completely undetected
        import random
        if random.random() < 0.40:
            self.logger.info("Hub Police Event bypassed by RNG. Player is safe.")
            
            # --- UNLOCK THE GHOST ACHIEVEMENT ---
            if getattr(self, 'achievements_system', None):
                self.achievements_system.unlock("ghost_in_the_garage")

            if room_data:
                room_data['first_entry_text'] = ""
                
            self.add_ui_event({
                "event_type": "show_message", 
                "message": "[color=aaaaaa]You hear sirens converging on the hospital, but you reach your car before they spot you. Safe... for now.[/color]"
            })
            
            # Print the newly injected movement options immediately
            exits_prompt = "\n".join([f"  [color=00ff00][b]'move {ext}'[/b][/color]" for ext in room_data.get('exits', {}).keys()])
            return self._build_response(
                message=f"\n[color=00FF00]Your options:[/color]\n{exits_prompt}",
                turn_taken=False
            )

        # 5. THE SIRENS (Red & Blue UI Flashes)
        self.add_ui_event({"event_type": "screen_flash", "color": "ff0000", "duration": 0.4, "opacity": 0.4})
        self.add_ui_event({"event_type": "show_message", "message": "Red and blue lights suddenly flood the concrete..."})
        self.add_ui_event({"event_type": "screen_flash", "color": "0044ff", "duration": 0.4, "opacity": 0.4})
    
        # 6. Build Narrative
        full_narrative = self._build_hub_narrative(hub_state)
        
        if room_data:
            room_data['first_entry_text'] = ""
    
        self.logger.info(f"Hub Police Event fired. State: {hub_state}")
    
        self.add_ui_event({
            "event_type": "show_popup",
            "title": "The Crossroads",
            "message": full_narrative,
            "priority": 1000
        })
    
        return self._build_response(
            message="The parking garage. The decision point. Choose carefully.",
            turn_taken=False
        )

    # ---------------------------------------------------------
    # --- Hub Intercept Helpers ---
    # ---------------------------------------------------------

    def _gather_hub_state(self) -> dict:
        """Pulls and normalizes all data needed for the Hub decision point."""
        from fd_terminal.utils import normalize_text
        
        inventory = {normalize_text(str(i)) for i in self.player.get('inventory', [])}
        interaction_flags = getattr(self, 'interaction_flags', set())
        role_map = self.player.get('_premonition_role_map', {})
        npc_status = self.player.get('npc_status', {})
        
        auth_figure = role_map.get('authority_figure', 'The lead officer')
        auth_status = npc_status.get(auth_figure.lower(), 'alive')
        
        deaths_list = self.player.get('deaths_list', [])
        alive_targets = [
            n for n in deaths_list
            if n.lower() != 'player' and npc_status.get(n.lower(), 'alive') in ('alive', 'injured')
        ]
        
        return {
            "auth_figure": auth_figure,
            "auth_is_dead": auth_status in ('dead', 'deceased', 'missing'),
            "has_key": 'bludworths house key' in inventory,
            "already_visited": self.player.get('visited_bludworth', False),
            "knows_list": 'learned_deaths_list' in interaction_flags,
            "alive_targets": alive_targets,
            "hunt_target_count": len(alive_targets),
            "witnessed_deaths": self.player.get('witnessed_deaths', []),
            "offscreen_deaths": self.player.get('offscreen_casualties', [])
        }

    def _inject_hub_exits(self, destination: str, state: dict):
        # Build the base dynamic exits dictionary FIRST
        dynamic_exits = {
            "surrender to police": {"target": "LEVEL_TRANSITION_POLICE"}
        }

        # Bludworth: only if player has key and hasn't visited yet
        if state["has_key"] and not state["already_visited"]:
            dynamic_exits["drive to bludworth's"] = {"target": "LEVEL_TRANSITION_BLUDWORTH"}

        # Dynamic hunt / finale
        if state["knows_list"]:
            if state["hunt_target_count"] > 0:
                dynamic_exits["find next target"] = {"target": "LEVEL_TRANSITION_DYNAMIC_HUNT"}
            else:
                dynamic_exits["prepare for the end"] = {"target": "LEVEL_TRANSITION_FINALE"}

        # Finale escape valve — AFTER building the dict, not before
        visited_bludworth = self.player.get('visited_bludworth', False)
        visited_police = self.player.get('police_status') is not None
        turns_taken = self.player.get('actions_taken', 0)
        if visited_bludworth and visited_police and turns_taken >= 10:
            if "prepare for the end" not in dynamic_exits and "find next target" not in dynamic_exits:
                dynamic_exits["prepare for the end"] = {"target": "LEVEL_TRANSITION_FINALE"}

        dynamic_exits["fight through police"] = {"target": "LEVEL_TRANSITION_POLICE_FOUGHT"}

        room_data = self.current_level_rooms_world_state.get(destination)
        if room_data:
            room_data['exits'] = dynamic_exits
            room_data['description'] = "You are in the parking garage. Choose your next destination carefully."
            
    def _build_hub_narrative(self, state: dict) -> str:
        """Constructs the cinematic text and option list for the UI popup."""
        reported_key = '_hub_offscreen_deaths_reported'
        include_offscreen_report = bool(state.get("offscreen_deaths")) and not self.player.get(reported_key)
        if include_offscreen_report:
            self.player[reported_key] = True

        # 1. Opening Scene
        if state["auth_is_dead"]:
            opening = (
                "Before you can catch your breath, flashing red and blue lights paint the concrete.\n\n"
                f"'We're looking into the death of [color=ff0000]{state['auth_figure']}[/color]. "
                "We have some questions — down at the precinct.' "
                "The officers are not treating you like a survivor. They're treating you like a suspect.\n\nDid they NOT just see what happened??"
            )
        elif state["witnessed_deaths"] or include_offscreen_report:
            opening = (
                "The parking garage hits you with cold night air and colder scrutiny. "
                "Spotlights snap on from every angle.\n\n"
                f"'[color=ff0000]{state['auth_figure'].upper()}[/color] WANTS TO SEE YOU — NOW.'\n\n"
                "People died. That makes you a person of interest, whether you saved anyone or not."
            )
        else:
            opening = (
                "You step into the parking garage. The night air is cold. "
                "Somewhere nearby, a police radio crackles.\n\n"
                "They haven't spotted you yet — but they will. Make your next move count."
            )
    
        # 2. Options Block
        options_lines = ["\n\n[color=00FF00]Your options:[/color]"]
        options_lines.append(
            " [color=0000FF] [b]'Surrender to police'[/b][/color] — Go in for questioning. "
            "Safe for now, but they'll hold you. Time is the one thing you don't have."
        )
    
        if state["has_key"] and not state["already_visited"]:
            options_lines.append(
                " [color=800080] [b]'Drive to Bludworth's'[/b][/color] — You have his key. "
                "His house might hold the answers Death doesn't want you to find."
            )
        elif state["already_visited"]:
            options_lines.append("  [color=555555]Bludworth's house — you've already been. There's nothing left there for you.[/color]")
        else:
            options_lines.append("  [color=555555]Bludworth's house — you don't have his key. You could try forcing your way in, but that burns time and makes noise.[/color]")
    
        if state["knows_list"] and state["hunt_target_count"] > 0:
            next_name = state["alive_targets"][0].title() if state["alive_targets"] else "someone"
            options_lines.append(
                f" [color=00ff00] [b]'Find next target'[/b][/color] — {next_name} is still alive. "
                f"{'You know the order. Get there first.' if state['hunt_target_count'] > 1 else 'They are the last one. Do not let Death finish what it started.'}"
            )
        elif state["knows_list"] and state["hunt_target_count"] == 0:
            options_lines.append("  [color=ff4444]The list is empty. Everyone on it is already gone. You are the only name left.[/color]")
        else:
            options_lines.append("  [color=555555]Find next target — you don't know who is next. Running blind is a death sentence. You need the list.[/color]")
    
        has_any_safe_exit = (state["has_key"] and not state["already_visited"]) or (state["knows_list"] and state["hunt_target_count"] > 0)
        fight_hint = "Not recommended — you'll take damage and burn your cover." if has_any_safe_exit else "It may be your only way out. You won't come out clean, but you'll come out."
        
        options_lines.append(
            f" [color=00ff00] [b]'Fight through police'[/b][/color] — Force your way through the cordon. {fight_hint}"
        )

        if include_offscreen_report:
            offscreen_names = [str(name).title() for name in state.get("offscreen_deaths", [])]
            if offscreen_names:
                options_lines.append(
                    " [color=ff4444]Offscreen deaths reported:[/color] " + ", ".join(offscreen_names)
                )
    
        return opening + "\n" + "\n".join(options_lines)

    def _move_survivor_group(self, companions: list, old_room_data: dict, new_room_data: dict):
        """Removes the survivor group from the old room and places them in the new room."""
        for comp_name in companions:
            if 'npcs' in old_room_data:
                old_room_data['npcs'] = [
                    n for n in old_room_data['npcs'] 
                    if (n.get('name', n) if isinstance(n, dict) else n).lower() != comp_name.lower()
                ]
            new_room_data.setdefault('npcs', []).append(comp_name)

    def _intercept_next_target(self, new_room_data: dict):
        """Automatically tethers the next target on Death's List if they are in the destination room."""
        room_npcs = new_room_data.get('npcs', [])
        deaths_list = self.player.get('deaths_list', [])
        deaths_idx = self.player.get('deaths_list_index', 0)
        
        next_target = None
        for i in range(deaths_idx, len(deaths_list)):
            candidate = deaths_list[i]
            if candidate.lower() != 'player' and self.player.get('npc_status', {}).get(candidate.lower()) in ('alive', 'injured'):
                next_target = candidate
                break
                
        if next_target:
            for npc in room_npcs:
                npc_name = npc.get('name', '') if isinstance(npc, dict) else npc
                if npc_name.lower() == next_target.lower():
                    comp_str = next_target.title()
                    self.player.setdefault('companions', []).append(comp_str)
                    
                    self.add_ui_event({
                        "event_type": "show_message",
                        "message": f"\n[color=55aaff]You spot {comp_str} here. They immediately make a beeline for you.[/color]\n"
                    })
                    break

    def _process_elevator_state(self, current_room_id: str, destination: str):
        """Updates internal floor tracking and resets elevator hazards upon exit."""
        if destination == "Elevator Car":
            prev_floor = self._elevator_floor_from_room(current_room_id)
            self.player['elevator_current_floor'] = prev_floor
            
        leaving_elevator = current_room_id == "Elevator Car" and destination != "Elevator Car"
        if leaving_elevator and getattr(self, 'hazard_engine', None):
            self.hazard_engine.reset_elevator_hazard("Elevator Car")

    def _trigger_room_ambushes(self, destination: str):
        """Fires off immediate hazard ambushes upon entering a new room."""
        if getattr(self, 'hazard_engine', None):
            ambushes = self.hazard_engine.trigger_ambushes_for_room(destination)
            if ambushes:
                for cons in ambushes:
                    self.handle_hazard_consequence(cons)

    def _generate_arrival_narrative(self, destination: str, companions: list) -> str:
        """Builds the room description and applies random companion flavor text."""
        import random
        
        message = self._get_rich_room_description(destination)
        
        if companions and random.random() < 0.15:
            # Ensures we pick a random survivor from the group to provide the flavor text
            c_color = color_text(random.choice(companions), 'npc', self.resource_manager)
            phrases = [
                f"\n\n{c_color} follows closely behind you.",
                f"\n\n{c_color} keeps pace with you, watching the shadows.",
                f"\n\nYou hear {c_color}'s footsteps right behind you."
            ]
            message += random.choice(phrases)
            
        return message