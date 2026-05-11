# fd_terminal/qte_engine.py

from bdb import effective
import logging, copy, time
import random
import math
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.properties import ListProperty


class QTE_Engine(Widget):
    def __init__(self, resource_manager=None, game_logic_ref=None, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger("QTE_Engine")
        self.resource_manager = resource_manager
        self.game_logic = game_logic_ref

        # Only load qte_definitions if resource_manager is available
        if self.resource_manager:
            self.qte_definitions = self.resource_manager.get_data('qte_definitions', {})
        else:
            self.qte_definitions = {}

        self.active_qte = None
        self.timeout_event = None
        self.sequence_widget = None
        self.is_dismissed = False
        
        # Mouse tracking for spiral detection
        self.mouse_positions = []
        self.spiral_center = None
        self.spiral_radius_history = []
        self.spiral_angle_total = 0
        self.debug_success_rate = None  # When set, overrides normal success calculation
        Window.bind(on_mouse_down=self._on_mouse_down)
        Window.bind(on_key_down=self._on_key_down)
        self.logger.info("QTE Engine forged and definitions loaded.")

    def start_qte(self, qte_type, context=None):
        self.logger.info(f"Starting QTE: {qte_type}")
        if not self.game_logic or not self.resource_manager:
            return

        # --- 1. Resolve Lists and 'random' keywords FIRST ---
        if isinstance(qte_type, list):
            qte_type = random.choice(qte_type)
            self.logger.info(f"QTE selected from list: '{qte_type}'")

        if qte_type == 'random':
            if not self.qte_definitions:
                self.logger.error("Cannot pick 'random' QTE: No definitions loaded.")
                return
            qte_type = random.choice(list(self.qte_definitions.keys()))
            self.logger.info(f"Random QTE resolved to: '{qte_type}'")

        # --- 2. Look up the definition ---
        blueprint = self.qte_definitions.get(qte_type)

        # --- 3. Dynamic Dictionary Fallback ---
        if not blueprint:
            if context and context.get("input_type"):
                self.logger.info(f"Using dynamic QTE definition for '{qte_type}'.")
                blueprint = context  # Use the gauntlet blueprint we built!
            else:
                self.logger.error(f"Could not find QTE definition for type: '{qte_type}'")
                return

        # --- 4. Safety Checks ---
        if self.game_logic and getattr(self.game_logic, 'is_transitioning', False):
            self.logger.warning(f"Blocked start of QTE '{qte_type}' because a level transition is in progress.")
            return

        if self.active_qte:
            self.logger.warning(f"Cannot start QTE '{qte_type}'; a QTE is already active.")
            return

        # --- 5. Merge and Build Final Data ---
        final_qte_data = copy.deepcopy(blueprint)
        if context and blueprint is not context:
            final_qte_data.update(context)
        
        # Resolve character overrides
        effective = self._resolve_character_overrides(final_qte_data)
        if 'effective_target_mash_count' not in effective:
            effective['effective_target_mash_count'] = self._effective_mash_target(final_qte_data)
        final_qte_data.update(effective)

        # --- Auto-generate required_sequence for sequence-type QTEs ---
        if final_qte_data.get('input_type') in ('sequence', 'pattern', 'directional'):
            labels = final_qte_data.get('button_labels', [])
            
            # Always randomize if we have button labels to choose from
            if labels:
                seq_len = final_qte_data.get('sequence_length', min(4, len(labels)))
                generated = [random.choice(labels) for _ in range(seq_len)]
                
                # Overwrite the JSON fallback with the newly generated sequence
                final_qte_data['required_sequence'] = generated
                final_qte_data['required_pattern'] = generated
                
                self.logger.info(f"Auto-generated required_sequence for '{qte_type}': {generated}")

        # --- 6. Set Runtime State ---
        final_qte_data['runtime_state'] = {
            'mash_count': 0,
            'tap_count': 0,
            'alternations_done': 0,
            'last_alt_key': None,
            'hold_start': None,
            'start_time': time.time(),
            'effective_target_mash_count': final_qte_data.get('effective_target_mash_count'),
        }

        self.active_qte = final_qte_data
        self.active_qte.setdefault('runtime_state', {})

        if self.game_logic:
            self.game_logic.player['qte_active'] = True

        # Reset mouse tracking
        if final_qte_data.get('input_type') == 'spiral':
            self.mouse_positions = []
            self.spiral_center = None
            self.spiral_radius_history = []
            self.spiral_angle_total = 0

        # --- 7. Prepare Context for UI ---
        final = self.active_qte
        prompt = final.get('ui_prompt_message') or final.get('description') or "React quick, bitch!"
        duration = float(final.get('duration') or final.get('default_duration') or 8.0)
        input_type = final.get('input_type', 'word')

        pass_through = (
            "ui_type",
            "ui_prompt_message", "description", "button_labels", "button_colors",
            "choices", "choices_default", "correct_choice", "input_to_next_state",
            "valid_responses", "expected_input_word",
            "required_sequence", "required_pattern", "required_code",
            "target_mash_count", "target_mash_count_default",
            "required_tap_count", "required_tap_count_default",
            "effective_target_mash_count", "required_key",
            "keys_default", "target_alternations_default",
            "beat_interval", "target_beats", "beat_speed", "timing_window", "target_zone",
            "wait_time_range", "path_type", "tolerance", "decay_rate",
            "required_hold_time", "required_hold_time_default",
            "release_window", "release_window_default",
        )
        
        qctx = {k: v for k, v in final.items() if k in pass_through}
        qctx["qte_source_hazard_id"] = final.get("qte_source_hazard_id")
        qctx["input_type"] = input_type

        self.logger.info(f"QTE '{qte_type}' started. Duration: {duration:.1f}s. Input: {input_type}.")

        # Announce to UI
        if self.game_logic:
            self.game_logic.add_ui_event({
                "event_type": "show_qte",
                "qte_type": qte_type,
                "input_type": input_type,
                "prompt": prompt,
                "duration": duration,
                "qte_context": qctx
            })

        # Guard: cancel any leftover timeout from a previous QTE
        if self.timeout_event:
            self.logger.warning("start_qte: Cancelling stale timeout_event before starting new QTE.")
            self.timeout_event.cancel()
            self.timeout_event = None

        self.timeout_event = Clock.schedule_once(self._on_qte_timeout, duration)
        self.is_dismissed = False # Reset dismissed state on new start

    def get_time(self):
        # Use this for timing, so you can mock/replace if needed
        return time.time()

    def handle_qte_input(self, player_input):
        if not self.active_qte or self.is_dismissed:
            return None

        q = self.active_qte
        qtype = (q.get('input_type') or '').lower()
        result = None

        # Route dictionary events
        if isinstance(player_input, dict):
            event = (player_input.get('event') or '').strip().lower()
            # Dispatch map
            handlers = {
                'submit_text': self._evt_submit_text,
                'mash_press': self._evt_mash_press,
                'tap': self._evt_tap,
                'sequence_input': self._evt_sequence_input,
                'correct_key': lambda p: self.resolve_qte(success=True),
                'wrong_key':   lambda p: self.resolve_qte(success=False),
                'hold_release': self._evt_hold_release,
                'hold_start': lambda p: None,  # Acknowledge hold start, no resolution yet
                'choice_selected': self._evt_choice_selected, 
                'alternation': self._evt_alternation,
                'alternation_success': lambda p: self.resolve_qte(success=True),
                'reaction_tap': self._evt_reaction_tap,
                'rhythm_tap': self._evt_rhythm_tap,
                'memory_input': self._evt_memory_input,
                'gauge_update': self._evt_gauge_update,
                'drag_complete': lambda p: self.resolve_qte(success=True),
                'drag_fail':     lambda p: self.resolve_qte(success=False),
                'spiral_complete': lambda p: self.resolve_qte(success=True),
                'spiral_move':  self._evt_spiral_move,
                'spiral_reset': self._evt_spiral_reset,
                'trace_start': lambda p: None,  # Acknowledge start, no resolution
                'trace_move': self._evt_trace_move,
                'trace_end': self._evt_trace_end,
                'trace_complete': lambda p: self.resolve_qte(success=True),
                'trace_fail':     lambda p: self.resolve_qte(success=False),
                'aim_success': lambda p: self.resolve_qte(success=True),
                'key_press': self._evt_key_press, 
            }
            handler = handlers.get(event)
            if handler:
                result = handler(player_input)

        # Route string inputs (legacy/cli)
        else:
            text = str(player_input).strip().lower()
            result = self._type_dispatch(qtype, text)
            
        return result

    # ---- Event handlers (dict payload) ----

    def _evt_spiral_reset(self, payload):
        """Reset spiral tracking state when player starts a new drawing attempt."""
        self.mouse_positions = []
        self.spiral_center = None
        self.spiral_radius_history = []
        self.spiral_angle_total = 0
        return None

    def _evt_spiral_move(self, payload):
        """Process a touch-move coordinate from the SpiralCanvas widget."""
        x = payload.get('x', 0)
        y = payload.get('y', 0)
        return self._handle_mouse_spiral(x, y)

    def _evt_alternation(self, payload):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        new_count = payload.get('count', rs.get('alternations_done', 0))
        # Only advance if the count increased (correct button was pressed by widget)
        if new_count > rs.get('alternations_done', 0):
            rs['alternations_done'] = new_count
        target = int(q.get('target_alternations_default', q.get('target_alternations', 12)))
        if rs['alternations_done'] >= target:
            return self.resolve_qte(success=True)
        return None

    def _evt_rhythm_tap(self, payload):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        rs.setdefault('tap_results', [])
        on_time = payload.get('on_time', payload.get('predicted_success', False))
        rs['tap_results'].append(on_time)
        target = int(q.get('target_beats', q.get('target_beats_default', 5)))
        required_accuracy = float(q.get('required_accuracy', q.get('required_accuracy_default', 0.7)))
        
        if len(rs['tap_results']) >= target:
            hits = sum(1 for t in rs['tap_results'] if t)
            accuracy = hits / float(target)
            self.logger.info(f"Rhythm complete: {hits}/{target} ({accuracy:.2f}) — need {required_accuracy:.2f}")
            return self.resolve_qte(success=(accuracy >= required_accuracy))
        return None

    def _evt_choice_selected(self, payload):
        q = self.active_qte
        choice = str(payload.get('choice', '')).lower()
        mapping = q.get('input_to_next_state') or {}
        choices = q.get('choices') or q.get('choices_default') or []
        correct = (q.get('correct_choice') or q.get('correct_choice_default'))
        
        if choice and mapping and choice in mapping:
            q['next_state_after_qte_success'] = mapping[choice]
            return self.resolve_qte(success=True)
        
        if correct:
            return self.resolve_qte(success=(choice == str(correct).lower()))
        
        # If no correct choice specified, any valid choice succeeds
        return self.resolve_qte(success=(choice in [str(c).lower() for c in choices]))

    def _evt_submit_text(self, payload):
        q = self.active_qte
        # Safety guard: prevent silent crashes if UI fires after QTE resolves
        if not q: 
            return None
            
        qtype = (q.get('input_type') or '').lower()

        # --- CRITICAL FIX: Intercept generic UI button clicks ---
        # The generic popup button triggers 'submit_text'. If the QTE is not a text QTE,
        # redirect the action to the correct handler.
        if qtype == 'mash':
            rs = q.get('runtime_state', {})
            return self._evt_mash_press({'event': 'mash_press', 'count': rs.get('mash_count', 0) + 1})
        elif qtype in ('tap', 'tap_count', 'precision_tap_count', 'rhythm'):
            rs = q.get('runtime_state', {})
            return self._evt_tap({'event': 'tap', 'count': rs.get('tap_count', 0) + 1})
        elif qtype in ('single_key', 'reaction', 'aim', 'aim_click'):
            # Instant success for generic tap on these types
            return self.resolve_qte(success=True)

        # Debug: identify how submit was triggered (button click vs Enter/Return)
        trigger = str(payload.get('trigger') or payload.get('source') or '').strip().lower()
        key = payload.get('key')

        if trigger in ('submit', 'submit_button', 'button', 'click', 'mouse_click'):
            self.logger.debug("QTE text submit triggered by submit button click.")
        elif trigger in ('enter', 'return', 'keyboard_enter', 'keyboard_return') or key in (13, 'enter', 'return'):
            self.logger.debug("QTE text submit triggered by Enter/Return key.")
        else:
            self.logger.debug(
                f"QTE text submit triggered (unknown source). "
                f"payload_keys={list(payload.keys())}"
            )

        typed = (payload.get('text') or '').strip().lower()
        expected = (q.get('expected_input_word') or '').strip().lower()
        alt = (q.get('alternative_input') or '').strip().lower()
        valids = [v.strip().lower() for v in (q.get('valid_responses') or [])]
        allowed = {v for v in [expected, alt] + valids if v}

        if not allowed:
            return self.resolve_qte(success=(len(typed) > 0))
        return self.resolve_qte(success=(typed in allowed))

    def _evt_mash_press(self, payload):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        rs['mash_count'] = int(payload.get('count', rs.get('mash_count', 0) + 1))
        target = (rs.get('effective_target_mash_count') or q.get('target_mash_count') or 15)
        if rs['mash_count'] >= int(target):
            return self.resolve_qte(success=True)
        return None

    def _evt_tap(self, payload):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        rs['tap_count'] = int(payload.get('count', rs.get('tap_count', 0) + 1))
        qtype = (q.get('input_type') or '').lower()
        if qtype in ('aim', 'aim_click', 'aim_and_click'):
            # For aim QTEs, required_tap_count is how many targets to hit
            need = int(q.get('required_tap_count', q.get('required_tap_count_default', q.get('target_hits', 3))))
        else:
            need = int(q.get('required_tap_count', q.get('required_tap_count_default', 10)))
        if rs['tap_count'] >= need:
            return self.resolve_qte(success=True)
        return None

    def _evt_sequence_input(self, payload):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        rs['key_sequence'] = list(payload.get('sequence', []))
        required = [s.strip().lower() for s in (q.get('required_sequence') or q.get('required_pattern') or [])]
        if not required:
            return None
        entered = [s.lower() for s in rs['key_sequence']]
        # Only resolve when the player has entered as many tokens as required
        if len(entered) >= len(required):
            return self.resolve_qte(success=(entered[:len(required)] == required))
        # Partial: check if what they've entered so far is a valid prefix
        # If not a valid prefix, let the UI widget signal a bad press but don't fail the QTE yet —
        # the popup's Undo/Clear lets them correct it.
        return None

    def _evt_single_key(self, success: bool, payload: dict):
        self.logger.info(f"Single Key QTE {'succeeded' if success else 'failed'}.")
        return self.resolve_qte(success=success)

    def _evt_reaction_tap(self, payload):
        """Handle reaction button tap from ReactionWidget."""
        reaction_time = float(payload.get('reaction_time', 999.0))
        # 999.0 means early tap (before green) — always fail
        if reaction_time >= 99.0:
            self.logger.info(f"Reaction QTE: Early tap (before activation). Fail.")
            return self.resolve_qte(success=False)
        # Success if they tapped within a reasonable window after green
        # Generous: anything under 2.0 seconds reaction time counts
        max_reaction = 2.0
        success = reaction_time <= max_reaction
        self.logger.info(f"Reaction QTE: reaction_time={reaction_time:.3f}s, max={max_reaction}s, success={success}")
        return self.resolve_qte(success=success)

    def _evt_hold_release(self, payload):
        q = self.active_qte
        dur = float(payload.get('duration', 0.0))
        qtype = q.get('input_type')

        if qtype in ('hold_release', 'hold_and_release'):
            window = (q.get('release_window') or q.get('release_window_default') or [0.6, 0.8])
            lo_frac, hi_frac = float(window[0]), float(window[1])
            
            # Interpret as fractions of total QTE duration if values are <= 1.0
            # This converts e.g. [0.6, 0.8] with a 5s QTE to [3.0, 4.0] seconds
            if lo_frac <= 1.0 and hi_frac <= 1.0:
                total_duration = float(q.get('duration', q.get('default_duration', 5.0)))
                lo = lo_frac * total_duration
                hi = hi_frac * total_duration
            else:
                # Already in absolute seconds
                lo, hi = lo_frac, hi_frac
            
            self.logger.info(f"Hold-Release QTE: held={dur:.2f}s, window=[{lo:.2f}, {hi:.2f}]s, success={lo <= dur <= hi}")
            return self.resolve_qte(success=(lo <= dur <= hi))

        # Simple hold-to-threshold
        need = float(q.get('required_hold_time', q.get('required_hold_time_default', 2.0)))
        self.logger.info(f"Hold QTE: held={dur:.2f}s, needed={need:.2f}s, success={dur >= need}")
        return self.resolve_qte(success=(dur >= need))

    def _evt_key_press(self, payload):
        """Bridge for raw key_press events from the keyboard handler.
        Routes to the correct logic based on the active QTE's input_type."""
        q = self.active_qte
        if not q:
            return None
        key = (payload.get('key') or '').lower()
        qtype = (q.get('input_type') or '').lower()

        if qtype in ('alternate', 'balance'):
            rs = q.get('runtime_state', {})
            keys = q.get('keys_default', q.get('keys', ['a', 'd']))
            if not keys or len(keys) < 2:
                keys = ['a', 'd']
            expected_idx = rs.get('alternations_done', 0) % len(keys)
            expected_key = str(keys[expected_idx]).lower()

            if key == expected_key:
                rs['alternations_done'] = rs.get('alternations_done', 0) + 1
                target = int(q.get('target_alternations_default',
                                q.get('target_alternations', 12)))
                self.logger.info(
                    f"key_press->alternate: key={key}, done={rs['alternations_done']}/{target}")
                if rs['alternations_done'] >= target:
                    return self.resolve_qte(success=True)
            else:
                self.logger.debug(
                    f"key_press->alternate: wrong key={key}, expected={expected_key}")
            return None

        elif qtype == 'single_key':
            req = (q.get('required_key') or '').lower()
            if req:
                return self.resolve_qte(success=(key == req))
            # No specific key required — any key succeeds
            return self.resolve_qte(success=bool(key))

        # Fallback: unrecognized input_type, ignore
        self.logger.debug(f"key_press: no handler for input_type={qtype}, ignoring.")
        return None

    def _evt_memory_input(self, payload):
        """Handle memory grid button press."""
        q = self.active_qte
        rs = q.get('runtime_state', {})
        rs.setdefault('input_sequence', [])
        idx = payload.get('index')
        rs['input_sequence'].append(idx)
        
        pattern = payload.get('pattern') or q.get('pattern', q.get('required_pattern', []))

        # Check input so far
        pos = len(rs['input_sequence']) - 1
        if pos < len(pattern):
            if rs['input_sequence'][pos] != pattern[pos]:
                self.logger.info(f"Memory QTE: Wrong input at position {pos}. Expected {pattern[pos]}, got {idx}")
                return self.resolve_qte(success=False)
        
        # Check if complete
        if len(rs['input_sequence']) >= len(pattern):
            self.logger.info(f"Memory QTE: Pattern complete! {rs['input_sequence']}")
            return self.resolve_qte(success=True)
        
        return None

    def _evt_trace_move(self, payload):
        """Route trace_move to spiral handler if spiral type, otherwise track for line."""
        q = self.active_qte
        if not q:
            return None
        path_type = q.get('path_type', 'line')
        pos = payload.get('pos', (0, 0))
        
        if path_type == 'spiral':
            return self._handle_mouse_spiral(pos[0], pos[1])
        
        # For line type, track progress (simple: accumulate points)
        rs = q.get('runtime_state', {})
        rs.setdefault('trace_points', [])
        rs['trace_points'].append(pos)
        return None

    def _evt_trace_end(self, payload):
        """Handle trace completion on touch up."""
        q = self.active_qte
        if not q:
            return None
        path_type = q.get('path_type', 'line')
        rs = q.get('runtime_state', {})
        points = rs.get('trace_points', [])
        
        if path_type == 'spiral':
            # Spiral must resolve during trace_move, not on lift
            # If we get here without resolution, spiral was incomplete
            if len(self.mouse_positions) < 8:
                return self.resolve_qte(success=False)
            return None
        
        # Line type: check if user dragged far enough
        if len(points) >= 5:
            return self.resolve_qte(success=True)
        else:
            self.logger.info(f"Trace QTE: Too few points ({len(points)}). Need more dragging.")
            return None  # Don't fail yet — let them try again

    def _evt_gauge_update(self, payload):
        """Handle precision gauge tap updates."""
        q = self.active_qte
        rs = q.get('runtime_state', {})
        value = float(payload.get('value', 0))
        target_zone = q.get('target_zone', [10, 15])
        rs['current_value'] = value
        # Resolution happens on timeout — gauge is a sustained maintenance QTE
        return None

    # ---- Type handlers (string payload) ----

    def _type_dispatch(self, qtype: str, text: str):
        # Minimal implementation for text fallback
        if qtype == 'word':
            return self._evt_submit_text({'text': text})
        if qtype == 'spiral':
            # allow CLI fallback
            if text == 'spiral':
                self.logger.info("Spiral QTE passed via text input.")
                return self.resolve_qte(success=True)
            return None
        if qtype in ('sequence', 'pattern', 'directional'):
            return self._type_sequence_like(text)
        if qtype == 'code':
            return self._type_code(text)
        if qtype in ('hold', 'hold_threshold', 'hold_to_threshold'):
            return self._type_hold(text)
        if qtype in ('hold_release', 'timed_release', 'hold_and_release'):
            return self._type_hold_release(text)
        if qtype in ('single_key', 'reaction'):
            return self._type_single_key(text)
        if qtype in ('choice', 'cancel', 'timed_choice'):
            return self._type_choice(text)
        if qtype in ('tap', 'tap_count', 'precision_tap_count'):
            return self._type_tap(text)
        if qtype in ('alternate', 'alternating_keys', 'balance'):
            return self._type_alternate(text)
        if qtype == 'rhythm':
            return self._type_rhythm(text)
        if qtype in ('analog', 'aim', 'aim_click', 'drag'):
            return self._type_analog_like(text)
        if qtype in ('trace_path', 'path_trace'):
            # CLI fallback: accept any non-empty input as a trace
            if text:
                return self.resolve_qte(success=True)
            return None

        # Fallback: accept any non-empty input
        if text:
            self.logger.info("Fallback QTE succeeded (any input).")
            return self.resolve_qte(success=True)
        return None

    def _type_word(self, text: str):
        q = self.active_qte
        expected = (q.get('expected_input_word') or '').strip().lower()
        alt = (q.get('alternative_input') or '').strip().lower()
        valids = [v.strip().lower() for v in (q.get('valid_responses') or [])]
        allowed = {v for v in [expected, alt] if v}
        allowed.update(valids)
        if not allowed:
            return self.resolve_qte(success=(len(text) > 0))
        return self.resolve_qte(success=(text in allowed))

    def _type_sequence_like(self, text: str):
        q = self.active_qte
        required = (q.get('required_sequence') or q.get('required_pattern'))
        self.logger.debug(f"Sequence QTE required: {required}")
        if isinstance(required, list) and text == " ".join(str(x).lower() for x in required):
            self.logger.info("Sequence QTE succeeded.")
            return self.resolve_qte(success=True)
        self.logger.info("Sequence QTE failed: wrong input.")
        return self.resolve_qte(success=False, reason="wrong_input")

    def _type_code(self, text: str):
        q = self.active_qte
        required = q.get('required_code')
        self.logger.debug(f"Code QTE required: {required}")
        if isinstance(required, list):
            if text == " ".join(required):
                self.logger.info("Code QTE succeeded.")
                return self.resolve_qte(success=True)
            self.logger.info("Code QTE failed: wrong input.")
            return self.resolve_qte(success=False, reason="wrong_input")
        expected = (q.get('expected_input_word') or '').lower()
        self.logger.debug(f"Code QTE fallback expected: {expected}")
        return self.resolve_qte(success=(text == expected))

    def _type_hold(self, text: str):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        if text == 'hold':
            rs['hold_start'] = time.time()
            self.logger.debug("Hold QTE: hold started.")
            return None
        if text == 'release':
            if rs.get('hold_start'):
                held = time.time() - rs['hold_start']
                need = float(q.get('required_hold_time', q.get('required_hold_time_default', 2.0)))
                self.logger.debug(f"Hold QTE: held={held:.2f}s, need={need:.2f}s")
                return self.resolve_qte(success=(held >= need))
            self.logger.info("Hold QTE failed: release without hold.")
            return self.resolve_qte(success=False, reason="wrong_input")
        self.logger.debug("Hold QTE input not recognized.")
        return None

    def _type_hold_release(self, text: str):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        if text == 'hold':
            rs['hold_start'] = time.time()
            self.logger.debug("Hold & Release QTE: hold started.")
            return None
        if text == 'release':
            if rs.get('hold_start'):
                held = time.time() - rs['hold_start']
                window = (q.get('release_window') or q.get('release_window_default') or [0.6, 0.8])
                lo, hi = float(window[0]), float(window[1])
                self.logger.debug(f"Hold & Release QTE: held={held:.2f}s, window=({lo:.2f}, {hi:.2f})")
                return self.resolve_qte(success=(lo <= held <= hi))
            self.logger.info("Hold & Release QTE failed: release without hold.")
            return self.resolve_qte(success=False, reason="wrong_input")
        self.logger.debug("Hold & Release QTE input not recognized.")
        return None

    def _type_single_key(self, text: str):
        q = self.active_qte
        req = (q.get('required_key') or '').lower()
        self.logger.debug(f"Single Key QTE required: {req}")
        if req:
            result = text == req
            self.logger.info(f"Single Key QTE {'succeeded' if result else 'failed'}.")
            return self.resolve_qte(success=result)
        result = len(text) == 1
        self.logger.info(f"Single Key QTE {'succeeded' if result else 'failed'} (any key).")
        return self.resolve_qte(success=result)

    def _type_choice(self, text: str):
        q = self.active_qte
        mapping = q.get('input_to_next_state') or {}
        choices = q.get('choices') or q.get('choices_default') or []
        correct = (q.get('correct_choice') or q.get('correct_choice_default'))
        self.logger.debug(f"Choice QTE: input={text}, choices={choices}, mapping={mapping}, correct={correct}")
        if text and mapping and text in mapping:
            q['next_state_after_qte_success'] = mapping[text]
            self.logger.info("Choice QTE succeeded via input_to_next_state mapping.")
            return self.resolve_qte(success=True)
        if correct:
            result = text == str(correct).lower()
            self.logger.info(f"Choice QTE {'succeeded' if result else 'failed'} (correct-choice mode).")
            return self.resolve_qte(success=result)
        result = text in [str(c).lower() for c in choices]
        self.logger.info(f"Choice QTE {'succeeded' if result else 'failed'} (any valid choice mode).")
        return self.resolve_qte(success=result)

    def _type_tap(self, text: str):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        prev = rs.get('tap_count', 0)
        rs['tap_count'] = prev + 1
        need = int(q.get('required_tap_count', q.get('required_tap_count_default', 10)))
        self.logger.debug(f"Tap QTE: count={rs['tap_count']}, need={need}")
        if rs['tap_count'] >= need:
            self.logger.info("Tap QTE succeeded.")
            return self.resolve_qte(success=True)
        return None

    def _type_alternate(self, text: str):
        q = self.active_qte
        keys = q.get('keys_default', q.get('keys', ['a', 'd']))
        if not keys or len(keys) < 2:
            keys = ['a', 'd']
        rs = q.get('runtime_state', {})
        rs['alternations_done'] = rs.get('alternations_done', 0)
        target = int(q.get('target_alternations_default', q.get('target_alternations', 12)))
        expected = str(keys[rs['alternations_done'] % 2]).lower()
        self.logger.debug(f"Alternate QTE: input={text}, expected={expected}, done={rs['alternations_done']}, target={target}")
        if text == expected:
            rs['alternations_done'] += 1
            if rs['alternations_done'] >= target:
                self.logger.info("Alternate QTE succeeded.")
                return self.resolve_qte(success=True)
            return None
        self.logger.info("Alternate QTE failed: wrong input.")
        return self.resolve_qte(success=False, reason="wrong_input")

    def _type_rhythm(self, text: str):
        q = self.active_qte
        rs = q.get('runtime_state', {})
        prev = rs.get('tap_count', 0)
        rs['tap_count'] = prev + 1
        need = int(q.get('target_beats', 5))
        self.logger.debug(f"Rhythm QTE: count={rs['tap_count']}, need={need}")
        if rs['tap_count'] >= need:
            self.logger.info("Rhythm QTE succeeded.")
            return self.resolve_qte(success=True)
        return None

    def _type_analog_like(self, text: str):
        self.logger.debug(f"Analog/Aim QTE input: {text!r}")
        if text:
            self.logger.info("Analog/Aim QTE succeeded.")
            return self.resolve_qte(success=True)
        return None

    def _on_qte_timeout(self, dt):
        if self.active_qte:
            self.logger.info(f"QTE timed out for '{self.active_qte.get('name')}'.")

            # Special case: gauge/tap QTEs can still succeed if value is in target zone at timeout
            if self.active_qte.get('input_type') == 'tap':
                rs = self.active_qte.get('runtime_state', {})
                val = float(rs.get('current_value', 0))
                tz = self.active_qte.get('target_zone', [10, 15])
                if isinstance(tz, (list, tuple)) and len(tz) >= 2:
                    lo, hi = float(tz[0]), float(tz[1])
                    if lo <= val <= hi:
                        self.logger.info(
                            f"Gauge QTE timeout-success: value={val:.1f}, zone=[{lo}, {hi}]")
                        result = self.resolve_qte(success=True)
                        self._apply_qte_resolution(result)
                        return

            result = self.resolve_qte(success=False, reason="timeout")
            self._apply_qte_resolution(result)
        else:
            self.logger.warning("_on_qte_timeout fired but no active_qte exists.")

    def _after_qte_resolved(self):
        queue = self.player.get('_qte_queue', [])
        if queue:
            next_qte = queue.pop(0)
            self.player['_qte_queue'] = queue
            self._handle_conseq_start_qte(next_qte, 0)

    def _force_qte_cleanup(self):
        """Force cleanup without resolving logic."""
        if self.game_logic:
            self.game_logic.player['qte_active'] = False
            self.game_logic.add_ui_event({"event_type": "hide_qte"})
        
        if self.timeout_event:
            self.timeout_event.cancel()
            self.timeout_event = None
        
        self.active_qte = None
        self.is_dismissed = True

    def dismiss(self, *largs, **kwargs):
        """Override dismiss to ensure proper cleanup."""
        self.logger.info("Dismissing QTE Engine and cleaning up event bindings.")
        
        # Unbind all events to prevent further input processing
        try:
            Window.unbind(on_key_down=self._on_key_down)
            Window.unbind(on_mouse_down=self._on_mouse_down)
        except Exception as e:
            self.logger.warning(f"Error unbinding events during dismiss: {e}")
        
        # Mark as dismissed to prevent further callbacks
        self.is_dismissed = True
        
        # Remove sequence widget if present
        if self.sequence_widget and self.sequence_widget.parent:
            self.remove_widget(self.sequence_widget)
            self.sequence_widget = None

        self.logger.info("QTE Engine dismiss complete.")

    def on_touch_down(self, touch):
        """Touch input during QTEs. All QTE types now use popup widgets
        as their sole input path, so we do not process touch here.
        Returning False lets the event propagate to the popup widget."""
        return False

    def on_touch_move(self, touch):
        """Touch move during QTEs. All QTE types use popup widgets."""
        return False

    def on_touch_up(self, touch):
        """Touch up during QTEs. All QTE types use popup widgets."""
        return False

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        """Keyboard input during QTEs. All QTE types now use popup widgets
        as their sole input path, so we do not process input here.
        Returning False lets the event propagate to the focused popup widget."""
        return False
            
    def _on_mouse_down(self, window, x, y, button, modifiers):
        """Mouse input during QTEs. All QTE types now use popup widgets
        as their sole input path, so we do not process input here.
        Returning False lets the event propagate to the popup widget."""
        return False

    def _build_resolution_message(self, qte_data: dict, success: bool, reason: str = "") -> str:
        """
        Build the player-facing result message for a completed QTE.
        Pure function — no side effects. Damage and fatality are handled
        by resolve_qte via the returned consequence dict.
        """
        if not qte_data:
            return "QTE resolved."

        if success:
            message = (
                qte_data.get('success_message') or
                qte_data.get('success_message_default') or
                "Success!"
            )
        else:
            if reason == "timeout" and qte_data.get('timeout_message'):
                message = qte_data['timeout_message']
            else:
                message = (
                    qte_data.get('failure_message') or
                    qte_data.get('failure_message_wrong_input') or
                    qte_data.get('failure_message_default') or
                    "Failed!"
                )

        # Append HP loss hint to message (informational only — damage applied by caller)
        if not success:
            hp_damage = (
                qte_data.get('hp_damage_on_failure') or
                qte_data.get('hp_damage_on_failure_default') or
                0
            )
            if hp_damage > 0:
                message += f" You lose {hp_damage} HP."
                is_fatal = (
                    qte_data.get('is_fatal_on_failure') or
                    qte_data.get('is_fatal_on_failure_default') or
                    False
                )
                if is_fatal:
                    message += " You have died!"

        self.logger.info(f"QTE '{qte_data.get('name', 'Unknown')}' resolved. Success: {success}. Message: {message}")
        # Resolve template placeholders in QTE messages
        if '{' in message:
            hazard_id = qte_data.get('qte_source_hazard_id', '')
            if hazard_id and self.game_logic and self.game_logic.hazard_engine:
                hazard = self.game_logic.hazard_engine.active_hazards.get(hazard_id, {})
                master = hazard.get('master_data', {})
                obj_name = (
                    hazard.get('target_object_override')
                    or (master.get('object_name_options', [None]) or [None])[0]
                    or master.get('name', 'hazard')
                )
                support_obj = hazard.get('support_object_override') or 'surface'
                message = message.replace('{object_name}', obj_name)
                message = message.replace('{support_object}', support_obj)
            else:
                # Fallback: strip unreplaced templates
                message = message.replace('{object_name}', 'hazard')
                message = message.replace('{support_object}', 'surface')        
        return message

    def _get_hazard_death_message(self, qte_data):
        """
        Extract the canonical death message from the hazard state that triggered this QTE.
        Falls back to a constructed message if not found.
        """
        hid = qte_data.get('qte_source_hazard_id')
        fail_state = qte_data.get('next_state_after_qte_failure')
        if hid and fail_state and self.game_logic and self.game_logic.hazard_engine:
            h = self.game_logic.hazard_engine.active_hazards.get(hid)
            if h:
                s = h.get('master_data', {}).get('states', {}).get(fail_state, {})
                return s.get('death_message') or s.get('description') or "Killed by hazard."
        return "You failed to overcome the danger."

    def _get_current_character(self) -> str:
        try:
            return (self.game_logic.player.get('character_class') or '').upper()
        except:
            return ''

    def resolve_qte(self, success: bool, reason: str = "") -> dict:
        """
        Resolves the QTE, cleans up logic state, and returns the result data.
        CRITICAL: DOES NOT emit UI events directly. GameLogic must handle the result.
        """
        trace = []

        def _mark(step: str, **data):
            entry = {"step": step, **data}
            trace.append(entry)
            self.logger.debug(f"[QTE resolve] {step} | {data}")

        _mark("enter", success=success, reason=reason, has_active_qte=bool(self.active_qte))

        if not self.active_qte:
            self.logger.warning("[QTE resolve] No active QTE at resolve call.")
            _mark("exit_no_active_qte")
            return {"success": False, "reason": "no_active_qte", "debug_trace": trace}

        try:
            qte_data = self.active_qte
            qte_source_hazard_id = qte_data.get('qte_source_hazard_id')
            _mark(
                "captured_active_qte",
                qte_name=qte_data.get("name"),
                qte_type=qte_data.get("qte_type"),
                qte_source_hazard_id=qte_source_hazard_id
            )

            self.active_qte = None
            self.is_dismissed = True
            _mark("state_cleared", active_qte_is_none=self.active_qte is None, is_dismissed=self.is_dismissed)

            if self.timeout_event:
                self.timeout_event.cancel()
                self.timeout_event = None
                _mark("timeout_event_canceled")
            else:
                _mark("timeout_event_absent")

            # Build message via the dedicated builder (no side effects)
            msg = self._build_resolution_message(qte_data, success, reason)
            _mark("message_built", message=msg)

            # Determine next state
            if success:
                next_state = qte_data.get('on_success', {}).get('target_state') or qte_data.get('next_state_after_qte_success')
            else:
                next_state = qte_data.get('on_failure', {}).get('target_state') or qte_data.get('next_state_after_qte_failure')
            _mark("next_state_resolved", success=success, next_state=next_state)

            # Calculate damage/fatality — returned for GameLogic to apply, NOT applied here
            hp_damage = 0
            is_fatal = False

            if not success:
                qte_type = qte_data.get('qte_type', 'input')
                base_def = getattr(self, 'qte_definitions', {}).get(qte_type, {})
                _mark("failure_branch_loaded_base_def", qte_type=qte_type, has_base_def=bool(base_def))

                hp_damage = qte_data.get('hp_damage_on_failure')
                if hp_damage is None:
                    hp_damage = base_def.get('hp_damage_on_failure_default', 0)
                    _mark("hp_damage_from_default", hp_damage=hp_damage)
                else:
                    _mark("hp_damage_from_qte_data", hp_damage=hp_damage)

                is_fatal = qte_data.get('is_fatal_on_failure')
                if is_fatal is None:
                    is_fatal = base_def.get('is_fatal_on_failure_default', False)
                    _mark("fatality_from_default", is_fatal=is_fatal)
                else:
                    _mark("fatality_from_qte_data", is_fatal=is_fatal)
            else:
                hp_damage = qte_data.get('on_success_apply_damage', 0)
                _mark("success_branch_damage", hp_damage=hp_damage)

            # Determine movement
            move_to = None
            if success:
                move_to = (
                    qte_data.get('on_success', {}).get('move_player_to') or
                    qte_data.get('on_state_entry_move_player_to') or
                    qte_data.get('move_player_to')
                )
            _mark("movement_resolved", move_player_to=move_to)

            death_reason = self._get_hazard_death_message(qte_data) if (is_fatal or (hp_damage > 0 and not success)) else None
            _mark("death_reason_resolved", death_reason=death_reason)

            result = {
                "success": success,
                "reason": reason,
                "message": msg,
                "qte_source_hazard_id": qte_source_hazard_id,
                "next_state_success": next_state if success else qte_data.get('next_state_after_qte_success'),
                "next_state_failure": next_state if not success else qte_data.get('next_state_after_qte_failure'),
                "hp_damage": hp_damage,
                "is_fatal": is_fatal,
                "move_player_to": move_to,
                "death_reason": death_reason,
                "effects_on_success": qte_data.get('effects_on_success', []),
                "debug_trace": trace,
                "target_npc": qte_data.get('target_npc'),
                "npc_fatal_on_failure": qte_data.get('npc_fatal_on_failure', False),
                "pending_move": qte_data.get('pending_move'),
            }

            _mark(
                "exit_success",
                result_summary={
                    "success": result["success"],
                    "reason": result["reason"],
                    "next_state_success": result["next_state_success"],
                    "next_state_failure": result["next_state_failure"],
                    "hp_damage": result["hp_damage"],
                    "is_fatal": result["is_fatal"],
                    "move_player_to": result["move_player_to"],
                }
            )
            return result

        except Exception as e:
            self.logger.exception(f"[QTE resolve] Exception during resolution: {e}")
            _mark("exit_exception", error=str(e))
            return {
                "success": False,
                "reason": "resolve_exception",
                "message": "QTE resolution failed unexpectedly.",
                "qte_source_hazard_id": None,
                "next_state_success": None,
                "next_state_failure": None,
                "hp_damage": 0,
                "is_fatal": False,
                "move_player_to": None,
                "death_reason": None,
                "effects_on_success": [],
                "debug_trace": trace,
                "npc_fatal_on_failure": False,
                "pending_move": None,   
            }
        
    def _apply_qte_resolution(self, result):
        """Helper method to push QTE results to GameLogic and update the UI."""
        if not result:
            return
            
        if self.game_logic:
            try:
                response = self.game_logic._handle_qte_resolution(result)
                response = response or {}  # CRITICAL: Prevent NoneType crash if logic returns nothing
                
                # Push results to the UI
                from kivy.app import App
                app = App.get_running_app()
                if app and app.root and hasattr(app.root, 'get_screen'):
                    try:
                        game_screen = app.root.get_screen('game')
                    except Exception:
                        game_screen = None
                        
                    if game_screen:
                        # Show messages safely
                        out = None
                        if hasattr(game_screen, '_get_widget'):
                            out = game_screen._get_widget('output_panel')
                        
                        for m in response.get('messages', []):
                            if out and hasattr(out, 'append_text'):
                                out.append_text(m)
                                
                        # Update UI state safely
                        if hasattr(game_screen, 'update_all_ui_elements'):
                            game_screen.update_all_ui_elements(response.get('game_state', {}))
                            
                        # Drain ALL queued events safely
                        ui_events = response.get('ui_events', [])
                        if hasattr(self.game_logic, 'get_ui_events'):
                            logic_events = self.game_logic.get_ui_events()
                            if logic_events:
                                ui_events.extend(logic_events)
                                
                        if hasattr(game_screen, '_handle_ui_events') and ui_events:
                            game_screen._handle_ui_events(ui_events)
                            
            except Exception as e:
                self.logger.error(f"Failed to apply QTE resolution: {e}")

    def _complete_qte_resolution(self, message: str, hazard_id: str, next_state: str):
        """Show result popup first; apply next state only after dismiss."""
        if not self.game_logic:
            return
        self.game_logic.add_ui_event({
            "event_type": "show_popup",
            "priority": 99,
            "title": "QTE Result",
            "message": message,
            "on_close_set_hazard_state": {
                "hazard_id": hazard_id,
                "target_state": next_state
            }
        })

    def _resolve_character_overrides(self, qte_data):
        effective = {}
        char = self._get_current_character()

        def _pick(val):
            if isinstance(val, dict):
                return val.get(char, val.get('default'))
            return val

        for k in ['target_mash_count', 'required_tap_count', 'required_hold_time', 'target_alternations_default']:
            if k in qte_data:
                res = _pick(qte_data[k])
                if res is not None:
                    qte_data[k] = res
                    effective[f"effective_{k}"] = res

        return effective

    def _resolve_for_character(self, value, default_key: str = 'default'):
        """
        Resolve a value that may be a per-character mapping, e.g. {"default": 25, "EMT": 15}.
        Returns a scalar (int/float/str) suitable for use by the QTE logic.
        """
        if not isinstance(value, dict):
            return value
        try:
            char = (self.game_logic.player.get('character_class') or self.game_logic.player.get('class') or '').upper()
        except Exception:
            char = ''
        if char and char in value:
            return value[char]
        if default_key in value:
            return value[default_key]
        # Fallback to any scalar value found
        for v in value.values():
            if isinstance(v, (int, float, str)):
                return v
        return None

    def _effective_mash_target(self, qte_data):
        """
        Compute the effective mash target with character rules:
        - Use per-character overrides if provided.
        - Otherwise apply EMT perk: -10 presses (min 1).
        """
        raw = qte_data.get('target_mash_count')
        target = raw.get('default') if isinstance(raw, dict) else raw
        target = target or 15
        if self._get_current_character() == 'EMT':
            target = max(1, int(target) - 10)
        return int(target)
    
    def _handle_mouse_spiral(self, x, y):
        """Process mouse/touch movement for spiral detection"""
        current_pos = (x, y)
        self.mouse_positions.append(current_pos)
        
        # Need at least 3 positions to start analyzing
        if len(self.mouse_positions) < 3:
            return None
            
        # Establish spiral center from early positions
        if self.spiral_center is None and len(self.mouse_positions) >= 5:
            # Use centroid of first few positions as approximate center
            center_x = sum(pos[0] for pos in self.mouse_positions[:5]) / 5
            center_y = sum(pos[1] for pos in self.mouse_positions[:5]) / 5
            self.spiral_center = (center_x, center_y)
            
        if self.spiral_center is None:
            return None
            
        # Calculate current radius and angle from center
        dx = x - self.spiral_center[0]
        dy = y - self.spiral_center[1]
        current_radius = math.sqrt(dx*dx + dy*dy)
        current_angle = math.atan2(dy, dx)
        
        self.spiral_radius_history.append(current_radius)
        
        # Track angle progression for spiral detection
        if len(self.mouse_positions) >= 2:
            prev_pos = self.mouse_positions[-2]
            prev_dx = prev_pos[0] - self.spiral_center[0]
            prev_dy = prev_pos[1] - self.spiral_center[1]
            prev_angle = math.atan2(prev_dy, prev_dx)
            
            # Calculate angle difference (accounting for wraparound)
            angle_diff = current_angle - prev_angle
            if angle_diff > math.pi:
                angle_diff -= 2 * math.pi
            elif angle_diff < -math.pi:
                angle_diff += 2 * math.pi
                
            self.spiral_angle_total += abs(angle_diff)
        
        # On mobile, a single full rotation (2π) at any consistent radius is enough.
        # We lower the radius-trend requirement since phone users draw smaller spirals.
        required_accuracy = float(self.active_qte.get('required_spiral_accuracy_default', 0.55))
        
        # Spiral is successful if:
        # 1. Total angle traversed is at least 2π (one full rotation)
        # 2. Has enough data points (indicates intentional drawing, not a flick)
        if (self.spiral_angle_total >= 2 * math.pi and 
            len(self.spiral_radius_history) >= 8):
            
            radius_trend = self._analyze_radius_trend()
            if radius_trend >= required_accuracy:
                return self.resolve_qte(success=True)
        
        return None  # Continue spiral

    def _analyze_radius_trend(self):
        """Analyze if radius history shows spiral pattern"""
        if len(self.spiral_radius_history) < 10:
            return 0.0
            
        # Check for consistent increase or decrease in radius (spiral pattern)
        increases = 0
        decreases = 0
        
        for i in range(1, len(self.spiral_radius_history)):
            if self.spiral_radius_history[i] > self.spiral_radius_history[i-1]:
                increases += 1
            elif self.spiral_radius_history[i] < self.spiral_radius_history[i-1]:
                decreases += 1
        
        total_changes = increases + decreases
        if total_changes == 0:
            return 0.0
            
        # Return the proportion of changes that follow the dominant trend
        dominant_trend = max(increases, decreases)
        return dominant_trend / total_changes

    def set_resource_manager(self, resource_manager):
        self.resource_manager = resource_manager
        self.qte_definitions = self.resource_manager.get_data('qte_definitions', {})

class QTESequenceWidget(BoxLayout):
    # directions or pattern alphabet, e.g. ["up", "down", "left", "right"]
    options = ListProperty(["up", "down", "left", "right"])
    required_length = 3  # or set dynamically
    qte_engine = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.current_sequence = []

        # Add direction/pattern buttons
        btn_row = BoxLayout(orientation="horizontal", size_hint_y=0.3)
        for opt in self.options:
            btn = Button(text=opt.capitalize())
            btn.bind(on_release=lambda btn, o=opt: self.on_option_press(o))
            btn_row.add_widget(btn)
        self.add_widget(btn_row)

        # Add a TextInput for manual entry (optional)
        self.input = TextInput(hint_text="Type sequence (space-separated)", multiline=False, size_hint_y=0.2)
        self.input.bind(on_text_validate=self.on_text_submit)
        self.add_widget(self.input)

        # Add a submit button
        submit_btn = Button(text="Submit", size_hint_y=0.2)
        submit_btn.bind(on_release=self.on_submit)
        self.add_widget(submit_btn)

    def on_option_press(self, option):
        self.current_sequence.append(option)
        # Optionally, show the sequence so far
        self.input.text = " ".join(self.current_sequence)
        if len(self.current_sequence) >= self.required_length:
            self.submit_sequence()

    def on_text_submit(self, instance):
        self.submit_sequence()

    def on_code_submit(self, instance):
        code = instance.text.strip()
        self.qte_engine.handle_qte_input(code)

    def on_submit(self, instance):
        self.submit_sequence()

    def submit_sequence(self):
        sequence = self.input.text.strip().lower().split()
        if not sequence and self.current_sequence:
            sequence = self.current_sequence
        if self.qte_engine:
            self.qte_engine.handle_qte_input({'event': 'sequence_input', 'sequence': sequence})
        # Optionally, reset for next QTE
        self.current_sequence = []
        self.input.text = ""

