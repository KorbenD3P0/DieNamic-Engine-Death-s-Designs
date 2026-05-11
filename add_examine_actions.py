import json
import copy

# Load hazards
with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

# Template for examine_actions
def generate_examine_actions(hazard_id, hazard_data):
    """Generate examine_actions based on hazard name and placement."""
    name = hazard_data.get('name', hazard_id.replace('_', ' ').title())
    placement = hazard_data.get('placement_object', [])
    object_names = hazard_data.get('object_name_options', [])
    
    # Generate targets from placement objects and object names
    targets = []
    
    # Add object name options
    for obj_name in object_names:
        targets.append(obj_name.lower())
    
    # Add placement objects if no object names
    if not targets and placement:
        for obj in placement:
            targets.append(obj.lower())
    
    # Add hazard name variations
    if not targets:
        targets.append(hazard_id.replace('_', ' '))
        targets.append(name.lower())
    
    # Remove duplicates
    targets = list(dict.fromkeys(targets))
    
    # Generate examine message
    if placement:
        message = f"You examine the {name.lower()}. "
    else:
        message = f"You see {name.lower()}. "
    
    # Add state-based description hint
    message += "It appears to be in a specific state that might change."
    
    examine_actions = {
        "any_state": [
            {
                "targets": targets[:5],  # Limit to 5 targets
                "message": message
            }
        ]
    }
    
    return examine_actions

# Find and fix hazards
fixed_count = 0
hazards_to_fix = []

for hazard_id, hazard_data in hazards.items():
    has_examine = 'examine_actions' in hazard_data or 'player_interaction' in hazard_data
    if not has_examine:
        hazards_to_fix.append(hazard_id)
        examine_actions = generate_examine_actions(hazard_id, hazard_data)
        hazards[hazard_id]['examine_actions'] = examine_actions
        fixed_count += 1

print(f"Fixed {fixed_count} hazards:")
for h in sorted(hazards_to_fix):
    print(f"  - {h}")

# Save updated hazards
with open('data/hazards.json', 'w', encoding='utf-8') as f:
    json.dump(hazards, f, indent=2, ensure_ascii=False)

print(f"\nUpdated hazards.json with examine_actions for {fixed_count} hazards")
