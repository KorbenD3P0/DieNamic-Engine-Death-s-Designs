# fd_terminal/mixins/door_mixin.py
from fd_terminal.utils import normalize_text, color_text


class DoorMixin:
    """
    Centralizes all door/exit resolution: locked checks, dict exits,
    key matching, and force eligibility. All movement, unlock, and force
    commands delegate here instead of reimplementing the logic.
    """


    def _resolve_exit(self, direction: str, exits: dict) -> dict:
        """
        Resolve an exit direction into a standardized result dict.
        Case-insensitive so lowercased direction strings match mixed-case exit keys.
        """
        # Case-insensitive lookup (original did exits.get(direction) — case-sensitive miss)
        dest = exits.get(direction)
        if dest is None:
            direction_lower = direction.lower()
            for k, v in exits.items():
                if k.lower() == direction_lower:
                    dest = v
                    direction = k   # use the canonical key for any downstream references
                    break
    
        if dest is None:
            return {
                "can_pass": False, "target_room": None, "is_locked": False,
                "lock_message": f"You can't go {direction}.", "exit_type": "missing",
                "_raw_ref": None
            }
    
        # --- rest of _resolve_exit is UNCHANGED from door_mixin.py ---
        if isinstance(dest, dict):
            target = dest.get('target')
            if not target:
                return {
                    "can_pass": False, "target_room": None, "is_locked": False,
                    "lock_message": "You can't go that way.", "exit_type": "dict",
                    "_raw_ref": dest
                }
            if dest.get('dynamic_destination'):
                return {
                    "can_pass": True, "target_room": None, "is_locked": False,
                    "lock_message": "", "exit_type": "dynamic", "_raw_ref": dest
                }
            if dest.get('locked', False):
                lock_msg = dest.get('locked_description', f"The way {direction} is locked.")
                return {
                    "can_pass": False, "target_room": target, "is_locked": True,
                    "lock_message": lock_msg,
                    "unlocks_with": dest.get('unlocks_with'),
                    "forceable": dest.get('forceable', False),
                    "force_threshold": dest.get('force_threshold', 5),
                    "exit_type": "dict", "_raw_ref": dest
                }
            return {
                "can_pass": True, "target_room": target, "is_locked": False,
                "lock_message": "", "exit_type": "dict", "_raw_ref": dest
            }
    
        dest_data = (
            self.current_level_rooms_world_state.get(dest)
            or self.get_room_data(dest)
            or {}
        )
        if dest_data.get('locked', False):
            if dest_data.get('locked_by_mri'):
                lock_msg = "The magnetic field has sealed that door shut!"
            else:
                lock_msg = f"The door to {dest} is locked."
            return {
                "can_pass": False, "target_room": dest, "is_locked": True,
                "lock_message": lock_msg,
                "unlocks_with": dest_data.get('unlocks_with'),
                "forceable": dest_data.get('forceable', False),
                "force_threshold": dest_data.get('force_threshold', 5),
                "exit_type": "string", "_raw_ref": dest
            }
        return {
            "can_pass": True, "target_room": dest, "is_locked": False,
            "lock_message": "", "exit_type": "string", "_raw_ref": dest
        }

    def _attempt_auto_unlock(self, direction: str, exits: dict, resolved: dict, current_room_id: str):
        """Secretly attempts to auto-unlock a door if the player has the key."""
        if not (resolved.get('is_locked') and hasattr(self, '_command_unlock')):
            return None

        # ── NEW: MRI magnetic seal check — must happen BEFORE consuming the key
        target_room = resolved.get('target_room', '')
        target_data = self.current_level_rooms_world_state.get(target_room, {})
        if target_data.get('locked_by_mri'):
            return self._build_response(
                message=f"\n[color=ff0000]The {direction} door is magnetically sealed shut! "
                        f"The magnetic field holds it closed — a key won't help.[/color]\n",
                turn_taken=False
            )
        # ────────────────────────────────────────────────────────────────────────

        unlock_resp = self._command_unlock(direction)
        if not unlock_resp.get('success'):
            return None

        # It worked! Re-resolve the exit to confirm the lock is physically gone
        new_resolved = self._resolve_exit(direction, exits)
        if not new_resolved['can_pass']:
            return None

        target_room = new_resolved.get('target_room') or ""
        target_data = self.current_level_rooms_world_state.get(target_room, {})

        # ---------------------------------------

        # Check if unlocking it triggered a transition
        transition_resp = self._route_level_transition(target_room)
        if transition_resp:
            return transition_resp

        # Finalize the move into the newly unlocked room
        move_resp = self._finalize_move(current_room_id, target_room)

        # Combine the "You unlocked the door" text with the new room description
        combined_msgs = []
        for resp in (unlock_resp, move_resp):
            if resp.get('messages'):
                combined_msgs.extend(resp['messages'])
            elif resp.get('message'):
                combined_msgs.append(resp['message'])

        move_resp['messages'] = combined_msgs
        if 'message' in move_resp:
            del move_resp['message']

        move_resp['ui_events'] = unlock_resp.get('ui_events', []) + move_resp.get('ui_events', [])
        return move_resp

    def _unlock_dict_exit(self, exit_ref: dict, key_id: str) -> bool:
        """Unlock a dict-style exit by setting locked=False."""
        if isinstance(exit_ref, dict) and exit_ref.get('locked', False):
            exit_ref['locked'] = False
            self.logger.info(f"_unlock_dict_exit: Unlocked dict exit to '{exit_ref.get('target')}' with '{key_id}'")
            return True
        return False

    def _find_exit_by_target(self, target_name: str, exits: dict) -> tuple:
        """
        Find an exit by direction name, target room name, or alias.
        Returns (direction, resolved_result) or (None, None).
        """
        tnorm = normalize_text(target_name)
        for direction, dest in exits.items():
            resolved = self._resolve_exit(direction, exits)
            d_norm = normalize_text(direction)
            t_room = normalize_text(resolved.get('target_room', '') or '')

            if (tnorm == d_norm or
                tnorm == f"{d_norm} door" or
                tnorm == t_room or
                tnorm == "door" or
                tnorm == "back door"):
                return direction, resolved

        return None, None