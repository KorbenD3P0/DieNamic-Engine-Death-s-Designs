import json

# Load hazards
with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

# The 9 hazards we just added examine_actions to
recently_added = [
    'bull_pen_gate',
    'falling_scaffolding', 
    'wrecking_ball',
    'wobbling_ceiling_fan',
    'falling_fan',
    'propane_tank_explosion',
    'photo_booth_electrocution',
    'hospital_exit',
    'spilled_hot_oil'
]

print("Hazards needing state-specific examine descriptions:\n")
for hazard_id in recently_added:
    if hazard_id in hazards:
        hazard = hazards[hazard_id]
        states = list(hazard.get('states', {}).keys())
        name = hazard.get('name', hazard_id)
        print(f"{hazard_id} ('{name}'):")
        print(f"  States ({len(states)}): {', '.join(states)}")
        
        # Check if it has generic any_state
        examine = hazard.get('examine_actions', {})
        if 'any_state' in examine:
            print(f"  ⚠ Currently has generic 'any_state' examine")
        print()
