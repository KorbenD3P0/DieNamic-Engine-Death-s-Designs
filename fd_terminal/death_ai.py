import collections
import random
import logging
from kivy.clock import Clock
from fd_terminal import hazard_engine

from .resource_manager import ResourceManager
from .utils import color_text  # Add this import for color_text


class DeathAI:
    """
    An intelligent antagonist system that learns player behavior and 
    creates targeted threats, scaling aggression based on level progression.
    """
    
    def __init__(self, game_logic_ref):
        self.game_logic = game_logic_ref
        self.resource_manager = game_logic_ref.resource_manager
        self.hazard_engine = None  # Will be set by GameLogic after both are created
        self.logger = logging.getLogger("DeathAI")
        
        # Threat scoring system
        self.location_threat_scores = collections.defaultdict(float)
        self.object_threat_scores = collections.defaultdict(float)
        self.room_safety_perception = collections.defaultdict(float)
        
        # --- NEW: Real-Time Pressure Variables ---
        self.impatience_timer = None
        self.current_pressure_room = None

        # Behavioral pattern tracking
        self.player_behavior_patterns = {
            'preferred_escape_routes': collections.deque(maxlen=10),
            'hiding_spots_used': collections.defaultdict(int),
            'item_usage_patterns': collections.defaultdict(list),
            'qte_success_rate': 0.0,
            'qte_successes': 0,
            'qte_attempts': 0,
            'room_visit_frequency': collections.defaultdict(int),
            'search_patterns': collections.defaultdict(int),
            'confidence_indicators': 0,
            'panic_indicators': 0
        }
        
        self.escalation_threshold = 5.0
        self.max_threat_score = 20.0
        self.pending_counter_strategies = []
        
        # --- NEW: Dynamic Aggression Baseline ---
        # We start with a base of 1.0, but it will be dampened by the progression_ratio
        # until the player makes progress.
        self.current_aggression_multiplier = 1.0
        self.fear_decay_per_turn = 0.03

        self.fear_increase_events = {
            'near_miss': 0.15,
            'witness_death': 0.25,
            'high_threat_location': 0.10,
            'qte_failure': 0.08,
            'examine_omen': 0.20, 
        }

        # --- NEW: Entropy System (Death's Design) ---
        self.entropy = 0.0
        self.freak_accident_cooldown = 0

    # --- NEW: REAL-TIME PRESSURE SYSTEM ---
    def cancel_impatience(self):
        """Stops the real-time Death Clock if the player escapes or the hazard triggers."""
        if self.impatience_timer:
            self.impatience_timer.cancel()
            self.impatience_timer = None

    def evaluate_room_pressure(self, room_id):
        """Scans the room for hazards that have real-time countdowns and starts the clock."""
        self.cancel_impatience()
        self.current_pressure_room = room_id

        if not self.hazard_engine:
            return

        active_hazards = self.hazard_engine.get_active_hazards_for_room(room_id)
        for hazard in active_hazards:
            hazard_id = hazard.get('hazard_id')
            current_state = hazard.get('current_state')
            
            # --- THE FIX: Strip the instance hash to get the base JSON key! ---
            hazard_key = hazard.get('hazard_key', hazard_id.split('#')[0])
            
            # Fetch the JSON data for this exact state
            h_data = self.hazard_engine.hazards_data.get(hazard_key, {})
            state_data = h_data.get('states', {}).get(current_state, {})
            # ------------------------------------------------------------------
            
            # Check if this state has an impatience timer
            time_limit = state_data.get('real_time_escalation_seconds')
            
            if time_limit:
                self.logger.info(f"DeathAI: Player entered kill zone. '{hazard_id}' will force trigger in {time_limit}s.")
                # Start the Kivy Clock. Pass the hazard_id and the expected next state.
                self.impatience_timer = Clock.schedule_once(
                    lambda dt, h_id=hazard_id, r_id=room_id, n_state=state_data.get('next_state'): 
                        self._force_real_time_escalation(h_id, r_id, n_state),
                    time_limit
                )
                break # Only track one master real-time threat at a time to prevent UI chaos

    def _force_real_time_escalation(self, hazard_id, room_id, next_state):
        """Executes when the player dawdles for too long. Forces the hazard to spring."""
        # Safety check: Did they leave the room at the exact millisecond this fired?
        if self.game_logic.player.get('location') != room_id:
            return 
            
        self.logger.warning(f"DeathAI: Player took too long in {room_id}. Forcing '{hazard_id}' to '{next_state}'!")
        self.impatience_timer = None

        # 1. Force the HazardEngine to transition the state immediately
        hazard_ref = next((h for h in self.hazard_engine.active_hazards if h['hazard_id'] == hazard_id), None)
        if hazard_ref and next_state:
            self.hazard_engine._transition_hazard(hazard_ref, next_state)
            
            # 2. Flush the UI events so the player is instantly interrupted!
            events = self.hazard_engine.flush_ui_events()
            if events:
                # Add to queue and trigger the UI to process them immediately
                self.game_logic.ui_event_queue.extend(events)
                
                # If you have a real-time UI flusher (like in your UI.py), call it here.
                # If not, App.get_running_app() allows you to reach the UI directly from the backend:
                try:
                    from kivy.app import App
                    app = App.get_running_app()
                    if app and hasattr(app, 'root'):
                        game_screen = app.root.get_screen('game')
                        game_screen._handle_ui_events(events)
                        self.game_logic.ui_event_queue.clear()
                except Exception as e:
                    self.logger.error(f"DeathAI failed to flush real-time UI: {e}")

    def _check_hazard_cap(self) -> bool:
        """
        Returns True if hazard cap is reached to prevent resource starvation.
        """
        if not self.game_logic or not self.game_logic.hazard_engine:
            return False
        
        # Cap at 30 active hazards
        if len(self.game_logic.hazard_engine.active_hazards) >= 30:
            self.logger.warning("[DeathAI] Hazard Cap Reached (30). Trigger suppressed to prevent overload.")
            return True
        return False

    def update_npc_threats(self):
        """
        Death's Design hunts in order, skipping those who have intervened.
        """
        if not self.game_logic: return
        
        roster = self.game_logic.player.get('npc_status', {})
        deaths_list = self.game_logic.player.get('deaths_list', [])
        skipped = self.game_logic.player.get('death_design_skipped', [])
        
        # 1. Identify the Primary Target
        primary_target = None
        for name in deaths_list:
            if name == 'player' and 'player' not in skipped:
                primary_target = 'player'
                break
            if name != 'player' and roster.get(name.lower()) in ('alive', 'injured') and name not in skipped:
                primary_target = name
                break

        if not primary_target: 
            return # Everyone has either died or been skipped. Death operates normally.

        # 2. Hunt them down
        player_loc = self.game_logic.player.get('location')
        rooms = getattr(self.game_logic, 'current_level_rooms_world_state', {})
        
        for room_name, room_data in rooms.items():
            # If the PLAYER is the target, drop the nuke on the player's room
            if primary_target == 'player' and room_name == player_loc:
                self.location_threat_scores[room_name] += (5.0 * self.current_aggression_multiplier)
                self.logger.info(f"DeathAI: Player is the primary target. Omega threat centered on {room_name}.")
                continue
                
            # If an NPC is the target, search the rooms for them
            npcs_in_room = room_data.get('npcs', [])
            for npc in npcs_in_room:
                name = npc.get('name', npc) if isinstance(npc, dict) else npc
                
                if name == primary_target:
                    self.location_threat_scores[room_name] += (5.0 * self.current_aggression_multiplier)
                    self.logger.info(f"DeathAI: Found PRIMARY TARGET ({name}) in {room_name}. Hunting.")
                elif roster.get(name.lower()) in ('alive', 'injured'):
                    self.location_threat_scores[room_name] += (0.5 * self.current_aggression_multiplier)

    def update_fear(self, event_type=None, custom_amount=None, hp_loss=None):
        """
        Increase or decrease player fear based on event type, custom amount, or HP loss.
        Integrates patch: event_type and custom_amount are additive, and fear is clamped to 1.0.
        Includes robust debugging and Death's Breath escalation on large fear jumps.
        """
        player = self.game_logic.player
        current = player.get('fear', 0.0)
        initial_fear = current
        increase = 0.0
        debug_details = []

        # Canonical: HP loss increases fear proportionally (e.g., 0.01 per HP lost)
        if hp_loss is not None and hp_loss > 0:
            hp_incr = hp_loss * 0.01
            increase += hp_incr
            debug_details.append(f"HP loss: {hp_loss} -> fear +{hp_incr:.3f}")

        if event_type:
            event_incr = self.fear_increase_events.get(event_type, 0.0)
            increase += event_incr
            debug_details.append(f"Event '{event_type}' -> fear +{event_incr:.3f}")
        if custom_amount is not None:
            increase += custom_amount
            debug_details.append(f"Custom amount -> fear +{custom_amount:.3f}")

        player['fear'] = min(1.0, max(0.0, current + increase))

    # --- PATCH: Death's Breath escalation on large fear jump ---
        old_fear = initial_fear
        new_fear = player['fear']
        fear_delta = new_fear - old_fear

        if fear_delta >= 0.15:
            # Find Death's Breath hazard INSTANCE ID
            current_room = self.game_logic.player.get('location')
            if current_room and self.game_logic.hazard_engine:
                breath_id = self.game_logic.hazard_engine.get_hazard_instance_id_by_type(current_room, "deaths_breath")

                if breath_id:
                    # THE FIX: Don't jump straight to malevolent_gust! 
                    # Call manifest to safely step it up based on the level!
                    self.manifest_deaths_presence(current_room, intensity=1.0)
                else:
                    self.manifest_deaths_presence(current_room, intensity=new_fear)
        # --- END PATCH ---
        self.logger.debug(
            f"update_fear called: initial={initial_fear:.3f}, final={player['fear']:.3f}, details={debug_details}"
        )

    def decay_fear(self):
        """Decay fear each turn."""
        player = self.game_logic.player
        current = player.get('fear', 0.0)
        player['fear'] = max(0.0, current - self.fear_decay_per_turn)

    def get_effective_aggression(self) -> float:
        """
        Returns the actual aggression level to use for logic checks.
        Scales the base multiplier by the level progression.
        """
        progression = self.calculate_level_progression()
        base = self.current_aggression_multiplier
        
        if progression >= 1.0:
            # FINALE MODE: All items found. Unleash everything.
            # Only log this once per turn, not every time this method is called
            if not getattr(self, '_finale_logged_this_turn', False):
                self.logger.info("[DeathAI] FINALE MODE ACTIVE. Maximum Aggression.")
                self._finale_logged_this_turn = True
            return base * 5.0 
        
        self._finale_logged_this_turn = False  # Reset when not in finale
        
        scaling_factor = 0.1 + (0.9 * progression)
        effective = base * scaling_factor
        
        entropy_factor = 1.0 + (self.entropy / 50.0)
        effective *= entropy_factor
        
        effective = min(effective, 5.0)
        
        self.logger.debug(f"[DeathAI] Progression: {progression:.2f}, Scaling: {scaling_factor:.2f}, Entropy: {self.entropy:.1f}, Effective: {effective:.2f}")
        return effective

    def analyze_player_action(self, action_type, target=None, location=None, success=None, context=None):
        """Main entry point for AI analysis per turn."""
        current_room = location or self.game_logic.player.get('location')
        if not current_room: return

        # 1. Update Fear
        if action_type == 'qte_failure':
            self.update_fear('qte_failure')
        
        # 2. Calculate Threat
        threat_increase = self._calculate_base_threat_increase(action_type, success)
        
        # 3. Update internal heatmaps
        self._update_location_threat_score(current_room, action_type, threat_increase)
        
        # 4. Evaluate Escalation (Gated by Progression)
        self._evaluate_escalation_triggers(current_room, action_type, success)
        
        # 5. Check specific triggers (Death's Breath)
        self._check_deaths_breath_triggers(action_type, target, current_room, success)

        #6. update_npc_threats
        self.update_npc_threats()

        
    def _check_deaths_breath_triggers(self, action, target, location, success):
        # Basic trigger check - can be expanded
        if action == 'examine' and 'mirror' in str(target):
            self.manifest_deaths_presence(location, intensity=0.5)

    def get_fear_hallucination(self) -> str | None:
        """
        If fear is high enough, has a chance to return a hallucination message.
        Enhanced: Level-aware contextual hallucinations.
        PATCH: If fear > 0.7 and random < 0.3, show a simple shadow hallucination.
        """
        player_fear = self.game_logic.player.get('fear', 0.0)
        current_level = self.game_logic.player.get('current_level', 1)
        current_room = self.game_logic.player.get('location', '')

        # PATCH: Simple hallucination if fear is high
        if player_fear > 0.7 and random.random() < 0.3:
            return "You see a shadow move in the corner of your eye."

        self.logger.debug(f"get_fear_hallucination called: player_fear={player_fear:.3f}, level={current_level}, room='{current_room}'")

        hallucination_triggered = False
        hallucination_message = None

        if random.random() < player_fear:
            # Get level-specific hallucinations
            hallucinations = self._get_level_hallucinations(current_level, current_room)
            hallucination_message = random.choice(hallucinations)
            hallucination_triggered = True

        self.logger.debug(
            f"Hallucination triggered: {hallucination_triggered}, message: {hallucination_message!r}"
        )
        return hallucination_message
    
    def _get_level_hallucinations(self, level: int, room: str) -> list:
        """Get contextually appropriate hallucinations from level_ambiance.json."""
        ambiance = self.resource_manager.get_data('level_ambiance', {})
        level_data = ambiance.get(str(level), {})
        hall_data = level_data.get('hallucinations', {})
        
        result = list(hall_data.get('base', [
            "For a split second, you see a shadowy figure at the edge of your vision, but it's gone when you turn.",
            "You hear a faint, distorted whisper that sounds like your own name.",
            "A sudden, inexplicable chill washes over you, raising goosebumps on your arms."
        ]))
        result.extend(hall_data.get('general', []))
        
        # Room-specific: check if any key is a substring of the room name
        room_lower = room.lower()
        for keyword, messages in hall_data.get('room_specific', {}).items():
            if keyword.lower() in room_lower:
                result.extend(messages)
        
        if not result:
            self.logger.warning(f"No hallucinations found for level {level}, using base set")
        
        return result

    def calculate_level_progression(self) -> float:
        """
        Calculates how close the player is to finishing the level (0.0 to 1.0).
        Based on collected required items/evidence vs total required.
        """
        try:
            if not self.game_logic: return 0.0
            
            # Get requirements for current level
            level_requirements = self.resource_manager.get_data('level_requirements', {})
            current_level = str(self.game_logic.player.get('current_level', 1))
            reqs = level_requirements.get(current_level, {})
            
            items_needed = reqs.get('items_needed', [])
            evidence_needed = reqs.get('evidence_needed', [])
            total_required_count = len(items_needed) + len(evidence_needed)
            
            # If no requirements, assume mid-game pacing (0.5) or endgame (1.0) depending on design
            if total_required_count == 0:
                return 0.5 

            # Count how many are missing
            # We reuse GameLogic's helper if available, or calculate manually to be safe
            _, missing_list = self.game_logic._requirements_met_for_level_exit()
            missing_count = len(missing_list)
            
            found_count = total_required_count - missing_count
            
            # Calculate Ratio
            ratio = found_count / total_required_count
            
            # Determine if we are in "Finale Mode" (All items found)
            # If ratio is 1.0, we return 1.0, which triggers max aggression.
            return max(0.0, min(1.0, ratio))
            
        except Exception as e:
            self.logger.error(f"Error calculating level progression: {e}")
            return 0.1 # Safe fallback

    def _apply_level_specific_fear_effects(self, description: str, room_name: str, level: int) -> str:
        """Apply level-specific atmospheric effects from level_ambiance.json."""
        ambiance = self.resource_manager.get_data('level_ambiance', {})
        level_data = ambiance.get(str(level), {})
        fear_data = level_data.get('fear_effects', {})
        
        if not fear_data:
            return description
        
        # Check room-specific first (exact match then substring)
        room_specific = fear_data.get('room_specific', {})
        for keyword, addition in room_specific.items():
            if keyword.lower() in room_name.lower():
                return description + " " + addition
        
        # Fallback to default
        default = fear_data.get('default', '')
        if default:
            return description + " " + default
        
        return description

    def _enforce_consequences(self, result: dict):
        if result and isinstance(result, dict):
            consequences = result.get('consequences', [])
            if consequences and self.game_logic:
                for cons in consequences:
                    self.game_logic.handle_hazard_consequence(cons)

    def _calculate_base_threat_increase(self, action, success):
        base = 0.1
        if action == 'qte_success':
            base = 2.0
        elif action == 'search':
            base = 0.5
        if success:
            base *= 1.2
        return base

    def _update_location_threat_score(self, location, action, amount):
        self.location_threat_scores[location] += amount

    def _update_object_threat_score(self, object_key: str, action: str, base_threat: float):
        """
        Track threat scores for specific objects (furniture, items, etc.).
        Enhanced: Adds robust logging for object threat score changes.
        """
        threat_increase = base_threat * 0.5
        old_score = self.object_threat_scores[object_key]
        new_score = min(old_score + threat_increase, self.max_threat_score)
        self.object_threat_scores[object_key] = new_score

        self.logger.debug(
            f"_update_object_threat_score: object_key={object_key}, action={action}, "
            f"base_threat={base_threat:.2f}, threat_increase={threat_increase:.2f}, "
            f"old_score={old_score:.2f}, new_score={new_score:.2f}"
        )

    def _update_safety_perception_enhanced(self, location: str, action: str, success: bool):
        """
        Enhanced safety perception tracking.
        Track how 'safe' the player likely perceives each location.
        Higher safety perception = bigger target for Death.
        Adds robust logging for debugging.
        """
        safety_increase = 0

        if action == 'search' and success:
            safety_increase += 0.5  # Successfully searching makes player feel room is "cleared"
        elif action == 'examine' and success:
            safety_increase += 0.2  # Examining without consequence feels safe
        elif action == 'move' and success:
            safety_increase += 0.1  # Easy movement feels safe
        elif action == 'qte_success':
            safety_increase += 1.0  # Surviving a QTE makes player feel temporarily safe

        old_safety = self.room_safety_perception[location]
        self.room_safety_perception[location] += safety_increase
        new_safety = self.room_safety_perception[location]

        self.logger.debug(
            f"_update_safety_perception_enhanced: location={location}, action={action}, success={success}, "
            f"safety_increase={safety_increase:.2f}, old_safety={old_safety:.2f}, new_safety={new_safety:.2f}"
        )

    def _analyze_behavioral_patterns_enhanced(self, action: str, location: str, target: str,
                                                success: bool, context: dict):
        """Enhanced pattern recognition with robust logging."""
        patterns = self.player_behavior_patterns
        debug_details = []

        # Track movement patterns
        if action == 'move' and success:
            patterns['preferred_escape_routes'].append(location)
            patterns['room_visit_frequency'][location] += 1
            debug_details.append(f"Moved to {location}, visit count: {patterns['room_visit_frequency'][location]}")

        # Track search patterns
        if action == 'search':
            key = f"{location}:{target}"
            patterns['search_patterns'][key] += 1
            debug_details.append(f"Searched {key}, count: {patterns['search_patterns'][key]}")

        # Track hiding behavior
        if action == 'search' and target and any(hiding_word in target.lower()
                                                    for hiding_word in ['closet', 'cabinet', 'under', 'behind']):
            key = f"{location}:{target}"
            patterns['hiding_spots_used'][key] += 1
            debug_details.append(f"Hiding spot used: {key}, count: {patterns['hiding_spots_used'][key]}")

        # Track QTE performance
        if action.startswith('qte_'):
            patterns['qte_attempts'] += 1
            if success:
                patterns['qte_successes'] += 1
            patterns['qte_success_rate'] = patterns['qte_successes'] / max(1, patterns['qte_attempts'])
            debug_details.append(
                f"QTE action: {action}, attempts: {patterns['qte_attempts']}, successes: {patterns['qte_successes']}, "
                f"success_rate: {patterns['qte_success_rate']:.2f}"
            )

        # Track item usage effectiveness
        if action == 'use' and target:
            usage_entry = {
                'location': location,
                'success': success,
                'turn': context.get('turn', 0)
            }
            patterns['item_usage_patterns'][target].append(usage_entry)
            debug_details.append(f"Used item: {target} at {location}, success: {success}, turn: {usage_entry['turn']}")

        self.logger.debug(
            f"_analyze_behavioral_patterns_enhanced: action={action}, location={location}, target={target}, "
            f"success={success}, context={context}, details={debug_details}"
        )

    def _evaluate_escalation_triggers(self, location: str, action: str, success: bool):
        """
        Decides if a counter-strategy should be queued.
        PATCH: Gates escalation behind the effective aggression score.
        """
        aggression = self.get_effective_aggression()
        
        # GATE: If aggression is too low (early game), ignore most triggers
        if aggression < 0.2:
            self.logger.debug("[DeathAI] Aggression too low for escalation logic.")
            return

        current_threat = self.location_threat_scores[location]
        
        # Scale the threshold: Harder to trigger escalation early on
        # Early game (agg=0.2): Threshold effectively 25.0
        # Late game (agg=1.0): Threshold 5.0
        adjusted_threshold = self.escalation_threshold / max(0.2, aggression)
        
        if current_threat >= adjusted_threshold:
            self._queue_escalation_response(f"location_threat_high_{location}", location)

    def _evaluate_dynamic_transition(self, level_req: dict) -> str:
        transitions = level_req.get('conditional_transitions', [])
        
        for trans in transitions:
            # Check defaults
            if trans.get('condition') == 'default':
                return trans.get('next_level_id')
                
            # Check flags
            if trans.get('condition') == 'has_flag':
                if trans.get('flag_name') in self.player.get('interaction_flags', set()):
                    return trans.get('next_level_id')
                    
            # Check items
            if trans.get('condition') == 'has_item':
                if trans.get('item_name') in self.player.get('inventory', []):
                    return trans.get('next_level_id')
                    
        return level_req.get('next_level_id', 'title_screen') # Fallback

    def _queue_escalation_response(self, reason, location):
        self.pending_counter_strategies.append({
            "reason": reason,
            "target_location": location,
            "strategy_type": "general_escalation",
            "priority": 10
        })

    def _calculate_strategy_priority(self, reason: str) -> float:
        """Calculate priority for counter-strategies. Adds robust logging."""
        priority_map = {
            'player_too_successful_at_qtes': 10.0,  # Highest priority
            'player_feels_too_safe': 8.0,
            'location_threat_high': 6.0,
            'overused_hiding_spot': 7.0
        }

        for key, priority in priority_map.items():
            if key in reason:
                self.logger.debug(f"_calculate_strategy_priority: reason={reason}, matched={key}, priority={priority}")
                return priority

        self.logger.debug(f"_calculate_strategy_priority: reason={reason}, default priority=5.0")
        return 5.0  # Default priority

    def _determine_strategy_type(self, reason: str, location: str) -> str:
        """Determine what type of counter-strategy to employ. Adds robust logging."""
        if 'hiding_spot' in reason:
            strategy_type = 'contaminate_hiding_spot'
        elif 'too_safe' in reason:
            strategy_type = 'spawn_in_safe_zone'
        elif 'qte_success' in reason:
            strategy_type = 'increase_qte_difficulty'
        elif 'location_threat_high' in reason:
            strategy_type = 'targeted_hazard_spawn'
        else:
            strategy_type = 'general_escalation'

        self.logger.debug(
            f"_determine_strategy_type: reason={reason}, location={location}, strategy_type={strategy_type}"
        )
        return strategy_type
    
    def execute_counter_strategies(self):
        """
        Executes pending strategies.
        PATCH: Gated by progression.
        """
        aggression = self.get_effective_aggression()
        
        messages = []
        
        # --- NEW: Check for Freak Accident regardless of strategy ---
        freak = self._trigger_freak_accident()
        if freak:
            messages.append(freak)

        # If we are in "Finale Mode" (aggression > 2.0), always execute
        if aggression < 2.0:
            # Otherwise, random chance based on aggression
            # 0.1 agg -> 10% chance to execute a pending strategy per turn
            if random.random() > aggression:
                return messages

        if not self.pending_counter_strategies:
            return messages

        strategy = self.pending_counter_strategies.pop(0)
        
        # Force target to current room (Proximity Law)
        current_room = self.game_logic.player.get('location')
        if not current_room: return messages
        strategy['target_location'] = current_room
        
        self.logger.info(f"[DeathAI] Executing strategy {strategy.get('strategy_type')} at {current_room}")
        
        # Dispatch
        stype = strategy.get('strategy_type')
        if stype == 'general_escalation':
            self._general_escalation(current_room, strategy)
        elif stype == 'targeted_hazard_spawn':
            self._spawn_specific_hazard('gas_leak', current_room) # Example fallback
            
        return messages

    def increase_entropy(self, amount: float):
        """
        Increases game entropy. Death doesn't like being cheated.
        """
        old = self.entropy
        self.entropy = min(100.0, self.entropy + amount)
        self.logger.info(f"[DeathAI] Entropy increased: {old:.1f} -> {self.entropy:.1f} (+{amount:.1f})")
        
        # POLISH: Visual Feedback (Death's ominous presence)
        if self.game_logic:
            self.game_logic.add_ui_event({
                "event_type": "screen_flash", 
                "color": "b266ff",   # purple — must be hex, not a color name
                "opacity": 0.1, 
                "duration": 0.5
            })

    def _trigger_freak_accident(self) -> str | None:
        """
        Attempts to cause unavoidable damage if entropy is high.
        Returns a message string if triggered, else None.
        Called by execute_counter_strategies.
        """
        if self.entropy < 25.0: return None
        if self.freak_accident_cooldown > 0:
            self.freak_accident_cooldown -= 1
            return None

        # Chance scales with entropy: 5% at 25, ~15% at 50, ~35% at 100
        chance = 0.05 + ((self.entropy - 25.0) / 250.0) 
        
        if random.random() < chance:
            damage = random.randint(1, 4)
            if self.game_logic:
                self.game_logic.apply_damage(damage, "freak_accident")
            
            self.freak_accident_cooldown = 5 # Turns
            
            msgs = [
                "You trip on an uneven floorboard.",
                "A loose screw cuts your arm as you pass.",
                "You are seized by a sudden, painful muscle spasm.",
                "Glass shards you didn't see slice your hand.",
                "You bite your tongue hard, drawing blood.",
                "Heat radiates from a pipe, singing your skin."
            ]
            msg = random.choice(msgs)
            self.logger.info(f"[DeathAI] Freak Accident: {msg} ({damage} dmg)")

            # POLISH: Visceral Feedback
            if self.game_logic:
                self.game_logic.add_ui_event({"event_type": "screen_shake", "intensity": "small", "duration": 0.3})
                self.game_logic.add_ui_event({"event_type": "screen_flash", "color": "ff0000", "opacity": 0.3, "duration": 0.3})

            return color_text(f"[b]{msg}[/b] (-{damage} HP)", "error", self.resource_manager)
        return None

    def get_fallback_death_narrative(self, room_data):
        """Generates a procedural death narrative using room JSON data."""
        available_props = []
        
        # Safely extract objects (handling both string lists and dict lists)
        for category in ["objects", "furniture"]:
            if category in room_data:
                for item in room_data[category]:
                    if isinstance(item, dict):
                        available_props.append(item.get("name", "fixture"))
                    else:
                        available_props.append(item)

        if not available_props:
            return "The unseen design claims you in the sterile emptiness of the room. You never even saw it coming."

        # Clean up the prop name (e.g., "waiting_room_chairs" -> "waiting room chairs")
        murder_weapon = random.choice(available_props).replace("_", " ")
        
        procedural_deaths = [
            f"A freak chain reaction turns the {murder_weapon} into a lethal hazard. You don't even have time to blink.",
            f"The room's structural integrity fails precisely where you stand. The {murder_weapon} is the last thing you see.",
            f"Death's design catches up to you. A misstep, a catastrophic failure involving the {murder_weapon}, and everything goes black."
        ]
        
        return random.choice(procedural_deaths)

    def manifest_deaths_presence(self, location: str, intensity: float = None):
        """
        Creates or escalates the Death's Breath hazard.
        The hazard always EXISTS, but only escalates once per turn to prevent
        3 state-change messages firing back-to-back.
        """
        if not self.game_logic.hazard_engine:
            return False

        # Throttle: Only one Death's Breath escalation per turn
        current_turn = self.game_logic.player.get('actions_taken', 0)
        if getattr(self, '_last_breath_escalation_turn', -1) == current_turn:
            self.logger.debug(f"DeathAI: Death's Breath already escalated this turn. Skipping.")
            return False

        self.logger.info(f"DeathAI manifesting presence in {location} (player fear: {self.game_logic.player.get('fear', 0):.2f})")

        deaths_breath_id = self.game_logic.hazard_engine.get_hazard_instance_id_by_type(location, "deaths_breath")

        if intensity is None:
            intensity = min(1.0, self.game_logic.player.get('fear', 0) * 1.5)

        if deaths_breath_id:
            hazard_inst = self.game_logic.hazard_engine.active_hazards.get(deaths_breath_id)
            curr_state = hazard_inst.get('state') if hazard_inst else None

            states = ["subtle_chill", "cold_breeze", "icy_presence", "malevolent_gust"]
            curr_idx = states.index(curr_state) if curr_state in states else 0

            # --- THE FIX: Gate the maximum state by the current level! ---
            current_level = str(self.game_logic.player.get('current_level', '1')).replace('level_', '')
            lvl = int(current_level) if current_level.isdigit() else 1
            
            max_allowed_idx = 1 # Default max: cold_breeze
            if lvl >= 2: max_allowed_idx = 2 # level 2+: icy_presence
            if lvl >= 3: max_allowed_idx = 3 # level 3+: malevolent_gust

            escalate_chance = intensity * 0.7
            if random.random() < escalate_chance and curr_idx < max_allowed_idx:
                target_state = states[curr_idx + 1]
                self.logger.info(f"DeathAI escalating Death's Breath from {curr_state} to {target_state}")
                result = self.game_logic.hazard_engine.set_hazard_state(deaths_breath_id, target_state)
                self._enforce_consequences(result)
                self._last_breath_escalation_turn = current_turn
                return True

        elif intensity > 0.3:
            if self._check_hazard_cap():
                 return False

            spawn_chance = intensity * 0.8
            if random.random() < spawn_chance:
                self.logger.info(f"DeathAI spawning new Death's Breath in {location}")
                self.game_logic.hazard_engine._add_active_hazard(
                    hazard_type="deaths_breath",
                    location=location,
                    source_trigger_id="death_ai_manifestation"
                )
                self._last_breath_escalation_turn = current_turn
                return True

        return False

    def process_death_ai_tick(self, target_state, room_data):
        """
        Enforces the 'Omen -> Trap -> Death' loop. 
        Prevents instant-kills on fast transitions.
        """
        if self.is_target_in_sights(target_state, room_data):
            
            # Phase 1: Give them the warning
            if not target_state.get("omen_experienced", False):
                self.trigger_room_omen(room_data)
                target_state["omen_experienced"] = True
                self.logger.info(f"DeathAI: Omen triggered for {target_state.get('name')} in {room_data.get('name')}.")
                return "STATE_OMEN"
                
            # Phase 2: Trap primes
            if not room_data.get("trap_armed", False):
                self.arm_room_trap(room_data)
                self.logger.info(f"DeathAI: Trap armed in {room_data.get('name')}.")
                return "STATE_ARMED"
                
            # Phase 3: Execution
            return self.execute_fatality(target_state, room_data)
            
        return "STATE_IDLE"

    def can_player_sense_death(self) -> bool:
        """
        Returns True if the current character class can perceive Death's Breath.
        Medium: Always senses it (has 'deaths_breath' in hazard_tags).
        Detective: Senses it at high perception (3+) but only at 50% chance.
        Others: Cannot sense it at all.
        """
        player = self.game_logic.player
        char_class = player.get('character_class', '')
        classes_data = self.resource_manager.get_data('character_classes', {})
        class_def = classes_data.get(char_class, {})
        affinities = class_def.get('affinities', {})
        hazard_tags = affinities.get('hazard_tags', [])
        perception = class_def.get('perception', 1)
        
        # Direct affinity — always perceive
        if 'deaths_breath' in hazard_tags or 'paranormal' in hazard_tags:
            return True
        
        # High perception — chance-based sensing
        if perception >= 3:
            return random.random() < 0.5
        
        return False

    def execute_fatality(self, target_state, room_data):
        room_name = room_data.get("name", "Unknown Room")
        active_hazards = room_data.get("hazards_present", [])
        
        # Check your master dictionary for the rich narrative
        narrative = self.get_bespoke_narrative(room_name, active_hazards)
        
        if not narrative:
            # --- CRAWLER VALIDATION & DEBUGGING ---
            # If your crawler script sets an environment variable or flag
            if getattr(self, 'IS_CRAWLER_RUN', False):
                self.logger.error(f"====== NARRATIVE GAP DETECTED ======")
                self.logger.error(f"ROOM: {room_name}")
                self.logger.error(f"HAZARDS: {active_hazards}")
                self.logger.error(f"INV: {target_state.get('inventory', [])}")
                self.logger.error(f"====================================")
                
            # --- TERMINAL UI AESTHETIC ---
            # Push a stylized error to the Kivy UI event queue
            glitch_msg = (
                f"[color=ff0000]ERR: FATAL_EXCEPTION_0xDE4D[/color]\n\n"
                f"MEMORY DUMP:\n"
                f"ADDR: {room_name}\n"
                f"TRACE: {active_hazards}\n\n"
                f"Initiating procedural termination protocol..."
            )
            self.ui_event_queue.append({
                'event_type': 'show_popup',
                'title': 'SYSTEM_FAILURE',
                'message': glitch_msg
            })
            
            # Fall back to the procedural generator created in Step 1
            narrative = self.get_fallback_death_narrative(room_data)
            
        return narrative

    def manifest_fear_in_environment(self, room_id: str):
        """Physically alter the environment based on player fear."""
        fear = self.game_logic.player.get('fear', 0.0)
        
        if fear < 0.5:
            return []
        
        room_data = self.game_logic.get_room_data(room_id)
        if not room_data:
            return []
        
        messages = []
        
        # Lights flicker/fail at high fear
        if fear >= 0.7 and room_data.get('lighting') != 'dark':
            if random.random() < (fear - 0.6) * 0.4:
                room_data['lighting'] = 'flickering' if fear < 0.85 else 'dark'
                msg = "The lights flicker violently." if fear < 0.85 else "The lights suddenly go out."
                messages.append(color_text(msg, "warning", self.resource_manager))
                self.game_logic.update_fear(custom_amount=0.05)
        
        # Temperature drops
        if fear >= 0.6 and room_data.get('temperature') != 'freezing':
            if random.random() < (fear - 0.5) * 0.3:
                room_data['temperature'] = 'cold' if fear < 0.8 else 'freezing'
                msg = "The temperature plummets. You can see your breath." if fear >= 0.8 else "The air grows noticeably colder."
                messages.append(color_text(msg, "warning", self.resource_manager))
        
        # Objects move/break
        if fear >= 0.75:
            furniture = room_data.get('furniture', [])
            if furniture and random.random() < (fear - 0.7) * 0.25:
                target_furn = random.choice(furniture)
                furn_name = target_furn.get('name', 'object')
                
                poltergeist_events = [
                    f"The {furn_name} rattles violently.",
                    f"The {furn_name} shifts position with a loud scrape.",
                    f"Something falls from the {furn_name} with a crash.",
                    f"The {furn_name} tips over on its own."
                ]
                messages.append(color_text(random.choice(poltergeist_events), "error", self.resource_manager))
                self.game_logic.update_fear(custom_amount=0.08)
        
        return messages

    def increase_aggression(self, amount: float, reason: str):
        """
        Public method to increase the AI's aggression multiplier from external events.
        This makes Death more active and dangerous in response to player failures.
        Enhanced: Adds robust logging and debugging.
        """
        if amount <= 0:
            self.logger.debug(f"DeathAI: increase_aggression called with non-positive amount ({amount}). No change.")
            return

        old_multiplier = self.current_aggression_multiplier
        self.current_aggression_multiplier = min(self.current_aggression_multiplier + amount, 5.0)

        self.logger.info(
            f"DeathAI aggression increased by {amount:.2f} due to '{reason}'. "
            f"New multiplier: {self.current_aggression_multiplier:.2f} (was {old_multiplier:.2f})"
        )

    def _escalate_immediate_threat(self, hazard_engine, params):
        """
        Create immediate danger in the player's current location.
        REVISED: If no high-impact hazards are available, escalate using synergistic hazard chains or random spawn, and return a narrative message.
        Enhanced: Adds robust logging and debugging.
        """
        messages = []
        location = params.get('location')
        self.logger.debug(f"_escalate_immediate_threat called with location={location}")

        if location:
            # Try to activate specific "Instant" threats first
            if self._activate_local_hazard("falling_marquee_letters", location):
                return [color_text("[b]LOOK OUT ABOVE![/b]", "error", self.resource_manager)]
            
            # Check Cap
            if self._check_hazard_cap():
                return []

            immediate_threats = ['sudden_collapse', 'electrical_surge', 'gas_explosion']
            hazards_data = self.game_logic.resource_manager.get_data('hazards', {})

            threat_spawned = False
            for hazard_type in immediate_threats:
                if hazard_type in hazards_data:
                    hazard_engine._add_active_hazard(
                        hazard_type=hazard_type,
                        location=location,
                        initial_state_override='imminent',
                        source_trigger_id="death_ai_escalation"
                    )
                    self.player_behavior_patterns['confidence_indicators'] = 0
                    self.player_behavior_patterns['panic_indicators'] += 3
                    self.logger.info(
                        f"[DeathAI] Immediate threat '{hazard_type}' spawned in '{location}'."
                    )
                    # --- CONSOLIDATION PATCH ---
                    messages.append(color_text("[b]Death will not be cheated![/b]", "error", self.resource_manager))
                    # --- END OF PATCH ---
                    threat_spawned = True
                    break
            if not threat_spawned:
                self.logger.info(
                    f"[DeathAI] No immediate threats available for '{location}'. Attempting synergistic escalation."
                )
                synergy_message = self._escalate_threat(location)
                if synergy_message:
                    messages.append(synergy_message)
                else:
                    self.logger.warning(
                        f"[DeathAI] Synergistic escalation failed or returned no message for '{location}'."
                    )
        else:
            self.logger.warning("[DeathAI] _escalate_immediate_threat called with no location specified.")

        # Fallback
        if self._activate_local_hazard(None, location):  # Random
            messages.append(color_text("Death tightens its grip.", "warning", self.resource_manager))

        self.logger.debug(f"_escalate_immediate_threat returning messages: {messages}")
        return messages

    
    def _contaminate_hiding_spot_enhanced(self, location: str, strategy: dict) -> bool:
        """Enhanced hiding spot contamination"""
        reason = strategy['reason']
        if 'overused_hiding_spot_' in reason:
            hiding_spot_key = reason.split('overused_hiding_spot_')[1]
            # --- START FIX ---
            # Add a defensive check to ensure the key can be split.
            if ':' not in hiding_spot_key:
                self.logger.warning(f"[DeathAI] Could not parse hiding spot key '{hiding_spot_key}' for contamination. Key must be in 'location:target' format.")
                return False
            # --- END FIX ---
            try:
                location, target = hiding_spot_key.split(':', 1)
                
                if self._check_hazard_cap(): return False

                # Spawn hazard that targets this specific hiding spot
                hazard_types = ['gas_leak', 'electrical_fault', 'structural_weakness']
                hazard_type = random.choice(hazard_types)
                
                self.game_logic.hazard_engine._add_active_hazard(
                    hazard_type, 
                    location,
                    initial_state_override="dormant",
                    target_object_override=target
                )
                
                self.logger.info(f"[DeathAI] Contaminated hiding spot: {location}:{target} with {hazard_type}")
                return True
            except ValueError:
                # --- PATCH START ---
                # Log the error instead of failing silently.
                self.logger.warning(f"[DeathAI] Could not parse hiding spot key '{hiding_spot_key}' after splitting. Ensure it is in 'location:target' format.")
                # --- PATCH END ---
                
        return False

    
    def _spawn_hazard_in_safe_zone(self, location: str, strategy: dict) -> bool:
        """Spawn hazards in locations where player feels safe"""
        # Choose hazard type based on room type and existing hazards
        existing_hazards = self.game_logic.hazard_engine.get_room_hazards_descriptions(location)

        # Avoid duplicate hazard types in same room
        existing_types = [h.get('type', '') for h in existing_hazards.values()]
        
        suitable_hazards = ['gas_leak', 'electrical_fault', 'structural_collapse', 
                          'ceiling_fan_malfunction', 'ventilation_blockage']
        available_hazards = [h for h in suitable_hazards if h not in existing_types]
        
        if available_hazards:
            if self._check_hazard_cap(): return False

            chosen_hazard = random.choice(available_hazards)
            self.game_logic.hazard_engine._add_active_hazard(
                chosen_hazard,
                location,
                initial_state_override="building_tension"
            )
            
            logging.info(f"[DeathAI] Spawned {chosen_hazard} in perceived safe zone: {location}")
            return True
            
        return False

    def _increase_qte_difficulty(self, strategy: dict) -> bool:
        """
        Increase global QTE difficulty due to player success.
        Enhanced: Adds robust logging and debugging.
        """
        old_multiplier = self.current_aggression_multiplier
        new_multiplier = min(old_multiplier * 1.2, 3.0)  # Cap at 3x difficulty

        self.logger.info(
            f"[DeathAI] _increase_qte_difficulty called. Reason: {strategy.get('reason', 'N/A')}, "
            f"Old multiplier: {old_multiplier:.2f}, New multiplier: {new_multiplier:.2f}"
        )

        self.current_aggression_multiplier = new_multiplier

        # Debug: Log strategy details
        self.logger.debug(
            f"[DeathAI] QTE difficulty increased. Strategy details: {strategy}"
        )

        if new_multiplier > old_multiplier:
            self.logger.info(
                f"[DeathAI] Aggression multiplier successfully increased to {new_multiplier:.2f}."
            )
        else:
            self.logger.warning(
                f"[DeathAI] Aggression multiplier already at cap ({new_multiplier:.2f}). No further increase."
            )

        return True
    
    def _spawn_targeted_hazard(self, location: str, strategy: dict) -> bool:
        """Spawn hazard specifically targeting high-threat locations. Adds robust logging and debugging."""
        threat_score = self.location_threat_scores[location]
        self.logger.debug(
            f"_spawn_targeted_hazard called: location={location}, threat_score={threat_score:.2f}, strategy={strategy}"
        )

        if self._check_hazard_cap(): return False

        # Higher threat = more dangerous hazard
        if threat_score >= 15.0:
            hazard_type = "catastrophic_failure"
        elif threat_score >= 10.0:
            hazard_type = "cascading_malfunction"
        else:
            hazard_type = "escalating_danger"

        self.logger.info(
            f"[DeathAI] Spawning targeted hazard '{hazard_type}' in high-threat location '{location}' (score={threat_score:.2f})"
        )

        try:
            self.game_logic.hazard_engine._add_active_hazard(
                hazard_type,
                location,
                initial_state_override="rapid_escalation"
            )
            self.logger.debug(
                f"[DeathAI] Hazard '{hazard_type}' successfully spawned in '{location}'."
            )
            return True
        except Exception as e:
            self.logger.error(
                f"[DeathAI] Failed to spawn hazard '{hazard_type}' in '{location}': {e}"
            )
            return False

    def _general_escalation(self, location: str, strategy: dict) -> bool:
        """General escalation of danger level. Adds robust logging and debugging."""
        self.logger.debug(
            f"_general_escalation called: location={location}, strategy={strategy}"
        )
        room_hazards = self.game_logic.hazard_engine.get_room_hazards_descriptions(location)
        escalated_count = 0

        # We must pull the actual hazard instance to check its master data
        for hazard_id in room_hazards.keys():
            hazard_instance = self.game_logic.hazard_engine.active_hazards.get(hazard_id)
            if not hazard_instance: continue

            master_data = hazard_instance.get('master_data', {})
            
            # --- THE REALISM FIX ---
            # If the hazard is not flagged as susceptible to supernatural acceleration, skip it.
            if not master_data.get('ai_can_accelerate', False):
                self.logger.debug(f"[DeathAI] Skipping escalation for '{hazard_id}' - requires natural physics.")
                continue

            # Accelerate hazard progression for "First Dominos"
            if 'progression_rate' in hazard_instance:
                old_rate = hazard_instance['progression_rate']
                hazard_instance['progression_rate'] *= 1.5
                escalated_count += 1
                self.logger.info(
                    f"[DeathAI] Escalated hazard '{hazard_id}' in '{location}': progression_rate {old_rate} -> {hazard_instance['progression_rate']}"
                )

        if escalated_count > 0:
            self.logger.info(f"[DeathAI] Escalated {escalated_count} hazards in {location}")
            return True

        self.logger.warning(f"[DeathAI] No valid first-domino hazards to escalate in {location}.")
        return False

    def _accelerate_hazards_by_fear(self):
        """
        If player is terrified, Death gets impatient.
        Accelerates hazards or triggers dormant ones, respecting physics.
        """
        player = self.game_logic.player
        fear = player.get('fear', 0.0)
        current_room = player.get('location')
        
        # Threshold: 60% Fear
        if fear < 0.6:
            return

        self.logger.info(f"DeathAI: Fear is high ({fear:.2f}). Accelerating entropy.")

        if not self.game_logic.hazard_engine:
            return

        active_hazards = self.game_logic.hazard_engine.get_active_hazards_for_room(current_room)
        
        for hid in active_hazards:
            hazard = self.game_logic.hazard_engine.active_hazards.get(hid)
            if not hazard: continue
            
            master = hazard.get('master_data', {})
            
            # --- THE REALISM FIX ---
            # Only fast-forward the "first dominos" (e.g. wind, loose screws)
            if not master.get('ai_can_accelerate', False):
                continue
                
            state = hazard.get('state')
            sdef = master.get('states', {}).get(state, {})
            
            # If it relies on RNG ('chance_to_progress'), FORCE IT.
            if 'chance_to_progress' in sdef:
                next_state = sdef.get('next_state')
                if next_state:
                    self.logger.info(f"DeathAI: Forcing '{hid}' progression due to fear.")
                    # Force the transition
                    res = self.game_logic.hazard_engine.set_hazard_state(hid, next_state)
                    self._enforce_consequences(res)
                    return # One acceleration per turn is enough

    def _spawn_specific_hazard(self, hazard_type: str, location: str) -> bool:
        """
        Manually spawn a specific hazard type at a location.
        """
        if self._check_hazard_cap(): return False

        try:
            self.game_logic.hazard_engine._add_active_hazard(
                hazard_type,
                location,
                initial_state_override="building_tension",
                source_trigger_id="death_ai_specific_spawn"
            )
            self.logger.info(f"[DeathAI] _spawn_specific_hazard: Spawned '{hazard_type}' at '{location}'")
            return True
        except Exception as e:
            self.logger.error(f"[DeathAI] _spawn_specific_hazard failed: {e}")
            return False

    def load_state(self, state_dict):
        """
        Loads the DeathAI state from a dictionary, ensuring types are correct.
        """
        if not state_dict:
            self.logger.warning("DeathAI.load_state called with empty state_dict.")
            return

        # Restore simple fields
        self.current_aggression_multiplier = state_dict.get('current_aggression_multiplier', 1.0)
        self.pending_counter_strategies = state_dict.get('pending_counter_strategies', [])

        # Restore collections
        def to_defaultdict(data, type_factory):
            d = collections.defaultdict(type_factory)
            d.update(data or {})
            return d

        self.location_threat_scores = to_defaultdict(state_dict.get('location_threat_scores'), float)
        self.object_threat_scores = to_defaultdict(state_dict.get('object_threat_scores'), float)
        self.room_safety_perception = to_defaultdict(state_dict.get('room_safety_perception'), float)
        
        # Restore complex patterns
        patterns = state_dict.get('player_behavior_patterns', {})
        self.player_behavior_patterns = {
            'preferred_escape_routes': collections.deque(patterns.get('preferred_escape_routes', []), maxlen=10),
            'hiding_spots_used': to_defaultdict(patterns.get('hiding_spots_used'), int),
            'item_usage_patterns': to_defaultdict(patterns.get('item_usage_patterns'), list),
            'qte_success_rate': patterns.get('qte_success_rate', 0.0),
            'qte_successes': patterns.get('qte_successes', 0),
            'qte_attempts': patterns.get('qte_attempts', 0),
            'room_visit_frequency': to_defaultdict(patterns.get('room_visit_frequency'), int),
            'search_patterns': to_defaultdict(patterns.get('search_patterns'), int),
            'confidence_indicators': patterns.get('confidence_indicators', 0),
            'panic_indicators': patterns.get('panic_indicators', 0)
        }
        
        self.logger.info("DeathAI.load_state: Brain state restored successfully.")

    def _contaminate_safe_space(self, hazard_engine, params):
        """Add hazards to rooms the player considers safe. Adds robust logging and debugging."""
        messages = []
        safe_rooms = params.get('rooms', [])
        self.logger.debug(f"_contaminate_safe_space called: safe_rooms={safe_rooms}, params={params}")

        for room in safe_rooms[:2]:  # Limit to 2 rooms per intervention
            room_data = self.game_logic.get_room_data(room)
            if not room_data:
                self.logger.warning(f"[DeathAI] No room data found for '{room}'. Skipping contamination.")
                continue

            if self._check_hazard_cap(): break

            hazard_type = self._select_contextual_hazard(room, room_data)
            if hazard_type:
                try:
                    hazard_engine._add_active_hazard(
                        hazard_type=hazard_type,
                        location=room,
                        source_trigger_id="death_ai_contamination"
                    )
                    self.logger.info(
                        f"[DeathAI] Contaminated safe room '{room}' with hazard '{hazard_type}'."
                    )
                    self.room_safety_perception[room] *= 0.6  # Significantly reduce safety feeling
                    messages.append(f"[i]Something feels different about the {room}...[/i]")
                except Exception as e:
                    self.logger.error(
                        f"[DeathAI] Failed to contaminate safe room '{room}' with hazard '{hazard_type}': {e}"
                    )
            else:
                self.logger.warning(f"[DeathAI] No suitable hazard found for safe room '{room}'.")
        self.logger.debug(f"_contaminate_safe_space returning messages: {messages}")
        return messages

    def _target_hiding_spots(self, hazard_engine, params):
        """Create hazards specifically in the player's preferred locations. Adds robust logging and debugging."""
        messages = []
        hiding_spots = params.get('rooms', [])
        self.logger.debug(f"_target_hiding_spots called: hiding_spots={hiding_spots}, params={params}")

        for room in hiding_spots[:1]:  # One hiding spot per intervention
            aggressive_hazards = ['gas_leak', 'electrical_hazard', 'structural_instability']
            hazards_data = self.game_logic.resource_manager.get_data('hazards', {})

            for hazard_type in aggressive_hazards:
                if self._check_hazard_cap(): break
                if hazard_type in hazards_data:
                    try:
                        hazard_engine._add_active_hazard(
                            hazard_type=hazard_type,
                            location=room,
                            source_trigger_id="death_ai_hiding_spot_target"
                        )
                        self.logger.info(
                            f"[DeathAI] Targeted hiding spot '{room}' with hazard '{hazard_type}'."
                        )
                        self.player_behavior_patterns['preferred_hiding_spots'].discard(room)
                        messages.append(color_text("Your sanctuary has been violated.", "error", self.resource_manager))
                        break
                    except Exception as e:
                        self.logger.error(
                            f"[DeathAI] Failed to target hiding spot '{room}' with hazard '{hazard_type}': {e}"
                        )
        self.logger.debug(f"_target_hiding_spots returning messages: {messages}")
        return messages

    def _corrupt_examined_objects(self, hazard_engine, params):
        """
        Make previously examined objects become dangerous.
        Enhanced: Logging, color_text usage, and robust fallback logic.
        """
        messages = []
        objects = params.get('objects', [])
        self.logger.info(f"DeathAI: Attempting to corrupt examined objects: {objects}")

        location = None
        if hasattr(self.game_logic, 'player') and self.game_logic.player:
            location = self.game_logic.player.get('location')
        else:
            self.logger.warning("DeathAI: Cannot get player location for object corruption")
            return messages

        if not location:
            self.logger.warning("DeathAI: Player location is None, cannot corrupt objects.")
            return messages

        for obj_name in objects[:1]:  # One object per intervention
            hazards_data = self.game_logic.resource_manager.get_data('hazards', {})
            if 'corrupted_object' not in hazards_data:
                self.logger.warning("DeathAI: 'corrupted_object' hazard type not defined in hazards data")
                hazard_type = 'environmental_hazard'  # Fallback to a generic hazard type
            else:
                hazard_type = 'corrupted_object'

            if self._check_hazard_cap(): break

            try:
                hazard_id = hazard_engine._add_active_hazard(
                    hazard_type=hazard_type,
                    location=location,
                    target_object_override=obj_name,
                    source_trigger_id="death_ai_object_corruption"
                )
                if hazard_id:
                    messages.append(color_text(f"The {obj_name} seems... different now.", "warning"))
                    self.logger.info(f"DeathAI: Corrupted object '{obj_name}' in '{location}' (hazard_id={hazard_id})")
                else:
                    self.logger.warning(f"DeathAI: Failed to create hazard for object '{obj_name}' in '{location}'")
            except Exception as e:
                self.logger.error(
                    f"DeathAI: Exception while corrupting object '{obj_name}' in '{location}': {e}"
                )
        self.logger.debug(f"_corrupt_examined_objects returning messages: {messages}")
        return messages

    def _escalate_threat(self, room_id):
        """
        Scans for dormant hazards that synergize with active ones, or just wakes random ones.
        """
        if not self.game_logic or not self.game_logic.hazard_engine:
            self.logger.warning(f"DeathAI._escalate_threat: Missing game_logic or hazard_engine for room '{room_id}'")
            return None

        self.logger.info(f"DeathAI evaluating threat escalation for room: '{room_id}'")
        synergies = self.game_logic.resource_manager.get_data("hazard_synergies", {})
        all_hazards_master = self.game_logic.resource_manager.get_data("hazards", {})
        spawnable_hazards = [h for h, d in all_hazards_master.items() if d.get("can_be_spawned")]

        existing_hazards_in_room = self.game_logic.hazard_engine.get_hazards_in_location(room_id)
        self.logger.debug(f"Existing hazards in room '{room_id}': {existing_hazards_in_room}")

        for existing_hazard in existing_hazards_in_room:
            existing_hazard_type = all_hazards_master.get(existing_hazard.get("type"), {}).get("hazard_class")
            if existing_hazard_type in synergies:
                possible_synergy_types = synergies[existing_hazard_type]
                for hazard_key in spawnable_hazards:
                    if all_hazards_master.get(hazard_key, {}).get("hazard_class") in possible_synergy_types:
                        self.logger.info(f"Synergy found! Existing hazard '{existing_hazard_type}' pairs with '{hazard_key}'.")
                        return self._activate_local_hazard(hazard_key, room_id)

        self.logger.info("No synergistic opportunity found. Attempting to wake a dormant hazard.")
        # Fallback: Wake ANY dormant hazard
        if self.game_logic.hazard_engine.activate_dormant_hazard(room_id):
            return "The shadows lengthen. Something has shifted."

        self.logger.warning("No dormant hazards available to wake.")
        return None

    def _activate_local_hazard(self, hazard_type, room_id):
        """
        Attempts to wake a specific dormant hazard. 
        Returns True/Message if successful, None otherwise.
        """
        if self.game_logic.hazard_engine.activate_dormant_hazard(room_id, hazard_type):
            self.logger.info(f"DeathAI activated existing '{hazard_type}' in '{room_id}'.")
            
            # Fetch a creepy omen message
            omen_messages = self.game_logic.resource_manager.get_data("omen_messages", [])
            msg = random.choice(omen_messages) if omen_messages else "The room feels suddenly hostile."
            return msg
            
        self.logger.debug(f"DeathAI failed to find dormant '{hazard_type}' in '{room_id}'.")
        return None

    def get_threat_weighted_location(self, candidate_locations: list) -> str:
        """
        Select a location for new hazard spawn based on threat weighting.
        Higher threat score = higher chance of selection.
        Adds robust logging and debugging.
        """
        self.logger.debug(f"get_threat_weighted_location called: candidate_locations={candidate_locations}")
        if not candidate_locations:
            self.logger.warning("get_threat_weighted_location called with empty candidate_locations.")
            return None

        weights = []
        for location in candidate_locations:
            threat_score = self.location_threat_scores[location]
            safety_perception = self.room_safety_perception[location]
            combined_weight = threat_score + (safety_perception * 2.0)
            weights.append(max(combined_weight, 0.1))  # Minimum weight of 0.1
            self.logger.debug(
                f"Location '{location}': threat_score={threat_score:.2f}, safety_perception={safety_perception:.2f}, combined_weight={combined_weight:.2f}"
            )

        total_weight = sum(weights)
        self.logger.debug(f"Total weight for selection: {total_weight:.2f}, weights={weights}")

        if total_weight == 0:
            selected = random.choice(candidate_locations)
            self.logger.info(f"All weights zero, randomly selected '{selected}'")
            return selected

        rand_value = random.uniform(0, total_weight)
        cumulative_weight = 0

        for i, weight in enumerate(weights):
            cumulative_weight += weight
            if rand_value <= cumulative_weight:
                selected_location = candidate_locations[i]
                self.logger.info(
                    f"[DeathAI] Selected {selected_location} for hazard spawn "
                    f"(threat: {self.location_threat_scores[selected_location]:.2f}, "
                    f"safety: {self.room_safety_perception[selected_location]:.2f})"
                )
                return selected_location

        self.logger.warning("Weighted selection fell through; returning last candidate.")
        return candidate_locations[-1]  # Fallback
    
    def get_status_report(self) -> dict:
        """Return current AI status for debugging with robust logging."""
        self.logger.debug("get_status_report called.")
        top_threat_locations = dict(sorted(
            self.location_threat_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5])
        top_safe_perception_locations = dict(sorted(
            self.room_safety_perception.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5])
        report = {
            'top_threat_locations': top_threat_locations,
            'top_safe_perception_locations': top_safe_perception_locations,
            'pending_strategies': len(self.pending_counter_strategies),
            'active_strategies': len(self.active_strategies),  # Legacy compatibility
            'aggression_multiplier': self.current_aggression_multiplier,
            'qte_success_rate': self.player_behavior_patterns['qte_success_rate']
        }
        self.logger.debug(f"get_status_report: {report}")
        return report

    def get_threat_analysis(self):
        """Return current threat analysis for debugging/display (legacy compatibility) with robust logging."""
        self.logger.debug("get_threat_analysis called.")
        analysis = {
            'location_threats': dict(self.location_threat_scores),
            'object_threats': dict(self.object_threat_scores),
            'safety_perception': dict(self.room_safety_perception),
            'behavior_pattern': self.player_behavior_patterns,
            'active_strategies': len(self.active_strategies),
            'last_intervention': self.last_intervention_turn
        }
        self.logger.debug(f"get_threat_analysis: {analysis}")
        return analysis

    def get_forced_hazard_activations(self, level_id, current_level_rooms):
        """
        Decide which hazards to force-activate at the start of a level.
        Returns a list of dicts with hazard activation parameters.
        This can use any AI logic or heuristics you want.
        Enhanced: Adds robust logging and debugging.
        """
        self.logger.debug(f"get_forced_hazard_activations called: level_id={level_id}, rooms={list(current_level_rooms.keys())}")
        activations = []

        # Example logic: Always spawn at least one electrical hazard in a utility room
        for room_name, room_data in current_level_rooms.items():
            self.logger.debug(f"Checking room '{room_name}' for gas lines and faulty wiring hazard.")
            if room_data.get("has_gas_lines") and "faulty_wiring" in self.game_logic.resource_manager.get_data("hazards", {}):
                activation = {
                    "hazard_type": "faulty_wiring",
                    "location": room_name,
                    "initial_state_override": None,
                    "target_object_override": None,
                    "support_object_override": None,
                    "source_trigger_id": "death_ai_forced_activation"
                }
                activations.append(activation)
                self.logger.info(f"Forced activation: {activation}")
                break  # Only one for demo; remove break for more

        # Example: If player has been too successful, spawn a hazard in a "safe" room
        safe_rooms = [room for room, score in self.room_safety_perception.items() if score > 2.0]
        self.logger.debug(f"Safe rooms with high safety perception: {safe_rooms}")
        if safe_rooms:
            chosen_room = random.choice(safe_rooms)
            activation = {
                "hazard_type": "gas_leak",
                "location": chosen_room,
                "initial_state_override": "building_tension",
                "target_object_override": None,
                "support_object_override": None,
                "source_trigger_id": "death_ai_forced_activation"
            }
            activations.append(activation)
            self.logger.info(f"Forced activation in safe room: {activation}")

        self.logger.debug(f"get_forced_hazard_activations returning: {activations}")
        # You can add more sophisticated logic here based on threat analysis, etc.
        return activations


    def _apply_high_fear_effects(self, description, room_name):
        """
        Applies high fear level modifications to room descriptions.
        """
        fear_modifications = {
            "Living Room": " The shadows in the corners seem to move when you're not looking directly at them.",
            "Kitchen": " Every creak of the house makes you jump. The silence feels oppressive.",
            "Main Basement Area": " Your breathing echoes unnaturally. Something feels fundamentally wrong here.",
            "MRI Scan Room": " The metallic surfaces seem to pulse with an otherworldly energy.",
            "Hospital Morgue": " The cold air carries whispers that might just be your imagination."
        }
        fear_addition = fear_modifications.get(room_name, " Your heart pounds as anxiety grips you.")
        return description + fear_addition

    def _apply_medium_fear_effects(self, description, room_name):
        """
        Applies medium fear level modifications to room descriptions.
        """
        fear_modifications = {
            "Living Room": " The room feels unnaturally quiet.",
            "Kitchen": " You can't shake the feeling that you're being watched.",
            "Main Basement Area": " The air feels heavy and oppressive.",
            "MRI Scan Room": " The machinery seems more ominous than before.",
            "Hospital Morgue": " The cold seems to seep into your bones."
        }
        fear_addition = fear_modifications.get(room_name, " You feel on edge.")
        return description + fear_addition

    def _apply_environmental_effects(self, description, room_name):
        """
        Applies environmental effects like temperature, lighting, etc.
        """
        rooms_data = self.game_logic.resource_manager.get_data('rooms', {})
        cold_rooms = []
        dark_rooms = []
        for level_rooms in rooms_data.values():
            for room_id, room_data in level_rooms.items():
                if (room_data.get('temperature') == 'cold' or 
                    'morgue' in room_data.get('name', '').lower() or
                    'basement' in room_data.get('name', '').lower()):
                    cold_rooms.append(room_id)
                if (room_data.get('lighting') == 'dark' or
                    'basement' in room_data.get('name', '').lower()):
                    dark_rooms.append(room_id)
        # Apply cold environment effects
        if room_name in cold_rooms and self.game_logic.player.get('temperature_status') != 'warm':
            description += " The cold air makes you shiver."
        # Apply dark environment effects
        if (room_name in dark_rooms and 
            not self._player_has_active_light_source() and
            self.game_logic.player.get('lighting_status') != 'illuminated'):
            description += " The darkness presses in around you."
        return description


    def _handle_mri_control_interaction(self, item_data, rule, messages):
        """
        Handles the special MRI control desk interaction.
        """
        if not self.hazard_engine:
            messages.append(color_text("Error: Hazard system offline for MRI interaction.", "error"))
            return {"death": False, "turn_taken": False}
        
        # Get hazards data from resource manager
        hazards_data = self.game_logic.resource_manager.get_data('hazards', {})
        
        # Find MRI hazard type
        mri_hazard_type = None
        for hazard_type, hazard_data in hazards_data.items():
            if 'MRI' in hazard_type or 'mri' in hazard_type.lower():
                mri_hazard_type = hazard_type
                break
        
        if not mri_hazard_type:
            messages.append(color_text("Error: MRI hazard type not found in hazards data.", "error"))
            return {"death": False, "turn_taken": False}
        
        # Find MRI hazard instance
        mri_hazard_id = None
        for hid, h_inst in self.hazard_engine.active_hazards.items():
            if h_inst.get('type') == mri_hazard_type:
                mri_hazard_id = hid
                break
        
        if not mri_hazard_id:
            messages.append(color_text("Error: MRI machine hazard not found.", "error"))
            return {"death": False, "turn_taken": False}
        
        mri_hazard = self.hazard_engine.active_hazards[mri_hazard_id]
        current_state = mri_hazard.get("state")
        allowed_deactivation_states = rule.get("mri_states_can_deactivate", [])
        
        if current_state in allowed_deactivation_states:
            # Deactivate MRI
            self.hazard_engine.set_hazard_state(mri_hazard_id, "safely_powered_down", messages)
            success_msg = rule.get("message_success", "You swipe the key card. The MRI machine powers down with a final whine.")
            messages.append(color_text(success_msg.format(item_name=item_data.get("name", "key card")), "success"))
            return {"death": False, "turn_taken": True}
        else:
            # Already off or can't be deactivated
            fail_msg = rule.get("message_fail_mri_state", "The MRI machine is not in a state that can be remotely deactivated.")
            messages.append(color_text(fail_msg, "warning"))
            return {"death": False, "turn_taken": False}
        
    def escalate_environment(self, aggression_level):
        """
        Dynamically alter the environment based on Death's aggression.
        aggression_level: float from 0.0 (calm) to 1.0 (maximum aggression)
        Enhanced: Adds robust logging and debugging.
        """
        self.logger.debug(f"escalate_environment called: aggression_level={aggression_level:.2f}")
        for room_name, room in self.game_logic.current_level_rooms.items():
            effects = {}
            if aggression_level > 0.3:
                effects['temperature'] = 'cold'
            if aggression_level > 0.6:
                effects['lighting'] = 'flickering'
            if aggression_level > 0.85:
                effects['lighting'] = 'dark'
            if effects:
                self.logger.info(
                    f"Applying environmental effects to '{room_name}': {effects}"
                )
                self.game_logic.apply_environmental_effect(room_name, effects)

        # Subtle object changes: randomly crack mirrors, tilt pictures, etc.
        for room_name, room in self.game_logic.current_level_rooms.items():
            for furn in room.get('furniture', []):
                # Example: crack mirrors
                if furn.get('type') == 'mirror' and aggression_level > 0.5:
                    chance = aggression_level - 0.5
                    rand_val = random.random()
                    self.logger.debug(
                        f"Checking mirror '{furn.get('name')}' in '{room_name}': chance={chance:.2f}, rand_val={rand_val:.2f}"
                    )
                    if rand_val < chance:
                        self.logger.info(
                            f"Cracking mirror '{furn['name']}' in '{room_name}' due to aggression."
                        )
                        self.game_logic.set_object_examine_overlay(
                            room_name, furn['name'],
                            "[color=ccccff]The mirror is now cracked, a jagged line splitting your reflection.[/color]"
                        )
                # Example: tilt picture frames
                if furn.get('type') == 'picture_frame' and aggression_level > 0.4:
                    chance = aggression_level - 0.4
                    rand_val = random.random()
                    self.logger.debug(
                        f"Checking picture frame '{furn.get('name')}' in '{room_name}': chance={chance:.2f}, rand_val={rand_val:.2f}"
                    )
                    if rand_val < chance:
                        self.logger.info(
                            f"Tilting picture frame '{furn['name']}' in '{room_name}' due to aggression."
                        )
                        self.game_logic.set_object_examine_overlay(
                            room_name, furn['name'],
                            "[color=ffffcc]The picture frame is now hanging crooked, as if disturbed by unseen hands.[/color]"
                        )

    def on_turn(self):
        """Call this each turn to escalate environment based on aggression."""
        self._finale_logged_this_turn = False  # Reset per-turn log throttle
        aggression = getattr(self, 'aggression', 0.0)
        self.logger.debug(f"on_turn called: aggression={aggression:.2f}")
        self.escalate_environment(aggression)
        self.logger.debug("on_turn completed environment escalation.")
        self._accelerate_hazards_by_fear()

    def analyze_room_for_threat_potential(self, room_name: str) -> float:
        """
        Analyze a room for its threat potential based on hazards, safety perception, and behavioral patterns.
        Returns a float score representing threat potential.
        Enhanced: Adds robust logging and debugging.
        """
        self.logger.debug(f"analyze_room_for_threat_potential called: room_name={room_name}")
        hazards = self.game_logic.get_room_hazards_descriptions(room_name)
        hazard_score = sum(h.get('threat_level', 1.0) for h in hazards.values()) if hazards else 0.0
        safety_score = self.room_safety_perception.get(room_name, 0.0)
        visit_freq = self.player_behavior_patterns['room_visit_frequency'].get(room_name, 0)
        threat_score = self.location_threat_scores.get(room_name, 0.0)
        total_score = hazard_score + (threat_score * 1.5) - (safety_score * 0.5) + (visit_freq * 0.2)
        self.logger.debug(
            f"Room '{room_name}': hazard_score={hazard_score:.2f}, threat_score={threat_score:.2f}, "
            f"safety_score={safety_score:.2f}, visit_freq={visit_freq}, total_score={total_score:.2f}"
        )
        return total_score

    def get_omen_message(self) -> str:
        """
        Generate an omen message based on pending counter-strategies.
        Returns None if no omen should be generated.
        Enhanced: Adds robust logging and debugging.
        """
        self.logger.debug("get_omen_message called.")
        if not self.pending_counter_strategies:
            self.logger.debug("No pending counter-strategies; no omen message generated.")
            return None
        strategy = self.pending_counter_strategies[0]
        reason = strategy.get('reason', '')
        location = strategy.get('location', '')
        self.logger.debug(f"Pending strategy for omen: reason={reason}, location={location}")
        if reason.startswith('player_feels_too_safe_'):
            loc = reason.split('player_feels_too_safe_')[1]
            msg = f"You sense you are no longer safe in the {loc}."
            self.logger.info(f"Omen message generated: {msg}")
            return msg
        elif reason.startswith('location_threat_high_'):
            loc = reason.split('location_threat_high_')[1]
            msg = f"Something dark is drawn towards the {loc}."
            self.logger.info(f"Omen message generated: {msg}")
            return msg
        elif reason == 'player_too_successful_at_qtes':
            msg = "You feel a growing malice watching your every move."
            self.logger.info(f"Omen message generated: {msg}")
            return msg
        # Default fallback
        msg = "A chill runs down your spine, as if something is about to happen..."
        self.logger.info(f"Omen message generated (default): {msg}")
        return msg
    
    def get_save_state(self) -> dict:
        """Get the current state for saving, converting types for JSON serialization."""
        return {
            "current_aggression_multiplier": self.current_aggression_multiplier,
            "pending_counter_strategies": self.pending_counter_strategies,
            
            # Serialize defaultdicts as dicts
            "location_threat_scores": dict(self.location_threat_scores),
            "object_threat_scores": dict(self.object_threat_scores),
            "room_safety_perception": dict(self.room_safety_perception),
            
            # Serialize patterns
            "player_behavior_patterns": {
                'preferred_escape_routes': list(self.player_behavior_patterns['preferred_escape_routes']),
                'hiding_spots_used': dict(self.player_behavior_patterns['hiding_spots_used']),
                'item_usage_patterns': dict(self.player_behavior_patterns['item_usage_patterns']),
                'qte_success_rate': self.player_behavior_patterns['qte_success_rate'],
                'qte_successes': self.player_behavior_patterns['qte_successes'],
                'qte_attempts': self.player_behavior_patterns['qte_attempts'],
                'room_visit_frequency': dict(self.player_behavior_patterns['room_visit_frequency']),
                'search_patterns': dict(self.player_behavior_patterns['search_patterns']),
                'confidence_indicators': self.player_behavior_patterns['confidence_indicators'],
                'panic_indicators': self.player_behavior_patterns['panic_indicators']
            }
        }