import json
import re

with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

def generate_spawn_entities(hazard_id, hazard_data):
    """Generate spawn_entities based on hazard name and placement."""
    name = hazard_data.get('name', hazard_id.replace('_', ' ').title())
    placement = hazard_data.get('placement_object', [])
    object_names = hazard_data.get('object_name_options', [])
    
    # Main hazard entity
    main_entity_name = object_names[0] if object_names else hazard_id.replace('_', ' ')
    
    entities = [
        {
            "name": main_entity_name.lower(),
            "type": "hazard_entity",
            "hazard_key": hazard_id,
            "description": f"The {name.lower()} sits {placement[0] if placement else 'in the room'}, ominous and potentially dangerous."
        }
    ]
    
    # Add placement object if it's different from main entity
    if placement and placement[0].lower() not in main_entity_name.lower():
        entities.append({
            "name": placement[0].lower(),
            "type": "hazard_entity",
            "hazard_key": hazard_id,
            "description": f"The {placement[0]} where the {name.lower()} is located."
        })
    
    return entities

def generate_examine_responses(hazard_id, hazard_data, spawn_entities):
    """Generate examine_responses for each spawned entity across all states."""
    states = hazard_data.get('states', {})
    responses = {}
    
    for entity in spawn_entities:
        entity_name = entity['name']
        base_desc = entity['description']
        
        entity_responses = {
            "base_description": base_desc
        }
        
        # Add state-specific descriptions
        for state_id, state_data in states.items():
            state_desc = state_data.get('description', '')
            # Create contextualized description based on state
            entity_responses[state_id] = f"{base_desc} Currently in {state_id.replace('_', ' ')} state."
        
        responses[entity_name] = entity_responses
    
    return responses

#  Process hazards
added_spawn = 0
added_examine = 0

print("Adding spawn_entities and examine_responses to hazards...")
print()

for hazard_id, hazard_data in sorted(hazards.items()):
    has_spawn = 'spawn_entities' in hazard_data
    has_examine = 'examine_responses' in hazard_data
    
    changes_made = False
    
    # Add spawn_entities if missing
    if not has_spawn:
        spawn_entities = generate_spawn_entities(hazard_id, hazard_data)
        hazards[hazard_id]['spawn_entities'] = spawn_entities
        added_spawn += 1
        changes_made = True
        print(f"✓ Added spawn_entities to {hazard_id} ({len(spawn_entities)} entities)")
    else:
        spawn_entities = hazard_data['spawn_entities']
    
    # Add examine_responses if missing
    if not has_examine:
        examine_responses = generate_examine_responses(hazard_id, hazard_data, spawn_entities)
        hazards[hazard_id]['examine_responses'] =examine_responses
        added_examine += 1
        changes_made = True
        print(f"✓ Added examine_responses to {hazard_id} ({len(examine_responses)} entities)")
    
    if changes_made:
        print()

print("=" * 80)
print(f"SUMMARY:")
print(f"  Added spawn_entities to: {added_spawn} hazards")
print(f"  Added examine_responses to: {added_examine} hazards")
print("=" * 80)

# Save updated hazards
with open('data/hazards.json', 'w', encoding='utf-8') as f:
    json.dump(hazards, f, indent=2, ensure_ascii=False)

print("\nUpdated hazards.json saved!")
