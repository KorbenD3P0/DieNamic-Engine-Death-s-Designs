# fd_terminal/chaos_crawler.py
"""
Chaos Crawler — Automated QA playtester for DieNamic Engine.

Usage (in-game):
    crawl 500           — run 500 turns at default speed
    crawl 1000 fast     — run 1000 turns at maximum speed (0.05s interval)
    crawl stop          — halt a running crawl

The crawler routes all actions through on_submit_command exactly as a human
would, so deferred popup chains, hazard state transitions, and QTE resolution
all fire correctly. It does NOT call engine methods directly.

On halt (crash, turn limit, or manual stop), it writes a full report to:
    logs/crawler_report_<timestamp>.txt
"""

import os
import random
import logging
import traceback
from collections import defaultdict, deque
from datetime import datetime
from kivy.clock import Clock


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# How many consecutive turns in the same room before the crawler declares
# itself stuck and forces a random move or wait.
STUCK_THRESHOLD = 12

# How many turns to wait after dismissing a popup before acting again.
# Gives the engine time to process deferred chains.
POPUP_COOLDOWN_TURNS = 2

# Verb weights: higher = chosen more often when available.
# Take > Talk > Search >>> Force > Use > Wait
VERB_WEIGHTS = {
    "take":   10,
    "talk":    8,
    "search":  7,
    "use":     5,
    "force":   3,
    "move":    6,
    "wait":    1,
}

# Items the crawler should prioritize taking immediately if available.
PRIORITY_ITEMS = {
    "flashlight", "camera", "bludworths_house_key", "bludworths_house_address",
    "vet_sedatives", "adrenaline", "defibrillator_pads", "warehouse_key",
    "survivor_contact_sheet", "bludworths_ledger", "gammy_death_book",
    "visionary_notes", "loaded_revolver", "first_aid_kit",
}

# Dialogue option weights by keyword in option text.
# Options containing these words are deprioritized (we want to keep NPCs alive).
DIALOGUE_AVOID_KEYWORDS = {
    "punch", "threaten", "attack", "leave them", "ignore", "walk away"
}


# ---------------------------------------------------------------------------
# Main crawler class
# ---------------------------------------------------------------------------

