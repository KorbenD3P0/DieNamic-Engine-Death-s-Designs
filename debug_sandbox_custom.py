import logging
import sys
import os

# Setup simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SandboxVerify")

try:
    # from fd_terminal.main import FinalDestinationApp # Avoid Kivy Window creation
    from fd_terminal.game_logic import GameLogic
    from fd_terminal.resource_manager import ResourceManager
    # from kivy.base import EventLoop
    # Mock App for Kivy deps
    # if not EventLoop.window:
    #     from kivy.core.window import Window
except ImportError as e:
    print(f"Failed to import game modules: {e}")
    sys.exit(1)

def verify_custom_sandbox():
    print("--- Starting Custom Sandbox Verification ---")
    
    # 1. Initialize Systems
    # app = FinalDestinationApp()
    resource_manager = ResourceManager()
    resource_manager.load_master_data()
    
    game = GameLogic(resource_manager)
    
    # 2. Define Configuration
    # We select ONLY the 'robo_vacuum'.
    # This should remove 'hospital_exit', 'prize_bull', etc. from the lobby.
    target_hazard = 'robo_vacuum'
    config = {
        'hazards': [target_hazard],
        'include_related': True,
        'include_all_items': False
    }
    
    print(f"Applying Config: {config}")
    
    # 3. Start Game in Gym
    try:
        game.start_new_game(character_class="Journalist", start_level="gym", sandbox_config=config)
    except Exception as e:
        print(f"CRITICAL: Failed to start game: {e}")
        import traceback
        traceback.print_exc()
        return

    # 4. Verify Hazards in Lobby
    lobby = game.current_level_rooms_world_state.get('Gym Lobby')
    if not lobby:
        print("FAIL: Gym Lobby not found.")
        return

    hazards = lobby.get('hazards_present', [])
    print(f"Lobby Hazards: {hazards}")
    
    hazard_types = [h if isinstance(h, str) else h.get('type') for h in hazards]
    
    if target_hazard in hazard_types:
        print(f"PASS: {target_hazard} is present.")
    else:
        print(f"FAIL: {target_hazard} is MISSING.")
        
    if 'hospital_exit' in hazard_types:
        print("FAIL: 'hospital_exit' should have been filtered out.")
    else:
        print("PASS: Unselected hazards filtered out.")

    # 5. Verify Items
    # In the Gym JSON, we didn't explicitly place robot items, but let's assume one was there or we can check what IS there.
    # Actually, the Gym level might not have robotic items by default if I didn't verify that.
    # Let's just check that UNRELATED items are gone?
    # Or, to be safer, let's check a hazard that definitely has items in the room.
    # 'hospital_exit' usually has 'bludworths_house_key' if we put it there?
    # Let's rely on the hazard check primarily, and just print items to inspect behavior.
    
    items = lobby.get('items_present', [])
    # Convert item IDs to types via world state
    item_types = []
    for i_id in items:
        i_data = game.current_level_items_world_state.get(i_id)
        if i_data:
            item_types.append(i_data.get('type'))
            
    print(f"Lobby Items (Types): {item_types}")
    
    # 6. Verify Container (Reception Desk)
    # The Gym Lobby has a 'reception_desk' with 'screwdriver' (valid) and 'flashlight' (invalid for robo_vacuum only)
    desk = None
    for furn in lobby.get('furniture', []):
        if furn.get('name') == 'reception_desk':
            desk = furn
            break
            
    if not desk:
        print("FAIL: Reception Desk not found.")
    else:
        desk_items = desk.get('items', [])
        # We need to map these IDs to types too because _populate converts them? 
        # Actually logic says: pre-placed items are registered in world state but KEPT in furniture list.
        # So we can look them up.
        
        desk_item_types = []
        for i_id in desk_items:
            i_data = game.current_level_items_world_state.get(i_id)
            if i_data:
                 desk_item_types.append(i_data.get('type'))
            else:
                 desk_item_types.append(i_id) # Fallback
                 
        print(f"Desk Items: {desk_item_types}")
        
        if 'screwdriver' in desk_item_types:
            print("PASS: Screwdriver (Related) is PRESENT.")
        else:
            print("FAIL: Screwdriver is MISSING.")
            
        if 'flashlight' in desk_item_types:
            print("FAIL: Flashlight (Unrelated) is PRESENT.")
        else:
            print("PASS: Flashlight filtered out.")

    print("--- Verification Complete ---")

if __name__ == "__main__":
    verify_custom_sandbox()
