from fd_terminal.resource_manager import ResourceManager
from fd_terminal.game_logic import GameLogic
from fd_terminal.hazard_engine import HazardEngine
from fd_terminal.death_ai import DeathAI
from fd_terminal.qte_engine import QTE_Engine
import logging

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SandboxVerify")

class MockSettings:
    def get(self, key, default):
        if key == 'sandbox_mode': return True
        return default

def test_sandbox_loading():
    print("--- Verifying Sandbox 'Gym' Level Loading ---")
    
    # 1. Initialize Managers
    rm = ResourceManager()
    rm.load_master_data()
    
    gl = GameLogic(resource_manager=rm)
    he = HazardEngine(resource_manager=rm)
    qte = QTE_Engine(resource_manager=rm, game_logic_ref=gl)
    dai = DeathAI(gl)

    # Link Systems
    gl.hazard_engine = he
    gl.qte_engine = qte
    gl.death_ai = dai
    he.game_logic = gl

    # 2. Start Game in "gym"
    logger.info("Attempting to start game in 'gym' level...")
    gl.start_new_game(character_class="Journalist", start_level="gym")

    # 3. Verify Player Location
    current_room = gl.player['location']
    print(f"Player Start Location: {current_room}")
    if current_room != "Gym Lobby":
        print("FAIL: Player not in Gym Lobby.")
        return

    # 4. Verify Hazards in Lobby
    lobby_hazards = gl.hazard_engine.active_hazards
    print(f"Active Hazards Count: {len(lobby_hazards)}")
    
    expected_hazards = ["hospital_exit", "robo_vacuum", "photo_booth_electrocution", "test_your_strength_game"]
    found = 0
    for h_data in lobby_hazards.values():
        if h_data['location'] != current_room: continue
        if h_data['type'] in expected_hazards:
            found += 1
            print(f"Found Hazard: {h_data['type']}")
            
    if found >= len(expected_hazards):
        print("SUCCESS: Lobby hazards loaded.")
    else:
        print(f"FAIL: Missing hazards. Found {found}/{len(expected_hazards)}")

    # 5. Verify Room Navigation & Weight Room Hazards
    print("Navigating to Weight Room...")
    gl._command_move("east")
    if gl.player['location'] == "Weight Room":
        print("SUCCESS: Moved to Weight Room.")
        # Check active hazards again (should include new room's hazards)
        weight_hazards = ["wobbling_ceiling_fan", "falling_scaffolding"]
        found_w = 0
        for h_data in gl.hazard_engine.active_hazards.values():
            if h_data['location'] != "Weight Room": continue
            if h_data['type'] in weight_hazards:
                found_w += 1
        
        if found_w > 0:
             print(f"SUCCESS: Found Weight Room hazards ({found_w}).")
        else:
             print("FAIL: Weight Room hazards not found.")

    print("--- Sandbox Verification Complete ---")

if __name__ == "__main__":
    test_sandbox_loading()