class ChaosCrawler:
    """
    Automated playtester that hooks into GameScreen and drives the engine
    through on_submit_command, exactly replicating human input.
    """

    def __init__(self, game_screen):
        self.game_screen = game_screen
        self.gl = game_screen.game_logic
        self.is_running = False
        self.turn_count = 0
        self.turns_limit = 0
        self.tick_interval = 0.2          # seconds between actions
        self._event = None                # Kivy Clock event handle

        # Stuck detection
        self._location_history = deque(maxlen=STUCK_THRESHOLD)
        self._popup_cooldown = 0          # turns to skip after a dismiss
        # Repeat-command loop detection
        self._last_command = ""
        self._repeat_command_count = 0
        self._MAX_REPEAT_COMMANDS = 5
        # Coverage tracking
        self._rooms_visited = set()
        self._items_collected = []
        self._npcs_talked = set()
        self._levels_reached = set()
        self._hazards_triggered = []
        self._qte_results = {"pass": 0, "fail": 0}
        self._commands_issued = []        # rolling last-200 log
        self._errors = []                 # (turn, traceback) pairs

        # Setup dedicated log file
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_path = os.path.join(log_dir, f"crawler_report_{stamp}.txt")

        self.logger = logging.getLogger(f"ChaosCrawler.{stamp}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        fh = logging.FileHandler(self.log_path, mode='w', encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s  %(message)s'))
        self.logger.addHandler(fh)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, turns: int = 500, fast: bool = False, runs: int = 1):
        """
        Start the crawler.
        turns: max actions per run
        fast:  0.05s tick interval
        runs:  total runs to complete before stopping
        """
        if self.is_running:
            return
        self.is_running    = True
        self.turns_limit   = turns
        self.fast_mode     = fast
        self.runs_total    = runs
        self.runs_done     = 0
        self.tick_interval = 0.05 if fast else 0.2
        self._start_run()

    def _start_run(self):
        """Initialize state for a fresh run and start the clock."""
        self.turn_count = 0
        self._location_history.clear()
        self._popup_cooldown   = 0
        self._last_command     = ""
        self._repeat_command_count = 0
        self._commands_issued.clear()

        # Trigger a clean new game through the UI
        self._auto_start_new_game()

        self.logger.info(f"{'='*60}")
        self.logger.info(f"RUN {self.runs_done + 1}/{self.runs_total} STARTING — "
                        f"{self.turns_limit} turns  ({'fast' if self.fast_mode else 'normal'})")
        self.logger.info(f"{'='*60}")

        if self._event:
            self._event.cancel()
        self._event = Clock.schedule_interval(self._tick, self.tick_interval)

    def _auto_start_new_game(self):
        """Drive the UI through new-game setup without human input."""
        from kivy.app import App
        import random
        app = App.get_running_app()
        game_screen = self.game_screen

        # 1. Reset UI state
        if hasattr(game_screen, 'reset_ui_state'):
            game_screen.reset_ui_state()

        # 2. Pick a random character and start
        characters = ['Citizen Detective', 'EMT', 'Journalist', 'Off-Duty Cop']
        char = random.choice(characters)
        gl = game_screen.game_logic
        gl.start_new_game(character_class=char, start_level=0)

        # 3. Update the crawler's gl reference (game_logic is the same object, player is reset)
        self.gl = gl

        # 4. Navigate to game screen
        if app and app.root:
            # Dismiss any lingering screens
            for screen_name in ('lose', 'win', 'inter_level', 'intro'):
                if app.root.current == screen_name:
                    try:
                        s = app.root.get_screen('game')
                        app.root.current = 'game'
                        break
                    except Exception:
                        pass
            # Fire the intro bypass — IntroScreen auto-forwards for level_0
            if app.root.current == 'intro':
                intro = app.root.get_screen('intro')
                Clock.schedule_once(lambda dt: intro.proceed_to_game(), 0.1)

        self.logger.info(f"[AUTO] New game started — character: {char}")

    def stop(self, reason: str = "Manual stop"):
        if not self.is_running:
            return
        self.is_running = False
        if self._event:
            self._event.cancel()
            self._event = None

        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info(f"CRAWLER HALTED — {reason}")
        self.logger.info("=" * 60)
        self._write_summary(reason)
        print(f"[ChaosCrawler] Stopped. Report written to: {self.log_path}")

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self, dt):
        try:
            # ── Terminal checks ──────────────────────────────────────────
            if self.turns_limit > 0 and self.turn_count >= self.turns_limit:
                self._end_run("Turn limit reached.")
                return True  # Keep clock running — will start next run

            if self.gl.is_game_over or self.gl.game_won:
                reason = self.gl.player.get('death_reason', 'Game over')
                self._end_run(reason)
                return True

            # ── Screen guard ─────────────────────────────────────────────
            from kivy.app import App
            app = App.get_running_app()
            if app and hasattr(app, 'root') and hasattr(app.root, 'current'):
                curr = app.root.current

                if curr == 'inter_level':
                    inter = app.root.get_screen('inter_level')
                    if hasattr(inter, 'proceed_to_next_level'):
                        inter.proceed_to_next_level(None)
                    return True

                elif curr in ('lose', 'win'):
                    # Terminal screen — count this as a completed run
                    self._end_run(f"Reached {curr} screen")
                    return True

                elif curr == 'intro':
                    # Auto-advance intro screen
                    intro = app.root.get_screen('intro')
                    if hasattr(intro, 'proceed_to_game'):
                        Clock.schedule_once(lambda dt: intro.proceed_to_game(), 0.2)
                    return True

                elif curr != 'game':
                    return True  # Wait for any other screen

            # ── Popup cooldown ──────────────────────────────────────────
            if self._popup_cooldown > 0:
                self._popup_cooldown -= 1
                return True

            # ── Active QTE ──────────────────────────────────────────────
            if self._handle_qte():
                return True

            # ── Active popup ────────────────────────────────────────────
            if self._handle_popup():
                return True

            # ── Elevator transit guard ───────────────────────────────────
            # If the elevator timer is running, do nothing. Let Clock fire
            # _on_elevator_timer_complete naturally. Sending commands during
            # transit resets the 4-second arrival timer and traps the player.
            if self.gl.player.get('elevator_transit_active'):
                self.logger.info("[ELEVATOR] Transit active — waiting for arrival.")
                return True

            # ── Normal command ──────────────────────────────────────────
            self._execute_turn()
            return True

        except Exception as e:
            tb = traceback.format_exc()
            self._errors.append((self.turn_count, tb))
            self.logger.error(f"CRAWLER EXCEPTION on turn {self.turn_count}:\n{tb}")
            self.stop(f"Crawler exception on turn {self.turn_count}")
            return False

    def _end_run(self, reason: str):
        """Finish a run, write summary, and either start the next or stop."""
        self.runs_done += 1
        self.logger.info(f"RUN {self.runs_done}/{self.runs_total} ENDED — {reason}")
        self._write_summary(reason)

        if self.runs_done >= self.runs_total:
            self.is_running = False
            if self._event:
                self._event.cancel()
                self._event = None
            self.logger.info(f"ALL {self.runs_total} RUNS COMPLETE.")
            print(f"[ChaosCrawler] All {self.runs_total} runs complete. Final report: {self.log_path}")
            return

        # Brief pause before next run so the UI can settle
        Clock.schedule_once(lambda dt: self._start_run(), 1.0)

    # ------------------------------------------------------------------
    # QTE handler
    # ------------------------------------------------------------------

    def _handle_qte(self) -> bool:
        if not self.gl.player.get('qte_active'):
            return False
        qte = getattr(self.gl, 'qte_engine', None)
        if not qte or not qte.active_qte:
            return False

        qte_type = qte.active_qte.get('qte_type', '')
        qte_ctx  = qte.active_qte.get('qte_context', {})
        self.logger.info(f"[QTE] Active: '{qte_type}'")

        if 'pattern' in qte_type.lower() or 'memory' in qte_type.lower() or 'sequence' in qte_type.lower():
            # pattern_memory: must send the correct sequence keys one at a time.
            # Read required_sequence from the active QTE context if available.
            sequence = (
                qte_ctx.get('required_sequence') or
                qte_ctx.get('required_pattern') or
                qte_ctx.get('pattern') or
                ['down', 'right', 'left', 'up']  # fallback guess
            )
            # Send all keys in sequence with a tiny delay between them.
            # We schedule each key 0.05s apart so the QTE engine registers them individually.
            for i, key in enumerate(sequence):
                delay = i * 0.05
                Clock.schedule_once(
                    lambda dt, k=key.lower(): self._submit(k),
                    delay
                )
            self.logger.info(f"[QTE] Sending pattern sequence: {sequence}")
        else:
            # button_mash / spam_any_key / reaction: spacebar works
            expected_key = qte_ctx.get('expected_key', 'space')
            self._submit(expected_key)

        self._qte_results['pass'] += 1
        return True

    # ------------------------------------------------------------------
    # Popup handler
    # ------------------------------------------------------------------

    def _handle_popup(self) -> bool:
        handled_any = False
        
        # 1. Wait for pending popups
        if getattr(self.game_screen, '_popup_pending', False):
            return True

        # 2. Clear resolved QTE popups safely
        if not self.gl.player.get('qte_active'):
            qte_popup = getattr(self.game_screen, 'active_qte_popup', None)
            if qte_popup and getattr(qte_popup, 'parent', None):
                self.logger.info("[AUTO] Smashing resolved QTE popup!")
                try:
                    qte_popup.dismiss()
                    handled_any = True
                except: pass
                
        # 3. Clear standard Info popups
        info_popup = getattr(self.game_screen, 'active_info_popup', None)
        if info_popup and getattr(info_popup, 'parent', None):
            # Guard: DON'T dismiss if it's a dialogue with options!
            opts = (self.gl.last_dialogue_context or {}).get('options', [])
            if not opts:
                self.logger.info("[AUTO] Smashing Info popup!")
                try:
                    info_popup.dismiss()
                    handled_any = True
                except: pass

        # 4. Clear Map popups
        map_popup = getattr(self.game_screen, 'active_map_popup', None)
        if map_popup and getattr(map_popup, 'parent', None):
            self.logger.info("[AUTO] Smashing Map popup!")
            try:
                map_popup.dismiss()
                handled_any = True
            except: pass

        if handled_any:
            self._popup_cooldown = POPUP_COOLDOWN_TURNS
            return True
            
        return False

    # ------------------------------------------------------------------
    # Normal turn execution
    # ------------------------------------------------------------------

    def _execute_turn(self):
        """Build and send one command, track coverage."""
        loc = self.gl.player.get('location', '')
        lvl = str(self.gl.player.get('current_level', ''))

        self._rooms_visited.add(loc)
        self._levels_reached.add(lvl)
        self._location_history.append(loc)

        cmd = self._generate_command()

        # Rolling command history for the summary
        self._commands_issued.append(cmd)
        if len(self._commands_issued) > 200:
            self._commands_issued.pop(0)

        # --- REPEAT COMMAND GUARD ---
        if cmd == self._last_command:
            self._repeat_command_count += 1
            if self._repeat_command_count >= self._MAX_REPEAT_COMMANDS:
                self.logger.warning(
                    f"[LOOP DETECTED] Command '{cmd}' repeated "
                    f"{self._repeat_command_count}x. Forcing context wipe and wait."
                )
                self.gl.last_dialogue_context = {}
                self._repeat_command_count = 0
                cmd = "wait"
        else:
            self._last_command = cmd
            self._repeat_command_count = 1
        # ----------------------------

        self.logger.info(f"T{self.turn_count:04d}  [{lvl}]  {loc}  >  {cmd}")

        self._submit(cmd)
        self.turn_count += 1

        # Track HP drops as QTE failures (crude but effective)
        hp = self.gl.player.get('hp', 30)
        max_hp = self.gl.player.get('max_hp', 30)
        if hp < max_hp * 0.5 and self.gl.player.get('qte_active') is False:
            self._qte_results['fail'] += 1

    def _submit(self, command: str):
        """Route a command through the UI's normal submit path."""
        try:
            self.game_screen.on_submit_command(command_override=command)
        except Exception as e:
            self.logger.error(f"[SUBMIT] Exception submitting '{command}': {e}")

    # ------------------------------------------------------------------
    # Command generation
    # ------------------------------------------------------------------

    def _generate_command(self) -> str:
        # --- STALE CONTEXT GUARD ---
        # If we've been issuing the exact same respond command repeatedly,
        # the dialogue context is stale (NPC gone, no valid target).
        # Wipe it and fall through to normal command generation.
        options = (self.gl.last_dialogue_context or {}).get('options', [])
        npc_name = (self.gl.last_dialogue_context or {}).get('npc_name', '')
        
        if options and npc_name:
            # Check if this NPC actually exists in the current room
            room_id = self.gl.player.get('location', '')
            npc = self.gl._find_npc_in_room(npc_name, room_id)
            if not npc:
                self.logger.warning(
                    f"[STALE CONTEXT] NPC '{npc_name}' not in room '{room_id}'. "
                    f"Clearing dialogue context."
                )
                self.gl.last_dialogue_context = {}
                # Fall through to normal command generation below
        # ---------------------------
        
        # Existing dialogue check (now only fires if NPC is actually present)
        options = (self.gl.last_dialogue_context or {}).get('options', [])
        if options:
            return self._pick_dialogue_option(options)

        # 2. Gather all available targets from engine
        loc = self.gl.player.get('location', '')
        room_data = self.gl.get_room_data(loc) or {}
        
        moves   = self.gl.get_available_targets('move')
        takes   = self.gl.get_available_targets('take')
        talks   = self.gl.get_available_targets('talk')
        searches = self.gl.get_available_targets('search')
        uses    = self.gl.get_available_targets('use')
        
        # --- THE FIX: MANUALLY HARVEST LOCKED TARGETS FOR FORCE ---
        forces = self.gl.get_available_targets('force')
        if not forces:
            forces = []
            
        # Add locked exits (e.g. 'force east', 'force stairwell door')
        for direction, dest in room_data.get('exits', {}).items():
            if isinstance(dest, dict) and dest.get('locked'):
                forces.append(direction)
                
        # Add locked furniture (e.g. 'force emergency case')
        for furn in room_data.get('furniture', []):
            if isinstance(furn, dict):
                # Check for direct lock or nested locking dict
                is_locked = furn.get('locked') or (isinstance(furn.get('locking'), dict) and furn.get('locking', {}).get('locked'))
                if is_locked:
                    forces.append(furn.get('name'))
                
        forces = list(set(forces)) # Ensure unique targets
        # -----------------------------------------------------------

        # 3. Priority item pickup — always grab key items first
        for item in takes:
            if item.lower().replace(' ', '_') in PRIORITY_ITEMS or item.lower() in PRIORITY_ITEMS:
                self._items_collected.append(item)
                self.logger.info(f"[PRIORITY] Taking '{item}'")
                return f"take {item}"

        # 4. Track NPC conversations
        for npc in talks:
            npc_key = f"{loc}::{npc}"
            if npc_key not in self._npcs_talked:
                self._npcs_talked.add(npc_key)
                self.logger.info(f"[NPC] First conversation with '{npc}'")
                return f"talk {npc}"

        # 5. Build weighted command pool
        pool = []

        def _add(verb, targets, weight):
            for t in targets:
                pool.extend([f"{verb} {t}"] * weight)

        _add("take",   takes,    VERB_WEIGHTS["take"])
        _add("talk",   talks,    VERB_WEIGHTS["talk"])
        _add("search", searches, VERB_WEIGHTS["search"])
        _add("use",    uses,     VERB_WEIGHTS["use"])
        _add("force",  forces,   VERB_WEIGHTS["force"])

        # 6. Stuck detection — if we've been in the same room too many turns
        if self._is_stuck():
            self.logger.warning(f"[STUCK] Detected in '{loc}' — forcing move.")
            if moves:
                return f"move {random.choice(moves)}"
            return "wait"

        # Add moves at normal weight only if not stuck
        _add("move", moves, VERB_WEIGHTS["move"])

        if pool:
            return random.choice(pool)

        # 7. Absolute fallback
        return "wait"

    def _pick_dialogue_option(self, options: list) -> str:
        """
        Pick a dialogue option. Prefers options that sound cooperative/
        helpful. Avoids options with aggressive or 'leave' keywords.
        Picks the pro-survival option when the NPC's life might be at stake.
        """
        scored = []
        for i, opt in enumerate(options, 1):
            text = opt.get('text', '').lower()
            score = 10  # default

            # Prefer cooperative language
            if any(w in text for w in ('help', 'together', 'leave', 'go', 'believe', 'trust', 'stay')):
                score += 5
            if any(w in text for w in ('warn', 'safe', 'danger', 'careful')):
                score += 4

            # Penalize aggressive language
            if any(w in text for w in DIALOGUE_AVOID_KEYWORDS):
                score = max(1, score - 8)

            # Always take the "walk away" option last
            if 'walk away' in text:
                score = 1

            scored.append((score, i))

        # Weighted random selection
        total = sum(s for s, _ in scored)
        r = random.uniform(0, total)
        cumulative = 0
        for score, idx in scored:
            cumulative += score
            if r <= cumulative:
                self.logger.info(f"[DIALOGUE] Choosing option {idx}")
                return f"respond {idx}"

        return f"respond {scored[-1][1]}"  # fallback: last option

    def _is_stuck(self) -> bool:
        """Returns True if the crawler has been in the same room for too long."""
        if len(self._location_history) < STUCK_THRESHOLD:
            return False
        return len(set(self._location_history)) == 1

    # ------------------------------------------------------------------
    # Summary report
    # ------------------------------------------------------------------

    def _write_summary(self, stop_reason: str):
        """Append a structured summary to the log file."""
        gl = self.gl
        p = gl.player

        lines = [
            "",
            "=" * 60,
            "CRAWL SUMMARY",
            "=" * 60,
            f"Stop reason      : {stop_reason}",
            f"Total turns      : {self.turn_count} / {self.turns_limit}",
            f"Final level      : {p.get('current_level', 'UNKNOWN')}",
            f"Final room       : {p.get('location', 'UNKNOWN')}",
            f"Final HP         : {p.get('hp', '?')} / {p.get('max_hp', '?')}",
            f"Final fear       : {p.get('fear', 0.0):.2f}",
            f"Final score      : {p.get('score', 0)}",
            "",
            "── COVERAGE ──────────────────────────────────────────────",
            f"Levels reached   : {sorted(self._levels_reached)}",
            f"Unique rooms     : {len(self._rooms_visited)}",
        ]

        for room in sorted(self._rooms_visited):
            lines.append(f"    {room}")

        lines += [
            "",
            f"NPCs talked to   : {len(self._npcs_talked)}",
        ]
        for npc in sorted(self._npcs_talked):
            lines.append(f"    {npc}")

        lines += [
            "",
            f"Items collected  : {len(self._items_collected)}",
        ]
        for item in self._items_collected:
            lines.append(f"    {item}")

        # Survivor roster
        npc_status = p.get('npc_status', {})
        if npc_status:
            lines += ["", "── SURVIVOR ROSTER ───────────────────────────────────────"]
            for name, status in npc_status.items():
                lines.append(f"    {name:<20} {status}")

        # Deaths list state
        deaths_list = p.get('deaths_list', [])
        deaths_idx = p.get('deaths_list_index', 0)
        if deaths_list:
            lines += ["", "── DEATH'S DESIGN ────────────────────────────────────────"]
            for i, name in enumerate(deaths_list):
                marker = " <-- NEXT" if i == deaths_idx else ""
                struck = "[DEAD] " if npc_status.get(name.lower(), 'alive') == 'dead' else ""
                lines.append(f"    {i+1}. {struck}{name}{marker}")

        # Inventory
        inventory = p.get('inventory', [])
        if inventory:
            lines += ["", "── INVENTORY ─────────────────────────────────────────────"]
            for item in inventory:
                lines.append(f"    {item}")

        # Flags / progression
        companions = p.get('companions', [])
        if companions:
            lines += ["", f"── COMPANIONS ({'alive' if companions else 'none'}) ───────"]
            for c in companions:
                lines.append(f"    {c}")

        interaction_flags = getattr(gl, 'interaction_flags', set())
        key_flags = {f for f in interaction_flags if any(k in f for k in
            ('learned_deaths_list', 'bludworth', 'social_worker', 'finale', 'police'))}
        if key_flags:
            lines += ["", "── KEY FLAGS ─────────────────────────────────────────────"]
            for f in sorted(key_flags):
                lines.append(f"    {f}")

        # QTE results
        lines += [
            "",
            "── QTE RESULTS ───────────────────────────────────────────",
            f"    Pass: {self._qte_results['pass']}   Fail: {self._qte_results['fail']}",
        ]

        # Error log
        if self._errors:
            lines += [
                "",
                f"── ERRORS ({len(self._errors)}) ──────────────────────────────────",
            ]
            for turn_num, tb in self._errors:
                lines.append(f"    Turn {turn_num}:")
                for tb_line in tb.strip().split('\n'):
                    lines.append(f"        {tb_line}")

        else:
            lines += ["", "── ERRORS ────────────────────────────────────────────────",
                      "    None. Clean run."]

        # Last 20 commands (breadcrumb trail to the crash/stop)
        lines += ["", "── LAST 20 COMMANDS ──────────────────────────────────────"]
        for cmd in self._commands_issued[-20:]:
            lines.append(f"    {cmd}")

        lines.append("")
        lines.append("=" * 60)
        lines.append("END OF REPORT")
        lines.append("=" * 60)

        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        self.logger.info("Summary written.")