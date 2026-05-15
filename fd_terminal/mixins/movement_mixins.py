import random
from typing import Optional
from fd_terminal.utils import color_text, TraceLogger, normalize_text

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
    # =============================================================================
    # REFACTORED _command_move  —  drop these methods into MovementMixin
    # =============================================================================
    # WHAT CHANGED vs the original:
    #
    #   _command_move            — Reduced to a clean 8-step pipeline (~40 lines).
    #                              All logic delegated to named helpers below.
    #
    #   NEW: _move_validate_input        — Normalise direction, locate current room.
    #   NEW: _move_check_mri_seal        — MRI magnetic-lock room-exit guard.
    #                                      (was a dead nested def that was never called)
    #   NEW: _move_check_hazard_blocks   — Wraps _handle_hazard_move_block +
    #                                      the per-hazard active-state loop.
    #   NEW: _move_resolve_elevator      — Both elevator bypass blocks merged into one.
    #   NEW: _move_track_elevator_entry  — Records lobby room when entering elevator.
    #   NEW: _move_match_exit            — Case-insensitive exit key lookup.
    #   NEW: _move_handle_locked_exit    — Auto-unlock attempt + dict-lock fallback.
    #   NEW: _move_handle_transition     — Level-transition routing + raw level_id guard.
    #
    #   PRESERVED UNCHANGED:
    #     _check_exit_requirements, _route_level_transition, _handle_elevator_move,
    #     _handle_hazard_move_block, _process_hazard_move_interactions,
    #     _handle_dynamic_exit, _finalize_move  (all still called the same way)
    # =============================================================================

    # ---------------------------------------------------------------------------
    # MAIN DISPATCHER
    # ---------------------------------------------------------------------------

    def _command_move(self, direction: str) -> dict:
        """
        Core movement dispatcher.

        Pipeline:
        1. Validate input & locate room
        2. MRI magnetic-seal check
        3. Elevator intercept (bypass normal pipeline when inside/entering)
        4. Hazard block checks
        5. Fuzzy exit match
        6. Exit requirements (item / flag / companion)
        7. Door resolution (lock / dynamic / auto-unlock)
        8. Level transition or standard finalized move
        """
        try:
            # ── 1. Validate input ────────────────────────────────────────────────
            direction, norm_dir, current_room_id, current_room, exits = \
                self._move_validate_input(direction)
            if current_room is None:
                return self._build_response(message="Move where?", turn_taken=False)

            # ── 2. MRI magnetic-seal ─────────────────────────────────────────────
            mri_block = self._move_check_mri_seal(current_room_id)
            if mri_block:
                return mri_block

            # ── 3. Elevator intercept ────────────────────────────────────────────
            elev_resp = self._move_resolve_elevator(direction, norm_dir, current_room_id, exits)
            if elev_resp is not None:
                return elev_resp

            # Record lobby when stepping INTO the elevator from outside
            self._move_track_elevator_entry(direction, current_room_id, exits)

            # ── 4. Hazard blocks ─────────────────────────────────────────────────
            hazard_block = self._move_check_hazard_blocks(direction, current_room_id, exits)
            if hazard_block:
                return hazard_block

            # ── 5. Fuzzy exit match ──────────────────────────────────────────────
            matched_key, matched_val = self._move_match_exit(direction, exits)
            exit_target = matched_val if matched_key is not None else exits.get(direction)

            # ── 6. Exit requirements ─────────────────────────────────────────────
            req_block = self._check_exit_requirements(exit_target)
            if req_block:
                return req_block

            # ── 7. Door resolution ───────────────────────────────────────────────
            resolved = self._resolve_exit(direction, exits)

            if resolved['exit_type'] == 'dynamic':
                return self._handle_dynamic_exit(resolved['_raw_ref'], direction, current_room_id)

            if not resolved['can_pass']:
                return self._move_handle_locked_exit(
                    direction, exits, resolved, exit_target, current_room_id
                )

            # ── 8. Transition or standard move ───────────────────────────────────
            target_room = resolved.get('target_room') or ""
            return self._move_handle_transition(target_room, current_room_id)

        except Exception as e:
            self.logger.error(f"_command_move: Error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong.", turn_taken=False)


    # ---------------------------------------------------------------------------
    # STEP 1  —  Input validation
    # ---------------------------------------------------------------------------

    def _move_validate_input(self, direction: str):
        """
        Normalise direction string and resolve the player's current room.

        Returns (direction, norm_dir, current_room_id, current_room, exits).
        current_room is None when direction is blank or the player has no room.
        """
        direction = (direction or "").strip().lower()
        norm_dir  = direction
        if not direction:
            return direction, norm_dir, None, None, {}

        current_room_id = self.player.get('location', '')
        current_room    = self.get_room_data(current_room_id)
        if not current_room:
            return direction, norm_dir, current_room_id, None, {}

        exits = current_room.get('exits', {})
        return direction, norm_dir, current_room_id, current_room, exits


    # ---------------------------------------------------------------------------
    # STEP 2  —  MRI magnetic-seal check
    # ---------------------------------------------------------------------------

    def _move_check_mri_seal(self, current_room_id: str):
        """
        Return a blocking response if an active MRI hazard has sealed the room.
        Returns None when the room is safe to leave.

        (This was previously a nested def _can_player_leave_room that was defined
        but never actually called — it is now wired into the pipeline.)
        """
            # States in which the MRI's field physically prevents the doors from opening.
        _MRI_LOCKED_STATES = frozenset({
            "field_active_doors_locked",
            "mri_wave_evaluator",
            "projectile_qte_active",
        })
        if not hasattr(self, 'hazard_engine'):
            return None

        for h_id, h in self.hazard_engine.active_hazards.items():
            if h.get('master_data', {}).get('id') == 'mri':
                if h.get('state') in _MRI_LOCKED_STATES:
                    return self._build_response(
                        message=(
                            "The door's electronic lock is screaming with static! "
                            "The MRI's magnetic field has effectively sealed the room shut. "
                            "You're not getting out until that machine stops!"
                        ),
                        turn_taken=False,
                    )
        return None


    # ---------------------------------------------------------------------------
    # STEP 3  —  Elevator intercept
    # ---------------------------------------------------------------------------

    def _move_resolve_elevator(
        self,
        direction: str,
        norm_dir:  str,
        current_room_id: str,
        exits: dict,
    ):
        """
        Handle all movement that originates from *inside* the Elevator Car.

        Returns a response dict if the move was handled, or None to fall through
        to the normal pipeline.

        Consolidates the two separate elevator bypass blocks from the original.
        """
        # Canonical alias table — maps normalised user input → exit key group.
        _ELEVATOR_FLOOR_ALIASES: dict[str, list[str]] = {
            'floor 2':  ['floor 2', 'l2', '2', 'upper', 'upper floor', 'floor2'],
            'floor 1':  ['floor 1', 'l1', '1', 'ground', 'ground floor', 'floor1'],
            'basement': ['basement', 'b', '-1', 'floor -1'],
            'out':      ['out', 'leave', 'exit'],
        }
        _ELEVATOR_EXIT_WORDS = frozenset({'out', 'leave', 'exit'})
        if current_room_id != "Elevator Car":
            return None

        # ── A. Resolve which exit key the player intended ────────────────────────
        dest_data = exits.get(direction)

        if not dest_data:
            for k, v in exits.items():
                if normalize_text(k) == norm_dir:
                    dest_data = v
                    direction = k
                    break

        if not dest_data:
            for exit_key, v in exits.items():
                k_norm = normalize_text(exit_key)
                for canonical, aliases in _ELEVATOR_FLOOR_ALIASES.items():
                    if (k_norm in aliases or k_norm == canonical) and \
                    (norm_dir in aliases or norm_dir == canonical):
                        dest_data = v
                        direction = exit_key
                        break
                if dest_data:
                    break

        # ── B. Dispatch based on intent ──────────────────────────────────────────
        if norm_dir in _ELEVATOR_EXIT_WORDS:
            # Guard: doors sealed while moving (hazard state check takes priority)
            if getattr(self, 'hazard_engine', None):
                h_state = self.hazard_engine.get_hazard_state("elevator_freefall", "Elevator Car")
                if h_state and h_state not in ("idle",):
                    return self._build_response(
                        message="The doors are sealed — the elevator is in motion!",
                        turn_taken=True, success=False,
                    )

            if self.player.get('elevator_transit_active'):
                return self._build_response(
                    message="The elevator is in motion. The doors are sealed.",
                    turn_taken=False,
                )

            # Exit to the tracked lobby floor
            target_room_id = self.player.get('elevator_door_open_to')
            if target_room_id:
                self.player.pop('elevator_transit_active', None)
                return self._finalize_move(current_room_id, target_room_id)

            # _resolve_elevator_target as second-chance (covers edge cases)
            dest_room, _ = self._resolve_elevator_target(norm_dir)
            if dest_room:
                return self._finalize_move(current_room_id, dest_room)

            return self._build_response(message="The doors won't open.", turn_taken=False)

        # Floor button press
        dest_room, _ = self._resolve_elevator_target(norm_dir)
        if dest_room:
            if self.player.get('elevator_transit_active'):
                return self._build_response(
                    message="The elevator is already moving.",
                    turn_taken=False, success=False,
                )
            return self._handle_elevator_move(direction,
                                            dest_data.get('target') if isinstance(dest_data, dict) else dest_data)

        # dest_data present but not a floor alias — fall through to normal pipeline
        if dest_data:
            target_room_id = dest_data.get('target') if isinstance(dest_data, dict) else dest_data
            return self._handle_elevator_move(direction, target_room_id)

        # Truly unknown button
        return self._build_response(
            message="That button doesn't exist on this panel.",
            turn_taken=False, success=False,
        )


    # ---------------------------------------------------------------------------
    # STEP 3b  —  Track elevator entry (called when OUTSIDE the elevator)
    # ---------------------------------------------------------------------------

    def _move_track_elevator_entry(self, direction: str, current_room_id: str, exits: dict):
        """
        When the player moves INTO the Elevator Car from outside, record which
        room they came from so the 'out' command knows where to return them.
        """
        resolved_pair = getattr(
            self, '_find_exit_by_target', lambda t, e: (None, None)
        )(direction, exits)

        tgt = None
        if len(resolved_pair) > 1 and resolved_pair[1]:
            tgt = resolved_pair[1].get('target_room')

        if tgt == "Elevator Car":
            self.player['elevator_door_open_to'] = current_room_id


    # ---------------------------------------------------------------------------
    # STEP 4  —  Hazard block checks
    # ---------------------------------------------------------------------------

    def _move_check_hazard_blocks(self, direction: str, current_room_id: str, exits: dict):
        """
        Return a blocking response if any active hazard prevents leaving the room.
        Returns None when movement is unobstructed.

        Combines:
        • _handle_hazard_move_block  (data-driven move rules from hazards JSON)
        • Per-hazard active-state guard (blocks move while any non-dormant hazard
            is active — the 'patch integration' loop from the original)
        """
        if not hasattr(self, 'hazard_engine'):
            return None

        # Data-driven rules (direction-specific, from hazards.json player_interaction.move)
        hazard_block = self._handle_hazard_move_block(direction, current_room_id, exits)
        if hazard_block and not hazard_block.get('success', True):
            return hazard_block

        # Generic active-state guard
        get_hazards = getattr(self.hazard_engine, 'get_active_hazards_for_room', None)
        get_data    = getattr(self.hazard_engine, 'get_hazard_data', None)
        if not (get_hazards and get_data):
            return None

        safe_states = {'dormant', 'off', 'safe', 'neutralized', None}
        for hazard_key in get_hazards(current_room_id):
            h_data = get_data(hazard_key, current_room_id) or {}
            if h_data.get('state') not in safe_states:
                return self._build_response(
                    message=f"You can't leave! You are currently dealing with a "
                            f"{hazard_key.replace('_', ' ')}!",
                    turn_taken=False,
                )
        return None


    # ---------------------------------------------------------------------------
    # STEP 5  —  Fuzzy case-insensitive exit match
    # ---------------------------------------------------------------------------

    def _move_match_exit(self, direction: str, exits: dict):
        """
        Case-insensitive scan of exit keys.
        Returns (matched_key, matched_value) or (None, None).
        Catches UI-generated direction strings that differ only in case/whitespace.
        """
        target = direction.lower().strip()
        for exit_key, exit_val in exits.items():
            if exit_key.lower() == target:
                return exit_key, exit_val
        return None, None


    # ---------------------------------------------------------------------------
    # STEP 7b  —  Locked-exit handling
    # ---------------------------------------------------------------------------

    def _move_handle_locked_exit(
        self,
        direction:      str,
        exits:          dict,
        resolved:       dict,
        exit_target,
        current_room_id: str,
    ) -> dict:
        """
        Called when _resolve_exit says the player cannot pass.

        Attempts auto-unlock first; if that fails, checks for a dict-exit with a
        multi-key requirement; finally returns the lock message.
        """
        # Auto-unlock (consumes key silently if present)
        auto_unlock_resp = self._attempt_auto_unlock(direction, exits, resolved, current_room_id)
        if auto_unlock_resp:
            return auto_unlock_resp

        # Dict-exit with array-key support (patch integration)
        if isinstance(exit_target, dict) and exit_target.get('locked'):
            required_key = exit_target.get('unlocks_with')
            if isinstance(required_key, list):
                has_key = all(k in self.player.get('inventory', []) for k in required_key)
            else:
                has_key = required_key in self.player.get('inventory', [])
            if not has_key:
                return self._build_response(
                    message="The way is locked. You need a key.",
                    turn_taken=False,
                )

        return self._build_response(
            message=color_text(resolved['lock_message'], "warning", self.resource_manager),
            turn_taken=False,
        )


    # ---------------------------------------------------------------------------
    # STEP 8  —  Transition or finalize
    # ---------------------------------------------------------------------------

    def _move_handle_transition(self, target_room: str, current_room_id: str) -> dict:
        """
        Route level transitions (LEVEL_TRANSITION_* macros and raw level_ IDs)
        or finalize a standard same-level room move.
        """
        target_str = str(target_room)

        # Named-macro and direct-level router (handles all LEVEL_TRANSITION_* keys)
        transition_resp = self._route_level_transition(target_str)
        if transition_resp:
            return transition_resp

        # Catch raw level IDs that slipped past the router (safety net)
        if target_str.startswith('level_'):
            self.logger.info(f"Cross-Level Travel Detected! Transitioning to {target_str}")
            self.add_ui_event({
                "event_type":    "level_complete",
                "level_name":    current_room_id,
                "narrative":     "You slip away into the shadows, following the trail of Death's design...",
                "next_level_id": target_str,
                "score":         0,
                "turns_taken":   1,
                "evidence_count": len([
                    i for i in self.player.get('inventory', [])
                    if 'evidence' in str(i)
                ]),
                "evaded_hazards":  [],
                "next_start_room": None,
            })
            self.player['pending_level_transition'] = target_str
            return self._build_response(
                message="\n[color=00ff00]Leaving area...[/color]\n",
                turn_taken=True,
            )

        return self._finalize_move(current_room_id, target_room)


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
            flags = self.player.get('flags', {})
            if not isinstance(flags, dict):
                flags = {}
                self.player['flags'] = flags
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
        # Catches gamble exits in BOTH formats:
        #   raw level_id:  'level_hotel'                      (from _setup_gamble_exits)
        #   sentinel:      'LEVEL_TRANSITION_LEVEL_HOTEL'     (legacy / belt-and-suspenders)
        #
        # Normalise to raw level_id for comparison against _resolve_dynamic_hunt_level.
        if 'blind_gamble_active' in getattr(self, 'interaction_flags', set()):
            # Derive a normalised level_id from either format
            guessed_level = None
            if target_room.startswith('level_') and not target_room.startswith('LEVEL_TRANSITION_'):
                guessed_level = target_room
            elif target_room.startswith('LEVEL_TRANSITION_LEVEL_'):
                # Strip the double prefix to recover the raw id
                guessed_level = target_room.replace('LEVEL_TRANSITION_', '', 1).lower()
    
            if guessed_level:
                actual_next_level = self._resolve_dynamic_hunt_level()
    
                if guessed_level == actual_next_level:
                    self.logger.info(
                        f"Blind Gamble: Player guessed correctly! "
                        f"'{guessed_level}' == '{actual_next_level}'. Proceeding safely."
                    )
                    self.interaction_flags.discard('blind_gamble_active')
                    self.interaction_flags.add('learned_deaths_list')
                    self.player['learned_deaths_list'] = True
                    # Fall through — normal transition to the correct level below
                else:
                    self.logger.warning(
                        f"Blind Gamble: Player guessed wrong. "
                        f"Chose '{guessed_level}', actual was '{actual_next_level}'. "
                        f"Killing actual target off-screen."
                    )
                    deaths_list  = self.player.get('deaths_list', [])
                    current_idx  = self.player.get('deaths_list_index', 0)
                    if current_idx < len(deaths_list):
                        actual_target_name = deaths_list[current_idx]
                        self.player.setdefault('npc_status', {})[actual_target_name.lower()] = 'dead'
                        self.player['deaths_list_index'] = current_idx + 1
                        self.player.pop('hub_fallback_triggered', None)
                        self.add_ui_event({
                            "event_type": "show_message",
                            "message": (
                                f"\n[color=ff0000]You went to the wrong place. "
                                f"{actual_target_name.title()} is dead.[/color]\n"
                            )
                        })
                    # Fall through — still transition to the guessed level (wrong one)
                    target_room = guessed_level  # ensure step 2 below uses the normalised id
    
        # ── 2. DIRECT LEVEL BYPASS ───────────────────────────────────────────────
        if target_room.startswith('level_') and not target_room.startswith('LEVEL_TRANSITION_'):
            self.logger.info(f"Direct level transition detected: '{target_room}'")
            return self._trigger_dynamic_transition(target_room)
    
        # ── 3. SENTINEL GUARD ────────────────────────────────────────────────────
        if not target_room.startswith("LEVEL_TRANSITION_"):
            return None
    
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

        if target_room == "LEVEL_TRANSITION_CAR_CHASE":
            # Triggered when an NPC panics and flees — set them as the chase target
            fleeing_npc = self.player.get('current_hunt_target', '')
            if fleeing_npc:
                self.player.setdefault('npc_status', {})[fleeing_npc.lower()] = 'fleeing'
                self.add_ui_event({
                    "event_type": "show_message",
                    "message": (
                        f"\\n[color=ff4444]{fleeing_npc.title()} has bolted. "
                        f"Get to your car — NOW.[/color]\\n"
                    )
                })
            return self._trigger_dynamic_transition("level_car_chase")

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
            companion_id = self.player.get('companion_id', '').lower()

            current_target = None
            for i in range(deaths_idx, len(deaths_list)):
                candidate = deaths_list[i]
                cand_lower = candidate.lower()
                
                if cand_lower == 'player':
                    continue
                    
                # --- THE FIX: Ignore the active companion ---
                if cand_lower == companion_id:
                    self.player[f"visited_workplace_{cand_lower}"] = True
                    continue
                    
                if npc_status.get(cand_lower) in ('alive', 'injured'):
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
            companion_id = self.player.get('companion_id', '').lower()

            next_target = None
            for candidate in deaths_list:
                cand_lower = candidate.lower()
                if cand_lower == 'player':
                    continue
                
                # --- THE FIX: Ignore the active companion ---
                if cand_lower == companion_id:
                    self.player[f"visited_workplace_{cand_lower}"] = True
                    continue
                
                status = npc_status.get(cand_lower)
                visited_flag = f"visited_workplace_{cand_lower}"
                
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
                and n.lower() != companion_id  # Ensure companion doesn't block the finale
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
        """
        Initiates an elevator transit.
        Resolves the destination internally — callers do NOT need to pass dest_room.
        Sets transit lock flags, queues the UI timer, and silently moves the hazard
        to 'moving' state to suppress double-messages from process_turn.
        """
        self.logger.info(f"[ELEVATOR DEBUG] Initiating move to '{direction}'")
    
        if self.player.get('elevator_transit_active'):
            return self._build_response(
                message="The elevator is already in motion.",
                turn_taken=False, success=False
            )
    
        # Always resolve internally — never rely on the caller's dest_room parameter.
        # (The parameter is kept for call-site compatibility but is intentionally ignored.)
        dest_room, new_floor = self._resolve_elevator_target(direction)
        if not dest_room:
            self.logger.warning(
                f"[ELEVATOR DEBUG] Button '{direction}' failed to resolve to a destination."
            )
            return self._build_response(
                message="That button doesn't seem to work.",
                turn_taken=False, success=False
            )
    
        # Lock in the destination — all three keys required:
        #   pending_elevator_dest  → finalize_elevator_arrival reads this to move the player
        #   pending_elevator_floor → finalize_elevator_arrival updates floor tracking
        #   elevator_door_open_to  → _move_resolve_elevator 'out' command reads this on exit
        self.player['pending_elevator_dest']  = dest_room
        self.player['pending_elevator_floor'] = new_floor
        self.player['elevator_door_open_to']  = dest_room
        self.player['elevator_transit_active'] = True
    
        # Silently move hazard to 'moving' state.
        # suppress_entry_effects=True prevents the state's on-enter consequences from
        # firing here — process_elevator_arrival owns the escalation decision.
        # NOTE: process_turn's Active Hazard Bypass will allow autonomous progression
        # from 'moving' on the very next turn. The elevator_freefall hazard JSON MUST
        # have duration_in_state >= 1 on the 'moving' state so that process_turn cannot
        # escalate to 'shaking' on turn 0 (the same turn the button is pressed).
        if getattr(self, 'hazard_engine', None):
            hid = self.hazard_engine.get_hazard_instance_id_by_type(
                "Elevator Car", "elevator_freefall"
            )
            if hid:
                self.hazard_engine.set_hazard_state(
                    hid, "moving", suppress_entry_effects=True
                )
    
        self.add_ui_event({"event_type": "refresh_map"})
        self.add_ui_event({"event_type": "schedule_transit", "duration": 4.0})
        self.add_ui_event({
            "event_type": "show_message",
            "message": "\n[color=aaaaaa]The elevator doors slide shut. The car begins to move...[/color]\n"
        })
    
        self.logger.info("[ELEVATOR DEBUG] Protected UI timer injected into queue.")
        return self._build_response(message="", turn_taken=False, success=True)
    
    
    def process_elevator_arrival(self) -> dict:
        """
        Called by the UI timer after schedule_transit expires.
        Owns the escalation decision: rolls whether the elevator 'shakes' mid-transit,
        suspends arrival if a QTE is triggered, and finalizes arrival on safe passage.
    
        Sequence:
        Safe:     timer fires → state==moving → chance miss → finalize_elevator_arrival
        Hazard:   timer fires → state==moving → chance hit  → set shaking → set
                    elevator_qte_pending=True → QTE fires → player resolves →
                    _qte_resolve_elevator → clears flag → emit schedule_transit(2s) →
                    timer fires again → state==idle/moving → finalize_elevator_arrival
        """
        self.logger.info("[ELEVATOR DEBUG] process_elevator_arrival triggered by UI timer.")
    
        if not self.player.get('elevator_transit_active'):
            return self._build_response()
    
        if self.player.get('location') != "Elevator Car":
            self.player.pop('elevator_transit_active', None)
            return self._build_response()
    
        # QTE suspension gate — QTE resolution will re-emit schedule_transit to resume.
        if self.player.get('elevator_qte_pending'):
            self.logger.info("[ELEVATOR DEBUG] QTE pending — transit suspended, waiting for resolution.")
            return self._build_response()
    
        hid = None
        if getattr(self, 'hazard_engine', None):
            hid = self.hazard_engine.get_hazard_instance_id_by_type(
                "Elevator Car", "elevator_freefall"
            )
    
        if hid and self.hazard_engine:
            h = self.hazard_engine.active_hazards.get(hid, {})
            current_state = h.get('state', 'idle')
    
            # Non-moving, non-idle state means a QTE was already triggered by process_turn
            # (autonomous tick) before our timer fired. Respect it — don't dodge it.
            if current_state not in ("idle", "moving"):
                if self.player.get('qte_active'):
                    self.logger.info("[ELEVATOR DEBUG] QTE already active from autonomous tick. Suspending.")
                    self.player['elevator_qte_pending'] = True
                    return self._build_response()
                else:
                    # QTE was triggered but already resolved (or never shown) — safe to arrive.
                    self.logger.info(
                        "[ELEVATOR DEBUG] Non-moving state with no active QTE — finalizing arrival."
                    )
                    self.hazard_engine.set_hazard_state(
                        hid, "idle", suppress_entry_effects=True
                    )
                    return self.finalize_elevator_arrival()
    
            if current_state == 'moving':
                master    = h.get('master_data', {})
                state_def = master.get('states', {}).get('moving', {})
                chance    = float(state_def.get('chance_to_progress', 0.35))
    
                import random
                if chance > 0 and random.random() < chance:
                    next_state = state_def.get('next_state', 'shaking')
                    self.logger.warning(
                        f"[ELEVATOR DEBUG] Hazard escalating to '{next_state}' "
                        f"(chance={chance:.0%})"
                    )
    
                    # Suspend transit — QTE resolution re-emits schedule_transit.
                    self.player['elevator_qte_pending'] = True
    
                    result = self.hazard_engine.set_hazard_state(hid, next_state)
                    for cons in result.get('consequences', []):
                        self.handle_hazard_consequence(cons)
    
                    return self._build_response(messages=result.get("messages", []))
    
        self.logger.info("[ELEVATOR DEBUG] Safe arrival executing.")
        return self.finalize_elevator_arrival()
    
    
    def finalize_elevator_arrival(self) -> dict:
        """
        The single authoritative function that ejects the player to the destination floor.
        Clears all transit flags, resets the hazard, triggers destination ambushes,
        and builds the arrival room description.
        """
        self.logger.info("[ELEVATOR DEBUG] finalize_elevator_arrival called.")
    
        dest_room = self.player.pop('pending_elevator_dest',  None)
        new_floor = self.player.pop('pending_elevator_floor', None)
        self.player.pop('elevator_transit_active',  None)
        self.player.pop('elevator_qte_pending',     None)   # always safe to clear
    
        self.logger.info(
            f"[ELEVATOR DEBUG] Popped destinations -> Room: '{dest_room}', Floor: '{new_floor}'"
        )
    
        # Cancel any legacy Kivy Clock timer that might still be scheduled.
        timer = getattr(self, '_elevator_timer', None)
        if timer:
            try:
                self.logger.debug("[ELEVATOR DEBUG] Cancelling legacy Kivy Clock timer.")
                timer.cancel()
            except Exception:
                pass
            self._elevator_timer = None
    
        if not dest_room or dest_room not in self.current_level_rooms_world_state:
            self.logger.error(
                f"[ELEVATOR DEBUG] FATAL: dest_room '{dest_room}' is invalid or missing "
                f"from world state! Failsafe triggered."
            )
            return self._build_response(
                message="The elevator doors open... onto a solid brick wall. Something went terribly wrong.",
                turn_taken=False
            )
    
        self.logger.info(f"[ELEVATOR DEBUG] Safely ejecting player into '{dest_room}'.")
    
        self.player['location'] = dest_room
        self.player.setdefault('visited_rooms', set()).add(dest_room)
        if new_floor is not None:
            self.player['elevator_current_floor'] = new_floor
    
        if getattr(self, 'hazard_engine', None):
            self.logger.debug(
                "[ELEVATOR DEBUG] Resetting elevator hazard to idle and triggering "
                "destination ambushes."
            )
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
    
        self.logger.info("[ELEVATOR DEBUG] Arrival complete. Pushing final room description to UI.")
        return self._build_response(
            message=f"*DING* — The doors slide open.\n\n{room_desc}",
            turn_taken=False,
            success=True,
            ui_events=[{"event_type": "refresh_map"}]
        )
    
    
    # =============================================================================
    # ALSO UPDATE: _qte_resolve_elevator in game_logic.py
    # When elevator QTE resolves successfully, clear the pending flag and re-emit
    # the transit timer so process_elevator_arrival can finalize.
    # =============================================================================
    
    def _qte_resolve_elevator(self, qte_result: dict):
        """
        Called by _handle_qte_resolution when a QTE originated from the elevator hazard.
    
        Routes outcome:
        shaking success → clear elevator_qte_pending → resume transit (2s) → arrive
        shaking failure → escalates to cable_snap (handled by consequence pipeline)
        cable_snap/plunge success → force-move to basement via consequence pipeline
        cable_snap/plunge failure → impact / game over
        """
        hazard_id = qte_result.get('qte_source_hazard_id') or qte_result.get('qte_context', {}).get('qte_source_hazard_id')
        if not hazard_id or 'elevator_freefall' not in hazard_id:
            return  # Not an elevator QTE
    
        success = qte_result.get('success', False)
    
        next_state = (
            (qte_result.get('next_state_success') or qte_result.get('next_state_after_qte_success'))
            if success else
            (qte_result.get('next_state_failure') or qte_result.get('next_state_after_qte_failure'))
        )
    
        # Terminal recovery states — consequence pipeline moves the player.
        # Clear transit flags so finalize_elevator_arrival doesn't double-fire.
        recovery_states = ('emergency_brake_catches', 'hard_landing_survival')
        if next_state in recovery_states:
            self.player.pop('elevator_transit_active', None)
            self.player.pop('pending_elevator_dest',   None)
            self.player.pop('pending_elevator_floor',  None)
            self.player.pop('elevator_qte_pending',    None)
            return
    
        if success:
            # Player survived the shaking — clear the suspension flag and resume transit.
            self.player.pop('elevator_qte_pending', None)
            self.add_ui_event({
                "event_type": "show_message",
                "message": "\n[color=ffaa00]The elevator groans and lurches back into motion...[/color]\n"
            })
            # Re-emit the transit timer through the normal UI event queue.
            # _on_elevator_timer_complete will call process_elevator_arrival,
            # which now finds state==idle/moving with no pending QTE → finalize.
            self.add_ui_event({"event_type": "schedule_transit", "duration": 2.0})

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
        flags = self.player.get('flags', {})
        if not isinstance(flags, dict):
            flags = {}
            self.player['flags'] = flags

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
        """
        Main orchestrator for finalizing player movement.
        Delegates state cleanup, NPC tracking, and event triggers to helpers.
        """
        tracer = TraceLogger("FinalizeMove")
        tracer.mark("start", current_room=current_room_id, destination=destination)

        try:
            old_room_data = self.get_room_data(current_room_id) or {}
            new_room_data = self.get_room_data(destination) or {}

            # 1. State Cleanup (Amnesia & UI Buttons)
            self._cleanup_dialogue_state(tracer)

            # 2. Update Core Locations (Player & Active Follower)
            self._update_player_and_npc_locations(destination, tracer)

            # 3. Check Hub Intercepts & Survivor Group Movement
            intercept_response = self._handle_post_move_events(current_room_id, destination, old_room_data, new_room_data, tracer)
            if intercept_response:
                tracer.mark("move_intercepted_by_event")
                return intercept_response

            # 4. Room Mechanics (Elevator, Hazards)
            self._process_elevator_state(current_room_id, destination)
            self._trigger_room_ambushes(destination)
            tracer.mark("room_mechanics_processed")
            # [Player successfully moves to 'dest_room']
            self.player['location'] = destination
            
            # --- NEW: Tell DeathAI to evaluate the new room for real-time traps ---
            if hasattr(self, 'death_ai') and self.death_ai:
                self.death_ai.evaluate_room_pressure(destination)
            # 5. Build Final Output
            return self._build_arrival_response(destination, new_room_data, tracer)

        except Exception as e:
            self.logger.exception(f"[_finalize_move] Unexpected error during move to {destination}: {e}")
            tracer.mark("exit_exception", error=str(e))
            # Attach the trace dump to a fallback error response if desired
            return self._build_response(
                message="[color=ff0000]An error occurred while moving. Please try again.[/color]", 
                turn_taken=False
            )

    # ==========================================
    # FINALIZE MOVE HELPERS
    # ==========================================

    def _cleanup_dialogue_state(self, tracer):
        """Cures companion amnesia and clears lingering UI dialogue contexts."""
        interacted_npc = self.player.get('current_interacted_npc')
        companions = self.player.get('companions', [])
        companion = self.player.get('companion_id')

        # Only wipe the conversation memory if the NPC is NOT following you
        if interacted_npc and interacted_npc not in companions and interacted_npc != companion:
            self.player.pop('current_interacted_npc', None)
            self.player.pop('current_conversation_state', None)
            tracer.mark("dialogue_state_cleared", cleared_npc=interacted_npc)

        # Always kill the UI respond buttons so they don't linger across rooms
        if hasattr(self, 'last_dialogue_context'):
            self.last_dialogue_context = {}
            tracer.mark("ui_dialogue_buttons_killed")

    def _update_player_and_npc_locations(self, destination: str, tracer):
        """Updates the player's core location, visited sets, and moves active followers."""
        self.player['location'] = destination
        self.player.setdefault('visited_rooms', set()).add(destination)
        tracer.mark("player_location_updated", destination=destination)

        # Companions Follow You
        companion = self.player.get('companion_id')
        if companion:
            self._move_npc(companion, destination)
            tracer.mark("companion_moved", companion=companion, destination=destination)

    def _handle_post_move_events(self, current_room_id: str, destination: str, old_data: dict, new_data: dict, tracer) -> Optional[dict]:
        """Checks for interrupts like Hub Police, Level Completion, and Group routing."""
        # Check Narrative Hub Intercepts (e.g., Police Arrest)
        hub_intercept = self._check_hub_intercepts(destination)
        if hub_intercept:
            tracer.mark("hub_intercept_triggered")
            return hub_intercept

        # Move Survivor Group & Dynamic Recruitment
        companions = self.player.get('companions', [])
        self._move_survivor_group(companions, old_data, new_data)

        if not companions:
            self._intercept_next_target(new_data)
            new_companions = self.player.get('companions', [])
            if new_companions:
                tracer.mark("dynamic_recruitment_triggered", new_companions=new_companions)

        # Check Level/Premonition Completion
        premonition_result = self._check_premonition_complete()
        if premonition_result:
            tracer.mark("premonition_completed")
            return premonition_result

        return None

    def _build_arrival_response(self, destination: str, new_room_data: dict, tracer) -> dict:
        """Generates the arrival narrative and handles self-healing JSON entry popups."""
        companions = self.player.get('companions', [])
        message = self._generate_arrival_narrative(destination, companions)
        ui_events = []
        
        # --- JSON-Safe Deduplication & Self-Healing ---
        # Grab the tracker. If it's a corrupted string or missing, force it to be a clean list.
        popups = self.player.get('shown_entry_popups', [])
        if not isinstance(popups, list):
            # Self-heal from sets or corrupted JSON strings
            popups = list(popups) if isinstance(popups, set) else []
            self.player['shown_entry_popups'] = popups
            tracer.mark("healed_popup_tracker", restored_type="list")

        first_text = new_room_data.get('first_entry_text')
        already_shown = destination in popups
        
        if first_text and not already_shown:
            # Lock it immediately using .append() instead of .add()
            self.player['shown_entry_popups'].append(destination)
            ui_events.append(self._make_first_entry_popup_event(destination, first_text))
            tracer.mark("first_entry_popup_queued", destination=destination)

        tracer.mark("exit_success", events_queued=len(ui_events))
        return self._build_response(message=message, turn_taken=True, success=True, ui_events=ui_events)
    # -------------------------------------------------------------------------
    # --- Movement Finalization Helpers ---
    # -------------------------------------------------------------------------

    def _trigger_dynamic_transition(self, next_level_id: str, start_room: str = None) -> dict:
        self.logger.info(f"Dynamically transitioning to level: {next_level_id} (room: {start_room})")
        self.player['level_complete_flag'] = False
        
        # Resolve start room from level_requirements if not explicitly provided
        if not start_room:
            level_reqs = self.resource_manager.get_data('level_requirements', {})
            start_room = level_reqs.get(next_level_id, {}).get('entry_room')
        
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
            
        self.add_ui_event(completion_event)
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
        
        # 4. AGILE GHOST CHECK: Dynamic stealth chance based on Agility
        char_class = self.player.get('character_class', 'Survivor')
        class_master = self.resource_manager.get_data('character_classes', {}).get(char_class, {})
        agility = class_master.get('agility', 1)
        
        # Base 20% chance to hide, + 10% per point of agility (capped at 85% so it's never guaranteed)
        stealth_chance = min(0.85, 0.20 + (agility * 0.10))
        
        import random
        if random.random() < stealth_chance:
            self.logger.info(f"Hub Police Event bypassed. (Agility: {agility} | Pass Chance: {int(stealth_chance * 100)}%)")
            
            # --- UNLOCK THE GHOST ACHIEVEMENT ---
            if getattr(self, 'achievements_system', None):
                self.achievements_system.unlock("ghost_in_the_garage")

            if room_data:
                room_data['first_entry_text'] = ""
                
            # THE SIRENS: Flash red and blue in the background behind the popup
            self.add_ui_event({"event_type": "screen_flash", "color": "ff0000", "duration": 0.5, "opacity": 0.3})
            self.add_ui_event({"event_type": "screen_flash", "color": "0044ff", "duration": 0.5, "opacity": 0.3})
                
            # THE POPUP
            self.add_ui_event({
                "event_type": "show_popup", 
                "title": "A Narrow Escape",
                "message": "[color=aaaaaa]You hear sirens converging on the hospital, but you reach your car before they spot you. Safe... for now.[/color]",
                "priority": 1000
            })
            
            # Print the newly injected movement options immediately
            exits_prompt = "\n".join([f"  [color=00ff00][b]'move {ext}'[/b][/color]" for ext in room_data.get('exits', {}).keys()])
            return self._build_response(
                message=f"\n[color=00FF00]Your options:[/color]\n{exits_prompt}",
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

    def _inject_hub_exits(self, room_id: str, hub_state: dict):
        """Dynamically populates the Hub with exits. Unlocks Free Roam if list is known."""
        room_data = self.current_level_rooms_world_state.get(room_id)
        if not room_data:
            return

        # Wipe default map exits so we can rebuild them dynamically
        exits = room_data.setdefault('exits', {})
        exits.clear() 

        npc_status = self.player.get('npc_status', {})
        npc_workplaces = self.player.get('npc_workplaces', {})
        companion_id = self.player.get('companion_id', '').lower()

        # --- THE FIX: Free Roam vs Dynamic Hunt ---
        if self.player.get('learned_deaths_list'):
            # FREE ROAM: The player knows the list. Let them drive to ANY alive NPC.
            for npc_name, wp_data in npc_workplaces.items():
                n_lower = npc_name.lower()
                
                # Skip dead people, the player, and the person already in the car
                if n_lower == 'player' or n_lower == companion_id:
                    continue
                if npc_status.get(n_lower) not in ('alive', 'injured'):
                    continue
                if self.player.get(f"visited_workplace_{n_lower}"):
                    continue
                    
                level_id = wp_data.get('level_id')
                wp_name = wp_data.get('workplace_name', f"{npc_name.title()}'s Workplace")
                
                if level_id:
                    # Inject a direct level bypass exit!
                    exits[f"drive to {wp_name.lower()}"] = {
                        "target": level_id, 
                        "locked": False,
                        "description": f"Go warn {npc_name.title()}."
                    }
        else:
            # BLIND GAMBLE: They don't know the list yet. Force the linear Dynamic Hunt.
            exits["hunt next target"] = {
                "target": "LEVEL_TRANSITION_DYNAMIC_HUNT", 
                "locked": False
            }

        # --- STATIC PROGRESSION EXITS ---
        # If Bludworth is unlocked but unvisited
        if self.player.get("knows_bludworth_address") and not self.player.get("visited_bludworth"):
             exits["drive to bludworth's house"] = {"target": "LEVEL_TRANSITION_BLUDWORTH", "locked": False}
             
        # If the Finale is ready (Triggered when all survivors are collected)
        if self.player.get('finale_prep_advised'):
            exits["prepare for the end"] = {"target": "LEVEL_TRANSITION_FINALE", "locked": False}

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
        # --- THE FIX: Level 0 Gate ---
        current_level = str(self.player.get('current_level', '0'))
        if current_level in ('0', 'level_0'):
            return  # No auto-bolting during the premonition!
        # -----------------------------

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
                    companions = self.player.setdefault('companions', [])
                    
                    # Ensure we only bolt them and show the message ONCE
                    if comp_str not in companions:
                        companions.append(comp_str)
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