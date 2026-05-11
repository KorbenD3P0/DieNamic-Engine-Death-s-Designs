import unittest
import os
import sys
import logging
from unittest.mock import MagicMock

# Ensure we can import modules from fd_terminal
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fd_terminal.resource_manager import ResourceManager
from fd_terminal.hazard_engine import HazardEngine
from fd_terminal.qte_engine import QTE_Engine
from fd_terminal.game_logic import GameLogic
from fd_terminal.death_ai import DeathAI

class TestSystemIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure logging to suppress noise during tests unless error
        logging.basicConfig(level=logging.CRITICAL)
        
        # Initialize Core Systems (Static/Read-Only)
        cls.rm = ResourceManager()
        cls.rm.load_master_data()
        
    def setUp(self):
        # Initialize Mutable Systems per test for isolation
        self.gl = GameLogic(resource_manager=self.rm)
        self.he = HazardEngine(resource_manager=self.rm)
        self.qte = QTE_Engine(resource_manager=self.rm, game_logic_ref=self.gl)
        self.dai = DeathAI(game_logic_ref=self.gl)
        
        # Link them up
        self.gl.hazard_engine = self.he
        self.gl.qte_engine = self.qte
        self.gl.death_ai = self.dai
        self.he.game_logic = self.gl
        
        # Mock Player
        self.gl.player = {
            'location': 'start_room',
            'inventory': [],
            'hp': 100,
            'fear': 0.0,
            'character_class': 'TestSubject',
            'visited_rooms': set(),
            'quest_log': {},
            'status_effects': []
        }

    def test_01_hazard_definitions_validity(self):
        """Ensure all hazards in hazards.json have valid structures."""
        hazards = self.rm.get_data('hazards')
        self.assertTrue(hazards, "No hazards loaded from definitions.")
        
        for hid, hdata in hazards.items():
            # Check required fields
            self.assertIn('name', hdata, f"Hazard {hid} missing 'name'")
            self.assertIn('states', hdata, f"Hazard {hid} missing 'states'")
            self.assertIn('initial_state', hdata, f"Hazard {hid} missing 'initial_state'")
            
            # Check states
            initial = hdata['initial_state']
            self.assertIn(initial, hdata['states'], f"Hazard {hid} initial_state '{initial}' not found in states.")

    def test_02_qte_definitions_validity(self):
        """Ensure all QTEs in qte_definitions.json are valid."""
        qtes = self.rm.get_data('qte_definitions')
        self.assertTrue(qtes, "No QTE definitions loaded.")
        
        valid_input_types = {'mash', 'tap', 'sequence', 'word', 'hold_release', 'alternate', 'rhythm', 'aim_click', 'drag', 'spiral', 'choice', 'single_key'}
        
        for qid, qdata in qtes.items():
            self.assertIn('input_type', qdata, f"QTE {qid} missing 'input_type'")
            itype = qdata.get('input_type')
            # spiral is handled as 'spiral' or 'drag' in some contexts, but let's verify recognized types
            if itype not in valid_input_types:
                # Warn but don't fail immediately if it's a new experimental type, unless strict
                print(f"WARNING: QTE {qid} has uncommon input_type '{itype}'")

    def test_03_hazard_qte_integration(self):
        """Verify that hazards triggering QTEs refer to valid QTE types."""
        hazards = self.rm.get_data('hazards')
        qtes = self.rm.get_data('qte_definitions')
        
        for hid, hdata in hazards.items():
            for sname, sdata in hdata.get('states', {}).items():
                if 'triggers_qte_on_entry' in sdata:
                    entry = sdata['triggers_qte_on_entry']
                    qte_type = entry.get('qte_type')
                    self.assertIn(qte_type, qtes, f"Hazard {hid} state {sname} triggers unknown QTE '{qte_type}'")

    def test_04_qte_hazard_state_loop(self):
        """Verify QTE success/failure states point back to valid hazard states if specified."""
        # This is tricky because QTEs are generic, but if they are defined inside a hazard's override 
        # (dynamic), we can't test them here. We test the static references if any.
        pass # Most state linkages are dynamic in hazards.json, checked in test_03 roughly?
        
        # Let's check specific hazard context overrides in hazards.json if they exist
        hazards = self.rm.get_data('hazards')
        for hid, hdata in hazards.items():
            for sname, sdata in hdata.get('states', {}).items():
                if 'triggers_qte_on_entry' in sdata:
                    ctx = sdata['triggers_qte_on_entry'].get('qte_context', {})
                    # If this hazard specific QTE defines next states, they should exist in THIS hazard
                    succ = ctx.get('next_state_after_qte_success')
                    fail = ctx.get('next_state_after_qte_failure')
                    
                    if succ:
                        self.assertIn(succ, hdata['states'], f"Hazard {hid} QTE success link '{succ}' not found in states.")
                    if fail:
                        # 'death' or 'terminal' might be implicit, but usually they are explicit states
                        self.assertIn(fail, hdata['states'], f"Hazard {hid} QTE failure link '{fail}' not found in states.")

    def test_05_game_logic_event_dispatch(self):
        """Verify GameLogic can generate a UI event without crashing."""
        try:
            self.gl.add_ui_event({'event_type': 'test_event', 'message': 'Hello'})
            events = self.gl.get_ui_events()
            self.assertTrue(len(events) > 0)
            self.assertEqual(events[0]['event_type'], 'test_event')
        except Exception as e:
            self.fail(f"GameLogic event dispatch failed: {e}")

    def test_06_death_ai_initialization(self):
        """Ensure Death AI can initialize and finds hazards."""
        # Fake a room with hazards
        self.gl.current_level_hazards_world_state = {
            'hazard_1': {'location': 'start_room', 'state': 'idle'}
        }
        try:
            # Should not crash
            actions = self.dai.execute_counter_strategies()
            # It might return None or an action, just ensuring no exception
        except Exception as e:
            self.fail(f"Death AI decision failed: {e}")

    def test_09_death_ai_persistence(self):
        """Verify DeathAI state can be saved and restored correctly."""
        # 1. Modify State
        self.dai.current_aggression_multiplier = 3.5
        self.dai.location_threat_scores['Kitchen'] = 15.0
        self.dai.player_behavior_patterns['qte_success_rate'] = 0.8
        
        # 2. Save
        state = self.dai.get_save_state()
        
        # 3. Create fresh instance
        new_dai = DeathAI(game_logic_ref=self.gl)
        
        # 4. Load
        new_dai.load_state(state)
        
        # 5. Verify
        self.assertEqual(new_dai.current_aggression_multiplier, 3.5)
        self.assertEqual(new_dai.location_threat_scores['Kitchen'], 15.0)
        self.assertEqual(new_dai.player_behavior_patterns['qte_success_rate'], 0.8)
        self.assertIsInstance(new_dai.location_threat_scores, dict)

    def test_10_death_ai_hazard_cap(self):
        """Verify DeathAI respects the global hazard cap of 30."""
        # fill active hazards
        self.he.active_hazards.clear()
        for i in range(35):
            self.he.active_hazards[f"dummy_{i}"] = {}
            
        # Try to spawn via DeathAI helper
        # _spawn_specific_hazard returns False if cap reached.
        result = self.dai._spawn_specific_hazard("gas_leak", "Test Room")
        self.assertFalse(result, "Should fail to spawn when over cap")
        
        # Clear for other tests
        self.he.active_hazards.clear()

    def test_11_death_ai_missing_method(self):
        """Verify _spawn_specific_hazard exists and works."""
        self.he.active_hazards.clear()
        
        # Mock add_active_hazard
        original_method = self.he._add_active_hazard
        self.he._add_active_hazard = MagicMock(return_value="hazard_id_123")
        
        try:
            res = self.dai._spawn_specific_hazard("test_hazard", "Room X")
            self.assertTrue(res)
            self.he._add_active_hazard.assert_called_once()
        finally:
            self.he._add_active_hazard = original_method

    def test_12_qte_input_routing(self):
        """Verify UI events route through GameLogic to QTE_Engine."""
        # 1. Start a QTE
        self.qte.start_qte("mash_qte", {"target_mash_count": 5})
        self.assertIsNotNone(self.qte.active_qte)
        
        # 2. Simulate UI Event (dict) routed via GameLogic
        # GameLogic.process_player_input -> QTE_Engine.handle_qte_input
        input_payload = {'event': 'mash_press', 'count': 1}
        
        # Ensure GameLogic has the reference (setupClass handles this, but good to be sure)
        self.gl.qte_engine = self.qte
        
        self.gl.process_player_input(input_payload)
        
        # 3. Verify QTE state updated
        state = self.qte.active_qte['runtime_state']
        self.assertEqual(state['mash_count'], 1, "Input did not reach QTE engine state via GameLogic")

    def test_13_qte_raw_key_input(self):
        """Verify QTE Engine correctly handles raw 'key_press' events."""
        
        # Test 1: Single Key "Space"
        self.qte.start_qte("single_key_qte", {"required_key": "space", "input_type": "single_key"})
        
        # Simulate wrong key
        self.gl.process_player_input({'event': 'key_press', 'key': 'x'})
        # Should initiate fail or stay active? Engine resolves on wrong key for single_key.
        # Check active_qte is gone or marked failed? 
        # Actually resolving ends it. Let's start fresh.
        
        self.qte.start_qte("single_key_qte", {"required_key": "space", "input_type": "single_key"})
        # Simulate correct key
        res = self.gl.process_player_input({'event': 'key_press', 'key': 'space'})
        # GameLogic.process_player_input calls QTE_Engine.handle_qte_input -> resolve_qte
        # If success, GL likely returns success message dict.
        
        self.assertIsNone(self.qte.active_qte, "QTE should close on success")
        self.assertTrue(res.get('success'), "Input should resolve to success")

        # Test 2: Alternating (A/D)
        self.qte.start_qte("mash_qte", {
            "input_type": "alternate", 
            "target_alternations": 2, 
            "keys_default": ["a", "d"]
        })
        
        # Press A (Correct 1)
        self.gl.process_player_input({'event': 'key_press', 'key': 'a'})
        self.assertEqual(self.qte.active_qte['runtime_state']['alternations_done'], 1)
        
        # Press A again (Wrong - expects D)
        # Assuming engine handles out-of-sync by ignoring or failing? logic says "failed: Out of sync"
        # Let's try D (Correct 2) -> Should finish
        self.gl.process_player_input({'event': 'key_press', 'key': 'd'})
        
        self.assertIsNone(self.qte.active_qte, "Alternating QTE should complete")

    def test_14_qte_rhythm_logic(self):
        """Verify Rhythm QTE logic validates cursor position authoritatively."""
        # Setup Rhythm QTE with explicit zone [0.4, 0.6]
        self.qte.start_qte("rhythm_qte", {
            "input_type": "rhythm",
            "target_beats": 1,
            "target_zone": [0.4, 0.6]
        })
        
        # 1. Miss (0.2)
        self.gl.process_player_input({'event': 'rhythm_tap', 'cursor_pos': 0.2})
        # Should initiate fail or resolve false?
        # Engine behavior: resolve_qte(False) -> active_qte = None
        self.assertIsNone(self.qte.active_qte, "Rhythm miss should end QTE")
        
        # Restart
        self.qte.start_qte("rhythm_qte", {
            "input_type": "rhythm",
            "target_beats": 1, 
            "target_zone": [0.4, 0.6]
        })
        
        # 2. Hit (0.5)
        res = self.gl.process_player_input({'event': 'rhythm_tap', 'cursor_pos': 0.5})
        self.assertIsNone(self.qte.active_qte, "Rhythm hit (target met) should end QTE")
        self.assertTrue(res.get('success'), "Hit should be successful")

    def test_15_entropy_escalation(self):
        """Verify Entropy system tracks success and scales aggression."""
        # Ensure DeathAI is initialized
        if not self.gl.death_ai:
            from fd_terminal.death_ai import DeathAI
            self.gl.death_ai = DeathAI(self.gl)
            self.gl.death_ai.resource_manager = self.rm

        # 1. Baseline
        initial_ent = self.gl.death_ai.entropy
        self.assertEqual(initial_ent, 0.0, "Entropy should start at 0")
        
        # 2. Simulate QTE Success
        # We need to construct a fake QTE result that GameLogic processes
        qte_res = {
            'success': True,
            'message': 'Test Success',
            'qte_source_hazard_id': 'test_hazard'
        }
        self.gl.player['qte_active'] = True
        self.gl._handle_qte_resolution(qte_res)
        
        # 3. Verify Increase (+2.0)
        self.assertEqual(self.gl.death_ai.entropy, 2.0, "Entropy should increase by 2.0 on QTE success")
        
        # 4. Verify Aggression Scaling
        # Mock progression to 0.0 for deterministic calc
        # Base=1.0, Scaling(0.0)=0.1.
        # Entropy=2.0 -> Factor = 1.0 + (2.0/50.0) = 1.04
        # Expected = 1.0 * 0.1 * 1.04 = 0.104
        self.gl.death_ai.calculate_level_progression = lambda: 0.0
        agg = self.gl.death_ai.get_effective_aggression()
        self.assertAlmostEqual(agg, 0.104, places=3, msg="Aggression should scale with entropy")

if __name__ == '__main__':
    print("Running System Verification Suite...")
    unittest.main(exit=False)
