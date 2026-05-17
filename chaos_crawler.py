# fd_terminal/chaos_crawler.py
"""
Chaos Crawler — Automated QA playtester for DieNamic Engine.

MODES:
    pathfinder  — Follows the golden path: level_0 exit → hospital → police →
                  Bludworth → workplaces → finale. Searches containers, reads
                  player_interaction, handles hub transitions. Use this to
                  verify that a full playthrough completes without softlocks.

    chaos       — Spams random legal actions every tick. Ignores progression.
                  Use this to stress-test hazard chains, NPC reactions, and
                  engine stability under random input.

    adversarial — Deliberately makes bad choices: picks aggressive dialogue,
                  ignores priority items, triggers hazards on purpose, tries
                  to force every door. Use this to test failure states,
                  death narratives, and game-over screens.

USAGE (in-game):
    crawl 500                       — 500 turns, pathfinder mode, normal speed
    crawl 1000 chaos                — 1000 turns, chaos mode
    crawl 500 adversarial fast      — adversarial, fast tick
    crawl 5 runs pathfinder         — 5 full runs in pathfinder mode
    crawl stop                      — halt immediately

Reports are written to logs/crawler_report_<timestamp>.txt on halt.
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

STUCK_THRESHOLD       = 12
POPUP_COOLDOWN_TURNS  = 2

# Per-mode verb weights
VERB_WEIGHTS_PATHFINDER = {
    "take": 10, "search": 9, "talk": 8, "use": 7,
    "examine": 4, "move": 6, "force": 2, "wait": 1,
}
VERB_WEIGHTS_CHAOS = {
    "take": 5, "search": 5, "talk": 5, "use": 5,
    "examine": 5, "move": 5, "force": 5, "wait": 5,
}
VERB_WEIGHTS_ADVERSARIAL = {
    "force": 10, "use": 8, "take": 3, "move": 6,
    "talk": 4, "search": 2, "examine": 2, "wait": 1,
}

PRIORITY_ITEMS = {
    "flashlight", "camera", "bludworths_house_key", "bludworths_house_address",
    "vet_sedatives", "adrenaline", "defibrillator_pads", "warehouse_key",
    "survivor_contact_sheet", "bludworths_ledger", "gammy_death_book",
    "visionary_notes", "loaded_revolver", "loaded_heavy_revolver",
    "first_aid_kit", "coroners_office_key", "coroners_report",
    "bullets", "empty_heavy_revolver", "defibrillator_pads",
    "industrial_jumper_cables", "liquid_nitrogen_dewar",
    "thermal_rewarming_blanket", "industrial_helium_tank",
    "pure_oxygen_resuscitator", "hypothermia_survival_kit",
    "asphyxiation_survival_kit",
}

DIALOGUE_AVOID_KEYWORDS = {
    "punch", "threaten", "attack", "leave them", "ignore", "walk away",
}
DIALOGUE_ADVERSARIAL_PREFER = {
    "refuse", "threaten", "attack", "walk away", "ignore", "no",
    "not going to", "leave", "disagree",
}

# Golden path: ordered list of (level_id_fragment, objective_description)
# Pathfinder uses this to bias its exit choices.
GOLDEN_PATH = [
    ("level_0",            "Reach the exit room during the premonition"),
    ("level_1",            "Find coroner key → Coroner's Office → Bludworth key"),
    ("level_police",       "Police station → evidence locker → hub"),
    ("level_house",        "Bludworth's house → notes → hub"),
    ("level_hub",          "Drive to next NPC workplace"),
    ("level_hotel",        "Warn the hunt target"),
    ("level_vet",          "Warn the hunt target"),
    ("level_auto",         "Warn the hunt target"),
    ("level_fair",         "Warn the hunt target"),
    ("level_bowl",         "Warn the hunt target"),
    ("level_finale",       "Use assembled items at the crossroads"),
]


# ---------------------------------------------------------------------------
# Main crawler class
# ---------------------------------------------------------------------------

class ChaosCrawler:
    """
    Automated playtester that hooks into GameScreen and drives the engine
    through on_submit_command, exactly replicating human input.
    """

    VALID_MODES = ("pathfinder", "chaos", "adversarial")

    def __init__(self, game_screen):
        self.game_screen = game_screen
        self.gl          = game_screen.game_logic
        self.is_running  = False
        self.mode        = "pathfinder"
        self.turn_count  = 0
        self.turns_limit = 0
        self.fast_mode   = False
        self.runs_total  = 1
        self.runs_done   = 0
        self.tick_interval = 0.2
        self._event = None

        # Stuck detection
        self._location_history     = deque(maxlen=STUCK_THRESHOLD)
        self._popup_cooldown       = 0
        self._last_command         = ""
        self._repeat_command_count = 0
        self._MAX_REPEAT_COMMANDS  = 5

        # Coverage tracking
        self._rooms_visited    = set()
        self._items_collected  = []
        self._npcs_talked      = set()
        self._levels_reached   = set()
        self._hazards_triggered = []
        self._qte_results      = {"pass": 0, "fail": 0}
        self._commands_issued  = []
        self._errors           = []

        # Pathfinder-specific state
        self._containers_searched = set()
        self._exits_used          = set()
        self._hub_drives_done     = 0

        # Logging
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

    def start(self, turns: int = 500, mode: str = "pathfinder",
              fast: bool = False, runs: int = 1):
        if self.is_running:
            return
        if mode not in self.VALID_MODES:
            print(f"[ChaosCrawler] Unknown mode '{mode}'. Valid: {self.VALID_MODES}")
            mode = "pathfinder"

        self.is_running    = True
        self.mode          = mode
        self.turns_limit   = turns
        self.fast_mode     = fast
        self.runs_total    = runs
        self.runs_done     = 0
        self.tick_interval = 0.05 if fast else 0.2
        self._start_run()

    def _start_run(self):
        self.turn_count            = 0
        self._location_history.clear()
        self._popup_cooldown       = 0
        self._last_command         = ""
        self._repeat_command_count = 0
        self._commands_issued.clear()
        self._containers_searched.clear()
        self._exits_used.clear()
        self._hub_drives_done = 0

        self._auto_start_new_game()

        self.logger.info("=" * 60)
        self.logger.info(
            f"RUN {self.runs_done + 1}/{self.runs_total} STARTING — "
            f"{self.turns_limit} turns  "
            f"mode={self.mode}  "
            f"({'fast' if self.fast_mode else 'normal'})"
        )
        self.logger.info("=" * 60)

        if self._event:
            self._event.cancel()
        self._event = Clock.schedule_interval(self._tick, self.tick_interval)

    def _auto_start_new_game(self):
        from kivy.app import App
        app = App.get_running_app()
        gs  = self.game_screen

        if hasattr(gs, 'reset_ui_state'):
            gs.reset_ui_state()

        # Pathfinder always picks a non-Visionary class to test the standard flow.
        # Chaos picks any. Adversarial picks whoever has the most HP (more time to die badly).
        if self.mode == "pathfinder":
            chars = ['Citizen Detective', 'Journalist', 'EMT']
        elif self.mode == "adversarial":
            chars = ['Athlete', 'EMT']
        else:
            chars = ['Citizen Detective', 'EMT', 'Journalist',
                     'Mechanic', 'Athlete', 'Medium']

        char = random.choice(chars)
        gl   = gs.game_logic
        gl.start_new_game(character_class=char, start_level=0)
        self.gl = gl

        if app and app.root:
            for screen_name in ('lose', 'win', 'inter_level', 'intro'):
                if app.root.current == screen_name:
                    try:
                        app.root.current = 'game'
                        break
                    except Exception:
                        pass
            if app.root.current == 'intro':
                intro = app.root.get_screen('intro')
                Clock.schedule_once(lambda dt: intro.proceed_to_game(), 0.1)

        self.logger.info(f"[AUTO] New game started — char={char}  mode={self.mode}")

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
        print(f"[ChaosCrawler] Stopped. Report: {self.log_path}")

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self, dt):
        try:
            if self.turns_limit > 0 and self.turn_count >= self.turns_limit:
                self._end_run("Turn limit reached.")
                return True

            if self.gl.is_game_over or self.gl.game_won:
                reason = self.gl.player.get('death_reason', 'Game over')
                self._end_run(reason)
                return True

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
                    self._end_run(f"Reached {curr} screen")
                    return True

                elif curr == 'intro':
                    intro = app.root.get_screen('intro')
                    if hasattr(intro, 'proceed_to_game'):
                        Clock.schedule_once(lambda dt: intro.proceed_to_game(), 0.2)
                    return True

                elif curr != 'game':
                    return True

            if self._popup_cooldown > 0:
                self._popup_cooldown -= 1
                return True

            if self._handle_qte():
                return True

            if self._handle_popup():
                return True

            if self.gl.player.get('elevator_transit_active'):
                self.logger.info("[ELEVATOR] Transit active — waiting.")
                return True

            self._execute_turn()
            return True

        except Exception as e:
            tb = traceback.format_exc()
            self._errors.append((self.turn_count, tb))
            self.logger.error(f"CRAWLER EXCEPTION on turn {self.turn_count}:\n{tb}")
            self.stop(f"Crawler exception on turn {self.turn_count}")
            return False

    def _end_run(self, reason: str):
        self.runs_done += 1
        self.logger.info(f"RUN {self.runs_done}/{self.runs_total} ENDED — {reason}")
        self._write_summary(reason)

        if self.runs_done >= self.runs_total:
            self.is_running = False
            if self._event:
                self._event.cancel()
                self._event = None
            self.logger.info(f"ALL {self.runs_total} RUNS COMPLETE.")
            print(f"[ChaosCrawler] All {self.runs_total} runs complete. Report: {self.log_path}")
            return

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
        self.logger.info(f"[QTE] Active: '{qte_type}'  mode={self.mode}")

        if 'pattern' in qte_type.lower() or 'sequence' in qte_type.lower() or 'memory' in qte_type.lower():
            sequence = (
                qte_ctx.get('required_sequence') or
                qte_ctx.get('required_pattern') or
                qte_ctx.get('pattern') or
                ['down', 'right', 'left', 'up']
            )
            if self.mode == 'adversarial':
                # Deliberately send the wrong sequence
                wrong = [k for k in ['up', 'down', 'left', 'right'] if k not in sequence[:1]]
                sequence = wrong[:len(sequence)] or sequence
            for i, key in enumerate(sequence):
                Clock.schedule_once(lambda dt, k=key.lower(): self._submit(k), i * 0.05)
            self.logger.info(f"[QTE] Sending sequence: {sequence}")

        elif 'word' in qte_type.lower() or 'input' in qte_type.lower():
            expected = qte_ctx.get('expected_input_word', 'brace')
            if self.mode == 'adversarial':
                word = 'wrong'
            else:
                word = expected
            self._submit(word)

        else:
            expected_key = qte_ctx.get('expected_key', 'space')
            if self.mode == 'adversarial':
                self._submit('x')  # wrong key
            else:
                self._submit(expected_key)

        result = 'fail' if self.mode == 'adversarial' else 'pass'
        self._qte_results[result] += 1
        return True

    # ------------------------------------------------------------------
    # Popup handler
    # ------------------------------------------------------------------

    def _handle_popup(self) -> bool:
        handled_any = False

        if getattr(self.game_screen, '_popup_pending', False):
            return True

        if not self.gl.player.get('qte_active'):
            qte_popup = getattr(self.game_screen, 'active_qte_popup', None)
            if qte_popup and getattr(qte_popup, 'parent', None):
                self.logger.info("[AUTO] Dismissing resolved QTE popup.")
                try:
                    qte_popup.dismiss()
                    handled_any = True
                except Exception:
                    pass

        info_popup = getattr(self.game_screen, 'active_info_popup', None)
        if info_popup and getattr(info_popup, 'parent', None):
            opts = (self.gl.last_dialogue_context or {}).get('options', [])
            if not opts:
                self.logger.info("[AUTO] Dismissing info popup.")
                try:
                    info_popup.dismiss()
                    handled_any = True
                except Exception:
                    pass

        map_popup = getattr(self.game_screen, 'active_map_popup', None)
        if map_popup and getattr(map_popup, 'parent', None):
            self.logger.info("[AUTO] Dismissing map popup.")
            try:
                map_popup.dismiss()
                handled_any = True
            except Exception:
                pass

        if handled_any:
            self._popup_cooldown = POPUP_COOLDOWN_TURNS
        return handled_any

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    def _execute_turn(self):
        loc = self.gl.player.get('location', '')
        lvl = str(self.gl.player.get('current_level', ''))

        self._rooms_visited.add(loc)
        self._levels_reached.add(lvl)
        self._location_history.append(loc)

        if self.mode == 'pathfinder':
            cmd = self._generate_pathfinder()
        elif self.mode == 'chaos':
            cmd = self._generate_chaos()
        else:
            cmd = self._generate_adversarial()

        self._commands_issued.append(cmd)
        if len(self._commands_issued) > 200:
            self._commands_issued.pop(0)

        # Repeat guard
        if cmd == self._last_command:
            self._repeat_command_count += 1
            if self._repeat_command_count >= self._MAX_REPEAT_COMMANDS:
                self.logger.warning(
                    f"[LOOP] '{cmd}' repeated {self._repeat_command_count}x. Forcing wait."
                )
                self.gl.last_dialogue_context = {}
                self._repeat_command_count = 0
                cmd = "wait"
        else:
            self._last_command         = cmd
            self._repeat_command_count = 1

        self.logger.info(f"T{self.turn_count:04d}  [{lvl}]  {loc}  [{self.mode}]  >  {cmd}")
        self._submit(cmd)
        self.turn_count += 1

        hp     = self.gl.player.get('hp', 30)
        max_hp = self.gl.player.get('max_hp', 30)
        if hp < max_hp * 0.5 and not self.gl.player.get('qte_active'):
            self._qte_results['fail'] += 1

    def _submit(self, command: str):
        try:
            self.game_screen.on_submit_command(command_override=command)
        except Exception as e:
            self.logger.error(f"[SUBMIT] Exception on '{command}': {e}")

    # ------------------------------------------------------------------
    # MODE: pathfinder
    # ------------------------------------------------------------------

    def _generate_pathfinder(self) -> str:
        """
        Knows the golden path. Priorities (in order):
        1. Clear active dialogue
        2. Pick up priority items
        3. Search unsearched containers (finds keys)
        4. Examine objects with player_interaction (finds hazard interactions)
        5. Talk to any unmet NPCs
        6. Identify and use the exit room path
        7. Use items on objects when possible (finale items, keys)
        8. Hub: drive to next workplace or Bludworth
        9. Fall back to weighted random if stuck
        """
        # 1. Active dialogue
        cmd = self._pathfinder_dialogue()
        if cmd:
            return cmd

        loc       = self.gl.player.get('location', '')
        lvl       = str(self.gl.player.get('current_level', ''))
        room_data = self.gl.get_room_data(loc) or {}

        # 2. Priority item pickup
        takes = self.gl.get_available_targets('take')
        for item in takes:
            norm = item.lower().replace(' ', '_')
            if norm in PRIORITY_ITEMS or item.lower() in PRIORITY_ITEMS:
                self._items_collected.append(item)
                self.logger.info(f"[PATH] Priority take: '{item}'")
                return f"take {item}"

        # 3. Search unsearched containers
        cmd = self._pathfinder_search_containers(room_data, loc)
        if cmd:
            return cmd

        # 4. Use items on objects (keys on doors, finale items on crossroads objects)
        cmd = self._pathfinder_use_items(room_data, loc)
        if cmd:
            return cmd

        # 5. Talk to unmet NPCs
        talks = self.gl.get_available_targets('talk')
        for npc in talks:
            key = f"{loc}::{npc}"
            if key not in self._npcs_talked:
                self._npcs_talked.add(key)
                self.logger.info(f"[PATH] First talk: '{npc}'")
                return f"talk {npc}"

        # 6. Exit room: if we're in the exit room, use its exit immediately
        cmd = self._pathfinder_handle_exit_room(room_data, loc, lvl)
        if cmd:
            return cmd

        # 7. Hub-specific: choose the right drive target
        if 'hub' in lvl.lower():
            cmd = self._pathfinder_hub_drive()
            if cmd:
                return cmd

        # 8. Finale room: try to use assembled items
        if 'finale' in lvl.lower():
            cmd = self._pathfinder_finale(room_data, loc)
            if cmd:
                return cmd

        # 9. Move toward unexplored rooms; stuck fallback
        return self._pathfinder_move(room_data, loc)

    def _pathfinder_dialogue(self) -> str:
        options  = (self.gl.last_dialogue_context or {}).get('options', [])
        npc_name = (self.gl.last_dialogue_context or {}).get('npc_name', '')
        if not options:
            return ''
        if npc_name:
            loc = self.gl.player.get('location', '')
            npc = self.gl._find_npc_in_room(npc_name, loc)
            if not npc:
                self.logger.warning(f"[PATH] Stale dialogue context for '{npc_name}'. Clearing.")
                self.gl.last_dialogue_context = {}
                return ''
        return self._pick_dialogue_option(options, mode='pathfinder')

    def _pathfinder_search_containers(self, room_data: dict, loc: str) -> str:
        for furn in room_data.get('furniture', []):
            if not isinstance(furn, dict):
                continue
            name = furn.get('name', '')
            key  = f"{loc}::{name}"
            if key in self._containers_searched:
                continue
            if furn.get('is_container') or furn.get('items'):
                self._containers_searched.add(key)
                self.logger.info(f"[PATH] Searching container: '{name}'")
                return f"search {name}"
        searches = self.gl.get_available_targets('search')
        for s in searches:
            key = f"{loc}::{s}"
            if key not in self._containers_searched:
                self._containers_searched.add(key)
                self.logger.info(f"[PATH] Searching: '{s}'")
                return f"search {s}"
        return ''

    def _pathfinder_use_items(self, room_data: dict, loc: str) -> str:
        inventory = self.gl.player.get('inventory', [])
        inv_ids   = {
            (i.get('id', '') if isinstance(i, dict) else str(i)).lower()
            for i in inventory
        }
        # Try every object in the room's player_interaction.use entries
        for obj in room_data.get('objects', []):
            if not isinstance(obj, dict):
                continue
            interactions = obj.get('player_interaction', {}).get('use', [])
            for rule in interactions:
                req_item = rule.get('required_item_name', '')
                if req_item and req_item.lower() in inv_ids:
                    obj_name = obj.get('name', '')
                    self.logger.info(f"[PATH] Using '{req_item}' on '{obj_name}'")
                    return f"use {req_item} on {obj_name}"
        # Try key-on-door for locked exits
        for direction, dest in room_data.get('exits', {}).items():
            if isinstance(dest, dict) and dest.get('locked'):
                key_id = dest.get('unlocks_with', '')
                if key_id and key_id.lower() in inv_ids:
                    self.logger.info(f"[PATH] Unlocking exit '{direction}' with '{key_id}'")
                    return f"unlock {direction}"
        return ''

    def _pathfinder_handle_exit_room(self, room_data: dict, loc: str, lvl: str) -> str:
        if not (room_data.get('is_exit') or room_data.get('exit_room')):
            return ''
        exits = room_data.get('exits', {})
        if not exits:
            return ''
        # Prefer exits that aren't back toward the entry room
        exit_key = next(iter(exits))
        dest = exits[exit_key]
        if isinstance(dest, dict):
            dest = dest.get('target', exit_key)
        exit_key_str = f"{loc}->{exit_key}"
        if exit_key_str not in self._exits_used:
            self._exits_used.add(exit_key_str)
            self.logger.info(f"[PATH] Exit room — using exit '{exit_key}' -> '{dest}'")
            return f"move {exit_key}"
        # Try all exits
        for k, v in exits.items():
            eks = f"{loc}->{k}"
            if eks not in self._exits_used:
                self._exits_used.add(eks)
                return f"move {k}"
        return ''

    def _pathfinder_hub_drive(self) -> str:
        moves = self.gl.get_available_targets('move')
        # Priority: workplaces > Bludworth > police > surrender last
        ORDER = ['drive to', 'find next', 'prepare', 'bludworth', 'surrender', 'fight']
        for keyword in ORDER:
            for m in moves:
                if keyword in m.lower():
                    self.logger.info(f"[PATH] Hub drive: '{m}'")
                    self._hub_drives_done += 1
                    return f"move {m}"
        if moves:
            return f"move {random.choice(moves)}"
        return ''

    def _pathfinder_finale(self, room_data: dict, loc: str) -> str:
        inventory = self.gl.player.get('inventory', [])
        inv_ids   = {
            (i.get('id', '') if isinstance(i, dict) else str(i)).lower()
            for i in inventory
        }
        finale_items = [
            ('defibrillator_pads',       'industrial battery array'),
            ('charged_defibrillator_rig','industrial battery array'),
            ('vet_sedatives',            'veterinary medical kit'),
            ('fentanyl_reversal_kit',    'veterinary medical kit'),
            ('loaded_heavy_revolver',    'heavy revolver'),
            ('hypothermia_survival_kit', 'cooling chamber'),
            ('asphyxiation_survival_kit','breathing apparatus'),
            ('willing_companion_token',  'companion'),
        ]
        for item_id, obj_name in finale_items:
            if item_id in inv_ids:
                self.logger.info(f"[PATH] Finale: using '{item_id}' on '{obj_name}'")
                return f"use {item_id} on {obj_name}"
        # Examine everything to trigger examine_details hints
        for obj in room_data.get('objects', []):
            if isinstance(obj, dict):
                return f"examine {obj.get('name', '')}"
        return ''

    def _pathfinder_move(self, room_data: dict, loc: str) -> str:
        moves = self.gl.get_available_targets('move')
        if self._is_stuck():
            self.logger.warning(f"[PATH] Stuck in '{loc}'. Forcing move.")
            if moves:
                return f"move {random.choice(moves)}"
            return "wait"
        # Prefer moves toward unvisited rooms
        for m in moves:
            dest = room_data.get('exits', {}).get(m)
            if isinstance(dest, dict):
                dest = dest.get('target', '')
            if dest and dest not in self._rooms_visited:
                return f"move {m}"
        if moves:
            return f"move {random.choice(moves)}"
        return "wait"

    # ------------------------------------------------------------------
    # MODE: chaos
    # ------------------------------------------------------------------

    def _generate_chaos(self) -> str:
        """Completely random legal actions. No strategy."""
        options = (self.gl.last_dialogue_context or {}).get('options', [])
        if options:
            return f"respond {random.randint(1, max(1, len(options)))}"

        loc  = self.gl.player.get('location', '')
        room = self.gl.get_room_data(loc) or {}

        # Build the full pool of everything legal
        pool = []
        for verb, weight in VERB_WEIGHTS_CHAOS.items():
            targets = self.gl.get_available_targets(verb)
            for t in targets:
                pool.extend([f"{verb} {t}"] * weight)

        # Add raw examine for objects/furniture
        for obj in room.get('objects', []) + room.get('furniture', []):
            if isinstance(obj, dict):
                pool.append(f"examine {obj.get('name', '')}")

        # Add completely random nonsense commands occasionally
        if random.random() < 0.05:
            nonsense = ["use nothing on nothing", "take air", "examine self",
                        "force reality", "talk nobody", "move up"]
            pool.extend(nonsense)

        if self._is_stuck():
            moves = self.gl.get_available_targets('move')
            if moves:
                return f"move {random.choice(moves)}"
            return "wait"

        if pool:
            return random.choice(pool)
        return "wait"

    # ------------------------------------------------------------------
    # MODE: adversarial
    # ------------------------------------------------------------------

    def _generate_adversarial(self) -> str:
        """
        Deliberately makes bad choices:
        - Picks aggressive dialogue options
        - Ignores priority items
        - Forces every door it sees
        - Tries to trigger hazards with use commands
        - Occasionally idles to let hazards escalate
        """
        options  = (self.gl.last_dialogue_context or {}).get('options', [])
        npc_name = (self.gl.last_dialogue_context or {}).get('npc_name', '')
        if options:
            if npc_name:
                loc = self.gl.player.get('location', '')
                if not self.gl._find_npc_in_room(npc_name, loc):
                    self.gl.last_dialogue_context = {}
                    options = []
            if options:
                return self._pick_dialogue_option(options, mode='adversarial')

        loc       = self.gl.player.get('location', '')
        room_data = self.gl.get_room_data(loc) or {}

        # 10% chance to just wait and let hazards escalate
        if random.random() < 0.10:
            self.logger.info("[ADV] Waiting deliberately.")
            return "wait"

        forces = self.gl.get_available_targets('force')
        for direction, dest in room_data.get('exits', {}).items():
            if isinstance(dest, dict) and dest.get('locked'):
                forces.append(direction)
        for furn in room_data.get('furniture', []):
            if isinstance(furn, dict) and furn.get('locked'):
                forces.append(furn.get('name', ''))
        forces = list(set(f for f in forces if f))

        pool = []
        for verb, weight in VERB_WEIGHTS_ADVERSARIAL.items():
            targets = self.gl.get_available_targets(verb)
            for t in targets:
                pool.extend([f"{verb} {t}"] * weight)

        # Extra weight on forcing things
        for f in forces:
            pool.extend([f"force {f}"] * 5)

        # Try to use items on hazard-related objects (may trigger bad QTEs)
        uses = self.gl.get_available_targets('use')
        for u in uses:
            pool.extend([f"use {u}"] * 3)

        if self._is_stuck():
            moves = self.gl.get_available_targets('move')
            if moves:
                return f"move {random.choice(moves)}"
            return "wait"

        moves = self.gl.get_available_targets('move')
        for m in moves:
            pool.extend([f"move {m}"] * VERB_WEIGHTS_ADVERSARIAL['move'])

        if pool:
            return random.choice(pool)
        return "wait"

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _pick_dialogue_option(self, options: list, mode: str = 'pathfinder') -> str:
        scored = []
        for i, opt in enumerate(options, 1):
            text  = opt.get('text', '').lower()
            score = 10

            if mode == 'adversarial':
                if any(w in text for w in DIALOGUE_ADVERSARIAL_PREFER):
                    score += 8
                if any(w in text for w in ('help', 'safe', 'trust', 'together')):
                    score = max(1, score - 6)
            else:
                if any(w in text for w in ('help', 'together', 'believe', 'trust', 'safe', 'warn')):
                    score += 5
                if any(w in text for w in ('danger', 'careful', 'leave', 'go')):
                    score += 3
                if any(w in text for w in DIALOGUE_AVOID_KEYWORDS):
                    score = max(1, score - 8)
                if 'walk away' in text:
                    score = 1

            scored.append((score, i))

        total = sum(s for s, _ in scored)
        if total == 0:
            return f"respond {scored[-1][1]}"
        r, cumulative = random.uniform(0, total), 0
        for score, idx in scored:
            cumulative += score
            if r <= cumulative:
                self.logger.info(f"[DIALOGUE] Option {idx}  (mode={mode})")
                return f"respond {idx}"
        return f"respond {scored[-1][1]}"

    def _is_stuck(self) -> bool:
        if len(self._location_history) < STUCK_THRESHOLD:
            return False
        return len(set(self._location_history)) == 1

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _write_summary(self, stop_reason: str):
        gl = self.gl
        p  = gl.player

        lines = [
            "",
            "=" * 60,
            "CRAWL SUMMARY",
            "=" * 60,
            f"Mode             : {self.mode}",
            f"Stop reason      : {stop_reason}",
            f"Total turns      : {self.turn_count} / {self.turns_limit}",
            f"Final level      : {p.get('current_level', 'UNKNOWN')}",
            f"Final room       : {p.get('location', 'UNKNOWN')}",
            f"Final HP         : {p.get('hp', '?')} / {p.get('max_hp', '?')}",
            f"Final fear       : {p.get('fear', 0.0):.2f}",
            f"Final score      : {p.get('score', 0)}",
            f"Hub drives done  : {self._hub_drives_done}",
            "",
            "── COVERAGE ──────────────────────────────────────────────",
            f"Levels reached   : {sorted(self._levels_reached)}",
            f"Unique rooms     : {len(self._rooms_visited)}",
        ]
        for room in sorted(self._rooms_visited):
            lines.append(f"    {room}")

        lines += ["", f"NPCs talked to   : {len(self._npcs_talked)}"]
        for npc in sorted(self._npcs_talked):
            lines.append(f"    {npc}")

        lines += ["", f"Items collected  : {len(self._items_collected)}"]
        for item in self._items_collected:
            lines.append(f"    {item}")

        lines += ["", f"Containers searched: {len(self._containers_searched)}"]
        for c in sorted(self._containers_searched):
            lines.append(f"    {c}")

        npc_status = p.get('npc_status', {})
        if npc_status:
            lines += ["", "── SURVIVOR ROSTER ───────────────────────────────────────"]
            for name, status in npc_status.items():
                lines.append(f"    {name:<20} {status}")

        deaths_list = p.get('deaths_list', [])
        deaths_idx  = p.get('deaths_list_index', 0)
        if deaths_list:
            lines += ["", "── DEATH'S DESIGN ────────────────────────────────────────"]
            for i, name in enumerate(deaths_list):
                marker = " <-- NEXT" if i == deaths_idx else ""
                struck = "[DEAD] " if npc_status.get(name.lower(), 'alive') == 'dead' else ""
                lines.append(f"    {i+1}. {struck}{name}{marker}")

        inventory = p.get('inventory', [])
        if inventory:
            lines += ["", "── INVENTORY ─────────────────────────────────────────────"]
            for item in inventory:
                lines.append(f"    {item}")

        interaction_flags = getattr(gl, 'interaction_flags', set())
        key_flags = {f for f in interaction_flags if any(k in f for k in
            ('learned_deaths_list', 'bludworth', 'social_worker', 'finale', 'police',
             'knows_cycle', 'knows_resurrection', 'knows_blood'))}
        if key_flags:
            lines += ["", "── KEY FLAGS ─────────────────────────────────────────────"]
            for f in sorted(key_flags):
                lines.append(f"    {f}")

        player_flags = p.get('flags', {})
        if isinstance(player_flags, dict) and player_flags:
            lines += ["", "── PLAYER FLAGS ──────────────────────────────────────────"]
            for k, v in player_flags.items():
                if v:
                    lines.append(f"    {k}: {v}")

        lines += [
            "",
            "── QTE RESULTS ───────────────────────────────────────────",
            f"    Pass: {self._qte_results['pass']}   Fail: {self._qte_results['fail']}",
        ]

        if self._errors:
            lines += ["", f"── ERRORS ({len(self._errors)}) ──────────────────────────────────"]
            for turn_num, tb in self._errors:
                lines.append(f"    Turn {turn_num}:")
                for tb_line in tb.strip().split('\n'):
                    lines.append(f"        {tb_line}")
        else:
            lines += ["", "── ERRORS ────────────────────────────────────────────────",
                      "    None. Clean run."]

        lines += ["", "── LAST 20 COMMANDS ──────────────────────────────────────"]
        for cmd in self._commands_issued[-20:]:
            lines.append(f"    {cmd}")

        lines += ["", "=" * 60, "END OF REPORT", "=" * 60]

        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines))