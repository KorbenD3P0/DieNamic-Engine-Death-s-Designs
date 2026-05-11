import json

# Load hazards
with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

# The 9 hazards we need to enhance
hazards_to_enhance = [
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

def create_state_specific_examine(hazard_id, hazard_data):
    """Create state-specific examine_actions from state descriptions."""
    states = hazard_data.get('states', {})
    object_names = hazard_data.get('object_name_options', [])
    placement = hazard_data.get('placement_object', [])
    
    # Get targets from object_names or placement
    targets = []
    if object_names:
        targets = [name.lower() for name in object_names]
    elif placement:
        targets = [obj.lower() for obj in placement[:2]]
    
    if not targets:
        targets = [hazard_id.replace('_', ' ')]
    
    # Create state-specific examine entries
    state_examines = []
    
    for state_id, state_data in states.items():
        state_desc = state_data.get('description', '')
        
        # Create examine message from state description
        # Take first sentence or first 150 chars of description
        if '. ' in state_desc:
            examine_msg = state_desc.split('. ')[0] + '.'
        else:
            examine_msg = state_desc[:150] + ('...' if len(state_desc) > 150 else '')
        
        state_examines.append({
            "requires_hazard_state": [state_id],
            "message": examine_msg
        })
    
    # Build new examine_actions structure
    new_examine = {}
    
    # Add state-specific examines
    for examine_entry in state_examines:
        state_name = examine_entry["requires_hazard_state"][0]
        if state_name not in new_examine:
            new_examine[state_name] = []
        
        new_examine[state_name].append({
            "targets": targets[:5],  # Limit to 5 targets
            "message": examine_entry["message"]
        })
    
    return new_examine

# Enhance each hazard
enhanced_count = 0

for hazard_id in hazards_to_enhance:
    if hazard_id in hazards:
        hazard_data = hazards[hazard_id]
        
        # Check if it has the generic any_state examine
        examine_actions = hazard_data.get('examine_actions', {})
        if 'any_state' in examine_actions and len(hazard_data.get('states', {})) > 0:
            # Replace with state-specific examine
            new_examine = create_state_specific_examine(hazard_id, hazard_data)
            hazards[hazard_id]['examine_actions'] = new_examine
            enhanced_count += 1
            print(f"✓ Enhanced {hazard_id} with {len(new_examine)} state-specific examines")

print(f"\nEnhanced {enhanced_count} hazards with state-specific examine_actions")

# Save updated hazards
with open('data/hazards.json', 'w', encoding='utf-8') as f:
    json.dump(hazards, f, indent=2, ensure_ascii=False)

print("Updated hazards.json saved")
