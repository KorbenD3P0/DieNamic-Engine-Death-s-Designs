# fd_terminal/mixins/combat_mixins.py

class CombatMixin:
    """
    Handles all physical interaction commands (kick, punch, break, force).
    Requires the parent class to have access to:
    - self.player
    - self.add_ui_event()
    - self.get_room_data()
    """
    
    def _command_force(self, target_str: str) -> dict:
        """
        Force a door/exit, or apply brute force to a breakable object.
        Supports 'with <tool>' and auto-picks the best tool if not specified.
        Defers to active hazards (e.g., MRI) via HazardEngine (already invoked before command).
        """
        self.logger.debug(f"_command_force called with target_str='{target_str}'")
        try:
            return self._force_main(target_str)
        except Exception as e:
            self.logger.error(f"_command_force: Unexpected error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong while forcing.", turn_taken=False, success=False)

    def _command_kick(self, target_str: str) -> dict:
        """
        Handles the 'kick' command.
        Most of the time, this just provides flavor text.
        Real interactions (like kicking the Robo-Vacuum) are handled by the HazardEngine
        BEFORE this method is ever called.
        """
        if not target_str:
            return self._build_response(message="Kick what? Thin air?", turn_taken=False)

        # If we reached this point, it means NO HAZARD intercepted the kick.
        # So we just provide generic feedback.
        return self._build_response(
            message=f"You kick the {target_str}. It hurts your toe more than it hurts the object.", 
            turn_taken=True
        )

    def _command_punch(self, target_str: str) -> dict:
        """
        Aggressive action. Mostly flavor, unless a hazard intercepts it.
        """
        target_str = (target_str or "").strip()
        if not target_str:
            return self._build_response(message="Punch what? The air?", turn_taken=False)

        # 1. Hazard Interception (e.g., punching a burning object)
        # The HazardEngine runs automatically via process_player_interaction before this is called.
        # If a hazard rule had 'punch', it would have already triggered and possibly blocked this.
        
        # 2. Flavor Response
        # If we are here, nothing special happened.
        return self._build_response(
            message=f"You punch the {target_str}. It hurts your hand more than it hurts the object.",
            turn_taken=True
        )

    def _command_break(self, target_name_str: str) -> dict:
        """
        Player intent to break an object; uses same core as 'force', preferring break behavior.
        Supports 'break <target> [with <tool>]'.
        """
        self.logger.debug(f"_command_break called with target_name_str='{target_name_str}'")
        try:
            return self._break_main(target_name_str)
        except Exception as e:
            self.logger.error(f"_command_break: Unexpected error: {e}", exc_info=True)
            return self._build_response(message="Something went wrong while breaking.", turn_taken=False, success=False)